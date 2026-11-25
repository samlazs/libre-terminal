"""BIS central-bank policy rates and historical policy rate views."""

from __future__ import annotations

import csv
import datetime as dt
import io

import composite as C

ISO_NAMES = {
    "AR": "Argentina", "AU": "Australia", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "CL": "Chile", "CN": "China", "CO": "Colombia", "CZ": "Czechia", "DK": "Denmark",
    "GB": "United Kingdom", "HK": "Hong Kong", "HU": "Hungary", "ID": "Indonesia", "IL": "Israel", "IN": "India", "IS": "Iceland", "JP": "Japan", "KR": "South Korea", "KW": "Kuwait",
    "MA": "Morocco", "MK": "North Macedonia", "MX": "Mexico", "MY": "Malaysia", "NO": "Norway", "NZ": "New Zealand", "PE": "Peru", "PH": "Philippines", "PL": "Poland", "RO": "Romania",
    "RS": "Serbia", "RU": "Russia", "SA": "Saudi Arabia", "SE": "Sweden", "TH": "Thailand", "TR": "Turkey", "US": "United States", "XM": "Euro area", "ZA": "South Africa",
}

POLICY_OPTIONS = [{"label": v, "value": k} for k, v in sorted(ISO_NAMES.items(), key=lambda x: x[1])]

EURO_MEMBERS = {"Austria", "Belgium", "Croatia", "Estonia", "Finland", "France", "Germany", "Greece", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg",
                "Netherlands", "Portugal", "Slovak Republic", "Slovenia", "Spain"}

BIS_SOURCE = C.src("BIS", "Central bank policy rates (WS_CBPOL), daily where available, else month-end", "https://data.bis.org/topics/CBPOL")


def _bis_rates(flow, start):
    """{iso2: [(period, rate), ...]} from the BIS central-bank policy-rate dataflow (M = monthly YYYY-MM, D = daily)."""
    url = f"https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CBPOL/1.0/{flow}.?startPeriod={start}&format=csv&detail=dataonly"
    try:
        text = C._download(url, 60).decode("utf-8", "replace")      # certifi-verified, like every other download here
    except C.CompositeError:
        raise C.CompositeError("The BIS policy-rate service is not reachable right now.") from None
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        v = C.num(row.get("OBS_VALUE"))
        area, per = row.get("REF_AREA"), row.get("TIME_PERIOD")
        if v is None or not area or not per:
            continue
        out.setdefault(area, []).append((per, v))
    for k in out:
        out[k].sort()
    return out


def _bis_policy():
    """{iso2: [(YYYY-MM, rate), ...]} monthly central-bank policy rates since 2000 (BIS, keyless): month-end values."""
    return C.cached_disk("bis_policy", 6 * 3600, lambda: C._bis_rates("M", "2000-01"))


def _bis_policy_daily():
    """{iso2: [(YYYY-MM-DD, rate), ...]} daily policy rates over the last 400 days (BIS daily flow, 38 central banks,
    about a week behind): a decision taken this month shows here before the monthly flow has the month."""
    return C.cached_disk("bis_policy_daily", 6 * 3600, lambda: C._bis_rates("D", (dt.date.today() - dt.timedelta(days=400)).isoformat()))


DAILY_POLICY_MAX_AGE = 120          # days: an older daily series is not used (the monthly one then speaks, labelled)


def _policy_series():
    """{iso2: [(date, rate), ...]}: the daily series where it is current, else the monthly one (month-end values).
    For views that show only the latest policy rate (Global Yields, Country Monitor)."""
    out = dict(C._bis_policy())
    try:
        daily = C._bis_policy_daily()
    except C.CompositeError:
        return out
    cutoff = (dt.date.today() - dt.timedelta(days=C.DAILY_POLICY_MAX_AGE)).isoformat()
    out.update({iso: s for iso, s in daily.items() if s and s[-1][0] >= cutoff})
    return out


def _policy_row(ser, monthly):
    """rate, as_of, 1-year change and last move from one series (daily or monthly, oldest first)."""
    last_p, last = ser[-1]
    prev_i = next((i for i in range(len(ser) - 2, -1, -1) if ser[i][1] != last), None)
    year_back = C.minus_months(dt.date.fromisoformat(last_p + "-01" if monthly else last_p), 12)
    year_ago = C._at_or_before(ser, year_back.strftime("%Y-%m") if monthly else year_back.isoformat())
    row = {"rate": last, "as_of": last_p, "chg_1y": round((last - year_ago) * 100) if year_ago is not None else None}
    if prev_i is not None:
        row["last_move_bp"] = round((last - ser[prev_i][1]) * 100)
        row["last_move"] = ser[prev_i + 1][0]
        row["previous"] = ser[prev_i][1]
    return row


