"""Provider preference, parameter/title overrides, concept families, credentials, and fallback rules."""

from __future__ import annotations

# Provider preference: sources that work without a key first, dead scrapers last.
PREF = ["yfinance", "sec", "federal_reserve", "ecb", "oecd", "imf", "cboe", "nasdaq", "us_treasury", "government_us", "cftc", "finra", "deribit", "tmx",
        "usda", "jodi", "famafrench", "congress_gov", "econdb", "finviz", "multpl", "wsj",
        "fred", "bls", "eia", "fmp", "polygon", "tiingo", "intrinio", "alpha_vantage", "tradier", "benzinga", "biztoc", "tradingeconomics", "stockgrid", "seeking_alpha"]

# Widget definitions whose built-in defaults the backend rejects, return nothing, or show something irrelevant first;
# working, meaningful values are pinned on the item (and on library choices). Values a card's dashboard group sets
# (ticker, country) still win where the card follows that group.
PARAM_FIXES = {
    "commodity/weather_bulletins_download": {"provider": "usda"},
    # required by the backend although the widget marks them optional
    "eia/data_browser": {"route": "petroleum/pri/spt"},
    "federal_reserve/fed_data": {"dataset": "H.15"},
    "economy/survey/bls_search": {"category": "cpi", "query": "gasoline"},
    "economy/survey/bls_series": {"symbol": "CUSR0000SA0", "category": "cpi"},
    "economy/fred_regional": {"symbol": "SMU56000000500000001"},
    "uscongress/search": {"query": "tariff", "collection": "BILLS"},       # bills: rows that drive Bill Info (review D2)
    # the widget defaults to USDJPY while Forward Points and Implied Vol next to it default to EURUSD (review B4)
    "cftc/fx_forward_curve": {"pair": "EURUSD"},
    # in-the-money options of the nearest expiries spike the smile to 200-600% IV (review B5): out-of-the-money by default
    "cboe/options/smile": {"otm": True},
    # the card's columns (bills, notes and bonds, MBS, total...) are the weekly summary; the detail view showed "–" rows (review F1)
    "economy/central_bank_holdings": {"summary": True},
    "sec/adviser_firms": {"query": "Vanguard"},
    "sec/adviser_individuals": {"query": "Buffett"},
    "sec/adviser_universe": {"registration_type": "registered"},
    "regulators/sec/sic_search": {"query": "software"},
    "regulators/sec/cik_map": {"symbol": "AAPL"},
    "regulators/sec/symbol_map": {"query": "0000320193"},
    "sec/symbol_map": {"query": "0000320193"},
    "sec/cik_map": {"symbol": "AAPL"},
    "equity/ownership/form_13f": {"symbol": "BRK-A"},
    "index/constituents": {"symbol": "BUK100P"},          # Cboe lists constituents of its European indices only (BUK100P = FTSE 100)
    "index/sectors": {"symbol": "^TSX"},
    "index/price/historical": {"symbol": "^GSPC"},
    "etf/nport_disclosure": {"symbol": "SPY"},
    "imf_utils/presentation_table": {"dataflow_group": "irfcl", "table": "irfcl_reserve_assets", "country": "JPN", "frequency": "M"},
    # FFIEC reports are keyed by RSSD id, not by ticker: JPMorgan Chase & Co. (holding company) and its main bank;
    # the custom peer group compares the four largest U.S. banks
    "federal_reserve/ffiec/bhcpr": {"rssd_id": "1039502"},
    "federal_reserve/ffiec/financial_report": {"rssd_id": "1039502"},
    "federal_reserve/ffiec/financial_report_pdf_download": {"rssd_id": "1039502"},
    "federal_reserve/ffiec/custom_peer_group": {"rssd_id": "852218", "peers": "480228,476810,451965"},
    "federal_reserve/ffiec/institution_structure": {"rssd_id": "1039502"},
    # the headline rate, not one age group or one region
    "economy/unemployment": {"age": "total", "sex": "total"},
    "eia/electricity_grid/demand": {"respondent": "US48"},
    "eia/electricity_grid/generation_by_fuel": {"respondent": "US48"},
    "federal_reserve/international_portfolio_investment": {"table": "table1"},
    "usda/livestock_and_meat_international_trade_data": {"table": "beef_veal", "direction": "exports", "frequency": "annual"},
    # bounded date ranges: FiscalData times out on its full history
    "ustreasury/auction_results": {"start_date": "@days:-120"},
    "ustreasury/weekly_bill_offerings": {"start_date": "@days:-365"},
    # swap data repositories publish nothing on weekends: ask for the last business day
    "cftc/swap_summary": {"date": "@bday:-1"},
    "cftc/swap_valuation": {"date": "@bday:-1"},
    "cftc/swap_trades": {"start_date": "@bday:-1", "end_date": "@bday:-1"},
    # the Canadian market tab must show Toronto, not Nasdaq
    "tmx/markets/movers": {"category": "gainers", "exchange": "tsx"},
    "ecb/mfi_interest_rates": {"symbol": "household_loans_for_house_purchase"},
    # calendars open on the days ahead (a provider's default is often today only: ten rows)
    "equity/calendar/earnings": {"start_date": "@days:0", "end_date": "@days:7"},
    "equity/calendar/dividend": {"start_date": "@days:0", "end_date": "@days:14"},
    "equity/calendar/splits": {"start_date": "@days:-30", "end_date": "@days:30"},
    "equity/calendar/ipo": {"start_date": "@days:-14", "end_date": "@days:30"},
}

