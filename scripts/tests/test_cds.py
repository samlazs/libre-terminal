"""HANDOFF P3-2 CDS indices (terminal/cds.py): quote conventions, on-the-run series, changes at the same maturity, rolls."""

import datetime as dt
import json

from _common import check, done, make_tmp_dir

import composite as c
import cds

TODAY = dt.date(2026, 11, 19)
NEW = "2031-12-20"
OLD = "2031-06-20"


def p(index, mat, spread, notation, notional, action="NEWT", event="TRAD", ccy="USD"):
    return {"index": index, "maturity_date": mat, "spread": spread, "spread_notation": notation, "notional_amount": notional,
            "action_type": action, "event_type": event, "notional_currency": ccy}


DAYS = {
    "2026-11-18": [p("CDX.NA.IG", NEW, 0.0060, "3", 50e6), p("CDX.NA.IG", NEW, 62, "4", 25e6), p("CDX.NA.IG", OLD, 0.0055, "3", 10e6),
                   p("CDX.NA.IG", NEW, 0.0099, "3", 50e6, action="MODI"), p("CDX.NA.IG", NEW, 0.0099, "3", 50e6, event="EXER"),
                   p("CDX.NA.IG", NEW, 99.0, "1", 50e6),
                   p("CDX.NA.IG", "2031-12-22", 0.0, "3", 50e6), p("CDX.NA.IG", NEW, 0.0, "3", 50e6),        # bespoke date, zero spread
                   p("CDX.NA.HY", NEW, 105, "1", 10e6), p("CDX.NA.HY", NEW, 0.0106, "3", 30e6),
                   p("CDX.NA.IG", NEW, 0.6, "3", 10e6), p("CDX.NA.HY", NEW, 1.0579, "3", 5e6),           # misreported: 6,000 bp, 10,579 points
                   p("ITRAXX EUROPE", NEW, 0.0055, "3", 20e6, ccy="EUR"), p("ITRAXX EUROPE SENIOR FINANCIALS", NEW, 0.007, "3", 20e6)],
    "2026-11-17": [p("CDX.NA.IG", NEW, 0.0058, "3", 10e6), p("CDX.NA.HY", OLD, 104, "1", 10e6)],
    "2026-11-11": [p("CDX.NA.IG", OLD, 0.0052, "3", 10e6)],         # U.S. holiday: off-the-run prints only
    "2026-11-10": [p("CDX.NA.IG", NEW, 0.0064, "3", 10e6)],
    "2026-10-16": [p("CDX.NA.IG", OLD, 0.0050, "3", 10e6)],
}
calls = []


FAIL = set()                    # dates whose file the repository does not serve just now


def fake_api(base, path, tenor=None, date=None, **_):
    calls.append(date)
    assert path == "cftc/cds_index_trades" and tenor == "5Y"
    if date in FAIL:
        raise c.CompositeError("The data service answered HTTP 502", kind="upstream")
    return DAYS.get(date, [])


def day_file(d):
    return c.CACHE_DIR / f"{cds.day_name(d)}.json"


def forget(*dates):
    """Drop days from the store: disk entry, memory entry and remembered failure."""
    for d in dates:
        day_file(d).unlink(missing_ok=True)
        c._cache.pop(("disk", cds.day_name(d)), None)
        c._backoff.pop(cds.day_name(d), None)


def age(d, secs):
    """Make the saved summary of d `secs` older, and drop its memory entry (as after a restart)."""
    rec = json.loads(day_file(d).read_text(encoding="utf-8"))
    rec["t"] -= secs
    day_file(d).write_text(json.dumps(rec), encoding="utf-8")
    c._cache.pop(("disk", cds.day_name(d)), None)


