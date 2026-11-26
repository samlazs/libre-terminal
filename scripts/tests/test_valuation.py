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
    v.sec_figures = lambda sym, items=None: {"shares": [("2025-09-30", 1.0, D(2025, 11, 1))], "net_income": [("2025-09-30", 1.0, D(2025, 11, 1))]}
    c.history = lambda syms, start: {"TST": [(f"2026-01-{i:02d}", float(i)) for i in range(1, 11)]}
    out = v.bands(symbol="tst", measure="pe", today=D(2026, 1, 11))
    s = out["summary"]
    check(s["current"] == 10.0 and s["median"] == 5.5 and abs(s["p10"] - 1.9) < 1e-9 and s["percentile"] == 100.0, "P/E 1..10: median 5.5, p10 1.9, today at the 100th percentile: " + str(s))
    check(out["data"][-1]["name"].startswith("P/E (TTM)  10.0x (100th percentile)") and [x["name"] for x in out["sources"]] == ["SEC", "Yahoo Finance"], "legend and sources")
    check(out["results"][0]["date"] == "2026-01-10" and out["results"][0]["multiple"] == 10.0 and "convention" in out, "bands include newest-first results table & convention")
    v._profile = lambda sym: {"industry": "Banks - Diversified", "currency": "USD"}
    try:
        v.bands(symbol="tst", measure="ev_ebitda", today=D(2026, 1, 11))
        check(False, "bank EV/EBITDA must be refused")
    except c.CompositeError as e:
        check("no meaning for a bank" in str(e), "bank: EV/EBITDA refused with the reason")
    try:
        v.bands(symbol="tst", measure="ev_sales", today=D(2026, 1, 11))
        check(False, "bank EV/Sales must be refused")
    except c.CompositeError as e:
        check("no meaning for a bank" in str(e), "bank: EV/Sales refused with the reason")
finally:
    v.sec_figures, v._profile, c.history = saved

# gatekeeper P2: SEC failing for a U.S. company switches to Yahoo's statements as a said substitute (status fallback),
# unless SEC simply has nothing for it (not_covered): then Yahoo is the usual basis and the answer is ok.
# Same figures either way (shares 1, net income 1; closes 1..10): P/E today 10.0.
saved = v.sec_figures, v._profile, c.history, v._yahoo_pack
try:
    v._profile = lambda sym: {"industry": "Consumer Electronics", "currency": "USD"}
    c.history = lambda syms, start: {syms[0]: [(f"2026-01-{i:02d}", float(i)) for i in range(1, 11)]}
    one = {"shares": [("2025-09-30", 1.0, D(2025, 11, 1))], "net_income": [("2025-09-30", 1.0, D(2025, 11, 1))]}
    v._yahoo_pack = lambda sym, px_ccy, prof=None, last_px=None: (one, "Yahoo annual statements", [v.YAHOO_FIN_SRC, v.YAHOO_SRC])
    for kind, want_status in (("timeout", "fallback"), ("upstream", "fallback"), ("not_covered", "ok")):
        def sec_fails(sym, items=None, _k=kind):
            raise c.CompositeError(f"HTTP 500 from http://127.0.0.1:6900/api/v1/equity/compare/company_facts ({_k})", kind=_k)
        v.sec_figures = sec_fails
        c._cache.clear()
        out = c.dispatch("valuation_bands", {"symbol": "TSTB", "measure": "pe"})
        said = any("Yahoo annual statements used instead: SEC EDGAR did not answer just now" in n for n in out.get("notes") or [])
        check(out["status"] == want_status and said == (want_status == "fallback") and out["summary"]["current"] == 10.0,
              f"SEC {kind}: status {want_status}, substitute {'said' if want_status == 'fallback' else 'not claimed'} ({out['status']}, {out.get('notes')})")
        # review R7: the note says the kind of failure, never the source's own text (URLs, backend detail)
        check(not any("http" in n or "6900" in n for n in out.get("notes") or []), f"SEC {kind}: no URL in the notes ({out.get('notes')})")
finally:
    v.sec_figures, v._profile, c.history, v._yahoo_pack = saved
    c._cache.clear()

# review R10: roic_dupont through dispatch. Figures (in units, shown in millions) as in the DuPont check above x 1e6:
# ROE 20/100 = 20%; tax 10/25 = 40% clipped to 35%; NOPAT 30 x 0.65 = 19.5 mn; invested capital 50 + 100 - 30 = 120 mn;
# ROIC 19.5 / 120 = 16.25%.
from composite.core import contract as K  # noqa: E402
import os  # noqa: E402

_FY = "2025-12-31"
_yf = {k: [] for k in ("net_income", "operating_income", "da", "ebitda", "equity", "debt", "cash", "shares", "assets", "revenue", "pretax", "tax")}
for _k, _val in (("net_income", 20), ("revenue", 200), ("assets", 400), ("equity", 100), ("operating_income", 30), ("pretax", 25),
                 ("tax", 10), ("debt", 50), ("cash", 30)):
    _yf[_k] = [(_FY, _val * 1e6, D(2026, 3, 31))]
saved = v._profile, v._sec_rows, v.yahoo_figures, v.C._first
_metrics = {"currency": "USD"}
sec_calls = []
try:
    v._profile = lambda sym: {"industry": "Consumer Electronics", "currency": "USD"}
    v.yahoo_figures = lambda sym, rate=1.0: _yf
    v.C._first = lambda path, sym, **kw: dict(_metrics)

    def sec_rows_timeout(sym, tags, instant=False, cache_key=None):
        sec_calls.append(sym)
        raise c.CompositeError("OpenBB V5 backend did not answer in time (http://127.0.0.1:6900/api/v1/equity/compare/company_facts).", kind="timeout")
    v._sec_rows = sec_rows_timeout
    c._cache.clear()
    out = c.dispatch("roic_dupont", {"symbol": "TSTD"})
    r0 = out["results"][0]
    check(out["status"] == "fallback" and out["currency"] == "USD" and (r0["roe"], r0["nopat"], r0["invested_capital"], r0["roic"]) == (20.0, 19.5, 120.0, 16.25)
          and any(n.startswith("Yahoo annual statements used instead: SEC EDGAR did not answer just now") for n in out["notes"])
          and not any("http" in n for n in out["notes"]),
          f"roic_dupont, SEC timeout: Yahoo said as a substitute, in USD, figures by hand ({out['status']}, {out.get('currency')}, {r0}, {out.get('notes')})")
    _metrics = {"currency": "EUR"}
    sec_calls.clear()
    c._cache.clear()
    out = c.dispatch("roic_dupont", {"symbol": "SAP.DE"})
    check(out["status"] == "ok" and out["currency"] == "EUR" and out["results"][0]["nopat"] == 19.5 and sec_calls == [],
          f"roic_dupont, non-U.S.: Yahoo is the usual basis (ok), in the metrics' currency EUR, SEC not asked ({out['status']}, {out.get('currency')})")
    _metrics = {}
    c._cache.clear()
    try:
        c.dispatch("roic_dupont", {"symbol": "SAP.DE"})
        named = "answered"
    except K.ContractViolation as e:
        named = str(e)
    check("names no currency" in named, f"roic_dupont, no currency named: a contract violation in strict mode ({named[:80]})")
    os.environ["TERMINAL_STRICT_CONTRACT"] = "0"
    try:
        c._cache.clear()
        out = c.dispatch("roic_dupont", {"symbol": "SAP.DE"})
    finally:
        os.environ["TERMINAL_STRICT_CONTRACT"] = "1"
    r0 = out["results"][0]
    check("currency" not in out and r0["nopat"] is None and r0["invested_capital"] is None and r0["roe"] == 20.0 and r0["roic"] == 16.25
          and K.CCY_BLANK_NOTE in out["notes"] and "currency not named" in out["note"],
          f"roic_dupont, no currency named (production): amounts blank with the note, ratios kept ({r0}, {out.get('notes')})")
