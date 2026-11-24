"""HTTP, OpenBB backend calls, URL downloads, and source-contract helpers."""

from __future__ import annotations

import json
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import composite as C

# Backend ports: the launcher passes TERMINAL_V5_PORT / TERMINAL_V4_PORT when other ports are chosen.
V5 = f"http://127.0.0.1:{os.environ.get('TERMINAL_V5_PORT') or 6900}/api/v1"

V4 = f"http://127.0.0.1:{os.environ.get('TERMINAL_V4_PORT') or 6901}/api/v1"


class CompositeError(Exception):
    """A view could not answer. kind is one of core.contract.ERROR_KINDS (None: classified by the server);
    status overrides the HTTP status the UI server answers with (400 for input, 404 for an unknown view)."""

    def __init__(self, msg, *, transient=False, kind=None, retry_after=None, status=None):
        super().__init__(msg)
        self.kind = kind
        # with a kind, "worth retrying" follows from it (contract.RETRYABLE_KINDS); without one, the caller says
        self.transient = kind in ("upstream", "rate_limited", "timeout", "unavailable") if kind else bool(transient)
        self.retry_after = retry_after
        self.status = status


_ORIG_URLOPEN = urllib.request.urlopen
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_RETRY_DELAYS = (0.4, 1.0)          # sleep before the 2nd, 3rd attempt (tests set zeros; the last value repeats)
_NO_RETRY_RE = re.compile(r"(?:[?&]provider=(?:oecd|nasdaq)(?:&|$)|/(?:oecd|nasdaq)/|sdmx\.oecd\.org|stats\.oecd\.org|api\.nasdaq\.com)", re.I)
DEFAULT_UA = "Mozilla/5.0 (compatible; terminal-ui)"
COOLDOWN_CAP = 600.0                # seconds: a Retry-After longer than this is not believed
_NET = "net"                        # in retry_on: connection errors and timeouts

