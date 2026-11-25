"""Macro chart, seasonal alignment, country monitor, and IMF CPI history."""

from __future__ import annotations

import datetime as dt
import hashlib

import composite as C

def macro_chart(preset="credit_spreads", series=None, since="2015", transform=None, **kw):
    """Curated economic chart: a named set of series (FRED, EIA, Yahoo, PortWatch, GPR), or any FRED series IDs.
    `range` (1Y, 3Y, 5Y, 10Y, 20Y, MAX) from the card's control strip wins over `since` (a year)."""
    custom = C._split(series)
    if custom:
        title, suffix, default_tf, items = "Custom series", "", None, [(None, s if ":" in s else s.upper(), 1) for s in custom[:8]]
    elif preset in C.MACRO_SETS:
        title, suffix, default_tf, items = C.MACRO_SETS[preset]
    else:
        raise C.CompositeError(f"Unknown chart '{preset}'.")
    tf = (transform or default_tf or "level").lower()
    tf = tf if tf in ("level", "yoy", "index", "seasonal") else "level"
    rng = str(kw.get("range") or "").upper() or None
    show_from = C.range_start(rng, since)
    fetch_from = show_from
    if tf == "yoy":
        fetch_from = f"{int(show_from[:4]) - 1}{show_from[4:]}"
    elif tf == "seasonal":                       # the current year against the five before it, whatever the range
        fetch_from = min(show_from, f"{dt.date.today().year - 6}-01-01")
    data, meta = C._series([i[1] for i in items], fetch_from)
    if custom and items and all("percent" in str(meta.get(i[1], {}).get("units") or "").lower() for i in items):
        suffix = "%"                            # custom FRED rates (DGS10, UNRATE): their year-over-year change is in bp
    unit, level_title, yoy_diff = C.transform_unit(suffix, default_tf, tf, [meta.get(i[1], {}).get("units") for i in items])
    first = next((data.get(i[1]) for i in items if data.get(i[1])), [])
    freq = C.freq_of(first)
    raw = [v for i in items for _, v in data.get(i[1], [])]
    positive = bool(raw) and min(raw) > 0
    rate_level = C.yoy_difference(suffix, default_tf) is not None      # rates, ratios, spreads: yoy as a difference, any sign
    valid = ["level"] + (["yoy"] if positive or rate_level or default_tf == "yoy" else []) + (["index"] if positive else []) \
        + (["seasonal"] if len(items) == 1 and freq in ("daily", "weekly", "monthly") else [])
    controls = {"freq": freq, "ranges": C.FREQ_RANGES[freq], "range": rng, "since": since, "transforms": valid, "transform": tf}
    if tf == "seasonal":
        label, sid, mult = items[0]
        out = C._seasonal(label or sid, [[d, v * mult] for d, v in data.get(sid, [])], unit, source_id=sid)
        controls["ranges"] = []                 # a seasonal chart always shows one year against the previous five
        obs = data.get(sid) or []               # dated by the last observation (the chart's x is a template year)
        return dict(out, sources=[C.series_source(sid, meta.get(sid))], controls=controls,
                    **({"as_of": obs[-1][0]} if obs else {"as_of_na": f"No observations for {sid}."}))
    traces, table, forecast_shown, last_actual = [], {}, False, []
    for n, (label, sid, mult) in enumerate(items):
        pts = [[d, v * mult] for d, v in data.get(sid, [])]
        if tf == "yoy":
            pts = C._yoy(pts, diff=yoy_diff)
        elif tf == "index" and pts:
            base = next((v for d, v in pts if d >= show_from and v), None)
            pts = [[d, v / base * 100] for d, v in pts] if base else pts
        pts = [p for p in pts if p[0] >= show_from]
        if not pts:
            continue
        name = label or (meta.get(sid, {}).get("title") or sid)[:60]
        # EIA STEO series run to the end of next year: from the current month on they are forecasts (dashed), and the
        # legend shows the latest actual month, never the last forecast
        actual, ahead = (pts, [])
        if str(sid).startswith("EIA:STEO."):
            cut = C._month_start()
            actual, ahead = [p for p in pts if p[0] < cut], [p for p in pts if p[0] >= cut]
            forecast_shown = forecast_shown or bool(ahead and actual)
        ref = actual or pts
        last = ref[-1][1]
        if actual:
            last_actual.append(actual[-1][0])          # the answer is dated by observations, never by a forecast month
        # counts in thousands (JOLTS 7,523k) are whole numbers: no ".00" (review A5 printed "7,523.00k")
        dec = 0 if unit.strip() == "k" else 2
        label_txt = f"{name}  {last:,.0f} bp" if unit.strip() == "bp" else f"{name}  {last:,.2f} pp" if unit.strip() == "pp" else \
            f"{name}  {last:,.{dec}f}{unit.strip() if unit.strip() in ('%', 'k') else ''}"
        if ahead and actual:
            label_txt += f" (latest actual {actual[-1][0][:7]})"
        color = C.PALETTE[n % len(C.PALETTE)]
        traces.append({"type": "scatter", "mode": "lines", "name": label_txt, "x": [p[0] for p in ref], "y": [round(p[1], 4) for p in ref],
                       "line": {"width": 2, "color": color}, "connectgaps": True, "hovertemplate": f"%{{y:,.{dec}f}}" + unit + "<extra>" + name + "</extra>"})
        if ahead and actual:
            joined = actual[-1:] + ahead
            traces.append({"type": "scatter", "mode": "lines", "name": f"{name} (EIA forecast)", "showlegend": False, "x": [p[0] for p in joined],
                           "y": [round(p[1], 4) for p in joined], "line": {"width": 2, "color": color, "dash": "dash"}, "connectgaps": True,
                           "hovertemplate": f"%{{y:,.{dec}f}}" + unit + "<extra>" + name + " (forecast)</extra>"})
        for d, v in pts:
            table.setdefault(d, {"date": d})[name] = round(v, 4)
    if not traces:
        raise C.CompositeError("No observations in the selected period.")
    lay = C._layout(len(traces) > 1)
    lay["yaxis"]["ticksuffix"] = unit
    if unit.strip():     # a suffix already names the unit: no SI letter on top (JOLTS 6,000 thousand read "6kk"; review A5)
        lay["yaxis"]["tickformat"] = ",~f"
    if not level_title and not unit.strip():     # no unit suffix: say on the axis what the numbers are
        units = {(meta.get(i[1], {}).get("units") or "").strip() for i in items} - {""}
        level_title = "Index (start = 100)" if tf == "index" else units.pop() if len(units) == 1 else None
    if level_title:
        lay["yaxis"]["title"] = {"text": level_title[:60], "font": {"size": 10}}
    if custom and not transform:
        units = {(meta.get(i[1], {}).get("units") or "") for i in items}
        if len(units) > 1:
            lay["annotations"] = [C._note("Series use different units; switch to Indexed to compare.")]
    if forecast_shown:
        lay.setdefault("annotations", []).append(C._note("Dashed: EIA forecast"))
    ice = [d for i in items if str(i[1]).startswith("BAML") for d, _ in data.get(i[1], [])[:1]]
    first = min(ice) if ice else ""
    if first and first[:10] > (dt.date.fromisoformat(show_from[:10]) + dt.timedelta(days=365)).isoformat():
        # FRED serves ICE BofA indices for the last three years only (probed 2026-11-18: BAMLH0A0HYM2 from 2023-11-20):
        # "Max" cannot reach 2024's start, and the chart says why (review F3)
        lay.setdefault("annotations", []).append(C._note(f"ICE BofA data on FRED: last 3 years only (from {first[:10]})"))
    used = C.uniq_sources(C.series_source(i[1], meta.get(i[1])) for i in items)
    if str(kw.get("shade") or "").lower() == "rec":
        xs = [x for t in traces for x in t["x"]]
        used += C.add_recession_shading(lay, min(xs), max(xs))
    out = {"data": traces, "layout": lay, "results": [table[d] for d in sorted(table, reverse=True)],
           "sources": used, "controls": controls, "as_of": C.newest_date(last_actual)}
    cut = [meta.get(i[1], {}).get("truncated") for i in items if meta.get(i[1], {}).get("truncated")]
    if cut:
        out["truncated"] = "; ".join(dict.fromkeys(cut))
    return out


