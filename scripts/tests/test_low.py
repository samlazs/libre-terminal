"""Low audit findings A13-A17. Expected values worked out by hand."""

import datetime as dt

from _common import check, done

import composite as c

today = dt.date.today()


def ago(n):
    return (today - dt.timedelta(days=n)).isoformat()


# ---- A13: the 1D performance chart compares the last two bars, whatever today is (a weekend gave +0.0%)
saved = c.history
try:
    c._cache.clear()
    c.history = lambda syms, start, interval="1d": c.PriceHistory({s: [(ago(3), 100.0), (ago(2), 100.0), (ago(1), 105.0)] for s in syms})
    t = c.performance(symbols="SPY", period="1D")["data"][0]
    check(t["name"] == "SPY  +5.0%" and t["x"] == [ago(2), ago(1)] and t["y"] == [0.0, 5.0], "1D: 105 against the previous bar 100 = +5.0%, two points")
    y0 = dt.date.fromisoformat(ago(1)).year - 1
    c.history = lambda syms, start, interval="1d": c.PriceHistory({s: [(f"{y0}-12-30", 90.0), (f"{y0}-12-31", 100.0), (ago(1), 110.0)] for s in syms})
    t = c.performance(symbols="SPY", period="YTD")["data"][0]
    check(t["x"][0] == f"{y0}-12-31" and t["y"][0] == 0.0 and t["name"] == "SPY  +10.0%", "YTD: from the last close of the previous year, drawn from 0")
finally:
    c.history = saved
    c._cache.clear()

# ---- A14: chart % change starts from the last close at or before the period start (a weekend start lost the first move)
start = c.horizon_date(today, "1M")
rows = [[(start - dt.timedelta(days=1)).isoformat(), None, None, None, 100.0, None],
        [(start + dt.timedelta(days=2)).isoformat(), None, None, None, 110.0, None],
        [(start + dt.timedelta(days=3)).isoformat(), None, None, None, 115.0, None],
        [ago(1), None, None, None, 121.0, None]]
saved = c._ohlc
try:
    c._ohlc = lambda s, start, interval: rows
    t = c.chart(symbol="SPY", view="pct", period="1M", studies="")["data"][0]
    check(t["name"] == "SPY  +21.0%", "121 against the close before the start (100) = +21.0%, not +10.0% from the first bar after it")
    check(t["x"][0] == rows[0][0] and t["y"][0] == 0.0 and len(t["x"]) == 4, "the line starts at 0 on that close")
    on = [[start.isoformat(), None, None, None, 100.0, None], [ago(1), None, None, None, 110.0, None]]
    c._ohlc = lambda s, st, interval: [[(start - dt.timedelta(days=3)).isoformat(), None, None, None, 90.0, None]] + on
    t = c.chart(symbol="SPY", view="pct", period="1M", studies="")["data"][0]
    check(t["name"] == "SPY  +10.0%" and t["x"][0] == start.isoformat(), "a bar on the start date itself is the base")
finally:
    c._ohlc = saved

# ---- A15: curve kind in the legend and the source column; a chart mixing par and zero-coupon says so
def fake_curves(countries, dates=None, us_source="auto"):
    out = {"__src__": {}}
    for k in countries:
        out[k] = {"2026-11-13": [(1.0, 3.5), (10.0, 4.2), (30.0, 4.6)]}
        out["__src__"][k] = c.COUNTRIES[k][1]
    return out


