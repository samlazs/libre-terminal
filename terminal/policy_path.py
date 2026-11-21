"""Policy path priced by overnight index swaps for six central banks (HANDOFF P3-3).

Data: `cftc/ois_policy_path` (CFTC swap-repository prints of forward-starting, meeting-dated OIS, pooled over ten daily
files; one node per period between policy meetings). Its `date` parameter works historically, so the path today is
shown against the same path one week and one month ago. Anchor: the BIS policy rate (composite._policy_series).
Conventions, declared rather than inferred: rates in percent (implied average overnight rate over the period), changes
and the gap to the policy rate in basis points. The overnight index (SOFR, ESTR, SONIA...) sits a few basis points from
the policy rate, so the gap to the policy rate includes that basis and is not by itself a priced move.
"""

import datetime as dt
import math

import composite as C

# currency: (BIS area code, central bank, overnight index)
BANKS = {
    "USD": ("US", "Federal Reserve", "SOFR"),
    "EUR": ("XM", "European Central Bank", "ESTR"),
    "GBP": ("GB", "Bank of England", "SONIA"),
    "JPY": ("JP", "Bank of Japan", "TONA"),
    "CAD": ("CA", "Bank of Canada", "CORRA"),
    "AUD": ("AU", "Reserve Bank of Australia", "AONIA"),
}
OPTIONS = [{"label": f"{c} ({b[1]})", "value": c} for c, b in BANKS.items()]
MATCH_DAYS = 7          # a past node is the same period when its start lies within a week of today's node
GAP_DAYS = 3            # periods further apart than this are not joined on the step chart (a node is missing)
STALE_DAYS = 7          # a path whose freshest print is older than this is flagged stale
CFTC_URL = "https://pddata.dtcc.com/ppd/cftcdashboard"


def _d(v):
    return dt.date.fromisoformat(str(v)[:10])


def nodes_from_rows(rows):
    """Backend rows -> [{start, end, rate, trades, as_of}] sorted by start, and the date the path is built as of."""
    nodes, built = [], None
    for r in rows or []:
        try:
            rate = float(r["rate"])
            node = {"start": str(r["start_date"])[:10], "end": str(r["end_date"])[:10], "rate": rate,
                    "trades": r.get("num_trades"), "as_of": str(r.get("as_of_date") or r.get("date") or "")[:10]}
        except (KeyError, TypeError, ValueError):
            continue
        nodes.append(node)
        if r.get("date"):
            built = max(built or "", str(r["date"])[:10])
    nodes.sort(key=lambda n: n["start"])
    return nodes, built


def match(node, past):
    """The node of an earlier path covering the same period: same start date, else the nearest start within
    MATCH_DAYS (meeting-dated forward starts move by a day or two with the spot convention). None when absent."""
    if not past:
        return None
    s = _d(node["start"])
    best = min(past, key=lambda p: abs((_d(p["start"]) - s).days))
    return best if abs((_d(best["start"]) - s).days) <= MATCH_DAYS else None


def steps(nodes):
    """x, y for a step chart (line shape 'hv'): each period drawn from its start to its end; a hole between periods
    (a node missing) is left as a gap instead of carrying the earlier rate across it."""
    xs, ys = [], []
    for i, n in enumerate(nodes):
        xs.append(n["start"])
        ys.append(n["rate"])
        nxt = nodes[i + 1] if i + 1 < len(nodes) else None
        if nxt is None or (_d(nxt["start"]) - _d(n["end"])).days > GAP_DAYS:
            xs.append(n["end"])
            ys.append(n["rate"])
            if nxt is not None:
                xs.append(n["end"])
                ys.append(None)
    return xs, ys


def bp(a, b):
    """a - b in whole basis points, halves away from zero; never '-0' (a 0.4 bp move shows as 0)."""
    if a is None or b is None:
        return None
    v = (a - b) * 100
    return int(math.copysign(math.floor(abs(v) + 0.5 + 1e-9), v)) or 0


def _fetch(ccy, date=None):
    """(nodes, built-as-of date) for one currency; date None = latest. Past paths do not change: kept 12 hours."""
    def run():
        return nodes_from_rows(C.api(C.V5, "cftc/ois_policy_path", currency=ccy, date=date))
    return C.cached(("ois_policy_path", ccy, date), 43200 if date else 1800, run)


def _policy_rate(ccy):
    """(rate %, as-of) of the central bank from BIS (daily where current, else month-end); (None, None) if absent."""
    try:
        ser = C._policy_series().get(BANKS[ccy][0]) or []
    except C.CompositeError:
        return None, None
    return (ser[-1][1], ser[-1][0]) if ser else (None, None)


def _currencies(currency):
    out = [c.upper() for c in C._split(currency or "USD") if c.upper() in BANKS]
    if not out:
        raise C.CompositeError("Pick one of: " + ", ".join(BANKS))
    return list(dict.fromkeys(out))


def _ago(today):
    return {"1W": today - dt.timedelta(days=7), "1M": C.minus_months(today, 1)}


