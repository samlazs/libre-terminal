"""OECD and FRED MEI interest-rate history and cross-country rate tables."""

from __future__ import annotations

import datetime as dt
import re

import composite as C

def _oecd_series(duration):
    """{country: [[iso_date, rate_pct], ...]} since 2000 for every OECD country.

    OECD limits requests per hour, and this data is monthly, so it is fetched once per
    duration and kept on disk for 12 hours. Every rates view below reads from this copy.
    """
    def run():
        # end_date matters: without it OECD answers "all countries" with only the last ~15 months
        rows = C.api(C.V5, "economy/interest_rates", provider="oecd", country="all", duration=duration, start_date="2000-01-01", end_date=dt.date.today().isoformat())
        out = {}
        for r in rows or []:
            if r.get("value") is not None:
                out.setdefault(str(r.get("country")).replace("’", "'"), []).append([str(r.get("date"))[:10], round(float(r["value"]) * 100, 4)])
        for k in out:
            out[k].sort()
        if not out:
            raise C.CompositeError("OECD returned no interest-rate data.")
        return out
    return C.cached_disk(f"oecd_rates_{duration}", 12 * 3600, run)


def _oecd_latest(duration):
    try:
        return C._oecd_series(duration)
    except C.CompositeError as e:
        C.partial_from(e, f"OECD {duration} interest rates")
        return {}


OECD_ALIAS = {"south_korea": "korea", "euro_area": "euro_area", "czech_republic": "czechia", "slovakia": "slovak_republic", "turkey": "türkiye"}

DURATIONS = {"immediate": "Overnight", "short": "3-month", "long": "10-year"}


def _oecd_key(name):
    return re.sub(r"\s*\(.*?\)", "", name).strip().lower().replace(" ", "_")


# FRED republishes the OECD's monthly interest-rate series (Main Economic Indicators) under fixed ids: the same numbers,
# without OECD's request limit. Used when OECD refuses, or when the card's source is set to FRED.
FRED_MEI = {"united_states": "US", "euro_area": "EZ", "germany": "DE", "france": "FR", "italy": "IT", "spain": "ES", "united_kingdom": "GB", "japan": "JP",
            "canada": "CA", "australia": "AU", "south_korea": "KR", "switzerland": "CH", "sweden": "SE", "norway": "NO", "denmark": "DK", "netherlands": "NL",
            "belgium": "BE", "austria": "AT", "portugal": "PT", "ireland": "IE", "greece": "GR", "finland": "FI", "poland": "PL", "czech_republic": "CZ",
            "hungary": "HU", "new_zealand": "NZ", "mexico": "MX", "chile": "CL", "colombia": "CO", "israel": "IL", "south_africa": "ZA", "india": "IN"}

FRED_MEI_PREFIX = {"long": "IRLTLT01", "short": "IR3TIB01", "immediate": "IRSTCI01"}


def oecd_source(duration):
    return C.src("OECD", f"{DURATIONS.get(duration, duration)} interest rates (Main Economic Indicators), monthly", "https://data-explorer.oecd.org/")


def _fred_mei(keys, duration, start):
    """{country key: [[date, value]]} from FRED's copies of the OECD series, one cached request per series."""
    want = {k: f"{FRED_MEI_PREFIX[duration]}{FRED_MEI[k]}M156N" for k in keys if k in FRED_MEI}

    def one(item):
        k, sid = item
        try:
            ser, _m = C._fred([sid], start)
            return k, sid, ser.get(sid) or []
        except C.CompositeError:
            return k, sid, []
    out = {}
    with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
        for k, sid, pts in ex.map(one, want.items()):
            if pts:
                out[k] = (sid, pts)
    return out


FALLBACK_OECD = "OECD did not answer: FRED's copies of the same OECD series are shown"

FALLBACK_US_CURVE = "U.S. curve: the Federal Reserve route did not answer, FRED's H.15 constant maturities are shown"


