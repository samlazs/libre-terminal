"""Table presentation (package 3): readable headers, number formats by column meaning, curated default columns,
board precision by instrument, and the dashboard layout rules (merged tabs keep saved edits, keyless landing tabs)."""

import json
import re

from _common import REAL_LOCAL, check, done

import columns as C
import composite

# ---- headers: no raw snake_case, acronyms right, units kept where the source wrote them by hand
check(C.header("percent_change") == "Change %", "percent_change reads 'Change %'")
# review A6: USAspending "percent_of_total" (70.47, suffix "%", tip "as a percentage") printed "70.47%%"
_pt = C.classify({"field": "percent_of_total", "cellDataType": "number", "suffix": "%", "headerTooltip": "The entry's share of the breakdown total, as a percentage."})
check(_pt.get("formatterFn") == "percent" and _pt.get("suffix") == "", "a percent formatter drops the source's own % suffix (no %%)")
check(C.UNIT_HEADERS["economy/export_destinations"]["value"] == "Exports (USD millions)", "export destinations say USD millions")
# review H1: maturity buckets of primary dealer positioning
check([C.header(f) for f in ("tips_gt_6_years_lt_11_years", "tips_gt_11_years", "coupons_lte_2_years", "tips_lt_2_years", "coupons_agency")]
      == ["TIPS 6-11Y", "TIPS >11Y", "Coupons <=2Y", "TIPS <2Y", "Coupons Agency"], "dealer maturity buckets: " + str([C.header(f) for f in ("tips_gt_6_years_lt_11_years", "tips_gt_11_years", "coupons_lte_2_years", "tips_lt_2_years")]))
# review H1: Daily Treasury Statement headers short, every preset column has a header, the repeated closing balances left out
_dts_h, _dts_p = C.UNIT_HEADERS["ustreasury/daily_statement"], C.PRESETS["ustreasury/daily_statement"]
check(_dts_h["Treasury General Account (TGA) Closing Balance (Open Today)"] == "TGA closing balance ($)"
      and all(f in _dts_h for f in _dts_p[1:]) and len(_dts_p) == 11 and not any("Closing Balance (Open Month)" in f for f in _dts_p),
      "daily statement: short headers, closing balance once")
# review B4: the CFTC outright curve is in the currency per USD (EURUSD 0.9114 = 1 / 1.0972); staleness shown by default
check(C.UNIT_HEADERS["cftc/fx_forward_curve"]["rate"] == "Forward (currency per USD)" and "staleness_days" in C.PRESETS["cftc/fx_forward_curve"],
      "FX forward curve says currency per USD and shows how old each tenor is")
# review D1: ticker cells are clickable (they open the ticker); numbers, other ids and existing click rules unchanged
check(C.classify({"field": "symbol", "cellDataType": "text"}) == {"renderFn": "cellOnClick", "renderFnParams": {"groupByParamName": "symbol"}}
      and C.classify({"field": "ticker"}).get("renderFn") == "cellOnClick"
      and "renderFn" not in C.classify({"field": "symbol", "renderFn": "cellOnClick", "renderFnParams": {"groupByParamName": "symbol"}})
      and "renderFn" not in C.classify({"field": "contract_symbol"}), "ticker cells open the ticker")
# review D2: the bill id a runtime patch adds to Congressional search rows is clickable (sets bill_id) and shown first
check(C.classify({"field": "bill_id"}) == {"renderFn": "cellOnClick", "renderFnParams": {"groupByParamName": "bill_id"}}, "bill id cells set bill_id")
check(all(C.classify({"field": f}).get("renderFnParams") == {"groupByParamName": f} for f in ("cusip", "crd", "rssd_id")), "CUSIP, CRD and RSSD cells set their parameter")
_cs = C.spec({"widgetId": "uscongress_search_congress_gov_obb", "data": {"table": {"columnsDefs": [{"field": "package_id"}, {"field": "title"}]}}}, "uscongress/search")
check(_cs["f"]["bill_id"]["renderFn"] == "cellOnClick" and _cs["show"][:2] == ["date", "bill_id"], "Congressional search spec: bill_id clickable and shown")
# review F1: values first on option chains and FRED search
check(C.preset_for("derivatives/options/chains")[:8] == ["expiration", "dte", "strike", "option_type", "bid", "ask", "last_trade_price", "volume"]
      and C.preset_for("economy/fred_search")[0] == "series_id", "option chains show bid/ask/IV first; FRED search shows the series id")
