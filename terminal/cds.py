"""CDS index levels from swap-repository prints (HANDOFF P3-2): CDX IG / HY / EM, iTraxx Europe Main / Crossover.

Data: `cftc/cds_index_trades` (DTCC public dissemination under CFTC rules), one daily file per dissemination date, 5Y
tenor, new trades only (`action_type` NEWT, `event_type` TRAD). Conventions, declared rather than inferred:
- IG, iTraxx Main and Crossover are spread-quoted: notation '3' is a decimal (x 10,000 = bp), '4' is already bp.
- HY and EM are price-quoted: notation '1' is the price in points, '3' is the price / 10,000 (x 10,000 = points).
  A price is never shown in bp.
- The data carries no series number: prints are grouped by index family and maturity date, and the maturity with the
  most notional that day is the on-the-run series. Changes compare that same maturity on the earlier day, so a roll to
  a new series never shows as a jump; when the earlier day has no print at that maturity the change is blank.
- Level = notional-weighted average of the day's prints; a notional above the reporting cap is disseminated at the cap,
  so the weights are floors for the largest trades.
Limits: index CDS only (no single-name CDS); prints, not dealer quotes; weekends and holidays carry almost no prints.
Each day is summarised once and kept on disk (past files do not change; 366 days are retained at the source).
"""

import datetime as dt
import json
import threading

import composite as C

# family (UPI underlier name): (short name, quote convention)
FAMILIES = {
    "CDX.NA.IG": ("CDX IG", "spread"),
    "CDX.NA.HY": ("CDX HY", "price"),
    "ITRAXX EUROPE": ("iTraxx Main", "spread"),
    "ITRAXX EUROPE CROSSOVER": ("iTraxx Crossover", "spread"),
    "CDX.EM": ("CDX EM", "price"),
}
UNIT = {"spread": "bp", "price": "points"}
OPTIONS = [{"label": v[0], "value": k} for k, v in FAMILIES.items()]
RANGES = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12}
# index series mature on 20 June or 20 December; other dates (seen live: 2031-12-22 at spread 0, 1-3 prints a day,
# the only IG print on the 2026-11-11 holiday) are bespoke trades, not the index
STANDARD_MATURITY = {"-06-20", "-12-20"}
SUMMARY_REV = "r3"              # bump when summarise() changes: summaries of past days are kept for good
FINAL_AFTER_DAYS = 2            # a dissemination file older than this is complete: its summary is kept for good
RECENT_TTL = 1800               # seconds a recent day, or a day with no index prints, is kept before it is fetched again
FINAL_TTL = 10 * 365 * 86400    # a final day with prints: kept for good
CDS_URL = "https://pddata.dtcc.com/ppd/cftcdashboard"
SOURCE = C.src("CFTC", "swap-repository prints (DTCC public dissemination): index CDS, 5Y, new trades; level = notional-weighted "
                       "average of the on-the-run series (most-traded maturity)", CDS_URL)

_lock = threading.Lock()
_days = None                    # the old one-file store {date: {"t", "s"}}: read once, only to seed final days without a fetch


def level(r, convention):
    """A print's level in the family's unit (bp for spread-quoted, points for price-quoted); None if not stated."""
    s, n = r.get("spread"), str(r.get("spread_notation") or "").strip()
    if not isinstance(s, (int, float)) or isinstance(s, bool):
        return None
    if convention == "spread":
        return s * 1e4 if n == "3" else float(s) if n == "4" else None
    return float(s) if n == "1" else s * 1e4 if n == "3" else None


# a print further than this from the day's notional-weighted median (same series) is off-market: a misreported
# convention, seen live as iTraxx Main 642,125 "bp" (2026-10-15) and CDX HY 10,579 "points" (2026-11-16)
BAND = {"spread": 0.5, "price": 0.2}


def weighted_median(pts):
    """Median of (level, notional) pairs by notional: the first level at which half the notional is reached."""
    pts = sorted(pts)
    half, cum = sum(n for _, n in pts) / 2, 0.0
    for v, n in pts:
        cum += n
        if cum >= half:
            return v
    return pts[-1][0]


