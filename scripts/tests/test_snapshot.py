"""Audit C9/C10. Snapshot (DES) text: audit C9 - price target currency stated, no upside across currencies; 52-week range basis declared."""

from pathlib import Path

from _common import check, done

import composite as c
from composite.equity import lookup as _lookup

_lookup.PREFETCH_ESTIMATES = False
DATA = {}
c._first = lambda path, sym, **kw: DATA.get(path, {})
c._usd_rates = lambda ccys: {}
c.cached_complete = lambda key, ttl, fn: fn()


def run(price_ccy, tgt_ccy, tgt=120.0, last=100.0, industry=None):
    DATA.clear()
    DATA.update({"equity/profile": {"name": "Test Co", "currency": price_ccy, "industry_category": industry},
                 "equity/fundamental/metrics": {"market_cap": 1e9, "currency": price_ccy, "gross_margin": 0.0, "debt_to_equity": 250.0},
                 "equity/price/quote": {"last_price": last, "prev_close": last, "currency": price_ccy, "year_low": 80.0, "year_high": 130.0},
                 "equity/estimates/consensus": {"target_consensus": tgt, "target_low": 90.0, "target_high": 150.0, "current_price": last, "currency": tgt_ccy},
                 "yfinance/company_calendar": {}})
    return c.snapshot(symbol="TST")["content"]


same = run("USD", "USD")
check("| Price target (mean, low – high) | 120.00 USD (+20%) (90.00 – 150.00) |" in same, "same currency: currency named, upside 120/100-1 = +20%")
check("| 52-week range (intraday low – high) | 80.00 – 130.00 |" in same.replace("**", ""), "52-week basis declared")
cross = run("EUR", "USD")
check("120.00 USD (target in USD, price in EUR: no upside computed)" in cross and "+20%" not in cross, "currencies differ: no upside")
none = run("JPY", "")
check("| Price target (mean, low – high) | 120.00 (target currency not stated: no upside computed) (90.00 – 150.00) |" in none, "currency not stated: no upside (never inferred)")
# Stage 3 T1-4: a bank's EV, EBITDA multiples, gross margin and debt/equity carry no meaning: blank with a note
bank = run("USD", "USD", industry="Banks - Diversified")
check(all(f"| {k} | n/m for banks |" in bank for k in ("Gross margin", "Enterprise value", "EV / EBITDA", "EV / Sales", "Debt / equity")), "bank: meaningless rows say n/m")
check("| Gross margin | 0.0% |" in same and "n/m for banks" not in same, "non-bank: gross margin shown as sent")
# Stage 3 8a-ext: a U.S. bank holding company gets its lead bank's latest FDIC quarter; a ratio not in that quarter is a dash
import banks as _banks
_saved_qd = _banks.quality_data
try:
    _banks.quality_data = lambda sym: {"bank": "Lead Bank, N.A.", "cert": 628, "series": {
        "NIMY": [["2026-03-31", 2.9], ["2026-06-30", 3.104]], "RBC1AAJ": [["2026-06-30", 7.4947]], "NTLNLSR": [["2026-03-31", 0.61]]}}
    sr = _banks.snapshot_rows("tst")
    check(sr["title"] == "Bank (lead bank, FDIC, Q2 2026)" and sr["rows"] == [("NIM", "3.10%"), ("Tier 1 leverage", "7.49%"), ("Net charge-off rate", "–")],
          "lead-bank rows from the latest quarter (2026-06-30 = Q2); charge-offs only in Q1 -> dash: " + str(sr))
    DATA_US = run("USD", "USD", industry="Banks - Diversified")
    check("| Bank (lead bank, FDIC, Q2 2026) | |" not in DATA_US, "no country stated: no FDIC rows")
    _orig_first = c._first
    c._first = lambda path, sym, **kw: dict(DATA.get(path, {}), hq_country="United States") if path == "equity/profile" else DATA.get(path, {})
    us = run("USD", "USD", industry="Banks - Diversified")
    check("| Bank (lead bank, FDIC, Q2 2026) | |" in us and "| NIM | 3.10% |" in us and "Lead Bank, N.A. (FDIC cert 628)" in us, "U.S. bank: FDIC rows in the snapshot")
    nb = run("USD", "USD")
    check("FDIC" not in nb, "non-bank: no FDIC rows")
    c._first = _orig_first
finally:
    _banks.quality_data = _saved_qd
check(c.is_bank("Banks—Regional") and not c.is_bank("Capital Markets") and not c.is_bank("Financial Data & Stock Exchanges") and not c.is_bank(None),
      "bank detection: Yahoo 'Banks' industries only (not exchanges, brokers, asset managers)")
check([_lookup.cik_of(q) for q in ("0000320193", "CIK 320193", "cik:320193", "7203", "320193", "AAPL", "00003201931")] ==
      ["0000320193", "0000320193", "0000320193", None, None, None, None], "SEC CIK: ten digits, or CIK with up to ten; short bare numbers are tickers")
# audit C10: the basis of the PortWatch and GPR dates is declared on the cards
import xwidgets
_ws = xwidgets.widgets()
_ws = _ws.values() if isinstance(_ws, dict) else _ws
tip = {w["widgetId"]: {col["field"]: col.get("headerTooltip", "") for col in w["data"]["table"].get("columnsDefs", [])} for w in _ws}
check("weekly release" in tip["x_chokepoints"]["as_of"] and "separate series" in tip["x_gpr_table"]["as_of"], "PortWatch as-of and GPR month declared")
from composite.geo import shipping as _shipping
check("metric tons" in Path(_shipping.__file__).read_text(encoding="utf-8").split("PORTWATCH_FIELDS = ")[0][-400:], "capacity_tanker unit declared")
done("test_snapshot")
