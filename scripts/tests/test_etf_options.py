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

# off-hours OI backfill: when Yahoo chain has IV=0.20 and OI=0, gex() backfills OI from _nasdaq_oi and marks fallback
saved_gex = ox._chain, getattr(ox, "_nasdaq_oi", None), c.history
try:
    EXP_GEX = "2027-11-23"  # 365 days from T0 (2026-11-23)
    raw_zero_oi = [
        {"strike": 100.0, "option_type": "call", "open_interest": 0, "implied_volatility": 0.2, "expiration": EXP_GEX,
         "last_trade_time": "2026-11-23T15:00:00", "underlying_price": 100.0},
        {"strike": 100.0, "option_type": "put", "open_interest": 0, "implied_volatility": 0.2, "expiration": EXP_GEX,
         "last_trade_time": "2026-11-23T15:00:00", "underlying_price": 100.0},
    ]
    ox._chain = lambda sym: list(raw_zero_oi)
    ox._nasdaq_oi = lambda sym: {(EXP_GEX, 100.0, "call"): 1000.0}
    out_fb = c.dispatch("gex_strike", {"symbol": "TSTO", "today": T0})
    src_names = [s["name"] for s in out_fb["sources"]]
    check(out_fb["status"] == "fallback" and "Nasdaq" in src_names, f"gex backfill marks fallback and Nasdaq source: {out_fb['status']} {src_names}")
    check(abs(out_fb["results"][0]["gex_mn"] - 0.198) <= 0.001 and out_fb["summary"]["max_pain"] == 100.0,
          f"gex backfill computes 1,000 calls GEX = +0.198mn and Max Pain = 100: {out_fb['results']} {out_fb['summary']}")

    raw_with_oi = [dict(raw_zero_oi[0], open_interest=1000), raw_zero_oi[1]]
    nasdaq_called = [0]
    ox._chain = lambda sym: list(raw_with_oi)
    ox._nasdaq_oi = lambda sym: (nasdaq_called.__setitem__(0, nasdaq_called[0] + 1) or {})
    out_ok = c.dispatch("gex_strike", {"symbol": "TSTO", "today": T0})
    check(nasdaq_called[0] == 0 and [s["name"] for s in out_ok["sources"]] == ["Yahoo Finance"],
          f"gex with existing OI skips _nasdaq_oi ({nasdaq_called[0]}) and keeps Yahoo Finance only")

    # Cboe-direct chain attribution on gex_strike and vol_cone (+ Cboe omitted OI -> Nasdaq)
    ox._chain = lambda sym: (list(raw_with_oi), "cboe")
    out_cboe = c.dispatch("gex_strike", {"symbol": "TSTO", "today": T0})
    check([s["name"] for s in out_cboe["sources"]] == ["Cboe"] and "Cboe IV" in out_cboe["convention"],
          f"gex Cboe-direct attribution: {out_cboe['sources']} | {out_cboe['convention']}")

    raw_cboe_vc = [dict(raw_with_oi[0], expiration="2026-12-24")]
    ox._chain = lambda sym: (list(raw_cboe_vc), "cboe")
    c.history = lambda syms, start: {"TSTO": list(cl)}
    out_vc_cboe = c.dispatch("vol_cone", {"symbol": "TSTO", "today": dt.date(2026, 11, 24)})
    check([s["name"] for s in out_vc_cboe["sources"]] == ["Yahoo Finance", "Cboe"],
          f"vol_cone Cboe-direct attribution: {out_vc_cboe['sources']}")

    ox._chain = lambda sym: (list(raw_zero_oi), "cboe")
    ox._nasdaq_oi = lambda sym: {(EXP_GEX, 100.0, "call"): 1000.0}
    out_cboe_fb = c.dispatch("gex_strike", {"symbol": "TSTO", "today": T0})
    check([s["name"] for s in out_cboe_fb["sources"]] == ["Cboe", "Nasdaq"]
          and any("Cboe omitted off-hours open interest" in nt for nt in (out_cboe_fb.get("notes") or ())),
          f"gex Cboe-direct + Nasdaq note: {out_cboe_fb['sources']} | {out_cboe_fb.get('notes')}")

    # Non-mixing OI replacement: 19/20 contracts zeroed, 1 contract has Yahoo OI=5000 at strike 105 (not in Nasdaq);
    # whole OI column is replaced by Nasdaq snapshot (strike 100 call = 1000) so Yahoo's 5000 does not mix into GEX.
    mixed_chain = [
        {"strike": 100.0 + i * 0.5, "option_type": "call" if i % 2 == 0 else "put",
         "open_interest": 5000 if (i == 10) else 0, "implied_volatility": 0.2,
         "expiration": EXP_GEX, "last_trade_time": "2026-11-23T15:00:00", "underlying_price": 100.0}
        for i in range(20)
    ]
    ox._chain = lambda sym: (list(mixed_chain), "yahoo")
    ox._nasdaq_oi = lambda sym: {(EXP_GEX, 100.0, "call"): 1000.0, (EXP_GEX, 100.5, "put"): 500.0}
    out_nomix = c.dispatch("gex_strike", {"symbol": "TSTO", "today": T0})
    strikes_shown = {r["strike"]: r["gex_mn"] for r in out_nomix["results"]}
    check(strikes_shown.get(105.0, 0.0) == 0.0 and abs(strikes_shown.get(100.0, 0.0) - 0.198) <= 0.001,
          f"gex whole-column OI replacement avoids mixing Yahoo strike 105 (5000) with Nasdaq: {strikes_shown}")

    # Partial Yahoo OI zeroing (>10% populated, but active contracts have volume > 0 & OI == 0):
    # 14 contracts have OI=100 (58.3%), 10 active contracts (incl. ATM 100.0 call) have volume=50 & OI=0, plus Yahoo-only strikes 110.0-111.5.
    partial_chain = [
        {"strike": 100.0 + i * 0.5, "option_type": "call" if i % 2 == 0 else "put",
         "open_interest": 0 if i < 10 else 100, "volume": 50 if i < 10 else 0,
         "implied_volatility": 0.2, "expiration": EXP_GEX,
         "last_trade_time": "2026-11-23T15:00:00", "underlying_price": 100.0}
        for i in range(24)
    ]
    nq_full = {(EXP_GEX, 100.0 + i * 0.5, "call" if i % 2 == 0 else "put"): (1000.0 if i == 0 else 100.0)
               for i in range(20)}
    ox._chain = lambda sym: (list(partial_chain), "yahoo_sub")
    ox._nasdaq_oi = lambda sym: dict(nq_full)
    out_partial = c.dispatch("gex_strike", {"symbol": "TSTP", "today": T0})
    p_srcs = [s["name"] for s in out_partial["sources"]]
    p_notes = " | ".join(out_partial.get("notes") or ())
    p_strikes = {r["strike"]: r["gex_mn"] for r in out_partial["results"]}
    check(out_partial["status"] == "fallback" and "Nasdaq" in p_srcs and "omitted partial open interest" in p_notes,
          f"partial Yahoo OI triggers Nasdaq replacement & note: {out_partial['status']} {p_srcs} {p_notes}")
    check(abs(p_strikes.get(100.0, 0.0) - 0.198) <= 0.001 and p_strikes.get(110.0, 0.0) == 0.0,
          f"partial Yahoo OI replaces whole column with Nasdaq (+0.198mn at 100.0, drops unreturned 110.0): {p_strikes}")

    # Index roots (SPX, ^SPX) must never ask Nasdaq for open interest (Nasdaq returns 400 -> spurious partial)
    idx_nq_calls = [0]

    def fail_if_called(sym):
        idx_nq_calls[0] += 1
        raise c.CompositeError(f"Nasdaq 400 for {sym}")

    ox._chain = lambda sym: (list(partial_chain), "yahoo_sub")
    ox._nasdaq_oi = fail_if_called
    for idx_sym in ("SPX", "^SPX"):
        out_idx = c.dispatch("gex_strike", {"symbol": idx_sym, "today": T0})
        check(idx_nq_calls[0] == 0 and out_idx["status"] == "ok"
              and [s["name"] for s in out_idx["sources"]] == ["Yahoo Finance"],
              f"index {idx_sym} skips _nasdaq_oi without spurious partial: calls={idx_nq_calls[0]} {out_idx['status']}")

    # Healthy intraday Yahoo chain (21/24 near-ATM have OI=100, 17 traded with OI + 3 newly opened with volume=5, OI=0 -> 15% < 20%):
    # must NOT call Nasdaq and status stays ok.
    healthy_chain = [
        {"strike": 100.0 + i * 0.5, "option_type": "call" if i % 2 == 0 else "put",
         "open_interest": 0 if i < 3 else 100, "volume": 5 if i < 3 else (10 if i < 20 else 0),
         "implied_volatility": 0.2, "expiration": EXP_GEX,
         "last_trade_time": "2026-11-23T15:00:00", "underlying_price": 100.0}
        for i in range(24)
    ]
    ox._chain = lambda sym: (list(healthy_chain), "yahoo_sub")
    ox._nasdaq_oi = fail_if_called
    out_healthy = c.dispatch("gex_strike", {"symbol": "TSTH", "today": T0})
    check(idx_nq_calls[0] == 0 and out_healthy["status"] == "ok"
          and [s["name"] for s in out_healthy["sources"]] == ["Yahoo Finance"],
          f"healthy intraday Yahoo chain skips _nasdaq_oi and stays ok: calls={idx_nq_calls[0]} {out_healthy['status']}")

    # When partial_zero asks Nasdaq and Nasdaq fails, keep Yahoo GEX as status=ok (never spurious partial).
    def raise_nq(sym):
        raise c.CompositeError("Nasdaq unavailable")

    ox._chain = lambda sym: (list(partial_chain), "yahoo_sub")
    ox._nasdaq_oi = raise_nq
    out_pfail = c.dispatch("gex_strike", {"symbol": "TSTN", "today": T0})
    check(out_pfail["status"] == "ok" and [s["name"] for s in out_pfail["sources"]] == ["Yahoo Finance"]
          and len(out_pfail["results"]) > 0,
          f"partial_zero failure from Nasdaq leaves Yahoo GEX ok: {out_pfail['status']} {out_pfail['sources']}")

    # When severe_zero (<10% OI) asks Nasdaq and Nasdaq fails, mark status=partial with Nasdaq open interest note.
    severe_chain = [
        {"strike": 100.0 + i * 0.5, "option_type": "call" if i % 2 == 0 else "put",
         "open_interest": 100 if i == 0 else 0, "volume": 10,
         "implied_volatility": 0.2, "expiration": EXP_GEX,
         "last_trade_time": "2026-11-23T15:00:00", "underlying_price": 100.0}
        for i in range(24)
    ]
    ox._chain = lambda sym: (list(severe_chain), "yahoo_sub")
    ox._nasdaq_oi = raise_nq
    out_sfail = c.dispatch("gex_strike", {"symbol": "TSTS", "today": T0})
    s_notes = " | ".join(out_sfail.get("notes") or ())
    check(out_sfail["status"] == "partial" and "Nasdaq open interest" in s_notes,
          f"severe_zero failure from Nasdaq marks partial: {out_sfail['status']} {s_notes}")
finally:
    ox._chain, c.history = saved_gex[0], saved_gex[2]
    if saved_gex[1] is not None:
        ox._nasdaq_oi = saved_gex[1]

done("test_etf_options")
