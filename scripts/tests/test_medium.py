"""Medium audit findings A6, A9, A10, A11 (A12 is in test_launcher_server.py). Expected values worked out by hand."""

import datetime as dt

from _common import check, done, make_tmp_dir

import composite as c

# ---- A6: the FRED disk copy is per start year, so it must hold the whole year and be cut to the asked start
calls = []


def fake_api_full(base, path, **kw):
    calls.append(kw.get("start_date"))
    rows = [{"date": d, "X": v} for d, v in (("2025-01-02", 1.0), ("2025-06-02", 2.0), ("2025-11-17", 3.0), ("2026-01-05", 4.0))]
    return {"results": [r for r in rows if r["date"] >= kw["start_date"]], "extra": {"results_metadata": {"X": {"title": "X", "units": "u", "frequency": "w"}}}}


saved = c.api_full
try:
    c._cache.clear()
    c.api_full = fake_api_full
    one, _m = c._fred(["X"], "2025-11-16")                 # a 1Y range chip: an exact date
    two, _m = c._fred(["X"], "2025-01-01")                 # the card's own "since 2025"
    check(calls == ["2025-01-01"], "one request, from the first of the year")
    check(one["X"][0][0] == "2025-11-17", "range chip: the answer starts at the asked date")
    check(two["X"][0][0] == "2025-01-02" and len(two["X"]) == 4, "same year from the disk copy: starts in January, not November")
finally:
    c.api_full = saved
    c._cache.clear()

# ---- A9: year over year compares with the observation nearest one year earlier (52 weeks for weekly data)
weekly = [[(dt.date(2024, 1, 5) + dt.timedelta(weeks=n)).isoformat(), 100.0 + n] for n in range(120)]
yoy = c._yoy(weekly)
check(abs(yoy[-1][1] - (219 / 167 - 1) * 100) < 1e-9 and abs(yoy[-1][1] - 31.1377) < 1e-4, "weekly: 219 vs 167 (52 weeks) = 31.1377%, not 31.9277% (53 weeks)")
monthly = []
d = dt.date(2023, 1, 1)
for i in range(36):
    monthly.append([d.isoformat(), 100.0 + i])
    d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
got = dict((p[0], p[1]) for p in c._yoy(monthly))
want = {monthly[i][0]: ((100.0 + i) / (100.0 + i - 12) - 1) * 100 for i in range(12, 36)}
check(set(got) == set(want) and all(abs(got[k] - want[k]) < 1e-9 for k in want), "monthly incl. leap year 2024: each month against the same month a year earlier")
holed = [p for p in monthly if p[0][:7] not in ("2024-04", "2024-05", "2024-06")]
got = dict((p[0], p[1]) for p in c._yoy(holed))
check(not {"2025-04-01", "2025-05-01", "2025-06-01"} & set(got) and "2025-07-01" in got and "2025-03-01" in got,
      "a three-month hole a year earlier gives no point, never one from another month")
bp = c._yoy([["2025-01-01", 4.0], ["2026-01-01", 4.25]], diff_bp=True)
check(len(bp) == 1 and abs(bp[0][1] - 25.0) < 1e-9, "a rate's year-over-year change in bp")

# ---- A10: EIA STEO charts end in forecasts: history solid, forecast dashed, legend value = latest actual month
months = []
d = dt.date(2026, 1, 1)
for i in range(24):
    months.append(d.isoformat())
    d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
