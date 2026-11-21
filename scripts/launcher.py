"""Install, start, stop and check the terminal on Windows 10/11 (official) and macOS/Linux (experimental, untested).

The double-click files and shell scripts in the project root (Install.cmd, Start.cmd,
Stop.cmd, Setup-Keys.cmd, install.sh, start.sh, stop.sh, setup-keys.sh) are thin
wrappers around this file, so every platform runs the same logic.
Standard library only; works with any Python 3.9 or newer.

  python scripts/launcher.py install [--upgrade] [--with-v4]    create/refresh .venv-v5 (and optional .venv-v4) from lock files
  python scripts/launcher.py start   [--no-browser] [--with-v4] start what is not running, rebuild dashboards, open the browser
  python scripts/launcher.py stop    [all|ui|v5|v4|backends]     stop all or selected service(s)
  python scripts/launcher.py restart [all|ui|v5|v4|backends]     restart all or selected service(s) (e.g. `restart ui` ~2s)
  python scripts/launcher.py status                              what is running, and where
  python scripts/launcher.py logs    [all|ui|v5|v4] [--errors]   tail service logs, freeze dumps, and patch status
  python scripts/launcher.py keys    [--no-edit]                 edit local/api_keys.env and apply it (optional)
  python scripts/launcher.py smoke   [v5|v4|all]                 live data checks against the running backend(s)
  python scripts/launcher.py health  [--dry-run] [--with-v4]     paced card health run -> local/notes/health-YYYY-MM-DD.csv
  python scripts/launcher.py dashboards                         rebuild local/dashboards.json
  python scripts/launcher.py test    [pattern...] [--fast]      offline checks (scripts/tests/run_all.py)
  python scripts/launcher.py check   [--live-sources]           encoding/EOL, syntax, ruff, hygiene, source & note checks
  python scripts/launcher.py snapshot [--label NAME] [--force]  create a snapshot zip in local/snapshots/
  python scripts/launcher.py diff    [SNAPSHOT] [--stat]        diff working tree against the latest (or given) snapshot
  python scripts/launcher.py run {v5,v4,ui}                     one service in the foreground (restarts after any abnormal exit)

Ports: --ui-port / --v5-port / --v4-port, or the environment variables TERMINAL_UI_PORT /
TERMINAL_V5_PORT / TERMINAL_V4_PORT. Defaults 6950 / 6900 / 6901. `start` remembers the
ports in local/run/ports.json, so later stop/status/smoke/start use the same ones.
Everything binds to 127.0.0.1 only. Everything written at run time goes to local/
(see terminal/local_paths.py; TERMINAL_LOCAL_DIR moves it).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
import local_paths  # noqa: E402  (where keys, state, logs and caches live: local/)

THIS = Path(__file__).resolve()
WIN = os.name == "nt"
HOST = "127.0.0.1"
LOGS = local_paths.LOGS_DIR
RUN = local_paths.RUN_DIR
PORTS_FILE = local_paths.PORTS_FILE
RESTART_CODE = 75                       # scripts/run_backend.py exits with this when its watchdog sees a freeze
PY_VERSION = "3.12"
DEFAULT_PORTS = {"v5": 6900, "v4": 6901, "ui": 6950}
ENV_PORT = {"v5": "TERMINAL_V5_PORT", "v4": "TERMINAL_V4_PORT", "ui": "TERMINAL_UI_PORT"}
ENVS = {"v5": ".venv-v5", "v4": ".venv-v4"}
NAMES = {"v5": "Data backend (OpenBB Platform 5)", "v4": "Legacy data backend (OpenBB 4.7)", "ui": "Terminal UI"}
PROBE = {"v5": "/", "v4": "/", "ui": "/api/config"}
START_TIMEOUT = 300                     # seconds; the very first start builds OpenBB's command index

FREE_KEYS = [
    ("FRED", "800k+ U.S. and international macro series, credit spreads, commodity spot, SLOOS", "https://fredaccount.stlouisfed.org/apikeys"),
    ("BLS", "U.S. inflation (CPI/PPI), jobs, wages and productivity detail", "https://data.bls.gov/registrationEngine/"),
    ("EIA", "the Energy dashboard: petroleum, gas, power, STEO outlooks", "https://www.eia.gov/opendata/register.php"),
    ("Congress.gov", "bills, votes, members, committee reports", "https://api.congress.gov/sign-up/"),
    ("FMP", "global fundamentals, transcripts, estimates, ETF data (free tier: 250 calls/day)", "https://site.financialmodelingprep.com/developer/docs"),
]


# ------------------------------------------------------------------ output
def say(msg=""):
    print(msg, flush=True)


def ok(msg):
    say(f"  [ok] {msg}")


def warn(msg):
    say(f"  [!]  {msg}")


def fail(msg, hint=None, code=1):
    say(f"\nERROR: {msg}")
    if hint:
        say(hint)
    sys.exit(code)


# ------------------------------------------------------------------ paths and settings
def venv_python(env: str) -> Path:
    return ROOT / ENVS[env] / ("Scripts/python.exe" if WIN else "bin/python")


def resolve_ports(a) -> dict:
    """Command-line flag > environment variable > ports remembered by the last start > default."""
    saved = local_paths.saved_ports()
    out = {}
    for k, default in DEFAULT_PORTS.items():
        v = getattr(a, f"{k}_port", None) or os.environ.get(ENV_PORT[k]) or saved.get(k) or default
        try:
            out[k] = int(v)
        except ValueError:
            fail(f"{ENV_PORT[k]}={v!r} is not a port number.")
    return out


def want_v4(a) -> bool:
    """True when --with-v4 was requested and not overridden by --skip-v4 (V5-only is the default)."""
    return bool(getattr(a, "with_v4", False)) and not bool(getattr(a, "skip_v4", False))


def child_env(ports: dict | None = None, with_v4: bool | None = None) -> dict:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"             # logs and piped output are UTF-8 even on a Windows code page
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if ports:
        for k, v in ports.items():
            env[ENV_PORT[k]] = str(v)
    if with_v4 is not None:
        env["TERMINAL_WITH_V4"] = "1" if with_v4 else "0"
    return env


def find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    exe = "uv.exe" if WIN else "uv"
    for d in (Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"):
        if (d / exe).exists():
            return str(d / exe)
    return None


# ------------------------------------------------------------------ probing
def probe(name: str, port: int) -> str:
    """'up' (answers like this service), 'busy' (something else holds the port) or 'down'."""
    try:
        with socket.create_connection((HOST, port), timeout=2):
            pass
    except OSError:
        return "down"
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}{PROBE[name]}", timeout=5) as r:
            body = r.read(4096)
        if name == "ui":
            return "up" if b"backends" in body else "busy"
        return "up"
    except urllib.error.HTTPError:
        return "busy" if name == "ui" else "up"
    except Exception:  # noqa: BLE001  (port open but not answering HTTP yet: still starting, or another program)
        return "busy"


# ------------------------------------------------------------------ install
def run_step(cmd, what, hint=None, env=None):
    say(f"  > {' '.join(str(c) for c in cmd)}")
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=ROOT, env=env)
    except OSError as e:
        fail(f"{what} failed: {e}", hint)
    if r.returncode != 0:
        fail(f"{what} failed (exit code {r.returncode}).", hint)


def env_works(py: Path) -> bool:
    try:
        return subprocess.run([str(py), "-c", "import sys"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def write_lock(uv, env):
    src = ROOT / "config" / f"requirements-{env}.in"
    lock = ROOT / "config" / f"requirements-{env}.lock.txt"
    tmp = lock.with_suffix(".tmp")
    run_step([uv, "pip", "compile", src, "--universal", "--python-version", PY_VERSION, "--upgrade", "--no-header", "--no-annotate", "--quiet", "-o", tmp],
             f"Resolving the latest versions for {env}", "Check your internet connection and try again.")
    header = (f"# Exact, tested versions for the {env} environment ({ENVS[env]}), all platforms (Windows, macOS, Linux).\n"
              f"# Regenerate with: python scripts/launcher.py install --upgrade   (resolves config/requirements-{env}.in)\n"
              "# Install uses this file as-is (uv pip sync), so every machine gets the same versions.\n")
    lock.write_text(header + tmp.read_text(encoding="utf-8").replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    tmp.unlink()


def cmd_install(a):
    uv = find_uv()
    if not uv:
        fail("uv was not found.", "uv installs Python and the packages for this project. Install.cmd / install.sh\n"
             "install it automatically; to do it by hand see https://docs.astral.sh/uv/getting-started/installation/")
    use_v4 = want_v4(a)
    targets = ["v5", "v4"] if use_v4 else ["v5"]
    if any(supervisor_alive(s) for s in DEFAULT_PORTS):
        say("Stopping the running terminal first (its files are in use while it runs)...")
        cmd_stop(a)
    say("Installing the terminal. Everything goes into this folder (.venv-v5" + (", .venv-v4" if use_v4 else "") + ") plus uv's download cache.")
    say("The first install downloads several hundred MB and needs about " + ("1.5 GB" if use_v4 else "1 GB") + " of disk. Re-running it is safe and quick.\n")
    net_hint = ("Most install failures are network related (proxy, firewall, VPN) or antivirus locking files.\n"
                "Fix that and run the install again; it continues where it stopped.")
    for i, env in enumerate(targets, 1):
        venv, py = ROOT / ENVS[env], venv_python(env)
        say(f"[{i}/{len(targets)}] {NAMES[env]}  ->  {ENVS[env]}")
        if venv.exists() and not (py.exists() and env_works(py)):
            warn(f"{ENVS[env]} exists but is broken or incomplete; recreating it")
            shutil.rmtree(venv, ignore_errors=True)
        if not py.exists():
            run_step([uv, "venv", venv, "--python", PY_VERSION, "--seed"], f"Creating {ENVS[env]} (uv downloads Python {PY_VERSION} if needed)", net_hint)
        if a.upgrade:
            write_lock(uv, env)
        lock = ROOT / "config" / f"requirements-{env}.lock.txt"
        if not lock.exists():
            fail(f"{lock.relative_to(ROOT)} is missing.", "Restore it from the repository (git checkout -- config) or run with --upgrade.")
        run_step([uv, "pip", "sync", "--python", py, lock], f"Installing packages into {ENVS[env]}", net_hint)
        say("  Preparing OpenBB (builds its command index; up to a minute)...")
        run_step([py, "-c", "from openbb import obb; print('  providers:', len(obb.coverage.providers), ' commands:', len(obb.coverage.commands))"],
                 f"Loading OpenBB in {ENVS[env]}", "Run the install again; if it keeps failing, delete the " + ENVS[env] + " folder and retry.", env=child_env())
        ok(f"{ENVS[env]} ready")
    say("\nInstall complete.")
    if not use_v4:
        say("Installed single-backend V5 (default). Optional legacy V4 sources (FMP, Finviz, EconDB, Intrinio) can be added any time with --with-v4.")
    starter = "Start.cmd (double-click)" if WIN else "sh start.sh"
    keys = "Setup-Keys.cmd" if WIN else "sh setup-keys.sh"
    say(f"Next: start the terminal with {starter}.")
    say(f"Optional: free API keys unlock more data; run {keys} (the terminal works without any).")


# ------------------------------------------------------------------ run one service (supervisor)
def child_cmd(name: str, p: dict) -> list:
    if name in ("v5", "v4"):
        return [str(venv_python(name)), str(ROOT / "scripts" / "run_backend.py"), name, "--host", HOST, "--port", str(p[name]),
                "--apps-json", str(local_paths.backend_dir(name) / "apps.json"), "--no-filter"]
    # the UI server reads the backend ports from TERMINAL_V5_PORT / TERMINAL_V4_PORT (set by child_env)
    return [str(venv_python("v5")), str(ROOT / "terminal" / "server.py"), "--host", HOST, "--port", str(p["ui"])]


def pidfile(name: str) -> Path:
    return RUN / f"{name}.json"


RESTART_DELAYS = (2, 5, 15, 30, 60)      # seconds before restart number 1, 2, 3...
CRASH_LIMIT, CRASH_WINDOW = 5, 600       # give up after 5 abnormal exits within 10 minutes


def restart_decision(code: int, crashes: list, now: float) -> tuple:
    """(restart?, seconds to wait, reason) after the service exited with `code`. `crashes` holds the times of earlier
    abnormal exits and is updated in place. Exit code 0 is a normal stop; anything else is restarted, with growing
    pauses, until CRASH_LIMIT abnormal exits fall inside CRASH_WINDOW seconds."""
    if code == 0:
        return False, 0, "stopped normally"
    crashes[:] = [t for t in crashes if now - t < CRASH_WINDOW] + [now]
    if len(crashes) >= CRASH_LIMIT:
        return False, 0, f"{len(crashes)} crashes within {CRASH_WINDOW // 60} minutes; not restarting again"
    why = "stopped answering (watchdog)" if code == RESTART_CODE else f"exited with code {code}"
    return True, RESTART_DELAYS[min(len(crashes) - 1, len(RESTART_DELAYS) - 1)], why


def cmd_run(a):
    """Run one service in the foreground; restart it after any abnormal exit (capped, see restart_decision)."""
    name, p = a.service, resolve_ports(a)
    py = venv_python("v5" if name == "ui" else name)
    if not py.exists():
        fail(f"{ENVS['v5' if name == 'ui' else name]} is missing.", "Run the install first (Install.cmd / install.sh).")
    cmd = child_cmd(name, p)
    code, crashes = 0, []
    stopped_file = RUN / f"{name}.stopped.json"            # written when this supervisor gives up (the page stops waiting)
    try:
        stopped_file.unlink()
    except OSError:
        pass
    while True:
        say(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {NAMES[name]} -> http://{HOST}:{p[name]}")
        proc = subprocess.Popen(cmd, cwd=ROOT, env=child_env(p))
        pidfile(name).write_text(json.dumps({"supervisor": os.getpid(), "child": proc.pid, "port": p[name], "root": str(ROOT)}), encoding="utf-8")
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            code = proc.wait()
            break
        if code > 2 ** 31:
            code -= 2 ** 32                 # Windows reports a killed process as 4294967295: show it as -1
        again, delay, why = restart_decision(code, crashes, time.time())
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if not again:
            if code:
                say(f"[{stamp}] {NAMES[name]} {why}. Last log lines are above; freeze dumps (if any) are in {LOGS}. Start it again with Start.cmd / start.sh.")
                try:
                    stopped_file.write_text(json.dumps({"time": stamp, "code": code, "why": why}), encoding="utf-8")
                except OSError:
                    pass
            break
        extra = f" (details in {LOGS / f'{name}-freeze-*.txt'})" if code == RESTART_CODE else ""
        say(f"[{stamp}] {NAMES[name]} {why}{extra}; restart {len(crashes)} of at most {CRASH_LIMIT - 1} in {CRASH_WINDOW // 60} minutes, in {delay} s...")
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            break
    try:
        if json.loads(pidfile(name).read_text(encoding="utf-8")).get("supervisor") == os.getpid():
            pidfile(name).unlink()
    except (OSError, ValueError):
        pass
    say(f"{NAMES[name]} exited (code {code}).")
    return code


def rebuild_dashboards(p: dict, quiet=False, with_v4: bool | None = None) -> bool:
    # build_dashboards.py reads the backend ports from TERMINAL_V5_PORT / TERMINAL_V4_PORT (set by child_env)
    cmd = [str(venv_python("v5")), str(ROOT / "scripts" / "build_dashboards.py")]
    try:
        r = subprocess.run(cmd, cwd=ROOT, env=child_env(p, with_v4=with_v4), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        warn(f"dashboards: could not rebuild ({e}); using the previous set")
        return False
    if r.returncode != 0:
        last = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
        warn(f"dashboards: could not rebuild ({last[0][:200]}); using the previous set")
        return False
    for line in r.stdout.strip().splitlines()[: (2 if quiet else 1000)]:
        say(f"  dashboards: {line}")
    return True


# ------------------------------------------------------------------ start / stop / status
def resolve_service_targets(target: str = "all") -> list[str]:
    if target == "all":
        return ["ui", "v5", "v4"]
    if target == "backends":
        return ["v5", "v4"]
    if target in ("ui", "v5", "v4"):
        return [target]
    raise ValueError(f"Unknown service target: {target!r}")


def spawn(name: str, p: dict):
    """Start `launcher.py run <name>` in the background, detached from this console, logging to local/logs/<name>.log."""
    log_path = LOGS / f"{name}.log"
    try:
        if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
            log_path.replace(log_path.with_suffix(".log.1"))
    except OSError:
        pass
    cmd = [str(venv_python("v5")), str(THIS), "run", name, "--ui-port", str(p["ui"]), "--v5-port", str(p["v5"]), "--v4-port", str(p["v4"])]
    with open(log_path, "ab") as log:
        kw = dict(cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=child_env(p), close_fds=True)
        if WIN:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            try:
                subprocess.Popen(cmd, creationflags=flags | 0x01000000, **kw)       # CREATE_BREAKAWAY_FROM_JOB: survive the console closing
            except OSError:
                subprocess.Popen(cmd, creationflags=flags, **kw)
        else:
            subprocess.Popen(cmd, start_new_session=True, **kw)


def supervisor_alive(name: str) -> bool:
    try:
        pid = json.loads(pidfile(name).read_text(encoding="utf-8"))["supervisor"]
    except (OSError, ValueError, KeyError):
        return False
    return pid_alive(pid) and "launcher.py" in command_line(pid)     # a stale file whose PID was reused does not count


def pid_alive(pid: int) -> bool:
    if WIN:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True)
        return f'"{pid}"' in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def tail(path: Path, n=12) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return "(no log)"


PROTECTED_CACHE = ("curvestore_", "catalog-", "earnings_consensus.json", "health-history.json", "yahoo_meta.json")


def _is_protected_cache(path: Path) -> bool:
    name = path.name
    return any(name.startswith(p) or name == p for p in PROTECTED_CACHE)


def prune_local(now: float | None = None, max_cache_mb: float = 150.0) -> int:
    """Disk caches and freeze dumps do not grow for ever: drop cache files untouched for 30 days (the history stores
    curvestore_*, catalog snapshots and earnings/health history stay), backend cache entries after 14 days, freeze dumps
    after 14; then trim non-protected files under local/cache/ oldest-first to stay within max_cache_mb."""
    now = now or time.time()
    gone = 0
    rules = [(local_paths.CACHE_DIR, "*.json", 30), (local_paths.CACHE_DIR / "backend", "**/*.pkl.gz", 14), (LOGS, "*-freeze-*.txt", 14)]
    for folder, pattern, days in rules:
        if not folder.exists():
            continue
        for f in folder.glob(pattern):
            if folder == local_paths.CACHE_DIR and _is_protected_cache(f):
                continue
            try:
                if f.is_file() and now - f.stat().st_mtime > days * 86400:
                    f.unlink()
                    gone += 1
            except OSError:
                pass
    cap = int(max_cache_mb * 1024 * 1024)
    if cap > 0 and local_paths.CACHE_DIR.exists():
        evictable = []
        total = 0
        for f in local_paths.CACHE_DIR.rglob("*"):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
                total += st.st_size
                if not _is_protected_cache(f):
                    evictable.append((st.st_mtime, st.st_size, f))
            except OSError:
                pass
        if total > cap:
            evictable.sort(key=lambda x: x[0])
            for _, size, f in evictable:
                if total <= cap:
                    break
                try:
                    f.unlink()
                    total -= size
                    gone += 1
                except OSError:
                    pass
    return gone


def keys_need_apply(env_file: Path | None = None, stamp: Path | None = None) -> bool:
    """True when local/api_keys.env changed since apply_keys.py last applied it (or was never applied)."""
    env_file = env_file or local_paths.KEYS_FILE
    stamp = stamp or (local_paths.RUN_DIR / "keys-applied.json")
    if not env_file.exists():
        return False
    try:
        done = float(json.loads(stamp.read_text(encoding="utf-8")).get("env_mtime", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return True
    return env_file.stat().st_mtime > done + 0.001


def apply_keys_if_changed(running: bool) -> None:
    """Edits to api_keys.env take effect on the next start without running Setup-Keys (cheap and idempotent)."""
    if not keys_need_apply():
        return
    r = subprocess.run([str(venv_python("v5")), str(ROOT / "scripts" / "apply_keys.py"), "--quiet"], cwd=ROOT, env=child_env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").strip().splitlines():
        say(f"  {line}")
    if r.returncode:
        warn("API keys were not applied (see above); the terminal starts with the keys applied before.")
    elif running:
        warn("API keys changed while the data backends were running: stop and start the terminal to use them.")


def cmd_start(a):
    p = resolve_ports(a)
    prune_local()
    if not venv_python("v5").exists():
        fail("The terminal is not installed yet.", "Run Install.cmd (Windows, double-click) or sh install.sh (experimental on macOS/Linux) first.")
    use_v4 = want_v4(a)
    services = ["v5", "ui"]
    if use_v4:
        if venv_python("v4").exists():
            services = ["v5", "v4", "ui"]
        else:
            warn("--with-v4 requested, but .venv-v4 is not installed. Run Install.cmd --with-v4 first; starting V5 only.")
            use_v4 = False
    apply_keys_if_changed(running=any(probe(s, p[s]) == "up" for s in services if s != "ui"))
    if len(set(p.values())) < 3:
        fail(f"Ports must differ: {p}")
    PORTS_FILE.write_text(json.dumps(p), encoding="utf-8")
    port_msg = "" if p == DEFAULT_PORTS else f" on ports UI {p['ui']}, V5 {p['v5']}" + (f", V4 {p['v4']}" if use_v4 else "")
    say(f"Starting the terminal{port_msg} ...")
    blocked = []

    def launch(s):
        state = probe(s, p[s])
        if state == "up":
            say(f"  {NAMES[s]} already running on :{p[s]}")
        elif state == "busy" and not supervisor_alive(s):
            blocked.append(s)
            warn(f"Port {p[s]} is used by another program, so the {NAMES[s]} cannot start there.")
            say(f"     Close that program, or pick another port: {'Start.cmd' if WIN else 'sh start.sh'} --{s}-port {p[s] + 100}")
        elif state == "down" and not supervisor_alive(s):
            spawn(s, p)

    deadline = time.time() + START_TIMEOUT
    up = {}

    def wait_for(s):
        if s in blocked:
            up[s] = False
            return
        grace = time.time() + 15          # the supervisor needs a moment to write its pid file
        while True:
            if probe(s, p[s]) == "up":
                up[s] = True
                break
            if time.time() > deadline or (time.time() > grace and not supervisor_alive(s) and probe(s, p[s]) == "down"):
                up[s] = False
                break
            time.sleep(2)
        url = f"http://{HOST}:{p[s]}"
        if up[s]:
            ok(f"{NAMES[s]:<34} {url}")
        else:
            warn(f"{NAMES[s]:<34} {url}  NOT responding. Last lines of {LOGS / f'{s}.log'}:")
            say("       " + tail(LOGS / f"{s}.log").replace("\n", "\n       "))

    backends = [s for s in services if s != "ui"]
    for s in backends:
        launch(s)
    say(f"Waiting for the data backends (the first start can take a few minutes; logs in {LOGS})...")
    for s in backends:
        wait_for(s)
    # The UI pre-loads its boards from the backends as soon as it starts, so it starts once they answer.
    launch("ui")
    wait_for("ui")
    # Keeps the asset-class dashboards in step with the backends and with keys added since the last start.
    # Without V4 (default, or down this time) build anyway when there is no previous set to fall back on.
    if up.get("v5") and (up.get("v4") or "v4" not in services or not local_paths.DASHBOARDS_FILE.exists()):
        rebuild_dashboards(p, quiet=True, with_v4=bool(up.get("v4")))
    url = f"http://{HOST}:{p['ui']}"
    say(f"\nTerminal: {url}")
    if up.get("ui") and not a.no_browser:
        webbrowser.open(url)
    return 0 if all(up.values()) else 1


def listening_pids(port: int) -> set:
    pids = set()
    if WIN:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"],
                           capture_output=True, text=True)
        pids = {int(x) for x in r.stdout.split() if x.isdigit()}
    else:
        for cmd in (["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], ["fuser", f"{port}/tcp"]):
            if shutil.which(cmd[0]):
                r = subprocess.run(cmd, capture_output=True, text=True)
                pids |= {int(x) for x in r.stdout.split() if x.isdigit()}
                break
    return {x for x in pids if x not in (0, 4, os.getpid())}


def command_line(pid: int) -> str:
    if WIN:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                           capture_output=True, text=True)
    else:
        r = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
    return r.stdout.strip()


OUR_SCRIPTS = ("run_backend.py", "server.py", "launcher.py", "openbb-api")


def ours(cmdline: str, root: Path = ROOT) -> bool:
    """True when a process command line is one of this terminal's services, started from this folder."""
    norm = cmdline.replace("\\", "/").lower()
    return any(s in norm for s in OUR_SCRIPTS) and str(root).replace("\\", "/").lower() in norm


