"""Correctness of the numbers the composite views compute (H1, M1, M2, M3, M4, M10, L3, L4, L6, L8, L13)."""

import datetime as dt
import threading

from _common import check, done

import composite as c


def series(start, values, step=1):
    d0 = dt.date.fromisoformat(start)
    return [((d0 + dt.timedelta(days=i * step)).isoformat(), v) for i, v in enumerate(values)]


# ---- H1: a currency named on a board moves against the dollar, whatever way Yahoo quotes the pair
check(c.fx_pair("JPY=X") == ("USD", "JPY") and c.fx_pair("EURUSD=X") == ("EUR", "USD") and c.fx_pair("^GSPC") is None, "Yahoo FX quote conventions")
check(c.fx_pair_label("JPY=X") == "USD/JPY", "pair label")
check(c.currency_view("Japanese Yen", "JPY=X") and not c.currency_view("USD/JPY", "JPY=X") and not c.currency_view("Euro", "EURUSD=X"), "which rows are inverted")
usdjpy = series("2026-01-01", [150.0] * 45 + [160.0])          # the dollar buys more yen: the yen fell
yen = c._quote_row("Japanese Yen", "JPY=X", usdjpy)
check(yen["chg_pct"] < 0 and abs(yen["chg_pct"] - (150 / 160 - 1)) < 1e-12, "yen row: 1D change is the yen's own move (negative)")
check(yen["chg"] is None and yen["price"] == 160.0 and yen["quote"] == "USD/JPY", "yen row keeps the market quote; no quote-unit change with a contradicting sign")
check(yen["_fmt"]["price"]["suffix"] == " USD/JPY", "yen row: Last is labelled with the pair it is quoted in")
check(yen["range52"] == 0.0 and yen["spark"][-1] < yen["spark"][0], "yen row: 52-week position and trend are the yen's own (weakest now, trending down)")
check("_rangeTip" in yen and "USD/JPY" in yen["_rangeTip"], "yen row: the range tooltip names both conventions")
check(yen["m1"] < 0, "yen row: 1M return negative when USD/JPY rose")
pair = c._quote_row("USD/JPY", "JPY=X", usdjpy)
check(pair["chg_pct"] > 0, "a row named as the pair shows the pair as quoted")
eur = c._quote_row("Euro", "EURUSD=X", series("2026-01-01", [1.10] * 45 + [1.11]))
check(eur["chg_pct"] > 0 and "quote" in eur, "EUR/USD up = euro stronger, unchanged")
for label, sym in [r for _, rows in c.BOARDS["fx"][1] for r in rows]:
    if c.fx_pair(sym) and c.fx_pair(sym)[0] == "USD":
        check("/" in label or c.currency_view(label, sym), f"fx board row {label} follows one convention")
for _, rows in c.BOARDS["risk_monitor"][1]:
    for label, sym in rows:
        if c.fx_pair(sym):
            check("/" in label or c.currency_view(label, sym), f"risk monitor row {label} follows one convention")

# ---- M1: yields move in basis points, never in percent of the yield
tnx = series("2026-01-01", [4.00] * 40 + [4.05])
row = c._quote_row("10-Year Note", "^TNX", tnx)
check(row["chg"] == 5.0 and row["chg_pct"] is None, "yield 1D change in bp")
check(row["_fmt"]["chg"]["suffix"] == " bp" and row["_fmt"]["m1"]["suffix"] == " bp", "table told to show bp")
check(abs(row["m1"] - 5.0) < 1e-9, "yield 1M change in bp")
check(c._diff_bp(tnx, "1D") == 5.0, "_diff_bp")

saved = c.history
try:
    c.history = lambda syms, start, interval="1d": {s: tnx for s in syms}
    bars = c.perf_bars(preset="us_rates", period="1D")
    check(bars["layout"]["xaxis"]["ticksuffix"] == " bp" and all(abs(x - 5.0) < 1e-9 for x in bars["data"][0]["x"]), "rates ranking in bp")
    mixed = c.perf_bars(preset="risk_monitor", period="1D")
    check("10-year yield" not in " ".join(mixed["data"][0]["y"]) and "annotations" in mixed["layout"], "a yield is not ranked among returns")
    perf = c.performance(symbols="^TNX,^FVX", period="1M")
    check(perf["layout"]["yaxis"]["ticksuffix"] == " bp", "performance of yields in bp")
    c.history = lambda syms, start, interval="1d": {s: (usdjpy if s == "JPY=X" else series("2026-01-01", [1.0] * 46)) for s in syms}
    fxbars = c.perf_bars(preset="fx", period="1D")
    check(dict(zip(fxbars["data"][0]["y"], fxbars["data"][0]["x"]))["Japanese Yen"] < 0, "ranking: the yen fell when USD/JPY rose (H1)")
    # review A1: an inverted USD/XXX row is in percent like every other bar (150/160 - 1 = -6.25%, not -0.0625)
    check(abs(dict(zip(fxbars["data"][0]["y"], fxbars["data"][0]["x"]))["Japanese Yen"] - (-6.25)) < 1e-9, "ranking: yen -6.25% (A1)")
