"""Offline checks for the substitutes used while Cboe refuses its delayed_quotes feed."""

import asyncio
import importlib
import importlib.util
import warnings
from datetime import date

import numpy as np
import pandas as pd
from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)
import option_math as om  # noqa: E402  (run_backend puts terminal/ on the path)


def close(a, b, tol=1e-5):
    return abs(float(a) - b) <= tol


# ---- Black-76, by hand: F = K = S = 100, T = 1, sigma = 0.2, r = 0: d1 = 0.1, N(d1) = 0.539828, phi(d1) = 0.396953
one = np.array([100.0])
call, put = np.array([True]), np.array([False])
g = om.greeks(100.0, 100.0, one, 1.0, np.array([0.2]), 0.0, call)
p = om.greeks(100.0, 100.0, one, 1.0, np.array([0.2]), 0.0, put)
for name, got, want in (("value", g["theoretical_price"], 7.965567), ("delta", g["delta"], 0.539828), ("put delta", p["delta"], -0.460172),
                        ("gamma = phi / 20", g["gamma"], 0.019848), ("vega per point = phi", g["vega"], 0.396953),
                        ("theta per day = -100 phi 0.2 / 2 / 365", g["theta"], -0.010875), ("rho per point = N(d2)", g["rho"], 0.460172),
                        ("put rho", p["rho"], -0.539828)):
    check(close(got[0], want), f"Black-76 {name}: {got[0]} != {want}")
check(close(om.implied_vol(np.array([7.965567]), 100.0, one, 1.0, 1.0, call)[0], 0.2), "7.965567 implies 20%")
check(np.isnan(om.implied_vol(np.array([0.0]), 100.0, one, 1.0, 1.0, call)[0]), "a zero price implies no volatility")
check(np.isnan(om.implied_vol(np.array([101.0]), 100.0, one, 1.0, 1.0, call)[0]), "a price above the forward implies none")
with warnings.catch_warnings(record=True) as zero_t_warn:
    warnings.simplefilter("always")
    c0 = om.black76(110.0, one, 0.0, np.array([0.2]), 1.0, call)[0]
    p0 = om.black76(110.0, one, 0.0, np.array([0.2]), 1.0, put)[0]
    check(close(c0, 10.0) and close(p0, 0.0) and not zero_t_warn, "black76 at T=0 returns intrinsic without divide-by-zero warnings")
check(close(om.parity_forward([90, 100, 110], [15, 8, 4], [3, 6, 12], 1.0), 102.0), "forward = K + C - P = 102")
check(np.isnan(om.parity_forward([100], [8], [6], 1.0)), "one pair is no forward")

friday = pd.Timestamp("2026-11-20 16:00", tz="America/New_York")
saturday = date(2026, 11, 21)
check(close(om.years_to_expiry(date(2026, 11, 23), "SPY", friday) * 365, 3.0), "Monday's expiry has 3 days from Friday's close")
check(close(om.years_to_expiry(date(2026, 12, 18), "SPX", friday) * 365, 28 - 6.5 / 24), "SPX settles at the open")


# ---- a chain priced at a known 30% comes back at 30%
def chain(root="ABC", expiry=date(2026, 12, 18), quotes=True, traded=friday, strikes=(80, 90, 100, 110, 120), oi=10, spot=100.0):
    years, out = om.years_to_expiry(expiry, root, friday), []
    for k in strikes:
        for side in ("call", "put"):
            v = float(om.black76(spot, np.array([float(k)]), years, np.array([0.3]), 1.0, np.array([side == "call"]))[0])
            out.append({"contract_symbol": f"{root}{expiry:%y%m%d}{side[0].upper()}{round(k * 1000):08d}", "expiration": expiry,
                        "strike": float(k), "option_type": side, "bid": v * 0.99 if quotes else 0.0,
                        "ask": v * 1.01 if quotes else 0.0, "last_trade_price": v,
                        "last_trade_time": traded.tz_convert("UTC"), "open_interest": oi, "volume": 1})
    return pd.DataFrame(out)