def rate_history(country="united_states", duration="long", start="2000", source="auto", **kw):
    """Monthly rate history for the chosen countries: OECD overnight, 3-month or 10-year rates, or FRED's copies of the same
    series (source="fred"; with "auto" FRED fills in when OECD refuses or lacks a country)."""
    duration = duration if duration in DURATIONS else "long"
    source = source if source in ("oecd", "fred") else "auto"
    start = C.range_start(kw.get("range"), start, "2000")
    series, oecd_err = {}, None
    if source != "fred":
        try:
            series = C._oecd_series(duration)
        except C.CompositeError as e:
            if source == "oecd":
                raise
            oecd_err = e
    by_key = {C._oecd_key(n): n for n in series}
    keys = [c.strip().lower().replace(" ", "_") for c in C._split(country)[:8]]
    found, need_fred = {}, []
    for key in keys:
        name = by_key.get(OECD_ALIAS.get(key, key)) or by_key.get(key)
        pts = [p for p in series.get(name, []) if p[0] >= start] if name else []
        if pts:
            found[key] = (re.sub(r"\s*\(.*?\)", "", name), pts, "oecd")
        elif source != "oecd":
            need_fred.append(key)
    fred_ids = []
    if need_fred:
        for key, (sid, pts) in C._fred_mei(need_fred, duration, start).items():
            pts = [p for p in pts if p[0] >= start]
            if pts:
                found[key] = (C.COUNTRIES[key][0] if key in C.COUNTRIES else key.replace("_", " ").title(), pts, "fred")
                fred_ids.append(sid)
    traces, missing = [], []
    # the legend shows each series' last value: one that stopped (Russia's 10Y ends 2018-06) says when, as in rates_by_country
    lasts = [s[-1][0][:10] for s in series.values() if s] + [v[1][-1][0][:10] for v in found.values()]
    cutoff = C.minus_months(dt.date.fromisoformat(max(lasts)), STALE_MONTHS).isoformat() if lasts else ""
    for key in keys:
        if key not in found:
            missing.append(C.COUNTRIES[key][0] if key in C.COUNTRIES else key)
            continue
        label, pts, _s = found[key]
        i = len(traces)
        ended = f" (last {pts[-1][0][:7]})" if pts[-1][0][:10] < cutoff else ""
        traces.append({"type": "scatter", "mode": "lines", "name": f"{label}  {pts[-1][1]:.2f}%{ended}", "x": [p[0] for p in pts], "y": [p[1] for p in pts],
                       "line": {"width": 2, "color": C.PALETTE[i % len(C.PALETTE)]}, "hovertemplate": "%{y:.2f}%<extra>" + label + "</extra>"})
    if not traces:
        if oecd_err and not need_fred:
            raise oecd_err
        raise C.CompositeError(f"No {DURATIONS[duration]} rate series for: " + ", ".join(missing) + ". OECD and FRED cover OECD members plus a few partners.")
    lay = C._layout(len(traces) > 1)
    lay["yaxis"]["ticksuffix"] = "%"
    if missing:
        lay["annotations"] = [C._note("No series: " + ", ".join(missing))]
    used = []
    if any(v[2] == "oecd" for v in found.values()):
        used.append(C.oecd_source(duration))
    for sid in fred_ids:
        used.append(C.src("FRED", f"{sid} (OECD series republished by FRED)" + (" — OECD did not answer" if oecd_err else ""), f"https://fred.stlouisfed.org/series/{sid}"))
    out = {"data": traces, "layout": lay, "sources": used, "controls": {"freq": "monthly", "ranges": C.MONTHLY_RANGES, "range": kw.get("range")}}
    if oecd_err and fred_ids:                     # HANDOFF P2: the footer says the source changed, not only the source panel
        out["fallback"] = FALLBACK_OECD
    return out


def _month_ago(series, months, tolerance=20):
    """Value of a monthly [[iso, v], ...] series `months` before its last point (None if no point within tolerance days)."""
    if len(series) < 2:
        return None
    want = C.minus_months(dt.date.fromisoformat(series[-1][0][:10]), months)
    best = min(series[:-1], key=lambda p: abs((dt.date.fromisoformat(p[0][:10]) - want).days))
    return best[1] if abs((dt.date.fromisoformat(best[0][:10]) - want).days) <= tolerance else None


STALE_MONTHS = 3         # a monthly rate counts as current only if its last month is within 3 months of the newest one

