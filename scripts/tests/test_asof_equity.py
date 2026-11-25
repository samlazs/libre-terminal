"""P3-03 for the equity views: each answers `as_of` = the newest data point it actually used. Real views through
C.dispatch (strict contract mode), upstream faked at the helper each view calls; every input is hand-built with distinct
dates and the expected date is a typed literal. Routes: snapshot, compare, earnings, earnings_history, earnings_reports,
valuation_bands, roic_dupont, sp500_multiples, news, debt_maturity_wall (two inputs each; US and non-US where the route
takes a company the data source covers)."""

import datetime as dt

from _common import check, check_as_of, covered, done

import composite as C
import bonds
import multpl
import news
import valuation as v
from composite.core import contract as K
from composite.equity import lookup as _lookup

_lookup.PREFETCH_ESTIMATES = False
D = dt.date
UTC = dt.timezone.utc


def fresh():
    C._cache.clear()
    C._backoff.clear()


# ---------------------------------------------------------------- snapshot (Yahoo quote's own timestamp)
def snap_data(ccy, quote):
    return {"equity/profile": {"name": "Test Co", "currency": ccy, "industry_category": "Software"},
            "equity/fundamental/metrics": {"market_cap": 1e9, "currency": ccy},
            "equity/price/quote": dict({"currency": ccy, "year_low": 80.0, "year_high": 130.0}, **quote),
            "equity/estimates/consensus": {"target_consensus": 120.0, "current_price": 100.0, "currency": ccy},
            "yfinance/company_calendar": {}}


saved_snap = C._first, C._usd_rates, C.cached_complete
try:
    SNAP = {}
    C._first = lambda path, sym, **kw: SNAP.get(path, {})
    C._usd_rates = lambda ccys: {}
    C.cached_complete = lambda key, ttl, fn: fn()
    fresh()
    SNAP = snap_data("USD", {"last_price": 100.0, "prev_close": 99.0, "date": "2026-11-23", "timestamp": "2026-11-24T09:30:00-05:00",
                             "last_timestamp": "2026-11-24T16:00:00-05:00"})
    check_as_of("snapshot", C.dispatch("snapshot", {"symbol": "AAPL"}), "2026-11-24T16:00:00-05:00", "AAPL: newest of the quote's own times")
    covered("snapshot", "us", C.dispatch("snapshot", {"symbol": "AAPL"}))
    fresh()
    SNAP = snap_data("EUR", {"last_price": 100.0, "prev_close": 99.0, "timestamp": "2026-11-20T17:35:00+01:00", "date": "2026-11-19"})
    check_as_of("snapshot", C.dispatch("snapshot", {"symbol": "SAP.DE"}), "2026-11-20T17:35:00+01:00", "SAP.DE: quote timestamp")
    covered("snapshot", "eur", C.dispatch("snapshot", {"symbol": "SAP.DE"}))
    fresh()
    SNAP = snap_data("JPY", {"last_price": 100.0, "prev_close": 99.0})
    out = C.dispatch("snapshot", {"symbol": "7203.T"})
    na = out.get("as_of_na")
    check(not out.get("as_of") and isinstance(na, str) and na.strip() and na != K.NO_DATE and "Yahoo" in na and K.violations(out) == [],
          f"snapshot without any quote time: a declared, non-generic as_of_na ({na!r})")
finally:
    C._first, C._usd_rates, C.cached_complete = saved_snap

