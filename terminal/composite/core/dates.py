"""Horizon dates, range windows, period alignment, and quarter helpers."""

from __future__ import annotations

import calendar
import datetime as dt
import re

import composite as C

# ---------------------------------------------------------------- dates
def minus_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.year * 12 + d.month - 1 - n, 12)
    m += 1
    return dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


HORIZON_MONTHS = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12, "2Y": 24, "3Y": 36, "5Y": 60, "10Y": 120}


def horizon_date(last: dt.date, key: str) -> dt.date:
    key = key.upper()
    if key == "1D":
        return last - dt.timedelta(days=1)
    if key in ("1W", "5D"):
        return last - dt.timedelta(days=7)
    if key == "YTD":
        return dt.date(last.year - 1, 12, 31)
    if key in HORIZON_MONTHS:
        return C.minus_months(last, HORIZON_MONTHS[key])
    raise C.CompositeError(f"Unknown period '{key}'.")


# Range chips on chart cards (the card's control strip): what fits the frequency of the series shown.
RANGE_YEARS = {"1Y": 1, "3Y": 3, "5Y": 5, "10Y": 10, "20Y": 20}

DAILY_RANGES = ["1Y", "3Y", "5Y", "10Y", "MAX"]

MONTHLY_RANGES = ["3Y", "5Y", "10Y", "20Y", "MAX"]

QUARTERLY_RANGES = ["5Y", "10Y", "20Y", "MAX"]

ANNUAL_RANGES = ["10Y", "20Y", "MAX"]


def range_start(rng, since, default="2015"):
    """ISO start date: a range chip (1Y, 5Y, MAX) wins over the card's own 'since' year."""
    r = str(rng or "").upper()
    if r in RANGE_YEARS:
        t = dt.date.today()
        return t.replace(year=t.year - RANGE_YEARS[r], day=min(t.day, 28)).isoformat()
    if r == "MAX":
        return "1950-01-01"
    y = re.sub(r"\D", "", str(since or ""))[:4] or default
    return f"{y}-01-01"


def freq_of(pts):
    if len(pts) < 3:
        return "monthly"
    tail = pts[-40:]
    gaps = sorted((dt.date.fromisoformat(b[0][:10]) - dt.date.fromisoformat(a[0][:10])).days for a, b in zip(tail, tail[1:]))
    g = gaps[len(gaps) // 2]
    return "daily" if g < 4 else "weekly" if g < 10 else "monthly" if g < 40 else "quarterly" if g < 120 else "annual"


FREQ_RANGES = {"daily": DAILY_RANGES, "weekly": DAILY_RANGES, "monthly": MONTHLY_RANGES, "quarterly": QUARTERLY_RANGES, "annual": ANNUAL_RANGES}


def _month_start():
    """First day of the current month (ISO): where EIA STEO history ends and its forecast begins."""
    return dt.date.today().replace(day=1).isoformat()


def _period_date(p):
    p = str(p)
    if len(p) >= 10:
        return p[:10]
    if "Q" in p:
        y, q = p.split("-Q") if "-Q" in p else p.split("Q")
        return f"{y}-{(int(q) - 1) * 3 + 1:02d}-01"
    if len(p) == 7:
        return p + "-01"
    return p[:4] + "-01-01"


def _next_month(iso):
    y, m = int(iso[:4]), int(iso[5:7])
    return f"{y + (m == 12)}-{1 if m == 12 else m + 1:02d}-01"


def _date_span(v):
    """(first ISO date, last ISO date or None) from Yahoo's earnings date: one date, or a list/"a, b" range when the company
    has not confirmed (audit C9: the card printed "['2026-01-2")."""
    if isinstance(v, (list, tuple)):
        ds = sorted(str(x)[:10] for x in v if x)
    else:
        ds = sorted(re.findall(r"\d{4}-\d{2}-\d{2}", str(v or "")))
    if not ds:
        return None, None
    return ds[0], (ds[-1] if ds[-1] != ds[0] else None)


def _today():
    return dt.date.today()


def _quarter(iso):
    d = dt.date.fromisoformat(iso[:10])
    return f"{d.year} Q{(d.month - 1) // 3 + 1}"
