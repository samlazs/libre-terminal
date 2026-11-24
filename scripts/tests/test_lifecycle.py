"""Live lifecycle faults on an isolated instance (spare ports, scratch local/ folder and profile; Windows):
(1) killed backend child restarted by its supervisor, (2) crash loop stops at the cap and says so, (3) stop twice is
idempotent and leaves nothing behind, (4) a restart from the page under concurrent view load answers no 5xx but 503
unavailable, (5) a pidfile naming a foreign process never kills it. The owner's state.json is never touched.

Runs only with `launcher.py test lifecycle --live` (several minutes); otherwise it reports itself skipped.
"""

import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time

from _common import REAL_LOCAL, ROOT, check, done, make_tmp_dir

if os.environ.get("TERMINAL_LIVE_TESTS") != "1" or os.name != "nt":
    print("test_lifecycle: skipped (live suite: launcher.py test lifecycle --live, Windows only)")
    done("test_lifecycle")
    sys.exit(0)

PY = str(ROOT / ".venv-v5" / "Scripts" / "python.exe")
LAUNCHER = str(ROOT / "scripts" / "launcher.py")
OWNER_STATE = REAL_LOCAL / "state" / "state.json"


def sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def free_port(prefer):
    for p in (prefer, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()


owner_before = sha(OWNER_STATE)
scratch = make_tmp_dir("lifecycle-")
LOCAL, HOME = scratch / "local", scratch / "home"
HOME.mkdir(parents=True, exist_ok=True)
ENV = {k: v for k, v in os.environ.items() if not k.startswith("TERMINAL_")}
ENV.update(TERMINAL_LOCAL_DIR=str(LOCAL), USERPROFILE=str(HOME), HOME=str(HOME))
UI, V5 = free_port(7961), free_port(7911)
PORTS = ["--ui-port", str(UI), "--v5-port", str(V5)]
RUN = LOCAL / "run"
OUT = scratch / "launcher-out.txt"


def launcher(*args, timeout=420):
    with open(OUT, "a", encoding="utf-8") as out:
        return subprocess.run([PY, LAUNCHER, *args, *PORTS], cwd=ROOT, env=ENV, stdout=out, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, timeout=timeout).returncode


def call(method, path, body=None, timeout=60):
    c = http.client.HTTPConnection("127.0.0.1", UI, timeout=timeout)
    h = {"Host": f"127.0.0.1:{UI}"}
    if body is not None:
        h.update({"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{UI}"})
    try:
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        raw = r.read()
        try:
            return r.status, json.loads(raw) if raw else None
        except ValueError:
            return r.status, None
    except OSError as e:
        return type(e).__name__, None
    finally:
        c.close()


def services():
    st, body = call("GET", "/api/services", timeout=10)
    return body if st == 200 and isinstance(body, dict) else {}


def v5_state():
    v = services().get("v5") or {}
    return v.get("state"), v.get("reachable")


def wait(cond, timeout, step=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = cond()
        if got:
            return got
        time.sleep(step)
    return None


def pidinfo(name="v5"):
    try:
        return json.loads((RUN / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def alive(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
    return f'"{pid}"' in (r.stdout or "")


def kill(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def our_processes():
    """python processes whose command line names this instance's ports (none may outlive a stop)."""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
                       capture_output=True, text=True, timeout=60)
    try:
        data = json.loads(r.stdout or "[]")
    except ValueError:
        return []
    data = data if isinstance(data, list) else [data]
    tokens = (f"--ui-port {UI}", f"--v5-port {V5}", f"--port {V5}", f"--port {UI}")
    return [d["ProcessId"] for d in data if any(t in (d.get("CommandLine") or "") for t in tokens)]


def port_open(port):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


try:
    rc = launcher("start", "--no-browser", "--no-wait", timeout=600)
    up = wait(lambda: v5_state() == ("running", True), 300)
    check(rc == 0 and up, f"isolated instance started on :{UI}/:{V5} (rc {rc})")

    # ---- (1) the V5 child is killed: the supervisor restarts it after 2 s and the service comes back
    child = pidinfo().get("child")
    t_kill = time.monotonic()
    kill(child)
    new_child = wait(lambda: (pidinfo().get("child") not in (None, child)) and pidinfo().get("child"), 60, 0.2)
    t_new = time.monotonic() - t_kill
    seen = set()
    back = wait(lambda: (seen.add(v5_state()) or v5_state() == ("running", True)), 300)
    check(new_child and 1.8 <= t_new <= 20, f"(1) killed child replaced after the 2 s backoff ({t_new:.1f} s)")
    check(back and ("running", False) in seen, f"(1) the service was seen down, then up again ({sorted(map(str, seen))})")

    # ---- (4) restart from the page while 20 clients load views: no 5xx except 503 unavailable
    statuses, bad, stop_load = [], [], threading.Event()
    syms = ["AAPL", "MSFT", "SAP.DE", "7203.T", "SHEL.L", "NVDA", "RY.TO", "BHP.AX", "ASML.AS", "NESN.SW"]

    def load(i):
        while not stop_load.is_set():
            path = (f"/x/snapshot?symbol={syms[i % len(syms)]}", f"/x/chart?symbol={syms[(i + 3) % len(syms)]}", "/x/board?preset=us_equity")[i % 3]
            st, body = call("GET", path, timeout=90)
            statuses.append(st)
            if isinstance(st, int) and st >= 500 and not (st == 503 and (body or {}).get("error", {}).get("kind") == "unavailable"):
                bad.append((st, path, ((body or {}).get("error") or {}).get("kind"), str((body or {}).get("detail"))[:120]))
            time.sleep(0.2)

    ts = [threading.Thread(target=load, args=(i,), daemon=True) for i in range(20)]
    for t in ts:
        t.start()
    time.sleep(3)
    st, body = call("POST", "/api/services/restart", {"target": "backends"})
    check(st == 202 and body.get("ok"), f"(4) restart accepted ({st})")
    done_ok = wait(lambda: (lambda r: (not r.get("restarting")) and r.get("seq") == body.get("restart_seq") and r)(services().get("restart") or {}), 420, 2)
    time.sleep(5)
    stop_load.set()
    for t in ts:
        t.join(120)
    n503 = sum(1 for s in statuses if s == 503)
    check(done_ok and done_ok.get("last_ok") is True, f"(4) the restart finished ({done_ok})")
    check(not bad, f"(4) {len(statuses)} view answers during the restart, no 5xx other than 503 unavailable ({bad[:3]})")
    import collections  # noqa: E402
    no_answer = collections.Counter(s for s in statuses if not isinstance(s, int))
    check(len(statuses) >= 40 and not no_answer, f"(4) every request got an HTTP answer ({len(statuses)}, 503 x{n503}, without answer: {dict(no_answer)})")
    check(wait(lambda: v5_state() == ("running", True), 300), "(4) the data service is up again")

    # ---- (2) five crashes within 600 s: the supervisor gives up, writes v5.stopped.json and the page shows it stopped
    killed = []
    for n in range(5):
        pid = wait(lambda: (lambda c: c if c and c not in killed and alive(c) else None)(pidinfo().get("child")), 90, 0.2)
        if not pid:
            break
        kill(pid)
        killed.append(pid)
    stopped = wait(lambda: (RUN / "v5.stopped.json").exists(), 120)
    why = json.loads((RUN / "v5.stopped.json").read_text(encoding="utf-8")).get("why", "") if stopped else ""
    check(len(killed) == 5 and stopped and "crashes" in why, f"(2) 5 crashes -> supervisor stops and records why ({len(killed)}, {why})")
    state = wait(lambda: (services().get("v5") or {}).get("state") == "stopped" and services()["v5"], 30)
    check(bool(state) and "crashes" in state.get("why", ""), f"(2) /api/services shows the service stopped ({state})")
    check(not pidinfo(), "(2) the supervisor removed its pidfile")

    # ---- (5) a pidfile naming a foreign process: stop never kills it
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        RUN.mkdir(parents=True, exist_ok=True)
        (RUN / "v5.json").write_text(json.dumps({"supervisor": foreign.pid, "child": foreign.pid, "port": V5, "root": str(ROOT)}), encoding="utf-8")
        rc5 = launcher("stop", "v5", timeout=120)
        check(rc5 == 0 and alive(foreign.pid) and foreign.poll() is None, f"(5) a foreign PID in the pidfile survives stop (rc {rc5})")
    finally:
        foreign.kill()
        (RUN / "v5.json").unlink(missing_ok=True)

    # ---- (3) stop twice: both succeed, ports are free and nothing of this instance is left running
    rc_a = launcher("stop", timeout=180)
    rc_b = launcher("stop", timeout=180)
    left = wait(lambda: (not our_processes() and not port_open(UI) and not port_open(V5)) or None, 30)
    check(rc_a == 0 and rc_b == 0, f"(3) stop twice: both exit 0 ({rc_a}, {rc_b})")
    check(bool(left), f"(3) ports free and no process of this instance left ({our_processes()})")
finally:
    try:
        launcher("stop", timeout=180)
    except Exception:  # noqa: BLE001
        pass
    for pid in our_processes():
        kill(pid)

check(sha(OWNER_STATE) == owner_before, "the owner's state.json is byte-identical")
done("test_lifecycle")