df, basis = om.analyse_chain(chain(), 100.0, friday, 0.0, saturday)
check(basis == "mid" and len(df) == 10, "two-sided quotes are priced at their mid")
check(np.allclose(df.implied_volatility, 0.3, atol=1e-5), "every strike returns the 30% it was priced at")
check(set(df.dte) == {27}, "DTE counts from today (27), time value from the quotes (28)")
atm = df[(df.strike == 100) & (df.option_type == "call")].iloc[0]
check(abs(atm.DEX - atm.delta * 10 * 100 * 100) <= 1 and abs(atm.GEX - atm.gamma * 10 * 100 * 10000 * 0.01) <= 1, "DEX and GEX in dollars")
check((df[df.option_type == "put"].GEX <= 0).all(), "put gamma exposure is negative")

closed, basis = om.analyse_chain(chain(quotes=False), 100.0, friday, 0.0, saturday)
check(basis == "last" and np.allclose(closed.implied_volatility, 0.3, atol=1e-5), "no live quotes: the closing trades")
check(closed.ask.isna().all() and closed.bid.isna().all(), "a zero ask is blank, never a free option")
thin = pd.concat([chain(quotes=False)] * 5 + [chain()], ignore_index=True)
thin, basis = om.analyse_chain(thin, 100.0, friday, 0.0, saturday)
check(basis == "last" and thin.ask.isna().all(), "a handful of quotes prices no strategy")
old, _ = om.analyse_chain(chain(quotes=False, traded=friday - pd.Timedelta(hours=3)), 100.0, friday, 0.0, saturday)
check(old.implied_volatility.isna().all(), "trades three hours before the quote time give blank, not zero")
check(om.priced_part(old, 100.0).empty, "nothing priced, nothing plotted")

both = pd.concat([chain("SPX"), chain("SPXW", strikes=(90, 100, 110))], ignore_index=True)
two, _ = om.analyse_chain(both, 100.0, friday, 0.0, saturday)
w = two.contract_symbol.str.startswith("SPXW")
check(two[~w].main.all() and not two[w].main.any() and two[w].implied_volatility.isna().all(), "one root per expiry on a curve")
check(two.open_interest.sum() == 160, "both roots keep their open interest")
expired, _ = om.analyse_chain(pd.concat([chain(), chain(expiry=date(2026, 11, 20)).assign(open_interest=99)], ignore_index=True),
                              100.0, friday, 0.0, saturday)
check(set(expired.expiration) == {date(2026, 12, 18)}, "Friday's expiry is gone on Saturday")
check(om.priced_part(df, 130.0).empty, "an expiry priced on one side of spot only is not plotted")
thin_side = pd.concat([
    chain(expiry=date(2026, 11, 23), strikes=(347.5, 350.0), spot=350.98),
    chain(expiry=date(2026, 11, 24), strikes=(347.5, 350.0, 352.5), spot=350.98),
], ignore_index=True)
thin_df, _ = om.analyse_chain(thin_side, 350.98, friday, 0.0, saturday)
check(om.priced_part(thin_df, 350.98).empty, "dying expiries with fewer than two priced strikes on each side of spot are excluded")
check(len(om.priced_part(df, 100.0)) == 10, "an expiry with at least two priced strikes on each side of spot is kept")

# ---- early close / closed session 0DTE expiry handling
early_close_et = pd.Timestamp("2026-11-27 13:00", tz="America/New_York")
early_close_utc = pd.Timestamp("2026-11-27 18:00", tz="UTC")
check(om.years_to_expiry(date(2026, 11, 27), "CRWV", early_close_et, session_closed=True) == 0.0,
      "early close drops same-day expiry when session_closed=True")
