"""P3-03: as_of = the newest data point actually used, for bank_failures, bank_quality, bond_curve, bond_lookup, crypto_vol,
etf_overlap, gex_strike and vol_cone. Each runs the real view through dispatch (strict contract) on two hand-built inputs
with distinct dates; the expected as_of is typed by hand. Upstream is faked at the composite helper the view calls."""

import datetime as dt
import math

from _common import check_as_of, done

import composite as C
import banks
import bonds
import optionsx


def reset():
    C._cache.clear()
    if hasattr(C, "_backoff"):
        C._backoff.clear()


def series(last_iso, n, base=100.0):
    """[(iso, close)] oldest first, n consecutive calendar days ending on last_iso, uneven daily moves."""
    last = dt.date.fromisoformat(last_iso)
    out, p = [], base
    for i in range(n):
        out.append(((last - dt.timedelta(days=n - 1 - i)).isoformat(), p))
        p *= math.exp(0.012 if i % 3 else -0.009)
    return out


# ---------------------------------------------------------------- bank_failures (FDIC dates come as M/D/YYYY, listed unsorted)
saved = C.cached_disk
try:
    reset()
    fail1 = [{"NAME": "ALPHA BANK", "CITY": "OMAHA", "PSTALP": "NE", "FAILDATE": "9/25/2026", "QBFASSET": 5000},
             {"NAME": "BETA BANK", "CITY": "MIAMI", "PSTALP": "FL", "FAILDATE": "11/3/2026", "QBFASSET": 9000},
             {"NAME": "GAMMA BANK", "CITY": "TULSA", "PSTALP": "OK", "FAILDATE": "2/14/2026", "QBFASSET": 7000}]
    C.cached_disk = lambda name, ttl, fn: fail1
    out = C.dispatch("bank_failures", {"limit": "5"})
    check_as_of("bank_failures", out, "2026-11-03", "newest closing date, 11/3 beats 9/25")
    reset()
    fail2 = [{"NAME": "DELTA BANK", "CITY": "RENO", "PSTALP": "NV", "FAILDATE": "2/14/2025", "QBFASSET": 1000},
             {"NAME": "EPSILON BANK", "CITY": "BOISE", "PSTALP": "ID", "FAILDATE": "12/5/2025", "QBFASSET": 2000}]
    C.cached_disk = lambda name, ttl, fn: fail2
    out = C.dispatch("bank_failures", {"limit": "10"})
    check_as_of("bank_failures", out, "2025-12-05", "an older list: its own newest closing")
finally:
    C.cached_disk = saved
    reset()

# ---------------------------------------------------------------- bank_quality (FDIC quarters, REPDTE YYYYMMDD)
saved = C.api, C.cached_disk, banks.lead_bank, banks._fdic
try:
    C.cached_disk = lambda name, ttl, fn: fn()
    HC = {"WFC": {"rssd_id": "1120754", "legal_name": "WELLS FARGO & COMPANY"}, "JPM": {"rssd_id": "1039502", "legal_name": "JPMORGAN CHASE & CO."}}
    C.api = lambda base, path, **kw: [HC[kw["ticker"]]] if kw.get("ticker") in HC else []
    banks.lead_bank = lambda rssd: {"CERT": 3511 if rssd == "1120754" else 628, "NAME": "Lead Bank " + rssd}
    fin = {"1120754": [{"REPDTE": "20260930", "NCLNLSR": 0.8, "NTLNLSR": 0.6, "RBC1AAJ": 7.5, "ROA": 1.5},
                       {"REPDTE": "20260331", "NCLNLSR": 0.85, "NTLNLSR": 0.5, "RBC1AAJ": 7.6, "ROA": 1.4},
                       {"REPDTE": "20260630", "NCLNLSR": 0.78, "NTLNLSR": 0.55, "RBC1AAJ": 7.4, "ROA": 1.45}],
           "1039502": [{"REPDTE": "20250630", "NCLNLSR": 0.5, "NTLNLSR": 0.7, "RBC1AAJ": 8.1, "ROA": 1.6},
                       {"REPDTE": "20241231", "NCLNLSR": 0.55, "NTLNLSR": 0.65, "RBC1AAJ": 8.0, "ROA": 1.7}]}
    cur = {}
    banks.lead_bank = (lambda f: lambda rssd: (cur.update(r=rssd) or f(rssd)))(banks.lead_bank)
    banks._fdic = lambda path, **p: fin[cur["r"]]
    reset()
    out = C.dispatch("bank_quality", {"symbol": "WFC"})
    check_as_of("bank_quality", out, "2026-09-30", "newest quarter end of the lead bank")
    reset()
    out = C.dispatch("bank_quality", {"symbol": "JPM"})
    check_as_of("bank_quality", out, "2025-06-30", "a different holding company, older newest quarter")