finally:
    v._profile, v._sec_rows, v.yahoo_figures, v.C._first = saved
    c._cache.clear()

check("valuation_bands" in c.ROUTES and "roic_dupont" in c.ROUTES and c.static_sources("valuation_bands", {"symbol": "AAPL"})[0]["name"] == "SEC"
      and c.static_sources("roic_dupont", {"symbol": "SAP.DE"})[0]["name"] == "Yahoo Finance", "routes and sources by market")

check([v.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 100)] == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "100th"], "ordinal suffixes")
_zero = v.AsOf([("0001-01-01", 0.0, dt.date.min)])
check(abs(v.multiples(closes, sh, dict(f, debt=_zero), "ev_ebitda")[1][1] - 9.8) < 1e-12, "EV/EBITDA with no debt: (500 - 10) / 50 = 9.8x")

# ---- v0.6.0 valuation hardening: staleness, sentinel, form filter, D&A & shares fallback, ADR scaling, spikes, n/m warning
stale_a = v.AsOf([("2024-01-01", 5.0, D(2024, 2, 1))])
check(stale_a.at(D(2024, 12, 31)) is not None and stale_a.at(D(2025, 9, 1)) is None, "AsOf expires figures >520 days old")
check(_zero.at(D(2026, 1, 1)) is not None, "no_debt sentinel (date.min) never expires")

check(v.available("2025-12-31", "2026-04-24", "DEF 14A", "quarter") == D(2026, 3, 31), "DEF 14A uses 90-day 10-K deadline")
f_rev = dict(f, revenue=v.AsOf([("2026-03-31", 200.0, D(2026, 5, 1))]))
check(abs(v.multiples(closes, sh, f_rev, "ps")[1][1] - 2.5) < 1e-12, "P/S 500/200 = 2.5x")
check(abs(v.multiples(closes, sh, f_rev, "ev_sales")[1][1] - 2.6) < 1e-12, "EV/Sales (500+30-10)/200 = 2.6x")
check(abs(v.multiples(closes, sh, dict(f_rev, debt=_zero), "ev_sales")[1][1] - 2.45) < 1e-12, "no_debt sentinel treats debt as 0: (500-10)/200 = 2.45x")
check(v.multiples(closes, sh, dict(f_rev, debt=v.AsOf([])), "ev_sales")[1][1] is None
      and v.multiples(closes, sh, dict(f, debt=v.AsOf([])), "ev_ebitda")[1][1] is None,
      "missing/expired debt returns None instead of silently counting debt as 0")
f_nodebt = dict(fa, pretax={"2025-12-31": 25.0}, debt={})
r_nd = v.dupont_rows(f_nodebt)[0]
check(r_nd["invested_capital"] == 70.0 and abs(r_nd["roic"] - 19.5 / 70.0 * 100) < 0.01, "debt-free company treated as 0 debt in ROIC invested capital")
f_partial_debt = dict(fa, pretax={"2025-12-31": 25.0}, debt={"2024-12-31": 50.0})
r_pd = v.dupont_rows(f_partial_debt)[0]
check(r_pd["invested_capital"] is None and r_pd["roic"] is None, "missing debt for one year in a company with debt leaves ROIC blank")