check(om.years_to_expiry(date(2026, 11, 27), "CRWV", early_close_utc, session_closed=True) == 0.0,
      "UTC-zoned quoted_at converts to NY exchange date for early close")
check(close(om.years_to_expiry(date(2026, 12, 4), "CRWV", early_close_utc, session_closed=True) * 365, 7.125),
      "later expiry from 13:00 ET early close has 7.125 calendar days to 16:00 ET settlement")
check(close(om.years_to_expiry(date(2026, 11, 27), "CRWV", early_close_et, session_closed=False) * 365, 0.125),
      "intraday session_closed=False preserves same-day 0DTE time to 16:00 ET (3h = 0.125d)")
check(close(om.years_to_expiry(date(2026, 11, 27), "CRWV", pd.Timestamp("2026-11-25 16:00", tz="America/New_York"),
                               session_closed=True) * 365, 2.0),
      "pre-market / prior-close quote with session_closed=True keeps future 2026-11-27 expiry")

def early_close_chain(expiry, yrs):
    rows_out = []
    for k in (80, 90, 100, 110, 120):
        for side in ("call", "put"):
            v = float(om.black76(100.0, np.array([float(k)]), yrs, np.array([0.30]), 1.0, np.array([side == "call"]))[0])
            rows_out.append({"contract_symbol": f"CRWV{expiry:%y%m%d}{side[0].upper()}{k * 1000:08d}", "expiration": expiry,
                             "strike": float(k), "option_type": side, "bid": v * 0.99, "ask": v * 1.01,
                             "last_trade_price": v, "last_trade_time": early_close_et.tz_convert("UTC"),
                             "open_interest": 10, "volume": 5})
    return pd.DataFrame(rows_out)

ec_rows = pd.concat([
    early_close_chain(date(2026, 11, 27), 0.125 / 365),
    early_close_chain(date(2026, 12, 4), 7.125 / 365),
], ignore_index=True)
ec_df, _ = om.analyse_chain(ec_rows, 100.0, early_close_et, 0.0, date(2026, 11, 27), session_closed=True)
check(set(ec_df.expiration) == {date(2026, 12, 4)} and np.allclose(ec_df.implied_volatility, 0.30, atol=1e-5),
      "analyse_chain drops settled 0DTE on early close and recovers 30% IV on next expiry")
ec_sub, _ = rb.build_substitute_chain("CRWV", (ec_rows, 100.0, early_close_et, True), 0.0, date(2026, 11, 27))
check(ec_sub.expirations == ["2026-12-04"] and np.allclose(ec_sub.dataframe["implied_volatility"], 0.30, atol=1e-5),
      "build_substitute_chain 4-tuple honors session_closed=True")
ec_compat, _ = rb.build_substitute_chain("CRWV", (ec_rows, 100.0, early_close_et), 0.0, date(2026, 11, 27))
check(ec_compat.expirations == ["2026-11-27", "2026-12-04"],
      "build_substitute_chain 3-tuple stays backward-compatible (session_closed=False)")
check({"POST", "POSTPOST", "CLOSED", "PREPRE"}.issubset(rb.CLOSED_MARKET_STATES)
      and "REGULAR" not in rb.CLOSED_MARKET_STATES and "PRE" not in rb.CLOSED_MARKET_STATES,
      "CLOSED_MARKET_STATES includes PREPRE overnight state and excludes REGULAR/PRE")
ec_prepre, _ = om.analyse_chain(ec_rows, 100.0, early_close_et, 0.0, date(2026, 11, 27),
                                session_closed="PREPRE" in rb.CLOSED_MARKET_STATES)
check(set(ec_prepre.expiration) == {date(2026, 12, 4)},
      "PREPRE overnight state drops settled 0DTE on early close and keeps next expiry")

# ---- settlements, index snapshots, symbols
rows = [{"product": "VX", "symbol": s, "expiration": e, "price": v, "settlement_date": "2026-11-20"} for s, e, v in (
    ("VX/F7", "2027-01-20", 26.2462), ("VX47/X6", "2026-11-24", 27.825), ("VX/Z6", "2026-12-16", 28.4582))]