# ---------------------------------------------------------------- compare (SEC filings: newest period end shown)
saved_facts = C._facts
try:
    FACTS = {}
    C._facts = lambda syms, fact: FACTS
    fresh()
    FACTS = {"AAPL": {"quarter": [["2026-03-28", 20e9], ["2026-06-27", 21e9], ["2026-09-26", 22e9]], "annual": [], "instant": []},
             "MSFT": {"quarter": [["2026-03-31", 30e9], ["2026-06-30", 31e9], ["2026-09-30", 32e9]], "annual": [], "instant": []}}
    out = C.dispatch("compare", {"symbols": "AAPL,MSFT", "fact": "capex", "period": "quarter", "since": "2025"})
    check_as_of("compare", out, "2026-09-30", "AAPL+MSFT quarterly: MSFT's quarter ends last")
    fresh()
    FACTS = {"SHEL": {"quarter": [], "annual": [["2024-12-31", 20e9], ["2025-12-31", 21e9]], "instant": []},
             "TM": {"quarter": [], "annual": [["2025-03-31", 40e9], ["2026-03-31", 41e9]], "instant": []}}
    out = C.dispatch("compare", {"symbols": "SHEL,TM", "fact": "capex", "period": "annual", "since": "2024"})
    check_as_of("compare", out, "2026-03-31", "SHEL+TM annual (foreign filers): TM's year ends last")
finally:
    C._facts = saved_facts

# ---------------------------------------------------------------- earnings, earnings_history, earnings_reports
# Hand-built Yahoo answers. The last report is followed by one close (the session after it, used for the 2-day move):
# that close is the newest data point used. The next report date is in the future.
def est_rows(ccy):
    return ([{"period": "0q", "mean": 2.1, "low": 1.9, "high": 2.3, "number_of_analysts": 30, "year_ago_eps": 1.9, "growth": 0.1, "currency": ccy},
             {"period": "+1q", "mean": 2.4, "low": 2.1, "high": 2.6, "number_of_analysts": 28, "year_ago_eps": 2.0, "growth": 0.2, "currency": ccy}],
            [{"period": "0q", "mean": 100e9, "low": 90e9, "high": 110e9, "number_of_analysts": 25, "growth": 0.05, "currency": ccy},
             {"period": "+1q", "mean": 105e9, "low": 95e9, "high": 115e9, "number_of_analysts": 24, "growth": 0.06, "currency": ccy}])


def earn_raw(ccy, hist, cal_date):
    eps, rev = est_rows(ccy)
    return {"hist": hist, "cal": {"earnings_date": cal_date, "earnings_average": 2.1, "earnings_low": 1.9, "earnings_high": 2.3,
                                  "revenue_average": 100e9, "revenue_low": 90e9, "revenue_high": 110e9},
            "eps": eps, "rev": rev, "trend": [], "revs": [], "inc": [{"period_ending": "2026-09-30", "total_revenue": 99e9}]}


US_RAW = earn_raw("USD", [{"date": "2026-10-29", "eps_actual": 2.20, "eps_estimated": 2.10}, {"date": "2026-07-30", "eps_actual": 2.00, "eps_estimated": 1.95},
                          {"date": "2026-04-30", "eps_actual": 1.90, "eps_estimated": 1.85}], "2027-01-28")
US_CLOSES = [("2026-04-29", 90.0), ("2026-05-01", 92.0), ("2026-07-29", 95.0), ("2026-07-31", 97.0), ("2026-10-28", 100.0), ("2026-10-29", 101.0), ("2026-10-30", 104.0)]
DE_RAW = earn_raw("EUR", [{"date": "2026-10-22", "eps_actual": 1.20, "eps_estimated": 1.10}, {"date": "2026-07-23", "eps_actual": 1.00, "eps_estimated": 0.95},
                          {"date": "2026-04-23", "eps_actual": 0.90, "eps_estimated": 0.85}], "2027-01-29")
DE_CLOSES = [("2026-04-22", 200.0), ("2026-04-24", 204.0), ("2026-07-22", 210.0), ("2026-07-24", 214.0), ("2026-10-21", 220.0), ("2026-10-22", 221.0), ("2026-10-23", 226.0)]
JP_RAW = earn_raw("JPY", [{"date": "2026-11-06", "eps_actual": 90.0, "eps_estimated": 85.0}, {"date": "2026-08-06", "eps_actual": 80.0, "eps_estimated": 78.0},
                          {"date": "2026-05-08", "eps_actual": 70.0, "eps_estimated": 69.0}], "2027-02-05")
