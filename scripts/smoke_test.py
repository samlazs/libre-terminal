"""Smoke-test OpenBB backends by calling a representative endpoint per data area.

Usage:  python scripts/smoke_test.py [v5|v4|all]
Requires the backend(s) to be running (start them with the launcher; see README).
Easiest: python scripts/launcher.py smoke [v5|v4|all].
Ports: TERMINAL_V5_PORT / TERMINAL_V4_PORT, else the ones the terminal was last started on
(local/run/ports.json), else 6900 / 6901.
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "terminal"))
import local_paths  # noqa: E402
from redact import redact  # noqa: E402

# dates relative to today, so the checks do not age
YTD = f"{dt.date.today().year}-01-01"
RECENT = (dt.date.today() - dt.timedelta(days=150)).isoformat()

_SAVED = local_paths.saved_ports()
V5 = f"http://127.0.0.1:{os.environ.get('TERMINAL_V5_PORT') or _SAVED.get('v5', 6900)}/api/v1"
V4 = f"http://127.0.0.1:{os.environ.get('TERMINAL_V4_PORT') or _SAVED.get('v4', 6901)}/api/v1"

# (label, path, params)
V5_TESTS = [
    # Equities / company data
    ("Equity quote (yfinance)", "/equity/price/quote", {"symbol": "AAPL,MSFT", "provider": "yfinance"}),
    ("Equity history (yfinance)", "/equity/price/historical", {"symbol": "NVDA", "provider": "yfinance", "start_date": YTD}),
    ("Equity profile", "/equity/profile", {"symbol": "AAPL", "provider": "yfinance"}),
    ("Income statement (yfinance)", "/equity/fundamental/income", {"symbol": "AAPL", "provider": "yfinance"}),
    ("Balance sheet (yfinance)", "/equity/fundamental/balance", {"symbol": "AAPL", "provider": "yfinance"}),
    ("Analyst estimates (yfinance)", "/yfinance/estimates/revenue", {"symbol": "AAPL"}),
    ("Intl equity history (Toyota 7203.T)", "/equity/price/historical", {"symbol": "7203.T", "provider": "yfinance", "start_date": RECENT}),
    ("Intl equity history (ASML.AS)", "/equity/price/historical", {"symbol": "ASML.AS", "provider": "yfinance", "start_date": RECENT}),
    ("Canada quote (TMX)", "/equity/price/quote", {"symbol": "RY", "provider": "tmx"}),
    ("Nordic fundamentals", "/nasdaq/nordic/info", {"symbol": "VOLV B"}),
    # SEC filings / statements
    ("SEC company filings", "/sec/company_filings", {"symbol": "AAPL", "limit": 5}),
    ("SEC XBRL financial statements", "/sec/financial_statements", {"symbol": "AAPL"}),
    ("SEC segment revenue", "/sec/segment_revenue", {"symbol": "AAPL"}),
    ("SEC 13F institutional", "/equity/ownership/form_13f", {"symbol": "0001067983", "provider": "sec"}),
    ("SEC insider trading", "/equity/ownership/insider_trading", {"symbol": "AAPL", "provider": "sec"}),
    ("SEC risk factors", "/sec/risk_factors", {"symbol": "AAPL"}),
    ("FINRA short interest", "/equity/shorts/short_interest", {"symbol": "GME", "provider": "finra"}),
    # Macro / world
    ("IMF CPI (multi-country)", "/economy/cpi", {"country": "united_kingdom,japan,germany", "provider": "imf"}),
    ("OECD unemployment", "/economy/unemployment", {"country": "all", "provider": "oecd"}),
    ("OECD real GDP", "/economy/gdp/real", {"country": "all", "provider": "oecd"}),
    ("IMF direction of trade", "/economy/direction_of_trade", {"country": "united_states", "counterpart": "china", "provider": "imf"}),
    ("IMF balance of payments", "/economy/balance_of_payments", {"country": "brazil", "provider": "imf"}),
    ("OECD interest rates", "/economy/interest_rates", {"country": "all", "provider": "oecd"}),
    ("IMF PortWatch chokepoints", "/economy/shipping/chokepoint_info", {"provider": "imf"}),
    ("Fed primary dealer positioning", "/economy/primary_dealer_positioning", {"provider": "federal_reserve"}),
    ("Fed money measures", "/economy/money_measures", {"provider": "federal_reserve"}),
    # Rates / fixed income / credit
    ("SOFR", "/fixedincome/rate/sofr", {"provider": "federal_reserve"}),
    ("ESTR (ECB)", "/fixedincome/rate/estr", {"provider": "ecb"}),
    ("ECB key interest rates", "/ecb/key_interest_rates", {}),
    ("ECB MFI mortgage rates", "/ecb/mfi_interest_rates", {"symbol": "household_loans_for_house_purchase"}),
    ("US Treasury rates", "/fixedincome/government/treasury_rates", {"provider": "federal_reserve"}),
    ("Yield curve (US Treasury)", "/fixedincome/government/yield_curve", {"provider": "federal_reserve"}),
    ("Yield curve (ECB)", "/fixedincome/government/yield_curve", {"provider": "ecb"}),
    ("Treasury auctions", "/fixedincome/government/treasury_auctions", {"provider": "us_treasury"}),
    ("FINRA TRACE corporate bonds", "/finra/fixedincome/bonds", {}),
    ("CFTC CDS index trades", "/cftc/cds_index_trades", {}),
    ("CFTC OIS curve", "/cftc/ois_curve", {}),
    ("CFTC swap summary", "/cftc/swap_summary", {}),
    ("Treasury debt to the penny", "/ustreasury/debt_to_penny", {}),
    # FX / crypto / commodities / derivatives
    ("FX history (yfinance)", "/currency/price/historical", {"symbol": "EURUSD", "provider": "yfinance", "start_date": RECENT}),
    ("ECB FX reference rates", "/currency/reference_rates", {"provider": "ecb"}),
    ("CFTC FX forward curve", "/cftc/fx_forward_curve", {}),
    ("Crypto history (yfinance)", "/crypto/price/historical", {"symbol": "BTCUSD", "provider": "yfinance", "start_date": RECENT}),
    ("Deribit BTC options stats", "/deribit/options/stats", {}),
    ("[KEY] Commodity spot (FRED)", "/commodity/price/spot", {"provider": "fred"}),
    ("JODI oil production", "/jodi/oil/production", {}),
    ("USDA PSD data", "/commodity/psd_data", {"provider": "usda"}),
    ("CFTC Commitments of Traders", "/cftc/cot", {"code": "067651"}),
    ("Options chains (Nasdaq)", "/derivatives/options/chains", {"symbol": "AAPL", "provider": "nasdaq"}),
    ("Futures curve (CBOE VIX)", "/derivatives/futures/curve", {"symbol": "VX", "provider": "cboe"}),
    ("Index constituents (CBOE Europe FTSE100)", "/index/constituents", {"symbol": "BUK100P", "provider": "cboe"}),
    ("ETF holdings (yfinance)", "/yfinance/funds/holdings", {"symbol": "SPY"}),
    # Factors / news
    ("Fama-French factors", "/famafrench/factors", {}),
    ("RSS news (BBC)", "/news/rss", {"outlet": "bbc", "limit": 5, "fetch_body": "false"}),
    # Keyed (will fail until keys are added)
    ("[KEY] FRED commercial paper", "/fixedincome/corporate/commercial_paper", {"provider": "fred"}),
    ("[KEY] FRED SLOOS", "/economy/survey/sloos", {"provider": "fred"}),
    ("[KEY] FRED series", "/economy/fred_series", {"symbol": "DGS10", "provider": "fred"}),
    ("[KEY] EIA STEO", "/commodity/short_term_energy_outlook", {"provider": "eia"}),
    ("[KEY] BLS series", "/economy/survey/bls_series", {"symbol": "CUSR0000SA0", "provider": "bls"}),
]

V4_TESTS = [
    ("V4 equity quote (yfinance)", "/equity/price/quote", {"symbol": "AAPL", "provider": "yfinance"}),
    ("V4 income stmt (yfinance)", "/equity/fundamental/income", {"symbol": "AAPL", "provider": "yfinance"}),
    ("V4 equity screener (finviz)", "/equity/screener", {"provider": "finviz"}),
    ("V4 fundamentals (finviz metrics)", "/equity/fundamental/metrics", {"symbol": "AAPL", "provider": "finviz"}),
    ("V4 price target (finviz)", "/equity/estimates/price_target", {"symbol": "AAPL", "provider": "finviz"}),
    ("V4 S&P500 multiples (multpl)", "/index/sp500_multiples", {"provider": "multpl"}),

    ("V4 WSJ ETF gainers", "/etf/discovery/gainers", {"provider": "wsj"}),
    ("V4 Finviz performance", "/equity/price/performance", {"symbol": "AAPL", "provider": "finviz"}),
    ("V4 EconDB port volumes", "/economy/shipping/port_volume", {"provider": "econdb"}),
    ("[KEY] V4 world news (biztoc)", "/news/world", {"provider": "biztoc"}),
    ("[KEY] V4 Intrinio income", "/equity/fundamental/income", {"symbol": "AAPL", "provider": "intrinio"}),
    ("[KEY] V4 Trading Economics calendar", "/economy/calendar", {"provider": "tradingeconomics"}),
    ("[KEY] V4 FMP income", "/equity/fundamental/income", {"symbol": "AAPL", "provider": "fmp"}),
]


def call(base, path, params, timeout=120):
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    t = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
        try:
            data = json.loads(body)
            res = data.get("results", data) if isinstance(data, dict) else data
            n = len(res) if isinstance(res, (list, dict)) else 1
        except Exception:
            n = f"{len(body)}B"
        return True, f"rows={n}", time.time() - t
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", "")
        except Exception:
            detail = ""
        return False, f"HTTP {e.code} {redact(str(detail))[:140]}", time.time() - t
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {redact(str(e))[:140]}", time.time() - t


def run(name, base, tests):
    print(f"\n=== {name} ({base}) ===")
    ok, hard = 0, 0
    for label, path, params in tests:
        good, msg, secs = call(base, path, params)
        ok += good
        hard += (not good) and not label.startswith("[KEY]")
        print(f"{'PASS' if good else 'FAIL'}  {label:<38} {secs:5.1f}s  {msg}", flush=True)
    print(f"--- {ok}/{len(tests)} passed")
    return hard


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "v5"
    failures = 0
    if which in ("v5", "all"):
        failures += run("V5", V5, V5_TESTS)
    if which in ("v4", "all"):
        failures += run("V4 legacy", V4, V4_TESTS)
    sys.exit(1 if failures else 0)          # [KEY] checks may fail without failing the run
