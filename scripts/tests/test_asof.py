"""P3-03 coverage: every /x route either dates its answer by the newest data point it used, or is declared undated
(contract.UNDATED) with a reason. A dated route needs at least two check_as_of(...) cases on different inputs (US and
non-US for a symbol route) somewhere in scripts/tests; an undated one answers its declared reason, never the generic one."""

import re

from _common import ROOT, check, done

import composite as C
from composite.core import contract as K

DATED = sorted(set(C.ROUTES) - set(K.UNDATED))
check(set(K.UNDATED) <= set(C.ROUTES), f"every declared undated view is a route: {sorted(set(K.UNDATED) - set(C.ROUTES))}")
check(all(isinstance(v, str) and v.strip() and v != K.NO_DATE for v in K.UNDATED.values()), "every undated view has its own reason")

calls = {}
for f in sorted((ROOT / "scripts" / "tests").glob("test_*.py")):
    for name in re.findall(r"check_as_of\(\s*[\"']([a-z0-9_]+)[\"']", f.read_text(encoding="utf-8")):
        calls[name] = calls.get(name, 0) + 1
thin = {r: calls.get(r, 0) for r in DATED if calls.get(r, 0) < 2}
check(not thin, f"dated routes without two as_of fixture cases: {thin}")
check(not set(calls) - set(C.ROUTES), f"as_of cases for unknown routes: {sorted(set(calls) - set(C.ROUTES))}")
check(not set(calls) & set(K.UNDATED), f"declared undated routes cannot also be dated: {sorted(set(calls) & set(K.UNDATED))}")

# ---- undated views: the declared reason, never the generic text, and never a violation
for name, reason in K.UNDATED.items():
    out = K.finish({"results": [{"name": "x"}], "sources": [{"name": "X", "detail": "d", "url": ""}]}, name)
    check(out.get("as_of_na") == reason and K.violations(out) == [], f"{name}: answers its declared reason")
# a dated view that finds no date in its answer is a violation (strict mode raises it)
gen = K.finish({"results": [{"name": "x"}], "sources": [{"name": "X", "detail": "d", "url": ""}]}, "board")
check(gen.get("as_of_na") == K.NO_DATE and K.NO_DATE_VIOLATION in K.violations(gen), "an undated answer of a dated view is a violation")
# a view's own as_of_na reason is kept (e.g. no rows right now), and its own as_of beats anything derived
own = K.finish({"results": [], "sources": [{"name": "X", "detail": "d", "url": ""}], "as_of_na": "No rows right now."}, "board")
check(own["as_of_na"] == "No rows right now." and K.violations(own) == [], "a view's own reason is kept")
mine = K.finish({"results": [{"date": "2026-11-24"}], "sources": [{"name": "X", "detail": "d", "url": ""}], "as_of": "2026-11-20"}, "board")
check(mine["as_of"] == "2026-11-20", "a view's own as_of wins over a derived one")
check(K.newest(["2026-11-20", "2026-11-21T09:00:00+09:00", None, "n/a", "2026-11-19"]) == "2026-11-21T09:00:00+09:00", "newest: datetime of the newest day")

# ---- a date after tomorrow is a maturity, a meeting or a release date, never an observation (bond_curve review)
import datetime as dt                                                          # noqa: E402
far = (dt.date.today() + dt.timedelta(days=400)).isoformat()
src1 = [{"name": "X", "detail": "d", "url": ""}]
check(K.derive_as_of({"data": [{"x": ["2030-01-15", far], "y": [4.1, 4.5]}], "results": [{"date": "2026-11-18"}]}) == "2026-11-18",
      "derive_as_of skips future chart x values (maturities)")
check(K.derive_as_of({"data": [{"x": [far], "y": [1]}]}) is None, "a chart dated only in the future has no derived date")
check(any("in the future" in v for v in K.violations({"sources": src1, "as_of": far})), "a future as_of is a violation")
tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
check(K.violations({"sources": src1, "as_of": tomorrow}) == [], "tomorrow is allowed (a market a day ahead)")

done("test_asof")
