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

# ---- P1-01: a backend that never answers after starting is dumped and restarted (exit 75), not waited on forever
import socket as _socket  # noqa: E402
import subprocess as _subprocess  # noqa: E402
import tempfile as _tempfile  # noqa: E402
import time as _time  # noqa: E402

check(rb.STARTUP_GRACE == float(os.environ.get("OPENBB_STARTUP_GRACE", "240")), "startup grace defaults to 240 s (env OPENBB_STARTUP_GRACE)")
_s = _socket.socket()
_s.bind(("127.0.0.1", 0))
_dead_port = _s.getsockname()[1]
_s.close()                                         # nothing listens there: the backend never answers
_wd_local = _tempfile.mkdtemp(prefix="wd-", dir=os.environ["TERMINAL_LOCAL_DIR"])
_code = (f"import importlib.util,sys; sys.path[:0]=[{str(ROOT / 'scripts')!r},{str(ROOT / 'terminal')!r}]\n"
         f"spec=importlib.util.spec_from_file_location('rb', {str(ROOT / 'scripts' / 'run_backend.py')!r})\n"
         "rb=importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)\n"
         f"rb.watchdog('v5', '127.0.0.1', '{_dead_port}')\n")
_t0 = _time.time()
_r = _subprocess.run([sys.executable, "-c", _code], env=dict(os.environ, OPENBB_STARTUP_GRACE="2", TERMINAL_LOCAL_DIR=_wd_local),
                     capture_output=True, text=True, timeout=120)
_elapsed = _time.time() - _t0
_dumps = list((importlib.import_module("pathlib").Path(_wd_local) / "logs").glob("v5-freeze-*.txt"))
check(_r.returncode == rb.RESTART_CODE == 75, f"no first answer within the grace -> exit 75 (got {_r.returncode}: {(_r.stderr or '')[-200:]})")
check(len(_dumps) == 1 and "gave no first answer within 2s" in _dumps[0].read_text(encoding="utf-8")
      and "Thread" in _dumps[0].read_text(encoding="utf-8"), "the freeze dump names the startup hang and holds the thread stacks")
check(_elapsed < 60, f"the hang is caught soon after the grace ({_elapsed:.1f} s)")
_lsrc = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
spec_l = importlib.util.spec_from_file_location("launcher_for_wd", ROOT / "scripts" / "launcher.py")
_lm = importlib.util.module_from_spec(spec_l)
spec_l.loader.exec_module(_lm)
_crashes = []
_go, _wait, _why = _lm.restart_decision(75, _crashes, 1000.0)
check(_go and _wait == _lm.RESTART_DELAYS[0] and "watchdog" in _why, "the supervisor restarts a startup hang like any watchdog exit")
for _i in range(1, _lm.CRASH_LIMIT):
    _go, _wait, _why = _lm.restart_decision(75, _crashes, 1000.0 + _i)
check(not _go and "crashes" in _why, "and stops after the same crash cap")


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
        _orig_sleep = rb.time.sleep
        rb.time.sleep = lambda _s: None
        try:
            _tvdf.yf_symbol_info("EMPTY")
            _tvdf.yf_symbol_info("EMPTY")
            check(_info_calls.count("EMPTY") == 4, "install_tvchart_fix retries once and never caches None symbol info")

            _b1 = _tvdf.BarCache().get("SPY", "1d")
            _b2 = _tvdf.BarCache().get("SPY", "1d")
            check(_b1 == _b2 and len(_b1) == 1 and _fetch_calls.count(("SPY", "1d")) == 1, "BarCache shares bars across instances")
            check(_tvdf.BarCache().get("EMPTY", "1d") == [] and _tvdf.BarCache().get("EMPTY", "1d") == [] and _fetch_calls.count(("EMPTY", "1d")) == 4, "BarCache retries once and never caches empty bars")
        finally:
            rb.time.sleep = _orig_sleep

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


