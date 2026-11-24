"""Release gate G1-G5 in one command, with a dated evidence file (also `launcher.py gate`).

  .venv-v5\\Scripts\\python.exe scripts\\gate.py [--only G1,G4] [--offline] [--health-limit N]

Writes local/notes/gates-YYYY-MM-DD.json: commit, version, and per gate {status: pass|fail|manual, evidence, started,
finished} (gates from an earlier run the same day are kept unless re-run). Exit code 0 only when no gate failed and
local/state/state.json is byte-for-byte unchanged. --offline runs only the parts that need no running terminal and
marks the live parts manual. G6 (owner decisions) is never automated.

G1  local/notes/g1-spotcheck.json: at least 10 figures, at least 4 non-US; each is fetched again through /x/* and must
    be within its recorded tolerance. Schema of one figure:
      {"id": "sap-revenue", "region": "DE", "view": "snapshot", "params": {"symbol": "SAP.DE"},
       "select": {"rows": "results", "match": {"label": "Revenue"}, "field": "value"}  (or "pointer": "/results/0/price"),
       "expected": 34176.0, "tol_pct": 1.0 (or "tol_abs": 0.05), "unit": "EUR m", "as_of": "2025-12-31",
       "independent_source": {"name": "SAP annual report 2025", "url": "https://..."}}
G2  paced health run (no our-bug, no key in an error, backend survives); cold start of an isolated keyless instance to
    the first screen in under 30 s; probe --all-routes --defaults --concurrent 8 with no 500 and no envelope violation.
G3  check_sources (live, or --offline) and probe --all-routes --defaults --require-as-of (every 200 carries sources and
    as_of or as_of_na).
G4  hygiene scan of publishable files; logs free of key material; live: foreign Host -> 403, Sec-Fetch-Site:
    cross-site -> 403 on /x/board and /api/state, CSP header on /.
G5  Windows: unpack local/releases/Libre-Terminal-Windows.zip into a short temp path, run Install.cmd and Start.cmd
    keyless on spare ports, /api/services must report the UI and V5 up; manual elsewhere.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "terminal")]

import probe  # noqa: E402

GATES = ("G1", "G2", "G3", "G4", "G5")
COLD_START_LIMIT_S = 30.0
G1_MIN_FIGURES, G1_MIN_NON_US = 10, 4
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def local_dir() -> Path:
    raw = (os.environ.get("TERMINAL_LOCAL_DIR") or "").strip()
    return (ROOT / Path(raw).expanduser()).resolve() if raw else ROOT / "local"


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def py_exe() -> str:
    p = ROOT / ".venv-v5" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(p) if p.exists() else sys.executable


def run(cmd: list, timeout: float, env: dict | None = None, cwd: Path = ROOT) -> tuple[int, list[str]]:
    """Run a command; (exit code, last output lines). A timeout is exit code 124."""
    try:
        r = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, [f"timed out after {timeout:.0f} s"]
    lines = [ln for ln in ((r.stdout or "") + (r.stderr or "")).splitlines() if ln.strip()]
    return r.returncode, lines[-8:]


# ---------------------------------------------------------------- G1
def g1_validate(record) -> list[str]:
    """Problems with a g1-spotcheck.json record (empty when it is well formed)."""
    if not isinstance(record, dict) or not isinstance(record.get("figures"), list):
        return ["not an object with a 'figures' list"]
    figs = record["figures"]
    out = []
    if len(figs) < G1_MIN_FIGURES:
        out.append(f"{len(figs)} figures (need {G1_MIN_FIGURES})")
    non_us = sum(1 for f in figs if isinstance(f, dict) and str(f.get("region") or "US").upper() != "US")
    if non_us < G1_MIN_NON_US:
        out.append(f"{non_us} non-US figures (need {G1_MIN_NON_US})")
    for i, f in enumerate(figs):
        fid = f.get("id", f"#{i}") if isinstance(f, dict) else f"#{i}"
        if not isinstance(f, dict):
            out.append(f"{fid}: not an object")
            continue
        if not f.get("view") or not isinstance(f.get("expected"), (int, float)):
            out.append(f"{fid}: needs view and a numeric expected value")
        if not isinstance(f.get("select"), dict) or not (f["select"].get("pointer") or f["select"].get("field")):
            out.append(f"{fid}: needs select.pointer or select.field")
        if not any(isinstance(f.get(k), (int, float)) for k in ("tol_abs", "tol_pct")):
            out.append(f"{fid}: needs tol_abs or tol_pct")
        src = f.get("independent_source")
        if not isinstance(src, dict) or not src.get("name"):
            out.append(f"{fid}: needs independent_source.name")
    return out


def extract(body, select: dict):
    """The selected value from a view's answer: select.pointer ('/results/0/price') or rows/match/field."""
    if select.get("pointer"):
        cur = body
        for part in str(select["pointer"]).strip("/").split("/"):
            if isinstance(cur, list):
                cur = cur[int(part)] if part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur) else None
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
        return cur
    rows = body.get(select.get("rows") or "results") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None
    match = select.get("match") or {}
    for r in rows:
        if isinstance(r, dict) and all(str(r.get(k)) == str(v) for k, v in match.items()):
            return r.get(select["field"])
    return None


