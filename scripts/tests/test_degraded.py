"""Every substitution, stale copy and missing part is flagged in the answer, request by request (P2-03): for each site,
the note is there under the injected failure and absent on success; notes survive cache hits and the disk."""

import datetime as dt
import json
import time
from pathlib import Path

from _common import check, done

import composite as C
import cryptovol
import fred_public
import news
import optionsx
import policy_path as pp
from composite.core import contract

TODAY = dt.date(2026, 11, 20)
saved_api = C.api


def run(fn):
    """fn() with the degraded notes it made: (value, missing, fallbacks, stale ages)."""
    got = C._collect(fn)
    if isinstance(got, C.Partial):
        return got.value, got.missing, got.fallbacks, got.stale
    return got, [], [], []


def down(*_a, **_k):
    raise C.CompositeError("The data service answered HTTP 502", kind="upstream")


# ---- core: notes reach the envelope, survive a memory hit and the disk
def view_with_substitute(**_):
    def fetch():
        C.note_fallback("Source B", "source A did not answer")
        return {"results": [{"date": "2026-11-19", "v": 1.0}], "sources": [C.src("Test", "b", "https://example.test/")]}
    return C.cache(("t_sub",), 3600, fetch, disk="t_sub")


C.ROUTES["t_sub"] = view_with_substitute
C.reset_runtime_caches()
(C.CACHE_DIR / "t_sub.json").unlink(missing_ok=True)
first = C.dispatch("t_sub", {})
check(first["status"] == "fallback" and "Source B used instead: source A did not answer" in first["notes"], f"fallback in the envelope: {first.get('notes')}")
check(C.dispatch("t_sub", {})["status"] == "fallback", "a memory hit still says so")
saved_file = json.loads((C.CACHE_DIR / "t_sub.json").read_text(encoding="utf-8"))
check(saved_file.get("fb") == ["Source B used instead: source A did not answer"], "a substitute's complete answer is saved with its note")
C.reset_runtime_caches()
check(C.dispatch("t_sub", {})["status"] == "fallback", "read back from disk after a restart: still says so")
C.ROUTES.pop("t_sub")
(C.CACHE_DIR / "t_sub.json").unlink(missing_ok=True)

# an outer cache built from an inner saved copy (source down) is stale on every hit, held short, never saved
inner_file = C.CACHE_DIR / "t_inner.json"
inner_file.write_text(json.dumps({"t": time.time() - 30 * 3600, "v": [1, 2]}), encoding="utf-8")
C.reset_runtime_caches()
outer = lambda: C.cache(("t_outer",), 3600, lambda: {"v": C.cache(("t_inner",), 3600, lambda: down(), disk="t_inner")}, disk="t_outer")  # noqa: E731
v, miss, fb, ages = run(outer)
check(v == {"v": [1, 2]} and len(ages) == 1 and abs(ages[0] - (time.time() - 30 * 3600)) < 5, "stale inner copy flagged on the outer answer")
check(not (C.CACHE_DIR / "t_outer.json").exists(), "an answer built from a stale copy is not saved as fresh")
v2, _, _, ages2 = run(outer)
check(ages2 == ages, "a memory hit of the outer answer is still flagged stale")
inner_file.unlink(missing_ok=True)
C.reset_runtime_caches()

# ---- 1. FRED: the switch to the public download says why; a failed series is a missing part
saved_fred, saved_csv = C._fred, C._fred_csv
try:
    C._fred = lambda ids, start: ({i: [("2026-11-19", 4.0)] for i in ids}, {})
    _, miss, fb, _ = run(lambda: C._fred_any(["DGS10", "DGS2"], "2026-01-01"))
    check(not miss and not fb, "FRED through the data service: no note")
    C._fred = lambda ids, start: (_ for _ in ()).throw(C.CompositeError("Missing credential: fred_api_key"))
    C._fred_csv = lambda sid, start: [("2026-11-19", 4.0)] if sid != "DGS2" else down()
    got, miss, fb, _ = run(lambda: C._fred_any(["DGS10", "DGS2"], "2026-01-01"))
    check(fb == ["FRED's public download used instead: no FRED key is set"], f"keyless switch flagged: {fb}")
    check(miss == ["FRED DGS2"] and got[0]["DGS2"] == [], f"the failed series is named: {miss}")
    C._fred = down
    C._fred_csv = lambda sid, start: [("2026-11-19", 4.0)]
    _, miss, fb, _ = run(lambda: C._fred_any(["DGS10"], "2026-01-01"))
    check(fb == ["FRED's public download used instead: the data service's FRED route did not answer"] and not miss, f"outage switch flagged: {fb}")
