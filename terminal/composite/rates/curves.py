"""Yield curve comparison, table, and G10 global yields views."""

from __future__ import annotations

import datetime as dt
import math

import composite as C

def curves(countries, dates=None, us_source="auto"):
    """{country: {iso_date: [(maturity_years, rate_pct), ...]}} ; dates=None means latest.
    out["__src__"] = {country: source code actually used} (the U.S. falls back from the Fed route to FRED, or uses
    FRED when us_source="fred")."""
    countries = [c for c in dict.fromkeys(countries) if c in C.COUNTRIES]
    if not countries:
        raise C.CompositeError("Choose at least one country: " + ", ".join(C.COUNTRIES))
    dates = sorted(set(dates)) if dates else None
    us_source = "fred" if us_source == "fred" else "auto"
    key = ("curves", tuple(sorted(countries)), tuple(d.isoformat() for d in dates) if dates else None, us_source)

    def run():
        out, errors = {c: {} for c in countries}, []
        used = {c: C.COUNTRIES[c][1] for c in countries}

        def one(c):
            src = C.COUNTRIES[c][1]
            if src == "fed":
                if us_source != "fred":
                    why = "the data service's Treasury curve was empty"
                    try:
                        got = C._backend_curve(src, dates)
                        if got:
                            return got
                    except C.CompositeError as e:
                        why = f"the data service's Treasury curve did not answer ({C.kind_of(e).replace('_', ' ')})"
                    C.note_fallback("FRED's Treasury yields", why)
                used[c] = "fred"
                return C._nearest(C._src_fred_us(), dates)
            if src == "ecb":
                return C._backend_curve(src, dates)
            if src == "oecd":
                return C._nearest(C._src_oecd(c), dates, max_gap=45)
            return C._nearest(C.HIST_SOURCES[src](), dates)

        with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(one, c): c for c in countries}
            for f in C.cf.as_completed(futs):
                c = futs[f]
                try:
                    out[c] = {d: [tuple(p) for p in pts] for d, pts in (f.result() or {}).items()}
                except C.CompositeError as e:
                    errors.append(f"{C.COUNTRIES[c][0]}: {e}")
                    C.partial_from(e, C.COUNTRIES[c][0])     # the others are served; held a minute, not 15
        if not any(out.values()):
            raise C.CompositeError("No yield-curve data returned. " + "; ".join(errors))
        out["__src__"] = used
        return out

    return C.cached(key, 900, run)


def comparison_date(avail, target, latest, max_gap):
    """The curve date to show as "<horizon> ago": the available date closest to `target`, if within `max_gap` days and
    not the latest curve itself; else None (no comparison is better than one labelled with the wrong horizon)."""
    if not avail:
        return None
    t = target if isinstance(target, dt.date) else dt.date.fromisoformat(str(target))
    near = min(avail, key=lambda x: abs(dt.date.fromisoformat(x) - t))
    if near == latest or abs((dt.date.fromisoformat(near) - t).days) > max_gap:
        return None
    return near


def _max_gap(country):
    return 45 if C.COUNTRIES.get(country, (None, None))[1] == "oecd" else 20


def compare_target(country, latest, today, h):
    """The date "<h> ago" aims at. Monthly OECD curves count back from their own latest month: the October 2026 curve
    against October 2025, not against the month nearest today minus a year (2025-12-01 was picked; review F3).
    Daily curves count back from today, as before."""
    if C._max_gap(country) > 20 and latest:
        return C.horizon_date(dt.date.fromisoformat(str(latest)[:10]), h)
    return C.horizon_date(today, h)


def _us_fallback(got, source="auto"):
    """The footer note when the U.S. curve came from FRED although the Federal Reserve route was asked for (HANDOFF P2)."""
    return C.FALLBACK_US_CURVE if source != "fred" and (got or {}).get("__src__", {}).get("united_states") == "fred" else ""