def seasonal_bucket(iso, monthly):
    """(year, bucket) for a seasonal chart: (calendar year, month) for monthly data, else (ISO year, ISO week) with
    week 53 folded into 52. Dec 29-31 can belong to week 1 of the next ISO year and Jan 1-3 to week 52/53 of the
    previous one, so the ISO year (not the calendar year) must key the week."""
    d = dt.date.fromisoformat(str(iso)[:10])
    if monthly:
        return d.year, d.month
    iy, iw, _ = d.isocalendar()
    return iy, min(iw, 52)


def _seasonal(name, pts, unit, years=5, source_id=None):
    """Current year against last year and the previous five-year range, by week (or month)."""
    if len(pts) < 30:
        raise C.CompositeError("Not enough history for a seasonal view.")
    gaps = sorted((dt.date.fromisoformat(b[0]) - dt.date.fromisoformat(a[0])).days for a, b in zip(pts[-40:], pts[-39:]))
    monthly = gaps[len(gaps) // 2] > 20

    def xdate(b):
        return f"2000-{b:02d}-15" if monthly else (dt.date(2000, 1, 3) + dt.timedelta(weeks=b - 1)).isoformat()

    by_year = {}
    for d, v in pts:                                   # in date order, so a week keeps its last value
        y, b = C.seasonal_bucket(d, monthly)
        by_year.setdefault(y, {})[b] = v
    cur = max(by_year)
    prev = [y for y in range(cur - years, cur) if y in by_year]
    if not prev:
        raise C.CompositeError("Not enough history for a seasonal view.")
    xs = sorted({b for y in prev for b in by_year[y]})
    band = [(b, [by_year[y][b] for y in prev if b in by_year[y]]) for b in xs]
    lo = [min(v) for _, v in band]; hi = [max(v) for _, v in band]; avg = [sum(v) / len(v) for _, v in band]
    X = [xdate(b) for b in xs]
    rng = f"{prev[0]}–{prev[-1]}"
    traces = [
        {"type": "scatter", "mode": "lines", "x": X, "y": hi, "line": {"width": 0}, "showlegend": False, "hoverinfo": "skip", "name": "max"},
        {"type": "scatter", "mode": "lines", "x": X, "y": lo, "line": {"width": 0}, "fill": "tonexty", "fillcolor": "rgba(137,135,129,0.22)", "name": f"5-year range ({rng})",
         "hovertemplate": "%{y:,.1f}" + unit + "<extra>5-yr low</extra>"},
        {"type": "scatter", "mode": "lines", "x": X, "y": [round(v, 3) for v in avg], "line": {"width": 1.5, "dash": "dot", "color": C.INK["text2"]}, "name": "5-year average",
         "hovertemplate": "%{y:,.1f}" + unit + "<extra>5-yr average</extra>"},
    ]
    for y, color, width in ((cur - 1, C.PALETTE[1], 1.5), (cur, C.PALETTE[0], 2.5)):
        if y in by_year:
            bs = sorted(by_year[y])
            last = by_year[y][bs[-1]]
            traces.append({"type": "scatter", "mode": "lines", "x": [xdate(b) for b in bs], "y": [by_year[y][b] for b in bs], "line": {"width": width, "color": color},
                           "name": f"{y}  {last:,.1f}{unit.strip() if len(unit.strip()) <= 2 else ''}", "hovertemplate": "%{y:,.1f}" + unit + f"<extra>{y}</extra>"})
    lay = C._layout(True)
    lay["xaxis"].update({"type": "date", "tickformat": "%b", "range": ["2000-01-01", "2000-12-31"], "dtick": "M1"})
    lay["yaxis"]["ticksuffix"] = unit
    rows = []
    for b, vals in band:
        rows.append({"period": ("Month " if monthly else "Week ") + str(b), f"{cur}": by_year.get(cur, {}).get(b), f"{cur - 1}": by_year.get(cur - 1, {}).get(b),
                     "avg_5y": round(sum(vals) / len(vals), 3), "min_5y": min(vals), "max_5y": max(vals)})
    return {"data": traces, "layout": lay, "results": rows}


# ---------------------------------------------------------------- country monitor (Bloomberg ECST-style actuals, no consensus)
MONITOR = ["united_states", "euro_area", "germany", "france", "italy", "spain", "united_kingdom", "japan", "china", "canada", "india", "brazil", "south_korea",
           "australia", "mexico", "switzerland", "sweden"]

MONITOR_LABELS = {"euro_area": "Euro area"}


def _imf_cpi(keys, start):
    """{country key: [[date, yoy %], ...]} consumer-price inflation from the IMF (the euro area only exists as the harmonised index)."""
    keys = sorted(set(keys))

    def run():
        out = {}
        plain = [k for k in keys if k != "euro_area"]
        calls = ([dict(country=",".join(plain))] if plain else []) + ([dict(country="euro_area", harmonized="true")] if "euro_area" in keys else [])
        for kw in calls:
            try:
                rows = C.api(C.V5, "economy/cpi", provider="imf", start_date=start, **kw) or []
            except C.CompositeError as e:
                C.partial_from(e, "IMF consumer prices" + (" (euro area)" if kw.get("harmonized") else ""))
                continue
            names = kw["country"].split(",")
            for r in rows:
                v = r.get("value")
                try:
                    vf = float(v)
                except (TypeError, ValueError):
                    continue
                if C.num(vf) is None:
                    continue
                key = C._imf_key(r.get("country"), r.get("country_code"), names)
                if key:
                    out.setdefault(key, []).append([str(r.get("date"))[:10], round(vf * 100, 3)])
        for k in out:
            out[k].sort()
        if not out:
            raise C.CompositeError("IMF returned no consumer-price data.")
        return out
    return C.cached_disk("imf_cpi_" + hashlib.sha1(",".join(keys).encode()).hexdigest()[:10] + "_" + start[:4], 12 * 3600, run)


IMF_ISO3 = {"united_states": "USA", "euro_area": "G163", "germany": "DEU", "france": "FRA", "italy": "ITA", "spain": "ESP", "united_kingdom": "GBR", "japan": "JPN", "china": "CHN",
            "canada": "CAN", "india": "IND", "brazil": "BRA", "south_korea": "KOR", "australia": "AUS", "mexico": "MEX", "switzerland": "CHE", "sweden": "SWE"}


def _imf_key(name, code, asked):
    for k in asked:
        if IMF_ISO3.get(k) == code or (name and C._oecd_key(name.split(",")[0]) == k):
            return k
    if len(asked) == 1:
        return asked[0]
    return None


def _oecd_all(path, start):
    """One OECD request for every country (kept 12 hours on disk): {oecd country name: [[date, value], ...]}."""
    def run():
        rows = C.api(C.V5, path, provider="oecd", country="all", start_date=start, end_date=dt.date.today().isoformat())
        out = {}
        for r in rows or []:
            try:
                vf = float(r.get("value"))
            except (TypeError, ValueError):
                continue
            if C.num(vf) is not None:
                out.setdefault(str(r.get("country")).replace("’", "'"), []).append([str(r.get("date"))[:10], vf])
        for k in out:
            out[k].sort()
        if not out:
            raise C.CompositeError("OECD returned no data.")
        return out
    return C.cached_disk("oecd_" + path.replace("/", "_") + "_" + start[:4], 12 * 3600, run)


def _oecd_for(series, key):
    by = {C._oecd_key(n): n for n in series}
    name = by.get(C.OECD_ALIAS.get(key, key)) or by.get(key)
    return series.get(name) or []


def country_monitor(**_):
    """Latest actuals by country: real GDP growth, inflation, unemployment, policy rate, 3-month and 10-year rates."""
    def run():
        start = f"{dt.date.today().year - 2}-01-01"
        gdp, une = (C._safe(C._oecd_all, p, start, missing=label) or {}
                    for p, label in (("economy/gdp/real", "OECD real GDP"), ("economy/unemployment", "OECD unemployment")))
        cpi = C._safe(C._imf_cpi, MONITOR, start, missing="IMF consumer prices") or {}
        pol = C._safe(C._policy_series, missing="policy rates (BIS)") or {}
        gy = {r["key"]: r for r in (C._safe(C.global_yields, missing="3-month and 10-year rates") or {}).get("results", [])}
        rows, dates = [], []                              # dates: every data point shown, for the answer's as_of
        for k in MONITOR:
            label, _, _, iso = C.COUNTRIES.get(k, (k.replace("_", " ").title(), None, None, None))
            row = {"country": MONITOR_LABELS.get(k, label), "key": k}
            g = C._oecd_for(gdp, k)
            g5, g1 = (C.num(g[-5][1]), C.num(g[-1][1])) if len(g) >= 5 else (None, None)
            if g5 and g1 is not None:
                row["gdp_yoy"], row["gdp_q"] = C.banded("gdp_yoy_pct", g1 / g5 - 1, k), C._quarter(g[-1][0])     # +-30%
                dates.append(g[-1][0])
            c = cpi.get(k) or []
            if c:
                row["cpi_yoy"], row["cpi_m"] = c[-1][1] / 100, c[-1][0][:7]
                dates.append(c[-1][0])
            u = C._oecd_for(une, k)
            if u:
                row["unemp"], row["unemp_m"] = u[-1][1], u[-1][0][:7]
                dates.append(u[-1][0])
            ser = pol.get(iso) if iso else None
            if ser:
                row["policy"], row["policy_d"] = ser[-1][1], str(ser[-1][0])[:10]      # the rate and its BIS series date
                dates.append(ser[-1][0])
            y = gy.get(k)
            if y:
                row["m3"], row["y10"], row["rates_as_of"], row["rates_src"] = y.get("m3"), y.get("y10"), y.get("as_of"), y.get("freq")
                dates.append(y.get("as_of"))
                if y.get("_m3_kind") == "interbank":
                    row["rates_src"] = "monthly (3M interbank)"
                elif row["m3"] is not None and row["y10"] is not None:          # a sovereign slope needs a bill yield, not an interbank rate
                    row["slope"] = round((row["y10"] - row["m3"]) * 100)
            rows.append(row)
        newest = C.newest_date(str(d) for d in dates if d)
        return {"results": rows, **({"as_of": newest} if newest else {"as_of_na": "No country figure answered just now."})}
    return C.cached(("country_monitor",), 1800, run)


IMF_CPI_SOURCE = C.src("IMF", "Consumer prices, year over year (euro area: HICP)", "https://data.imf.org/")


def cpi_history(country="united_states", start="2015", **kw):
    """Consumer-price inflation (year over year) for the countries you pick (IMF; the euro area is the harmonised index)."""
    keys = [k for k in C._split(country) if k][:8] or ["united_states"]
    start_iso = C.range_start(kw.get("range"), start)
    data = C._imf_cpi(keys, f"{max(start_iso[:4], '1990')}-01-01")
    traces, missing = [], []
    for i, k in enumerate(keys):
        pts = [p for p in data.get(k) or [] if p[0] >= start_iso]
        name = MONITOR_LABELS.get(k) or (C.COUNTRIES[k][0] if k in C.COUNTRIES else k.replace("_", " ").title())
        if not pts:
            missing.append(name)
            continue
        traces.append({"type": "scatter", "mode": "lines", "name": f"{name}  {pts[-1][1]:.1f}% ({pts[-1][0][:7]})", "x": [p[0] for p in pts], "y": [p[1] for p in pts],
                       "line": {"width": 2, "color": C.PALETTE[len(traces) % len(C.PALETTE)]}, "hovertemplate": "%{y:.1f}%<extra>" + name + "</extra>"})
    if not traces:
        raise C.CompositeError("IMF has no consumer-price series for: " + ", ".join(missing))
    lay = C._layout(len(traces) > 1)
    lay["yaxis"]["ticksuffix"] = "%"
    if missing:
        lay["annotations"] = [C._note("No data: " + ", ".join(missing))]
    return {"data": traces, "layout": lay, "sources": [IMF_CPI_SOURCE], "controls": {"freq": "monthly", "ranges": C.MONTHLY_RANGES, "range": kw.get("range")}}
