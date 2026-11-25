"""Numeric boundary (P3-01): num() turns every source value into a finite float or None, banded() blanks a figure
outside its plausible range with a note, the note travels through every cache, and in strict mode no NaN/Inf reaches
the serializer. Every expected value is worked out by hand."""

import io
import json
import math
from contextlib import redirect_stdout

import numpy as np

from _common import check, done

import composite as C
import fred_public
import server
from composite.core import contract as K

# ---- num(): finite float or None
for x in (None, True, False, float("nan"), float("inf"), float("-inf"), "nan", "NaN", "inf", "-inf", "Infinity", "", " ", ".",
          "n/a", "abc", "1,5", 999999.99, -999999.99, 9999999.99, "999999.99", 10 ** 400, [1], {"a": 1}):
    check(C.num(x) is None, f"num({x!r:.20}) is None")
for x, want in ((0, 0.0), (1, 1.0), (-2.5, -2.5), ("1.5", 1.5), (" 2 ", 2.0), ("-0.25", -0.25), ("1e3", 1000.0),
                (999999, 999999.0), (999999.98, 999999.98), (np.float64(2.5), 2.5), (np.int64(7), 7.0), (np.float32(0.5), 0.5)):
    got = C.num(x)
    check(isinstance(got, float) and got == want, f"num({x!r}) == {want}")
check(C.num(np.float64("nan")) is None, "numpy NaN is None")

# ---- finite(): only the finiteness test (the serializer scrub keeps a placeholder as it is)
check(C.finite(999999.99) == 999999.99 and C.finite(3) == 3.0, "finite keeps any finite number")
check(all(C.finite(x) is None for x in (float("nan"), float("inf"), "1", True, None)), "finite: NaN, Inf, text, bool -> None")

# ---- band edges, (lo, hi, just outside) for each initial band; fractions are stored values
EDGES = {
    "ev_ebitda": ([0.0001, 200, 12.5], [0, -1, 200.01, 1e6]),                  # (0, 200]
    "ev_sales": ([0.0001, 100, 3], [0, 100.01, -0.5]),                          # (0, 100]
    "pb": ([0.0001, 200, 1.2], [0, 200.5]),                                     # (0, 200]
    "eps_surprise_pct": ([-5.0, 5.0, 0.1, 0.0], [-5.01, 5.01, 999.0]),          # [-500%, 500%] as fractions
    "move_2d_pct": ([-0.8, 3.0, 0.0], [-0.81, 3.01]),                           # [-80%, 300%] as fractions
    "daily_return": ([-0.9, 9.0, 0.0], [-0.91, 9.01, 19.0]),                    # [-0.9, 9]
    "yield_pct": ([-5, 40, 4.25, 0.0], [-5.01, 40.01, 999999.0]),               # [-5, 40] percent
    "gdp_yoy_pct": ([-0.3, 0.3, 0.021], [-0.31, 0.31]),                         # [-30%, 30%] as fractions
}
check(set(EDGES) <= set(C.BANDS), "every initial band is registered")
for metric, (inside, outside) in EDGES.items():
    for x in inside:
        check(C.in_band(metric, x) and C.banded(metric, x) == float(x), f"{metric}: {x} inside")
    for x in outside:
        check(not C.in_band(metric, x) and C.banded(metric, x) is None, f"{metric}: {x} outside -> blank")
check(C.banded("pb", None) is None and C.banded("pb", float("nan")) is None and C.banded("pb", "nan") is None, "missing in, blank out")

check(C.band_note("ev_ebitda", 350, "XYZ") == "EV/EBITDA for XYZ left blank: 350x is outside the plausible range (above 0x up to 200x)",
      "EV/EBITDA note: " + C.band_note("ev_ebitda", 350, "XYZ"))
check(C.band_note("eps_surprise_pct", 9.99) == "EPS surprise left blank: 999% is outside the plausible range (-500% to 500%)",
      "surprise note in percent: " + C.band_note("eps_surprise_pct", 9.99))
check(C.band_note("daily_return", 19.0) == "1-day return left blank: 1,900% is outside the plausible range (-90% to 900%)",
      "daily return note: " + C.band_note("daily_return", 19.0))

