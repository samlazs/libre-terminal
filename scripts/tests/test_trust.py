"""HANDOFF P2 trust signals beyond 'stale': fallback source, capped history, delayed prices (footer markers)."""

import datetime as dt
import json
import sys
import pathlib
import tempfile
import time

from _common import check, done

import composite as c

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_backend as rb  # noqa: E402

# ---- fallback: OECD refuses -> FRED copies, and the answer says so for the footer
saved = c._oecd_series, c._fred
try:
    def refuse(duration):
        raise c.CompositeError("OECD rate limit reached (HTTP 429)")
    c._oecd_series = refuse
    c._fred = lambda ids, start: ({ids[0]: [["2026-01-01", 2.5], ["2026-02-01", 2.6]]}, {})
    out = c.rate_history(country="germany", duration="long", start="2025")
    check(out.get("fallback") == c.FALLBACK_OECD, "rate history: OECD refused, FRED used, footer marker set")
finally:
    c._oecd_series, c._fred = saved


def fake_curves(src):
    def f(countries, dates=None, us_source="auto"):
        out = {k: {"2026-11-13": [(1.0, 3.6), (10.0, 4.1)]} for k in countries}
        out["__src__"] = {k: (src if k == "united_states" else c.COUNTRIES[k][1]) for k in countries}
        return out
    return f


saved = c.curves
try:
    c.curves = fake_curves("fred")
    check(c.yield_curves(country="united_states").get("fallback") == c.FALLBACK_US_CURVE, "U.S. curve from FRED under 'auto': marker")
    check("fallback" not in c.yield_curves(country="united_states", source="fred"), "FRED picked on purpose: no marker")
    check(c.yield_curve_table(country="united_states", compare="").get("fallback") == c.FALLBACK_US_CURVE, "curve table: marker")
    c.curves = fake_curves("fed")
    check("fallback" not in c.yield_curves(country="united_states,germany"), "Fed route answered: no marker")
finally:
    c.curves = saved

# ---- truncated: EIA sends the newest 5,000 rows; a chart starting before the oldest says so
saved = c._eia_one
try:
    c._eia_one = lambda sid: {"pts": [["2010-01-04", 80.0], ["2026-11-14", 60.0]], "meta": {"title": "WTI", "units": "$/bbl", "capped_from": "2010-01-04"}}
    _, meta = c._series(["EIA:PET.RWTC.D"], "2000-01-01")
    check(meta["EIA:PET.RWTC.D"].get("truncated") == "EIA sends the latest 5,000 observations: PET.RWTC.D starts 2010-01-04", "capped before the start asked")
    _, meta = c._series(["EIA:PET.RWTC.D"], "2015-01-01")
    check("truncated" not in meta["EIA:PET.RWTC.D"], "start after the cap: nothing to say")
finally:
    c._eia_one = saved

# ---- delayed: terminal views over Yahoo prices, backend price snapshots by path and provider
saved = dict(c.ROUTES)
try:
    c.ROUTES["board"] = lambda **p: {"results": []}
    c.ROUTES["chart"] = lambda **p: {"data": []}
    c.ROUTES["gpr_table"] = lambda **p: {"results": []}
    check(c.dispatch("board", {}).get("delayed") == c.DELAYED_YAHOO, "board: delayed")
    check("delayed" not in c.dispatch("chart", {"symbol": "FRED:DGS10"}) and c.dispatch("chart", {"symbol": "AAPL"}).get("delayed"), "FRED chart not delayed, AAPL chart delayed")
    check("delayed" not in c.dispatch("gpr_table", {}), "monthly index: not a price, no marker")
finally:
    c.ROUTES.clear()
    c.ROUTES.update(saved)
check(rb.delayed_note("/api/v1/equity/price/quote", b"symbol=AAPL&provider=cboe") == "Delayed prices from Cboe (free feed, not real time)", "Cboe quote")
check(rb.delayed_note("/api/v1/tmx/markets/movers", b"") == "Delayed prices from TMX (free feed, not real time)", "TMX movers, provider from the path")
check(rb.delayed_note("/api/v1/derivatives/options/chains", b"symbol=BTC&provider=deribit") == "", "Deribit is real time")
check(rb.delayed_note("/api/v1/equity/price/historical", b"provider=yfinance") == "", "daily history is not marked")
check(rb.stale_header([rb.delayed_note("/api/v1/index/snapshots", b"provider=yfinance")]) == b"Delayed prices from Yahoo Finance (free feed, not real time)", "header value is ASCII")

