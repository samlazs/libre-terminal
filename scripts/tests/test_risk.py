"""Stage 3 3a/14a (terminal/risk.py): frequency choice, returns, correlation, risk stats and the FX differential by hand."""

import datetime as dt
import math

from _common import check, done

import composite as c
import risk as r

check(r.auto_freq(["SPY", "TLT", "GLD"])[0] == "daily" and r.auto_freq(["SPY", "BTC-USD"])[0] == "weekly" and r.auto_freq(["SAP.DE", "SIE.DE"])[0] == "daily"
      and r.auto_freq(["SPY", "SAP.DE"])[0] == "weekly" and r.auto_freq(["EURUSD=X", "GBPUSD=X"])[0] == "weekly", "daily only when every symbol closes in one market")
# 2026-11-02 is a Monday; week of 11-02: Mon..Sun, Friday 11-06 kept, Sunday dropped
cl = [(f"2026-11-{d:02d}", float(d)) for d in range(2, 16)]
check(r.resample(cl, "weekly") == [("2026-11-06", 6.0), ("2026-11-13", 13.0)], "weekly = Friday close, weekends ignored")
check(r.returns([("a", 100.0), ("b", 110.0), ("c", 99.0)]) == {"b": 0.10000000000000009, "c": -0.09999999999999998} or
      all(abs(x - y) < 1e-12 for x, y in zip(r.returns([("a", 100.0), ("b", 110.0), ("c", 99.0)]).values(), (0.1, -0.1))), "simple returns")
check(abs(r.corr([1, 2, 3, 4], [2, 4, 6, 8]) - 1) < 1e-12 and abs(r.corr([1, 2, 3, 4], [8, 6, 4, 2]) + 1) < 1e-12, "correlation +1 / -1")
check(r.max_drawdown([100, 120, 90, 130, 117]) == -25.0, "max drawdown 120 -> 90 = -25%")
check(abs(r.beta([0.02, -0.02, 0.04], [0.01, -0.01, 0.02]) - 2.0) < 1e-12, "beta of a 2x series = 2")

# risk_stats with stubs: returns +1%/-1% alternating daily -> sd = 0.01 x sqrt(n/(n-1))
D0 = dt.date(2026, 1, 5)
days = [D0 + dt.timedelta(days=i) for i in range(400) if (D0 + dt.timedelta(days=i)).weekday() < 5][:253]
px, p = [], 100.0
for i, d in enumerate(days):
    px.append((d.isoformat(), p))
    p *= 1.01 if i % 2 == 0 else 1 / 1.01
saved = c.history, c._fred
try:
    c.history = lambda syms, start: {s: list(px) for s in syms}
    c._fred = lambda ids, start: ({"DGS3MO": [(days[0].isoformat(), 4.0), (days[-1].isoformat(), 4.0)]}, {})
    out = r.risk_stats(symbols="AAA,BBB", benchmark="BBB", range="1Y", today=days[-1] + dt.timedelta(days=1))
    a = out["results"][0]
    rets = list(r.returns(px).values())
    sd = r.stdev(rets)
    check(a["vol"] == round(sd * math.sqrt(252) * 100, 2) and a["beta"] == 1.0, f"annualised vol x sqrt(252) = {a['vol']}%, beta 1 vs itself")
    check(a["sharpe"] == round((sum(rets) / len(rets) - 0.04 / 252) * 252 / (sd * math.sqrt(252)), 2), "Sharpe = mean excess x 252 / annual vol, rf 4%")
    check("daily: all close in the same market" in out["convention"] and "4.00%" in out["convention"], "convention names frequency and risk-free rate")
    ch = r.correlation(symbols="AAA,BBB", today=days[-1] + dt.timedelta(days=1))
    check(ch["data"][0]["z"] == [[1.0, 1.0], [1.0, 1.0]], "identical series correlate at 1")
finally:
    c.history, c._fred = saved

# FX: EUR 2Y 2.10 - US 2Y 3.60 = -150 bp; dates matched
check(r.differential([("d1", 2.10), ("d2", 2.20)], [("d1", 3.60), ("d3", 3.5)]) == [("d1", -150.0)], "base minus quote in bp, common dates only")
sp = [(f"d{i:03d}", 1.0 + 0.001 * i + (0.002 if i % 2 else 0)) for i in range(70)]
df = [(f"d{i:03d}", 10.0 * i + (20 if i % 2 else 0)) for i in range(70)]
rc = r.rolling_corr(sp, df)
check(len(rc) == 10 and rc[-1][1] > 0.99, "rolling 60-day correlation: same zig-zag moves correlate near +1")
check([x["name"] for x in r.fx_sources("EURUSD")] == ["Yahoo Finance", "Bundesbank", "FRED"], "EURUSD: Yahoo, Bundesbank 2Y, FRED DGS2")
check(all(k in c.ROUTES for k in ("correlation", "risk_stats", "fx_rate_diff")), "routes")
done("test_risk")
