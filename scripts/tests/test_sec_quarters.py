"""SEC cash-flow quarters (audit A4): 10-Q cash-flow items are filed year-to-date, so standalone quarters are differences
along each fiscal year's chain (Q1, H1, 9M, FY). The provider grouped the chain by the filing's `fy`, which mixes
fiscal years and drops most quarters; the runtime patch groups by the period start. The Compare chart never draws a
line across a missing quarter. Expected values are worked out by hand."""

import importlib.util

from _common import ROOT, check, done

import composite as c

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

try:
    from openbb_sec.utils import frames
except ImportError:
    frames = None
    print("test_sec_quarters: openbb_sec not installed here, provider patch checks skipped")


def entry(start, end, val, fy, fp, filed, sym="X"):
    return frames._enrich_entry({"symbol": sym, "unit": "USD", "start": start, "end": end, "val": val, "fy": fy, "fp": fp,
                                 "form": "10-Q" if fp != "FY" else "10-K", "filed": filed, "accn": filed.replace("-", "")})


if frames is not None:
    # fiscal year starting 2024-06-01 (an Oracle-like May year end); SEC's `fy` is the filing's year, not the period's
    fy1 = [entry("2024-06-01", "2024-08-31", 100.0, 2026, "Q1", "2025-09-10"),
           entry("2024-06-01", "2024-11-30", 250.0, 2026, "Q2", "2025-12-10"),
           entry("2024-06-01", "2025-02-28", 450.0, 2026, "Q3", "2026-03-10"),
           entry("2024-06-01", "2025-05-31", 700.0, 2027, "FY", "2026-06-20")]
    # the next fiscal year carries the same fy values: chains must not mix
    fy2 = [entry("2025-06-01", "2025-08-31", 120.0, 2026, "Q1", "2025-09-10"),
           entry("2025-06-01", "2025-11-30", 300.0, 2026, "Q2", "2025-12-10"),
           entry("2025-06-01", "2026-02-28", 500.0, 2026, "Q3", "2026-03-10"),
           entry("2025-06-01", "2026-05-31", 800.0, 2026, "FY", "2026-06-20")]
    check([e["span"] for e in fy1] == ["quarter", "h1", "nine_month", "annual"], "fixture: spans classified as the provider does")
    got = {d["end"]: d for d in rb.sec_standalone_quarters(fy1 + fy2, frames)}
    check({e: d["val"] for e, d in got.items()} == {"2024-11-30": 150.0, "2025-02-28": 200.0, "2025-05-31": 250.0,
                                                   "2025-11-30": 180.0, "2026-02-28": 200.0, "2026-05-31": 300.0},
          "quarters: 250-100, 450-250, 700-450 and 300-120, 500-300, 800-500")
    check(all(d["span"] == "quarter" and d["derived"] for d in got.values()), "derived rows are standalone quarters, marked derived")
    check(got["2024-11-30"]["start"] == "2024-09-01" and got["2025-05-31"]["start"] == "2025-03-01", "a derived quarter starts the day after the previous link")
    check(got["2025-05-31"]["calendar_period"] == "Q2" and got["2025-05-31"]["calendar_year"] == 2025, "May quarter end sits on calendar Q2 2025 (nearest quarter end)")
    check(got["2025-05-31"]["fp"] == "Q4" and got["2024-11-30"]["fp"] == "Q2", "FY - 9M is labelled fiscal Q4, not FY")
    old = frames._derive_standalone_quarters(fy1 + fy2)
    check(len(old) < 6, f"the provider's own grouping loses quarters on this fixture ({len(old)} of 6)")

    # a quarter already reported as three months is not derived again; a broken chain derives nothing across the gap
    direct = entry("2024-09-01", "2024-11-30", 150.0, 2026, "Q2", "2025-12-10")
    ends = sorted(d["end"] for d in rb.sec_standalone_quarters(fy1 + [direct], frames))
    check(ends == ["2025-02-28", "2025-05-31"], "reported Q2 kept as is, not duplicated by a derived one")
    gap = [fy1[0], fy1[2], fy1[3]]                     # Q1 then 9M: no H1
    got = {d["end"]: d["val"] for d in rb.sec_standalone_quarters(gap, frames)}
    check(got == {"2025-05-31": 250.0}, "Q1 -> 9M spans six months: nothing derived; FY - 9M still gives Q4")
    other = [entry("2024-06-01", "2024-08-31", 10.0, 2026, "Q1", "2025-09-10", "Y"), entry("2024-06-01", "2024-11-30", 25.0, 2026, "Q2", "2025-12-10", "Y")]
    got = {(d["symbol"], d["end"]): d["val"] for d in rb.sec_standalone_quarters(fy1[:2] + other, frames)}
    check(got == {("X", "2024-11-30"): 150.0, ("Y", "2024-11-30"): 15.0}, "two companies with the same dates stay apart")
    check(rb.sec_standalone_quarters([], frames) == [], "no records -> nothing")

    # the install replaces the provider function and says so; the legacy backend (no such function) is left alone
    saved = frames._derive_standalone_quarters
    try:
        applied = []
        rb.install_sec_quarters_fix(applied)
        check(len(applied) == 1 and "year-to-date" in applied[0], "patch reports itself applied")
        check({d["end"] for d in frames._derive_standalone_quarters(fy1 + fy2)} >= {"2024-11-30", "2025-11-30"}, "the provider now uses the fixed derivation")
    finally:
        frames._derive_standalone_quarters = saved

# ---- Compare: a missing quarter breaks the line and is named; complete companies are untouched
saved = c._facts
try:
    c._cache.clear()
    c._facts = lambda syms, fact: {
        "AAA": {"quarter": [["2024-03-31", 5e9], ["2025-03-31", 6e9], ["2025-06-30", 7e9]], "annual": [], "instant": []},
        "BBB": {"quarter": [["2024-12-31", 1e9], ["2025-03-31", 2e9], ["2025-06-30", 3e9]], "annual": [], "instant": []}}
    out = c.compare(symbols="AAA,BBB", fact="capex", period="quarter", since="2023")
    a, b = out["data"]
    check(a["y"] == [5.0, None, 6.0, 7.0] and a["x"][1] == "2024-04-01", "AAA: a year without quarters is a break in the line, not a slope")
    check(b["y"] == [1.0, 2.0, 3.0] and None not in b["x"], "BBB: complete quarters drawn as before")
    check(a.get("connectgaps") is False, "gaps are not bridged by the chart")
    notes = " ".join(n["text"] for n in out["layout"].get("annotations", []))
    check("Missing quarters" in notes and "AAA" in notes and "BBB" not in notes, "the chart names the company with missing quarters")
    check(all(None not in row.values() for row in out["results"]) and len(out["results"]) == 4, "the table lists only real quarters")

    q = ["2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31", "2024-03-31", "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30"]
    c._facts = lambda syms, fact: {"CCC": {"quarter": [[d, 1e9] for d in q], "annual": [], "instant": []}}
    t = c.compare(symbols="CCC", fact="capex", period="ttm", since="2023")["data"][0]
    check(t["y"] == [4.0, 4.0, None, 4.0] and t["x"][0] == "2023-12-31" and t["x"][-1] == "2025-06-30",
          "TTM: only windows of four consecutive quarters, and the missing 2024-06 quarter breaks the line")
finally:
    c._facts = saved
    c._cache.clear()

done("test_sec_quarters")