# ---- SWR cached_text vs stale_text + cached_disk failure keeps 'stale' + Data status V5-only phrasing
t_ref = dt.datetime(2026, 11, 19, 14, 20, tzinfo=dt.timezone.utc).timestamp()
swr_msg = c.cached_text([time.time() - 600])
stale_msg = c.stale_text([t_ref])
check(swr_msg.startswith("Cached ") and swr_msg.endswith("· refreshing") and "Source not answering" not in swr_msg,
      "cached_text formats calm SWR message without 'Source not answering'")
check(stale_msg.startswith("Source not answering: showing the copy saved 2026-11-19 "),
      "stale_text keeps 'Source not answering' for failed fetches")

_cd_saved = c.CACHE_DIR
_tmp_cd = tempfile.TemporaryDirectory()
try:
    c.CACHE_DIR = pathlib.Path(_tmp_cd.name)
    c._cache.clear()
    c._backoff.clear()
    c._STALE_SINCE.clear()
    (c.CACHE_DIR / "disk_fail.json").write_text(json.dumps({"t": time.time() - 7200, "v": {"results": [{"x": 1}], "sources": [{"name": "OECD", "detail": "d", "url": ""}]}}), encoding="utf-8")
    saved_r = dict(c.ROUTES)
    try:
        def _fail_fn():
            raise c.CompositeError("OECD rate limit reached")
        c.ROUTES["__disk_fail"] = lambda **_: c.cached_disk("disk_fail", 60, _fail_fn)
        out_df = c.dispatch("__disk_fail", {})
        check("stale" in out_df and "Source not answering:" in out_df["stale"] and "cached" not in out_df,
              "cached_disk fallback after CompositeError still emits 'stale' (not 'cached')")
        check(out_df["status"] == "stale" and out_df["notes"] == [out_df["stale"]], "a stale fallback answers status 'stale' with the note")
    finally:
        c.ROUTES.clear()
        c.ROUTES.update(saved_r)
finally:
    c.CACHE_DIR = _cd_saved
    c._cache.clear()
    c._backoff.clear()
    c._STALE_SINCE.clear()
    _tmp_cd.cleanup()

ds_md = c.data_status()["content"]
check("main V5 data service by default" in ds_md and "--with-v4" in ds_md, "Data status documents V5-only default")

# ---- http_json / _download retries, no-OECD-retry invariant, cached_disk backoff tiers, reset_runtime_caches
import io as _io
import urllib.error as _ue
import urllib.request as _ur
from composite.core import cache as _cache_mod
from composite.core import http as _http

