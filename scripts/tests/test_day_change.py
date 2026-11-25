"""A day's change counts from the previous close, everywhere it is shown, for every exchange. Expected values worked by
hand from Yahoo's numbers of 2026-11-25 (EUNL.DE, Xetra, Europe/Berlin; SPY, NYSE Arca, America/New_York).

- Yahoo quotes (Snapshot "Last", the Quote card): funds had no last price (Yahoo has no currentPrice for an ETF) and
  no symbol had a change; the amount and the percent now come from one previous close.
- TradingView legend: it printed close - open of the bar (EUNL.DE "-2.29 (-1.83%)" beside Yahoo's -2.325 (-1.87%));
  each of its three sites now counts from the previous bar's close (the browser half is test_legend_change.py).
- Boards and watchlist: 1D % and Chg from the same two daily bars (the bar before the last)."""

import asyncio
import math
import pathlib
import sys
import types

from _common import check, done

import composite as c

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_backend as rb  # noqa: E402

FIELDS = ["symbol", "currentPrice", "previousClose", "currency", "open"]


def near(a, b, tol=1e-9):
    return a is not None and b is not None and math.isclose(a, b, rel_tol=0, abs_tol=tol)


# ---- Yahoo quote: a fund's price and every symbol's change, from one previous close
# EUNL.DE (ETF): no currentPrice. 122.24 - 124.565 = -2.325; -2.325 / 124.565 = -0.018665%... = -1.8665% (Yahoo -1.866499)
eunl = {"symbol": "EUNL.DE", "quoteType": "ETF", "regularMarketPrice": 122.24, "previousClose": 124.565,
        "regularMarketPreviousClose": 124.565, "open": 124.525, "currency": "EUR"}
r = rb.yahoo_quote_record(eunl, FIELDS)
check(r["currentPrice"] == 122.24, "ETF: the last price falls back to regularMarketPrice (was blank)")
check(near(r["change"], -2.325) and near(r["change_percent"], -2.325 / 124.565) and near(r["change_percent"] * 100, -1.86649, 1e-5),
      "ETF on Xetra: change -2.325 and -1.8665% from the previous close 124.565, not from the open 124.525")
check(near(r["change"] / 124.565, r["change_percent"]), "the amount and the percent count from the same close")
# SAP.DE (share): currentPrice 189.82 = regularMarketPrice; 189.82 - 190.08 = -0.26; -0.26 / 190.08 = -0.13678% (Yahoo -0.13678162)
sap = {"symbol": "SAP.DE", "currentPrice": 189.82, "regularMarketPrice": 189.82, "previousClose": 190.08, "currency": "EUR"}
r = rb.yahoo_quote_record(sap, FIELDS)
check(r["currentPrice"] == 189.82 and near(r["change"], -0.26) and near(r["change_percent"] * 100, -0.136784, 1e-6), "share: Yahoo's own fields kept")
# SPY (U.S. ETF): 717.18 - 718.78 = -1.60; -1.60 / 718.78 = -0.22260%
spy = {"symbol": "SPY", "regularMarketPrice": 717.18, "previousClose": 718.78, "open": 703.2}
r = rb.yahoo_quote_record(spy, FIELDS)
check(near(r["change"], -1.6) and near(r["change_percent"] * 100, -0.222599, 1e-6), "U.S. fund: -1.60, -0.2226% (from the open it would be +1.99%)")
# VUSA.L in GBP (pounds; a GBp line would be in pence, the percent the same): 101.5475 - 103.59 = -2.0425; -1.97171%
r = rb.yahoo_quote_record({"regularMarketPrice": 101.5475, "regularMarketPreviousClose": 103.59, "currency": "GBP"}, FIELDS)
check(r["previousClose"] == 103.59 and near(r["change"], -2.0425) and near(r["change_percent"] * 100, -1.971715, 1e-6),
      "LSE fund: the previous close falls back to regularMarketPreviousClose")
r = rb.yahoo_quote_record({"symbol": "X", "regularMarketPrice": 10.0}, FIELDS)
check("change" not in r and "change_percent" not in r, "no previous close: no change (blank, never a guess)")
r = rb.yahoo_quote_record({"currentPrice": float("nan"), "regularMarketPrice": 5.0, "previousClose": 0}, FIELDS)
check(r["currentPrice"] == 5.0 and "change" not in r, "a NaN price or a zero close is skipped")

# through the fetcher and OpenBB's own model: last_price, change and change_percent (a fraction, as the model declares)
import yfinance  # noqa: E402
from openbb_yfinance.models import equity_quote as yq  # noqa: E402

