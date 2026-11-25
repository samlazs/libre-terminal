"""SEC XBRL company-facts comparison across peer groups."""

from __future__ import annotations

import datetime as dt

import composite as C

COMPARE_GROUPS = {
    "hyperscalers": ("AI hyperscalers", "MSFT,GOOGL,AMZN,META,ORCL"),
    "semis": ("Semiconductors", "NVDA,AVGO,AMD,MU,INTC,QCOM,TXN"),
    "megacaps": ("Mega-cap tech", "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA"),
    "datacenter": ("Data-center infrastructure", "EQIX,DLR,VRT,ANET,ETN,CEG,VST"),
    "utilities": ("Power producers and utilities", "NEE,SO,DUK,D,AEP,CEG,VST"),
    "alt_managers": ("Alternative asset managers", "BX,KKR,APO,ARES,OWL,CG"),
    "bdcs": ("Business development companies", "ARCC,OBDC,BXSL,FSK,MAIN,GBDC"),
    "banks": ("Large banks", "JPM,BAC,C,WFC,GS,MS"),
    "energy": ("Oil majors", "XOM,CVX,COP,OXY,EOG"),
}

GROUP_OPTIONS = [{"label": v[0], "value": k} for k, v in COMPARE_GROUPS.items()]

# key -> (label, [XBRL tags, first that has data wins], balance-sheet item?)
FACTS = {
    "capex": ("Capital expenditure", ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements"], False),
    "revenue": ("Revenue", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"], False),
    "operating_income": ("Operating income", ["OperatingIncomeLoss"], False),
    "net_income": ("Net income", ["NetIncomeLoss"], False),
    "operating_cash_flow": ("Operating cash flow", ["NetCashProvidedByUsedInOperatingActivities"], False),
    "rnd": ("Research & development", ["ResearchAndDevelopmentExpense"], False),
    "sbc": ("Stock-based compensation", ["ShareBasedCompensation"], False),
    "buybacks": ("Share buybacks", ["PaymentsForRepurchaseOfCommonStock"], False),
    "dividends": ("Dividends paid", ["PaymentsOfDividends"], False),
    "interest_expense": ("Interest expense", ["InterestAndDebtExpense", "InterestExpenseDebt", "InterestPaidNet", "InterestPaid"], False),
    "debt": ("Long-term debt", ["LongTermDebt", "LongTermDebtNoncurrent", "DebtLongtermAndShorttermCombinedAmount"], True),
    "cash": ("Cash and equivalents", ["CashAndCashEquivalentsAtCarryingValue"], True),
    "ppe": ("Property, plant & equipment (net)", ["PropertyPlantAndEquipmentNet"], True),
}

VALID_FORMS = ("10-Q", "10-K", "20-F", "40-F", "DEF 14A")
ANNUAL_FORMS = ("10-K", "20-F", "40-F", "DEF 14A")

FACT_OPTIONS = [{"label": v[0], "value": k} for k, v in FACTS.items()]


def _facts(symbols, fact):
    """{symbol: {"quarter": [[end, value]], "annual": [...], "instant": [...]}} for one line item, kept 12 hours on disk."""
    label, tags, instant = FACTS[fact]

    def run():
        # every tag is asked for every company and merged by period: companies change tags over time (AAPL revenue is
        # SalesRevenueNet to FY2018, then RevenueFromContractWithCustomerExcludingAssessedTax; AMZN capex moved to
        # PaymentsToAcquireProductiveAssets in 2017), so "the first tag with data" cut their history short. A period
        # reported under several tags keeps the value of the tag listed first.
        merged = {}
        for tag in tags:
            try:
                rows = C.api(C.V5, "equity/compare/company_facts", provider="sec", symbol=",".join(symbols), fact=tag, instantaneous="true" if instant else None)
            except C.CompositeError as e:
                C.partial_from(e, f"SEC {tag}")
                continue
            for r in rows or []:
                if r.get("value") is None or not r.get("period_ending") or r.get("symbol") not in symbols:
                    continue
                base_form = str(r.get("form") or "").split("/")[0]
                if base_form and base_form not in VALID_FORMS:
                    continue
                span = "instant" if instant else r.get("span")
                if span not in ("quarter", "annual", "instant"):
                    continue
                if not instant and span == "annual" and base_form and base_form not in ANNUAL_FORMS:
                    continue
                val = C.num(r.get("value"))
                if val is None:
                    continue
                d = merged.setdefault(r["symbol"], {"quarter": {}, "annual": {}, "instant": {}})[span]
                d.setdefault(str(r["period_ending"])[:10], (str(r.get("period_beginning") or "")[:10], float(val)))
        out = {}
        for sym, spans in merged.items():
            # fiscal Q4 is only reported inside the annual figure: annual minus the three reported quarters
            for end, (begin, total) in spans["annual"].items():
                inside = sorted((qe, qb, v) for qe, (qb, v) in spans["quarter"].items() if begin and qb >= begin and qe <= end)
                if len(inside) == 3 and end not in spans["quarter"]:
                    covered_to = max(q[0] for q in inside)
                    if (dt.date.fromisoformat(end) - dt.date.fromisoformat(covered_to)).days > 60:
                        spans["quarter"][end] = (covered_to, total - sum(q[2] for q in inside))
            out[sym] = {k: sorted([e, v[1]] for e, v in d.items()) for k, d in spans.items()}
        if not out:
            raise C.CompositeError(f"SEC filings have no '{label}' figures for: {', '.join(symbols)}")
        return out
    return C.cached_disk(C.disk_name("facts4_" + fact + "_", "_".join(sorted(symbols))), 12 * 3600, run)


# HANDOFF P3-5: peer ratios. Derived figures combine two reported ones matched on the period end; a ratio is None when
# its denominator is missing or <= 0, never a number built from a guess.
DERIVED = {
    "fcf": ("Free cash flow (operating cash flow - capex)", "operating_cash_flow", "capex", "minus"),
    "capex_revenue": ("Capex / revenue", "capex", "revenue", "ratio"),
    "sbc_ocf": ("Stock-based compensation / operating cash flow", "sbc", "operating_cash_flow", "ratio"),
}

MEASURES = {"level": "Level", "pct_revenue": "% of revenue", "yoy": "Year over year %"}

FACT_OPTIONS.extend({"label": v[0], "value": k} for k, v in DERIVED.items())

MEASURE_OPTIONS = [{"label": v, "value": k} for k, v in MEASURES.items()]


def _period_pts(d, period):
    """[[end, value]] of one company for a period; TTM = four reported quarters spanning under 300 days, summed."""
    pts = (d or {}).get("quarter" if period == "ttm" else period, [])
    if period != "ttm":
        return list(pts)
    roll = []
    for i in range(3, len(pts)):
        window = pts[i - 3:i + 1]
        if (dt.date.fromisoformat(window[-1][0]) - dt.date.fromisoformat(window[0][0])).days < 300:
            roll.append([window[-1][0], sum(q[1] for q in window)])
    return roll


def ratio_pct(a, b):
    """a / b in percent; None when either is missing or the denominator is <= 0."""
    return None if a is None or b is None or b <= 0 else a / b * 100


def combine(pa, pb, how):
    """Two series matched on the period end: a - b ('minus') or a / b in percent ('ratio'); None where b is missing."""
    bm = dict((e, v) for e, v in pb)
    out = []
    for e, v in pa:
        if how == "minus":
            out.append([e, None if v is None or bm.get(e) is None else v - bm[e]])
        else:
            out.append([e, C.ratio_pct(v, bm.get(e))])
    return out


def yoy_pct(pts):
    """Change against the same period a year earlier (365 +/- 20 days), percent; None when that value is missing or <= 0."""
    out = []
    for e, v in pts:
        d = dt.date.fromisoformat(e)
        prev = next((pv for pe, pv in pts if abs((d - dt.date.fromisoformat(pe)).days - 365) <= 20), None)
        r = C.ratio_pct(v, prev)
        out.append([e, None if r is None else r - 100])
    return out


def compare(group="hyperscalers", symbols=None, fact="capex", period="quarter", since="2019", measure="level", **_):
    """One reported line item (capex, revenue, debt...) or a ratio of two across companies over time, from SEC filings."""
    syms = [s.upper() for s in C._split(symbols)][:8] or C._split(COMPARE_GROUPS.get(group, COMPARE_GROUPS["hyperscalers"])[1])
    measure = measure if measure in MEASURES else "level"
    if fact in DERIVED:
        label, fa, fb, how = DERIVED[fact]
        needed, instant = [fa, fb], False
        if how == "ratio" and measure != "level":
            raise C.CompositeError(f"'{label}' is already a ratio: shown as a level only.")
    elif fact in FACTS:
        label, _, instant = FACTS[fact]
        needed = [fact]
    else:
        raise C.CompositeError(f"Unknown item '{fact}'.")
    if instant and measure != "level":
        raise C.CompositeError(f"'{label}' is a balance-sheet amount: shown as a level only.")
    if measure == "pct_revenue":
        if fact == "revenue":
            raise C.CompositeError("Revenue as a % of revenue is 100%: pick another figure.")
        needed.append("revenue")
    datas = {f: C._facts(syms, f) for f in dict.fromkeys(needed)}
    since = str(since or "2019")[:4]
    period = "instant" if instant else (period if period in ("quarter", "annual", "ttm") else "quarter")
    pct = measure != "level" or (fact in DERIVED and DERIVED[fact][3] == "ratio")
    traces, table, missing, holes = [], {}, [], []
    for n, s in enumerate(syms):
        def series(f):
            return C._period_pts(datas[f].get(s), period)
        pts = C.combine(series(fa), series(fb), how) if fact in DERIVED else series(fact)
        if measure == "pct_revenue":
            pts = C.combine(pts, series("revenue"), "ratio")
        elif measure == "yoy":
            pts = C.yoy_pct(pts)
        pts = [[e, v if (v is None or pct) else v / 1e9] for e, v in pts if e >= f"{since}-01-01"]
        shown = [v for _, v in pts if v is not None]
        if not shown:
            missing.append(s)
            continue
        for e, v in pts:
            table.setdefault(e, {"period_ending": e})[s] = None if v is None else round(v, 2 if pct else 3)
        if pts[-1][1] is None:
            name = f"{s}  n/m (last {shown[-1]:,.1f}%)" if pct else f"{s}  n/m (last ${shown[-1]:,.1f}bn)"
        else:
            name = f"{s}  {shown[-1]:,.1f}%" if pct else f"{s}  ${shown[-1]:,.1f}bn"
        if period in ("quarter", "ttm"):         # a missing quarter is a break in the line, never a slope across it
            gapped = pts[:1]
            for q in pts[1:]:
                if (dt.date.fromisoformat(q[0]) - dt.date.fromisoformat(gapped[-1][0])).days > 120:
                    gapped.append([(dt.date.fromisoformat(gapped[-1][0]) + dt.timedelta(days=1)).isoformat(), None])
                gapped.append(q)
            if len(gapped) > len(pts):
                holes.append(s)
            pts = gapped
        traces.append({"type": "scatter", "mode": "lines+markers", "name": name, "x": [q[0] for q in pts], "y": [None if q[1] is None else round(q[1], 3) for q in pts],
                       "line": {"width": 2, "color": C.PALETTE[n % len(C.PALETTE)]}, "marker": {"size": 5, "color": C.PALETTE[n % len(C.PALETTE)]}, "connectgaps": False,
                       "hovertemplate": ("%{y:,.1f}%" if pct else "$%{y:,.2f}bn") + "<extra>" + s + "</extra>"})
    if not traces:
        raise C.CompositeError(f"No '{label}' figures since {since} for: {', '.join(syms)}")
    lay = C._layout(len(traces) > 1, hovermode="closest")
    lay["yaxis"].update({"ticksuffix": "%"} if pct else {"tickprefix": "$", "ticksuffix": "bn"})
    what = {"pct_revenue": f"{label} as % of revenue", "yoy": f"{label}, change on a year earlier (%)"}.get(measure)
    notes = ([what] if what else []) + (["No figures under the tags this source covers: " + ", ".join(missing)] if missing else []) + \
        (["Missing quarters in the SEC data (line not drawn across them): " + ", ".join(holes)] if holes else []) + \
        (["Blank where the denominator is missing or not above zero"] if pct else [])
    if notes:
        lay["annotations"] = [{"text": t, "xref": "paper", "yref": "paper", "x": 1, "y": 0.06 * i, "showarrow": False, "font": {"color": C.INK["muted"], "size": 10}}
                              for i, t in enumerate(notes)]
    return {"data": traces, "layout": lay, "results": [table[e] for e in sorted(table, reverse=True)]}
