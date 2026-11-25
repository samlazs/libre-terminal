"""The response envelope every /x/* view answers with, checked in one place (composite.dispatch and server.py).

Success: the view's payload plus
  sources  non-empty list of {name, detail, url}: what this answer was actually built from
  as_of    ISO date or datetime of the newest data point, or as_of_na: why the answer has no data date
  status   optional, one of ok | partial | stale | fallback
  notes    optional list of plain-text notes (stale, cached, delayed, substitutions)
Error (every non-2xx, /x/* and /api/*):
  {"error": {"kind", "message", "retryable", "retry_after"?}, "detail": message (kept for older pages), "sources"}
"""

from __future__ import annotations

import contextvars
import inspect
import importlib
import logging
import os
import re
import time

STATUSES = ("ok", "partial", "stale", "fallback")
ERROR_KINDS = ("input", "not_covered", "upstream", "rate_limited", "timeout", "unavailable", "internal")
RETRYABLE_KINDS = frozenset({"upstream", "rate_limited", "timeout", "unavailable"})
KIND_BY_STATUS = {400: "input", 403: "input", 404: "input", 405: "input", 409: "input", 413: "input", 415: "input",
                  422: "input", 429: "rate_limited", 500: "internal", 502: "upstream", 503: "unavailable", 504: "timeout"}
STATUS_BY_KIND = {"input": 400, "not_covered": 404, "rate_limited": 429, "timeout": 504, "upstream": 502,
                  "unavailable": 503, "internal": 500}
NO_DATE = "This answer has no dated data points (links, a directory or a prompt)."
_RATE_RE = re.compile(r"\b429\b|too many requests|rate.?limit", re.I)
_TIMEOUT_RE = re.compile(r"did not answer in time|timed? ?out\b|\btimeout\b|slow to answer the first request", re.I)
_INPUT_RE = re.compile(r"^(?:Click|Enter|Choose|Pick|Type|Select|Give|Provide|Please)\b|\bis required\b|^Bad parameters", re.I)
_NOT_COVERED_RE = re.compile(r"\bnot found\b|not covered|\bno (?:data|options|results|price history|filings?|quotes?)\b|covers U\.S\.|"
                             r"is not (?:supported|available) for|\bhas no\b|\bhas nothing for\b|\bunknown (?:symbol|ticker|series|country)", re.I)

_ISO = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_DATE_KEYS = ("as_of", "date", "period", "time", "datetime", "timestamp", "last_trade", "updated")
_ROW_LISTS = ("results", "rows", "data", "table", "points", "series")
_log = logging.getLogger("terminal.contract")
_logged: set = set()


class ContractViolation(Exception):
    """A view answered outside the envelope (raised only when TERMINAL_STRICT_CONTRACT=1, as in the offline tests)."""


def strict() -> bool:
    return os.environ.get("TERMINAL_STRICT_CONTRACT", "").strip() == "1"


def is_iso(v) -> bool:
    return isinstance(v, str) and bool(_ISO.match(v.strip()))


def _newest(values) -> str | None:
    best = None
    for v in values:
        if is_iso(v):
            v = v.strip()
            if best is None or v[:10] > best[:10] or (v[:10] == best[:10] and len(v) > len(best)):
                best = v
    return best


def derive_as_of(out: dict) -> str | None:
    """The newest ISO date in a view's answer: its own as_of, row dates in its tables, or the x values of its chart."""
    if not isinstance(out, dict):
        return None
    if is_iso(out.get("as_of")):
        return out["as_of"].strip()
    cands = []
    for key in _ROW_LISTS:
        rows = out.get(key)
        if isinstance(rows, list):
            for r in rows[:5000]:
                if isinstance(r, dict):
                    cands += [r.get(k) for k in _DATE_KEYS]
    fig = out.get("figure") if isinstance(out.get("figure"), dict) else out
    traces = fig.get("data") if isinstance(fig, dict) else None
    if isinstance(traces, list):
        for t in traces:
            xs = t.get("x") if isinstance(t, dict) else None
            if isinstance(xs, list) and xs:
                cands += [xs[-1], xs[0]] + (xs if len(xs) <= 50 else [])
    return _newest(cands)


