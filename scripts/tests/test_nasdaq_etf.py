"""Nasdaq ETF fallback (owner 2026-11-18: BWET, an NYSE Arca ETF, came up thin). openbb_nasdaq asks every quote,
profile, history and short-interest call with assetclass=stocks; Nasdaq answers ETFs only with assetclass=etf. The
runtime patch asks again as an ETF when Nasdaq has no such stock. Short interest: FINRA first (every U.S. listing)."""

import asyncio
import gc
import importlib.util
import inspect
import os
import pathlib
import sys
import tempfile
import threading
import time
import types
import weakref

from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

p = rb.nasdaq_etf_path
check(p("quote/BWET/info?assetclass=stocks") == "quote/BWET/info?assetclass=etf", "info asked as an ETF")
check(p("quote/KO/short-interest?assetClass=stocks") == "quote/KO/short-interest?assetClass=etf", "short interest (assetClass spelling)")
check(p("quote/SPY/historical?assetclass=stocks&fromdate=2026-01-01&limit=9999") == "quote/SPY/historical?assetclass=etf&fromdate=2026-01-01&limit=9999",
      "history: other parameters kept")
check(p("quote/SPY/info?assetclass=etf") is None and p("quote/X/info?assetclass=stocksplus") is None and p("calendar/splits?date=2026-01-01") is None,
      "only per-symbol paths asked as stocks")

# the wrapper, on a fake openbb_nasdaq.utils.helpers
calls = []
STORE = {"quote/BWET/info?assetclass=etf": {"symbol": "BWET"}, "quote/AAPL/info?assetclass=stocks": {"symbol": "AAPL"},
         "quote/QQQ/short-interest?assetClass=etf": {"rows": 1}}


async def fake(path, use_cache=True, **kw):
    calls.append(path)
    if path == "quote/BWET/info?assetclass=stocks":
        raise RuntimeError("Nasdaq request failed for 'quote/BWET/info?assetclass=stocks'. Symbol not exists.")
    if path == "quote/BOOM/info?assetclass=stocks":
        raise RuntimeError("HTTP 503")
    if path == "quote/RL/info?assetclass=stocks":
        raise RuntimeError("Nasdaq rate limit active (HTTP 429). Cooling down for ~120s.")
    if path == "quote/RL2/info?assetclass=stocks":
        return {}
    if path == "quote/RL2/info?assetclass=etf":
        raise RuntimeError("Nasdaq rate limit reached (HTTP 429). Cooling down for ~120s.")
    if path == "quote/KO/short-interest?assetClass=etf":
        raise RuntimeError("Symbol not exists.")
    return STORE.get(path)


helpers = types.ModuleType("openbb_nasdaq.utils.helpers")
helpers.get_nasdaq_data = fake
pkg, utils = types.ModuleType("openbb_nasdaq"), types.ModuleType("openbb_nasdaq.utils")
pkg.utils, utils.helpers = utils, helpers
saved = {k: sys.modules.get(k) for k in ("openbb_nasdaq", "openbb_nasdaq.utils", "openbb_nasdaq.utils.helpers")}
sys.modules.update({"openbb_nasdaq": pkg, "openbb_nasdaq.utils": utils, "openbb_nasdaq.utils.helpers": helpers})
try:
    applied = []
    rb.install_nasdaq_etf_fallback(applied)
    g = helpers.get_nasdaq_data
    check(applied and g is not fake, "installed")
    run = asyncio.run
    check(run(g("quote/BWET/info?assetclass=stocks")) == {"symbol": "BWET"}, "'Symbol not exists' as stocks -> answered as an ETF")
    calls.clear()
    check(run(g("quote/AAPL/info?assetclass=stocks")) == {"symbol": "AAPL"} and calls == ["quote/AAPL/info?assetclass=stocks"], "a stock: one call")
    check(run(g("quote/QQQ/short-interest?assetClass=stocks")) == {"rows": 1}, "empty as stocks -> asked as an ETF")
    check(run(g("quote/KO/short-interest?assetClass=stocks")) is None, "not an ETF either: the stocks answer (empty) stays")
    try:
        run(g("quote/BOOM/info?assetclass=stocks"))
        check(False, "other errors are raised")
    except RuntimeError as e:
        check("503" in str(e), "other errors are raised, not retried")
    calls.clear()
    try:
        run(g("quote/RL/info?assetclass=stocks"))
        check(False, "rate-limit error on stocks is raised")
    except RuntimeError as e:
        check("rate limit" in str(e).lower() and calls == ["quote/RL/info?assetclass=stocks"],
              "rate-limit error on stocks does not fire assetclass=etf")
    calls.clear()
    try:
        run(g("quote/RL2/info?assetclass=stocks"))
        check(False, "rate-limit error on ETF fallback is raised")
    except RuntimeError as e:
        check("rate limit" in str(e).lower() and calls == ["quote/RL2/info?assetclass=stocks", "quote/RL2/info?assetclass=etf"],
              "rate-limit error on empty->etf fallback is re-raised, not swallowed")
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

sys.path.insert(0, str(ROOT / "scripts"))
import build_dashboards as bd  # noqa: E402

