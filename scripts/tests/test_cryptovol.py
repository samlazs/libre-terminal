"""Review H4 (terminal/cryptovol.py): 30-day realised volatility by hand, and the chart from stubbed answers."""

import datetime as dt
import math

from _common import check, done

import composite as c
import cryptovol as cv

# returns alternating +1% / -1% (log): mean 0, sample sd = 0.01 * sqrt(30/29); x sqrt(365) x 100 = 19.4316%
closes, p = [], 100.0
for i in range(32):
    closes.append(((dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat(), p))
    p *= math.exp(0.01 if i % 2 == 0 else -0.01)
rv = cv.realised(closes)
check(len(rv) == 2 and rv[0][0] == "2026-10-31", "first value once 30 returns exist (31st close): " + str(rv[:1]))
check(abs(rv[0][1] - 19.4316) < 0.001, f"alternating 1% moves: 19.4316% by hand, got {rv[0][1]:.4f}")
check(cv.realised(closes[:30]) == [], "fewer than 30 returns: no value")
# a zero price makes the two returns around it unusable: the first value waits for 30 clean returns after it
_z = cv.realised([("2026-09-30", 1.0), ("2026-10-01", 0.0)] + closes[1:])
check([d for d, _ in _z] == ["2026-11-01"], "a zero price breaks the window instead of producing a number: " + str(_z))

# chart: today's (incomplete) UTC day left out of both lines; the gap is implied minus realised on shared dates
TODAY = dt.date(2026, 11, 19)
px = [{"date": (TODAY - dt.timedelta(days=60 - i)).isoformat(), "close": 100.0 * math.exp(0.01 if i % 2 == 0 else -0.01) ** (i % 2)} for i in range(61)]
dv = [{"date": (TODAY - dt.timedelta(days=k)).isoformat() + "T00:00:00Z", "close": 40.0} for k in range(0, 20)]
saved = c.api, c.cached
try:
    c.api = lambda base, path, **kw: dv if path.startswith("deribit") else px
    c.cached = lambda key, ttl, fn: fn()
    out = cv.chart(currency="btc", range="3M", today=TODAY)
finally:
    c.api, c.cached = saved
names = [t["name"] for t in out["data"]]
check(len(out["data"]) == 3 and names[0].startswith("Implied (DVOL, 30-day)  40.0%"), "three traces, implied first: " + str(names))
check(out["data"][0]["x"][-1] == "2026-11-18" and out["data"][1]["x"][-1] == "2026-11-18", "today's open day left out of both lines")
gap = out["data"][2]
check(abs(gap["y"][-1] - (40.0 - out["data"][1]["y"][-1])) < 0.01, "gap = implied - realised on the same day")
check([s["name"] for s in out["sources"]] == ["Deribit", "Yahoo Finance"], "sources carried")

done("test_cryptovol")