finally:
    C._fred, C._fred_csv = saved_fred, saved_csv

# ---- 2. Yahoo metadata: an expired copy served because quotes failed is stale; a fresh answer is not
meta_file = C.CACHE_DIR / "yahoo_meta.json"
old_t = time.time() - C.META_TTL - 3600
meta_file.write_text(json.dumps({"TSTM": {"t": old_t, "v": {"name": "Test Metals", "currency": "CAD", "type": "equity"}}}), encoding="utf-8")
try:
    C.reset_runtime_caches()
    C.api = down
    got, miss, _, ages = run(lambda: C._meta(["TSTM", "TSTN"]))
    check(got["TSTM"]["name"] == "Test Metals" and len(ages) == 1 and abs(ages[0] - old_t) < 1, "expired metadata served flagged stale")
    check(miss == ["Yahoo Finance quotes"], f"a symbol with no saved copy is a missing part: {miss}")
    C.reset_runtime_caches()
    C.api = lambda base, path, **kw: [{"symbol": "TSTM", "name": "Test Metals", "currency": "CAD", "asset_type": "EQUITY"},
                                      {"symbol": "TSTN", "name": "Test Nickel", "currency": "CAD", "asset_type": "EQUITY"}]
    got, miss, _, ages = run(lambda: C._meta(["TSTM", "TSTN"]))
    check(got["TSTN"]["name"] == "Test Nickel" and not ages and not miss, "fresh metadata: no note")
finally:
    C.api = saved_api
    meta_file.unlink(missing_ok=True)

# ---- 4. Curves: the U.S. data-service curve -> FRED switch is flagged; a working data service is not
saved_bc, saved_fu, saved_near = C._backend_curve, C._src_fred_us, C._nearest
try:
    C._nearest = lambda src, dates, max_gap=20: {"2026-11-19": [(1.0, 4.1), (10.0, 4.3)]}
    C._backend_curve = lambda src, dates: {"2026-11-19": [(1.0, 4.0), (10.0, 4.2)]}
    C.reset_runtime_caches()
    _, miss, fb, _ = run(lambda: C.curves(["united_states"], [TODAY]))
    check(not fb and not miss, "U.S. curve from the data service: no note")
    C._backend_curve = down
    C._src_fred_us = lambda: {}
    C.reset_runtime_caches()
    got, miss, fb, _ = run(lambda: C.curves(["united_states"], [TODAY]))
    check(len(fb) == 1 and fb[0].startswith("FRED's Treasury yields used instead") and "upstream" in fb[0] and got["__src__"]["united_states"] == "fred",
          f"FRED switch flagged with its reason: {fb}")
finally:
    C._backend_curve, C._src_fred_us, C._nearest = saved_bc, saved_fu, saved_near
    C.reset_runtime_caches()

# ---- 5. News: a failed feed's old items are flagged stale; a failed feed with none is a missing part
URL = "https://feeds.example.test/markets.xml"
saved_get, saved_parse = news._get, news.parse_feed
try:
    news._FEED_CACHE.clear(), news._FEED_FAIL.clear(), news._FEED_ERR.clear()
    news._get = lambda url, gnews=False: b"<rss/>"
    news.parse_feed = lambda body: [{"title": "Rates steady"}]
    items, miss, _, ages = run(lambda: news.fetch(URL, ttl=300))
    check(items == [{"title": "Rates steady"}] and not miss and not ages, "a feed that answers: no note")
    news._FEED_CACHE[URL] = (time.monotonic() - 400, items)            # older than its 300 s
    news._get = down
    items2, miss, _, ages = run(lambda: news.fetch(URL, ttl=300))
    check(items2 == items and len(ages) == 1 and abs(ages[0] - (time.time() - 400)) < 5 and not miss, "failed feed: old items flagged stale")
    _, _, _, ages = run(lambda: news.fetch(URL, ttl=300))
    check(len(ages) == 1, "during the 10-minute pause the old items stay flagged")
    other = "https://other.example.test/feed"
    empty, miss, _, _ = run(lambda: news.fetch(other, ttl=300))
    check(empty == [] and miss == ["news feed other.example.test"], f"failed feed with nothing saved: a missing part ({miss})")
    seen = []
    tok = C._PARTIAL_NOTES.set(seen)
    try:
        news._submit(news.fetch, "https://third.example.test/x", 300).result(timeout=10)
    finally:
        C._PARTIAL_NOTES.reset(tok)
    check(seen == ["news feed third.example.test"], "the news pool carries the request's notes into its workers")