# _sec_rows form filter & AMZN 10-Q span="annual" exclusion + transient error re-raise + D&A & share fallbacks
saved_api, saved_splits = c.api, v._splits
try:
    c._cache.clear()
    v._splits = lambda sym: []

    def sec_mock(base, path, **p):
        fact = p.get("fact")
        if fact == "NetIncomeLoss":
            return [
                {"span": "annual", "period_ending": "2025-09-30", "period_beginning": "2024-10-01", "value": 999.0, "reported_date": "2025-11-01", "form": "10-Q", "unit": "USD"},
                {"span": "quarter", "period_ending": "2025-09-30", "period_beginning": "2025-07-01", "value": 10.0, "reported_date": "2025-11-01", "form": "10-Q", "unit": "USD"},
                {"span": "quarter", "period_ending": "2025-09-30", "period_beginning": "2025-07-01", "value": 777.0, "reported_date": "2025-10-20", "form": "8-K", "unit": "USD"},
                {"span": "quarter", "period_ending": "2025-12-31", "period_beginning": "2025-10-01", "value": 15.0, "reported_date": "2026-04-20", "form": "DEF 14A", "unit": "USD"},
            ]
        if fact == "OperatingIncomeLoss":
            return [
                {"span": "quarter", "period_ending": f"2025-{m:02d}-28", "period_beginning": f"2025-{m - 2:02d}-01", "value": 25.0, "reported_date": f"2025-{m:02d}-28", "form": "10-Q", "unit": "USD"}
                for m in (3, 6, 9, 12)
            ]
        if fact in v.TAGS["da"]:
            return []
        if fact == "Depreciation":
            return [{"span": "quarter", "period_ending": f"2025-{m:02d}-28", "period_beginning": f"2025-{m - 2:02d}-01", "value": 3.0, "reported_date": f"2025-{m:02d}-28", "form": "10-Q", "unit": "USD"} for m in (3, 6, 9, 12)]
        if fact == "AmortizationOfIntangibleAssets":
            return [{"span": "quarter", "period_ending": f"2025-{m:02d}-28", "period_beginning": f"2025-{m - 2:02d}-01", "value": 2.0, "reported_date": f"2025-{m:02d}-28", "form": "10-Q", "unit": "USD"} for m in (3, 6, 9, 12)]
        if fact == "WeightedAverageNumberOfDilutedSharesOutstanding":
            return [{"span": "quarter", "period_ending": "2024-03-31", "period_beginning": "2024-01-01", "value": 10.0, "reported_date": "2024-05-01", "form": "10-Q", "unit": "shares"}]
        if fact == "WeightedAverageNumberOfSharesOutstandingBasic":
            return [{"span": "quarter", "period_ending": "2024-12-31", "period_beginning": "2024-10-01", "value": 12.0, "reported_date": "2025-02-01", "form": "10-Q", "unit": "shares"}]
        if fact == "CommonStockSharesOutstanding":
            return [{"span": "instant", "period_ending": "2025-12-28", "value": 13.0, "reported_date": "2026-02-01", "form": "10-Q", "unit": "shares"}]
        return []

    c.api = sec_mock
    ni_rows = v._sec_rows("SECMOD", v.TAGS["net_income"])
    check(len(ni_rows) == 2 and ni_rows[0]["value"] == 10.0 and ni_rows[1]["form"] == "DEF 14A", "8-K and 10-Q annual span filtered out; DEF 14A kept")
    sf = v.sec_figures("SECMOD", items=["operating_income", "da", "shares"])
    check(sf["da"][0][1] == 20.0, "D&A fallback sums TTM Depreciation + AmortizationOfIntangibleAssets: " + str(sf["da"]))
    check([p[1] for p in sf["shares"]] == [10.0, 12.0, 13.0], "shares fallback merges Basic and instant CommonStockSharesOutstanding: " + str(sf["shares"]))

    # transient errors re-raise instead of caching empty; non-transient errors with digits like 504 do not
    for i, (msg, kw) in enumerate((("OpenBB V5 backend did not answer in time (TimeoutError)", {}),
                                   ("OpenBB V5 backend timed out", {}),
                                   ("upstream error", {"transient": True}))):
        def err_api(base, path, _m=msg, _kw=kw, **p):
            raise c.CompositeError(_m, **_kw)
        c.api = err_api
        try:
            v._sec_rows(f"TIMED{i}", ["NetIncomeLoss"])
            check(False, "transient error must re-raise: " + msg)
        except c.CompositeError as e:
            check(msg in str(e), "transient error propagates out of _sec_rows: " + msg)

    def cik_504_api(base, path, **p):
        raise c.CompositeError("No data for CIK 0001504123")
    c.api = cik_504_api
    try:
        v._sec_rows("CIK504", ["NetIncomeLoss"])
        check(False, "CIK error still raises after trying all tags")
    except c.CompositeError as e:
        check("CIK 0001504123" in str(e), "CIK error propagates cleanly")
    def fact_504_api(base, path, **p):
        if p.get("fact") == "FirstTag":
            raise c.CompositeError("No fact 504123 found")
        return [{"span": "quarter", "period_ending": "2025-09-30", "period_beginning": "2025-07-01", "value": 42.0, "reported_date": "2025-11-01", "form": "10-Q", "unit": "USD"}]
    c.api = fact_504_api
    got_504 = v._sec_rows("FACT504", ["FirstTag", "SecondTag"])
    check(len(got_504) == 1 and got_504[0]["value"] == 42.0, "bare 504 in a permanent error message does not abort remaining tags")
finally:
    c.api, v._splits = saved_api, saved_splits
    c._cache.clear()

# ADR / minor-unit scaling & n/m warning + spike clipping
check(abs(v._adr_scale({"shares": [("2025-12-31", 25.0, D(2026, 3, 1))]}, {"market_cap": 100.0}, "USD", 20.0) - 0.2) < 1e-6, "5:1 ADR share scaling factor = 0.2")
check(v._adr_scale({"shares": [("2025-12-31", 100.0, D(2026, 3, 1))]}, {"market_cap": 2000.0}, "GBp", 2000.0) == 1.0, "UK GBp quote price converted to GBP major before comparing market cap")
check(v._adr_scale({"shares": [("2025-12-31", 100.0, D(2026, 3, 1))]}, {"market_cap": 1150.0}, "USD", 10.0) == 1.0
      and v._adr_scale({"shares": [("2025-12-31", 100.0, D(2026, 3, 1))]}, {"market_cap": 850.0}, "USD", 10.0) == 1.0,
      "normal 15% share issuance or buyback is not rescaled as an ADR mismatch")

saved = v.sec_figures, v._profile, c.history
try:
    v._profile = lambda sym: {"industry": "Interactive Home Entertainment", "currency": "USD"}
    v.sec_figures = lambda sym, items=None: {
        "shares": [("2022-09-30", 1.0, D(2022, 11, 1)), ("2026-09-30", 1.0, D(2026, 11, 1))],
        "net_income": [("2022-09-30", 1.0, D(2022, 11, 1)), ("2026-09-30", -2.0, D(2026, 11, 1))],
        "operating_income": [("2026-09-30", 0.01, D(2026, 11, 1))],
        "da": [("2026-09-30", 0.0, D(2026, 11, 1))],
        "cash": [("2026-09-30", 0.0, D(2026, 11, 1))],
        "ebitda": [],
    }
    c.history = lambda syms, start: {"BADCO": [("2022-11-11", 50.0), ("2026-11-18", 200.0)]}
    nm_out = v.bands(symbol="badco", measure="pe", years="10", today=D(2026, 11, 20))
    check(nm_out["summary"]["current"] is None and nm_out["summary"]["last_valid"] == 50.0 and nm_out["summary"]["last_valid_date"] == "2022-11-11",
          "n/m summary preserves last_valid & date: " + str(nm_out["summary"]))
    check("n/m now (last 50.0x on 2022-11-11)" in nm_out["data"][-1]["name"] and "EV / Sales, P / S or P / B" in nm_out.get("warning", ""),
          "n/m legend & actionable warning: " + str(nm_out.get("warning")))

    # spike clipping (> MAX_MULT)
    v.sec_figures = lambda sym, items=None: {
        "shares": [("2026-09-30", 1.0, D(2026, 10, 1))],
        "net_income": [("2026-09-30", 0.01, D(2026, 10, 1))],
    }
    c.history = lambda syms, start: {"SPK": [("2026-10-05", 0.2), ("2026-10-06", 5.0)]}   # 20x kept, 500x clipped >250x
    sp_out = v.bands(symbol="spk", measure="pe", today=D(2026, 10, 10))
    check(sp_out["results"][0]["multiple"] is None and sp_out["results"][1]["multiple"] == 20.0 and "clipped" in sp_out["convention"],
          "extreme near-zero-denominator spike clipped above 250x")
    # stale SEC shares (>520d) with fresh net_income triggers Yahoo fallback (BRK-B regression)
    saved_yp = v._yahoo_pack
    try:
        v.sec_figures = lambda sym, items=None: {
            "shares": [("2015-09-30", 1.0, D(2015, 11, 1))],
            "net_income": [("2025-09-30", 10.0, D(2025, 11, 1))],
        }
        v._yahoo_pack = lambda sym, px_ccy, prof=None, last_px=None: (
            {"shares": [("2025-12-31", 2.0, D(2026, 3, 31))], "net_income": [("2025-12-31", 5.0, D(2026, 3, 31))]},
            "Yahoo annual statements",
            [v.YAHOO_FIN_SRC, v.YAHOO_SRC],
        )
        c.history = lambda syms, start: {"BRK-B": [("2026-04-01", 50.0), ("2026-04-02", 55.0)]}
        yb_out = v.bands(symbol="brk-b", measure="pe", today=D(2026, 4, 10))
        check(yb_out["summary"]["current"] == 22.0 and yb_out["sources"][0]["name"] == "Yahoo Finance",
              "stale SEC shares falls back to Yahoo instead of 502: " + str(yb_out["summary"]))
    finally:
        v._yahoo_pack = saved_yp
