"""Static and headless-browser checks for the frozen interactive web preview under docs/.

Validates:
1. Relative asset paths, .nojekyll, static mirror byte-identity against terminal/static/*, pack & frame integrity,
   lean bundle size (< 8 MB), and zero keys/paths/username/restricted terms across docs/index.html & docs/demo/**.
2. Subpath HTTP hosting (/libre-terminal/) in headless Edge on desktop (1440x900) and mobile (390x844):
   every showcase dashboard & tab renders cleanly with interactive charts/tables/iframes, uncaptured
   dashboards/tickers show a clean demo_unavailable notice, and mobile collapses the sidebar with zero overflow.
"""

from __future__ import annotations

import getpass
import http.server
import json
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from _browser import CAPTURE, REPORT, find_browser
from _common import ROOT, check, done

import build_demo as bd  # noqa: E402
import project_check as pc  # noqa: E402
import redact  # noqa: E402

DOCS = ROOT / "docs"
DEMO = DOCS / "demo"
DATA = DEMO / "data"
FRAMES = DEMO / "frames"
STATIC_SRC = ROOT / "terminal" / "static"
STATIC_DST = DEMO / "static"

REQUIRED_PACKS = (
    "markets",
    "company-aapl",
    "company-jpm",
    "credit",
    "rates",
    "options",
    "energy",
    "trade",
)

# 1. Static assets, manifest, pack integrity & hygiene checks
index_path = DOCS / "index.html"
demo_js_path = DEMO / "demo.js"
demo_css_path = DEMO / "demo.css"
manifest_path = DATA / "manifest.json"

check(index_path.is_file(), "docs/index.html exists")
check((DOCS / ".nojekyll").is_file(), "docs/.nojekyll exists")
check(demo_js_path.is_file(), "docs/demo/demo.js exists")
check(demo_css_path.is_file(), "docs/demo/demo.css exists")
check(manifest_path.is_file(), "docs/demo/data/manifest.json exists")
check(not (DATA / "company-msft.json").exists(), "stale company-msft.json removed")
check(not (FRAMES / "sec-8k-msft.html").exists(), "stale sec-8k-msft.html removed")

index_txt = index_path.read_text(encoding="utf-8")
demo_js_txt = demo_js_path.read_text(encoding="utf-8")
check("./demo/static/app.css" in index_txt and "./demo/demo.js" in index_txt, "docs/index.html uses relative ./demo/ assets")
check(
    not re.search(r'(?:href|src)=["\']/(?:demo|static)/', index_txt),
    "docs/index.html has zero root-absolute /demo/ or /static/ paths",
)
check('DEMO_BASE = "./demo/data/"' in demo_js_txt, "demo.js uses relative ./demo/data/ base")

for name in bd.STATIC_FILES:
    src_p = STATIC_SRC / name
    dst_p = STATIC_DST / name
    check(dst_p.is_file(), f"docs/demo/static/{name} exists")
    src_norm = src_p.read_bytes().replace(b"\r\n", b"\n")
    dst_bytes = dst_p.read_bytes()
    check(dst_bytes == src_norm, f"docs/demo/static/{name} is byte-identical (LF) to terminal/static/{name}")

demo_total_bytes = sum(p.stat().st_size for p in DEMO.rglob("*") if p.is_file())
check(demo_total_bytes < 8_000_000, f"docs/demo total raw size < 8.0 MB ({demo_total_bytes / 1e6:.2f} MB)")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
snap_date = str(manifest.get("snapshot_date") or "")
check(bool(re.match(r"^\d{4}-\d{2}-\d{2}$", snap_date)), f"manifest snapshot_date valid ({snap_date!r})")
routes = manifest.get("routes") or {}
check(isinstance(routes, dict) and len(routes) >= 100, f"manifest has showcase routes ({len(routes)})")
check(manifest.get("fallback_routes") == {}, f"manifest has no silent fallback_routes ({manifest.get('fallback_routes')})")
check(isinstance(manifest.get("default_groups"), dict) and "d:credit" in manifest["default_groups"], "manifest has default_groups for Credit")
search_syms = {str(r.get("symbol") or "").upper() for r in (manifest.get("search_index") or []) if isinstance(r, dict)}
check({"AAPL", "JPM", "SPY"}.issubset(search_syms), f"manifest search_index includes showcase tickers ({len(search_syms)} total)")

