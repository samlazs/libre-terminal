"""Stage 3 item 4: bond arithmetic (terminal/bond_math.py), TRACE enrichment, pricer and debt maturity wall, by hand."""

import datetime as dt

from _common import check, done

import bond_math as bm
import bonds
import composite as c

D = dt.date
check(bm.d360(D(2026, 1, 31), D(2026, 3, 1)) == 31 and bm.d360(D(2026, 6, 15), D(2026, 8, 15)) == 60, "30/360 day counts")
check(abs(bm.price(0.05, 5.0, D(2026, 8, 15), D(2031, 6, 15))[1] - 2.5 * 60 / 180) < 1e-12, "accrued 2 months into a 5% coupon = 0.8333")
y = bm.ytm(100.0, 5.0, D(2026, 6, 15), D(2031, 6, 15))
check(abs(y - 0.05) < 1e-9, "a 5% bond at par on a coupon date yields 5%")
# 2-year 5% par bond: Macaulay 1.92801 years, modified 1.88099, convexity 4.53114 (closed forms, semiannual), DV01 0.0188099
k = bm.risk(0.05, 5.0, D(2026, 6, 15), D(2028, 6, 15))
check(abs(k["mod_duration"] - 1.88099) < 1e-4 and abs(k["convexity"] - 4.53114) < 1e-2 and abs(k["dv01"] - 0.0188099) < 1e-6, "duration, convexity, DV01: " + str(k))
check(bm.isin_us("037833100") == "US0378331005", "ISIN from CUSIP (Apple common: US0378331005)")
check(bm.benchmark_tenor(6.1) == 7 and bm.benchmark_tenor(1.2) == 2 and bm.benchmark_tenor(28) == 30, "nearest on-the-run tenor")

# enrichment: G-spread = our yield - par curve at the remaining maturity (2Y 4.0%, 10Y 4.5% -> 5Y 4.1875%)
rows = [{"coupon": 5.0, "price": 100.0, "maturity": "2031-06-15", "last_trade": "2026-06-15", "coupon_type": "Fixed Plain Vanilla", "callable": "yes, from 2031-03-15"},
        {"coupon": 5.0, "price": 99.0, "maturity": "2031-06-15", "last_trade": "2026-06-15", "coupon_type": "Floating", "callable": "no"}]
bonds.enrich(rows, {"2026-06-12": [(2.0, 4.0), (10.0, 4.5)]})
check(rows[0]["ytm"] == 5.0 and rows[0]["g_spread"] == 81.3 and rows[0]["benchmark"] == "5Y" and rows[0]["risk_basis"] == "to maturity; call ignored",
      "G-spread 5.000 - 4.1875 = 81.3 bp (curve from the last day before the trade); callable flagged: " + str(rows[0]))
check(rows[1]["ytm"] is None and rows[1]["risk_basis"] == "not fixed rate: not computed", "floating coupon: no yield math")
r2 = [dict(rows[0], last_trade="2026-07-15")]
bonds.enrich(r2, {"2026-06-12": [(2.0, 4.0)]})
check(r2[0]["g_spread"] is None and r2[0]["ytm"] is not None, "no par curve within 7 days of the trade: no G-spread")

# pricer: from a typed yield, the price is recomputed; scenarios move the price the right way
saved = bonds._prices
try:
    bonds._prices = lambda **kw: [{"issuer_name": "TEST CO", "coupon_rate": 5.0, "maturity_date": "2031-06-15", "coupon_type": "Fixed Plain Vanilla",
                                   "price": 100.0, "last_trade_date": "2026-06-10", "is_callable": False}]
    md = bonds.pricer(cusip="123456AB1", today=D(2026, 6, 15))["content"]
    check("| Clean price | 100.000 |" in md and "| Yield to maturity | 5.000% |" in md and "| Accrued interest | 0.0000 |" in md, "pricer from the last sale")
    up = [line for line in md.splitlines() if line.startswith("| +100 bp")][0]
    check("-" in up.split("|")[4] and "6.000%" in up, "+100 bp: price down: " + up)
    md2 = bonds.pricer(cusip="123456AB1", ytm="5", today=D(2026, 6, 15))["content"]
    check("| Clean price | 100.000 |" in md2, "typed yield 5% -> price 100")
finally:
    bonds._prices = saved

# maturity wall: latest 10-K period of the first-year tag; a tag missing that year -> blank bar
facts = {bonds.WALL[0][0]: [{"form": "10-K", "value": 10e9, "period_ending": "2025-12-31", "reported_date": "2026-02-09", "cik": 732717},
                            {"form": "10-K", "value": 8e9, "period_ending": "2024-12-31", "reported_date": "2025-02-10"}],
         bonds.WALL[5][0]: [{"form": "10-K", "value": 100e9, "period_ending": "2025-12-31", "reported_date": "2026-02-09"}]}
end, filed, cik, pts = bonds.wall_points(facts)
check(end == "2025-12-31" and filed == "2026-02-09" and cik == 732717 and pts[0] == ("Year 1", 10e9) and pts[1] == ("Year 2", None) and pts[5][1] == 100e9,
      "maturity wall from the latest 10-K")
check(all(k in c.ROUTES for k in ("bond_pricer", "debt_maturity_wall")) and c.static_sources("bond_lookup")[-1]["name"] == "FRED", "routes and sources")

check(bm.isin_us("12345*AB1") is None and bm.isin_us("1234@#AB1") is None, "private-placement CUSIP characters give no ISIN, never an error")

done("test_bond_math")
