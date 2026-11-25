"""P3-04: the Country Monitor gives every number its period (policy rate included, from the BIS series date), and a
failed source makes the answer partial, names the source, and is held at most a minute. Hand-computed: US, Germany and
Japan; the IMF consumer-price source fails in the second run."""

import contextlib

from _common import check, done

import composite as C
import composite.macro.views as V
from composite.core.cache import Partial

KEY = ("country_monitor",)
QTRS = ["2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01"]
# real GDP levels: y/y = last / five quarters back - 1, typed by hand
GDP = {"United States": list(zip(QTRS, [100.0, 100.5, 101.0, 101.5, 102.0])),        # 102 / 100 - 1 = 2.0%
       "Germany": list(zip(QTRS, [200.0, 200.0, 201.0, 201.0, 202.0])),              # 202 / 200 - 1 = 1.0%
       "Japan": list(zip(QTRS, [500.0, 499.0, 498.0, 497.0, 497.5]))}                # 497.5 / 500 - 1 = -0.5%
UNE = {"United States": [("2026-09-01", 4.3), ("2026-10-01", 4.4)], "Germany": [("2026-09-01", 3.6)], "Japan": [("2026-10-01", 2.5)]}
CPI = {"united_states": [("2026-09-01", 2.9), ("2026-10-01", 3.1)], "germany": [("2026-10-01", 2.3)], "japan": [("2026-09-01", 2.7)]}
POL = {"US": [("2026-10-29", 3.875), ("2026-11-19", 3.625)], "XM": [("2026-11-12", 2.0)], "JP": [("2026-10-31", 0.5)]}
GY = [{"key": "united_states", "m3": 3.70, "y10": 4.40, "as_of": "2026-11-21", "freq": "daily"},
      {"key": "germany", "m3": 1.90, "y10": 2.65, "as_of": "2026-11-21", "freq": "daily"},
      {"key": "japan", "m3": 0.45, "y10": 1.70, "as_of": "2026-11-20", "freq": "daily"}]


@contextlib.contextmanager
def sources(imf_down=False):
    def imf(keys, start):
        if imf_down:
            raise C.CompositeError("IMF did not answer in time.", kind="timeout")
        return CPI
    names = ("_oecd_all", "_imf_cpi", "_policy_series", "global_yields")
    saved, monitor = {k: getattr(C, k) for k in names}, V.MONITOR
    C._oecd_all = lambda path, start: GDP if path.endswith("real") else UNE
    C._imf_cpi, C._policy_series = imf, (lambda: POL)
    C.global_yields = lambda **_: {"results": GY}
    V.MONITOR = ["united_states", "germany", "japan"]
    try:
        yield
    finally:
        V.MONITOR = monitor
        for k, v in saved.items():
            setattr(C, k, v)


def fresh():
    C._cache.clear(); C._backoff.clear(); C._neg.clear()


fresh()
with sources():
    out = C.dispatch("country_monitor", {})
rows = {r["key"]: r for r in out["results"]}
us, de, jp = rows["united_states"], rows["germany"], rows["japan"]
check(round(us["gdp_yoy"], 6) == 0.02 and round(de["gdp_yoy"], 6) == 0.01 and round(jp["gdp_yoy"], 6) == -0.005, f"GDP y/y by hand: {us['gdp_yoy']}, {de['gdp_yoy']}, {jp['gdp_yoy']}")
check(us["gdp_q"] == de["gdp_q"] == jp["gdp_q"] == "2026 Q3", f"GDP period: the quarter of the last level ({us['gdp_q']})")
check((round(us["cpi_yoy"], 6), us["cpi_m"], round(de["cpi_yoy"], 6), de["cpi_m"], round(jp["cpi_yoy"], 6), jp["cpi_m"]) == (0.031, "2026-10", 0.023, "2026-10", 0.027, "2026-09"),
      "CPI y/y and its month per country")