F = yq.YFinanceEquityQuoteFetcher
saved_ticker, saved_extract = yfinance.Ticker, (F.aextract_data, F.extract_data)
try:
    infos = {"EUNL.DE": eunl, "SPY": spy}
    yfinance.Ticker = lambda s: types.SimpleNamespace(get_info=lambda: dict(infos[s]))
    rb.install_yfinance_quote_regular_market([])
    q = yq.YFinanceEquityQuoteQueryParams(symbol="EUNL.DE,SPY")
    rows = asyncio.run(yq.YFinanceEquityQuoteFetcher.aextract_data(q, None))
    got = {d.symbol: d for d in yq.YFinanceEquityQuoteFetcher.transform_data(q, rows)}
    check(got["EUNL.DE"].last_price == 122.24 and got["EUNL.DE"].prev_close == 124.565, "model: the ETF has its last price and previous close")
    check(near(got["EUNL.DE"].change, -2.325) and near(got["EUNL.DE"].change_percent, -0.0186649, 1e-7), "model: change and change percent filled")
    check(near(got["SPY"].change, -1.6) and got["SPY"].open == 703.2, "model: a U.S. fund too, the open left as it is")
    via = {d.symbol: d for d in asyncio.run(F().fetch_data({"symbol": "EUNL.DE"}, {}))}
    check(via["EUNL.DE"].last_price == 122.24 and near(via["EUNL.DE"].change, -2.325), "fetch_data (what the route calls) uses the fix")
finally:
    yfinance.Ticker = saved_ticker
    F.aextract_data, F.extract_data = saved_extract

# ---- TradingView legend: the page's three change sites, rewritten all or none
SITES = """
function _tvRenderHoverLegend(chartId, param) {
    if (d && d.open !== undefined) {
        var chg = Number(d.close) - Number(d.open);
        var chgPct = Number(d.open) !== 0 ? ((chg / Number(d.open)) * 100) : 0;
        var clr = _tvLegendColorize(d.close, d.open);
    }
}
function _legendRenderMainFromLastData() {
        if (last.open !== undefined) {
            var chg = Number(last.close) - Number(last.open);
            var chgPct = Number(last.open) !== 0 ? ((chg / Number(last.open)) * 100) : 0;
            var clr = colorize(last.close, last.open);
        }
}
function hover(param) {
            if (d.open !== undefined) {
                var chg    = d.close - d.open;
                var chgPct = d.open !== 0 ? ((chg / d.open) * 100) : 0;
                var clr    = colorize(d.close, d.open);
            }
}"""
page = "<html><head><title>t</title></head><body><script>" + SITES + "</script></body></html>"
out, n = rb.legend_change_from_previous_close(page)
check(n == 3 and out.count("window.__terminalChangeBase(") == 3, "all three legend sites count from the previous close")
check("Number(d.close) - Number(d.open)" not in out and "d.close - d.open" not in out and "last.close) - Number(last.open)" not in out,
      "no site counts from the bar's open any more")
check("__terminalChangeBase(entry && entry._seriesRawData && entry._seriesRawData[mainKey], d)" in out
      and "__terminalChangeBase(rows, last)" in out and "entry._seriesRawData[sKey], d)" in out, "each site looks in its own series' bars")
check("_tvLegendColorize(d.close, __base)" in out and "colorize(last.close, __base)" in out, "the values are coloured by the same change")
check(out.index("__terminalChangeBase = function") < out.index("<title>"), "the helper is defined in <head>, before any chart code runs")
check(rb.legend_change_from_previous_close(page.replace("var clr = colorize(last.close, last.open);", "var clr = paint(last);")) == (
      page.replace("var clr = colorize(last.close, last.open);", "var clr = paint(last);"), 0), "a legend it does not recognise is left whole")

# the real pywry sources in .venv-v5 (the version VERIFIED names): exactly the three sites
import pywry  # noqa: E402

src = pathlib.Path(pywry.__file__).parent / "frontend" / "src" / "tvchart"
real = (src / "07-drawing" / "02-helpers.js").read_text(encoding="utf-8") + (src / "11-legend.js").read_text(encoding="utf-8")
check(rb.legend_change_from_previous_close("<head></head>" + real)[1] == 3, "pywry's own legend code: its three sites are found")
calls = []
native = types.SimpleNamespace(tvchart_widget_html=None)


async def served(symbol="AAPL", interval="1d", theme="dark"):
    calls.append((symbol, interval))
    return page


native.tvchart_widget_html = served
rb.install_tv_legend_change([], native)
html = asyncio.run(native.tvchart_widget_html("EUNL.DE", "1d"))
check(calls == [("EUNL.DE", "1d")] and html == out, "installed: the chart page is served with the legend fixed")

# ---- boards and watchlist: Chg and 1D % from the same two bars, any exchange
row = c._quote_row("iShares Core MSCI World", "EUNL.DE", [("2026-11-23", 126.645), ("2026-11-24", 124.565), ("2026-11-25", 122.24)])
check(near(row["chg"], -2.325, 1e-9) and near(row["chg_pct"], -2.325 / 124.565) and row["as_of"] == "2026-11-25", "board: EUNL.DE -2.325, -1.8665%")
check(near(row["chg"] / 124.565, row["chg_pct"]), "board: Chg and 1D % agree")
row = c._quote_row("SPDR S&P 500", "SPY", [("2026-11-24", 718.78), ("2026-11-25", 717.18)])
check(near(row["chg"], -1.6) and near(row["chg_pct"] * 100, -0.222599, 1e-6), "board: SPY -1.60, -0.2226%")
done("test_day_change")
