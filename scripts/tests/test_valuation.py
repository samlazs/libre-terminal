"""Stage 3 8d (terminal/valuation.py): no-lookahead dates, TTM, split restatement, bands and DuPont/ROIC by hand."""

import datetime as dt

from _common import check, done

import composite as c
import valuation as v

D = dt.date
# a figure counts from its filing date, or from the form's deadline when a later filing carries it as a comparative
check(v.available("2026-03-31", "2026-05-01", "10-Q", "quarter") == D(2026, 5, 1), "filed before the deadline: filing date")
check(v.available("2025-03-31", "2026-05-01", "10-Q", "quarter") == D(2025, 5, 15), "comparative re-filed a year later: 45-day deadline")
check(v.available("2025-12-31", "", "10-K", "annual") == D(2026, 3, 31) and v.available("2025-12-31", None, "20-F", "annual") == D(2026, 4, 30), "10-K 90 days, 20-F 120 days")

rows = [{"span": "quarter", "end": "2025-03-31", "begin": "2025-01-01", "value": 10.0, "filed": "2025-04-30", "form": "10-Q"},
        {"span": "quarter", "end": "2025-06-30", "begin": "2025-04-01", "value": 20.0, "filed": "2025-07-30", "form": "10-Q"},
        {"span": "quarter", "end": "2025-09-30", "begin": "2025-07-01", "value": 30.0, "filed": "2025-10-30", "form": "10-Q"},
        {"span": "annual", "end": "2025-12-31", "begin": "2025-01-01", "value": 100.0, "filed": "2026-02-20", "form": "10-K"}]
q = v.quarters(rows)
check([x[1] for x in q] == [10.0, 20.0, 30.0, 40.0] and q[-1][2] == D(2026, 2, 20), "Q4 = 100 - 10 - 20 - 30 = 40, known when the 10-K is filed")
t = v.ttm(q)
check(t == [("2025-12-31", 100.0, D(2026, 2, 20))], "TTM = four quarters, available with the last of them: " + str(t))
check(v.split_factor([("2024-06-10", 10.0)], "2024-05-29") == 10.0 and v.split_factor([("2024-06-10", 10.0)], "2024-08-28") == 1.0,
      "a count filed before a 10:1 split is restated x10; one filed after is already on the new basis")
check(abs(v.quantile([1, 2, 3, 4, 5], 0.1) - 1.4) < 1e-12 and v.quantile([1, 2, 3, 4, 5], 0.5) == 3 and v.percentile_of([1, 2, 3, 4, 5], 4) == 80.0,
      "10th percentile of 1..5 = 1.4 (linear), median 3, 4 sits at the 80th percentile")

# multiples: close 50 x 10 shares = 500 market cap; TTM net income 100 -> P/E 5; nothing known before the filing date
sh = v.AsOf([("2026-03-31", 10.0, D(2026, 5, 1))])
f = {"net_income": v.AsOf([("2026-03-31", 100.0, D(2026, 5, 1))]), "operating_income": v.AsOf([("2026-03-31", 40.0, D(2026, 5, 1))]),
     "da": v.AsOf([("2026-03-31", 10.0, D(2026, 5, 1))]), "debt": v.AsOf([("2026-03-31", 30.0, D(2026, 5, 1))]),
     "cash": v.AsOf([("2026-03-31", 10.0, D(2026, 5, 1))]), "equity": v.AsOf([("2026-03-31", -5.0, D(2026, 5, 1))])}
closes = [("2026-04-20", 48.0), ("2026-05-20", 50.0)]
check(v.multiples(closes, sh, f, "pe") == [("2026-04-20", None), ("2026-05-20", 5.0)], "P/E 500/100 = 5x, blank before the figures were filed")
check(abs(v.multiples(closes, sh, f, "ev_ebitda")[1][1] - 10.4) < 1e-12, "EV/EBITDA (500 + 30 - 10) / (40 + 10) = 10.4x")
check(v.multiples(closes, sh, f, "pb")[1][1] is None, "negative book value: no P/B (a gap, never negative)")
# a restated older period filed later never replaces a newer period already known
a = v.AsOf([("2026-03-31", 1.0, D(2026, 5, 1)), ("2025-12-31", 2.0, D(2026, 6, 1))])
check(a.at(D(2026, 6, 15))[0] == "2026-03-31", "AsOf keeps the latest period end among the known figures")

