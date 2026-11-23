"""scripts/run_backend.py without starting a server: C1 (output redirects), H2 (redaction), H3 (request guard)."""

import asyncio
import contextlib
import importlib.util
import io
import json
import logging
import os
import sys

from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

real_out, real_err = sys.stdout, sys.stderr
rb.guard_output()
guarded_out, guarded_err = sys.stdout, sys.stderr


# ---- C1: two overlapping requests that each redirect output to their own devnull around an await
async def curve_like(delay_in, delay_out):
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        await asyncio.sleep(delay_in)
        print("inside")
        await asyncio.sleep(delay_out)


async def overlap():
    # A enters, B enters (saving A's devnull), A leaves first: stock contextlib then restores a closed file
    await asyncio.gather(curve_like(0.0, 0.01), curve_like(0.0, 0.05), curve_like(0.02, 0.02))

asyncio.run(overlap())
check(sys.stdout is guarded_out and sys.stderr is guarded_err, "after interleaved redirects the real streams are back (not a closed devnull)")
check(getattr(sys.stdout, "closed", True) is not True, "stdout open")
logging.getLogger("asyncio").warning("logging after the redirects still works")       # used to raise 'I/O operation on closed file'
print("print after the redirects still works", file=sys.stderr)
check(logging.raiseExceptions is False, "a logging error can never escape")

# the guarded stream survives being closed and redacts what passes through
buf = io.StringIO()
g = rb._GuardedStream(buf)
g.write("GET /x?apikey=abcdef123456 200\n")
g.close()
g.write("still writable\n")
check("abcdef123456" not in buf.getvalue() and "still writable" in buf.getvalue(), "guarded stream redacts and cannot be closed")

# log records are redacted at creation (message, args and traceback)
rec_buf = io.StringIO()
h = logging.StreamHandler(rec_buf)
log = logging.getLogger("redact-test")
log.addHandler(h)
log.propagate = False
log.error("request failed for url %s", "https://financialmodelingprep.com/stable/x?apikey=SECRETVALUE123")
try:
    raise ValueError("402 for url: https://x.example/y?api_key=SECRETVALUE456")
except ValueError:
    log.exception("boom")
check("SECRETVALUE123" not in rec_buf.getvalue() and "SECRETVALUE456" not in rec_buf.getvalue(), "log records and tracebacks redacted")

# ---- H4 (watchdog): the restart exit must not depend on the output streams
check(any("os._exit(RESTART_CODE)" in seg[:400] for seg in (ROOT / "scripts" / "run_backend.py").read_text(encoding="utf-8").split("finally:")[1:]),
      "watchdog exits in a finally block")


# ---- H3 + H2: the ASGI guard
class FakeConfig:
    def __init__(self, app):
        self.app = app
        self.loaded_app = None

    def load(self):
        self.loaded_app = self.app


import types  # noqa: E402

uc = types.ModuleType("uvicorn.config")
uc.Config = FakeConfig
sys.modules.setdefault("uvicorn", types.ModuleType("uvicorn"))
sys.modules["uvicorn.config"] = uc
os.environ["TERMINAL_UI_PORT"] = "6950"
rb.UI_PORT = "6950"