saved = c._series, c._month_start
try:
    c._series = lambda ids, start: ({"EIA:STEO.BREPUUS.M": [[m, 60.0 + i] for i, m in enumerate(months)],
                                     "EIA:STEO.WTIPUUS.M": [[m, 55.0 + i] for i, m in enumerate(months)]}, {})
    c._month_start = lambda: "2026-11-01"
    out = c.macro_chart(preset="oil_price_forecast", since="2026")
    tr = out["data"]
    check(len(tr) == 4, "two series, each split into actual and forecast")
    brent, brent_f = tr[0], tr[1]
    check(brent["name"] == "Brent  69.00 (latest actual 2026-10)" and brent["x"][-1] == "2026-10-01", "legend: Brent 2026-10 = 60 + 9 = 69.00, the latest actual month")
    check(brent_f["x"][0] == "2026-10-01" and brent_f["x"][-1] == "2027-12-01" and brent_f["line"]["dash"] == "dash", "forecast dashed, joined to the last actual")
    check(brent_f["showlegend"] is False and brent_f["line"]["color"] == brent["line"]["color"], "forecast: same colour, no second legend entry")
    check(tr[2]["name"] == "WTI  64.00 (latest actual 2026-10)", "WTI 2026-10 = 55 + 9 = 64.00")
    check("Dashed: EIA forecast" in " ".join(a["text"] for a in out["layout"].get("annotations", [])), "the chart says what dashed means")
    c._series = lambda ids, start: ({"NAPM": [[m, 50.0] for m in months[:10]]}, {})
    plain = c.macro_chart(series="NAPM", since="2026")
    check(len(plain["data"]) == 1 and "latest actual" not in plain["data"][0]["name"], "other series are not split")
    # review A5: JOLTS is in thousands with a "k" suffix; the axis must not add an SI "k" too ("6kk")
    c._series = lambda ids, start: ({"JTSJOL": [[m, 7200.0 + i] for i, m in enumerate(months[:10])]}, {})
    jo = c.macro_chart(preset="job_openings", since="2026")
    check(jo["layout"]["yaxis"]["ticksuffix"].strip() == "k" and jo["layout"]["yaxis"].get("tickformat") == ",~f", "JOLTS axis 7,200k, not 7.2kk")
    check(plain["layout"]["yaxis"].get("tickformat") is None, "a chart without a unit suffix keeps Plotly's own ticks")
    # last JOLTS point 7200 + 9 = 7209 (thousands): the legend says 7,209k, the hover has no decimals either
    check(jo["data"][0]["name"].endswith("  7,209k") and jo["data"][0]["hovertemplate"].startswith("%{y:,.0f}"), "JOLTS legend 7,209k, not 7,209.00k")
    check(plain["data"][0]["name"].endswith("  50.00") and plain["data"][0]["hovertemplate"].startswith("%{y:,.2f}"), "other series keep 2 decimals")
    # review A6: OAS charts in bp like the euro sovereign spreads (FRED HY OAS 3.34 % = 334 bp)
    c._series = lambda ids, start: ({i: [[m, 3.34] for m in months[:10]] for i in ids}, {})
    oas = c.macro_chart(preset="credit_spreads", since="2026")
    check(oas["data"][0]["name"] == "High yield  334 bp" and oas["layout"]["yaxis"]["ticksuffix"].strip() == "bp", "credit spreads in bp: 3.34 % -> 334 bp")
    check(all(c.MACRO_SETS[k][1] == " bp" and all(m == 100 for _, _, m in c.MACRO_SETS[k][3])
              for k in ("credit_spreads", "distressed_spreads", "ig_by_rating", "hy_by_rating", "credit_regions", "euro_spreads")), "every spread chart in bp")
finally:
    c._series, c._month_start = saved

# ---- A11: policy rates from the BIS daily series when it is current; month-end values said as such otherwise
today = dt.date.today()


def ago(n):
    return (today - dt.timedelta(days=n)).isoformat()


m1, m2 = c.minus_months(today, 1).strftime("%Y-%m"), c.minus_months(today, 2).strftime("%Y-%m")
monthly = {"US": [(c.minus_months(today, k).strftime("%Y-%m"), 4.0) for k in range(14, 0, -1)],
           "GB": [(c.minus_months(today, k).strftime("%Y-%m"), 5.0 if k > 6 else 4.5) for k in range(14, 0, -1)]}