# DuPont and ROIC: 20/200 = 10% x 200/400 = 0.5 x 400/100 = 4 -> ROE 20%; tax 10/25 = 40% clipped to 35%;
# NOPAT 30 x 0.65 = 19.5; invested capital 50 + 100 - 30 = 120; ROIC 16.25%
fa = {"net_income": {"2025-12-31": 20.0}, "revenue": {"2025-12-31": 200.0}, "assets": {"2025-12-31": 400.0}, "equity": {"2025-12-31": 100.0},
      "operating_income": {"2025-12-31": 30.0}, "pretax": {"2025-12-31": 25.0}, "tax": {"2025-12-31": 10.0}, "debt": {"2025-12-31": 50.0}, "cash": {"2025-12-31": 30.0}}
r = v.dupont_rows(fa)[0]
check((r["net_margin"], r["asset_turnover"], r["equity_multiplier"], r["roe"]) == (10.0, 0.5, 4.0, 20.0), "DuPont parts multiply to ROE: " + str(r))
check((r["tax_rate"], r["nopat"], r["invested_capital"], r["roic"]) == (35.0, 19.5, 120.0, 16.25), "ROIC with the tax rate clipped at 35%")
rb = v.dupont_rows(fa, bank=True)[0]
check(rb["roe"] == 20.0 and rb["roic"] is None and rb["nopat"] is None, "bank: DuPont only")
fa["pretax"] = {"2025-12-31": -5.0}
check(v.dupont_rows(fa)[0]["tax_rate"] == 0.0, "pre-tax loss: tax rate 0")

# the chart: band and percentile of the window; EV/EBITDA refused for a bank
saved = v.sec_figures, v._profile, c.history
try:
    v._profile = lambda sym: {"industry": "Consumer Electronics", "currency": "USD"}
    v.sec_figures = lambda sym: {"shares": [("2020-12-31", 1.0, D(2021, 1, 1))], "net_income": [("2020-12-31", 1.0, D(2021, 1, 1))]}
    c.history = lambda syms, start: {"TST": [(f"2026-01-{i:02d}", float(i)) for i in range(1, 11)]}
    out = v.bands(symbol="tst", measure="pe", today=D(2026, 1, 11))
    s = out["summary"]
    check(s["current"] == 10.0 and s["median"] == 5.5 and abs(s["p10"] - 1.9) < 1e-9 and s["percentile"] == 100.0, "P/E 1..10: median 5.5, p10 1.9, today at the 100th percentile: " + str(s))
    check(out["data"][-1]["name"].startswith("P/E (TTM)  10.0x (100th percentile)") and [x["name"] for x in out["sources"]] == ["SEC", "Yahoo Finance"], "legend and sources")
    v._profile = lambda sym: {"industry": "Banks - Diversified", "currency": "USD"}
    try:
        v.bands(symbol="tst", measure="ev_ebitda", today=D(2026, 1, 11))
        check(False, "bank EV/EBITDA must be refused")
    except c.CompositeError as e:
        check("no meaning for a bank" in str(e), "bank: EV/EBITDA refused with the reason")
finally:
    v.sec_figures, v._profile, c.history = saved

check("valuation_bands" in c.ROUTES and "roic_dupont" in c.ROUTES and c.static_sources("valuation_bands", {"symbol": "AAPL"})[0]["name"] == "SEC"
      and c.static_sources("roic_dupont", {"symbol": "SAP.DE"})[0]["name"] == "Yahoo Finance", "routes and sources by market")

check([v.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 100)] == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "100th"], "ordinal suffixes")
_zero = v.AsOf([("0001-01-01", 0.0, dt.date.min)])
check(abs(v.multiples(closes, sh, dict(f, debt=_zero), "ev_ebitda")[1][1] - 9.8) < 1e-12, "EV/EBITDA with no debt: (500 - 10) / 50 = 9.8x")

done("test_valuation")