LEG_NAMES = {"overnight": "overnight", "m3": "3M", "y10": "10Y"}


def _rate_rows(imm, short, long_):
    """Rows of rates_by_country from {country: [[iso, pct], ...]} per duration (OECD or FRED's copies).

    OECD keeps publishing countries whose series stopped years ago (Russia's 10Y ends 2018-06, Sweden's overnight
    2020-10, Türkiye's 3M 2008-04): a leg whose last month is more than STALE_MONTHS before the newest month of any
    country is blank, and the note says what it was and when. The slope needs both legs from months at most 45 days
    apart; 10Y changes need a current 10Y. A country with no current leg is not listed."""
    lasts = [s[-1][0][:10] for d in (imm, short, long_) for s in d.values() if s]
    if not lasts:
        return []
    cutoff = C.minus_months(dt.date.fromisoformat(max(lasts)), STALE_MONTHS).isoformat()
    rows = []
    for c in sorted(set(imm) | set(short) | set(long_)):
        legs = {"overnight": imm.get(c), "m3": short.get(c), "y10": long_.get(c)}
        fresh = {k: s for k, s in legs.items() if s and s[-1][0][:10] >= cutoff}
        if not fresh:
            continue
        row = {"country": c}
        for k in legs:
            row[k] = fresh[k][-1][1] if k in fresh else None
        row["as_of"] = min(s[-1][0][:7] for s in fresh.values())
        stale = [f"{LEG_NAMES[k]} (last {s[-1][0][:7]})" for k, s in legs.items() if s and k not in fresh]
        notes = ["stale, not shown: " + ", ".join(stale)] if stale else []
        l, s = fresh.get("y10"), fresh.get("m3")
        if l and s:
            if abs((dt.date.fromisoformat(l[-1][0][:10]) - dt.date.fromisoformat(s[-1][0][:10])).days) <= 45:
                row["slope"] = round((l[-1][1] - s[-1][1]) * 100)
            else:
                notes.append("no slope: 10Y and 3M from different months")
        if l:
            for field, months in (("y10_chg_1y", 12), ("y10_chg_1m", 1)):
                old = C._month_ago(l, months)      # by date, so a gap in the series cannot shift it
                if old is not None:
                    row[field] = round((l[-1][1] - old) * 100)
        row["note"] = "; ".join(notes)
        rows.append(row)
    return rows


def rates_by_country(source="auto", **_):
    """Overnight, 3-month and 10-year rates for about 40 countries (OECD, monthly; FRED's copies of the same series when
    OECD refuses, or when source="fred")."""
    source = source if source in ("oecd", "fred") else "auto"

    def from_fred():
        start = f"{dt.date.today().year - 2}-01-01"
        got = {d: C._fred_mei(list(FRED_MEI), d, start) for d in ("immediate", "short", "long")}
        names = {k: C.COUNTRIES[k][0] if k in C.COUNTRIES else k.replace("_", " ").title() for k in FRED_MEI}
        return [{names[k]: pts for k, (sid, pts) in got[d].items()} for d in ("immediate", "short", "long")], \
            [C.src("FRED", f"OECD interest-rate series republished by FRED ({FRED_MEI_PREFIX[d]}xxM156N), {DURATIONS[d]}", "https://fred.stlouisfed.org/tags/series?t=interest+rate%3Boecd")
             for d in ("immediate", "short", "long") if got[d]]

    def run():
        used = [C.oecd_source(d) for d in ("immediate", "short", "long")]
        if source == "fred":
            (imm, short, long_), used = from_fred()
        else:
            imm, short, long_ = (C._oecd_latest(d) for d in ("immediate", "short", "long"))     # one at a time: OECD limits request rates
            if not (imm or short or long_):
                if source == "oecd":
                    C._oecd_series("long")          # nothing cached and OECD unavailable: surface its error message
                (imm, short, long_), used = from_fred()
                if not (imm or short or long_):
                    C._oecd_series("long")
                return {"results": C._rate_rows(imm, short, long_), "sources": used, "fallback": FALLBACK_OECD}
        return {"results": C._rate_rows(imm, short, long_), "sources": used}
    return C.cached(("oecd_rates", source), 900, run)