def finish(out: dict) -> dict:
    """A copy of a successful answer with as_of (or as_of_na) and status filled in. Never changes the cached object."""
    add = {}
    if not is_iso(out.get("as_of")) and not out.get("as_of_na"):
        derived = derive_as_of(out)
        if derived:
            add["as_of"] = derived
        else:
            add["as_of_na"] = NO_DATE
    if "status" not in out:
        add["status"] = "stale" if out.get("stale") else "ok"
    have = out.get("notes")
    notes = [str(out[k]) for k in ("stale", "cached", "delayed") if out.get(k)]
    if not have:
        if notes:
            add["notes"] = notes
    elif isinstance(have, list) and any(n not in have for n in notes):   # a view's own notes (a partial answer) keep these too
        add["notes"] = have + [n for n in notes if n not in have]
    return dict(out, **add) if add else out


def violations(out) -> list[str]:
    """Why an answer is outside the success envelope (empty when it is inside)."""
    if not isinstance(out, dict):
        return [f"answer is a {type(out).__name__}, not an object"]
    v = []
    srcs = out.get("sources")
    if not isinstance(srcs, list) or not srcs:
        v.append("sources missing or empty")
    elif not all(isinstance(s, dict) and s.get("name") and {"name", "detail", "url"} <= set(s) for s in srcs):
        v.append("a source is not {name, detail, url}")
    if not is_iso(out.get("as_of")) and not (isinstance(out.get("as_of_na"), str) and out["as_of_na"].strip()):
        v.append("neither an ISO as_of nor an as_of_na reason")
    if "status" in out and out["status"] not in STATUSES:
        v.append(f"status {out['status']!r} is not one of {', '.join(STATUSES)}")
    if "notes" in out and not (isinstance(out["notes"], list) and all(isinstance(n, str) for n in out["notes"])):
        v.append("notes is not a list of text")
    return v


def enforce(name: str, out) -> None:
    """Raise (strict mode) or log once per view when an answer is outside the envelope."""
    v = violations(out)
    if not v:
        return
    if strict():
        raise ContractViolation(f"view {name}: " + "; ".join(v))
    if name not in _logged:
        _logged.add(name)
        _log.warning("view %s answered outside the envelope: %s", name, "; ".join(v))


def enforce_finite(name: str, out) -> None:
    """Strict mode: a NaN or Inf anywhere in a view's answer raises (in production the server scrubs it to null and
    logs the view once, server.py _dumps_strict)."""
    if not strict():
        return
    from .num import nonfinite_path
    where = nonfinite_path(out)
    if where:
        raise ContractViolation(f"view {name}: non-finite number at {where}")


def kind_of(exc) -> str:
    """The one place an error is classified: the kind the raiser gave, else read once from its message."""
    k = getattr(exc, "kind", None)
    if k in ERROR_KINDS:
        return k
    m = str(exc)
    if _RATE_RE.search(m):
        return "rate_limited"
    if _TIMEOUT_RE.search(m):
        return "timeout"
    if _INPUT_RE.search(m):
        return "input"
    if _NOT_COVERED_RE.search(m):
        return "not_covered"
    return "upstream"


def error_body(kind: str, message: str, *, retryable: bool | None = None, retry_after: float | None = None,
               sources: list | None = None, **extra) -> dict:
    """The one error object every non-2xx answer carries."""
    kind = kind if kind in ERROR_KINDS else "internal"
    err = {"kind": kind, "message": str(message), "retryable": bool(kind in RETRYABLE_KINDS if retryable is None else retryable)}
    if retry_after is not None:
        err["retry_after"] = max(0, int(round(float(retry_after))))
    return {"error": err, "detail": str(message), "sources": list(sources or []), **extra}


def with_error(code: int, obj: dict) -> dict:
    """Give an error answer built elsewhere ({detail, ...}) the shared error object, keeping its other fields."""
    if not isinstance(obj, dict) or isinstance(obj.get("error"), dict):
        return obj
    message = str(obj.get("detail") or "Request failed.")
    kind = KIND_BY_STATUS.get(code, "internal" if code >= 500 else "input")
    rest = {k: v for k, v in obj.items() if k not in ("detail", "error")}
    return {**error_body(kind, message, sources=rest.pop("sources", None)), **rest}


def target(fn):
    """The function a lazy view wrapper calls (its signature is the one parameters are checked against)."""
    t = getattr(fn, "_target", None)
    if t:
        return getattr(importlib.import_module(t[0]), t[1])
    return fn


