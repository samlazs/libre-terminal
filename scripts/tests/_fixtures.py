"""Shared offline fixtures and fault injection for scripts/tests/ (standard library only).

    from _fixtures import fault, fixture, serve

    with fault("http429", retry_after=30):          # every outbound request answers 429 with Retry-After: 30
        ...
    with serve({r"/equity/price/quote": fixture("v5", "quote_aapl")}):   # canned answers by URL regex
        ...

Faults are injected at the single outbound seam (composite._transport, used by http_json and _download), so a test
needs no knowledge of a module's internals. Fixture files live in scripts/tests/fixtures/<provider>/<name>.json:
sanitized, UTF-8 without BOM, LF line endings, no keys, user names, paths or e-mail addresses.
"""

from __future__ import annotations

import contextlib
import email.message
import io
import json
import re
import socket
import threading
import time
import urllib.error
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

KINDS = ("timeout", "hang", "refused", "http429", "http5xx", "http404", "empty_body", "html_body", "nan_values", "partial")

HTML_BODY = b"<!DOCTYPE html><html><head><title>Service unavailable</title></head><body>Please try again later.</body></html>"
NAN_BODY = b'{"results": [{"date": "2026-01-02", "close": NaN, "volume": Infinity}, {"date": "2026-01-03", "close": 101.5, "volume": -Infinity}]}'


def fixture(provider: str, name: str):
    """Load scripts/tests/fixtures/<provider>/<name>.json (parsed JSON)."""
    path = FIXTURES / provider / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class FakeResponse(io.BytesIO):
    """What urlopen returns: a readable body that also works as a context manager, with status and headers."""

    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None, url: str = ""):
        super().__init__(body)
        self.status = self.code = status
        msg = email.message.Message()
        for k, v in (headers or {}).items():
            msg[k] = str(v)
        self.headers = msg
        self.url = url

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url


def _http_error(url: str, code: int, reason: str, headers: dict | None = None, body: bytes = b""):
    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = str(v)
    return urllib.error.HTTPError(url, code, reason, msg, io.BytesIO(body))


def _body(payload) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(payload).encode("utf-8")


def make_answer(kind: str, **kw):
    """A transport function (req, timeout, context=None) that answers every request with fault `kind`."""
    if kind not in KINDS:
        raise ValueError(f"unknown fault kind {kind!r}; known: {', '.join(KINDS)}")

    def answer(req, timeout, context=None):
        url = req.full_url
        if kind == "timeout":
            raise socket.timeout("timed out")
        if kind == "hang":                    # a source that never answers: the socket gives up after the caller's timeout
            time.sleep(float(timeout))
            raise socket.timeout("timed out")
        if kind == "refused":
            raise urllib.error.URLError(ConnectionRefusedError(10061, "No connection could be made because the target machine actively refused it"))
        if kind == "http429":
            ra = kw.get("retry_after")
            return _raise(_http_error(url, 429, "Too Many Requests", {"Retry-After": ra} if ra is not None else None,
                                      _body(kw.get("body", {"detail": "Too many requests"}))))
        if kind == "http5xx":
            return _raise(_http_error(url, int(kw.get("code", 503)), "Service Unavailable", body=_body(kw.get("body", b""))))
        if kind == "http404":
            return _raise(_http_error(url, 404, "Not Found", body=_body(kw.get("body", {"detail": "not found"}))))
        if kind == "empty_body":
            return FakeResponse(b"", int(kw.get("status", 200)), url=url)
        if kind == "html_body":
            return FakeResponse(_body(kw.get("body", HTML_BODY)), 200, {"Content-Type": "text/html"}, url=url)
        if kind == "nan_values":
            return FakeResponse(_body(kw.get("body", NAN_BODY)), 200, {"Content-Type": "application/json"}, url=url)
        raise AssertionError("partial is handled by fault()")
    return answer


def _raise(exc):
    raise exc


class Recorder:
    """What a fault()/serve() block saw: the URLs requested, in order (thread-safe)."""

    def __init__(self):
        self.urls: list[str] = []
        self._lock = threading.Lock()

    def add(self, url: str):
        with self._lock:
            self.urls.append(url)

    @property
    def calls(self) -> int:
        return len(self.urls)


@contextlib.contextmanager
def _installed(transport):
    import composite as C
    rec = Recorder()

    def wrapped(req, timeout, context=None):
        rec.add(req.full_url)
        return transport(req, timeout, context)

    saved = C._transport
    C._transport = wrapped
    C.reset_outbound()                 # cooldowns and outage gates from an earlier block never leak into this one
    try:
        yield rec
    finally:
        C._transport = saved
        C.reset_outbound()


def fault(kind: str, **kw):
    """Context manager: every outbound request through composite._transport answers with fault `kind`.

    kinds: timeout, hang (sleeps the caller's timeout, then times out), refused, http429(retry_after=None), http5xx(code=503), http404, empty_body, html_body, nan_values,
    partial(ok=payload, fail="http5xx", match=regex): URLs matching `match` fail with `fail`, every other answers `ok`
    (default: every second distinct URL fails, retries included). Yields a Recorder with the requested URLs."""
    if kind == "partial":
        ok = _body(kw.get("ok", {"results": [{"date": "2026-01-02", "close": 100.0}]}))
        failing = make_answer(kw.get("fail", "http5xx"))
        pat = re.compile(kw["match"]) if kw.get("match") else None
        seen: dict[str, int] = {}
        lock = threading.Lock()

        def transport(req, timeout, context=None):
            with lock:
                n = seen.setdefault(req.full_url, len(seen) + 1)     # a retry of the same URL fails again
            bad = bool(pat.search(req.full_url)) if pat else n % 2 == 0
            if bad:
                return failing(req, timeout, context)
            return FakeResponse(ok, 200, {"Content-Type": "application/json"}, url=req.full_url)
        return _installed(transport)
    return _installed(make_answer(kind, **kw))


def serve(routes: dict, default=None):
    """Context manager: answer outbound requests from canned payloads. `routes` maps a URL regex to a payload
    (dict/list -> JSON, bytes/str -> raw body), a fault kind name from KINDS, or a callable(url) returning either.
    Unmatched URLs answer `default` (a payload or fault kind), or fail with http404 when it is None."""
    compiled = [(re.compile(k), v) for k, v in routes.items()]

    def resolve(v, req, timeout, context):
        if callable(v):
            v = v(req.full_url)
        if isinstance(v, str) and v in KINDS and v != "partial":
            return make_answer(v)(req, timeout, context)
        return FakeResponse(_body(v), 200, {"Content-Type": "application/json"}, url=req.full_url)

    def transport(req, timeout, context=None):
        for pat, v in compiled:
            if pat.search(req.full_url):
                return resolve(v, req, timeout, context)
        if default is None:
            return make_answer("http404")(req, timeout, context)
        return resolve(default, req, timeout, context)
    return _installed(transport)


def no_retry_delays():
    """Context manager: retries happen without sleeping, same number of attempts (restored on exit)."""
    from composite.core import http as H

    @contextlib.contextmanager
    def cm():
        saved = H._RETRY_DELAYS
        H._RETRY_DELAYS = tuple(0.0 for _ in saved)
        try:
            yield
        finally:
            H._RETRY_DELAYS = saved
    return cm()
