"""Return, YoY, rolling, interpolation, and number formatting helpers."""

from __future__ import annotations

import bisect
import datetime as dt

import composite as C

from .num import banded, finite, num


def _is_finite_num(v):
    return isinstance(v, (int, float)) and finite(v) is not None

def _at_or_before(series, iso):
    # series items may be tuples (fresh) or lists (read back from the disk cache)
    i = bisect.bisect_right([p[0] for p in series], iso) - 1
    return series[i][1] if i >= 0 else None


def _base(series, key):
    last_d = dt.date.fromisoformat(series[-1][0])
    if key == "1D":
        return series[-2][1]
    return C._at_or_before(series[:-1], C.horizon_date(last_d, key).isoformat())


def _ret(series, key):
    if len(series) < 2:
        return None
    last = series[-1][1]
    base = C._base(series, key)
    if not (_is_finite_num(last) and _is_finite_num(base)) or base == 0:
        return None
    r = last / base - 1
    return banded("daily_return", r) if key == "1D" else r     # a one-day move past -90%/+900% is a bad print


def _diff_bp(series, key):
    """Change of a yield or rate over the horizon, in basis points (a yield's move is never quoted in percent of itself)."""
    if len(series) < 2:
        return None
    last = series[-1][1]
    base = C._base(series, key)
    if not (_is_finite_num(last) and _is_finite_num(base)):
        return None
    if banded("yield_pct", last) is None or banded("yield_pct", base) is None:      # a yield outside -5%..40% is a bad print
        return None
    return round((last - base) * 100, 1)


def total_return(series, divs):
    """Total-return index from closes and cash distributions on their ex-dates: each distribution is reinvested at the
    previous close (factor *= 1 + d / previous close; value = close x factor). Same dates as the closes; with no
    distributions it is the closes themselves. Yahoo closes leave distributions out (HYG fell 77.43 -> 76.89 on the
    2026-11-02 ex-date of 0.401), so a price return understates a bond fund by its yield."""
    if not divs:
        return series
    out, factor, prev = [], 1.0, None
    for d, c in series:
        if not _is_finite_num(c):
            continue
        x = divs.get(d)
        if _is_finite_num(x) and _is_finite_num(prev) and prev > 0 and x >= 0:
            factor *= 1 + x / prev
        out.append((d, c * factor))
        prev = c
    return out


def _num_or_none(s):
    """A number from text that may use a decimal comma ("4,25"), or None."""
    return num(str(s).strip().replace(",", "."))


def _nearest(hist, dates, max_gap=20):
    """The latest curve (dates None), or for each wanted date the closest available one within max_gap days."""
    if not hist:
        return {}
    if not dates:
        d = max(hist)
        return {d: hist[d]}
    avail = sorted(hist)
    out = {}
    for want in dates:
        near = min(avail, key=lambda x: abs(dt.date.fromisoformat(x) - want))
        if abs(dt.date.fromisoformat(near) - want).days <= max_gap:
            out[near] = hist[near]
    return out


def _pick(points, years):
    for my, rate in points:
        if abs(my - years) < 1e-6:
            return rate
    return None


def _interp(points, years):
    """Exact tenor if present, else linear interpolation between the two neighbouring maturities (never extrapolated)."""
    exact = C._pick(points, years)
    if exact is not None or len(points) < 2:
        return exact
    for (a, ra), (b, rb) in zip(points, points[1:]):
        if a < years < b and b != a and _is_finite_num(ra) and _is_finite_num(rb):
            return round(ra + (rb - ra) * (years - a) / (b - a), 4)
    return None


def _latest(curve_by_date):
    if not curve_by_date:
        return None, []
    d = max(curve_by_date)
    return d, curve_by_date[d]


def _split(value):
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value or "").split(",") if v.strip()]


