"""Generate local/dashboards.json: the Terminal's asset-class dashboards.

Reads the live widget lists of both OpenBB backends plus the Terminal's own
composite views, then lays every data function out under a logical dashboard
and tab (asset class / topic) instead of by data provider. A function offered
by several providers is placed once, using the best provider that needs no key;
the UI lets you switch source on the card.

Usage (backends must be running):  python scripts/build_dashboards.py [--report]
The output is generated on every start, so it lives with this computer's data in
local/ (see terminal/local_paths.py), not in the project files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
import columns  # noqa: E402
import composite  # noqa: E402
import local_paths  # noqa: E402
import xwidgets  # noqa: E402

OUT = local_paths.DASHBOARDS_FILE
# Data functions known to fail upstream right now ({endpoint: reason}); they are kept in Data Explorers > Data status.
HEALTH = ROOT / "terminal" / "health.json"
BACKENDS = {"v5": f"http://127.0.0.1:{os.environ.get('TERMINAL_V5_PORT') or 6900}", "v4": f"http://127.0.0.1:{os.environ.get('TERMINAL_V4_PORT') or 6901}"}

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
    "ustreasury/weekly_bill_offerings": {"start_date": "@days:-90"},
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
SORTS = {"equity/discovery/gainers": "percent_change:desc", "equity/discovery/losers": "percent_change:asc", "equity/discovery/active": "volume:desc"}

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
    "currency/search": "Currency search", "crypto/price/historical": "Crypto price history", "crypto/search": "Crypto search",
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
# The same concept served by different routes or data services (source inventory, local/notes/source-inventory.md, section H).
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
    "options.covered_calls": dict(label="Covered calls", none="Covered-call screens cover Canadian listings only (TMX); {sym} is not one.",
                                  members=[("tmx/derivatives/options/covered_calls", None, "TMX (Montreal Exchange)", {"mk": ["ca"], "sym": "tmx"})]),
    "fund.holdings": dict(label="Fund holdings", none="No keyless source has holdings for {sym}.", members=[
        ("etf/holdings", "nasdaq", "Nasdaq (full list)", {"mk": ["us"]}), ("etf/holdings", "tmx", "TMX", {"mk": ["ca"], "sym": "tmx"}),
        ("yfinance/funds/holdings", "yfinance", "Yahoo Finance (top holdings)", {"mk": ["us", "ca", "other", "nordic"]})]),
    "etf.sectors": dict(label="ETF sector weights", none="No keyless source has sector weights for {sym}.",
                        hint="FMP has them for U.S. ETFs on a paid plan", members=[
        ("etf/sectors", "tmx", "TMX", {"mk": ["ca"], "sym": "tmx"}), ("etf/sectors", "fmp", "FMP (paid plan)", {"mk": ["us", "other"], "paid": True})]),
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
        ("tmx/markets/movers", "tmx", "TMX (Toronto, among the 100 most active)", {"p": {"category": "gainers", "exchange": "tsx"}})]),
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
        (_yf_ep, "yfinance", "Yahoo Finance", {"mk": ["other", "nordic", "us", "ca"], "dp": ["statement_type", "transform", "transpose"], "alt": True}),
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


def needs_key(prov, ep):
    """The credential a card needs, or None (FRED series work without a key; see FRED_KEY_ONLY)."""
    if prov == "fred" and ep not in FRED_KEY_ONLY:
        return None
    return KEYS.get(prov)


# The dashboard whose last tab, "Data status", holds everything broken upstream.
STATUS_DASH = "explorers"


def credentials():
    try:
        return {k for k, v in json.loads((Path.home() / ".openbb_platform" / "user_settings.json").read_text(encoding="utf-8")).get("credentials", {}).items() if v}
    except (OSError, ValueError):
        return set()


MACRO_COUNTRIES = ["united_states", "euro_area", "germany", "france", "italy", "spain", "united_kingdom", "japan", "canada", "australia", "china", "india", "brazil", "south_korea", "mexico", "switzerland",
                   "sweden", "norway", "denmark", "netherlands", "belgium", "austria", "ireland", "portugal", "greece", "poland", "turkey", "south_africa", "indonesia", "israel",
                   "chile", "colombia", "new_zealand", "finland"]

SIZES = {"table": (20, 14), "chart": (20, 16), "iframe": (40, 22), "html": (40, 20), "markdown": (20, 16), "newsfeed": (20, 16), "multi_file_viewer": (20, 22), "pdf": (20, 22)}


# ------------------------------------------------------------------ index
CATALOG = local_paths.CACHE_DIR / "catalog-{}.json"     # last widgets.json of each backend: lets scripts/tests replay a build offline


def load():
    defs = {"x": xwidgets.widgets()}
    with_v4 = os.environ.get("TERMINAL_WITH_V4", "").strip().lower()
    for bid, url in BACKENDS.items():
        if bid == "v4" and with_v4 in ("0", "false", "no"):
            defs[bid] = {}
            continue
        try:
            with urllib.request.urlopen(url + "/widgets.json", timeout=5 if bid == "v4" else 120) as r:
                defs[bid] = json.load(r)
            try:
                local_paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = Path(str(CATALOG).format(bid) + ".tmp")
                tmp.write_text(json.dumps(defs[bid], separators=(",", ":")), encoding="utf-8")
                os.replace(tmp, str(CATALOG).format(bid))
            except OSError:
                pass                            # only a test aid
        except OSError:
            if bid != "v4":
                raise
            # The legacy backend is optional (opt-in via --with-v4): build without its sources.
            print(f"Optional legacy V4 backend not running at {url}: building V5-only dashboards")
            defs[bid] = {}
    return defs


def provider_of(d):
    for p in d.get("params", []):
        if p.get("paramName") == "provider":
            return p.get("value")
    return None


def is_chart_variant(d):
    return d.get("type") == "chart" and any(p.get("paramName") == "chart" for p in d.get("params", []))


class Index:
    def __init__(self, defs):
        self.defs = defs
        self.by_ep = {}            # (endpoint, chart) -> [(rank, bid, wid)]
        for bid, ws in defs.items():
            for wid, d in ws.items():
                ep = d.get("endpoint", "")
                ep = "x:" + ep[3:] if bid == "x" else ep.replace("/api/v1/", "")
                prov = provider_of(d)
                if prov in DEAD or ep in UNREGISTERED:
                    continue
                rank = (PREF.index(prov) if prov in PREF else len(PREF) - 3 if prov else 0, 0 if bid == "v5" else 1)
                self.by_ep.setdefault((ep, is_chart_variant(d)), []).append((rank, bid, wid, prov))
        for k in self.by_ep:
            self.by_ep[k].sort()
        self.functions = sorted({ep for ep, chart in self.by_ep if not chart} | {ep for ep, chart in self.by_ep if chart and (ep, False) not in self.by_ep})

    def pick(self, ep, chart=False, provider=None, wid=None):
        cands = self.by_ep.get((ep, chart)) or (self.by_ep.get((ep, False)) if chart else None) or self.by_ep.get((ep, True))
        if not cands:
            return None
        if wid:
            for c in cands:
                if c[2] == wid:
                    return c[1], c[2]
            return None
        if provider:
            for c in cands:
                if c[3] == provider:
                    return c[1], c[2]
        return cands[0][1], cands[0][2]

    def match(self, pattern):
        """'ns/sub/*' glob or regex (prefix 're:'), optional '@Sub Category' filter."""
        sub = None
        if "@" in pattern:
            pattern, sub = pattern.split("@", 1)
        rx = re.compile(pattern[3:] if pattern.startswith("re:") else re.escape(pattern).replace(r"\*", ".*"))
        out = []
        for ep in self.functions:
            if not rx.fullmatch(ep):
                continue
            if sub is not None:
                b, w = self.pick(ep)
                if (self.defs[b][w].get("subCategory") or "") != sub:
                    continue
            out.append(ep)
        return out


# ------------------------------------------------------------------ spec DSL
def W(ep, w=None, h=None, provider=None, chart=False, params=None, title=None, groups=None, compact=None, view=None, manual=None, sort=None, since=None, cols=None, plot=None, wid=None, only=None,
      tparam=None):
    """sort="date:asc" opens the table sorted that way; since="today" (with sort on a date) hides rows before today.
    tparam: {"param": name, "labels": {VALUE: label}}: the title ends with the parameter's current value, e.g. "(E-mini S&P 500)"
    for ES and "(CL)" once the owner types CL (a fixed "(E-mini S&P 500)" was wrong after a change; review F3).
    cols: the columns this card shows first (overrides the endpoint preset in terminal/columns.py).
    plot: open the table as a chart, {"x": field, "ys": [fields], "split": field, "type": line|step|bar|area|scatter}.
    wid: pin a specific backend widgetId when multiple widgets share an endpoint.
    only: the providers the card's Source menu offers (a commodity curve is not on Cboe or Deribit)."""
    return {"ep": ep, "w": w, "h": h, "provider": provider, "chart": chart, "params": params or {}, "title": title, "groups": groups, "compact": compact,
            "view": "chart" if plot else view, "manual": manual, "sort": sort, "since": since, "cols": cols, "plot": plot, "wid": wid, "only": only, "tparam": tparam}


def CURVE(symbol, title, w=20, h=16):
    """A futures curve drawn as a curve: price by expiry, nearest contract first."""
    return W("derivatives/futures/curve", w, h, provider="yfinance", params={"symbol": symbol}, title=title, plot={"x": "expiration", "ys": ["price"], "split": "", "type": "line"},
             only=["yfinance"])          # the endpoint's Cboe (VX) and Deribit (crypto) curves do not list commodities (review C3)

# The economic calendar people read: event names, consensus, previous, actual; upcoming events first, from today.
ECON_CAL = dict(provider="nasdaq", sort="date:asc", since="today")


def M(preset, w=20, h=16, **kw):
    """Curated economic chart (a named set of FRED series)."""
    return W("x:macro_chart", w, h, params=dict(preset=preset, **kw))


def macro_key_provider(preset):
    """'eia' when every series of a curated chart comes from EIA (no keyless fallback), else None (FRED series fall
    back to FRED's public download, calculated series need nothing)."""
    entry = composite.MACRO_SETS.get(preset or "")
    ids = [s[1] for s in (entry[3] if entry and len(entry) > 3 else [])]
    return "eia" if ids and all(str(i).startswith("EIA:") for i in ids) else None


def C(group, fact, w=20, h=18, period=None, **kw):
    """One reported figure across a peer group, from SEC filings."""
    return W("x:compare", w, h, params=dict(group=group, fact=fact, **({"period": period} if period else {}), **kw))


def LIB(title, selectors, w=40, h=26, groups=None, first=None):
    """One card holding a whole family of datasets, with a picker (instead of pages of near-identical cards).

    A selector prefixed "rest:" ("rest:usda/*") only takes what this dashboard has not placed yet.
    `first` names the endpoint the card opens on (the most useful dataset of the family, not the alphabetical first)."""
    spec = W("lib:" + title, w, h, title=title, groups=groups)
    spec["library"] = selectors
    spec["first"] = first
    return spec


def METRICS(symbols, title, w=20, h=16):
    """Valuation, growth and margin comparison for a list of tickers."""
    return W("equity/fundamental/metrics", w, h, provider="yfinance", params={"symbol": symbols}, title=title, groups=[])


# analyst actions (upgrades, downgrades, target changes): newest first, the columns a desk reads
ANALYST_ACTIONS = dict(sort="published_date:desc", cols=["published_date", "analyst_firm", "rating_current", "rating_previous", "price_target",
                                                        "price_target_previous", "price_target_action"])


def X(name, w=None, h=None, title=None, groups=None, **params):
    return W("x:" + name, w, h, params=params, title=title, groups=groups)


def NEWS(w=None, h=None, title=None, groups=None, compact=None, **params):
    """Ranked or monitor headlines (terminal/news.py): subject="symbol" follows the dashboard ticker, "country" its country,
    "market" the whole market; mode="monitor" lists newest first (the situation room)."""
    return W("x:news", w, h, params=params, title=title, groups=groups, compact=compact)


def OVB(preset, h):
    """A Markets Summary > Overview board: five columns fit a third of the screen (14 of 17 clipped Symbol/Ccy; review F1).
    Yields keep Chg (basis points), currencies their quote convention."""
    cols = ["name", "quote", "price", "chg_pct", "ytd"] if preset == "fx" else \
        ["name", "price", "chg", "chg_pct", "ytd"] if preset == "fixed_income" else ["name", "price", "chg_pct", "w1", "ytd"]
    return W("x:board", None, h, params={"preset": preset}, cols=cols)


def WATCH(w=None, h=None):
    """The user's own watchlist (lists are kept in the saved state, edited on the card)."""
    return X("watchlist", w, h, groups=[])


HYPERSCALERS = "MSFT,GOOGL,AMZN,META,ORCL"


def T(name, rows=None, auto=None, columns=None, cols=2, groups=None, h=None):
    return {"name": name, "rows": rows or [], "auto": auto or [], "columns": columns, "cols": cols, "groups": groups, "h": h}


def D(did, name, section, desc, tabs, groups=None, more=None):
    return {"id": did, "name": name, "section": section, "desc": desc, "tabs": tabs, "groups": groups or [], "more": more or []}


