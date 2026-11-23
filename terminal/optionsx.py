"""Options analytics from the Yahoo chain (Stage 3 10a).

/x/gex_strike: net gamma exposure by strike. Stated dealer convention: dealers long the calls and short the puts open
(call gamma counts +, put gamma -). Gamma from Black-Scholes with Yahoo's implied volatility per contract, no rates or
dividends, time = days to expiry / 365 (a same-day expiry counts half a day). GEX per strike = (call OI x gamma - put OI x
gamma) x 100 shares x spot^2 x 1%: the dollar change in dealer delta for a 1% move, in $ millions. Strikes within 25% of
spot; contracts with an implied volatility outside 1%-300% are left out. Expiry: the nearest monthly (third Friday) by
default, or every expiry within 60 days. Max Pain (the nearest expiry shown): the strike where options open would pay
their holders least at expiry.
/x/vol_cone: realised-volatility cone, close-to-close log returns annualised x sqrt(252), windows of 10/21/42/63/126
trading days; for each window the min, 25th, median, 75th percentile and max over the last year of daily rolling values,
and today's value; the ATM implied-volatility term structure (average of call and put IV at the strike nearest spot,
per expiry, days to expiry converted to trading days x 252/365) on top.
"""

import datetime as dt
import math

import composite as C

EXPIRY_OPTIONS = [{"label": "Nearest monthly", "value": "monthly"}, {"label": "All expiries within 60 days", "value": "60d"}]
WINDOWS = [10, 21, 42, 63, 126]
YAHOO = C.src("Yahoo Finance", "option chain (open interest, implied volatility per contract) and daily closes", "https://finance.yahoo.com/")


def _chain(sym):
    def run():
        rows = C.api(C.V5, "derivatives/options/chains", provider="yfinance", symbol=sym, _timeout=60) or []
        if not rows:
            raise C.CompositeError(f"Yahoo Finance has no option chain for {sym}.")
        return rows
    return C.cached(("yahoo_chain", sym), 900, run)


def pick_expiries(expiries, mode, today):
    """Sorted ISO expiries -> those used: the nearest monthly (third Friday; Thursday when that Friday is a holiday) or all within 60 days."""
    future = sorted(e for e in set(expiries) if e >= today.isoformat())
    if mode == "60d":
        return [e for e in future if (dt.date.fromisoformat(e) - today).days <= 60]
    listed = set(future)
    months = [e for e in future if third_friday_week(e, listed)]
    return months[:1] or future[:1]


def third_friday_week(iso, listed=None):
    """A third-Friday expiry, or the Thursday before it when that Friday is a holiday (not among `listed`)."""
    d = dt.date.fromisoformat(iso)
    fri = d + dt.timedelta(days=(4 - d.weekday()) % 7) if d.weekday() <= 4 else None
    if not fri or not 15 <= fri.day <= 21 or d.weekday() not in (3, 4):
        return False
    if d.weekday() == 4:
        return True
    return (fri - d).days == 1 and (listed is None or fri.isoformat() not in listed)


def bs_gamma(spot, strike, years, sigma):
    if spot <= 0 or strike <= 0 or years <= 0 or sigma <= 0:
        return 0.0
    st = sigma * math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * years) / st
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (spot * st)


def max_pain(rows):
    """Strike (among those listed) where calls and puts open would pay their holders least at expiry."""
    strikes = sorted({r["strike"] for r in rows})
    best = None
    for k in strikes:
        pay = sum(r["oi"] * (max(0.0, k - r["strike"]) if r["type"] == "call" else max(0.0, r["strike"] - k)) for r in rows)
        if best is None or pay < best[1]:
            best = (k, pay)
    return best[0] if best else None


def gex_by_strike(rows, spot, today):
    """rows [{strike, type, oi, iv, expiration}] -> {strike: $ millions per 1% move} (dealers long calls, short puts)."""
    out = {}
    for r in rows:
        if not r["oi"] or not (0.01 <= (r["iv"] or 0) <= 3.0) or abs(r["strike"] / spot - 1) > 0.25:
            continue
        days = (dt.date.fromisoformat(r["expiration"]) - today).days
        g = bs_gamma(spot, r["strike"], max(days, 0.5) / 365, r["iv"])
        sign = 1 if r["type"] == "call" else -1
        out[r["strike"]] = out.get(r["strike"], 0.0) + sign * r["oi"] * g * 100 * spot * spot * 0.01 / 1e6
    return dict(sorted(out.items()))