async def app(scope, receive, send):
    if scope["path"] == "/err":
        body = json.dumps({"detail": "402 Payment Required for url: https://fmp.example/x?apikey=TOPSECRET999"}).encode()
        await send({"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
        return
    await send({"type": "http.response.start", "status": 200, "headers": [(b"access-control-allow-origin", b"*")]})
    await send({"type": "http.response.body", "body": b"ok"})

rb.install_guard("6900")
cfg = FakeConfig(app)
cfg.load()
guard = cfg.loaded_app


def call(path, host="127.0.0.1:6900", origin=None, site=None):
    sent = []
    headers = [(b"host", host.encode())] + ([(b"origin", origin.encode())] if origin else []) + ([(b"sec-fetch-site", site.encode())] if site else [])

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}
    asyncio.run(guard({"type": "http", "path": path, "headers": headers}, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict((k.decode(), v.decode()) for k, v in start["headers"]), body


check(call("/", host="rebind.example:6900")[0] == 403, "foreign Host refused (DNS rebinding)")
check(call("/", origin="https://evil.example")[0] == 403, "foreign Origin refused")
st, hs, _ = call("/", origin="http://127.0.0.1:6950")
check(st == 200 and hs.get("access-control-allow-origin") == "http://127.0.0.1:6950", "UI origin allowed and named instead of *")
st, hs, _ = call("/")
check(st == 200 and "access-control-allow-origin" not in hs, "no CORS header without an Origin")
check(call("/", host="localhost:6900")[0] == 200 and call("/", host="127.0.0.1")[0] == 200, "localhost and a bare host accepted")
# origin checks: [::1] like the other local names, case-insensitive; Sec-Fetch-Site for requests without an Origin
check(call("/", origin="http://[::1]:6950")[0] == 200, "[::1] UI origin allowed")
check(call("/", origin="HTTP://LocalHost:6950")[0] == 200 and call("/", origin=" http://127.0.0.1:6950 ")[0] == 200, "origin compared without case or spaces")
check(call("/", origin="http://127.0.0.1:7777")[0] == 403, "another local port is not the UI")
check(call("/", site="cross-site")[0] == 403, "a cross-site no-cors GET (no Origin) is refused")
check(all(call("/", site=s)[0] == 200 for s in ("same-origin", "same-site", "none")), "same-origin, same-site and typed-in requests allowed")
check(call("/", origin="http://localhost:6950", site="cross-site")[0] == 200, "the UI on localhost calling 127.0.0.1 is let in by its Origin")
check(rb.request_allowed(None, None, rb.origins_for("6900")), "non-browser clients (no Origin, no Sec-Fetch-Site) allowed")
# Firefox 2026-11-18, frame navigations (probed with a header echo): a frame on 127.0.0.1:A loading 127.0.0.1:B directly
# sends Sec-Fetch-Site: same-site and no Origin; the same load redirected from A to B sends cross-site. The guard stays
# strict; the earnings release card loads the viewer directly instead of through the UI server's redirect.
check(rb.request_allowed(None, "same-site", rb.origins_for("6900")), "a frame loading the SEC viewer directly from the UI page is let in")
check(not rb.request_allowed(None, "cross-site", rb.origins_for("6900")), "a redirected (or foreign) frame load labelled cross-site stays refused")
# runtime fixes that do not apply are reported, one line each, and written for the Data status view
rb.FAILED_PATCHES.clear()
rb._not_applied("test fix", ImportError("No module named 'x'", name="x"))
check(rb.FAILED_PATCHES == [{"patch": "test fix", "why": "module not in this backend (x)"}], "failed patch recorded with a readable reason")
rb.write_patch_status("v5", ["one applied"])
st_file = json.loads((rb.local_paths.RUN_DIR / "patches-v5.json").read_text(encoding="utf-8"))
check(st_file["applied"] == ["one applied"] and st_file["failed"][0]["patch"] == "test fix", "patch status file written")
import composite  # noqa: E402
md = composite.data_status()["content"]
check("#### Backend fixes" in md and "**not applied**: module not in this backend (x)" in md and "| applied |" in md, "Data status lists the backend fixes")
src_rb = (ROOT / "scripts" / "run_backend.py").read_text(encoding="utf-8")
check("except ImportError:\n        pass" not in src_rb and "except Exception:  # noqa: BLE001\n            pass\n        applied" not in src_rb, "no fix fails silently")
st, hs, body = call("/err", origin="http://127.0.0.1:6950")
check(st == 500 and b"TOPSECRET999" not in body and b"REDACTED" in body, "error body redacted")
check(int(hs["content-length"]) == len(body) and hs.get("access-control-allow-origin") == "http://127.0.0.1:6950", "length fixed and the page may read the error")

# ---- IMF PortWatch ArcGIS fix: HTTP 200 error payloads must raise (never silently truncate or cache page 1)
from openbb_core.app.model.abstract.error import OpenBBError  # noqa: E402
from openbb_core.provider.utils import helpers as core_helpers  # noqa: E402
from openbb_imf.utils import port_watch_helpers as pwh  # noqa: E402

imf_applied = []
rb.install_imf_arcgis_fix(imf_applied)
check(len(imf_applied) == 2 and "IMF PortWatch" in imf_applied[0] and "port" in imf_applied[1], f"IMF ArcGIS fix and port names apply cleanly: {imf_applied}")


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler

    async def get(self, url):
        return self._handler(url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


orig_get_session = core_helpers.get_async_requests_session
try:
    def with_handler(handler):
        async def fake_session(**_kwargs):
            return _FakeSession(handler)
        core_helpers.get_async_requests_session = fake_session

    # Case 1: page 1 succeeds with exceededTransferLimit=True, page 2 returns HTTP 200 {"error": {code: 429, ...}}
    def page2_429(url):
        if "resultOffset=0" in url:
            return _FakeResp(200, {
                "exceededTransferLimit": True,
                "features": [{"attributes": {"year": 2019, "month": 1, "day": 1, "portname": "Strait of Hormuz", "n_total": 80}}],
            })
        return _FakeResp(200, {
            "error": {"code": 429, "message": "Unable to perform query. Too many requests.", "details": ["quota exceeded"]},
        })

    with_handler(page2_429)
    err_msg = ""
    try:
        asyncio.run(pwh.get_daily_chokepoint_data("chokepoint6"))
    except OpenBBError as e:
        err_msg = str(e)
    check("429" in err_msg and "Too many requests" in err_msg, "HTTP-200 ArcGIS 429 on page 2 raises OpenBBError instead of truncating")

    # Case 2: error on page 1 raises OpenBBError (not KeyError: 'features'), and missing 'features' raises too
    with_handler(lambda _url: _FakeResp(200, {"error": {"code": 429, "message": "Too many requests"}}))
    p1_err = None
    try:
        asyncio.run(pwh.get_daily_port_activity_data("port1"))
    except Exception as e:  # noqa: BLE001
        p1_err = e
    check(isinstance(p1_err, OpenBBError) and "429" in str(p1_err), "HTTP-200 ArcGIS error on page 1 raises OpenBBError (not KeyError)")

    # Case 3: once ArcGIS recovers, the failed chokepoint6 call was not cached and all pages are returned
    def two_full_pages(url):
        if "resultOffset=0" in url:
            return _FakeResp(200, {
                "exceededTransferLimit": True,
                "features": [
                    {"attributes": {"year": 2019, "month": 1, "day": 1, "portname": "Strait of Hormuz", "n_total": 80}},
                    {"attributes": {"year": 2019, "month": 1, "day": 2, "portname": "Strait of Hormuz", "n_total": 81}},
                ],
            })
        return _FakeResp(200, {
            "exceededTransferLimit": False,
            "features": [{"attributes": {"year": 2026, "month": 11, "day": 8, "portname": "Strait of Hormuz", "n_total": 90}}],
        })

    with_handler(two_full_pages)
    rows = asyncio.run(pwh.get_daily_chokepoint_data("chokepoint6"))
    check(len(rows) == 3 and rows[0]["date"] == "2019-01-01" and rows[-1]["date"] == "2026-11-08", "failed query is not cached; subsequent query returns all pages")

    # Case 4: ArcGIS 499 Token Required (GWM_0003) translates to an upstream message without triggering fake imf_api_key prompts
    with_handler(lambda _url: _FakeResp(200, {"error": {"code": 499, "message": "Token Required", "messageCode": "GWM_0003", "details": ["Token Required"]}}))
    err_499 = ""
    try:
        asyncio.run(pwh.get_daily_chokepoint_data("chokepoint1"))
    except OpenBBError as e:
        err_499 = str(e)
    check("499" in err_499 and "not answering" in err_499 and "token required" not in err_499.lower(), "ArcGIS 499 reports upstream restriction without 'Token Required'")

    # Case 5: stale unfiltered chokepoint cache fallback filters cleanly by chokepoint and date
    from datetime import date as _date  # noqa: E402
    from types import SimpleNamespace  # noqa: E402
    import time as _time  # noqa: E402

    class ImfMaritimeChokePointVolumeFetcher:
        __module__ = "openbb_imf.models.maritime_chokepoint_volume"
        __qualname__ = "ImfMaritimeChokePointVolumeFetcher"

    sample_rows = [
        SimpleNamespace(chokepoint="Strait of Hormuz", date=_date(2021, 9, 26)),
        SimpleNamespace(chokepoint="Strait of Hormuz", date=_date(2026, 11, 8)),
        SimpleNamespace(chokepoint="Panama Canal", date=_date(2026, 11, 8)),
    ]
    seen_paths = []
    sliced = rb._stale_chokepoint_fallback(
        ImfMaritimeChokePointVolumeFetcher,
        {"chokepoint": "chokepoint6", "start_date": "2023-01-01", "end_date": None},
        lambda p: (seen_paths.append(p.name), (_time.time() - 3600, sample_rows))[1],
        lambda k, prov: rb.BACKEND_CACHE / prov / f"{k}.pkl.gz",
    )
    check(
        seen_paths == ["5fb697aef26804e770cd025f.pkl.gz"]
        and sliced is not None and len(sliced) == 1
        and sliced[0].chokepoint == "Strait of Hormuz" and sliced[0].date == _date(2026, 11, 8),
        "stale chokepoint fallback hashes unfiltered key and filters by chokepoint and start_date",
    )
finally:
    core_helpers.get_async_requests_session = orig_get_session

# ---- Yahoo Finance top losers fix (V4 openbb_yfinance.models.losers filters > -3 instead of < -3)
from openbb_core.provider.utils.errors import EmptyDataError  # noqa: E402
saved_mods = {k: sys.modules.get(k) for k in ("openbb_yfinance.models", "openbb_yfinance.models.losers", "openbb_yfinance.utils", "openbb_yfinance.utils.helpers")}
try:
    seen_bodies = []

    async def fake_screener(body, limit=None):
        seen_bodies.append((body, limit))
        if body["query"]["operands"][2]["operands"][1] == -3:
            raise EmptyDataError("empty")
        return [{"symbol": "TEST", "regularMarketChangePercent": -1.5}]

    class FakeFetcher:
        @staticmethod
        async def aextract_data(query, credentials=None, **kwargs):
            return []

        extract_data = aextract_data

    models_mod = types.ModuleType("openbb_yfinance.models")
    losers_mod = types.ModuleType("openbb_yfinance.models.losers")
    losers_mod.YFLosersFetcher = FakeFetcher
    utils_mod = types.ModuleType("openbb_yfinance.utils")
    helpers_mod = types.ModuleType("openbb_yfinance.utils.helpers")
    helpers_mod.get_custom_screener = fake_screener
    sys.modules["openbb_yfinance.models"] = models_mod
    sys.modules["openbb_yfinance.models.losers"] = losers_mod
    sys.modules["openbb_yfinance.utils"] = utils_mod
    sys.modules["openbb_yfinance.utils.helpers"] = helpers_mod

    yf_applied = []
    rb.install_yfinance_losers_fix(yf_applied)
    check(len(yf_applied) == 1 and "< -3" in yf_applied[0], "yfinance losers fix applies cleanly")
    rows_fb = asyncio.run(FakeFetcher.extract_data(SimpleNamespace(limit=50)))
    check(len(seen_bodies) == 2
          and seen_bodies[0][0]["query"]["operands"][2] == {"operator": "lt", "operands": ["percentchange", -3]}
          and seen_bodies[1][0]["query"]["operands"][2] == {"operator": "lt", "operands": ["percentchange", 0]}
          and seen_bodies[0][1] == 50 and rows_fb[0]["symbol"] == "TEST",
          "yfinance losers uses lt -3 and falls back to lt 0 on empty days")
finally:
    for k, v in saved_mods.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

# ---- TMX SEDAR+ filings missing description coercion (e.g. ENB has rows with description=None)
tmx_saved = {k: sys.modules.get(k) for k in ("openbb_tmx.models", "openbb_tmx.models.company_filings")}
try:
    class FakeTmxFilingsFetcher:
        @staticmethod
        def transform_data(query, data, **kwargs):
            for row in data:
                if not isinstance(row.get("description"), str):
                    raise ValueError("description must be str")
            return data

    tmx_models = types.ModuleType("openbb_tmx.models")
    tmx_cf = types.ModuleType("openbb_tmx.models.company_filings")
    tmx_cf.TmxCompanyFilingsFetcher = FakeTmxFilingsFetcher
    sys.modules["openbb_tmx.models"] = tmx_models
    sys.modules["openbb_tmx.models.company_filings"] = tmx_cf

    tmx_applied = []
    rb.install_tmx_filings_fix(tmx_applied)
    check(len(tmx_applied) == 1 and "missing descriptions" in tmx_applied[0], "TMX filings fix applies cleanly")
    out_tmx = FakeTmxFilingsFetcher.transform_data(None, [{"name": "News release", "description": None}, {"name": "10-K", "description": "Annual"}])
    check(out_tmx[0]["description"] == "" and out_tmx[1]["description"] == "Annual", "TMX filings fix coerces description=None without dropping valid rows")
finally:
    for k, v in tmx_saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

# ---- TMX Asset Info in-memory cache + coalescing (complete answers only)
calls = [0]


async def fake_asset_info(symbol="AC", theme="dark"):
    calls[0] += 1
    await asyncio.sleep(0.01)
    if symbol == "MISS":
        return '<div class="ai-missing">Nothing is published</div>'
    return f'<div class="ai-wrap">{symbol}:{theme}</div>'


cached_ai = rb.tmx_asset_info_cached(fake_asset_info, ttl=60.0)


async def run_ai_checks():
    a, b = await asyncio.gather(cached_ai("RY", "dark"), cached_ai("ry", "dark"))
    check(a == b == '<div class="ai-wrap">RY:dark</div>' and calls[0] == 1, "concurrent TMX asset info calls coalesce to 1 fetch")
    c2 = await cached_ai("RY", "dark")
    check(c2 == a and calls[0] == 1, "complete TMX asset info HTML served from cache")
    m1 = await cached_ai("MISS", "dark")
    m2 = await cached_ai("MISS", "dark")
    check("ai-missing" in m1 and "ai-missing" in m2 and calls[0] == 3, "ai-missing HTML is never cached")


asyncio.run(run_ai_checks())

# ---- Cboe 403 HTML block -> readable OpenBBError without leaking upstream URL (aextract_data + extract_data)
cboe_saved = {k: sys.modules.get(k) for k in ("openbb_cboe.models", "openbb_cboe.models.options_chains")}
try:
    class FakeCboeFetcher:
        @staticmethod
        async def aextract_data(query, credentials, **kwargs):
            raise RuntimeError("403, message='Attempt to decode JSON with unexpected mimetype: text/html; charset=utf-8', url='https://cdn-api.cboe.com/api/global/delayed_quotes/options/AAPL.json'")

        @staticmethod
        def extract_data(query, credentials, **kwargs):
            raise RuntimeError("403 Forbidden")

        @staticmethod
        def transform_data(query, data, **kwargs):
            return data

    cboe_models = types.ModuleType("openbb_cboe.models")
    cboe_oc = types.ModuleType("openbb_cboe.models.options_chains")
    cboe_oc.CboeOptionsChainsFetcher = FakeCboeFetcher
    sys.modules["openbb_cboe.models"] = cboe_models
    sys.modules["openbb_cboe.models.options_chains"] = cboe_oc

    cboe_applied = []
    rb.install_cboe_chain_symbols(cboe_applied)
    check(len(cboe_applied) == 1 and "403" in cboe_applied[0], "Cboe chain wrapper applies cleanly")
    check(asyncio.iscoroutinefunction(FakeCboeFetcher.extract_data),
          "Cboe extract_data stays a coroutine (asyncio.run inside it poisoned the shared HTTP cache)")
    for mode, call_fn in (("aextract", lambda: asyncio.run(FakeCboeFetcher.aextract_data(None, None))),
                          ("extract", lambda: asyncio.run(FakeCboeFetcher.extract_data(None, None)))):
        caught = None
        try:
            call_fn()
        except OpenBBError as e:
            caught = str(e)
        check(caught is not None and "HTTP 403" in caught and "https://" not in caught,
              f"Cboe {mode} 403 HTML error converted to clean OpenBBError without URL: {caught}")
finally:
    for k, v in cboe_saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

sys.stdout, sys.stderr = real_out, real_err
# ---- W8: authoritative server CACHE_POLICY, complete-answer check, forced refresh header & CORS preflight
for _p, _ttl, _att in (
    ("openbb_oecd", 12 * 3600, 1),
    ("openbb_ecb", 6 * 3600, 2),
    ("openbb_federal_reserve", 3 * 3600, 2),
    ("openbb_fred", 3 * 3600, 2),
    ("openbb_jodi", 12 * 3600, 2),
):
    check(_p in rb.CACHE_POLICY and rb.CACHE_POLICY[_p][0] == _ttl and rb.CACHE_POLICY[_p][2] == _att,
          f"W8: CACHE_POLICY covers {_p} (ttl={_ttl}s, attempts={_att})")
for _live_p in ("openbb_yfinance", "openbb_cboe", "openbb_tmx", "openbb_nasdaq", "openbb_deribit", "openbb_sec", "openbb_news"):
    check(_live_p not in rb.CACHE_POLICY, f"W8: live/equity fetcher {_live_p} excluded from CACHE_POLICY")

from openbb_core.provider.abstract.annotated_result import AnnotatedResult  # noqa: E402
from openbb_core.provider.abstract.fetcher import Fetcher  # noqa: E402

check(not rb._is_cacheable_result(None) and not rb._is_cacheable_result([]) and not rb._is_cacheable_result({}),
      "W8: _is_cacheable_result rejects None and empty containers")
check(not rb._is_cacheable_result(AnnotatedResult(result=[])) and not rb._is_cacheable_result(AnnotatedResult(result=None)),
      "W8: _is_cacheable_result rejects empty AnnotatedResult")
check(not rb._is_cacheable_result(types.SimpleNamespace(empty=True)),
      "W8: _is_cacheable_result rejects empty DataFrame-like result")
check(rb._is_cacheable_result([{"v": 1}]) and rb._is_cacheable_result(AnnotatedResult(result=[{"v": 1}])),
      "W8: _is_cacheable_result accepts non-empty answers")

# Guard: OPTIONS preflight allows X-Terminal-Refresh and GET propagates FORCE_REFRESH + X-Terminal-Cache
async def _refresh_probe_app(scope, receive, send):
    if scope["path"] == "/refresh_probe":
        cs = rb.CACHE_STATUS.get()
        if cs is not None:
            cs[:] = ["refresh" if rb.FORCE_REFRESH.get() else "hit"]
        body = (b"refreshed" if rb.FORCE_REFRESH.get() else b"cached")
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
        return
    await app(scope, receive, send)


cfg_r = FakeConfig(_refresh_probe_app)
cfg_r.load()
guard_r = cfg_r.loaded_app


def _call_r(method, path, extra_headers=None):
    sent = []
    hdrs = [(b"host", b"127.0.0.1:6900"), (b"origin", b"http://127.0.0.1:6950")] + list(extra_headers or [])

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request"}

    asyncio.run(guard_r({"type": "http", "method": method, "path": path, "headers": hdrs}, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body


st_opt, hs_opt, _ = _call_r("OPTIONS", "/refresh_probe", [(b"access-control-request-headers", b"x-terminal-refresh")])
check(st_opt == 200 and "x-terminal-refresh" in hs_opt.get("access-control-allow-headers", "").lower(),
      "W8: OPTIONS preflight allows X-Terminal-Refresh")
st_n, hs_n, b_n = _call_r("GET", "/refresh_probe")
check(st_n == 200 and b_n == b"cached" and hs_n.get("x-terminal-cache") == "hit", "W8: normal GET omits forced refresh")
st_f, hs_f, b_f = _call_r("GET", "/refresh_probe", [(b"x-terminal-refresh", b"1")])
check(st_f == 200 and b_f == b"refreshed" and hs_f.get("x-terminal-cache") == "refresh"
      and "x-terminal-cache" in hs_f.get("access-control-expose-headers", "").lower(),
      "W8: X-Terminal-Refresh: 1 sets FORCE_REFRESH and exposes X-Terminal-Cache")
check(rb.FORCE_REFRESH.get() is False and rb.CACHE_STATUS.get() is None, "W8: guard resets FORCE_REFRESH and CACHE_STATUS")

# End-to-end install_fetch_cache behavior (cold miss -> warm hit -> forced refresh -> stale fallback on 429 -> empty skipped)
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

orig_fetch_data = Fetcher.fetch_data
orig_backend_cache = rb.BACKEND_CACHE
with tempfile.TemporaryDirectory() as td_w8:
    rb.BACKEND_CACHE = Path(td_w8)
    try:
        oecd_calls = [0]
        oecd_mode = ["ok"]

        class _FakeOecdFetcher(Fetcher):
            @staticmethod
            def transform_query(params, **kwargs):
                return dict(params)

            @staticmethod
            async def aextract_data(query, credentials=None, **kwargs):
                oecd_calls[0] += 1
                if oecd_mode[0] == "fail":
                    raise RuntimeError("HTTP 429 rate limit")
                if oecd_mode[0] == "empty":
                    return []
                return [{"country": query.get("country"), "call": oecd_calls[0]}]

            @staticmethod
            def transform_data(query, data, **kwargs):
                return data

        _FakeOecdFetcher.__module__ = "openbb_oecd.models.gdp_real"
        _FakeOecdFetcher.__qualname__ = "_FakeOecdFetcher"
        cache_applied = []
        rb.install_fetch_cache(cache_applied)
        check(len(cache_applied) == 1 and "OECD" in cache_applied[0], "W8: install_fetch_cache reports OECD in applied fixes")

        async def _run_cache_cases():
            # 1) Cold miss
            cs1, sn1 = [], []
            t1, t2 = rb.CACHE_STATUS.set(cs1), rb.STALE_NOTES.set(sn1)
            r1 = await _FakeOecdFetcher.fetch_data({"country": "DEU"})
            rb.CACHE_STATUS.reset(t1); rb.STALE_NOTES.reset(t2)

            # 2) Warm hit (no upstream call)
            cs2 = []
            t1 = rb.CACHE_STATUS.set(cs2)
            r2 = await _FakeOecdFetcher.fetch_data({"country": "DEU"})
            rb.CACHE_STATUS.reset(t1)

            # 3) Forced refresh bypasses TTL and updates cache
            cs3 = []
            t1, t3 = rb.CACHE_STATUS.set(cs3), rb.FORCE_REFRESH.set(True)
            r3 = await _FakeOecdFetcher.fetch_data({"country": "DEU"})
            rb.FORCE_REFRESH.reset(t3); rb.CACHE_STATUS.reset(t1)

            # 4) Forced refresh while OECD fails -> 1 attempt (no retry), serves saved copy + X-Terminal-Stale
            oecd_mode[0] = "fail"
            cs4, sn4 = [], []
            t1, t2, t3 = rb.CACHE_STATUS.set(cs4), rb.STALE_NOTES.set(sn4), rb.FORCE_REFRESH.set(True)
            r4 = await _FakeOecdFetcher.fetch_data({"country": "DEU"})
            rb.FORCE_REFRESH.reset(t3); rb.STALE_NOTES.reset(t2); rb.CACHE_STATUS.reset(t1)

            # 5) Empty result is not cached (second call hits upstream again)
            oecd_mode[0] = "empty"
            r5 = await _FakeOecdFetcher.fetch_data({"country": "FRA"})
            r6 = await _FakeOecdFetcher.fetch_data({"country": "FRA"})
            return (cs1, r1), (cs2, r2), (cs3, r3), (cs4, sn4, r4), (r5, r6)

        (cs1, r1), (cs2, r2), (cs3, r3), (cs4, sn4, r4), (r5, r6) = asyncio.run(_run_cache_cases())
        check(cs1 == ["miss"] and r1 == [{"country": "DEU", "call": 1}], "W8: cold fetch populates cache with status miss")
        check(cs2 == ["hit"] and r2 == [{"country": "DEU", "call": 1}], "W8: warm fetch hits disk cache without upstream call")
        check(cs3 == ["refresh"] and r3 == [{"country": "DEU", "call": 2}], "W8: forced refresh hits upstream and refreshes cache")
        check(cs4 == ["stale"] and len(sn4) == 1 and "OECD not answering" in sn4[0] and r4 == [{"country": "DEU", "call": 2}],
              "W8: failed forced refresh serves saved copy with stale note")
        check(r5 == [] and r6 == [] and oecd_calls[0] == 5, "W8: OECD attempts=1 (no retry on failure) and empty answers are never cached")
    finally:
        Fetcher.fetch_data = orig_fetch_data
        rb.BACKEND_CACHE = orig_backend_cache

# ---- G2 error body URL sanitization (keeps friendly signup URLs, strips aiohttp ContentTypeError client URLs)
cboe_err = '{"detail":"ContentTypeError -> 403, message=\'Attempt to decode JSON with unexpected mimetype: text/html; charset=utf-8\', url=\'https://cdn-api.cboe.com/api/global/delayed_quotes/quotes/all_us_indices.json\'"}'
san_cboe = rb.sanitize_error_body(cboe_err)
check("https://" not in san_cboe and "403" in san_cboe and "HTML error page instead of JSON" in san_cboe, "sanitize_error_body strips Cboe ContentTypeError URL")
fred_err = '{"detail":"502, message=\'Attempt to decode JSON with unexpected mimetype: text/html\', url=\'https://api.stlouisfed.org/fred/series?series_id=RIFSPPAAAD30NB&api_key=REDACTED&file_type=json\'"}'
san_fred = rb.sanitize_error_body(fred_err)
check("https://" not in san_fred and "502" in san_fred and "HTML error page instead of JSON" in san_fred, "sanitize_error_body strips FRED ContentTypeError URL")
check(rb.sanitize_error_body(rb.FRED_KEY_MESSAGE) == rb.FRED_KEY_MESSAGE, "sanitize_error_body preserves friendly FRED signup URL")


def _test_tvchart_and_warm_route():
    import json as _json
    import sys as _sys
    import threading as _th
    import types as _types

    _mod_names = (
        "openbb_yfinance",
        "openbb_yfinance.utils",
        "openbb_yfinance.utils.tvchart_datafeed",
        "openbb_yfinance.utils.tvchart_native",
    )
    _saved_mods = {m: _sys.modules.get(m) for m in _mod_names}
    _info_calls = []
    _fetch_calls = []

    _pkg = _types.ModuleType("openbb_yfinance")
    _utils = _types.ModuleType("openbb_yfinance.utils")
    _tvdf = _types.ModuleType("openbb_yfinance.utils.tvchart_datafeed")
    _tvn = _types.ModuleType("openbb_yfinance.utils.tvchart_native")

    _tvdf.DERIVED_RESOLUTIONS = {"4h": {"source": "1h", "factor": 4}}
    _tvdf.normalize_resolution = lambda r: str(r or "1d").lower()
    _tvdf.aggregate_bars = lambda bars, _factor: [bars[0]] if bars else []

    def _yf_info(sym):
        _info_calls.append(sym)
        return None if sym == "EMPTY" else {"name": sym, "ticker": sym}

    def _yf_fetch(sym, res):
        _fetch_calls.append((sym, res))
        return [] if sym == "EMPTY" else [{"time": 1700000000, "close": 500.0}]

    class _FakeBarCache:
        def __init__(self):
            self._lock = _th.Lock()
            self._cache = {}

        def invalidate(self, symbol, resolution=None):
            sym = str(symbol or "").upper()
            with self._lock:
                if resolution:
                    self._cache.pop((sym, _tvdf.normalize_resolution(resolution)), None)
                else:
                    for k in [k for k in self._cache if k[0] == sym]:
                        self._cache.pop(k, None)

    async def _orig_tv_html(symbol="AAPL", interval="1d", theme="dark"):
        return f"<html><body><div id='tv'>{symbol}-{interval}-{theme}</div></body></html>"

    _tvdf.yf_symbol_info = _yf_info
    _tvdf.yf_fetch_full_history = _yf_fetch
    _tvdf.BarCache = _FakeBarCache
    _tvn.tvchart_widget_html = _orig_tv_html
    _pkg.utils = _utils
    _utils.tvchart_datafeed = _tvdf
    _utils.tvchart_native = _tvn

    _orig_warm_tvchart = rb.warm_tvchart
    try:
        for _n, _m in zip(_mod_names, (_pkg, _utils, _tvdf, _tvn)):
            _sys.modules[_n] = _m

        _applied = []
        rb.install_tvchart_fix(_applied)
        check(bool(_applied) and getattr(_tvdf, "_terminal_tvchart_patched", False), "install_tvchart_fix applies patches")

        _s1 = _tvdf.yf_symbol_info("SPY")
        _s2 = _tvdf.yf_symbol_info("spy")
        check(_s1 == _s2 and len(_info_calls) == 1, "install_tvchart_fix caches yf_symbol_info case-insensitively")
        _tvdf.yf_symbol_info("EMPTY")
        _tvdf.yf_symbol_info("EMPTY")
        check(_info_calls.count("EMPTY") == 2, "install_tvchart_fix never caches None symbol info")

        _b1 = _tvdf.BarCache().get("SPY", "1d")
        _b2 = _tvdf.BarCache().get("SPY", "1d")
        check(_b1 == _b2 and len(_b1) == 1 and _fetch_calls.count(("SPY", "1d")) == 1, "BarCache shares bars across instances")
        check(_tvdf.BarCache().get("EMPTY", "1d") == [] and _tvdf.BarCache().get("EMPTY", "1d") == [] and _fetch_calls.count(("EMPTY", "1d")) == 2, "BarCache never caches empty bars")

        _tvdf.BarCache().invalidate("SPY", "1d")
        _tvdf.BarCache().get("SPY", "1d")
        check(_fetch_calls.count(("SPY", "1d")) == 2, "BarCache.invalidate clears shared entry")

        _html = asyncio.run(_tvn.tvchart_widget_html("SPY", "1d", "dark"))
        check('id="obb-tv-loading"' in _html and "openbb-chart-ready" in _html, "tvchart_widget_html injects loading overlay and ready notifier")

        _st403, _, _ = _call_r("GET", "/_terminal/warm_chart", [(b"origin", b"http://evil.example")])
        check(_st403 == 403, "/_terminal/warm_chart rejects foreign Origin")

        async def _fake_warm(symbol="^GSPC"):
            return {"ok": True, "symbol": symbol, "bars": 42}

        rb.warm_tvchart = _fake_warm
        _sent = []

        async def _send(msg):
            _sent.append(msg)

        async def _recv():
            return {"type": "http.request"}

        asyncio.run(guard_r({
            "type": "http",
            "method": "GET",
            "path": "/_terminal/warm_chart",
            "query_string": b"symbol=AAPL",
            "headers": [(b"host", b"127.0.0.1:6900"), (b"origin", b"http://127.0.0.1:6950")],
        }, _recv, _send))
        _start = next(m for m in _sent if m["type"] == "http.response.start")
        _hdrs = {k.decode().lower(): v.decode() for k, v in _start["headers"]}
        _body = _json.loads(b"".join(m.get("body", b"") for m in _sent if m["type"] == "http.response.body").decode("utf-8"))
        check(
            _start["status"] == 200
            and _hdrs.get("access-control-allow-origin") == "http://127.0.0.1:6950"
            and _body == {"ok": True, "symbol": "AAPL", "bars": 42},
            "/_terminal/warm_chart runs inside Guard with CORS headers",
        )
    finally:
        rb.warm_tvchart = _orig_warm_tvchart
        for _n, _old in _saved_mods.items():
            if _old is None:
                _sys.modules.pop(_n, None)
            else:
                _sys.modules[_n] = _old


_test_tvchart_and_warm_route()

done("test_backend_wrapper")