# review A6: EIA power units from the source's tooltips
check(C.unit_from_tip("Net Generation (megawatthours). Withheld or unavailable values return as null.") == "MWh"
      and C.unit_from_tip("Outage (megawatts). Withheld ...") == "MW" and C.unit_from_tip("Percent outage (percent).") is None, "EIA MWh / MW header units")
check(C.header("change_percent") == "Change %", "change_percent reads 'Change %'")
check(C.header("ma_50d") == "50-day MA" and C.header("ma200") == "200-day MA", "moving averages")
check(C.header("pe_ratio") == "P/E" and C.header("forward_pe") == "Forward P/E", "P/E")
check(C.header("coupons_gt_3_years_lt_6_years", "Coupons 3-6Y") == "Coupons 3-6Y"
      and C.header("ltv_lte_80_fico_lt_680", "30-Year Fixed Rate Conforming LTV <= 80 FICO < 680").endswith("FICO < 680")
      and C.header("total_value", "Total Value") == "Total Value", "source labels with ranges kept (review H1); plain title case rebuilt as before")
check(C.header("eps_ttm") == "EPS TTM", "EPS TTM")
check(C.header("sveny10") == "10Y zero-coupon" and C.header("svenpy02") == "2Y par", "Svensson curve columns")
check(C.header("year_10") == "10Y" and C.header("month_3") == "3M" and C.header("tenor_1y") == "1Y", "tenors")
check(C.header("year_1_year_3") == "1-3Y", "maturity buckets")
check(C.header("liquid_bbb") == "Liquid BBB", "ratings in capitals")
check(C.header("mcap_usd", "Mkt cap $bn") == "Mkt cap $bn", "a header written by hand at the source is kept")
check(C.header("cik") == "CIK" and C.header("cusip") == "CUSIP", "identifiers in capitals")
check("_" not in C.header("growth_total_operating_income"), "no underscores")

# ---- formats by column meaning
def cls(field, tip="", fn=None, num=True, rfn=None):
    return C.classify({"field": field, "headerTooltip": tip, "formatterFn": fn, "cellDataType": "number" if num else "text", "renderFn": rfn})

check(cls("cik") == {"kind": "id", "formatterFn": "none"}, "CIK: no thousands separators")
check(cls("fiscal_year").get("kind") == "id", "years are ids")
check(cls("event_id", rfn="greenRed").get("renderFn", "x") is None, "an id is never coloured")
check(cls("number_of_analysts") == {}, "a count is not an id")
check(cls("high_yield", "highest accepted yield, as a normalized decimal (percent / 100).")["formatterFn"] == "normalizedPercent", "normalized decimal -> percent")
check(cls("relative_importance", "relative-importance weight, as a percentage of the parent index.").get("formatterFn") == "percent", "'as a percentage' -> percent sign")
check(cls("rate_change", "estimated rate change in percentage points over the comparison window.").get("suffix") == " pp", "percentage points -> pp")
lvl = cls("month_1", fn="normalizedPercent", rfn="greenRed")
check(lvl.get("renderFn", "x") is None, "a yield level loses green/red")
check("renderFn" not in cls("change_percent", fn="normalizedPercent", rfn="greenRed"), "a change keeps its colour")
check(cls("growth_total_revenue", fn="normalizedPercent").get("renderFn") == "greenRed", "growth gets green/red")
check(cls("tenor_1y", rfn="greenRed").get("renderFn", "x") is None, "OIS tenor columns are levels")
check(cls("volatility_1w", fn="percent", rfn="greenRed").get("renderFn", "x") is None, "volatility is a level")
check(C.unit_from_tip("over-the-month change, in thousands.") == "k", "thousands unit")
check(C.unit_from_tip("value in millions of USD") == "$mn", "millions of dollars unit")