def _yoy(points, diff_bp=False, diff=None):
    """Year-over-year change: percent; or the difference x `diff` (rates: x 100 = bp, ratios in % and spreads in bp: x 1);
    diff_bp=True is diff=100.

    The base is the earlier observation nearest to one year before (either side), accepted within 20 days: weekly data
    then compares with 52 weeks earlier (the point 364 days back), not 53; a hole a year earlier gives no point."""
    out = []
    dates = [p[0][:10] for p in points]
    for i, (d, v) in enumerate(points):
        target = dt.date.fromisoformat(d[:10]) - dt.timedelta(days=365)
        k = bisect.bisect_left(dates, target.isoformat(), 0, i)
        near = [(abs((dt.date.fromisoformat(dates[j]) - target).days), j) for j in (k - 1, k) if 0 <= j < i]
        gap, j = min(near) if near else (None, None)
        if j is None or gap > 20:
            continue
        if not _is_finite_num(v) or not _is_finite_num(points[j][1]):
            continue
        if diff or diff_bp:
            out.append([d, (v - points[j][1]) * (diff or 100)])
        elif points[j][1]:
            out.append([d, (v / points[j][1] - 1) * 100])
    return out


def yoy_difference(suffix, default_tf):
    """(unit, factor) when a set's level changes year-over-year as a difference, else None (percent change).

    A level in percent changes in basis points ("%": a rate, difference x 100) or in percentage points ("% of GDP": a
    ratio, difference x 1); a level already in basis points (spreads) changes in bp (x 1). Never in percent of itself:
    federal debt 118.0 -> 121.2 % of GDP is +3.2 pp, not +2.7%."""
    s = suffix.strip()
    if default_tf == "yoy":
        return None                             # the suffix belongs to the yoy view itself (CPI: "%")
    if s == "%":
        return (" bp", 100)
    if s.startswith("%"):
        return (" pp", 1)
    if s == "bp":
        return (" bp", 1)
    return None


def transform_unit(suffix, default_tf, tf, level_units=()):
    """(axis suffix, axis title or None, yoy difference factor or None) for a curated chart shown with transform `tf`.

    A set's suffix describes its *default* view: "%" on a set shown year-over-year by default (CPI, retail sales) is
    the unit of the growth rate, not of the level. So the level unit is only known when the default is the level;
    otherwise the source's own unit name (FRED/EIA metadata) becomes the axis title. Rates, ratios and spreads change
    year-over-year as differences (yoy_difference); everything else in percent.
    """
    if tf == "yoy":
        diff = C.yoy_difference(suffix, default_tf)
        return (diff[0], None, diff[1]) if diff else ("%", None, None)
    if tf == "index":
        return ("", None, None)
    if default_tf == "yoy":                     # level (or seasonal) of a set whose suffix belongs to its yoy view
        units = {u for u in level_units if u}
        return ("", units.pop() if len(units) == 1 else None, None)
    return (suffix, None, None)


def _rolling(pts, days):
    out, window, total = [], [], 0.0
    for d, v in pts:
        cut = (dt.date.fromisoformat(d) - dt.timedelta(days=days)).isoformat()
        window.append((d, v)); total += v
        while window and window[0][0] <= cut:
            total -= window.pop(0)[1]
        out.append([d, total / len(window)])
    return out


def _sma(vals, n):
    out, total = [], 0.0
    for i, v in enumerate(vals):
        total += v
        if i >= n:
            total -= vals[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def drawdown(values):
    """Percent below the running high, for each value (0 at a new high). Values must be positive prices."""
    out, peak = [], None
    for v in values:
        if not _is_finite_num(v) or v <= 0:
            out.append(None)
            continue
        peak = v if peak is None or v > peak else peak
        out.append((v / peak - 1) * 100 if peak else None)
    return out


def _big(v, ccy=""):
    if not _is_finite_num(v):
        return "–"
    a = abs(v)
    s = f"{v / 1e12:,.2f}T" if a >= 1e12 else f"{v / 1e9:,.1f}B" if a >= 1e9 else f"{v / 1e6:,.0f}M" if a >= 1e6 else f"{v:,.0f}"
    return f"{s} {ccy}".strip()


def _num(v, fmt="{:,.2f}", suffix=""):
    return fmt.format(v) + suffix if _is_finite_num(v) else "–"


def _pc(v, signed=False):
    return (f"{v * 100:+.1f}%" if signed else f"{v * 100:.1f}%") if _is_finite_num(v) else "–"