def policy_rates(**_):
    """Central-bank policy rates by country with the latest move and 1-year change: from the BIS daily series when it is
    current (a cut this month shows this month), else the month-end value, labelled as such."""
    data = C._bis_policy()
    try:
        daily = C._bis_policy_daily()
    except C.CompositeError as e:
        C.partial_from(e, "BIS daily policy rates")
        daily = {}
    cutoff = (dt.date.today() - dt.timedelta(days=C.DAILY_POLICY_MAX_AGE)).isoformat()
    rows = []
    for iso, name in ISO_NAMES.items():
        ser, dser = data.get(iso) or [], daily.get(iso) or []
        if dser and dser[-1][0] >= cutoff:
            row = C._policy_row(dser, monthly=False)
            if "last_move" not in row and ser:            # no change in the daily window: the move is older, take it from the months
                old = C._policy_row(ser, monthly=True)
                row.update({k: old[k] for k in ("last_move", "last_move_bp", "previous") if k in old and old["rate"] == row["rate"]})
        elif ser and ser[-1][0] >= C.minus_months(dt.date.today(), 4).strftime("%Y-%m"):
            row = dict(C._policy_row(ser, monthly=True), note="month-end value")
        else:
            continue
        rows.append(dict({"country": name, "code": iso}, **row))
    us = next((r["rate"] for r in rows if r["code"] == "US"), None)
    if us is not None:
        for r in rows:                                    # carry against the dollar, the first thing an FX desk asks
            r["vs_us"] = round((r["rate"] - us) * 100)
    rows.sort(key=lambda r: r["country"])
    return {"results": rows}


def _policy_history_points(ser, dser):
    """Month-end values (plotted at the month's first day, as before) followed by the daily values dated after the last
    month: the monthly flow lags a month, so a Bank of England move on 2026-11-05 (3.75 -> 4.00) was missing from the
    chart and its legend while the policy-rate table showed it (review F3)."""
    pts = [(p + "-01", v) for p, v in ser]
    last = ser[-1][0] if ser else ""
    return pts + [(d, v) for d, v in dser if d[:7] > last]


def policy_rate_history(central_bank="US,XM,GB,JP", start="2015", country=None, **kw):
    """Policy-rate history (step chart) for the selected central banks."""
    data = C._bis_policy()
    try:
        daily = C._bis_policy_daily()
    except C.CompositeError as e:
        C.partial_from(e, "BIS daily policy rates")
        daily = {}
    codes = [c.upper() for c in C._split(central_bank)][:8]
    if country:                                   # a dashboard country (e.g. "japan") overrides the list
        mapped = [C.COUNTRIES[c][3] for c in C._split(country) if c in C.COUNTRIES and C.COUNTRIES[c][3]]
        codes = mapped or codes
    codes = list(dict.fromkeys(codes))            # France and Germany both map to the euro area: one trace, not two
    start = C.range_start(kw.get("range"), start)[:7]
    traces = []
    for i, iso in enumerate(codes):
        ser = [(p, v) for p, v in data.get(iso, []) if p >= start]
        if not ser:
            continue
        ser = C._policy_history_points(ser, daily.get(iso) or [])
        name = ISO_NAMES.get(iso, iso)
        traces.append({"type": "scatter", "mode": "lines", "name": f"{name}  {ser[-1][1]:.2f}%", "x": [p for p, _ in ser], "y": [v for _, v in ser],
                       "line": {"width": 2, "shape": "hv", "color": C.PALETTE[i % len(C.PALETTE)]}, "hovertemplate": "%{y:.2f}%<extra>" + name + "</extra>"})
    if not traces:
        raise C.CompositeError("No policy-rate history for: " + ", ".join(codes))
    lay = C._layout(len(traces) > 1)
    lay["yaxis"]["ticksuffix"] = "%"
    return {"data": traces, "layout": lay, "sources": [BIS_SOURCE], "controls": {"freq": "monthly", "ranges": C.MONTHLY_RANGES, "range": kw.get("range")}}
