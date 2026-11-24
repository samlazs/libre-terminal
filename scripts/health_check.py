"""Paced live health runner for all placed cards.

Standard library only; never reads/writes outside local/ (plus local/notes/ reports)
and redacts all secrets via terminal/redact.py before writing CSV, JSON, or logs.

Usage (usually via launcher):
  python scripts/launcher.py health [--dry-run] [--phase all|normal|oecd]
                                    [--filter TEXT] [--limit N] [--resume]
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
import local_paths  # noqa: E402
from redact import contains_secret, redact  # noqa: E402

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover (Python < 3.9 fallback not expected)
    ZoneInfo = None  # type: ignore[assignment]

    class ZoneInfoNotFoundError(Exception):
        pass


HEALTH_FILE = ROOT / "terminal" / "health.json"
WATCH_DEFAULT = {
    "id": "main",
    "symbols": ["AAPL", "MSFT", "NVDA", "ASML", "7203.T", "SHEL.L", "RHM.DE", "^GSPC", "EURUSD=X", "GC=F", "BTC-USD"],
}
FILE_TYPES = {"multi_file_viewer", "pdf"}
OECD_X = {"x_rate_history", "x_rates_by_country", "x_global_yields", "x_country_monitor"}
EIA_RX = re.compile(r"/api/v1/eia/|[?&]provider=eia(&|$)|/api/v1/jodi/")
RETRY_RX = re.compile(r"closed file|database is locked|Too Many Requests|RemoteDisconnected", re.I)
URL_RX = re.compile(r"https?://[^\s\"'<>)]+")
ALIASES = {
    "south_korea": ["korea"],
    "korea": ["south_korea"],
    "turkey": ["turkiye"],
    "turkiye": ["turkey"],
    "czech_republic": ["czechia"],
    "czechia": ["czech_republic"],
    "slovakia": ["slovak_republic"],
    "euro_area": ["euro_area_20_countries", "euro_area_19_countries", "euro_area20", "ea20"],
    "united_states": ["united_states_of_america"],
}
DEFAULT_PORTS = {"ui": 6950, "v5": 6900, "v4": 6901}
ENV_PORT = {"ui": "TERMINAL_UI_PORT", "v5": "TERMINAL_V5_PORT", "v4": "TERMINAL_V4_PORT"}
DEFAULT_GAP = 0.7
DEFAULT_OECD_GAP = 3.0
DEFAULT_TIMEOUT = 90.0
SLOW_SECS = 8.0
UA = {"User-Agent": "Mozilla/5.0 (openbb-terminal-health)", "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}

CSV_COLS = [
    "dashboard", "tab", "title", "lib_title", "choice", "backend", "widget", "ep_key",
    "source", "provider", "status", "secs", "rows", "traces", "empty", "cls", "cause",
    "error", "err_has_url", "err_has_key", "retried", "url",
]


def resolve_ports(ui_port: int | None = None, v5_port: int | None = None, v4_port: int | None = None) -> dict[str, int]:
    """Port precedence: explicit arg > TERMINAL_*_PORT env > local/run/ports.json > defaults."""
    saved = local_paths.saved_ports()
    explicit = {"ui": ui_port, "v5": v5_port, "v4": v4_port}
    out: dict[str, int] = {}
    for k, default in DEFAULT_PORTS.items():
        raw = explicit[k] if explicit[k] is not None else os.environ.get(ENV_PORT[k]) or saved.get(k) or default
        out[k] = int(raw)
    return out


def bases_for(ports: dict[str, int]) -> dict[str, str]:
    return {
        "x": f"http://127.0.0.1:{ports['ui']}",
        "v5": f"http://127.0.0.1:{ports['v5']}",
        "v4": f"http://127.0.0.1:{ports['v4']}",
    }


def _ny_date_fallback(utc_dt: dt.datetime) -> dt.date:
    """Compute America/New_York calendar date in pure stdlib (fallback when tzdata is absent)."""
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=dt.timezone.utc)
    else:
        utc_dt = utc_dt.astimezone(dt.timezone.utc)
    y = utc_dt.year
    mar1 = dt.date(y, 3, 1)
    first_mar_sun = 1 + (6 - mar1.weekday()) % 7
    dst_start = dt.datetime(y, 3, first_mar_sun + 7, 7, 0, tzinfo=dt.timezone.utc)
    nov1 = dt.date(y, 11, 1)
    first_nov_sun = 1 + (6 - nov1.weekday()) % 7
    dst_end = dt.datetime(y, 11, first_nov_sun, 6, 0, tzinfo=dt.timezone.utc)
    offset = -4 if dst_start <= utc_dt < dst_end else -5
    return (utc_dt + dt.timedelta(hours=offset)).date()


def market_date(now: dt.datetime | None = None) -> dt.date:
    """Today's calendar date in America/New_York (matching T.marketDate in widgets.js)."""
    if now is None:
        utc_dt = dt.datetime.now(dt.timezone.utc)
    elif now.tzinfo is None:
        utc_dt = now.replace(tzinfo=dt.timezone.utc)
    else:
        utc_dt = now.astimezone(dt.timezone.utc)
    if ZoneInfo is not None:
        try:
            return utc_dt.astimezone(ZoneInfo("America/New_York")).date()
        except ZoneInfoNotFoundError:
            pass
    return _ny_date_fallback(utc_dt)