SYMBOL = {"name": "Symbol", "paramName": "symbol", "value": "AAPL"}
# Company > Estimates: which earnings report the scorecard and the release show (latest unless picked); the choices are the
# company's own reports, and a new company starts again on its latest
REPORT = {"name": "Report", "paramName": "report", "value": "", "optionsEndpoint": "/x/earnings_reports", "optionsParams": {"symbol": "$symbol"},
          "resetWith": "Symbol"}
COUNTRY = {"name": "Country", "paramName": "country", "value": "united_states", "options": "countries"}
MACRO_COUNTRY = {"name": "Country", "paramName": "country", "value": "united_states", "options": "macro"}

SECTIONS = [
    ("overview", "Overview"), ("equities", "Equities"), ("funds", "Indices, ETFs & Funds"), ("rates", "Rates & Credit"), ("derivatives", "Derivatives"),
    ("fx", "Currencies"), ("commodities", "Commodities & Energy"), ("crypto", "Crypto"), ("macro", "Macro & Economy"), ("policy", "Government & Regulation"),
    ("research", "News & Research"),
]

PERF = lambda syms, period="YTD", **kw: X("performance", symbols=syms, period=period, **kw)  # noqa: E731

DASHBOARDS = [
    # ============================================================ OVERVIEW
    D("markets", "Markets Summary", "overview", "Cross-asset overview: equities, sectors, rates, credit, currencies, commodities, crypto, movers and news on one screen.", [
        T("Overview", columns=[
            (12, [W("x:watchlist", h=16, groups=[], cols=["name", "currency", "price", "chg_pct", "ytd"]), OVB("us_equity", 15), OVB("us_sectors", 17), OVB("fixed_income", 22)]),
            # review G2: the chart was ~610 px and Global Yields 42 rows while Market News showed two headlines
            (16, [NEWS(h=14, subject="market", title="Top market news", groups=[], compact=True),
                  PERF("^GSPC,^NDX,^DJI,^RUT", h=17, title="Normalized Performance: U.S. indices"),
                  W("yfinance/tv_widget/view", h=15, title="Chart"), X("global_yields", h=16, countries="g10", title="Global Yields (G10)"), X("style_box", h=9)]),
            (12, [OVB("fx", 15), OVB("global_markets", 30), OVB("commodities", 25), OVB("crypto", 20)]),
        ]),
        T("Chart", rows=[[W("yfinance/tv_widget/view", 28, 26), WATCH(12, 26)],
                         [X("chart", 28, 22, title="Compare chart",compare="^NDX,^RUT", view="pct", period="1Y", studies="ma50,ma200"), X("board", 12, 22, preset="megacaps")],
                         [X("board", 20, 18, preset="us_equity"), X("board", 20, 18, preset="global_indices")]]),
        T("Equities", rows=[[X("board", 15, 24, preset="global_indices"), PERF("^GSPC,^STOXX50E,^N225,^HSI,EEM", w=25, h=24, title="Normalized Performance: world equities")],
                            [X("perf_bars", 13, 18, preset="us_sectors", period="1M"), X("perf_bars", 14, 18, preset="global_markets", period="1M"),
                             X("perf_bars", 13, 18, preset="us_industries", period="1M")],          # one horizon per row (review G2)
                            [X("perf_bars", 14, 22, preset="themes", period="1M"), X("board", 26, 22, preset="themes")],
                            [X("board", 15, 18, preset="volatility"), PERF("^VIX,^MOVE,^OVX", "1Y", w=12, h=18, title="Normalized Performance: volatility"), X("board", 13, 18, preset="us_factors")],
                            [W("equity/discovery/gainers", 13, 16), W("equity/discovery/losers", 14, 16), W("equity/discovery/active", 13, 16)]]),
        T("Rates & Credit", rows=[[X("global_yields", 24, 20), X("yield_curves", 16, 20)],
                                  [X("policy_rates", 14, 18), X("policy_rate_history", 13, 18), X("board", 13, 18, preset="fixed_income")],
                                  [M("credit_spreads", 14, 16), M("treasury_yields", 13, 16), M("fed_liquidity", 13, 16)],
                                  [X("board", 20, 14, preset="us_rates"), X("cds_indices", 20, 14)]]),
        T("FX, Commodities & Crypto", rows=[[X("board", 15, 22, preset="fx"), X("perf_bars", 12, 22, preset="fx", period="1M"), PERF("DX-Y.NYB,EURUSD=X,JPY=X,GBPUSD=X", "1Y", w=13, h=22, title="Normalized Performance: majors")],
                                            [X("board", 15, 22, preset="commodities"), X("perf_bars", 12, 22, preset="commodities", period="1M"), PERF("CL=F,GC=F,HG=F,NG=F", "1Y", w=13, h=22, title="Normalized Performance: commodities")],
                                            [X("board", 15, 18, preset="crypto"), PERF("BTC-USD,ETH-USD,SOL-USD", "1Y", w=13, h=18, title="Normalized Performance: crypto"), X("crypto_vol", 12, 18, title="Bitcoin implied vs realised volatility")]]),
        T("Risk & Geopolitics", columns=[
            (14, [X("board", h=30, preset="risk_monitor")]),
            (13, [M("gpr_daily", h=15, since="2019"), M("oil_volatility", h=15, since="2021")]),
            (13, [M("policy_uncertainty", h=15, since="2019"), M("chokepoint_tankers", h=15, since="2023")]),
        ], rows=[[X("chokepoint_monitor", 24, 16), X("gpr_table", 16, 16)]]),
        T("News & Calendar", rows=[[NEWS(27, 22, subject="market", mode="monitor", title="Market monitor (newest first)", groups=[]), W("economy/calendar", 13, 22, title="Economic calendar (upcoming)", **ECON_CAL)],
                                   [W("equity/calendar/earnings", 20, 16, provider="nasdaq"), W("equity/calendar/ipo", 20, 16)]]),
    ], groups=[dict(SYMBOL, value="^GSPC")]),

    # ============================================================ EQUITIES
    D("eq-company", "Company", "equities", "Everything about one company: price, profile, statements, estimates, ownership, filings, options. Type a ticker once.", [
        # Tab order follows how an analyst works a name: DES, FA, EE/ANR, RV, HDS, EVTS, then the U.S.-only depth (SEC, filings, shorts, options).
        # DES: chart, snapshot and company news first, consensus and recent analyst actions beside them, comps below
        T("Overview", columns=[(24, [W("yfinance/tv_widget/view", h=22), X("snapshot", h=26)]),
                               (16, [NEWS(h=22, subject="symbol", title="Company news"), W("equity/estimates/consensus", h=12, title="Analyst consensus"),
                                     W("equity/estimates/price_target", h=14, title="Latest analyst actions", **ANALYST_ACTIONS)])],
          rows=[[X("comps", 40, 16, title="Comparable companies")]]),
        T("Financials", cols=1, h=16, auto=["equity/fundamental/income", "equity/fundamental/balance", "equity/fundamental/cash"]),
        # EE / ANR, earnings day first: the report against the consensus (with the reaction and the next quarter the company
        # guides for), how the consensus is moving, the record of surprises, what analysts did since; everything else in one library
        T("Estimates", rows=[
            [X("earnings", 18, 22, title="Earnings: expected vs reported"), X("earnings_release", 22, 22, title="Earnings release (SEC 8-K, exhibit 99.1)")],
            [X("earnings_history", 20, 16, title="EPS surprise history"), X("consensus", 20, 16, title="Consensus and revisions")],
            [W("equity/estimates/price_target", 20, 14, title="Analyst actions", **ANALYST_ACTIONS), W("equity/estimates/consensus", 20, 14, title="Analyst consensus")],
            [W("yfinance/estimates/eps_revisions", 20, 12, title="EPS estimate revisions: analysts raising vs cutting (7 and 30 days)"), LIB("More estimates", ["rest:yfinance/estimates/*", "rest:equity/estimates/*"], w=20, h=12)]]),
        T("Price & Peers", rows=[[X("chart", 40, 24, compare="^GSPC", view="pct", period="1Y")],
                                 [X("comps", 40, 18)], [W("equity/fundamental/metrics", 20, 14), W("equity/price/quote", 20, 14)],
                                 [W("equity/profile", 20, 14, title="Company profile"), W("yfinance/asset_info/view", 20, 14)]],
          auto=["equity/price/historical", "equity/compare/peers", "equity/compare/groups", "equity/price/nbbo"]),
        T("Growth & Ratios", auto=["equity/fundamental/income_growth", "equity/fundamental/balance_growth", "equity/fundamental/cash_growth", "equity/fundamental/ratios",
                                    "equity/historical_market_cap", "equity/fundamental/trailing_dividend_yield", "equity/price/performance"]),
        T("Ownership & Insiders", auto=["yfinance/ownership/major", "yfinance/ownership/institutional", "yfinance/ownership/mutual_fund", "equity/ownership/insider_trading",
                                         "equity/ownership/share_statistics", "yfinance/ownership/*", "equity/ownership/institutional", "equity/ownership/major_holders", "sec/beneficial_ownership",
                                         "equity/fundamental/management", "equity/fundamental/management_compensation", "sec/executive_compensation", "sec/management_profiles",
                                         "sec/pay_versus_performance"]),
        T("Dividends & Events", auto=["yfinance/company_calendar", "equity/fundamental/dividends", "equity/fundamental/historical_eps", "equity/fundamental/historical_splits",
                                       "equity/fundamental/transcript", "equity/calendar/events"]),
        T("Segments & KPIs", auto=["equity/fundamental/revenue_per_segment", "equity/fundamental/revenue_per_geography", "equity/fundamental/employee_count",
                                    "equity/fundamental/historical_attributes", "equity/fundamental/latest_attributes", "equity/fundamental/search_attributes", "equity/fundamental/esg_score"]),
        # filings & header drill-down into the viewer first, then overview/MD&A/sections/exhibits, then XBRL & as-filed statements
        T("Filings & SEC Data", rows=[
            [W("sec/company_filings", 20, 14), W("sec/filing_headers", 20, 14)],
            [W("sec/filing_viewer_app", 40, 24, groups=["Symbol"])],
            [W("sec/company_overview", 20, 16), W("equity/fundamental/management_discussion_analysis", 20, 16)],
            [LIB("SEC Filing Sections", [
                W("sec/section_markdown", wid="sec_risk_factors_sec_obb"),
                W("sec/section_markdown", wid="sec_segment_revenue_sec_obb"),
                W("sec/section_markdown", wid="sec_disclosures_sec_obb"),
                W("sec/section_markdown", wid="sec_legal_proceedings_sec_obb"),
            ], w=20, h=16, first="sec_risk_factors_sec_obb"), W("sec/exhibit", 20, 16)],
            [W("sec/standardized_statement", 20, 16, wid="sec_income_statement_facts_sec_obb"),
             W("sec/standardized_statement", 20, 16, wid="sec_balance_sheet_facts_sec_obb")],
            [W("sec/standardized_statement", 20, 16, wid="sec_cash_flow_facts_sec_obb"), W("sec/as_filed_statement", 20, 16)],
        ], auto=[W("nasdaq/equity/filings_viewer", 20, 16), W("equity/compare/company_facts", 20, 16), "equity/fundamental/reported_financials"]),
        T("Shorts & Dark Pool", auto=["equity/shorts/*", "equity/darkpool/otc"]),
        T("Options", rows=[[W("derivatives/options/chains", 40, 16, provider="yfinance", groups=["Symbol"])]], auto=["cboe/options/term_structure", "cboe/options/smile", "cboe/options/surface", "cboe/options/stats"]),
    ], groups=[SYMBOL, REPORT], more=["equity/fundamental/*"]),

    D("eq-screen", "Screeners & Movers", "equities", "Find stocks: screeners, daily movers, sectors and industries, relative rotation, symbol lookup.", [
        T("Screener", rows=[[W("yfinance/screener_builder/view", 40, 30)], [W("equity/screener", 40, 20, provider="finviz")]]),
        T("Movers", rows=[[W("equity/discovery/gainers", 13, 18), W("equity/discovery/losers", 14, 18), W("equity/discovery/active", 13, 18)]],
          auto=["re:equity/discovery/(?!filings|latest).*", "nasdaq/markets/movers", "equity/market_snapshots"]),
        T("Sectors & Industries", rows=[[X("perf_bars", 13, 18, preset="us_sectors"), X("board", 14, 18, preset="us_sectors"), X("board", 13, 18, preset="us_industries")]],
          auto=["yfinance/sectors/*", "yfinance/industry/*"]),
        T("Themes", rows=[[X("perf_bars", 13, 22, preset="themes", period="1M"), X("board", 27, 22, preset="themes")],
                          [X("board", 14, 24, preset="ai_complex"), X("board", 13, 24, preset="datacenter_power"), X("board", 13, 24, preset="private_markets")],
                          [C("hyperscalers", "capex", 20, 18, period="ttm"), C("hyperscalers", "debt", 20, 18)],
                          [METRICS(HYPERSCALERS, "Valuation and margins: AI hyperscalers"), METRICS("NVDA,AVGO,AMD,MU,INTC,QCOM,TXN", "Valuation and margins: semiconductors")],
                          [X("board", 16, 18, preset="defense"), PERF("ITA,EUAD,XAR,SPY", "3Y", w=12, h=18, title="Normalized Performance: defense vs market"), M("defense_spending", 12, 18, since="2005")],
                          [X("board", 20, 18, preset="banks"), M("tech_investment", 20, 18)]]),
        # lookups and the institutional / insider searches: reference tools, all on one tab
        T("Lookup & Reference", auto=[W("equity/search", params={"query": "apple"}), W("yfinance/search", params={"query": "apple"}), W("nasdaq/markets/quotes", params={"symbol": "AAPL,MSFT,NVDA,AMZN,GOOGL"}),
                                       "equity/ownership/form_13f", "sec/institutions_search", "equity/ownership/government_trades", "regulators/sec/institutions_search",
                                       "nasdaq/markets/status", W("sec/sic_search", params={"query": "software"}), "sec/symbol_map", "sec/cik_map", "yfinance/private_companies", "finra/equity/securities",
                                       "re:regulators/sec/(symbol_map|cik_map|sic_search)", "sec/schema_files"]),
    ], more=["equity/discovery/*", "equity/*"]),

    D("eq-calendar", "Calendars & Events", "equities", "The economic calendar, earnings, dividends, splits, IPOs and newly filed reports.", [
        T("Economic", rows=[[W("economy/calendar", 40, 24, title="Economic calendar (upcoming events)", **ECON_CAL)]],
          auto=["federal_reserve/release_calendar", "usda/report_calendar"]),
        # the week ahead: earnings first (full width, by date), then dividends, splits and IPOs
        T("Corporate", rows=[[W("equity/calendar/earnings", 40, 20, provider="nasdaq", title="Earnings calendar (next 7 days)", sort="report_date:asc")]],
          auto=["equity/calendar/dividend", "equity/calendar/splits", "equity/calendar/ipo", "nasdaq/markets/upcoming", "equity/calendar/*",
                "equity/discovery/latest_financial_reports", "equity/discovery/filings"]),
    ]),

    D("eq-canada", "Canada (TMX)", "equities", "Toronto Stock Exchange: company pages, fundamentals, screener, movers, insiders, short interest and news.", [
        T("Company", auto=["tmx/equity/asset_info/view", "tmx/equity/fundamental/*", "tmx/equity/filings_view", "tmx/equity/ownership/insider_transactions"]),
        # Fund Info describes an ETF: pinned to XIU, not the dashboard's stock picker (review A3: it ran on BCE)
        T("Market", auto=["tmx/equity/screener_builder/view", "tmx/markets/*", "tmx/equity/rankings", W("tmx/etf/fund_info/view", params={"symbol": "XIU"}, groups=[]), "tmx/index/document_view"]),
        # ranked news on the TMX ticker (RY -> RY.TO: Canadian press, TMX releases), the issuer's own releases beside it, other TMX feeds in a library
        T("News", rows=[[NEWS(24, 24, subject="symbol", listing="TO", title="Company news"), W("tmx/news/feed", 16, 24, title="Issuer releases (TMX)")],
                        [LIB("More TMX news feeds", ["tmx/news/market", "tmx/news/search", "tmx/news/blog"], 40, 20, first="tmx/news/market")]]),
    ], groups=[dict(SYMBOL, value="RY")], more=["tmx/equity/*", "tmx/etf/*", "tmx/index/*", "tmx/markets/*", "tmx/news/*"]),

    D("eq-nordic", "Nordics (Nasdaq Nordic)", "equities", "Stockholm, Copenhagen, Helsinki and Iceland: screener, fundamentals, movers, bonds and news. Tickers look like VOLV B or NOVO B.", [
        # an index board first (Yahoo, keyless), then the exchange's own lists
        T("Market", rows=[[X("board", 16, 26, preset="nordics", groups=[]), PERF("^OMX,^OMXC25,^OMXH25,OBX.OL", "1Y", w=24, h=26, title="Normalized Performance: Nordic indices")]],
          auto=["nasdaq/nordic/movers", "nasdaq/nordic/screener", "nasdaq/nordic/news"]),
        T("Company", auto=["nasdaq/nordic/info", "nasdaq/nordic/fundamentals", "nasdaq/nordic/historical", "nasdaq/nordic/historical_trades", "nasdaq/nordic/dividends"]),
        T("Rates & Derivatives", auto=["nasdaq/nordic/bond_yields", "nasdaq/nordic/mortgage_rates", "nasdaq/nordic/index_factors", "nasdaq/nordic/derivatives_volume", "nasdaq/nordic/knocked_out"]),
        T("Reference", auto=["nasdaq/nordic/*"]),
    ], groups=[dict(SYMBOL, value="VOLV B")]),

    # ============================================================ INDICES / ETFs / FUNDS
    D("indices", "Indices", "funds", "Index levels, history, constituents, valuation multiples and volatility.", [
        T("World Indices", rows=[[X("board", 16, 26, preset="global_indices"), PERF("^GSPC,^STOXX50E,^FTSE,^N225,^HSI", "1Y", w=24, h=26, title="Normalized Performance: world indices")],
                                 [X("board", 20, 16, preset="us_equity"), X("perf_bars", 20, 16, preset="global_indices", period="YTD")]]),
        T("History & Constituents", rows=[[W("index/price/historical", 40, 20, chart=True, params={"symbol": "^GSPC"}, title="S&P 500 price history")]],
          auto=["index/price/historical", "index/snapshots", W("index/constituents", title="Index constituents (FTSE 100; Cboe Europe indices)"),
                W("index/sectors", title="Index sector weights (S&P/TSX)"), "index/available", "index/search", "cboe/index/documents_viewer"]),
        T("Valuation", rows=[[PERF("RSP,SPY,QQQ,IWM", "3Y", w=22, h=18, title="Normalized Performance: equal weight vs cap weight"), X("board", 18, 18, preset="megacaps")],
                             [X("sp500_multiples", 40, 18)]],
          auto=["economy/share_price_index", "economy/risk_premium"]),
        T("Volatility", rows=[[X("board", 16, 16, preset="volatility"), PERF("^VIX,^VVIX,^MOVE", "1Y", w=24, h=16, title="Normalized Performance: volatility indices")],
                              [W("derivatives/futures/curve", 20, 16, provider="cboe", params={"symbol": "VX"}, title="VIX futures curve", plot={"x": "expiration", "ys": ["price"], "split": "", "type": "line"}),
                               W("cboe/futures/settlement_prices", 20, 16)]]),
    ], more=["index/*"]),

    D("etfs", "ETFs & Mutual Funds", "funds", "ETF profiles, holdings, exposures and flows; mutual-fund analytics; fund regulatory filings.", [
        # holdings, sector and country weights take the source that covers the ETF (Nasdaq for SPY, TMX for XIU.TO)
        T("ETF Profile", rows=[[W("etf/historical", 24, 18, chart=True, title="ETF price chart"), W("etf/info", 16, 18)]],
          # sector and country weights are keyless for Canadian ETFs only (TMX): after the cards that cover the default SPY
          auto=["etf/holdings", "etf/price_performance", "etf/equity_exposure", "etf/historical", "etf/sectors", "etf/countries"]),
        T("ETF Search & Movers", auto=[W("etf/discovery/gainers", title="ETF movers (gainers, losers, most active)"), "etf/search", "etf/discovery/*"], groups=[]),
        T("Mutual Funds", auto=["yfinance/funds/*"], groups=["Fund"]),
        T("Fund Filings (N-PORT)", auto=["etf/nport_disclosure", "sec/nport_fund_metrics"]),
    ], groups=[dict(SYMBOL, value="SPY"), {"name": "Fund", "paramName": "symbol", "value": "VFIAX"}], more=["etf/*"]),

    # ============================================================ RATES & CREDIT
    D("rates", "Rates & Curves", "rates", "Government yield curves by country, policy rates, money markets, swaps and policy expectations.", [
        T("Global", rows=[[X("global_yields", 24, 22), X("yield_curves", 16, 22)],
                          [X("policy_rates", 14, 20), X("policy_rate_history", 13, 20), X("rates_by_country", 13, 20)]], groups=[]),
        T("By Country", rows=[[X("yield_curves", 24, 20, compare="1M,1Y", title="Yield curve: now and earlier"), X("yield_curve_table", 16, 20)],
                              [W("x:policy_rate_history", 20, 16, params={"start": "2005"}, compact=True, title="Policy rate history"), X("rate_history", 20, 16, duration="long", title="10-Year Yield History (monthly)")],
                              [X("rate_history", 20, 14, duration="short", title="3-Month Rate History (monthly)"),
                               X("cpi_history", 20, 14, start="2015", title="Consumer Prices (year over year, IMF)")]], groups=["Country"]),
        T("United States", rows=[[W("fixedincome/government/yield_curve", 22, 18, chart=True, provider="federal_reserve"), W("fixedincome/government/treasury_rates", 18, 18)],
                                 [M("treasury_yields", 14, 16), M("curve_spreads", 13, 16), M("real_yields", 13, 16)],
                                 [X("board", 20, 14, preset="us_rates"), W("fixedincome/government/tips_yields", 20, 14)]],
          # auctions live once, in Government Finance > Auctions
          auto=["fixedincome/government/treasury_prices", "fixedincome/government/svensson_yield_curve", "fixedincome/spreads/*"], groups=[]),
        T("Euro Area", rows=[[W("fixedincome/government/yield_curve", 22, 18, chart=True, provider="ecb"), W("ecb/key_interest_rates", 18, 18)],
                             [X("rate_history", 20, 16, country="germany,france,italy,spain", duration="long", start="2015", title="Euro-area 10-year yields (monthly, OECD)", groups=[]),
                              M("euro_spreads", 20, 16, since="2015")]],
          auto=["fixedincome/rate/estr", "fixedincome/rate/ecb", W("ecb/mfi_interest_rates", params={"symbol": "household_loans_for_house_purchase"}), "ecb/eligible_assets"], groups=[]),
        # SOFR and fed funds as cards; every other money-market and policy rate in one library card
        T("Money Markets", rows=[[M("policy_corridor", 20, 16, since="2022"), M("fed_liquidity", 20, 16)], [M("fed_balance_sheet", 13, 16), M("cb_balance_sheets", 14, 16, since="2019"), M("money_supply", 13, 16)],
                                 [W("fixedincome/rate/sofr", 13, 18), W("fixedincome/rate/effr", 13, 18),
                                  LIB("Money-market and policy rates", ["fixedincome/rate/*"], 14, 18, first="fixedincome/rate/iorb")]],
          auto=["fixedincome/corporate/commercial_paper", W("federal_reserve/money_market_funds", title="Money market fund holdings (USD)")], groups=[]),
        # review G2: the curves open as curves (rate in percent by tenor in years), not as dense tables
        T("Swaps & OIS", auto=[W("cftc/ois_curve", plot={"x": "tenor_years", "ys": ["rate"], "split": "", "type": "line"}),
                               W("cftc/ois_forward_curve", plot={"x": "start_years", "ys": ["forward_rate"], "split": "", "type": "line"}), "cftc/ois_curve_history", W("cftc/ois_policy_path", sort="start_date:asc"), "cftc/swap_summary", "cftc/swap_valuation", "cftc/swap_trades", "cftc/historical_fixings"], groups=[]),
        # WIRP: the OIS-implied policy path first (keyless, CFTC swap reports), then market inflation and curve shape
        # HANDOFF P3-3: six central banks, today vs 1W / 1M ago, BIS policy rate as anchor (terminal/policy_path.py)
        # owner 2026-11-19: the FOMC documents (statements, minutes, projections, newest first) beside the market-implied path
        T("Policy Expectations", rows=[[X("policy_path", 24, 18), X("policy_path_table", 16, 18)],
                                        [W("federal_reserve/fomc_documents_download", 40, 26, title="FOMC documents: statements, minutes, projections (newest first)")],
                                        [W("federal_reserve/sf/term_premium", 20, 16, params={"start_date": "2015-01-01"}, title="10-year yield, expected short rate and term premium (SF Fed model, %)",
                                           plot={"x": "date", "ys": ["yield_zero_coupon", "expected_short_rate", "term_premium"], "split": "", "type": "line"}),
                                         W("federal_reserve/sf/proxy_funds_rate", 20, 16, params={"start_date": "2015-01-01"}, title="Proxy funds rate vs effective fed funds (SF Fed, %)",
                                           plot={"x": "date", "ys": ["proxy_funds_rate", "effective_funds_rate"], "split": "", "type": "line"})]],
          auto=["federal_reserve/atlanta/market_probability", "federal_reserve/atlanta/taylor_rule",
                                        "federal_reserve/sf/short_rate_path", "federal_reserve/kc/natural_rate", "federal_reserve/kc/policy_rate_uncertainty",
                                        "fixedincome/rate/effr_forecast"], groups=[]),
    ], groups=[COUNTRY], more=["fixedincome/government/*", "fixedincome/rate/*"]),

    D("credit", "Credit", "rates", "Credit spreads by rating and region, sovereign spreads, private credit and loans, corporate debt, bank lending, stress, CDS index trades and bond lookup.", [
        # Spreads first: credit ETFs (keyless) on top, then ICE BofA option-adjusted spreads (FRED) by rating, region and euro sovereigns
        T("Spreads", rows=[[X("board", 14, 18, preset="fixed_income"), PERF("LQD,HYG,EMB,BKLN,TLT", "1Y", w=26, h=18, title="Normalized Performance: credit vs Treasuries")],
                           [M("credit_spreads", 14, 16, since="2019"), M("ig_by_rating", 13, 16, since="2023"), M("hy_by_rating", 13, 16, since="2023")],
                           [X("cds_indices", 14, 16), X("cds_history", 13, 16), M("credit_regions", 13, 16, since="2023")]],
          auto=["fixedincome/bond_indices", W("fixedincome/corporate/hqm", plot={"x": "maturity", "ys": ["rate"], "split": "", "type": "line"}), "fixedincome/corporate/spot_rates", "fixedincome/mortgage_indices", "cftc/cds_index_trades"]),
        T("Private Credit & Loans", rows=[[X("board", 15, 26, preset="private_markets"), PERF("BIZD,BKLN,JAAA,HYG,PSP", "3Y", w=25, h=26, title="Normalized Performance: BDCs, loans, CLOs, high yield, listed PE")],
                                          [M("bank_lending", 14, 18), C("alt_managers", "revenue", 13, 18, period="annual"), METRICS("ARCC,OBDC,BXSL,FSK,MAIN,GBDC", "Valuation and yield: BDCs", 13, 18)],
                                          [METRICS("BX,KKR,APO,ARES,OWL,CG", "Valuation and margins: alternative managers", 20, 16), M("distressed_spreads", 20, 16)]]),
        T("Corporate Debt", rows=[[M("corporate_yields", 14, 18), M("corporate_debt", 13, 18, since="2000"), C("hyperscalers", "debt", 13, 18)],
                                  [C("megacaps", "interest_expense", 14, 16, period="annual"), C("megacaps", "buybacks", 13, 16, period="ttm"), M("corporate_profits", 13, 16)]]),
        # bank lending and financial stress on one tab: charts first, the source tables in two library cards
        T("Lending & Stress", rows=[[M("bank_lending", 14, 18), M("lending_standards", 13, 18), M("delinquencies", 13, 18)],
                                    [M("financial_conditions", 20, 16), W("federal_reserve/ny/corporate_bond_distress", 20, 16, params={"start_date": "2000-01-01"}, title="Corporate bond distress index (NY Fed; 0-1, percentile of history)",
                                                                         plot={"x": "date", "ys": ["market", "investment_grade", "high_yield"], "split": "", "type": "line"})],
                                    [LIB("Bank lending surveys and conditions", ["economy/survey/sloos", "federal_reserve/ny/consumer_credit_access", "federal_reserve/dallas/banking_conditions",
                                                                                  "ecb/mfi_interest_rates"], 20, 22),
                                     LIB("Financial stress indices (regional Feds)", ["federal_reserve/kc/financial_stress_index", "federal_reserve/chicago/financial_conditions",
                                                                                       "federal_reserve/cleveland/systemic_risk", "federal_reserve/ny/corporate_bond_distress",
                                                                                       "federal_reserve/kc/risk_index", "federal_reserve/stl/national_index"], 20, 22)]]),
        # HANDOFF P3-1: company -> FINRA issuer name, filtered and typed (terminal/bonds.py); the raw TRACE card stays last
        T("Bond Lookup (TRACE)", rows=[[X("bond_lookup", 24, 18), X("bond_curve", 16, 18)]], cols=1, h=18, auto=[
            W("finra/fixedincome/bond_historical", plot={"x": "date", "ys": ["price"], "split": "", "type": "line"}, sort="date:desc"),
            W("fixedincome/corporate/bond_prices", provider="finra", only=["finra"], params={"issuer_name": "APPLE INC", "limit": 1000}, title="TRACE bond prices (raw, by issuer name)"),
            W("finra/fixedincome/bonds", manual="The full FINRA bond list is about 150 MB (300,000 bonds) and cannot be filtered. Use the issuer filter in the card above, or load everything:")]),
    ], more=["fixedincome/*", "finra/fixedincome/*"]),

    D("govfin", "Government Finance", "rates", "U.S. public debt, the Treasury's daily cash position, auctions, Fed balance sheet and primary dealers.", [
        # Treasury's own daily data (keyless) first, then the FRED history charts
        T("Debt & Cash", rows=[[W("ustreasury/debt_to_penny", 20, 14), W("ustreasury/daily_statement", 20, 14, title="Treasury cash (TGA closing balance, Daily Treasury Statement)",
                                                                                     plot={"x": "date", "ys": ["Treasury General Account (TGA) Closing Balance (Open Today)"], "split": "", "type": "line"})],
                               [M("federal_debt", 13, 16, since="1990"), M("federal_deficit", 14, 16, since="1990"), M("federal_interest_tariffs", 13, 16, since="2000")],
                               [M("defense_spending", 20, 16, since="2000"), M("trade_uncertainty", 20, 16, since="2015")]],
          auto=["ustreasury/bulletin"]),
        T("Auctions", auto=[W("ustreasury/auction_results", sort="auction_date:desc"), "ustreasury/upcoming_auctions",
                            "ustreasury/weekly_bill_offerings", W("fixedincome/government/treasury_auctions", sort="auction_date:desc")]),
        # review H1: dealer fails open as the two totals over time (fails to deliver / to receive; the table is a click away)
        T("Fed Balance Sheet & Dealers", auto=["economy/central_bank_holdings", "economy/primary_dealer_positioning",
                                                W("economy/primary_dealer_fails", plot={"x": "date", "ys": ["FTD Total", "FTR Total"], "split": "", "type": "line"}),
                                                # review H1: the newest month, largest holders first
                                                W("federal_reserve/international_portfolio_investment",
                                                  sort="date:desc,Foreign residents' holdings of total U.S. long-term securities:desc")]),
    ]),

    # ============================================================ DERIVATIVES
    D("options", "Equity Options", "derivatives", "Option chains, volatility surfaces, smiles and term structure, and strategy pricing.", [
        # the chain with open interest and the term structure first; smile and surface on Volatility
        T("Chain", rows=[[W("derivatives/options/chains", 40, 22, provider="yfinance", groups=["Symbol"])], [W("cboe/options/stats", 20, 16), W("cboe/options/term_structure", 20, 16)]]),
        T("Volatility", auto=["cboe/options/smile", "cboe/options/surface"]),
        # one card per analysis; its source follows the ticker (Cboe for U.S. listings, TMX for .TO, Deribit for BTC/ETH).
        # The market-wide Intrinio scans need a paid key: last on the tab.
        T("Strategies", auto=["cboe/options/straddle", "cboe/options/strangle", "cboe/options/spreads", "cboe/options/payoff", "cboe/options/trade_optimizer",
                              "tmx/derivatives/options/covered_calls", W("derivatives/options/snapshots", groups=[]), W("derivatives/options/unusual", groups=[])]),
    ], groups=[dict(SYMBOL, value="AAPL")], more=["cboe/options/*", "derivatives/options/*"]),

    # Swaps (SDR) and FX derivatives live in Rates & Curves and Currencies; CDS index trades in Credit
    D("futures", "Futures & Positioning", "derivatives", "Front-month futures, curves and history, contract reference, and Commitments of Traders positioning.", [
        T("Futures", rows=[[X("board", 16, 24, preset="commodities"), CURVE("CL", "Crude oil futures curve (WTI)", 24, 24)],
                           [W("derivatives/futures/historical", 20, 16, provider="yfinance", params={"symbol": "ES"}, title="Futures price history",
                             tparam={"param": "symbol", "labels": {"ES": "E-mini S&P 500", "NQ": "E-mini Nasdaq-100", "CL": "WTI crude", "GC": "Gold", "ZN": "10-year T-note"}}),
                            CURVE("GC", "Gold futures curve", 20, 16)]],
          auto=["cboe/futures/settlement_prices", "derivatives/futures/instruments"]),
        # the positioning index (spec score, zones) first; the raw report last
        T("Positioning (COT)", rows=[[W("cftc/cot_index", 24, 18, title="COT positioning index (all markets)"), W("cftc/cot_movers", 16, 18)],
                                     [W("cftc/cot_search", 14, 16, params={"query": "crude"}), W("cftc/cot", 26, 16, params={"code": "067651"}, title="Commitments of Traders report",
                                                                                                                     tparam={"param": "code", "labels": {"067651": "067651 · WTI crude"}})]],
          auto=[W("cftc/cot_positioning", plot={"x": "report_date", "ys": ["large_specs_net_pct_oi", "commercials_net_pct_oi", "small_specs_net_pct_oi"], "split": "", "type": "line"})]),
    ], more=["derivatives/futures/*", "cftc/*"]),

    # ============================================================ FX
    D("fx", "Currencies", "fx", "Spot boards, cross-rate matrix, policy-rate differentials, history, real effective rates, reserves, forwards, implied volatility and positioning.", [
        T("Board", columns=[
            (16, [X("board", h=26, preset="fx"), X("perf_bars", h=18, preset="fx", period="1M")]),
            (24, [X("fx_matrix", h=12), PERF("DX-Y.NYB,EURUSD=X,GBPUSD=X,JPY=X", "1Y", h=16, title="Normalized Performance: majors"),
                  X("policy_rates", h=16, title="Policy rates and differential vs the Fed")]),
        ], rows=[[NEWS(40, 16, subject="eurusd", title="Currency news", groups=[])]]),
        T("History & Reference", rows=[[W("currency/price/historical", 26, 20, chart=True, params={"symbol": "EURUSD"}), M("dollar", 14, 20, since="2006")],
                                       [M("reer", 20, 16, since="2010"), M("fx_reserves", 20, 16, since="2010")]],
          auto=["currency/reference_rates", "currency/snapshots", "currency/search"]),
        T("Forwards, Vol & Positioning", rows=[[W("cftc/cot_index", 40, 16, params={"asset_class": "currencies"}, title="Positioning: currencies (COT positioning index)")]],
          auto=["cftc/fx_forward_curve", "cftc/fx_forward_points", "cftc/fx_implied_vol", "cftc/fx_forward_trades", "cftc/fx_option_trades"]),
    ], more=["currency/*"]),

    # ============================================================ COMMODITIES
    D("commodities", "Commodities", "commodities", "Futures boards, spot prices, curves, positioning and official outlooks.", [
        T("Board", rows=[[X("board", 16, 26, preset="commodities"), PERF("CL=F,BZ=F,NG=F,GC=F,HG=F", "1Y", w=24, h=26, title="Normalized Performance: key commodities")],
                         [X("perf_bars", 20, 20, preset="commodities", period="1M"), NEWS(20, 20, subject="gold", title="Commodity news", groups=[])]]),   # YTD bars: the 1M card's period chips
        T("Prices & Curves", rows=[[CURVE("CL", "Crude oil futures curve (WTI)"), CURVE("NG", "Natural gas futures curve (Henry Hub)")],
                                   [CURVE("GC", "Gold futures curve"), CURVE("HG", "Copper futures curve")]],
          auto=[M("oil_prices", since="2010"), M("gas_europe_us", since="2021"), M("uranium", since="2022"), "commodity/price/spot"]),
        T("Positioning", rows=[[W("cftc/cot_index", 24, 16, params={"asset_class": "hard_commodities"}, title="COT positioning index: energy and metals"),
                                 W("cftc/cot_movers", 16, 16, params={"asset_class": "hard_commodities"}, title="COT largest changes: energy and metals")],   # review F2/H3: was "all"
                               [W("cftc/cot_search", 14, 16, params={"query": "gold"}), W("cftc/cot", 26, 16, params={"code": "088691"}, title="Commitments of Traders report (gold)")]]),
    ], more=["commodity/*"]),

    D("energy", "Energy", "commodities", "Oil, gas, power and uranium: prices, inventories against their seasonal range, refining margins, global supply, shipping chokepoints, geopolitical risk and outlooks.", [
        T("Overview", columns=[
            (14, [X("board", h=30, preset="energy"), M("oil_volatility", h=16, since="2021")]),
            (13, [M("oil_prices", h=15, since="2020"), M("crude_stocks_seasonal", h=15), M("crack_spreads", h=16, since="2022")]),
            (13, [M("gas_europe_us", h=15, since="2021"), M("natgas_storage_seasonal", h=15), M("chokepoint_tankers", h=16, since="2023")]),
        ], rows=[[CURVE("CL", "Crude oil futures curve (WTI)", 20, 16),
                  NEWS(20, 16, subject="crude", title="Oil news", groups=[])]]),        # the gas curve stays on Natural Gas & LNG
        T("Oil", rows=[[M("oil_prices", 20, 16, since="2015"), M("brent_wti", 20, 16, since="2020")],
                       [M("crack_spreads", 14, 16, since="2021"), M("pump_prices", 13, 16, since="2020"), M("refinery_utilization", 13, 16)],
                       [M("crude_stocks", 14, 16, since="2015"), M("cushing_seasonal", 13, 16), M("us_oil_flows", 13, 16, since="2018")],
                       [M("gasoline_stocks_seasonal", 14, 16), M("distillate_stocks_seasonal", 13, 16), M("oil_demand", 13, 16, since="2018")],
                       [CURVE("CL", "Crude oil futures curve (WTI)", 20, 16),
                        W("cftc/cot", 20, 16, params={"code": "067651"}, title="Positioning: WTI crude (Commitments of Traders)")],
                       [W("commodity/petroleum_status_report", 40, 18, title="EIA Weekly Petroleum Status Report")]]),
        T("Natural Gas & LNG", rows=[[M("gas_europe_us", 20, 16, since="2019"), M("natgas_storage_seasonal", 20, 16)],
                                     [M("lng_exports", 14, 16, since="2016"), M("gas_supply", 13, 16, since="2016"), M("natgas_price", 13, 16, since="2015")],
                                     [CURVE("NG", "Natural gas futures curve (Henry Hub)", 20, 16),
                                      W("cftc/cot", 20, 16, params={"code": "023651"}, title="Positioning: natural gas (Commitments of Traders)")]]),
        T("Power & Uranium", rows=[[X("board", 14, 22, preset="datacenter_power"), M("electricity_prices", 13, 22, since="2015"), M("power_output", 13, 22, since="2015")],
                                   [M("construction", 20, 16, since="2015"), M("uranium", 20, 16, since="2022")],
                                   # review H3: the lower 48 as a whole, plotted; fuels by name (type_name), not EIA codes (BAT, COL)
                                   [W("eia/electricity_grid/demand", 20, 16, title="U.S. lower-48 demand, forecast and generation (hourly, MWh)", params={"respondent": "united_states_lower_48"},
                                      plot={"x": "date", "ys": ["value"], "split": "series_type_name", "type": "line"}),
                                    W("eia/electricity_grid/generation_by_fuel", 20, 16, title="U.S. lower-48 generation by fuel (hourly, MWh)", params={"respondent": "united_states_lower_48"},
                                      plot={"x": "date", "ys": ["value"], "split": "type_name", "type": "line"})],
                                   [W("eia/nuclear_outages/us_nuclear_outages", 20, 14, title="U.S. nuclear outages")]]),
        T("Global Supply & Geopolitics", rows=[[X("chokepoint_monitor", 24, 18), M("chokepoint_tankers", 16, 18, since="2023")],
                                               [M("oil_producers", 20, 16, since="2019"), M("world_oil_balance", 20, 16, since="2019")],
                                               [M("red_sea_diversion", 20, 16, since="2023"), M("gpr_daily", 20, 16, since="2019")],
                                               [X("board", 14, 18, preset="shipping"), W("imf/portwatch/disruption_events", 26, 18, title="Current shipping and port disruptions")],
                                               [W("jodi/oil/production", 20, 16, title="Crude production by country (JODI)"), W("jodi/oil/exports", 20, 16, title="Crude exports by country (JODI)")]]),
        T("Outlooks & Reports", rows=[[M("oil_price_forecast", 14, 16, since="2022"), M("oecd_oil_stocks", 13, 16, since="2019"), M("gas_price_forecast", 13, 16, since="2022")]],
          auto=["commodity/short_term_energy_outlook", "eia/aeo", "eia/ieo", "eia/rss_feed", "federal_reserve/dallas/energy_survey", "federal_reserve/dallas/breakeven_prices",
                "re:federal_reserve/kc/.*energy.*"]),
        T("Data Library", rows=[[LIB("Petroleum (EIA)", ["eia/petroleum/*"], 20, 26, first="eia/petroleum/prices/weekly_retail_gasoline_and_diesel_prices"),
                                 LIB("Natural gas (EIA)", ["eia/natural_gas/*"], 20, 26, first="eia/natural_gas/storage/weekly_working_gas_in_underground_storage")],
                                [LIB("Electricity, grid and nuclear (EIA)", ["eia/electricity_grid/*", "eia/electricity/*", "eia/nuclear_outages/*", "eia/state_electricity_profiles/*"], 20, 26),
                                 LIB("Coal and biomass (EIA)", ["eia/coal/*", "eia/densified_biomass/*"], 20, 26)],
                                [LIB("World oil and gas statistics (JODI)", ["jodi/oil/*", "jodi/gas/*"], 20, 26),
                                 LIB("EIA overview, international and data browsers", ["eia/total_energy", "eia/international", "eia/seds", "eia/crude_oil_imports", "eia/*@Data Browsers", "eia/*@Reference"], 20, 26)]]),
    ], more=["eia/*", "jodi/*"]),
    D("agri", "Agriculture & Food", "commodities", "Grain, softs and livestock futures, positioning, world supply and demand (WASDE/PSD), and USDA and Federal Reserve farm data in library cards.", [
        T("Markets", rows=[[X("board", 16, 30, preset="agriculture"), PERF("ZC=F,ZW=F,ZS=F,KC=F,CC=F,LE=F", "1Y", w=24, h=30, title="Normalized Performance: corn, wheat, soybeans, coffee, cocoa, cattle")],
                           [CURVE("ZS", "Soybean futures curve", 20, 16),
                            CURVE("KC", "Coffee futures curve", 20, 16)],
                           [W("cftc/cot_index", 24, 16, params={"asset_class": "soft_commodities"}, title="COT positioning index: agriculture and softs"),
                            W("cftc/cot", 16, 16, params={"code": "002602"}, title="Commitments of Traders report (corn)")]]),
        T("Supply & Demand", auto=["commodity/psd_data", "commodity/psd_report", "usda/bell_report", "usda/report_calendar", "usda/publication_documents", "usda/ers_chart", "commodity/weather_bulletins_download"]),
        # USDA ERS publishes ~60 similar datasets: one library card per topic instead of tabs of cards
        T("USDA Data Library", rows=[
            [LIB("Crops, oilseeds, sugar, bioenergy and fertilizer", ["re:usda/(wheat_data|rice_yearbook|feed_grains_database|oil_crops_yearbook|cotton_wool_and_textile_data|sugar_sweeteners_yearbook|fruit_and_tree_nuts_data|vegetables_and_pulses|season_average_price_forecasts|adoption_of_genetically_engineered_crops|us_bioenergy_statistics|fertilizer_use_and_price)"], 20, 26, first="usda/feed_grains_database"),
             LIB("Livestock, meat and dairy", ["re:usda/(livestock.*|dairy_data|milk_cost_of_production|meat_price_spreads)"], 20, 26)],
            [LIB("Agricultural trade and world markets", ["re:usda/(agricultural_trade.*|state_agricultural_trade|us_food_imports|international_.*|global_food_assessment|agricultural_exchange_rates)"], 20, 26),
             LIB("Food prices, spending and consumption", ["re:usda/(food_.*|price_spreads|fruit_and_vegetable_prices|purchase_to_plate|normalized_prices|cost_estimates_of_foodborne_illnesses|eating_and_health_module_atus|resource_requirements_of_food_demand|snap_policy_data_sets)"], 20, 26, first="usda/food_price_outlook")],
            [LIB("Farm income, costs, productivity and land", ["re:usda/(farm_.*|commodity_costs_and_returns|agricultural_productivity|agricultural_and_food_rd_expenditures|major_land_uses)"], 20, 26, first="usda/farm_income_and_wealth_statistics"),
             LIB("Rural economy and other USDA data", ["rest:usda/*"], 20, 26, first="usda/rural_urban_continuum_codes")],
            [LIB("Farm credit and farmland surveys (regional Feds)", ["re:federal_reserve/(kc/ag_.*|chicago/(ag_credit_conditions|farm_loan_rates|farmland_values)|dallas/agricultural_survey|minneapolis/.*ag.*)"], 40, 26)]]),
    ]),

    # ============================================================ CRYPTO
    D("crypto", "Crypto", "crypto", "Coin boards and history, plus Deribit order books, options, funding and volatility.", [
        # coins, then funding and implied volatility (what a crypto desk reads next to price)
        T("Board", rows=[[X("board", 16, 20, preset="crypto"), PERF("BTC-USD,ETH-USD,SOL-USD,XRP-USD", "1Y", w=24, h=20, title="Normalized Performance: major coins")],
                         [W("crypto/price/historical", 24, 20, chart=True, params={"symbol": "BTCUSD"}, title="Bitcoin price chart"), NEWS(16, 20, subject="btc", title="Crypto news", groups=[])]],
          auto=[X("crypto_vol", 20, 18), "deribit/volatility/*", "deribit/rates/*"]),     # review H4: DVOL against realised
        T("Market (Deribit)", auto=["deribit/market/ticker", "deribit/market/book_summary", "deribit/market/order_book", "deribit/market/trades", "deribit/market/*"]),
        T("Options (Deribit)", auto=["deribit/options/surface", "deribit/options/smile", "deribit/options/term_structure", "deribit/options/stats", "deribit/options/*"]),
        T("Indices & Reference", auto=["deribit/index/*", W("derivatives/futures/info", params={"symbol": "BTC-PERPETUAL"}, title="Perpetual contract details (BTC)"),
                                       W("crypto/search", params={"query": "bitcoin"}), "deribit/reference/*"]),
    ], more=["crypto/*", "deribit/*"]),

    # ============================================================ MACRO
    D("macro-country", "Economy by Country", "macro", "Growth, prices, jobs, rates and external accounts for any country, side by side.", [
        # ECST-style: every main economy on one screen first (keyless, one OECD request per 12 hours), then the picked countries
        T("Country Monitor", rows=[[X("country_monitor", 40, 16, groups=[])],
                                   [X("cpi_history", 20, 16, start="2018", title="Consumer prices, y/y % (IMF)"), W("x:policy_rate_history", 20, 16, params={"start": "2018"}, compact=True, title="Policy rate history")],
                                   [X("yield_curves", 20, 16, compare="1Y", title="Government yield curve: now and a year ago"), X("rate_history", 20, 16, duration="long", start="2010", title="10-year yields (monthly, OECD)")],
                                   [W("economy/indicators", 24, 16, provider="imf", params={"symbol": "WEO::NGDP_RPCH"}, title="Real GDP growth with IMF forecasts (World Economic Outlook, annual)"),
                                    NEWS(16, 16, subject="country", title="Country news")]]),
        T("Snapshot", auto=["economy/gdp/real", "economy/gdp/nominal", "economy/gdp/forecast", "economy/cpi", "economy/unemployment", "economy/interest_rates",
                            "economy/composite_leading_indicator", "economy/house_price_index", "economy/share_price_index", "economy/country_profile"]),
        T("External Accounts", auto=[W("economy/balance_of_payments", provider="ecb", groups=[], title="Euro-area balance of payments (ECB)"), "economy/direction_of_trade",
                                     W("economy/export_destinations", sort="value:desc"), W("federal_reserve/international_portfolio_investment", groups=[])]),
        # rates by country live in Rates & Curves > Global and By Country
        T("Geopolitical Risk", rows=[[X("gpr_table", 16, 22), M("gpr_countries", 24, 22, since="2020")], [M("gpr_daily", 20, 16, since="2015"), M("policy_uncertainty", 20, 16, since="2015")]], groups=[]),
        T("IMF & World Data", rows=[[W("economy/indicators", 40, 18, provider="imf", params={"symbol": "WEO::PCPIPCH"}, groups=["Country"],
                                       title="IMF World Economic Outlook: inflation with forecasts (any IMF symbol in settings)",
                                       plot={"x": "date", "ys": ["value"], "split": "country", "type": "line"})]],
          auto=[W("economy/available_indicators", provider="imf", params={"query": "inflation"}),
                "usda/international_macroeconomic_data_set", "re:federal_reserve/dallas/global_.*"], groups=[]),
    ], groups=[MACRO_COUNTRY]),

    D("macro-us", "U.S. Economy", "macro", "Growth nowcasts, inflation, jobs, productivity, consumers and housing, business surveys and every regional Federal Reserve bank.", [
        T("Overview", rows=[[M("gdpnow", 13, 16, since="2019"), M("inflation", 14, 16, since="2019"), M("unemployment", 13, 16, since="2015")],
                            [M("jobless_claims", 13, 16, since="2021"), M("retail_sales", 14, 16, since="2019"), M("consumer_sentiment", 13, 16, since="2015")],
                            [M("industry", 13, 16, since="2015"), M("corporate_profits", 14, 16, since="2015"), M("policy_uncertainty", 13, 16, since="2019")]]),
        T("Growth & Activity", rows=[[W("federal_reserve/philadelphia/gdpplus", 20, 16), W("federal_reserve/chicago/national_activity", 20, 16)],
                                     [M("tech_investment", 20, 16), W("federal_reserve/ny/supply_chain_pressure", 20, 16, params={"start_date": "2015-01-01"}, title="Global supply chain pressure index (NY Fed; standard deviations from average)",
                                                                          plot={"x": "date", "ys": ["gscpi"], "split": "", "type": "line"})],
                                     [LIB("Nowcasts, activity and recession indicators, forecaster surveys", [
                                         "federal_reserve/atlanta/gdpnow", "re:federal_reserve/ny/.*(nowcast|weekly_economic).*", "federal_reserve/richmond/recession_indicator",
                                         "federal_reserve/chicago/brave_butters_kelley", "federal_reserve/ny/supply_chain_pressure", "federal_reserve/sf/news_sentiment",
                                         "re:federal_reserve/philadelphia/(anxious_index|survey_professional_forecasters|livingston_survey|partisan_conflict)",
                                         "federal_reserve/atlanta/business_uncertainty", "federal_reserve/stl/fred_md", "federal_reserve/stl/fred_qd", "economy/total_factor_productivity"], 40, 24)]]),
        T("Inflation", rows=[[M("inflation", 20, 16, since="2015"), M("breakevens", 20, 16, since="2015")],
                             [LIB("CPI release tables (BLS)", ["bls/cpi/*", "economy/retail_prices"], 20, 24, first="bls/cpi/t3_special_aggregates"), LIB("PPI and import/export prices (BLS)", ["bls/ppi/*", "bls/import_export/*"], 20, 24)],
                             [W("federal_reserve/cleveland/inflation_nowcast", 20, 22, params={"frequency": "year"}, sort="date:desc", title="Inflation nowcast, year over year % (Cleveland Fed)",
                                cols=["date", "CPI Inflation", "Core CPI Inflation", "PCE Inflation", "Core PCE Inflation"]),
                              LIB("Underlying inflation and expectations (regional Feds, surveys)", ["federal_reserve/dallas/trimmed_mean_pce", "economy/pce",
                                 "re:federal_reserve/(cleveland/(inflation_.*|median_cpi.*)|atlanta/(sticky_cpi|business_inflation_expectations)|sf/(cyclical_pce|supply_demand_pce)|ny/core_trend_inflation|minneapolis/regional_cpi)",
                                 "economy/survey/inflation_expectations", "economy/survey/university_of_michigan"], 20, 22, first="federal_reserve/dallas/trimmed_mean_pce")]]),
        T("Jobs", rows=[[M("unemployment", 20, 16, since="2015"), M("jobless_claims", 20, 16, since="2021")], [M("continuing_claims", 20, 16, since="2021"), M("job_openings", 20, 16, since="2015")],
                        [LIB("Employment Situation tables (BLS)", ["bls/employment_situation/*", "economy/survey/nonfarm_payrolls", "bls/real_earnings/*"], 20, 24, first="bls/employment_situation/summary_establishment"), LIB("Job openings and turnover (JOLTS)", ["bls/jolts/*"], 20, 24, first="bls/jolts/beveridge_curve")],
                        [LIB("Labour-market indicators (regional Feds)", ["federal_reserve/atlanta/wage_growth",
                             "re:federal_reserve/(minneapolis/(job_openings_hiring|labor_force_participation|quits_rate|regional_employment|regional_unemployment)|kc/.*lmci.*|chicago/labor_market|richmond/non_employment_index|ny/consumer_labor_market)"], 20, 24),
                         LIB("Productivity and costs (BLS)", ["bls/productivity/*"], 20, 24)]]),
        T("Consumers & Housing", rows=[[M("consumer_health", 13, 16), M("consumer_sentiment", 14, 16), M("retail_sales", 13, 16)],
                                       [M("mortgage_rates", 13, 16), M("housing_activity", 14, 16), M("home_prices", 13, 16)],
                                       [M("cre_prices", 20, 16), M("delinquencies", 20, 16)],
                                       [LIB("Consumer, household and housing surveys (Fed banks)", ["re:federal_reserve/ny/(consumer_.*|household.*|sce.*)", "re:federal_reserve/.*(housing|home|retail).*"], 40, 24)]]),
        T("Business Surveys", rows=[[LIB("Manufacturing surveys (regional Feds)", ["re:federal_reserve/.*manufactur.*"], 20, 24),
                                     LIB("Services, CFO and other business surveys", ["re:federal_reserve/.*(service|business|cfo|survey|outlook|economic_conditions|economic_indicators).*"], 20, 24)]],
          auto=["economy/survey/*"]),
        # gauges whose regional-Fed pages moved or changed layout (parked in Data status), from their FRED copies
        T("Regional Feds", rows=[[M("sticky_cpi", 10, 16, since="2019"), M("wage_tracker", 10, 16, since="2015"), M("texas_manufacturing", 10, 16, since="2019"),
                                  M("global_activity", 10, 16, since="2015")],
                                 [LIB("New York Fed", ["federal_reserve/ny/*"], 20, 22, first="federal_reserve/ny/empire_state_manufacturing"), LIB("Atlanta Fed", ["federal_reserve/atlanta/*"], 20, 22)],
                                 [LIB("Chicago Fed", ["federal_reserve/chicago/*"], 20, 22, first="federal_reserve/chicago/national_activity"),
                                  LIB("Philadelphia Fed", ["federal_reserve/philadelphia/*"], 20, 22, first="federal_reserve/philadelphia/manufacturing_outlook")],
                                 [LIB("Dallas Fed", ["federal_reserve/dallas/*"], 20, 22, first="federal_reserve/dallas/weekly_economic_index"),
                                  LIB("Kansas City Fed", ["federal_reserve/kc/*"], 20, 22, first="federal_reserve/kc/financial_stress_index")],
                                 [LIB("Cleveland Fed", ["federal_reserve/cleveland/*"], 20, 22), LIB("Richmond Fed", ["federal_reserve/richmond/*"], 20, 22, first="federal_reserve/richmond/manufacturing_survey")],
                                 [LIB("San Francisco Fed", ["federal_reserve/sf/*"], 20, 22),
                                  LIB("Minneapolis, St. Louis and Boston Feds", ["federal_reserve/minneapolis/*", "federal_reserve/stl/*", "federal_reserve/boston/*"], 20, 22)]]),
    ], more=["bls/*"]),
    # owner 2026-11-19: built around what bank supervisors and credit analysts read; the Bank picker drives the Bank Lookup tab only
    D("banking", "Money & Banking", "macro", "Bank credit quality, loans and deposits, securitization, one bank against its peers, what bank supervisors publish, money and central-bank balance sheets.", [
        T("Credit Quality", rows=[[M("delinquencies", 14, 16, since="2007"), M("charge_offs", 13, 16, since="2007"), M("problem_loans", 13, 16, since="2007")],
                                  [W("federal_reserve/ny/household_debt", 20, 16, params={"table": "flow_into_serious_delinquency_by_loan_type"},
                                     title="Households: % of balances moving into 90+ day delinquency, by loan type (NY Fed, annualized)",
                                     plot={"x": "date", "ys": ["CC", "AUTO", "STUDENT LOAN", "MORTGAGE", "HELOC", "ALL"], "split": "", "type": "line"}),
                                   X("bank_failures", 20, 16)]], groups=[]),
        T("Loans & Deposits", rows=[[M("bank_lending", 14, 16, since="2015"), M("bank_funding", 13, 16, since="2015"), M("lending_standards", 13, 16, since="2007")],
                                    [M("securitization", 20, 16, since="2000"), M("cre_prices", 20, 16, since="2007")]], groups=[]),
        # one bank: its own credit quality history (FDIC), its ratios against its peer group (UBPR), the FFIEC executive summary
        T("Bank Lookup", rows=[[X("bank_quality", 20, 18), W("federal_reserve/ffiec/ubpr", 20, 18, title="Ratios against the peer group (UBPR: bank, peer group, percentile)")],
                               [W("federal_reserve/ffiec/executive_summary", 24, 20, title="Executive summary (FFIEC call report, last eight quarters)"),
                                W("federal_reserve/ffiec/large_holding_companies", 16, 20, groups=[], title="Largest U.S. bank holding companies")]], groups=["Bank"]),
        # what supervisors publish (newest first), the Fed release calendar, then the regional Fed and FFIEC report libraries
        T("Regulators & Reports", rows=[[NEWS(24, 24, subject="market", mode="monitor", feeds="banks", title="Bank supervisors: releases, rules and enforcement", groups=[]),
                                         W("federal_reserve/release_calendar", 16, 24)],
                                        [LIB("Regional Fed publications", ["federal_reserve/*/regional_publications_download"], 20, 26),
                                         LIB("Bank and holding-company reports (FFIEC)", ["rest:federal_reserve/ffiec/*"], 20, 26, groups=["RSSD"])]], groups=[]),
        T("Money", rows=[[M("fed_balance_sheet", 20, 16), M("fed_liquidity", 20, 16)], [M("money_supply", 13, 16), M("policy_corridor", 14, 16, since="2022"), M("cb_balance_sheets", 13, 16, since="2019")]],
          auto=["economy/money_measures", W("federal_reserve/money_market_funds", title="Money market fund holdings (USD)"), "economy/central_bank_holdings"], groups=[]),
        T("ECB", auto=["ecb/key_interest_rates", "ecb/eligible_assets", "ecb/release_document", "ecb/dataflow_information", W("ecb/mfi_interest_rates", params={"symbol": "household_loans_for_house_purchase"})], groups=[]),
    ], groups=[{"name": "Bank", "paramName": "symbol", "value": "JPM"},
               {"name": "RSSD", "paramName": "rssd_id", "value": "1039502", "syncFrom": "Bank", "syncUrl": "/x/bank_rssd"}]),

    D("trade", "Trade & Shipping", "macro", "Shipping chokepoints (Hormuz, Red Sea, Suez, Black Sea, Panama, Taiwan Strait), disruptions, ports, freight and shipping stocks, trade flows, tariffs and trade-policy uncertainty.", [
        T("Chokepoints", rows=[[X("chokepoint_monitor", 24, 18), M("chokepoint_tankers", 16, 18, since="2023")],
                               [M("red_sea_diversion", 20, 16, since="2023"), M("asia_straits", 20, 16, since="2023")],
                               [X("board", 16, 20, preset="shipping"), PERF("BWET,BDRY,ZIM,FRO,SPY", "1Y", w=24, h=20, title="Normalized Performance: freight and shipping")]],
          auto=["economy/shipping/chokepoint_info", "economy/shipping/chokepoint_volume"]),
        T("Disruptions", auto=["imf/portwatch/disruption_events", "imf/portwatch/disruptions_map", "imf/portwatch/disruption_sankey"]),
        T("Ports & Trade Volumes", auto=["economy/shipping/port_volume", W("economy/shipping/port_info", sort="vessel_count_total:desc"), "imf/portwatch/*"]),
        T("Trade Flows & Tariffs", rows=[[M("trade_balance", 13, 16, since="2015"), M("federal_interest_tariffs", 14, 16, since="2000"), M("trade_uncertainty", 13, 16, since="2015")],
                                         [M("dollar", 20, 16, since="2006")]],
          auto=["economy/direction_of_trade", W("economy/export_destinations", params={"country": "united_states"}, sort="value:desc", title="Main export destinations (change the country in settings)"),
                "economy/balance_of_payments", "usda/agricultural_trade"]),
    ]),
    D("explorers", "Data Explorers", "macro", "Search and pull any series from FRED, BLS, EIA, IMF, OECD, ECB and the Federal Reserve. Data status: what is down upstream and which keys are missing.", [
        # every explorer opens on a working example; change the settings to pull anything else
        T("FRED & BLS", auto=["economy/fred_search", W("economy/fred_series", params={"symbol": "DGS10,DGS2"}, title="FRED series: 10-year and 2-year Treasury yields"),
                              W("economy/survey/bls_search", params={"category": "cpi", "query": "gasoline"}, title="BLS search: CPI, gasoline"),
                              W("economy/survey/bls_series", params={"symbol": "CUSR0000SA0", "category": "cpi"}, title="BLS series: CPI, all items"),
                              W("economy/fred_release_table", params={"release_id": "10"}, title="FRED release table: CPI"),
                              W("fred/economy/release_table", params={"release_id": "10"}),
                              W("economy/fred_regional", params={"symbol": "SMU56000000500000001"}, title="FRED regional data: payroll employment by state")]),
        T("IMF", rows=[[W("imf/presentation_table", 40, 22, params={"dataflow_group": "irfcl", "table": "irfcl_reserve_assets", "country": "JPN", "frequency": "M"},
                          title="IMF tables: Japan's official reserve assets (any dataflow, table and country in settings)")], [LIB("IMF dataflows, tables and dimensions", ["imf/list_dataflows", "imf/list_tables", "imf/get_dataflow_dimensions", "imf_utils/*"], 40, 22)]]),
        T("OECD", auto=["oecd/*"]),
        T("ECB", rows=[[W("ecb/presentation_table", 40, 22)],
                       [LIB("ECB dataflows, concepts and tables", ["ecb/list_dataflows", "ecb/search_dataflows", "ecb/topic_dataflows", "ecb/list_concepts", "ecb/list_tables", "ecb/get_dataflow_dimensions", "ecb/dataflow_information"], 40, 22)]]),
        # EIA's data browsers are in Energy > Data Library
        T("Federal Reserve", auto=["federal_reserve/fed_data", "federal_reserve/list_datasets", "federal_reserve/list_releases"]),
    ], more=["imf/*", "ecb/*"]),

    # ============================================================ GOVERNMENT
    D("congress", "U.S. Congress", "policy", "Bills, laws, amendments, votes, members and committees.", [
        # How To Use beside the cards it explains (review H2)
        T("Legislation", auto=["uscongress/how_to_use", "uscongress/bills", "uscongress/laws", "uscongress/amendments", "uscongress/search", "uscongress/bill_info", "uscongress/bill_text", "uscongress/amendment_info", "uscongress/amendment_text"]),
        T("Members & Committees", auto=["uscongress/members", "uscongress/member_info", "uscongress/member_legislation", "uscongress/member_votes", "uscongress/committee_members", "uscongress/committee_info"]),
        T("Calendars & Reports", auto=["uscongress/*"]),
    ]),
    D("spending", "Federal Spending", "policy", "Where U.S. federal money goes: agencies, awards, loans and recipients (USAspending).", [
        T("Spending & Awards", auto=["ustreasury/spending_explorer", W("ustreasury/award_search", sort="award_amount:desc"),
                                      W("ustreasury/recipient_search", sort="amount:desc"), "ustreasury/award_info", "ustreasury/recipient_info",
                                      "ustreasury/award", "ustreasury/recipient_awards"]),
    ], more=["ustreasury/*"]),

    # ============================================================ NEWS & RESEARCH
    D("news", "News", "research", "Market, economy and company news; regional exchange feeds.", [
        # the situation room: every curated feed newest first (duplicates folded), official releases beside it; then the ranked view
        T("Markets", rows=[[NEWS(26, 30, subject="market", mode="monitor", title="Market monitor (newest first)", groups=[]),
                            NEWS(14, 30, subject="market", mode="monitor", feeds="official", title="Official releases (central banks, statistics, regulators)", groups=[])],
                           [NEWS(20, 22, subject="market", title="Top market news (most material first)", groups=[]),
                            NEWS(20, 22, subject="market", mode="monitor", feeds="wires", title="Wires & financial press (newest first)", groups=[])]]),
        # company news follows the Symbol at the top (ranked: issuer filings, press, home-market press); world news needs a paid FMP plan (last)
        T("Company & World", rows=[[NEWS(24, 24, subject="symbol", title="Company news"), LIB("Raw news feeds (unfiltered)", ["news/company", "news/rss", "yfinance/news"], 16, 24, first="news/company")]],
          auto=["news/world"]),
        T("Regional", rows=[[NEWS(20, 26, subject="market", mode="monitor", feeds="ea", title="Regional news monitor", groups=[])]],
          auto=["nasdaq/nordic/news"], groups=[]),
    ], groups=[dict(SYMBOL, value="AAPL")], more=["news/*"]),
    # SEC filings, plus the SEC's enforcement and adviser data (folded in from the former SEC & Regulation dashboard)
    D("filings", "SEC Filings & Enforcement", "research", "Search and read SEC filings (full text, EDGAR, viewer, sections), enforcement actions and investment advisers.", [
        T("Search & Read", rows=[
            [W("sec/company_filings", 20, 14),
             W("sec/full_text_search", 20, 14, params={"query": "artificial intelligence"}, title='Full-text search: "artificial intelligence"')],
            [W("sec/latest_financial_reports", 20, 14), W("sec/filing_headers", 20, 14)],
            [W("sec/filing_viewer_app", 40, 24)],
            [W("sec/exhibit", 20, 16), W("sec/htm_file", 20, 16)],
        ], auto=[W("sec/section_markdown", 40, 16, wid="sec_disclosures_sec_obb"), "https://www.sec.gov/edgar/search/"]),
        T("Enforcement & Advisers", auto=["sec/rss_litigation", "regulators/sec/rss_litigation", "sec/adviser_*"], groups=[]),
    ], groups=[SYMBOL], more=["sec/*", "regulators/*"]),
    D("factors", "Factors & Styles", "funds",          # review G2: beside indices and ETFs, not under News & Research
        "Live factor, style and rotation views, plus Fama-French factor and portfolio returns.", [
        T("Live", rows=[[X("board", 16, 22, preset="us_factors"), X("perf_bars", 14, 22, preset="us_factors", period="YTD"), X("style_box", 10, 11)],
                        [PERF("MTUM,QUAL,USMV,VLUE,IWM,SPY", "1Y", w=20, h=22, title="Normalized Performance: factor ETFs vs S&P 500"), W("yfinance/rrg/view", 20, 22)]]),
        T("Fama-French", auto=["famafrench/factors", "famafrench/us_portfolio_returns", "famafrench/*"]),
    ]),
]