def summarise(rows):
    """{family: {maturity: {level, low, high, trades, notional, currency, dropped}}} from one day's prints. Level =
    notional-weighted mean of the prints within BAND of the weighted median; low/high/trades/notional count those."""
    acc = {}
    for r in rows or []:
        fam = str(r.get("index") or "").upper()
        if fam not in FAMILIES or r.get("action_type") != "NEWT" or r.get("event_type") != "TRAD":
            continue
        lv, notional, mat = level(r, FAMILIES[fam][1]), r.get("notional_amount"), str(r.get("maturity_date") or "")[:10]
        if lv is None or lv <= 0 or mat[4:] not in STANDARD_MATURITY or not isinstance(notional, (int, float)) or notional <= 0:
            continue
        acc.setdefault(fam, {}).setdefault(mat, []).append((lv, float(notional), r.get("notional_currency") or ""))
    out = {}
    for fam, mats in acc.items():
        band = BAND[FAMILIES[fam][1]]
        for mat, prints in mats.items():
            med = weighted_median([(v, n) for v, n, _ in prints])
            kept = [x for x in prints if abs(x[0] - med) <= band * med]
            n = sum(x[1] for x in kept)
            ccy = {}
            for _, k, c in kept:
                ccy[c] = ccy.get(c, 0) + k
            out.setdefault(fam, {})[mat] = {"level": round(sum(v * k for v, k, _ in kept) / n, 4), "low": round(min(x[0] for x in kept), 4),
                                            "high": round(max(x[0] for x in kept), 4), "trades": len(kept), "notional": n,
                                            "currency": max(ccy, key=ccy.get), "dropped": len(prints) - len(kept)}
    return out


def on_the_run(mats):
    """(maturity, summary) with the most notional; a tie goes to the later maturity (the newer series)."""
    if not mats:
        return None, None
    m = max(mats, key=lambda k: (mats[k]["notional"], k))
    return m, mats[m]


def _path():
    return C.CACHE_DIR / f"cds_index_days_{SUMMARY_REV}.json"      # the old one-file store (read-only seed)


