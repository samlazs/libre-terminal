"""Source contract and source switching (package 2): every view names what it used; concept siblings are well formed."""

import datetime as dt

from _common import check, done

import build_dashboards as bd
import composite as c

REAL = {"FRED", "U.S. EIA", "Yahoo Finance", "IMF PortWatch", "Caldara & Iacoviello", "BIS", "OECD", "IMF", "SEC", "Terminal"} | set(c.CURVE_SHORT.values())

# ---- every curated chart names real sources, with links for FRED / EIA series
for key in c.MACRO_SETS:
    got = c.static_sources("macro_chart", {"preset": key})
    check(got and all(s["name"] in REAL for s in got), f"chart {key} names its sources")
check(all(s["url"].startswith("https://fred.stlouisfed.org/series/") for s in c.static_sources("macro_chart", {"preset": "construction"})), "FRED series link to their pages")
check(c.series_source("EIA:PET.WCESTUS1.W")["url"].endswith("s=WCESTUS1&f=W"), "EIA petroleum series link")
check(c.series_source("CALC:BRENT_WTI")["detail"].startswith("calculated from"), "derived series say how they are calculated")
check(c.series_source("USEPUINDXD@ma30")["detail"].endswith("30-day average"), "rolling averages are named")
check(len(c.static_sources("macro_chart", {"preset": "crude_stocks_seasonal"})) == 1, "a seasonal chart names only the series it shows")

# ---- every view answers with sources; views that list many sources show only the one(s) used
for view in c.ROUTES:
    if view == "search":
        continue
    check(c.static_sources(view, {}), f"view {view} has a static source")
check([s["name"] for s in c.static_sources("yield_curves", {"country": "germany"})] == ["Bundesbank"], "one country, one curve source")
check([s["name"] for s in c.static_sources("yield_curve_table", {"country": "united_states", "source": "fred"})] == ["FRED"], "U.S. curve from FRED when picked")
saved = c.ROUTES["style_box"]
try:
    c.ROUTES["style_box"] = lambda **_: {"results": []}
    check(c.dispatch("style_box", {})["sources"][0]["name"] == "Yahoo Finance", "dispatch fills in sources a view did not send")
finally:
    c.ROUTES["style_box"] = saved

# ---- range chips and frequency
check(c.range_start("MAX", "2015") == "1950-01-01" and c.range_start(None, "2010") == "2010-01-01", "range MAX and plain since")
check(c.range_start("5Y", "2015")[:4] == str(dt.date.today().year - 5), "range 5Y")
weekly = [((dt.date(2020, 1, 3) + dt.timedelta(weeks=i)).isoformat(), 1.0) for i in range(60)]
check(c.freq_of(weekly) == "weekly" and c.FREQ_RANGES["weekly"][0] == "1Y", "weekly series get 1Y..Max ranges")

# ---- OECD rate series fall back to FRED's copies (same ids for every country the card offers)
check(all(k in c.COUNTRIES for k in c.FRED_MEI), "FRED mirror countries are known countries")
saved = c._oecd_series, c._fred
try:
    def refuse(duration):
        raise c.CompositeError("OECD rate limit reached (HTTP 429)")
    c._oecd_series = refuse
    c._fred = lambda ids, start: ({ids[0]: [["2026-01-01", 2.5], ["2026-02-01", 2.6]]}, {})
    out = c.rate_history(country="germany", duration="long", start="2025")
    check(out["sources"][0]["name"] == "FRED" and "IRLTLT01DEM156N" in out["sources"][0]["detail"], "OECD refuses: FRED fills in and says so")
finally:
    c._oecd_series, c._fred = saved

# ---- concept siblings: members well formed, duplicates only skipped when a picker reaches them
keys = {"mk", "sym", "rn", "dp", "p", "paid", "alt", "wid"}
for cid, spec in bd.CONCEPTS.items():
    check(spec["label"] and spec["members"], f"concept {cid} has a label and members")
    for ep, prov, label, opt in spec["members"]:
        check(set(opt) <= keys and label, f"{cid}: member {ep} options")
        check(all(m in ("us", "ca", "crypto", "nordic", "other") for m in opt.get("mk", [])), f"{cid}: markets of {ep}")
members = {m[0] for s in bd.CONCEPTS.values() for m in s["members"]}
check(bd.SKIP <= members, "every skipped duplicate is reachable through a source picker")

# ---- check_sources.py: v4 overrides, user dashboards, and source validation
import check_sources as cs  # noqa: E402

sample_state = {
    "v": 4,
    "dashboards": {
        "d:credit": {"mode": "overrides", "overrides": {"t1": {"added": [{"backend": "v5", "widgetId": "w_ok"}], "cards": {"c1": {"h": 10}}}}},
        "d:legacy": {"tabs": [{"name": "Ed", "edited": True, "items": [{"backend": "v5", "widgetId": "w_ed"}]},
                              {"name": "Raw", "edited": False, "items": [{"backend": "v5", "widgetId": "w_skip"}]}]},
        "user:mine": {"tabs": [{"name": "Mine", "items": [{"backend": "v5", "widgetId": "w_user"}]}]},
    },
}
saved_cards = cs.extract_saved_cards(sample_state)
saved_wids = [it["widgetId"] for _, it in saved_cards]
check(saved_wids == ["w_ok", "w_ed", "w_user"], f"extract_saved_cards handles v4 overrides, legacy edited, and user dashboards: {saved_wids}")

mini_defs = {
    "x": {},
    "v4": {},
    "v5": {
        "w_ok": {"endpoint": "/api/v1/ok", "params": [{"paramName": "provider", "value": "fred"}]},
        "w_ed": {"endpoint": "/api/v1/ed", "params": [{"paramName": "provider", "value": "sec"}]},
        "w_user": {"endpoint": "", "source": ["Custom"]},
    },
}
mini_built = {"concepts": {}, "dashboards": [{"name": "D", "tabs": [{"name": "T", "items": [{"backend": "v5", "widgetId": "w_ok"}]}]}]}
res_ok = cs.check_all(mini_defs, mini_built, sample_state)
check(res_ok["seen"] == 4 and res_ok["bad"] == ["(saved) user:mine > Mine: w_user -> ''"] and not res_ok["code_hits"],
      f"check_all validates built-in + saved cards and catches BAD placeholder sources: {res_ok['bad']}")

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
with tempfile.TemporaryDirectory() as td_cs:
    r_cs = Path(td_cs)
    (r_cs / "scripts" / "dashboards").mkdir(parents=True)
    (r_cs / "scripts" / "dashboards" / "fake.py").write_text('MSG = "Source shown"\n', encoding="utf-8")
    res_hit = cs.check_all({"x": {}, "v4": {}, "v5": {}}, {"concepts": {}, "dashboards": []}, {}, root=r_cs)
    check(len(res_hit["code_hits"]) == 1 and res_hit["code_hits"][0].name == "fake.py",
          "check_sources scans scripts/dashboards/*.py for 'Source shown'")

done("test_sources")