members = bd.CONCEPTS["shorts.interest"]["members"]
check(members[0][1] == "finra" and members[1][1] == "nasdaq" and "Nasdaq-listed only" in members[1][2],
      "short interest: FINRA first (every U.S. listing), Nasdaq labelled Nasdaq-listed only")

# ---- Track 1: Nasdaq transport throttling, pooling, stable UA, 403/429 circuit breaker, options chain fan-out removal
import aiohttp  # noqa: E402
from aiohttp_client_cache import CacheBackend  # noqa: E402
from http.cookies import SimpleCookie  # noqa: E402
from multidict import CIMultiDict, CIMultiDictProxy  # noqa: E402
from yarl import URL  # noqa: E402
from openbb_nasdaq.models import options_chains as oc  # noqa: E402
from openbb_nasdaq.utils import helpers as nh, nordic as nn  # noqa: E402

# Apply rate-limit fix on real openbb_nasdaq modules (restoring originals in finally)
_orig_agent = nh.get_random_agent
_orig_cached = nh._cached_request
_orig_live = nh._live_request
_orig_cache_backend = nh.get_cache_backend
_orig_resolve = nh.resolve_asset_class
_orig_get_data = nh.get_nasdaq_data
_orig_contract_detail = nh.get_contract_detail
_orig_aextract = oc.NasdaqOptionsChainsFetcher.aextract_data
_orig_extract = getattr(oc.NasdaqOptionsChainsFetcher, "extract_data", None)
_orig_nordic_conc = nn.DIRECTORY_CONCURRENCY


class _FakeResp(aiohttp.ClientResponse):
    def __init__(self, url: str, status: int = 200, body: bytes = b"{}", headers: dict | None = None, from_cache: bool = False):
        self.method = "GET"
        self.status = status
        self.reason = "OK" if status == 200 else "ERR"
        self._body = body
        self._url = URL(url)
        self._headers = CIMultiDictProxy(CIMultiDict(headers or {"Content-Type": "application/json"}))
        self._raw_headers = tuple((k.encode(), v.encode()) for k, v in self._headers.items())
        self._request_info = types.SimpleNamespace(url=self._url, method="GET", headers=self._headers, real_url=self._url)
        self._history = ()
        self.cookies = SimpleCookie()
        self.version = aiohttp.HttpVersion(1, 1)
        self.from_cache = from_cache
        self._released = False
        self.released = False

    @property
    def request_info(self):
        return self._request_info

    @property
    def url(self) -> URL:
        return self._url

    @property
    def headers(self):
        return self._headers

    @property
    def raw_headers(self):
        return self._raw_headers

    @property
    def history(self):
        return self._history

    @property
    def links(self):
        return {}

    def get_encoding(self) -> str:
        return "utf-8"

    async def read(self) -> bytes:
        return self._body

    def release(self) -> None:
        self._released = True
        self.released = True