finally:
    news._get, news.parse_feed = saved_get, saved_parse
    news._FEED_CACHE.clear(), news._FEED_FAIL.clear(), news._FEED_ERR.clear()

# ---- 6. Crypto volatility: an empty leg is a missing part (held a minute), not a complete answer for 15 minutes
DV = [{"date": (TODAY - dt.timedelta(days=i)).isoformat(), "close": 50.0 + i % 3} for i in range(1, 400)]
PX = [{"date": (TODAY - dt.timedelta(days=i)).isoformat(), "close": 60000.0 * (1 + 0.01 * ((i * 7) % 5 - 2))} for i in range(1, 450)]
try:
    C.reset_runtime_caches()
    C.api = lambda base, path, **kw: DV if path.startswith("deribit") else PX
    _, miss, _, _ = run(lambda: cryptovol.chart("BTC", "1Y", today=TODAY))
    check(not miss, "both legs answer: no note")
    C.reset_runtime_caches()
    C.api = lambda base, path, **kw: [] if path.startswith("deribit") else PX
    out, miss, _, _ = run(lambda: cryptovol.chart("BTC", "1Y", today=TODAY))
    check(miss == ["Deribit DVOL"] and len(out["data"]) >= 1, f"empty DVOL leg named, realised still drawn: {miss}")
    hit = C._cache[("cryptovol", "BTC", C.minus_months(TODAY, 12).isoformat())]
    check(isinstance(hit[1], C.Partial) and hit[1].short, "held as a partial answer, not for 15 minutes")
finally:
    C.api = saved_api
    C.reset_runtime_caches()

# ---- 7. Policy path: a missing fixing is named; a defect is not swallowed
saved_obs = fred_public.observations
try:
    C.reset_runtime_caches()
    fred_public.observations = lambda sid, start=None: [("2026-11-18", 4.33)]
    last, miss, _, _ = run(lambda: pp._fred_last("SOFR"))
    check(last == ("2026-11-18", 4.33) and not miss, "fixing answers: no note")
    C.reset_runtime_caches()
    fred_public.observations = lambda sid, start=None: down()
    last, miss, _, _ = run(lambda: pp._fred_last("SOFR"))
    check(last is None and miss == ["FRED SOFR (latest overnight fixing)"], f"missing fixing named: {miss}")
    C.reset_runtime_caches()
    fred_public.observations = lambda sid, start=None: [("2026-11-18",)]          # a defect (bad shape), not a source failure
    try:
        pp._fred_last("SOFR")
        defect = None
    except ValueError as e:
        defect = e
    check(defect is not None, "a defect in the fixing path propagates (500 with a ref), never a silent blank")
finally:
    fred_public.observations = saved_obs
    C.reset_runtime_caches()

# ---- 8. Options: the ATM implied-vol term on the last close is flagged; on the chain's own spot it is not
CLOSES = [((TODAY - dt.timedelta(days=i)).isoformat(), 100.0 * (1 + 0.01 * ((i * 3) % 7 - 3))) for i in range(560, 0, -1)]
EXP = (TODAY + dt.timedelta(days=30)).isoformat()
CHAIN = [{"strike": 100.0, "option_type": t, "expiration": EXP, "implied_volatility": 0.2, "open_interest": 10, "underlying_price": 101.0}
         for t in ("call", "put")]
saved_hist, saved_chain = C.history, optionsx._chain
try:
    C.history = lambda syms, start, interval="1d": C.PriceHistory({s: list(CLOSES) for s in syms})
    optionsx._chain = lambda sym: CHAIN
    _, _, fb, _ = run(lambda: optionsx.vol_cone("TSTO", today=TODAY))
    check(not fb, "chain with its own spot: no note")
    optionsx._chain = lambda sym: [{k: v for k, v in r.items() if k != "underlying_price"} for r in CHAIN]
    _, _, fb, _ = run(lambda: optionsx.vol_cone("TSTO", today=TODAY))
    check(len(fb) == 1 and fb[0].startswith("the last daily close used instead"), f"no chain spot: last close flagged ({fb})")
finally:
    C.history, optionsx._chain = saved_hist, saved_chain

# ---- the browser draws the notes and the status for every card type (one footer renderer, widgets.js T.renderTrust)
src = (Path(contract.__file__).resolve().parents[2] / "static" / "widgets.js").read_text(encoding="utf-8")
check('lj.status === "partial" || lj.status === "fallback"' in src and "Array.isArray(lj.notes)" in src, "renderTrust draws notes and partial/fallback status")
done("test_degraded")