finally:
    v.sec_figures, v._profile, c.history = saved

# ---- P2-05: a 10:1 split on 2024-06-10 (NVDA-like). Hand-computed: price 100 (Yahoo, split-adjusted), TTM net income
# 4 x 25 = 100, diluted shares 2.5 filed before the split (x10 = 25 on today's basis) and 25 after -> EPS 100 / 25 = 4,
# P/E 100 / 4 = 25 on every day. Without the split adjustment the pre-split days would show 100 / (100 / 2.5) = 2.5.
import json  # noqa: E402

TODAY_S = dt.date(2025, 9, 30)
Q_ENDS = [dt.date(y, m, 28) for y in (2021, 2022, 2023, 2024, 2025) for m in (1, 4, 7, 10) if dt.date(y, m, 28) < dt.date(2025, 8, 1)]


def split_api(base, path, **p):
    fact = p.get("fact")
    if path.endswith("historical_splits"):
        raise c.CompositeError("HTTP 429 Too Many Requests", kind="rate_limited", retry_after=30.0)
    rows = []
    for end in Q_ENDS:
        filed = end + dt.timedelta(days=31)
        row = {"span": "quarter", "period_ending": end.isoformat(), "period_beginning": (end - dt.timedelta(days=89)).isoformat(),
               "reported_date": filed.isoformat(), "form": "10-Q"}
        if fact == "NetIncomeLoss":
            rows.append(dict(row, value=25.0, unit="USD"))
        elif fact == "WeightedAverageNumberOfDilutedSharesOutstanding":
            rows.append(dict(row, value=2.5 if filed.isoformat() < "2024-06-10" else 25.0, unit="shares"))
    return rows


CLOSES_S = [((TODAY_S - dt.timedelta(days=i)).isoformat(), 100.0) for i in range(3 * 366, 0, -1)
            if (TODAY_S - dt.timedelta(days=i)).weekday() < 5]
saved = c.api, v._splits, v._profile, c.history
try:
    c.reset_runtime_caches()
    c.api = split_api
    v._profile = lambda sym: {"currency": "USD", "industry": "Semiconductors"}
    c.history = lambda syms, start, interval="1d": c.PriceHistory({s: list(CLOSES_S) for s in syms})
    v._splits = lambda sym: [("2024-06-10", 10.0)]
    sh = v.sec_figures("NVSX", items=["net_income", "shares"])["shares"]
    check({x[1] for x in sh} == {25.0}, f"every share count on today's basis: 2.5 filed before the split x10 = 25 ({sorted({x[1] for x in sh})})")
    out = v.bands("NVSX", "pe", "3", today=TODAY_S)
    s = out["summary"]
    check(s["p10"] == s["median"] == s["p90"] == 25.0, f"P/E continuous across the split: 25.0 everywhere ({s})")
    v._splits = lambda sym: []
    s_raw = v.bands("NVSX", "pe", "3", today=TODAY_S)["summary"]
    check(s_raw["p10"] == 2.5 and s_raw["p90"] == 25.0, f"(control) without the adjustment the pre-split P/E would be 2.5 ({s_raw})")

    # the split fetch fails and nothing is saved: blank with the note (typed, retryable), never Yahoo figures in silence
    c.reset_runtime_caches()
    v._splits = saved[1]
    split_file = c.CACHE_DIR / (c.disk_name("splits_", "NVSX") + ".json")
    split_file.unlink(missing_ok=True)
    try:
        v.bands("NVSX", "pe", "3", today=TODAY_S)
        err = None
    except c.CompositeError as e:
        err = e
    check(isinstance(err, v.SplitsUnavailable) and err.kind == "rate_limited" and str(err).startswith("Bands not drawn")
          and "split history" in str(err), f"no split list: bands blank with the note ({err})")
    check(not split_file.exists(), "nothing saved for the failed split fetch")

    # the split fetch fails, a saved list exists (8 days old, past its 7-day life): it is used, and the answer says stale
    split_file.write_text(json.dumps({"t": c.time.time() - 8 * 86400, "v": [["2024-06-10", 10.0]]}), encoding="utf-8")
    c.reset_runtime_caches()
    got = c._collect(lambda: v.bands("NVSX", "pe", "3", today=TODAY_S))
    check(isinstance(got, c.Partial) and len(got.stale) == 1 and got.value["summary"]["median"] == 25.0,
          f"saved split list used (bands right), flagged stale ({got!r})")
finally:
    c.api, v._splits, v._profile, c.history = saved
    c.reset_runtime_caches()

# ---- Release 1.1: true point-in-time SEC filings, fiscal-year transitions (10-QT), contiguous quarters, chip tags
import pathlib  # noqa: E402
import sys  # noqa: E402

from openbb_sec.utils import frames as sec_frames  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_backend as rb  # noqa: E402