def yield_curves(country="united_states,euro_area,united_kingdom,japan", compare="", source="auto", **_):
    """Yield-curve chart: one line per country (and per comparison date)."""
    countries = C._split(country)
    horizons = [h.upper() for h in C._split(compare)]
    today = dt.date.today()
    latest = C.curves(countries, us_source=source)
    past = {}
    if horizons:
        # monthly curves count back from their own latest month (compare_target): fetch those dates too
        want = sorted({C.compare_target(c, C._latest(latest.get(c, {}))[0], today, h) for c in countries if c in C.COUNTRIES for h in horizons} |
                      {C.horizon_date(today, h) for h in horizons})
        past = C.curves(countries, want, us_source=source)
    data, i, used, missing, kinds = [], 0, [], [], set()
    for c in countries:
        if c not in C.COUNTRIES:
            continue
        label, src = C.COUNTRIES[c][0], C.COUNTRIES[c][1]
        sparse = src == "oecd"
        d, pts = C._latest(latest.get(c, {}))
        if not pts:
            missing.append(label)
            continue
        code = latest["__src__"].get(c, src)
        used.append(C.curve_source(code, label))
        kinds.add(C.CURVE_KIND.get(code))
        tag = f" (monthly {d[:7]}: 3M and 10Y only)" if sparse else f" ({d}, {C.CURVE_KIND.get(code, '')})".replace(", )", ")")
        series = [(label + tag, pts, "solid")]
        for h in horizons:
            near = C.comparison_date(sorted(past.get(c, {})), C.compare_target(c, d, today, h), d, C._max_gap(c))
            if near:
                ppts = past[c][near]
                part = "" if sparse or len(ppts) >= len(pts) - 2 else " (" + ", ".join(f"{int(y)}Y" for y, _ in ppts) + " only)"
                series.append((f"{label} {h} ago ({near}){part}", ppts, "dot"))
        for name, pts, dash in series:
            if not pts or len(data) >= 10:
                continue
            col = C.PALETTE[i % len(C.PALETTE)] if dash == "solid" or len(countries) == 1 else C.PALETTE[(i - 1) % len(C.PALETTE)]
            if dash == "solid" or len(countries) == 1:
                i += 1
            data.append({"type": "scatter", "mode": "markers" if sparse else "lines+markers", "name": name, "x": [p[0] for p in pts], "y": [p[1] for p in pts],
                         "line": {"width": 2, "color": col, "dash": dash}, "marker": {"size": 9 if sparse else 6, "color": col, "symbol": "diamond" if sparse else "circle"},
                         "hovertemplate": "%{x}y: %{y:.3f}%<extra>" + name + "</extra>"})
    if not data:
        raise C.CompositeError("No curve data for: " + ", ".join(missing or countries) + ".")
    lay = C._layout(True, hovermode="closest")
    lay["xaxis"].update({"type": "log", "tickvals": [1 / 12, 0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30, 40], "ticktext": ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y", "40Y"],
                         "title": {"text": "Maturity", "font": {"size": 10}}})
    xs = [x for t in data for x in t["x"] if x and x > 0]
    if xs:     # a little room at both ends: autorange ended on 30 and the 30Y label was dropped at the edge (review F3)
        lay["xaxis"]["range"] = [math.log10(min(xs)) - 0.06, math.log10(max(xs)) + 0.06]
    lay["yaxis"]["ticksuffix"] = "%"
    lay["yaxis"]["title"] = {"text": "Yield", "font": {"size": 10}}
    notes = (["No data: " + ", ".join(missing)] if missing else []) + \
        (["Par and zero-coupon curves are not directly comparable"] if "zero-coupon" in kinds and kinds & {"par", "bond yields"} else [])
    if notes:
        lay.setdefault("annotations", []).append(C._note("; ".join(notes)))
    out = {"data": data, "layout": lay, "sources": C.uniq_sources(used)}
    if C._us_fallback(latest, source):
        out["fallback"] = C._us_fallback(latest, source)
    return out


def yield_curve_table(country="united_states", compare="1M,1Y", source="auto", **_):
    """Each selected country's curve by maturity (up to 4, in the order picked), today against earlier dates, with changes
    in basis points. Only the first country was shown before, so adding one to the tab's Country list left the table
    on the first (review F3); with several countries each row says its country."""
    cs = [c for c in C._split(country)][:4] or ["united_states"]
    bad = [c for c in cs if c not in C.COUNTRIES]
    if bad:
        raise C.CompositeError(f"No curve source for '{bad[0]}'. Available: {', '.join(C.COUNTRIES)}")
    horizons = [h.upper() for h in C._split(compare)]
    rows, sources, missing, fallback = [], [], [], ""
    for c in cs:
        try:
            r, s = C._curve_table(c, horizons, source)
        except C.CompositeError:
            missing.append(C.COUNTRIES[c][0])
            continue
        if c == "united_states" and source != "fred" and s.get("name") == "FRED":
            fallback = C.FALLBACK_US_CURVE
        rows += [dict({"country": C.COUNTRIES[c][0]}, **x) for x in r] if len(cs) > 1 else r
        sources.append(s)
    if not rows:
        raise C.CompositeError("No curve data for " + ", ".join(missing) + ".")
    out = {"results": rows, "sources": sources}
    if missing:
        out["note"] = "No curve data for " + ", ".join(missing) + "."
    if fallback:
        out["fallback"] = fallback
    return out