# ---- specs and presets
w = {"data": {"table": {"columnsDefs": [
    {"field": "symbol", "headerName": "Symbol", "cellDataType": "text"},
    {"field": "percent_change", "headerName": "Percent Change", "cellDataType": "number", "formatterFn": "normalizedPercent", "renderFn": "greenRed"},
    {"field": "ma_50", "headerName": "Ma 50", "cellDataType": "number"},
    {"field": "year_high", "headerName": "Year High", "cellDataType": "number", "renderFn": "greenRed"}]}}}
s = C.spec(w, "equity/discovery/gainers")
check(s["show"][:4] == ["symbol", "percent_change", "change_percent", "price"] and s["show"].index("name") > s["show"].index("market_cap"),
      "movers preset: ticker, change %, price first; the long name after the numbers (review F1)")
check(s["h"]["percent_change"] == "Change %" and s["h"]["ma_50"] == "50-day MA", "spec carries readable headers")
check(s["f"]["year_high"].get("renderFn", "x") is None and "percent_change" not in s.get("f", {}), "spec strips colour from levels only")
check(C.preset_for("equity/discovery/undervalued_large_caps") == C.PRESETS["equity/discovery/*"], "glob presets")
check(len(C.preset_for("cftc/cot")) <= 12, "COT opens with at most 12 of its 320 columns")
check(C.preset_for("jodi/oil/production")[0] == "date" and "saudi_arabia" in C.preset_for("jodi/oil/production"), "JODI: date + main producers")
check(all(len(v) <= 16 for v in C.PRESETS.values()), "no preset is a wide table")
check(C.spec({"data": {"table": {"columnsDefs": []}}}) is None, "nothing to say -> no spec")
# review A2/A4: a horizon named "..._year" is not a calendar-year id (Finviz one_year 0.1003 must stay x100 -> 10.03%)
for _f in ("one_year", "ten_year", "volatility_one_year", "change_prior_year"):
    check(C.classify({"field": _f, "cellDataType": "number", "formatterFn": "normalizedPercent"}).get("kind") != "id", f"{_f} keeps its number format")
for _f in ("year", "fiscal_year", "calendar_year"):
    check(C.classify({"field": _f, "cellDataType": "number"}).get("kind") == "id", f"{_f} is still a year id (no thousands separator)")
# review A2: Yahoo ETF YTD return arrives in percent (SPY 11.69) while the widget declares x100 -> shown as 11.69%, labelled
_etf = C.spec({"widgetId": "etf_info_yfinance_obb", "data": {"table": {"columnsDefs": [
    {"field": "return_ytd", "cellDataType": "number", "formatterFn": "normalizedPercent"},
    {"field": "return_3y_avg", "cellDataType": "number", "formatterFn": "normalizedPercent"}]}}})
check(_etf["f"]["return_ytd"]["formatterFn"] == "percent" and _etf["f"].get("return_3y_avg", {}).get("formatterFn") != "percent"
      and "month-end" in _etf["h"]["return_ytd"], "ETF YTD return: percent, to last month-end; 3y average stays a fraction (A2)")


# ---- declared units (audit A1): percent, fraction or plain number comes from UNITS, never from the values
def unit_of(wid, field, tip=""):
    w = {"widgetId": wid, "data": {"table": {"columnsDefs": [{"field": field, "headerName": field, "cellDataType": "number", "headerTooltip": tip}]}}}
    return ((C.spec(w) or {}).get("f") or {}).get(field, {})