rows.append({"product": "ZZZ", "symbol": "ZZZ/Z6", "expiration": "2026-12-16", "price": 1.0, "settlement_date": "2026-11-20"})
check(rb.vix_curve_from_settlements(rows) == [
    {"expiration": "2026-12", "price": 28.4582, "symbol": "VX1", "date": "2026-11-20"},
    {"expiration": "2027-01", "price": 26.2462, "symbol": "VX2", "date": "2026-11-20"}], "monthly VX only, by expiry")
check(rb.cfe_instruments_from_settlements(rows) == [{"future_root": "VX", "name": rb.CFE_NAMES["VX"]},
                                                  {"future_root": "ZZZ", "name": None}], "an unknown product has no invented name")
row = rb.index_snapshot_row("SPX", {"regularMarketPrice": 99.0, "regularMarketPreviousClose": 100.0, "regularMarketTime": 1795208400})
check(close(row["price_change_percent"], -1.0) and str(row["last_trade_time"]) == "2026-11-20 16:00:00", "points and Eastern time")
check(rb.index_snapshot_row("SPX", {}) is None, "no price, no row")
check([rb.canonical_option_symbol(s) for s in ("^GSPC", "gspc", "^VIX", "AAPL")] == ["SPX", "SPX", "VIX", "AAPL"],
      "canonical option symbol maps ^GSPC/GSPC -> SPX")
check([rb.yahoo_option_symbol(s) for s in ("spx", "^GSPC", "GSPC", "^VIX", "BRK.B", "AAPL", "../x", "")] == [
    "^SPX", "^SPX", "^SPX", "^VIX", "BRK-B", "AAPL", None, None], "Yahoo symbols, ^GSPC alias, and no path text")


answers = iter([TypeError("len of None"), ConnectionError("reset"), ("2026-12-18",)])


def flaky():
    got = next(answers)
    if isinstance(got, Exception):
        raise got
    return got


check(rb.yahoo_retry(flaky, 0) == ("2026-12-18",), "a refused crumb or transient network error is asked again")
check(rb.yahoo_retry(lambda: (), 0) is None, "three empty answers are no data")
try:
    rb.yahoo_retry(lambda: len(None), 0)
    check(False, "three failures must raise")
except TypeError:
    check(True, "three failures are an outage, not 'no options'")


class YFRateLimitError(Exception):
    pass


rl_calls = [0]


def rate_limited():
    rl_calls[0] += 1
    raise YFRateLimitError("Too Many Requests")


try:
    rb.yahoo_retry(rate_limited, 0)
    check(False, "rate limit must raise immediately")
except YFRateLimitError:
    check(rl_calls[0] == 1, "yahoo_retry never retries rate limits")