def kill_tree(pid: int):
    if WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        return
    try:
        target = os.getpgid(pid) == pid
        (os.killpg if target else os.kill)(pid, signal.SIGTERM)
        for _ in range(25):
            time.sleep(0.2)
            os.kill(pid, 0)
        (os.killpg if target else os.kill)(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def cmd_stop(a):
    p = resolve_ports(a)
    target = getattr(a, "service", "all") or "all"
    targets = resolve_service_targets(target)
    stopped = 0
    # 1. supervisors this launcher started (checked against their command line, so a reused PID is never touched)
    if target == "all":
        run_files = sorted(RUN.glob("*.json")) if RUN.exists() else []
    else:
        run_files = [pidfile(t) for t in targets if pidfile(t).exists()]
    for f in run_files:
        if f == PORTS_FILE or f.name.endswith(".stopped.json"):
            continue
        try:
            sup = int(json.loads(f.read_text(encoding="utf-8"))["supervisor"])
        except (OSError, ValueError, KeyError):
            sup = None
        if sup and pid_alive(sup) and "launcher.py" in command_line(sup) and ours(command_line(sup)):
            kill_tree(sup)
            say(f"  stopped {NAMES.get(f.stem, f.stem)} (PID {sup})")
            stopped += 1
        f.unlink(missing_ok=True)
    for t in targets:
        (RUN / f"{t}.stopped.json").unlink(missing_ok=True)
    # 2. a service of this terminal still listening on its port (e.g. started by an older version of the scripts or by
    #    `run` in a console). Anything else on these ports belongs to another program and is left alone.
    for name in targets:
        for pid in listening_pids(p[name]):
            cmdline = command_line(pid)
            if ours(cmdline):
                kill_tree(pid)
                say(f"  stopped PID {pid} on :{p[name]} ({NAMES[name]})")
                stopped += 1
            else:
                prog = (cmdline.split()[0] if cmdline else "unknown program").strip('"')
                warn(f"port {p[name]} is used by PID {pid} ({Path(prog).name or prog}), which this terminal did not start; not stopped.")
    say("Stopped." if stopped else "Nothing was running.")
    return 0


def _wait_for_service(s: str, p: dict, deadline: float, poll: float = 2.0) -> bool:
    grace = time.time() + 15
    ready = False
    while True:
        if probe(s, p[s]) == "up":
            ready = True
            break
        if time.time() > deadline or (time.time() > grace and not supervisor_alive(s) and probe(s, p[s]) == "down"):
            break
        time.sleep(poll)
    url = f"http://{HOST}:{p[s]}"
    if ready:
        ok(f"{NAMES[s]:<34} {url}")
    else:
        warn(f"{NAMES[s]:<34} {url}  NOT responding. Last lines of {LOGS / f'{s}.log'}:")
        say("       " + tail(LOGS / f"{s}.log").replace("\n", "\n       "))
    return ready


def cmd_restart(a):
    target = getattr(a, "service", "all") or "all"
    if target == "all":
        cmd_stop(a)
        return cmd_start(a)
    p = resolve_ports(a)
    if not venv_python("v5").exists():
        fail("The terminal is not installed yet.", "Run Install.cmd (Windows, double-click) or sh install.sh (experimental on macOS/Linux) first.")
    targets = resolve_service_targets(target)
    use_v4 = want_v4(a)
    if target == "v4" and not venv_python("v4").exists():
        warn("Legacy V4 backend (.venv-v4) is not installed. Run Install.cmd --with-v4 first.")
        return 1
    if target == "backends" and "v4" in targets:
        if not use_v4:
            targets.remove("v4")
        elif not venv_python("v4").exists():
            warn("--with-v4 requested, but .venv-v4 is not installed. Run Install.cmd --with-v4 first; restarting V5 only.")
            targets.remove("v4")
    if not targets:
        warn("Nothing to restart.")
        return 0
    cmd_stop(argparse.Namespace(service=target, ui_port=a.ui_port, v5_port=a.v5_port, v4_port=a.v4_port))
    PORTS_FILE.write_text(json.dumps(p), encoding="utf-8")
    if any(s in ("v5", "v4") for s in targets):
        apply_keys_if_changed(running=False)
    v4_up_now = probe("v4", p["v4"]) == "up"
    if target == "ui" and getattr(a, "dashboards", False):
        rebuild_dashboards(p, quiet=True, with_v4=v4_up_now)
    up = {}
    for s in targets:
        state = probe(s, p[s])
        if state == "busy" and not supervisor_alive(s):
            warn(f"Port {p[s]} is used by another program, so the {NAMES[s]} cannot start there.")
            up[s] = False
        elif state == "down":
            spawn(s, p)
    timeout = 30 if targets == ["ui"] else START_TIMEOUT
    poll = 0.5 if targets == ["ui"] else 2.0
    deadline = time.time() + timeout
    for s in targets:
        if s not in up:
            up[s] = _wait_for_service(s, p, deadline, poll=poll)
    if any(s in ("v5", "v4") for s in targets) and (getattr(a, "dashboards", False) or up.get("v5")):
        rebuild_dashboards(p, quiet=True, with_v4=bool(up.get("v4") or (v4_up_now and "v4" not in targets)))
    return 0 if all(up.values()) else 1


def cmd_status(a):
    p = resolve_ports(a)
    worst = 0
    for s in ("v5", "v4", "ui"):
        state = probe(s, p[s])
        if s == "v4" and state == "down" and not supervisor_alive("v4") and not (RUN / "v4.stopped.json").exists():
            say(f"  {NAMES[s]:<34} http://{HOST}:{p[s]:<6} not started (optional; --with-v4)")
            continue
        say(f"  {NAMES[s]:<34} http://{HOST}:{p[s]:<6} {state}")
        worst = worst or (state != "up")
    say(f"Logs: {LOGS}")
    return 1 if worst else 0


def cmd_keys(a):
    env_file = local_paths.KEYS_FILE
    if not env_file.exists():
        shutil.copyfile(local_paths.KEYS_TEMPLATE, env_file)
        say(f"Created {env_file} from the template. It stays on this computer (the local folder is never shared).")
    say("\nAPI keys are optional. The terminal works without any; these free keys unlock more:\n")
    for name, what, url in FREE_KEYS:
        say(f"  {name:<13} {what}\n  {'':<13} sign up: {url}")
    say("\nOther (paid or niche) providers are listed in the file with their sign-up links.")
    if not a.no_edit:
        say(f"\nOpening {env_file}. Paste the keys you have after the '=' signs, save, close the editor.")
        if WIN:
            subprocess.run(["notepad.exe", str(env_file)])
        else:
            editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or next((e for e in ("nano", "vi") if shutil.which(e)), None)
            if editor and sys.stdin.isatty():
                subprocess.run([*editor.split(), str(env_file)])
            else:
                # no terminal or editor here: apply what the file holds now; edits made later apply at the next start
                say(f"No editor available here. Edit {env_file} with any text editor; the next start applies it "
                    "(or run: python3 scripts/launcher.py keys --no-edit). Applying the file as it is now:")
    py = venv_python("v5")
    if not py.exists():
        fail("The terminal is not installed yet.", "Run the install first, then this again.")
    r = subprocess.run([str(py), str(ROOT / "scripts" / "apply_keys.py")], cwd=ROOT, env=child_env())
    if r.returncode == 0:
        say("Restart the terminal to use the keys: stop it, then start it again.")
    return r.returncode


def cmd_smoke(a):
    p = resolve_ports(a)
    py = venv_python("v5")
    if not py.exists():
        fail("The terminal is not installed yet.")
    return subprocess.run([str(py), str(ROOT / "scripts" / "smoke_test.py"), a.which], cwd=ROOT, env=child_env(p)).returncode


def cmd_health(a):
    p = resolve_ports(a)
    py = venv_python("v5")
    exe = str(py) if py.exists() else sys.executable
    cmd = [exe, str(ROOT / "scripts" / "health_check.py"), "--phase", a.phase, "--limit", str(a.limit),
           "--gap", str(a.gap), "--oecd-gap", str(a.oecd_gap), "--timeout", str(a.timeout)]
    if a.filt:
        cmd += ["--filter", a.filt]
    for flag, attr in (("--dry-run", "dry_run"), ("--resume", "resume"), ("--include-broken", "include_broken"),
                       ("--include-manual", "include_manual")):
        if getattr(a, attr):
            cmd.append(flag)
    cmd.append("--with-v4" if want_v4(a) else "--skip-v4")
    if a.out_dir:
        cmd += ["--out-dir", a.out_dir]
    if a.cache_dir:
        cmd += ["--cache-dir", a.cache_dir]
    if a.run_date:
        cmd += ["--date", a.run_date]
    return subprocess.run(cmd, cwd=ROOT, env=child_env(p)).returncode


def cmd_dashboards(a):
    return 0 if rebuild_dashboards(resolve_ports(a)) else 1


def cmd_test(a):
    """Offline checks (no network, no running services needed)."""
    py = venv_python("v5")
    cmd = [str(py) if py.exists() else sys.executable, str(ROOT / "scripts" / "tests" / "run_all.py")]
    if getattr(a, "fast", False):
        cmd.append("--fast")
    if getattr(a, "fail_fast", False):
        cmd.append("--fail-fast")
    if getattr(a, "jobs", None):
        cmd += ["-j", str(a.jobs)]
    cmd += list(getattr(a, "patterns", None) or [])
    return subprocess.run(cmd, cwd=ROOT).returncode


def _run_project_check(args: list[str]) -> int:
    py = venv_python("v5")
    exe = str(py) if py.exists() else sys.executable
    return subprocess.run([exe, str(ROOT / "scripts" / "project_check.py"), *args], cwd=ROOT, env=child_env()).returncode


def cmd_check(a):
    args = ["check"]
    if getattr(a, "live_sources", False):
        args.append("--live-sources")
    return _run_project_check(args)


def cmd_snapshot(a):
    args = ["snapshot"]
    if getattr(a, "label", ""):
        args += ["--label", a.label]
    if getattr(a, "force", False):
        args.append("--force")
    return _run_project_check(args)


def cmd_diff(a):
    args = ["diff"]
    if getattr(a, "snapshot", None):
        args.append(a.snapshot)
    if getattr(a, "stat", False):
        args.append("--stat")
    args += list(getattr(a, "paths", None) or [])
    return _run_project_check(args)


def cmd_logs(a):
    target = getattr(a, "service", "all") or "all"
    targets = ["ui", "v5", "v4"] if target == "all" else [target]
    n = max(1, int(getattr(a, "lines", 40) or 40))
    err_rx = re.compile(r"ERROR|WARNING|Traceback|Refused|not applied|\[fixes\]", re.I)
    for s in targets:
        log_path = LOGS / f"{s}.log"
        say(f"=== {s}.log ({log_path}) ===")
        if not log_path.exists():
            say("  (no log)")
            continue
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if getattr(a, "errors", False):
            lines = [ln for ln in lines if err_rx.search(ln)]
        shown = lines[-n:]
        if not shown:
            say("  (no matching lines)")
        else:
            for ln in shown:
                say("  " + ln)
    freezes = sorted(LOGS.glob("*-freeze-*.txt")) if LOGS.exists() else []
    if freezes:
        say(f"\nFreeze dumps ({len(freezes)}): " + ", ".join(p.name for p in freezes[-5:]))
    if RUN.exists():
        for pf in sorted(RUN.glob("patches-*.json")):
            try:
                data = json.loads(pf.read_text(encoding="utf-8"))
                say(f"{pf.name}: {len(data.get('applied') or [])} applied, {len(data.get('failed') or [])} failed ({data.get('time', '?')})")
            except (OSError, ValueError):
                pass
    return 0


# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="Install, start and stop the terminal.", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n")[2])
    ports = argparse.ArgumentParser(add_help=False)
    ports.add_argument("--ui-port", type=int)
    ports.add_argument("--v5-port", type=int)
    ports.add_argument("--v4-port", type=int)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("install", help="create or refresh the Python environments (V5 default; --with-v4 adds legacy V4)")
    s.add_argument("--upgrade", action="store_true", help="move to the latest compatible releases and rewrite the lock files")
    s.add_argument("--with-v4", action="store_true", help="also install the optional legacy V4 backend (FMP, Finviz, EconDB, Intrinio)")
    s.add_argument("--skip-v4", action="store_true", help="backward-compatible alias (V5-only is now the default)")
    s = sub.add_parser("start", parents=[ports], help="start everything that is not running (V5 + UI default; --with-v4 adds legacy V4)")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--with-v4", action="store_true", help="also start the optional legacy V4 backend")
    s.add_argument("--skip-v4", action="store_true", help="backward-compatible alias (V5-only is now the default)")
    s = sub.add_parser("stop", parents=[ports], help="stop all or selected service(s)")
    s.add_argument("service", nargs="?", default="all", choices=["all", "ui", "v5", "v4", "backends"])
    s = sub.add_parser("restart", parents=[ports], help="restart all or selected service(s) (e.g. restart ui)")
    s.add_argument("service", nargs="?", default="all", choices=["all", "ui", "v5", "v4", "backends"])
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--with-v4", action="store_true", help="also start/restart the optional legacy V4 backend")
    s.add_argument("--skip-v4", action="store_true", help="backward-compatible alias (V5-only is now the default)")
    s.add_argument("--dashboards", action="store_true", help="rebuild local/dashboards.json during restart")
    sub.add_parser("status", parents=[ports], help="show what is running")
    s = sub.add_parser("logs", help="inspect service logs, freeze dumps, and backend patch status")
    s.add_argument("service", nargs="?", default="all", choices=["all", "ui", "v5", "v4"])
    s.add_argument("-n", "--lines", type=int, default=40, help="number of trailing lines to show")
    s.add_argument("--errors", action="store_true", help="filter to warnings, errors, tracebacks, and patch status")
    s = sub.add_parser("keys", help="edit and apply optional API keys")
    s.add_argument("--no-edit", action="store_true", help="only apply local/api_keys.env")
    s = sub.add_parser("smoke", parents=[ports], help="live data checks (defaults to v5)")
    s.add_argument("which", nargs="?", default="v5", choices=["v5", "v4", "all"], help="which backend(s) to check (defaults to v5)")
    s = sub.add_parser("health", parents=[ports], help="paced card health run -> local/notes/health-YYYY-MM-DD.csv")
    s.add_argument("--phase", default="all", choices=["all", "normal", "oecd"], help="normal, oecd, or all (default)")
    s.add_argument("--filter", dest="filt", default=None, help="case-insensitive substring filter")
    s.add_argument("--limit", type=int, default=0, help="cap unique URLs fetched (0 = no limit)")
    s.add_argument("--dry-run", action="store_true", help="resolve URLs without sending card requests")
    s.add_argument("--resume", action="store_true", help="resume today's run from local/cache/")
    s.add_argument("--include-broken", action="store_true", help="also test Data status cards (terminal/health.json)")
    s.add_argument("--include-manual", action="store_true", help="also test click-to-load manual cards")
    s.add_argument("--with-v4", action="store_true", help="also check the optional legacy V4 backend")
    s.add_argument("--skip-v4", action="store_true", help="backward-compatible alias (V5-only is now the default)")
    s.add_argument("--gap", type=float, default=0.7, help="seconds between normal requests")
    s.add_argument("--oecd-gap", type=float, default=3.0, help="seconds between OECD/EconDB requests")
    s.add_argument("--timeout", type=float, default=90.0, help="HTTP timeout per request (s)")
    s.add_argument("--out-dir", default=None, help="output directory for CSV & candidates JSON")
    s.add_argument("--cache-dir", default=None, help="cache directory for resume JSONL & history")
    s.add_argument("--date", dest="run_date", default=None, help="override YYYY-MM-DD run date")
    sub.add_parser("dashboards", parents=[ports], help="rebuild local/dashboards.json")
    s = sub.add_parser("test", help="offline checks (no network needed)")
    s.add_argument("patterns", nargs="*", help="optional substring filters on test filenames")
    s.add_argument("--fast", action="store_true", help="skip headless browser checks (test_frontend)")
    s.add_argument("-j", "--jobs", type=int, default=None, help="parallel worker count")
    s.add_argument("--fail-fast", action="store_true", help="stop after first failure")
    s = sub.add_parser("check", help="run encoding, syntax, ruff, hygiene, source, state, and note checks")
    s.add_argument("--live-sources", action="store_true", help="check sources against live backends")
    s = sub.add_parser("snapshot", help="create a snapshot zip in local/snapshots/")
    s.add_argument("--label", default="", help="optional suffix label")
    s.add_argument("--force", action="store_true", help="write snapshot even if unchanged")
    s = sub.add_parser("diff", help="diff working tree against the latest (or given) snapshot")
    s.add_argument("snapshot", nargs="?", help="optional snapshot zip path")
    s.add_argument("--stat", action="store_true", help="show summary counts only")
    s.add_argument("paths", nargs="*", help="optional path filters")
    s = sub.add_parser("run", parents=[ports], help="run one service in the foreground")
    s.add_argument("service", choices=["v5", "v4", "ui"])
    a = ap.parse_args()
    local_paths.ensure()        # create local/ (or move an old layout into it) before anything reads or writes there
    handler = {"install": cmd_install, "start": cmd_start, "stop": cmd_stop, "restart": cmd_restart, "status": cmd_status,
               "logs": cmd_logs, "keys": cmd_keys, "smoke": cmd_smoke, "health": cmd_health, "dashboards": cmd_dashboards,
               "test": cmd_test, "check": cmd_check, "snapshot": cmd_snapshot, "diff": cmd_diff, "run": cmd_run}[a.cmd]
    return handler(a) or 0


if __name__ == "__main__":
    sys.exit(main())