try:
    rb.reset_nasdaq_rate_limit_state()
    applied_rl = []
    rb.install_nasdaq_rate_limit_fix(applied_rl)
    check(bool(applied_rl), "install_nasdaq_rate_limit_fix applied")

    # 1. Stable User-Agent & idempotency
    ua1, ua2 = nh.get_random_agent(), nh.get_random_agent()
    check(ua1 == ua2 == rb._NASDAQ_USER_AGENT, "stable UA constant returned across calls")
    applied_rl_2 = []
    rb.install_nasdaq_rate_limit_fix(applied_rl_2)
    check(nh.get_random_agent() == rb._NASDAQ_USER_AGENT, "install_nasdaq_rate_limit_fix is idempotent")

    # 8. nordic.DIRECTORY_CONCURRENCY capped to NASDAQ_MAX_CONCURRENCY
    check(nn.DIRECTORY_CONCURRENCY == rb.NASDAQ_MAX_CONCURRENCY, "nordic.DIRECTORY_CONCURRENCY == NASDAQ_MAX_CONCURRENCY")

    # 2. Global concurrency cap & pacing gap + numeric timeout normalization
    async def _test_concurrency_and_pacing():
        rb.reset_nasdaq_rate_limit_state()
        active = 0
        peak = 0
        entered = []

        async def _worker():
            nonlocal active, peak
            async with rb._nasdaq_network_slot():
                active += 1
                peak = max(peak, active)
                entered.append(time.monotonic())
                await asyncio.sleep(0.5)
                active -= 1

        await asyncio.gather(*[_worker() for _ in range(6)])
        gaps = [entered[i + 1] - entered[i] for i in range(len(entered) - 1)]
        check(peak == rb.NASDAQ_MAX_CONCURRENCY, f"concurrency capped at {rb.NASDAQ_MAX_CONCURRENCY} (got {peak})")
        check(all(g >= rb.NASDAQ_MIN_GAP_SEC - 0.03 for g in gaps), f"pacing gap >= {rb.NASDAQ_MIN_GAP_SEC}s (gaps={[round(g, 3) for g in gaps]})")

        # Numeric timeout normalization on _NasdaqThrottledClient
        seen_timeouts = []
        orig_cs_req = aiohttp.ClientSession._request

        async def _stub_cs_req(self, method, str_or_url, *args, **kwargs):
            seen_timeouts.append(kwargs.get("timeout"))
            return _FakeResp(str(str_or_url), status=200, body=b'{"ok": true}')

        aiohttp.ClientSession._request = _stub_cs_req
        try:
            res = await nh._live_request("https://api.nasdaq.com/api/calendar/earnings", timeout=5)
            check(res == {"ok": True}, "_live_request decodes JSON")
            check(
                len(seen_timeouts) == 1
                and isinstance(seen_timeouts[0], aiohttp.ClientTimeout)
                and seen_timeouts[0].total == 5.0,
                "numeric timeout=5 normalized to ClientTimeout(total=5.0)",
            )
        finally:
            aiohttp.ClientSession._request = orig_cs_req
            await rb.close_nasdaq_sessions()

    asyncio.run(_test_concurrency_and_pacing())

    # 3. Cache hit bypasses network slot (works during cooldown) & concurrent misses coalesce to 1 fetch
    async def _test_cache_and_coalescing():
        rb.reset_nasdaq_rate_limit_state()
        mem_cache = CacheBackend()
        nh.get_cache_backend = lambda: mem_cache
        net_calls = []
        orig_cs_req = aiohttp.ClientSession._request

        async def _slow_stub(self, method, str_or_url, *args, **kwargs):
            net_calls.append(str(str_or_url))
            await asyncio.sleep(0.05)
            return _FakeResp(str(str_or_url), status=200, body=b'{"data": {"symbol": "AAPL"}, "status": {"rCode": 200}}')

        aiohttp.ClientSession._request = _slow_stub
        try:
            url = "https://api.nasdaq.com/api/quote/AAPL/info?assetclass=stocks"
            r1, r2, r3 = await asyncio.gather(
                nh._cached_request(url),
                nh._cached_request(url),
                nh._cached_request(url),
            )
            check(len(net_calls) == 1, f"3 concurrent cache misses coalesced to 1 fetch (got {len(net_calls)})")
            check(r1 == r2 == r3 == {"data": {"symbol": "AAPL"}, "status": {"rCode": 200}}, "all coalesced callers got payload")

            # Trip cooldown; cached URL must STILL succeed without hitting network
            try:
                rb._trip_nasdaq_cooldown(429)
            except Exception:  # noqa: BLE001
                pass
            r_hit = await nh._cached_request(url)
            check(r_hit == {"data": {"symbol": "AAPL"}, "status": {"rCode": 200}} and len(net_calls) == 1,
                  "cache hit bypasses network slot and succeeds during active cooldown")

            # Uncached URL must fail fast during cooldown with 0 extra network calls
            try:
                await nh._cached_request("https://api.nasdaq.com/api/quote/MSFT/info?assetclass=stocks")
                check(False, "uncached URL during cooldown should raise")
            except Exception as e:  # noqa: BLE001
                check("rate limit" in str(e).lower() and len(net_calls) == 1,
                      "uncached URL during cooldown fails fast with 0 network calls")
        finally:
            aiohttp.ClientSession._request = orig_cs_req
            nh.get_cache_backend = _orig_cache_backend
            await rb.close_nasdaq_sessions()

    asyncio.run(_test_cache_and_coalescing())

    # 4 & 5. 429 (with Retry-After clamping), 403, Akamai Access Denied, queued abort, 204 & empty 200
    async def _test_decoder_and_breaker():
        # 5. HTTP 204 and empty 200 still return {}
        rb.reset_nasdaq_rate_limit_state()
        check(await rb._decode_nasdaq_response(_FakeResp("https://api.nasdaq.com/x", status=204, body=b"")) == {},
              "HTTP 204 returns {}")
        check(await rb._decode_nasdaq_response(_FakeResp("https://api.nasdaq.com/x", status=200, body=b"   ")) == {},
              "HTTP 200 empty body returns {}")
        try:
            await rb._decode_nasdaq_response(_FakeResp("https://api.nasdaq.com/x", status=500, body=b""))
            check(False, "HTTP 500 empty body should raise")
        except Exception as e:  # noqa: BLE001
            check("500" in str(e), "HTTP 500 empty body raises error")

        # 4a. HTTP 429 Retry-After clamping (min 30s, max 600s, default 120s)
        for ra, expected_tag in (("5", "~30s"), ("9999", "~600s"), (None, "~120s")):
            rb.reset_nasdaq_rate_limit_state()
            hdrs = {"Retry-After": ra} if ra is not None else {}
            try:
                await rb._decode_nasdaq_response(_FakeResp("https://api.nasdaq.com/x", status=429, body=b"", headers=hdrs))
                check(False, f"429 Retry-After={ra} should raise")
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                check("rate limit" in msg and expected_tag in msg, f"429 Retry-After={ra} clamped to {expected_tag}")

        # 4b. HTTP 403 trips cooldown without exposing "403" or "Access Denied" (avoids missing paid key classification)
        rb.reset_nasdaq_rate_limit_state()
        try:
            await rb._decode_nasdaq_response(_FakeResp("https://api.nasdaq.com/x", status=403, body=b"Forbidden"))
            check(False, "HTTP 403 should raise")
        except Exception as e:  # noqa: BLE001
            msg403 = str(e).lower()
            check(
                "rate limit" in msg403 and "temporarily blocked" in msg403 and "403" not in msg403 and "access denied" not in msg403,
                "HTTP 403 trips cooldown with temporarily-blocked wording (no 403/access denied)",
            )

        # 4c. Akamai HTML Access Denied (even on 200) trips cooldown instead of returning {}
        rb.reset_nasdaq_rate_limit_state()
        akamai_html = b"<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>Reference #18.abc</BODY></HTML>"
        try:
            await rb._decode_nasdaq_response(_FakeResp("https://api.nasdaq.com/x", status=200, body=akamai_html))
            check(False, "Akamai Access Denied HTML should trip cooldown")
        except Exception as e:  # noqa: BLE001
            msg_ak = str(e).lower()
            check("rate limit" in msg_ak and "temporarily blocked" in msg_ak, "Akamai Access Denied HTML trips cooldown (not {})")

        # 4c2. HTTP 200 Akamai HTML block and empty 200 body are NOT cached into SQLite/CacheBackend, and pre-poisoned cache hits evict
        rb.reset_nasdaq_rate_limit_state()
        poison_cache = CacheBackend()
        nh.get_cache_backend = lambda: poison_cache
        orig_cs_req = aiohttp.ClientSession._request
        try:
            # First call: 200 Akamai HTML block -> trips cooldown and MUST NOT be stored in cache
            blk_url = "https://api.nasdaq.com/api/calendar/economicevents?date=2026-11-24"
            empty_url = "https://api.nasdaq.com/api/calendar/economicevents?date=2026-11-25"
            calls_seen = []

            async def _poison_stub(self, method, str_or_url, *args, **kwargs):
                su = str(str_or_url)
                calls_seen.append(su)
                if su == blk_url and len([c for c in calls_seen if c == blk_url]) == 1:
                    return _FakeResp(su, status=200, body=akamai_html)
                if su == empty_url and len([c for c in calls_seen if c == empty_url]) == 1:
                    return _FakeResp(su, status=200, body=b"   ")
                return _FakeResp(su, status=200, body=b'{"data": {"rows": [{"ok": 1}]}, "status": {"rCode": 200}}')

            aiohttp.ClientSession._request = _poison_stub
            try:
                await nh._cached_request(blk_url)
                check(False, "200 Akamai HTML should trip cooldown")
            except Exception as e:  # noqa: BLE001
                check("rate limit" in str(e).lower(), "200 Akamai HTML raises rate limit")

            with rb._nasdaq_rl_lock:
                rb._nasdaq_blocked_until = 0.0
                rb._nasdaq_blocked_reason = ""

            res_after_blk = await nh._cached_request(blk_url)
            check(
                res_after_blk == {"data": {"rows": [{"ok": 1}]}, "status": {"rCode": 200}}
                and calls_seen.count(blk_url) == 2,
                "200 Akamai HTML was not cached: next request refetches valid JSON",
            )

            res_empty1 = await nh._cached_request(empty_url)
            res_empty2 = await nh._cached_request(empty_url)
            check(
                res_empty1 == {}
                and res_empty2 == {"data": {"rows": [{"ok": 1}]}, "status": {"rCode": 200}}
                and calls_seen.count(empty_url) == 2,
                "empty 200 response was not cached: next request refetches valid JSON",
            )

            # Pre-poisoned entry already in cache (from_cache=True) is evicted on read
            evict_url = "https://api.nasdaq.com/api/calendar/economicevents?date=2026-11-26"
            key_evict = poison_cache.create_key("GET", evict_url)
            await poison_cache.save_response(_FakeResp(evict_url, status=200, body=b"<html>bad</html>"), key_evict)
            check(await poison_cache.has_url(evict_url), "pre-poisoned entry seeded in cache")
            dec_evict = await rb._decode_nasdaq_response(
                _FakeResp(evict_url, status=200, body=b"<html>bad</html>", from_cache=True),
                from_cache=True,
                cache=poison_cache,
                url=evict_url,
            )
            check(
                dec_evict == {} and not await poison_cache.has_url(evict_url),
                "pre-poisoned cached entry returns {} and is evicted from cache",
            )
        finally:
            aiohttp.ClientSession._request = orig_cs_req
            nh.get_cache_backend = _orig_cache_backend
            await rb.close_nasdaq_sessions()

        # 4d. Queued calls waiting for slot abort immediately with 0 extra network requests once breaker trips
        rb.reset_nasdaq_rate_limit_state()
        slot_entries = []

        async def _first_trips():
            async with rb._nasdaq_network_slot():
                slot_entries.append("first")
                await asyncio.sleep(0.05)
                rb._trip_nasdaq_cooldown(429)

        async def _queued_follower():
            await asyncio.sleep(0.01)
            async with rb._nasdaq_network_slot():
                slot_entries.append("follower")

        results = await asyncio.gather(_first_trips(), _queued_follower(), _queued_follower(), return_exceptions=True)
        check(
            all(isinstance(r, Exception) and "rate limit" in str(r).lower() for r in results)
            and slot_entries == ["first"],
            f"queued tasks fail fast with 0 extra network requests (entries={slot_entries})",
        )

    asyncio.run(_test_decoder_and_breaker())

    # 6. NasdaqOptionsChainsFetcher: both aextract_data & extract_data are coroutines, 0 detail calls, halts on RL, valid transform_data
    check(
        inspect.iscoroutinefunction(oc.NasdaqOptionsChainsFetcher.aextract_data)
        and inspect.iscoroutinefunction(oc.NasdaqOptionsChainsFetcher.extract_data),
        "NasdaqOptionsChainsFetcher.aextract_data and extract_data are both coroutines",
    )

    async def _test_options_chains():
        rb.reset_nasdaq_rate_limit_state()
        requested_paths = []
        detail_called = False

        async def _fake_resolve(sym):
            return "stocks"

        async def _fake_detail(*args, **kwargs):
            nonlocal detail_called
            detail_called = True
            raise AssertionError("get_contract_detail must not be called")

        sample_chain = {
            "lastTrade": "LAST TRADE: $200.50 (AS OF NOV 24, 2026)",
            "table": {
                "rows": [
                    {
                        "strike": "200.00",
                        "drillDownURL": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261218c00200000",
                        "c_Last": "5.25",
                        "c_Change": "0.50",
                        "c_Bid": "5.20",
                        "c_Ask": "5.30",
                        "c_Volume": "120",
                        "c_Openinterest": "500",
                        "p_Last": "4.75",
                        "p_Change": "-0.25",
                        "p_Bid": "4.70",
                        "p_Ask": "4.80",
                        "p_Volume": "80",
                        "p_Openinterest": "300",
                    }
                ]
            },
        }
        sample_greeks = {
            "table": {
                "rows": [
                    {
                        "strike": "200.00",
                        "url": "/market-activity/stocks/aapl/option-chain/call-put-options/aapl--261218c00200000",
                        "cDelta": "0.52",
                        "cGamma": "0.04",
                        "cTheta": "-0.08",
                        "cVega": "0.15",
                        "cRho": "0.05",
                        "cIV": "0.24",
                        "pDelta": "-0.48",
                        "pGamma": "0.04",
                        "pTheta": "-0.07",
                        "pVega": "0.15",
                        "pRho": "-0.04",
                        "pIV": "0.25",
                    }
                ]
            }
        }

        async def _fake_get_data(path, *args, **kwargs):
            requested_paths.append(path)
            if "option-chain/greeks" in path:
                return sample_greeks
            return sample_chain

        nh.resolve_asset_class = _fake_resolve
        nh.get_nasdaq_data = _fake_get_data
        nh.get_contract_detail = _fake_detail
        try:
            q = oc.NasdaqOptionsChainsFetcher.transform_query({"symbol": "AAPL"})
            raw_a = await oc.NasdaqOptionsChainsFetcher.aextract_data(q, None)
            check(
                not detail_called
                and raw_a["detail"] == {}
                and len(requested_paths) == 2
                and "option-chain?" in requested_paths[0]
                and "option-chain/greeks?" in requested_paths[1],
                f"aextract_data issues only chain + greeks (paths={requested_paths})",
            )
            requested_paths.clear()
            raw_e = await oc.NasdaqOptionsChainsFetcher.extract_data(q, None)
            check(raw_e["detail"] == {} and len(requested_paths) == 2, "extract_data matches aextract_data")

            transformed = oc.NasdaqOptionsChainsFetcher.transform_data(q, raw_a)
            res = transformed.result
            check(
                res.underlying_symbol == ["AAPL", "AAPL"]
                and res.strike == [200.0, 200.0]
                and res.option_type == ["call", "put"]
                and res.bid == [5.2, 4.7]
                and res.delta == [0.52, -0.48],
                "transform_data produces valid call/put rows with detail={}",
            )

            # Rate-limit error during greeks fetch stops remaining expirations
            requested_paths.clear()

            async def _fake_get_data_rl(path, *args, **kwargs):
                requested_paths.append(path)
                if "option-chain/greeks" in path:
                    raise rb._nasdaq_error("Nasdaq rate limit reached (HTTP 429). Cooling down for ~120s.")
                return sample_chain

            nh.get_nasdaq_data = _fake_get_data_rl
            q2 = oc.NasdaqOptionsChainsFetcher.transform_query({"symbol": "AAPL", "expiration": "2026-12-18,2027-01-15"})
            try:
                await oc.NasdaqOptionsChainsFetcher.aextract_data(q2, None)
                check(False, "greeks rate-limit error should propagate")
            except Exception as e:  # noqa: BLE001
                check(
                    "rate limit" in str(e).lower() and len(requested_paths) == 2,
                    f"options greeks halts immediately on rate limit (paths={len(requested_paths)})",
                )
        finally:
            nh.resolve_asset_class = _orig_resolve
            nh.get_nasdaq_data = _orig_get_data
            nh.get_contract_detail = _orig_contract_detail

    asyncio.run(_test_options_chains())

    # 11. Review fixes: resolve_asset_class never memoizes "stocks" during a cooldown; rCode misses stay cacheable;
    #     plain HTML 404 does not trip the cooldown; sessions carry a 10s default timeout and env proxy; process-wide cap
    #     across loops; delete_expired_responses awaits the async filter.
    rb.reset_nasdaq_rate_limit_state()
    nh.resolve_asset_class = _orig_resolve
    rb.install_nasdaq_rate_limit_fix([])
    patched_resolve = nh.resolve_asset_class
    check(getattr(patched_resolve, "_terminal_nasdaq_rl_fixed", False), "resolve_asset_class replaced by rate-limit-aware version")
    probe_calls = []
    cooldown_on = [True]

    async def _probe_data(path, use_cache=True, **kw):
        probe_calls.append(path)
        if cooldown_on[0]:
            raise RuntimeError("Nasdaq rate limit active (HTTP 429). Cooling down for ~100s")
        if "assetclass=index" in path:
            return {"symbol": "SPX"}
        raise RuntimeError(f"Nasdaq request failed for '{path}'. Symbol not exists.")

    nh.get_nasdaq_data = _probe_data
    try:
        async def _test_resolve():
            patched_resolve.cache_clear()
            try:
                await nh.resolve_asset_class("ZZSPXTEST")
                got = "no error"
            except Exception as e:  # noqa: BLE001
                got = str(e)
            first = len(probe_calls)
            cooldown_on[0] = False
            ac = await nh.resolve_asset_class("ZZSPXTEST")
            return got, first, ac

        got_rl, n_first, ac_after = asyncio.run(_test_resolve())
        check("rate limit" in got_rl.lower() and n_first == 1, f"resolve_asset_class re-raises on cooldown after 1 probe ({n_first})")
        check(ac_after == "index", f"after cooldown the same symbol resolves to index, not a memoized 'stocks' ({ac_after})")
    finally:
        nh.get_nasdaq_data = _orig_get_data
        nh.resolve_asset_class = _orig_resolve

    async def _test_review_transport():
        out = {}
        rb.reset_nasdaq_rate_limit_state()
        out["rcode400_cacheable"] = await rb._nasdaq_cache_filter(
            _FakeResp("https://api.nasdaq.com/x", body=b'{"data": null, "status": {"rCode": 400}}'))
        out["html_rejected"] = not await rb._nasdaq_cache_filter(
            _FakeResp("https://api.nasdaq.com/x", body=b"<html>blocked</html>"))
        html404 = b"<html><head><script src='https://www.nasdaq.com.edgesuite.net/a.js'></script></head><body>Page not found (akamai)</body></html>"
        res404 = await rb._decode_nasdaq_response(_FakeResp("https://api.nasdaq.com/old", status=404, body=html404))
        try:
            rb._check_nasdaq_cooldown()
            out["404_no_cooldown"] = res404 == {}
        except Exception:  # noqa: BLE001
            out["404_no_cooldown"] = False
        saved_env = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY")}
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = "http://127.0.0.1:3128"
        try:
            await rb.close_nasdaq_sessions()
            live = await rb._get_nasdaq_live_session()
            out["timeout"] = live.timeout.total
            out["proxy"] = str(getattr(live, "_default_proxy", "") or "")
        finally:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            await rb.close_nasdaq_sessions()
        mem = CacheBackend()
        rb._configure_nasdaq_cache_backend(mem)
        good = _FakeResp("https://api.nasdaq.com/good", body=b'{"status": {"rCode": 200}}')
        bad = _FakeResp("https://api.nasdaq.com/bad", body=b"<html>x</html>")
        await mem.responses.write("good", good)
        await mem.responses.write("bad", bad)
        await mem.delete_expired_responses()
        out["kept"] = sorted([k async for k in mem.responses.keys()])
        return out

    rv = asyncio.run(_test_review_transport())
    check(rv["rcode400_cacheable"] and rv["html_rejected"], "cache filter keeps rCode 400 JSON misses, rejects HTML")
    check(rv["404_no_cooldown"], "plain HTML 404 mentioning Akamai/edgesuite does not trip the cooldown")
    check(rv["timeout"] == 10.0, f"pooled Nasdaq session defaults to a 10s timeout ({rv['timeout']})")
    check(rv["proxy"].startswith("http://127.0.0.1:3128"), f"pooled Nasdaq session honors HTTP(S)_PROXY ({rv['proxy']})")
    check(rv["kept"] == ["good"], f"delete_expired_responses awaits the async filter ({rv['kept']})")

    # Process-wide cap: 3 threads x 4 requests on separate loops never exceed NASDAQ_MAX_CONCURRENCY in flight
    rb.reset_nasdaq_rate_limit_state()
    peak = [0, 0]
    peak_lock = threading.Lock()

    async def _one_slot():
        async with rb._nasdaq_network_slot():
            with peak_lock:
                peak[0] += 1
                peak[1] = max(peak[1], peak[0])
            await asyncio.sleep(0.7)
            with peak_lock:
                peak[0] -= 1

    def _loop_worker():
        async def _many():
            await asyncio.gather(*[_one_slot() for _ in range(4)])
        asyncio.run(_many())

    workers = [threading.Thread(target=_loop_worker) for _ in range(3)]
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=10)
    check(peak[1] == rb.NASDAQ_MAX_CONCURRENCY, f"cap holds across event loops (peak {peak[1]}, max {rb.NASDAQ_MAX_CONCURRENCY})")

    # 9. close_nasdaq_sessions() + closed event loop GC check
    rb.reset_nasdaq_rate_limit_state()
    nh.get_cache_backend = lambda: CacheBackend()
    try:
        def _run_ephemeral_loop():
            loop = asyncio.new_event_loop()
            ref = weakref.ref(loop)

            async def _make_and_close():
                await rb._get_nasdaq_live_session()
                await rb._get_nasdaq_cached_session(nh)
                await rb.close_nasdaq_sessions(loop)

            try:
                loop.run_until_complete(_make_and_close())
            finally:
                loop.close()
            return ref

        loop_ref = _run_ephemeral_loop()
        gc.collect()
        check(loop_ref() is None, "closed event loop is garbage-collected after close_nasdaq_sessions()")

        # 10. Real SQLiteBackend: worker threads marked daemon & stopped synchronously by reset_nasdaq_rate_limit_state()
        from aiohttp_client_cache import SQLiteBackend

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(pathlib.Path(tmpdir) / "nasdaq_test")
            nh.get_cache_backend = lambda: SQLiteBackend(
                cache_name=db_path,
                expire_after=60,
                allowed_codes=(200,),
                allowed_methods=("GET",),
            )
            rb.reset_nasdaq_rate_limit_state()
            orig_cs_req = aiohttp.ClientSession._request

            async def _sqlite_ok(self, method, str_or_url, *args, **kwargs):
                return _FakeResp(str(str_or_url), status=200, body=b'{"data": {"ok": 1}, "status": {"rCode": 200}}')

            aiohttp.ClientSession._request = _sqlite_ok
            worker_threads = []
            sqlite_loop = asyncio.new_event_loop()
            try:
                async def _exercise_sqlite():
                    res = await nh._cached_request("https://api.nasdaq.com/api/quote/AAPL/info?assetclass=stocks")
                    sess = await rb._get_nasdaq_cached_session(nh)
                    for attr in ("responses", "redirects"):
                        conn = getattr(getattr(sess.cache, attr, None), "_connection", None)
                        th = getattr(conn, "_thread", None)
                        if th is not None:
                            worker_threads.append(th)
                    return res

                res_sql = sqlite_loop.run_until_complete(_exercise_sqlite())
            finally:
                aiohttp.ClientSession._request = orig_cs_req
                sqlite_loop.close()

            check(res_sql == {"data": {"ok": 1}, "status": {"rCode": 200}}, "real SQLiteBackend _cached_request succeeds")
            check(bool(worker_threads) and all(th.daemon for th in worker_threads), "aiosqlite worker threads are daemon")
            rb.reset_nasdaq_rate_limit_state()
            for th in worker_threads:
                th.join(timeout=2.0)
            check(all(not th.is_alive() for th in worker_threads), "reset_nasdaq_rate_limit_state() stops aiosqlite worker threads on closed loop")
    finally:
        nh.get_cache_backend = _orig_cache_backend