def _curve_table(c, horizons, source):
    today = dt.date.today()
    got = C.curves([c], us_source=source)
    d0, now = C._latest(got.get(c, {}))
    if not now:
        raise C.CompositeError(f"No curve data for {C.COUNTRIES[c][0]}.")
    used = got["__src__"].get(c, C.COUNTRIES[c][1])
    past = C.curves([c], [C.compare_target(c, d0, today, h) for h in horizons], us_source=source).get(c, {}) if horizons else {}
    cols = []
    for h in horizons:
        # within 20 days of the target (45 for monthly OECD data), and never the latest curve itself
        near = C.comparison_date(sorted(past), C.compare_target(c, d0, today, h), d0, C._max_gap(c))
        if near:
            cols.append((h, near, dict(past[near])))
    rows = []
    src = C.COUNTRIES[c][1]
    for my, rate in now:
        label = f"{int(round(my * 12))}M" if my < 1 else f"{int(my) if float(my).is_integer() else my}Y"
        row = {"maturity": label, "maturity_years": my, "yield": rate, "as_of": d0, "source": C.curve_label(used)}
        for h, near, pts in cols:
            # same maturity on the earlier date; Brazil's bonds have fixed maturity dates, so their curve is interpolated
            old = pts.get(my) if src != "tesouro" else C._interp(sorted(pts.items()), my)
            row[f"y_{h.lower()}"] = old
            row[f"chg_{h.lower()}"] = round((rate - old) * 100, 1) if old is not None else None
            row[f"d_{h.lower()}"] = near if old is not None else None      # the date actually compared against
        rows.append(row)
    return rows, C.curve_source(used, C.COUNTRIES[c][0])


G10_KEYS = ["united_states", "euro_area", "germany", "united_kingdom", "japan", "canada", "switzerland", "australia", "new_zealand", "sweden", "norway"]


