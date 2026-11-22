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

httpd.shutdown()

done("test_launcher_server")
