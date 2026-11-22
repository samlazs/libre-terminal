"""Currencies: daily closes (17:00 New York), what each change measures, and the strength graphs. Expected values worked
by hand: Yahoo's daily FX "close" of a date is that date's 00:00 London rate, so it must never be read as that day's close."""

import datetime as dt
import math
import pathlib
import sys

import pandas as pd

from _common import check, done

import composite as c

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_backend as rb  # noqa: E402


def bars(*rows):
    return pd.DataFrame([dict(date=d, open=o, high=h, low=lo, close=x, volume=0) for d, o, h, lo, x in rows])


def closes(df):
    return dict(zip(df["date"], df["close"]))


# ---- daily bars alone: a day's close is the next weekday's opening rate; weekend ticks and the unknown last close are dropped
DAILY = bars(("2026-11-18", 100, 103, 99, 100), ("2026-11-19", 102, 106, 101, 102), ("2026-11-20", 105, 109, 104, 105), ("2026-11-22", 999, 999, 999, 999))
out = rb.fx_daily_bars(DAILY, None)
check(closes(out) == {"2026-11-18": 102, "2026-11-19": 105}, "Wed closes 102 (Thu's open), Thu 105; Fri unknown -> no bar; Sunday tick dropped")
check(closes(rb.fx_daily_bars(bars(("2026-12-31", 10, 11, 9, 10), ("2027-01-04", 12, 12, 12, 12)), None)) == {"2026-12-31": 12}, "over a holiday: the next bar there is")
check(rb.fx_daily_bars(bars(("2026-11-18", 1, 2, 1, 1), ("2026-11-19", 5, 5, 5, 5)), None).iloc[0]["high"] == 5, "high widened to contain the close")

# ---- hourly bars (UTC, stamped at their start): the session ends 17:00 New York = 22:00 UTC in November, 21:00 UTC in July
HOURLY = bars(("2026-11-19 20:00:00", 103, 104, 103, 104), ("2026-11-19 21:00:00", 104, 105, 104, 104.5),
              ("2026-11-19 22:00:00", 104.6, 104.8, 104.4, 104.7), ("2026-11-20 21:00:00", 107, 108.5, 106, 108),
              ("2026-11-20 22:00:00", 500, 500, 500, 500), ("2026-11-22 21:00:00", 700, 700, 700, 700))
out = rb.fx_daily_bars(DAILY, HOURLY)
check(closes(out) == {"2026-11-18": 102, "2026-11-19": 104.5, "2026-11-20": 108}, "hourly: Thu 104.5, Fri 108; ticks while the market is shut ignored")
fri = out[out["date"] == "2026-11-20"].iloc[0]
check((fri["open"], fri["high"], fri["low"]) == (104.6, 108.5, 104.4), "Friday's bar runs from Thu 17:00 to Fri 17:00 New York")
check(closes(rb.fx_daily_bars(DAILY, bars(("2026-11-22 22:00:00", 109, 109, 109, 109))))["2026-11-23"] == 109, "Sunday 17:00 New York opens Monday")
jul = bars(("2026-07-16 20:00:00", 1, 1, 1, 1), ("2026-07-16 21:00:00", 2, 2, 2, 2))
check(closes(rb.fx_daily_bars(None, jul)) == {"2026-07-16": 1, "2026-07-17": 2}, "summer time: 21:00 UTC starts the next session")

# ---- before 2010-08-04 Yahoo's close was a close: kept, so a bad open the next day is not pulled into it
old = bars(("2008-04-07", 102, 103, 101, 102.44), ("2008-04-08", 107.51, 108, 102, 102.6), ("2010-08-03", 86, 87, 85, 85.8), ("2010-08-04", 85.5, 86, 85, 85.5), ("2010-08-05", 86.2, 87, 86, 86.2))
check(closes(rb.fx_daily_bars(old, None)) == {"2008-04-07": 102.44, "2008-04-08": 102.6, "2010-08-03": 85.8, "2010-08-04": 86.2}, "real closes kept up to 2010-08-03, shifted from the 4th")
check(closes(rb.fx_daily_bars(bars(("2005-03-01", 1, 2, 1, 1.5)), None)) == {"2005-03-01": 1.5}, "a last bar from that era keeps its close")

# ---- weeks and months are built from those days
days = bars(("2026-10-29", 1, 4, 1, 2), ("2026-10-30", 2, 3, 2, 3), ("2026-11-02", 3, 9, 2.5, 8))
wk, mo = rb.fx_resample(days, "1wk"), rb.fx_resample(days, "1mo")
check(closes(wk) == {"2026-10-26": 3, "2026-11-02": 8} and wk.iloc[0]["high"] == 4, "weeks: Monday label, Friday's close")
check(closes(mo) == {"2026-10-01": 3, "2026-11-01": 8}, "months: last day's close")

