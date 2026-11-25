"""Cross-asset correlation and risk statistics, and the FX rate differential (Stage 3 3a, 14a).

/x/correlation, /x/risk_stats: Yahoo Finance closes (split-adjusted, price return). Frequency "auto": daily returns on
common dates when every symbol closes in the same market; Friday (week-end) closes when the set mixes time zones, FX or
24/7 crypto, because daily closes taken hours apart are not simultaneous. Simple returns. Annualised with 252 trading
days (365 for a crypto-only daily set), 52 weeks or 12 months. Sharpe = (mean excess return per period x periods) /
annualised volatility, risk-free = average 3-month Treasury bill (FRED DGS3MO) over the window. Max drawdown and the
1-year return from each symbol's own daily closes. Beta = cov(asset, benchmark) / var(benchmark) on the same returns.

/x/fx_rate_diff: spot (Yahoo daily close) against the 2-year yield differential, base-currency 2Y minus quote-currency
2Y in basis points, so the two lines rise together when carry drives the pair. U.S. 2Y = H.15 constant maturity (par,
FRED DGS2); others are the central banks' own daily curves (zero-coupon except Japan, MoF bond yields), interpolated
at 2 years; the euro leg is Germany (Bundesbank). About one year of daily history (what the curve stores keep).
Rolling 60-day correlation of daily changes (spot log return vs differential change), stated in the note.
"""

import datetime as dt
import math

import composite as C

DEFAULT = "SPY,TLT,GLD,UUP,HYG,BTC-USD"
RANGES = {"6M": 6, "1Y": 12, "2Y": 24, "3Y": 36, "5Y": 60}
FREQS = {"auto": "Auto", "daily": "Daily", "weekly": "Weekly (Friday closes)", "monthly": "Monthly"}
FREQ_OPTIONS = [{"label": v, "value": k} for k, v in FREQS.items()]
PERIODS = {"daily": 252, "weekly": 52, "monthly": 12}
US_INDICES = {"^GSPC", "^NDX", "^DJI", "^RUT", "^IXIC", "^VIX", "^SPX"}
YAHOO = C.src("Yahoo Finance", "daily closes (split-adjusted; price return)", "https://finance.yahoo.com/")
TBILL = C.src("FRED", "DGS3MO: 3-month Treasury bill, constant maturity (risk-free rate for the Sharpe ratio)", "https://fred.stlouisfed.org/series/DGS3MO")


def market_of(sym):
    """Which closing time a Yahoo symbol keeps: us, a listing suffix, fx, crypto, futures or other."""
    s = str(sym).upper()
    if s.endswith("=X"):
        return "fx"
    if s.endswith("=F"):
        return "futures"
    if s.endswith(("-USD", "-USDT", "-EUR")):
        return "crypto"
    if s.startswith("^"):
        return "us" if s in US_INDICES else "other:" + s
    return s.rsplit(".", 1)[1] if "." in s else "us"


def auto_freq(symbols):
    """("daily" | "weekly", reason)."""
    mk = {market_of(s) for s in symbols}
    if len(mk) == 1 and next(iter(mk)) not in ("fx", "futures") and not next(iter(mk)).startswith("other:"):
        return "daily", "daily: all close in the same market"
    why = "crypto trades 24/7" if "crypto" in mk else "FX closes at a different time" if "fx" in mk else "the set mixes closing times"
    return "weekly", f"weekly (Friday closes): {why}"


def resample(closes, freq):
    """[(iso, close)] -> the last close of each week (Friday or earlier) or month; daily unchanged."""
    if freq == "daily":
        return list(closes)
    out = {}
    for d, v in closes:
        day = dt.date.fromisoformat(d)
        if freq == "weekly":
            if day.weekday() > 4:                      # crypto weekends: the week is taken at Friday's close
                continue
            key = day.isocalendar()[:2]
        else:
            key = (day.year, day.month)
        out[key] = (d, v)
    return [out[k] for k in sorted(out)]


def returns(closes):
    """{date: simple return} from consecutive closes."""
    return {closes[i][0]: closes[i][1] / closes[i - 1][1] - 1 for i in range(1, len(closes)) if closes[i - 1][1] and closes[i][1]}


def _mean(x):
    if not x:
        return None
    return sum(x) / len(x)


def corr(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    ma, mb = _mean(a), _mean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb) if sa and sb else None


