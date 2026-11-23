"""How data tables present their columns: readable headers, number formats by column meaning, curated default sets.

The dashboard build (scripts/build_dashboards.py) turns the column definitions of every placed widget into a small spec,
stored in local/dashboards.json under "columns" ({widgetId: spec}); static/table.js applies it and then settles the
value-dependent parts (one unit and one number of decimals per column, fractions shown as percent, clutter hidden on
tables without a preset). A spec only carries what differs from the backend's own definition:

    {"show": [fields, in order],           default visible columns (the rest stay one click away under "Columns")
     "h": {field: header},                 readable header, with the unit when the source states one
     "f": {field: {...}}}                  format overrides in the table's column-definition vocabulary
                                           (formatterFn, decimalPlaces, suffix, renderFn) plus "kind": id | level | change

Rules (one place, so every dashboard reads the same):
- ids, codes, years and CIKs never get thousands separators;
- fractions the source calls "normalized" (0.0253) are shown as percent (2.53%);
- green/red only on changes (returns, growth, surprises, deltas), never on levels such as yields or prices;
- units the source states in a description (thousands, millions of USD, percent) go into the header.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------- headers
_WORDS = {
    "pe": "P/E", "eps": "EPS", "ytd": "YTD", "mtd": "MTD", "qtd": "QTD", "wtd": "WTD", "usd": "USD", "eur": "EUR", "id": "ID", "cik": "CIK",
    "cusip": "CUSIP", "isin": "ISIN", "lei": "LEI", "figi": "FIGI", "sic": "SIC", "naics": "NAICS", "etf": "ETF", "nav": "NAV", "aum": "AUM",
    "ebitda": "EBITDA", "ebit": "EBIT", "roe": "ROE", "roa": "ROA", "roic": "ROIC", "gdp": "GDP", "cpi": "CPI", "pce": "PCE", "ppi": "PPI",
    "iv": "IV", "oi": "OI", "fx": "FX", "ttm": "TTM", "yoy": "y/y", "qoq": "q/q", "mom": "m/m", "url": "URL", "sec": "SEC", "otc": "OTC",
    "sma": "SMA", "rsi": "RSI", "peg": "PEG", "fcf": "FCF", "sga": "SG&A", "rd": "R&D", "ceo": "CEO", "cfo": "CFO", "ipo": "IPO", "mbs": "MBS",
    "cmbs": "CMBS", "tips": "TIPS", "frn": "FRN", "fima": "FIMA", "ois": "OIS", "sofr": "SOFR", "effr": "EFFR", "nci": "NCI", "pct": "%",
    "percent": "%", "num": "No.", "avg": "Avg", "chg": "Chg", "dte": "DTE", "atm": "ATM", "oas": "OAS", "mic": "MIC", "rssd": "RSSD",
    "fips": "FIPS", "bp": "bp", "bps": "bp", "ohlc": "OHLC", "vwap": "VWAP", "adr": "ADR", "uk": "UK", "us": "US", "eu": "EU", "uae": "UAE",
    "nms": "NMS", "aaa": "AAA", "aa": "AA", "bbb": "BBB", "bb": "BB", "ccc": "CCC", "pdf": "PDF", "html": "HTML", "fha": "FHA", "usda": "USDA", "uuid": "UUID", "to": "to", "of": "of", "and": "and", "per": "per", "vs": "vs", "otm": "OTM", "itm": "ITM", "lt": "LT", "st": "ST", "nsa": "NSA", "sa": "SA", "ci": "CI", "ma": "MA", "apr": "APR", "apy": "APY",
}
_TENOR = re.compile(r"^\d+[dwmyq]$", re.I)


def header(field: str, given: str | None = None) -> str:
    """'percent_change' -> 'Change %', 'ma_50d' -> '50-day MA', 'sveny10' -> '10Y zero-coupon', 'pe_ratio' -> 'P/E Ratio'."""
    f = str(field)
    m = re.fullmatch(r"sven(y|py|f)(\d\d)", f, re.I)
    if m:
        return f"{int(m.group(2))}Y " + {"y": "zero-coupon", "py": "par", "f": "inst. forward"}[m.group(1).lower()]
    m = re.fullmatch(r"(?:ma|sma)_?(\d+)d?", f, re.I)
    if m:
        return f"{m.group(1)}-day MA"
    m = re.fullmatch(r"tenor_(\d+[dwmy])", f, re.I)
    if m:
        return m.group(1).upper()
    m = re.fullmatch(r"(day|week|month|year)_(\d+)", f, re.I)
    if m:
        return m.group(2) + m.group(1)[0].upper()          # treasury curves: month_3 -> 3M, year_10 -> 10Y
    m = re.fullmatch(r"year_(\d+)_year_(\d+)", f, re.I)
    if m:
        return f"{m.group(1)}-{m.group(2)}Y"
    g = (given or "").strip()
    # written by hand at the source ("Mkt cap $bn", "1Y chg", "Coupons 3-6Y", "LTV <= 80 FICO < 680"): keep it (review H1:
    # "Coupons 3-6Y" was rebuilt from the field as "Coupons Gt 3 Years Lt 6 Years")
    if g and re.search(r"[()/%$&:.,<>=]|\d-\d|\b[a-z]{2,}\b", g):
        return g
    # maturity buckets (Fed primary dealer statistics): tips_gt_6_years_lt_11_years -> TIPS 6-11Y, coupons_lte_2_years -> Coupons <=2Y
    f = re.sub(r"_gt_(\d+)_years?_lt_(\d+)_years?", r"_\1-\2Y", f, flags=re.I)
    f = re.sub(r"_lt_(\d+)_years?$", r"_<\1Y", re.sub(r"_lte_(\d+)_years?$", r"_<=\1Y", re.sub(r"_gt_(\d+)_years?$", r"_>\1Y", f, flags=re.I), flags=re.I), flags=re.I)
    toks = re.split(r"_+", re.sub(r"(?<=[a-z])(?=[A-Z])", "_", f).strip("_"))
    out = []
    for t in toks:
        lo = t.lower()
        mm = re.fullmatch(r"(?:ma|sma)(\d+)d?", lo)
        if mm:
            out.append(f"{mm.group(1)}-day MA")
        elif lo in _WORDS:
            out.append(_WORDS[lo])
        elif _TENOR.match(lo):
            out.append(lo.upper())
        elif t.isupper() and len(t) > 1:
            out.append(t)
        else:
            out.append(t[:1].upper() + t[1:])
    if out and out[0] in ("to", "of", "and", "per", "vs"):
        out[0] = out[0].capitalize()
    s = " ".join(out)
    s = re.sub(r"^% (Change|Chg)\b", r"\1 %", s)                     # 'Percent Change' reads 'Change %'
    s = re.sub(r"\b(Change|Chg) % (\w+)", r"\1 % \2", s)
    s = re.sub(r"\bP/E Ratio\b", "P/E", s)
    return s


# ---------------------------------------------------------------- formats
ID_RE = re.compile(r"(^|_)(id|cik|cusip|isin|figi|lei|sic|naics|rssd|rssd_id|zip|fips|code|year|fiscal_year|fy|key|uuid|number|orderbook_id|crd)$", re.I)
# "one_year", "ten_year", "volatility_one_year", "change_prior_year" are horizons, not a calendar year: they keep their
# number format (review A2/A4: Finviz "One Year" printed 0.1003 beside "YTD 7.41%" because it was taken for an id)
NOT_ID_RE = re.compile(r"^(num|number)_|_(count|amount)$|number_of|"
                       r"(^|_)(one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|\d+|prior|previous|last|per)_years?$", re.I)
CHANGE_RE = re.compile(r"(^|_)(chg|change|changes|return|returns|growth|surprise|diff|delta|perf|performance|gain|revision|var|"
                       r"1d|1w|2w|1m|3m|6m|1y|3y|5y|10y|ytd|mtd|qtd|wtd|one_day|one_week|one_month|three_month|six_month|one_year)(_|$)|^growth_|_chg$|change_percent|percent_change", re.I)
LEVEL_HINT = re.compile(r"(^|_)(rate|yield|price|close|open|high|low|level|value|index|ratio|weight|margin|tenor|month|year|week|day)s?(_\d+)?$", re.I)


LEVEL_PREFIX = re.compile(r"^(tenor|month|year|week|day|sven|maturity|volatility)(_|\d)|volatility", re.I)


def is_change(field: str, tip: str = "") -> bool:
    f = str(field)
    if LEVEL_PREFIX.search(f):
        return False                                     # tenor_1y, month_3, volatility_1w: levels, not changes
    if CHANGE_RE.search(f):
        return True
    return bool(re.search(r"\bchange (in|from|over|since)\b|\bpercent change\b|\breturn over\b", tip or "", re.I)) and not LEVEL_HINT.search(f)


# column -> parameter its click sets (review D1/D2: TRACE CUSIPs, adviser CRDs and FFIEC RSSD ids were plain text)
CLICK_FIELDS = {"symbol": "symbol", "ticker": "symbol", "bill_id": "bill_id", "cusip": "cusip", "crd": "crd", "rssd_id": "rssd_id"}


def classify(col: dict) -> dict:
    """Format overrides for one backend column definition (empty dict: the definition is fine as it is)."""
    field = str(col.get("field") or "")
    tip = str(col.get("headerTooltip") or "").lower()
    fn = col.get("formatterFn")
    num = col.get("cellDataType") == "number"
    rfn = col.get("renderFn")
    green = rfn == "greenRed" or (isinstance(rfn, list) and "greenRed" in rfn)
    out: dict = {}
    if not num and field.lower() in CLICK_FIELDS and not rfn:
        # a ticker cell opens that ticker: the cards on the tab that take it, else Company / ETFs (review D1: clicking
        # AAPL in the screener or IAM in Movers did nothing); a bill id drives Bill Info and the Bill Viewer (D2)
        out.update(renderFn="cellOnClick", renderFnParams={"groupByParamName": CLICK_FIELDS[field.lower()]})
        return out
    if ID_RE.search(field) and not NOT_ID_RE.search(field):
        if num or fn not in ("none",):
            out["kind"] = "id"
            if num and fn != "none":
                out["formatterFn"] = "none"
        if green:
            out["renderFn"] = None
        return out
    if not num:
        return out
    if not fn:
        if re.search(r"normali[sz]ed (percent|percentage|decimal)|as a (normali[sz]ed )?decimal|percent ?/ ?100|\(percent ?/ ?100\)", tip):
            out["formatterFn"] = "normalizedPercent"
        elif re.search(r"percentage points|\bin pp\b", tip):
            out.update(suffix=" pp", decimalPlaces=2)
        elif re.search(r"\b(in|as a|as) percent(age)?\b|\bpercent of\b|, in percent", tip) and "per cent" not in tip:
            out["formatterFn"] = "percent"
        elif re.search(r"\bin basis points\b", tip):
            out.update(suffix=" bp", decimalPlaces=0)
    if out.get("formatterFn") in ("percent", "normalizedPercent") and str(col.get("suffix") or "").strip() == "%":
        out["suffix"] = ""                           # the formatter writes the %: USAspending share read "70.47%%" (review A6)
    change = is_change(field, tip)
    if green and not change:
        out["renderFn"] = None                       # levels (yields, prices, ratios) are never good or bad by colour
        out["kind"] = "level"
    elif change:
        out["kind"] = "change"
        if not green and (fn in ("percent", "normalizedPercent") or out.get("formatterFn") in ("percent", "normalizedPercent") or field.startswith("growth_")):
            out["renderFn"] = "greenRed"
    if out.get("kind") in ("change", "level"):
        del out["kind"]                              # informational only; the table needs "id" (no thousands separators)
    return out


def unit_from_tip(tip: str) -> str | None:
    """'(thousands)', 'in millions of USD' -> a header unit."""
    t = (tip or "").lower()
    if re.search(r"\bin thousands\b|\(thousands\)|thousands of (persons|people|jobs|units)", t):
        return "k"
    if re.search(r"\bin (millions|mn) of (usd|u\.s\. dollars|dollars)\b|\$ ?millions|millions of usd", t):
        return "$mn"
    if re.search(r"\bin billions of (usd|u\.s\. dollars|dollars)\b|\$ ?billions", t):
        return "$bn"
    if re.search(r"\bin millions\b", t):
        return "mn"
    if re.search(r"\(megawatt ?hours\)|\bin megawatt ?hours\b", t):          # EIA grid demand / generation (review A6)
        return "MWh"
    if re.search(r"\(megawatts\)|\bin megawatts\b", t):                       # EIA nuclear capacity and outages
        return "MW"
    return None


# ---------------------------------------------------------------- declared units (never inferred from the values)
# A number column named like a percent or a yield whose source declares no format gets its unit here, per widget.
# The table used to decide from the day's values (|p90| <= 1.5 -> x100 and "%"), so one column changed unit with the
# data and dollar amounts or PEG ratios named "surprise"/"growth" became percents. Each entry was decided on
# 2026-11-17 by a live request compared with a known value, noted beside it. "raw": the unit could not be confirmed
# (no key, provider down, or a parameter changes it): the number is shown as delivered, no % sign, and the tip says so.
PCT_NAME = re.compile(r"(^|_)(pct|percent|percentage|growth|return|returns|margin|weight|surprise|ytd)(_|$)|^growth_|_growth$|"
                      r"change_percent|percent_change|dividend_yield|payout_ratio|return_on_", re.I)
YIELD_NAME = re.compile(r"(^|_)(yield|ytm|ytw|coupon_rate|current_yield)$", re.I)
UNIT_FORMATS = {
    "fraction": {"formatterFn": "normalizedPercent", "decimalPlaces": 2},    # 0.2945 -> 29.45%
    "percent": {"formatterFn": "percent", "decimalPlaces": 2},               # 16.49 -> 16.49%
    "percent4": {"formatterFn": "percent", "decimalPlaces": 4},              # funding per 8 hours: 0.000198 -> 0.0002%
    "number": {"decimalPlaces": 2},                                          # dollars, ratios
    "integer": {"decimalPlaces": 0},
}
RAW_NOTE = "Unit not confirmed by the source: shown as delivered."
# Fields a runtime patch (scripts/run_backend.py) adds to a provider's rows; the widget catalog, built from OpenBB's
# static reference, does not list them, but the rows carry them.
PATCH_FIELDS = {"cftc_cds_index_trades_cftc_obb": {"spread_bp"}, "uscongress_search_congress_gov_obb": {"bill_id"},
                "deribit_market_ticker_deribit_obb": {"funding_apr"}, "derivatives_futures_curve_yfinance_obb": {"implied_rate"}}
_TMX_SCREENER_PCT = ("gross_margin", "ebitda_margin", "ebit_margin", "return_on_assets", "return_on_equity", "return_on_capital",
                     "dividend_growth_3y", "dividend_growth_5y", "revenue_growth_3y", "revenue_growth_5y", "income_growth_3y", "income_growth_5y")
UNITS = {
    "equity_calendar_earnings_tmx_obb": {"eps_surprise": "number"},                     # dollars: actual 0.52 - consensus 0.27 = 0.25
    "equity_fundamental_historical_eps_alpha_vantage_obb": {"surprise": "number"},      # "Actual - Estimated" at the source (needs a key: not probed)
    "equity_fundamental_metrics_yfinance_obb": {"payout_ratio": "fraction"},            # AAPL 0.118, SAP.DE 0.362
    "equity_fundamental_ratios_fmp_obb": {"price_to_earnings_growth_ratio_ttm": "number", "forward_price_to_earnings_growth_ratio_ttm": "number",
                                          "dividend_payout_ratio_ttm": "fraction"},     # PEG is a ratio; FMP payout ratio AAPL 0.119
    "equity_fundamental_trailing_dividend_yield_tiingo_obb": {"trailing_dividend_yield": "raw"},   # needs a Tiingo key: not probed
    "equity_shorts_short_volume_stockgrid_obb": {"short_volume_percent": "raw"},        # provider failing upstream: not probed
    "etf_discovery_active_wsj_obb": {"percent_change": "percent"},                      # SOXL 0.64, RWM 1.5
    "etf_discovery_gainers_wsj_obb": {"percent_change": "percent"},                     # top gainer 15.21
    "etf_discovery_losers_wsj_obb": {"percent_change": "percent"},
    # Yahoo ytdReturn is in percent although the widget multiplies it by 100 (SPY showed 1,168.68%), and it is a total
    # return to the last month-end: SPY 11.69 = close 2025-12-31 681.92 -> 2026-10-30 755.72 (+10.82%) plus dividends;
    # XIU.TO 16.32 vs +14.45% price to 2026-10-30. The 3y/5y averages are fractions (SPY 0.194) and keep the source format.
    "etf_info_yfinance_obb": {"return_ytd": "percent"},
    "tmx_markets_movers_tmx_obb": {"change_percent": "fraction"},
    # Cleveland Fed model: 1y expected inflation 2026-11 0.0304 = 3.04% (range -0.48%..6.43% since 1982), columns "N year ..."
    "cleveland_inflation_expectations_federal_reserve_obb": {f"{n} year Expected Inflation": "fraction" for n in range(1, 31)},
    "economy_interest_rates_oecd_obb": {"value": "fraction"},       # France 2026-10 0.0263 = 2.63% (Country Monitor); US long 0.0513   # CNQ -0.67 on 68.96 = -0.00972 (the fetcher divides TMX's percent by 100)
    "fixedincome_government_treasury_auctions_government_us_obb": {"allocation_percentage_decimals": "integer"},
    "ustreasury_auction_results_us_treasury_obb": {"allocation_percentage_decimals": "integer"},   # a count of decimals: 2
    "index_sectors_tmx_obb": {"weight": "fraction"},                                    # ^TSX sector weights sum to 0.988
    "bls_employment_situation_long_term_unemployed_share_bls_obb": {"percent": "percent"},   # 2006-10: 15.9
    "cboe_options_trade_optimizer_custom_obb": {"regt_margin": "number",                # dollars (SPY median 1,044)
                                                "annualized_return": "fraction"},       # upstream says percent; 5.4919 = 549% (0.4815 in 33 days)
    "eia_nuclear_outages_facility_level_nuclear_outages_eia_obb": {"percent_outage": "percent"},
    "eia_nuclear_outages_generator_level_nuclear_outages_eia_obb": {"percent_outage": "percent"},
    "eia_nuclear_outages_us_nuclear_outages_eia_obb": {"percent_outage": "percent"},    # outage 13,956 / capacity 99,723 = 13.99
    "equity_screener_tmx_obb": {f: "percent" for f in _TMX_SCREENER_PCT},               # RY ROE 16.49, ROA 0.9
    "yfinance_estimates_earnings_yfinance_obb": {"growth": "fraction"},                 # AAPL FY EPS 8.98 x 1.05 = next year's base 9.43
    "yfinance_estimates_revenue_yfinance_obb": {"growth": "fraction"},                  # SAP 36.8bn x 1.0992 = 40.45bn
    "yfinance_funds_info_yfinance_obb": {"three_year_earnings_growth": "percent"},      # VFIAX 18.51, AGTHX 25.12
    "yfinance_funds_risk_yfinance_obb": {"annualized_return": "fraction", "volatility": "fraction"},   # SPY 0.177, vol 0.155 (Sharpe 1.14 = 0.177/0.155)
    "yfinance_industry_overview_yfinance_obb": {"market_weight": "fraction"},           # semiconductors 0.335 of technology
    "yfinance_industry_top_growth_yfinance_obb": {"ytd_return": "fraction", "growth_estimate": "fraction"},   # AAPL 0.2945, 0.0948
    "yfinance_industry_top_performing_yfinance_obb": {"ytd_return": "fraction"},        # AAPL 0.2945 (top names above 1.5 = +150%)
    "yfinance_sectors_industries_yfinance_obb": {"market_weight": "fraction"},
    "yfinance_sectors_overview_yfinance_obb": {"market_weight": "fraction"},            # the 11 sectors sum to 1.0000
    "yfinance_sectors_top_companies_yfinance_obb": {"market_weight": "fraction"},       # LIN 0.155 of basic materials
    "ecb_eligible_assets_ecb_obb": {"coupon_rate": "percent"},                          # "Coupon rate, percent." at the source
    # FMP (Damodaran) equity risk premia in percent (review A6, live 2026-11-18): Zimbabwe total 15.89 = mature-market
    # premium 4.23 + country premium 11.66
    "economy_risk_premium_fmp_obb": {"total_equity_risk_premium": "percent", "country_risk_premium": "percent"},
    "cftc_cds_index_trades_cftc_obb": {"spread_bp": "number"},
    "derivatives_futures_curve_yfinance_obb": {"implied_rate": "percent"},              # Fed funds (ZQ) 100 - 95.865 = 4.135 (run_backend.futures_curve_points)                         # run_backend.cds_spread_bp: 0.006112 (notation 3) = 61.12
    # yield 8.31 / OAS 3.43 (percent) or total-return index 1,868.8, by index_type: shown as delivered; the rows carry a
    # `unit` column from index_type (run_backend.install_fred_bond_index_unit)
    "fixedincome_bond_indices_fred_obb": {"high_yield": "raw"},
    # options chains: the backend sends no column list, so these reach the fields found in the rows (spec())
    "derivatives_options_chains_yfinance_obb": {"implied_volatility": "fraction"},      # AAPL median 0.4304 (placed: Options and Chain tabs)
    "derivatives_options_chains_cboe_obb": {"implied_volatility": "fraction"},          # AAPL median 0.5289 (0.20 - 4.05)
    "derivatives_options_chains_tmx_obb": {"implied_volatility": "fraction"},           # RY median 0.271 (0.12 - 1.83)
    "derivatives_options_chains_deribit_obb": {"implied_volatility": "percent", "bid_iv": "percent", "ask_iv": "percent"},   # BTC median 57.46
    # openbb_deribit sends funding in percent (exchange fraction x 100): BTC-PERPETUAL 0.000198 per 8 h on 2026-11-18,
    # "0.00%" at two decimals (review H4); the APR is a runtime-patch field (8-hour rate x 3 x 365)
    "deribit_market_ticker_deribit_obb": {"funding_8h": "percent4", "current_funding": "percent4", "funding_apr": "percent"},
}
# Headers that depend on the source of one widget (UNIT_HEADERS is per route, shared by every provider of it)
WIDGET_HEADERS = {
    "etf_info_yfinance_obb": {"return_ytd": "YTD total return (to last month-end)"},
    "derivatives_futures_curve_yfinance_obb": {"implied_rate": "Implied rate (100 - price)"},
    # Deribit quotes BTC/ETH options in the coin; openbb_deribit carries prices to USD at the index level of the quote
    "derivatives_options_chains_deribit_obb": {"bid": "Bid (USD)", "ask": "Ask (USD)", "mark": "Mark (USD)", "last_trade_price": "Last (USD)",
                                               "underlying_price": "Underlying (USD)", "dte": "DTE (calendar)"},
    # DTE conventions differ (2026-11-17 22:00 ET, expiry 11-18): Cboe floors the time left to the 4 pm ET expiry (0);
    # TMX and Deribit count calendar days from the backend's date (1). The expiry date is on every row.
    "derivatives_options_chains_cboe_obb": {"dte": "DTE (full days)"},
    "derivatives_options_chains_tmx_obb": {"dte": "DTE (calendar)"},
    "derivatives_options_chains_yfinance_obb": {"dte": "DTE (calendar)"},
    # review H4: openbb_deribit declares these volumes in "the currency the volume was traded in" (the Currency column)
    "deribit_market_trade_volumes_deribit_obb": {f"{k}_volume{s}": f"{k.capitalize()} {p} (in Currency)" for k in ("futures", "calls", "puts", "spot")
                                                 for s, p in (("", "24h"), ("_7d", "7d"), ("_30d", "30d"))},
}


def unit_format(widget_id: str | None, col: dict) -> dict:
    """The declared format of one column of one widget (empty: nothing declared here)."""
    u = UNITS.get(widget_id or "", {}).get(str(col.get("field") or ""))
    if not u:
        return {}
    if u == "raw":
        return {"headerTooltip": f"{(col.get('headerTooltip') or '').strip()} {RAW_NOTE}".strip()}
    return dict(UNIT_FORMATS[u])


def undeclared(widget: dict) -> list[str]:
    """Number columns named like a percent or a yield whose unit nobody declares (source format, classify(), UNITS)."""
    wid = (widget or {}).get("widgetId") or ""
    out = []
    for c in (((widget or {}).get("data") or {}).get("table") or {}).get("columnsDefs") or []:
        f = str(c.get("field") or "")
        if c.get("cellDataType") != "number" or not (PCT_NAME.search(f) or YIELD_NAME.search(f)):
            continue
        if c.get("formatterFn") or c.get("decimalPlaces") is not None or f in UNITS.get(wid, {}):
            continue
        o = classify(c)
        if o.get("formatterFn") or o.get("decimalPlaces") is not None or o.get("kind") == "id":
            continue
        out.append(f)
    return out


# ---------------------------------------------------------------- curated default columns, by endpoint
# The columns a professional reads first; everything else is one click away under "Columns". Fields missing from an
# answer are skipped; with fewer than two present the table falls back to the generic rule (static/table.js).
# review F1: the move first, the long company name after the numbers (it pushed Change % off narrow cards)
_MOVERS = ["symbol", "percent_change", "change_percent", "price", "change", "volume", "market_cap", "name", "pe_forward"]
_COT = ["date", "market_and_exchange_names", "open_interest_all", "change_in_open_interest_all", "non_commercial_positions_long_all",
        "non_commercial_positions_short_all", "open_interest_pct_non_commercial_long_all", "open_interest_pct_non_commercial_short_all",
        "commercial_positions_long_all", "commercial_positions_short_all", "managed_money_positions_long_all", "managed_money_positions_short_all"]
_JODI = ["date", "united_states", "saudi_arabia", "russia", "canada", "china", "iraq", "iran", "united_arab_emirates", "brazil", "kuwait", "norway",
         "kazakhstan", "mexico", "united_kingdom"]
_BONDS = ["issuer_name", "cusip", "coupon_rate", "maturity_date", "price", "ytm", "current_yield", "sp_rating", "moodys_rating", "last_trade_date"]
_FILINGS = ["filing_date", "report_type", "report_url", "title", "description", "period_ending", "report_date",
            "primary_doc_description", "items", "reporting_owner", "pdf_url", "primary_doc", "accession_number",
            "size", "filing_detail_url"]
_LATEST_REPORTS = ["filing_date", "period_ending", "symbol", "name", "report_type", "url", "description", "items", "sic"]
_FILING_HEADERS = ["sequence", "type", "filename", "url", "description", "content_description"]
_AUCTIONS = ["auction_date", "security_term", "security_type", "cusip", "high_yield", "high_investment_rate", "high_discount_rate",
             "bid_to_cover_ratio", "offering_amount", "total_accepted", "indirect_bidder_accepted", "direct_bidder_accepted",
             "primary_dealer_accepted", "issue_date", "maturity_date"]

PRESETS: dict[str, list[str]] = {
    "equity/discovery/*": _MOVERS, "nasdaq/markets/movers": _MOVERS, "etf/discovery/*": _MOVERS, "tmx/markets/movers": _MOVERS,
    "cftc/cot": _COT,
    "jodi/oil/production": _JODI, "jodi/oil/exports": _JODI, "jodi/oil/*": _JODI, "jodi/gas/*": _JODI,
    "ustreasury/auction_results": _AUCTIONS,
    "fixedincome/government/treasury_auctions": _AUCTIONS,
    "finra/fixedincome/bond_historical": ["date", "price", "last_sale_yield", "yield_direction", "cusip", "symbol"],
    "ustreasury/upcoming_auctions": ["auction_date", "security_term", "security_type", "offering_amount", "reopening", "issue_date", "announcement_date", "cusip"],
    "ustreasury/weekly_bill_offerings": ["issue_date", "maturity_date", "days_to_maturity", "high_discount_rate", "high_investment_rate", "high_price_per_hundred",
                                         "bids_tendered", "bids_accepted_total"],
    "fixedincome/government/svensson_yield_curve": ["date", "sveny01", "sveny02", "sveny03", "sveny05", "sveny07", "sveny10", "sveny20", "sveny30"],
    "cftc/swap_trades": ["execution_timestamp", "product_name", "action_type", "notional_amount_leg_1", "notional_currency_leg_1", "fixed_rate_leg_1",
                         "fixed_rate_leg_2", "spread_leg_1", "effective_date", "expiration_date", "cleared"],
    "economy/balance_of_payments": ["period", "current_account_balance", "goods_balance", "services_balance", "primary_income_balance",
                                    "secondary_income_balance", "capital_account_balance", "financial_account_balance", "net_errors_and_omissions"],
    "equity/fundamental/income_growth": ["period_ending", "fiscal_period", "fiscal_year", "growth_total_revenue", "growth_total_gross_profit",
                                         "growth_total_operating_income", "growth_net_income", "growth_diluted_eps", "growth_ebitda", "growth_revenue",
                                         "growth_gross_profit", "growth_operating_income", "growth_eps_diluted"],
    "equity/fundamental/balance_growth": ["period_ending", "fiscal_period", "fiscal_year", "growth_cash_and_equivalents", "growth_total_current_assets",
                                          "growth_total_assets", "growth_long_term_debt", "growth_total_liabilities", "growth_total_common_equity",
                                          "growth_total_equity", "growth_total_debt", "growth_net_debt"],
    "equity/fundamental/cash_growth": ["period_ending", "fiscal_period", "fiscal_year", "growth_net_cash_from_operating_activities", "growth_capital_expenditure",
                                       "growth_free_cash_flow", "growth_net_cash_from_investing_activities", "growth_repurchase_of_common_equity",
                                       "growth_payment_of_dividends", "growth_net_cash_from_financing_activities", "growth_operating_cash_flow"],
    "equity/screener": ["symbol", "name", "sector", "industry", "market_cap", "price", "change_percent", "volume", "price_to_earnings", "forward_pe",
                        "dividend_yield", "price_change_ytd", "price_change_1y"],
    "fixedincome/corporate/bond_prices": _BONDS, "finra/fixedincome/bonds": _BONDS,
    "cftc/fx_option_trades": ["execution_timestamp", "pair", "option_type", "strike", "expiration_date", "days_to_expiry", "notional_1", "notional_currency_1",
                              "premium_amount", "premium_currency", "venue"],
    "cftc/fx_forward_trades": ["execution_timestamp", "pair", "product_type", "exchange_rate", "notional_1", "notional_currency_1", "expiration_date",
                               "days_to_expiry", "venue"],
    "nasdaq/nordic/screener": ["symbol", "name", "sector", "currency", "last_price", "change", "change_percent", "volume", "turnover"],
    "deribit/market/ticker": ["symbol", "last_price", "mark_price", "index_price", "change_percent", "volume_usd", "open_interest", "funding_8h",
                              "funding_apr", "current_funding", "implied_volatility"],
    # review H4: the identifiers people quote first (ID columns are otherwise hidden by default)
    "deribit/reference/combos": ["combo_id", "state", "leg", "amount", "creation_timestamp", "state_timestamp", "instrument_id"],
    "deribit/market/block_rfq_trades": ["timestamp", "rfq_id", "combo_id", "direction", "amount", "mark_price", "leg", "leg_direction", "leg_price",
                                        "leg_ratio"],
    "fixedincome/corporate/commercial_paper": ["date", "nonfinancial_overnight", "nonfinancial_day_30", "nonfinancial_day_90", "financial_overnight",
                                               "financial_day_30", "financial_day_90", "asset_backed_day_30", "a_2p_2_day_30"],
    "economy/central_bank_holdings": ["date", "bills", "notes_and_bonds", "tips", "frn", "mbs", "agencies", "total", "change_prior_week", "change_prior_year"],
    # review F1: the values first (rows carry 19+ columns; Bid/Ask/IV were off screen), Greeks where the source has them
    "derivatives/options/chains": ["expiration", "dte", "strike", "option_type", "bid", "ask", "last_trade_price", "volume", "open_interest",
                                   "implied_volatility", "delta", "gamma", "theta", "vega"],
    "economy/fred_search": ["series_id", "title", "frequency_short", "units_short", "seasonal_adjustment_short", "observation_end", "popularity", "release_id"],
    "uscongress/search": ["date", "bill_id", "title", "citation", "collection", "package_id"],
    "cftc/fx_forward_curve": ["index", "rate", "spot", "as_of_date", "staleness_days", "num_trades", "total_notional"],
    "cftc/cds_index_trades": ["execution_timestamp", "index", "tenor", "maturity_date", "spread_bp", "coupon", "upfront_amount", "notional_amount",
                              "notional_currency", "venue"],
    "cftc/ois_curve_history": ["date", "tenor_1m", "tenor_3m", "tenor_6m", "tenor_1y", "tenor_2y", "tenor_3y", "tenor_5y", "tenor_10y", "tenor_30y"],
    "tmx/equity/ownership/insider_transactions": ["transaction_date", "owner_name", "relationship", "transaction_type", "securities_transacted", "price",
                                                  "market_value", "securities_owned", "currency"],
    "equity/estimates/price_target": ["published_date", "analyst_firm", "analyst_name", "rating_current", "rating_previous", "action", "price_target",
                                      "price_target_previous", "price_when_posted"],
    "equity/estimates/consensus": ["symbol", "name", "current_price", "target_consensus", "target_median", "target_low", "target_high", "recommendation",
                                   "recommendation_mean", "number_of_analysts", "currency"],
    "equity/fundamental/metrics": ["symbol", "market_cap", "pe_ratio", "forward_pe", "peg_ratio", "enterprise_to_ebitda", "price_to_book", "gross_margin",
                                   "operating_margin", "profit_margin", "revenue_growth", "earnings_growth", "return_on_equity", "dividend_yield", "beta"],
    "equity/profile": ["name", "stock_exchange", "sector", "industry_category", "market_cap", "employees", "ceo", "hq_country", "hq_address_city",
                       "company_url", "dividend_yield", "beta", "shares_outstanding", "short_description"],
    "etf/info": ["name", "issuer", "fund_family", "category", "inception_date", "nav_price", "total_assets", "dividend_yield", "return_ytd", "return_3y_avg",
                 "return_5y_avg", "beta_3y_avg", "year_high", "year_low", "volume_avg", "description"],
    # the currency sits beside the market value it qualifies (TMX values are in the fund's currency: run_backend)
    "etf/holdings": ["symbol", "name", "weight", "shares", "market_value", "currency", "country", "sector", "exchange"],
    "equity/price/quote": ["symbol", "name", "exchange", "last_price", "change", "change_percent", "open", "high", "low", "prev_close", "volume",
                           "year_high", "year_low", "ma_50d", "ma_200d", "currency"],
    "imf/portwatch/country_activity": ["date", "country", "portcalls", "portcalls_tanker", "portcalls_container", "imports", "exports", "shipment_30ma",
                                       "shipment_30ma_yoy_doy"],
    "economy/shipping/port_volume": ["date", "port_name", "country", "portcalls", "portcalls_tanker", "portcalls_container", "portcalls_dry_bulk",
                                     "imports", "exports"],
    "economy/shipping/port_info": ["port_name", "country", "continent", "vessel_count_total", "vessel_count_container", "vessel_count_tanker",
                                   "vessel_count_dry_bulk", "port_code"],
    "economy/export_destinations": ["destination_country", "value", "units", "origin_country", "title"],
    "nasdaq/nordic/fundamentals": ["section", "label", "period", "value"],
    "ustreasury/award_search": ["recipient_name", "award_amount", "total_outlays", "awarding_agency", "start_date", "end_date", "award_type",
                                "description", "award_id"],
    "ustreasury/recipient_search": ["name", "amount", "recipient_level", "uei", "recipient_id"],
    "economy/calendar": ["date", "country", "event", "actual", "consensus", "previous", "importance", "unit"],
    "equity/calendar/earnings": ["report_date", "reporting_time", "symbol", "name", "eps_consensus", "eps_previous", "eps_actual", "surprise_percent",
                                 "num_estimates", "market_cap", "period_ending"],
    "equity/ownership/insider_trading": ["transaction_date", "owner_name", "owner_title", "transaction_type", "acquisition_or_disposition",
                                         "securities_transacted", "transaction_price", "securities_owned", "filing_date"],
    "equity/ownership/institutional": ["owner_name", "date", "shares_held", "shares_change", "shares_change_percent", "market_value"],
    "fixedincome/bond_indices": ["date", "us", "europe", "emerging", "corporate", "high_yield", "aaa", "bbb", "ccc"],
    "equity/price/performance": ["symbol", "one_day", "one_week", "one_month", "three_month", "six_month", "ytd", "one_year", "three_year", "five_year",
                                 "volatility_month", "price"],
    "imf/portwatch/disruption_events": ["fromdate", "eventname", "eventtype", "alertlevel", "country", "severitytext", "todate"],
    "cftc/ois_policy_path": ["start_date", "end_date", "rate", "min_rate", "max_rate", "num_trades"],
    "sec/company_filings": _FILINGS,
    "equity/fundamental/filings": _FILINGS,
    "sec/full_text_search": ["filing_date", "form", "symbol", "name", "url", "description", "cik"],
    "sec/latest_financial_reports": _LATEST_REPORTS,
    "equity/discovery/latest_financial_reports": _LATEST_REPORTS,
    "equity/discovery/filings": ["filing_date", "form_type", "symbol", "accepted_date", "final_link", "link", "cik"],
    "sec/filing_headers": _FILING_HEADERS,
    "regulators/sec/filing_headers": _FILING_HEADERS,
    "equity/compare/company_facts": ["period_ending", "fiscal_year", "fiscal_period", "value", "unit", "form", "reported_date",
                                     "fact", "symbol", "period_beginning", "frame"],
    # the closing balance once (the source repeats it under Month and Fiscal Year)
    "ustreasury/daily_statement": ["date", "Treasury General Account (TGA) Closing Balance (Open Today)", "Total TGA Deposits (Table II) (Open Today)",
                                   "Total TGA Withdrawals (Table II) (-) (Open Today)", "Treasury General Account (TGA) Opening Balance (Open Today)",
                                   "Total TGA Deposits (Table II) (Open Month)", "Total TGA Withdrawals (Table II) (-) (Open Month)",
                                   "Treasury General Account (TGA) Opening Balance (Open Month)", "Total TGA Deposits (Table II) (Open Fiscal Year)",
                                   "Total TGA Withdrawals (Table II) (-) (Open Fiscal Year)", "Treasury General Account (TGA) Opening Balance (Open Fiscal Year)"],
}
_FILINGS_HEADERS = {"report_type": "Form", "report_url": "Document", "accession_number": "Accession", "filing_detail_url": "Index Page"}
_LATEST_REPORT_HEADERS = {"period_ending": "Period", "name": "Company", "report_type": "Form", "url": "Document"}
_FILING_HEADER_COLS = {"sequence": "Seq", "type": "Document Type", "url": "Document", "content_description": "Description"}
# Daily Treasury Statement, Table I/II: the source repeats "Open Today / Month / Fiscal Year" on every column (review H1).
# For balances "Open Month" is the balance at the start of the month; for flows it is month to date. The closing balance
# is the same number under all three (2026-11-17: 921.222B = 895.774B + 332.600B - 307.152B).
_DTS = "Treasury General Account (TGA) "
_DTS_HEADERS = {
    _DTS + "Closing Balance (Open Today)": "TGA closing balance ($)", _DTS + "Closing Balance (Open Month)": "TGA closing ($, repeated)",
    _DTS + "Closing Balance (Open Fiscal Year)": "TGA closing ($, repeated FY)",
    _DTS + "Opening Balance (Open Today)": "TGA opening, today ($)", _DTS + "Opening Balance (Open Month)": "TGA at start of month ($)",
    _DTS + "Opening Balance (Open Fiscal Year)": "TGA at start of fiscal year ($)",
    "Total TGA Deposits (Table II) (Open Today)": "Deposits, today ($)", "Total TGA Deposits (Table II) (Open Month)": "Deposits, month to date ($)",
    "Total TGA Deposits (Table II) (Open Fiscal Year)": "Deposits, fiscal year to date ($)",
    "Total TGA Withdrawals (Table II) (-) (Open Today)": "Withdrawals, today ($)", "Total TGA Withdrawals (Table II) (-) (Open Month)": "Withdrawals, month to date ($)",
    "Total TGA Withdrawals (Table II) (-) (Open Fiscal Year)": "Withdrawals, fiscal year to date ($)",
}
# endpoints whose headers need the unit the source documents only in prose (or clear label preserved over field name)
UNIT_HEADERS = {
    "equity/ownership/institutional": {"market_value": "Market value ($ thous.)"},
    "cftc/cds_index_trades": {"spread_bp": "Spread (bp)", "spread": "Spread (as reported)"},
    # EconDB (UN Comtrade) sends "Value million USD"; its fetcher puts "Millions of USD" in a units column (review A6;
    # live 2026-11-18: United States -> Mexico 33,656)
    "economy/export_destinations": {"value": "Exports (USD millions)"},
    # openbb_cftc builds the outright curve in the currency per USD whatever the pair is called (EURUSD 0.9114 = 1 / 1.0972;
    # the Forward Points card quotes the same pair 1.0972; review B4, live 2026-11-18)
    "cftc/fx_forward_curve": {"index": "Tenor", "rate": "Forward (currency per USD)", "spot": "Spot (currency per USD)", "min_rate": "Low (currency per USD)",
                              "max_rate": "High (currency per USD)", "as_of_date": "Priced on", "staleness_days": "Days before curve date"},
    "imf/portwatch/disruption_events": {"fromdate": "From", "todate": "To", "eventname": "Event", "eventtype": "Type", "alertlevel": "Alert", "severitytext": "Severity",
                                        "eventid": "Event ID", "country": "Country"},
    "cftc/cot": {"market_and_exchange_names": "Market", "open_interest_all": "Open interest", "change_in_open_interest_all": "OI chg (1w)",
                 "non_commercial_positions_long_all": "Non-comm. long", "non_commercial_positions_short_all": "Non-comm. short",
                 "open_interest_pct_non_commercial_long_all": "Non-comm. long (% OI)",
                 "open_interest_pct_non_commercial_short_all": "Non-comm. short (% OI)",
                 "change_in_non_commercial_long_all": "Non-comm. long chg", "change_in_non_commercial_short_all": "Non-comm. short chg",
                 "commercial_positions_long_all": "Comm. long", "commercial_positions_short_all": "Comm. short",
                 "managed_money_positions_long_all": "Managed money long", "managed_money_positions_short_all": "Managed money short"},
    "ustreasury/daily_statement": _DTS_HEADERS,
    "sec/company_filings": _FILINGS_HEADERS,
    "equity/fundamental/filings": {**_FILINGS_HEADERS, "period_ending": "Period"},
    "sec/full_text_search": {"name": "Company", "url": "Document"},
    "sec/latest_financial_reports": _LATEST_REPORT_HEADERS,
    "equity/discovery/latest_financial_reports": _LATEST_REPORT_HEADERS,
    "equity/discovery/filings": {"form_type": "Form", "final_link": "Document", "link": "Index Page"},
    "sec/filing_headers": _FILING_HEADER_COLS,
    "regulators/sec/filing_headers": _FILING_HEADER_COLS,
    "equity/estimates/price_target": {"published_date": "Date", "analyst_firm": "Firm", "rating_current": "Rating", "rating_previous": "Previous rating",
                                      "price_target": "Target", "price_target_previous": "Previous target", "price_target_action": "Target change"},
}


def preset_for(endpoint: str) -> list[str] | None:
    if endpoint in PRESETS:
        return PRESETS[endpoint]
    for k, v in PRESETS.items():
        if k.endswith("*") and endpoint.startswith(k[:-1]):
            return v
    return None


def spec(widget: dict, endpoint: str | None = None) -> dict | None:
    """The compact column spec for one widget definition (None when nothing needs saying)."""
    cols = (((widget or {}).get("data") or {}).get("table") or {}).get("columnsDefs") or []
    wid = (widget or {}).get("widgetId")
    h, f = {}, {}
    for c in cols:
        field = c.get("field")
        if not field:
            continue
        name = header(field, c.get("headerName"))
        unit = unit_from_tip(c.get("headerTooltip") or "")
        if unit and unit not in name:
            name = f"{name} ({unit})"
        if name != (c.get("headerName") or ""):
            h[field] = name
        o = classify(c)
        o.update(unit_format(wid, c))               # declared unit (percent / fraction / number), never guessed from values
        if o:
            f[field] = o
    listed = {c.get("field") for c in cols}
    for field in PATCH_FIELDS.get(wid or "", ()):   # fields a runtime patch adds: the same rules as listed ones
        if field not in listed and (o := classify({"field": field})):
            f[field] = o
    for field in UNITS.get(wid or "", {}):          # a declared field the widget does not list (options chains list none)
        if field not in listed and (o := unit_format(wid, {"field": field})):
            f[field] = o
    out: dict = {}
    show = preset_for(endpoint) if endpoint else None
    if show:
        out["show"] = list(show)
    h.update(UNIT_HEADERS.get(endpoint or "", {}))
    h.update(WIDGET_HEADERS.get(wid or "", {}))
    if h:
        out["h"] = h
    if f:
        out["f"] = f
    return out or None
