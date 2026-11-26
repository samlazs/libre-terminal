"""A day's change counts from the previous close, everywhere it is shown, for every exchange. Expected values worked by
hand from Yahoo's numbers of 2026-11-25 (EUNL.DE, Xetra, Europe/Berlin; SPY, NYSE Arca, America/New_York).

- Yahoo quotes (Snapshot "Last", the Quote card): funds had no last price (Yahoo has no currentPrice for an ETF) and
  no symbol had a change; the amount and the percent now come from one previous close.
- TradingView legend: it printed close - open of the bar (EUNL.DE "-2.29 (-1.83%)" beside Yahoo's -2.325 (-1.87%));
  each of its three sites now counts from the previous bar's close (the browser half is test_legend_change.py).
- Boards and watchlist: 1D % and Chg from the same two daily bars (the bar before the last)."""

import asyncio
import datetime as dt
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

# ---- continuous futures (=F) 1D% on contract roll days vs closed holiday settled sessions (Comment 1)
# Active session on 2026-11-26: timestamp[-1] == regularMarketTime, chartPreviousClose is active front-month settlement.
ts_act = int(dt.datetime(2026, 11, 26, 12, 0, tzinfo=dt.timezone.utc).timestamp())
bz_chart = {"chart": {"result": [{"timestamp": [ts_act], "meta": {
    "gmtoffset": -18000, "regularMarketTime": ts_act, "chartPreviousClose": 90.64, "regularMarketPrice": 91.76}}]}}
ng_chart = {"chart": {"result": [{"timestamp": [ts_act], "meta": {
    "gmtoffset": -18000, "regularMarketTime": ts_act, "chartPreviousClose": 3.658, "regularMarketPrice": 3.593}}]}}
# Closed Thanksgiving settled session: daily bar timestamp is 00:00 exchange midnight, hours/days away from regularMarketTime
he_chart = {"chart": {"result": [{"timestamp": [ts_act - 86400], "meta": {
    "gmtoffset": -21600, "regularMarketTime": ts_act - 43200, "chartPreviousClose": 79.88, "regularMarketPrice": 79.88}}]}}
check(c.futures_prev_from_chart(bz_chart) == ("2026-11-26", 90.64), "BZ=F active roll: extracts active contract settlement 90.64")
check(c.futures_prev_from_chart({"chart": {"result": [{"timestamp": [ts_act], "meta": {
    "gmtoffset": -18000, "regularMarketTime": ts_act, "previousClose": 94.61, "chartPreviousClose": 90.64, "regularMarketPrice": 91.76}}]}}) == ("2026-11-26", 90.64),
      "chartPreviousClose takes precedence over previousClose when both fields are present")
check(c.futures_prev_from_chart(ng_chart) == ("2026-11-26", 3.658), "NG=F active roll: extracts active contract settlement 3.658")
check(c.futures_prev_from_chart(he_chart) is None, "HE=F closed session: ignored so yesterday's settled move is kept")
check(c.futures_prev_from_chart({"chart": {"result": [{"timestamp": [ts_act], "meta": {"regularMarketTime": ts_act, "chartPreviousClose": 0}}]}}) is None,
      "zero chartPreviousClose ignored")

bz_ser = [("2026-11-25", 94.61), ("2026-11-26", 91.76)]
ng_ser = [("2026-11-25", 3.269), ("2026-11-26", 3.593)]
he_ser = [("2026-11-24", 82.88), ("2026-11-25", 79.88)]
r_bz = c._quote_row("Brent Crude", "BZ=F", bz_ser, prev_close=("2026-11-26", 90.64))
check(near(r_bz["chg"], 1.12) and near(r_bz["chg_pct"], 1.12 / 90.64) and near(r_bz["chg_pct"] * 100, 1.2356575, 1e-5),
      f"BZ=F roll 1D: +1.12, +1.2357% from 90.64 (not -3.01% from 94.61) ({r_bz['chg']}, {r_bz['chg_pct']})")
r_ng = c._quote_row("Natural Gas", "NG=F", ng_ser, prev_close=("2026-11-26", 3.658))
check(near(r_ng["chg"], -0.065) and near(r_ng["chg_pct"], -0.065 / 3.658) and near(r_ng["chg_pct"] * 100, -1.776927, 1e-5),
      f"NG=F roll 1D: -0.065, -1.7769% from 3.658 (not +9.91% from 3.269) ({r_ng['chg']}, {r_ng['chg_pct']})")
r_mis = c._quote_row("Brent Crude", "BZ=F", bz_ser, prev_close=("2026-11-25", 90.64))
check(near(r_mis["chg"], -2.85), "date-mismatched prev_close falls back to ser[-2]")
r_he = c._quote_row("Lean Hogs", "HE=F", he_ser, prev_close=None)
check(near(r_he["chg"], -3.0) and near(r_he["chg_pct"], -3.0 / 82.88), "closed holiday HE=F keeps prior settled move (-3.00, -3.6197%)")