# ---- the download wrapper
import openbb_yfinance.utils.helpers as yh  # noqa: E402

saved_dl, calls = yh.yf_download, []
try:
    def fake(symbol, start_date=None, end_date=None, interval="1d", *a, **kw):
        calls.append((symbol, interval))
        if symbol == "AAPL":
            return bars(("2026-11-20", 7, 7, 7, 7)).assign(dividends=0.25)
        if interval == "1h":
            if symbol == "CAD=X":
                raise ValueError("no hourly bars")
            return HOURLY.copy()
        return DAILY.copy()
    yh.yf_download = fake
    rb.install_fx_session_closes([])
    one = yh.yf_download("jpy=x", "2026-11-18", None, "1d")
    check(closes(one)["2026-11-20"] == 108 and "symbol" not in one.columns, "one pair: corrected, same shape as before")
    check(list(yh.yf_download("JPY=X", "2026-11-18", dt.date(2026, 11, 19), "1d")["date"]) == ["2026-11-18", "2026-11-19"], "an end date in the past is kept")
    check(closes(yh.yf_download("CAD=X", "2026-11-18", None, "1d")) == {"2026-11-18": 102, "2026-11-19": 105}, "no hourly bars: no invented Friday close")
    mix = yh.yf_download("AAPL,JPY=X", "2026-11-18", None, "1d")
    by = {(r["symbol"], r["date"]): r for r in mix.to_dict("records")}
    check(by["AAPL", "2026-11-20"]["close"] == 7 and by["JPY=X", "2026-11-20"]["close"] == 108, "mixed request: the share untouched, the pair corrected")
    check(by["AAPL", "2026-11-20"]["dividends"] == 0.25 and by["JPY=X", "2026-11-20"]["dividends"] == 0, "mixed request: no NaN dividend on a pair")
    calls.clear()
    yh.yf_download("DX-Y.NYB", "2026-11-18", None, "1d")
    yh.yf_download("JPY=X", "2026-11-18", None, "5m")
    check(calls == [("DX-Y.NYB", "1d"), ("JPY=X", "5m")], "the dollar index and intraday bars pass through")
finally:
    yh.yf_download = saved_dl

check(c._sym_hist_path("JPY=X") != c.CACHE_DIR / f"{c.disk_name('h1d', 'JPY=X')}.json", "copies saved with the wrong closes are not served")
check(c._sym_hist_path("AAPL") == c.CACHE_DIR / f"{c.disk_name('h1d', 'AAPL')}.json", "other saved histories are kept")

# ---- TradingView chart: the same bars, and a header that agrees with the board
import asyncio  # noqa: E402
import types  # noqa: E402


def utc(*a):
    return int(dt.datetime(*a, tzinfo=dt.timezone.utc).timestamp())


check(rb.fx_session_date(utc(2026, 11, 20, 21, 59)) == "2026-11-20" and rb.fx_session_date(utc(2026, 11, 20, 22, 0)) is None, "Fri 16:59 New York trades, 17:00 is shut")
check(rb.fx_session_date(utc(2026, 11, 22, 21, 59)) is None and rb.fx_session_date(utc(2026, 11, 22, 22, 0)) == "2026-11-23", "Sunday 17:00 opens Monday")
check(rb.fx_session_date(utc(2026, 7, 16, 21, 0)) == "2026-07-17", "summer: 21:00 UTC starts the next session")
check([rb.fx_decimals(v) for v in (158.5, 1.0971, 0.6623, 0.0063)] == [2, 4, 5, 6], "chart header decimals as on the board")

CHART = pd.DataFrame({"date": ["2026-07-16", "2026-11-19", "2026-11-20"], "open": [160, 159.3, 159.6], "high": [161, 160, 159.8],
                      "low": [159, 159, 158.4], "close": [160.5, 160.0, 158.4], "volume": 0})
raw, seeded, ticked, downloads, shown = [], [], [], [], {}
tv = types.SimpleNamespace(
    normalize_resolution=lambda r: {"D": "1d"}.get(r, r),
    yf_fetch_full_history=lambda s, r: raw.append((s, r)) or ["raw"],
    is_overnight=lambda e, s: "orig", current_session_label=lambda s: ("orig", ""),
    _seed_marquee=lambda app, s: seeded.append(s),
    RealtimeStreamer=type("RS", (), {"_update_marquee": lambda self, s, m: ticked.append(s)}))