# Parameters the backend requires although the widget definition marks them optional: the card asks for them
# ("Enter CUSIP to load") instead of sending a request that fails. Row clicks elsewhere on the tab can fill them.
# library datasets that open plotted (review G2: the Beveridge curve was a dense table): {endpoint: W(plot=...)}
CHOICE_PLOTS = {"bls/jolts/beveridge_curve": {"x": "unemployment_rate", "ys": ["job_openings_rate"], "split": "", "type": "scatter"}}
REQUIRED_PARAMS = {
    "finra/fixedincome/bond_historical": ["cusip"],
    "ustreasury/award": ["award_id"],
    "ustreasury/recipient_awards": ["recipient_id"],
    "sec/adviser_documents": ["crd"],
    "sec/adviser_universe": ["crd"],
    "sec/htm_file": ["url"],
    "sec/filing_headers": ["url"],
    "sec/filing_viewer_app": ["document_url"],
    "regulators/sec/htm_file": ["url"],
    "regulators/sec/filing_headers": ["url"],
    "eia/facet_options": ["group", "facet"],
}

# Listed in a backend's widgets.json but never registered as a route in this build (404 for every request): not
# real functions, so they are neither placed nor counted. The same data comes from fixedincome/rate/* and economy/*
# (New York Fed) and from etf/nport_disclosure (N-PORT).
UNREGISTERED = {"sec/nport_disclosure", "federal_reserve/ny/central_bank_holdings", "federal_reserve/ny/effr", "federal_reserve/ny/overnight_bank_funding",
                "federal_reserve/ny/primary_dealer_fails", "federal_reserve/ny/primary_dealer_positioning", "federal_reserve/ny/sofr"}

# Tab ids are slug(tab name). When a release renames or splits a built-in tab, map the old id to the new one here
# ({"dashboard id": {"old-tab-id": "new-tab-id"}}): a user's edited copy of the old tab then follows to the new tab
# instead of being set aside as "(from an older layout)" (static/app.js mergeDashboard).
RENAMED_TABS = {
    # 2026-11 product pass: one-card and duplicate tabs merged into their neighbours
    "markets": {"crypto-volatility": "fx-commodities-crypto", "fx-commodities": "fx-commodities-crypto"},
    "eq-company": {"news": "overview", "management-pay": "ownership-insiders", "filings": "filings-sec-data", "sec-statements": "filings-sec-data"},
    "eq-screen": {"institutions-insiders": "lookup-reference"},
    "indices": {"history-snapshots": "history-constituents", "constituents-sectors": "history-constituents", "documents": "history-constituents"},
    "credit": {"bank-lending": "lending-stress", "financial-stress": "lending-stress", "cds-index-trades": "spreads"},
    "options": {"market-wide-intrinio": "strategies"},
    "agri": {"farm-credit-fed": "usda-data-library"},
    "crypto": {"funding-volatility": "board", "reference": "indices-reference"},
    "eq-calendar": {"new-filings": "corporate"},
    "macro-country": {"indicators": "imf-world-data", "world-data-fed": "imf-world-data"},
    "macro-us": {"productivity-costs": "jobs"},
    "banking": {"bank-reports-ffiec": "regulators-reports", "federal-reserve": "regulators-reports", "fed-banks": "regulators-reports"},
    "trade": {"freight-shipping-stocks": "chokepoints"},
    "spending": {"spending": "spending-awards", "recipients": "spending-awards"},
    "filings": {"search": "search-read", "read": "search-read"},
    "factors": {"rotation": "live"},
}