def within(value, expected: float, fig: dict) -> tuple[bool, str]:
    """Is a fetched value within the figure's tolerance? (ok, explanation)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, f"got {value!r}, not a number"
    diff = abs(float(value) - float(expected))
    if isinstance(fig.get("tol_abs"), (int, float)):
        return diff <= fig["tol_abs"] + 1e-12, f"{value} vs {expected} (|diff| {diff:.6g} <= {fig['tol_abs']})"
    if not expected:
        return diff == 0, f"{value} vs {expected}"
    pct = diff / abs(float(expected)) * 100.0
    return pct <= fig["tol_pct"] + 1e-12, f"{value} vs {expected} ({pct:.3g}% <= {fig['tol_pct']}%)"


def gate_g1(ctx) -> tuple[str, list[str]]:
    path = local_dir() / "notes" / "g1-spotcheck.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "fail", ["local/notes/g1-spotcheck.json is missing (manifest P3-07 records it)"]
    except (OSError, ValueError) as e:
        return "fail", [f"local/notes/g1-spotcheck.json unreadable: {type(e).__name__}"]
    problems = g1_validate(record)
    if problems:
        return "fail", problems[:10]
    if ctx.offline:
        return "manual", [f"record well formed ({len(record['figures'])} figures); values are re-fetched only live"]
    ev, bad = [], 0
    for f in record["figures"]:
        status, body, _ = probe.fetch(ctx.ui + f"/x/{f['view']}?" + urllib.parse.urlencode(f.get("params") or {}), ctx.ui_host, 90)
        ok, why = (False, f"HTTP {status}") if status != 200 else within(extract(body, f["select"]), f["expected"], f)
        bad += not ok
        ev.append(f"{'ok ' if ok else 'BAD'} {f.get('id')}: {why}")
    return ("pass" if not bad else "fail"), [f"{len(ev) - bad}/{len(ev)} figures within tolerance"] + ev


# ---------------------------------------------------------------- G2
def free_port(prefer: int) -> int:
    for port in (prefer, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise OSError("no free port")


def isolated_env(scratch: Path, local: Path | None = None) -> dict:
    """Environment for an isolated keyless instance: its own local/ folder and profile (REFERENCE.md section 2)."""
    home = scratch / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, USERPROFILE=str(home), HOME=str(home))
    if local is not None:
        env["TERMINAL_LOCAL_DIR"] = str(local)
    else:
        env.pop("TERMINAL_LOCAL_DIR", None)
    for k in ("TERMINAL_UI_PORT", "TERMINAL_V5_PORT", "TERMINAL_V4_PORT"):
        env.pop(k, None)
    return env


def wait_http(url: str, host: str, deadline: float, ok=lambda s, b: s == 200) -> bool:
    while time.monotonic() < deadline:
        status, body, _ = probe.fetch(url, host, 5)
        if ok(status, body):
            return True
        time.sleep(0.5)
    return False


def cold_start(ctx) -> tuple[bool, str]:
    scratch = Path(tempfile.mkdtemp(prefix="lt-cold-"))
    env = isolated_env(scratch, scratch / "local")
    ui, v5 = free_port(7950), free_port(7900)
    ports = ["--ui-port", str(ui), "--v5-port", str(v5)]
    host = f"127.0.0.1:{ui}"
    t0 = time.monotonic()
    proc = subprocess.Popen([py_exe(), str(ROOT / "scripts" / "launcher.py"), "start", "--no-browser", *ports], cwd=ROOT,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        first = wait_http(f"http://{host}/api/dashboards", host, t0 + 180,
                          ok=lambda s, b: s == 200 and isinstance(b, dict) and bool(b.get("dashboards")))
        elapsed = time.monotonic() - t0
        if first:
            # the first screen is usable when its first card answers too
            first_view = wait_http(f"http://{host}/x/board", host, time.monotonic() + 60)
            elapsed = time.monotonic() - t0
            first &= first_view
    finally:
        try:
            proc.wait(timeout=240)
        except subprocess.TimeoutExpired:
            proc.kill()
        run([py_exe(), ROOT / "scripts" / "launcher.py", "stop", *ports], 120, env=env)
        shutil.rmtree(scratch, ignore_errors=True)
    if not first:
        return False, f"isolated keyless instance showed no first screen within {elapsed:.0f} s"
    return elapsed < COLD_START_LIMIT_S, f"cold start to first screen {elapsed:.1f} s (limit {COLD_START_LIMIT_S:.0f} s)"


def probe_all(ctx, *extra) -> tuple[int, list[str]]:
    return run([py_exe(), ROOT / "scripts" / "probe.py", "--all-routes", "--defaults", "--concurrent", "8",
                "--ui-port", str(ctx.ui_port), *extra], 1800)


def gate_g2(ctx) -> tuple[str, list[str]]:
    if ctx.offline:
        return "manual", ["health run, cold start and route probe need the running terminal"]
    ev, ok = [], True
    cmd = [py_exe(), ROOT / "scripts" / "health_check.py", "--phase", "all", "--skip-v4", "--limit", str(ctx.health_limit)]
    rc, lines = run(cmd, 4 * 3600, env=dict(os.environ, TERMINAL_UI_PORT=str(ctx.ui_port), TERMINAL_V5_PORT=str(ctx.v5_port)))
    ok &= rc == 0
    ev += [f"health exit {rc}"] + [ln for ln in lines if ln.startswith("[health]")][-2:]
    good, why = cold_start(ctx)
    ok &= good
    ev.append(why)
    rc, lines = probe_all(ctx, "--assert-envelope", "--allow", "200,204,400,404,429,502,503,504")
    ok &= rc == 0
    ev += [f"probe --all-routes exit {rc}"] + lines[-4:]
    return ("pass" if ok else "fail"), ev


# ---------------------------------------------------------------- G3
def gate_g3(ctx) -> tuple[str, list[str]]:
    cmd = [py_exe(), ROOT / "scripts" / "check_sources.py"] + (["--offline"] if ctx.offline else [])
    rc, lines = run(cmd, 1800, env=dict(os.environ, TERMINAL_UI_PORT=str(ctx.ui_port), TERMINAL_V5_PORT=str(ctx.v5_port)))
    ev = [f"check_sources{' --offline' if ctx.offline else ''} exit {rc}"] + lines[-2:]
    if ctx.offline:
        return ("manual" if rc == 0 else "fail"), ev + ["live as_of coverage needs the running terminal"]
    rc2, lines2 = probe_all(ctx, "--assert-envelope", "--require-as-of", "--allow", "200,204,400,404,429,502,503,504")
    ev += [f"probe --require-as-of exit {rc2}"] + lines2[-4:]
    return ("pass" if rc == 0 and rc2 == 0 else "fail"), ev


# ---------------------------------------------------------------- G4
def scan_logs(logs: Path) -> list[str]:
    """Log lines that still carry key material (file:line), using the same detector as the redaction layer."""
    from redact import contains_secret
    hits = []
    for f in sorted(logs.glob("*.log*")) if logs.exists() else []:
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if contains_secret(line):
                        hits.append(f"{f.name}:{n}")
        except OSError:
            continue
    return hits


def raw_get(url: str, headers: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with _NO_PROXY.open(req, timeout=15) as r:
            return r.status, dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {})
    except (urllib.error.URLError, OSError) as e:
        return -1, {"error": type(e).__name__}


def live_g4_probes(base: str, host: str) -> list[tuple[bool, str]]:
    out = []
    st, _ = raw_get(base + "/api/config", {"Host": "evil.example"})
    out.append((st == 403, f"foreign Host on /api/config -> {st} (want 403)"))
    for path in ("/x/board", "/api/state"):
        st, _ = raw_get(base + path, {"Host": host, "Sec-Fetch-Site": "cross-site"})
        out.append((st == 403, f"Sec-Fetch-Site: cross-site on {path} -> {st} (want 403)"))
    st, h = raw_get(base + "/", {"Host": host})
    csp = next((v for k, v in h.items() if k.lower() == "content-security-policy"), "")
    out.append((st == 200 and "script-src 'self'" in csp, f"GET / -> {st}, CSP {'present' if csp else 'missing'}"))
    return out


def gate_g4(ctx) -> tuple[str, list[str]]:
    import project_check
    ev, ok = [], True
    hyg = project_check.check_hygiene(ROOT)
    ok &= not hyg
    ev.append(f"hygiene scan of publishable files: {len(hyg)} problems" + (f" ({hyg[0]})" if hyg else ""))
    hits = scan_logs(local_dir() / "logs")
    ok &= not hits
    ev.append(f"key material in logs: {len(hits)} lines" + (f" (first {hits[0]})" if hits else ""))
    if ctx.offline:
        return ("manual" if ok else "fail"), ev + ["Host/Sec-Fetch-Site/CSP probes need the running terminal"]
    for good, why in live_g4_probes(ctx.ui, ctx.ui_host):
        ok &= good
        ev.append(why)
    return ("pass" if ok else "fail"), ev


# ---------------------------------------------------------------- G5
def short_temp() -> Path:
    """A short scratch folder (MAX_PATH): <drive>\\ltg5-xxxx when writable, else the user temp folder."""
    drive = Path((os.environ.get("SystemDrive") or "C:") + "\\")
    try:
        return Path(tempfile.mkdtemp(prefix="ltg5-", dir=drive))
    except OSError:
        return Path(tempfile.mkdtemp(prefix="ltg5-"))


def gate_g5(ctx) -> tuple[str, list[str]]:
    if os.name != "nt":
        return "manual", ["fresh-install check runs on Windows only"]
    if ctx.offline:
        return "manual", ["fresh install needs the network and a release zip"]
    zpath = local_dir() / "releases" / "Libre-Terminal-Windows.zip"
    if not zpath.exists():
        return "fail", ["local/releases/Libre-Terminal-Windows.zip is missing (project_check.py release-zip builds it)"]
    scratch = short_temp()
    ev, ok = [f"unpacked into {len(str(scratch))}-character path"], False
    ui, v5 = free_port(7950), free_port(7900)
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(scratch)
        top = [p for p in scratch.iterdir() if (p / "Install.cmd").exists()]
        app = top[0] if top else scratch
        env = isolated_env(scratch)
        t = time.monotonic()
        rc, lines = run(["cmd", "/c", str(app / "Install.cmd")], 3600, env=env, cwd=app)
        ev.append(f"Install.cmd exit {rc} in {time.monotonic() - t:.0f} s")
        if rc == 0:
            t = time.monotonic()
            rc, lines = run(["cmd", "/c", str(app / "Start.cmd"), "--no-browser", "--ui-port", str(ui), "--v5-port", str(v5)],
                            900, env=env, cwd=app)
            ev.append(f"Start.cmd exit {rc} in {time.monotonic() - t:.0f} s")
            host = f"127.0.0.1:{ui}"
            st, body, _ = probe.fetch(f"http://{host}/api/services", host, 15)
            v5_up = isinstance(body, dict) and (body.get("v5") or {}).get("state") == "running" and (body.get("v5") or {}).get("reachable")
            ok = rc == 0 and st == 200 and bool(v5_up)
            ev.append(f"/api/services -> {st}: UI {'up' if st == 200 else 'down'}, V5 {'up' if v5_up else 'down'}")
        else:
            ev += lines[-3:]
    finally:
        for cand in [*scratch.glob(".venv-v5/Scripts/python.exe"), *scratch.glob("*/.venv-v5/Scripts/python.exe")][:1]:
            app_dir = cand.parents[2]
            run([cand, app_dir / "scripts" / "launcher.py", "stop", "--ui-port", str(ui), "--v5-port", str(v5)],
                120, env=isolated_env(scratch), cwd=app_dir)
        shutil.rmtree(scratch, ignore_errors=True)
    return ("pass" if ok else "fail"), ev


GATE_FUNCS = {"G1": gate_g1, "G2": gate_g2, "G3": gate_g3, "G4": gate_g4, "G5": gate_g5}


# ---------------------------------------------------------------- driver
class Ctx:
    def __init__(self, offline: bool, ui_port: int, v5_port: int, health_limit: int = 0):
        self.offline = offline
        self.ui_port, self.v5_port = ui_port, v5_port
        self.ui_host = f"127.0.0.1:{ui_port}"
        self.ui = f"http://{self.ui_host}"
        self.health_limit = health_limit


def parse_only(value: str | None) -> list[str]:
    if not value:
        return list(GATES)
    out = [g.strip().upper() for g in value.split(",") if g.strip()]
    bad = [g for g in out if g not in GATES]
    if bad:
        raise SystemExit(f"unknown gate(s): {', '.join(bad)}; known: {', '.join(GATES)} (G6 is the owner's decision)")
    return out


def exit_code(gates: dict, state_unchanged: bool) -> int:
    return 0 if state_unchanged and not any(g.get("status") == "fail" for g in gates.values()) else 1


def git_sha() -> str:
    rc, lines = run(["git", "rev-parse", "HEAD"], 15)
    return lines[-1].strip() if rc == 0 and lines else "unknown"


def version() -> str:
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', (ROOT / "terminal" / "version.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "unknown"


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Release gate G1-G5 with a dated evidence file.")
    ap.add_argument("--only", default=None, help="comma-separated gates, e.g. G1,G4 (default: all)")
    ap.add_argument("--offline", action="store_true", help="only what needs no running terminal; live parts are manual")
    ap.add_argument("--health-limit", type=int, default=0, help="cap the G2 health run to N unique URLs (0 = all)")
    ap.add_argument("--ui-port", type=int, default=None)
    ap.add_argument("--v5-port", type=int, default=None)
    a = ap.parse_args(argv)
    gates = parse_only(a.only)
    ctx = Ctx(a.offline, probe.port_of("ui", a.ui_port), probe.port_of("v5", a.v5_port), a.health_limit)
    before = probe.state_digest()
    date = dt.date.today().isoformat()
    out_path = local_dir() / "notes" / f"gates-{date}.json"
    try:
        record = json.loads(out_path.read_text(encoding="utf-8"))
        if not isinstance(record.get("gates"), dict):
            raise ValueError
    except (OSError, ValueError, AttributeError):
        record = {"gates": {}}
    record.update(date=date, commit=git_sha(), version=version(), offline=a.offline)
    for g in gates:
        started = now()
        try:
            status, evidence = GATE_FUNCS[g](ctx)
        except Exception as e:  # noqa: BLE001  - a gate that crashes is a failed gate, never a passed one
            status, evidence = "fail", [f"gate crashed: {type(e).__name__}: {str(e)[:200]}"]
        record["gates"][g] = {"status": status, "evidence": evidence, "started": started, "finished": now()}
        print(f"{g}: {status.upper()}  " + (evidence[0] if evidence else ""), flush=True)
        for line in evidence[1:6]:
            print("    " + line)
    unchanged = probe.state_digest() == before
    record["state_json_unchanged"] = unchanged
    record["gates"] = {k: record["gates"][k] for k in sorted(record["gates"])}
    write_record(out_path, record)
    code = exit_code({g: record["gates"][g] for g in gates}, unchanged)       # this run's gates decide the exit code
    print(f"wrote {out_path.relative_to(local_dir().parent) if out_path.is_relative_to(local_dir().parent) else out_path.name}; "
          f"state.json {'unchanged' if unchanged else 'CHANGED'}; {'PASS' if code == 0 else 'FAIL'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
