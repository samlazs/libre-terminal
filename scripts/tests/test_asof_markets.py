"""P3-03 for the market views: board, watchlist, performance, perf_bars, style_box, chart, fx_matrix, fx_moves, fx_trend,
fx_rate_diff, correlation, risk_stats. Each answers as_of = the newest data point it actually used. Every case builds its
upstream bars by hand with a distinct last date per symbol, runs the real view through dispatch, and compares with a date
typed here as a literal (never computed). Calendar used: 2026-11-18 Wed, 19 Thu, 20 Fri, 23 Mon, 24 Tue (weekdays only)."""

import contextlib
import datetime as dt
import math

from _common import check_as_of, covered, done

import composite as C
import risk

TODAY = dt.date(2026, 11, 25)


def bdays(last, n=300):
    """n weekdays ending at the ISO date `last`, oldest first."""
    d, out = dt.date.fromisoformat(last), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out[::-1]


def series(last, base=100.0, k=0.7):
    """[(iso, close)] on weekdays, last bar on `last`, moving enough for returns, correlations and averages."""
    return [(d, round(base * (1 + 0.0004 * i + 0.01 * math.sin(k * i)), 6)) for i, d in enumerate(bdays(last))]


def ohlc(last, base=100.0, k=0.7):
    return [[d, c * 0.99, c * 1.01, c * 0.98, c, 1000 + i] for i, (d, c) in enumerate(series(last, base, k))]


BASE = {"AAPL": 190.0, "SAP.DE": 210.0, "SPY": 480.0, "7203.T": 2900.0, "SHEL.L": 2700.0, "EURUSD=X": 1.08, "JPY=X": 150.0, "TLT": 95.0,
        "GLD": 215.0, "^GSPC": 5900.0, "^N225": 38000.0}


def fake_history(last_by_symbol, default=None):
    """A history seam: each symbol gets bars up to its own last date (default: the same for the rest)."""
    def history(symbols, start, interval="1d"):
        out = {}
        for i, s in enumerate(symbols):
            last = last_by_symbol.get(s, default)
            out[s] = series(last, BASE.get(s, 100.0), 0.3 + 0.17 * (i % 7) + 0.05 * len(s)) if last else []
        return C.PriceHistory(out)
    return history


@contextlib.contextmanager
def patched(**attrs):
    saved = {}
    for dotted, val in attrs.items():
        mod, name = dotted.split("__")
        obj = {"C": C, "risk": risk}[mod]
        saved[dotted] = (obj, name, getattr(obj, name))
        setattr(obj, name, val)
    try:
        yield
    finally:
        for obj, name, old in saved.values():
            setattr(obj, name, old)


def run(route, params, **seams):
    C._cache.clear()
    C._backoff.clear()
    try:
        with patched(**seams):
            return C.dispatch(route, params)
    finally:
        C._cache.clear()
        C._backoff.clear()


META = lambda syms: {s: {"name": s, "currency": "USD"} for s in syms}  # noqa: E731
MAJORS = ["EURUSD=X", "JPY=X", "GBPUSD=X", "CHF=X", "CAD=X", "AUDUSD=X", "NZDUSD=X"]
MATRIX = ["EURUSD=X", "JPY=X", "GBPUSD=X", "CHF=X", "CAD=X", "AUDUSD=X", "NZDUSD=X", "CNY=X"]

# ---- board: the newest last bar among the rows
out = run("board", {"symbols": "AAPL,SAP.DE"}, C__history=fake_history({"AAPL": "2026-11-20", "SAP.DE": "2026-11-23"}))
check_as_of("board", out, "2026-11-23", "SAP.DE newer than AAPL")
covered("board", "us", out)
covered("board", "eur", out)
out = run("board", {"symbols": "SPY,7203.T"}, C__history=fake_history({"SPY": "2026-11-20", "7203.T": "2026-11-19"}))
check_as_of("board", out, "2026-11-20", "SPY newer than 7203.T")
covered("board", "jpy", out)