# Lists that must open in their own order (whatever order the provider sends)
SORTS = {"equity/discovery/gainers": "percent_change:desc", "equity/discovery/losers": "percent_change:asc", "equity/discovery/active": "volume:desc",
         "etf/sectors": "weight:desc", "yfinance/funds/holdings": "weight:desc", "etf/holdings": "weight:desc", "etf/nport_disclosure": "weight:desc"}

# Plain-language card names by endpoint, used wherever a card has no title of its own (also on saved layouts:
# dashboards.json "titles" by widget id, read by static/widgets.js). No codes, no provider names, unit where it helps.
TITLES = {
    "fixedincome/rate/sofr": "SOFR (secured overnight rate, %)", "fixedincome/rate/effr": "Effective fed funds rate (%)",
    "fixedincome/rate/ameribor": "AMERIBOR (unsecured interbank rate, %)", "fixedincome/rate/dpcredit": "Discount window primary credit rate (%)",
    "fixedincome/rate/iorb": "Interest on reserve balances (IORB, %)", "fixedincome/rate/sonia": "SONIA (sterling overnight rate, %)",
    "fixedincome/rate/estr": "€STR (euro short-term rate, %)", "fixedincome/rate/overnight_bank_funding": "Overnight bank funding rate (%)",
    "fixedincome/rate/effr_forecast": "Fed funds rate projections (FOMC)", "fixedincome/rate/ecb": "ECB policy rates (%)",
    "fixedincome/spreads/tcm": "Treasury yield spreads (constant maturity)", "fixedincome/spreads/tcm_effr": "Treasury yields minus fed funds",
    "fixedincome/spreads/treasury_effr": "Treasury bills minus fed funds", "fixedincome/corporate/hqm": "Corporate bond yield curve (HQM)",
    "fixedincome/corporate/spot_rates": "Corporate spot rates by maturity", "economy/survey/sloos": "Senior loan officer survey (SLOOS)",
    "economy/pce": "Personal income and outlays (PCE)", "economy/gdp/real": "Real GDP", "economy/gdp/nominal": "Nominal GDP",
    "economy/gdp/forecast": "GDP forecast (OECD)", "economy/cpi": "Consumer prices (CPI)", "economy/unemployment": "Unemployment rate",
    "economy/interest_rates": "Interest rates (OECD)", "index/sp500_multiples": "S&P 500 valuation multiples", "equity/fundamental/esg_score": "ESG score",
    "equity/price/nbbo": "Best bid and offer (NBBO)", "equity/darkpool/otc": "Off-exchange (OTC) volume", "equity/estimates/historical": "Estimates history",
    "equity/price/historical": "Price history", "index/price/historical": "Index price history", "index/snapshots": "Index snapshots", "index/search": "Index search",
    "etf/historical": "ETF price history", "etf/info": "ETF profile", "etf/search": "ETF search", "yfinance/funds/historical": "Fund price history",
    "yfinance/funds/info": "Fund profile", "currency/price/historical": "Exchange-rate history", "currency/snapshots": "Currency snapshots",
    "currency/search": "Currency search", "currency/reference_rates": "ECB reference rates (units per 1 EUR)",
    "cftc/fx_forward_curve": "FX forward curve (always units per 1 USD)", "cftc/fx_forward_points": "FX forward points (pair as quoted)",
    "cftc/fx_forward_trades": "FX forward trades (pair order is not the quote direction)",
    "crypto/price/historical": "Crypto price history", "crypto/search": "Crypto search",
    "derivatives/futures/historical": "Futures price history", "derivatives/futures/info": "Futures contract details",
    "nasdaq/nordic/info": "Company information", "nasdaq/nordic/historical": "Price history", "nasdaq/nordic/historical_trades": "Recent trades",
    "equity/search": "Company search", "sec/cik_map": "Ticker to CIK lookup", "sec/sic_search": "Industry code (SIC) search", "sec/symbol_map": "CIK to ticker lookup", "sec/as_filed_statement": "As-filed financial statements",
    "yfinance/sectors/overview": "Sector overview", "yfinance/industry/overview": "Industry overview", "derivatives/options/snapshots": "Options market snapshot",
    "cftc/historical_fixings": "Rate fixings history", "equity/calendar/ipo": "IPO calendar", "equity/fundamental/historical_eps": "Reported EPS history",
    "equity/fundamental/historical_splits": "Stock splits", "equity/historical_market_cap": "Market cap history", "equity/profile": "Company profile",
    "equity/estimates/consensus": "Analyst consensus", "equity/estimates/price_target": "Analyst price targets", "cftc/cot": "Commitments of Traders report",
    "cftc/cot_index": "COT positioning index", "cftc/ois_policy_path": "OIS policy path", "economy/calendar": "Economic calendar",
    "equity/calendar/earnings": "Earnings calendar", "equity/calendar/dividend": "Dividend calendar", "equity/calendar/splits": "Splits calendar",
    "fixedincome/government/yield_curve": "Government yield curve", "fixedincome/government/treasury_rates": "Treasury yields by maturity (%)",
    "fixedincome/government/tips_yields": "TIPS yields", "fixedincome/government/svensson_yield_curve": "Fitted Treasury curve (Svensson)",
    "fixedincome/government/treasury_prices": "Treasury prices", "ecb/key_interest_rates": "ECB key interest rates",
}