check(unit_of("equity_calendar_earnings_tmx_obb", "eps_surprise") == {"decimalPlaces": 2}, "TMX EPS surprise is dollars (0.52 - 0.27 = 0.25): plain number")
check(unit_of("equity_fundamental_ratios_fmp_obb", "price_to_earnings_growth_ratio_ttm") == {"decimalPlaces": 2}, "PEG is a ratio, not a percent")
check(unit_of("yfinance_industry_top_performing_yfinance_obb", "ytd_return") == {"formatterFn": "normalizedPercent", "decimalPlaces": 2}, "Yahoo YTD return is a fraction")
check(unit_of("yfinance_sectors_overview_yfinance_obb", "market_weight")["formatterFn"] == "normalizedPercent", "Yahoo sector weights are fractions (sum 1.0)")
check(unit_of("equity_screener_tmx_obb", "return_on_equity") == {"formatterFn": "percent", "decimalPlaces": 2}, "TMX screener ratios are already percent (RY ROE 16.49)")
check(unit_of("eia_nuclear_outages_us_nuclear_outages_eia_obb", "percent_outage")["formatterFn"] == "percent", "EIA outage share is percent (13,956 / 99,723 = 13.99)")
check(unit_of("etf_discovery_gainers_wsj_obb", "percent_change")["formatterFn"] == "percent", "WSJ ETF movers: percent")
check(unit_of("ustreasury_auction_results_us_treasury_obb", "allocation_percentage_decimals") == {"decimalPlaces": 0}, "a count of decimals is an integer")
raw = unit_of("fixedincome_bond_indices_fred_obb", "high_yield", "Emerging Markets High Yield.")
check("formatterFn" not in raw and "decimalPlaces" not in raw and raw["headerTooltip"].startswith("Emerging Markets High Yield. ")
      and "not confirmed" in raw["headerTooltip"], "unit set by a parameter (yield 8.71 or index 641.24): raw, with a note")
check(unit_of("some_other_widget", "eps_surprise") == {}, "UNITS is per widget: another source's column is not affected")
new_w = {"widgetId": "new_widget", "data": {"table": {"columnsDefs": [
    {"field": "ytd_return", "cellDataType": "number"},
    {"field": "dividend_yield", "cellDataType": "number", "formatterFn": "percent"},
    {"field": "growth", "cellDataType": "number", "headerTooltip": "Growth, as a normalized decimal."},
    {"field": "coupon_rate", "cellDataType": "number"},
    {"field": "fiscal_year", "cellDataType": "number"},
    {"field": "name_return", "cellDataType": "text"}]}}}
check(C.undeclared(new_w) == ["ytd_return", "coupon_rate"], "undeclared: percent- and yield-named numbers without any declared unit")
check(C.undeclared({"widgetId": "equity_calendar_earnings_tmx_obb", "data": {"table": {"columnsDefs": [{"field": "eps_surprise", "cellDataType": "number"}]}}}) == [],
      "a UNITS entry declares the column")

# every recorded catalog column named like a percent or a yield has a declared unit; every UNITS entry names a real column
cat = {b: REAL_LOCAL / "cache" / f"catalog-{b}.json" for b in ("v5", "v4")}
if not all(p.exists() for p in cat.values()):
    print("test_columns: catalog unit replay skipped (no recorded catalogs yet: start the terminal once)")
else:
    missing, fields = [], {}
    for b, p in cat.items():
        for wid, w in json.loads(p.read_text(encoding="utf-8")).items():
            if isinstance(w, dict):
                missing += [f"{wid}.{f}" for f in C.undeclared(w)]
                fields.setdefault(wid, set()).update(str(c.get("field")) for c in (((w.get("data") or {}).get("table") or {}).get("columnsDefs") or []))
    check(not missing, "every percent- or yield-named number column has a declared unit: " + ", ".join(missing[:6]))
    # a widget that lists no columns (options chains: columns come from the rows) may declare fields; it must still exist
    rowcols = {wid for wid, fs in fields.items() if not (fs - {"None", "", "date"})}     # Cleveland Fed lists only "date"
    stale = [f"{wid}.{f}" for wid, fs in C.UNITS.items() for f in fs if f not in fields.get(wid, ()) and wid not in rowcols
             and f not in C.PATCH_FIELDS.get(wid, ())]
    check(not stale, "every UNITS entry names a catalog column: " + ", ".join(stale[:6]))
    check(all(wid in fields for wid in list(C.UNITS) + list(C.WIDGET_HEADERS)), "every UNITS / WIDGET_HEADERS widget is in the catalog")
    check(all(u in C.UNIT_FORMATS or u == "raw" for fs in C.UNITS.values() for u in fs.values()), "every UNITS value is a known unit")

