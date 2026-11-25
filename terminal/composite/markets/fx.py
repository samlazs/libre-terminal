"""Currencies: the three different things "the change in CAD" can mean, and their graphs.

pair  USD/CAD as the market quotes it (up = more CAD per dollar = weaker CAD)
usd   the currency against the U.S. dollar (+ = stronger), which also moves when only the dollar moved
own   the currency's own strength: the geometric average of its moves against the other seven majors
"""

from __future__ import annotations

import bisect
import datetime as dt
import math

import composite as C

FX_MAJORS = {"USD": None, "EUR": "EURUSD=X", "JPY": "JPY=X", "GBP": "GBPUSD=X", "CHF": "CHF=X", "CAD": "CAD=X", "AUD": "AUDUSD=X", "NZD": "NZDUSD=X"}

OWN_COLOR, USD_COLOR = C.PALETTE[0], C.PALETTE[1]          # two measures, so never the green/red of up and down
OWN_NAME, USD_NAME = "Own strength (vs the other 7 majors)", "Against USD only"


def _usd_values(hist):
    """(dates shared by all, {currency: its value in dollars on those dates}). A missing major is an error: a strength
    against a different basket would carry the same name."""
    by = {}
    for ccy, sym in FX_MAJORS.items():
        if sym:
            ser = hist.get(sym) or []
            by[ccy] = dict(C._invert(ser) if C.fx_pair(sym)[0] == "USD" else ser)
    missing = [ccy for ccy, v in by.items() if len(v) < 2]
    if missing:
        raise C.CompositeError(f"No prices for {', '.join(missing)}: currency strength needs all eight majors. Press reload in a minute.")
    dates = sorted(set.intersection(*(set(v) for v in by.values())))
    if len(dates) < 2:
        raise C.CompositeError("The eight majors have no two closes on the same dates.")
    return dates, {"USD": [1.0] * len(dates), **{ccy: [v[d] for d in dates] for ccy, v in by.items()}}


def _own_strength(values, base, at):
    """{currency: change from base to at against the other seven, equally weighted, geometric}."""
    logs = {}
    for ccy, v in values.items():
        vb, va = C.num(v[base]), C.num(v[at])
        if not (vb is not None and va is not None and vb > 0 and va > 0):
            raise C.CompositeError(f"Invalid price series for {ccy} while computing currency strength.")
        logs[ccy] = math.log(va / vb)
    total, n = sum(logs.values()), len(logs) - 1
    return {ccy: math.exp(x - (total - x) / n) - 1 for ccy, x in logs.items()}


def fx_changes(period="1D"):
    """Every measure for the eight majors between the same two closes, plus the path from the first to the last."""
    period = str(period or "1D").upper()
    if period not in ("1D", "1W", "1M", "3M", "6M", "YTD", "1Y"):
        raise C.CompositeError(f"Unknown period {period}.")
    hist = C.history([s for s in FX_MAJORS.values() if s], dt.date.today() - dt.timedelta(days=410))          # as the board
    dates, values = C._usd_values(hist)
    last = len(dates) - 1
    if period == "1D":
        base = last - 1
    else:          # the last close at or before the horizon, counted from the last close (as the board does)
        ref = C.horizon_date(dt.date.fromisoformat(dates[last]), period).isoformat()
        base = max(bisect.bisect_right(dates, ref) - 1, 0)
    own = C._own_strength(values, base, last)
    rows = []
    for ccy, sym in FX_MAJORS.items():
        usd = values[ccy][last] / values[ccy][base] - 1 if (sym and values[ccy][base]) else None
        inverted = sym and C.fx_pair(sym)[0] == "USD"
        pair = (1 / (1 + usd) - 1) if (inverted and usd is not None and 1 + usd > 0) else usd
        rows.append({"ccy": ccy, "quote": C.fx_pair_label(sym) if sym else None, "own": own[ccy], "usd": usd,
                     "pair": pair})
    return {"rows": rows, "dates": dates[base:], "values": {ccy: v[base:] for ccy, v in values.items()}}


