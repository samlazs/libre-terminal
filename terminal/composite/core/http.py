"""HTTP, OpenBB backend calls, URL downloads, and source-contract helpers."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request

import composite as C

# Backend ports: the launcher passes TERMINAL_V5_PORT / TERMINAL_V4_PORT when other ports are chosen.
V5 = f"http://127.0.0.1:{os.environ.get('TERMINAL_V5_PORT') or 6900}/api/v1"

V4 = f"http://127.0.0.1:{os.environ.get('TERMINAL_V4_PORT') or 6901}/api/v1"


class CompositeError(Exception):
    def __init__(self, msg, *, transient=False):
        super().__init__(msg)
        self.transient = bool(transient)


_ORIG_URLOPEN = urllib.request.urlopen
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, timeout=45):
    headers = {"User-Agent": "openbb-terminal-ui", "Accept": "application/json"}
    is_local = url.startswith((C.V5, C.V4))
    if is_local:
        headers["X-Terminal-Pool"] = "x"
    req = urllib.request.Request(url, headers=headers)
    try:
        # Bypass corporate/system HTTP proxies on loopback requests unless tests monkeypatched urlopen.
        opener = _LOCAL_OPENER.open if (is_local and urllib.request.urlopen is _ORIG_URLOPEN) else urllib.request.urlopen
        with opener(req, timeout=timeout) as r:
            body = r.read()
            return json.loads(body) if body.strip() else {"results": []}      # 204 / empty body means "no data"
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail")
        except Exception:
            detail = None
        raise C.CompositeError(str(detail) if detail else f"HTTP {e.code} from {url.split('?')[0]}",
                               transient=e.code in (429, 502, 503, 504)) from None
    except urllib.error.URLError as e:
        raise C.CompositeError(f"{C._which(url)} is not reachable ({e.reason}).", transient=True) from None
    except (TimeoutError, OSError) as e:                 # read timeouts are not URLErrors
        raise C.CompositeError(f"{C._which(url)} did not answer in time ({type(e).__name__}).", transient=True) from None


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


def _download(url, timeout=60, data=None, ua="Mozilla/5.0 (compatible; terminal-ui)"):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": ua, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=C._ssl_context()) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        raise C.CompositeError(f"{url.split('/')[2]} is not reachable ({e}).") from None