# ---- SEC / filings presets and readable headers (clickable Document column visible early)
for ep in ("sec/company_filings", "equity/fundamental/filings", "sec/full_text_search",
           "sec/latest_financial_reports", "equity/discovery/latest_financial_reports",
           "equity/discovery/filings", "sec/filing_headers", "regulators/sec/filing_headers",
           "equity/compare/company_facts"):
    p = C.preset_for(ep)
    check(p is not None and 5 <= len(p) <= 16, f"{ep}: preset present and compact")

cf_w = {"data": {"table": {"columnsDefs": [
    {"field": "filing_date", "headerName": "Filing Date", "cellDataType": "date"},
    {"field": "report_type", "headerName": "Form", "cellDataType": "text"},
    {"field": "report_url", "headerName": "Document", "cellDataType": "text", "renderFn": "cellOnClick"},
    {"field": "accession_number", "headerName": "Accession", "cellDataType": "text"},
    {"field": "filing_detail_url", "headerName": "Index Page", "cellDataType": "text"}]}}}
cf_s = C.spec(cf_w, "sec/company_filings")
check(cf_s["show"][:3] == ["filing_date", "report_type", "report_url"], "company_filings: Document is col 3")
check(cf_s["h"] == {"report_type": "Form", "report_url": "Document", "accession_number": "Accession", "filing_detail_url": "Index Page"},
      "company_filings: keeps Form, Document, Accession, Index Page")
check("title" in C.preset_for("equity/fundamental/filings") and "description" in C.preset_for("equity/fundamental/filings")
      and "pdf_url" in C.preset_for("equity/fundamental/filings"), "equity/fundamental/filings covers Yahoo, TMX, and Nasdaq fields")

fts_w = {"data": {"table": {"columnsDefs": [
    {"field": "filing_date", "headerName": "Filing Date", "cellDataType": "date"},
    {"field": "form", "headerName": "Form", "cellDataType": "text"},
    {"field": "name", "headerName": "Company", "cellDataType": "text"},
    {"field": "url", "headerName": "Document", "cellDataType": "text", "renderFn": "cellOnClick"}]}}}
fts_s = C.spec(fts_w, "sec/full_text_search")
check("url" in fts_s["show"][:5] and fts_s["h"] == {"name": "Company", "url": "Document"}, "full_text_search: Company and Document headers")

for lfr_ep in ("sec/latest_financial_reports", "equity/discovery/latest_financial_reports"):
    lfr_w = {"data": {"table": {"columnsDefs": [
        {"field": "filing_date", "headerName": "Filing Date", "cellDataType": "date"},
        {"field": "period_ending", "headerName": "Period", "cellDataType": "date"},
        {"field": "symbol", "headerName": "Symbol", "cellDataType": "text"},
        {"field": "name", "headerName": "Company", "cellDataType": "text"},
        {"field": "report_type", "headerName": "Form", "cellDataType": "text"},
        {"field": "url", "headerName": "Document", "cellDataType": "text", "renderFn": "cellOnClick"}]}}}
    lfr_s = C.spec(lfr_w, lfr_ep)
    check("price" not in lfr_s["show"] and "url" in lfr_s["show"][:6], f"{lfr_ep}: not _MOVERS, Document early")
    check(lfr_s["h"] == {"period_ending": "Period", "name": "Company", "report_type": "Form", "url": "Document"},
          f"{lfr_ep}: Period, Company, Form, Document headers")

check("final_link" in C.preset_for("equity/discovery/filings") and "price" not in C.preset_for("equity/discovery/filings"),
      "equity/discovery/filings overrides _MOVERS")

for fh_ep in ("sec/filing_headers", "regulators/sec/filing_headers"):
    fh_w = {"data": {"table": {"columnsDefs": [
        {"field": "sequence", "headerName": "Sequence"},
        {"field": "type", "headerName": "Document Type"},
        {"field": "filename", "headerName": "Filename"},
        {"field": "content_description", "headerName": "Description"},
        {"field": "url", "headerName": "URL", "renderFn": "cellOnClick"}]}}}
    fh_s = C.spec(fh_w, fh_ep)
    check(fh_s["show"].index("url") <= 3, f"{fh_ep}: Document column appears early")
    check(fh_s["h"] == {"sequence": "Seq", "type": "Document Type", "url": "Document", "content_description": "Description"},
          f"{fh_ep}: readable Seq, Document Type, Document, Description headers")

