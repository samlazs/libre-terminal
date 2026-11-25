"""Policy path priced by overnight index swaps for six central banks (HANDOFF P3-3).

Data: `cftc/ois_policy_path` (CFTC swap-repository prints of forward-starting, meeting-dated OIS, pooled over ten daily
files; one node per period between policy meetings). Its `date` parameter works historically, so the path today is
shown against the same path one week and one month ago. Anchor: the BIS policy rate (composite._policy_series).
Conventions, declared rather than inferred: rates in percent (implied average overnight rate over the period), changes
and the gap to the policy rate in basis points. The overnight index (SOFR, ESTR, SONIA...) sits a few basis points from
the policy rate, so the gap to the policy rate includes that basis and is not by itself a priced move.

Meeting path: the pool also holds bespoke forward periods (a month from mid-December...) that overlap the meeting-dated
ones. One node per meeting period is kept by chaining periods end to start (each meeting's period starts where the
previous one ends): the non-overlapping set with the most trades. No meeting calendar to maintain.
Current period: today to the first meeting, at the latest overnight fixing where one is published keylessly (SOFR, ESTR,
SONIA through FRED's public download); the first meeting's step is measured from it, so the index basis cancels.
Elsewhere the current period is not drawn and the first step is left blank (it would include the basis).
Step: a period's rate minus the previous period's, in bp. Priced move: the step as a percent of a 25 bp move (+100 =
one full hike priced, -150 = one cut and half of a second).
"""

import datetime as dt
import math

import composite as C
import fred_public

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
# overnight fixings published keylessly (FRED public download): currency -> (FRED series, index name)
FIXINGS = {"USD": ("SOFR", "SOFR"), "EUR": ("ECBESTRVOLWGTTRMDMNRT", "ESTR"), "GBP": ("IUDSOIA", "SONIA")}
FIXING_DAYS = 10        # a fixing older than this does not stand for today
FED_RANGE = ("DFEDTARL", "DFEDTARU")
STEP_BP = 25


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


def meeting_path(nodes):
    """One node per meeting period: the non-overlapping periods (one may start up to GAP_DAYS before the previous one
    ends) with the most trades, by weighted interval scheduling. Bespoke forward periods overlap the meeting-dated ones
    and carry few trades, so they drop out."""
    ns = sorted(nodes, key=lambda n: (n["start"], n["end"]))
    best = [(0, [])] * (len(ns) + 1)
    for i in range(len(ns) - 1, -1, -1):
        end = _d(ns[i]["end"])
        k = next((j for j in range(i + 1, len(ns)) if (_d(ns[j]["start"]) - end).days >= -GAP_DAYS), len(ns))
        take = (max(int(ns[i].get("trades") or 0), 1) + best[k][0], [ns[i]] + best[k][1])
        best[i] = take if take[0] >= best[i + 1][0] else best[i + 1]
    return best[0][1]


def ahead(nodes, today):
    """Periods still ahead of today; one that has begun starts at today (its real start kept as orig_start)."""
    t = today.isoformat()
    return [dict(n, start=max(n["start"], t), orig_start=n.get("orig_start", n["start"])) for n in nodes if n["end"] > t]


def fresh(nodes):
    """Freshest trade date among the nodes: the day the path is as of."""
    return max((n["as_of"] for n in nodes if n.get("as_of")), default=None)


def meeting_label(start):
    return _d(start).strftime("%b %Y")


def priced_move(step):
    """A step in bp as a percent of a 25 bp move (+100 = one full hike)."""
    return None if step is None else round(step / STEP_BP * 100)


def match(node, past):
    """The node of an earlier path covering the same period: same start date, else the nearest start within
    MATCH_DAYS (meeting-dated forward starts move by a day or two with the spot convention). None when absent."""
    if not past:
        return None
    s = _d(node.get("orig_start") or node["start"])
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
    """(meeting-path nodes, built-as-of date) for one currency; date None = latest. Past paths do not change: 12 hours."""
    def run():
        nodes, built = nodes_from_rows(C.api(C.V5, "cftc/ois_policy_path", currency=ccy, date=date))
        return meeting_path(nodes), built
    return C.cached(("ois_policy_path", ccy, date), 43200 if date else 1800, run)


