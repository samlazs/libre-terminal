"""Monthly OECD rates (audit A3): a series that stopped years ago is never shown as current, the slope never mixes
months, and Global Yields ages monthly rows by their real date. All expected values below are worked out by hand."""

import datetime as dt

from _common import check, done

import composite as c


def ser(last, *vals):
    """Monthly [[iso, value], ...] ending on the first of `last` (YYYY-MM), oldest first."""
    y, m = map(int, last.split("-"))
    out = []
    for i, v in enumerate(reversed(vals)):
        d = c.minus_months(dt.date(y, m, 1), i)
        out.append([d.isoformat(), v])
    return list(reversed(out))


# country A: all three legs end 2026-10 (the newest month); 10Y: 4.0 in 2025-10 ... 4.3 in 2026-09, 4.5 in 2026-10
long_a = ser("2026-10", *([4.0] + [4.1] * 10 + [4.3, 4.5]))
imm = {"A": ser("2026-10", 2.5, 2.75), "B": ser("2026-10", 16.0, 16.5), "D": ser("2026-10", 4.0), "G": ser("2026-07", 1.25)}
short = {"A": ser("2026-10", 2.9, 3.0), "B": ser("2022-02", 15.0, 16.0), "D": ser("2026-02", 3.9), "E": ser("2026-09", 0.5), "F": ser("2026-08", 2.0),
         "G": ser("2026-06", 1.5)}
long_ = {"A": long_a, "B": ser("2018-06", 7.5, 7.62), "C": ser("2018-06", 9.0), "D": ser("2026-10", 4.4), "E": ser("2026-10", 1.6), "F": ser("2026-10", 3.0)}
check(long_a[0][0] == "2025-10-01" and long_a[-2] == ["2026-09-01", 4.3] and len(long_a) == 13, "fixture: 13 monthly points 2025-10 .. 2026-10")

rows = {r["country"]: r for r in c._rate_rows(imm, short, long_)}
a = rows["A"]
check((a["overnight"], a["m3"], a["y10"], a["as_of"]) == (2.75, 3.0, 4.5, "2026-10"), "A: every leg current, as of 2026-10")
check(a["slope"] == 150, "A: slope 4.5 - 3.0 = 150 bp")
check(a["y10_chg_1m"] == 20 and a["y10_chg_1y"] == 50, "A: 10Y 1M 4.5 - 4.3 = 20 bp, 1Y 4.5 - 4.0 = 50 bp")
check(a["note"] == "", "A: no note")
b = rows["B"]
check(b["y10"] is None and b["m3"] is None and b["overnight"] == 16.5, "B: 10Y of 2018 and 3M of 2022 blanked, overnight of 2026-10 kept")
check("slope" not in b and "y10_chg_1m" not in b and "y10_chg_1y" not in b, "B: no slope and no 10Y changes from a stale 10Y")
check(b["as_of"] == "2026-10", "B: as of the newest shown leg")
check("10Y" in b["note"] and "2018-06" in b["note"] and "3M" in b["note"] and "2022-02" in b["note"], "B: the note names what is not shown and its last month")
check("C" not in rows, "C: only a 2018 10Y -> no row")
d = rows["D"]
check(d["m3"] is None and d["y10"] == 4.4 and d["overnight"] == 4.0 and "slope" not in d and "2026-02" in d["note"],
      "D: 3M of 2026-02 is more than 3 months before 2026-10 -> blank, no slope")
e = rows["E"]
check(e["m3"] == 0.5 and e["y10"] == 1.6 and e["slope"] == 110 and e["as_of"] == "2026-09", "E: 3M 2026-09 and 10Y 2026-10 (30 days apart): slope 110, as of the older")
f = rows["F"]
check(f["m3"] == 2.0 and f["y10"] == 3.0 and "slope" not in f and "slope" in f["note"] and f["as_of"] == "2026-08",
      "F: 3M 2026-08 and 10Y 2026-10 (61 days apart): both shown, no slope, said in the note")
g = rows["G"]
check(g["overnight"] == 1.25 and g["m3"] is None and g["as_of"] == "2026-07", "G: 2026-07 is exactly 3 months before 2026-10 (kept); 2026-06 is not")
check(c._rate_rows({}, {}, {}) == [], "no data -> no rows")

# the view uses the same rule, from OECD or from FRED's copies
saved = c._oecd_latest
try:
    c._cache.clear()
    c._oecd_latest = lambda d: {"immediate": imm, "short": short, "long": long_}[d]
    out = c.rates_by_country(source="oecd")
    got = {r["country"]: r for r in out["results"]}
    check(set(got) == {"A", "B", "D", "E", "F", "G"} and got["B"]["y10"] is None, "rates_by_country: stale legs blanked, empty rows dropped")
    check(out["sources"] and all(s["name"] == "OECD" for s in out["sources"]), "rates_by_country: OECD sources")
finally:
    c._oecd_latest = saved
    c._cache.clear()

# ---- Global Yields: monthly rows are aged by their month, dropped after 400 days, "older data" after 75 days
today = dt.date.today()
three_months = c.minus_months(today, 3).strftime("%Y-%m")
last_month = c.minus_months(today, 1).strftime("%Y-%m")
saved = c.curves, c._policy_series, c.rates_by_country
try:
    c._cache.clear()
    c.curves = lambda keys, dates=None, us_source="auto": {}
    c._policy_series = lambda: {}
    c.rates_by_country = lambda **_: {"results": [
        {"country": "Indonesia", "m3": 5.0, "y10": 6.5, "as_of": "2024-12"},
        {"country": "Chile", "m3": 4.8, "y10": 5.5, "as_of": three_months},
        {"country": "Israel", "m3": None, "y10": 4.2, "as_of": last_month},
        {"country": "Russia", "m3": None, "y10": None, "overnight": 16.5, "as_of": last_month}],
        "sources": [c.oecd_source("short"), c.oecd_source("long")]}
    gy = {r["country"]: r for r in c.global_yields()["results"]}
    check("Indonesia" not in gy, "a monthly row from 2024-12 is not shown as current")
    check("Russia" not in gy, "a country without a current 10Y is not listed")
    check(gy["Chile"]["note"].startswith("older data") and gy["Chile"]["as_of"] == three_months, f"a three-month-old monthly row ({three_months}) says older data")
    check("older data" not in gy["Israel"]["note"] and gy["Israel"]["freq"] == "monthly", f"last month's row ({last_month}) is normal for monthly data")
    check("3M = interbank rate" in gy["Chile"]["note"] and "interbank" not in gy["Israel"]["note"], "interbank note only where a 3M is shown")
    check("age" not in gy["Chile"] and "_old_after" not in gy["Chile"], "no internal fields in the answer")
finally:
    c.curves, c._policy_series, c.rates_by_country = saved
    c._cache.clear()

# ---- Rate History: a series that ended long ago says so in its legend instead of passing for today's level
saved = c._oecd_series
try:
    c._cache.clear()
    c._oecd_series = lambda duration: {"Russia": ser("2018-06", 7.5, 7.62), "Germany": ser(last_month, 2.6, 2.7)}
    names = [t["name"] for t in c.rate_history(country="russia,germany", duration="long", start="2018")["data"]]
    check(names[0].startswith("Russia") and "last 2018-06" in names[0], "stale series: legend says when it ended")
    check("last" not in names[1] and "2.70%" in names[1], "current series: legend shows the level only")
finally:
    c._oecd_series = saved
    c._cache.clear()

done("test_rates")
