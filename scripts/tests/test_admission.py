"""UI server admission control: at most 24 views at once, a 503 (unavailable, retry_after) after a 5 s wait, the page
and state never wait for a slot, HEAD never runs a view, /api/services reports the thread count."""

import http.client
import json
import socket
import threading
import time

from _common import check, done

import composite as C
import server

server.log = lambda _msg: None
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv = server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
HOST = f"127.0.0.1:{port}"


def slots_free(want=24, wait=3.0):
    """The server releases a slot just after the answer is written: give it a moment."""
    end = time.monotonic() + wait
    while server._x_slots._value != want and time.monotonic() < end:
        time.sleep(0.02)
    return server._x_slots._value


def req(method, path, timeout=30):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.request(method, path, headers={"Host": HOST})
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = raw
    return r.status, body, dict(r.getheaders())


check(server.X_SLOTS == 24 and server.X_WAIT == 5.0, "24 view slots, 5 s wait")

# ---- 40 slow views in flight: 24 run, the rest wait; the page and state answer at once
release = threading.Event()
running = [0]
peak = [0]
lock = threading.Lock()


def slow_view(**_):
    with lock:
        running[0] += 1
        peak[0] = max(peak[0], running[0])
    release.wait(30)                       # a source that does not answer (the harness's timeout fault, held open)
    with lock:
        running[0] -= 1
    return {"results": [{"date": "2026-01-02", "v": 1}]}


saved = dict(C.ROUTES)
C.ROUTES["board"] = slow_view
server.X_WAIT = 1.5                       # same rule, shorter wait for the test
results = []


def call():
    results.append(req("GET", "/x/board?symbols=" + str(time.time())))


ts = [threading.Thread(target=call) for _ in range(40)]
try:
    for t in ts:
        t.start()
    time.sleep(0.5)
    with lock:
        now_running = running[0]
    t0 = time.perf_counter()
    st_state, _, _ = req("GET", "/api/state")
    ms_state = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    st_cfg, _, _ = req("GET", "/api/config")
    st_svc, svc, _ = req("GET", "/api/services")
    st_page, _, _ = req("GET", "/")
    ms_rest = (time.perf_counter() - t0) * 1000
    check(now_running == 24, f"24 views run at once ({now_running})")
    check(st_state in (200, 503) and ms_state < 300, f"GET /api/state answers in {ms_state:.0f} ms with 40 views in flight")
    check(st_cfg == 200 and st_svc == 200 and st_page == 200 and ms_rest < 900, f"config, services and the page answer at once ({ms_rest:.0f} ms)")
    check(isinstance(svc.get("ui_threads"), int) and svc["ui_threads"] >= 40 and svc["x_slots"]["max"] == 24, f"/api/services reports ui_threads ({svc.get('ui_threads')})")
    deadline = time.monotonic() + 15      # the 16 waiting past X_WAIT answer 503 (each 1.5 s after it arrived)
    while time.monotonic() < deadline:
        with lock:
            n503 = sum(1 for r in results if r[0] == 503)
        if n503 >= 16:
            break
        time.sleep(0.1)
    time.sleep(0.3)                       # and no more than 16 do
    with lock:
        n503 = sum(1 for r in results if r[0] == 503)
    check(n503 == 16, f"the 16 requests without a slot answer 503 after the wait ({n503})")
    r503 = next(r for r in results if r[0] == 503)
    check(r503[1]["error"]["kind"] == "unavailable" and r503[1]["error"]["retry_after"] == 5 and r503[1]["error"]["retryable"] is True
          and r503[2].get("Retry-After") == "5" and r503[1]["sources"], "503: kind unavailable, retry_after 5, Retry-After header, sources")
finally:
    release.set()
    for t in ts:
        t.join(30)
check(sum(1 for r in results if r[0] == 200) == 24 and peak[0] == 24, f"the 24 admitted views finish normally (peak {peak[0]})")
check(slots_free() == 24, "every slot is released afterwards")

# ---- a view that fails still releases its slot
C.ROUTES["board"] = lambda **_: 1 / 0
for _ in range(30):
    req("GET", "/x/board")
check(slots_free() == 24, "slots are released after defects too")

# ---- HEAD never runs a view
calls = [0]
orig_dispatch = C.dispatch


def counting(name, params):
    calls[0] += 1
    return orig_dispatch(name, params)


server.composite.dispatch = counting
try:
    st, body, hdr = req("HEAD", "/x/board?preset=us_equity")
    check(st == 405 and hdr.get("Allow") == "GET" and calls[0] == 0, f"HEAD /x/board -> 405, dispatch not called ({st}, {calls[0]})")
    st, _, _ = req("HEAD", "/api/config")
    check(st == 200, "HEAD on other paths still answers")
finally:
    server.composite.dispatch = orig_dispatch
    C.ROUTES.clear()
    C.ROUTES.update(saved)
    server.X_WAIT = 5.0
srv.shutdown()

done("test_admission")