# ---- the note is recorded on the request (and only once), never outside one
C.banded("pb", 500, "ZZZ")                                  # no request running: nothing recorded, nothing raised
got = C._collect(lambda: [C.banded("pb", 500, "ZZZ"), C.banded("pb", 500, "ZZZ"), C.banded("pb", 2, "ZZZ")])
check(isinstance(got, C.Partial) and got.value == [None, None, 2.0], "collected value keeps the in-band figure")
check(got.blanked == ["P/B for ZZZ left blank: 500x is outside the plausible range (above 0x up to 200x)"], "one note per blank")
check(not got.missing and not got.fallbacks and not got.short, "a blanked figure is a complete answer: normal cache life")

# ---- the note travels through the disk cache and reaches the view's notes; status unchanged
calls = []


def banded_view(**_):
    def run():
        calls.append(1)
        return {"results": [{"symbol": "ZZZ", "pb": C.banded("pb", 500, "ZZZ"), "pe": 21.0}], "as_of": "2026-11-20",
                "sources": [C.src("Test", "band test", "https://example.test/")]}
    return C.cache(("t_band",), 3600, run, disk="t_band")


C.ROUTES["t_band"] = banded_view
DISK = C.CACHE_DIR / "t_band.json"
C.reset_runtime_caches()
DISK.unlink(missing_ok=True)
NOTE = "P/B for ZZZ left blank: 500x is outside the plausible range (above 0x up to 200x)"
first = C.dispatch("t_band", {})
check(first["results"][0]["pb"] is None and first["results"][0]["pe"] == 21.0, "out-of-band P/B blank, P/E kept")
check(NOTE in first.get("notes", []) and first.get("status", "ok") == "ok", "band note on the answer, status ok")
saved = json.loads(DISK.read_text(encoding="utf-8"))
check(saved.get("bn") == [NOTE] and not saved.get("fb"), "saved to disk with its blank note")
again = C.dispatch("t_band", {})
check(len(calls) == 1 and NOTE in again.get("notes", []), "memory hit keeps the note")
C.reset_runtime_caches()
disk_hit = C.dispatch("t_band", {})
check(len(calls) == 1 and NOTE in disk_hit.get("notes", []) and disk_hit["results"][0]["pb"] is None, "disk hit keeps the note")
DISK.unlink(missing_ok=True)

# ---- strict mode: a NaN/Inf in a view's answer is a defect, named by its path
check(C.nonfinite_path({"results": [{"a": 1.0}, {"b": [1, float("inf")]}]}) == "results[1].b[1]", "path of the first Inf")
check(C.nonfinite_path({"a": [1.0, "nan", None]}) is None, "text and None are not non-finite floats")


def nan_view(**_):
    return {"results": [{"symbol": "ZZZ", "pe": float("nan")}], "as_of": "2026-11-20",
            "sources": [C.src("Test", "nan test", "https://example.test/")]}


C.ROUTES["t_nan"] = nan_view
try:
    C.dispatch("t_nan", {})
    check(False, "strict mode raises on NaN")
except K.ContractViolation as e:
    check("non-finite number at results[0].pe" in str(e), f"strict mode names the path: {e}")

# ---- production: the server still scrubs, and logs the view once
buf = io.StringIO()
with redirect_stdout(buf):
    a = server.Handler._dumps_strict({"x": float("nan"), "y": 999999.99}, "t_nan")
    b = server.Handler._dumps_strict({"x": float("inf")}, "t_nan")
check(json.loads(a) == {"x": None, "y": 999999.99} and json.loads(b) == {"x": None}, "scrubbed to null (placeholder untouched)")
logged = [ln for ln in buf.getvalue().splitlines() if "non-finite in t_nan" in ln]
check(len(logged) == 1 and "x" in logged[0], f"logged once per view: {logged}")

# ---- FRED public CSV: 'nan'/'inf' strings never enter a series
rows = fred_public.parse_csv("DATE,DGS10\n2026-01-02,nan\n2026-01-05,inf\n2026-01-06,4.25\n2026-01-07,.\n2026-01-08,-Infinity\n")
check(rows == [("2026-01-02", None), ("2026-01-05", None), ("2026-01-06", 4.25), ("2026-01-07", None), ("2026-01-08", None)],
      f"FRED CSV non-finite strings -> None: {rows}")