finally:
    C.api, C.cached_disk, banks.lead_bank, banks._fdic = saved
    reset()

# ---------------------------------------------------------------- crypto_vol (complete UTC days only; newest of the two lines)
saved = C.api, C.cached
try:
    C.cached = lambda key, ttl, fn: fn()

    def feed(dv_last, px_last, today):
        dv = [{"date": (dt.date.fromisoformat(dv_last) - dt.timedelta(days=k)).isoformat() + "T00:00:00Z", "close": 45.0 + k} for k in range(0, 25)]
        dv.append({"date": today.isoformat() + "T00:00:00Z", "close": 50.0})          # today's open UTC day: left out of both lines
        px = [{"date": d, "close": c} for d, c in series(px_last, 70, 60000.0)] + [{"date": today.isoformat(), "close": 61000.0}]
        return dv, px

    T1 = dt.date(2026, 11, 19)
    dv, px = feed("2026-11-18", "2026-11-17", T1)
    C.api = lambda base, path, **kw: dv if path.startswith("deribit") else px
    out = C.dispatch("crypto_vol", {"currency": "BTC", "range": "3M", "today": T1})
    check_as_of("crypto_vol", out, "2026-11-18", "BTC: DVOL newer than the realised line; today's open day excluded")
    T2 = dt.date(2026, 11, 24)
    dv, px = feed("2026-11-20", "2026-11-22", T2)
    out = C.dispatch("crypto_vol", {"currency": "ETH", "range": "3M", "today": T2})
    check_as_of("crypto_vol", out, "2026-11-22", "ETH: realised line newer than DVOL")
finally:
    C.api, C.cached = saved
    reset()

# ---------------------------------------------------------------- etf_overlap (SEC N-PORT is U.S. registered funds only: two U.S. pairs)
saved = C.api_full, C.cached_disk
try:
    C.cached_disk = lambda name, ttl, fn: fn()
    funds = {
        "SPY": ("2026-09-30", [("Apple Inc", "037833100", 0.07), ("Microsoft Corp", "594918104", 0.06), ("Only Spy", "111111103", 0.01)]),
        "QQQ": ("2026-06-30", [("Apple Inc", "037833100", 0.09), ("Microsoft Corp", "594918104", 0.08), ("Only Qqq", "222222202", 0.02)]),
        "VTI": ("2026-03-31", [("Apple Inc", "037833100", 0.05), ("Exxon Mobil Corp", "30231G102", 0.02)]),
        "VOO": ("2026-06-30", [("Apple Inc", "037833100", 0.06), ("Exxon Mobil Corp", "30231G102", 0.03)]),
    }

    def nport(base, path, **kw):
        end, rows = funds[kw["symbol"]]
        return {"results": [{"name": n, "cusip": cu, "weight": w} for n, cu, w in rows],
                "extra": {"results_metadata": {"fund_name": kw["symbol"], "period_ending": end}}}
    C.api_full = nport
    out = C.dispatch("etf_overlap", {"symbol": "SPY", "symbol2": "QQQ"})
    check_as_of("etf_overlap", out, "2026-09-30", "SPY newer than QQQ")
    out = C.dispatch("etf_overlap", {"symbol": "VTI", "symbol2": "VOO"})
    check_as_of("etf_overlap", out, "2026-06-30", "the second fund is the newer one")
finally:
    C.api_full, C.cached_disk = saved
    reset()

# ---------------------------------------------------------------- gex_strike (newest contract trade, shown expiries first)
saved = optionsx._chain
try:
    T = dt.date(2026, 11, 23)

    def chain(spot, shown, traded_shown, other_exp, traded_other):
        rows = []
        for k, tr in zip((spot - 5, spot, spot + 5), traded_shown):
            for t in ("call", "put"):
                rows.append({"strike": float(k), "option_type": t, "open_interest": 1000, "implied_volatility": 0.22, "expiration": shown,
                             "last_trade_time": tr, "underlying_price": float(spot)})
        rows.append({"strike": float(spot), "option_type": "call", "open_interest": 500, "implied_volatility": 0.25, "expiration": other_exp,
                     "last_trade_time": traded_other, "underlying_price": float(spot)})
        return rows

    spy = chain(500, "2026-12-18", ["2026-11-20T15:59:58", "2026-11-21T15:59:58", "2026-11-19T10:00:00"], "2027-01-15", "2026-11-24T10:00:00")
    optionsx._chain = lambda sym: spy
    out = C.dispatch("gex_strike", {"symbol": "SPY", "today": T})
    check_as_of("gex_strike", out, "2026-11-21T15:59:58", "newest contract trade of the shown expiry; a later trade in another expiry is not used")
    aapl = chain(200, "2026-12-18", ["2026-11-19T20:59:59+00:00", "2026-11-23T20:59:59+00:00", "2026-11-18T20:59:59+00:00"], "2027-01-15", "2026-11-25T09:00:00+00:00")
    optionsx._chain = lambda sym: aapl
    out = C.dispatch("gex_strike", {"symbol": "AAPL", "today": T})
    check_as_of("gex_strike", out, "2026-11-23T20:59:59+00:00", "AAPL: timezone-carrying trade times")