def _fred_last(sid):
    """(date, value) of a FRED series' latest observation (public download), or None."""
    def run():
        start = (dt.date.today() - dt.timedelta(days=40)).isoformat()
        rows = [(d, v) for d, v in fred_public.observations(sid, start=start) if v is not None]
        return rows[-1] if rows else None
    try:
        return C.cached(("fred_last", sid), 3 * 3600, run)
    except C.CompositeError as e:  # a missing fixing leaves the current period undrawn, never an error, and says so
        C.partial_from(e, f"FRED {sid} (latest overnight fixing)")
        return None


def fixing(ccy, today):
    """(rate %, date, index name) of the latest overnight fixing, or None (not published keylessly, or stale)."""
    sid, name = FIXINGS.get(ccy, (None, None))
    last = _fred_last(sid) if sid else None
    if not last or (today - _d(last[0])).days > FIXING_DAYS:
        return None
    return float(last[1]), str(last[0])[:10], name


def fed_range():
    """'3.75–4.00%': the Fed's target range (FRED), or None."""
    lo, hi = _fred_last(FED_RANGE[0]), _fred_last(FED_RANGE[1])
    return f"{lo[1]:.2f}–{hi[1]:.2f}%" if lo and hi else None


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
    out = [C.src("CFTC", f"swap-repository prints (DTCC public dissemination): forward-starting meeting-dated OIS ({idx}), "
                         "ten daily files pooled per path", CFTC_URL), C.BIS_SOURCE]
    fx = [FIXINGS[c] for c in ccys if c in FIXINGS]
    if fx:
        out.append(C.src("FRED", "overnight fixing for the current period: " + ", ".join(f"{n} ({s})" for s, n in fx)
                         + ("; Fed target range (DFEDTARL, DFEDTARU)" if "USD" in ccys else ""),
                         "https://fred.stlouisfed.org/series/" + fx[0][0]))
    return out


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


def _with_current(nodes, fx, today):
    """Today's path with the current period (today to the first meeting) at the overnight fixing, when there is one."""
    if fx and nodes and nodes[0]["start"] > today.isoformat():
        return [{"start": today.isoformat(), "end": nodes[0]["start"], "rate": fx[0]}] + nodes
    return nodes


def table(currency="USD", today=None, **_):
    """One row per meeting period of the path: rate today, the step at that meeting and the move it prices, one week and
    one month ago, and the gap to the policy rate. Only the first currency is shown (a table compares dates, the chart
    compares central banks)."""
    today = today or dt.date.today()
    ccy = _currencies(currency)[0]
    nodes, built = _fetch(ccy)
    nodes = ahead(nodes, today)
    if not nodes:
        raise C.CompositeError(f"No {ccy} OIS policy path ahead of today in the CFTC prints right now.")
    pr, pr_asof = _policy_rate(ccy)
    past = {k: _fetch(ccy, d.isoformat())[0] for k, d in _ago(today).items()}
    # first rows: what the path starts from (the table has no other place for a per-answer note)
    rng = fed_range() if ccy == "USD" else None
    rows = [{"start": "Policy rate", "end": f"BIS, {pr_asof}", "meeting": f"target {rng}" if rng else None, "rate": pr}] if pr is not None else []
    fx = fixing(ccy, today)
    prev, prev_end = None, None
    if fx and nodes[0]["start"] > today.isoformat():
        rows.append({"start": today.isoformat(), "end": nodes[0]["start"], "meeting": f"now ({fx[2]} fixing)", "rate": round(fx[0], 4),
                     "vs_policy": bp(fx[0], pr), "as_of": fx[1]})
        prev, prev_end = fx[0], nodes[0]["start"]
    for n in nodes:
        w, m = match(n, past["1W"]), match(n, past["1M"])
        # a step is one meeting's move: blank after a missing period (it would add up two meetings)
        joined = prev_end is not None and abs((_d(n["start"]) - _d(prev_end)).days) <= GAP_DAYS
        step = bp(n["rate"], prev) if joined else None
        rows.append({"start": n["start"], "end": n["end"], "meeting": meeting_label(n.get("orig_start") or n["start"]), "rate": round(n["rate"], 4),
                     "step": step, "priced": priced_move(step),
                     "rate_1w": round(w["rate"], 4) if w else None, "chg_1w": bp(n["rate"], w and w["rate"]),
                     "rate_1m": round(m["rate"], 4) if m else None, "chg_1m": bp(n["rate"], m and m["rate"]),
                     "vs_policy": bp(n["rate"], pr), "trades": n["trades"], "as_of": n["as_of"]})
        prev, prev_end = n["rate"], n["end"]
    out = {"results": rows, "sources": _sources([ccy])}
    st = _stale(built, today)
    if st:
        out["stale"] = st
    return out