# ---- watchlist: same, with the trading-currency lookup faked
out = run("watchlist", {"symbols": "AAPL,SAP.DE"}, C__history=fake_history({"AAPL": "2026-11-23", "SAP.DE": "2026-11-20"}), C___meta=META)
check_as_of("watchlist", out, "2026-11-23", "AAPL newer than SAP.DE")
covered("watchlist", "us", out)
covered("watchlist", "eur", out)
out = run("watchlist", {"symbols": "SHEL.L,EURUSD=X"}, C__history=fake_history({"SHEL.L": "2026-11-19", "EURUSD=X": "2026-11-20"}), C___meta=META)
check_as_of("watchlist", out, "2026-11-20", "EURUSD=X newer than SHEL.L")
covered("watchlist", "gbp", out)

# ---- performance: the newest x of the plotted lines
out = run("performance", {"symbols": "AAPL,SAP.DE", "period": "3M"}, C__history=fake_history({"AAPL": "2026-11-20", "SAP.DE": "2026-11-23"}))
check_as_of("performance", out, "2026-11-23", "SAP.DE newer than AAPL")
covered("performance", "us", out)
covered("performance", "eur", out)
out = run("performance", {"symbols": "^GSPC,^N225", "period": "YTD"}, C__history=fake_history({"^GSPC": "2026-11-24", "^N225": "2026-11-20"}))
check_as_of("performance", out, "2026-11-24", "^GSPC newer than ^N225")
covered("performance", "jpy", out)

# ---- perf_bars: the newest last bar among the ranked members
out = run("perf_bars", {"symbols": "AAPL,SAP.DE", "period": "1D"}, C__history=fake_history({"AAPL": "2026-11-20", "SAP.DE": "2026-11-23"}))
check_as_of("perf_bars", out, "2026-11-23", "SAP.DE newer than AAPL")
covered("perf_bars", "us", out)
covered("perf_bars", "eur", out)
out = run("perf_bars", {"symbols": "SPY,7203.T", "period": "1M"}, C__history=fake_history({"SPY": "2026-11-24", "7203.T": "2026-11-23"}))
check_as_of("perf_bars", out, "2026-11-24", "SPY newer than 7203.T")
covered("perf_bars", "jpy", out)

# ---- style_box: U.S. Russell funds only (no non-U.S. input exists), so two different laggard/leader layouts
out = run("style_box", {}, C__history=fake_history({"IWO": "2026-11-23"}, default="2026-11-20"))
check_as_of("style_box", out, "2026-11-23", "one small-growth fund a session ahead")
out = run("style_box", {"period": "1M"}, C__history=fake_history({"IWB": "2026-11-20"}, default="2026-11-19"))
check_as_of("style_box", out, "2026-11-20", "one large-core fund a session ahead")

# ---- fx_matrix: the newest last bar among the eight pairs
out = run("fx_matrix", {}, C__history=fake_history({"JPY=X": "2026-11-23"}, default="2026-11-20"))
check_as_of("fx_matrix", out, "2026-11-23", "USD/JPY a session ahead of the rest")
out = run("fx_matrix", {}, C__history=fake_history({"GBPUSD=X": "2026-11-20"}, default="2026-11-19"))
check_as_of("fx_matrix", out, "2026-11-20", "GBP/USD a session ahead of the rest")

# ---- fx_moves / fx_trend: strength is measured on the closes all eight majors share, so a pair's later bar is not used
out = run("fx_moves", {"period": "1D"}, C__history=fake_history({"JPY=X": "2026-11-23"}, default="2026-11-20"))
check_as_of("fx_moves", out, "2026-11-20", "last close shared by the majors, not the lone USD/JPY bar of the 23rd")
out = run("fx_moves", {"period": "1W"}, C__history=fake_history({}, default="2026-11-24"))
check_as_of("fx_moves", out, "2026-11-24", "all majors to the 24th")
out = run("fx_trend", {"period": "1M"}, C__history=fake_history({"CAD=X": "2026-11-24"}, default="2026-11-19"))
check_as_of("fx_trend", out, "2026-11-19", "last close shared by the majors, not the lone USD/CAD bar of the 24th")
out = run("fx_trend", {"period": "3M"}, C__history=fake_history({}, default="2026-11-23"))
check_as_of("fx_trend", out, "2026-11-23", "all majors to the 23rd")