pack_cache: dict[str, dict] = {}
for p_name in REQUIRED_PACKS:
    p_file = DATA / f"{p_name}.json"
    check(p_file.is_file(), f"showcase pack {p_name}.json exists")
    pack_obj = json.loads(p_file.read_text(encoding="utf-8"))
    pack_cache[f"{p_name}.json"] = pack_obj
    check(bool(pack_obj.get("entries")), f"showcase pack {p_name}.json has non-empty entries")

missing_keys = 0
missing_frames = []
for r_key, p_fname in routes.items():
    if p_fname not in pack_cache:
        p_file = DATA / p_fname
        pack_cache[p_fname] = json.loads(p_file.read_text(encoding="utf-8"))
    entries = pack_cache[p_fname].get("entries") or {}
    if r_key not in entries:
        missing_keys += 1
    elif r_key.startswith("GET /x/earnings_release?"):
        frm = (entries[r_key].get("body") or {}).get("frame") or ""
        if frm:
            rel_frm = DOCS / frm.removeprefix("./")
            if not rel_frm.is_file():
                missing_frames.append(frm)
check(missing_keys == 0, f"all manifest routes resolve inside their packs ({missing_keys} missing)")
check(not missing_frames, f"all earnings release frames exist ({missing_frames})")
check((FRAMES / "sec-8k-aapl.html").is_file() and (FRAMES / "sec-8k-jpm.html").is_file(), "AAPL and JPM SEC 8-K frames exist")

user = getpass.getuser()
user_rx = re.compile(rf"(?i)(?:\\|/){re.escape(user)}(?:\\|/|$)") if user and len(user) >= 3 else None
secrets = redact._key_values()
hygiene_errors: list[str] = []
scan_files = [index_path, demo_js_path, demo_css_path] + sorted(p for p in DEMO.rglob("*") if p.is_file())
for f in scan_files:
    rel = f.relative_to(ROOT).as_posix()
    raw = f.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        hygiene_errors.append(f"{rel}: BOM")
    if b"\r\n" in raw:
        hygiene_errors.append(f"{rel}: CRLF")
    txt = raw.decode("utf-8", errors="strict")
    if f.suffix == ".json":
        json.loads(txt)
    if pc._PATH_RX.search(txt) or (user_rx and user_rx.search(txt)):
        hygiene_errors.append(f"{rel}: path leak")
    if pc._AI_ATTR_RX.search(txt):
        hygiene_errors.append(f"{rel}: restricted term")
    if any(sec in txt for sec in secrets):
        hygiene_errors.append(f"{rel}: secret leak")
check(not hygiene_errors, f"demo privacy & hygiene scan clean ({hygiene_errors[:3]})")


# 2. Headless Edge subpath checks (/libre-terminal/)
browser = find_browser()
if not browser:
    print("test_demo: browser checks skipped: Microsoft Edge / Chrome not found")
    done("test_demo")
    sys.exit(0)

import plotly  # noqa: E402

LOCAL_PLOTLY = Path(plotly.__file__).resolve().parent / "package_data" / "plotly.min.js"
check(LOCAL_PLOTLY.is_file(), f"local plotly.min.js available for offline browser harness ({LOCAL_PLOTLY})")

BENIGN_RO = (
    'window.addEventListener("error", function (e) { '
    'if (/^ResizeObserver loop/.test(String(e.message))) e.stopImmediatePropagation(); }, true);\n'
)
TRAP_CONSOLE_ERR = (
    "(function () {\n"
    "  var origErr = console.error;\n"
    "  console.error = function () {\n"
    "    var s = Array.prototype.map.call(arguments, function (x) { return String(x); }).join(' ');\n"
    "    window.__fcError = window.__fcError || ('console.error: ' + s);\n"
    "    return origErr.apply(console, arguments);\n"
    "  };\n"
    "})();\n"
)

