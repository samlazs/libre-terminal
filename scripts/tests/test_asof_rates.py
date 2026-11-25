"""P3-03: rates, policy, macro, geo and CDS views answer as_of = the newest data point of their input.

Each view runs for real through C.dispatch(route, params) (strict contract mode) with its upstream faked at the composite
helper it calls; the expected date is typed by hand from the fixture, never computed. The last section holds cases whose
fixture is correct but whose view may answer another date (see the report of the run that added this file)."""

import contextlib
import datetime as dt

from _common import check_as_of, done, make_tmp_dir

import cds
import composite as C
import policy_path as pp


@contextlib.contextmanager
def patch(obj, **kw):
    saved = {k: getattr(obj, k) for k in kw}
    try:
        for k, v in kw.items():
            setattr(obj, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


def fresh():
    C._cache.clear()
    C._backoff.clear()


def run(route, params=None, **patches):
    """The real view through dispatch with the named composite helpers replaced, caches cleared before and after."""
    fresh()
    try:
        with patch(C, **patches):
            return C.dispatch(route, dict(params or {}))
    finally:
        fresh()


def daily(end, n, v=1.0):
    d0 = dt.date.fromisoformat(end)
    return [[(d0 - dt.timedelta(days=n - 1 - i)).isoformat(), round(v + i * 0.01, 4)] for i in range(n)]


def monthly(first, last, v=1.0):
    """[[YYYY-MM-01, value], ...] from month `first` to month `last` (both YYYY-MM)."""
    y, m = int(first[:4]), int(first[5:7])
    out = []
    while f"{y}-{m:02d}" <= last:
        out.append([f"{y}-{m:02d}-01", round(v + len(out) * 0.02, 4)])
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def months_ym(first, last, v=1.0):
    """[(YYYY-MM, value), ...]: BIS monthly policy-rate points."""
    return [(d[:7], x) for d, x in monthly(first, last, v)]


# ---------------------------------------------------------------- yield curves (C.curves is the seam)
US_CURVE = [(0.25, 4.20), (1.0, 4.10), (2.0, 4.00), (5.0, 4.15), (10.0, 4.50), (30.0, 4.80)]
EU_CURVE = [(0.25, 2.10), (1.0, 2.10), (2.0, 2.20), (5.0, 2.45), (10.0, 2.80), (30.0, 3.10)]
JP_CURVE = [(1.0, 0.70), (2.0, 0.90), (5.0, 1.20), (10.0, 1.70)]
GB_CURVE = [(0.25, 4.00), (1.0, 3.90), (2.0, 3.80), (5.0, 3.90), (10.0, 4.30), (30.0, 4.90)]
DE_CURVE = [(0.25, 2.00), (1.0, 2.05), (2.0, 2.15), (5.0, 2.40), (10.0, 2.70), (30.0, 3.00)]
FR_MONTHLY = [(0.25, 2.40), (10.0, 3.10)]


def fake_curves(data):
    def curves(countries, dates=None, us_source="auto"):
        out = {c: dict(data.get(c, {})) for c in countries}
        out["__src__"] = {c: C.COUNTRIES[c][1] for c in countries}
        return out
    return curves


def no_rates():
    return {"results": [], "sources": []}


# global_yields: US newest, then euro area and Japan; every policy rate is older than the curve it sits beside
o = run("global_yields", {"countries": "g10"},
        curves=fake_curves({"united_states": {"2026-11-10": US_CURVE, "2026-11-24": US_CURVE}, "euro_area": {"2026-11-21": EU_CURVE}, "japan": {"2026-11-20": JP_CURVE}}),
        _policy_series=lambda: {"US": [("2026-11-20", 3.625)], "XM": [("2026-11-14", 2.0)]}, rates_by_country=no_rates)
check_as_of("global_yields", o, "2026-11-24", "US curve is the newest point")
# non-U.S.: Germany and the U.K. daily, France only as an OECD monthly row (older)
o = run("global_yields", {},
        curves=fake_curves({"germany": {"2026-11-23": DE_CURVE}, "united_kingdom": {"2026-11-19": GB_CURVE}}),
        _policy_series=lambda: {"XM": [("2026-11-12", 2.0)], "GB": [("2026-11-06", 4.0)]},
        rates_by_country=lambda **_: {"results": [{"country": "France", "m3": 2.4, "y10": 3.1, "as_of": "2026-10", "y10_chg_1m": 3, "y10_chg_1y": -20}],
                                      "sources": [{"name": "OECD", "detail": "3-month interbank and 10-year interest rates, monthly", "url": ""}]})
check_as_of("global_yields", o, "2026-11-23", "Germany's curve is the newest point, the monthly row is older")

# ---------------------------------------------------------------- yield_curves
o = run("yield_curves", {"country": "united_states"}, curves=fake_curves({"united_states": {"2026-11-24": US_CURVE, "2026-11-23": US_CURVE}}))
check_as_of("yield_curves", o, "2026-11-24", "US: newest curve date")
o = run("yield_curves", {"country": "germany,united_kingdom,japan", "compare": "1M"},
        curves=fake_curves({"germany": {"2026-11-20": DE_CURVE, "2026-10-21": DE_CURVE}, "united_kingdom": {"2026-11-23": GB_CURVE, "2026-10-23": GB_CURVE},
                            "japan": {"2026-11-19": JP_CURVE, "2026-10-20": JP_CURVE}}))
check_as_of("yield_curves", o, "2026-11-23", "non-US: the U.K. curve is newest; the earlier comparison dates do not count")
o = run("yield_curves", {"country": "france"}, curves=fake_curves({"france": {"2026-09-01": FR_MONTHLY, "2026-10-01": FR_MONTHLY}}))
check_as_of("yield_curves", o, "2026-10-01", "monthly OECD curve: its newest month")

# ---------------------------------------------------------------- yield_curve_table
o = run("yield_curve_table", {"country": "united_states", "compare": ""}, curves=fake_curves({"united_states": {"2026-11-24": US_CURVE}}))
check_as_of("yield_curve_table", o, "2026-11-24", "US: the curve date")
o = run("yield_curve_table", {"country": "japan,germany", "compare": "1M"},
        curves=fake_curves({"japan": {"2026-11-18": JP_CURVE, "2026-10-19": JP_CURVE}, "germany": {"2026-11-23": DE_CURVE, "2026-10-23": DE_CURVE}}))
check_as_of("yield_curve_table", o, "2026-11-23", "non-US: Germany's curve is newer than Japan's")

# ---------------------------------------------------------------- rate_history (OECD copy at C._oecd_series, FRED's copy at C._fred_mei)
OECD_LONG = {"United States": monthly("2024-01", "2026-10", 4.0), "Germany": monthly("2024-01", "2026-09", 2.0), "Japan": monthly("2024-01", "2026-08", 1.0)}
o = run("rate_history", {"country": "united_states", "duration": "long", "source": "oecd", "start": "2024"}, _oecd_series=lambda duration: OECD_LONG)
check_as_of("rate_history", o, "2026-10-01", "US: last OECD month")
o = run("rate_history", {"country": "germany,japan", "duration": "long", "source": "oecd", "start": "2024"}, _oecd_series=lambda duration: OECD_LONG)
check_as_of("rate_history", o, "2026-09-01", "Germany and Japan asked: the newer U.S. month is not used")


def busy(duration):
    raise C.CompositeError("OECD limits requests per hour", kind="upstream")


o = run("rate_history", {"country": "united_kingdom", "duration": "long", "source": "auto", "start": "2024"}, _oecd_series=busy,
        _fred_mei=lambda keys, duration, start: {"united_kingdom": ("IRLTLT01GBM156N", monthly("2024-01", "2026-09", 4.0))})
check_as_of("rate_history", o, "2026-09-01", "OECD refused: FRED's copy, its last month")

# ---------------------------------------------------------------- rates_by_country
ALL = {"United States": monthly("2025-06", "2026-10", 4.0), "Germany": monthly("2025-06", "2026-09", 2.0)}
o = run("rates_by_country", {"source": "oecd"}, _oecd_latest=lambda d: ALL)
check_as_of("rates_by_country", o, "2026-10", "US: every leg in October")
ALL2 = {"Germany": monthly("2025-06", "2026-09", 2.0), "Japan": monthly("2025-06", "2026-07", 0.5), "United Kingdom": monthly("2025-06", "2026-08", 4.0)}
o = run("rates_by_country", {"source": "oecd"}, _oecd_latest=lambda d: ALL2)
check_as_of("rates_by_country", o, "2026-09", "non-US: Germany's September is the newest month")
FRED_ALL = {"united_states": ("SRS", monthly("2025-06", "2026-10", 4.0)), "italy": ("SRI", monthly("2025-06", "2026-08", 3.0))}
o = run("rates_by_country", {"source": "fred"}, _fred_mei=lambda keys, duration, start: {k: v for k, v in FRED_ALL.items() if k in keys})
check_as_of("rates_by_country", o, "2026-10", "FRED copy: the U.S. October")

# ---------------------------------------------------------------- policy_rates / policy_rate_history (BIS monthly + daily)
US_M = months_ym("2025-01", "2026-10", 3.5)
GB_M = months_ym("2025-01", "2026-09", 4.0)
o = run("policy_rates", {}, _bis_policy=lambda: {"US": US_M, "GB": GB_M}, _bis_policy_daily=lambda: {})
check_as_of("policy_rates", o, "2026-10", "month-end values: US October")
JP_M = months_ym("2025-01", "2026-10", 0.25)
JP_D = [("2026-11-18", 0.5), ("2026-11-24", 0.75)]
o = run("policy_rates", {}, _bis_policy=lambda: {"JP": JP_M, "GB": GB_M}, _bis_policy_daily=lambda: {"JP": JP_D})
check_as_of("policy_rates", o, "2026-11-24", "Japan's daily series is newest")

o = run("policy_rate_history", {"central_bank": "US", "start": "2025"}, _bis_policy=lambda: {"US": US_M}, _bis_policy_daily=lambda: {})
check_as_of("policy_rate_history", o, "2026-10-01", "US: monthly points are plotted on the first of the month")
XM_M = months_ym("2025-01", "2026-10", 2.5)
o = run("policy_rate_history", {"central_bank": "XM,JP", "start": "2025"}, _bis_policy=lambda: {"XM": XM_M, "JP": JP_M},
        _bis_policy_daily=lambda: {"XM": [("2026-11-12", 2.25), ("2026-11-19", 2.0)]})
check_as_of("policy_rate_history", o, "2026-11-19", "euro area daily points after the last month")

# ---------------------------------------------------------------- policy_path / policy_path_table (CFTC prints at C.api, fixings at pp._fred_last)
TODAY = dt.date(2026, 11, 19)


def prow(start, end, rate, date, trades=10):
    return {"start_date": start, "end_date": end, "rate": rate, "num_trades": trades, "as_of_date": date, "date": date}


def run_path(route, params, paths, fixings, policy):
    fresh()
    calls = []

    def api(base, path, currency=None, date=None, **_):
        calls.append((path, currency, date))
        return paths.get((currency, date), [])

    saved = pp._fred_last
    try:
        pp._fred_last = fixings.get
        with patch(C, api=api, _policy_series=lambda: policy):
            return C.dispatch(route, dict(params, today=TODAY))
    finally:
        pp._fred_last = saved
        fresh()


USD_PATHS = {("USD", None): [prow("2026-12-10", "2027-01-28", 3.62, "2026-11-18", 50), prow("2027-01-28", "2027-03-18", 3.50, "2026-11-18")]}
GBP_PATHS = {("GBP", None): [prow("2026-12-17", "2027-02-04", 3.90, "2026-11-17", 40), prow("2027-02-04", "2027-03-19", 3.80, "2026-11-16")]}
USD_OLD = {("USD", None): [prow("2026-12-10", "2027-01-28", 3.62, "2026-11-17", 50), prow("2027-01-28", "2027-03-18", 3.50, "2026-11-17")]}
FIX = {"SOFR": ("2026-11-18", 3.60), "DFEDTARL": ("2026-11-18", 3.50), "DFEDTARU": ("2026-11-18", 3.75), "IUDSOIA": ("2026-11-16", 3.95)}
POL = {"US": [("2026-11-10", 3.625)], "GB": [("2026-11-12", 4.0)]}

o = run_path("policy_path_table", {"currency": "USD"}, USD_OLD, FIX, POL)
check_as_of("policy_path_table", o, "2026-11-18", "USD: prints of 2026-11-17, the SOFR fixing of 2026-11-18 is newer and shown")
o = run_path("policy_path_table", {"currency": "GBP"}, GBP_PATHS, FIX, POL)
check_as_of("policy_path_table", o, "2026-11-17", "GBP: prints of 2026-11-17, SONIA of 2026-11-16 is older")

# ---------------------------------------------------------------- cpi_history (IMF at C._imf_cpi)
IMF = {"united_states": monthly("2024-01", "2026-10", 2.5), "germany": monthly("2024-01", "2026-09", 2.0), "japan": monthly("2024-01", "2026-08", 1.5)}
o = run("cpi_history", {"country": "united_states", "start": "2024"}, _imf_cpi=lambda keys, start: IMF)
check_as_of("cpi_history", o, "2026-10-01", "US: last IMF month")
o = run("cpi_history", {"country": "germany,japan", "start": "2024"}, _imf_cpi=lambda keys, start: IMF)
check_as_of("cpi_history", o, "2026-09-01", "Germany and Japan: Germany's September")

# ---------------------------------------------------------------- macro_chart (C._series is the seam)
FRED_SETS = {"BAMLH0A0HYM2": daily("2026-11-24", 30, 3.0), "BAMLC0A4CBBB": daily("2026-11-23", 30, 1.2),
             "BAMLC0A0CM": daily("2026-11-24", 30, 0.9), "BAMLEMCBPIOAS": daily("2026-11-20", 30, 2.4)}


def series_fake(data, meta=None):
    return lambda ids, start: ({i: data[i] for i in ids if i in data}, {i: (meta or {}).get(i, {}) for i in ids})


o = run("macro_chart", {"preset": "credit_spreads", "since": "2026"}, _series=series_fake(FRED_SETS))
check_as_of("macro_chart", o, "2026-11-24", "credit spreads: the newest of four daily series")
o = run("macro_chart", {"series": "UNRATE", "since": "2024"}, _series=series_fake({"UNRATE": monthly("2024-01", "2026-10", 4.0)}, {"UNRATE": {"units": "Percent", "title": "Unemployment rate"}}))
check_as_of("macro_chart", o, "2026-10-01", "custom monthly FRED series")

# ---------------------------------------------------------------- country_monitor (OECD, IMF, BIS and Global Yields at their composite helpers)
GDP_Q = lambda last: [[d, 100 + i * 0.5] for i, d in enumerate(last)]       # five quarters ending at the date typed in the call
QTRS = ["2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01"]


def monitor(gdp, une, cpi, pol, gy):
    return run("country_monitor", {}, _oecd_all=lambda path, start: gdp if path.endswith("real") else une, _imf_cpi=lambda keys, start: cpi,
               _policy_series=lambda: pol, global_yields=lambda **_: {"results": gy})


o = monitor({"United States": GDP_Q(QTRS)}, {"United States": monthly("2025-01", "2026-09", 4.0)}, {"united_states": monthly("2025-01", "2026-10", 3.0)},
            {"US": [("2026-11-05", 3.875), ("2026-11-20", 3.625)]}, [{"key": "united_states", "m3": 3.7, "y10": 4.4, "as_of": "2026-11-18", "freq": "daily"}])
check_as_of("country_monitor", o, "2026-11-20", "US: the policy rate is the newest point")
o = monitor({"Germany": GDP_Q(QTRS)}, {"Germany": monthly("2025-01", "2026-10", 3.5)}, {"germany": monthly("2025-01", "2026-10", 2.2), "japan": monthly("2025-01", "2026-09", 2.8)},
            {"XM": [("2026-11-12", 2.0)], "JP": [("2026-10-31", 0.5)]},
            [{"key": "germany", "m3": 2.0, "y10": 2.7, "as_of": "2026-11-21", "freq": "daily"}, {"key": "japan", "m3": 0.4, "y10": 1.7, "as_of": "2026-10", "freq": "monthly"}])
check_as_of("country_monitor", o, "2026-11-21", "Germany/Japan: Germany's yield curve is the newest point")

# ---------------------------------------------------------------- gpr_table (C._gpr) and chokepoint_monitor (C._chokepoints)
o = run("gpr_table", {}, _gpr=lambda: {"M_GPR": monthly("2024-01", "2026-10", 100), "M_GPRC_USA": monthly("2024-01", "2026-10", 90), "M_GPRC_DEU": monthly("2024-01", "2026-08", 80)})
check_as_of("gpr_table", o, "2026-10", "US: October is the newest month")
o = run("gpr_table", {}, _gpr=lambda: {"M_GPR": monthly("2024-01", "2026-08", 100), "M_GPRC_RUS": monthly("2024-01", "2026-09", 150), "M_GPRC_DEU": monthly("2024-01", "2026-06", 80)})
check_as_of("gpr_table", o, "2026-09", "Russia's September beats the world index (August) and Germany (June)")


def pw(name, end, fields=("total", "tanker", "container", "dry_bulk"), ends=None):
    return {f"{name}:vessels_{f}": daily((ends or {}).get(f, end), 40, 20.0) for f in fields}


o = run("chokepoint_monitor", {}, _chokepoints=lambda: dict(pw("Strait of Hormuz", "2026-11-23"), **pw("Suez Canal", "2026-11-22")))
check_as_of("chokepoint_monitor", o, "2026-11-23", "Hormuz is the newest")
o = run("chokepoint_monitor", {}, _chokepoints=lambda: dict(pw("Panama Canal", "2026-11-21"), **pw("Malacca Strait", "2026-11-19")))
check_as_of("chokepoint_monitor", o, "2026-11-21", "Panama is the newest")

# ---------------------------------------------------------------- cds_indices / cds_history (CFTC day files at C.api)
NEW = "2031-12-20"


def cp(index, spread, notional, ccy="USD", notation="3"):
    return {"index": index, "maturity_date": NEW, "spread": spread, "spread_notation": notation, "notional_amount": notional,
            "action_type": "NEWT", "event_type": "TRAD", "notional_currency": ccy}


def run_cds(route, params, days, today):
    fresh()
    C.reset_runtime_caches()
    for f in C.CACHE_DIR.glob(cds.day_name("*") + ".json"):
        f.unlink()
    tmp = make_tmp_dir("asof-cds-") / "days.json"
    saved = C.api, cds._path, cds._days

    def api(base, path, tenor=None, date=None, **_):
        return days.get(date, [])

    try:
        C.api, cds._path, cds._days = api, (lambda: tmp), None
        return C.dispatch(route, dict(params, today=today))
    finally:
        C.api, cds._path, cds._days = saved
        fresh()


DAYS_A = {"2026-11-18": [cp("CDX.NA.IG", 0.0060, 50e6), cp("CDX.NA.HY", 0.0106, 30e6, notation="3")], "2026-11-17": [cp("CDX.NA.IG", 0.0058, 10e6)]}
DAYS_B = {"2026-11-20": [cp("ITRAXX EUROPE", 0.0055, 20e6, "EUR")], "2026-11-19": [cp("CDX.NA.IG", 0.0059, 40e6), cp("ITRAXX EUROPE", 0.0054, 20e6, "EUR")]}
o = run_cds("cds_indices", {}, DAYS_A, dt.date(2026, 11, 19))
check_as_of("cds_indices", o, "2026-11-18", "CDX: newest dissemination day")
o = run_cds("cds_indices", {}, DAYS_B, dt.date(2026, 11, 24))
check_as_of("cds_indices", o, "2026-11-20", "iTraxx prints one day after CDX")
o = run_cds("cds_history", {"family": "CDX.NA.IG", "range": "1M"}, DAYS_A, dt.date(2026, 11, 19))
check_as_of("cds_history", o, "2026-11-18", "CDX IG: last plotted day")
o = run_cds("cds_history", {"family": "ITRAXX EUROPE,CDX.NA.IG", "range": "1M"}, DAYS_B, dt.date(2026, 11, 24))
check_as_of("cds_history", o, "2026-11-20", "iTraxx and CDX IG: the later last day")

# ================================================================ cases whose view may date its answer differently
# policy_path: the chart's x values are meeting periods in the future; its as_of must be the day of the newest print
o = run_path("policy_path", {"currency": "USD"}, USD_PATHS, FIX, POL)
check_as_of("policy_path", o, "2026-11-18", "USD: newest print 2026-11-18, not the end of the last meeting period")
o = run_path("policy_path", {"currency": "GBP"}, GBP_PATHS, FIX, POL)
check_as_of("policy_path", o, "2026-11-17", "GBP: newest print 2026-11-17")

# macro_chart: a seasonal view plots on a template year (2000); an EIA STEO forecast is not an observation
o = run("macro_chart", {"series": "UNRATE", "transform": "seasonal"}, _series=series_fake({"UNRATE": monthly("2019-01", "2026-10", 4.0)}, {"UNRATE": {"units": "Percent"}}))
check_as_of("macro_chart", o, "2026-10-01", "seasonal view: the newest observation, not a template-year date")
steo = [[d, v] for d, v in monthly("2026-05", "2027-03", 90.0)]
o = run("macro_chart", {"series": "EIA:STEO.BREPUUS", "since": "2026"}, _series=series_fake({"EIA:STEO.BREPUUS": steo}, {"EIA:STEO.BREPUUS": {"title": "Brent"}}),
        _month_start=lambda: "2026-11-01")
check_as_of("macro_chart", o, "2026-10-01", "EIA STEO: the latest actual month, not the last forecast month")

# chokepoint_monitor: each field has its own last day; the row (and so the answer) is dated by the newest of them
o = run("chokepoint_monitor", {}, _chokepoints=lambda: pw("Panama Canal", "2026-11-24", ends={"dry_bulk": "2026-11-20"}))
check_as_of("chokepoint_monitor", o, "2026-11-24", "dry bulk stopped on 2026-11-20, total and tanker run to 2026-11-24")

# global_yields: the policy rate shown in a row is a data point of the answer
o = run("global_yields", {}, curves=fake_curves({"united_states": {"2026-11-20": US_CURVE}}), _policy_series=lambda: {"US": [("2026-11-24", 3.875)]}, rates_by_country=no_rates)
check_as_of("global_yields", o, "2026-11-24", "the policy rate of 2026-11-24 is newer than the curve of 2026-11-20")

done("test_asof_rates")