finally:
    rb.reset_nasdaq_rate_limit_state()
    nh.get_random_agent = _orig_agent
    nh._cached_request = _orig_cached
    nh._live_request = _orig_live
    nh.get_cache_backend = _orig_cache_backend
    nh.resolve_asset_class = _orig_resolve
    nh.get_nasdaq_data = _orig_get_data
    nh.get_contract_detail = _orig_contract_detail
    oc.NasdaqOptionsChainsFetcher.aextract_data = _orig_aextract
    if _orig_extract is not None:
        oc.NasdaqOptionsChainsFetcher.extract_data = _orig_extract
    nn.DIRECTORY_CONCURRENCY = _orig_nordic_conc

# ---- P2-04: the gate is fair (arrival order), bounded (no hang), and a 429 stops all traffic until its cooldown ends
rb.reset_nasdaq_rate_limit_state()
arrivals, acquired, starts = [], [], []
inflight = [0, 0]
order_lock = threading.Lock()


async def _queued(i):
    await asyncio.sleep(0.01 * i)                       # distinct arrival times, 10 ms apart, across three loops
    with order_lock:
        arrivals.append(i)
    async with rb._nasdaq_network_slot(30):
        with order_lock:
            acquired.append(i)
            starts.append(time.monotonic())
            inflight[0] += 1
            inflight[1] = max(inflight[1], inflight[0])
        await asyncio.sleep(0.05)
        with order_lock:
            inflight[0] -= 1


