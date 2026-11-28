"""Policy path priced by overnight index swaps for six central banks (HANDOFF P3-3).

Data: `cftc/ois_policy_path` (CFTC swap-repository prints of forward-starting, meeting-dated OIS, pooled over a 14-day
window; one node per period between policy meetings, priced from its freshest trades). Its `date` parameter works
historically, so the path today is shown against the same path one week and one month ago. Anchor: the BIS policy rate
(composite._policy_series). Conventions, declared rather than inferred: rates in percent (implied average overnight rate
over the period), changes and the gap to the policy rate in basis points. The overnight index (EFFR, ESTR, SONIA...)
sits a few basis points from the policy rate, so the gap to the policy rate includes that basis and is not by itself a
priced move.

Meeting path: the pool also holds bespoke forward periods (a month from mid-December...) that overlap the meeting-dated
ones. One node per meeting period is kept by chaining periods end to start (each meeting's period starts where the
previous one ends): the non-overlapping set with the most trades. No meeting calendar to maintain.
Current period: today to the first meeting, at the latest official overnight fixing (FRED for EFFR/ESTR/SONIA, with
V5 `cftc/historical_fixings` for TONA/CORRA/AONIA and fallback); the first meeting's step is measured from it, so the
index basis cancels. Note: USD meeting-dated OIS trade predominantly on Federal Funds (`EFFR`), not `SOFR`.
Step: a period's rate minus the previous period's, in bp. Priced move: the unrounded step as a percent of a 25 bp move
(+100 = one full hike priced, -150 = one cut and half of a second). Cum. move: cumulative move in bp from the fixing.
"""

import datetime as dt
import math

import composite as C
import fred_public