# Provider pins where the generic preference picks a source that does not fit the dashboard.
PROVIDER_FIXES = {"tmx/markets/movers": "tmx", "economy/calendar": "nasdaq", "commodity/psd_data": "usda", "commodity/psd_report": "usda"}

# ------------------------------------------------------------------ concept siblings: one card, several sources
# The same concept served by different routes or data services (source inventory, local/notes/archive/source-inventory.md, section H).
# Each member: (endpoint, provider or None, label, options). Options: mk = markets the source covers (us, ca, crypto, nordic,
# other; a ticker-driven card takes the first member covering the ticker's market), sym = how the source wants the ticker
# ("tmx": RY for RY.TO; "nordic": VOLV B for VOLV-B.ST; "crypto_base": BTC for BTC-USD), rn = parameters renamed when
# switching to it, dp = parameters dropped, p = parameters pinned for it, wid = exact widget id when an endpoint has several,
# alt = target-only member (not indexed as an entry point so its own home tab keeps its default).
OPT = {"cboe": ("Cboe", {"mk": ["us"]}), "tmx": ("TMX (Montreal Exchange)", {"mk": ["ca"], "sym": "tmx"}), "deribit": ("Deribit", {"mk": ["crypto"], "sym": "crypto_base"})}
OPT_EP = {"cboe": "cboe/options/{}", "tmx": "tmx/derivatives/options/{}", "deribit": "deribit/options/{}"}
OPT_EXTRA = {("surface", "tmx"): {"dp": ["oi", "volume"]}, ("straddle", "deribit"): {"dp": ["strike"]},
             ("spreads", "deribit"): {"rn": {"near_moneyness": "moneyness"}, "dp": ["spread_type", "far_moneyness"]}}
OPT_LABELS = {"smile": "Implied-volatility smile", "surface": "Implied-volatility surface", "term_structure": "Options term structure", "stats": "Open interest and volume",
              "straddle": "Straddle", "strangle": "Strangle", "spreads": "Vertical spreads"}
NO_OPTIONS = dict(none="No listed-options source covers {sym}.", hint="The option chain on the Chain tab (Yahoo Finance) may still list it")
CONCEPTS = {f"options.{k}": dict(label=v, members=[(OPT_EP[s].format(k), None, OPT[s][0], dict(OPT[s][1], **OPT_EXTRA.get((k, s), {}))) for s in ("cboe", "tmx", "deribit")], **NO_OPTIONS)
            for k, v in OPT_LABELS.items()}