def _rows(raw):
    out = []
    for r in raw:
        k, t = r.get("strike"), str(r.get("option_type") or "").lower()
        if isinstance(k, (int, float)) and t in ("call", "put") and r.get("expiration"):
            out.append({"strike": float(k), "type": t, "oi": float(r.get("open_interest") or 0), "iv": r.get("implied_volatility"),
                        "expiration": str(r["expiration"])[:10]})
    return out


def gex(symbol="SPY", expiry="monthly", today=None, **_):
    sym = (C._syms(symbol, 1) or ["SPY"])[0]
    today = today or dt.date.today()
    raw = _chain(sym)
    spot = next((r.get("underlying_price") for r in raw if isinstance(r.get("underlying_price"), (int, float))), None)
    if not spot:
        raise C.CompositeError(f"The {sym} chain carries no underlying price.")
    rows = _rows(raw)
    mode = expiry if expiry in ("monthly", "60d") else "monthly"
    exps = pick_expiries([r["expiration"] for r in rows], mode, today)
    if not exps:
        raise C.CompositeError(f"No {sym} expiry from today on in the Yahoo chain.")
    use = [r for r in rows if r["expiration"] in exps]
    g = gex_by_strike(use, spot, today)
    if not g:
        raise C.CompositeError(f"No open interest with a usable implied volatility near the money for {sym}.")
    mp = max_pain([r for r in use if r["expiration"] == exps[0] and r["oi"]])
    total = sum(g.values())
    xs, ys = list(g), [round(v, 3) for v in g.values()]
    traces = [{"type": "bar", "x": xs, "y": ys, "name": f"Net GEX  {total:+,.0f} $mn per 1%", "marker": {"color": [C.PALETTE[2] if v >= 0 else C.PALETTE[1] for v in ys]},
               "hovertemplate": "strike %{x}: %{y:+,.1f} $mn per 1% move<extra></extra>"}]
    lay = C._layout(False)
    lay["yaxis"]["title"] = {"text": "$mn per 1% move", "font": {"size": 10}}
    lay["xaxis"]["title"] = {"text": "strike", "font": {"size": 10}}
    lay["shapes"] = [{"type": "line", "x0": spot, "x1": spot, "yref": "paper", "y0": 0, "y1": 1, "line": {"color": C.INK["text2"], "width": 1, "dash": "dot"}}]
    if mp is not None:
        lay["shapes"].append({"type": "line", "x0": mp, "x1": mp, "yref": "paper", "y0": 0, "y1": 1, "line": {"color": C.PALETTE[3], "width": 1.5, "dash": "dash"}})
    which = f"expiry {exps[0]}" if len(exps) == 1 else f"{len(exps)} expiries {exps[0]} to {exps[-1]}"
    lay["annotations"] = [C._note(f"{sym} spot {spot:,.2f} (dotted) · " + (f"Max Pain {mp:,.2f} for {exps[0]} (dashed)" if mp is not None else f"Max Pain n/a for {exps[0]} (no open interest)") + f" · {which} · dealers assumed long calls, short puts · "
                                  "Black-Scholes gamma from Yahoo IV")]
    return {"data": traces, "layout": lay, "sources": [YAHOO], "summary": {"spot": spot, "max_pain": mp, "net_gex_mn": round(total, 1), "expiries": exps}}


def rolling_vol(closes, window):
    """[(iso, close)] -> [(iso, annualised % vol of the last `window` log returns)]."""
    rets = [(closes[i][0], math.log(closes[i][1] / closes[i - 1][1])) for i in range(1, len(closes)) if closes[i - 1][1] > 0 and closes[i][1] > 0]
    out = []
    for i in range(window, len(rets) + 1):
        w = [x for _, x in rets[i - window:i]]
        m = sum(w) / window
        out.append((rets[i - 1][0], math.sqrt(sum((x - m) ** 2 for x in w) / (window - 1)) * math.sqrt(252) * 100))
    return out