JP_CLOSES = [("2026-05-07", 2800.0), ("2026-05-11", 2850.0), ("2026-08-05", 2900.0), ("2026-08-07", 2950.0), ("2026-11-05", 3000.0), ("2026-11-06", 3010.0),
             ("2026-11-09", 3100.0)]
SYMS = {"AAPL": (US_RAW, US_CLOSES), "SAP.DE": (DE_RAW, DE_CLOSES), "7203.T": (JP_RAW, JP_CLOSES)}

saved_e = {n: getattr(C, n) for n in ("_est_raw", "_report_info", "_earnings_release", "_last_price", "history", "_today", "_options_chain", "_yahoo_quarter_chart")}
try:
    C._est_raw = lambda sym: SYMS[sym][0]
    C._report_info = lambda sym, date: {}
    C._earnings_release = lambda sym, date: None
    C._last_price = lambda sym: None
    C.history = lambda syms, start, interval="1d": C.PriceHistory({s: list(SYMS[s][1]) for s in syms})
    C._today = lambda: D(2026, 11, 25)
    C._options_chain = lambda sym: None
    C._yahoo_quarter_chart = lambda sym: None
    fresh()
    check_as_of("earnings", C.dispatch("earnings", {"symbol": "AAPL"}), "2026-10-30", "AAPL: close after the 2026-10-29 report")
    covered("earnings", "us", C.dispatch("earnings", {"symbol": "AAPL"}))
    fresh()
    check_as_of("earnings", C.dispatch("earnings", {"symbol": "SAP.DE"}), "2026-10-23", "SAP.DE: close after the 2026-10-22 report")
    covered("earnings", "eur", C.dispatch("earnings", {"symbol": "SAP.DE"}))
    fresh()
    check_as_of("earnings_history", C.dispatch("earnings_history", {"symbol": "AAPL"}), "2026-10-30", "AAPL: close after the 2026-10-29 report")
    covered("earnings_history", "us", C.dispatch("earnings_history", {"symbol": "AAPL"}))
    fresh()
    check_as_of("earnings_history", C.dispatch("earnings_history", {"symbol": "SAP.DE"}), "2026-10-23", "SAP.DE: close after the 2026-10-22 report")
    covered("earnings_history", "eur", C.dispatch("earnings_history", {"symbol": "SAP.DE"}))
    fresh()
    check_as_of("earnings_reports", C.dispatch("earnings_reports", {"symbol": "AAPL"}), "2026-10-30", "AAPL: close after the 2026-10-29 report")
    covered("earnings_reports", "us", C.dispatch("earnings_reports", {"symbol": "AAPL"}))
    fresh()
    check_as_of("earnings_reports", C.dispatch("earnings_reports", {"symbol": "SAP.DE"}), "2026-10-23", "SAP.DE: close after the 2026-10-22 report")
    covered("earnings_reports", "eur", C.dispatch("earnings_reports", {"symbol": "SAP.DE"}))
    # a third input (Tokyo, JPY): the close after the 2026-11-06 report
    fresh()
    check_as_of("earnings", C.dispatch("earnings", {"symbol": "7203.T"}), "2026-11-09", "7203.T: close after the 2026-11-06 report")
    covered("earnings", "jpy", C.dispatch("earnings", {"symbol": "7203.T"}))
finally:
    for n, f in saved_e.items():
        setattr(C, n, f)

