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
                    row("2027-04-29", "2027-06-17", 3.40, "2026-11-18")],
    ("USD", "2026-11-12"): [row("2026-12-10", "2027-01-28", 3.70, "2026-11-12"), row("2027-01-29", "2027-03-18", 3.55, "2026-11-12")],
    ("USD", "2026-10-19"): [row("2026-10-29", "2026-12-10", 3.90, "2026-10-17"), row("2026-12-11", "2027-01-28", 3.80, "2026-10-17"),
                            row("2027-01-28", "2027-03-18", 3.62, "2026-10-17"), row("2027-04-20", "2027-06-17", 3.45, "2026-10-17")],
    ("EUR", None): [row("2026-12-23", "2027-02-10", 2.681, "2026-11-18")],
}
calls = []


def fake_api(base, path, currency=None, date=None, **_):
    calls.append((path, currency, date))
    return PATHS.get((currency, date), [])


saved = c.api, c._policy_series
try:
    c.api = fake_api
    c._policy_series = lambda: {"US": [("2026-11-01", 3.875), ("2026-11-10", 3.625)], "XM": [("2026-11-10", 2.0)]}
    c._cache.clear()

    # ---- matching: same start, else nearest within 7 days
    past = pp.nodes_from_rows(PATHS[("USD", "2026-10-19")])[0]
    check(pp.match({"start": "2026-12-10"}, past)["rate"] == 3.80, "match: 1 day off is the same period")
    check(pp.match({"start": "2027-04-29"}, past) is None, "match: 9 days off is not the same period")
    check(pp.match({"start": "2027-01-28"}, []) is None, "match: no earlier path")

    # ---- table: rows hand-computed
    t = pp.table(currency="usd", today=TODAY)
    r = t["results"]
    check(r[0] == {"start": "Policy rate", "end": "BIS, 2026-11-10", "rate": 3.625}, "table: first row is the BIS anchor (latest value)")
    check([x["start"] for x in r[1:]] == ["2026-12-10", "2027-01-28", "2027-04-29"], "table: periods sorted by start")
    a, b, d = r[1:]
    check((a["rate_1w"], a["chg_1w"], a["rate_1m"], a["chg_1m"], a["vs_policy"]) == (3.70, -8, 3.80, -18, -1), f"table row 1: {a}")
    check((b["rate_1w"], b["chg_1w"], b["rate_1m"], b["chg_1m"], b["vs_policy"]) == (3.55, -5, 3.62, -12, -13), f"table row 2: {b}")
    check((d["rate_1w"], d["chg_1w"], d["rate_1m"], d["chg_1m"], d["vs_policy"]) == (None, None, None, None, -23), f"table row 3 (no match): {d}")
    check(a["trades"] == 50 and a["as_of"] == "2026-11-18", "table: trades and as-of carried")
    check(("cftc/ois_policy_path", "USD", "2026-11-12") in calls and ("cftc/ois_policy_path", "USD", "2026-10-19") in calls,
          "table: 1W = today - 7 days, 1M = same day last month")
    check("stale" not in t and [s["name"] for s in t["sources"]] == ["CFTC", "BIS"] and "SOFR" in t["sources"][0]["detail"], "table: sources, not stale")

    check(pp.bp(3.624, 3.62) == 0 and str(pp.bp(3.616, 3.62)) == "0" and pp.bp(3.625, 3.62) == 1 and pp.bp(3.615, 3.62) == -1,
          "bp: whole numbers, halves away from zero, no -0")

    # ---- step chart: a missing node leaves a gap
    xs, ys = pp.steps(pp.nodes_from_rows(PATHS[("USD", None)])[0])
    check(xs == ["2026-12-10", "2027-01-28", "2027-03-18", "2027-03-18", "2027-04-29", "2027-06-17"], f"steps x: {xs}")
    check(ys == [3.62, 3.50, 3.50, None, 3.40, 3.40], f"steps y: {ys}")

    ch = pp.chart(currency="USD", today=TODAY)
    names = [tr["name"] for tr in ch["data"]]
    check(names == ["Today (2026-11-18)", "1W ago (2026-11-12)", "1M ago (2026-10-17)", "Policy rate 3.62% (BIS, 2026-11-10)"], f"chart names: {names}")
    pol = ch["data"][-1]
    check(pol["x"] == ["2026-10-29", "2027-06-17"] and pol["y"] == [3.625, 3.625], "chart: policy line spans every path")
    check(all(tr["line"]["shape"] == "hv" for tr in ch["data"][:3]), "chart: step lines")

    multi = pp.chart(currency="USD,EUR,XXX", today=TODAY)
    check([tr["name"] for tr in multi["data"]] == ["USD (policy 3.62%)", "EUR (policy 2.00%)"], "overlay: one trace per known currency")
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
    check([s["name"] for s in c.static_sources("policy_path_table", {"currency": "XXX"})] == ["CFTC", "BIS"], "static sources never raise")
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
    c.api, c._policy_series = saved
    c._cache.clear()

done("test_policy_path")