def cone(closes, windows=WINDOWS, lookback=252):
    """{window: (min, p25, median, p75, max, current)} over the last `lookback` rolling values."""
    from valuation import quantile
    out = {}
    for w in windows:
        vals = [v for _, v in rolling_vol(closes, w)][-lookback:]
        if vals:
            s = sorted(vals)
            out[w] = (s[0], quantile(s, 0.25), quantile(s, 0.5), quantile(s, 0.75), s[-1], vals[-1])
    return out


def atm_term(rows, spot, today):
    """[(trading days to expiry, ATM IV %)] per expiry 5 to 260 calendar days out."""
    by = {}
    for r in rows:
        if 0.01 <= (r["iv"] or 0) <= 3.0:
            by.setdefault(r["expiration"], []).append(r)
    out = []
    for e, rs in sorted(by.items()):
        days = (dt.date.fromisoformat(e) - today).days
        if not 5 <= days <= 260:
            continue
        k = min({r["strike"] for r in rs}, key=lambda s: abs(s - spot))
        ivs = [r["iv"] for r in rs if r["strike"] == k]
        if ivs:
            out.append((round(days * 252 / 365, 1), sum(ivs) / len(ivs) * 100, e))
    return out


def vol_cone(symbol="SPY", today=None, **_):
    sym = (C._syms(symbol, 1) or ["SPY"])[0]
    today = today or dt.date.today()
    # complete sessions only: today's bar is a live price until the close
    closes = [x for x in (C.history([sym], today - dt.timedelta(days=560)) or {}).get(sym) or [] if x[0] < today.isoformat()]
    c = cone(closes)
    if not c:
        raise C.CompositeError(f"Too few daily closes for {sym} to build a volatility cone.")
    xs = list(c)
    lab = {0: "Min", 1: "25th", 2: "Median", 3: "75th", 4: "Max"}
    traces = []
    for i, dash in ((4, "dot"), (3, "dash"), (2, "solid"), (1, "dash"), (0, "dot")):
        traces.append({"type": "scatter", "mode": "lines", "name": f"{lab[i]} realised", "x": xs, "y": [round(c[w][i], 2) for w in xs],
                       "line": {"width": 1.5 if i != 2 else 2, "dash": dash, "color": C.INK["text2"] if i != 2 else C.PALETTE[0]}, "hovertemplate": "%{y:.1f}%<extra>" + lab[i] + "</extra>"})
    traces.append({"type": "scatter", "mode": "lines+markers", "name": f"Today realised  {c[21][5]:.1f}% (21d)" if 21 in c else "Today realised", "x": xs,
                   "y": [round(c[w][5], 2) for w in xs], "line": {"width": 2, "color": C.PALETTE[1]}, "hovertemplate": "%{y:.1f}%<extra>today</extra>"})
    note_iv = ""
    try:
        raw = _chain(sym)
        spot = next((r.get("underlying_price") for r in raw if isinstance(r.get("underlying_price"), (int, float))), None) or closes[-1][1]
        term = atm_term(_rows(raw), spot, today)
    except C.CompositeError as e:
        term, note_iv = [], f" · no implied volatility ({e})"
    if term:
        traces.append({"type": "scatter", "mode": "lines+markers", "name": f"ATM implied (by expiry)  {term[0][1]:.1f}% front", "x": [t[0] for t in term],
                       "y": [round(t[1], 2) for t in term], "text": [t[2] for t in term], "line": {"width": 2, "color": C.PALETTE[3]},
                       "hovertemplate": "%{y:.1f}% · expiry %{text}<extra>ATM implied</extra>"})
    lay = C._layout(True)
    lay["yaxis"]["ticksuffix"] = "%"
    lay["xaxis"]["title"] = {"text": "window / time to expiry, trading days", "font": {"size": 10}}
    lay["annotations"] = [C._note(f"{sym} · realised = close-to-close, annualised x sqrt(252); range of daily rolling values over the last year · complete sessions to {closes[-1][0]}{note_iv}")]
    return {"data": traces, "layout": lay, "sources": [YAHOO]}


def static_sources(name, p):
    return [YAHOO]