# ---------------------------------------------------------------- valuation_bands (the newest close the bands were drawn to)
saved_v = v.sec_figures, v._profile, C.history, v._yahoo, C._first
try:
    CLOSES = {}
    v._profile = lambda sym: {"industry": "Software", "currency": "USD" if "." not in sym else "EUR"}
    C.history = lambda syms, start, interval="1d": {s: list(CLOSES[s]) for s in syms}
    v.sec_figures = lambda sym, items=None: {"shares": [("2026-06-30", 10.0, D(2026, 8, 1))], "net_income": [("2026-06-30", 100.0, D(2026, 8, 1))]}
    fresh()
    CLOSES = {"AAPL": [("2026-11-17", 50.0), ("2026-11-18", 51.0), ("2026-11-19", 52.0), ("2026-11-20", 53.0), ("2026-11-23", 54.0), ("2026-11-24", 55.0)]}
    check_as_of("valuation_bands", C.dispatch("valuation_bands", {"symbol": "AAPL", "measure": "pe"}), "2026-11-24", "AAPL (SEC figures): last close 2026-11-24")
    covered("valuation_bands", "us", C.dispatch("valuation_bands", {"symbol": "AAPL", "measure": "pe"}))
    # non-U.S.: Yahoo annual statements in the trading currency
    C._first = lambda path, sym, **kw: {"currency": "EUR"}
    v._yahoo = lambda sym, kind: ([{"period_ending": "2024-12-31", "net_income_common_stockholders": 80.0}, {"period_ending": "2025-12-31", "net_income_common_stockholders": 100.0}]
                                  if kind == "income" else
                                  [{"period_ending": "2024-12-31", "ordinary_shares_number": 10.0}, {"period_ending": "2025-12-31", "ordinary_shares_number": 10.0}])
    fresh()
    CLOSES = {"SAP.DE": [("2026-11-13", 200.0), ("2026-11-16", 201.0), ("2026-11-17", 202.0), ("2026-11-18", 203.0), ("2026-11-19", 204.0), ("2026-11-20", 205.0)]}
    check_as_of("valuation_bands", C.dispatch("valuation_bands", {"symbol": "SAP.DE", "measure": "pe"}), "2026-11-20", "SAP.DE (Yahoo statements, EUR): last close 2026-11-20")
    covered("valuation_bands", "eur", C.dispatch("valuation_bands", {"symbol": "SAP.DE", "measure": "pe"}))
finally:
    v.sec_figures, v._profile, C.history, v._yahoo, C._first = saved_v

# ---------------------------------------------------------------- roic_dupont (the latest fiscal year end shown)
saved_r = v._sec_rows, v._profile, v._yahoo, C._first
try:
    ALL = {**v.TAGS, **v.INSTANT}
    US_FY = {"net_income": {"2024-09-28": 90.0, "2025-09-27": 100.0}, "revenue": {"2024-09-28": 400.0, "2025-09-27": 420.0},
             "assets": {"2024-09-28": 350.0, "2025-09-27": 360.0}, "equity": {"2024-09-28": 60.0, "2025-09-27": 70.0},
             "operating_income": {"2024-09-28": 120.0, "2025-09-27": 130.0}, "pretax": {"2024-09-28": 110.0, "2025-09-27": 125.0},
             "tax": {"2024-09-28": 20.0, "2025-09-27": 25.0}, "debt": {"2024-09-28": 100.0, "2025-09-27": 95.0}, "cash": {"2024-09-28": 30.0, "2025-09-27": 35.0}}

    def fake_sec_rows(sym, tags, instant=False, cache_key=None):
        name = next(k for k, t in ALL.items() if t == tags)
        return [{"span": "instant" if instant else "annual", "end": e, "begin": "", "value": val, "filed": "", "form": "10-K"} for e, val in US_FY.get(name, {}).items()]

    v._sec_rows = fake_sec_rows
    v._profile = lambda sym: {"industry": "Consumer Electronics", "currency": "USD"}
    fresh()
    out = C.dispatch("roic_dupont", {"symbol": "AAPL"})
    check_as_of("roic_dupont", out, "2025-09-27", "AAPL (SEC 10-K): fiscal year ended 2025-09-27")
    covered("roic_dupont", "us", out)
    check(out["results"][0]["fiscal_year_end"] == "2025-09-27", "roic_dupont AAPL: newest year first")
    # non-U.S.: Yahoo annual statements
    v._profile = lambda sym: {"industry": "Software", "currency": "EUR"}
    C._first = lambda path, sym, **kw: {"currency": "EUR"}
    v._yahoo = lambda sym, kind: ([{"period_ending": "2024-12-31", "net_income_common_stockholders": 80.0, "total_revenue": 400.0, "operating_income": 100.0,
                                    "total_pre_tax_income": 95.0, "tax_provision": 20.0},
                                   {"period_ending": "2025-12-31", "net_income_common_stockholders": 90.0, "total_revenue": 430.0, "operating_income": 110.0,
                                    "total_pre_tax_income": 105.0, "tax_provision": 22.0}] if kind == "income" else
                                  [{"period_ending": "2024-12-31", "common_stock_equity": 200.0, "total_debt": 50.0, "cash_and_cash_equivalents": 40.0, "total_assets": 500.0},
                                   {"period_ending": "2025-12-31", "common_stock_equity": 220.0, "total_debt": 55.0, "cash_and_cash_equivalents": 45.0, "total_assets": 520.0}])
    fresh()
    out = C.dispatch("roic_dupont", {"symbol": "SAP.DE"})
    check_as_of("roic_dupont", out, "2025-12-31", "SAP.DE (Yahoo statements, EUR): fiscal year ended 2025-12-31")
    covered("roic_dupont", "eur", out)
    check(out["results"][0]["fiscal_year_end"] == "2025-12-31", "roic_dupont SAP.DE: newest year first")