CONCEPTS.update({
    # the chain card routes by listing too: SU.TO -> TMX (Montreal Exchange), BTC-USD -> Deribit
    "options.chains": dict(label="Option chain", none="No listed-options source covers {sym}.", members=[
        ("derivatives/options/chains", "yfinance", "Yahoo Finance", {"mk": ["us"]}),
        ("derivatives/options/chains", "tmx", "TMX (Montreal Exchange)", {"mk": ["ca"], "sym": "tmx"}),
        ("derivatives/options/chains", "deribit", "Deribit", {"mk": ["crypto"], "sym": "crypto_base"}),
        ("derivatives/options/chains", "cboe", "Cboe (Yahoo quotes while Cboe refuses its feed)", {"mk": ["us"], "alt": True})]),
    "options.covered_calls": dict(label="Covered calls", none="Covered-call screens cover Canadian listings only (TMX); {sym} is not one.",
                                  members=[("tmx/derivatives/options/covered_calls", None, "TMX (Montreal Exchange)", {"mk": ["ca"], "sym": "tmx"})]),
    "fund.holdings": dict(label="Fund holdings", none="No keyless source has holdings for {sym}.", members=[
        ("etf/holdings", "tmx", "TMX", {"mk": ["ca"], "sym": "tmx"}),
        ("yfinance/funds/holdings", "yfinance", "Yahoo Finance (top holdings)", {"mk": ["us", "other", "nordic", "ca"]}),
        # when top holdings are not enough: the fund's own SEC filing (full list, quarterly, filed up to 60 days after the quarter)
        ("etf/nport_disclosure", "sec", "SEC N-PORT (quarterly filing, up to 60 days old)", {"mk": ["us"]}),
        ("etf/holdings", "nasdaq", "Nasdaq", {"mk": ["us"]})]),
    "etf.sectors": dict(label="ETF sector weights", none="No keyless source has sector weights for {sym}.",
                        hint="FMP has them for U.S. ETFs on a paid plan", members=[
        ("etf/sectors", "tmx", "TMX", {"mk": ["ca"], "sym": "tmx"}),
        # Stage 3 13a: Yahoo's fund sector breakdown (keyless) ahead of FMP for U.S. and other listings; TMX for .TO
        ("yfinance/funds/allocation", None, "Yahoo Finance (sector breakdown)", {"mk": ["us", "other", "nordic", "ca"], "p": {"breakdown": "sector"}}),
        ("etf/sectors", "fmp", "FMP (paid plan)", {"mk": ["us", "other"], "paid": True})]),
    "etf.countries": dict(label="ETF country weights", none="No keyless source has country weights for {sym}.",
                          hint="FMP has them for U.S. ETFs on a paid plan", members=[
        ("etf/countries", "tmx", "TMX", {"mk": ["ca"], "sym": "tmx"}), ("etf/countries", "fmp", "FMP (paid plan)", {"mk": ["us", "other"], "paid": True})]),
    "shorts.interest": dict(label="Short interest", none="No short-interest source covers {sym}.", members=[
        # FINRA first: it covers every U.S. listing (BWET on NYSE Arca from 2023); Nasdaq answers only for Nasdaq-listed
        # symbols (AAPL, QQQ yes; KO, SPY, BWET empty; probed 2026-11-18)
        ("equity/shorts/short_interest", "finra", "FINRA", {"mk": ["us"]}),
        ("equity/shorts/short_interest", "nasdaq", "Nasdaq (Nasdaq-listed only)", {"mk": ["us"]}),
        ("tmx/markets/short_interest", "tmx", "TMX", {"mk": ["ca"], "sym": "tmx"})]),
    "own.institutional": dict(label="Institutional holders", members=[
        ("equity/ownership/institutional", "nasdaq", "Nasdaq (U.S. 13F filers)", {"mk": ["us"]}),
        ("yfinance/ownership/institutional", "yfinance", "Yahoo Finance", {"mk": ["us", "ca", "other", "nordic"], "dp": ["limit", "holder_type", "sort_by"]})]),
    "own.insiders": dict(label="Insider trading", members=[
        ("equity/ownership/insider_trading", "yfinance", "Yahoo Finance", {"mk": ["us", "other", "nordic"]}),
        ("tmx/equity/ownership/insider_transactions", "tmx", "TMX (Canada)", {"mk": ["ca"], "sym": "tmx", "alt": True})]),
    "est.forward_eps": dict(label="Forward EPS estimates", members=[
        ("yfinance/estimates/earnings", "yfinance", "Yahoo Finance", {}), ("equity/estimates/forward_eps", "fmp", "FMP (paid plan)", {"paid": True})]),
    "sec.latest_reports": dict(label="Latest financial reports", members=[
        ("sec/latest_financial_reports", None, "SEC", {}), ("equity/discovery/latest_financial_reports", "sec", "SEC (standard route)", {})]),
    "company.filings": dict(label="Company filings",
        none="No free filing archive in OpenBB covers {sym}.",
        hint="Use a U.S.-listed ADR ticker (e.g., SAP, ASML, NVO) for SEC EDGAR filings",
        members=[
            ("sec/company_filings", "sec", "SEC (EDGAR)", {"mk": ["us"]}),
            ("equity/fundamental/filings", "tmx", "TMX (SEDAR+ Canada)", {"mk": ["ca"], "sym": "tmx"}),
            ("equity/fundamental/filings", "nasdaq", "Nasdaq (U.S. PDFs)", {"mk": ["us"]}),
            ("equity/fundamental/filings", "yfinance", "Yahoo Finance (SEC/ADR)", {"mk": ["us"]})]),
    "company.filing_viewer": dict(label="Filing viewer",
        none="No free filing viewer in OpenBB covers {sym}.",
        hint="Use a U.S.-listed ADR ticker (e.g., SAP, ASML, NVO) for SEC EDGAR filings",
        members=[
            ("sec/filing_viewer_app", None, "SEC (EDGAR)", {"mk": ["us"]}),
            ("tmx/equity/filings_view", None, "TMX (SEDAR+)", {"mk": ["ca"], "sym": "tmx", "alt": True})]),
    "company.overview": dict(label="Company overview", members=[
        ("sec/company_overview", "sec", "SEC (EDGAR)", {"mk": ["us"]}),
        ("tmx/equity/asset_info/view", None, "TMX (Canada)", {"mk": ["ca"], "sym": "tmx", "alt": True}),
        ("nasdaq/nordic/info", "nasdaq", "Nasdaq Nordic", {"mk": ["nordic"], "sym": "nordic", "alt": True}),
        ("equity/profile", "yfinance", "Yahoo Finance", {"mk": ["other", "us", "ca", "nordic"], "alt": True})]),
    "movers.gainers": dict(label="Top gainers", members=[
        ("equity/discovery/gainers", "yfinance", "Yahoo Finance (U.S.)", {}), ("nasdaq/markets/movers", "nasdaq", "Nasdaq (most advanced)", {"p": {"category": "most_advanced"}}),
        ("tmx/markets/movers", "tmx", "TMX (Toronto, among the 100 most active)", {"p": {"category": "gainers", "exchange": "tsx"}}),
        ("equity/discovery/gainers", "tmx", "TMX (Toronto gainers, standard route)", {})]),
    "movers.losers": dict(label="Top losers", members=[
        ("equity/discovery/losers", "yfinance", "Yahoo Finance (U.S.)", {}), ("nasdaq/markets/movers", "nasdaq", "Nasdaq (most declined)", {"p": {"category": "most_declined"}}),
        ("tmx/markets/movers", "tmx", "TMX (Toronto, among the 100 most active)", {"p": {"category": "losers", "exchange": "tsx"}})]),
    "movers.active": dict(label="Most active", members=[
        ("equity/discovery/active", "yfinance", "Yahoo Finance (U.S.)", {}), ("nasdaq/markets/movers", "nasdaq", "Nasdaq (most active, shares)", {"p": {"category": "most_active_share_volume"}})]),
    "movers.etf": dict(label="ETF movers", members=[
        ("etf/discovery/gainers", "wsj", "WSJ: gainers", {}), ("etf/discovery/losers", "wsj", "WSJ: losers", {}), ("etf/discovery/active", "wsj", "WSJ: most active", {})]),
    "rates.ecb_policy": dict(label="ECB policy rates", members=[
        ("ecb/key_interest_rates", "ecb", "ECB", {}), ("fixedincome/rate/ecb", "fred", "FRED (copy of the ECB rates)", {})]),
    "fred.release_table": dict(label="FRED release table", members=[
        ("economy/fred_release_table", "fred", "FRED", {}), ("fred/economy/release_table", None, "FRED (with trend column)", {})]),
    "macro.tfp": dict(label="Total factor productivity", members=[
        ("federal_reserve/sf/total_factor_productivity", "federal_reserve", "San Francisco Fed", {}),
        ("economy/total_factor_productivity", "federal_reserve", "San Francisco Fed (legacy data service)", {})]),
})
for _fn, _lab in (("list_dataflows", "SDMX dataflows"), ("list_tables", "SDMX tables"), ("presentation_table", "SDMX presentation table"),
                  ("get_dataflow_dimensions", "SDMX dataflow dimensions")):
    _oecd = "get_dataflow_parameters" if _fn == "get_dataflow_dimensions" else _fn
    CONCEPTS["sdmx." + _fn] = dict(label=_lab, members=[
        (f"ecb/{_fn}", None, "ECB", {"rn": {"dataflow_id": "dataflow", "country": "reference_area"}}),
        (f"imf/{_fn}", None, "IMF", {"rn": {"dataflow": "dataflow_id", "reference_area": "country"}}),
        (f"oecd/{_oecd}", None, "OECD", {"rn": {"dataflow": "dataflow_id", "reference_area": "country"}}),
        (f"imf_utils/{_fn}", None, "IMF (legacy data service)", {"rn": {"dataflow": "dataflow_id", "reference_area": "country"}})])