def global_yields(countries="", **_):
    """Sovereign yields by country and maturity, with slope, 10Y changes and the policy rate. countries="g10" (or a
    list of keys) keeps those rows: the Overview card showed 42 rows and pushed Market News down to two headlines (review G2)."""
    def run():
        today = dt.date.today()
        keys = [k for k, v in C.COUNTRIES.items() if v[1] != "oecd"]          # the rest come from the OECD block below
        latest = C.curves(keys)
        past = {}
        for h in ("1M", "1Y"):        # one date per call: each source then returns its curve closest to that date
            try:
                for c, by_d in C.curves(keys, [C.horizon_date(today, h)]).items():
                    past.setdefault(c, {}).update(by_d)
            except C.CompositeError as e:
                C.partial_from(e, f"curves of {h} ago (change columns)")
        try:
            pol = C._policy_series()             # daily where current: a decision this month is the rate shown
        except C.CompositeError as e:
            C.partial_from(e, "policy rates (BIS)")
            pol = {}
        rows = []
        for c in keys:
            label, src, _, iso = C.COUNTRIES[c]
            d, pts = C._latest(latest.get(c, {}))
            if not pts:
                continue
            pick = C._interp if src == "tesouro" else C._pick
            src = latest.get("__src__", {}).get(c, src)
            row = {"country": label, "key": c, "as_of": d, "source": C.curve_label(src), "_src": src}
            for field, years, _ in C.TENORS:
                row[field] = pick(pts, years)
            if row.get("y30") is None and src == "boc":
                row["y30"] = C._pick(pts, 30)
            if row.get("y10") is not None and row.get("y2") is not None:
                row["slope_2s10s"] = round((row["y10"] - row["y2"]) * 100)
            for h, field in (("1M", "y10_chg_1m"), ("1Y", "y10_chg_1y")):
                near = C.comparison_date(sorted(past.get(c, {})), C.horizon_date(today, h), d, 20) if row.get("y10") is not None else None
                old = pick(sorted(past[c][near]), 10) if near else None
                if old is not None:
                    row[field] = round((row["y10"] - old) * 100)
            ser = pol.get(iso) if iso else None
            if ser:
                row["policy"] = ser[-1][1]
            row["age"] = (today - dt.date.fromisoformat(d)).days if d else 9999
            row["freq"] = "daily" + (" (interpolated)" if src == "tesouro" else "")
            rows.append(row)
        # Countries with no fresh daily curve: fall back to the monthly OECD 3M / 10Y rates.
        try:
            rbc = C.rates_by_country()
            oecd = {r["country"]: r for r in rbc["results"]}
            monthly_src = [s for s in rbc.get("sources", []) if "immediate" not in s["detail"].lower() and "overnight" not in s["detail"].lower()]
        except C.CompositeError:
            oecd, monthly_src = {}, []
        alias = {"Korea": "south_korea", "China (People's Republic of)": "china", "Euro area (20 countries)": "euro_area", "Czechia": "czech_republic", "Türkiye": "turkey"}
        by_key = {r["key"]: r for r in rows}
        for name, o in oecd.items():
            key = alias.get(name, name.lower().replace(" ", "_"))
            have = by_key.get(key)
            if have and have["age"] <= 45:
                continue
            if o.get("y10") is None:
                continue
            month = str(o.get("as_of") or "")[:7]             # YYYY-MM: aged from the first of that month, never assumed recent
            try:
                age = (today - dt.date.fromisoformat(month + "-01")).days if len(month) == 7 else 9999
            except ValueError:
                age = 9999
            new = {"country": C.COUNTRIES[key][0] if key in C.COUNTRIES else name, "key": key, "as_of": month, "m3": o.get("m3"), "y10": o.get("y10"),
                   "y10_chg_1m": o.get("y10_chg_1m"), "y10_chg_1y": o.get("y10_chg_1y"), "freq": "monthly", "age": age, "source": C.curve_label("oecd"),
                   "policy": have.get("policy") if have else None, "_m3_kind": "interbank"}
            if have:
                rows[rows.index(have)] = new
            else:
                iso = "XM" if name in C.EURO_MEMBERS else next((k for k, v in C.ISO_NAMES.items() if v == name), None)
                if iso and pol.get(iso):
                    new["policy"] = pol[iso][-1][1]
                rows.append(new)
        rows = [r for r in rows if r["age"] <= 400]
        order = ["united_states", "euro_area", "germany", "france", "italy", "spain", "netherlands", "united_kingdom", "switzerland", "japan", "canada", "australia", "new_zealand",
                 "sweden", "norway", "denmark", "china", "south_korea", "india", "brazil", "mexico", "south_africa", "poland"]
        rows.sort(key=lambda r: (order.index(r["key"]) if r["key"] in order else len(order), r["country"]))
        for r in rows:
            # a monthly average is normally 1-2 months old when published: "older data" after 75 days, daily after 45
            notes = ["older data"] if r.pop("age") > (75 if r.get("freq") == "monthly" else 45) else []
            if r.get("_m3_kind") == "interbank" and r.get("m3") is not None:
                notes.append("3M = interbank rate")
            r["note"] = "; ".join(notes)
        if not rows:
            raise C.CompositeError("No sovereign yield data returned.")
        used = [C.curve_source(c) for c in dict.fromkeys(r.pop("_src") for r in rows if r.get("_src"))]
        if any(r.get("freq") == "monthly" for r in rows):
            used += monthly_src
        if any(r.get("policy") is not None for r in rows):
            used.append(C.BIS_SOURCE)
        for r in rows:
            r.pop("_src", None)
        out = {"results": rows, "sources": C.uniq_sources(used)}
        if C._us_fallback(latest):
            out["fallback"] = C._us_fallback(latest)
        return out
    out = C.cached(("global_yields",), 900, run)
    want = G10_KEYS if str(countries or "").strip().lower() == "g10" else C._split(countries)
    if want:
        out = dict(out, results=[r for r in out["results"] if r["key"] in want])
    return out
