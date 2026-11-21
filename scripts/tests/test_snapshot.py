"""Audit C9/C10. Snapshot (DES) text: audit C9 - price target currency stated, no upside across currencies; 52-week range basis declared."""

from pathlib import Path

from _common import check, done

import composite as c

DATA = {}
c._first = lambda path, sym, **kw: DATA.get(path, {})
c._usd_rates = lambda ccys: {}
c.cached_complete = lambda key, ttl, fn: fn()


def run(price_ccy, tgt_ccy, tgt=120.0, last=100.0):
    DATA.clear()
    DATA.update({"equity/profile": {"name": "Test Co", "currency": price_ccy},
                 "equity/fundamental/metrics": {"market_cap": 1e9, "currency": price_ccy},
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
# audit C10: the basis of the PortWatch and GPR dates is declared on the cards
import xwidgets
_ws = xwidgets.widgets()
_ws = _ws.values() if isinstance(_ws, dict) else _ws
tip = {w["widgetId"]: {col["field"]: col.get("headerTooltip", "") for col in w["data"]["table"].get("columnsDefs", [])} for w in _ws}
check("weekly release" in tip["x_chokepoints"]["as_of"] and "separate series" in tip["x_gpr_table"]["as_of"], "PortWatch as-of and GPR month declared")
from composite.geo import shipping as _shipping
check("metric tons" in Path(_shipping.__file__).read_text(encoding="utf-8").split("PORTWATCH_FIELDS = ")[0][-400:], "capacity_tanker unit declared")
done("test_snapshot")