# currency: (BIS area code, central bank, overnight index)
BANKS = {
    "USD": ("US", "Federal Reserve", "EFFR"),
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
FIXINGS = {"USD": ("EFFR", "EFFR"), "EUR": ("ECBESTRVOLWGTTRMDMNRT", "ESTR"), "GBP": ("IUDSOIA", "SONIA")}
ADMIN_SOURCES = {
    "USD": ("NY Fed", "https://www.newyorkfed.org/markets/reference-rates/effr"),
    "EUR": ("ECB", "https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_short-term_rate/html/index.en.html"),
    "GBP": ("Bank of England", "https://www.bankofengland.co.uk/markets/sonia-benchmark"),
    "JPY": ("Bank of Japan", "https://www.stat-search.boj.or.jp/"),
    "CAD": ("Bank of Canada", "https://www.bankofcanada.ca/rates/interest-rates/corra/"),
    "AUD": ("Reserve Bank of Australia", "https://www.rba.gov.au/statistics/cash-rate/"),
}
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
    d = _d(start)
    return f"{d.day} {d.strftime('%b %Y')}"


def priced_move(step):
    """A step in bp (exact float or int) as a percent of a 25 bp move (+100 = one full hike), halves away from zero."""
    if step is None:
        return None
    v = step / STEP_BP * 100
    return int(math.copysign(math.floor(abs(v) + 0.5 + 1e-9), v)) or 0


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


def steps_meta(nodes, base_rate=None):
    """x, y, marker sizes, and hover customdata [period_span, step_detail] for the step chart (line shape 'hv')."""
    xs, ys, sizes, customdata = [], [], [], []
    prev_rate = base_rate
    prev_end = nodes[0]["start"] if (nodes and not nodes[0].get("_current") and base_rate is not None) else None
    for i, n in enumerate(nodes):
        s_lbl = "Now" if n.get("_current") else meeting_label(n.get("orig_start") or n["start"])
        e_lbl = meeting_label(n["end"])
        span = f"{s_lbl} → {e_lbl}"
        if n.get("_current"):
            detail = " · current fixing"
        else:
            joined = prev_end is not None and prev_rate is not None and abs((_d(n["start"]) - _d(prev_end)).days) <= GAP_DAYS
            if joined:
                raw_bp = (n["rate"] - prev_rate) * 100
                bp_1 = math.copysign(math.floor(abs(raw_bp) * 10 + 0.5 + 1e-9) / 10, raw_bp) or 0.0
                pm = priced_move(raw_bp)
                s_txt = f"{bp_1:+.1f}" if bp_1 else "0.0"
                p_txt = f"{pm:+d}" if pm else "0"
                detail = f" · {s_txt} bp ({p_txt}% of 25 bp)"
            else:
                detail = ""
        xs.append(n["start"])
        ys.append(n["rate"])
        sizes.append(5)
        customdata.append([span, detail])
        prev_rate, prev_end = n["rate"], n["end"]
        nxt = nodes[i + 1] if i + 1 < len(nodes) else None
        if nxt is None or (_d(nxt["start"]) - _d(n["end"])).days > GAP_DAYS:
            xs.append(n["end"])
            ys.append(n["rate"])
            sizes.append(0)
            customdata.append([span, detail])
            if nxt is not None:
                xs.append(n["end"])
                ys.append(None)
                sizes.append(0)
                customdata.append(["", ""])
    return xs, ys, sizes, customdata


def bp(a, b):
    """a - b in whole basis points, halves away from zero; never '-0' (a 0.4 bp move shows as 0)."""
    if a is None or b is None:
        return None
    v = (a - b) * 100
    return int(math.copysign(math.floor(abs(v) + 0.5 + 1e-9), v)) or 0


def _fetch(ccy, date=None):
    """(meeting-path nodes, built-as-of date) for one currency; date None = latest. Past paths do not change: 12 hours."""
    def run():
        nodes, built = nodes_from_rows(C.api(C.V5, "cftc/ois_policy_path", currency=ccy, date=date, lookback_days=14))
        return meeting_path(nodes), built
    return C.cached(("ois_policy_path_v3", ccy, date), 43200 if date else 1800, run)


def _fetch_past(ccy, d, label):
    """Earlier meeting path for 1W / 1M comparison; a missing historical snapshot degrades cleanly to partial."""
    try:
        return _fetch(ccy, d.isoformat())
    except C.CompositeError as e:
        C.partial_from(e, f"CFTC OIS path ({label} ago)")
        return [], None


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


def _cftc_fixing(index):
    """(date, rate %) of an overnight index's latest official fixing via V5 cftc/historical_fixings, or None."""
    def run():
        start = (dt.date.today() - dt.timedelta(days=30)).isoformat()
        rows = C.api(C.V5, "cftc/historical_fixings", index=index, start_date=start)
        obs = []
        for r in rows or []:
            try:
                if r.get("date") is not None and r.get("rate") is not None:
                    obs.append((str(r["date"])[:10], float(r["rate"])))
            except (TypeError, ValueError):
                continue
        return max(obs, key=lambda x: x[0]) if obs else None
    try:
        return C.cached(("cftc_fixing", index), 3 * 3600, run)
    except C.CompositeError as e:
        C.partial_from(e, f"{index} (latest overnight fixing)")
        return None


def _fresh_obs(obs, today):
    return bool(obs) and (today - _d(obs[0])).days <= FIXING_DAYS


def _clear_partial(what):
    notes = C._PARTIAL_NOTES.get()
    if notes is not None and what in notes:
        notes.remove(what)


def _fixing_with_src(ccy, today):
    """((rate %, date, index name) or None, used_admin_fallback)."""
    idx = BANKS[ccy][2]
    if ccy in FIXINGS:
        sid = FIXINGS[ccy][0]
        last = _fred_last(sid)
        if _fresh_obs(last, today):
            return (float(last[1]), str(last[0])[:10], idx), False
        alt = _cftc_fixing(idx)
        if _fresh_obs(alt, today):
            _clear_partial(f"FRED {sid} (latest overnight fixing)")
            C.note_fallback(ADMIN_SOURCES[ccy][0], f"FRED {sid} unavailable or stale")
            return (float(alt[1]), str(alt[0])[:10], idx), True
        return None, False
    last = _cftc_fixing(idx)
    if _fresh_obs(last, today):
        return (float(last[1]), str(last[0])[:10], idx), False
    return None, False


def fixing(ccy, today):
    """(rate %, date, index name) of the latest overnight fixing, or None (unavailable or stale)."""
    return _fixing_with_src(ccy, today)[0]


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


def _sources(ccys, fallback_ccys=(), include_fed_range=True):
    idx = ", ".join(BANKS[c][2] for c in ccys)
    out = [C.src("CFTC", f"swap-repository prints (DTCC public dissemination): forward-starting meeting-dated OIS ({idx}), "
                         "14-day window priced from freshest trades", CFTC_URL), C.BIS_SOURCE]
    fred_fx = [FIXINGS[c] for c in ccys if c in FIXINGS and c not in fallback_ccys]
    fred_parts = [f"{n} ({s})" for s, n in fred_fx]
    if include_fed_range and "USD" in ccys:
        fred_parts.append("Fed target range (DFEDTARL, DFEDTARU)")
    if fred_parts:
        anchor_sid = fred_fx[0][0] if fred_fx else FED_RANGE[0]
        desc = ("overnight fixing for the current period: " + ", ".join(fred_parts)) if fred_fx else "Fed target range (DFEDTARL, DFEDTARU)"
        out.append(C.src("FRED", desc, "https://fred.stlouisfed.org/series/" + anchor_sid))
    for c in ccys:
        if (c not in FIXINGS or c in fallback_ccys) and c in ADMIN_SOURCES:
            name, url = ADMIN_SOURCES[c]
            out.append(C.src(name, f"overnight fixing for the current period: {BANKS[c][2]}", url))
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
        return [{"start": today.isoformat(), "end": nodes[0]["start"], "rate": fx[0], "_current": True}] + nodes
    return nodes


def table(currency="USD", today=None, **_):
    """One row per meeting period of the path: rate today, the step at that meeting and the move it prices, cumulative
    move from the fixing, one week and one month ago, and the gap to the policy rate. Only the first currency is shown."""
    today = today or dt.date.today()
    ccy = _currencies(currency)[0]
    nodes, built = _fetch(ccy)
    nodes = ahead(nodes, today)
    if not nodes:
        raise C.CompositeError(f"No {ccy} OIS policy path ahead of today in the CFTC prints right now.")
    pr, pr_asof = _policy_rate(ccy)
    past = {k: _fetch_past(ccy, d, k)[0] for k, d in _ago(today).items()}
    rng = fed_range() if ccy == "USD" else None
    rows = [{"meeting": f"Policy rate ({rng})" if rng else "Policy rate", "start": None, "end": None,
             "rate": pr, "as_of": pr_asof}] if pr is not None else []
    fx, fb = _fixing_with_src(ccy, today)
    fallback_ccys = {ccy} if fb else set()
    prev, prev_end, base_fx = None, None, None
    if fx and nodes[0]["start"] > today.isoformat():
        rows.append({"meeting": f"Now ({fx[2]} fixing)", "start": today.isoformat(), "end": nodes[0]["start"],
                     "rate": round(fx[0], 4), "vs_policy": bp(fx[0], pr), "as_of": fx[1]})
        prev, prev_end, base_fx = fx[0], nodes[0]["start"], fx[0]
    elif fx and nodes[0].get("orig_start") == today.isoformat() and fx[1] < today.isoformat():
        rows.append({"meeting": f"Now ({fx[2]} fixing)", "start": None, "end": today.isoformat(),
                     "rate": round(fx[0], 4), "vs_policy": bp(fx[0], pr), "as_of": fx[1]})
        prev, prev_end, base_fx = fx[0], today.isoformat(), fx[0]
    for n in nodes:
        w, m = match(n, past["1W"]), match(n, past["1M"])
        # a step is one meeting's move: blank after a missing period (it would add up two meetings)
        joined = prev_end is not None and prev is not None and abs((_d(n["start"]) - _d(prev_end)).days) <= GAP_DAYS
        step = bp(n["rate"], prev) if joined else None
        priced = priced_move((n["rate"] - prev) * 100) if joined else None
        rows.append({"meeting": meeting_label(n.get("orig_start") or n["start"]), "start": n["start"], "end": n["end"],
                     "rate": round(n["rate"], 4), "step": step, "priced": priced,
                     "cum_bp": bp(n["rate"], base_fx),
                     "rate_1w": round(w["rate"], 4) if w else None, "chg_1w": bp(n["rate"], w and w["rate"]),
                     "rate_1m": round(m["rate"], 4) if m else None, "chg_1m": bp(n["rate"], m and m["rate"]),
                     "vs_policy": bp(n["rate"], pr), "trades": n["trades"], "as_of": n["as_of"]})
        prev, prev_end = n["rate"], n["end"]
    out = {"results": rows, "sources": _sources([ccy], fallback_ccys, include_fed_range=bool(rng))}
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
    fallback_ccys = set()
    include_fed_range = False
    if len(ccys) == 1:
        ccy = ccys[0]
        pr, pr_asof = _policy_rate(ccy)
        fx, fb = _fixing_with_src(ccy, today)
        if fb:
            fallback_ccys.add(ccy)
        styles = [("Today", None, C.PALETTE[0], "solid", 2.4), ("1W ago", "1W", C.PALETTE[1], "dot", 1.6), ("1M ago", "1M", C.PALETTE[2], "dash", 1.6)]
        ago = _ago(today)
        x_all = []
        meeting_day = False
        for label, key, color, dash, width in styles:
            nodes, built = _fetch_past(ccy, ago[key], key) if key else _fetch(ccy)
            nodes = ahead(nodes, today)            # past paths too: only periods still ahead, so every line starts at today
            if not nodes:
                continue
            asof = fresh(nodes) or built
            if key is None:
                asof_all.append(asof)
                built_all.append(built)
                meeting_day = bool(fx and nodes[0].get("orig_start") == today.isoformat() and fx[1] < today.isoformat())
                xs, ys, sizes, cd = steps_meta(_with_current(nodes, fx, today), base_rate=fx[0] if meeting_day else None)
                traces.append({"type": "scatter", "mode": "lines+markers", "name": f"{label} ({asof})", "x": xs, "y": ys,
                               "line": {"shape": "hv", "color": color, "dash": dash, "width": width},
                               "marker": {"size": sizes, "color": color}, "customdata": cd,
                               "hovertemplate": "%{y:.3f}%<br>%{customdata[0]}%{customdata[1]}<extra>" + label + "</extra>"})
            else:
                xs, ys = steps(nodes)
                traces.append({"type": "scatter", "mode": "lines", "name": f"{label} ({asof})", "x": xs, "y": ys,
                               "line": {"shape": "hv", "color": color, "dash": dash, "width": width},
                               "hovertemplate": "%{y:.3f}%<extra>" + label + "</extra>"})
            x_all += xs
        if not asof_all:
            raise C.CompositeError(f"No {ccy} OIS policy path in the CFTC prints right now.")
        if pr is not None and x_all:
            rng = fed_range() if ccy == "USD" else None
            include_fed_range = bool(rng)
            traces.append({"type": "scatter", "mode": "lines", "name": f"Policy rate {pr:.3f}%" + (f" (target {rng})" if rng else "") + f" (BIS, {pr_asof})",
                           "x": [min(x_all), max(x_all)], "y": [pr, pr], "line": {"color": C.INK["muted"], "dash": "dash", "width": 1},
                           "hovertemplate": "%{y:.3f}%<extra>policy rate</extra>"})
        fix_note = (f" (baseline: {fx[2]} fixing of {fx[1]})" if meeting_day else f" (to the first meeting: {fx[2]} fixing of {fx[1]})") if fx else ""
        what = f"{BANKS[ccy][1]}: implied {BANKS[ccy][2]} per meeting period, %{fix_note}"
    else:
        for i, ccy in enumerate(ccys):
            nodes, built = _fetch(ccy)
            nodes = ahead(nodes, today)
            if not nodes:
                continue
            asof_all.append(fresh(nodes) or built)
            built_all.append(built)
            pr, _ = _policy_rate(ccy)
            fx, fb = _fixing_with_src(ccy, today)
            if fb:
                fallback_ccys.add(ccy)
            meeting_day = bool(fx and nodes[0].get("orig_start") == today.isoformat() and fx[1] < today.isoformat())
            color = C.PALETTE[i % len(C.PALETTE)]
            xs, ys, sizes, cd = steps_meta(_with_current(nodes, fx, today), base_rate=fx[0] if meeting_day else None)
            traces.append({"type": "scatter", "mode": "lines+markers", "name": f"{ccy}" + (f" (policy {pr:.3f}%)" if pr is not None else ""), "x": xs, "y": ys,
                           "line": {"shape": "hv", "color": color, "width": 2},
                           "marker": {"size": sizes, "color": color}, "customdata": cd,
                           "hovertemplate": "%{y:.3f}%<br>%{customdata[0]}%{customdata[1]}<extra>" + ccy + "</extra>"})
        if not traces:
            raise C.CompositeError("No OIS policy path in the CFTC prints right now for: " + ", ".join(ccys))
        what = "OIS-implied average overnight rate per meeting period, %"
    lay = C._layout(True)
    lay["yaxis"]["ticksuffix"] = "%"
    lay["yaxis"]["tickformat"] = ".2f"
    # place the note in the corner the curve leaves clear (bottom-right for rising/multi-bank, top-right for falling)
    vals = [v for v in traces[0]["y"] if v is not None]
    rising = len(vals) > 1 and vals[-1] >= vals[0]
    note = C._note(f"{what} · as of {max(asof_all)}")
    if len(ccys) > 1 or rising:
        note.update({"x": 1, "xanchor": "right", "y": 0, "yanchor": "bottom"})
    else:
        note.update({"x": 1, "xanchor": "right", "y": 1, "yanchor": "top"})
    lay["annotations"] = [note]
    out = {"data": traces, "layout": lay, "sources": _sources(ccys, fallback_ccys, include_fed_range=include_fed_range), "as_of": max(asof_all)}    # the newest futures print (x = meetings ahead)
    st = _stale(min(built_all), today)
    if st:
        out["stale"] = st
    return out