# ---- returns and bp changes
check(C._ret([("2026-01-05", 10.0), ("2026-01-06", 11.0)], "1D") == 11.0 / 10.0 - 1, "1D +10% kept")
check(C._ret([("2026-01-05", 10.0), ("2026-01-06", 200.0)], "1D") is None, "1D +1,900% blanked (bad print)")
check(C._ret([("2026-01-05", 10.0), ("2026-01-06", 0.5)], "1D") is None, "1D -95% blanked")
check(C._diff_bp([("2026-01-05", 4.0), ("2026-01-06", 4.25)], "1D") == 25.0, "4.00% -> 4.25% = +25.0 bp")
check(C._diff_bp([("2026-01-05", 4.0), ("2026-01-06", 999999.99)], "1D") is None, "FINRA placeholder yield: no bp change")
check(C._diff_bp([("2026-01-05", 4.0), ("2026-01-06", 45.0)], "1D") is None, "a 45% yield is outside -5..40: no bp change")
check(C._num_or_none("4,25") == 4.25 and C._num_or_none("nan") is None and C._num_or_none(None) is None, "decimal comma text")

# ---- earnings: surprise and 2-day move
check(abs(C._eps_surprise(1.10, 1.00) - 0.1) < 1e-12, "EPS 1.10 vs 1.00 = +10%")
check(C._eps_surprise(1.0, 0.001) is None, "EPS 1.00 vs 0.001 = +99,900%: blank")
check(abs(C._eps_surprise(-0.5, 0.25) - (-3.0)) < 1e-12, "EPS -0.50 vs 0.25 = -300%: kept")
closes = [("2026-01-01", 100.0), ("2026-01-03", 150.0)]
check(abs(C._two_day(closes, "2026-01-02") - 0.5) < 1e-12, "100 -> 150 = +50%")
check(C._two_day([("2026-01-01", 100.0), ("2026-01-03", 450.0)], "2026-01-02") is None, "100 -> 450 = +350%: blank")
check(C._two_day([("2026-01-01", 100.0), ("2026-01-03", 15.0)], "2026-01-02") is None, "100 -> 15 = -85%: blank")

# ---- comps EV multiples: US one-currency and a London (GBp) listing
us = {"market_cap": 100e9, "enterprise_value": 110e9, "enterprise_to_ebitda": 350.0, "enterprise_to_revenue": 5.0,
      "price_to_book": 3.0, "currency": "USD"}
e = C._ev_parts(us, "USD", {"USD": 1.0}, "ZZZ")
check(e["ev_ebitda"] is None and e["ev_sales"] == 5.0 and e["pb"] == 3.0, "US: EV/EBITDA 350x blank, EV/sales 5x and P/B 3x kept")
check(abs(e["ev_usd"] - 110.0) < 1e-9 and abs(e["mcap_usd"] - 100.0) < 1e-9, "US: EV $110bn, market cap $100bn unchanged")
raw = C._ev_figures(us, "USD", {"USD": 1.0})
check(raw["ev_ebitda"] == 350.0, "the unbanded figure is what Yahoo sent (the band is the only change)")
gb = {"market_cap": 50e9, "enterprise_value": 60e9, "enterprise_to_ebitda": 8.0, "enterprise_to_revenue": 150.0,
      "price_to_book": 250.0, "currency": "GBp"}
g = C._ev_parts(gb, "GBp", {"GBp": 0.0127}, "SHEL.L")        # fx: USD per quoted unit (one penny = $0.0127)
check(g["ev_ebitda"] == 8.0 and g["ev_sales"] is None and g["pb"] is None, "GBp: EV/EBITDA 8x kept, EV/sales 150x and P/B 250x blank")
check(abs(g["mcap_usd"] - 63.5) < 1e-9, "GBp: market cap GBP 50bn x (0.0127 / 0.01 = 1.27 USD per GBP) = $63.5bn")
check(math.isclose(C.num("0.1") + C.num("0.2"), 0.3), "num values add as floats")

done("num")