tmp = make_tmp_dir("cds-") / "cds_index_days.json"
saved = c.api, cds._path, cds._days
try:
    c.api, cds._path, cds._days = fake_api, (lambda: tmp), None

    # ---- conventions
    check(cds.level({"spread": 0.0061, "spread_notation": "3"}, "spread") == 0.0061 * 1e4, "spread notation 3: decimal x 10,000 = bp")
    check(cds.level({"spread": 61.2, "spread_notation": "4"}, "spread") == 61.2, "spread notation 4: already bp")
    check(cds.level({"spread": 99.0, "spread_notation": "1"}, "spread") is None, "spread family: a price print is not a spread")
    check(cds.level({"spread": 105.3, "spread_notation": "1"}, "price") == 105.3, "price notation 1: points")
    check(abs(cds.level({"spread": 0.010545, "spread_notation": "3"}, "price") - 105.45) < 1e-9, "price notation 3: x 10,000 = points")
    check(cds.level({"spread": 300, "spread_notation": "4"}, "price") is None, "price family: never a bp print")
    check(cds.level({"spread": None, "spread_notation": "3"}, "spread") is None, "no spread: blank")

    s = cds.summarise(DAYS["2026-11-18"])
    check(set(s) == {"CDX.NA.IG", "CDX.NA.HY", "ITRAXX EUROPE"}, f"families kept: {sorted(s)}")
    ig = s["CDX.NA.IG"][NEW]
    check(abs(ig["level"] - 60.6667) < 1e-4 and ig["low"] == 60.0 and ig["high"] == 62.0 and ig["trades"] == 2 and ig["notional"] == 75e6,
          f"IG: (60x50 + 62x25)/75 = 60.6667, NEWT/TRAD only: {ig}")
    check(cds.on_the_run(s["CDX.NA.IG"])[0] == NEW, "on-the-run = most notional")
    check(ig["dropped"] == 1 and s["CDX.NA.HY"][NEW]["dropped"] == 1 and s["CDX.NA.HY"][NEW]["high"] == 106.0, "off-market prints dropped (median 60 bp / 106 points)")
    check(cds.weighted_median([(60, 50), (62, 25), (6000, 10)]) == 60 and cds.weighted_median([(1, 1), (2, 1)]) == 1, "weighted median: half the notional")
    check(cds.on_the_run({"A": {"notional": 5}, "B": {"notional": 5}})[0] == "B", "tie: the later maturity")

    # ---- table: hand-computed
    t = cds.table(today=TODAY)
    rows = {r["index"]: r for r in t["results"]}
    check(list(rows) == ["CDX IG", "CDX HY", "iTraxx Main"], f"rows: {list(rows)}")
    r = rows["CDX IG"]
    check((r["level"], r["unit"], r["chg_1d"], r["chg_1w"], r["chg_1m"]) == (60.67, "bp", 2.67, -3.33, None), f"IG: {r}")
    check((r["low"], r["high"], r["trades"], r["notional_mn"], r["currency"], r["maturity"], r["date"]) == (60.0, 62.0, 2, 75, "USD", NEW, "2026-11-18"), f"IG detail: {r}")
    check(r["dropped"] == 1, "table: off-market count shown")
    check("note" not in r, "IG: no roll")
    h = rows["CDX HY"]
    check((h["level"], h["unit"], h["chg_1d"], h["chg_1w"]) == (105.75, "points", None, None), f"HY: (105x10 + 106x30)/40 = 105.75 points: {h}")
    check(h.get("note", "").startswith("new series"), "HY: roll noted, no change across series")
    check(rows["iTraxx Main"]["currency"] == "EUR" and rows["iTraxx Main"]["level"] == 55.0, "iTraxx Main: EUR notional, 55 bp")
    check("2026-11-14" not in calls and "2026-11-15" not in calls, "weekends not fetched")
    check(min(calls) == "2026-10-14" and max(calls) == "2026-11-18", f"window: one month and four days back from yesterday: {min(calls)}..{max(calls)}")
    check("stale" not in t and t["sources"][0]["name"] == "CFTC", "sources, not stale")

    # ---- day store on the shared cache: final days kept, recent days and empty days refreshed after 30 minutes
    n = len(calls)
    cds.day("2026-11-17", TODAY)
    cds.day("2026-11-18", TODAY)
    check(len(calls) == n, "cached days not fetched again")
    age("2026-11-18", 4000)
    age("2026-11-17", 4000)
    cds.day("2026-11-18", TODAY)
    cds.day("2026-11-17", TODAY)
    check(calls[n:] == ["2026-11-18"], "yesterday refreshed after 30 min, an older day with prints is final")
    check(day_file("2026-11-17").exists() and not tmp.exists(), "summaries written through the shared cache, one entry per day")
    import local_paths
    check(local_paths._is_protected_cache(day_file("2026-11-17")), "the day store is a history store: not pruned after 30 days")
    check(json.loads(day_file("2026-10-20").read_text(encoding="utf-8"))["v"] == {}, "a day with no prints is saved empty")
    n = len(calls)
    age("2026-10-20", 1000)
    cds.day("2026-10-20", TODAY)
    check(calls[n:] == [], "an empty final day is kept 30 minutes")
    age("2026-10-20", 1000)
    cds.day("2026-10-20", TODAY)
    check(calls[n:] == ["2026-10-20"], "an empty final day is fetched again after 30 minutes, not kept for good")

    # ---- the old one-file store seeds final days with prints (no fetch), never empty ones
    tmp.write_text(json.dumps({"2026-11-03": {"t": 0, "s": {"CDX.NA.IG": {NEW: {"level": 61.0}}}}, "2026-11-04": {"t": 0, "s": {}}}), encoding="utf-8")
    cds._days = None
    forget("2026-11-03", "2026-11-04")
    n = len(calls)
    check(cds.day("2026-11-03", TODAY) == {"CDX.NA.IG": {NEW: {"level": 61.0}}} and calls[n:] == [], "old store: a final day with prints is reused")
    cds.day("2026-11-04", TODAY)
    check(calls[n:] == ["2026-11-04"], "old store: an empty day is fetched")
    check(json.loads(day_file("2026-11-03").read_text(encoding="utf-8"))["v"]["CDX.NA.IG"][NEW]["level"] == 61.0, "reused day moved to the new store")
    forget("2026-11-03")
    tmp.unlink()
    cds._days = None

    # ---- a day the repository does not serve: left out, the answer is partial (held a minute, nothing saved for it)
    forget("2026-11-12")
    FAIL.add("2026-11-12")
    got = c._collect(lambda: cds.table(today=TODAY))
    check(isinstance(got, c.Partial) and got.missing == ["CFTC swap file of 2026-11-12"], f"failed day named: {got!r}")
    check(not day_file("2026-11-12").exists(), "no entry saved for the failed day")
    FAIL.clear()
    c._backoff.pop(cds.day_name("2026-11-12"), None)
    check(not isinstance(c._collect(lambda: cds.table(today=TODAY)), c.Partial), "after the backoff the day is fetched: complete again")

    # ---- history: roll leaves a gap, price on its own axis
    hist = cds.history(family="CDX.NA.IG,CDX.NA.HY,BOGUS", range="1M", today=TODAY)
    tr = {x["name"]: x for x in hist["data"]}
    check(list(tr) == ["CDX IG  60.67 bp", "CDX HY  105.75 points"], f"history names: {list(tr)}")
    g = tr["CDX IG  60.67 bp"]
    check(g["x"] == ["2026-11-10", "2026-11-17", "2026-11-18"] and g["y"] == [64.0, 58.0, 60.67] and "yaxis" not in g, f"IG history: {g['x']} {g['y']}")
    y = tr["CDX HY  105.75 points"]
    check(y["x"] == ["2026-11-17", "2026-11-18", "2026-11-18"] and y["y"] == [104.0, None, 105.75] and y["yaxis"] == "y2", f"HY history with roll gap: {y['x']} {y['y']}")
    check(hist["layout"]["yaxis2"]["side"] == "right" and hist["controls"]["range"] == "1M", "second axis and range chip")
    only = cds.history(family="CDX.NA.HY", range="bogus", today=TODAY)
    check("yaxis" not in only["data"][0] and only["layout"]["yaxis"]["ticksuffix"] == "" and only["controls"]["range"] == "3M",
          "price only: one axis in points, unknown range -> 3M")

    # ---- stale and errors
    st = cds.table(today=dt.date(2026, 12, 1))
    check(st.get("stale") == "Latest index CDS prints are from 2026-11-18", f"stale: {st.get('stale')}")
    DAYS.clear()
    for f in c.CACHE_DIR.glob(cds.day_name("*") + ".json"):
        f.unlink()
    c.reset_runtime_caches()
    try:
        c.dispatch("cds_indices", {})
        check(False, "no prints raises")
    except c.CompositeError:
        check(True, "no prints raises")
    check(c.static_sources("cds_history", {})[0]["name"] == "CFTC", "static sources")
finally:
    c.api, cds._path, cds._days = saved
    tmp.unlink(missing_ok=True)

done("test_cds")