daily = {"US": [(ago(n), 4.0 if n > 12 else 3.75) for n in range(400, 4, -7)]}
saved = c._bis_policy, c._bis_policy_daily
try:
    c._cache.clear()
    c._bis_policy, c._bis_policy_daily = (lambda: monthly), (lambda: daily)
    rows = {r["code"]: r for r in c.policy_rates()["results"]}
    us, gb = rows["US"], rows["GB"]
    last_day = daily["US"][-1][0]
    move_day = next(dd for dd, v in daily["US"] if v == 3.75)
    check(us["rate"] == 3.75 and us["as_of"] == last_day, "U.S.: this month's cut is shown (daily series), as of its last day")
    check(us["last_move"] == move_day and us["last_move_bp"] == -25 and us["previous"] == 4.0, "last move: the day of the cut, -25 bp from 4.00")
    check(us["chg_1y"] == -25 and not us.get("note"), "1-year change from the daily series: 3.75 - 4.00")
    check(gb["rate"] == 4.5 and gb["as_of"] == m1 and gb["note"] == "month-end value", "no daily series: the month-end value, said as such")
    check(gb["last_move_bp"] == -50 and gb["chg_1y"] == -50, "monthly logic unchanged: 5.0 -> 4.5")
    check(gb["vs_us"] == 75, "carry vs the dollar from the current U.S. rate: 4.50 - 3.75")
    series = c._policy_series()
    check(series["US"][-1] == (last_day, 3.75) and series["GB"][-1] == (m1, 4.5), "other views get the daily series where current, else the monthly one")
    c._bis_policy_daily = lambda: (_ for _ in ()).throw(c.CompositeError("BIS daily not reachable"))
    c._cache.clear()
    rows = {r["code"]: r for r in c.policy_rates()["results"]}
    check(rows["US"]["rate"] == 4.0 and rows["US"]["note"] == "month-end value", "daily flow down: month-end values, labelled")
    # history chart: month-end points, then the days after the last month (BoE 2026-11-05: 3.75 -> 4.00, review F3)
    pts = c._policy_history_points([("2026-09", 3.75), ("2026-10", 3.75)], [("2026-10-30", 3.75), ("2026-11-05", 4.0), ("2026-11-09", 4.0)])
    check(pts == [("2026-09-01", 3.75), ("2026-10-01", 3.75), ("2026-11-05", 4.0), ("2026-11-09", 4.0)], "policy history: daily points after the last month")
    check(c._policy_history_points([("2026-10", 3.75)], []) == [("2026-10-01", 3.75)], "policy history: no daily series, months only")
    c._bis_policy, c._bis_policy_daily = (lambda: monthly), (lambda: daily)
    c._cache.clear()
    tr = {t["name"].split("  ")[0]: t for t in c.policy_rate_history("US,GB", start="2000")["data"]}
    check(tr["United States"]["y"][-1] == 3.75 and tr["United States"]["x"][-1] == last_day and tr["United Kingdom"]["y"][-1] == 4.5,
          "history legend and last point: the daily cut for the U.S., month-end for the U.K.")
finally:
    c._bis_policy, c._bis_policy_daily = saved
    c._cache.clear()

# P0-6: comps keep metrics per symbol; a symbol Yahoo dropped is asked for again, the others are not
_saved = c.api, c._meta, c._usd_rates, c.time.sleep
_asked = []
try:
    def _api(base, path, **kw):
        if path == "equity/fundamental/metrics":
            need = kw["symbol"].split(",")
            _asked.append(need)
            got = [s for s in need if s != "BBB"] if len(_asked) == 1 else need       # first answer drops BBB
            return [{"symbol": s, "market_cap": 100e9, "pe_ratio": 20.0, "currency": "USD"} for s in got]
        return []
    c.api, c._meta, c._usd_rates, c.time.sleep = _api, (lambda syms: {s: {"currency": "USD", "name": s} for s in syms}), (lambda cs: {"USD": 1.0}), (lambda s: None)
    c._METRICS.clear()
    r1 = {r["symbol"]: r for r in c.comps("AAA", peers="BBB,CCC")["results"] if r.get("symbol")}
    check(_asked == [["AAA", "BBB", "CCC"], ["BBB"]] and r1["BBB"]["pe"] == 20.0, "dropped peer retried once, alone: " + str(_asked))
    _asked.clear()
    c.comps("AAA", peers="BBB,CCC")
    check(_asked == [], "second view within the hour: all three from the per-symbol cache")
finally:
    c.api, c._meta, c._usd_rates, c.time.sleep = _saved
    c._METRICS.clear()

