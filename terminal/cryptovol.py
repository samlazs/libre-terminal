"""Implied against realised volatility for bitcoin and ether (review H4).

Implied: Deribit DVOL, the 30-day implied volatility index built from Deribit options, daily candles (UTC day, close).
Realised: computed here from Yahoo Finance daily closes of BTC-USD / ETH-USD (UTC days), declared rather than taken
from Deribit's own "historical volatility" (hourly points whose window Deribit does not state):
- close-to-close log returns, the last 30 of them (the same 30-day horizon DVOL prices);
- sample standard deviation (n - 1), annualised with 365 days (crypto trades every day), in percent.
Only complete UTC days are used: the current day is left out of both lines until it closes.
"""

import datetime as dt
import math

import composite as C

COINS = {"BTC": ("Bitcoin", "BTC-USD"), "ETH": ("Ether", "ETH-USD")}
OPTIONS = [{"label": v[0], "value": k} for k, v in COINS.items()]
RANGES = {"3M": 3, "6M": 6, "1Y": 12, "2Y": 24}
WINDOW = 30
SOURCES = [C.src("Deribit", "DVOL: 30-day implied volatility index from Deribit options, daily close (UTC)", "https://www.deribit.com/statistics/BTC/volatility-index"),
           C.src("Yahoo Finance", "daily closes (UTC days) for realised volatility: 30 close-to-close log returns, annualised x sqrt(365)",
                 "https://finance.yahoo.com/quote/BTC-USD/history")]


def _fin(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def realised(closes, window=WINDOW):
    """[(date, close)] in date order -> [(date, realised vol in %)] from the trailing `window` log returns."""
    out, rets = [], []
    for i in range(1, len(closes)):
        p0, p1 = closes[i - 1][1], closes[i][1]
        rets.append(math.log(p1 / p0) if p0 and p1 and p0 > 0 and p1 > 0 else None)
        last = rets[-window:]
        if len(last) == window and None not in last:
            m = sum(last) / window
            sd = math.sqrt(sum((r - m) ** 2 for r in last) / (window - 1))
            out.append((closes[i][0], sd * math.sqrt(365) * 100))
    return out


def _day(v):
    return str(v)[:10]


def chart(currency="BTC", range="1Y", today=None, **_):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    ccy = str(currency or "BTC").upper()
    ccy = ccy if ccy in COINS else "BTC"
    name, ticker = COINS[ccy]
    rng = str(range or "1Y").upper()
    rng = rng if rng in RANGES else "1Y"
    start = C.minus_months(today, RANGES[rng])
    fetch_from = (start - dt.timedelta(days=WINDOW + 10)).isoformat()          # room for the first 30-day window

    def load():
        dv = C.api(C.V5, "deribit/volatility/index", provider="deribit", currency=ccy, interval="1d", start_date=start.isoformat()) or []
        px = C.api(C.V5, "crypto/price/historical", provider="yfinance", symbol=ticker, start_date=fetch_from) or []
        gone = (["Deribit DVOL"] if not dv else []) + (["Yahoo Finance daily closes"] if not px else [])
        return C.Partial((dv, px), gone) if gone else (dv, px)
    dv, px = C.cached(("cryptovol", ccy, start.isoformat()), 900, load)
    done = today.isoformat()                                   # complete UTC days only
    implied = sorted((_day(r.get("date")), c) for r in dv for c in [_fin(r.get("close"))] if c is not None and _day(r.get("date")) < done)
    closes = sorted((_day(r.get("date")), c) for r in px for c in [_fin(r.get("close"))] if c is not None and c > 0 and _day(r.get("date")) < done)
    real = [(d, v) for d, v in realised(closes) if d >= start.isoformat()]
    if not implied and not real:
        raise C.CompositeError(f"Neither Deribit DVOL nor Yahoo daily closes answered for {name} just now.")
    traces = []
    for i, (label, pts) in enumerate((("Implied (DVOL, 30-day)", implied), (f"Realised ({WINDOW}-day)", real))):
        if not pts:
            continue
        traces.append({"type": "scatter", "mode": "lines", "name": f"{label}  {pts[-1][1]:.1f}%", "x": [d for d, _ in pts], "y": [round(v, 2) for _, v in pts],
                       "line": {"width": 2, "color": C.PALETTE[i]}, "hovertemplate": "%{y:.1f}%<extra>" + label + "</extra>"})
    rmap = dict(real)
    gap = [(d, v - rmap[d]) for d, v in implied if d in rmap]
    if gap:
        traces.append({"type": "bar", "name": f"Implied minus realised  {gap[-1][1]:+.1f} pts", "x": [d for d, _ in gap], "y": [round(v, 2) for _, v in gap],
                       "yaxis": "y2", "marker": {"color": C.PALETTE[2]}, "opacity": 0.35, "hovertemplate": "%{y:+.1f} pts<extra>implied - realised</extra>"})
    lay = C._layout(True)
    lay["yaxis"]["ticksuffix"] = "%"
    lay["yaxis"]["title"] = {"text": "annualised volatility", "font": {"size": 10}}
    if gap:
        lay["yaxis2"] = {"overlaying": "y", "side": "right", "showgrid": False, "zeroline": True, "automargin": True, "ticksuffix": " pts",
                         "title": {"text": "implied - realised", "font": {"size": 10}}}
    asof = max([p[-1][0] for p in (implied, real) if p])
    lay["annotations"] = [C._note(f"{name} · UTC daily closes to {asof}")]
    return {"data": traces, "layout": lay, "sources": SOURCES, "controls": {"freq": "daily", "ranges": list(RANGES), "range": rng}}
