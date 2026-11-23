"""Stage 3 Wave 4: ETF overlap (terminal/etfs.py), GEX / Max Pain and the volatility cone (terminal/optionsx.py), by hand."""

import datetime as dt
import math

from _common import check, done

import composite as c
import etfs
import optionsx as ox

A = [{"name": "Xco Inc", "cusip": "111111111", "isin": None, "weight": 10.0}, {"name": "Y Corp", "cusip": None, "isin": "US0000000001", "weight": 5.0},
     {"name": "Foo Inc", "cusip": None, "isin": None, "weight": 3.0}, {"name": "Only A", "cusip": "222222222", "isin": None, "weight": 2.0}]
B = [{"name": "X CO", "cusip": "111111111", "isin": None, "weight": 4.0}, {"name": "Y Corporation", "cusip": "999999999", "isin": "US0000000001", "weight": 6.0},
     {"name": "FOO CORP", "cusip": None, "isin": None, "weight": 1.0}, {"name": "Only B", "cusip": "333333333", "isin": None, "weight": 50.0}]
rows, w, n = etfs.overlap(A, B)
check(n == 3 and w == 10.0 and [r[0] for r in rows] == ["Y Corp", "Xco Inc", "Foo Inc"], "matched by CUSIP, ISIN, then name; overlap = 4 + 5 + 1 = 10%: " + str(rows))
check(etfs._ident("000000000", 9) is None and etfs._ident("N/A", 3) is None, "placeholder identifiers never match")

g = ox.bs_gamma(100, 100, 1.0, 0.2)
check(abs(g - 0.0198476) < 1e-6, f"Black-Scholes gamma S=K=100, 20%, 1y: phi(0.1)/(100 x 0.2) = 0.0198476, got {g:.7f}")
T0 = dt.date(2026, 11, 23)
one = [{"strike": 100.0, "type": "call", "oi": 1000.0, "iv": 0.2, "expiration": (T0 + dt.timedelta(days=365)).isoformat()}]
gx = ox.gex_by_strike(one, 100.0, T0)
check(abs(gx[100.0] - 0.198476) < 1e-5, "1,000 calls: 1000 x gamma x 100 x 100^2 x 1% = $0.198mn per 1%")
gx2 = ox.gex_by_strike(one + [dict(one[0], type="put")], 100.0, T0)
check(abs(gx2[100.0]) < 1e-12, "dealers long calls, short puts: equal call and put open interest nets to zero")
check(ox.gex_by_strike([dict(one[0], strike=130.0)], 100.0, T0) == {} and ox.gex_by_strike([dict(one[0], iv=5.0)], 100.0, T0) == {}, "outside 25% of spot or IV > 300%: left out")
mp = [{"strike": 90.0, "type": "call", "oi": 10}, {"strike": 100.0, "type": "call", "oi": 1}, {"strike": 110.0, "type": "put", "oi": 5}]
check(ox.max_pain(mp) == 90.0, "Max Pain: payouts 100 at 90, 150 at 100, 210 at 110 -> 90")
ex = ["2026-11-24", "2026-12-18", "2027-02-19"]
check(ox.pick_expiries(ex, "monthly", T0) == ["2026-12-18"] and ox.pick_expiries(ex, "60d", T0) == ["2026-11-24", "2026-12-18"], "nearest monthly; all within 60 days")
check(ox.third_friday_week("2025-04-17") and not ox.third_friday_week("2026-11-24"), "Thursday before a Good-Friday third Friday counts as the monthly")

cl, p = [], 100.0
for i in range(40):
    cl.append(((T0 - dt.timedelta(days=40 - i)).isoformat(), p))
    p *= math.exp(0.01 if i % 2 == 0 else -0.01)
rv = ox.rolling_vol(cl, 10)
check(abs(rv[-1][1] - 0.01 * math.sqrt(10 / 9) * math.sqrt(252) * 100) < 1e-9 and abs(rv[-1][1] - 16.7332) < 1e-3, "10-day realised vol of +/-1% moves = 16.733%")
cn = ox.cone(cl, windows=[10])
check(abs(cn[10][0] - cn[10][4]) < 1e-9 and abs(cn[10][5] - rv[-1][1]) < 1e-12, "cone: steady moves give min = max = today")
term = ox.atm_term([{"strike": k, "type": t, "oi": 1, "iv": iv, "expiration": "2026-12-23"} for k, t, iv in
                    ((95.0, "call", 0.3), (100.0, "call", 0.20), (100.0, "put", 0.22), (105.0, "put", 0.4))], 101.0, T0)
check(term == [(20.7, 21.0, "2026-12-23")] or (len(term) == 1 and term[0][0] == 20.7 and abs(term[0][1] - 21.0) < 1e-9), "ATM IV = (20% + 22%) / 2 at the strike nearest 101; 30 days = 20.7 trading days")
check(all(k in c.ROUTES for k in ("etf_overlap", "gex_strike", "vol_cone")), "routes")

# review fix: a daily-listed Thursday is not the monthly when the third Friday is listed too
check(ox.pick_expiries(["2026-12-16", "2026-12-17", "2026-12-18", "2027-01-15"], "monthly", dt.date(2026, 12, 14)) == ["2026-12-18"],
      "monthly: the third Friday, not the daily Thursday before it")
check(ox.pick_expiries(["2025-04-16", "2025-04-17", "2025-05-16"], "monthly", dt.date(2025, 4, 14)) == ["2025-04-17"], "monthly: Thursday when the Friday is a holiday")

done("test_etf_options")