# P0-6: an answer flagged _partial is kept 60 s, a complete one the full ttl
_n = []
c._cache.clear()
_v = c.cached_complete(("t", 1), 900, lambda: (_n.append(1), {"x": 1, "_partial": True})[1])
c._cache[("t", 1)] = (c.time.monotonic() - 61, c._cache[("t", 1)][1])
c.cached_complete(("t", 1), 900, lambda: (_n.append(1), {"x": 2})[1])
c._cache[("t", 1)] = (c.time.monotonic() - 600, c._cache[("t", 1)][1])
_v3 = c.cached_complete(("t", 1), 900, lambda: (_n.append(1), {"x": 3})[1])
check(len(_n) == 2 and _v3 == {"x": 2}, "partial refetched after 60 s; complete kept 15 minutes")
c._cache.clear()

# Compare (W2): line items merged across XBRL tags by period, first-listed tag wins on overlap (AAPL revenue tags)
_sa, _sd = c.api, c.cached_disk
try:
    def _api2(base, path, **kw):
        tag = kw["fact"]
        if tag == "Revenues":
            return []
        if tag == "RevenueFromContractWithCustomerExcludingAssessedTax":
            return [{"symbol": "AAPL", "period_ending": "2018-09-29", "period_beginning": "2017-10-01", "span": "annual", "value": 265.6e9},
                    {"symbol": "AAPL", "period_ending": "2026-09-26", "period_beginning": "2025-09-28", "span": "annual", "value": 420.0e9}]
        return [{"symbol": "AAPL", "period_ending": "2018-09-29", "period_beginning": "2017-10-01", "span": "annual", "value": 999e9},
                {"symbol": "AAPL", "period_ending": "2016-09-24", "period_beginning": "2015-09-27", "span": "annual", "value": 215.6e9}]
    c.api, c.cached_disk = _api2, (lambda name, ttl, fn: fn())
    _f = c._facts(["AAPL"], "revenue")["AAPL"]["annual"]
    check(_f == [["2016-09-24", 215.6e9], ["2018-09-29", 265.6e9], ["2026-09-26", 420.0e9]], "revenue: SalesRevenueNet fills FY2016, current tag wins FY2018: " + str(_f))
finally:
    c.api, c.cached_disk = _sa, _sd

# audit C9: Yahoo's earnings date as one date, a list or a "a, b" range
check(c._date_span(["2026-02-02", "2026-01-28"]) == ("2026-01-28", "2026-02-02") and c._date_span("2026-01-28") == ("2026-01-28", None)
      and c._date_span("2026-01-28 00:00:00, 2026-02-02 00:00:00") == ("2026-01-28", "2026-02-02") and c._date_span(None) == (None, None), "earnings date span")
# P2: a saved copy served after a failed fetch is named in the view's answer, also from a pool thread
import json as _json
_scd = c.CACHE_DIR
try:
    c.CACHE_DIR = make_tmp_dir("p2-")
    (c.CACHE_DIR / "p2_test.json").write_text(_json.dumps({"t": c.time.time() - 7200, "v": [1, 2]}), encoding="utf-8")
    def _fail():
        raise c.CompositeError("down")
    def _view(**_):
        with c.cf.ThreadPoolExecutor(max_workers=2) as ex:
            v = list(ex.map(lambda _x: c.cached_disk("p2_test", 60, _fail), [0]))[0]
        return {"results": v, "sources": [c.src("X")], "as_of": "2026-11-20"}
    c.ROUTES["p2_test"] = _view
    c._cache.clear(); c._backoff.clear()
    _o = c.dispatch("p2_test", {})
    check(_o["results"] == [1, 2] and _o.get("stale", "").startswith("Source not answering: showing the copy saved") and "(2 h old)" in _o["stale"],
          "stale copy named, with its age, through a pool thread: " + str(_o.get("stale")))
finally:
    c.CACHE_DIR = _scd
    c.ROUTES.pop("p2_test", None)
    c._STALE_SINCE.pop("p2_test", None)
    c._cache.clear(); c._backoff.clear()
done("test_medium")