finally:
    c.history = saved

# ---- M2: the unit follows the transform actually shown
check(c.transform_unit("%", "yoy", "yoy")[0] == "%", "CPI shown yoy: %")
u, title, bp = c.transform_unit("%", "yoy", "level", ["Index 1982-1984=100"])
check(u == "" and title == "Index 1982-1984=100" and not bp, "CPI switched to level: no % on the axis, the index unit instead")
check(c.transform_unit("%", None, "yoy") == (" bp", None, 100), "a rate shown year over year changes in bp (difference x 100)")
check(c.transform_unit(" $/bbl", None, "level")[0] == " $/bbl", "level keeps the set unit")
check(c.transform_unit(" $/bbl", None, "index")[0] == "", "indexed has no unit")
yo = c._yoy([["2025-01-01", 4.0], ["2026-01-01", 4.5]], diff_bp=True)
check(yo == [["2026-01-01", 50.0]], "yoy of a rate in bp")

# ---- M3: comparison curves within the gap, never the latest one, with their date
avail = ["2025-11-14", "2026-10-15", "2026-11-13"]
check(c.comparison_date(avail, dt.date(2025, 11, 15), "2026-11-13", 20) == "2025-11-14", "1Y ago found")
check(c.comparison_date(["2026-10-15", "2026-11-13"], dt.date(2025, 11, 15), "2026-11-13", 20) is None, "no 1Y curve: no 1Y column (not the 1M-old one)")
check(c.comparison_date(["2026-11-13"], dt.date(2026, 11, 10), "2026-11-13", 20) is None, "the latest curve is never its own comparison")
check(c.comparison_date(["2026-09-01"], dt.date(2026, 10, 15), "2026-11-01", 45) == "2026-09-01", "monthly data: 45-day window")

# ---- M4: seasonal weeks by ISO year
check(c.seasonal_bucket("2025-12-31", False) == (2026, 1), "Dec 31 2025 is week 1 of ISO 2026")
check(c.seasonal_bucket("2027-01-01", False) == (2026, 52), "Friday 1 Jan 2027 is ISO 2026-W53, folded into 52")
check(c.seasonal_bucket("2026-03-15", True) == (2026, 3), "monthly by calendar month")
daily = []
d = dt.date(2019, 1, 1)
while d <= dt.date(2026, 11, 13):
    daily.append([d.isoformat(), 100.0 + (50.0 if (d.month, d.day) >= (12, 29) else 0.0)])
    d += dt.timedelta(days=1)
out = c._seasonal("x", daily, "")
wk1 = next(r for r in out["results"] if r["period"] == "Week 1")
check(wk1["2025"] == 100.0 and wk1["max_5y"] == 100.0, "week 1 holds early-January values, not 29-31 December")

# ---- M10 + missing tests: country_monitor (interbank vs sovereign slope), _rolling, CALCS, chokepoint_monitor
_saved_cm = c._oecd_all, c._imf_cpi, c._policy_series, c.global_yields
try:
    c._cache.clear()
    _gdp = {"united_states": [["2025-04-01", 100.0], ["2025-07-01", 101.0], ["2025-10-01", 102.0], ["2026-01-01", 103.0], ["2026-04-01", 105.0]]}
    _une = {"united_states": [["2026-10-01", 0.041]]}
    c._oecd_all = lambda path, start: _gdp if "gdp" in path else _une
    c._imf_cpi = lambda keys, start: {"united_states": [["2026-10-01", 2.5]]}
    c._policy_series = lambda: {"US": [["2026-11-01", 4.0]]}
    c.global_yields = lambda: {"results": [
        {"key": "united_states", "m3": 4.20, "y10": 4.65, "as_of": "2026-11-20", "freq": "daily"},
        {"key": "france", "m3": 2.50, "y10": 3.10, "as_of": "2026-10", "freq": "monthly", "_m3_kind": "interbank"},
    ]}
    cm = {r["key"]: r for r in c.country_monitor()["results"]}
    us_row, fr_row = cm["united_states"], cm["france"]
    check(abs(us_row["gdp_yoy"] - 0.05) < 1e-9 and us_row["gdp_q"] == "2026 Q2"
          and abs(us_row["cpi_yoy"] - 0.025) < 1e-9 and abs(us_row["unemp"] - 0.041) < 1e-9
          and us_row["policy"] == 4.0 and us_row["slope"] == 45, "country_monitor computes GDP/CPI/unemp/policy/slope")
    check(fr_row["rates_src"] == "monthly (3M interbank)" and "slope" not in fr_row, "M10: monthly OECD 3M interbank rate gives no sovereign slope")
finally:
    c._oecd_all, c._imf_cpi, c._policy_series, c.global_yields = _saved_cm
    c._cache.clear()

_roll_in = [["2026-01-01", 1.0], ["2026-01-08", 2.0], ["2026-01-15", 3.0], ["2026-01-22", 4.0], ["2026-01-29", 5.0]]
_roll_out = c._rolling(_roll_in, 28)
check(_roll_out[0] == ["2026-01-01", 1.0] and abs(_roll_out[-1][1] - 3.5) < 1e-9 and _roll_out[-1][0] == "2026-01-29",
      "_rolling 28d mean of 1..5 weekly points = 3.5")

