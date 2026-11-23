"""HANDOFF P3-3 policy path (terminal/policy_path.py): node matching across dates, step gaps, changes in bp, anchor, stale."""

import datetime as dt

from _common import check, done

import composite as c
import policy_path as pp

TODAY = dt.date(2026, 11, 19)


def row(start, end, rate, date, trades=10):
    return {"start_date": start, "end_date": end, "rate": rate, "num_trades": trades, "as_of_date": date, "date": date}


PATHS = {   # (currency, date param) -> backend rows
    ("USD", None): [row("2027-01-28", "2027-03-18", 3.50, "2026-11-18"), row("2026-12-10", "2027-01-28", 3.62, "2026-11-18", 50),
                    row("2027-04-29", "2027-06-17", 3.40, "2026-11-18"),
                    row("2026-12-21", "2027-01-21", 3.70, "2026-11-17", 3)],      # bespoke period over a meeting one: dropped
    ("USD", "2026-11-12"): [row("2026-12-10", "2027-01-28", 3.70, "2026-11-12"), row("2027-01-29", "2027-03-18", 3.55, "2026-11-12")],
    ("USD", "2026-10-19"): [row("2026-10-29", "2026-12-10", 3.90, "2026-10-17"), row("2026-12-11", "2027-01-28", 3.80, "2026-10-17"),
                            row("2027-01-28", "2027-03-18", 3.62, "2026-10-17"), row("2027-04-20", "2027-06-17", 3.45, "2026-10-17")],
    ("EUR", None): [row("2026-12-23", "2027-02-10", 2.681, "2026-11-18")],
}
FRED = {"SOFR": ("2026-11-18", 3.60), "DFEDTARL": ("2026-11-18", 3.50), "DFEDTARU": ("2026-11-18", 3.75)}
calls = []


def fake_api(base, path, currency=None, date=None, **_):
    calls.append((path, currency, date))
    return PATHS.get((currency, date), [])


