"""Fixed-rate bond arithmetic for TRACE rows and the pricer (Stage 3, item 4).

Convention (U.S. corporate street convention): semiannual coupons, 30/360 day count, prices per 100 of par, clean price
quoted, accrued interest = coupon / 2 x days since the last coupon / 180. Yield to maturity solved from the dirty price
with fractional first period w = days to next coupon / 180 (30/360):
    dirty = sum_k CF_k / (1 + y/2) ** (k - 1 + w)
Modified duration and convexity from the dirty price by central differences of 1 bp; DV01 = dollar value of one basis
point per 100 par. Callables are computed to maturity (the call is ignored, said in the table).
"""

import datetime as dt

TENORS = [2, 3, 5, 7, 10, 20, 30]           # on-the-run Treasury tenors


def d360(a, b):
    """30/360 (U.S. bond basis) days from a to b."""
    d1, d2 = min(a.day, 30), b.day
    if d1 == 30 and d2 == 31:
        d2 = 30
    return 360 * (b.year - a.year) + 30 * (b.month - a.month) + (d2 - d1)


def _add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def schedule(settle, maturity):
    """(previous coupon date, [coupon dates after settle up to maturity]) stepping back 6 months from maturity."""
    dates, d, k = [], maturity, 0
    while d > settle:
        dates.append(d)
        k += 1
        d = _add_months(maturity, -6 * k)
    return d, sorted(dates)


def price(y, coupon, settle, maturity):
    """(dirty, accrued) per 100 par for yield y (decimal)."""
    prev, dates = schedule(settle, maturity)
    if not dates:
        return None, None
    c = coupon / 2
    accrued = c * d360(prev, settle) / 180
    w = d360(settle, dates[0]) / 180
    v = 1 + y / 2
    dirty = sum((c + (100 if i == len(dates) - 1 else 0)) / v ** (i + w) for i in range(len(dates)))
    return dirty, accrued


def ytm(clean, coupon, settle, maturity):
    """Yield to maturity (decimal) from a clean price, by bisection between -50% and 200%; None when no root."""
    if not clean or clean <= 0 or maturity <= settle:
        return None
    _, acc = price(0.05, coupon, settle, maturity)
    target = clean + acc
    lo, hi = -0.5, 2.0
    f = lambda y: price(y, coupon, settle, maturity)[0] - target  # noqa: E731
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2


def risk(y, coupon, settle, maturity):
    """{dirty, accrued, mod_duration, convexity, dv01} at yield y (decimal)."""
    p0, acc = price(y, coupon, settle, maturity)
    if p0 is None:
        return None
    h = 0.0001
    up, dn = price(y + h, coupon, settle, maturity)[0], price(y - h, coupon, settle, maturity)[0]
    return {"dirty": p0, "accrued": acc, "mod_duration": (dn - up) / (2 * h * p0), "convexity": (up + dn - 2 * p0) / (h * h * p0), "dv01": (dn - up) / 2}


def years_between(a, b):
    return (b - a).days / 365.25


def benchmark_tenor(years):
    """Nearest on-the-run Treasury tenor (years) to the remaining maturity."""
    return min(TENORS, key=lambda t: (abs(t - years), t)) if years > 0 else None


def interp(points, years):
    """Linear interpolation on [(years, rate)], flat beyond the ends."""
    pts = sorted(points)
    if not pts:
        return None
    if years <= pts[0][0]:
        return pts[0][1]
    if years >= pts[-1][0]:
        return pts[-1][1]
    for (a, ra), (b, rb) in zip(pts, pts[1:]):
        if a <= years <= b:
            return ra + (rb - ra) * (years - a) / (b - a)
    return None


def isin_us(cusip):
    """US ISIN from a 9-character CUSIP (Luhn check digit on the letters-as-numbers string)."""
    if not cusip or len(cusip) != 9 or not cusip.isascii() or not cusip.isalnum():
        return None                           # '*', '@', '#' (private placements) carry no ISIN here
    body = "US" + cusip.upper()
    digits = "".join(str(int(ch, 36)) for ch in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return body + str((10 - total % 10) % 10)
