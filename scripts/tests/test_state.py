"""Saved state under write faults: a failed save answers 503, leaves state.json byte-identical and no temp file."""

import errno
import http.client
import json
import os
import re
import socket
import threading
import time

from _common import ROOT, check, done

import server

server.log = lambda _msg: None
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv = server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
HOST = f"127.0.0.1:{port}"
SD = server.STATE_DIR


def put(obj):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("PUT", "/api/state", body=json.dumps(obj).encode(), headers={"Host": HOST, "Content-Type": "application/json"})
    r = c.getresponse()
    body = json.loads(r.read() or b"{}")
    c.close()
    return r.status, body


def temps():
    return sorted(p.name for p in SD.glob("*.tmp*")) if SD.exists() else []


def snapshot():
    return server.STATE_FILE.read_bytes() if server.STATE_FILE.exists() else None


# a first good save, and today's backup already taken (so only state.json is written below)
st, body = put({"v": 2, "dashboards": {}, "watchlists": [{"name": "keep", "symbols": ["AAPL"]}]})
check(st == 200 and body["rev"] == 1, f"first save works ({st})")
st, body = put({"v": 2, "dashboards": {}, "_rev": 1, "watchlists": [{"name": "keep", "symbols": ["AAPL", "SAP.DE"]}]})
check(st == 200 and body["rev"] == 2 and any(SD.glob("state.*.bak.json")), "second save works and takes the daily backup")
before = snapshot()
check(before is not None and temps() == [], "no temporary file after successful saves")

# ---- 1. a full disk while writing state.json
real_fsync, real_replace, real_sleep = os.fsync, os.replace, time.sleep


def enospc(_fd):
    raise OSError(errno.ENOSPC, "No space left on device")


try:
    os.fsync = enospc
    st, body = put({"v": 2, "dashboards": {}, "_rev": 2, "watchlists": [{"name": "lost", "symbols": []}]})
finally:
    os.fsync = real_fsync
check(st == 503 and body["error"]["kind"] == "unavailable" and "No space left" in body["detail"], f"ENOSPC while writing -> 503 ({st}, {body.get('detail')})")
check(snapshot() == before, "state.json is byte-identical after the failed write")
check(temps() == [], f"no temporary file left after the failed write ({temps()})")

# ---- 2. the old file held on all 5 replace attempts (Windows scanner / indexer)
calls = []


def held(src, dst):
    calls.append(dst)
    raise PermissionError(errno.EACCES, "The process cannot access the file because it is being used by another process")


try:
    os.replace = held
    time.sleep = lambda _s: None
    st, body = put({"v": 2, "dashboards": {}, "_rev": 2, "watchlists": [{"name": "lost2", "symbols": []}]})
finally:
    os.replace, time.sleep = real_replace, real_sleep
check(st == 503 and len(calls) == 5, f"replace refused 5 times -> 503 after 5 attempts ({st}, {len(calls)})")
check(snapshot() == before and temps() == [], "state.json byte-identical, no temporary file")

# ---- 3. a transient hold: replace refused twice, then the save goes through
calls.clear()


def held_twice(src, dst):
    calls.append(dst)
    if len(calls) <= 2:
        raise PermissionError(errno.EACCES, "busy")
    return real_replace(src, dst)


try:
    os.replace = held_twice
    time.sleep = lambda _s: None
    st, body = put({"v": 2, "dashboards": {}, "_rev": 2, "watchlists": [{"name": "kept", "symbols": ["MSFT"]}]})
finally:
    os.replace, time.sleep = real_replace, real_sleep
check(st == 200 and body["rev"] == 3 and len(calls) == 3 and temps() == [], "a short hold is retried and the save lands")
before = snapshot()

# ---- 4. the daily backup write fails (new day, full disk): 503, state.json unchanged
for b in SD.glob("state.*.bak.json"):
    b.unlink()
try:
    os.fsync = enospc
    st, body = put({"v": 2, "dashboards": {}, "_rev": 3, "watchlists": []})
finally:
    os.fsync = real_fsync
check(st == 503 and snapshot() == before and temps() == [] and not any(SD.glob("state.*.bak.json")),
      f"backup write fails -> 503, state.json unchanged, no temp or half backup ({st})")
st, body = put({"v": 2, "dashboards": {}, "_rev": 3, "watchlists": []})
check(st == 200 and body["rev"] == 4 and any(SD.glob("state.*.bak.json")), "once the disk has room the next save works and backs up")

# ---- atomic_write on its own: an interrupt mid-write also cleans up
target = SD / "probe.json"
target.write_text("old", encoding="utf-8")


def interrupted(_fd):
    raise KeyboardInterrupt


try:
    os.fsync = interrupted
    try:
        server.atomic_write(target, "new")
    except KeyboardInterrupt:
        pass
finally:
    os.fsync = real_fsync
check(target.read_text(encoding="utf-8") == "old" and temps() == [], "an interrupted write leaves the old file and no temp")
target.unlink()

# ---- no other writer of files under local/state in server.py
src = (ROOT / "terminal" / "server.py").read_text(encoding="utf-8")
writers = [n + 1 for n, ln in enumerate(src.splitlines()) if re.search(r"write_text|write_bytes|open\(.*['\"][wa]b?['\"]", ln)]
aw = src.index("def atomic_write")
aw_lines = range(src[:aw].count("\n") + 1, src[:aw].count("\n") + 30)
check(writers and all(n in aw_lines for n in writers), f"atomic_write is the only file writer in server.py ({writers})")
srv.shutdown()

done("test_state")
