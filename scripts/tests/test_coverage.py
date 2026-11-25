"""P3-05: the declared coverage of each route that takes a symbol, company or currency (contract.COVERAGE), held by
offline cases. A U.S.-only source asked about a listing elsewhere answers not_covered with a plain message before any
call; a closed list answers an input error for anything outside it (never another coin's or pair's figures). Comps and
consensus (global) answer a U.S. and a non-U.S. input from canned Yahoo rows."""

from _common import check, covered, done, refused

import composite as C
from composite.core import contract as K

# ---- the shared guard: the page's rule for a listing elsewhere (widgets.js T.nonUSNote)
check([C.non_us_listing(s) for s in ("AAPL", "BRK.B", "BF.B", "^GSPC", "SAP.DE", "7203.T", "SHEL.L", "RY.TO", "VOLV-B.ST", "")]
      == [False, False, False, False, True, True, True, True, True, False], "non_us_listing: share classes and indices stay U.S.")

# ---- us_only: a listing elsewhere -> not_covered, no source asked
m = refused("bank_quality", {"symbol": "SAN.MC"}, "not_covered")
check("FDIC" in m and "SAN.MC" in m, f"the message names the source and the ticker: {m}")
refused("bank_rssd", {"symbol": "RY.TO"}, "not_covered")
refused("bond_lookup", {"company": "SHEL.L"}, "not_covered")
refused("bond_curve", {"company": "7203.T"}, "not_covered")
refused("compare", {"symbols": "MSFT,SAP.DE"}, "not_covered")
refused("debt_maturity_wall", {"company": "SAP.DE"}, "not_covered")
refused("earnings_release", {"symbol": "7203.T"}, "not_covered")
refused("etf_overlap", {"symbol": "SPY", "symbol2": "VWRL.L"}, "not_covered")
refused("gex_strike", {"symbol": "SAP.DE"}, "not_covered")

# ---- set: outside the list -> input, no source asked
m = refused("crypto_vol", {"currency": "DOGE"}, "input")
check("BTC" in m and "ETH" in m, f"the choices are listed: {m}")
check("EURUSD" in refused("fx_rate_diff", {"pair": "EURXYZ"}, "input"), "fx_rate_diff lists its pairs")
refused("policy_path", {"currency": "XXX"}, "input")
refused("policy_path_table", {"currency": "XXX"}, "input")
refused("bond_pricer", {"cusip": "DE0001102580"}, "input")          # an ISIN, not a CUSIP

# ---- global: comps and consensus for a U.S. and a non-U.S. (EUR) input, from canned Yahoo rows
import composite.equity.comps as CM  # noqa: E402

MET = {"AAPL": {"symbol": "AAPL", "currency": "USD", "pe_ratio": 35.0, "market_cap": 3.9e12, "enterprise_value": 3.95e12},
       "MSFT": {"symbol": "MSFT", "currency": "USD", "pe_ratio": 33.0, "market_cap": 3.6e12, "enterprise_value": 3.58e12},
       "SAP.DE": {"symbol": "SAP.DE", "currency": "EUR", "pe_ratio": 42.0, "market_cap": 2.5e11, "enterprise_value": 2.45e11},
       "ADS.DE": {"symbol": "ADS.DE", "currency": "EUR", "pe_ratio": 28.0, "market_cap": 3.5e10, "enterprise_value": 3.9e10}}
META = {s: {"name": s + " name", "currency": m["currency"]} for s, m in MET.items()}
saved = {k: getattr(C, k) for k in ("api", "_meta", "_profiles", "_usd_rates", "_est_raw")}
try:
    C.api = lambda base, path, **kw: [MET[x] for x in kw["symbol"].split(",") if x in MET] if path == "equity/fundamental/metrics" else []
    C._meta = lambda syms: {x: META[x] for x in syms if x in META}
    C._profiles = lambda syms: {}
    C._usd_rates = lambda ccys: {"USD": 1.0, "EUR": 1.10}
    for sym, peer, mkt in (("AAPL", "MSFT", "us"), ("SAP.DE", "ADS.DE", "eur")):
        C._cache.clear(); CM._METRICS.clear()
        out = C.dispatch("comps", {"symbol": sym, "peers": peer})
        covered("comps", "us", out) if mkt == "us" else covered("comps", "eur", out)      # literal markets: test_contract reads them
        row = out["results"][0]
        check(row["symbol"] == sym and row["currency"] == MET[sym]["currency"] and row["pe"] == MET[sym]["pe_ratio"], f"comps {sym}: its own row, currency and P/E ({row})")
        check(out.get("as_of_na") == K.UNDATED["comps"], "comps: Yahoo's undated figures, said so")
    est = {"AAPL": {"eps": [{"period": "0q", "mean": 1.85, "low": 1.7, "high": 2.0, "number_of_analysts": 30, "currency": "USD"}],
                    "rev": [{"period": "0q", "mean": 1.38e11, "currency": "USD"}], "trend": [], "revs": []},
           "SAP.DE": {"eps": [{"period": "0q", "mean": 1.62, "low": 1.5, "high": 1.75, "number_of_analysts": 20, "currency": "EUR"}],
                      "rev": [{"period": "0q", "mean": 9.6e9, "currency": "EUR"}], "trend": [], "revs": []}}
    C._est_raw = lambda sym: est[sym]
    for sym, mkt in (("AAPL", "us"), ("SAP.DE", "eur")):
        C._cache.clear()
        out = C.dispatch("consensus", {"symbol": sym})
        covered("consensus", "us", out) if mkt == "us" else covered("consensus", "eur", out)      # literal markets: test_contract reads them
        check(out["results"][0]["eps"] == est[sym]["eps"][0]["mean"] and out["results"][0]["rev"] == est[sym]["rev"][0]["mean"], f"consensus {sym}: EPS and revenue as given")
        want = {"AAPL": "USD", "SAP.DE": "EUR"}[sym]
        check(out.get("currency") == want and want in out.get("convention", ""), f"consensus {sym}: the answer names {want} and the card's footer says it")
finally:
    for k, v in saved.items():
        setattr(C, k, v)
    C._cache.clear(); CM._METRICS.clear()

check(set(K.COVERAGE.values()) == {"global", "us_only", "set"}, "three coverage kinds in use")
done("test_coverage")