finally:
    optionsx._chain = saved
    reset()

# ---------------------------------------------------------------- vol_cone (last complete session of the price history; today's bar left out)
saved = C.history, optionsx._chain
try:
    spy_px = series("2026-11-20", 60, 500.0) + [("2026-11-23", 505.0)]            # Friday close is the last complete session
    jp_px = series("2026-11-24", 60, 3000.0) + [("2026-11-25", 3050.0)]
    C.history = lambda syms, start, **kw: {"SPY": spy_px, "7203.T": jp_px}
    spy_chain = [{"strike": 500.0, "option_type": t, "open_interest": 100, "implied_volatility": 0.2, "expiration": "2026-12-23",
                  "last_trade_time": "2026-11-20T15:00:00", "underlying_price": 500.0} for t in ("call", "put")]
    optionsx._chain = lambda sym: spy_chain
    out = C.dispatch("vol_cone", {"symbol": "SPY", "today": dt.date(2026, 11, 23)})
    check_as_of("vol_cone", out, "2026-11-20", "SPY: last complete session, the 11/23 live bar is not used")

    def nochain(sym):
        raise C.CompositeError("Yahoo Finance has no option chain for 7203.T.")
    optionsx._chain = nochain
    out = C.dispatch("vol_cone", {"symbol": "7203.T", "today": dt.date(2026, 11, 25)})
    check_as_of("vol_cone", out, "2026-11-24", "Toyota (Tokyo), no option chain: realised cone alone")
finally:
    C.history, optionsx._chain = saved
    reset()

# ---------------------------------------------------------------- bond_lookup (newest TRACE last-sale date among the bonds shown)
saved_api, saved_par = C.api, bonds._par_curve
try:
    bonds._par_curve = lambda: {}                                       # offline: no FRED request for the G-spread curve

    def bond(issuer, coupon, mat, last, **kw):
        return dict({"issuer_name": issuer, "coupon_rate": coupon, "maturity_date": mat, "price": 100.0, "last_sale_yield": 5.0,
                     "last_trade_date": last, "cusip": f"X{coupon}", "coupon_type": "Fixed rate"}, **kw)
    FINRA = [bond("FORD MTR CO DEL", 6.1, "2032-08-19", "2026-11-18"), bond("FORD MTR CO DEL", 4.75, "2043-01-15", "2026-11-10"),
             bond("FORD MTR CO DEL MTN", 5.0, "2030-01-01", "2026-11-19"),            # MTN: hidden, its date is not used
             bond("FORD MTR CO DEL", 7.4, "2046-11-01", "2026-09-01"),                # not traded in 45 days: hidden
             bond("APPLE INC", 2.85, "2061-08-05", "2026-11-12"), bond("APPLE INC", 3.0, "2050-02-09", "2026-11-16"),
             bond("APPLE INC", 1.2, "2035-02-08", "2026-10-30")]

    def fake_api(base, path, **p):
        return [r for r in FINRA if p.get("issuer_name", "") in r["issuer_name"]]
    C.api = fake_api
    TD = dt.date(2026, 11, 19)
    reset()
    out = C.dispatch("bond_lookup", {"issuer": "ford mtr", "today": TD})
    check_as_of("bond_lookup", out, "2026-11-18", "Ford: newest shown last sale, a hidden MTN trade is not used")
    reset()
    out = C.dispatch("bond_lookup", {"issuer": "apple inc", "today": TD})
    check_as_of("bond_lookup", out, "2026-11-16", "Apple: a different issuer and dates")

    # ---- bond_curve last: the chart's x values are maturities (years after the last sale), which must not date the answer
    reset()
    out = C.dispatch("bond_curve", {"issuer": "ford mtr", "today": TD})
    check_as_of("bond_curve", out, "2026-11-18", "Ford: last-sale date, not a maturity")
    reset()
    out = C.dispatch("bond_curve", {"issuer": "apple inc", "today": TD})
    check_as_of("bond_curve", out, "2026-11-16", "Apple: last-sale date, not a maturity")
finally:
    C.api, bonds._par_curve = saved_api, saved_par
    reset()

done("test_asof_other")