for _st, _st_lab, _swid, _tmx_ep, _yf_ep in (
    ("income", "Income statement", "sec_income_statement_facts_sec_obb", "tmx/equity/fundamental/income_view", "equity/fundamental/income"),
    ("balance", "Balance sheet", "sec_balance_sheet_facts_sec_obb", "tmx/equity/fundamental/balance_view", "equity/fundamental/balance"),
    ("cash", "Cash flow statement", "sec_cash_flow_facts_sec_obb", "tmx/equity/fundamental/cash_view", "equity/fundamental/cash"),
):
    CONCEPTS["company." + _st] = dict(label=_st_lab, members=[
        ("sec/standardized_statement", None, "SEC (EDGAR XBRL)", {"mk": ["us"], "p": {"statement_type": _st}, "wid": _swid}),
        (_tmx_ep, None, "TMX (Canada)", {"mk": ["ca"], "sym": "tmx", "dp": ["statement_type", "transform", "transpose"], "alt": True}),
        # the Financials tab's own cards (Yahoo, or SEC company facts when picked) are entry points too, so SHEL.L routes off SEC
        (_yf_ep, "sec", "SEC (company facts)", {"mk": ["us"], "dp": ["statement_type", "transform", "transpose"]}),
        (_yf_ep, "yfinance", "Yahoo Finance", {"mk": ["other", "nordic", "us", "ca"], "dp": ["statement_type", "transform", "transpose"]}),
        ("nasdaq/nordic/fundamentals", "nasdaq", "Nasdaq Nordic (fact sheet)", {"mk": ["nordic"], "sym": "nordic", "dp": ["statement_type", "period", "transform", "transpose", "limit"], "alt": True})])