check((us["unemp"], us["unemp_m"], de["unemp"], de["unemp_m"], jp["unemp"], jp["unemp_m"]) == (4.4, "2026-10", 3.6, "2026-09", 2.5, "2026-10"), "unemployment and its month")
check((us["policy"], us["policy_d"], de["policy"], de["policy_d"], jp["policy"], jp["policy_d"]) == (3.625, "2026-11-19", 2.0, "2026-11-12", 0.5, "2026-10-31"),
      "policy rate and its BIS date (euro area rate for Germany)")
check((us["slope"], de["slope"], jp["slope"]) == (70, 75, 125) and us["rates_as_of"] == "2026-11-21" and jp["rates_as_of"] == "2026-11-20", "10Y-3M in bp and the rates' date")
check(out["status"] == "ok" and not out.get("notes") and out["as_of"] == "2026-11-21", "all sources answered: status ok, dated by the newest figure")
# every number column has its period column in the declared table
import xwidgets  # noqa: E402
cols = [c["field"] for c in next(w for w in xwidgets.widgets().values() if w["widgetId"] == "x_country_monitor")["data"]["table"]["columnsDefs"]]
PERIOD = {"gdp_yoy": "gdp_q", "cpi_yoy": "cpi_m", "unemp": "unemp_m", "policy": "policy_d", "m3": "rates_as_of", "y10": "rates_as_of", "slope": "rates_as_of"}
check(all(f in cols and PERIOD[f] in cols for f in PERIOD), f"every number column has its period column ({cols})")

# ---- one source failed: partial, the source named, the CPI cells blank, held at most a minute
fresh()
with sources(imf_down=True):
    out = C.dispatch("country_monitor", {})
    rows = {r["key"]: r for r in out["results"]}
    check(all(rows[k].get("cpi_yoy") is None and rows[k].get("cpi_m") is None for k in rows), "CPI blank for every country when the IMF failed")
    check(rows["united_states"]["policy"] == 3.625 and round(rows["germany"]["gdp_yoy"], 6) == 0.01, "the other sources still answer")
    check(out["status"] == "partial" and any("IMF consumer prices" in n for n in out.get("notes") or []), f"status partial with the source named: {out.get('status')} {out.get('notes')}")
    hit = C._cache.get(KEY)
    check(hit and isinstance(hit[1], Partial) and hit[1].short, "the partial answer is cached as a short Partial")
    C._cache[KEY] = (hit[0] - 59, hit[1])
    check(C.dispatch("country_monitor", {})["status"] == "partial", "still served from the cache at 59 s")
with sources():
    C._cache[KEY] = (C._cache[KEY][0] - 2, C._cache[KEY][1])        # now 61 s old: the sources are asked again
    out = C.dispatch("country_monitor", {})
check(out["status"] == "ok" and round(out["results"][0]["cpi_yoy"], 6) == 0.031, "after 60 s the sources are asked again and the IMF answers")
# ---- each of the other sources failing alone: partial, and that source named
def down(*_a, **_k):
    raise C.CompositeError("Source did not answer in time.", kind="timeout")


for name, label, blank in (("_policy_series", "policy rates (BIS)", "policy"), ("global_yields", "3-month and 10-year rates", "y10")):
    fresh()
    with sources():
        saved_fn = getattr(C, name)
        setattr(C, name, down)
        try:
            out = C.dispatch("country_monitor", {})
        finally:
            setattr(C, name, saved_fn)
    check(out["status"] == "partial" and any(label in n for n in out["notes"]) and all(r.get(blank) is None for r in out["results"]),
          f"{label} failed: partial, named, its cells blank ({out.get('notes')})")
fresh()
with sources():
    real_all = C._oecd_all
    C._oecd_all = lambda path, start: down() if path.endswith("real") else UNE
    try:
        out = C.dispatch("country_monitor", {})
    finally:
        C._oecd_all = real_all
check(out["status"] == "partial" and any("OECD real GDP" in n for n in out["notes"]) and all(r.get("gdp_yoy") is None and r.get("gdp_q") is None for r in out["results"])
      and out["results"][0]["unemp"] == 4.4, f"OECD real GDP failed: partial, named, GDP blank, unemployment kept ({out.get('notes')})")
fresh()
done("test_country_monitor")