# ---- v0.7.0 TradingView sanitization, metadata bar repair, JSON scrubbing, provider & Guard hardening
def _test_v07_reliability():
    import datetime as _dt
    import sys as _sys
    import types as _types

    # 1. _clean_json_val + strict JSON dumps
    dirty = {"a": float("nan"), "b": [1.0, float("inf"), float("-inf")], "nested": {"x": 2.5, "y": float("nan")}}
    cleaned = rb._clean_json_val(dirty)
    check(cleaned == {"a": None, "b": [1.0, None, None], "nested": {"x": 2.5, "y": None}}, "_clean_json_val scrubs NaN/Inf")
    check(json.dumps(cleaned, allow_nan=False) is not None, "cleaned payload serializes with allow_nan=False")

    # 2. _sanitize_tv_bars drops NaN/Inf bars, preserves close-only bars, clamps negative volume
    raw_bars = [
        {"time": 1700000000, "open": 10.0, "high": 9.0, "low": 11.0, "close": 10.5, "volume": 100},
        {"time": 1700086400, "open": float("nan"), "high": 12.0, "low": 9.5, "close": 11.0, "volume": 50},
        {"time": 1700172800, "close": 11.2},
        {"time": 1700259200, "open": 11.0, "high": 11.5, "low": 10.8, "close": float("inf"), "volume": 10},
        {"time": 1700345600, "close": 12.0, "volume": -5},
    ]
    san = rb._sanitize_tv_bars(raw_bars)
    check(len(san) == 3, f"_sanitize_tv_bars keeps 3 finite bars (got {len(san)})")
    check(san[0]["high"] == 10.5 and san[0]["low"] == 10.0, "_sanitize_tv_bars clamps high/low envelope around O/C")
    check(san[1] == {"time": 1700172800, "close": 11.2}, "_sanitize_tv_bars preserves close-only bars")
    check(san[2]["volume"] == 0, "_sanitize_tv_bars clamps negative volume to 0")

    # 3. _repair_daily_bar_from_meta appends active session bar when history stops before market date
    today_dt = _dt.datetime.now(_dt.timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    if today_dt.weekday() >= 5:
        today_dt -= _dt.timedelta(days=today_dt.weekday() - 4)
    yest_ts = int((today_dt - _dt.timedelta(days=1)).timestamp())
    repaired = rb._repair_daily_bar_from_meta(
        [{"time": yest_ts, "open": 40.0, "high": 42.0, "low": 39.0, "close": 41.0, "volume": 1000}],
        {
            "regularMarketPrice": 48.33,
            "regularMarketOpen": 47.0,
            "regularMarketDayHigh": 51.9,
            "regularMarketDayLow": 46.53,
            "regularMarketVolume": 94610000,
            "regularMarketTime": int(today_dt.timestamp()),
            "exchangeTimezoneName": "America/New_York",
            "instrumentType": "EQUITY",
        },
    )
    check(len(repaired) == 2 and repaired[-1]["close"] == 48.33 and repaired[-1]["open"] == 47.0, "_repair_daily_bar_from_meta appends active daily bar")

    # 4. install_tmx_gainers_fix drops None / "-" strings safely
    _saved_tmx = {m: _sys.modules.get(m) for m in ("openbb_tmx", "openbb_tmx.models", "openbb_tmx.models.gainers")}
    try:
        pkg = _types.ModuleType("openbb_tmx")
        mods = _types.ModuleType("openbb_tmx.models")
        gmod = _types.ModuleType("openbb_tmx.models.gainers")

        class _GainersData:
            @classmethod
            def model_validate(cls, row):
                return dict(row)

        class _GainersFetcher:
            @staticmethod
            def transform_data(query, data, **kwargs):
                return data

        gmod.TmxGainersData = _GainersData
        gmod.TmxGainersFetcher = _GainersFetcher
        mods.gainers = gmod
        pkg.models = mods
        _sys.modules["openbb_tmx"] = pkg
        _sys.modules["openbb_tmx.models"] = mods
        _sys.modules["openbb_tmx.models.gainers"] = gmod

        ap_tmx = []
        rb.install_tmx_gainers_fix(ap_tmx)
        rows = _GainersFetcher.transform_data(None, [{"symbol": "RY", "longName": None, "exchange": "-"}, {"symbol": ""}])
        check(bool(ap_tmx) and rows == [{"symbol": "RY"}], "install_tmx_gainers_fix strips None/'-' strings and blank symbols")
    finally:
        for _n, _old in _saved_tmx.items():
            if _old is None:
                _sys.modules.pop(_n, None)
            else:
                _sys.modules[_n] = _old

    # 5. Guard pre-header exception returns sanitized 500 JSON with CORS headers
    class _CrashConfig:
        def __init__(self, app_in):
            self.loaded_app = app_in

        def load(self):
            pass

    async def _boom_app(scope, receive, send):
        raise RuntimeError("boom token=TOPSECRET_ERR")

    uc_mod = _sys.modules["uvicorn.config"]
    orig_cfg = uc_mod.Config
    try:
        uc_mod.Config = _CrashConfig
        rb.install_guard(6900)
        cfg_inst = _CrashConfig(_boom_app)
        uc_mod.Config.load(cfg_inst)
        guard_crash = cfg_inst.loaded_app
        sent = []

        async def _send_c(msg):
            sent.append(msg)

        async def _recv_c():
            return {"type": "http.request"}

        asyncio.run(guard_crash({
            "type": "http",
            "method": "GET",
            "path": "/api/v1/crash",
            "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:6900"), (b"origin", b"http://127.0.0.1:6950")],
        }, _recv_c, _send_c))
        st = next(m for m in sent if m["type"] == "http.response.start")
        hs = {k.decode().lower(): v.decode() for k, v in st["headers"]}
        btxt = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode("utf-8")
        check(
            st["status"] == 500
            and hs.get("access-control-allow-origin") == "http://127.0.0.1:6950"
            and "TOPSECRET_ERR" not in btxt
            and "RuntimeError" in btxt,
            "Guard converts unhandled pre-header crash to redacted 500 JSON with CORS headers",
        )
    finally:
        uc_mod.Config = orig_cfg


