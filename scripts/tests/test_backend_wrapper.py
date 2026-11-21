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
check(len(imf_applied) == 1 and "IMF PortWatch" in imf_applied[0], "IMF ArcGIS fix applies cleanly")


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
    for mode, call_fn in (("async", lambda: asyncio.run(FakeCboeFetcher.aextract_data(None, None))),
                          ("sync", lambda: FakeCboeFetcher.extract_data(None, None))):
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
done("test_backend_wrapper")