for _fn in ("cik_map", "symbol_map", "sic_search", "institutions_search", "filing_headers", "htm_file", "schema_files", "rss_litigation"):
    CONCEPTS["sec." + _fn] = dict(label="SEC " + _fn.replace("_", " "), members=[
        (f"sec/{_fn}", "sec", "SEC", {}), (f"regulators/sec/{_fn}", "sec", "SEC (legacy data service)", {})])
CONCEPTS["index.sp500_multiples"] = dict(label="S&P 500 valuation multiples", members=[
    ("x:sp500_multiples", None, "multpl.com", {}),
    ("index/sp500_multiples", "multpl", "multpl (legacy V4)", {}),
])

# Families whose cards are titled by the concept, not by one provider's widget name ("Implied-volatility smile", not "Cboe Options Smile").
for _cid in list(CONCEPTS):
    if _cid.startswith(("options.", "fund.", "etf.", "shorts.", "own.", "est.", "movers.", "company.")):
        CONCEPTS[_cid]["title"] = CONCEPTS[_cid]["label"]

# Reached through a card's source picker instead of being placed as a card of their own (the duplicate of a placed member).
SKIP = {f"tmx/derivatives/options/{k}" for k in OPT_LABELS} | {f"regulators/sec/{f}" for f in ("cik_map", "symbol_map", "sic_search", "filing_headers", "htm_file",
        "schema_files", "rss_litigation")} | {"equity/discovery/latest_financial_reports", "equity/fundamental/filings", "nasdaq/markets/movers", "etf/discovery/losers", "etf/discovery/active",
        "fixedincome/rate/ecb", "fred/economy/release_table", "economy/total_factor_productivity", "equity/estimates/forward_eps", "equity/ownership/institutional",
        "imf_utils/list_dataflows", "imf_utils/list_tables", "imf_utils/presentation_table", "imf_utils/get_dataflow_dimensions", "index/sp500_multiples"}