saved = c.curves
try:
    c.curves = fake_curves
    both = c.yield_curves(country="united_states,germany")
    names = [t["name"] for t in both["data"]]
    check(names == ["United States (2026-11-13, par)", "Germany (2026-11-13, zero-coupon)"], "legend names the curve kind: " + "; ".join(names))
    check("not directly comparable" in " ".join(a["text"] for a in both["layout"].get("annotations", [])), "par next to zero-coupon: the chart says so")
    us = c.yield_curves(country="united_states")
    check(not us["layout"].get("annotations"), "one kind only: no note")
    check(c.yield_curve_table(country="germany", compare="")["results"][0]["source"] == "Bundesbank (zero-coupon)", "table source names the kind")
    check(set(c.CURVE_KIND) == set(c.CURVE_SOURCES), "every curve source has a kind")
    # review F3: the table follows every selected country (it stayed on the first), each row naming its country
    t2 = c.yield_curve_table(country="germany,united_states", compare="")
    one = c.yield_curve_table(country="germany", compare="")["results"]
    us1 = c.yield_curve_table(country="united_states", compare="")["results"]
    check([r["country"] for r in t2["results"]] == ["Germany"] * len(one) + ["United States"] * len(us1)
          and [r["yield"] for r in t2["results"]] == [r["yield"] for r in one + us1] and len(t2["sources"]) == 2,
          "two countries: Germany's rows then the U.S.'s, with both sources")
    check("country" not in one[0], "one country: no country column")
    # review F3: monthly OECD curves count "1Y ago" back from their own latest month; daily curves from today
    _d = dt.date(2026, 11, 18)
    check(c.compare_target("france", "2026-10-01", _d, "1Y") == dt.date(2025, 10, 1) and c.compare_target("france", "2026-10-01", _d, "1M") == dt.date(2026, 9, 1),
          "OECD monthly: Oct 2026 -> Oct 2025 / Sep 2026")
    check(c.compare_target("united_states", "2026-11-17", _d, "1Y") == dt.date(2025, 11, 18) and c.compare_target("france", None, _d, "1Y") == dt.date(2025, 11, 18),
          "daily curve (or no latest): from today")
    # review G2: Global Yields filtered to the G10 (Overview) or a list; unfiltered by default
    _sc = c.cached
    try:
        c.cached = lambda key, ttl, fn: {"results": [{"key": k} for k in ("united_states", "brazil", "japan", "india", "euro_area")], "sources": []}
        check([r["key"] for r in c.global_yields(countries="g10")["results"]] == ["united_states", "japan", "euro_area"]
              and [r["key"] for r in c.global_yields(countries="brazil,india")["results"]] == ["brazil", "india"]
              and len(c.global_yields()["results"]) == 5, "global yields: g10, a list, or all")
    finally:
        c.cached = _sc
finally:
    c.curves = saved

# ---- A16: year over year of a ratio (% of GDP) in percentage points, of a spread in bp: differences, not percents
check(c.transform_unit("% of GDP", None, "yoy") == (" pp", None, 1), "% of GDP: yoy in pp")
check(c.transform_unit(" bp", None, "yoy") == (" bp", None, 1), "bp: yoy in bp")
check(c.transform_unit("%", None, "yoy") == (" bp", None, 100), "a rate in %: yoy in bp")
check(c.transform_unit("%", "yoy", "yoy") == ("%", None, None), "CPI (default yoy): percent change")
pp = c._yoy([["2024-01-01", 118.0], ["2025-01-01", 121.2]], diff=1)
check(len(pp) == 1 and abs(pp[0][1] - 3.2) < 1e-9, "118.0 -> 121.2 = +3.2 pp (not +2.7%)")
saved = c._series
try:
    c._series = lambda ids, start: ({i: [["2024-01-01", 118.0], ["2025-01-01", 121.2]] for i in ids}, {})
    out = c.macro_chart(preset="federal_debt", transform="yoy", since="2025")
    check(out["layout"]["yaxis"]["ticksuffix"] == " pp" and out["data"][0]["name"].endswith("3.20 pp"), "federal debt yoy: +3.20 pp on a pp axis")
finally:
    c._series = saved

# ---- A17: a board row whose last bar is days older than the rest shows its last trade date and no 1D change
saved = c.history
try:
    c.history = lambda syms, start, interval="1d": c.PriceHistory({"AAA": [("2026-11-12", 10.0), ("2026-11-13", 11.0)],
                                                                  "BBB": [("2026-10-17", 20.0), ("2026-10-20", 21.0)]})
    rows = {r["symbol"]: r for r in c.board(symbols="AAA,BBB")["results"]}
    check(rows["BBB"]["stale"] == "2026-10-20" and rows["BBB"]["chg"] is None and rows["BBB"]["chg_pct"] is None, "BBB (24 days behind): last trade date, no 1D change")
    check("stale" not in rows["AAA"] and abs(rows["AAA"]["chg_pct"] - 0.1) < 1e-12, "AAA current: 11 vs 10 = +10%")
    rows = {r["symbol"]: r for r in c.board(symbols="AAA")["results"]}
    check("stale" not in rows["AAA"], "a board of one is never stale against itself")
finally:
    c.history = saved

# ---- P1: drawdown and recession shading (chart view "dd", shade "rec"), worked out by hand
check([round(x, 9) for x in c.drawdown([100, 120, 90, 130, 117])] == [0, 0, -25.0, 0, -10.0], "drawdown: % below the running high (90 vs 120 = -25%, 117 vs 130 = -10%)")
saved = c._recessions
try:
    c._recessions = lambda: [("2020-02-01", "2020-04-30"), ("2007-12-01", "2009-06-30")]
    lay = {}
    used = c.add_recession_shading(lay, "2008-06-01", "2026-11-17")
    xs = [(s["x0"], s["x1"]) for s in lay["shapes"]]
    check(sorted(xs) == [("2008-06-01", "2009-06-30"), ("2020-02-01", "2020-04-30")] and used and used[0]["name"] == "FRED", "recessions clipped to the chart's dates, source named")
    c._recessions = lambda: (_ for _ in ()).throw(c.CompositeError("FRED down"))
    lay = {}
    check(c.add_recession_shading(lay, "2020-01-01", "2026-01-01") == [] and "unavailable" in lay["annotations"][0]["text"], "no recession data: said on the chart")