def _loop_of(ids):
    async def _all():
        await asyncio.gather(*[_queued(i) for i in ids])
    asyncio.run(_all())


loops = [threading.Thread(target=_loop_of, args=(list(range(k, 30, 3)),)) for k in range(3)]
for t in loops:
    t.start()
for t in loops:
    t.join(timeout=60)
gaps = [b - a for a, b in zip(starts, starts[1:])]
check(len(acquired) == 30 and acquired == arrivals, f"30 waiters on 3 loops get slots in arrival order ({acquired[:8]}... vs {arrivals[:8]}...)")
check(inflight[1] <= rb.NASDAQ_MAX_CONCURRENCY, f"peak in flight {inflight[1]} <= {rb.NASDAQ_MAX_CONCURRENCY}")
# the gate stamps each start under its lock; the test reads the clock a moment later in another thread (10 ms of hand-off)
check(all(g >= rb.NASDAQ_MIN_GAP_SEC - 0.01 for g in gaps), f"every start gap >= {rb.NASDAQ_MIN_GAP_SEC} s (min {min(gaps):.3f})")
check(not rb._nasdaq_queue, "the queue is empty afterwards")


async def _bounded():
    hold = asyncio.Event()

    async def _holder():
        async with rb._nasdaq_network_slot(30):
            await hold.wait()
    holders = [asyncio.ensure_future(_holder()) for _ in range(rb.NASDAQ_MAX_CONCURRENCY)]
    await asyncio.sleep(0.8)                            # every slot taken
    t0 = time.monotonic()
    err = None
    try:
        async with rb._nasdaq_network_slot(0.4):
            pass
    except Exception as e:  # noqa: BLE001
        err = e
    took = time.monotonic() - t0
    queued_after = len(rb._nasdaq_queue)
    hold.set()
    await asyncio.gather(*holders)
    return err, took, queued_after