finally:
    v._sec_rows, v._profile, v._yahoo, C._first = saved_r

# ---------------------------------------------------------------- sp500_multiples (the newest point of the series drawn)
saved_m = multpl.fetch_series
try:
    MP = {"pe_month": [("2026-07-31", 24.0, False), ("2026-08-31", 24.5, False), ("2026-09-30", 25.0, False)],
          "shiller_pe_month": [("2026-08-31", 36.0, False), ("2026-09-30", 36.5, False), ("2026-10-31", 37.0, False)],
          "price_to_book_value_quarter": [("2026-03-31", 5.1, False), ("2026-06-30", 5.2, False), ("2026-09-30", 5.3, False)],
          "dividend_yield_month": [("2026-06-30", 1.30, False), ("2026-07-31", 1.28, False), ("2026-08-31", 1.26, False)]}
    multpl.fetch_series = lambda k: MP[k]
    fresh()
    check_as_of("sp500_multiples", C.dispatch("sp500_multiples", {"series_name": "pe_month,shiller_pe_month", "range": "5Y"}), "2026-10-31", "P/E + CAPE: CAPE has the newest month")
    fresh()
    check_as_of("sp500_multiples", C.dispatch("sp500_multiples", {"series_name": "price_to_book_value_quarter,dividend_yield_month", "range": "10Y"}), "2026-09-30",
                "P/B (quarterly) + dividend yield (%): the quarter 2026-09-30 beats the month 2026-08-31")
finally:
    multpl.fetch_series = saved_m