check(abs(c.CALCS["CRACK321"][1](2.50, 2.60, 80.0) - 26.40) < 1e-9, "CALCS CRACK321")
check(abs(c.CALCS["DIESEL_CRACK"][1](2.60, 85.0) - 24.20) < 1e-9, "CALCS DIESEL_CRACK")
check(abs(c.CALCS["GASOLINE_CRACK"][1](2.50, 85.0) - 20.00) < 1e-9, "CALCS GASOLINE_CRACK")
check(abs(c.CALCS["BRENT_WTI"][1](85.0, 80.0) - 5.00) < 1e-9, "CALCS BRENT_WTI")
check(abs(c.CALCS["TTF_USD"][1](30.0, 1.10) - (30.0 * 1.10 / 3.412)) < 1e-6, "CALCS TTF_USD")

_saved_cp = c._chokepoints
try:
    _cp_pts = (
        [[(dt.date(2023, 1, 1) + dt.timedelta(days=i)).isoformat(), 50.0] for i in range(304)]
        + [[(dt.date(2025, 10, 2) + dt.timedelta(days=i)).isoformat(), 40.0] for i in range(31)]
        + [[(dt.date(2026, 10, 3) + dt.timedelta(days=i)).isoformat(), 30.0] for i in range(30)]
    )
    c._chokepoints = lambda: {"Suez Canal:vessels_total": _cp_pts}
    _cp_res = c.chokepoint_monitor()["results"]
    _suez = next(r for r in _cp_res if r["chokepoint"] == "Suez Canal")
    check(_suez["total"] == 30.0 and abs(_suez["total_1y"] - (-0.25)) < 1e-9
          and abs(_suez["total_pre"] - (-0.40)) < 1e-9 and _suez["as_of"] == "2026-11-01",
          "chokepoint_monitor 30d vs 1y and Jan-Oct 2023")
finally:
    c._chokepoints = _saved_cp

# ---- L4: two long id lists never share a cache file
a, b = "A" * 130 + "X", "A" * 130 + "Y"
check(c.disk_name("fred_", a) != c.disk_name("fred_", b), "disk names hashed")

# ---- L13: month-ago by date, not by position
ser = [["2025-09-01", 4.0], ["2025-11-01", 4.2], ["2026-10-01", 4.9], ["2026-11-01", 5.0]]
check(c._month_ago(ser, 12) == 4.2 and c._month_ago(ser, 1) == 4.9, "month ago by date")
check(c._month_ago([["2025-01-01", 1.0], ["2026-11-01", 2.0]], 12) is None, "a gap gives no change instead of a wrong one")

# ---- L6: eviction never drops a lock in use
c._cache.clear(); c._locks.clear()
held = threading.Lock(); held.acquire()
c._cache["busy"] = (0.0, 1); c._locks["busy"] = held
for i in range(420):
    c._cache[("k", i)] = (1.0 + i, i); c._locks[("k", i)] = threading.Lock()
c.cached(("new",), 60, lambda: 1)
check("busy" in c._locks, "held lock kept on eviction")
held.release()

# ---- L8: weekends hidden only when nothing trades on them
def fake_ohlc(symbol, start, interval):
    days = [dt.date(2026, 10, 1) + dt.timedelta(days=i) for i in range(60)]
    if symbol == "^TASI.SR":                                        # trades Sunday to Thursday
        days = [x for x in days if x.weekday() not in (4, 5)]
    else:
        days = [x for x in days if x.weekday() < 5]
    return [[x.isoformat(), 1, 1, 1, 1.0 + i / 100, 10] for i, x in enumerate(days)]


saved_ohlc = c._ohlc
try:
    c._ohlc = fake_ohlc
    check("rangebreaks" in c.chart("^GSPC", period="1M")["layout"]["xaxis"], "weekday market: weekends hidden")
    check("rangebreaks" not in c.chart("^TASI.SR", period="1M")["layout"]["xaxis"], "Sunday trading: weekends kept")
    pct = c.chart("^TNX", compare="^FVX", view="pct", period="1M")
    check(pct["layout"]["yaxis"]["ticksuffix"] == " bp", "compare chart of yields in bp")
finally:
    c._ohlc = saved_ohlc

# ---- L3: a multi-symbol answer missing one symbol refetches that one
calls = []


def fake_api(base, path, **kw):
    calls.append(kw["symbol"])
    if "," in kw["symbol"]:
        return [{"symbol": "AAA", "date": "2026-11-13", "close": 1.0}]
    return [{"symbol": kw["symbol"], "date": "2026-11-13", "close": 2.0}]


saved_api = c.api
try:
    c.api = fake_api
    got = c._fetch_history(["AAA", "BBB"], "2026-11-01", "1d")
    check(got["BBB"] == [("2026-11-13", 2.0)] and "BBB" in calls, "missing symbol refetched alone")
finally:
    c.api = saved_api

done("test_numbers")