check(C.preset_for("equity/compare/company_facts")[:5] == ["period_ending", "fiscal_year", "fiscal_period", "value", "unit"],
      "company_facts: period and value first")

# ---- boards: one precision per instrument
check(composite.price_decimals(5432.1) == 2 and composite.price_decimals(1.1052) == 4 and composite.price_decimals(152.3) == 2, "index 2, EUR/USD 4, USD/JPY 2")
check(composite.price_decimals(0.153) == 5 and composite.price_decimals(0.00002) == 6, "sub-dollar coins keep their digits")
row = composite._quote_row("Euro", "EURUSD=X", [("2026-01-0%d" % (i + 1), 1.10 + i / 1000) for i in range(5)])
check(row["_fmt"]["price"]["decimalPlaces"] == 4 and row["_fmt"]["chg"]["decimalPlaces"] == 4, "EUR/USD row: price and change at 4 decimals")
yen = composite._quote_row("Japanese Yen", "JPY=X", [("2026-01-0%d" % (i + 1), 150.0 + i) for i in range(5)])
check(yen["_fmt"]["price"]["suffix"] == " USD/JPY" and yen["_fmt"]["price"]["decimalPlaces"] == 2, "yen row keeps its pair label, 2 decimals")
# review A6: futures rows say their quoting unit (TTF EUR/MWh beside Henry Hub $/MMBtu); equities say nothing
_ttf = composite._quote_row("Natural Gas (Europe TTF)", "TTF=F", [("2026-01-0%d" % (i + 1), 85.0 + i) for i in range(5)])
_ng = composite._quote_row("Natural Gas (U.S.)", "NG=F", [("2026-01-0%d" % (i + 1), 3.1 + i / 100) for i in range(5)])
_spy = composite._quote_row("S&P 500", "SPY", [("2026-01-0%d" % (i + 1), 600.0 + i) for i in range(5)])
check(_ttf["_fmt"]["price"]["suffix"] == " EUR/MWh" and _ng["_fmt"]["price"]["suffix"] == " $/MMBtu" and "suffix" not in _spy["_fmt"]["price"],
      "futures: TTF EUR/MWh, Henry Hub $/MMBtu; SPY no unit")

# ---- layout rules in the generator
import build_dashboards as B  # noqa: E402

ids = {d[0] if isinstance(d, tuple) else d["id"]: d for d in B.DASHBOARDS}
check("regulation" not in ids, "SEC & Regulation folded into SEC Filings & Enforcement")
for did, ren in B.RENAMED_TABS.items():
    check(did in ids, f"renamed tabs belong to a dashboard ({did})")
    names = {B.slug(t["name"]) for t in ids[did]["tabs"]}
    for old, new in ren.items():
        check(new in names, f"{did}: {old} follows to an existing tab ({new})")
        check(old not in names or old == new, f"{did}: {old} no longer exists as its own tab")
for d in B.DASHBOARDS:
    for t in d["tabs"]:
        n = sum(len(r) for r in t["rows"]) + sum(len(c[1]) for c in (t["columns"] or []))
        check(n <= 14, f"{d['name']} > {t['name']}: at most 14 placed cards before automatic ones")

def spec_titles(tab):
    specs = [s for r in tab["rows"] for s in r] + [s for c in (tab["columns"] or []) for s in c[1]] + [s for s in tab["auto"] if isinstance(s, dict)]
    return [s.get("title") or "" for s in specs]


INSTRUCTION = re.compile(r"\((press|follows|pick|example)\b|click any", re.I)
for d in B.DASHBOARDS:
    for t in d["tabs"]:
        for title in spec_titles(t):
            check(not INSTRUCTION.search(title), f"title without instructions: {title}")
check(not any(INSTRUCTION.search(v) for v in B.TITLES.values()), "title map without instructions")

