"""Service logs (P1-08): the background supervisor owns local/logs/<name>.log and rotates it by size at every child
(re)start and while the child runs, keeping two generations, without losing or altering the child's output."""

import sys
import types

from _common import check, done, make_tmp_dir

import launcher

WIN = sys.platform == "win32"
MB = 1024 * 1024

# ---- a 6 MB log from an earlier session is rotated when the supervisor starts
tmp = make_tmp_dir("logs-")
big = tmp / "ui.log"
big.write_bytes(b"x" * (6 * MB))
log = launcher.RotatingLog(big)
check(log.limit == 5 * MB and log.size == 6 * MB, f"the 5 MB limit applies and the existing size is counted ({log.size})")
log.rotate_if_large()
log.write(b"after rotation\n")
log.close()
check((tmp / "ui.log.1").stat().st_size == 6 * MB, "the 6 MB file became ui.log.1")
check(big.read_bytes() == b"after rotation\n", "ui.log starts again empty and keeps being written")

# ---- two generations are kept, never a third
log = launcher.RotatingLog(tmp / "g.log", limit=1000)
for i in range(4):
    log.write(bytes([65 + i]) * 1500)                  # each write passes the limit and rotates
log.close()
gens = sorted(p.name for p in tmp.glob("g.log*"))
check(gens == ["g.log", "g.log.1", "g.log.2"], f"g.log, g.log.1 and g.log.2 only ({gens})")
check((tmp / "g.log.1").read_bytes() == b"D" * 1500 and (tmp / "g.log.2").read_bytes() == b"C" * 1500,
      "the newest rotated block is .1, the one before it .2, older ones are dropped")

# ---- bytes are copied unchanged (the children redact; the supervisor must not alter or re-encode)
raw = "price 12,5 € été apikey=REDACTED\n".encode() + b"\xff\xfe broken utf-8\n"
log = launcher.RotatingLog(tmp / "raw.log")
log.write(raw)
log.close()
check((tmp / "raw.log").read_bytes() == raw, "bytes reach the log exactly as the child wrote them")

# ---- Windows: a reader holding the file blocks the rename; writing continues and the rotation happens afterwards
if WIN:
    log = launcher.RotatingLog(tmp / "held.log", limit=1000, retry_s=0)
    with open(tmp / "held.log", "rb"):
        log.write(b"a" * 1500)
        check(not (tmp / "held.log.1").exists() and log.size == 1500, "while a reader holds it the file is not renamed and nothing raises")
    log.write(b"b" * 10)
    log.close()
    check((tmp / "held.log.1").read_bytes() == b"a" * 1500 + b"b" * 10 and (tmp / "held.log").read_bytes() == b"",
          "once the reader is gone the next write rotates")

# ---- end to end: cmd_run --log pipes a real child through the log and rotates while it runs and at its restart
child = tmp / "child.py"
marker = tmp / "first-done"
child.write_text(
    "import pathlib, sys\n"
    f"m = pathlib.Path({str(marker)!r})\n"
    "if not m.exists():\n"
    "    for i in range(1500):\n"
    "        print(f'first-run line {i:05d} ' + 'y' * 80)\n"
    "    print('first-run-end', flush=True)\n"
    "    m.write_text('1')\n"
    "    sys.exit(3)\n"
    "for i in range(10):\n"
    "    print(f'second-run line {i}')\n",
    encoding="utf-8")
saved = {k: getattr(launcher, k) for k in ("LOGS", "RUN", "LOG_LIMIT", "RESTART_DELAYS", "child_cmd", "venv_python", "resolve_ports")}
out, err = sys.stdout, sys.stderr
run_dir = tmp / "run"
run_dir.mkdir()
try:
    launcher.LOGS, launcher.RUN, launcher.LOG_LIMIT, launcher.RESTART_DELAYS = tmp / "svc", run_dir, 64 * 1024, (0, 0, 0, 0, 0)
    launcher.child_cmd = lambda _n, _p: [sys.executable, str(child)]
    launcher.venv_python = lambda _s: launcher.Path(sys.executable)
    launcher.resolve_ports = lambda _a: {"ui": 1, "v5": 2, "v4": 3}
    code = launcher.cmd_run(types.SimpleNamespace(service="ui", log=True))
finally:
    sys.stdout, sys.stderr = out, err
    for k, v in saved.items():
        setattr(launcher, k, v)
svc = tmp / "svc"
files = {p.name: p.read_bytes() for p in svc.glob("ui.log*")}
check(code == 0 and set(files) == {"ui.log", "ui.log.1", "ui.log.2"}, f"a 140 KB session with a 64 KB limit leaves three generations ({sorted(files)}, code {code})")
check(all(len(b) <= 64 * 1024 + 65536 for b in files.values()), f"no generation passes the limit by more than one read ({[len(b) for b in files.values()]})")
text = (files["ui.log.2"] + files["ui.log.1"] + files["ui.log"]).decode("utf-8")
check("second-run line 9" in files["ui.log"].decode() and files["ui.log"].decode().rstrip().endswith("exited (code 0)."),
      "the current ui.log holds the restarted child's output and the supervisor's last line")
check(text.index("first-run-end") < text.index("restart 1 of") < text.index("second-run line 0"),
      "the child's last lines land before the supervisor's restart line")
last = [int(ln.split()[2]) for ln in text.splitlines() if ln.startswith("first-run line ")]
check(last == list(range(last[0], 1500)), f"the kept part of the first run is complete and in order (from line {last[0] if last else '?'})")
check(not any(run_dir.glob("ui.stopped.json")), "a clean exit writes no stopped file")

done("test_logs")