def bad_params(fn, params: dict) -> str | None:
    """Why these parameters do not fit the view's signature (None when they do)."""
    try:
        sig = inspect.signature(target(fn))
    except (TypeError, ValueError):
        return None
    try:
        sig.bind(**params)
    except TypeError as e:
        return str(e)
    return None


# ---------------------------------------------------------------- time budgets: one table for the server and the browser
# Seconds a request may take on the server. The browser waits CLIENT_MARGIN_S longer (served by /api/config), so a view
# always answers (data, partial, or a typed timeout) before the card gives up. A /x/ view runs under a deadline (dispatch):
# every outbound call is cut to the time left, so a slow source makes the answer partial instead of the card time out.
CLIENT_MARGIN_S = 5
DEFAULT_BUDGET_S = 40
SLOW_BUDGET_S = 65                 # slow public datasets (OECD, IMF, BLS, SEC...), files, options chains
_SLOW_VIEWS = ("global_yields", "policy_path", "yield_curves", "yield_curve_table", "rate_history", "rates_by_country", "compare",
               "earnings_release", "valuation_bands", "roic_dupont", "gex_strike", "vol_cone", "debt_maturity_wall", "etf_overlap",
               "correlation", "risk_stats", "fx_rate_diff")
_FAST_VIEWS = ("board", "watchlist", "perf_bars", "performance", "style_box", "snapshot", "comps", "news", "fx_matrix", "fx_moves",
               "fx_trend", "crypto_vol", "cds_indices")
ROUTE_BUDGETS = {
    "default": DEFAULT_BUDGET_S,
    "files": SLOW_BUDGET_S,
    # /x/<view>
    "views": {**{v: SLOW_BUDGET_S for v in _SLOW_VIEWS}, "chart": 55, **{v: 25 for v in _FAST_VIEWS}},
    # backend routes the page calls directly (first match wins); the regexes are valid in Python and JavaScript
    "backend": [
        (r"/api/v1/(?:cboe/options/|derivatives/options/chains)\b", SLOW_BUDGET_S),
        (r"[?&]provider=(?:oecd|imf|eia|bls|sec|cftc|usda|fred|congress_gov|famafrench|us_treasury|federal_reserve|ecb)(?:&|$)", SLOW_BUDGET_S),
        (r"/api/v1/(?:oecd|imf|eia|bls|sec|cftc|usda|uscongress|ustreasury|federal_reserve|ecb|famafrench|jodi)/", SLOW_BUDGET_S),
        (r"[?&]provider=(?:yfinance|cboe|tmx|nasdaq|deribit|finviz)(?:&|$)", 25),
        (r"/api/v1/(?:yfinance|cboe|tmx|nasdaq|deribit|finviz)/", 25),
    ],
}


def budget_for(view: str) -> float:
    """Seconds the view may take on the server."""
    return float(ROUTE_BUDGETS["views"].get(view, ROUTE_BUDGETS["default"]))


def client_budgets() -> dict:
    """The browser's per-route timeouts in milliseconds (server budget + CLIENT_MARGIN_S), for /api/config."""
    ms = lambda s: int((s + CLIENT_MARGIN_S) * 1000)  # noqa: E731
    return {"default_ms": ms(ROUTE_BUDGETS["default"]), "files_ms": ms(ROUTE_BUDGETS["files"]),
            "views": {k: ms(v) for k, v in ROUTE_BUDGETS["views"].items()},
            "patterns": [[p, ms(s)] for p, s in ROUTE_BUDGETS["backend"]]}


_DEADLINE = contextvars.ContextVar("view_deadline", default=None)
DEADLINE_RESERVE_S = 1.0           # kept back from every outbound call to build and send the answer


def set_deadline(seconds: float):
    """Start a deadline `seconds` from now for the request running in this context (an earlier one already running is
    kept); returns the token to reset it."""
    new = time.monotonic() + float(seconds)
    cur = _DEADLINE.get()
    return _DEADLINE.set(new if cur is None else min(cur, new))


def time_left(cap: float | None = None) -> float | None:
    """Seconds an outbound call or a wait may still take under the request's deadline (minus the reserve), at most cap.
    None when no deadline is running and no cap is given (background work: the caller's own timeouts apply)."""
    dl = _DEADLINE.get()
    if dl is None:
        return cap
    left = dl - time.monotonic() - DEADLINE_RESERVE_S
    return left if cap is None else min(cap, left)
