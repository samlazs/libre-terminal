"""Multi-symbol equity and FRED comparison chart view."""

from __future__ import annotations

import bisect
import datetime as dt

import composite as C

CHART_VIEWS = {"price", "pct", "ratio", "spread", "dd"}


def chart(symbol="^GSPC", compare=None, view="price", period="1Y", studies="candles,volume,ma50,ma200", shade=None, **_):
    """Price chart for any Yahoo symbol or FRED series: candles + volume + moving averages, overlays, % change, ratio or spread,
    drawdown from the running high, log scale, optional U.S. recession shading."""
    main = (C._syms(symbol, 1) or ["^GSPC"])[0]
    others = [c for c in C._syms(compare, 7) if c != main]
    view = str(view or "price").lower()
    view = view if view in CHART_VIEWS else "price"
    period = str(period or "1Y").upper()
    st = {s.lower() for s in C._split(studies)}
    log = "log" in st and view != "dd"
    if view in ("ratio", "spread") and not others:
        raise C.CompositeError("Type a second symbol in 'Compare with' to plot a ratio or a spread.")
    today = dt.date.today()
    weekly = period == "MAX"
    start = dt.date(1970, 1, 1) if weekly else C.horizon_date(today, period)
    want_ma = view not in ("pct", "dd") and bool({"ma50", "ma200"} & st)
    lead = 0 if weekly else 420 if (want_ma and "ma200" in st) else 120 if want_ma else 10
    fetch_start = (start - dt.timedelta(days=lead)).isoformat()
    iso = start.isoformat()

    def load(s):
        if s.startswith("FRED:"):
            return [[d, None, None, None, v, None] for d, v in C._fred_points(s[5:], fetch_start)]
        return C._ohlc(s, fetch_start, "1W" if weekly else "1d")

    series, errors = {}, []
    with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {s: ex.submit(load, s) for s in [main] + others}
        for s, f in futs.items():
            try:
                series[s] = f.result()
            except C.CompositeError as e:
                errors.append(f"{s}: {e}")
    if main not in series:
        raise C.CompositeError("; ".join(errors))
    others = [s for s in others if s in series]
    unit = "weeks" if weekly else "days"
    traces, table, notes = [], {}, []
    yref = {}

    def window(rows):
        return [r for r in rows if r[0] >= iso] or rows[-2:]

    def add_mas(dates_all, vals_all, label):
        for key, n, color in (("ma50", 50, "#c98500"), ("ma200", 200, "#9085e9")):
            if key not in st:
                continue
            ma = C._sma(vals_all, n)
            pts = [(d, m) for d, m in zip(dates_all, ma) if d >= iso and m is not None]
            if not pts:
                continue
            name = f"{n}-{unit[:-1]} avg"
            traces.append({"type": "scatter", "mode": "lines", "name": name, "x": [p[0] for p in pts], "y": [round(p[1], 6) for p in pts],
                           "line": {"width": 1.4, "color": color}, "hovertemplate": "%{y:,.4~f}<extra>" + name + "</extra>"})
            for d, m in pts:
                table.setdefault(d, {"date": d})[f"{label} {n}{unit[0]} avg"] = round(m, 6)

    main_rows = series[main]
    if view == "price":
        rows = window(main_rows)
        has_ohlc = all(r[1] is not None and r[2] is not None and r[3] is not None for r in rows)
        if "candles" in st and has_ohlc:
            traces.append({"type": "candlestick", "name": main, "x": [r[0] for r in rows], "open": [r[1] for r in rows], "high": [r[2] for r in rows],
                           "low": [r[3] for r in rows], "close": [r[4] for r in rows],
                           "increasing": {"line": {"color": C.UP, "width": 1}, "fillcolor": C.UP}, "decreasing": {"line": {"color": C.DOWN, "width": 1}, "fillcolor": C.DOWN}})
        else:
            traces.append({"type": "scatter", "mode": "lines", "name": main, "x": [r[0] for r in rows], "y": [r[4] for r in rows],
                           "line": {"width": 2, "color": C.PALETTE[0]}, "hovertemplate": "%{y:,.4~f}<extra>" + main + "</extra>"})
        for r in rows:
            row = table.setdefault(r[0], {"date": r[0]})
            if has_ohlc:
                row.update({f"{main} open": r[1], f"{main} high": r[2], f"{main} low": r[3]})
            row[f"{main} close"] = r[4]
            if r[5] is not None:
                row[f"{main} volume"] = r[5]
        add_mas([r[0] for r in main_rows], [r[4] for r in main_rows], main)
        for i, s in enumerate(others):
            pts = window(series[s])
            traces.append({"type": "scatter", "mode": "lines", "name": s + " (right)", "x": [r[0] for r in pts], "y": [r[4] for r in pts], "yaxis": "y3",
                           "line": {"width": 1.6, "color": C.PALETTE[(i + 1) % len(C.PALETTE)]}, "hovertemplate": "%{y:,.4~f}<extra>" + s + "</extra>"})
            for r in pts:
                table.setdefault(r[0], {"date": r[0]})[f"{s} close"] = r[4]
        if others:
            yref["y3"] = True
        if "volume" in st and any(r[5] for r in rows):
            traces.append({"type": "bar", "name": "Volume", "x": [r[0] for r in rows], "y": [r[5] for r in rows], "yaxis": "y2", "showlegend": False,
                           "marker": {"color": [C.UP if (r[1] is None or r[4] >= r[1]) else C.DOWN for r in rows], "opacity": 0.45},
                           "hovertemplate": "%{y:,.3s}<extra>Volume</extra>"})
            yref["y2"] = True
    elif view == "pct":
        all_y = all(C.is_yield(s) for s in [main] + others)
        for i, s in enumerate([main] + others):
            pts = window(series[s])
            before = [r for r in series[s] if r[0] < iso]
            if before and pts[0][0] > iso:
                pts = before[-1:] + pts             # base = last close before a non-trading start (as the boards), so the line starts at 0
            base = pts[0][4]
            if not base:
                continue
            if C.is_yield(s):                     # a yield moves in basis points, not in percent of itself
                ys = [round((r[4] - base) * 100, 1) for r in pts]
                tr = {"name": f"{s}  {ys[-1]:+.0f} bp", "hovertemplate": "%{y:+.0f} bp<extra>" + s + "</extra>"}
                if not all_y:
                    tr["yaxis"] = "y2"
                    yref["y2bp"] = True
                col = s + " (bp)"
            else:
                ys = [round((r[4] / base) * 100 if log else (r[4] / base - 1) * 100, 4) for r in pts]
                last = (pts[-1][4] / base - 1) * 100
                tr = {"name": f"{s}  {last:+.1f}%", "hovertemplate": ("%{y:,.1f}" if log else "%{y:+.2f}%") + "<extra>" + s + "</extra>"}
                col = s + (" (start = 100)" if log else " %")
            traces.append(dict({"type": "scatter", "mode": "lines", "x": [r[0] for r in pts], "y": ys, "line": {"width": 2, "color": C.PALETTE[i % len(C.PALETTE)]}}, **tr))
            for r, y in zip(pts, ys):
                table.setdefault(r[0], {"date": r[0]})[col] = y
        if all_y:
            log = False
    elif view == "dd":
        skipped = []
        for i, s in enumerate([main] + others):
            pts = [r for r in window(series[s]) if r[4]]
            if C.is_yield(s) or s.startswith("FRED:") or not pts or min(r[4] for r in pts) <= 0:
                skipped.append(s)                 # a drawdown is a price concept: not for yields or economic series
                continue
            ys = [round(v, 3) for v in C.drawdown([r[4] for r in pts])]
            worst = min(ys)
            at = pts[ys.index(worst)][0]
            tr = {"type": "scatter", "mode": "lines", "name": f"{s}  {ys[-1]:.1f}% (max {worst:.1f}% on {at})", "x": [r[0] for r in pts], "y": ys,
                  "line": {"width": 1.6 if i else 2, "color": C.PALETTE[i % len(C.PALETTE)]}, "hovertemplate": "%{y:.1f}%<extra>" + s + "</extra>"}
            if not others:
                tr.update({"fill": "tozeroy", "fillcolor": "rgba(217,89,38,0.18)"})
            traces.append(tr)
            for r, y in zip(pts, ys):
                table.setdefault(r[0], {"date": r[0]})[s + " drawdown %"] = y
        if not traces:
            raise C.CompositeError("Drawdown is shown for prices only (not for yields or FRED series).")
        if skipped:
            notes.append("No drawdown for " + ", ".join(skipped) + " (not a price)")
        notes.append("Drawdown from the running high within the range")
    else:
        other = others[0]
        o_dates = [r[0] for r in series[other]]
        o_vals = [r[4] for r in series[other]]
        dates_all, vals_all = [], []
        for r in main_rows:
            j = bisect.bisect_right(o_dates, r[0]) - 1
            if j < 0 or (dt.date.fromisoformat(r[0]) - dt.date.fromisoformat(o_dates[j])).days > 10:
                continue
            o = o_vals[j]
            if view == "ratio":
                if not o:
                    continue
                v = r[4] / o
            else:
                v = r[4] - o
            dates_all.append(r[0])
            vals_all.append(v)
        if not dates_all:
            raise C.CompositeError(f"{main} and {other} have no dates in common.")
        name = f"{main} / {other}" if view == "ratio" else f"{main} − {other}"
        pts = [(d, v) for d, v in zip(dates_all, vals_all) if d >= iso] or list(zip(dates_all, vals_all))[-2:]
        traces.append({"type": "scatter", "mode": "lines", "name": name, "x": [p[0] for p in pts], "y": [round(p[1], 6) for p in pts],
                       "line": {"width": 2, "color": C.PALETTE[0]}, "hovertemplate": "%{y:,.4~f}<extra>" + name + "</extra>"})
        for d, v in pts:
            table.setdefault(d, {"date": d})[name] = round(v, 6)
        add_mas(dates_all, vals_all, name)
        if len(others) > 1:
            notes.append(f"{view.title()} uses the first comparison symbol only ({other}).")
        if view == "spread" and log:
            log = False
            notes.append("Log scale is off for a spread.")

    lay = C._layout(len([t for t in traces if t.get("showlegend") is not False]) > 1)
    lay["xaxis"]["rangeslider"] = {"visible": False}
    lay["xaxis"]["type"] = "date"
    if view == "pct":
        all_y = all(C.is_yield(s) for s in [main] + others)
        lay["yaxis"]["ticksuffix"] = " bp" if all_y else "" if log else "%"
        lay["yaxis"]["zeroline"] = not log
        if yref.get("y2bp"):
            lay["yaxis2"] = {"overlaying": "y", "side": "right", "ticksuffix": " bp", "showgrid": False, "zeroline": False, "automargin": True}
            lay["margin"]["r"] = 52
    if view == "dd":
        lay["yaxis"]["ticksuffix"] = "%"
        lay["yaxis"]["zeroline"] = True
    if log:
        lay["yaxis"]["type"] = "log"
    if yref.get("y2"):
        lay["yaxis"]["domain"] = [0.24, 1]
        lay["yaxis2"] = {"domain": [0, 0.18], "showgrid": False, "zeroline": False, "tickformat": "~s", "nticks": 3, "linecolor": "rgba(0,0,0,0)", "automargin": True}
    if yref.get("y3"):
        lay["yaxis3"] = {"overlaying": "y", "side": "right", "showgrid": False, "zeroline": False, "automargin": True, "linecolor": "rgba(0,0,0,0)", "type": "log" if log else "linear"}
        lay["margin"]["r"] = 52
    all_syms = [main] + others
    # hide weekends only when no series trades on them (Tadawul, Kuwait, Egypt and crypto do)
    weekend_bars = any(dt.date.fromisoformat(r[0][:10]).weekday() >= 5 for s in all_syms for r in window(series[s]))
    if not weekly and period in ("1M", "3M", "6M", "YTD", "1Y") and not weekend_bars and not any(s.startswith("FRED:") for s in all_syms):
        lay["xaxis"]["rangebreaks"] = [{"bounds": ["sat", "mon"]}]
    if errors:
        notes.append("No data: " + "; ".join(e.split(":")[0] for e in errors))
    if weekly:
        notes.append("Weekly bars")
    if notes:
        lay["annotations"] = [dict(C._note(" · ".join(notes)), y=1, yanchor="top")]
    used = ([C.yahoo_source([s for s in all_syms if not s.startswith("FRED:")])] if any(not s.startswith("FRED:") for s in all_syms) else []) \
        + [C.series_source(s[5:]) for s in all_syms if s.startswith("FRED:")]
    if str(shade or "").lower() == "rec":
        xs = [x for t in traces for x in (t.get("x") or [])]
        if xs:
            used += C.add_recession_shading(lay, min(xs), max(xs))
    return {"data": traces, "layout": lay, "results": [table[d] for d in sorted(table, reverse=True)], "sources": used}
