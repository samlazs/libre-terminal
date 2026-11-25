"""Launcher restart policy (H4), stop only our own processes (M5), UI server guard and state checks (H3, M8, M9)."""

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
import urllib.parse

from _common import ROOT, check, done, make_tmp_dir

import launcher
import local_paths

# ---- H4: restart after any abnormal exit, capped
crashes = []
again, delay, why = launcher.restart_decision(1, crashes, 1000.0)
check(again and delay == 2 and "code 1" in why, "exit code 1 is restarted")
check(launcher.restart_decision(75, crashes, 1010.0)[0] and "watchdog" in launcher.restart_decision(75, [], 0.0)[2], "frozen (75) restarted")
check(not launcher.restart_decision(0, [], 0.0)[0], "a normal stop is not restarted")
crashes = []
results = [launcher.restart_decision(1, crashes, 100.0 + i)[0] for i in range(5)]
check(results == [True, True, True, True, False], "gives up at the 5th crash within 10 minutes")
crashes = []
results = [launcher.restart_decision(1, crashes, 100.0 + i * 700)[0] for i in range(8)]
check(all(results), "crashes far apart keep being restarted")

# ---- M5: only processes of this terminal are stopped
root = str(launcher.ROOT)
check(launcher.ours(f'"{root}\\.venv-v5\\Scripts\\python.exe" "{root}\\scripts\\run_backend.py" v5 --port 6900'), "our backend")
check(launcher.ours(f'"{root}\\.venv-v5\\Scripts\\python.exe" {root}\\terminal\\server.py --port 6950'), "our UI")
check(not launcher.ours('"C:\\Program Files\\Other\\server.exe" --port 6900'), "another program is not ours")
check(not launcher.ours('python D:\\elsewhere\\terminal\\server.py'), "a copy in another folder is not ours")
src = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
check("if ours(cmdline):" in src and "not stopped" in src, "stop checks the command line before killing a listener")

# ---- H3 / M8 / M9: the UI server, on a free port, with a temporary local/ folder
import server  # noqa: E402

check(server.validate_state({"v": 2, "dashboards": {}, "_rev": 3}) is None, "valid state accepted")
check(server.validate_state({"dashboards": []}) is not None, "dashboards must be an object")
check(server.validate_state({"watchlists": [{"symbols": "AAPL"}]}) is not None, "watchlist symbols must be a list")
check(server.validate_state({"dashboards": {"d:x": {"tabs": [{"items": [1]}]}}}) is not None, "cards must be objects")
check(server.validate_state({"_rev": True}) is not None, "bool is not a revision")
check(server.validate_state({"v": 4, "dashboards": {"d:x": {"id": "d:x", "mode": "overrides", "overrides": {"t1": {"name": "Mine", "cards": {"t1-0": {"h": 12}}, "hidden": ["t1-1"], "added": [{"uid": "w1"}]}}, "hiddenTabs": ["t2"], "tabs": []}}}) is None, "v4 override dashboard accepted")
check(server.validate_state({"dashboards": {"d:x": {"mode": "bad"}}}) is not None, "unknown dashboard mode refused")
check(server.validate_state({"dashboards": {"d:x": {"mode": "overrides", "overrides": []}}}) is not None, "overrides must be an object")
check(server.validate_state({"dashboards": {"d:x": {"mode": "overrides", "overrides": {"t1": {"cards": {"c": "bad"}}}}}}) is not None, "override card entry must be an object")
check(server.validate_state({"dashboards": {"d:x": {"mode": "overrides", "overrides": {"t1": {"hidden": [1]}}}}}) is not None, "override hidden list must be strings")
check(server.validate_state({"dashboards": {"d:x": {"mode": "overrides", "overrides": {"t1": {"added": [1]}}}}}) is not None, "override added cards must be objects")
check(server.validate_state({"dashboards": {"d:x": {"mode": "overrides", "hiddenTabs": [1]}}}) is not None, "hiddenTabs must be strings")
owner = json.loads((ROOT / "local" / "state" / "state.json").read_text(encoding="utf-8")) if (ROOT / "local" / "state" / "state.json").exists() else {}
check(server.validate_state(owner) is None, "the owner's current state passes (read only)")

s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
local_paths.ensure()


class Srv(server.ThreadingHTTPServer):
    daemon_threads = True