_test_v07_reliability()


def _test_reliability_hardening():
    import gc as _gc
    import sys as _sys
    import threading as _th
    import types as _types
    import weakref as _weakref

    # 1. CACHE_POLICY attempts and pool_slot_spec for openbb_yfinance
    check(rb.CACHE_POLICY["openbb_us_eia"][2] == 1, "CACHE_POLICY: openbb_us_eia attempts == 1")
    check(rb.CACHE_POLICY["openbb_government_us"][2] == 2, "CACHE_POLICY: openbb_government_us attempts == 2")
    check(rb.CACHE_POLICY["openbb_oecd"][2] == 1, "CACHE_POLICY: openbb_oecd attempts == 1")
    check(
        rb.pool_slot_spec("openbb_yfinance", "ui") == (("openbb_yfinance", "ui"), 5)
        and rb.pool_slot_spec("openbb_yfinance", "x") == (("openbb_yfinance", "x"), 4),
        "pool_slot_spec: openbb_yfinance gives 5 slots to ui and 4 to x",
    )

    # 2. install_yfinance_crumb_fix: poisoned crumbs, 401 recovery, concurrent 401 guard
    _yf_names = ("yfinance", "yfinance.data", "yfinance.config", "yfinance.exceptions")
    _saved_yf = {m: _sys.modules.get(m) for m in _yf_names}
    try:
        _yf_pkg = _types.ModuleType("yfinance")
        _yf_data = _types.ModuleType("yfinance.data")
        _yf_cfg = _types.ModuleType("yfinance.config")
        _yf_exc = _types.ModuleType("yfinance.exceptions")

        class _FakeRateLimit(Exception):
            pass

        class _FakeYFException(Exception):
            pass

        class _FakeNet:
            retries = 0
            proxy = None

        class _FakeYfConfig:
            network = _FakeNet()

        _resp_q = []
        _crumb_q = []
        _get_cc_calls = []
        _cache_clears = []
        _strategy_calls = []
        _req_params_seen = []

        class _FakeCookieJar(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.clear_count = 0

            def clear(self):
                self.clear_count += 1
                super().clear()

        class _FakeYfData:
            def __init__(self):
                self._crumb = "init_crumb"
                self._cookie = "init_cookie"
                self._cookie_strategy = "basic"
                self._cookie_lock = _th.Lock()
                self._session = _types.SimpleNamespace(cookies=_FakeCookieJar({"init": "1"}), proxies=None)

            def _set_session(self, session):
                self._session = session

            def _set_cookie_strategy(self, strategy):
                _strategy_calls.append(strategy)
                self._cookie_strategy = strategy

            def _get_crumb_basic(self, timeout=30):
                val = _crumb_q.pop(0)
                if isinstance(val, BaseException):
                    self._crumb = "Too Many Requests"
                    raise val
                self._crumb = val
                return val

            def _get_crumb_csrf(self, timeout=30):
                val = _crumb_q.pop(0)
                if isinstance(val, BaseException):
                    self._crumb = "Too Many Requests"
                    raise val
                self._crumb = val
                return val

            def _get_cookie_basic(self, timeout=30):
                _get_cc_calls.append(("cookie_basic", timeout))
                return getattr(self, "_fake_cookie_basic_ok", True)

            def _get_cookie_and_crumb(self, timeout=30):
                with self._cookie_lock:
                    _get_cc_calls.append(timeout)
                    if getattr(self, "_fake_cc_exc", None) is not None:
                        raise self._fake_cc_exc
                    if self._crumb is None:
                        if getattr(self, "_fake_cc_none", False):
                            return None, self._cookie_strategy
                        self._crumb = "fresh_crumb"
                        self._cookie = "fresh_cookie"
                    return self._crumb, self._cookie_strategy

            def cache_get(self, *args, **kwargs):
                return None

            cache_get.cache_clear = lambda: _cache_clears.append(1)

            def _make_request(self, url, request_method=None, body=None, params=None, timeout=30, data=None, headers=None):
                raise AssertionError("original _make_request should be replaced")

        def _fake_request_method(url, params=None, timeout=30, **kwargs):
            _req_params_seen.append(dict(params or {}))
            item = _resp_q.pop(0)
            if callable(item):
                return item(_yd)
            return item

        _yf_exc.YFRateLimitError = _FakeRateLimit
        _yf_cfg.YfConfig = _FakeYfConfig
        _yf_data.YfData = _FakeYfData
        _yf_data.YFException = _FakeYFException
        _yf_data._is_transient_error = lambda exc: isinstance(exc, TimeoutError)
        _yf_data._normalize_proxy = lambda p: p
        _yf_data.utils = _types.SimpleNamespace()
        _yf_pkg.data = _yf_data
        _yf_pkg.config = _yf_cfg
        _yf_pkg.exceptions = _yf_exc

        for _n, _m in zip(_yf_names, (_yf_pkg, _yf_data, _yf_cfg, _yf_exc)):
            _sys.modules[_n] = _m

        _ap = []
        rb.install_yfinance_crumb_fix(_ap)
        check(bool(_ap) and getattr(_FakeYfData, "_terminal_crumb_fixed", False), "install_yfinance_crumb_fix applies cleanly")
        check(_FakeYfConfig.network.retries >= 2, "install_yfinance_crumb_fix raises YfConfig.network.retries >= 2")

        _yd = _FakeYfData()
        for _bad in ("<html>bad</html>", '{"error":"unauthorized"}', "", None, "Too Many Requests", "Unauthorized", "Invalid Crumb",
                     "abc def", "abc\ndef", " good ", "x" * 65):
            _crumb_q.append(_bad)
            _got = _yd._get_crumb_basic()
            check(_got is None and _yd._crumb is None, f"poisoned basic crumb cleared: {str(_bad)[:20]!r}")

        _crumb_q.append(_FakeRateLimit("429"))
        _rl_raised = False
        try:
            _yd._get_crumb_csrf()
        except _FakeRateLimit:
            _rl_raised = True
        check(_rl_raised and _yd._crumb is None, "YFRateLimitError in _get_crumb_csrf clears _crumb before re-raising")

        _crumb_q.append("good_crumb_123")
        check(_yd._get_crumb_basic() == "good_crumb_123" and _yd._crumb == "good_crumb_123", "valid crumb is preserved")

        # _get_cookie_basic memoizes _cookie = True in memory and respects False
        _get_cc_calls.clear()
        _yd._cookie = None
        _yd._fake_cookie_basic_ok = False
        check(
            _yd._get_cookie_basic(15) is False and _yd._cookie is None and len(_get_cc_calls) == 1,
            "_get_cookie_basic leaves _cookie=None when orig_cookie_basic returns False",
        )
        _yd._fake_cookie_basic_ok = True
        check(
            _yd._get_cookie_basic(15) is True
            and _yd._get_cookie_basic(15) is True
            and _yd._cookie is True
            and len(_get_cc_calls) == 2,
            "_get_cookie_basic memoizes _cookie=True across repeated calls",
        )
        _yd._cookie = None
        check(
            _yd._get_cookie_basic(15) is True and _yd._cookie is True and len(_get_cc_calls) == 3,
            "clearing _cookie=None allows _get_cookie_basic to fetch again",
        )

        # _set_session ignores throwaway empty-cookie sessions when authenticated, but
        # replaces _session and clears _cookie/_crumb when proxies are provided.
        _orig_sess = _yd._session
        _yd._cookie = True
        _yd._crumb = "keep_crumb"
        _throwaway_sess = _types.SimpleNamespace(cookies=_FakeCookieJar({}), proxies=None)
        _yd._set_session(_throwaway_sess)
        check(
            _yd._session is _orig_sess and _yd._cookie is True and _yd._crumb == "keep_crumb",
            "_set_session ignores throwaway empty-cookie session when authenticated",
        )
        _proxy_sess = _types.SimpleNamespace(cookies=_FakeCookieJar({}), proxies={"https": "http://127.0.0.1:8080"})
        _yd._set_session(_proxy_sess)
        check(
            _yd._session is _proxy_sess and _yd._cookie is None and _yd._crumb is None,
            "_set_session replaces session with proxies and resets _cookie/_crumb when new session has empty cookies",
        )
        _yd._session = _orig_sess

        # 404 Not Found does NOT call _set_cookie_strategy, clear cookies, wipe _crumb, or retry
        _yd._crumb = "keep_crumb"
        _yd._cookie = "keep_cookie"
        _yd._session.cookies = _FakeCookieJar({"k": "v"})
        _get_cc_calls.clear()
        _cache_clears.clear()
        _strategy_calls.clear()
        _req_params_seen.clear()
        _resp_q.append(_types.SimpleNamespace(status_code=404, text="Not Found"))
        _r_404 = _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=15)
        check(
            _r_404.status_code == 404
            and _yd._crumb == "keep_crumb"
            and _yd._cookie == "keep_cookie"
            and _yd._session.cookies == {"k": "v"}
            and _yd._session.cookies.clear_count == 0
            and not _strategy_calls
            and not _cache_clears
            and len(_req_params_seen) == 1,
            "404 Not Found does not wipe crumb/cookies, toggle strategy, or retry",
        )

        # 401 permanent feature-restriction does NOT call _set_cookie_strategy, wipe _crumb, or retry
        _get_cc_calls.clear()
        _cache_clears.clear()
        _strategy_calls.clear()
        _req_params_seen.clear()
        _resp_q.append(_types.SimpleNamespace(status_code=401, text="User is unable to access this feature"))
        _r_perm = _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=15)
        check(
            _r_perm.status_code == 401
            and _yd._crumb == "keep_crumb"
            and _yd._cookie == "keep_cookie"
            and _yd._session.cookies == {"k": "v"}
            and _yd._session.cookies.clear_count == 0
            and not _strategy_calls
            and not _cache_clears
            and len(_req_params_seen) == 1,
            "401 feature-restriction does not wipe crumb/cookies, toggle strategy, or retry",
        )

        # 401 Invalid Crumb clears crumb/cookie/session.cookies, refreshes without deadlocking on non-reentrant _cookie_lock, and retries once
        _yd._crumb = "stale_crumb"
        _yd._cookie = "stale_cookie"
        _yd._session.cookies = _FakeCookieJar({"stale": "1"})
        _get_cc_calls.clear()
        _cache_clears.clear()
        _strategy_calls.clear()
        _req_params_seen.clear()
        _resp_q.extend([
            _types.SimpleNamespace(status_code=401, text="Invalid Crumb"),
            _types.SimpleNamespace(status_code=200, text="ok"),
        ])
        _r = _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=15)
        check(
            _r.status_code == 200
            and _get_cc_calls == [30, 15]
            and len(_cache_clears) == 1
            and _yd._crumb == "fresh_crumb"
            and _yd._cookie == "fresh_cookie"
            and len(_yd._session.cookies) == 0
            and _yd._session.cookies.clear_count == 1
            and _req_params_seen == [{"crumb": "stale_crumb"}, {"crumb": "fresh_crumb"}],
            "401 Invalid Crumb resets crumb/cookie/session.cookies without deadlock, refreshes, and retries",
        )

        # Concurrent 401 where another thread already updated _crumb reuses new crumb without wiping _session.cookies again
        _yd._crumb = "thread_a_crumb"
        _yd._cookie = "thread_a_cookie"
        _yd._session.cookies = _FakeCookieJar({"live": "1"})
        _get_cc_calls.clear()
        _cache_clears.clear()
        _req_params_seen.clear()

        def _mutate_crumb_during_req(inst):
            inst._crumb = "thread_b_newer_crumb"
            inst._cookie = "thread_b_newer_cookie"
            return _types.SimpleNamespace(status_code=401, text="Invalid Crumb")

        _resp_q.extend([
            _mutate_crumb_during_req,
            _types.SimpleNamespace(status_code=200, text="ok"),
        ])
        _r2 = _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=20)
        check(
            _r2.status_code == 200
            and _yd._crumb == "thread_b_newer_crumb"
            and _yd._cookie == "thread_b_newer_cookie"
            and _yd._session.cookies.clear_count == 0
            and _yd._session.cookies == {"live": "1"}
            and _req_params_seen == [{"crumb": "thread_a_crumb"}, {"crumb": "thread_b_newer_crumb"}],
            "concurrent 401 reuses refreshed crumb without re-wiping session.cookies",
        )

        # 503 retries transiently without touching cookies/crumb
        _yd._crumb = "steady_crumb"
        _yd._session.cookies = _FakeCookieJar({"steady": "1"})
        _get_cc_calls.clear()
        _strategy_calls.clear()
        _req_params_seen.clear()
        _resp_q.extend([
            _types.SimpleNamespace(status_code=503, text="Service Unavailable"),
            _types.SimpleNamespace(status_code=200, text="ok"),
        ])
        _r503 = _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=15)
        check(
            _r503.status_code == 200
            and _yd._crumb == "steady_crumb"
            and _yd._session.cookies == {"steady": "1"}
            and _yd._session.cookies.clear_count == 0
            and not _strategy_calls
            and len(_req_params_seen) == 2,
            "503 retries transiently without touching cookies or crumb",
        )

        # 429 raises YFRateLimitError without wiping cookies or toggling strategy
        _yd._crumb = "steady_crumb"
        _yd._session.cookies = _FakeCookieJar({"steady": "1"})
        _strategy_calls.clear()
        _req_params_seen.clear()
        _resp_q.append(_types.SimpleNamespace(status_code=429, text="Too Many Requests"))
        _rl429 = False
        try:
            _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=15)
        except _FakeRateLimit:
            _rl429 = True
        check(
            _rl429
            and _yd._crumb == "steady_crumb"
            and _yd._session.cookies == {"steady": "1"}
            and _yd._session.cookies.clear_count == 0
            and not _strategy_calls
            and len(_req_params_seen) == 1,
            "429 raises YFRateLimitError without wiping cookies or toggling strategy",
        )

        # If _get_cookie_and_crumb() raised YFRateLimitError and request gets 401, raise YFRateLimitError without re-hitting getcrumb or clearing cookies
        _yd._crumb = "keep_rl_crumb"
        _yd._cookie = "keep_rl_cookie"
        _yd._session.cookies = _FakeCookieJar({"rl": "1"})
        _yd._fake_cc_exc = _FakeRateLimit("429")
        _get_cc_calls.clear()
        _strategy_calls.clear()
        _req_params_seen.clear()
        _resp_q.append(_types.SimpleNamespace(status_code=401, text="Invalid Crumb"))
        _rl_on_401 = False
        try:
            _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=15)
        except _FakeRateLimit:
            _rl_on_401 = True
        _yd._fake_cc_exc = None
        check(
            _rl_on_401
            and _get_cc_calls == [30]
            and _yd._session.cookies == {"rl": "1"}
            and _yd._session.cookies.clear_count == 0
            and not _strategy_calls
            and len(_req_params_seen) == 1,
            "401 after YFRateLimitError in _get_cookie_and_crumb does not wipe cookies or re-hit getcrumb",
        )

        # 401 Invalid Crumb refresh calls _get_cookie_and_crumb(timeout) once without redundant double strategy toggle
        _yd._crumb = "stale_once"
        _yd._cookie = "stale_once_cookie"
        _yd._session.cookies = _FakeCookieJar({"s": "1"})
        _yd._fake_cc_none = True
        _get_cc_calls.clear()
        _strategy_calls.clear()
        _req_params_seen.clear()
        _resp_q.extend([
            _types.SimpleNamespace(status_code=401, text="Invalid Crumb"),
            _types.SimpleNamespace(status_code=200, text="ok"),
        ])
        _r_notog = _yd._make_request("https://query1.finance.yahoo.com/v7/finance/quote", _fake_request_method, timeout=15)
        _yd._fake_cc_none = False
        check(
            _r_notog.status_code == 200
            and _get_cc_calls == [30, 15]
            and not _strategy_calls
            and _req_params_seen == [{"crumb": "stale_once"}, {}],
            "401 Invalid Crumb refresh calls _get_cookie_and_crumb(timeout) once without redundant strategy toggle",
        )

        # install_yfinance_options_fix: patches aextract_data + extract_data, uses locked crumb reset on crumb 401, ignores permanent 401 and 429
        _opt_names = ("openbb_yfinance", "openbb_yfinance.models", "openbb_yfinance.models.options_chains")
        _saved_opt = {m: _sys.modules.get(m) for m in _opt_names}
        try:
            _opt_pkg = _types.ModuleType("openbb_yfinance")
            _opt_models = _types.ModuleType("openbb_yfinance.models")
            _opt_mod = _types.ModuleType("openbb_yfinance.models.options_chains")
            _opt_outcomes = []
            _opt_calls = [0]

            class _FakeOptionsFetcher:
                @staticmethod
                async def aextract_data(query, credentials=None, **kwargs):
                    _opt_calls[0] += 1
                    nxt = _opt_outcomes.pop(0)
                    if isinstance(nxt, BaseException):
                        raise nxt
                    return nxt

            _FakeOptionsFetcher.extract_data = _FakeOptionsFetcher.aextract_data
            _opt_mod.YFinanceOptionsChainsFetcher = _FakeOptionsFetcher
            _opt_models.options_chains = _opt_mod
            _opt_pkg.models = _opt_models
            for _n, _m in zip(_opt_names, (_opt_pkg, _opt_models, _opt_mod)):
                _sys.modules[_n] = _m

            # Make YfData() return singleton _yd for options fix test
            _orig_yfdata_cls = _yf_data.YfData
            _yf_data.YfData = lambda: _yd
            try:
                _opt_ap = []
                rb.install_yfinance_options_fix(_opt_ap)
                check(bool(_opt_ap) and _FakeOptionsFetcher.extract_data is _FakeOptionsFetcher.aextract_data,
                      "install_yfinance_options_fix patches both aextract_data and extract_data")

                _yd._crumb = "old_opt_c"
                _yd._cookie = "old_opt_k"
                _yd._session.cookies = _FakeCookieJar({"opt": "1"})
                _opt_calls[0] = 0
                _opt_outcomes[:] = [
                    RuntimeError("HTTP 401 Invalid Crumb"),
                    {"underlying": {"symbol": "AAPL"}, "chains": [{"strike": 200}]},
                ]
                _opt_res = asyncio.run(_FakeOptionsFetcher.extract_data(_types.SimpleNamespace(symbol="AAPL")))
                check(_opt_res["underlying"]["symbol"] == "AAPL" and _opt_calls[0] == 2
                      and _yd._crumb is None and _yd._cookie is None and _yd._session.cookies.clear_count == 1,
                      "options fix resets crumb via _terminal_reset_crumb on crumb 401 and retries once")

                for _perm_msg in ("401 User is unable to access this feature", "429 Too Many Requests"):
                    _yd._crumb = "keep_opt_c"
                    _yd._cookie = "keep_opt_k"
                    _yd._session.cookies = _FakeCookieJar({"opt": "1"})
                    _opt_calls[0] = 0
                    _opt_outcomes[:] = [RuntimeError(_perm_msg)]
                    _raised_perm = False
                    try:
                        asyncio.run(_FakeOptionsFetcher.aextract_data(_types.SimpleNamespace(symbol="AAPL")))
                    except RuntimeError:
                        _raised_perm = True
                    check(_raised_perm and _opt_calls[0] == 1 and _yd._crumb == "keep_opt_c"
                          and _yd._cookie == "keep_opt_k" and _yd._session.cookies.clear_count == 0,
                          f"options fix never clears crumb/cookies on {_perm_msg!r}")
            finally:
                _yf_data.YfData = _orig_yfdata_cls
        finally:
            for _n, _old in _saved_opt.items():
                if _old is None:
                    _sys.modules.pop(_n, None)
                else:
                    _sys.modules[_n] = _old
    finally:
        for _n, _old in _saved_yf.items():
            if _old is None:
                _sys.modules.pop(_n, None)
            else:
                _sys.modules[_n] = _old

    # 3. BarCache.get & yf_symbol_info retry once and fall back to stale cache
    _tv_names = (
        "openbb_yfinance",
        "openbb_yfinance.utils",
        "openbb_yfinance.utils.tvchart_datafeed",
        "openbb_yfinance.utils.tvchart_native",
    )
    _saved_tv = {m: _sys.modules.get(m) for m in _tv_names}
    _orig_sleep = rb.time.sleep
    _orig_mono = rb.time.monotonic
    try:
        _pkg = _types.ModuleType("openbb_yfinance")
        _utils = _types.ModuleType("openbb_yfinance.utils")
        _tvdf = _types.ModuleType("openbb_yfinance.utils.tvchart_datafeed")
        _tvn = _types.ModuleType("openbb_yfinance.utils.tvchart_native")

        _now = [100.0]
        rb.time.sleep = lambda _s: None
        rb.time.monotonic = lambda: _now[0]

        _info_seq = []
        _bar_seq = []

        def _yf_info(sym):
            res = _info_seq.pop(0)
            if isinstance(res, BaseException):
                raise res
            return res

        def _yf_fetch(sym, res):
            val = _bar_seq.pop(0)
            if isinstance(val, BaseException):
                raise val
            return val

        class _FakeBarCache:
            def __init__(self):
                self._lock = _th.Lock()
                self._cache = {}

            def invalidate(self, symbol, resolution=None):
                pass

        async def _orig_tv_html(symbol="AAPL", interval="1d", theme="dark"):
            return "<html><body></body></html>"

        _tvdf.DERIVED_RESOLUTIONS = {}
        _tvdf.normalize_resolution = lambda r: str(r or "1d").lower()
        _tvdf.aggregate_bars = lambda bars, _f: bars
        _tvdf.yf_symbol_info = _yf_info
        _tvdf.yf_fetch_full_history = _yf_fetch
        _tvdf.BarCache = _FakeBarCache
        _tvn.tvchart_widget_html = _orig_tv_html
        _pkg.utils = _utils
        _utils.tvchart_datafeed = _tvdf
        _utils.tvchart_native = _tvn

        for _n, _m in zip(_tv_names, (_pkg, _utils, _tvdf, _tvn)):
            _sys.modules[_n] = _m

        rb.install_tvchart_fix([])

        # Retry once on transient failure
        _info_seq.extend([RuntimeError("transient"), {"name": "Apple", "ticker": "AAPL"}])
        check(_tvdf.yf_symbol_info("AAPL") == {"name": "Apple", "ticker": "AAPL"}, "yf_symbol_info retries once on error")

        _bar_seq.extend([[], [{"time": 1700000000, "close": 190.0}]])
        _bc = _tvdf.BarCache()
        check(_bc.get("AAPL", "1d") == [{"time": 1700000000, "close": 190.0}], "BarCache.get retries once on empty")

        # Expire TTL and fail both attempts -> serve stale cache
        _now[0] += 100000.0
        _info_seq.extend([None, RuntimeError("still down")])
        check(_tvdf.yf_symbol_info("AAPL") == {"name": "Apple", "ticker": "AAPL"}, "yf_symbol_info falls back to stale cache")

        _bc._cache.clear()
        _bar_seq.extend([RuntimeError("err1"), []])
        check(_bc.get("AAPL", "1d") == [{"time": 1700000000, "close": 190.0}], "BarCache.get falls back to stale cache")

        # Derived-resolution stale fallback when source resolution fails/returns empty after TTL expiry
        _tvdf.DERIVED_RESOLUTIONS = {"4h": {"source": "1h", "factor": 4}}
        _tvdf.aggregate_bars = lambda bars, factor: [{"time": bars[0]["time"], "close": bars[0]["close"] * factor}] if bars else []
        _bar_seq.extend([[{"time": 1700000000, "close": 210.0}]])
        check(_bc.get("AAPL", "4h") == [{"time": 1700000000, "close": 840.0}],
              "BarCache.get builds derived resolution from source bars")
        _bc.invalidate("AAPL", "1h")
        _now[0] += 100000.0
        _bc._cache.clear()
        _bar_seq.extend([RuntimeError("src1"), RuntimeError("src2")])
        check(_bc.get("AAPL", "4h") == [{"time": 1700000000, "close": 840.0}],
              "BarCache.get falls back to stale derived-resolution cache when source fails")

        # tvchart_widget_html overlay delays ws:close lockout and lets hide() clear closeTimer
        _ov_html = asyncio.run(_tvn.tvchart_widget_html("AAPL", "1d", "dark"))
        check('["ws:close","close"]' in _ov_html and "closeTimer" in _ov_html and "if(hidden)return;hidden=true;" in _ov_html,
              "tvchart_widget_html overlay gives ws:close reconnect grace window and keeps hide() unlocked")
    finally:
        rb.time.sleep = _orig_sleep
        rb.time.monotonic = _orig_mono
        for _n, _old in _saved_tv.items():
            if _old is None:
                _sys.modules.pop(_n, None)
            else:
                _sys.modules[_n] = _old

    # 4. WeakKeyDictionary event-loop GC checks for install_thread_offload and install_imf_arcgis_fix
    from openbb_core.provider.abstract import fetcher as _abs_fetcher
    _orig_mc = _abs_fetcher.maybe_coroutine
    try:
        rb.install_thread_offload([])
        _lp1 = asyncio.new_event_loop()
        _ref1 = _weakref.ref(_lp1)
        check(_lp1.run_until_complete(_abs_fetcher.maybe_coroutine(lambda: 42)) == 42, "install_thread_offload runs sync callable")
        _lp1.run_until_complete(_lp1.shutdown_default_executor())
        _lp1.close()
        del _lp1
        _gc.collect()
        check(_ref1() is None, "install_thread_offload WeakKeyDictionary does not leak closed event loops")
    finally:
        _abs_fetcher.maybe_coroutine = _orig_mc

    _saved_get_sess = core_helpers.get_async_requests_session
    try:
        async def _ok_sess(**_kw):
            return _FakeSession(lambda _u: _FakeResp(200, {"features": []}))
        core_helpers.get_async_requests_session = _ok_sess
        _lp2 = asyncio.new_event_loop()
        _ref2 = _weakref.ref(_lp2)
        check(_lp2.run_until_complete(pwh._arcgis_query("https://example.com/q?")) == [], "install_imf_arcgis_fix _arcgis_query works across loops")
        _lp2.close()
        del _lp2
        _gc.collect()
        check(_ref2() is None, "install_imf_arcgis_fix WeakKeyDictionary does not leak closed event loops")
    finally:
        core_helpers.get_async_requests_session = _saved_get_sess


_test_reliability_hardening()

done("test_backend_wrapper")