# Where anything not placed above ends up, by endpoint prefix (first match wins).
FALLBACK = [("federal_reserve/", "macro-us"), ("economy/", "macro-country"), ("equity/", "eq-screen"), ("nasdaq/", "eq-screen"), ("yfinance/", "eq-screen"),
            ("sec/", "filings"), ("regulators/", "filings"), ("tmx/", "eq-canada"), ("cboe/", "options"), ("derivatives/", "futures"), ("finra/", "credit"),
            ("fixedincome/", "rates"), ("fred/", "explorers"), ("usda/", "agri"), ("", "explorers")]


# ------------------------------------------------------------------ build
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


LAST_REPORT = {}                                # coverage accounting of the last build (read by scripts/tests)


def build(report=False):
    defs = load()
    ix = Index(defs)
    have_keys = credentials()
    try:
        broken = {k: v for k, v in json.loads(HEALTH.read_text(encoding="utf-8-sig")).items() if any((k, c) in ix.by_ep for c in (False, True))}
    except (OSError, ValueError):
        broken = {}
    covered = {}                               # endpoint -> first "Dashboard > Tab"
    n_down = [0]
    out, problems = [], []

    ctx = {"placed": set(), "home": ""}        # what the dashboard being built has placed so far, and the tab being built
    down_home = {}                             # broken upstream: endpoint -> "Dashboard > Tab" where it would normally be
    wid_ep = {}                                # (backend, widgetId) -> endpoint, for the column specs

    def note_down(ep):
        down_home.setdefault(ep, ctx["home"])
        ctx["placed"].add(ep)

    def make_item(spec, tab_groups, dash_groups, n, tid):
        if spec.get("library"):
            eps, down_eps, seen = [], [], set()
            for sel in spec["library"]:
                if isinstance(sel, dict):
                    e, swid = sel["ep"], sel.get("wid")
                    if e in SKIP or (e, swid) in seen:
                        continue
                    if e in broken and not sel.get("params", {}).get("_keep"):
                        down_eps.append(e)
                        continue
                    if not ix.pick(e, sel.get("chart", False), sel.get("provider"), wid=swid):
                        problems.append(f"library {spec['title']}: unknown widget {swid or e}")
                        continue
                    seen.add((e, swid))
                    eps.append((e, swid))
                    continue
                rest = sel.startswith("rest:")
                sel = sel[5:] if rest else sel
                found = ix.match(sel) if ("*" in sel or "@" in sel or sel.startswith("re:")) else ([sel] if ix.pick(sel) else [])
                if rest:
                    found = [e for e in found if e not in ctx["placed"]]
                for e in found:
                    if e in broken and e not in down_eps:
                        down_eps.append(e)
                    elif e not in SKIP and (e, None) not in seen:
                        seen.add((e, None))
                        eps.append((e, None))
            for e in down_eps:
                note_down(e)
            if not eps:
                problems.append(f"empty library {spec['title']}")
                return None
            if spec.get("first"):
                idx = next((i for i, (e, swid) in enumerate(eps) if spec["first"] in (e, swid)), None)
                if idx is not None:
                    eps.insert(0, eps.pop(idx))
                else:
                    problems.append(f"library {spec['title']}: first choice {spec['first']} not in it")
            ctx["placed"].update([e for e, _ in eps] + down_eps)
            choices = []
            for e, swid in eps:
                b, w = ix.pick(e, wid=swid)
                d = defs[b][w]
                lbl = TITLES.get(e) or d.get("name") or w
                sub = d.get("subCategory") or d.get("category") or ""
                ch = {"b": b, "w": w, "l": lbl, "g": "" if sub == lbl else sub}
                wid_ep.setdefault((b, w), e)
                if PARAM_FIXES.get(e):
                    ch["p"] = PARAM_FIXES[e]                   # the working defaults travel with the choice
                if REQUIRED_PARAMS.get(e):
                    ch["r"] = REQUIRED_PARAMS[e]
                if CHOICE_PLOTS.get(e):
                    ch["c"] = CHOICE_PLOTS[e]                  # this dataset opens plotted

                if needs_key(provider_of(d), e) and needs_key(provider_of(d), e) not in have_keys:
                    ch["k"] = needs_key(provider_of(d), e)
                choices.append(ch)
            item = make_item(dict(spec, ep=eps[0][0], wid=eps[0][1], library=None, title=None), tab_groups, dash_groups, n, tid)
            item.update({"title": spec["title"], "choices": choices, "_eps": [e for e, _ in eps]})
            item.pop("compact", None)
            if choices[0].get("c"):
                item.update({"chart": dict(choices[0]["c"]), "view": "chart"})
            return item
        if spec["ep"] in FRED_KEYLESS_SUB and KEYS["fred"] not in have_keys:
            sub = make_item(M(FRED_KEYLESS_SUB[spec["ep"]], spec["w"] or 20, spec["h"] or 16), tab_groups, dash_groups, n, tid)
            if sub:
                sub["_eps"] = ["x:macro_chart"]          # the endpoint counts as placed here; its series are shown keyless
            return sub
        got = ix.pick(spec["ep"], spec["chart"], spec["provider"] or PROVIDER_FIXES.get(spec["ep"]), wid=spec.get("wid"))
        if not got:
            problems.append(f"no widget for {spec['ep']} (provider={spec['provider']}, wid={spec.get('wid')})" if spec.get("wid")
                            else f"no widget for {spec['ep']} (provider={spec['provider']})")
            return None
        bid, wid = got
        d = defs[bid][wid]
        wid_ep.setdefault((bid, wid), spec["ep"])
        dw, dh = SIZES.get(d.get("type"), (20, 14))
        item = {"uid": f"{tid}-{n}", "widgetId": wid, "backend": bid, "w": spec["w"] or dw, "h": spec["h"] or dh, "params": dict(spec["params"]), "groups": [],
                "name": TITLES.get(spec["ep"]) or d.get("name") or wid}   # shown if the backend is down when the card opens (never the raw id)
        if REQUIRED_PARAMS.get(spec["ep"]):
            item["require"] = REQUIRED_PARAMS[spec["ep"]]
        if needs_key(provider_of(d), spec["ep"]):
            item["_kprov"] = provider_of(d)
            if needs_key(provider_of(d), spec["ep"]) not in have_keys:
                item["_keyed"] = True
                item["needsKey"] = needs_key(provider_of(d), spec["ep"])   # the card says so at once instead of sending a request that must fail
        if wid == "x_macro":                    # a curated chart drawn only from EIA series needs the EIA key
            prov = macro_key_provider(spec["params"].get("preset"))
            if prov:
                item["_kprov"] = prov
                if KEYS[prov] not in have_keys:
                    item["_keyed"] = True
                    item["needsKey"] = KEYS[prov]
        if spec["title"]:
            item["title"] = spec["title"]
        if bid == "x":                          # the card header shows these names before the first answer arrives
            try:
                item["src"] = list(dict.fromkeys(s["name"] for s in composite.static_sources(spec["ep"][2:], {**{p["paramName"]: p.get("value") for p in d.get("params", []) if p.get("paramName")}, **spec["params"]})))
            except Exception:  # noqa: BLE001
                pass
        if spec.get("view"):
            item["view"] = spec["view"]          # "chart": open a table already plotted
        if spec.get("manual"):
            item["manual"] = spec["manual"]      # too heavy to fetch automatically: the card asks first
        if spec.get("sort") or SORTS.get(spec["ep"]):
            item["sort"] = spec.get("sort") or SORTS[spec["ep"]]
        if spec.get("since"):
            item["since"] = spec["since"]
        if spec.get("tparam"):
            item["titleParam"] = spec["tparam"]
        if spec.get("cols"):
            item["cols"] = spec["cols"]
        if spec.get("plot"):
            item["chart"] = dict(spec["plot"])
        if spec.get("only"):
            item["srcOnly"] = list(spec["only"])
        # boards and rankings read better without their settings strip (the gear on the card brings it back)
        if spec["compact"] or (spec["compact"] is None and wid in ("x_board", "x_perf_bars", "x_style_box", "x_global_yields", "x_policy_rates", "x_rates_by_country", "x_macro", "x_compare", "x_chokepoints", "x_gpr_table")):
            item["compact"] = True
        # A widget follows a dashboard group when it has that parameter and the spec did not pin a value.
        allowed = spec["groups"] if spec["groups"] is not None else tab_groups
        names = {p.get("paramName") for p in d.get("params", [])}
        for g in dash_groups:
            if allowed is not None:
                if g["name"] not in allowed:
                    continue
            elif g is not next(o for o in dash_groups if o["paramName"] == g["paramName"]):
                continue                        # a second group on the same parameter only applies where a tab asks for it
            forced = spec["groups"] is not None and g["name"] in spec["groups"]      # the spec asked for it even though the widget definition omits the parameter
            if (g["paramName"] in names or forced) and g["paramName"] not in item["params"] and not any(x["paramName"] == g["paramName"] and x["name"] in item["groups"] for x in dash_groups):
                item["groups"].append(g["name"])
        # working defaults, except where a dashboard group (ticker, country) drives the parameter
        driven = {g["paramName"] for g in dash_groups if g["name"] in item["groups"]}
        for k, v in PARAM_FIXES.get(spec["ep"], {}).items():
            if k not in item["params"] and k not in driven:
                item["params"][k] = v
        return item

    for dash in DASHBOARDS:
        placed_here = ctx["placed"] = set()
        tabs = []

        def down(spec):
            if spec["ep"] in broken and not spec["params"].get("_keep"):
                note_down(spec["ep"])                  # broken upstream: goes to Data Explorers > Data status
                return True
            return False

        def add_tab(name, items):
            if not items:
                return
            tabs.append({"id": slug(name) or f"t{len(tabs)}", "name": name, "items": items})
            for it in items:
                ep = it.pop("_ep")
                for e in [ep] + it.pop("_eps", []):
                    placed_here.add(e)
                    covered.setdefault(e, f"{dash['name']} > {name}")

        def auto_items(selectors, tab, tid, start_y, start_n):
            eps = []
            for sel in selectors:
                if isinstance(sel, dict):
                    if sel["ep"] not in SKIP:
                        eps.append(sel)
                    continue
                found = ix.match(sel) if ("*" in sel or sel.startswith("re:") or "@" in sel) else ([sel] if ix.pick(sel) else [])
                if not found and not ("*" in sel or sel.startswith("re:") or "@" in sel):
                    problems.append(f"{dash['name']} > {tab['name']}: unknown endpoint {sel}")
                is_glob = "*" in sel or sel.startswith("re:") or "@" in sel
                for ep in found:
                    if ep in SKIP or ep in [e if isinstance(e, str) else e["ep"] for e in eps]:
                        continue
                    if is_glob and ep in placed_here:
                        continue
                    eps.append(ep)
            items, x, y, row_h, n = [], 0, start_y, 0, start_n
            built = []
            for e in eps:
                spec = e if isinstance(e, dict) else W(e)
                if down(spec):
                    continue
                it = make_item(spec, tab["groups"], dash["groups"], n, tid)
                if not it:
                    continue
                n += 1
                built.append((spec, it))
            built.sort(key=lambda p: 1 if p[1].get("_keyed") else 0)       # sources that still need a key go last
            for spec, it in built:
                d = defs[it["backend"]][it["widgetId"]]
                full = tab["cols"] == 1 or d.get("type") in ("iframe", "html")
                it["w"] = 40 if full else (spec["w"] or 20)
                if tab["h"] and not spec["h"]:
                    it["h"] = tab["h"]
                if x + it["w"] > 40:
                    x, y, row_h = 0, y + row_h, 0
                it["x"], it["y"], it["_ep"] = x, y, spec["ep"]
                x += it["w"]
                row_h = max(row_h, it["h"])
                items.append(it)
                placed_here.add(spec["ep"])
            return items

        for tab in dash["tabs"]:
            tid = slug(tab["name"])
            ctx["home"] = f"{dash['name']} > {tab['name']}"
            items, y, n = [], 0, 0
            def build_specs(specs):
                nonlocal n
                out_ = []
                for spec in specs:
                    if down(spec):
                        continue
                    it = make_item(spec, tab["groups"], dash["groups"], n, tid)
                    if not it:
                        continue
                    n += 1
                    out_.append((spec, it))
                # cards that need a key this computer does not have move behind the keyless ones (ids stay the same)
                out_.sort(key=lambda p: 1 if p[1].get("_keyed") else 0)
                return out_

            if tab["columns"]:
                x = 0
                for width, col in tab["columns"]:
                    cy = 0
                    for spec, it in build_specs(col):
                        it.update({"x": x, "y": cy, "w": width, "_ep": spec["ep"]})
                        cy += it["h"]
                        items.append(it)
                        placed_here.add(spec["ep"])
                    x += width
                    y = max(y, cy)
            for row in tab["rows"]:
                x, row_h = 0, 0
                for spec, it in build_specs(row):
                    it.update({"x": x, "y": y, "_ep": spec["ep"]})
                    x += it["w"]
                    row_h = max(row_h, it["h"])
                    items.append(it)
                    placed_here.add(spec["ep"])
                y += row_h
            items += auto_items(tab["auto"], tab, tid, y, n)
            # keep tabs readable: split very long ones
            limit = 16
            if len(items) > limit and not tab["rows"] and not tab["columns"]:
                parts = [items[i:i + limit] for i in range(0, len(items), limit)]
                for i, part in enumerate(parts):
                    base = part[0]["y"]
                    for it in part:
                        it["y"] -= base
                        it["uid"] = f"{tid}{i + 1}-{it['uid'].split('-')[-1]}"
                    add_tab(f"{tab['name']} ({i + 1}/{len(parts)})", part)
            else:
                add_tab(tab["name"], items)

        # the landing tab must show data without keys: a first tab that is mostly key-gated (for this computer's keys)
        # gives its place to the first tab that is mostly keyless
        def keyed_share(t):
            its = t["items"]
            return sum(1 for i in its if i.get("needsKey")) / len(its) if its else 1.0
        if tabs and keyed_share(tabs[0]) >= 0.75:
            j = next((i for i, t in enumerate(tabs) if keyed_share(t) < 0.5), None)
            if j:
                tabs.insert(0, tabs.pop(j))

        groups = []
        for g in dash["groups"]:
            g2 = {"name": g["name"], "paramName": g["paramName"], "value": g["value"]}
            g2.update({k: g[k] for k in ("optionsEndpoint", "optionsParams", "resetWith", "syncFrom", "syncUrl") if k in g})
            if g.get("options") == "countries":
                import composite
                g2["options"] = composite.COUNTRY_OPTIONS
                g2["multi"] = True
            elif g.get("options") == "macro":
                g2["options"] = [{"label": c.replace("_", " ").title(), "value": c} for c in MACRO_COUNTRIES]
                g2["multi"] = True
            groups.append(g2)
        out.append({"id": "d:" + dash["id"], "name": dash["name"], "section": dash["section"], "description": dash["desc"], "source": "app", "groups": groups, "tabs": tabs, "_spec": dash,
                    "renamed": RENAMED_TABS.get(dash["id"], {})})

    by_id = {d["id"]: d for d in out}

    # ---- one place for everything broken upstream (instead of an "Unavailable" tab on each dashboard):
    # the status card, then the broken functions themselves so they can be retried
    status = by_id["d:" + STATUS_DASH]
    items = [dict(make_item(X("data_status", 40, 24, groups=[]), [], [], 0, "data-status"), x=0, y=0)]
    covered["x:data_status"] = f"{status['name']} > Data status"
    x, y, row_h = 0, 24, 0
    broken_rows = []
    for n, ep in enumerate([e for e in down_home if e not in covered], 1):
        # broken cards wait for a click: loading 30 failing sources at once would only stall the backends
        it = make_item(W(ep, manual=f"Broken upstream: {broken.get(ep) or 'source failing'}. Normally in {down_home[ep]}."), [], [], n, "data-status")
        if not it:
            continue
        it["w"] = 20
        if x + it["w"] > 40:
            x, y, row_h = 0, y + row_h, 0
        it["x"], it["y"] = x, y
        x += it["w"]
        row_h = max(row_h, it["h"])
        items.append(it)
        covered[ep] = f"{status['name']} > Data status"
        d = defs[it["backend"]][it["widgetId"]]
        broken_rows.append({"name": d.get("name") or ep, "home": down_home[ep].replace(" > ", " › "), "reason": broken.get(ep, "")})
    n_down[0] = len(broken_rows)
    status["tabs"].append({"id": "data-status", "name": "Data status", "items": items})

    # ---- concept siblings: resolve members, and count a member reached through a placed card's source picker as placed
    concepts = {}
    for cid, c in CONCEPTS.items():
        members = []
        for ep, prov, label, opt in c["members"]:
            opt = dict(opt)
            wid = opt.pop("wid", None)
            got = ix.pick(ep, False, prov, wid=wid)
            if not got or (prov and provider_of(defs[got[0]][got[1]]) not in (prov, None)):
                continue
            b, w = got
            wid_ep.setdefault((b, w), ep)
            m = {"b": b, "w": w, "l": label, "_ep": ep, **opt}
            pk = provider_of(defs[b][w])
            if KEYS.get(pk) and KEYS[pk] not in have_keys:
                m["k"] = KEYS[pk]
            members.append(m)
        if len(members) < 1 or (len(members) < 2 and not any(m.get("mk") for m in members)):
            continue
        home = next((covered[m["_ep"]] for m in members if m["_ep"] in covered), None)
        for m in members:
            if home and m["_ep"] not in covered:
                covered[m["_ep"]] = home + " (source picker)"
            m.pop("_ep")
        concepts[cid] = {k: v for k, v in c.items() if k != "members"}
        concepts[cid]["members"] = members

    # ---- guarantee coverage: every function must live somewhere
    leftovers = [ep for ep in ix.functions if ep not in covered]
    extra = {}
    for ep in leftovers:
        # first choice: a dashboard that claims this namespace in its "more" list, else the prefix table
        target = next((d["_spec"]["id"] for d in out for sel in d["_spec"]["more"] if ep in ix.match(sel)), None)
        target = target or next(did for prefix, did in FALLBACK if ep.startswith(prefix))
        extra.setdefault(target, []).append(ep)
    for did, eps in extra.items():
        dash = by_id["d:" + did]
        spec = dash["_spec"]
        for i in range(0, len(eps), 14):
            items, x, y, row_h = [], 0, 0, 0
            for n, ep in enumerate(eps[i:i + 14]):
                it = make_item(W(ep), [], spec["groups"], n, f"other{i}")
                if not it:
                    continue
                d = defs[it["backend"]][it["widgetId"]]
                it["w"] = 40 if d.get("type") in ("iframe", "html") else 20
                if x + it["w"] > 40:
                    x, y, row_h = 0, y + row_h, 0
                it["x"], it["y"] = x, y
                x += it["w"]
                row_h = max(row_h, it["h"])
                items.append(it)
                covered[ep] = f"{dash['name']} > More"
            name = "More" if len(eps) <= 14 else f"More ({i // 14 + 1}/{(len(eps) + 13) // 14})"
            dash["tabs"].append({"id": slug(name), "name": name, "items": items})

    key_cards = {}
    for d in out:
        d.pop("_spec")
        for t in d["tabs"]:
            for it in t["items"]:
                it.pop("_keyed", None)
                prov = it.pop("_kprov", None)
                if prov:
                    key_cards[prov] = key_cards.get(prov, 0) + 1
        d["rev"] = hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10]

    # What the Data status card shows: broken functions, and which keys are set (never their values)
    keys = [{"source": KEY_LABELS.get(p, p), "set": KEYS[p] in have_keys, "cards": key_cards.get(p, 0), "signup": KEY_SIGNUP.get(p, "")}
            for p in KEYS if key_cards.get(p) or KEYS[p] in have_keys]
    keys.sort(key=lambda k: (k["set"], -k["cards"]))
    # How each placed table presents its columns (headers, formats, default column set): terminal/columns.py
    colspecs = {}
    for (b, w), ep in wid_ep.items():
        if b == "x":
            continue                            # the Terminal's own views define their columns by hand
        s = columns.spec(defs[b][w], ep)
        if s:
            colspecs[w] = s
    titles = {}
    for (b, w), ep in wid_ep.items():
        if b != "x" and TITLES.get(ep):
            titles[w] = TITLES[ep]
    stamp = composite.mtl_time(time.time())                 # shown on Data status: Montreal time like every card
    result = {"generated": stamp, "sections": [{"id": s, "name": n} for s, n in SECTIONS], "dashboards": out, "concepts": concepts, "columns": colspecs, "titles": titles,
              "status": {"generated": stamp, "broken": broken_rows, "keys": keys}}
    local_paths.ensure()
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result), encoding="utf-8")
    os.replace(tmp, OUT)                    # the UI server may be reading the previous file right now

    n_items = sum(len(t["items"]) for d in out for t in d["tabs"])
    missing = [ep for ep in ix.functions if ep not in covered]
    print(f"{len(out)} dashboards, {sum(len(d['tabs']) for d in out)} tabs, {n_items} widget placements")
    print(f"functions covered: {len(ix.functions) - len(missing)}/{len(ix.functions)}; placed by fallback: {len(leftovers)}; parked as unavailable upstream: {n_down[0]}")
    print(f"concept families: {len(concepts)}; reached only through a source picker: {sum(1 for v in covered.values() if v.endswith('(source picker)'))}")
    for p in sorted(set(problems)):
        print("  WARN", p)
    LAST_REPORT.clear()
    LAST_REPORT.update(functions=len(ix.functions), missing=missing, fallback=list(leftovers), parked=n_down[0], warnings=sorted(set(problems)))
    if report:
        for d in out:
            print(f"\n[{d['section']}] {d['name']}")
            for t in d["tabs"]:
                print(f"   {t['name']:<34} {len(t['items']):>3}")
        if leftovers:
            print("\nfallback placements:")
            for ep in leftovers:
                print("   ", ep, "->", covered[ep])
    return result


if __name__ == "__main__":
    build(report="--report" in sys.argv)