httpd = Srv(("127.0.0.1", port), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def req(method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r.status, r.read()


check(req("GET", "/api/state", {"Host": f"rebind.example:{port}"})[0] == 403, "rebinding Host refused")
check(req("GET", "/api/state", {"Host": f"localhost:{port}"})[0] == 200, "localhost accepted")
check(req("GET", "/api/state", {"Sec-Fetch-Site": "cross-site"})[0] == 403, "cross-site read of the state refused")
check(req("GET", "/x/board?preset=fx", {"Origin": "http://evil.example"})[0] == 403, "cross-origin view refused")
check(req("PUT", "/api/state", {"Origin": "http://evil.example", "Content-Type": "application/json"}, b"{}")[0] == 403, "cross-origin write refused")
check(req("OPTIONS", "/api/state")[0] == 405, "no preflight")
check(req("PUT", "/api/state", {"Content-Type": "application/json"}, b'{"dashboards": []}')[0] == 400, "malformed state refused")
check(req("PUT", "/api/state", {"Content-Type": "text/plain"}, b"{}")[0] == 415, "state must be JSON")
st, body = req("PUT", "/api/state", {"Content-Type": "application/json"}, json.dumps({"v": 2, "dashboards": {}}).encode())
check(st == 200 and json.loads(body)["rev"] == 1, "first save")
st, body = req("PUT", "/api/state", {"Content-Type": "application/json"}, json.dumps({"v": 2, "dashboards": {}, "_rev": 0}).encode())
check(st == 409, "stale save refused with 409")
st, body = req("GET", "/x/macro_chart?preset=nope")
check(st == 502 and json.loads(body).get("user_message") is True, "view errors are marked as written for the user")


# ---- audit A12: a ValueError/TypeError inside a view is a logged internal error; only a call mismatch is "bad parameters"
def _t_boom(**_):
    raise ValueError("bad date from upstream")


def _t_strict(a="1"):
    return {"results": [], "sources": [{"name": "Terminal", "detail": "test", "url": ""}]}


def _frame_ok(**_):
    return {"_frame": "/sec/filing_viewer_app?document_url=https%3A%2F%2Fwww.sec.gov%2Fx.htm", "sources": [{"name": "SEC", "detail": "t", "url": ""}]}


def _frame_bad(**_):
    return {"_frame": "https://evil.example/phish", "_message": "<b>No release</b> for X", "sources": [{"name": "SEC", "detail": "t", "url": ""}]}


server.composite.ROUTES.update(t_frame_ok=_frame_ok, t_frame_bad=_frame_bad)
try:
    c0 = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c0.request("GET", "/x/t_frame_ok", headers={"Host": f"localhost:{port}"})
    r0 = c0.getresponse()
    j0 = json.loads(r0.read() or b"{}")
    check(r0.status == 200 and j0.get("frame") == f"http://localhost:{server.V5_PORT}/api/v1/sec/filing_viewer_app?document_url=https%3A%2F%2Fwww.sec.gov%2Fx.htm"
          and j0.get("sources") and r0.getheader("Location") is None,
          "a frame card gets the backend's SEC viewer URL on the page's own host, as JSON (no redirect: Firefox calls it cross-site)")
    st, body = req("GET", "/x/t_frame_bad")
    jb = json.loads(body)
    check(st == 200 and "frame" not in jb and b"evil.example" not in body and jb.get("message") == "<b>No release</b> for X",
          "any other target is never answered; the message is text (the card renders it as text)")
finally:
    for k in ("t_frame_ok", "t_frame_bad"):
        server.composite.ROUTES.pop(k, None)

server.composite.ROUTES.update(t_boom=_t_boom, t_strict=_t_strict)
lines, real_log = [], server.log
server.log = lines.append                      # the launcher sends the server's log lines to local/logs/ui.log
try:
    st, body = req("GET", "/x/t_boom")
    check(st == 500 and json.loads(body).get("ref"), "a ValueError inside a view: 500 with a reference, not 'bad parameters'")
    logged = "\n".join(lines)
    check("bad date from upstream" in logged and json.loads(body)["ref"] in logged and "Traceback" in logged, "and it is logged with its reference and traceback")
    st, body = req("GET", "/x/t_strict?nosuch=1")
    check(st == 400 and "Bad parameters" in json.loads(body)["detail"], "a parameter the view does not take: 400")
    check(req("GET", "/x/t_strict?a=2")[0] == 200, "a parameter it takes: 200")
finally:
    server.log = real_log
    for k in ("t_boom", "t_strict"):
        server.composite.ROUTES.pop(k, None)

# ---- audit 2026-11-16: an unreadable state.json is never overwritten; restored from the newest valid daily copy
def full(method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": f"127.0.0.1:{port}"}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


sd = local_paths.STATE_DIR
for f in sd.glob("state*"):
    f.unlink()
local_paths.STATE_FILE.write_text('{"v": 2, "dashboards": {', encoding="utf-8")          # truncated file, no backups
st, hs, body = full("GET", "/api/state")
check(st == 503 and json.loads(body).get("state_unreadable") is True, "unreadable state without a backup: explicit error, not {}")
st, _, body = full("PUT", "/api/state", {"Content-Type": "application/json"}, json.dumps({"v": 2, "dashboards": {}}).encode())
check(st == 503 and local_paths.STATE_FILE.read_text(encoding="utf-8") == '{"v": 2, "dashboards": {', "a save never overwrites an unreadable file")
check(not list(sd.glob("state.*.bak.json")), "no daily backup is made of a damaged file")
(sd / "state.20260101.bak.json").write_text(json.dumps({"v": 2, "_rev": 4, "dashboards": {}, "watchlists": [{"name": "old", "symbols": ["A"]}]}), encoding="utf-8")
(sd / "state.20260102.bak.json").write_text(json.dumps({"v": 2, "_rev": 7, "dashboards": {}, "watchlists": [{"name": "new", "symbols": ["B"]}]}), encoding="utf-8")
(sd / "state.20260103.bak.json").write_text("not json", encoding="utf-8")                  # newest copy damaged too: skipped
st, hs, body = full("GET", "/api/state")
restored = json.loads(body)
check(st == 200 and restored.get("_rev") == 7 and restored["watchlists"][0]["name"] == "new", "restored from the newest valid daily copy")
check("X-State-Notice" in hs and "20260102" in urllib.parse.unquote(hs["X-State-Notice"]), "the page is told what was restored")
aside = list(sd.glob("state.corrupt-*.json"))
check(len(aside) == 1 and aside[0].read_text(encoding="utf-8") == '{"v": 2, "dashboards": {', "the damaged file is kept aside, unchanged")
st, _, body = full("PUT", "/api/state", {"Content-Type": "application/json"}, json.dumps({"v": 2, "dashboards": {}, "_rev": 7}).encode())
check(st == 200 and json.loads(body)["rev"] == 8, "saving works again after the restore")
check(not list(sd.glob("*.tmp")), "atomic write leaves no temporary file")
# audit C7: daily copies pruned to the newest 30; other files in the folder untouched
for _d in range(1, 36):
    (sd / f"state.202512{_d:02d}.bak.json" if _d <= 31 else sd / f"state.2026020{_d - 31}.bak.json").write_text("{}", encoding="utf-8")
(sd / "state.session-x.bak.json").write_text("{}", encoding="utf-8")
_gone = server.prune_backups()
_left = sorted(p.name for p in sd.glob("state.[0-9]*.bak.json"))
check(len(_left) == 30 and "state.20260204.bak.json" in _left and "state.20251201.bak.json" in _gone
      and (sd / "state.session-x.bak.json").exists(), f"backups pruned to 30 newest ({len(_left)} left, removed {_gone}, newest {_left[-1]})")
st, hs, body = full("GET", "/api/state")
st2, _, _ = full("GET", "/api/state", {"If-None-Match": hs.get("ETag", "")})
check(st == 200 and hs.get("ETag") and st2 == 304, "state answers 304 when unchanged")
for f in sd.glob("state*"):
    f.unlink()

# dashboards: 503 with a reason when missing or damaged; ETag / 304; re-read only when the file changes
st, _, body = full("GET", "/api/dashboards")
check(st == 503 and "not built yet" in json.loads(body)["detail"], "missing dashboards.json: 503 with a reason")
local_paths.DASHBOARDS_FILE.write_text("{broken", encoding="utf-8")
st, _, body = full("GET", "/api/dashboards")
check(st == 503 and "could not be read" in json.loads(body)["detail"], "damaged dashboards.json: 503, not an empty layout")
local_paths.DASHBOARDS_FILE.write_text(json.dumps({"sections": [], "dashboards": [{"id": "d:a"}]}), encoding="utf-8")
st, hs, body = full("GET", "/api/dashboards")
check(st == 200 and json.loads(body)["dashboards"][0]["id"] == "d:a" and hs.get("ETag"), "dashboards served with an ETag")
st, _, body = full("GET", "/api/dashboards", {"If-None-Match": hs["ETag"]})
check(st == 304 and body == b"", "unchanged dashboards: 304")
time.sleep(0.05)
local_paths.DASHBOARDS_FILE.write_text(json.dumps({"sections": [], "dashboards": [{"id": "d:b"}]}), encoding="utf-8")
st, _, body = full("GET", "/api/dashboards", {"If-None-Match": hs["ETag"]})
check(st == 200 and json.loads(body)["dashboards"][0]["id"] == "d:b", "a rebuilt file is served at once")

# origin compared without case / spaces; CSP on the page; backend URLs on the page's own host name; services
check(req("GET", "/x/macro_chart?preset=nope", {"Origin": f"HTTP://127.0.0.1:{port}"})[0] == 502, "origin compared case-insensitively")
check(req("GET", "/x/macro_chart?preset=nope", {"Origin": f"http://127.0.0.1:{port + 1}"})[0] == 403, "another port is another origin")
st, hs, _ = full("GET", "/")
csp = hs.get("Content-Security-Policy", "")
check("script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0] and "object-src 'none'" in csp
      and f"http://127.0.0.1:{server.V5_PORT}" in csp and "frame-src" in csp and "blob:" in csp, "restrictive CSP that still allows the backends and frames")
cfg = json.loads(req("GET", "/api/config", {"Host": f"localhost:{port}"})[1])
check(cfg["backends"][0]["url"].startswith("http://localhost:"), "page on localhost calls the backends on localhost (same-site)")
check(json.loads(req("GET", "/api/config")[1])["backends"][0]["url"].startswith("http://127.0.0.1:"), "page on 127.0.0.1 calls 127.0.0.1")
(local_paths.RUN_DIR / "v4.stopped.json").write_text(json.dumps({"why": "5 crashes within 10 minutes"}), encoding="utf-8")
(local_paths.RUN_DIR / "v5.json").write_text("{}", encoding="utf-8")
svc = json.loads(req("GET", "/api/services")[1])
check(svc["v5"]["state"] == "running" and svc["v4"]["state"] == "stopped" and "5 crashes" in svc["v4"]["why"], "service status for the page")

# keys: apply on start when api_keys.env is newer than the last apply; corrupt settings never overwritten; companions removed
import apply_keys  # noqa: E402
home = make_tmp_dir("fake-home-")
settings = home / ".openbb_platform" / "user_settings.json"
env = local_paths.KEYS_FILE
env.write_text("FRED_API_KEY=abc\nTRADIER_API_KEY=t1\nTRADIER_ACCOUNT_TYPE=sandbox\n", encoding="utf-8")
check(launcher.keys_need_apply(), "never applied: apply on start")
check(apply_keys.apply(env, settings, quiet=True) == 0, "keys applied")
creds = json.loads(settings.read_text(encoding="utf-8"))["credentials"]
check(creds == {"fred_api_key": "abc", "tradier_api_key": "t1", "tradier_account_type": "sandbox"}, "keys written")
check(not launcher.keys_need_apply(), "nothing to do while the file is unchanged")
time.sleep(0.05)
env.write_text("FRED_API_KEY=abc\nTRADIER_API_KEY=REMOVE\n", encoding="utf-8")
os.utime(env, (time.time() + 5, time.time() + 5))
check(launcher.keys_need_apply(), "edited file: apply on the next start")
apply_keys.apply(env, settings, quiet=True)
creds = json.loads(settings.read_text(encoding="utf-8"))["credentials"]
check(creds == {"fred_api_key": "abc"}, "removing a key removes its companion settings too")
settings.write_text('{"credentials": {"fred_api_key": "abc"', encoding="utf-8")
check(apply_keys.apply(env, settings, quiet=True) == 2 and settings.read_text(encoding="utf-8") == '{"credentials": {"fred_api_key": "abc"', "damaged user_settings.json is never overwritten")
check(not list(settings.parent.glob("*.tmp")), "no temporary file left")
lsrc = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
check("apply_keys_if_changed(" in lsrc.split("def cmd_start")[1][:600], "start applies changed keys before the backends start")
check("return 0\n    py = venv_python" not in lsrc, "keys without an editor still applies the file")
check('stopped_file.write_text' in lsrc, "a supervisor that gives up leaves a note for the page")

# M9: a client that disconnects mid-answer leaves no traceback
c = socket.create_connection(("127.0.0.1", port))
c.sendall(f"GET /static/app.js HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
c.close()
time.sleep(0.3)
check(req("GET", "/api/config")[0] == 200, "server still answers after an aborted request")

# W8: size-bounded cache eviction keeps protected history/catalog files and evicts oldest non-protected entries
cd = local_paths.CACHE_DIR
cd.mkdir(parents=True, exist_ok=True)
t_base = time.time()
prot = cd / "curvestore_us.json"
prot.write_bytes(b"p" * 600)
os.utime(prot, (t_base - 40 * 86400, t_base - 40 * 86400))       # older than 30d but protected
old_evict = cd / "h1d_OLD.json"
old_evict.write_bytes(b"a" * 700)
os.utime(old_evict, (t_base - 2 * 86400, t_base - 2 * 86400))
new_keep = cd / "h1d_NEW.json"
new_keep.write_bytes(b"b" * 700)
os.utime(new_keep, (t_base - 60, t_base - 60))
# cap just under 0.0014 MB (~1468 B) -> total ~2000 B -> must evict old_evict (700 B) to 1300 B, keeping prot & new_keep
evicted = launcher.prune_local(now=t_base, max_cache_mb=1400 / (1024 * 1024))
check(evicted == 1 and prot.exists() and not old_evict.exists() and new_keep.exists(), "prune_local evicts oldest non-protected cache entry under size cap while preserving protected stores")
for f in (prot, new_keep):
    f.unlink(missing_ok=True)

# B9: daily background timer in server.py runs local_paths.prune_local() safely and stops cleanly
check(local_paths.prune_local is launcher.prune_local and server.PRUNE_INTERVAL_S == 86400,
      "B9: prune_local lives in local_paths and server PRUNE_INTERVAL_S is 24h")
b9_prot = cd / "curvestore_eu.json"
b9_old = cd / "h1d_B9_OLD.json"
b9_prot.write_bytes(b"p" * 100)
b9_old.write_bytes(b"x" * 100)
os.utime(b9_prot, (t_base - 45 * 86400, t_base - 45 * 86400))
os.utime(b9_old, (t_base - 45 * 86400, t_base - 45 * 86400))
b9_calls = [0]
b9_done = threading.Event()


def _b9_tick():
    b9_calls[0] += 1
    if b9_calls[0] == 1:
        raise RuntimeError("transient prune error")
    gone = local_paths.prune_local(now=t_base)
    if b9_calls[0] >= 2:
        b9_done.set()
    return gone


b9_thread, b9_stop = server.start_prune_timer(interval_s=0.02, prune_fn=_b9_tick)
try:
    check(b9_thread.daemon and b9_done.wait(timeout=2.0), "B9: prune timer runs in daemon thread and survives exceptions")
    check(b9_prot.exists() and not b9_old.exists(), "B9: periodic prune removes expired cache file while keeping protected store")
finally:
    b9_stop.set()
    b9_thread.join(timeout=2.0)
    b9_prot.unlink(missing_ok=True)
    b9_old.unlink(missing_ok=True)
check(not b9_thread.is_alive(), "B9: prune timer exits cleanly on stop")

# W3: V5-only default, --with-v4 opt-in, --skip-v4 backward-compatible alias, and server config_for()
check(not launcher.want_v4(types.SimpleNamespace()), "want_v4 defaults to False (single-backend V5)")
check(launcher.want_v4(types.SimpleNamespace(with_v4=True, skip_v4=False)), "want_v4 is True with --with-v4")
check(not launcher.want_v4(types.SimpleNamespace(with_v4=False, skip_v4=True)), "want_v4 is False with --skip-v4")
check(not launcher.want_v4(types.SimpleNamespace(with_v4=True, skip_v4=True)), "--skip-v4 overrides --with-v4")

for sub in ("install", "start", "restart", "health"):
    h_out = subprocess.run([sys.executable, str(ROOT / "scripts" / "launcher.py"), sub, "--help"], capture_output=True, text=True, timeout=15)
    check(h_out.returncode == 0 and "--with-v4" in h_out.stdout and "--skip-v4" in h_out.stdout, f"launcher.py {sub} --help exposes --with-v4 and --skip-v4")
smoke_h = subprocess.run([sys.executable, str(ROOT / "scripts" / "launcher.py"), "smoke", "--help"], capture_output=True, text=True, timeout=15)
check(smoke_h.returncode == 0 and "defaults to v5" in smoke_h.stdout, "launcher.py smoke defaults to v5")

(local_paths.RUN_DIR / "v4.json").unlink(missing_ok=True)
(local_paths.RUN_DIR / "v4.stopped.json").unlink(missing_ok=True)
saved_env_v4 = os.environ.pop("TERMINAL_WITH_V4", None)
try:
    check([b["id"] for b in server.config_for("127.0.0.1")["backends"]] == ["v5", "x"], "config_for defaults to V5 + terminal views only")
    (local_paths.RUN_DIR / "v4.json").write_text("{}", encoding="utf-8")
    check([b["id"] for b in server.config_for("127.0.0.1")["backends"]] == ["v5", "v4", "x"], "config_for includes v4 when v4.json exists")
    (local_paths.RUN_DIR / "v4.json").unlink(missing_ok=True)
    os.environ["TERMINAL_WITH_V4"] = "1"
    check([b["id"] for b in server.config_for("127.0.0.1")["backends"]] == ["v5", "v4", "x"], "config_for includes v4 when TERMINAL_WITH_V4=1")
    os.environ["TERMINAL_WITH_V4"] = "0"
    (local_paths.RUN_DIR / "v4.stopped.json").write_text("{}", encoding="utf-8")
    check([b["id"] for b in server.config_for("127.0.0.1")["backends"]] == ["v5", "x"], "TERMINAL_WITH_V4=0 keeps v4 hidden")
finally:
    (local_paths.RUN_DIR / "v4.json").unlink(missing_ok=True)
    (local_paths.RUN_DIR / "v4.stopped.json").unlink(missing_ok=True)
    if saved_env_v4 is None:
        os.environ.pop("TERMINAL_WITH_V4", None)
    else:
        os.environ["TERMINAL_WITH_V4"] = saved_env_v4

# Phase 3B: Libre Terminal branding, /api/config product name, verbatim AGPL-3.0 LICENSE, and NOTICE
check(server.PRODUCT_NAME == "Libre Terminal", "PRODUCT_NAME is Libre Terminal")
st_cfg, body_cfg = req("GET", "/api/config")
cfg_json = json.loads(body_cfg)
check(st_cfg == 200 and cfg_json.get("product") == "Libre Terminal", "/api/config reports Libre Terminal")

lic_raw = (ROOT / "LICENSE").read_bytes()
lic_txt = lic_raw.decode("utf-8")
check(not lic_raw.startswith(b"\xef\xbb\xbf") and b"\r" not in lic_raw, "LICENSE is UTF-8 without BOM and pure LF")
check(lic_txt.lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE")
      and "Version 3, 19 November 2007" in lic_txt and "END OF TERMS AND CONDITIONS" in lic_txt,
      "LICENSE holds verbatim GNU AGPL v3.0 text for SPDX detection")
check("RELEASE " + "BLOCKER" not in lic_txt and "Open " + "Terminal" not in lic_txt, "LICENSE has no placeholder or old name")

not_raw = (ROOT / "NOTICE").read_bytes()
not_txt = not_raw.decode("utf-8")
check(not not_raw.startswith(b"\xef\xbb\xbf") and b"\r" not in not_raw, "NOTICE is UTF-8 without BOM and pure LF")
check("Libre Terminal" in not_txt and "SPDX-License-Identifier: AGPL-3.0-or-later" in not_txt
      and "Open " + "Terminal" not in not_txt,
      "NOTICE uses Libre Terminal with SPDX identifier and has no old name")


def _test_warm_and_install():
    import http.server as _hs
    import importlib as _imp
    import inspect as _ins
    import subprocess as _sp
    import sys as _sys
    import threading as _th

    for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "terminal")):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    import composite  # noqa: E402
    import launcher  # noqa: E402

    _cw = _imp.import_module("composite.warm")

    _st_warm, _body_warm = req("GET", "/api/warm")
    _w0 = json.loads(_body_warm)
    check(
        _st_warm == 200
        and isinstance(_w0, dict)
        and {"phase", "landing_ready", "done", "total", "bg_done", "bg_total"} <= set(_w0),
        "GET /api/warm returns structured progress JSON",
    )

    _calls = []
    _orig = {}
    _stubs = {
        "history": lambda syms, *_a, **_kw: _calls.append(("history", len(syms))) or {},
        "watchlist": lambda *_a, **_kw: _calls.append(("watchlist",)) or [],
        "board": lambda preset="us_equity", *_a, **_kw: _calls.append(("board", preset)) or {},
        "performance": lambda syms="", *_a, **_kw: _calls.append(("performance", syms)) or {},
        "style_box": lambda *_a, **_kw: _calls.append(("style_box",)) or {},
        "news_view": lambda **_kw: _calls.append(("news",)) or {},
        "global_yields": lambda *_a, **_kw: _calls.append(("global_yields",)) or {},
        "policy_rates": lambda *_a, **_kw: _calls.append(("policy_rates",)) or {},
        "yield_curves": lambda *_a, **_kw: _calls.append(("yield_curves",)) or {},
        "_gpr": lambda *_a, **_kw: _calls.append(("gpr",)) or {},
        "_chokepoints": lambda *_a, **_kw: _calls.append(("chokepoints",)) or {},
        "_eia_key": lambda: "",
        "_eia_one": lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("EIA fetched without key")),
        "http_json": lambda url, *_a, **_kw: _calls.append(("http_json", url)) or {"ok": True, "bars": 250},
    }
    for _k, _v in _stubs.items():
        _orig[_k] = getattr(composite, _k)
        setattr(composite, _k, _v)
    try:
        _cw._reset_warm_state()
        _t1 = _cw.warm()
        _t2 = _cw.warm()
        check(_t1 is _t2 and _t1 is not None, "composite.warm() is idempotent and returns active thread")
        _t1.join(timeout=10)
        _st = _cw.warm_status()
        check(
            _st["phase"] == "ready"
            and _st["landing_ready"] is True
            and _st["done"] == _st["total"] == 4
            and _st["bg_done"] == _st["bg_total"] == 4
            and all(s["state"] == "done" for s in _st["steps"]),
            "composite.warm() finishes landing + background phases cleanly",
        )
        _calls.clear()
        _t3 = _cw.warm(force=True)
        check(_t3 is not None and _t3 is not _t1, "composite.warm(force=True) starts a fresh warm pass after prior warm finished")
        _t3.join(timeout=10)
        _st2 = _cw.warm_status()
        check(
            _st2["phase"] == "ready"
            and _st2["done"] == _st2["total"] == 4
            and _st2["bg_done"] == _st2["bg_total"] == 4
            and any(c[0] == "http_json" and "/_terminal/warm_chart?symbol=%5EGSPC" in c[1] for c in _calls),
            "composite.warm(force=True) actually re-runs landing + background warming",
        )
        check(
            any(c[0] == "http_json" and "/_terminal/warm_chart?symbol=%5EGSPC" in c[1] for c in _calls)
            and any(c[0] == "performance" for c in _calls)
            and any(c[0] == "style_box" for c in _calls),
            "composite.warm() pre-warms TradingView chart, performance, and style_box",
        )
    finally:
        for _k, _v in _orig.items():
            setattr(composite, _k, _v)
        _cw._reset_warm_state()

    _responses = [
        {"phase": "landing", "landing_ready": False, "done": 2, "total": 4, "current": "Market news"},
        {"phase": "background", "landing_ready": True, "done": 4, "total": 4, "current": ""},
    ]
    _idx = {"n": 0}

    class _MockWarmHandler(_hs.BaseHTTPRequestHandler):
        def do_GET(self):
            i = min(_idx["n"], len(_responses) - 1)
            _idx["n"] += 1
            body = json.dumps(_responses[i]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_a):
            pass

    _ms = _hs.ThreadingHTTPServer(("127.0.0.1", 0), _MockWarmHandler)
    _th.Thread(target=_ms.serve_forever, daemon=True).start()
    try:
        _res_ok = launcher.wait_for_warm(_ms.server_address[1], timeout=5, poll=0.02)
        check(bool(_res_ok and _res_ok.get("landing_ready") is True), "wait_for_warm waits until landing_ready transitions True")
        _responses[:] = [{"phase": "landing", "landing_ready": False, "done": 0, "total": 4, "current": "Charts"}]
        _idx["n"] = 0
        _res_to = launcher.wait_for_warm(_ms.server_address[1], timeout=0.12, poll=0.03)
        check(bool(_res_to and _res_to.get("landing_ready") is False), "wait_for_warm exits cleanly when timeout expires")
    finally:
        _ms.shutdown()
        _ms.server_close()

    _hp = _sp.run([_sys.executable, str(ROOT / "scripts" / "launcher.py"), "start", "--help"], capture_output=True, text=True, timeout=15)
    check(_hp.returncode == 0 and "--no-wait" in _hp.stdout, "launcher.py start --help exposes --no-wait")
    check("--compile-bytecode" in _ins.getsource(launcher.cmd_install), "cmd_install pre-compiles venv bytecode with --compile-bytecode")


_test_warm_and_install()

# ---- v0.6.0 compression & static asset ETag / 304
import gzip as _gz  # noqa: E402
st_u, hs_u, b_u = full("GET", "/static/app.js")
check(st_u == 200 and hs_u.get("ETag") and not hs_u.get("Content-Encoding") and b"function openHelp" in b_u,
      "static file serves uncompressed with ETag when Accept-Encoding has no gzip")
st_g, hs_g, b_g = full("GET", "/static/app.js", {"Accept-Encoding": "gzip, deflate"})
check(st_g == 200 and hs_g.get("Content-Encoding") == "gzip" and "Accept-Encoding" in (hs_g.get("Vary") or "")
      and hs_g.get("ETag") and hs_g["ETag"] != hs_u["ETag"] and hs_g["ETag"].endswith('-gz"')
      and len(b_g) < len(b_u) and _gz.decompress(b_g) == b_u,
      "static file gzip-compresses cleanly with a distinct -gz ETag when Accept-Encoding includes gzip")
st_304, hs_304, b_304 = full("GET", "/static/app.js", {"If-None-Match": hs_u["ETag"]})
check(st_304 == 304 and b_304 == b"" and hs_304.get("ETag") == hs_u["ETag"],
      "unchanged static file returns 304 Not Modified with matching ETag")
st_304g, hs_304g, b_304g = full("GET", "/static/app.js", {"Accept-Encoding": "gzip", "If-None-Match": hs_g["ETag"]})
check(st_304g == 304 and b_304g == b"" and hs_304g.get("ETag") == hs_g["ETag"],
      "unchanged gzipped static file returns 304 Not Modified with matching -gz ETag")
st_mix, hs_mix, b_mix = full("GET", "/static/app.js", {"Accept-Encoding": "gzip", "If-None-Match": hs_u["ETag"]})
check(st_mix == 200 and hs_mix.get("Content-Encoding") == "gzip" and hs_mix.get("ETag") == hs_g["ETag"],
      "uncompressed ETag sent with Accept-Encoding: gzip returns 200 gzipped instead of mismatched 304")

# ---- v0.7.0 version, strict finite JSON, chart comparison errors, series/date & port guards
import composite as _comp  # noqa: E402
import version as _ver  # noqa: E402

st_cfg, cfg_raw = req("GET", "/api/config")
cfg_json = json.loads(cfg_raw)
check(st_cfg == 200 and cfg_json.get("version") == _ver.VERSION == "0.8.0", "/api/config reports VERSION 0.8.0")
check(_ver.VERSION in server.Handler.server_version, "Handler.server_version includes VERSION")

strict_bytes = server.Handler._dumps_strict({"a": float("nan"), "b": [1.5, float("inf"), float("-inf")]})
check(strict_bytes == b'{"a": null, "b": [1.5, null, null]}', "Handler._dumps_strict replaces NaN/Inf with null")

st_nan, _ = req("PUT", "/api/state", {"Content-Type": "application/json"}, b'{"v": 2, "dashboards": {}, "bad": NaN}')
check(st_nan == 400, "PUT /api/state rejects non-standard NaN JSON with 400")

_orig_ohlc = _comp._ohlc
try:
    def _fake_ohlc(sym, *_a, **_kw):
        if sym == "AAPL":
            return [["2026-11-20", 99.0, 101.0, 98.5, 100.0, 1000], ["2026-11-21", 100.0, 102.0, 99.5, 101.0, 1100]]
        if sym == "SECRET_URL_SYM":
            raise _comp.CompositeError("HTTP 500 from https://secret.example/x?apikey=supersecret123 " + ("z" * 120))
        raise _comp.CompositeError(f"No price history for {sym}.")

    _comp._ohlc = _fake_ohlc
    st_ratio, body_ratio = req("GET", "/x/chart?symbol=AAPL&compare=ZZZZZZZ_BAD&view=ratio")
    j_ratio = json.loads(body_ratio)
    check(st_ratio == 502 and j_ratio.get("user_message") is True and "comparison" in j_ratio.get("detail", "").lower(),
          "chart ratio raises clean 502 CompositeError when comparison symbol has no data")
    st_cmp_err, body_cmp_err = req("GET", "/x/chart?symbol=AAPL&compare=SECRET_URL_SYM&view=price")
    j_cmp_err = json.loads(body_cmp_err)
    _ann_txt = (((j_cmp_err.get("layout") or {}).get("annotations") or [{}])[0]).get("text", "")
    check(st_cmp_err == 200 and "Could not load comparison:" in _ann_txt
          and "https://" not in _ann_txt and "supersecret123" not in _ann_txt and len(_ann_txt) <= 185,
          "chart comparison annotation strips URLs, redacts secrets, and caps length")
finally:
    _comp._ohlc = _orig_ohlc

check(_comp._num_or_none("inf") is None and _comp._num_or_none("nan") is None, "_num_or_none rejects inf and nan")
check(_comp._ret([("2026-11-20", float("nan")), ("2026-11-21", 10.0)], "1D") is None, "_ret rejects NaN values")
check(_comp._big(float("inf")) == "–" and _comp._big(True) == "–", "_big rejects Inf and bool")
check(_comp.range_start(None, "20") == "2015-01-01", "range_start falls back to default when year < 4 digits")
check(_comp._period_date("2026Q2") == "2026-04-01" and _comp._period_date("2026Q") == "2026-01-01",
      "_period_date parses valid quarters and handles malformed strings safely")

try:
    launcher.resolve_ports(types.SimpleNamespace(ui_port=99999, v5_port=None, v4_port=None))
    check(False, "resolve_ports rejects out-of-range port")
except SystemExit:
    check(True, "resolve_ports rejects out-of-range port")

# POST /api/services/restart
st_xo, _ = req("POST", "/api/services/restart", headers={"Origin": "http://evil.example"}, body=b"{}")
check(st_xo == 403, "POST /api/services/restart rejects cross-origin")
st_badj, _ = req("POST", "/api/services/restart", body=b"{bad")
check(st_badj == 400, "POST /api/services/restart rejects invalid JSON")
st_badt, _ = req("POST", "/api/services/restart", body=json.dumps({"target": "nope"}).encode("utf-8"))
check(st_badt == 400, "POST /api/services/restart rejects invalid target")
st_big, _ = req("POST", "/api/services/restart", body=b"{" + b" " * 5000 + b"}")
check(st_big == 413, "POST /api/services/restart rejects bodies > 4096 bytes with 413")
_saved_v4_env_r = os.environ.get("TERMINAL_WITH_V4")
os.environ["TERMINAL_WITH_V4"] = "0"
try:
    st_v4_off, _ = req("POST", "/api/services/restart", body=json.dumps({"target": "v4"}).encode("utf-8"))
    check(st_v4_off == 400, "POST /api/services/restart rejects target='v4' when v4 is inactive")
finally:
    if _saved_v4_env_r is None:
        os.environ.pop("TERMINAL_WITH_V4", None)
    else:
        os.environ["TERMINAL_WITH_V4"] = _saved_v4_env_r

_orig_run_restart = server._run_restart_command
_orig_reset_caches = getattr(_comp, "reset_runtime_caches", None)
_orig_warm = _comp.warm
_reset_calls = []
_warm_calls = []


def _fake_reset_caches():
    _reset_calls.append(1)
    if _orig_reset_caches is not None:
        return _orig_reset_caches()
    return {}


_comp.reset_runtime_caches = _fake_reset_caches
_comp.warm = lambda force=False: _warm_calls.append(force) or None
_restart_gate = threading.Event()
_restart_result = [0, ""]
_restart_raise = [None]


def _fake_run_restart(target, ui_port):
    _restart_gate.wait(timeout=5)
    if _restart_raise[0] is not None:
        raise _restart_raise[0]
    return tuple(_restart_result)


server._run_restart_command = _fake_run_restart
try:
    with server._restart_lock:
        server._restart_state.update({"restarting": False, "seq": 0, "target": "", "last_ok": None, "started_at": 0.0, "finished_at": 0.0, "error": ""})
        server._restart_thread = None
    st_r1, body_r1 = req("POST", "/api/services/restart", body=b"")
    j_r1 = json.loads(body_r1)
    check(st_r1 == 202 and j_r1.get("ok") is True and j_r1.get("target") == "backends" and j_r1.get("restart_seq") == 1,
          "POST /api/services/restart accepts empty body as backends")
    check(len(_reset_calls) == 1, "POST /api/services/restart calls composite.reset_runtime_caches() on start")
    # Simulate an error during the restart window poisoning _backoff; rc == 0 must clear it before marking ready
    _comp._backoff["restart_window_err"] = (time.time() + 600, "restarting")
    st_r2, body_r2 = req("POST", "/api/services/restart", body=json.dumps({"target": "v5"}).encode("utf-8"))
    j_r2 = json.loads(body_r2)
    check(st_r2 == 202 and j_r2.get("already_running") is True and j_r2.get("restart_seq") == 1,
          "POST /api/services/restart deduplicates concurrent requests")
    # Even past 120s timestamp, live worker thread prevents overlapping restart
    with server._restart_lock:
        server._restart_state["started_at"] = time.time() - 200
    st_r2b, body_r2b = req("POST", "/api/services/restart", body=json.dumps({"target": "v5"}).encode("utf-8"))
    check(st_r2b == 202 and json.loads(body_r2b).get("already_running") is True,
          "POST /api/services/restart prevents overlapping worker past 120s while thread is alive")
    _saved_v4_mid = os.environ.get("TERMINAL_WITH_V4")
    os.environ["TERMINAL_WITH_V4"] = "0"
    svc_mid = json.loads(req("GET", "/api/services")[1])
    check(svc_mid["v5"]["state"] == "restarting" and svc_mid["v4"]["state"] != "restarting" and svc_mid["restart"]["restarting"] is True,
          "/api/services marks v5 restarting without falsely marking inactive v4 restarting")
    os.environ["TERMINAL_WITH_V4"] = "1"
    svc_mid_v4 = json.loads(req("GET", "/api/services")[1])
    check(svc_mid_v4["v4"]["state"] == "restarting", "/api/services marks v4 restarting when active")
    if _saved_v4_mid is None:
        os.environ.pop("TERMINAL_WITH_V4", None)
    else:
        os.environ["TERMINAL_WITH_V4"] = _saved_v4_mid
    _restart_gate.set()
    for _ in range(40):
        svc_done = json.loads(req("GET", "/api/services")[1])
        if not svc_done["restart"]["restarting"]:
            break
        time.sleep(0.05)
    check(svc_done["restart"]["restarting"] is False and svc_done["restart"]["last_ok"] is True and svc_done["restart"]["error"] == "",
          "/api/services updates restart state on worker completion")
    check(len(_reset_calls) == 2 and "restart_window_err" not in _comp._backoff and _warm_calls == [True],
          "POST /api/services/restart resets caches after rc==0 and calls composite.warm(force=True)")

    # Next restart resets last_ok=None while running and catches worker exceptions via try/finally
    _restart_gate.clear()
    _restart_raise[0] = RuntimeError("worker crash")
    st_r3, _ = req("POST", "/api/services/restart", body=json.dumps({"target": "v5"}).encode("utf-8"))
    svc_r3_mid = json.loads(req("GET", "/api/services")[1])
    check(st_r3 == 202 and svc_r3_mid["restart"]["last_ok"] is None and svc_r3_mid["restart"]["restarting"] is True,
          "new restart resets last_ok to None while running")
    _restart_gate.set()
    for _ in range(40):
        svc_exc = json.loads(req("GET", "/api/services")[1])
        if not svc_exc["restart"]["restarting"]:
            break
        time.sleep(0.05)
    check(svc_exc["restart"]["restarting"] is False and svc_exc["restart"]["last_ok"] is False and "worker crash" in svc_exc["restart"]["error"],
          "restart worker exception clears restarting=False and records error via try/finally")

    _restart_raise[0] = None
    _restart_gate.clear()
    _restart_result[:] = [1, "boom stderr"]
    st_r4, _ = req("POST", "/api/services/restart", body=json.dumps({"target": "v5"}).encode("utf-8"))
    check(st_r4 == 202, "POST /api/services/restart accepts next request after worker exception")
    _restart_gate.set()
    for _ in range(40):
        svc_fail = json.loads(req("GET", "/api/services")[1])
        if not svc_fail["restart"]["restarting"]:
            break
        time.sleep(0.05)
    check(svc_fail["restart"]["last_ok"] is False and "boom stderr" in svc_fail["restart"]["error"],
          "/api/services records failure in restart state")
finally:
    _restart_gate.set()
    server._run_restart_command = _orig_run_restart
    if _orig_reset_caches is not None:
        _comp.reset_runtime_caches = _orig_reset_caches
    _comp.warm = _orig_warm
    with server._restart_lock:
        server._restart_thread = None

# POSIX kill_tree: reaping process-group leader must not skip SIGKILL for surviving grandchildren
_orig_win = launcher.WIN
_orig_getpgid = getattr(os, "getpgid", None)
_orig_killpg = getattr(os, "killpg", None)
_orig_wnohang = getattr(os, "WNOHANG", None)
_orig_sigkill = getattr(launcher.signal, "SIGKILL", None)
_orig_kill = os.kill
_orig_waitpid = os.waitpid
_orig_l_sleep = launcher.time.sleep
_kill_events = []
try:
    launcher.WIN = False
    launcher.time.sleep = lambda _s: None
    os.getpgid = lambda p: p
    os.killpg = lambda pg, sig: _kill_events.append(("killpg", pg, sig))
    os.WNOHANG = 1
    launcher.signal.SIGKILL = 9
    os.kill = lambda p, sig: _kill_events.append(("kill", p, sig))
    os.waitpid = lambda p, flags: (p, 0)
    launcher.kill_tree(4242)
    check(_kill_events[0] == ("killpg", 4242, launcher.signal.SIGTERM)
          and any(e == ("killpg", 4242, 0) for e in _kill_events)
          and _kill_events[-1] == ("killpg", 4242, launcher.signal.SIGKILL),
          "POSIX kill_tree probes process group and sends SIGKILL to surviving grandchildren after leader exits")
finally:
    launcher.WIN = _orig_win
    launcher.time.sleep = _orig_l_sleep
    if _orig_getpgid is not None:
        os.getpgid = _orig_getpgid
    elif hasattr(os, "getpgid"):
        delattr(os, "getpgid")
    if _orig_killpg is not None:
        os.killpg = _orig_killpg
    elif hasattr(os, "killpg"):
        delattr(os, "killpg")
    if _orig_wnohang is not None:
        os.WNOHANG = _orig_wnohang
    elif hasattr(os, "WNOHANG"):
        delattr(os, "WNOHANG")
    if _orig_sigkill is not None:
        launcher.signal.SIGKILL = _orig_sigkill
    elif hasattr(launcher.signal, "SIGKILL"):
        delattr(launcher.signal, "SIGKILL")
    os.kill = _orig_kill
    os.waitpid = _orig_waitpid

# cmd_restart: waits for port release when probe transiently returns "up" before calling spawn()
_orig_probe = launcher.probe
_orig_cmd_stop = launcher.cmd_stop
_orig_spawn = launcher.spawn
_orig_wait_svc = launcher._wait_for_service
_orig_apply_keys = launcher.apply_keys_if_changed
_orig_rebuild = launcher.rebuild_dashboards
_orig_venv_py = launcher.venv_python
_probe_calls = [0]
_spawn_calls = []


def _fake_probe(name, port):
    if name == "v5":
        _probe_calls[0] += 1
        return "up" if _probe_calls[0] < 3 else "down"
    return "down"


launcher.probe = _fake_probe
launcher.cmd_stop = lambda _a: 0
launcher.spawn = lambda s, _p: _spawn_calls.append((s, _probe_calls[0]))
launcher._wait_for_service = lambda *_a, **_kw: True
launcher.apply_keys_if_changed = lambda **_kw: None
launcher.rebuild_dashboards = lambda *_a, **_kw: None
launcher.venv_python = lambda _s: launcher.Path(sys.executable)
try:
    rc_cmd = launcher.cmd_restart(types.SimpleNamespace(service="v5", ui_port=6950, v5_port=6900, v4_port=6901, with_v4=False, dashboards=False))
    check(rc_cmd == 0 and _spawn_calls == [("v5", 4)],
          "cmd_restart waits for port release when probe transiently returns up before calling spawn()")
finally:
    launcher.probe = _orig_probe
    launcher.cmd_stop = _orig_cmd_stop
    launcher.spawn = _orig_spawn
    launcher._wait_for_service = _orig_wait_svc
    launcher.apply_keys_if_changed = _orig_apply_keys
    launcher.rebuild_dashboards = _orig_rebuild
    launcher.venv_python = _orig_venv_py

# ---- pid files are replaced while others read them (P1-06): on Windows os.replace fails while a reader has the old
# file open, which once killed a supervisor mid crash loop; the write waits a moment, and a failure never stops it
pf_dir = make_tmp_dir("pidfile-")
target = pf_dir / "v5.json"
target.write_text('{"child": 1}', encoding="utf-8")
holder = open(target, encoding="utf-8")                  # noqa: SIM115  (a reader holding the file, like /api/services)
threading.Timer(0.3, holder.close).start()
t0 = time.monotonic()
launcher._write_atomic(target, '{"child": 2}')
took = time.monotonic() - t0
check(target.read_text(encoding="utf-8") == '{"child": 2}', "a pid file held open by a reader is still replaced")
if os.name == "nt":
    check(took >= 0.25, f"the replace waited for the reader to let go ({took:.2f} s)")
    held = open(target, encoding="utf-8")                # noqa: SIM115  (a reader that never lets go)
    try:
        t0 = time.monotonic()
        try:
            launcher._write_atomic(target, '{"child": 3}')
            raised = None
        except PermissionError as e:
            raised = e
        took = time.monotonic() - t0
    finally:
        held.close()
    check(raised is not None and took < 4, f"a file held for good fails in bounded time ({took:.2f} s)")
    check(target.read_text(encoding="utf-8") == '{"child": 2}' and not list(pf_dir.glob("*.tmp")),
          "the old pid file is intact and no temporary file is left")

sup_dir = make_tmp_dir("supervisor-")
saved_l = {k: getattr(launcher, k) for k in ("LOGS", "RUN", "child_cmd", "venv_python", "resolve_ports", "_write_atomic")}
out_, err_ = sys.stdout, sys.stderr


def _deny_pidfile(path, text):
    raise PermissionError(5, "Access is denied", str(path))


try:
    launcher.LOGS, launcher.RUN = sup_dir / "logs", sup_dir / "run"
    launcher.child_cmd = lambda _n, _p: [sys.executable, "-c", "print('service ran')"]
    launcher.venv_python = lambda _s: launcher.Path(sys.executable)
    launcher.resolve_ports = lambda _a: {"ui": 1, "v5": 2, "v4": 3}
    launcher._write_atomic = _deny_pidfile
    rc_sup = launcher.cmd_run(types.SimpleNamespace(service="v5", log=True))
finally:
    sys.stdout, sys.stderr = out_, err_
    for k, v in saved_l.items():
        setattr(launcher, k, v)
sup_log = (sup_dir / "logs" / "v5.log").read_text(encoding="utf-8")
check(rc_sup == 0 and "service ran" in sup_log and "could not record the new process in v5.json" in sup_log,
      "a pid file that cannot be written leaves the supervisor running its service, and says so in the log")

httpd.shutdown()

done("test_launcher_server")