# ---- the installed loader and the real openbb_cboe chart builders
async def installed():
    try:
        from openbb_cboe.models.options_chains import CboeOptionsChainsFetcher
        from openbb_cboe.utils import helpers as cboe_helpers
        from openbb_cboe.utils.options import data_handler
        from openbb_core.app.model.abstract.error import OpenBBError
        from openbb_core.provider.abstract.annotated_result import AnnotatedResult
    except ImportError:
        print("openbb_cboe not installed: loader checks skipped")
        return
    asked = {"cboe": 0, "yahoo": 0}

    async def refused(*args, **kwargs):
        asked["cboe"] += 1
        raise OpenBBError("Cboe refused the delayed options feed (HTTP 403).")

    def fetch(symbol):
        asked["yahoo"] += 1
        if symbol == "NONE":
            return None
        if symbol == "DOWN":
            raise TimeoutError("slow")
        later = chain(expiry=date(2099, 12, 18))
        return pd.concat([chain(expiry=date(2099, 11, 20)), later], ignore_index=True), 100.0, friday

    CboeOptionsChainsFetcher.fetch_data = refused
    cb_applied = []
    rb.install_cboe_chain_symbols(cb_applied)
    check(len(cb_applied) == 1, "cboe chain symbols / 403 callback wrapper applies")

    class FakeResp:
        def __init__(self, status, data):
            self.status = status
            self.headers = {"Content-Type": "application/json"}
            self._data = data

        async def json(self):
            return self._data

    check(await cboe_helpers.response_callback(FakeResp(200, {"ok": 1}), None) == {"ok": 1}, "response_callback passes through 200")
    try:
        await cboe_helpers.response_callback(FakeResp(403, {}), None)
        check(False, "response_callback must raise OpenBBError on HTTP 403")
    except OpenBBError as e:
        check("403" in str(e), "response_callback raises OpenBBError on HTTP 403")

    applied = []
    rb.install_cboe_options_fallback(applied, fetch=fetch, get_rate=lambda: 0.0)
    check(len(applied) == 1, "options fallback applies")
    box = {}
    token = rb.SUBSTITUTE.set(box)
    got = await asyncio.gather(*[data_handler.load_symbol("abc") for _ in range(4)])
    check(asked == {"cboe": 1, "yahoo": 1} and all(x is got[0] for x in got), "four cards share one download")
    check(box["sources"] == "Yahoo Finance" and "calculated" in box["note"], "the answer names Yahoo and says calculated")
    box.clear()
    await data_handler.load_symbol("ABC")
    check(box.get("sources") == "Yahoo Finance" and asked["yahoo"] == 1, "a remembered chain is labelled too")
    box.clear()
    fetched_chain = await CboeOptionsChainsFetcher.fetch_data({"symbol": "abc"}, {})
    check(isinstance(fetched_chain, AnnotatedResult) and asked["yahoo"] == 1
          and box.get("sources") == "Yahoo Finance" and "Delayed prices from Yahoo Finance" in box.get("delayed", ""),
          "CboeOptionsChainsFetcher.fetch_data shares the Yahoo chain with IV/Greeks and delayed label")
    check(any(v is not None for v in fetched_chain.result.implied_volatility)
          and any(v is not None for v in fetched_chain.result.delta),
          "CboeOptionsChainsFetcher.fetch_data returns calculated IV and Greeks")
    box.clear()
    gspc_chain = await data_handler.load_symbol("^GSPC")
    check(asked["yahoo"] == 2 and gspc_chain.expirations == ["2099-11-20", "2099-12-18"]
          and len(data_handler.get_expirations("^GSPC")) == 2,
          "^GSPC aliases cleanly to SPX across load_symbol and expiry lookup")
    check(got[0].expirations == ["2099-11-20", "2099-12-18"] and data_handler.get_expirations("^abc"), "expiry menu filled")
    for module, kwargs in (("create_surface", {}), ("create_surface", {"metric": "gex"}), ("create_smile", {"otm": True}),
                           ("create_term_structure", {}), ("create_stats", {})):
        build = getattr(importlib.import_module(f"openbb_cboe.utils.options.{module}"), module)
        check(len(build(got[0], **kwargs).results) > 0, f"{module} {kwargs} draws from the substitute chain")

    # default smile skips 0DTE when a later multi-strike expiry exists, while still honoring an explicit expiry
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    today_et = now_et.date()
    exp0, exp1 = today_et, today_et + timedelta(days=7)
    quote_t = pd.Timestamp(f"{today_et} 10:00", tz="America/New_York")

    def smile_chain_for(expiry, strikes=(80, 90, 100, 110, 120)):
        yrs, rows_out = om.years_to_expiry(expiry, "SML", quote_t), []
        for k in strikes:
            for side in ("call", "put"):
                v = float(om.black76(100.0, np.array([float(k)]), yrs, np.array([0.28]), 1.0, np.array([side == "call"]))[0])
                rows_out.append({"contract_symbol": f"SML{expiry:%y%m%d}{side[0].upper()}{k * 1000:08d}", "expiration": expiry,
                                 "strike": float(k), "option_type": side, "bid": v * 0.99, "ask": v * 1.01,
                                 "last_trade_price": v, "last_trade_time": quote_t.tz_convert("UTC"), "open_interest": 10, "volume": 5})
        return pd.DataFrame(rows_out)

    built_smile = rb.build_substitute_chain("SML", (pd.concat([smile_chain_for(exp0), smile_chain_for(exp1)], ignore_index=True), 100.0, quote_t), 0.0, today_et)
    smile_mod = importlib.import_module("openbb_cboe.utils.options.create_smile")
    def_smile = smile_mod.create_smile(built_smile[0], otm=True)
    def_exps = {str(r.Expiration)[:10] for r in def_smile.results}
    check(def_exps == {exp1.isoformat()} and len(def_smile.results) >= 4, f"default smile skips 0DTE {exp0} -> {def_exps}")
    def_traces = def_smile.chart.content["data"]
    check(len(def_traces) == 2 and def_traces[0]["x"] == [100.0, 110.0, 120.0]
          and def_traces[1]["x"] == [80.0, 90.0, 100.0],
          f"flat-vol smile centers ATM at 100 and keeps top call strike ({[t['x'] for t in def_traces]})")
    exp_smile = smile_mod.create_smile(built_smile[0], expirations=exp0.isoformat(), otm=True)
    check({str(r.Expiration)[:10] for r in exp_smile.results} == {exp0.isoformat()}, "explicit 0DTE expiration is still honored")

    # multi-expiration smile preserves top call strike and overlays puts on otm=False
    multi_exps = f"{exp0.isoformat()},{exp1.isoformat()}"
    multi_otm = smile_mod.create_smile(built_smile[0], expirations=multi_exps, otm=True)
    multi_otm_tr = multi_otm.chart.content["data"]
    check(len(multi_otm_tr) == 4
          and multi_otm_tr[0]["x"] == [100.0, 110.0, 120.0] and multi_otm_tr[2]["x"] == [100.0, 110.0, 120.0]
          and multi_otm_tr[1]["x"] == [80.0, 90.0, 100.0] and multi_otm_tr[3]["x"] == [80.0, 90.0, 100.0],
          "multi-expiry OTM smile preserves top call strike on all expirations")
    multi_all = smile_mod.create_smile(built_smile[0], expirations=multi_exps, otm=False)
    multi_all_tr = multi_all.chart.content["data"]
    check(len(multi_all_tr) == 4
          and all(tr["x"] == [80.0, 90.0, 100.0, 110.0, 120.0]
                  and all(close(v, 0.28, tol=1e-4) for v in tr["y"]) for tr in multi_all_tr)
          and all((multi_all_tr[i].get("line") or {}).get("dash") == "dash"
                  and (multi_all_tr[i].get("marker") or {}).get("symbol") == "circle-open" for i in (1, 3)),
          "multi-expiry otm=False preserves all strikes/IVs and overlays dashed open-circle Puts over solid Calls")

    # F-shaped 5-strike sloped smile with noisy ITM quote and one-sided wings
    yrs_f = om.years_to_expiry(exp1, "F", quote_t)
    f_vols = {11.0: 0.48, 11.5: 0.40, 12.0: 0.33, 12.5: 0.30, 13.0: 0.299}
    f_list = []
    for k, vol in f_vols.items():
        for side in ("call", "put"):
            v = float(om.black76(12.33, np.array([k]), yrs_f, np.array([vol]), 1.0, np.array([side == "call"]))[0])
            bid, ask = (1.30, 1.44) if (k == 11.0 and side == "call") else (v * 0.99, v * 1.01)
            f_list.append({"contract_symbol": f"F{exp1:%y%m%d}{side[0].upper()}{round(k * 1000):08d}",
                           "expiration": exp1, "strike": k, "option_type": side,
                           "bid": bid, "ask": ask, "last_trade_price": v,
                           "last_trade_time": quote_t.tz_convert("UTC"), "open_interest": 20, "volume": 5})
    f_list.append({"contract_symbol": f"F{exp1:%y%m%d}P00010500", "expiration": exp1, "strike": 10.5, "option_type": "put",
                   "bid": 0.0, "ask": 0.02, "last_trade_price": 0.01, "last_trade_time": quote_t.tz_convert("UTC"),
                   "open_interest": 20, "volume": 1})
    f_list.append({"contract_symbol": f"F{exp1:%y%m%d}C00013500", "expiration": exp1, "strike": 13.5, "option_type": "call",
                   "bid": 0.0, "ask": 0.01, "last_trade_price": 0.01, "last_trade_time": quote_t.tz_convert("UTC"),
                   "open_interest": 20, "volume": 1})
    f_rows = pd.DataFrame(f_list)
    f_df, _ = om.analyse_chain(f_rows, 12.33, quote_t, 0.0, today_et)
    f_priced = om.priced_part(f_df, 12.33)
    check(sorted(f_priced["strike"].unique()) == [11.0, 11.5, 12.0, 12.5, 13.0],
          "F-shaped chain keeps 5 two-sided strikes and excludes bid=0 wings")
    for k, want_iv in f_vols.items():
        got_iv = f_priced.loc[f_priced["strike"] == k, "implied_volatility"].iloc[0]
        check(close(got_iv, want_iv, tol=1e-4), f"F strike {k} OTM parity IV ignores ITM noise: {got_iv} != {want_iv}")

    f_chain, _ = rb.build_substitute_chain("F", (f_rows, 12.33, quote_t), 0.0, today_et)
    f_otm = smile_mod.create_smile(f_chain, otm=True)
    f_otm_tr = f_otm.chart.content["data"]
    check(len(f_otm_tr) == 2 and f_otm_tr[0]["x"] == [12.5, 13.0] and f_otm_tr[1]["x"] == [11.0, 11.5, 12.0, 12.5],
          f"F OTM smile keeps top call strike 13.0 ({[t['x'] for t in f_otm_tr]})")
    check(all(close(a, b, tol=1e-4) for a, b in zip(f_otm_tr[0]["y"], [0.30, 0.299]))
          and all(close(a, b, tol=1e-4) for a, b in zip(f_otm_tr[1]["y"], [0.48, 0.40, 0.33, 0.30])),
          "F OTM smile trace IVs match hand-computed values")
    check((f_otm_tr[0].get("marker") or {}).get("color") == "royalblue"
          and (f_otm_tr[0].get("line") or {}).get("dash") in (None, "solid")
          and (f_otm_tr[1].get("marker") or {}).get("color") == "red"
          and (f_otm_tr[1].get("line") or {}).get("dash") in (None, "solid"),
          "F OTM smile uses solid royalblue Calls and solid red Puts")
    f_res_calls = sorted(r.Strike for r in f_otm.results if getattr(r, "Option Type", None) == "call")
    f_res_puts = sorted(r.Strike for r in f_otm.results if getattr(r, "Option Type", None) == "put")
    check(f_res_calls == [12.5, 13.0] and f_res_puts == [11.0, 11.5, 12.0, 12.5],
          f"F OTM smile results match plotted strikes (calls={f_res_calls}, puts={f_res_puts})")

    f_all = smile_mod.create_smile(f_chain, otm=False)
    f_all_tr = f_all.chart.content["data"]
    check(len(f_all_tr) == 2
          and f_all_tr[0]["x"] == [11.0, 11.5, 12.0, 12.5, 13.0]
          and f_all_tr[1]["x"] == [11.0, 11.5, 12.0, 12.5, 13.0]
          and all(abs(a - b) <= 1e-9 for a, b in zip(f_all_tr[0]["y"], f_all_tr[1]["y"]))
          and (f_all_tr[0].get("line") or {}).get("color") == "royalblue"
          and (f_all_tr[0].get("line") or {}).get("dash") in (None, "solid")
          and (f_all_tr[0].get("marker") or {}).get("symbol") == "circle"
          and (f_all_tr[1].get("line") or {}).get("color") == "red"
          and (f_all_tr[1].get("line") or {}).get("dash") == "dash"
          and (f_all_tr[1].get("marker") or {}).get("symbol") == "circle-open",
          "F otm=False overlays dashed open-circle red Puts over solid royalblue Calls")

    f_skew = smile_mod.create_smile(f_chain, otm=True, skew=True)
    f_skew_tr = f_skew.chart.content["data"]
    check(len(f_skew_tr) == 2 and f_skew_tr[0]["x"] == [12.5, 13.0] and f_skew_tr[1]["x"] == [11.0, 11.5, 12.0, 12.5]
          and all(close(a, b, tol=1e-4) for a, b in zip(f_skew_tr[0]["y"], [0.0, -0.001]))
          and all(close(a, b, tol=1e-4) for a, b in zip(f_skew_tr[1]["y"], [0.18, 0.10, 0.03, 0.0])),
          "F OTM skew=True computes Skew = IV - ATM IV cleanly across all expected strikes")

    # degenerate single-call-strike OTM chain (non-SubstituteChain / raw Cboe chain) keeps ATM call visible
    from openbb_cboe.models.options_chains import CboeOptionsChainsData
    raw_chain = CboeOptionsChainsData.model_validate({
        "underlying_symbol": ["RAW"] * 6,
        "underlying_price": [100.0] * 6,
        "contract_symbol": [f"RAW{exp1:%y%m%d}{s}{round(k * 1000):08d}" for s in ("C", "P") for k in (90.0, 95.0, 100.0)],
        "expiration": [exp1] * 6,
        "dte": [7] * 6,
        "strike": [90.0, 95.0, 100.0, 90.0, 95.0, 100.0],
        "option_type": ["call", "call", "call", "put", "put", "put"],
        "implied_volatility": [0.35, 0.32, 0.30, 0.35, 0.32, 0.30],
    })
    raw_otm = smile_mod.create_smile(raw_chain, otm=True)
    raw_tr = raw_otm.chart.content["data"]
    check(len(raw_tr) == 2 and raw_tr[0]["x"] == [100.0] and raw_tr[1]["x"] == [90.0, 95.0, 100.0]
          and (raw_tr[1].get("line") or {}).get("dash") == "dash"
          and (raw_tr[1].get("marker") or {}).get("symbol") == "circle-open",
          "single-call-strike OTM overlap styles Puts as dashed open-circle so ATM call dot stays visible")
    raw_all = smile_mod.create_smile(raw_chain, otm=False)
    check("(OTM parity IV)" in f_all.chart.fig.layout.title.text
          and "(OTM parity IV)" not in f_otm.chart.fig.layout.title.text
          and "(OTM parity IV)" not in raw_all.chart.fig.layout.title.text,
          "OTM parity IV label appears only on SubstituteChain otm=False smile")

    check(len(got[0].strategies(days=-1)) == 2, "a straddle for each expiry")
    try:
        await data_handler.load_symbol("NONE")
        check(False, "no options must raise")
    except OpenBBError as e:
        check("No options available for NONE" in str(e), "no listed options reads as not covered")
    try:
        await data_handler.load_symbol("DOWN")
        check(False, "an outage must raise")
    except RuntimeError as e:
        check("not answering" in str(e) and not isinstance(e, OpenBBError), "an outage is not reported as 'no options'")
    check(asked["cboe"] == 1, "a refusing Cboe is left alone for five minutes")
    rb.SUBSTITUTE.reset(token)


asyncio.run(installed())
done("test_cboe_fallback")