# 1) Comparative & FY restatement (AMD FY17 ASC 606 pattern): +43M original filed 2018-02-27,
# comparative Q1-Q3 2017 restated later in 2018, restated FY17 -33M filed 2019-02-08.
# Neither historical dates in March 2018 nor intermediate dates before 2019-02-08 leak restated or mixed-GAAP numbers.
amd_rows = [
    {"span": "quarter", "begin": "2017-01-01", "end": "2017-03-31", "value": 10.0, "filed": "2017-05-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2017-04-01", "end": "2017-06-30", "value": 11.0, "filed": "2017-08-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2017-07-01", "end": "2017-09-30", "value": 12.0, "filed": "2017-11-01", "form": "10-Q"},
    {"span": "annual", "begin": "2017-01-01", "end": "2017-12-30", "value": 43.0, "filed": "2018-02-27", "form": "10-K"},  # orig Q4 = 10 -> TTM 43
    {"span": "quarter", "begin": "2017-01-01", "end": "2017-03-31", "value": -5.0, "filed": "2018-05-01", "form": "10-Q"},  # ASC 606 comparative
    {"span": "quarter", "begin": "2017-04-01", "end": "2017-06-30", "value": -8.0, "filed": "2018-08-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2017-07-01", "end": "2017-09-30", "value": -10.0, "filed": "2018-11-01", "form": "10-Q"},
    {"span": "annual", "begin": "2017-01-01", "end": "2017-12-30", "value": -33.0, "filed": "2019-02-08", "form": "10-K"},  # restated Q4 = -10 -> TTM -33
]
pit_amd = v.AsOf(v.ttm_pit(amd_rows))
check(pit_amd.at(D(2018, 3, 15)) == ("2017-12-30", 43.0, D(2018, 2, 27)),
      f"March 2018 sees original +43M (no lookahead to -33M): {pit_amd.at(D(2018, 3, 15))}")
check(pit_amd.at(D(2018, 6, 1))[1] == 28.0,
      f"May 2018 comparative Q1 restatement (-5 - 10 = -15 delta) preserves original Q4=10 without mixed-GAAP FY17 subtraction: {pit_amd.at(D(2018, 6, 1))}")
check(pit_amd.at(D(2019, 2, 8)) == ("2017-12-30", -33.0, D(2019, 2, 8)),
      f"FY17 restatement (-33M) applies only from its 2019-02-08 filing date: {pit_amd.at(D(2019, 2, 8))}")

# 2) Mid-year 10-Q/A amendment (Q1-25 15 -> 5 filed 2025-06-15)
am_rows = [
    {"span": "quarter", "begin": "2024-01-01", "end": "2024-03-31", "value": 10.0, "filed": "2024-05-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2024-04-01", "end": "2024-06-30", "value": 10.0, "filed": "2024-08-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2024-07-01", "end": "2024-09-30", "value": 10.0, "filed": "2024-11-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2024-10-01", "end": "2024-12-31", "value": 10.0, "filed": "2025-02-20", "form": "10-K"},
    {"span": "quarter", "begin": "2025-01-01", "end": "2025-03-31", "value": 15.0, "filed": "2025-05-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2025-01-01", "end": "2025-03-31", "value": 5.0, "filed": "2025-06-15", "form": "10-Q/A"},
]
pit_am = v.AsOf(v.ttm_pit(am_rows))
check(pit_am.at(D(2025, 5, 20)) == ("2025-03-31", 45.0, D(2025, 5, 1))
      and pit_am.at(D(2025, 6, 15)) == ("2025-03-31", 35.0, D(2025, 6, 15)),
      "10-Q/A amendment updates TTM from 45 to 35 on its 2025-06-15 filing date")

# 3) Instant restatement & _latest_end / _sec_needs_fallback ordering check
inst_pts = v.instants_pit([
    {"span": "instant", "end": "2024-12-31", "value": 100.0, "filed": "2025-02-20", "form": "10-K"},
    {"span": "instant", "end": "2025-12-31", "value": 120.0, "filed": "2026-02-20", "form": "10-K"},
    {"span": "instant", "end": "2024-12-31", "value": 95.0, "filed": "2026-05-01", "form": "10-K"},
])
check(v._latest_end(inst_pts) == "2025-12-31", f"_latest_end reads max period end (2025-12-31), not last list item ({inst_pts[-1]})")
inst_ao = v.AsOf(inst_pts)
check(inst_ao.at(D(2025, 6, 1))[1] == 100.0 and inst_ao.at(D(2026, 6, 1))[:2] == ("2025-12-31", 120.0),
      "later comparative restatement of 2024-12-31 never overwrites newer 2025-12-31 balance")

# 4) Fiscal-year transition (10-QT) & non-contiguous quarter stub guard
fy_change_rows = [
    {"span": "quarter", "begin": "2017-04-01", "end": "2017-06-30", "value": 25.0, "filed": "2017-08-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2017-07-01", "end": "2017-09-30", "value": 25.0, "filed": "2017-11-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2017-10-01", "end": "2017-12-31", "value": 25.0, "filed": "2018-02-15", "form": "10-K"},
    {"span": "quarter", "begin": "2018-01-01", "end": "2018-03-31", "value": 25.0, "filed": "2018-05-01", "form": "10-QT"},
]
check(v.ttm(v.quarters(fy_change_rows)) == [("2018-03-31", 100.0, D(2018, 5, 1))]
      and v.ttm_pit(fy_change_rows) == [("2018-03-31", 100.0, D(2018, 5, 1))],
      "quarter-aligned 10-QT fiscal-year transition computes valid TTM")
stub_rows = [
    {"span": "quarter", "begin": "2017-04-01", "end": "2017-06-30", "value": 25.0, "filed": "2017-08-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2017-07-01", "end": "2017-09-30", "value": 25.0, "filed": "2017-11-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2017-10-01", "end": "2017-11-10", "value": 10.0, "filed": "2017-12-20", "form": "10-QT"},  # 41d gap
    {"span": "quarter", "begin": "2017-11-11", "end": "2018-03-31", "value": 40.0, "filed": "2018-05-01", "form": "10-Q"},  # 141d gap
]
check(v.ttm(v.quarters(stub_rows)) == [] and v.ttm_pit(stub_rows) == [],
      "overlapping/stretched transition stub (gap outside 60-120d) leaves TTM blank instead of summing mismatched spans")