COMMON_HELPERS = r"""
  var n = 0;
  function ok(c, what) { if (!c) throw new Error("FAILED: " + what); n++; }
  function settle(ms) { return new Promise(function (r) { setTimeout(r, ms || 0); }); }
  async function waitBoot() {
    for (var i = 0; i < 400; i++) {
      var dt = (document.getElementById("demo-date") || {}).textContent || "";
      if (T.S && T.S.booted && dt && dt !== "loading…") break;
      await settle(50);
    }
    ok(!!(T.S && T.S.booted), "demo app booted");
  }
  async function revealAndWait() {
    var wrap = document.getElementById("canvas-wrap");
    for (var pass = 0; pass < 4; pass++) {
      if (wrap) {
        var h = Math.max(wrap.scrollHeight, 1200);
        for (var y = 0; y <= h; y += 350) {
          wrap.scrollTop = y;
          wrap.dispatchEvent(new Event("scroll"));
          await waitSettle(90);
        }
        wrap.scrollTop = h;
        wrap.dispatchEvent(new Event("scroll"));
        await waitSettle(120);
      }
      for (var w = 0; w < 140; w++) {
        if (!document.querySelector("#canvas .spin")) break;
        await waitSettle(50);
      }
      if (!document.querySelector("#canvas .spin")) break;
    }
    if (wrap) { wrap.scrollTop = 0; wrap.dispatchEvent(new Event("scroll")); }
  }
  async function waitSettle(ms) { return settle(ms); }
  function tabStats() {
    var errs = [].slice.call(document.querySelectorAll("#canvas .msg.err")).map(function (x) { return x.textContent; });
    var demos = [].slice.call(document.querySelectorAll("#canvas .msg:not(.err)")).map(function (x) { return x.textContent; });
    var spins = document.querySelectorAll("#canvas .spin").length;
    var plots = [].filter.call(document.querySelectorAll("#canvas .js-plotly-plot"), function (p) { return p.data && p.data.length > 0; }).length;
    var rows = document.querySelectorAll("#canvas table tbody tr").length;
    var frames = [].slice.call(document.querySelectorAll("#canvas iframe")).map(function (f) { return f.getAttribute("src") || ""; });
    return { errs: errs, demos: demos, spins: spins, plots: plots, rows: rows, frames: frames, cards: document.querySelectorAll("#canvas .w").length };
  }
"""