def _sources(ccys):
    idx = ", ".join(BANKS[c][2] for c in ccys)
    return [C.src("CFTC", f"swap-repository prints (DTCC public dissemination): forward-starting meeting-dated OIS ({idx}), "
                          "ten daily files pooled per path", CFTC_URL), C.BIS_SOURCE]


def static_sources(currency, table_view):
    """Sources without fetching (composite.static_sources): never raises, an unknown currency names the default."""
    try:
        ccys = _currencies(currency)
    except C.CompositeError:
        ccys = ["USD"]
    return _sources(ccys[:1] if table_view else ccys)


def _stale(built, today):
    if built and (today - _d(built)).days > STALE_DAYS:
        return f"Latest OIS prints are from {built} ({(today - _d(built)).days} days old)"
    return None


def table(currency="USD", today=None, **_):
    """One row per period of the path: rate today, one week and one month ago, the changes and the gap to the policy
    rate. Only the first currency is shown (a table compares dates, the chart compares central banks)."""
    today = today or dt.date.today()
    ccy = _currencies(currency)[0]
    nodes, built = _fetch(ccy)
    if not nodes:
        raise C.CompositeError(f"No {ccy} OIS policy path in the CFTC prints right now.")
    pr, pr_asof = _policy_rate(ccy)
    past = {k: _fetch(ccy, d.isoformat())[0] for k, d in _ago(today).items()}
    # first row: the anchor the path starts from (the table has no other place for a per-answer note)
    rows = [{"start": "Policy rate", "end": f"BIS, {pr_asof}", "rate": pr}] if pr is not None else []
    for n in nodes:
        w, m = match(n, past["1W"]), match(n, past["1M"])
        rows.append({"start": n["start"], "end": n["end"], "rate": round(n["rate"], 4),
                     "rate_1w": round(w["rate"], 4) if w else None, "chg_1w": bp(n["rate"], w and w["rate"]),
                     "rate_1m": round(m["rate"], 4) if m else None, "chg_1m": bp(n["rate"], m and m["rate"]),
                     "vs_policy": bp(n["rate"], pr), "trades": n["trades"], "as_of": n["as_of"]})
    out = {"results": rows, "sources": _sources([ccy])}
    st = _stale(built, today)
    if st:
        out["stale"] = st
    return out


def chart(currency="USD", today=None, **_):
    """Step chart. One currency: the path today, one week and one month ago, with the policy rate as a dashed line.
    Several: today's path of each, the legend giving each policy rate."""
    today = today or dt.date.today()
    ccys = _currencies(currency)
    traces, built_all = [], []
    if len(ccys) == 1:
        ccy = ccys[0]
        pr, pr_asof = _policy_rate(ccy)
        styles = [("Today", None, C.PALETTE[0], "solid", 2.4), ("1W ago", "1W", C.PALETTE[1], "dot", 1.6), ("1M ago", "1M", C.PALETTE[2], "dash", 1.6)]
        ago = _ago(today)
        x_all = []
        for label, key, color, dash, width in styles:
            nodes, built = _fetch(ccy, ago[key].isoformat() if key else None)
            if not nodes:
                continue
            if key is None:
                built_all.append(built)
            xs, ys = steps(nodes)
            x_all += xs
            traces.append({"type": "scatter", "mode": "lines", "name": f"{label} ({built})", "x": xs, "y": ys,
                           "line": {"shape": "hv", "color": color, "dash": dash, "width": width},
                           "hovertemplate": "%{y:.3f}%<extra>" + label + "</extra>"})
        if not built_all:
            raise C.CompositeError(f"No {ccy} OIS policy path in the CFTC prints right now.")
        if pr is not None and x_all:
            traces.append({"type": "scatter", "mode": "lines", "name": f"Policy rate {pr:.2f}% (BIS, {pr_asof})", "x": [min(x_all), max(x_all)],
                           "y": [pr, pr], "line": {"color": C.INK["muted"], "dash": "dash", "width": 1}, "hovertemplate": "%{y:.2f}%<extra>policy rate</extra>"})
        what = f"{BANKS[ccy][1]}: implied {BANKS[ccy][2]}"
    else:
        for i, ccy in enumerate(ccys):
            nodes, built = _fetch(ccy)
            if not nodes:
                continue
            built_all.append(built)
            pr, _ = _policy_rate(ccy)
            xs, ys = steps(nodes)
            traces.append({"type": "scatter", "mode": "lines", "name": f"{ccy}" + (f" (policy {pr:.2f}%)" if pr is not None else ""), "x": xs, "y": ys,
                           "line": {"shape": "hv", "color": C.PALETTE[i % len(C.PALETTE)], "width": 2},
                           "hovertemplate": "%{y:.3f}%<extra>" + ccy + "</extra>"})
        if not traces:
            raise C.CompositeError("No OIS policy path in the CFTC prints right now for: " + ", ".join(ccys))
        what = "OIS-implied average overnight rate"
    lay = C._layout(True)
    lay["yaxis"]["ticksuffix"] = "%"
    lay["annotations"] = [C._note(f"{what} per meeting period, % · as of {max(built_all)}")]
    out = {"data": traces, "layout": lay, "sources": _sources(ccys)}
    st = _stale(min(built_all), today)
    if st:
        out["stale"] = st
    return out
