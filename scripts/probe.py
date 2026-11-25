"""Live probe for a running terminal (standard library only): status counts, latency and envelope checks per view.

  .venv-v5\\Scripts\\python.exe scripts\\probe.py --view snapshot --inputs symbol=AAPL symbol=SAP.DE --concurrent 8
  .venv-v5\\Scripts\\python.exe scripts\\probe.py --all-routes --defaults --assert-envelope
  .venv-v5\\Scripts\\python.exe scripts\\probe.py --backend v5 --url /equity/price/quote?symbol=AAPL --url /ping

Each --inputs value is one input set (k=v[,k=v]); every view is asked once per input set (times --repeat), with up to
--concurrent requests in flight. One summary line per view (or backend URL): status counts, p50/p95 latency and
envelope violations. Exit code 1 when any status is not in --allow or any envelope is violated, 2 when there is
nothing to probe. The probe only sends GET requests (it never sends PUT /api/state) and no key material; it checks that
local/state/state.json is byte-for-byte unchanged after the run.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTS = {"ui": 6950, "v5": 6900, "v4": 6901}
ENV_PORT = {"ui": "TERMINAL_UI_PORT", "v5": "TERMINAL_V5_PORT", "v4": "TERMINAL_V4_PORT"}
SECRET_PARAMS = ("key", "token", "secret", "password", "auth")
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
# The generic reason the server puts on an answer whose view neither dated its data nor declared why it has no date
# (terminal/composite/core/contract.py NO_DATE; test_harness pins the two equal). A live audit counts it as missing.
GENERIC_NO_DATE = "This answer has no dated data points (links, a directory or a prompt)."


def port_of(service: str, override: int | None = None) -> int:
    if override:
        return int(override)
    raw = (os.environ.get(ENV_PORT[service]) or "").strip()
    return int(raw) if raw.isdigit() else DEFAULT_PORTS[service]


def parse_inputs(values: list[str] | None) -> list[dict]:
    """['symbol=AAPL', 'symbol=SAP.DE,range=1y'] -> [{'symbol': 'AAPL'}, {'symbol': 'SAP.DE', 'range': '1y'}]."""
    out = []
    for v in values or []:
        d = {}
        for part in str(v).split(","):
            if "=" in part:
                k, val = part.split("=", 1)
                d[k.strip()] = val.strip()
        out.append(d)
    return out


def envelope_violations(status: int, body, require_as_of: bool = False) -> list[str]:
    """The response contract every /x/* answer must meet: a JSON object; on 200 a non-empty 'sources' list (and, with
    require_as_of, an 'as_of' date or a declared 'as_of_na' reason, never the generic one); on any error status a 'detail' text."""
    if not isinstance(body, dict):
        return [f"{status} body is not a JSON object"]
    out = []
    if status == 200 and not body.get("sources"):
        out.append("200 without sources")
    if status == 200 and require_as_of and not (body.get("as_of") or body.get("as_of_na")):
        out.append("200 without as_of or as_of_na")
    elif status == 200 and require_as_of and not body.get("as_of") and body.get("as_of_na") == GENERIC_NO_DATE:
        out.append("200 with only the generic no-date reason (neither a data date nor a declared reason)")
    if status >= 400 and not str(body.get("detail") or "").strip():
        out.append(f"{status} without detail")
    return out


def percentile(values: list[float], pct: float):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(pct / 100.0 * len(s) + 0.5)) - 1))
    return s[k]


def redact_url(url: str) -> str:
    """Hide the value of any query parameter whose name suggests key material."""
    if "?" not in url:
        return url
    base, q = url.split("?", 1)
    parts = []
    for p in q.split("&"):
        k, _, v = p.partition("=")
        parts.append(f"{k}=***" if any(s in k.lower() for s in SECRET_PARAMS) else p)
    return base + "?" + "&".join(parts)


def summary_line(name: str, statuses: dict, latencies_ms: list[float], violations: int) -> str:
    counts = " ".join(f"{k}x{v}" for k, v in sorted(statuses.items(), key=lambda kv: str(kv[0])))
    p50, p95 = percentile(latencies_ms, 50), percentile(latencies_ms, 95)
    lat = f"p50 {p50:.0f} ms, p95 {p95:.0f} ms" if p50 is not None else "no answers"
    return f"{name}: n={sum(statuses.values())} [{counts}] {lat}, envelope violations {violations}"


def state_digest() -> str | None:
    raw = (os.environ.get("TERMINAL_LOCAL_DIR") or "").strip()
    local = (ROOT / Path(raw).expanduser()).resolve() if raw else ROOT / "local"
    path = local / "state" / "state.json"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def fetch(url: str, host_header: str, timeout: float) -> tuple[int | str, object, float]:
    """GET url; returns (status or error name, parsed body or None, elapsed ms)."""
    req = urllib.request.Request(url, method="GET", headers={
        "Host": host_header, "Accept": "application/json", "Sec-Fetch-Site": "same-origin", "User-Agent": "terminal-probe"})
    t = time.perf_counter()
    try:
        with _NO_PROXY.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        return type(reason).__name__, None, (time.perf_counter() - t) * 1000
    ms = (time.perf_counter() - t) * 1000
    try:
        body = json.loads(raw) if raw.strip() else None
    except ValueError:
        body = raw[:80].decode("utf-8", "replace")
    return status, body, ms


def ui_routes(base: str, host: str, timeout: float) -> list[tuple[str, dict]]:
    """(view name, default params) for every widget the UI server lists in /widgets.json."""
    status, body, _ = fetch(base + "/widgets.json", host, timeout)
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"/widgets.json answered {status}; is the terminal running?")
    out = []
    for w in body.values():
        ep = str(w.get("endpoint") or "")
        if not ep.startswith("/x/"):
            continue
        params = {p["paramName"]: p["value"] for p in w.get("params") or []
                  if p.get("paramName") and p.get("value") not in (None, "")}
        out.append((ep[3:], params))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Probe a running terminal: status counts, latency and envelope checks.")
    ap.add_argument("--view", action="append", default=[], help="a /x/<view> name (repeatable)")
    ap.add_argument("--inputs", nargs="*", default=[], help="input sets, each k=v[,k=v] (one request per set and view)")
    ap.add_argument("--concurrent", type=int, default=1, help="requests in flight at once")
    ap.add_argument("--repeat", type=int, default=1, help="ask each view/input pair this many times")
    ap.add_argument("--assert-envelope", action="store_true", help="count envelope violations as failures")
    ap.add_argument("--require-as-of", action="store_true", help="a 200 must also carry as_of or as_of_na (with --assert-envelope)")
    ap.add_argument("--all-routes", action="store_true", help="every /x/ view listed in /widgets.json")
    ap.add_argument("--defaults", action="store_true", help="use each widget's default parameters (with --all-routes)")
    ap.add_argument("--allow", default="200", help="comma-separated statuses that count as success (default 200)")
    ap.add_argument("--backend", choices=("v5", "v4"), help="probe raw backend URLs (/api/v1 + --url) instead of /x/ views")
    ap.add_argument("--url", action="append", default=[], help="backend path under /api/v1 (repeatable, with --backend)")
    ap.add_argument("--ui-port", type=int, default=None)
    ap.add_argument("--port", type=int, default=None, help="backend port (with --backend)")
    ap.add_argument("--timeout", type=float, default=60.0)
    a = ap.parse_args(argv)

    allowed = {s.strip() for s in a.allow.split(",") if s.strip()}
    inputs = parse_inputs(a.inputs) or [{}]
    jobs: list[tuple[str, str]] = []                       # (summary key, url)
    if a.backend:
        if not a.url:
            print("--backend needs at least one --url PATH")
            return 2
        port = port_of(a.backend, a.port)
        host = f"127.0.0.1:{port}"
        for path in a.url:
            path = path if path.startswith("/") else "/" + path
            jobs += [(redact_url(path), f"http://{host}/api/v1{path}")] * max(1, a.repeat)
    else:
        port = port_of("ui", a.ui_port)
        host = f"127.0.0.1:{port}"
        base = f"http://{host}"
        routes = [(v, {}) for v in a.view]
        if a.all_routes:
            routes += [(v, p if a.defaults else {}) for v, p in ui_routes(base, host, a.timeout)]
        if not routes:
            print("Nothing to probe: give --view NAME, --all-routes, or --backend v5 --url PATH.")
            return 2
        for view, defaults in routes:
            for inp in inputs:
                q = urllib.parse.urlencode({**defaults, **inp})
                jobs += [(view, f"{base}/x/{view}" + (f"?{q}" if q else ""))] * max(1, a.repeat)

    before = state_digest()
    results: dict[str, dict] = {}
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, a.concurrent)) as ex:
        futs = {ex.submit(fetch, url, host, a.timeout): (key, url) for key, url in jobs}
        for fut in concurrent.futures.as_completed(futs):
            key, url = futs[fut]
            status, body, ms = fut.result()
            r = results.setdefault(key, {"statuses": {}, "lat": [], "viol": [], "bad": []})
            r["statuses"][status] = r["statuses"].get(status, 0) + 1
            r["lat"].append(ms)
            if str(status) not in allowed:
                detail = body.get("detail") if isinstance(body, dict) else body
                r["bad"].append(f"{status} {redact_url(url.split('/', 3)[-1])}: {str(detail or '')[:120]}")
            if not a.backend and isinstance(status, int):
                r["viol"] += [f"{v} ({redact_url(url.split('/', 3)[-1])})" for v in envelope_violations(status, body, a.require_as_of)]
    failed = False
    for key in sorted(results):
        r = results[key]
        nviol = len(r["viol"])
        print(summary_line(key, r["statuses"], r["lat"], nviol))
        for line in (r["bad"] + (r["viol"] if a.assert_envelope else []))[:5]:
            print("    " + line)
        failed |= bool(r["bad"]) or (a.assert_envelope and nviol > 0)
    after = state_digest()
    state_ok = before == after
    print(f"total: {len(jobs)} requests in {time.perf_counter() - t0:.1f} s; state.json "
          + ("unchanged" if state_ok else "CHANGED"))
    return 1 if failed or not state_ok else 0


if __name__ == "__main__":
    sys.exit(main())