# ---- options chains (audit C4): the backend sends no column list, so declared units must reach fields found in the rows.
# Probed 2026-11-17: Cboe AAPL IV median 0.5289, TMX RY 0.271 (fractions); Deribit BTC 57.46 (percent; prices carried to USD)
def chain(wid):
    return C.spec({"widgetId": wid, "data": {"table": {"columnsDefs": [{}]}}}, "derivatives/options/chains") or {}
cb, tx, db = chain("derivatives_options_chains_cboe_obb"), chain("derivatives_options_chains_tmx_obb"), chain("derivatives_options_chains_deribit_obb")
check(cb.get("f", {}).get("implied_volatility", {}).get("formatterFn") == "normalizedPercent", "Cboe IV 0.5289 reads 52.89%")
check(tx.get("f", {}).get("implied_volatility", {}).get("formatterFn") == "normalizedPercent", "TMX IV 0.271 reads 27.10%")
check(all(db.get("f", {}).get(k, {}).get("formatterFn") == "percent" for k in ("implied_volatility", "bid_iv", "ask_iv")), "Deribit IV 57.46 reads 57.46%")
check(db.get("h", {}).get("bid") == "Bid (USD)" and db.get("h", {}).get("mark") == "Mark (USD)" and "bid" not in cb.get("h", {}) and "bid" not in tx.get("h", {}),
      "Deribit prices say USD (not BTC); Cboe and TMX headers untouched (TMX quotes in CAD)")
check(cb["h"]["dte"] == "DTE (full days)" and tx["h"]["dte"] == db["h"]["dte"] == "DTE (calendar)", "DTE headers name each source's convention")
_to = C.spec({"widgetId": "cboe_options_trade_optimizer_custom_obb", "data": {"table": {"columnsDefs": [
    {"field": "annualized_return", "cellDataType": "number", "formatterFn": "percent"}]}}}, "cboe/options/trade_optimizer") or {}
check(_to.get("f", {}).get("annualized_return", {}).get("formatterFn") == "normalizedPercent",
      "Cboe optimizer annualized 5.4919 reads 549.19% (0.4815 over 32 days x 365 / 32), not 5.49%")
fr = C.spec({"widgetId": "yfinance_funds_risk_yfinance_obb", "data": {"table": {"columnsDefs": [{"field": "volatility", "cellDataType": "number"}]}}}) or {}
check(fr.get("f", {}).get("volatility", {}).get("formatterFn") == "normalizedPercent", "Yahoo fund risk volatility 0.155 reads 15.51%")
yf = chain("derivatives_options_chains_yfinance_obb")     # the chain placed on Company > Options and Derivatives > Chain
check(yf["f"]["implied_volatility"]["formatterFn"] == "normalizedPercent" and yf["h"]["dte"] == "DTE (calendar)", "Yahoo chain IV 0.4304 reads 43.04%")

# UX D2 / F2: TRACE bond history & Treasury auctions surface date first
check(C.preset_for("finra/fixedincome/bond_historical")[:3] == ["date", "price", "last_sale_yield"], "TRACE bond history shows date, price, yield first")
check(C.preset_for("fixedincome/government/treasury_auctions")[:2] == ["auction_date", "security_term"], "Treasury auctions surface auction_date first")

# UX F1 / F2: Federal Spending, Trade Ports/Exports, Nordics Fundamentals, and COT % OI
check(C.preset_for("ustreasury/award_search")[:2] == ["recipient_name", "award_amount"], "award_search surfaces recipient_name and award_amount first")
check(C.preset_for("ustreasury/recipient_search")[:2] == ["name", "amount"], "recipient_search surfaces name and amount first")
check(C.preset_for("economy/shipping/port_info")[:4] == ["port_name", "country", "continent", "vessel_count_total"], "port_info surfaces port_name, country, vessel_count_total first")
check(C.preset_for("economy/export_destinations")[:3] == ["destination_country", "value", "units"], "export_destinations surfaces destination_country, value, units first")
check(C.preset_for("nasdaq/nordic/fundamentals") == ["section", "label", "period", "value"], "nordic fundamentals preset")
cot_p = C.preset_for("cftc/cot")
cot_h = C.UNIT_HEADERS["cftc/cot"]
check("open_interest_pct_non_commercial_long_all" in cot_p and "open_interest_pct_non_commercial_short_all" in cot_p
      and cot_h["open_interest_pct_non_commercial_long_all"] == "Non-comm. long (% OI)"
      and cot_h["open_interest_pct_non_commercial_short_all"] == "Non-comm. short (% OI)", "COT % OI columns and headers")