DESKTOP_CHECKS = r"""
window.__fcPromise = (async function (T) {
__HELPERS__
  await waitBoot();
  var bar = document.getElementById("demo-bar");
  ok(bar && bar.getBoundingClientRect().height >= 28, "demo bar visible");
  ok((document.getElementById("demo-date") || {}).textContent === "__SNAP_DATE__", "demo bar displays snapshot date");
  var dl = bar.querySelector('a[href*="Libre-Terminal-Windows.zip"]');
  var gh = bar.querySelector('a[href="https://github.com/samlazs/libre-terminal"]');
  ok(!!dl && !!gh, "demo bar includes Download for Windows and GitHub links");

  // 1. Markets Summary > Overview
  ok(T.openDashboard("d:markets", "overview"), "open Markets Summary > Overview");
  await revealAndWait();
  var st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0, "Markets Overview no errors/spinners: " + JSON.stringify(st.errs));
  ok(st.plots >= 2 && st.rows >= 50, "Markets Overview rendered charts (" + st.plots + ") and table rows (" + st.rows + ")");
  var sortTh = document.querySelector("#canvas table thead th");
  if (sortTh) { sortTh.click(); await settle(40); }
  ok(document.querySelectorAll("#canvas table tbody tr").length >= 10, "Markets table sortable click kept rows intact");

  // 2. Company (AAPL & JPM + SEC 8-K + ZZZZ unavailable)
  var compTabs = [
    { tab: "overview", minPlots: 1, minRows: 5 },
    { tab: "estimates", minPlots: 0, minRows: 5, frameEnd: "sec-8k-aapl.html" },
    { tab: "price-peers", minPlots: 1, minRows: 5 },
    { tab: "growth-ratios", minPlots: 1, minRows: 5 }
  ];
  for (var i = 0; i < compTabs.length; i++) {
    var ct = compTabs[i];
    ok(T.openDashboard("d:eq-company", ct.tab), "open Company > " + ct.tab);
    await revealAndWait();
    st = tabStats();
    ok(st.errs.length === 0 && st.spins === 0, "Company " + ct.tab + " AAPL clean: " + JSON.stringify(st.errs));
    ok(st.plots >= ct.minPlots && st.rows >= ct.minRows, "Company " + ct.tab + " AAPL plots=" + st.plots + " rows=" + st.rows);
    if (ct.frameEnd) {
      ok(st.frames.some(function (s) { return s.indexOf(ct.frameEnd) >= 0; }), "Company Estimates AAPL iframe loaded " + ct.frameEnd + " got " + JSON.stringify(st.frames));
    }
  }
  var symG = (T.S.cur.groups || []).filter(function (g) { return g.paramName === "symbol"; })[0];
  ok(!!symG, "Company has symbol group");
  T.setGroup(symG, "JPM");
  await revealAndWait();
  ok(T.openDashboard("d:eq-company", "estimates"), "switch to Company Estimates for JPM");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0, "Company Estimates JPM clean: " + JSON.stringify(st.errs));
  ok(st.frames.some(function (s) { return s.indexOf("sec-8k-jpm.html") >= 0; }), "Company Estimates JPM iframe loaded sec-8k-jpm.html");
  ok(T.openDashboard("d:eq-company", "overview"), "Company Overview JPM");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.plots >= 1 && st.rows >= 5, "Company Overview JPM clean plots=" + st.plots + " rows=" + st.rows);

  symG = (T.S.cur.groups || []).filter(function (g) { return g.paramName === "symbol"; })[0];
  ok(!!symG, "Company overview has fresh symbol group");
  T.setGroup(symG, "ZZZZ");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0, "Uncaptured ticker ZZZZ produces no hard error/spinner");
  ok(st.demos.some(function (txt) { return txt.indexOf("Not included in this frozen preview.") >= 0; }), "Uncaptured ticker ZZZZ shows clean demo notice");
  T.setGroup(symG, "AAPL");

  // 3. Credit > Bond Lookup (TRACE) & Spreads
  ok(T.openDashboard("d:credit", "bond-lookup-trace"), "open Credit > Bond Lookup");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 2 && st.rows >= 5, "Credit Bond Lookup rendered plots=" + st.plots + " rows=" + st.rows);
  ok(T.openDashboard("d:credit", "spreads"), "open Credit > Spreads");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 1, "Credit Spreads rendered plots=" + st.plots);

  // 4. Rates & Curves > Policy Expectations & By Country (US -> germany)
  ok(T.openDashboard("d:rates", "policy-expectations"), "open Rates > Policy Expectations");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 1, "Rates Policy Expectations rendered plots=" + st.plots);
  ok(T.openDashboard("d:rates", "by-country"), "open Rates > By Country");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 1, "Rates By Country US rendered plots=" + st.plots);
  var ctyG = (T.S.cur.groups || []).filter(function (g) { return g.paramName === "country"; })[0];
  ok(!!ctyG, "Rates By Country has country group");
  T.setGroup(ctyG, "germany");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 1, "Rates By Country germany rendered plots=" + st.plots);

  // 5. Equity Options > Volatility (AAPL & SPY: 4 charts each)
  ok(T.openDashboard("d:options", "volatility"), "open Equity Options > Volatility");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 4, "Options Volatility AAPL rendered 4 charts (" + st.plots + ")");
  var optSymG = (T.S.cur.groups || []).filter(function (g) { return g.paramName === "symbol"; })[0];
  T.setGroup(optSymG, "SPY");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 4, "Options Volatility SPY rendered 4 charts (" + st.plots + ")");

  // 6. Energy > Oil & Trade & Shipping > Chokepoints
  ok(T.openDashboard("d:energy", "oil"), "open Energy > Oil");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 3 && st.rows >= 5, "Energy Oil rendered plots=" + st.plots + " rows=" + st.rows);
  ok(T.openDashboard("d:trade", "chokepoints"), "open Trade & Shipping > Chokepoints");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 2 && st.rows >= 5, "Trade Chokepoints rendered plots=" + st.plots + " rows=" + st.rows);

  // 7. Uncaptured dashboard shows clean demo_unavailable notice; changed start_date does not return 2015 range
  ok(T.openDashboard("d:congress"), "open uncaptured dashboard U.S. Congress");
  await revealAndWait();
  st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0, "Uncaptured dashboard clean (no err/spinner)");
  ok(st.demos.some(function (txt) { return txt.indexOf("Not included in this frozen preview.") >= 0; }), "Uncaptured dashboard shows clean demo notice");
  var tpMiss = await T.request("https://demo.libre-terminal.local/api/v1/federal_reserve/sf/term_premium?maturity=10&provider=federal_reserve&start_date=2022-01-01");
  var tpBody = await tpMiss.json();
  ok(tpMiss.status === 404 && tpBody && tpBody.error && tpBody.error.kind === "demo_unavailable", "Changed start_date returns demo_unavailable instead of 2015 data");

  window.__fcNotes = ["desktop demo checks passed across all 6 showcase suites + uncaptured fallbacks"];
  console.log("frontend checks: " + n + " passed");
})(window.T);
"""