# Scrapers that no longer return data (blocked or shut down upstream); their widgets are left out.
DEAD = {"stockgrid", "seeking_alpha"}

# Providers that need an API key, and the credential each one reads.
KEYS = {"fred": "fred_api_key", "bls": "bls_api_key", "eia": "eia_api_key", "congress_gov": "congress_gov_api_key", "fmp": "fmp_api_key", "polygon": "polygon_api_key",
        "tiingo": "tiingo_token", "intrinio": "intrinio_api_key", "alpha_vantage": "alpha_vantage_api_key", "tradier": "tradier_api_key", "benzinga": "benzinga_api_key",
        "biztoc": "biztoc_api_key", "tradingeconomics": "tradingeconomics_api_key"}
KEY_LABELS = {"fred": "FRED", "bls": "BLS", "eia": "EIA", "congress_gov": "Congress.gov", "fmp": "FMP", "polygon": "Polygon", "tiingo": "Tiingo", "intrinio": "Intrinio (paid)",
              "alpha_vantage": "Alpha Vantage", "tradier": "Tradier", "benzinga": "Benzinga (paid)", "biztoc": "BizToc", "tradingeconomics": "Trading Economics (paid)"}
KEY_SIGNUP = {"fred": "https://fredaccount.stlouisfed.org/apikeys", "bls": "https://data.bls.gov/registrationEngine/", "eia": "https://www.eia.gov/opendata/register.php",
              "congress_gov": "https://api.congress.gov/sign-up/", "fmp": "https://site.financialmodelingprep.com/developer/docs",
              "polygon": "https://polygon.io/dashboard/signup", "tiingo": "https://www.tiingo.com/account/api/token", "alpha_vantage": "https://www.alphavantage.co/support/#api-key"}

# FRED without a key: scripts/run_backend.py serves series requests from FRED's public download, so most FRED cards
# work keyless. These still need the key (search, release tables, GeoFRED maps are not in the public download).
FRED_KEY_ONLY = {"economy/fred_search", "economy/fred_release_table", "economy/fred_regional", "economy/pce", "economy/survey/nonfarm_payrolls",
                 "fixedincome/corporate/hqm", "fixedincome/government/tips_yields"}
# ... and for these, a keyless build shows the same series as a curated chart (composite.MACRO_SETS) instead.
FRED_KEYLESS_SUB = {"fixedincome/government/tips_yields": "real_yields", "fixedincome/corporate/hqm": "hqm_yields", "economy/pce": "pce_report"}

# The dashboard whose last tab, "Data status", holds everything broken upstream.
STATUS_DASH = "explorers"

MACRO_COUNTRIES = ["united_states", "euro_area", "germany", "france", "italy", "spain", "united_kingdom", "japan", "canada", "australia", "china", "india", "brazil", "south_korea", "mexico", "switzerland",
                   "sweden", "norway", "denmark", "netherlands", "belgium", "austria", "ireland", "portugal", "greece", "poland", "turkey", "south_africa", "indonesia", "israel",
                   "chile", "colombia", "new_zealand", "finland"]

SIZES = {"table": (20, 14), "chart": (20, 16), "iframe": (40, 22), "html": (40, 20), "markdown": (20, 16), "newsfeed": (20, 16), "multi_file_viewer": (20, 22), "pdf": (20, 22)}

# Where anything not placed above ends up, by endpoint prefix (first match wins).
FALLBACK = [("federal_reserve/", "macro-us"), ("economy/", "macro-country"), ("equity/", "eq-screen"), ("nasdaq/", "eq-screen"), ("yfinance/", "eq-screen"),
            ("sec/", "filings"), ("regulators/", "filings"), ("tmx/", "eq-canada"), ("cboe/", "options"), ("derivatives/", "futures"), ("finra/", "credit"),
            ("fixedincome/", "rates"), ("fred/", "explorers"), ("usda/", "agri"), ("", "explorers")]
