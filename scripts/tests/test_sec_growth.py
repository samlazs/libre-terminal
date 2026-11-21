"""SEC growth (owner 2026-11-18, Growth & Ratios, period quarterly): openbb_sec gave q/q growth multiplied by 100 while
y/y stayed a fraction, so the page (which multiplies growth fractions by 100) showed NVDA net income +2077 % q/q. The
runtime patch keeps every growth figure a fraction, compares a quarter only with the one just before, builds TTM from
four consecutive quarters, and answers period=ttm with TTM growth. Expected values are worked out by hand."""

import importlib.util

from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)


def q(end, val, fy, fp, tag="net_income", ptype="duration"):
    return {"period_ending": end, "fiscal_year": fy, "fiscal_period": fp, "calendar_year": None, "calendar_period": None, "tag": tag,
            "label": tag, "description": "", "parent": None, "sequence": 1, "factor": "+", "balance": "", "unit": "monetary",
            "period_type": ptype, "currency": "USD", "value": val}


def by_end(recs):
    return {r["period_ending"]: r["value"] for r in recs}


# NVDA net income ($M, 10-Q): Q2 FY25 16,599; Q1 FY26 18,775; Q2 FY26 26,422; Q3 FY26 31,910
nvda = [q("2024-07-28", 16599.0, 2025, "Q2"), q("2025-04-27", 18775.0, 2026, "Q1"), q("2025-07-27", 26422.0, 2026, "Q2"),
        q("2025-10-26", 31910.0, 2026, "Q3")]
pop = by_end(rb.sec_pct_change(nvda, "pop"))
# 26422 / 18775 = 1.407297 -> 0.407297; 31910 / 26422 = 1.207706 -> 0.207706 (26422 * 1.2 = 31706.4; 203.6 / 26422 = 0.0077057)
check(abs(pop["2025-07-27"] - 0.407297) < 1e-6 and abs(pop["2025-10-26"] - 0.207706) < 1e-6, "q/q is a fraction (+20.8 %, not 20.77)")
check("2025-04-27" not in pop, "no q/q across a 9-month gap (Q2 FY25 -> Q1 FY26)")
yoy = by_end(rb.sec_pct_change(nvda, "yoy"))
# 26422 / 16599 = 1.591783 (16599 * 1.59 = 26392.41; 29.59 / 16599 = 0.0017826)
check(set(yoy) == {"2025-07-27"} and abs(yoy["2025-07-27"] - 0.591783) < 1e-6, "y/y: same fiscal quarter a year earlier, a fraction")
src = rb.sec_pct_change(nvda, "pop")[-1]["source"]
check(src.endswith("= 20.77%"), "source text shows the percent of the fraction")

# a fiscal label a year back that is not a year back in time (mislabelled filing): no y/y
odd = [q("2025-01-26", 100.0, 2025, "Q2"), q("2025-07-27", 150.0, 2026, "Q2")]
check(rb.sec_pct_change(odd, "yoy") == [], "y/y prior must end 340-390 days earlier")
check(rb.sec_pct_change([q("2025-04-27", 0.0, 2026, "Q1"), q("2025-07-27", 5.0, 2026, "Q2")], "pop") == [], "zero prior: no figure")

# TTM: eight 91-day quarters 10..80; TTM at q4 = 100, q5 = 140, q6 = 180, q7 = 220, q8 = 260
ends = ["2024-01-01", "2024-04-01", "2024-07-01", "2024-09-30", "2024-12-30", "2025-03-31", "2025-06-30", "2025-09-29"]
eight = [q(e, 10.0 * (i + 1), 2024 + i // 4, f"Q{i % 4 + 1}") for i, e in enumerate(ends)]
ttm = rb.sec_ttm(eight, set())
check([r["value"] for r in ttm] == [100.0, 140.0, 180.0, 220.0, 260.0] and all(r["fiscal_period"] == "TTM" for r in ttm), "TTM sums four quarters")
g = by_end(rb.sec_pct_change(ttm, "ttm"))
# only q8's TTM has one a year earlier (q4, 364 days): 260 / 100 - 1 = 1.6
check(g == {"2025-09-29": 1.6}, "TTM growth: TTM against the TTM ending a year earlier")
gap = [eight[0], eight[1], eight[3], eight[4], eight[5]]          # q3 missing
check([r["period_ending"] for r in rb.sec_ttm(gap, set())] == [], "no TTM over a missing quarter")
inst = rb.sec_ttm([q(e, v, 2024, "Q1", tag="total_assets", ptype="instant") for e, v in zip(ends[:4], (10.0, 20.0, 30.0, 40.0))], set())
check(inst and inst[0]["value"] == 25.0, "instants are averaged, not summed")

try:
    from openbb_sec.utils import company_facts as cf
except ImportError:
    cf = None
    print("test_sec_growth: openbb_sec not installed here, install checks skipped")
if cf is not None:
    applied = []
    rb.install_sec_growth_fix(applied)
    check(applied and cf._compute_pct_change([q("2025-04-27", 4.0, 2026, "Q1"), q("2025-07-27", 5.0, 2026, "Q2")], "pop")[0]["value"] == 0.25,
          "installed: the provider's q/q goes through the fraction version")
    from openbb_sec.models.income_statement_growth import SecIncomeStatementGrowthFetcher as F
    check(F.extract_data is F.aextract_data and F.extract_data.__name__ == "aextract_data" and F.extract_data.__module__ == rb.__name__,
          "installed: fetch_data's extract_data is the wrapper that tells the resolver period=ttm")
done("test_sec_growth")