saved = c.api, c._policy_series, pp._fred_last
try:
    c.api = fake_api
    pp._fred_last = FRED.get
    c._policy_series = lambda: {"US": [("2026-11-01", 3.875), ("2026-11-10", 3.625)], "XM": [("2026-11-10", 2.0)]}
    c._cache.clear()

    # ---- matching: same start, else nearest within 7 days
    past = pp.nodes_from_rows(PATHS[("USD", "2026-10-19")])[0]
    check(pp.match({"start": "2026-12-10"}, past)["rate"] == 3.80, "match: 1 day off is the same period")
    check(pp.match({"start": "2027-04-29"}, past) is None, "match: 9 days off is not the same period")
    check(pp.match({"start": "2027-01-28"}, []) is None, "match: no earlier path")

    # ---- meeting path: overlapping bespoke periods drop out, a missing meeting stays a gap
    mp = pp.meeting_path(pp.nodes_from_rows(PATHS[("USD", None)])[0])
    check([n["start"] for n in mp] == ["2026-12-10", "2027-01-28", "2027-04-29"], f"meeting path: {[n['start'] for n in mp]}")
    two_thin = [{"start": "2027-01-01", "end": "2027-02-01", "rate": 1, "trades": 4}, {"start": "2027-02-01", "end": "2027-03-01", "rate": 1, "trades": 4},
                {"start": "2027-01-10", "end": "2027-02-20", "rate": 1, "trades": 9}]
    check([n["trades"] for n in pp.meeting_path(two_thin)] == [9], "meeting path: more trades wins over two thin overlapping periods (9 > 4 + 4)")
    check(pp.priced_move(-12) == -48 and pp.priced_move(30) == 120 and pp.priced_move(None) is None, "priced move: step / 25 bp")
    check(pp.meeting_label("2026-12-10") == "Dec 2026", "meeting label")
    check([n["start"] for n in pp.ahead([{"start": "2026-11-01", "end": "2026-12-01"}, {"start": "2026-10-01", "end": "2026-11-19"}], TODAY)] == ["2026-11-19"],
          "ahead: a begun period starts today, a finished one is dropped")

    # ---- table: rows hand-computed
    t = pp.table(currency="usd", today=TODAY)
    r = t["results"]
    check(r[0] == {"start": "Policy rate", "end": "BIS, 2026-11-10", "meeting": "target 3.50–3.75%", "rate": 3.625}, f"table: first row is the BIS anchor with the Fed range: {r[0]}")
    check(r[1] == {"start": "2026-11-19", "end": "2026-12-10", "meeting": "now (SOFR fixing)", "rate": 3.60, "vs_policy": -3, "as_of": "2026-11-18"},
          f"table: current period at the SOFR fixing: {r[1]}")
    check([x["start"] for x in r[2:]] == ["2026-12-10", "2027-01-28", "2027-04-29"], "table: meeting periods sorted by start, bespoke one gone")
    a, b, d = r[2:]
    check([(x["meeting"], x["step"], x["priced"]) for x in (a, b, d)] == [("Dec 2026", 2, 8), ("Jan 2027", -12, -48), ("Apr 2027", None, None)],
          f"table: step from the fixing, then period to period, blank after a missing period (Mar 2027); priced move in % of 25 bp: {[(x['step'], x['priced']) for x in (a, b, d)]}")
    check((a["rate_1w"], a["chg_1w"], a["rate_1m"], a["chg_1m"], a["vs_policy"]) == (3.70, -8, 3.80, -18, -1), f"table row 1: {a}")
    check((b["rate_1w"], b["chg_1w"], b["rate_1m"], b["chg_1m"], b["vs_policy"]) == (3.55, -5, 3.62, -12, -13), f"table row 2: {b}")
    check((d["rate_1w"], d["chg_1w"], d["rate_1m"], d["chg_1m"], d["vs_policy"]) == (None, None, None, None, -23), f"table row 3 (no match): {d}")
    check(a["trades"] == 50 and a["as_of"] == "2026-11-18", "table: trades and as-of carried")
    check(("cftc/ois_policy_path", "USD", "2026-11-12") in calls and ("cftc/ois_policy_path", "USD", "2026-10-19") in calls,
          "table: 1W = today - 7 days, 1M = same day last month")
    check("stale" not in t and [s["name"] for s in t["sources"]] == ["CFTC", "BIS", "FRED"] and "SOFR" in t["sources"][0]["detail"], "table: sources, not stale")

    check(pp.bp(3.624, 3.62) == 0 and str(pp.bp(3.616, 3.62)) == "0" and pp.bp(3.625, 3.62) == 1 and pp.bp(3.615, 3.62) == -1,
          "bp: whole numbers, halves away from zero, no -0")

    # ---- step chart: a missing node leaves a gap
    xs, ys = pp.steps(pp.meeting_path(pp.nodes_from_rows(PATHS[("USD", None)])[0]))
    check(xs == ["2026-12-10", "2027-01-28", "2027-03-18", "2027-03-18", "2027-04-29", "2027-06-17"], f"steps x: {xs}")
    check(ys == [3.62, 3.50, 3.50, None, 3.40, 3.40], f"steps y: {ys}")

    ch = pp.chart(currency="USD", today=TODAY)
    names = [tr["name"] for tr in ch["data"]]
    check(names == ["Today (2026-11-18)", "1W ago (2026-11-12)", "1M ago (2026-10-17)", "Policy rate 3.625% (target 3.50–3.75%) (BIS, 2026-11-10)"], f"chart names: {names}")
    pol = ch["data"][-1]
    check(pol["x"] == ["2026-11-19", "2027-06-17"] and pol["y"] == [3.625, 3.625], "chart: policy line spans every path, from today")
    check(ch["data"][0]["x"][:2] == ["2026-11-19", "2026-12-10"] and ch["data"][0]["y"][:2] == [3.60, 3.62], "chart: today's path starts today at the fixing")
    check(ch["data"][2]["x"][0] == "2026-11-19", "chart: the 1M path is clipped to periods still ahead (starts today)")
    check(ch["layout"]["yaxis"]["tickformat"] == ".2f" and ch["layout"]["annotations"][0]["y"] == 1, "chart: fixed tick format, note at the top")
    check(all(tr["line"]["shape"] == "hv" for tr in ch["data"][:3]), "chart: step lines")

    multi = pp.chart(currency="USD,EUR,XXX", today=TODAY)
    check([tr["name"] for tr in multi["data"]] == ["USD (policy 3.625%)", "EUR (policy 2.000%)"], "overlay: one trace per known currency")
    check("SOFR, ESTR" in multi["sources"][0]["detail"], "overlay: indices named in the source")

    # ---- stale: freshest print more than a week old
    c._cache.clear()
    st = pp.table(currency="USD", today=dt.date(2026, 12, 1))
    check(st.get("stale") == "Latest OIS prints are from 2026-11-18 (13 days old)", f"stale: {st.get('stale')}")
    check(st["results"][1]["chg_1w"] is None, "stale day: no earlier path, change blank")

    # ---- errors and the source contract
    try:
        pp.chart(currency="XXX", today=TODAY)
        check(False, "unknown currency raises")
    except c.CompositeError:
        check(True, "unknown currency raises")
    check([s["name"] for s in c.static_sources("policy_path_table", {"currency": "XXX"})] == ["CFTC", "BIS", "FRED"], "static sources never raise")
    check([s["name"] for s in c.static_sources("policy_path_table", {"currency": "CAD"})] == ["CFTC", "BIS"], "no keyless fixing for CORRA: no FRED source")
    check("ESTR" in c.static_sources("policy_path", {"currency": "EUR"})[0]["detail"], "static sources follow the currency")
    c._cache.clear()
    PATHS[("GBP", None)] = []
    try:
        c.dispatch("policy_path_table", {"currency": "GBP"})
        check(False, "empty path raises")
    except c.CompositeError as e:
        check("GBP" in str(e), "empty path: says which currency")
    check(c.ROUTES["policy_path"] and c.ROUTES["policy_path_table"], "routes registered")
finally:
    c.api, c._policy_series, pp._fred_last = saved
    c._cache.clear()


# review fix: a period under way starts at today after ahead() but still matches the earlier paths by its real start
_cur = pp.ahead([{"start": "2026-10-30", "end": "2026-12-11", "rate": 3.6}], dt.date(2026, 11, 23))[0]
check(_cur["start"] == "2026-11-23" and pp.match(_cur, [{"start": "2026-10-30", "end": "2026-12-11", "rate": 3.7}]) is not None,
      "match: the period under way keeps its real start for the 1W / 1M match")

done("test_policy_path")