MOBILE_CHECKS = r"""
window.__fcPromise = (async function (T) {
__HELPERS__
  await waitBoot();
  var bar = document.getElementById("demo-bar");
  var sb = document.getElementById("sidebar");
  var ex = document.getElementById("btn-expand");
  ok(bar && bar.getBoundingClientRect().height >= 30, "mobile demo bar visible");
  ok(sb && sb.classList.contains("collapsed"), "mobile sidebar collapsed on load");
  ok(ex && !ex.classList.contains("hidden") && ex.getBoundingClientRect().width > 0, "mobile expand button visible");
  await revealAndWait();
  ok(document.documentElement.scrollWidth <= window.innerWidth + 2, "mobile initial screen no horizontal overflow (" + document.documentElement.scrollWidth + " <= " + window.innerWidth + ")");

  var jumpBtn = document.querySelector('#demo-bar .demo-jump[data-dash="d:options"]');
  ok(!!jumpBtn, "mobile Options Vol jump button present");
  jumpBtn.click();
  await revealAndWait();
  var st = tabStats();
  ok(st.errs.length === 0 && st.spins === 0 && st.plots >= 4, "mobile Options Vol rendered 4 charts cleanly (" + st.plots + ")");
  ok(document.documentElement.scrollWidth <= window.innerWidth + 2, "mobile Options Vol no horizontal overflow (" + document.documentElement.scrollWidth + " <= " + window.innerWidth + ")");

  window.__fcNotes = ["mobile demo checks passed (390x844, zero overflow)"];
  console.log("frontend checks: " + n + " passed");
})(window.T);
"""

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}

injected_html = index_txt.replace("https://cdn.plot.ly/plotly-4.1.1.min.js", "/_local_plotly.js").replace(
    "</body>",
    '<script src="/__capture.js"></script><script src="/__checks.js"></script><script src="/__report.js"></script>\n</body>',
)
plotly_bytes = LOCAL_PLOTLY.read_bytes()
http_failures: list[str] = []
current_mode = ["desktop"]


class DemoHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _reply(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        raw_path, _, query = self.path.partition("?")
        if raw_path == "/libre-terminal":
            self.send_response(301)
            self.send_header("Location", "/libre-terminal/")
            self.end_headers()
            return
        if raw_path in ("/libre-terminal/", "/libre-terminal/index.html"):
            if "mobile=1" in query:
                current_mode[0] = "mobile"
            return self._reply(200, injected_html.encode("utf-8"), "text/html; charset=utf-8")
        if raw_path == "/_local_plotly.js":
            return self._reply(200, plotly_bytes, "application/javascript; charset=utf-8")
        if raw_path == "/__capture.js":
            return self._reply(200, (BENIGN_RO + TRAP_CONSOLE_ERR + CAPTURE).encode("utf-8"), "application/javascript; charset=utf-8")
        if raw_path == "/__report.js":
            return self._reply(200, REPORT.encode("utf-8"), "application/javascript; charset=utf-8")
        if raw_path == "/__checks.js":
            tmpl = MOBILE_CHECKS if current_mode[0] == "mobile" else DESKTOP_CHECKS
            js_src = (
                tmpl.replace("__HELPERS__", COMMON_HELPERS)
                .replace("__SNAP_DATE__", snap_date)
            )
            wrapped = "try {\n" + js_src + "\n} catch (e) { window.__fcError = window.__fcError || String(e && e.message || e); }\n"
            return self._reply(200, wrapped.encode("utf-8"), "application/javascript; charset=utf-8")
        if raw_path.startswith("/libre-terminal/demo/"):
            rel = raw_path[len("/libre-terminal/demo/") :].lstrip("/")
            target = (DEMO / rel).resolve()
            if DEMO.resolve() in target.parents and target.is_file():
                ctype = MIME_TYPES.get(target.suffix.lower(), "application/octet-stream")
                return self._reply(200, target.read_bytes(), ctype)
        http_failures.append(f"404 {self.path}")
        self._reply(404, b"Not found", "text/plain; charset=utf-8")


class Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


sock = socket.socket()
sock.bind(("127.0.0.1", 0))
port = sock.getsockname()[1]
sock.close()
httpd = Srv(("127.0.0.1", port), DemoHandler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def _run_edge(url_suffix: str, width: int, height: int, budget_ms: int) -> tuple[int, list[str]]:
    profile = tempfile.mkdtemp(prefix="demo-edge-")
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        f"--window-size={width},{height}",
        f"--user-data-dir={profile}",
        f"--virtual-time-budget={budget_ms}",
        "--dump-dom",
        f"http://127.0.0.1:{port}/libre-terminal/{url_suffix}",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        dom = r.stdout or ""
        tail = (r.stderr or "").strip().splitlines()
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    m = re.search(r'id="frontend-result">([^<]*)<', dom)
    res_txt = m.group(1) if m else "no result in page" + (f" ({tail[-1][:200]})" if tail else "")
    check(m is not None, f"headless Edge ({width}x{height}) ran demo: {res_txt}")
    mm = re.match(r"frontend checks: (\d+) passed", res_txt)
    check(mm is not None, f"headless Edge ({width}x{height}) passed: {res_txt}")
    notes_m = re.search(r'id="frontend-notes">([^<]*)<', dom)
    notes = [notes_m.group(1)] if notes_m and notes_m.group(1) else []
    return int(mm.group(1)), notes


try:
    current_mode[0] = "desktop"
    n_desk, desk_notes = _run_edge("", 1440, 900, 60000)
    current_mode[0] = "mobile"
    n_mob, mob_notes = _run_edge("?mobile=1", 390, 844, 30000)
finally:
    httpd.shutdown()

check(not http_failures, f"zero 404/unexpected static asset requests ({http_failures})")
if desk_notes or mob_notes:
    print("  " + " | ".join(desk_notes + mob_notes))

import _common  # noqa: E402

_common._count[0] += n_desk + n_mob - 4
done("test_demo")