# One outbound policy per provider. timeout: seconds per attempt (a tuple: per attempt); attempts: tries in all;
# retry_on: HTTP statuses (and "net") worth another attempt; max_concurrency: requests in flight at once (None: no
# cap); min_interval: seconds kept between one request's end and the next one's start; cooldown_on_429: seconds the
# provider is not asked after a 429 without Retry-After (Retry-After wins, capped at COOLDOWN_CAP; 0: no cooldown);
# down_for: seconds the provider is skipped after every attempt failed; ua: User-Agent.
PROVIDERS = {
    "backend":     dict(timeout=45, attempts=3, retry_on=(502, 503, 504, _NET), max_concurrency=None, min_interval=0.0,
                        cooldown_on_429=30, down_for=0, ua="openbb-terminal-ui"),
    # OECD and Nasdaq (direct, or through the backend's provider=oecd / provider=nasdaq) are never retried
    "oecd":        dict(timeout=60, attempts=1, retry_on=(), max_concurrency=None, min_interval=0.0, cooldown_on_429=600,
                        down_for=0, ua="openbb-terminal-ui"),
    "nasdaq":      dict(timeout=45, attempts=1, retry_on=(), max_concurrency=None, min_interval=0.0, cooldown_on_429=120,
                        down_for=0, ua="openbb-terminal-ui"),
    # FRED's edge stalls browser-like agents and answers the plain Python one; it is not retried on 429
    "fred":        dict(timeout=(15, 25), attempts=2, retry_on=(408, 500, 502, 503, 504, _NET), max_concurrency=4,
                        min_interval=0.0, cooldown_on_429=60, down_for=120,
                        ua=f"Python-urllib/{sys.version_info[0]}.{sys.version_info[1]}"),
    "multpl":      dict(timeout=20, attempts=2, retry_on=(502, 503, 504, _NET), max_concurrency=2, min_interval=0.0,
                        cooldown_on_429=300, down_for=0,
                        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    # Google News answers 429 to a burst (probed: the 4th query within a few seconds): one at a time, 2.5 s apart
    "google_news": dict(timeout=15, attempts=1, retry_on=(), max_concurrency=1, min_interval=2.5, cooldown_on_429=300,
                        down_for=0, ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "default":     dict(timeout=60, attempts=2, retry_on=(408, 502, 503, 504, _NET), max_concurrency=None, min_interval=0.0,
                        cooldown_on_429=60, down_for=0, ua=DEFAULT_UA),
}

_state_lock = threading.Lock()
_cool: dict = {}                    # cooldown key -> monotonic time the provider may be asked again
_down: dict = {}                    # provider -> (monotonic time it may be asked again, message)
_sems: dict = {}                    # provider -> BoundedSemaphore
_pace: dict = {}                    # provider -> monotonic time the next request may start


def _no_retry(url: str) -> bool:
    return bool(_NO_RETRY_RE.search(str(url or "")))


def provider_of(url: str) -> str:
    """The policy a URL falls under (PROVIDERS key)."""
    u = str(url or "")
    low = u.lower()
    if re.search(r"[?&]provider=oecd(?:&|$)|/oecd/|oecd\.org", low):
        return "oecd"
    if re.search(r"[?&]provider=nasdaq(?:&|$)|/nasdaq/|api\.nasdaq\.com", low):
        return "nasdaq"
    if u.startswith((C.V5, C.V4)):
        return "backend"
    host = low.split("/")[2] if "://" in low else ""
    if host.endswith("stlouisfed.org"):
        return "fred"
    if host.endswith("multpl.com"):
        return "multpl"
    if host == "news.google.com":
        return "google_news"
    return "default"


def _cool_key(url: str, provider: str) -> str:
    """What a 429 blocks: a named provider as a whole; the backend per upstream provider; anything else per host."""
    if provider == "backend":
        m = re.search(r"[?&]provider=([\w-]+)", url)
        return "backend:" + (m.group(1).lower() if m else urllib.parse.urlsplit(url).path)
    if provider == "default":
        return "host:" + (urllib.parse.urlsplit(url).hostname or "")
    return provider


def reset_outbound(provider: str | None = None) -> None:
    """Forget cooldowns, outages and pacing (all, or one provider's): after a restart, and between tests."""
    with _state_lock:
        for d in (_cool, _down, _pace):
            for k in [k for k in d if provider is None or k == provider or str(k).startswith(provider + ":")]:
                d.pop(k, None)


def _sem(provider: str, cap):
    with _state_lock:
        s = _sems.get(provider)
        if s is None or getattr(s, "_cap", None) != cap:
            s = _sems[provider] = threading.BoundedSemaphore(cap)
            s._cap = cap
        return s


def _retry_after(headers) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or an HTTP date); None when absent or unreadable."""
    raw = (headers.get("Retry-After") if headers is not None else None) or ""
    raw = str(raw).strip()
    if not raw:
        return None
    if raw.isdigit():
        return float(raw)
    try:
        import email.utils
        when = email.utils.parsedate_to_datetime(raw)
        return max(0.0, when.timestamp() - time.time())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _status_kind(code: int) -> str | None:
    """The error kind of an HTTP status from a source (contract.ERROR_KINDS)."""
    if code == 429:
        return "rate_limited"
    if code in (408, 504):
        return "timeout"
    if code == 404:
        return "not_covered"
    if code in (400, 422):
        return None                  # a backend's 400/422 is often a provider's own error: the server reads its message once
    return "upstream"


def _net_kind(e) -> str:
    """timeout for a connect/read timeout, upstream for anything else that kept the answer from arriving."""
    reason = getattr(e, "reason", e)
    if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
        return "timeout"
    return "upstream"


def _parse_json(body: bytes, url: str):
    """Parse a JSON answer. NaN/Infinity become None (a number is either right or blank), and a body that is not JSON
    (an HTML error or login page, a truncated answer) is a transient CompositeError, never a raw ValueError."""
    try:
        return json.loads(body, parse_constant=lambda _c: None)
    except (ValueError, UnicodeDecodeError):
        head = body[:200].lstrip().lower()
        what = "an HTML page" if head.startswith((b"<!doctype", b"<html", b"<")) else "a reply that is not JSON"
        raise C.CompositeError(f"{C._which(url)} answered with {what} instead of data.", kind="upstream") from None


def _transport(req, timeout, context=None):
    """The single outbound seam: every request is opened here (through fetch).
    Offline tests inject faults by replacing composite._transport (scripts/tests/_fixtures.py)."""
    url = req.full_url
    if url.startswith((C.V5, C.V4)) and urllib.request.urlopen is _ORIG_URLOPEN:
        return _LOCAL_OPENER.open(req, timeout=timeout)     # bypass corporate/system proxies on loopback requests
    if context is not None:
        return urllib.request.urlopen(req, timeout=timeout, context=context)
    return urllib.request.urlopen(req, timeout=timeout)


def _fail(msg, *, kind, http_status=None, retry_after=None):
    e = C.CompositeError(msg, kind=kind, retry_after=retry_after)
    e.http_status = http_status
    return e


def fetch(url, *, provider=None, parse="bytes", timeout=None, data=None, ua=None, headers=None):
    """GET (or POST `data`) one URL under its provider's policy; the one place the terminal opens a connection.

    parse: "json" (NaN -> None; empty body -> {"results": []}; non-JSON -> upstream error), "text" (UTF-8) or "bytes".
    timeout: overall seconds for all attempts (default: the policy's per-attempt timeouts).
    Raises CompositeError with a kind (and .http_status when the source answered with an error status)."""
    pname = provider or provider_of(url)
    pol = PROVIDERS.get(pname) or PROVIDERS["default"]
    per = pol["timeout"] if isinstance(pol["timeout"], (tuple, list)) else (pol["timeout"],) * pol["attempts"]
    attempts = max(1, min(pol["attempts"], len(per)))
    if pname == "backend" and _no_retry(url):
        attempts = 1
    budget = float(timeout) if timeout else float(sum(per[:attempts]))
    deadline = time.monotonic() + max(1.0, budget)
    is_local = url.startswith((C.V5, C.V4))
    label = C._which(url)
    ckey = _cool_key(url, pname)

    now = time.monotonic()
    with _state_lock:
        until = _cool.get(ckey, 0.0)
        down = _down.get(pname)
    if now < until:                                       # cooling down after a 429: no network I/O at all
        left = until - now
        raise _fail(f"{label} is limiting requests; try again in ~{int(left) + 1} s.", kind="rate_limited", retry_after=left)
    if down and now < down[0]:
        raise _fail(down[1], kind="upstream")

    h = {"User-Agent": ua or pol["ua"], "Accept": "application/json" if parse == "json" else "*/*"}
    if is_local:
        h["X-Terminal-Pool"] = "x"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    ctx = C._ssl_context() if url.lower().startswith("https://") else None
    sem = _sem(pname, pol["max_concurrency"]) if pol["max_concurrency"] else None
    retry_on = set(pol["retry_on"])
    last: CompositeError | None = None
    for attempt in range(attempts):
        if attempt:
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)] if _RETRY_DELAYS else 0.0
            if delay > 0:
                time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        rem = deadline - time.monotonic()
        if rem <= 0 and attempt:
            break
        this_timeout = max(1.0, min(per[min(attempt, len(per) - 1)], rem) if not timeout else rem)
        if sem is not None and not sem.acquire(timeout=max(0.1, rem)):
            raise _fail(f"{label} did not answer in time (too many requests waiting).", kind="timeout")
        try:
            if pol["min_interval"]:
                with _state_lock:
                    wait = _pace.get(pname, 0.0) - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            try:
                with C._transport(req, this_timeout, ctx) as r:
                    body = r.read()
            finally:
                if pol["min_interval"]:
                    with _state_lock:
                        _pace[pname] = time.monotonic() + pol["min_interval"]
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read() or b""
            except Exception:  # noqa: BLE001
                pass
            if e.code == 429:
                ra = _retry_after(e.headers)
                cool = min(COOLDOWN_CAP, ra if ra is not None else float(pol["cooldown_on_429"] or 0))
                if cool > 0:
                    with _state_lock:
                        _cool[ckey] = max(_cool.get(ckey, 0.0), time.monotonic() + cool)
            if parse == "json":
                try:
                    detail = json.loads(raw).get("detail")
                except Exception:  # noqa: BLE001
                    detail = None
                msg = str(detail) if detail else f"HTTP {e.code} from {url.split('?')[0]}"
            else:
                msg = f"{urllib.parse.urlsplit(url).hostname} is not reachable ({e})."
            kind = _status_kind(e.code)
            last = _fail(msg, kind=kind, http_status=e.code, retry_after=_retry_after(e.headers) if e.code == 429 else None)
            if e.code in retry_on and attempt + 1 < attempts and time.monotonic() < deadline:
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError) as e:      # read timeouts are not URLErrors
            kind = _net_kind(e)
            reason = getattr(e, "reason", e)
            if parse == "json":
                msg = f"{label} " + ("did not answer in time." if kind == "timeout" else f"is not reachable ({reason}).")
            else:
                host = urllib.parse.urlsplit(url).hostname
                msg = f"{host} " + ("did not answer in time." if kind == "timeout" else f"is not reachable ({e}).")
            last = _fail(msg, kind=kind)
            if _NET in retry_on and attempt + 1 < attempts and time.monotonic() < deadline:
                continue
            break
        except Exception as e:  # noqa: BLE001
            raise _fail(f"{urllib.parse.urlsplit(url).hostname} could not be read ({type(e).__name__}).", kind="internal") from None
        else:
            if parse == "json":
                return {"results": []} if not body.strip() else _parse_json(body, url)
            if parse == "text":
                return body.decode("utf-8", "replace")
            return body
        finally:
            if sem is not None:
                sem.release()
    assert last is not None
    if pol["down_for"] and last.kind in ("upstream", "timeout") and getattr(last, "http_status", None) not in (400, 404, 429):
        with _state_lock:
            _down[pname] = (time.monotonic() + pol["down_for"], str(last))
    raise last


def http_json(url, timeout=45):
    """A JSON answer (backend or public API) under the URL's provider policy; see fetch."""
    return C.fetch(url, parse="json", timeout=timeout)


def _which(url):
    return "OpenBB V5 backend" if url.startswith(C.V5) else "OpenBB V4 backend" if url.startswith(C.V4) else url.split("/")[2]


def api_full(base, path, _timeout=45, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    j = C.http_json(f"{base}/{path}?{q}", timeout=_timeout)
    return j if isinstance(j, dict) else {"results": j}


def api(base, path, _timeout=45, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    j = C.http_json(f"{base}/{path}?{q}", timeout=_timeout)
    return j.get("results", j) if isinstance(j, dict) else j


# ---------------------------------------------------------------- source contract
# Every view answers with "sources": [{name, detail, url}] describing what this answer was actually built from.
# The card header shows the distinct names ("FRED · EIA"); its source panel lists every entry with a link.
def src(name, detail="", url=""):
    return {"name": name, "detail": detail, "url": url}


def uniq_sources(items):
    out, seen = [], set()
    for s in items:
        if s and (s["name"], s["detail"]) not in seen:
            seen.add((s["name"], s["detail"]))
            out.append(s)
    return out


YAHOO_URL = "https://finance.yahoo.com/"


def yahoo_source(symbols, what="delayed daily closes"):
    syms = list(dict.fromkeys(s for s in symbols if s))
    shown = ", ".join(syms[:14]) + (f" and {len(syms) - 14} more" if len(syms) > 14 else "")
    return C.src("Yahoo Finance", f"{what}: {shown}" if shown else what,
               C.YAHOO_URL + "quote/" + urllib.parse.quote(syms[0]) if len(syms) == 1 else C.YAHOO_URL)


_SSL = []


def _ssl_context():
    """Verify certificates against certifi's bundle when it is installed (it ships with the backends' environment):
    some official sites (Bundesbank, SNB) send incomplete certificate chains that Python's default store rejects."""
    with C._guard:                                 # built once, even when the first callers arrive together
        if not C._SSL:
            try:
                import certifi
                C._SSL.append(ssl.create_default_context(cafile=certifi.where()))
            except Exception:  # noqa: BLE001
                C._SSL.append(ssl.create_default_context())
        return C._SSL[0]


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _download(url, timeout=60, data=None, ua=None):
    """Raw bytes from a public source under the URL's provider policy; see fetch."""
    return C.fetch(url, parse="bytes", timeout=timeout, data=data, ua=ua)