def chart(currency="USD", today=None, **_):
    """Step chart. One currency: the path today, one week and one month ago, with the policy rate as a dashed line.
    Several: today's path of each, the legend giving each policy rate. Every line starts at today."""
    today = today or dt.date.today()
    ccys = _currencies(currency)
    traces, asof_all, built_all = [], [], []
    if len(ccys) == 1:
        ccy = ccys[0]
        pr, pr_asof = _policy_rate(ccy)
        fx = fixing(ccy, today)
        styles = [("Today", None, C.PALETTE[0], "solid", 2.4), ("1W ago", "1W", C.PALETTE[1], "dot", 1.6), ("1M ago", "1M", C.PALETTE[2], "dash", 1.6)]
        ago = _ago(today)
        x_all = []
        for label, key, color, dash, width in styles:
            nodes, built = _fetch(ccy, ago[key].isoformat() if key else None)
            nodes = ahead(nodes, today)            # past paths too: only periods still ahead, so every line starts at today
            if not nodes:
                continue
            asof = fresh(nodes) or built
            if key is None:
                asof_all.append(asof)
                built_all.append(built)
                nodes = _with_current(nodes, fx, today)
            xs, ys = steps(nodes)
            x_all += xs
            traces.append({"type": "scatter", "mode": "lines", "name": f"{label} ({asof})", "x": xs, "y": ys,
                           "line": {"shape": "hv", "color": color, "dash": dash, "width": width},
                           "hovertemplate": "%{y:.3f}%<extra>" + label + "</extra>"})
        if not asof_all:
            raise C.CompositeError(f"No {ccy} OIS policy path in the CFTC prints right now.")
        if pr is not None and x_all:
            rng = fed_range() if ccy == "USD" else None
            traces.append({"type": "scatter", "mode": "lines", "name": f"Policy rate {pr:.3f}%" + (f" (target {rng})" if rng else "") + f" (BIS, {pr_asof})",
                           "x": [min(x_all), max(x_all)], "y": [pr, pr], "line": {"color": C.INK["muted"], "dash": "dash", "width": 1},
                           "hovertemplate": "%{y:.3f}%<extra>policy rate</extra>"})
        what = f"{BANKS[ccy][1]}: implied {BANKS[ccy][2]} per meeting period, %" + (f" (to the first meeting: {fx[2]} fixing of {fx[1]})" if fx else "")
    else:
        for i, ccy in enumerate(ccys):
            nodes, built = _fetch(ccy)
            nodes = ahead(nodes, today)
            if not nodes:
                continue
            asof_all.append(fresh(nodes) or built)
            built_all.append(built)
            pr, _ = _policy_rate(ccy)
            xs, ys = steps(_with_current(nodes, fixing(ccy, today), today))
            traces.append({"type": "scatter", "mode": "lines", "name": f"{ccy}" + (f" (policy {pr:.3f}%)" if pr is not None else ""), "x": xs, "y": ys,
                           "line": {"shape": "hv", "color": C.PALETTE[i % len(C.PALETTE)], "width": 2},
                           "hovertemplate": "%{y:.3f}%<extra>" + ccy + "</extra>"})
        if not traces:
            raise C.CompositeError("No OIS policy path in the CFTC prints right now for: " + ", ".join(ccys))
        what = "OIS-implied average overnight rate per meeting period, %"
    lay = C._layout(True)
    lay["yaxis"]["ticksuffix"] = "%"
    lay["yaxis"]["tickformat"] = ".2f"
    # the note sits in the top corner the path leaves empty (a rising path starts low on the left), off the policy line
    vals = [v for v in traces[0]["y"] if v is not None]
    rising = len(vals) > 1 and vals[-1] >= vals[0]
    note = C._note(f"{what} · as of {max(asof_all)}")
    note.update({"x": 0 if rising else 1, "xanchor": "left" if rising else "right", "y": 1, "yanchor": "top"})
    lay["annotations"] = [note]
    out = {"data": traces, "layout": lay, "sources": _sources(ccys), "as_of": max(asof_all)}    # the newest futures print (x = meetings ahead)
    st = _stale(min(built_all), today)
    if st:
        out["stale"] = st
    return out