# 5) Non-mixing tag selection: AVGO switches wholesale to ProfitLoss; INTC keeps NetIncomeLoss; LRCX uses single consistent debt tag
saved_api = c.api, v._splits
try:
    c.reset_runtime_caches()
    calls = []
    v._splits = lambda sym: []

    def chip_api(base, path, **p):
        calls.append(dict(p))
        sym = p.get("symbol")
        fact = p.get("fact")
        if sym == "AVGO":
            if fact == "NetIncomeLoss":
                return [
                    {"span": "quarter", "period_ending": f"2024-{m:02d}-04", "period_beginning": f"2024-{m - 2:02d}-01",
                     "value": 1000.0, "reported_date": f"2024-{m:02d}-20", "form": "10-Q", "unit": "USD"}
                    for m in (2, 5, 8)
                ] + [{"span": "quarter", "period_ending": "2024-11-03", "period_beginning": "2024-08-05", "value": 4324.0, "reported_date": "2024-12-20", "form": "10-K", "unit": "USD"}]
            if fact == "ProfitLoss":
                return [
                    {"span": "quarter", "period_ending": e, "period_beginning": b, "value": val, "reported_date": fd, "form": "10-Q", "unit": "USD"}
                    for b, e, val, fd in (
                        ("2024-11-04", "2025-02-02", 5500.0, "2025-03-12"),
                        ("2025-02-03", "2025-05-04", 5600.0, "2025-06-10"),
                        ("2025-05-05", "2025-08-03", 5700.0, "2025-09-10"),
                        ("2025-08-04", "2025-11-02", 5800.0, "2025-12-18"),
                        ("2024-11-04", "2025-02-02", 5550.0, "2026-03-11"),
                    )
                ]
            if fact == "WeightedAverageNumberOfDilutedSharesOutstanding":
                return [{"span": "quarter", "period_ending": "2025-11-02", "period_beginning": "2025-08-04", "value": 4800.0, "reported_date": "2025-12-18", "form": "10-Q", "unit": "shares"}]
        if sym == "INTC":
            if fact == "NetIncomeLoss":
                return [
                    {"span": "quarter", "period_ending": f"2026-{m:02d}-28", "period_beginning": f"2026-{m - 2:02d}-01",
                     "value": 100.0, "reported_date": f"2026-{m:02d}-28", "form": "10-Q", "unit": "USD"}
                    for m in (3, 6, 9, 12)
                ]
            if fact == "ProfitLoss":
                return [{"span": "quarter", "period_ending": "2026-12-28", "period_beginning": "2026-10-01", "value": 999.0, "reported_date": "2027-01-25", "form": "10-Q", "unit": "USD"}]
            if fact == "WeightedAverageNumberOfDilutedSharesOutstanding":
                return [{"span": "quarter", "period_ending": "2026-12-28", "period_beginning": "2026-10-01", "value": 5000.0, "reported_date": "2027-01-25", "form": "10-Q", "unit": "shares"}]
        if sym == "LRCX":
            if fact == "LongTermDebt":
                return [{"span": "instant", "period_ending": "2026-06-28", "value": 3722.0, "reported_date": "2026-08-07", "form": "10-K", "unit": "USD"}]
            if fact == "LongTermDebtAndCapitalLeaseObligations":
                return [
                    {"span": "instant", "period_ending": "2026-06-28", "value": 3730.0, "reported_date": "2026-08-07", "form": "10-Q", "unit": "USD"},
                    {"span": "instant", "period_ending": "2026-09-27", "value": 3732.0, "reported_date": "2026-10-22", "form": "10-Q", "unit": "USD"},
                ]
        return []

    c.api = chip_api
    avgo_sf = v.sec_figures("AVGO", items=["net_income", "shares"])
    check(any(p.get("point_in_time") == "true" for p in calls), "_sec_rows sends point_in_time='true' when point_in_time=True")
    check(v._latest_end(avgo_sf["net_income"]) == "2025-11-02"
          and len(avgo_sf["_annual"]["net_income"]) == 5
          and all(r["value"] >= 5500.0 for r in avgo_sf["_annual"]["net_income"]),
          f"AVGO switches wholesale to ProfitLoss without mixing NetIncomeLoss rows: {avgo_sf}")
    calls.clear()
    intc_sf = v.sec_figures("INTC", items=["net_income", "shares"])
    check(not any(p.get("fact") == "ProfitLoss" for p in calls) and intc_sf["net_income"] == [("2026-12-28", 400.0, D(2026, 12, 28))],
          f"INTC with current NetIncomeLoss never switches to ProfitLoss (NCI): {intc_sf}")
    lrcx_debt = v._sec_rows("LRCX", v.INSTANT["debt"], instant=True, point_in_time=True)
    check([(r["end"], r["value"]) for r in lrcx_debt] == [("2026-06-28", 3730.0), ("2026-09-27", 3732.0)],
          f"LRCX picks single consistent lease-inclusive debt tag across all periods: {lrcx_debt}")
finally:
    c.api, v._splits = saved_api
    c.reset_runtime_caches()

# 6) run_backend.sec_pit_records + install_sec_pit_facts ProviderInterface & Fetcher integration
import asyncio  # noqa: E402
import dataclasses  # noqa: E402
from openbb_core.app.provider_interface import ProviderInterface  # noqa: E402
from openbb_sec.models.compare_company_facts import SecCompareCompanyFactsFetcher as SECFetch  # noqa: E402

saved_sec = SECFetch.aextract_data, SECFetch.extract_data, sec_frames.symbol_map, sec_frames.fetch_data
try:
    async def fake_map(_sym):
        return "0000002488"

    async def fake_fetch(_url, _uc, _p):
        return {
            "cik": "0000002488", "taxonomy": "us-gaap", "tag": "NetIncomeLoss",
            "label": "Net Income (Loss)", "description": "", "entityName": "AMD",
            "units": {"USD": [
                {"start": "2017-01-01", "end": "2017-12-30", "val": 43e6, "filed": "2018-02-27", "form": "10-K", "accn": "0001"},
                {"start": "2017-01-01", "end": "2017-12-30", "val": -33e6, "filed": "2019-02-08", "form": "10-K", "accn": "0002"},
            ]},
        }

    sec_frames.symbol_map, sec_frames.fetch_data = fake_map, fake_fetch
    pre_names = [f.name for f in dataclasses.fields(ProviderInterface().params["CompareCompanyFacts"]["extra"])]
    check("point_in_time" not in pre_names, "pre-warmed ProviderInterface lacks point_in_time before patch")
    rb.install_sec_pit_facts([])
    pi_names = [f.name for f in dataclasses.fields(ProviderInterface().params["CompareCompanyFacts"]["extra"])]
    check("point_in_time" in pi_names, f"ProviderInterface refreshes singleton and exposes point_in_time query param: {pi_names}")
    fetched_pit = asyncio.run(SECFetch().fetch_data({"symbol": "AMD", "fact": "NetIncomeLoss", "point_in_time": True}, {}))
    pit_rows = getattr(fetched_pit, "result", fetched_pit)
    check(sorted((str(d.reported_date), d.value) for d in pit_rows) == [("2018-02-27", 43e6), ("2019-02-08", -33e6)],
          f"Fetcher.fetch_data with point_in_time=True preserves both original and restated filings: {pit_rows}")
