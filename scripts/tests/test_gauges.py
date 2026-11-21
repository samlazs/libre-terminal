"""HANDOFF P3-6 buried gauges: each is a curated card (not only a library choice) where a professional looks, its plotted
fields exist in the rows the source sends, its title states the unit, and it replaced a card instead of lengthening the tab."""

import pathlib
import sys

from _common import check, done

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import build_dashboards as B  # noqa: E402

# Fields the endpoints answered with when probed live on 2026-11-19 (HANDOFF P3-6), and the unit each title must state
GAUGES = {
    "federal_reserve/ny/corporate_bond_distress": ("Credit", "Lending & Stress", {"date", "market", "investment_grade", "high_yield"}, "0-1"),
    "federal_reserve/ny/supply_chain_pressure": ("Economy", "Growth & Activity", {"date", "gscpi"}, "standard deviations"),
    "federal_reserve/sf/term_premium": ("Rates", "Policy Expectations", {"date", "yield_zero_coupon", "expected_short_rate", "term_premium"}, "%"),
    "federal_reserve/sf/proxy_funds_rate": ("Rates", "Policy Expectations", {"date", "effective_funds_rate", "proxy_funds_rate"}, "%"),
    "federal_reserve/cleveland/inflation_nowcast": ("Economy", "Inflation", {"date", "CPI Inflation", "Core CPI Inflation", "Core PCE Inflation", "PCE Inflation"}, "%"),
    "yfinance/estimates/eps_revisions": ("Company", "Estimates", {"symbol", "period", "up_last_7_days", "up_last_30_days", "down_last_7_days", "down_last_30_days"}, "7 and 30 days"),
}


def placed(ep):
    """[(dashboard name, tab name, spec, row)] where ep is a card in a tab's rows."""
    out = []
    for d in B.DASHBOARDS:
        for t in d["tabs"]:
            for row in t["rows"]:
                out += [(d["name"], t["name"], s, row) for s in row if isinstance(s, dict) and s.get("ep") == ep]
    return out


for ep, (dash, tab, fields, unit) in GAUGES.items():
    where = placed(ep)
    hit = [w for w in where if w[1] == tab and dash.lower() in w[0].lower()]
    check(bool(hit), f"{ep}: a card on {dash} > {tab} (found: {[(w[0], w[1]) for w in where]})")
    if not hit:
        continue
    spec, row = hit[0][2], hit[0][3]
    plot = spec.get("plot") or {}
    used = set(plot.get("ys") or []) | ({plot["x"]} if plot.get("x") else set())
    check(used <= fields, f"{ep}: plotted fields exist in the source's rows ({sorted(used - fields)} missing)")
    check(unit in (spec.get("title") or ""), f"{ep}: title states the unit '{unit}'")
    check(len(row) == 2, f"{ep}: the row still holds two cards (replaced, not added)")

check(placed("cftc/cot_index") and any(t == "Positioning (COT)" for _, t, _, _ in placed("cftc/cot_index")), "COT index already first on Futures > Positioning")
inf = [t for d in B.DASHBOARDS for t in d["tabs"] if t["name"] == "Inflation"][0]
libs = [s for r in inf["rows"] for s in r if isinstance(s, dict) and "trimmed_mean_pce" in str(s)]
check(bool(libs) and "trimmed_mean_pce" in str(libs[0].get("params") or libs[0]), "Dallas trimmed-mean PCE opens first in the underlying-inflation library")
check(any("economy/pce" in str(s) for r in inf["rows"] for s in r), "PCE table still reachable on Inflation (library choice)")
pe = [t for d in B.DASHBOARDS for t in d["tabs"] if t["name"] == "Policy Expectations"][0]
check(not any(a in ("federal_reserve/sf/term_premium", "federal_reserve/sf/proxy_funds_rate") for a in pe["auto"]), "promoted gauges left the tab's automatic list")

done("test_gauges")
