"""Nasdaq ETF fallback (owner 2026-11-18: BWET, an NYSE Arca ETF, came up thin). openbb_nasdaq asks every quote,
profile, history and short-interest call with assetclass=stocks; Nasdaq answers ETFs only with assetclass=etf. The
runtime patch asks again as an ETF when Nasdaq has no such stock. Short interest: FINRA first (every U.S. listing)."""

import asyncio
import importlib.util
import sys
import types

from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

p = rb.nasdaq_etf_path
check(p("quote/BWET/info?assetclass=stocks") == "quote/BWET/info?assetclass=etf", "info asked as an ETF")
check(p("quote/KO/short-interest?assetClass=stocks") == "quote/KO/short-interest?assetClass=etf", "short interest (assetClass spelling)")
check(p("quote/SPY/historical?assetclass=stocks&fromdate=2026-01-01&limit=9999") == "quote/SPY/historical?assetclass=etf&fromdate=2026-01-01&limit=9999",
      "history: other parameters kept")
check(p("quote/SPY/info?assetclass=etf") is None and p("quote/X/info?assetclass=stocksplus") is None and p("calendar/splits?date=2026-01-01") is None,
      "only per-symbol paths asked as stocks")

# the wrapper, on a fake openbb_nasdaq.utils.helpers
calls = []
STORE = {"quote/BWET/info?assetclass=etf": {"symbol": "BWET"}, "quote/AAPL/info?assetclass=stocks": {"symbol": "AAPL"},
         "quote/QQQ/short-interest?assetClass=etf": {"rows": 1}}


async def fake(path, use_cache=True, **kw):
    calls.append(path)
    if path == "quote/BWET/info?assetclass=stocks":
        raise RuntimeError("Nasdaq request failed for 'quote/BWET/info?assetclass=stocks'. Symbol not exists.")
    if path == "quote/BOOM/info?assetclass=stocks":
        raise RuntimeError("HTTP 503")
    if path == "quote/KO/short-interest?assetClass=etf":
        raise RuntimeError("Symbol not exists.")
    return STORE.get(path)


helpers = types.ModuleType("openbb_nasdaq.utils.helpers")
helpers.get_nasdaq_data = fake
pkg, utils = types.ModuleType("openbb_nasdaq"), types.ModuleType("openbb_nasdaq.utils")
pkg.utils, utils.helpers = utils, helpers
saved = {k: sys.modules.get(k) for k in ("openbb_nasdaq", "openbb_nasdaq.utils", "openbb_nasdaq.utils.helpers")}
sys.modules.update({"openbb_nasdaq": pkg, "openbb_nasdaq.utils": utils, "openbb_nasdaq.utils.helpers": helpers})
try:
    applied = []
    rb.install_nasdaq_etf_fallback(applied)
    g = helpers.get_nasdaq_data
    check(applied and g is not fake, "installed")
    run = asyncio.run
    check(run(g("quote/BWET/info?assetclass=stocks")) == {"symbol": "BWET"}, "'Symbol not exists' as stocks -> answered as an ETF")
    calls.clear()
    check(run(g("quote/AAPL/info?assetclass=stocks")) == {"symbol": "AAPL"} and calls == ["quote/AAPL/info?assetclass=stocks"], "a stock: one call")
    check(run(g("quote/QQQ/short-interest?assetClass=stocks")) == {"rows": 1}, "empty as stocks -> asked as an ETF")
    check(run(g("quote/KO/short-interest?assetClass=stocks")) is None, "not an ETF either: the stocks answer (empty) stays")
    try:
        run(g("quote/BOOM/info?assetclass=stocks"))
        check(False, "other errors are raised")
    except RuntimeError as e:
        check("503" in str(e), "other errors are raised, not retried")
finally:
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

sys.path.insert(0, str(ROOT / "scripts"))
import build_dashboards as bd  # noqa: E402

members = bd.CONCEPTS["shorts.interest"]["members"]
check(members[0][1] == "finra" and members[1][1] == "nasdaq" and "Nasdaq-listed only" in members[1][2],
      "short interest: FINRA first (every U.S. listing), Nasdaq labelled Nasdaq-listed only")
done("test_nasdaq_etf")