finally:
    SECFetch.aextract_data, SECFetch.extract_data, sec_frames.symbol_map, sec_frames.fetch_data = saved_sec
pit_input = [
    # Original + unchanged comparative + reverted restatement
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-04-30", "val": 100.0, "filed": "2017-05-25", "form": "10-Q", "accn": "0001"},
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-04-30", "val": 100.0, "filed": "2018-05-24", "form": "10-Q", "accn": "0002"},  # unchanged comp -> collapsed
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-04-30", "val": 999.0, "filed": "2017-05-10", "form": "8-K", "accn": "0000"},   # 8-K dropped
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-07-30", "val": 1091.0, "filed": "2017-08-23", "form": "10-Q", "accn": "0003"},
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-07-30", "val": 583.0, "filed": "2018-08-22", "form": "10-Q", "accn": "0004"},
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-07-30", "val": 1091.0, "filed": "2019-02-21", "form": "10-K", "accn": "0005"},  # reverted -> kept!
    # 9M + FY vintage Q4 derivation (and later comparative 9M restatement + later FY restatement)
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-10-29", "val": 1500.0, "filed": "2017-11-21", "form": "10-Q", "accn": "0006"},
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2018-01-28", "val": 2100.0, "filed": "2018-02-28", "form": "10-K", "accn": "0007"},  # orig Q4 = 600
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2017-10-29", "val": 1400.0, "filed": "2018-11-20", "form": "10-Q", "accn": "0008"},  # comp 9M restatement (no mixed-GAAP Q4!)
    {"symbol": "NVDA", "unit": "USD", "start": "2017-01-30", "end": "2018-01-28", "val": 2050.0, "filed": "2019-02-21", "form": "10-K", "accn": "0009"},  # restated FY -> Q4 = 650
]
pit_out = rb.sec_pit_records(pit_input, sec_frames)
check(not any(r.get("form") == "8-K" for r in pit_out), "sec_pit_records filters out 8-K rows")
q1_vers = [(r["filed"], r["val"]) for r in pit_out if r.get("start") == "2017-01-30" and r.get("end") == "2017-04-30" and r.get("span") == "quarter"]
check(q1_vers == [("2017-05-25", 100.0)], f"unchanged comparative collapsed to single original filing: {q1_vers}")
h1_vers = [(r["filed"], r["val"]) for r in pit_out if r.get("start") == "2017-01-30" and r.get("end") == "2017-07-30" and r.get("span") == "h1"]
check(h1_vers == [("2017-08-23", 1091.0), ("2018-08-22", 583.0), ("2019-02-21", 1091.0)],
      f"run-length dedup preserves reverted value 1091 -> 583 -> 1091: {h1_vers}")
q4_vers = [(r["filed"], r["val"]) for r in pit_out if r.get("end") == "2018-01-28" and r.get("span") == "quarter"]
check(q4_vers == [("2018-02-28", 600.0), ("2019-02-21", 650.0)],
      f"vintage Q4 derivation ignores comparative 9M restatement until restated FY arrives: {q4_vers}")

# 7) Late filer (SMCI): PIT uses actual filing date even past deadline (no lookahead); D&A waits for both parts;
#    multi-era debt backfill keeps older history without blending overlapping years; ProfitLoss disclosure & 20-F skip
smci_rows = [
    {"span": "quarter", "begin": "2023-07-01", "end": "2023-09-30", "value": 100.0, "filed": "2023-11-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2023-10-01", "end": "2023-12-31", "value": 100.0, "filed": "2024-02-01", "form": "10-Q"},
    {"span": "quarter", "begin": "2024-01-01", "end": "2024-03-31", "value": 100.0, "filed": "2024-05-01", "form": "10-Q"},
    {"span": "annual", "begin": "2023-07-01", "end": "2024-06-30", "value": 450.0, "filed": "2025-02-25", "form": "10-K"},
]
pit_smci = v.AsOf(v.ttm_pit(smci_rows))
check(pit_smci.at(D(2025, 1, 15)) is None and pit_smci.at(D(2025, 2, 25)) == ("2024-06-30", 450.0, D(2025, 2, 25)),
      f"SMCI late 10-K (2025-02-25) is not backdated to 90d deadline 2024-09-28: {pit_smci.at(D(2025, 1, 15))}")

from composite.equity import filings as fl  # noqa: E402

