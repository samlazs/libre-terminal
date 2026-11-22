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
def chain(root="ABC", expiry=date(2026, 12, 18), quotes=True, traded=friday, strikes=(80, 90, 100, 110, 120), oi=10):
    years, out = om.years_to_expiry(expiry, root, friday), []
    for k in strikes:
        for side in ("call", "put"):
            v = float(om.black76(100.0, np.array([float(k)]), years, np.array([0.3]), 1.0, np.array([side == "call"]))[0])
            out.append({"contract_symbol": f"{root}{expiry:%y%m%d}{side[0].upper()}{k * 1000:08d}", "expiration": expiry,
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
