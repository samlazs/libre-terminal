"""P3-06: viewing never changes the saved layout, across every tab of every built-in dashboard, in headless Edge.

The dashboards are replayed offline from the backend catalogs the last live build recorded (as test_generator does);
the saved state is a copy of the owner's state.json when there is one (read only; the run writes only to its temporary
local/ folder). Data requests answer 503 at once, so nothing leaves the machine. Skipped with a notice without a browser
or without recorded catalogs.
"""

import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

from _common import REAL_LOCAL, ROOT, TMP_LOCAL, check, done
from _browser import CAPTURE, REPORT, find_browser

cat = {b: REAL_LOCAL / "cache" / f"catalog-{b}.json" for b in ("v5", "v4")}
browser = find_browser()
if not browser or not all(p.exists() for p in cat.values()):
    print("test_viewing: skipped: " + ("no Chromium browser" if not browser else "no recorded catalogs yet (start the terminal once)"))
    print("test_viewing: 0 checks passed")
    sys.exit(0)

import contextlib  # noqa: E402
import io  # noqa: E402

import build_dashboards as bd  # noqa: E402
import local_paths  # noqa: E402
import server  # noqa: E402
import xwidgets  # noqa: E402

local_paths.ensure()
defs = {"x": xwidgets.widgets()}
for b, p in cat.items():
    defs[b] = json.loads(p.read_text(encoding="utf-8"))
bd.load = lambda: defs
with contextlib.redirect_stdout(io.StringIO()):
    bd.build()                                             # writes the temporary local/dashboards.json
real_state = REAL_LOCAL / "state" / "state.json"
if real_state.is_file():                                   # an isolated copy: the owner's file is only read
    (TMP_LOCAL / "state").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(real_state, TMP_LOCAL / "state" / "state.json")
state_copy = TMP_LOCAL / "state" / "state.json"

STATIC = ROOT / "terminal" / "static"
CHECKS = (ROOT / "scripts" / "tests" / "viewing_checks.js").read_text(encoding="utf-8")
page = (STATIC / "index.html").read_text(encoding="utf-8").replace(
    "</body>", '<script src="/__capture.js"></script><script src="/__checks.js"></script><script src="/__report.js"></script>\n</body>')
# Switching 120 tabs back to back lays the grid out many times in one frame; the browser then reports "ResizeObserver
# loop completed with undelivered notifications", a notice (the observers run next frame), not a page error.
BENIGN = """window.addEventListener("error", function (e) { if (/^ResizeObserver loop/.test(String(e.message))) e.stopImmediatePropagation(); }, true);
"""
SCRIPTS = {"/__capture.js": BENIGN + CAPTURE, "/__report.js": REPORT,
           "/__checks.js": "try {\n" + CHECKS + "\n} catch (e) { window.__fcError = String(e && e.message || e); }\n"}
NO_DATA = {"error": {"kind": "unavailable", "message": "No data in this check.", "retryable": False}, "detail": "No data in this check.", "sources": []}


class Handler(server.Handler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/__checks.html":
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        if path in SCRIPTS:
            return self._send(200, SCRIPTS[path].encode("utf-8"), "application/javascript; charset=utf-8")
        if path == "/api/config":
            return self._json(200, {"product": "Test", "budgets": server.contract.client_budgets(), "backends": [
                {"id": "x", "name": "Terminal views", "url": ""}, {"id": "v5", "name": "V5", "url": "/__v5"}, {"id": "v4", "name": "V4", "url": "/__v4"}]})
        m = re.match(r"^/__(v5|v4)/(widgets|apps)\.json$", path)
        if m:
            return self._json(200, defs[m.group(1)] if m.group(2) == "widgets" else [])
        if path.startswith(("/x/", "/__v5/", "/__v4/")):
            return self._json(503, NO_DATA)                 # no data: the check is about the layout, not the numbers
        return super().do_GET()


class Srv(server.ThreadingHTTPServer):
    daemon_threads = True


s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
httpd = Srv(("127.0.0.1", port), Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
profile = tempfile.mkdtemp(prefix="terminal-edge-")
cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
       f"--user-data-dir={profile}", "--virtual-time-budget=240000", "--dump-dom", f"http://127.0.0.1:{port}/__checks.html"]
try:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    dom = r.stdout or ""
finally:
    httpd.shutdown()
    shutil.rmtree(profile, ignore_errors=True)

m = re.search(r'id="frontend-result">([^<]*)<', dom)
tail = (r.stderr or "").strip().splitlines()
result = m.group(1) if m else "no result in the page" + (f" ({tail[-1][:200]})" if tail else "")
check(m is not None, "the browser ran the page: " + result)
notes = re.search(r'id="frontend-notes">([^<]*)<', dom)
if notes and notes.group(1):
    print("  " + notes.group(1))
mm = re.match(r"frontend checks: (\d+) passed", result)
check(mm is not None, result)
import _common  # noqa: E402
_common._count[0] += int(mm.group(1)) - 1
done("test_viewing")