saved_rev = c.api, v._splits, v._profile, c.history
try:
    c.reset_runtime_caches()
    v._splits = lambda sym: []
    v._profile = lambda sym: {"industry": "Semiconductors", "currency": "USD"}
    rev_calls = []

    def rev_api(base, path, **p):
        rev_calls.append(dict(p))
        sym, fact = p.get("symbol"), p.get("fact")
        if sym == "DACO" and fact == "OperatingIncomeLoss":
            return [{"span": "quarter", "period_ending": f"2025-{m:02d}-30", "period_beginning": f"2025-{m - 2:02d}-01",
                     "value": 50.0, "reported_date": f"2025-{m:02d}-30", "form": "10-Q", "unit": "USD"} for m in (3, 6, 9, 12)]
        if sym == "DACO" and fact == "Depreciation":
            return [{"span": "quarter", "period_ending": f"2025-{m:02d}-30", "period_beginning": f"2025-{m - 2:02d}-01",
                     "value": 10.0, "reported_date": "2025-12-30" if m == 12 else f"2025-{m:02d}-30", "form": "10-Q", "unit": "USD"} for m in (3, 6, 9, 12)]
        if sym == "DACO" and fact == "AmortizationOfIntangibleAssets":
            return [{"span": "quarter", "period_ending": f"2025-{m:02d}-30", "period_beginning": f"2025-{m - 2:02d}-01",
                     "value": 5.0, "reported_date": "2026-01-20" if m == 12 else f"2025-{m:02d}-30", "form": "10-Q", "unit": "USD"} for m in (3, 6, 9, 12)]
        if sym == "AMD2":
            if fact == "DebtLongtermAndShorttermCombinedAmount":
                return [
                    {"span": "instant", "period_ending": "2025-12-27", "value": 3000.0, "reported_date": "2026-02-01", "form": "10-K", "unit": "USD"},
                    {"span": "instant", "period_ending": "2026-09-26", "value": 3200.0, "reported_date": "2026-10-30", "form": "10-Q", "unit": "USD"},
                ]
            if fact == "LongTermDebtNoncurrent":
                return [
                    {"span": "instant", "period_ending": "2018-12-29", "value": 1100.0, "reported_date": "2019-02-08", "form": "10-K", "unit": "USD"},
                    {"span": "instant", "period_ending": "2025-12-27", "value": 2999.0, "reported_date": "2026-02-01", "form": "10-K", "unit": "USD"},
                    {"span": "instant", "period_ending": "2026-09-26", "value": 2999.0, "reported_date": "2026-10-30", "form": "10-Q", "unit": "USD"},
                ]
            if fact == "LongTermDebt":
                return [{"span": "instant", "period_ending": "2018-12-29", "value": 9999.0, "reported_date": "2019-02-08", "form": "10-K", "unit": "USD"}]
        if sym == "AVGO2":
            if fact == "NetIncomeLoss":
                return [{"span": "annual", "period_ending": "2024-11-03", "period_beginning": "2023-10-30", "value": 5895.0, "reported_date": "2024-12-20", "form": "10-K", "unit": "USD"}]
            if fact == "ProfitLoss":
                return [{"span": "quarter", "period_ending": e, "period_beginning": b, "value": 5000.0, "reported_date": fd, "form": "10-Q", "unit": "USD"}
                        for b, e, fd in (("2025-11-03", "2026-02-01", "2026-03-11"), ("2026-02-02", "2026-05-03", "2026-06-09"),
                                         ("2026-05-04", "2026-08-02", "2026-09-10"), ("2026-08-03", "2026-11-01", "2026-12-15"))]
            if fact == "WeightedAverageNumberOfDilutedSharesOutstanding":
                return [{"span": "quarter", "period_ending": "2026-11-01", "period_beginning": "2026-08-03", "value": 4800.0, "reported_date": "2026-12-15", "form": "10-Q", "unit": "shares"}]
        if sym == "AMD3":
            if fact == "DebtLongtermAndShorttermCombinedAmount":
                return [
                    {"span": "instant", "period_ending": "2012-12-31", "value": 500.0, "reported_date": "2013-02-15", "form": "10-K", "unit": "USD"},
                    {"span": "instant", "period_ending": "2023-06-30", "value": 2900.0, "reported_date": "2023-08-01", "form": "10-Q", "unit": "USD"},
                    {"span": "instant", "period_ending": "2023-12-31", "value": 3000.0, "reported_date": "2024-02-01", "form": "10-K", "unit": "USD"},
                ]
            if fact == "LongTermDebt":
                return [
                    {"span": "instant", "period_ending": "2018-12-31", "value": 1500.0, "reported_date": "2019-02-15", "form": "10-K", "unit": "USD"},
                    {"span": "instant", "period_ending": "2023-09-30", "value": 2800.0, "reported_date": "2023-11-01", "form": "10-Q", "unit": "USD"},
                ]
        if sym == "TWOF,PEER":
            return [
                {"symbol": "TWOF", "span": "annual", "period_ending": "2025-12-31", "period_beginning": "2025-01-01", "value": 50.0, "form": "20-F", "unit": "USD"},
                {"symbol": "PEER", "span": "annual", "period_ending": "2026-03-31", "period_beginning": "2025-04-01", "value": 60.0, "form": "20-F", "unit": "USD"},
            ] if fact == "NetIncomeLoss" else []
        if sym in ("TWOF2,USQ2", "TWOF2"):
            if fact == "ProfitLoss":
                raise c.CompositeError("SEC upstream down", kind="upstream")
            return [
                {"symbol": "TWOF2", "span": "annual", "period_ending": "2025-12-31", "period_beginning": "2025-01-01", "value": 50.0, "form": "20-F", "unit": "USD"},
                {"symbol": "USQ2", "span": "quarter", "period_ending": "2026-09-30", "period_beginning": "2026-07-01", "value": 20.0, "form": "10-Q", "unit": "USD"},
            ] if fact == "NetIncomeLoss" else []
        return []

    c.api = rev_api
    da_pts = v._fill_da("DACO", [], [{"end": "2025-12-30"}], point_in_time=True)
    check(da_pts == [("2025-12-30", 60.0, D(2026, 1, 20))],
          f"D&A waits for both Depreciation (12-30) and Amortization (01-20) before emitting: {da_pts}")
    amd2_debt = v._sec_rows("AMD2", v.INSTANT["debt"], instant=True, point_in_time=True)
    check([(r["end"], r["value"]) for r in amd2_debt] == [("2018-12-29", 9999.0), ("2025-12-27", 3000.0), ("2026-09-26", 3200.0)],
          f"Multi-era debt backfill keeps pre-2019 history without mixing inside 2025-2026: {amd2_debt}")
    amd3_debt = v._sec_rows("AMD3", v.INSTANT["debt"], instant=True, point_in_time=True)
    check([(r["end"], r["value"]) for r in amd3_debt] == [("2012-12-31", 500.0), ("2018-12-31", 1500.0), ("2023-06-30", 2900.0), ("2023-12-31", 3000.0)],
          f"Multi-era gap fills 2018 across stray 2012 while omitting single-quarter 2023-09-30 from fallback tag: {amd3_debt}")
    c.history = lambda syms, start, interval="1d": c.PriceHistory({"AVGO2": [("2026-12-10", 10.0), ("2026-12-20", 10.0)]})
    vb_pl = v.bands("AVGO2", "pe", today=D(2026, 12, 22))
    check("ProfitLoss" in vb_pl["convention"] and "without required filings left blank" in vb_pl["convention"],
          f"ProfitLoss disclosure + missing-filing note vs denominator note: {vb_pl['convention']}")
    rev_calls.clear()
    twof = fl._facts(["TWOF", "PEER"], "net_income")
    check("TWOF" in twof and not any(p.get("fact") == "ProfitLoss" for p in rev_calls),
          f"Annual 20-F peers within 240d of each other never trigger optional ProfitLoss fetch: {rev_calls}")
    pnotes = []
    ptok = c._PARTIAL_NOTES.set(pnotes)
    try:
        opt_fail = fl._facts(["TWOF2", "USQ2"], "net_income")
        check("TWOF2" in opt_fail and pnotes == [],
              f"Failed optional ProfitLoss check on already-covered 20-F peer does not mark card partial: {pnotes}")
    finally:
        c._PARTIAL_NOTES.reset(ptok)
finally:
    c.api, v._splits, v._profile, c.history = saved_rev
    c.reset_runtime_caches()

done("test_valuation")