_saved_delays = _http._RETRY_DELAYS
_saved_urlopen = _ur.urlopen
_http._RETRY_DELAYS = (0.0,)
try:
    _calls = [0]

    def _flaky_503(req, timeout=None, **kw):
        _calls[0] += 1
        if _calls[0] == 1:
            raise _ue.HTTPError(req.full_url, 503, "Unavailable", {}, _io.BytesIO(b""))
        class _R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": 1}'
        return _R()

    _ur.urlopen = _flaky_503
    check(_http.http_json("https://example.com/test") == {"ok": 1} and _calls[0] == 2, "http_json retries once on 503")

    _calls_429 = [0]
    def _fail_429(req, timeout=None, **kw):
        _calls_429[0] += 1
        raise _ue.HTTPError(req.full_url, 429, "Too Many", {}, _io.BytesIO(b""))

    _ur.urlopen = _fail_429
    try:
        _http.http_json("https://example.com/rate")
        check(False, "429 should raise")
    except c.CompositeError as e:
        check(e.transient and _calls_429[0] == 1, "http_json 429 raises transient=True without retrying")
    try:
        _http.http_json("https://example.com/rate")
    except c.CompositeError as e:
        check(e.kind == "rate_limited" and _calls_429[0] == 1, "during the 429 cooldown nothing more is sent to that host")
    c.reset_outbound()                    # the cases below are about retries, not the cooldown

    for _oecd_url in ("http://127.0.0.1:6900/api/v1/economy/gdp?provider=oecd", "http://127.0.0.1:6900/api/v1/oecd/nominal"):
        _oecd_calls = [0]
        def _fail_oecd_503(req, timeout=None, **kw):
            _oecd_calls[0] += 1
            raise _ue.HTTPError(req.full_url, 503, "Unavailable", {}, _io.BytesIO(b""))
        _ur.urlopen = _fail_oecd_503
        try:
            _http.http_json(_oecd_url)
            check(False, "OECD 503 should raise")
        except c.CompositeError as e:
            check(e.transient and _oecd_calls[0] == 1, f"http_json never retries OECD 503: {_oecd_url}")

        _oecd_calls[0] = 0
        def _fail_oecd_urlerr(req, timeout=None, **kw):
            _oecd_calls[0] += 1
            raise _ue.URLError("reset")
        _ur.urlopen = _fail_oecd_urlerr
        try:
            _http.http_json(_oecd_url)
            check(False, "OECD URLError should raise")
        except c.CompositeError as e:
            check(e.transient and _oecd_calls[0] == 1, f"http_json never retries OECD URLError: {_oecd_url}")

    for _nasdaq_url in (
        "http://127.0.0.1:6900/api/v1/economy/calendar?provider=nasdaq",
        "http://127.0.0.1:6900/api/v1/nasdaq/markets/movers",
    ):
        _nasdaq_calls = [0]
        def _fail_nasdaq_503(req, timeout=None, **kw):
            _nasdaq_calls[0] += 1
            raise _ue.HTTPError(req.full_url, 503, "Unavailable", {}, _io.BytesIO(b""))
        _ur.urlopen = _fail_nasdaq_503
        try:
            _http.http_json(_nasdaq_url)
            check(False, "Nasdaq 503 should raise")
        except c.CompositeError as e:
            check(e.transient and _nasdaq_calls[0] == 1, f"http_json never retries Nasdaq 503: {_nasdaq_url}")

        _nasdaq_calls[0] = 0
        def _fail_nasdaq_urlerr(req, timeout=None, **kw):
            _nasdaq_calls[0] += 1
            raise _ue.URLError("reset")
        _ur.urlopen = _fail_nasdaq_urlerr
        try:
            _http.http_json(_nasdaq_url)
            check(False, "Nasdaq URLError should raise")
        except c.CompositeError as e:
            check(e.transient and _nasdaq_calls[0] == 1, f"http_json never retries Nasdaq URLError: {_nasdaq_url}")

        _nasdaq_calls[0] = 0
        def _fail_nasdaq_timeout(req, timeout=None, **kw):
            _nasdaq_calls[0] += 1
            raise TimeoutError("timed out")
        _ur.urlopen = _fail_nasdaq_timeout
        try:
            _http.http_json(_nasdaq_url)
            check(False, "Nasdaq TimeoutError should raise")
        except c.CompositeError as e:
            check(e.transient and _nasdaq_calls[0] == 1, f"http_json never retries Nasdaq TimeoutError: {_nasdaq_url}")

    _dl_calls = [0]
    def _dl_503(req, timeout=None, **kw):
        _dl_calls[0] += 1
        if _dl_calls[0] == 1:
            raise _ue.HTTPError(req.full_url, 503, "Unavailable", {}, _io.BytesIO(b""))
        class _R2:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"ok"
        return _R2()
    _ur.urlopen = _dl_503
    check(_http._download("https://example.com/file.csv") == b"ok" and _dl_calls[0] == 2, "_download retries once on 503")

    _dl_calls[0] = 0
    def _dl_urlerr(req, timeout=None, **kw):
        _dl_calls[0] += 1
        if _dl_calls[0] == 1:
            raise _ue.URLError("dropped")
        class _R3:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"ok2"
        return _R3()
    _ur.urlopen = _dl_urlerr
    check(_http._download("https://example.com/file.csv") == b"ok2" and _dl_calls[0] == 2, "_download retries once on URLError")

    for _code, _exp_t in ((429, True), (404, False)):
        _dl_calls[0] = 0
        c.reset_outbound()                # each status on its own (a 429 leaves the host cooling down)
        def _dl_no_retry(req, timeout=None, _c=_code, **kw):
            _dl_calls[0] += 1
            raise _ue.HTTPError(req.full_url, _c, "Err", {}, _io.BytesIO(b""))
        _ur.urlopen = _dl_no_retry
        try:
            _http._download("https://example.com/file.csv")
            check(False, f"_download {_code} should raise")
        except c.CompositeError as e:
            check(e.transient is _exp_t and _dl_calls[0] == 1, f"_download {_code} does not retry (transient={_exp_t})")

    _dl_calls[0] = 0
    def _dl_oecd_503(req, timeout=None, **kw):
        _dl_calls[0] += 1
        raise _ue.HTTPError(req.full_url, 503, "Unavailable", {}, _io.BytesIO(b""))
    _ur.urlopen = _dl_oecd_503
    try:
        _http._download("https://sdmx.oecd.org/public/rest/data/x")
        check(False, "_download OECD 503 should raise")
    except c.CompositeError as e:
        check(e.transient and _dl_calls[0] == 1, "_download never retries OECD URLs")