tv.BarCache = type("BC", (), {"get": lambda self, s, r: tv.yf_fetch_full_history(s, r)})
app = types.SimpleNamespace(emit=lambda ev, p: shown.__setitem__(p["ticker"], p.get("text")))
saved_dl = yh.yf_download
try:
    yh.yf_download = lambda sym, start=None, end=None, interval="1d", **kw: downloads.append(interval) or CHART.copy()
    async def page(symbol="AAPL", interval="1d", theme="dark"):
        return ("<body>function fmt(v){ return Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}); }"
                "function g(v){ return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }</body>")
    native = types.SimpleNamespace(tvchart_widget_html=page)
    rb.install_fx_chart_fix([], tv, native)
    html = asyncio.run(native.tvchart_widget_html("EURUSD=X"))
    check('info.type === "Currency"' in html, "chart page carries the pair precision script")
    check(html.count("maximumFractionDigits: dp") == 2 and "maximumFractionDigits: 2" not in html, "both legend formatters follow the pair's precision")
    got = tv.yf_fetch_full_history("JPY=X", "D")
    check(got[-1]["close"] == 158.4 and not raw, "chart: candles come from the corrected download")
    check([b["time"] for b in got] == [utc(2026, 7, 15, 23), utc(2026, 11, 19), utc(2026, 11, 20)], "chart: stamped midnight London, summer and winter, so the axis shows the right date")
    tv.yf_fetch_full_history("JPY=X", "1w")
    tv.yf_fetch_full_history("JPY=X", "1M")
    check(downloads == ["1d", "1wk", "1mo"], "chart: weeks and months from the same path")
    check(tv.yf_fetch_full_history("JPY=X", "1h") == ["raw"] and tv.yf_fetch_full_history("AAPL", "1d") == ["raw"], "intraday and shares untouched")
    tv._seed_marquee(app, "JPY=X")
    check(shown["ws-price"] == "158.40" and shown["ws-change"] == "▼ -1.60 (-1.00%)", "header: change from the previous 17:00 New York close, not Yahoo's")
    feed = tv.RealtimeStreamer()
    feed._app = app
    tick = lambda when, px: feed._update_marquee("JPY=X", {"time": str(when * 1000), "price": px, "change": 9, "change_percent": 9})
    tick(utc(2026, 11, 22, 21, 0), 170.0)
    check(shown["ws-price"] == "158.40", "a tick while the market is shut does not move the header")
    tick(utc(2026, 11, 22, 22, 30), 159.192)
    check(shown["ws-change"] == "▲ +0.79 (+0.50%)" and shown["ws-open"] == "159.19" and not ticked, "new session: change from Friday's close, Yahoo's change ignored")
    tick(utc(2026, 11, 23, 3, 0), 157.608)
    check(shown["ws-change"] == "▼ -0.79 (-0.50%)" and shown["ws-high"] == "159.19" and shown["ws-low"] == "157.61", "same session: same base, high and low follow")
    feed._update_marquee("AAPL", {"price": 1})
    tv._seed_marquee(app, "AAPL")
    check(ticked == ["AAPL"] and seeded[-1] == "AAPL" and tv.is_overnight(0, "AAPL") == "orig", "shares keep their own header")
    check(tv.is_overnight(utc(2026, 11, 21, 12), "JPY=X") is True and tv.is_overnight(utc(2026, 11, 20, 12), "JPY=X") is False, "weekend ticks stay off intraday candles")
finally:
    yh.yf_download = saved_dl

# ---- Canada on the currency cards
import openbb_cftc.utils.cot_markets as cm  # noqa: E402

saved_markets = cm.COT_MARKETS
try:
    rb.install_cot_canada([])
    rb.install_cot_canada([])
    ccys = [m["label"] for m in cm.markets_for("currencies")]
    check(ccys.count("Canadian Dollar") == 1 and ccys.index("Canadian Dollar") == ccys.index("Franc") + 1, "COT: the Canadian dollar once, after the franc")
    check(cm.market_for_code("CFTC_090741")["contract"] == "CANADIAN DOLLAR", "COT: CME Canadian dollar contract")
finally:
    cm.COT_MARKETS = saved_markets
check("RBCABIS" in [s[1] for s in c.MACRO_SETS["reer"][3]], "real effective rates include Canada")

# ---- what is measured. Only the U.S. dollar moves: every other currency is worth 1% more dollars
D0, D1 = "2026-11-19", "2026-11-20"
PAIRS = {"EUR": "EURUSD=X", "JPY": "JPY=X", "GBP": "GBPUSD=X", "CHF": "CHF=X", "CAD": "CAD=X", "AUD": "AUDUSD=X", "NZD": "NZDUSD=X"}


