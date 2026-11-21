"""HANDOFF P2 trust signals beyond 'stale': fallback source, capped history, delayed prices (footer markers)."""

import sys
import pathlib

from _common import check, done

import composite as c

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_backend as rb  # noqa: E402

# ---- fallback: OECD refuses -> FRED copies, and the answer says so for the footer
saved = c._oecd_series, c._fred
try:
    def refuse(duration):
        raise c.CompositeError("OECD rate limit reached (HTTP 429)")
    c._oecd_series = refuse
    c._fred = lambda ids, start: ({ids[0]: [["2026-01-01", 2.5], ["2026-02-01", 2.6]]}, {})
    out = c.rate_history(country="germany", duration="long", start="2025")
    check(out.get("fallback") == c.FALLBACK_OECD, "rate history: OECD refused, FRED used, footer marker set")
finally:
    c._oecd_series, c._fred = saved


def fake_curves(src):
    def f(countries, dates=None, us_source="auto"):
        out = {k: {"2026-11-13": [(1.0, 3.6), (10.0, 4.1)]} for k in countries}
        out["__src__"] = {k: (src if k == "united_states" else c.COUNTRIES[k][1]) for k in countries}
        return out
    return f


saved = c.curves
try:
    c.curves = fake_curves("fred")
    check(c.yield_curves(country="united_states").get("fallback") == c.FALLBACK_US_CURVE, "U.S. curve from FRED under 'auto': marker")
    check("fallback" not in c.yield_curves(country="united_states", source="fred"), "FRED picked on purpose: no marker")
    check(c.yield_curve_table(country="united_states", compare="").get("fallback") == c.FALLBACK_US_CURVE, "curve table: marker")
    c.curves = fake_curves("fed")
    check("fallback" not in c.yield_curves(country="united_states,germany"), "Fed route answered: no marker")
finally:
    c.curves = saved

# ---- truncated: EIA sends the newest 5,000 rows; a chart starting before the oldest says so
saved = c._eia_one
try:
    c._eia_one = lambda sid: {"pts": [["2010-01-04", 80.0], ["2026-11-14", 60.0]], "meta": {"title": "WTI", "units": "$/bbl", "capped_from": "2010-01-04"}}
    _, meta = c._series(["EIA:PET.RWTC.D"], "2000-01-01")
    check(meta["EIA:PET.RWTC.D"].get("truncated") == "EIA sends the latest 5,000 observations: PET.RWTC.D starts 2010-01-04", "capped before the start asked")
    _, meta = c._series(["EIA:PET.RWTC.D"], "2015-01-01")
    check("truncated" not in meta["EIA:PET.RWTC.D"], "start after the cap: nothing to say")
finally:
    c._eia_one = saved

# ---- delayed: terminal views over Yahoo prices, backend price snapshots by path and provider
saved = dict(c.ROUTES)
try:
    c.ROUTES["board"] = lambda **p: {"results": []}
    c.ROUTES["chart"] = lambda **p: {"data": []}
    c.ROUTES["gpr_table"] = lambda **p: {"results": []}
    check(c.dispatch("board", {}).get("delayed") == c.DELAYED_YAHOO, "board: delayed")
    check("delayed" not in c.dispatch("chart", {"symbol": "FRED:DGS10"}) and c.dispatch("chart", {"symbol": "AAPL"}).get("delayed"), "FRED chart not delayed, AAPL chart delayed")
    check("delayed" not in c.dispatch("gpr_table", {}), "monthly index: not a price, no marker")
finally:
    c.ROUTES.clear()
    c.ROUTES.update(saved)
check(rb.delayed_note("/api/v1/equity/price/quote", b"symbol=AAPL&provider=cboe") == "Delayed prices from Cboe (free feed, not real time)", "Cboe quote")
check(rb.delayed_note("/api/v1/tmx/markets/movers", b"") == "Delayed prices from TMX (free feed, not real time)", "TMX movers, provider from the path")
check(rb.delayed_note("/api/v1/derivatives/options/chains", b"symbol=BTC&provider=deribit") == "", "Deribit is real time")
check(rb.delayed_note("/api/v1/equity/price/historical", b"provider=yfinance") == "", "daily history is not marked")
check(rb.stale_header([rb.delayed_note("/api/v1/index/snapshots", b"provider=yfinance")]) == b"Delayed prices from Yahoo Finance (free feed, not real time)", "header value is ASCII")
done("test_trust")
