"""Futures curve points from Yahoo quotes (run_backend.futures_curve_points; audit C4). Quotes recorded live 2026-11-17."""

import importlib.util

from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

MONTHS = "FGHJKMNQUVXZ"


def exp(sym):        # CLZ26.NYM -> 2026-12 (what openbb_yfinance's get_expiration_month gives)
    root = sym.split(".")[0]
    return f"20{root[-2:]}-{MONTHS.index(root[-3]) + 1:02d}"


Q = [
    {"symbol": "CLZ31.NYM", "bid": 0.0, "ask": 0.0, "prev_close": 61.71, "volume_average": 284.0},     # no quote: stock fetcher said 0.00
    {"symbol": "CLZ26.NYM", "bid": 85.02, "ask": 85.04, "prev_close": 86.01, "volume_average": 310000.0},
    {"symbol": "CLJ30.NYM", "bid": 52.7, "ask": 0.0, "prev_close": 58.56, "volume_average": 40.0},       # one side: stock fetcher 26.35
    {"symbol": "GCN28.CMX", "bid": 4728.0, "ask": 4755.1, "prev_close": 4758.1, "volume_average": None},  # untraded month, off the curve
    {"symbol": "GCM28.CMX", "bid": 4093.7, "ask": 4095.2, "prev_close": 4165.4, "volume_average": 149.0},
    {"symbol": "GCZ29.CMX", "bid": 4679.8, "ask": 0.0, "prev_close": 4654.5, "volume_average": 3.0},      # traded, but less than 10 a day
    {"symbol": "HGH27.CMX", "bid": 5.00, "ask": 5.10, "prev_close": 5.04, "volume_average": 25.0},        # spread 2% of mid: too wide
    {"symbol": "NGF27.NYM", "bid": 0.0, "ask": 0.0, "prev_close": None, "volume_average": 900.0},         # nothing to price it with
]
pts = rb.futures_curve_points(Q, exp)
by = {p["expiration"]: p for p in pts}
check([p["expiration"] for p in pts] == sorted(by), "points in expiry order")
check(by["2026-12"]["price"] == 85.03 and by["2026-12"]["basis"] == "bid/ask mid", "two-sided tight quote: mid (85.02 + 85.04) / 2 = 85.03")
check(by["2031-12"]["price"] == 61.71 and by["2031-12"]["basis"] == "previous close", "no bid/ask: previous close 61.71, not 0.00")
check(by["2030-04"]["price"] == 58.56 and by["2030-04"]["basis"] == "previous close", "one-sided quote: previous close 58.56, not 52.7 / 2 = 26.35")
check(by["2028-06"]["price"] == 4094.45, "gold Jun-28: mid (4093.70 + 4095.20) / 2 = 4,094.45")
check("2028-07" not in by and "2029-12" not in by, "months without real trading left out (no 3-month average volume; 3 a day)")
check(by["2027-03"]["price"] == 5.04 and by["2027-03"]["basis"] == "previous close", "spread above 1% of mid: previous close")
check("2027-01" not in by and len(pts) == 5, "a month with neither a quote nor a close is left out")
check(rb.futures_curve_points([], exp) == [], "no quotes, no points")

# precious metals carry only financing and storage: a month far off the curve is bad data (live 2026-11-17: GC Aug-28
# quoted 4,791.4 / 4,819.6 between Jun-28 4,098.9 and Oct-28 4,161.55, i.e. sell Aug, buy Oct for ~630 a contract)
gold = [("GCZ26.CMX", 3800.2, 3802.0), ("GCG27.CMX", 3832.6, 3834.2), ("GCJ27.CMX", 3864.4, 3866.0), ("GCM27.CMX", 3898.3, 3900.0),
        ("GCQ27.CMX", 3933.3, 3935.0), ("GCV27.CMX", 3967.0, 3968.7), ("GCZ27.CMX", 4001.0, 4002.8), ("GCG28.CMX", 4033.5, 4035.2),
        ("GCJ28.CMX", 4067.1, 4068.8), ("GCM28.CMX", 4098.1, 4099.7), ("GCQ28.CMX", 4791.4, 4819.6), ("GCV28.CMX", 4160.7, 4162.4),
        ("GCZ28.CMX", 4192.4, 4194.2)]
gp = {p["expiration"]: p["price"] for p in rb.futures_curve_points(
    [{"symbol": s, "bid": b, "ask": a, "prev_close": b, "volume_average": 20.0} for s, b, a in gold], exp)}
check("2028-08" not in gp and len(gp) == 12, "gold: the Aug-28 month 16% above the curve is left out, its neighbours stay")
check(gp["2028-06"] == 4098.9 and gp["2028-10"] == 4161.55, "gold neighbours keep their mids")
# seasonal markets are not screened: natural gas winter months sit far above the shoulder months by design
gas = [("NGX26.NYM", 3.20, 3.21), ("NGZ26.NYM", 4.10, 4.11), ("NGF27.NYM", 4.60, 4.61), ("NGH27.NYM", 3.70, 3.71), ("NGJ27.NYM", 3.10, 3.11)]
ng = rb.futures_curve_points([{"symbol": s, "bid": b, "ask": a, "prev_close": b, "volume_average": 500.0} for s, b, a in gas], exp)
check(len(ng) == 5, "natural gas: winter premium kept (no screen outside precious metals)")
done("test_futures")