# ---------------------------------------------------------------- news (newest headline shown)
NOW = dt.datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
saved_n = news.collect, news._window, news.rank, C._profiles
_rank = news.rank
try:
    ITEMS = []
    news.collect = lambda subj, feed_ids, queries=True: (list(ITEMS), {"bloomberg"})
    news._window = lambda h: h
    news.rank = lambda subj, items, **kw: _rank(subj, items, **dict(kw, now=NOW))        # a fixed clock; the view is otherwise real
    C._profiles = lambda syms: {s: {"name": {"AAPL": "Apple Inc.", "SAP.DE": "SAP SE"}.get(s, ""), "sector": "Technology", "industry": ""} for s in syms}

    def item(title, pub, tier, when, feed="bloomberg"):
        return {"title": title, "url": "https://x/" + title[:8].replace(" ", "-"), "date": when, "publisher": pub, "tier": tier, "feed": feed, "summary": ""}

    ITEMS = [item("AAPL results beat estimates", "CNBC", 2, dt.datetime(2026, 11, 24, 14, 30, tzinfo=UTC)),
             item("Apple unveils a new iPhone", "Bloomberg", 2, dt.datetime(2026, 11, 23, 9, 0, tzinfo=UTC)),
             item("AAPL slides in late trading", "Reuters", 2, dt.datetime(2026, 11, 22, 21, 15, tzinfo=UTC))]
    fresh()
    out = C.dispatch("news", {"subject": "symbol", "symbol": "AAPL"})
    check(len(out["results"]) >= 1, "news AAPL: headlines kept")
    check_as_of("news", out, "2026-11-24T14:30:00Z", "AAPL: newest headline 2026-11-24 14:30 UTC")
    covered("news", "us", out)
    ITEMS = [item("SAP-Aktie: Kurs gibt nach", "Handelsblatt", 2, dt.datetime(2026, 11, 21, 8, 5, tzinfo=UTC)),
             item("SAP-Aktie: Analysten heben Kursziel an", "Reuters", 2, dt.datetime(2026, 11, 24, 6, 45, tzinfo=UTC)),
             item("SAP-Aktie: Rueckblick auf das Quartal", "Handelsblatt", 2, dt.datetime(2026, 11, 20, 17, 0, tzinfo=UTC))]
    fresh()
    out = C.dispatch("news", {"subject": "symbol", "symbol": "SAP.DE", "hours": "168"})
    check(len(out["results"]) == 3, "news SAP.DE: all three headlines kept")
    check_as_of("news", out, "2026-11-24T06:45:00Z", "SAP.DE: newest headline 2026-11-24 06:45 UTC")
    covered("news", "eur", out)
finally:
    news.collect, news._window, news.rank, C._profiles = saved_n

# ---------------------------------------------------------------- debt_maturity_wall (the 10-K's fiscal year end)
saved_api = C.api
try:
    WALL = {}

    def fake_api(base, path, **p):
        return WALL.get(p.get("fact"), [])

    C.api = fake_api
    t0, t1 = bonds.WALL[0][0], bonds.WALL[1][0]
    WALL = {t0: [{"form": "10-K", "value": 10e9, "period_ending": "2025-09-27", "reported_date": "2025-10-31", "cik": 320193},
                 {"form": "10-K", "value": 9e9, "period_ending": "2024-09-28", "reported_date": "2024-11-01", "cik": 320193},
                 {"form": "10-Q", "value": 8e9, "period_ending": "2026-06-27", "reported_date": "2026-08-01", "cik": 320193}],
            t1: [{"form": "10-K", "value": 12e9, "period_ending": "2025-09-27", "reported_date": "2025-10-31", "cik": 320193}]}
    fresh()
    check_as_of("debt_maturity_wall", C.dispatch("debt_maturity_wall", {"company": "AAPL"}), "2025-09-27", "AAPL: 10-K for FY ended 2025-09-27 (a later 10-Q is not a schedule)")
    WALL = {t0: [{"form": "10-K", "value": 7e9, "period_ending": "2025-12-31", "reported_date": "2026-02-10", "cik": 37996},
                 {"form": "10-K", "value": 6e9, "period_ending": "2024-12-31", "reported_date": "2025-02-11", "cik": 37996}],
            t1: [{"form": "10-K/A", "value": 5e9, "period_ending": "2025-12-31", "reported_date": "2026-03-01", "cik": 37996}]}
    fresh()
    check_as_of("debt_maturity_wall", C.dispatch("debt_maturity_wall", {"company": "F"}), "2025-12-31", "F: 10-K for FY ended 2025-12-31 (calendar year)")
finally:
    C.api = saved_api

done("test_asof_equity")