def _load():
    global _days
    with _lock:
        if _days is None:
            try:
                _days = json.loads(_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _days = {}
        return _days


def _fetch_day(d):
    return summarise(C.api(C.V5, "cftc/cds_index_trades", tenor="5Y", date=d))


def day_name(d):
    return f"cdsday_{SUMMARY_REV}_{d}"             # a new rule for summarising a day = new entries


def day(d, today=None):
    """Summary of dissemination date d (ISO), on the shared cache: a final day with prints is kept for good; a recent
    day, or a day with no index prints (an empty file is as likely a late or failed dissemination as a quiet day), is
    fetched again after 30 minutes. A failed fetch serves the saved summary, or raises."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    final = (today - dt.date.fromisoformat(d)).days >= FINAL_AFTER_DAYS

    def fetch():
        old = (_load().get(d) or {}).get("s") if final else None
        return old if old else _fetch_day(d)       # a final day with prints in the old store needs no fetch

    return C.cache(("cds_day", d), lambda s: FINAL_TTL if final and s else RECENT_TTL, fetch, disk=day_name(d))


def days(dates, today=None):
    """{date: summary} for several dates, four fetches at a time; a day that fails is left out."""
    out = {}
    with C.cf.ThreadPoolExecutor(4) as ex:
        futs = {ex.submit(day, d, today): d for d in dates}
        for f, d in futs.items():
            try:
                out[d] = f.result()
            except C.CompositeError as e:
                C.partial_from(e, f"CFTC swap file of {d}")
                continue
    return out


def weekdays(start, end):
    """ISO weekdays from start to end inclusive, newest first (weekend files carry almost no prints)."""
    out, d = [], end
    while d >= start:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return out


REF_WINDOW = 5                  # days: an earlier level is taken from at most this far before the target date


def _ref(hist, target, mat):
    """Level of maturity mat on the newest day in hist (newest first) dated on or before target and at most REF_WINDOW
    days before it (a holiday such as Veterans Day has a few prints, often none in the on-the-run series)."""
    t = dt.date.fromisoformat(target)
    for d, mats in hist:
        if d > target:
            continue
        if (t - dt.date.fromisoformat(d)).days > REF_WINDOW:
            return None
        if mat in mats:
            return mats[mat]["level"]
    return None


def _chg(cur, ref):
    return None if ref is None else round(cur - ref, 2)


def table(today=None, **_):
    """One row per index: latest day's level, range, trades and notional, with 1D / 1W / 1M changes."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    last = today - dt.timedelta(days=1)                   # today's file is still filling during the day
    month_ago = C.minus_months(last, 1)
    dates = weekdays(month_ago - dt.timedelta(days=4), last)
    got = days(dates, today)
    series = [(d, got[d]) for d in dates if got.get(d)]
    if not series:
        raise C.CompositeError("No index CDS prints in the last month of CFTC swap-repository files.")
    rows = []
    for fam, (name, conv) in FAMILIES.items():
        hist = [(d, s[fam]) for d, s in series if s.get(fam)]
        if not hist:
            continue
        d0, mats0 = hist[0]
        mat, cur = on_the_run(mats0)
        d0d = dt.date.fromisoformat(d0)
        earlier = hist[1:]
        ref_1d = _ref(earlier, (d0d - dt.timedelta(days=1)).isoformat(), mat)
        ref_1w = _ref(earlier, (d0d - dt.timedelta(days=7)).isoformat(), mat)
        ref_1m = _ref(earlier, C.minus_months(d0d, 1).isoformat(), mat)
        row = {"index": name, "level": round(cur["level"], 2), "unit": UNIT[conv],
               "chg_1d": _chg(cur["level"], ref_1d), "chg_1w": _chg(cur["level"], ref_1w), "chg_1m": _chg(cur["level"], ref_1m),
               "low": round(cur["low"], 2), "high": round(cur["high"], 2), "trades": cur["trades"],
               "notional_mn": round(cur["notional"] / 1e6), "currency": cur["currency"], "maturity": mat, "date": d0, "family": fam,
               "dropped": cur.get("dropped", 0)}
        if earlier and ref_1d is None:
            row["note"] = f"new series: no print at this maturity in the {REF_WINDOW} days before"
        rows.append(row)
    out = {"results": rows, "sources": [SOURCE]}
    newest = max(r["date"] for r in rows)
    if (today - dt.date.fromisoformat(newest)).days > 5:
        out["stale"] = f"Latest index CDS prints are from {newest}"
    return out


def history(family="CDX.NA.IG,CDX.NA.HY", range="3M", today=None, **_):
    """Daily on-the-run level per index; spread-quoted indices in bp on the left axis, price-quoted in points on the
    right. The line breaks where the on-the-run maturity changes (a roll to a new series)."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    fams = [f.upper() for f in C._split(family) if f.upper() in FAMILIES] or ["CDX.NA.IG"]
    fams = list(dict.fromkeys(fams))
    rng = str(range or "3M").upper()
    rng = rng if rng in RANGES else "3M"
    last = today - dt.timedelta(days=1)
    dates = weekdays(C.minus_months(last, RANGES[rng]), last)
    got = days(dates, today)
    traces = []
    convs = {FAMILIES[f][1] for f in fams}
    for i, fam in enumerate(fams):
        name, conv = FAMILIES[fam]
        pts = []
        for d in reversed(dates):
            mats = (got.get(d) or {}).get(fam) or {}
            if pts:                     # a series never rolls back: a day with only older maturities (a holiday) is skipped
                mats = {m: v for m, v in mats.items() if m >= pts[-1][2]}
            mat, cur = on_the_run(mats)
            if cur:
                pts.append((d, cur["level"], mat))
        if not pts:
            continue
        xs, ys, prev = [], [], None
        for d, v, mat in pts:
            if prev is not None and mat != prev:
                xs.append(d)
                ys.append(None)
            xs.append(d)
            ys.append(round(v, 2))
            prev = mat
        right = conv == "price" and len(convs) > 1
        tr = {"type": "scatter", "mode": "lines", "name": f"{name}  {pts[-1][1]:.2f} {UNIT[conv]}", "x": xs, "y": ys, "connectgaps": False,
              "line": {"width": 2, "color": C.PALETTE[i % len(C.PALETTE)]}, "hovertemplate": "%{y:.2f} " + UNIT[conv] + "<extra>" + name + "</extra>"}
        if right:
            tr["yaxis"] = "y2"
        traces.append(tr)
    if not traces:
        raise C.CompositeError("No index CDS prints in the CFTC swap-repository files for: " + ", ".join(FAMILIES[f][0] for f in fams))
    lay = C._layout(True)
    only = next(iter(convs))
    lay["yaxis"]["ticksuffix"] = " bp" if only == "spread" or len(convs) > 1 else ""
    lay["yaxis"]["title"] = {"text": "spread, bp" if only == "spread" or len(convs) > 1 else "price, points", "font": {"size": 10}}
    if len(convs) > 1:
        lay["yaxis2"] = {"overlaying": "y", "side": "right", "showgrid": False, "zeroline": False, "automargin": True,
                         "title": {"text": "price, points (HY, EM)", "font": {"size": 10}}}
    lay["annotations"] = [C._note("5Y on-the-run; gap = series roll")]
    return {"data": traces, "layout": lay, "sources": [SOURCE], "controls": {"freq": "daily", "ranges": list(RANGES), "range": rng}}