finally:
    _http._RETRY_DELAYS = _saved_delays
    _ur.urlopen = _saved_urlopen

# ---- cached_disk backoff tiers + memory cache non-shadowing + OECD / Too Many Requests 600s protection
_saved_time_fn = _cache_mod.time.time
_saved_cd = c.CACHE_DIR
_tmp_bo = tempfile.TemporaryDirectory()
_clk = [10000.0]
try:
    _cache_mod.time.time = lambda: _clk[0]
    c.CACHE_DIR = pathlib.Path(_tmp_bo.name)
    c.reset_runtime_caches()

    # 1. Transient with saved copy -> 30s backoff, NOT shadowed by 1800s memory cache
    (c.CACHE_DIR / "bo_trans_saved.json").write_text(json.dumps({"t": _clk[0] - 7200, "v": {"saved": 1}}), encoding="utf-8")
    _ts_calls = [0]
    def _bo_trans_saved():
        _ts_calls[0] += 1
        if _ts_calls[0] == 1:
            raise c.CompositeError("upstream reset", transient=True)
        return {"fresh": 2}
    check(c.cached_disk("bo_trans_saved", 3600, _bo_trans_saved) == {"saved": 1} and _ts_calls[0] == 1,
          "cached_disk serves saved copy on transient error")
    _clk[0] += 10.0
    check(c.cached_disk("bo_trans_saved", 3600, _bo_trans_saved) == {"saved": 1} and _ts_calls[0] == 1,
          "cached_disk honors 30s backoff without calling fn")
    _clk[0] += 25.0  # t + 35s > 30s backoff (must not be shadowed by 1800s mem TTL)
    check(c.cached_disk("bo_trans_saved", 3600, _bo_trans_saved) == {"fresh": 2} and _ts_calls[0] == 2
          and "bo_trans_saved" not in c._STALE_SINCE,
          "cached_disk retries after 30s transient backoff and clears stale marker")

    # 2. Transient without saved copy -> 10s backoff
    _t_nosave = [0]
    def _bo_trans_none():
        _t_nosave[0] += 1
        if _t_nosave[0] == 1:
            raise c.CompositeError("timeout", transient=True)
        return {"ok": True}
    for _step in (0, 5):
        if _step:
            _clk[0] += _step
        try:
            c.cached_disk("bo_trans_none", 3600, _bo_trans_none)
            check(False, "should fail while in 10s backoff")
        except c.CompositeError:
            pass
    check(_t_nosave[0] == 1, "transient error without saved file backs off for 10s")
    _clk[0] += 6.0  # +11s total
    check(c.cached_disk("bo_trans_none", 3600, _bo_trans_none) == {"ok": True} and _t_nosave[0] == 2,
          "transient error without saved file retries after 10s")

    # 3. Non-transient with saved copy -> 120s backoff
    (c.CACHE_DIR / "bo_perm_saved.json").write_text(json.dumps({"t": _clk[0] - 7200, "v": {"saved": 9}}), encoding="utf-8")
    _ps_calls = [0]
    def _bo_perm_saved():
        _ps_calls[0] += 1
        if _ps_calls[0] == 1:
            raise c.CompositeError("bad schema", transient=False)
        return {"fresh": 10}
    check(c.cached_disk("bo_perm_saved", 3600, _bo_perm_saved) == {"saved": 9}, "non-transient serves saved")
    _clk[0] += 60.0
    check(c.cached_disk("bo_perm_saved", 3600, _bo_perm_saved) == {"saved": 9} and _ps_calls[0] == 1,
          "non-transient with saved stays in 120s backoff at 60s")
    _clk[0] += 65.0  # +125s total
    check(c.cached_disk("bo_perm_saved", 3600, _bo_perm_saved) == {"fresh": 10} and _ps_calls[0] == 2,
          "non-transient with saved retries after 120s")

    # 4. Non-transient without saved -> 30s backoff
    _pn_calls = [0]
    def _bo_perm_none():
        _pn_calls[0] += 1
        if _pn_calls[0] == 1:
            raise c.CompositeError("404 not found", transient=False)
        return {"ok": 3}
    try:
        c.cached_disk("bo_perm_none", 3600, _bo_perm_none)
    except c.CompositeError:
        pass
    _clk[0] += 20.0
    try:
        c.cached_disk("bo_perm_none", 3600, _bo_perm_none)
    except c.CompositeError:
        pass
    check(_pn_calls[0] == 1, "non-transient without saved stays in 30s backoff at 20s")
    _clk[0] += 15.0  # +35s total
    check(c.cached_disk("bo_perm_none", 3600, _bo_perm_none) == {"ok": 3} and _pn_calls[0] == 2,
          "non-transient without saved retries after 30s")

    # 5. "Too Many Requests" and non-429 OECD failures keep 600s quota backoff
    _tmr_calls = [0]
    def _bo_tmr():
        _tmr_calls[0] += 1
        if _tmr_calls[0] == 1:
            raise c.CompositeError("HTTP Error: Too Many Requests", transient=True)
        return {"ok": "tmr"}
    try:
        c.cached_disk("bo_tmr", 3600, _bo_tmr)
    except c.CompositeError:
        pass
    _clk[0] += 300.0
    try:
        c.cached_disk("bo_tmr", 3600, _bo_tmr)
    except c.CompositeError:
        pass
    check(_tmr_calls[0] == 1, "'Too Many Requests' keeps 600s backoff at 300s")
    _clk[0] += 305.0
    check(c.cached_disk("bo_tmr", 3600, _bo_tmr) == {"ok": "tmr"} and _tmr_calls[0] == 2,
          "'Too Many Requests' retries after 600s")

    _oecd_bo_calls = [0]
    def _bo_oecd():
        _oecd_bo_calls[0] += 1
        if _oecd_bo_calls[0] == 1:
            raise c.CompositeError("upstream timeout", transient=True)
        return {"ok": "oecd"}
    try:
        c.cached_disk("oecd_rates_long", 3600, _bo_oecd)
    except c.CompositeError:
        pass
    _clk[0] += 300.0
    try:
        c.cached_disk("oecd_rates_long", 3600, _bo_oecd)
    except c.CompositeError:
        pass
    check(_oecd_bo_calls[0] == 1, "non-429 OECD error keeps 600s quota protection")
    _clk[0] += 305.0
    check(c.cached_disk("oecd_rates_long", 3600, _bo_oecd) == {"ok": "oecd"} and _oecd_bo_calls[0] == 2,
          "non-429 OECD error retries after 600s")
finally:
    _cache_mod.time.time = _saved_time_fn
    c.CACHE_DIR = _saved_cd
    c.reset_runtime_caches()
    _tmp_bo.cleanup()

c._cache[("t", 1)] = (0, 1)
c._backoff["b"] = (0, "err")
c._STALE_SINCE["s"] = 0
_cleared = c.reset_runtime_caches()
check(_cleared["cache"] >= 1 and _cleared["backoff"] >= 1 and _cleared["stale"] >= 1
      and not c._cache and not c._backoff and not c._STALE_SINCE, "reset_runtime_caches clears all maps")
done("test_trust")
