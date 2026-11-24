"""Browser-side checks (frontend_checks.js) in headless Microsoft Edge, against a UI server on a free port with a
temporary local/ folder and no data backends. Skipped with a notice when Edge (or Chrome / Chromium) is not installed.

The page is the real index.html with every script, plus frontend_checks.js at the end; its result is read from the DOM.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from _common import ROOT, check, done


def find_browser():
    env = os.environ.get("TERMINAL_TEST_BROWSER", "").strip()
    cands = [env] if env else []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                      str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")]
    cands += [shutil.which(n) or "" for n in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
    cands.append("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    return next((c for c in cands if c and Path(c).is_file()), None)


browser = find_browser()
if not browser:
    print("test_frontend: skipped: Microsoft Edge / Chrome not found (set TERMINAL_TEST_BROWSER to a Chromium browser to run the browser checks)")
    print("test_frontend: 0 checks passed")
    sys.exit(0)

import server  # noqa: E402
import local_paths  # noqa: E402

local_paths.ensure()
STATIC = ROOT / "terminal" / "static"
CHECKS = (ROOT / "scripts" / "tests" / "frontend_checks.js").read_text(encoding="utf-8")
CAPTURE = """
(function () {
  window.addEventListener("error", function (e) { window.__fcError = window.__fcError || ("uncaught " + e.message + " (" + String(e.filename || "").split("/").pop() + ":" + e.lineno + ")"); });
  window.addEventListener("unhandledrejection", function (e) { var r = e && e.reason; window.__fcError = window.__fcError || ("unhandled rejection: " + String(r && r.message || r)); });
  var log = console.log;
  console.log = function (m) { if (/^frontend checks: \\d+ passed$/.test(String(m))) window.__fc = String(m); return log.apply(console, arguments); };
})();
"""
REPORT = """
(function () {
  function emit() {
    if (document.getElementById("frontend-result")) return;
    var out = document.createElement("pre"); out.id = "frontend-result";
    out.textContent = window.__fcError ? "FRONTEND FAILED: " + window.__fcError : (window.__fc || "FRONTEND FAILED: no result");
    document.body.appendChild(out);
  }
  var p = window.__fcPromise;
  if (p && typeof p.then === "function") {
    p.then(function () { setTimeout(emit, 0); }, function (e) {
      window.__fcError = window.__fcError || String(e && e.message || e);
      setTimeout(emit, 0);
    });
  } else {
    emit();
  }
})();
"""
page = (STATIC / "index.html").read_text(encoding="utf-8").replace(
    "</body>", '<script src="/__capture.js"></script><script src="/__checks.js"></script><script src="/__report.js"></script>\n</body>')
SCRIPTS = {
    "/__capture.js": CAPTURE,
    "/__checks.js": "try {\n" + CHECKS + "\n} catch (e) { window.__fcError = String(e && e.message || e); }\n",
    "/__report.js": REPORT,
}


class Handler(server.Handler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/__checks.html":
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if path in SCRIPTS:
            return self._send(200, SCRIPTS[path].encode("utf-8"), "application/javascript; charset=utf-8")
        if path == "/api/config":            # no data backends in this test: only the terminal's own views
            return self._json(200, {"product": "Test", "backends": [{"id": "x", "name": "Terminal views", "url": ""}]})
        return super().do_GET()


class Srv(server.ThreadingHTTPServer):
    daemon_threads = True


s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
httpd = Srv(("127.0.0.1", port), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

profile = tempfile.mkdtemp(prefix="terminal-edge-")
cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
       f"--user-data-dir={profile}", "--virtual-time-budget=30000", "--dump-dom", f"http://127.0.0.1:{port}/__checks.html"]
try:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    dom = r.stdout or ""
finally:
    httpd.shutdown()
    shutil.rmtree(profile, ignore_errors=True)

m = re.search(r'id="frontend-result">([^<]*)<', dom)
tail = (r.stderr or "").strip().splitlines()
result = m.group(1) if m else "no result in the page" + (f" ({tail[-1][:200]})" if tail else "")
check(m is not None, "the browser ran the page: " + result)
mm = re.match(r"frontend checks: (\d+) passed", result)
check(mm is not None, result)
n = int(mm.group(1))
check(n >= 230, f"all browser checks ran ({n})")
wjs = (STATIC / "widgets.js").read_text(encoding="utf-8")
check('caches.delete("obb-slow-v1")' in wjs and 'caches.open("obb-slow-v1")' not in wjs
      and "slowCacheGet" not in wjs and "slowCachePut" not in wjs,
      "W8: widgets.js retires obb-slow-v1 with one-time caches.delete cleanup")
idx_html = (STATIC / "index.html").read_text(encoding="utf-8")
check('<link rel="icon"' in idx_html, "index.html defines inline icon so browsers do not log 404 /favicon.ico")
# P4-06: one request wrapper, no raw error text in user-visible strings
src_js = {f.name: f.read_text(encoding="utf-8") for f in STATIC.glob("*.js")}
fetch_calls = [(name, i + 1) for name, txt in src_js.items() for i, line in enumerate(txt.splitlines())
               if re.search(r"(?<![\w.])fetch\(", line) and not line.lstrip().startswith(("*", "//", "/*"))]
check(fetch_calls == [("request.js", next(i + 1 for i, ln in enumerate(src_js["request.js"].splitlines()) if "return fetch(url, init)" in ln))],
      f"fetch( is called only inside T.request ({fetch_calls})")
leaks = [(name, i + 1) for name, txt in src_js.items() for i, line in enumerate(txt.splitlines())
         if re.search(r"\+\s*\(?\s*\w+\s*(?:&&\s*\w+\s*)?\.message|\.message\s*\+(?!\+)", line)]
check(not leaks, f"no user-visible string concatenates e.message ({leaks})")
check(idx_html.index("/static/request.js") < idx_html.index("/static/md.js"), "request.js loads before every script that uses T.request")
# count the browser checks as checks of this file
import _common  # noqa: E402
_common._count[0] += n - 1
done("test_frontend")