# ---- correlation: the last return date every symbol shares
out = run("correlation", {"symbols": "SPY,GLD,TLT", "today": TODAY}, C__history=fake_history({"SPY": "2026-11-24", "GLD": "2026-11-24", "TLT": "2026-11-23"}))
check_as_of("correlation", out, "2026-11-23", "TLT one session behind the other two U.S. funds")
covered("correlation", "us", out)
out = run("correlation", {"symbols": "SAP.DE,EURUSD=X,JPY=X", "today": TODAY}, C__history=fake_history({}, default="2026-11-24"))
check_as_of("correlation", out, "2026-11-24", "SAP.DE and two pairs, weekly, all to the 24th")
covered("correlation", "eur", out)

# ---- risk_stats: the newest close among the series the statistics came from (T-bill answer faked, no network)
tbill = lambda ids, start, **kw: ({"DGS3MO": [("2026-11-20", 4.0)]}, None)  # noqa: E731
out = run("risk_stats", {"symbols": "SPY,TLT", "benchmark": "SPY", "today": TODAY}, C__history=fake_history({"SPY": "2026-11-20", "TLT": "2026-11-19"}), C___fred=tbill)
check_as_of("risk_stats", out, "2026-11-20", "SPY ahead of TLT")
covered("risk_stats", "us", out)
out = run("risk_stats", {"symbols": "SAP.DE,SHEL.L", "benchmark": "SAP.DE", "freq": "daily", "today": TODAY},
          C__history=fake_history({"SAP.DE": "2026-11-23", "SHEL.L": "2026-11-24"}), C___fred=tbill)
check_as_of("risk_stats", out, "2026-11-24", "SHEL.L ahead of SAP.DE")
covered("risk_stats", "gbp", out)
covered("risk_stats", "eur", out)


# ---- fx_rate_diff: the newest of the spot line and the differential line that are drawn
def two_year(ends, base=3.0):
    return lambda country: [(d, base + 0.01 * i) for i, d in enumerate(bdays(ends[country]))]


out = run("fx_rate_diff", {"pair": "EURUSD", "today": TODAY}, C__history=fake_history({"EURUSD=X": "2026-11-24"}),
          risk__two_year=two_year({"germany": "2026-11-23", "united_states": "2026-11-20"}))
check_as_of("fx_rate_diff", out, "2026-11-24", "spot to the 24th, yields shared to the 20th")
out = run("fx_rate_diff", {"pair": "USDJPY", "today": TODAY}, C__history=fake_history({"JPY=X": "2026-11-20"}),
          risk__two_year=two_year({"united_states": "2026-11-20", "japan": "2026-11-24"}, 1.0))
check_as_of("fx_rate_diff", out, "2026-11-20", "spot and shared yields to the 20th, a Japanese 2Y of the 24th unused")

# ---- chart: the newest bar drawn
out = run("chart", {"symbol": "AAPL", "compare": "SAP.DE", "period": "3M"},
          C___ohlc=lambda s, start, interval="1d": ohlc({"AAPL": "2026-11-20", "SAP.DE": "2026-11-23"}[s], BASE[s]))
check_as_of("chart", out, "2026-11-23", "SAP.DE overlay newer than AAPL")
covered("chart", "us", out)
covered("chart", "eur", out)
out = run("chart", {"symbol": "7203.T", "view": "pct", "period": "3M"},
          C___ohlc=lambda s, start, interval="1d": ohlc({"7203.T": "2026-11-19"}[s], BASE[s]))
check_as_of("chart", out, "2026-11-19", "7203.T alone")
covered("chart", "jpy", out)
out = run("chart", {"symbol": "SHEL.L", "compare": "SPY", "view": "ratio", "period": "3M"},
          C___ohlc=lambda s, start, interval="1d": ohlc({"SHEL.L": "2026-11-20", "SPY": "2026-11-24"}[s], BASE[s]))
check_as_of("chart", out, "2026-11-20", "ratio runs on the main symbol's dates; SPY's later bars are not used")

done("test_asof_markets")
