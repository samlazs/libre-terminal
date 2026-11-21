"""HANDOFF P3-5 peer ratios in x_compare: % of revenue, YoY %, FCF = OCF - capex, capex/revenue, SBC/OCF; a ratio is
None when its denominator is missing or <= 0. Expected values worked out by hand from the fixture below."""

from _common import check, done

import composite as c

B = 1e9
FIX = {   # fact -> {symbol: {"quarter": [[end, value]], ...}}
    "capex": {"AAA": {"quarter": [["2024-03-31", 2 * B], ["2024-06-30", 3 * B], ["2025-03-31", 4 * B]]},
              "BBB": {"quarter": [["2024-03-31", 1 * B], ["2024-06-30", 1 * B]]}},
    "revenue": {"AAA": {"quarter": [["2024-03-31", 10 * B], ["2024-06-30", 12 * B], ["2025-03-31", 16 * B]]},
                "BBB": {"quarter": [["2024-03-31", -5 * B]]}},
    "operating_cash_flow": {"AAA": {"quarter": [["2024-03-31", 5 * B], ["2024-06-30", 2 * B], ["2025-03-31", 0.0]]}},
    "sbc": {"AAA": {"quarter": [["2024-03-31", 1 * B], ["2024-06-30", 1 * B], ["2025-03-31", 1 * B]]}},
    "debt": {"AAA": {"instant": [["2024-03-31", 50 * B]]}},
}


def fake_facts(symbols, fact):
    return FIX[fact]


def tab(out, sym):
    return {r["period_ending"]: r.get(sym) for r in out["results"] if sym in r}


saved = c._facts
try:
    c._facts = fake_facts
    q = dict(symbols="AAA,BBB", period="quarter", since="2019")

    check(c.ratio_pct(1, 0) is None and c.ratio_pct(1, -2) is None and c.ratio_pct(None, 4) is None and c.ratio_pct(1, 4) == 25.0, "ratio: None when denominator <= 0 or missing")

    cr = c.compare(fact="capex_revenue", **q)
    check(tab(cr, "AAA") == {"2024-03-31": 20.0, "2024-06-30": 25.0, "2025-03-31": 25.0}, f"capex/revenue AAA 2/10, 3/12, 4/16: {tab(cr, 'AAA')}")
    check(tab(cr, "BBB") == {} and "BBB" in cr["layout"]["annotations"][0]["text"], "BBB: revenue negative or missing -> no ratio, named as missing")
    check(cr["data"][0]["name"] == "AAA  25.0%" and cr["layout"]["yaxis"]["ticksuffix"] == "%", "ratio in percent on the axis and legend")

    pr = c.compare(fact="capex", measure="pct_revenue", **q)
    check(tab(pr, "AAA") == tab(cr, "AAA"), "capex as % of revenue = capex / revenue")

    f = c.compare(fact="fcf", **q)
    check(tab(f, "AAA") == {"2024-03-31": 3.0, "2024-06-30": -1.0, "2025-03-31": -4.0}, f"FCF = OCF - capex: 5-2, 2-3, 0-4 ($bn): {tab(f, 'AAA')}")
    check(f["data"][0]["name"] == "AAA  $-4.0bn" and f["layout"]["yaxis"]["tickprefix"] == "$", "FCF in $bn")

    so = c.compare(fact="sbc_ocf", **q)
    check(tab(so, "AAA") == {"2024-03-31": 20.0, "2024-06-30": 50.0, "2025-03-31": None}, f"SBC/OCF 1/5, 1/2, OCF 0 -> blank: {tab(so, 'AAA')}")

    y = c.compare(fact="capex", measure="yoy", **q)
    check(tab(y, "AAA") == {"2024-03-31": None, "2024-06-30": None, "2025-03-31": 100.0}, f"capex YoY: 4 vs 2 a year earlier = +100%: {tab(y, 'AAA')}")
    fy = c.compare(fact="fcf", measure="yoy", **q)
    check(tab(fy, "AAA")["2025-03-31"] == round((-4 / 3 - 1) * 100, 2), "FCF YoY: -4 vs 3 = -233.33%")
    check(c.yoy_pct([["2024-03-31", 5.0], ["2025-03-31", 6.0]])[1][1] is not None and c.yoy_pct([["2024-03-31", -5.0], ["2025-03-31", 6.0]])[1][1] is None,
          "YoY blank when the year-earlier value is <= 0")

    for kw, why in ((dict(fact="revenue", measure="pct_revenue"), "100%"), (dict(fact="debt", measure="yoy"), "balance-sheet"),
                    (dict(fact="sbc_ocf", measure="yoy"), "already a ratio")):
        try:
            c.compare(**kw, symbols="AAA")
            check(False, f"{kw} raises")
        except c.CompositeError as e:
            check(why in str(e), f"{kw}: {e}")
    lv = c.compare(fact="capex", **q)
    check(tab(lv, "AAA")["2025-03-31"] == 4.0 and lv["data"][0]["name"] == "AAA  $4.0bn", "level unchanged ($bn)")
    src = c.static_sources("compare", {"fact": "sbc_ocf"})[0]["detail"]
    check("stock-based compensation" in src and "operating cash flow" in src, "sources name both figures of a ratio")
    check("revenue" in c.static_sources("compare", {"fact": "capex", "measure": "pct_revenue"})[0]["detail"], "sources add revenue for % of revenue")
    check({"fcf", "capex_revenue", "sbc_ocf"} <= {o["value"] for o in c.FACT_OPTIONS}, "derived figures offered in the picker")
finally:
    c._facts = saved

done("test_peer_ratios")