def market(moves):
    """{currency: factor on its value in dollars} as Yahoo would quote it."""
    out = {}
    for ccy, sym in PAIRS.items():
        f = moves.get(ccy, 1.0)
        out[sym] = [(D0, 2.0), (D1, 2.0 / f if sym == ccy + "=X" else 2.0 * f)]
    return c.PriceHistory(out)


saved = c.history
try:
    c.history = lambda syms, start, interval="1d": market({k: 1.01 for k in PAIRS})
    m = {r["ccy"]: r for r in c.fx_changes("1D")["rows"]}
    check(abs(m["CAD"]["usd"] - 0.01) < 1e-12, "CAD +1% against the dollar")
    check(abs(m["CAD"]["pair"] - (1 / 1.01 - 1)) < 1e-12 and m["CAD"]["quote"] == "USD/CAD", "while USD/CAD fell 0.990%")
    check(abs(m["CAD"]["own"] - (1.01 ** (1 / 7) - 1)) < 1e-12, "CAD's own strength is only +0.142%: 1.01 against USD, flat against the other six")
    check(abs(m["USD"]["own"] - (1 / 1.01 - 1)) < 1e-12 and m["USD"]["usd"] is None, "USD -0.990% against all seven; no 'against itself'")

    # CAD gains 1% on the dollar while the rest gain 2%: up against USD, yet a weaker currency
    c.history = lambda syms, start, interval="1d": market({k: 1.01 if k == "CAD" else 1.02 for k in PAIRS})
    m = {r["ccy"]: r for r in c.fx_changes("1D")["rows"]}
    want = math.exp((math.log(1.01) + 6 * math.log(1.01 / 1.02)) / 7) - 1
    check(m["CAD"]["usd"] > 0 > m["CAD"]["own"] and abs(m["CAD"]["own"] - want) < 1e-12, "up against USD, down on its own strength")
    check(abs(sum(math.log(1 + r["own"]) for r in m.values())) < 1e-12, "strength is relative: the eight sum to zero in logs")

    fig = c.fx_moves("1D")
    usd, own = fig["data"]
    at = next(i for i, y in enumerate(own["y"]) if "<b>CAD</b>" in y)
    check(own["x"][at] < 0 < usd["x"][at] and "USD/CAD -0.99%" in own["y"][at], "bars: both measures and the pair as quoted")
    check(D0 in fig["layout"]["xaxis"]["title"]["text"] and D1 in fig["layout"]["xaxis"]["title"]["text"], "bars say from which close to which")
    tr = c.fx_trend("1M")
    check(len(tr["data"]) == 15 and all(t["y"][0] == 0 for t in tr["data"]), "trend: 8 own + 7 against USD, all from zero")
    cad = [t for t in tr["data"] if t["name"].startswith("CAD")]
    check(abs(cad[0]["y"][-1] - round(want * 100, 3)) < 1e-9 and abs(cad[1]["y"][-1] - 1.0) < 1e-9, "trend ends where the bars do")

    def gap(syms, start, interval="1d"):
        h = market({})
        h["CAD=X"] = []
        return h
    c.history = gap
    try:
        c.fx_changes("1D")
        check(False, "must refuse")
    except c.CompositeError as e:
        check("CAD" in str(e), "a missing major: no strength from a different basket")

    fx_syms = [s for _, rows in c.BOARDS["fx"][1] for _, s in rows]
    base = market({"CAD": 1.01, "EUR": 0.99})
    c.history = lambda syms, start, interval="1d": c.PriceHistory({s: base.get(s) or [(D0, 1.0), (D1, 1.02)] for s in fx_syms})
    board = {r.get("symbol"): r for r in c.board("fx")["results"] if r.get("symbol")}
    r = board["CAD=X"]
    check(r["pair_pct"] < 0 < r["usd_pct"] and r["_fmt"]["pair_last"]["suffix"] == " USD/CAD", "board: USD/CAD down, CAD up, each in its own column")
    r = board["EURUSD=X"]
    check(r["pair_pct"] == r["usd_pct"] < 0 and r["_fmt"]["pair_last"]["suffix"] == " EUR/USD", "board: EUR/USD is both")
    check(board["EURJPY=X"]["usd_pct"] is None and board["DX-Y.NYB"]["usd_pct"] is None and board["EURJPY=X"]["pair_pct"] > 0, "a cross and DXY have no 'vs USD' move")
    ranked = c.perf_bars(preset="fx", period="1D")["data"][0]["y"]
    check(len(ranked) == 14 and "EUR/JPY" not in ranked and not any("Index" in x for x in ranked), "ranking holds one measure: currencies against USD")
finally:
    c.history = saved

done("test_fx")