def stdev(x):
    if not x or len(x) < 2:
        return None
    m = _mean(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def max_drawdown(values):
    """Largest fall from a running high, in percent (negative), from closes."""
    worst = 0.0
    peak = None
    for v in values:
        peak = v if peak is None or v > peak else peak
        worst = min(worst, (v / peak - 1) * 100)
    return worst


def beta(r, rb):
    if not r or not rb:
        return None
    mb = _mean(rb)
    var = sum((y - mb) ** 2 for y in rb)
    if not var:
        return None
    ma = _mean(r)
    return sum((x - ma) * (y - mb) for x, y in zip(r, rb)) / var


def _symbols(symbols, symbol=None, peers=None):
    if symbol:
        lst = C._syms(symbol, 1) + [p for p in C._syms(peers, 12) if p not in C._syms(symbol, 1)]
        if len(lst) == 1:
            try:
                lst += [p for p in C._peers(lst[0])[:8] if p != lst[0]]
            except C.CompositeError as e:
                C.partial_from(e, "peers")
        return lst
    return C._syms(symbols or DEFAULT, 20) or C._syms(DEFAULT, 20)


def _load(syms, rng, today):
    rng = rng if rng in RANGES else "1Y"
    start = C.minus_months(today, RANGES[rng])
    hist = C.history(syms, start - dt.timedelta(days=10)) or {}
    return rng, start, {s: [(d, v) for d, v in (hist.get(s) or []) if v and d >= (start - dt.timedelta(days=7)).isoformat()] for s in syms}


def _freq(freq, syms):
    if freq in PERIODS:
        return freq, freq + (" (Friday closes)" if freq == "weekly" else "")
    return auto_freq(syms)


def _aligned(series, freq):
    """{sym: {date: return}} on the dates every symbol has."""
    rets = {s: returns(resample(v, freq)) for s, v in series.items() if len(v) > 2}
    common = sorted(set.intersection(*(set(r) for r in rets.values()))) if rets else []
    return {s: [r[d] for d in common] for s, r in rets.items()}, common


def correlation(symbols=DEFAULT, range="1Y", freq="auto", today=None, **_):
    today = today or dt.date.today()
    syms = _symbols(symbols)
    if len(syms) < 2:
        raise C.CompositeError("Give at least two symbols.")
    rng, start, series = _load(syms, range, today)
    f, why = _freq(freq, syms)
    al, dates = _aligned(series, f)
    names = [s for s in syms if s in al]
    if len(names) < 2 or len(dates) < 10:
        raise C.CompositeError("Fewer than 10 common return dates for these symbols at this frequency.")
    m = [[1.0 if a == b else corr(al[a], al[b]) for b in names] for a in names]
    z = [[None if v is None else round(v, 3) for v in row] for row in m]
    txt = [["" if v is None else f"{v:.2f}" for v in row] for row in m]
    trace = {"type": "heatmap", "z": z, "x": names, "y": names, "zmin": -1, "zmax": 1, "text": txt, "texttemplate": "%{text}",
             "colorscale": [[0, "#3987e5"], [0.5, "#2c2c2a"], [1, "#d95926"]], "hovertemplate": "%{y} / %{x}: %{z:.2f}<extra></extra>",
             "colorbar": {"thickness": 10, "tickvals": [-1, -0.5, 0, 0.5, 1]}}
    lay = C._layout(False)
    lay["yaxis"] = {"autorange": "reversed", "automargin": True}
    lay["xaxis"] = {"side": "bottom", "automargin": True}
    missing = [s for s in syms if s not in al]
    lay["annotations"] = [C._note(f"Correlation of {why.split(':')[0]} returns, {len(dates)} periods {dates[0]} to {dates[-1]} · {why}"
                                  + (f" · no prices: {', '.join(missing)}" if missing else ""))]
    return {"data": [trace], "layout": lay, "sources": [YAHOO], "controls": {"ranges": list(RANGES), "range": rng},
            "as_of": dates[-1]}                  # the last return date every symbol shares


def risk_stats(symbols=DEFAULT, benchmark="SPY", range="1Y", freq="auto", symbol=None, peers=None, today=None, **_):
    today = today or dt.date.today()
    syms = _symbols(symbols, symbol, peers)
    bench = (C._syms(benchmark, 1) or ["SPY"])[0]
    allsyms = list(dict.fromkeys(syms + [bench]))
    rng, start, series = _load(allsyms, range, today)
    f, why = _freq(freq, allsyms)
    per = PERIODS[f]
    if f == "daily" and all(market_of(s) == "crypto" for s in allsyms):
        per = 365
    # each symbol on its own return dates (a short history does not shorten the others); beta on dates shared with the benchmark
    rets = {s: returns(resample(v, f)) for s, v in series.items() if len(v) > 2}
    try:
        tb, _ = C._fred(["DGS3MO"], start.isoformat())
        bills = [v for d, v in tb.get("DGS3MO", []) if v is not None and d >= start.isoformat()]
    except C.CompositeError as e:
        C.partial_from(e, "3-month T-bill rate (FRED)")
        bills = []
    rf_pct = _mean(bills)
    rf = rf_pct / 100 if rf_pct is not None else None
    rows = []
    rb = rets.get(bench) or {}
    for s in syms:
        px = [v for d, v in series.get(s, []) if d >= start.isoformat()]
        rd = rets.get(s) or {}
        r = list(rd.values())
        row = {"symbol": s, "vol": None, "sharpe": None, "max_dd": None, "beta": None, "ret_1y": None, "periods": len(r)}
        if len(r) > 2:
            sd = stdev(r)
            row["vol"] = round(sd * math.sqrt(per) * 100, 2) if sd else None
            if sd and rf is not None:
                row["sharpe"] = round((_mean(r) - rf / per) * per / (sd * math.sqrt(per)), 2)
            shared = sorted(set(rd) & set(rb))
            if s == bench:
                row["beta"] = 1.0
            elif len(shared) > 2:
                b = beta([rd[d] for d in shared], [rb[d] for d in shared])
                row["beta"] = None if b is None else round(b, 2)
        if len(px) > 1:
            row["max_dd"] = round(max_drawdown(px), 2)
            p0, p1 = C.num(px[0]), C.num(px[-1])
            row["ret_1y"] = round((p1 / p0 - 1) * 100, 2) if (p0 and p0 > 0 and p1 is not None) else None
        rows.append(row)
    if not any(r["vol"] is not None for r in rows):
        raise C.CompositeError("Too few common prices to compute risk statistics.")
    n = max((r["periods"] for r in rows), default=0)
    conv = (f"{why}; up to {n} periods; Sharpe vs average 3M T-bill {rf * 100:.2f}%" if rf is not None else f"{why}; Sharpe not computed (3M T-bill not answered)") + \
        f"; beta vs {bench}; return = price change over the window ({rng})"
    # the newest close among the price series the statistics were computed from
    return {"results": rows, "sources": [YAHOO, TBILL], "convention": conv, "as_of": C.newest_date(series[s][-1][0] for s in rets if series.get(s))}


# ---------------------------------------------------------------- FX: spot vs 2-year yield differential
PAIRS = {  # pair: (Yahoo symbol, base country, quote country)
    "EURUSD": ("EURUSD=X", "germany", "united_states"), "GBPUSD": ("GBPUSD=X", "united_kingdom", "united_states"),
    "USDJPY": ("JPY=X", "united_states", "japan"), "USDCAD": ("CAD=X", "united_states", "canada"), "USDSEK": ("SEK=X", "united_states", "sweden"),
    "EURGBP": ("EURGBP=X", "germany", "united_kingdom"), "USDBRL": ("BRL=X", "united_states", "brazil"),
}
PAIR_OPTIONS = [{"label": k[:3] + "/" + k[3:], "value": k} for k in PAIRS]
CORR_WINDOW = 60


def two_year(country):
    """[(iso, 2Y yield %)] daily: FRED DGS2 for the U.S., else the country's own daily curve interpolated at 2 years."""
    if country == "united_states":
        ser, _ = C._fred(["DGS2"], C._curve_since().isoformat())
        return sorted((d, v) for d, v in ser.get("DGS2", []) if v is not None)
    src = C.COUNTRIES[country][1]
    curve = C.HIST_SOURCES[src]()
    return sorted((d, y) for d, pts in curve.items() for y in [C._interp(sorted(tuple(p) for p in pts), 2.0)] if y is not None)


def differential(base, quote):
    """Base 2Y minus quote 2Y in basis points on the dates both have."""
    q = dict(quote)
    return [(d, round((v - q[d]) * 100, 2)) for d, v in base if d in q]


def rolling_corr(spot, diff, window=CORR_WINDOW):
    """Correlation of daily spot log returns with daily differential changes over the last `window` shared dates."""
    s, d = dict(spot), dict(diff)
    days = sorted(set(s) & set(d))
    ch = [(days[i], math.log(s[days[i]] / s[days[i - 1]]), d[days[i]] - d[days[i - 1]])
          for i in range(1, len(days)) if s[days[i - 1]] and s[days[i - 1]] > 0 and s[days[i]] and s[days[i]] > 0]
    out = []
    for i in range(window, len(ch) + 1):
        w = ch[i - window:i]
        c = corr([x[1] for x in w], [x[2] for x in w])
        if c is not None:
            out.append((w[-1][0], c))
    return out


def fx_rate_diff(pair="EURUSD", range="1Y", today=None, **_):
    pair = str(pair or "EURUSD").strip().upper().replace("/", "")
    if pair not in PAIRS:                       # never another pair's figures under this name
        raise C.CompositeError("Pick one of: " + ", ".join(PAIRS), kind="input")
    ysym, base_c, quote_c = PAIRS[pair]
    today = today or dt.date.today()
    with C.cf.ThreadPoolExecutor(max_workers=3) as ex:
        fb, fq = ex.submit(two_year, base_c), ex.submit(two_year, quote_c)
        spot = (C.history([ysym], C.minus_months(today, 13)) or {}).get(ysym) or []
        b, q = fb.result(), fq.result()
    diff = differential(b, q)
    if not diff or not spot:
        raise C.CompositeError(f"{pair}: no {'2-year yields on common dates' if not diff else 'spot prices'} just now.")
    start = max(diff[0][0], spot[0][0], C.minus_months(today, 12).isoformat())
    spot = [(d, v) for d, v in spot if d >= start]
    diff = [(d, v) for d, v in diff if d >= start]
    rc = rolling_corr(spot, diff)
    name = pair[:3] + "/" + pair[3:]
    lb, lq = (C.COUNTRIES[c][0] for c in (base_c, quote_c))
    traces = [{"type": "scatter", "mode": "lines", "name": f"{name} spot  {spot[-1][1]:.4f}", "x": [d for d, _ in spot], "y": [v for _, v in spot],
               "line": {"width": 2, "color": C.PALETTE[0]}, "hovertemplate": "%{y:.4f}<extra>" + name + "</extra>"},
              {"type": "scatter", "mode": "lines", "name": f"2Y differential  {diff[-1][1]:+.0f} bp", "x": [d for d, _ in diff], "y": [v for _, v in diff], "yaxis": "y2",
               "line": {"width": 2, "color": C.PALETTE[1]}, "hovertemplate": "%{y:+.0f} bp<extra>" + f"{lb} 2Y - {lq} 2Y" + "</extra>"}]
    lay = C._layout(True)
    lay["yaxis"]["title"] = {"text": name, "font": {"size": 10}}
    lay["yaxis2"] = {"overlaying": "y", "side": "right", "showgrid": False, "zeroline": False, "automargin": True, "ticksuffix": " bp",
                     "title": {"text": f"{lb} 2Y - {lq} 2Y", "font": {"size": 10}}}
    kinds = {c: ("par, H.15" if c == "united_states" else C.CURVE_KIND.get(C.COUNTRIES[c][1], "")) for c in (base_c, quote_c)}
    corr_txt = f"60-day correlation of daily changes {rc[-1][1]:+.2f}" if rc else "too few shared days for a 60-day correlation"
    short = f" · 2Y history only from {diff[0][0]} (the keyless daily file holds no more)" if diff[0][0] > (dt.date.fromisoformat(start) + dt.timedelta(days=30)).isoformat() or         diff[0][0] > C.minus_months(today, 11).isoformat() else ""
    lay["annotations"] = [C._note(f"{lb} 2Y ({kinds[base_c]}) minus {lq} 2Y ({kinds[quote_c]}), bp · {corr_txt} · to {min(spot[-1][0], diff[-1][0])}{short}")]
    return {"data": traces, "layout": lay, "sources": fx_sources(pair), "controls": {"ranges": ["6M", "1Y"], "range": "1Y"}}


def fx_sources(pair):
    _, base_c, quote_c = PAIRS.get(pair, PAIRS["EURUSD"])
    out = [YAHOO]
    for c in (base_c, quote_c):
        if c == "united_states":
            out.append(C.src("FRED", "DGS2: 2-year Treasury, constant maturity (H.15)", "https://fred.stlouisfed.org/series/DGS2"))
        else:
            code = C.COUNTRIES[c][1]
            out.append(C.src(C.CURVE_SHORT.get(code, code), C.CURVE_SOURCES.get(code, "") + ", 2-year point", C.CURVE_URLS.get(code, "")))
    return out


def static_sources(name, p):
    if name == "fx_rate_diff":
        return fx_sources(str((p or {}).get("pair") or "EURUSD").upper())
    return [YAHOO, TBILL] if name == "risk_stats" else [YAHOO]