rb.reset_nasdaq_rate_limit_state()
err, took, queued_after = asyncio.run(_bounded())
check(err is not None and "rate limit" in str(err).lower() and "try again" in str(err).lower() and 0.35 <= took < 1.0,
      f"a slot wait ends at its bound with a rate-limit message and a retry hint ({took:.2f} s: {err})")
check(queued_after == 0, "a waiter that gave up leaves the queue (it never blocks the ones behind it)")

async def _cancelled_turn():
    """A request cancelled while waiting for its start turn is skipped: the one behind it still starts."""
    rb._nasdaq_last_start = time.monotonic() + 0.3                # the next start is 0.45 s away
    got = []

    async def _req(tag):
        async with rb._nasdaq_network_slot(5):
            got.append((tag, time.monotonic()))
    a = asyncio.ensure_future(_req("a"))
    await asyncio.sleep(0.05)
    b = asyncio.ensure_future(_req("b"))
    await asyncio.sleep(0.05)
    a.cancel()
    t0 = time.monotonic()
    await asyncio.wait_for(b, timeout=3)
    return got, time.monotonic() - t0, a.cancelled()


rb.reset_nasdaq_rate_limit_state()
got, took, was_cancelled = asyncio.run(_cancelled_turn())
check(was_cancelled and [g[0] for g in got] == ["b"] and took < 1.0, f"a cancelled waiter does not hold up the next one ({got}, {took:.2f} s)")
rb.reset_nasdaq_rate_limit_state()