# ---- P3-02: declared units on the Terminal's own tables; tooltip inference only on raw backend cards, never growing
import xwidgets  # noqa: E402

xw = xwidgets.widgets()
xcols = {(wid, c["field"]): c for wid, w in xw.items() for c in (((w.get("data") or {}).get("table") or {}).get("columnsDefs") or [])
         if c.get("cellDataType") == "number"}
check(all(C.x_unit_ok(c.get("unit")) for c in xcols.values()), "every /x/* number column carries a known unit")
check(xcols[("x_comps", "div_yield")]["unit"] == "fraction", "comps dividend yield is declared a fraction (Yahoo percent / 100)")
check(xcols[("x_country_monitor", "cpi_yoy")]["unit"] == "fraction" and xcols[("x_country_monitor", "gdp_yoy")]["unit"] == "fraction",
      "country monitor CPI and GDP y/y are declared fractions")
check(xcols[("x_comps", "mcap_usd")]["unit"] == "USD bn" and xcols[("x_bond_lookup", "g_spread")]["unit"] == "bp", "hand-declared units")
check(xcols[("x_board", "chg")]["unit"] == "row", "board change: unit per row (bp on a yield row)")
stale_x = [f"{wid}.{f}" for wid, fs in C.X_UNITS.items() for f in fs if (wid, f) not in xcols]
check(not stale_x, "every X_UNITS entry names an /x/* number column: " + ", ".join(stale_x[:6]))
clash = [f"{wid}.{f}" for wid, fs in C.X_UNITS.items() for f, u in fs.items()
         if (wid, f) in xcols and C.format_unit(xcols[(wid, f)]) not in (None, u)]
check(not clash, "a declared unit never contradicts the column's own format: " + ", ".join(clash[:6]))
check(C.format_unit({"formatterFn": "normalizedPercent"}) == "fraction" and C.format_unit({"suffix": "%"}) == "percent"
      and C.format_unit({"suffix": " bp"}) == "bp" and C.format_unit({"suffix": "x"}) == "x" and C.format_unit({"suffix": "M"}) is None,
      "units from formats; '$M' is not a unit by itself (declared)")
check(C.x_unit_ok("USD bn") and C.x_unit_ok("EUR") and not C.x_unit_ok("usd") and not C.x_unit_ok("") and not C.x_unit_ok(None),
      "currency units: ISO code with an optional scale")
check(C.undeclared({"widgetId": "x_t", "data": {"table": {"columnsDefs": [{"field": "a", "cellDataType": "number"},
                                                                          {"field": "b", "cellDataType": "number", "unit": "bp"}]}}}) == ["a"],
      "an /x/* number column without a unit is reported")
check(C.inferred({"data": {"table": {"columnsDefs": [
    {"field": "a", "cellDataType": "number", "headerTooltip": "Change, in basis points."},
    {"field": "b", "cellDataType": "number", "headerTooltip": "Level (thousands)."},
    {"field": "c", "cellDataType": "number", "headerTooltip": "Rate.", "formatterFn": "percent"},
    {"field": "d", "cellDataType": "number", "headerTooltip": "Share, in percent.", "formatterFn": "percent"}]}}}) == ["a", "b"],
      "inferred(): tooltip-read formats and units, not source-declared ones")
INFERRED_MAX = 196          # raw backend columns whose unit is read from tooltip text (2026-11-24: v5 175 + v4 21): may shrink, never grow
if all(p.exists() for p in cat.values()):
    n_inf = sum(len(C.inferred(w)) for p in cat.values() for w in json.loads(p.read_text(encoding="utf-8")).values() if isinstance(w, dict))
    check(n_inf <= INFERRED_MAX, f"tooltip unit inference on backend cards is frozen: {n_inf} <= {INFERRED_MAX}")

done("test_columns")