fut_hist = c.PriceHistory({"BZ=F": bz_ser, "NG=F": ng_ser, "HE=F": he_ser})
fut_hist.prev_close = {"BZ=F": ("2026-11-26", 90.64), "NG=F": ("2026-11-26", 3.658)}
saved_f = c.history, c._meta
try:
    c.reset_runtime_caches()
    c.history = lambda syms, start, interval="1d": fut_hist
    c._meta = lambda syms: {s: {"name": s, "currency": "USD"} for s in syms}
    bd = c.dispatch("board", {"symbols": "BZ=F,NG=F,HE=F"})
    by_s = {x["symbol"]: x for x in bd["results"]}
    check(near(by_s["BZ=F"]["chg_pct"] * 100, 1.2356575, 1e-5) and near(by_s["NG=F"]["chg_pct"] * 100, -1.776927, 1e-5)
          and near(by_s["HE=F"]["chg_pct"] * 100, -3.619691, 1e-5), "board uses futures prev_close override per symbol")
    wl = c.dispatch("watchlist", {"symbols": "BZ=F,NG=F,HE=F"})
    w_s = {x["symbol"]: x for x in wl["results"]}
    check(near(w_s["BZ=F"]["chg_pct"] * 100, 1.2356575, 1e-5) and near(w_s["NG=F"]["chg_pct"] * 100, -1.776927, 1e-5),
          "watchlist uses futures prev_close override")
    pb = c.dispatch("perf_bars", {"symbols": "BZ=F,NG=F,HE=F", "period": "1D"})
    pb_map = dict(zip(pb["data"][0]["y"], pb["data"][0]["x"]))
    check(pb_map["BZ=F"] == 1.24 and pb_map["NG=F"] == -1.78 and pb_map["HE=F"] == -3.62,
          f"perf_bars 1D uses roll-free futures moves: {pb_map}")
    pf = c.dispatch("performance", {"symbols": "BZ=F,NG=F", "period": "1D"})
    pf_map = {t["name"].split()[0]: t["y"][-1] for t in pf["data"]}
    check(pf_map["BZ=F"] == 1.236 and pf_map["NG=F"] == -1.777, f"performance 1D uses roll-free futures moves: {pf_map}")
finally:
    c.history, c._meta = saved_f
    c.reset_runtime_caches()

# ---- Expired cache refetch keeps previously saved active settlement prev_close on disk (no roll-gap flip after close)
saved_fh = c._fetch_history
try:
    sym_rf = "BZF_REFETCH_TEST=F"
    c.reset_runtime_caches()
    c._sym_hist_path(sym_rf).unlink(missing_ok=True)
    # Seed expired cache (> 24h old) that holds active settlement prev_close from earlier in the session
    old_ts = c.time.time() - c.HIST_STALE_TTL - 60
    c._sym_hist_save(sym_rf, "1d", "2026-11-01", bz_ser, {}, now=old_ts, prev_close=("2026-11-26", 90.64))

    def settled_refetch(syms, start, interval="1d"):
        # Simulate after-close refetch: daily bars still end at 2026-11-26, but chart prev_close is absent (None)
        return c.PriceHistory({s: list(bz_ser) for s in syms})

    c._fetch_history = settled_refetch
    h_refetch = c.history([sym_rf], dt.date(2026, 11, 1), "1d")
    check(h_refetch.prev_close.get(sym_rf) == ("2026-11-26", 90.64),
          f"expired cache refetch keeps saved prev_close immediately: {h_refetch.prev_close}")
    c._cache.clear()
    h_fresh = c.history([sym_rf], dt.date(2026, 11, 1), "1d")
    check(h_fresh.prev_close.get(sym_rf) == ("2026-11-26", 90.64),
          f"subsequent fresh cache hit also returns saved prev_close: {h_fresh.prev_close}")

    # Concurrent save race: slow reader that loaded old entry before foreground saved pc must wait behind _hist_lock
    import threading  # noqa: E402
    entered = threading.Event()
    release = threading.Event()
    orig_load = c._sym_hist_load

    def slow_bg_load(s, iv):
        res = orig_load(s, iv)
        if threading.current_thread().name == "bg-slow" and res is not None:
            entered.set()
            release.wait(2.0)
        return res

    c._sym_hist_save(sym_rf, "1d", "2026-11-01", bz_ser, {}, prev_close=("2026-11-26", 90.64))
    t_bg = threading.Thread(target=lambda: c._sym_hist_save(sym_rf, "1d", "2026-11-01", bz_ser, {}), name="bg-slow")
    t_fg = threading.Thread(target=lambda: c._sym_hist_save(sym_rf, "1d", "2026-11-01", bz_ser, {}, prev_close=("2026-11-26", 91.05)), name="fg-fast")
    c._sym_hist_load = slow_bg_load
    try:
        t_bg.start()
        check(entered.wait(2.0), "bg save holds per-symbol lock")
        t_fg.start()
        release.set()
        t_bg.join(2.0)
        t_fg.join(2.0)
        check(c._sym_hist_pc(sym_rf, "1d") == ("2026-11-26", 91.05),
              f"per-symbol save lock serializes saves without dropping foreground pc: {c._sym_hist_pc(sym_rf, '1d')}")
    finally:
        c._sym_hist_load = orig_load
finally:
    c._fetch_history = saved_fh
    c._sym_hist_path("BZF_REFETCH_TEST=F").unlink(missing_ok=True)
    c.reset_runtime_caches()

# ---- YFinance historical default end_date includes tomorrow across time zones
from openbb_yfinance.models import equity_historical as yeh  # noqa: E402

saved_tq = yeh.YFinanceEquityHistoricalFetcher.transform_query
try:
    rb.install_yfinance_historical_end_date([])
    q_default = yeh.YFinanceEquityHistoricalFetcher.transform_query({"symbol": "7203.T"})
    check(q_default.end_date == dt.date.today() + dt.timedelta(days=1),
          f"omitted historical end_date defaults to tomorrow ({q_default.end_date})")
    explicit = dt.date(2026, 5, 1)
    q_explicit = yeh.YFinanceEquityHistoricalFetcher.transform_query({"symbol": "7203.T", "end_date": explicit})
    check(q_explicit.end_date == explicit, f"explicit historical end_date is preserved ({q_explicit.end_date})")
finally:
    yeh.YFinanceEquityHistoricalFetcher.transform_query = saved_tq

done("test_day_change")