upstream = [0]


async def _caller(first):
    async with rb._nasdaq_network_slot(5):
        upstream[0] += 1
        if first:
            rb._trip_nasdaq_cooldown(429, retry_after="30")


async def _after_429():
    msgs = []
    try:
        await _caller(True)
    except Exception as e:  # noqa: BLE001
        msgs.append(str(e))
    for r in await asyncio.gather(*[_caller(False) for _ in range(8)], return_exceptions=True):
        msgs.append(str(r))
    return msgs


rb.reset_nasdaq_rate_limit_state()
msgs = asyncio.run(_after_429())
left = rb._nasdaq_blocked_until - time.monotonic()
check(upstream[0] == 1, f"after a 429 with Retry-After: 30, no further upstream request ({upstream[0]})")
check(len(msgs) == 9 and all("rate limit" in m.lower() and "cooling down" in m.lower() for m in msgs), "every caller gets the cooldown message")
check(28 <= left <= 30, f"cooldown honours Retry-After: 30 ({left:.1f} s left)")
rb._nasdaq_blocked_until = time.monotonic() - 1                # the cooldown ends
asyncio.run(_caller(False))
check(upstream[0] == 2, "after the cooldown the next request goes upstream")
rb.reset_nasdaq_rate_limit_state()

# the gate's limits are declared once more in the UI's provider policy table: both copies are equal
from composite.core import http as _H  # noqa: E402

gate = _H.PROVIDERS["nasdaq"]["backend_gate"]
check(gate == {"max_concurrency": rb.NASDAQ_MAX_CONCURRENCY, "min_gap": rb.NASDAQ_MIN_GAP_SEC, "cooldown": rb.NASDAQ_COOLDOWN_SEC,
               "retry_after_min": rb.NASDAQ_RETRY_AFTER_MIN, "retry_after_max": rb.NASDAQ_RETRY_AFTER_MAX,
               "slot_wait": rb.NASDAQ_DEFAULT_TIMEOUT_SEC}, f"PROVIDERS['nasdaq']['backend_gate'] equals run_backend's NASDAQ_* ({gate})")

done("test_nasdaq_etf")