finally:
    c._recessions = saved
saved = c._ohlc
try:
    today = dt.date.today()
    bars = [[(today - dt.timedelta(days=n)).isoformat(), None, None, None, v, None] for n, v in ((300, 100.0), (200, 120.0), (100, 90.0), (1, 108.0))]
    c._ohlc = lambda s, st, interval: bars
    out = c.chart(symbol="SPY", compare="^TNX", view="dd", period="1Y", studies="")
    t = out["data"][0]
    check(t["y"] == [0.0, 0.0, -25.0, -10.0] and "max -25.0%" in t["name"] and len(out["data"]) == 1, "chart drawdown: SPY -10% now, -25% at worst; the yield (^TNX) is left out")
finally:
    c._ohlc = saved

# ---- audit B10: on 1 January the last bar is still 31 Dec; its YTD needs the close of the year before
seen = []
saved = c.history, c._today
try:
    c._today = lambda: dt.date(2027, 1, 1)

    def fx_hist(syms, start, interval="1d"):
        seen.append(start)
        return c.PriceHistory({s: [("2025-12-31", 1.0), ("2026-06-30", 1.05), ("2026-12-31", 1.10)] for s in syms})
    c.history = fx_hist
    eur = next(r for r in c.fx_matrix()["results"] if r["ccy"] == "EUR")
    check(seen[0] <= dt.date(2025, 12, 31) and abs(eur["ytd"] - 0.10) < 1e-12, "1 January: YTD of the year just ended, 1.10 vs 1.00 = +10%")
    # review A6: about four significant figures per cell (JPY 0.0064 USD, USD 155.3 JPY)
    c.history = lambda syms, start, interval="1d": c.PriceHistory({s: [("2026-12-30", 1.0), ("2026-12-31", 156.25 if s == "JPY=X" else 1.10)] for s in syms})
    m = {r["ccy"]: r for r in c.fx_matrix()["results"]}
    check(m["JPY"]["_fmt"]["USD"] == {"decimalPlaces": 6} and m["USD"]["_fmt"]["JPY"] == {"decimalPlaces": 2} and m["EUR"]["_fmt"]["USD"] == {"decimalPlaces": 4},
          "cross rates: 0.0064 -> 6 decimals, 156.25 -> 2, 1.10 -> 4")
finally:
    c.history, c._today = saved

# review C4: partial Yahoo answers (most symbols empty) are kept 30 s, full ones 5 minutes; boards say when mostly empty
import tempfile as _tempfile
import time as _time
from pathlib import Path as _Path
_calls = []
_sf = c._fetch_history
_scd = c.CACHE_DIR
_tmp_cd = _tempfile.TemporaryDirectory()
try:
    c.CACHE_DIR = _Path(_tmp_cd.name)
    def _fh(chunk, start, interval):
        _calls.append(tuple(chunk))
        return c.PriceHistory({s: ([(ago(1), 1.0)] if s == "AAA" else []) for s in chunk})
    c._fetch_history = _fh
    c._cache.clear()
    h1 = c.history(["AAA", "BBB", "CCC"], dt.date(2026, 1, 1))
    h2 = c.history(["AAA", "BBB", "CCC"], dt.date(2026, 1, 1))
    check(h1.partial and len(_calls) == 1 and h2 is h1, "partial answer: flagged, served from cache within 30 s")
    _k = ("hist", ("AAA", "BBB", "CCC"), "2026-01-01", "1d")
    c._cache[_k] = (_time.time() - 31, c._cache[_k][1])
    c.history(["AAA", "BBB", "CCC"], dt.date(2026, 1, 1))
    check(len(_calls) == 2, "partial answer older than 30 s: fetched again")
    _calls.clear(); c._cache.clear()
    for _f in c.CACHE_DIR.glob("*.json"):
        _f.unlink()
    c.history(["AAA"], dt.date(2026, 1, 1))
    _k1 = ("hist", ("AAA",), "2026-01-01", "1d")
    c._cache[_k1] = (_time.time() - 200, c._cache[_k1][1])
    c.history(["AAA"], dt.date(2026, 1, 1))
    check(len(_calls) == 1, "complete answer: kept the full 5 minutes")
finally:
    c._fetch_history = _sf
    c.CACHE_DIR = _scd
    c._cache.clear()
    _tmp_cd.cleanup()
_rows = [{"_section": "Asia"}] + [{"price": None} for _ in range(20)] + [{"price": 1.0}]
check(c.null_warning(_rows).startswith("20 of 21 rows have no price") and c.null_warning([{"price": 1}, {"price": None}]) is None,
      "board warning above half empty only (sections not counted)")
done("test_low")