def resolve_param(v, now: dt.datetime | None = None):
    """Resolve '@days:N' and '@bday:N' tokens using New York date (matching T.resolveParam)."""
    if not isinstance(v, str) or not v.startswith("@"):
        return v
    m = re.match(r"^@(days|bday):(-?\d+)$", v)
    if not m:
        return v
    d = market_date(now)
    n = int(m.group(2))
    if m.group(1) == "days":
        return (d + dt.timedelta(days=n)).isoformat()
    step = -1 if n < 0 else 1
    left = abs(n)
    while left > 0:
        d += dt.timedelta(days=step)
        if d.weekday() < 5:
            left -= 1
    return d.isoformat()


def symbol_for(sym: str, kind: str) -> str:
    """Format a ticker for source expectations (matching T.symbolFor in widgets.js)."""
    s = str(sym or "").upper()
    if kind == "tmx":
        return re.sub(r"\.(TO|V|NE|CN)$", "", s)
    if kind == "nordic":
        return re.sub(r"\.(ST|HE|CO|IC)$", "", s).replace("-", " ")
    if kind == "crypto_base":
        return re.sub(r"-?USDT?$", "", s)
    return s


def empty(v) -> bool:
    return v is None or v == "" or (isinstance(v, list) and not v)


def js_str(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return ",".join(js_str(x) for x in v)
    return str(v)


def qs(params: dict) -> str:
    pairs = [(k, js_str(v)) for k, v in params.items() if k is not None and not empty(v)]
    s = urllib.parse.urlencode(pairs)
    return "?" + s if s else ""


def country_for(p: dict, value):
    """Map alias country slugs against a widget's option list (matching widgets.js)."""
    opts = [str(o.get("value")) for o in (p.get("options") or []) if isinstance(o, dict)]
    if not opts:
        return value

    def one(c: str) -> str:
        c = str(c)
        if c in opts:
            return c
        alt = [a for a in ALIASES.get(c, []) if a in opts]
        return alt[0] if alt else c

    lst = value if isinstance(value, list) else str(value).split(",")
    mapped = [one(c) for c in lst]
    return mapped if isinstance(value, list) else ",".join(mapped)


def group_of(item: dict, pn: str, groups: list[dict]) -> dict | None:
    item_groups = set(item.get("groups") or [])
    for g in groups:
        if g.get("paramName") == pn and g.get("name") in item_groups:
            return g
    return None


def assign_groups(df: dict, groups: list[dict]) -> list[str]:
    """Attach dashboard groups to a card (matching assignGroups in app.js, including syncFrom)."""
    out: list[str] = []
    pnames = [p.get("paramName") for p in (df.get("params") or []) if p.get("show") is not False and p.get("paramName")]
    for g in (groups or []):
        if any(o.get("name") == n and o.get("paramName") == g.get("paramName") for n in out for o in groups):
            continue
        if g.get("syncFrom"):
            parent = next((o for o in groups if o.get("name") == g["syncFrom"]), None)
            if parent and parent.get("paramName") in pnames:
                continue
        if g.get("paramName") in pnames:
            out.append(g["name"])
    return out


def concept_member_map(D: dict) -> dict[tuple[str, str], dict]:
    """Map (backend, widgetId) -> concept member metadata (e.g. 'sym' market format)."""
    out: dict[tuple[str, str], dict] = {}
    for c in (D.get("concepts") or {}).values():
        for m in (c.get("members") or []):
            if m.get("b") and m.get("w"):
                out[(m["b"], m["w"])] = m
    return out


def eff(item: dict, df: dict, groups: list[dict], member: dict | None = None, now: dt.datetime | None = None) -> dict:
    """Compute effective request parameters for a card (matching widgets.js eff())."""
    out: dict = {}
    ip = item.get("params") or {}
    for p in (df.get("params") or []):
        pn = p.get("paramName")
        if not pn:
            continue
        v = ip[pn] if pn in ip else p.get("value")
        g = group_of(item, pn, groups)
        if g and not empty(g.get("value")):
            v = country_for(p, g["value"]) if pn == "country" else g["value"]
        out[pn] = resolve_param(v, now=now)
    for k, v in ip.items():
        if k not in out and not empty(v):
            out[k] = resolve_param(v, now=now)
    for g in groups:
        if g.get("name") in (item.get("groups") or []) and g.get("paramName") not in out and not empty(g.get("value")):
            out[g["paramName"]] = resolve_param(g["value"], now=now)
    sl = df.get("symbolList")
    if sl == "watchlist":
        out["list"] = WATCH_DEFAULT["id"]
        out["symbols"] = ",".join(WATCH_DEFAULT["symbols"])
    elif sl == "peers":
        out["peers"] = None
    if member and member.get("sym") and isinstance(out.get("symbol"), str):
        out["symbol"] = symbol_for(out["symbol"], member["sym"])
    return out


def file_param(df: dict) -> dict | None:
    for p in (df.get("params") or []):
        if "fileSelector" in (p.get("roles") or []):
            return p
    return None


def endpoint_key(backend: str, endpoint: str) -> str:
    """Normalize an endpoint into the key format used by terminal/health.json and build_dashboards.py."""
    ep = str(endpoint or "")
    if not ep:
        return ""
    if backend == "x" and ep.startswith("/x/"):
        return "x:" + ep[3:]
    return ep.replace("/api/v1/", "").lstrip("/")


def request_for(
    item: dict,
    df: dict,
    backend: str,
    groups: list[dict],
    bases: dict[str, str],
    member: dict | None = None,
    now: dt.datetime | None = None,
) -> tuple[str | None, str, dict, str]:
    """Return (url or None, note, effective_params, missing_kind)."""
    p = eff(item, df, groups, member=member, now=now)
    req = [r for r in (item.get("require") or []) if empty(p.get(r))]
    if req:
        return None, "requires user input: " + ", ".join(req), p, "require"
    fp = file_param(df)
    missing = [x for x in (df.get("params") or []) if x.get("optional") is False and x is not fp and empty(p.get(x.get("paramName")))]
    if missing:
        names = ", ".join(x.get("label") or x.get("paramName") for x in missing)
        return None, "required parameter empty: " + names, p, "bad-default"
    ep = df["endpoint"]
    base_url = ep if re.match(r"^https?:", ep) else bases[backend] + ep
    return base_url + qs(p), "", p, ""


def provider_of(df: dict) -> str | None:
    for p in (df.get("params") or []):
        if p.get("paramName") == "provider":
            return p.get("value")
    return None


def source_label(df: dict, backend: str) -> str:
    s = df.get("source")
    s = ", ".join(s) if isinstance(s, list) else (s or "")
    return s or (provider_of(df) or "")


def is_oecd(url: str | None, wid: str) -> bool:
    if wid in OECD_X:
        return True
    if not url:
        return False
    return bool(re.search(r"[?&]provider=(oecd|econdb)(&|$)|/api/v1/(oecd|econdb)/", url))


def _http_get_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_catalogs(ports: dict[str, int], skip_v4: bool = False, timeout: float = 30.0) -> tuple[dict, dict[str, dict], bool]:
    """Fetch dashboards and widget catalogs from the running UI and backend processes."""
    bases = bases_for(ports)
    try:
        D = _http_get_json(bases["x"] + "/api/dashboards", timeout=timeout)
        wx = _http_get_json(bases["x"] + "/widgets.json", timeout=timeout)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        raise RuntimeError(f"UI server not reachable on {bases['x']} ({e}). Start the terminal first: launcher.py start") from e
    try:
        w5 = _http_get_json(bases["v5"] + "/widgets.json", timeout=timeout)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        raise RuntimeError(f"V5 backend not reachable on {bases['v5']} ({e}). Start the terminal first: launcher.py start") from e
    v4_up = False
    w4: dict = {}
    if not skip_v4:
        try:
            w4 = _http_get_json(bases["v4"] + "/widgets.json", timeout=timeout)
            v4_up = True
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            print(f"[health] warning: V4 backend not reachable on {bases['v4']} ({e}); skipping V4 cards", flush=True)
    return D, {"v5": w5, "v4": w4, "x": wx}, v4_up


def cards(
    D: dict,
    W: dict[str, dict],
    bases: dict[str, str],
    include_broken: bool = False,
    v4_up: bool = True,
    now: dt.datetime | None = None,
):
    """Yield one dict per placed card (and per library choice)."""
    members = concept_member_map(D)
    for d in (D.get("dashboards") or []):
        groups = d.get("groups") or []
        for ti, t in enumerate(d.get("tabs") or []):
            is_status_tab = t.get("name") == "Data status"
            if is_status_tab and not include_broken:
                continue
            for it in (t.get("items") or []):
                base = {
                    "dashboard": d.get("name", ""),
                    "tab": t.get("name", ""),
                    "tab_index": ti,
                    "uid": it.get("uid", ""),
                    "manual": bool(it.get("manual")),
                    "is_status_tab": is_status_tab,
                    "lib_title": (it.get("title") or "") if it.get("choices") else "",
                }
                variants = []
                if it.get("choices"):
                    for c in it["choices"]:
                        if c.get("w") == it.get("widgetId") and c.get("b") == it.get("backend"):
                            variants.append((dict(it), c, True))
                        else:
                            wmap = W.get(c.get("b") or "") or {}
                            df = wmap.get(c.get("w"))
                            if df is None and not (c.get("b") == "v4" and not v4_up):
                                # Non-default choice missing definition on a running backend should still be surfaced
                                variants.append((
                                    {
                                        "widgetId": c.get("w"),
                                        "backend": c.get("b"),
                                        "params": dict(c.get("p") or {}),
                                        "require": c.get("r"),
                                        "needsKey": c.get("k"),
                                        "groups": [],
                                    },
                                    c,
                                    False,
                                ))
                                continue
                            vgroups = assign_groups(df, groups) if df is not None else []
                            variants.append((
                                {
                                    "widgetId": c.get("w"),
                                    "backend": c.get("b"),
                                    "params": dict(c.get("p") or {}),
                                    "require": c.get("r"),
                                    "needsKey": c.get("k"),
                                    "groups": vgroups,
                                },
                                c,
                                False,
                            ))
                else:
                    variants.append((it, None, True))
                for v, c, is_default in variants:
                    backend = v.get("backend") or ""
                    wid = v.get("widgetId") or ""
                    v4_skipped = backend == "v4" and not v4_up
                    df = (W.get(backend) or {}).get(wid)
                    nkey = c.get("k") if c else v.get("needsKey")
                    row = dict(
                        base,
                        backend=backend,
                        widget=wid,
                        choice=(c.get("l") if c else ""),
                        lib_default=is_default if c else "",
                        title=((c.get("l") if c else it.get("title")) or (df or {}).get("name") or wid),
                        type=(df or {}).get("type"),
                        source=source_label(df or {}, backend),
                        provider=(provider_of(df or {}) or ""),
                        needsKey=nkey or "",
                        v4_skipped=v4_skipped,
                    )
                    if v4_skipped:
                        row.update(url=None, note="v4 backend not running", missing_kind="v4", endpoint="", ep_key="", dataKey=None)
                    elif df is None:
                        row.update(url=None, note="definition missing", missing_kind="missing-def", endpoint="", ep_key="", dataKey=None)
                    else:
                        member = members.get((backend, wid))
                        url, note, _, mkind = request_for(v, df, backend, groups, bases, member=member, now=now)
                        ep = df.get("endpoint") or ""
                        row.update(
                            url=url,
                            note=note,
                            missing_kind=mkind,
                            endpoint=ep,
                            ep_key=endpoint_key(backend, ep),
                            dataKey=(df.get("data") or {}).get("dataKey"),
                        )
                    yield row


def matches_filter(card: dict, filt: str | None) -> bool:
    if not filt:
        return True
    needle = filt.lower()
    hay = " ".join(
        str(card.get(k) or "")
        for k in ("dashboard", "tab", "title", "lib_title", "choice", "widget", "endpoint", "ep_key", "source", "provider", "url")
    ).lower()
    return needle in hay


def pick(o, path: str):
    if not path:
        return o
    for k in path.split("."):
        if o is None:
            return None
        o = o.get(k) if isinstance(o, dict) else None
    return o


def measure(card: dict, body: str) -> tuple[int, int | None, bool, str]:
    """Inspect a 2xx response body -> (rows, traces, empty, kind)."""
    t = card.get("type")
    if t in ("iframe", "html"):
        has = bool(body.strip())
        return (1 if has else 0), None, not has, "html"
    if not body:
        return 0, None, True, "nobody"
    try:
        j = json.loads(body)
    except ValueError:
        return 1, None, False, "text"
    if j is None:
        return 0, None, True, "null"
    key = card.get("dataKey") or ""
    if t == "chart":
        fig = pick(j, key)
        if isinstance(fig, dict) and fig.get("content") and not fig.get("data"):
            fig = fig["content"]
        if isinstance(fig, str):
            try:
                fig = json.loads(fig)
            except ValueError:
                pass
        if isinstance(fig, dict) and isinstance(fig.get("data"), list):
            tr = fig["data"]
            pts = 0
            for x in tr:
                if isinstance(x, dict):
                    for f in ("y", "x", "values", "z", "cells", "lat"):
                        v = x.get(f)
                        if isinstance(v, (list, dict)) and len(v):
                            pts += len(v) if isinstance(v, list) else 1
                            break
            return pts, len(tr), (len(tr) == 0 or pts == 0), "figure"
        if isinstance(j, dict) and isinstance(j.get("results"), list):
            return len(j["results"]), None, not j["results"], "results"
        return 0, None, True, "chart?"
    if t == "markdown":
        d = pick(j, key)
        if isinstance(d, str):
            has = bool(d.strip())
            return (1 if has else 0), None, not has, "md"
        if isinstance(d, list):
            return len(d), None, not d, "mdlist"
        if isinstance(d, dict):
            txt = d.get("content") or d.get("markdown") or d.get("text")
            has = bool(txt or d)
            return (1 if has else 0), None, not has, "mddict"
        return 0, None, True, "md?"
    data = pick(j, key) if key else (j["results"] if isinstance(j, dict) and "results" in j else j)
    if isinstance(data, list):
        return len(data), None, not data, "list"
    if isinstance(data, dict):
        return 1, None, not data, "dict"
    if data is None:
        return 0, None, True, "none"
    return 1, None, False, "scalar"


def _one_get(url: str, timeout: float) -> tuple[int, str, str | None]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return r.status, body, r.headers.get("Content-Length")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp is not None else ""
        clen = e.headers.get("Content-Length") if e.headers else None
        return int(e.code), raw, clen


def fetch_url(
    url: str,
    card: dict,
    timeout: float = DEFAULT_TIMEOUT,
    is_oecd_phase: bool = False,
    conn_waits: tuple[float, ...] = (1.5, 5.0, 10.0, 10.0),
    retry_wait: float = 2.0,
    empty_wait: float = 1.2,
) -> dict:
    """Perform one stdlib HTTP GET with connection & transient-error retries."""
    t0 = time.monotonic()
    rec: dict = {"retried": ""}
    status = -1
    body = ""
    clen = None
    attempts = len(conn_waits) + 1
    for attempt in range(attempts):
        try:
            status, body, clen = _one_get(url, timeout)
            break
        except (TimeoutError, socket.timeout):
            return dict(rec, status=0, secs=round(time.monotonic() - t0, 1), error=f"timeout after {timeout:g} s", body_len=0)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            reason = getattr(e, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                return dict(rec, status=0, secs=round(time.monotonic() - t0, 1), error=f"timeout after {timeout:g} s", body_len=0)
            rec["retried"] += "conn;"
            if attempt < len(conn_waits):
                wait = conn_waits[attempt]
                if wait > 0:
                    time.sleep(wait)
                continue
            return dict(rec, status=-1, secs=round(time.monotonic() - t0, 1), error=redact("connection failed: " + str(e)[:200]), body_len=0)

    if status >= 400 and not is_oecd_phase and body and RETRY_RX.search(body):
        m = RETRY_RX.search(body)
        rec["retried"] += f"ui-retry({m.group(0)});"
        rec["first_error"] = redact(body[:300])
        if retry_wait > 0:
            time.sleep(retry_wait)
        try:
            status, body, clen = _one_get(url, timeout)
        except Exception as e:  # noqa: BLE001
            rec["retried"] += "retry-err;"
            body = body or str(e)

    if 200 <= status < 400 and not body:
        rec["retried"] += "empty-retry;"
        if empty_wait > 0:
            time.sleep(empty_wait)
        try:
            st2, body2, clen2 = _one_get(url, timeout)
            status = st2
            clen = clen2
            body = body2 if st2 < 400 else ""
        except Exception:  # noqa: BLE001
            body = ""

    secs = round(time.monotonic() - t0, 1)
    rec.update(status=status, secs=secs, body_len=len(body or ""), clen=clen)
    if status >= 400:
        err_slice = (body or "")[:3000]
        etext = err_text(err_slice, status)
        rec["err_has_key"] = contains_secret(err_slice) or contains_secret(etext)
        rec["err_has_url"] = bool(URL_RX.search(etext))
        rec["error"] = err_slice
    else:
        rows, traces, emp, kind = measure(card, body or "")
        rec.update(rows=rows, traces=traces, empty=emp, kind=kind)
        if emp:
            rec["sample"] = (body or "")[:300]
    return rec


def err_text(body: str, status: int | None) -> str:
    """Extract readable error text from FastAPI JSON / plain-text responses."""
    d = body
    try:
        j = json.loads(body)
        d = j.get("detail", j) if isinstance(j, dict) else j
    except (ValueError, TypeError):
        pass
    if isinstance(d, list):
        parts = []
        for e in d:
            if isinstance(e, dict):
                loc = ".".join(str(x) for x in (e.get("loc") or [])[1:])
                parts.append((loc + ": " if loc else "") + str(e.get("msg") or json.dumps(e)))
            else:
                parts.append(json.dumps(e))
        d = "; ".join(parts)
    elif isinstance(d, dict):
        d = json.dumps(d)
    txt = str(d or f"HTTP {status}")
    txt = txt.replace("Unexpected Error -> ", "")
    txt = re.sub(r"\s*For further information visit \S+", "", txt)
    return txt


# The terminal's own views (/x/*) say what kind of failure they hit (terminal/composite/core/contract.py): no text matching
X_KIND_CLASS = {
    "input": ("bad-default", "needs user input (the card asks for it)"),
    "not_covered": ("not-covered", "default input not covered by the source"),
    "rate_limited": ("rate-limited", "source rate limit"),
    "timeout": ("upstream-error", "source did not answer in time"),
    "upstream": ("upstream-error", "source unreachable, slow or changed format"),
    "unavailable": ("slow", "terminal busy (all view slots in use); retried later"),
    "internal": ("our-bug", "terminal view raised an internal error"),
}


def error_kind(body) -> str | None:
    """error.kind from a terminal error answer ({error: {kind, ...}}), else None."""
    try:
        j = json.loads(body) if isinstance(body, str) else body
    except (ValueError, TypeError):
        return None
    err = j.get("error") if isinstance(j, dict) else None
    kind = err.get("kind") if isinstance(err, dict) else None
    return kind if kind in X_KIND_CLASS else None


def classify(card: dict, rec: dict | None, include_manual: bool = False) -> tuple[str, str]:
    """Classify a card + response record into a triage bucket and short cause."""
    if card.get("v4_skipped"):
        return "skipped", "skipped: v4 backend not running"
    if card.get("needsKey"):
        return "needs-key", f"{card['needsKey']} key not configured"
    if card.get("manual") and not card.get("is_status_tab") and not include_manual:
        return "skipped", "skipped: click-to-load"
    if card.get("type") in FILE_TYPES:
        return "skipped", "skipped: file download card"
    if card.get("missing_kind") == "require":
        return "skipped", "skipped: " + (card.get("note") or "requires user input")
    if card.get("note") == "definition missing":
        return "our-bug", "widget definition missing from running backend"
    url = card.get("url")
    if not url:
        return "bad-default", "no request sent: " + (card.get("note") or "missing parameter")
    if not str(url).startswith("http://127.0.0.1"):
        return "skipped", "skipped: external page"

    if rec is None:
        return "untested", "not run"
    if rec.get("untested"):
        return "rate-limited", "untested: OECD limit reached earlier in the run"

    st = rec.get("status")
    txt = rec.get("etext") or ""
    prov = (card.get("provider") or "").lower()

    if st == -1:
        return "our-bug", "backend not reachable (crashed/restarting)"
    if st == 0:
        return "upstream-error", "timeout waiting for response"
    if st is None or st < 0:
        return "our-bug", txt[:120] or "runner error"
    if st >= 400:
        kind = error_kind(rec.get("error"))
        if kind:
            return X_KIND_CLASS[kind]
        if st == 429 or re.search(r"rate limit|too many requests", txt, re.I):
            return "rate-limited", "source rate limit"
        if st == 402 or re.search(r"Premium|Special Endpoint|Payment Required|subscription|upgrade|Legacy Endpoint|Restricted Endpoint|exclusive|not available under your", txt, re.I):
            return "paid-plan", (prov or "?") + " paid plan"
        if st == 401 or re.search(r"Missing credential|Missing or invalid credentials|invalid api key|api key is (missing|invalid)|Unauthorized|apikey.*invalid", txt, re.I):
            m = re.search(r"Missing credential '(\w+)'", txt)
            return "needs-key", (m.group(1) if m else (prov or "?") + " key")
        if re.search(r"closed file|database is locked", txt, re.I):
            return "our-bug", "yfinance concurrency (closed file) through run_backend thread patch"
        if re.search(r"not found in the Cboe options directory|CIK not found|Could not find CIK|No CIK|not a valid (symbol|ticker)|Symbol not found|no data found for (the )?(symbol|ticker)|is not supported for|not available for", txt, re.I):
            return "not-covered", "default symbol not covered by source"
        if st == 404 and re.fullmatch(r"\s*Not Found\s*", txt):
            return "our-bug", "route missing in backend (widgets.json advertises a route the backend does not serve)"
        if re.search(r"Connection aborted|ConnectionResetError|timed out|TimeoutError|FiscalData request failed|Error fetching data from|HTTP Error|HTTPError|curl: \(|Expecting value: line 1|Worksheet named|no item named|404 Client Error|50[234]|did not (?:issue|answer|respond)|not answering|not responding|HTML error page instead of JSON", txt):
            return "upstream-error", "source unreachable, slow or changed format"
        m = re.search(r"validation errors? for (\w+)\s+(\w+)\s+Field required", txt)
        if m and "QueryParams" in m.group(1):
            return "bad-default", f"required parameter '{m.group(2)}' not declared in the widget; the UI asks for it"
        if re.search(r"^(Please (enter|select)|Provide )", txt):
            return "bad-default", "needs user input (the UI shows it as a prompt)"
        if card.get("backend") == "x" and not re.search(r"returned|upstream|HTTP Error|timed out|unavailable|blocked|refused|429|403|404|50\d|no data|empty|failed", txt, re.I):
            return "our-bug", "terminal view raised an internal error"
        if re.search(r"validation error for \w+Data\b|KeyError|invalid literal|Mixed timezones|TypeError|AttributeError|IndexError", txt):
            return "upstream-error", "provider parser failed on the source's current format (OpenBB extension code)"
        if st in (400, 422):
            return "bad-default", "default parameters rejected"
        return "upstream-error", "source failed"
    if rec.get("empty"):
        return "empty", "no rows with default parameters"
    if (rec.get("secs") or 0) > SLOW_SECS:
        return "slow", f"{rec.get('secs')} s"
    return "ok", ""


def load_health_file(path: Path | None = None) -> dict[str, str]:
    p = path or HEALTH_FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_history(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_history(path: Path, history: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, sort_keys=True), encoding="utf-8")


def build_candidates(
    rows: list[dict],
    existing_health: dict[str, str],
    history: dict[str, dict],
    run_date: str,
) -> tuple[dict, dict[str, dict]]:
    """Compare live results against terminal/health.json + per-endpoint history."""
    updated: dict[str, dict] = {k: dict(v) for k, v in (history or {}).items() if isinstance(v, dict)}
    by_ep: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        ep = r.get("ep_key") or ""
        if not ep:
            continue
        by_ep[ep].append(r)
        if r["cls"] not in ("skipped", "untested", "needs-key"):
            ent = updated.setdefault(ep, {})
            ent["last_seen"] = run_date
            ent["last_cls"] = r["cls"]
            ent["last_status"] = r["status"]
            if r["cls"] in ("ok", "slow"):
                ent["last_ok"] = run_date

    add: list[dict] = []
    recovered: list[dict] = []
    x_errors: list[dict] = []

    for ep in sorted(by_ep):
        group = by_ep[ep]
        tested = [r for r in group if r["cls"] not in ("skipped", "untested", "needs-key")]
        card_labels = [f"{r['dashboard']} > {r['tab']}: {r['title']}" for r in group]
        if ep.startswith("x:"):
            bad = [r for r in tested if r["cls"] in ("our-bug", "upstream-error")]
            if bad:
                r0 = bad[0]
                x_errors.append({
                    "endpoint": ep,
                    "cls": r0["cls"],
                    "status": r0["status"],
                    "cause": r0["cause"],
                    "error": redact(r0["error"]),
                    "last_seen_ok": updated.get(ep, {}).get("last_ok"),
                    "cards": card_labels,
                })
            continue

        if ep in existing_health:
            ok_rows = [r for r in tested if r["cls"] in ("ok", "slow")]
            if ok_rows:
                r0 = ok_rows[0]
                recovered.append({
                    "endpoint": ep,
                    "current_reason": existing_health[ep],
                    "seen_ok_on": run_date,
                    "rows": r0["rows"],
                    "secs": r0["secs"],
                    "cards": card_labels,
                })
        elif tested and all(r["cls"] == "upstream-error" for r in tested):
            r0 = tested[0]
            add.append({
                "endpoint": ep,
                "suggested_reason": redact(r0["error"] or r0["cause"])[:240],
                "last_seen_ok": updated.get(ep, {}).get("last_ok"),
                "status": r0["status"],
                "cause": r0["cause"],
                "error": redact(r0["error"]),
                "cards": card_labels,
            })

    counts = dict(collections.Counter(r["cls"] for r in rows))
    our_bug_count = counts.get("our-bug", 0)
    err_has_key_count = sum(1 for r in rows if r.get("err_has_key"))
    err_has_url_count = sum(1 for r in rows if r.get("err_has_url"))
    backend_down_count = sum(1 for r in rows if r.get("status") == -1)

    candidates = {
        "summary": {
            "date": run_date,
            "total_cards": len(rows),
            "counts": counts,
            "our_bug": our_bug_count,
            "err_has_key": err_has_key_count,
            "err_has_url": err_has_url_count,
            "backend_down": backend_down_count,
            "health_json_count": len(existing_health),
            "add_count": len(add),
            "recovered_count": len(recovered),
            "x_error_count": len(x_errors),
        },
        "add": add,
        "recovered": recovered,
        "x_errors": x_errors,
    }
    return candidates, updated


def _classify_row(card: dict, rec: dict | None, include_manual: bool = False) -> dict:
    raw = ""
    if rec is not None and rec.get("status") is not None and rec.get("status", 0) >= 400:
        raw = err_text(rec.get("error") or "", rec["status"])
    elif rec is not None and rec.get("error"):
        raw = str(rec["error"])
    if rec is not None:
        rec["etext"] = raw
    has_url = bool((rec or {}).get("err_has_url")) or bool(URL_RX.search(raw))
    has_key = bool((rec or {}).get("err_has_key")) or contains_secret(raw)
    cls, cause = classify(card, rec, include_manual=include_manual)
    return {
        **card,
        "status": (rec or {}).get("status", ""),
        "secs": (rec or {}).get("secs", ""),
        "rows": (rec or {}).get("rows", ""),
        "traces": (rec or {}).get("traces", ""),
        "empty": (rec or {}).get("empty", ""),
        "retried": (rec or {}).get("retried", ""),
        "error": redact(raw)[:400].replace("\n", " "),
        "err_has_url": has_url,
        "err_has_key": has_key,
        "cls": cls,
        "cause": cause,
        "sample": redact((rec or {}).get("sample", "")),
        "url": redact(card.get("url") or ""),
    }


def _should_skip_card(c: dict, include_manual: bool) -> bool:
    if c.get("v4_skipped") or c.get("needsKey"):
        return True
    if c.get("manual") and not c.get("is_status_tab") and not include_manual:
        return True
    if c.get("type") in FILE_TYPES:
        return True
    url = c.get("url")
    if not url or not str(url).startswith("http://127.0.0.1"):
        return True
    return False


def run_health(
    ui_port: int | None = None,
    v5_port: int | None = None,
    v4_port: int | None = None,
    phase: str = "all",
    filt: str | None = None,
    limit: int = 0,
    dry_run: bool = False,
    resume: bool = False,
    include_broken: bool = False,
    include_manual: bool = False,
    skip_v4: bool = False,
    gap: float = DEFAULT_GAP,
    oecd_gap: float = DEFAULT_OECD_GAP,
    timeout: float = DEFAULT_TIMEOUT,
    out_dir: Path | str | None = None,
    cache_dir: Path | str | None = None,
    run_date: str | None = None,
    catalogs: tuple[dict, dict[str, dict], bool] | None = None,
    health_file: Path | str | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Execute a paced health check, write dated CSV & candidates JSON, return summary dict."""
    date_str = run_date or dt.date.today().isoformat()
    out_path = Path(out_dir) if out_dir else (local_paths.LOCAL / "notes")
    cache_path = Path(cache_dir) if cache_dir else local_paths.CACHE_DIR
    ports = resolve_ports(ui_port=ui_port, v5_port=v5_port, v4_port=v4_port)
    bases = bases_for(ports)

    if catalogs is not None:
        D, W, v4_up = catalogs
    else:
        D, W, v4_up = load_catalogs(ports, skip_v4=skip_v4, timeout=min(timeout, 30.0))

    all_cards = list(cards(D, W, bases, include_broken=include_broken, v4_up=v4_up, now=now))
    if filt:
        all_cards = [c for c in all_cards if matches_filter(c, filt)]

    urls: dict[str, dict] = {}
    for c in all_cards:
        if _should_skip_card(c, include_manual=include_manual):
            continue
        urls.setdefault(c["url"], c)

    normal = [(u, c) for u, c in urls.items() if not is_oecd(u, c["widget"])]
    oecd = [(u, c) for u, c in urls.items() if is_oecd(u, c["widget"])]

    phases = []
    if phase in ("all", "normal"):
        phases.append(("normal", normal, gap))
    if phase in ("all", "oecd"):
        phases.append(("oecd", oecd, oecd_gap))

    if limit and limit > 0:
        capped_phases = []
        remaining = limit
        for name, lst, pgap in phases:
            if remaining <= 0:
                break
            chunk = lst[:remaining]
            capped_phases.append((name, chunk, pgap))
            remaining -= len(chunk)
        phases = capped_phases

    if dry_run:
        pre_counts = collections.Counter(
            classify(c, None, include_manual=include_manual)[0] for c in all_cards if _should_skip_card(c, include_manual=include_manual)
        )
        n_norm = sum(len(lst) for name, lst, _ in phases if name == "normal")
        n_oecd = sum(len(lst) for name, lst, _ in phases if name == "oecd")
        print(
            f"[health --dry-run] cards={len(all_cards)} unique_urls={n_norm + n_oecd} "
            f"(normal={n_norm}, oecd={n_oecd}) pre_classified={dict(pre_counts)}",
            flush=True,
        )
        return {
            "dry_run": True,
            "exit_code": 0,
            "cards": len(all_cards),
            "normal_urls": n_norm,
            "oecd_urls": n_oecd,
            "pre_counts": dict(pre_counts),
        }

    state_file = cache_path / f"health-run-{date_str}.jsonl"
    done: dict[str, dict] = {}
    if resume and state_file.exists():
        for line in state_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("url"):
                    done[rec["url"]] = rec
            except ValueError:
                pass

    cache_path.mkdir(parents=True, exist_ok=True)
    log_file = state_file.open("a" if resume else "w", encoding="utf-8")
    stop_oecd = False
    try:
        for name, lst, pgap in phases:
            todo = [(u, c) for u, c in lst if redact(u) not in done]
            print(f"[health] phase={name}: {len(todo)} todo of {len(lst)}", flush=True)
            for idx, (u, c) in enumerate(todo, 1):
                if name == "oecd" and stop_oecd:
                    rec = {"status": None, "secs": 0, "error": "not tested: OECD rate limit reached earlier in this run", "untested": True}
                else:
                    t0 = time.monotonic()
                    try:
                        rec = fetch_url(u, c, timeout=timeout, is_oecd_phase=(name == "oecd"))
                    except Exception as e:  # noqa: BLE001
                        rec = {"status": -2, "secs": round(time.monotonic() - t0, 1), "error": redact("runner exception: " + repr(e)[:300])}
                    if name == "oecd" and (rec.get("status") == 429 or re.search(r"rate limit|Too Many Requests", rec.get("error") or "", re.I)):
                        stop_oecd = True
                        print("[health] OECD rate limit hit; stopping further OECD requests", flush=True)
                    effective_gap = max(pgap, 2.0) if (pgap > 0 and name == "normal" and EIA_RX.search(u)) else pgap
                    wait = effective_gap - (time.monotonic() - t0)
                    if wait > 0 and idx < len(todo):
                        time.sleep(wait)
                safe_u = redact(u)
                stored = {**rec, "url": safe_u, "phase": name, "ts": dt.datetime.now().strftime("%H:%M:%S")}
                if "error" in stored and stored["error"]:
                    stored["error"] = redact(stored["error"])
                if "sample" in stored and stored["sample"]:
                    stored["sample"] = redact(stored["sample"])
                done[safe_u] = stored
                log_file.write(json.dumps(stored) + "\n")
                log_file.flush()
                if idx % 25 == 0 or stored.get("status") not in (200,) or len(todo) <= 10:
                    print(f"[health:{name} {idx}/{len(todo)}] status={stored.get('status')} {stored.get('secs')}s {safe_u[:120]}", flush=True)
    finally:
        log_file.close()

    joined: list[dict] = []
    for c in all_cards:
        rec = None if _should_skip_card(c, include_manual=include_manual) else done.get(redact(c["url"]))
        joined.append(_classify_row(c, rec, include_manual=include_manual))

    existing_health = load_health_file(Path(health_file) if health_file else HEALTH_FILE)
    history_path = cache_path / "health-history.json"
    history = load_history(history_path)
    candidates, updated_history = build_candidates(joined, existing_health, history, date_str)
    save_history(history_path, updated_history)

    out_path.mkdir(parents=True, exist_ok=True)
    csv_path = out_path / f"health-{date_str}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in joined:
            w.writerow(r)

    cand_path = out_path / f"health-candidates-{date_str}.json"
    cand_path.write_text(json.dumps(candidates, indent=2), encoding="utf-8")

    s = candidates["summary"]
    print(f"[health] date={date_str} cards={s['total_cards']} counts={s['counts']}", flush=True)
    print(
        f"[health] G2 check: our-bug={s['our_bug']} backend-down={s['backend_down']} "
        f"err_has_key={s['err_has_key']} err_has_url={s['err_has_url']} "
        f"candidates(add={s['add_count']}, recovered={s['recovered_count']}, x_errors={s['x_error_count']})",
        flush=True,
    )
    if s["our_bug"]:
        for r in [x for x in joined if x["cls"] == "our-bug"][:10]:
            print(f"  [our-bug] {r['dashboard']} > {r['tab']}: {r['title']} ({r['widget']}) -> {r['cause']}", flush=True)
    print(f"[health] wrote {csv_path} and {cand_path}", flush=True)

    exit_code = 1 if (s["our_bug"] > 0 or s["err_has_key"] > 0 or s["backend_down"] > 0) else 0
    return {
        "dry_run": False,
        "exit_code": exit_code,
        "csv_path": str(csv_path),
        "candidates_path": str(cand_path),
        "state_path": str(state_file),
        "history_path": str(history_path),
        "summary": s,
        "candidates": candidates,
        "rows": joined,
    }


def add_health_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", default="all", choices=["all", "normal", "oecd"], help="run all cards, only normal phase, or only OECD/EconDB phase")
    parser.add_argument("--filter", dest="filt", default=None, help="case-insensitive substring filter over card metadata/URL")
    parser.add_argument("--limit", type=int, default=0, help="cap the number of unique URLs executed (0 = no cap)")
    parser.add_argument("--dry-run", action="store_true", help="resolve card URLs and print counts without sending card requests")
    parser.add_argument("--resume", action="store_true", help="resume from today's local/cache/health-run-YYYY-MM-DD.jsonl")
    parser.add_argument("--include-broken", action="store_true", help="also test Data status cards (terminal/health.json)")
    parser.add_argument("--include-manual", action="store_true", help="also test click-to-load manual cards")
    parser.add_argument("--with-v4", action="store_true", help="also check the optional legacy V4 backend")
    parser.add_argument("--skip-v4", action="store_true", help="skip checking the optional V4 backend (default unless --with-v4)")
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP, help="seconds between normal requests (default 0.7)")
    parser.add_argument("--oecd-gap", type=float, default=DEFAULT_OECD_GAP, help="seconds between OECD/EconDB requests (default 3.0)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP read timeout per request in seconds (default 90)")
    parser.add_argument("--out-dir", default=None, help="directory for CSV and candidates JSON (default local/notes/)")
    parser.add_argument("--cache-dir", default=None, help="directory for resumable JSONL & health-history.json (default local/cache/)")
    parser.add_argument("--date", dest="run_date", default=None, help="override YYYY-MM-DD run date")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Paced live health runner for OpenBB Terminal cards.")
    ap.add_argument("--ui-port", type=int)
    ap.add_argument("--v5-port", type=int)
    ap.add_argument("--v4-port", type=int)
    add_health_args(ap)
    a = ap.parse_args(argv)
    try:
        res = run_health(
            ui_port=a.ui_port,
            v5_port=a.v5_port,
            v4_port=a.v4_port,
            phase=a.phase,
            filt=a.filt,
            limit=a.limit,
            dry_run=a.dry_run,
            resume=a.resume,
            include_broken=a.include_broken,
            include_manual=a.include_manual,
            skip_v4=(a.skip_v4 or not a.with_v4),
            gap=a.gap,
            oecd_gap=a.oecd_gap,
            timeout=a.timeout,
            out_dir=a.out_dir,
            cache_dir=a.cache_dir,
            run_date=a.run_date,
        )
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr, flush=True)
        return 1
    return int(res.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