def fx_moves(period="1D", **_):
    """Bars: own strength beside the move against the dollar, with the pair as quoted under each currency."""
    res = C.fx_changes(period)
    rows = sorted(res["rows"], key=lambda r: r["own"])
    ys = [f"<b>{r['ccy']}</b><br>" + (f"{r['quote']} {r['pair'] * 100:+.2f}%" if r["quote"] else "no single pair") for r in rows]

    def bar(name, key, color):
        xs = [None if r[key] is None else round(r[key] * 100, 4) for r in rows]
        return {"type": "bar", "orientation": "h", "name": name, "y": ys, "x": xs, "marker": {"color": color},
                "text": ["" if x is None else f"{x:+.2f}%" for x in xs], "textposition": "outside", "cliponaxis": False,
                "textfont": {"color": C.INK["text2"], "size": 10}, "hovertemplate": "%{x:+.2f}%<extra>" + name + "</extra>"}

    data = [bar(USD_NAME, "usd", USD_COLOR), bar(OWN_NAME, "own", OWN_COLOR)]          # grouped bars stack upwards: own strength on top
    xs = [0.0] + [x for t in data for x in t["x"] if x is not None]
    pad = (max(xs) - min(xs) or 1) * 0.24          # room for the value written past the end of the longest bar, also in a narrow card
    lay = C._layout(True, hovermode="closest", barmode="group", bargap=0.28, bargroupgap=0.06)
    lay["margin"] = {"l": 8, "r": 12, "t": 28, "b": 52}
    lay["xaxis"].update({"ticksuffix": "%", "showgrid": True, "gridcolor": C.INK["grid"], "zeroline": True, "zerolinecolor": C.INK["muted"],
                         "range": [min(xs) - pad, max(xs) + pad],
                         "title": {"text": f"Right = stronger<br>{res['dates'][0]} to {res['dates'][-1]}, 17:00 New York", "font": {"size": 10}}})
    lay["yaxis"].update({"showgrid": False, "tickfont": {"color": C.INK["text2"], "size": 10}})
    lay["legend"]["traceorder"] = "reversed"
    return {"data": data, "layout": lay, "as_of": C.newest_date(res["dates"])}


def fx_trend(period="3M", **_):
    """One panel per major on one scale: own strength and the move against the dollar since the start of the period."""
    res = C.fx_changes(period)
    dates, values = res["dates"], res["values"]
    own = [C._own_strength(values, 0, i) for i in range(len(dates))]
    lay = C._layout(True)
    lay["margin"] = {"l": 40, "r": 12, "t": 46, "b": 28}
    lay["legend"]["y"] = 1.08
    lay["annotations"] = []
    data, every = [], [0.0]
    for i, row in enumerate(res["rows"]):
        ccy, col, top = row["ccy"], i % 4, i < 4
        n = str(i + 1) if i else ""
        lines = [(OWN_NAME, OWN_COLOR, [round(o[ccy] * 100, 3) for o in own])]
        if row["quote"]:
            lines.append((USD_NAME, USD_COLOR, [round((v / values[ccy][0] - 1) * 100, 3) for v in values[ccy]]))
        for name, color, ys in lines:
            every += ys
            data.append({"type": "scatter", "mode": "lines", "name": f"{ccy} {name}", "x": dates, "y": ys, "xaxis": "x" + n, "yaxis": "y" + n,
                         "showlegend": False, "line": {"width": 1.8, "color": color}, "hovertemplate": "%{y:+.2f}%<extra>" + f"{ccy} {name}" + "</extra>"})
        x0, y0 = col * 0.255, 0.56 if top else 0.0
        x1, y1 = x0 + 0.235, y0 + 0.36
        base = C._layout(False)
        lay["xaxis" + n] = {**base["xaxis"], "domain": [x0, x1], "anchor": "y" + n, "showticklabels": not top, "nticks": 3, "tickformat": "%b", "tickangle": 0}
        lay["yaxis" + n] = {**base["yaxis"], "domain": [y0, y1], "anchor": "x" + n, "showticklabels": not col, "ticksuffix": "%",
                            "zeroline": True, "zerolinecolor": C.INK["muted"]}
        title = f"<b>{ccy}</b> <span style='color:{OWN_COLOR}'>{row['own'] * 100:+.1f}%</span>"
        if row["quote"]:
            title += f" <span style='color:{USD_COLOR}'>{row['usd'] * 100:+.1f}%</span>"
        lay["annotations"].append({"text": title, "xref": "paper", "yref": "paper", "x": x0, "y": y1, "xanchor": "left", "yanchor": "bottom",
                                   "showarrow": False, "font": {"size": 11, "color": C.INK["text2"]}})
    lo, hi = min(every), max(every)
    pad = (hi - lo or 1) * 0.06
    for i in range(len(res["rows"])):
        lay["yaxis" + (str(i + 1) if i else "")]["range"] = [lo - pad, hi + pad]          # one scale, so panels compare
    # the key: the first panel with both lines lends its two traces to the legend
    for t, name in zip([t for t in data if t["name"].startswith("EUR ")], (OWN_NAME, USD_NAME)):
        t.update(showlegend=True, name=name)
    return {"data": data, "layout": lay}
