"""Dashboard generator: coverage accounting, stable ids, renamed tabs, and source routing by ticker.

The full build is replayed offline from the backend catalogs the last live build recorded
(local/cache/catalog-v5.json, catalog-v4.json). Before the first start they do not exist and that part is skipped.
"""

import contextlib
import io
import json
import re

from _common import REAL_LOCAL, check, done

import build_dashboards as bd
import xwidgets


# ---- routing by ticker (mirror of widgets.js T.marketOf + app.js routeItem's automatic pick)
def market_of(sym):
    s = str(sym or "").upper().split(",")[0].strip()
    if not s:
        return ""
    if re.match(r"^(BTC|ETH|SOL|XRP|PAXG|MATIC|BNB|ADA|DOGE)(-?USDT?)?$", s):
        return "crypto"
    if re.search(r"\.(TO|V|NE|CN)$", s):
        return "ca"
    if re.search(r"\.(ST|HE|CO|IC)$", s) or re.match(r"^[A-Z0-9]+ [A-Z]$", s):
        return "nordic"
    if re.match(r"^\^?[A-Z][A-Z0-9\-]{0,6}(\.[A-C])?$", s):
        return "us"
    return "other"


def route(cid, sym):
    """Label of the member automatic routing lands on (never a paid plan), or None (the card says nothing covers it)."""
    mkt = market_of(sym)
    for ep, prov, label, opt in bd.CONCEPTS[cid]["members"]:
        if (not opt.get("mk") or mkt in opt["mk"]) and not opt.get("paid"):
            return label
    return None


check(market_of("AAPL") == "us" and market_of("BRK.B") == "us" and market_of("^GSPC") == "us", "U.S. tickers")
check(market_of("SU.TO") == "ca" and market_of("BTC") == "crypto" and market_of("BTC-USD") == "crypto", "Canadian and crypto tickers")
check(market_of("VOLV-B.ST") == "nordic" and market_of("SAP.DE") == "other", "Nordic and other listings")
for k in bd.OPT_LABELS:
    cid = "options." + k
    check(route(cid, "AAPL").startswith("Cboe"), f"{cid}: AAPL -> Cboe")
    check(route(cid, "SU.TO").startswith("TMX"), f"{cid}: SU.TO -> TMX")
    check(route(cid, "BTC").startswith("Deribit"), f"{cid}: BTC -> Deribit")
    check(route(cid, "SAP.DE") is None, f"{cid}: SAP.DE -> no listed-options source (the card says so)")
check(route("fund.holdings", "SPY").startswith("Nasdaq") and route("fund.holdings", "XIU.TO") == "TMX", "ETF holdings: SPY -> Nasdaq, XIU.TO -> TMX")
check(route("fund.holdings", "VWRL.L").startswith("Yahoo"), "ETF holdings elsewhere -> Yahoo top holdings")
check(route("etf.sectors", "SPY").startswith("Yahoo") and route("etf.sectors", "XIU.TO") == "TMX" and route("etf.sectors", "VWRL.L").startswith("Yahoo"),
      "ETF sectors: Yahoo (keyless) for U.S. and other listings, TMX first for .TO (Stage 3 13a)")
check(all(route(c_, s_) is None or not route(c_, s_).startswith("FMP") for c_ in ("etf.sectors",) for s_ in ("SPY", "VWRL.L", "XIU.TO")),
      "automatic routing never lands on a paid plan")
check(route("shorts.interest", "AAPL") is not None, "short interest for a U.S. ticker")
check(route("company.filings", "AAPL") == "SEC (EDGAR)", "company.filings: AAPL -> SEC (EDGAR)")
check(route("company.filings", "ASML") == "SEC (EDGAR)", "company.filings: US ADR ASML -> SEC (EDGAR)")
check(route("company.filings", "RY.TO") == "TMX (SEDAR+ Canada)", "company.filings: RY.TO -> TMX (SEDAR+ Canada)")
check(route("company.filings", "SAP.DE") is None and route("company.filings", "VOLV-B.ST") is None, "company.filings: local Euro/Nordic -> honest no-archive message")
check(route("company.filings", "BTC") is None, "company.filings: BTC -> None")
check(bd.CONCEPTS["company.filings"].get("title") == "Company filings", "company.filings keeps consistent title across sources")
check(route("company.filing_viewer", "AAPL") == "SEC (EDGAR)" and route("company.filing_viewer", "RY.TO") == "TMX (SEDAR+)", "company.filing_viewer: AAPL -> SEC, RY.TO -> TMX (SEDAR+)")
check(route("company.filing_viewer", "SAP.DE") is None, "company.filing_viewer: SAP.DE -> honest no-viewer message")
check(route("company.overview", "AAPL") == "SEC (EDGAR)" and route("company.overview", "RY.TO") == "TMX (Canada)", "company.overview: US/CA")
check(route("company.overview", "VOLV-B.ST") == "Nasdaq Nordic" and route("company.overview", "SAP.DE") == "Yahoo Finance", "company.overview: Nordic/Euro")
for _scid in ("company.income", "company.balance", "company.cash"):
    check(route(_scid, "AAPL") == "SEC (EDGAR XBRL)" and route(_scid, "RY.TO") == "TMX (Canada)", f"{_scid}: US/CA")
    check(route(_scid, "SAP.DE") == "Yahoo Finance" and route(_scid, "VOLV-B.ST") == "Yahoo Finance", f"{_scid}: Euro/Nordic")
check(route("own.insiders", "AAPL") == "Yahoo Finance" and route("own.insiders", "RY.TO") == "TMX (Canada)", "own.insiders: US/CA")
check("equity/fundamental/filings" in bd.SKIP, "equity/fundamental/filings reached via company.filings source picker")
check(bd.REQUIRED_PARAMS.get("sec/filing_viewer_app") == ["document_url"], "sec/filing_viewer_app requires document_url")

# ---- full build replay: coverage accounting, unique and stable ids, renamed tabs, build without V4
def replay(defs):
    bd.load = lambda: defs
    with contextlib.redirect_stdout(io.StringIO()):
        return bd.build()


cat = {b: REAL_LOCAL / "cache" / f"catalog-{b}.json" for b in ("v5", "v4")}
if not all(p.exists() for p in cat.values()):
    print("test_generator: build replay skipped (no recorded catalogs yet: start the terminal once)")
else:
    defs = {"x": xwidgets.widgets()}
    for b, p in cat.items():
        defs[b] = json.loads(p.read_text(encoding="utf-8"))
    out = replay(defs)
    rep = dict(bd.LAST_REPORT)
    check(rep["functions"] > 900, f"catalog lists the expected number of functions ({rep['functions']})")
    check(not rep["missing"], "every function is placed: " + ", ".join(rep["missing"][:5]))
    check(not rep["fallback"], "nothing is placed by fallback: " + ", ".join(rep["fallback"][:5]))
    check(not [w for w in rep["warnings"] if "unknown endpoint" in w or "no widget for" in w], "no card names an unknown function")
    dashes = out["dashboards"]
    check(len(dashes) == len(bd.DASHBOARDS), "one dashboard per definition")
    # review C3: commodity futures curves offer Yahoo only; the VIX curve (Cboe) keeps its menu
    _curves = [it for d in dashes for t in d["tabs"] for it in t["items"] if it["widgetId"].startswith("derivatives_futures_curve_")]
    check(_curves and all(it.get("srcOnly") == ["yfinance"] for it in _curves if "_yfinance_" in it["widgetId"])
          and all("srcOnly" not in it for it in _curves if "_cboe_" in it["widgetId"]), "commodity curves: Source menu Yahoo only; VIX curve unchanged")
    for d in dashes:
        tids = [t["id"] for t in d["tabs"]]
        check(len(tids) == len(set(tids)), f"{d['name']}: tab ids unique")
        uids = [it["uid"] for t in d["tabs"] for it in t["items"]]
        check(len(uids) == len(set(uids)), f"{d['name']}: card ids unique")
        for t in d["tabs"]:
            seen = [(it["backend"], it["widgetId"], json.dumps(it.get("params") or {}, sort_keys=True), it.get("view")) for it in t["items"]]
            check(len(seen) == len(set(seen)), f"{d['name']} > {t['name']}: no card twice (a chart next to its table is fine)")
            for it in t["items"]:
                check(it["widgetId"] in defs[it["backend"]], f"{d['name']} > {t['name']}: {it['widgetId']} exists")
        for old, new in bd.RENAMED_TABS.get(d["id"], {}).items():
            check(new in tids, f"{d['id']}: renamed tab {old} -> {new} exists")
            check(old not in tids, f"{d['id']}: old tab id {old} no longer built")
    for w in out["columns"]:
        check(any(w in defs[b] for b in ("v5", "v4")), f"column preset {w} names a backend widget")
    # stable ids: the same catalog gives the same ids
    again = replay(defs)
    check([(d["id"], [t["id"] for t in d["tabs"]], [i["uid"] for t in d["tabs"] for i in t["items"]]) for d in again["dashboards"]] ==
          [(d["id"], [t["id"] for t in d["tabs"]], [i["uid"] for t in d["tabs"] for i in t["items"]]) for d in dashes], "ids stable across builds")
    # the legacy backend is optional (now V5-only default): a build without it works, places nothing on it, and leaves landing tabs complete
    no_v4 = replay(dict(defs, v4={}))
    rep_nov4 = dict(bd.LAST_REPORT)
    check(no_v4["dashboards"] and not any(i["backend"] == "v4" for d in no_v4["dashboards"] for t in d["tabs"] for i in t["items"]), "build without the V4 backend")
    check(not rep_nov4["missing"] and not rep_nov4["fallback"], "V5-only build covers all V5+X functions with zero fallback")
    check(all(d["tabs"] and d["tabs"][0]["items"] for d in no_v4["dashboards"]), "every dashboard has a non-empty landing tab in V5-only build")
    scr_nov4 = next(d["tabs"][0] for d in no_v4["dashboards"] if d["id"] == "d:eq-screen")
    check({i["widgetId"] for i in scr_nov4["items"]} == {"yfinance_screener_builder_obb", "equity_screener_yfinance_obb"},
          "Screeners & Movers landing tab falls back to V5 Yahoo screener + builder")
    etf_nov4 = next(d["tabs"][0] for d in no_v4["dashboards"] if d["id"] == "d:etfs")
    check(len(etf_nov4["items"]) >= 7 and all(not i.get("needsKey") for i in etf_nov4["items"]),
          "ETFs & Mutual Funds landing tab stays complete and keyless in V5-only build")
    val_nov4 = next(t for d in no_v4["dashboards"] if d["id"] == "d:indices" for t in d["tabs"] if t["id"] == "valuation")
    val_both = next(t for d in dashes if d["id"] == "d:indices" for t in d["tabs"] if t["id"] == "valuation")
    check(any(i["backend"] == "x" and i["widgetId"] == "x_sp500_multiples" for i in val_nov4["items"]),
          "Indices > Valuation places own x_sp500_multiples in V5-only build")
    check(any(i["backend"] == "x" and i["widgetId"] == "x_sp500_multiples" for i in val_both["items"])
          and not any(i["widgetId"] == "index_sp500_multiples_multpl_obb" for i in val_both["items"])
          and "index.sp500_multiples" in out["concepts"],
          "Indices > Valuation keeps x_sp500_multiples primary in V5+V4 build with legacy multpl in source picker")
    # keyless computer (V5-only default): cards that need a key sit behind the keyless ones across landing tabs and rows
    real_creds = bd.credentials
    bd.credentials = lambda: set()
    try:
        keyless = replay(defs)
        keyless_nov4 = replay(dict(defs, v4={}))
    finally:
        bd.credentials = real_creds
    check(bd.macro_key_provider("crude_stocks_seasonal") == "eia" and bd.macro_key_provider("crack_spreads") is None, "EIA-only curated charts need the EIA key")
    for label, build_out in (("V5+V4", keyless), ("V5-only", keyless_nov4)):
        en = next(t for d in build_out["dashboards"] if d["id"] == "d:energy" for t in d["tabs"] if t["id"] == "overview")
        en_free = [i for i in en["items"] if not i.get("needsKey")]
        en_keyed = [i for i in en["items"] if i.get("needsKey")]
        check(en_free and en_keyed and max(i["y"] + i["h"] for i in en_free) <= min(i["y"] for i in en_keyed),
              f"Energy > Overview ({label}): all keyless cards sit strictly above EIA key-gated cards")
        for d in build_out["dashboards"]:
            t0 = d["tabs"][0]
            t0_free = [i for i in t0["items"] if not i.get("needsKey")]
            t0_keyed = [i for i in t0["items"] if i.get("needsKey")]
            if t0_free and t0_keyed:
                check(max(i["y"] for i in t0_free) <= min(i["y"] for i in t0_keyed),
                      f"{d['name']} > {t0['name']} ({label}): no key-gated card starts above a keyless card")
        for did, tid in (("d:energy", "oil"), ("d:energy", "outlooks-reports"), ("d:macro-us", "inflation")):
            tab = next(t for d in build_out["dashboards"] if d["id"] == did for t in d["tabs"] if t["id"] == tid)
            t_free = [i for i in tab["items"] if not i.get("needsKey")]
            t_keyed = [i for i in tab["items"] if i.get("needsKey")]
            if t_free and t_keyed:
                check(max(i["y"] for i in t_free) <= min(i["y"] for i in t_keyed),
                      f"{did} > {tid} ({label}): keyless rows/auto items sit above all-keyed rows")
    en_ids = sorted(i["uid"] for d in keyless["dashboards"] if d["id"] == "d:energy" for t in d["tabs"] if t["id"] == "overview" for i in t["items"])
    check(en_ids == sorted(i["uid"] for d in dashes if d["id"] == "d:energy" for t in d["tabs"] if t["id"] == "overview" for i in t["items"]), "same card ids with or without keys")
    check(any(i.get("needsKey") == "eia_api_key" and i["widgetId"] == "x_macro" for d in keyless["dashboards"] if d["id"] == "d:energy" for t in d["tabs"] if t["id"] == "overview" for i in t["items"]),
          "EIA seasonal charts say they need the key")

    # Markets Summary > Overview Global Yields (G10) uses a 6-column 1440 px preset while Rates & Curves > Global keeps full columns
    ov_gy = next(i for d in no_v4["dashboards"] if d["id"] == "d:markets" for t in d["tabs"] if t["id"] == "overview" for i in t["items"] if i["widgetId"] == "x_global_yields")
    rc_gy = next(i for d in no_v4["dashboards"] if d["id"] == "d:rates" for t in d["tabs"] if t["id"] == "global" for i in t["items"] if i["widgetId"] == "x_global_yields")
    check(ov_gy.get("cols") == ["country", "m3", "y2", "y10", "slope_2s10s", "policy"] and "cols" not in rc_gy,
          "Overview Global Yields (G10) uses 6-col 1440 px preset; Rates & Curves keeps all columns")

    # SEC Filings & Data + Search & Read contracts: viewer requires document_url, variants & library placed, keyed Intrinio last
    ix = bd.Index(defs)
    check(ix.pick("sec/standardized_statement", wid="sec_income_statement_facts_sec_obb") == ("v5", "sec_income_statement_facts_sec_obb"), "Index.pick exact wid match")
    check(ix.pick("sec/standardized_statement", wid="nonexistent_widget") is None, "Index.pick returns None on unknown wid (no fallback)")
    # Stage 3 T1-1/T1-2: U.S. movers from Nasdaq (V5 has no Yahoo losers / most active); Bond Lookup cards linked by Issuer and Bond
    mov = next(t for d in dashes if d["id"] == "d:eq-screen" for t in d["tabs"] if t["id"] == "movers")
    cats = [it["params"].get("category") for it in mov["items"] if it["widgetId"] == "nasdaq_markets_movers_nasdaq_obb"][:3]
    check(cats == ["most_advanced", "most_declined", "most_active_share_volume"], f"Movers: Nasdaq gainers, losers, most active: {cats}")
    cr = next(d for d in dashes if d["id"] == "d:credit")
    bl = next(t for t in cr["tabs"] if t["id"] == "bond-lookup-trace")
    g = {it["widgetId"]: it.get("groups") for it in bl["items"]}
    check(g["x_bond_lookup"] == ["Issuer"] and g["x_bond_curve"] == ["Issuer"] and g["finra_fixedincome_bond_historical_finra_obb"] == ["Bond"],
          f"Bond Lookup: table and curve share Issuer, history follows Bond: {g}")
    bond = next(x for x in cr["groups"] if x["name"] == "Bond")
    check(bond["paramName"] == "cusip" and bond.get("resetWith") == "Issuer" and "Click a CUSIP" in bond.get("prompt", ""), "Bond group: reset by Issuer, prompt until a bond is clicked")
    co_fil = next(t for d in dashes if d["id"] == "d:eq-company" for t in d["tabs"] if t["id"] == "filings-sec-data")
    co_wids = [it["widgetId"] for it in co_fil["items"]]
    for req_wid in ("sec_company_filings_sec_obb", "sec_filing_headers_sec_obb", "sec_filing_viewer_sec_obb",
                    "sec_exhibit_sec_obb", "sec_income_statement_facts_sec_obb", "sec_balance_sheet_facts_sec_obb",
                    "sec_cash_flow_facts_sec_obb", "sec_financial_statements_sec_obb", "equity_compare_company_facts_sec_obb"):
        check(req_wid in co_wids, f"Company > Filings & SEC Data places {req_wid}")
    check("equity_fundamental_filings_yfinance_obb" not in co_wids, "redundant Yahoo-only Filings card replaced by company.filings concept")
    viewer_co = next(it for it in co_fil["items"] if it["widgetId"] == "sec_filing_viewer_sec_obb")
    check(viewer_co.get("require") == ["document_url"] and not viewer_co.get("params") and viewer_co.get("groups") == ["Symbol"],
          "Company Filing Viewer requires document_url and tracks Symbol for CA/US concept switching")
    stmt_wids = {c: [m["w"] for m in out["concepts"][c]["members"]] for c in ("company.income", "company.balance", "company.cash")}
    check(stmt_wids["company.income"][:2] == ["sec_income_statement_facts_sec_obb", "tmx_equity_fundamental_income_view_obb"], "company.income resolves SEC income + TMX income_view")
    check(stmt_wids["company.balance"][:2] == ["sec_balance_sheet_facts_sec_obb", "tmx_equity_fundamental_balance_view_obb"], "company.balance resolves SEC balance + TMX balance_view")
    check(stmt_wids["company.cash"][:2] == ["sec_cash_flow_facts_sec_obb", "tmx_equity_fundamental_cash_view_obb"], "company.cash resolves SEC cash + TMX cash_view")
    check(all(bool(m.get("alt")) == (not m["w"].startswith("equity_fundamental_")) for c in ("company.income", "company.balance", "company.cash") for m in out["concepts"][c]["members"][1:]),
          "TMX / Nordic statement targets are alt; the Financials tab's own cards (Yahoo, SEC company facts) are entry points, so SHEL.L routes off SEC")
    yf = [m for m in out["concepts"]["company.income"]["members"] if m["w"] == "equity_fundamental_income_yfinance_obb"]
    check(yf and "other" in yf[0]["mk"] and "us" in yf[0]["mk"], "Yahoo statements cover U.S. and other listings (a U.S. ticker keeps Yahoo on Financials)")
    ch = out["concepts"].get("options.chains") or {}
    check([(m["w"], m.get("mk")) for m in ch.get("members", [])][:3] == [("derivatives_options_chains_yfinance_obb", ["us"]), ("derivatives_options_chains_tmx_obb", ["ca"]),
          ("derivatives_options_chains_deribit_obb", ["crypto"])], "option chains route by listing: Yahoo (U.S.), TMX (SU.TO), Deribit (BTC-USD)")
    check([m["w"] for m in out["concepts"]["company.filing_viewer"]["members"]] == ["sec_filing_viewer_sec_obb", "tmx_equity_filings_viewer_obb"], "company.filing_viewer resolves SEC + TMX viewers")
    sec_lib = next((it for it in co_fil["items"] if it.get("title") == "SEC Filing Sections"), None)
    check(sec_lib is not None and [c["w"] for c in sec_lib["choices"]] == [
        "sec_risk_factors_sec_obb", "sec_segment_revenue_sec_obb", "sec_disclosures_sec_obb", "sec_legal_proceedings_sec_obb"
    ], "SEC Filing Sections library exposes all 4 section_markdown variants")
    check(all(c["g"] != c["l"] for c in sec_lib["choices"]), "SEC Filing Sections choice group labels do not duplicate item label")
    co_keyless = next(t for d in keyless["dashboards"] if d["id"] == "d:eq-company" for t in d["tabs"] if t["id"] == "filings-sec-data")
    check(co_keyless["items"][-1]["widgetId"] == "equity_fundamental_reported_financials_intrinio_obb"
          and co_keyless["items"][-1].get("needsKey") == "intrinio_api_key", "keyed Intrinio reported_financials sorted last on Filings & SEC Data")
    co_nov4 = next(t for d in no_v4["dashboards"] if d["id"] == "d:eq-company" for t in d["tabs"] if t["id"] == "filings-sec-data")
    check("equity_fundamental_reported_financials_intrinio_obb" not in [it["widgetId"] for it in co_nov4["items"]]
          and "sec_income_statement_facts_sec_obb" in [it["widgetId"] for it in co_nov4["items"]], "--skip-v4 preserves Filings & SEC Data")

    sr = next(t for d in dashes if d["id"] == "d:filings" for t in d["tabs"] if t["id"] == "search-read")
    sr_wids = [it["widgetId"] for it in sr["items"]]
    for req_wid in ("sec_company_filings_sec_obb", "sec_full_text_search_sec_obb", "sec_latest_financial_reports_sec_obb",
                    "sec_filing_headers_sec_obb", "sec_filing_viewer_sec_obb", "sec_exhibit_sec_obb", "sec_htm_file_sec_obb"):
        check(req_wid in sr_wids, f"Search & Read places {req_wid}")
    viewer_sr = next(it for it in sr["items"] if it["widgetId"] == "sec_filing_viewer_sec_obb")
    check(viewer_sr.get("require") == ["document_url"], "Search & Read Filing Viewer requires document_url")

    for w in ("sec_company_filings_sec_obb", "sec_full_text_search_sec_obb", "sec_latest_financial_reports_sec_obb",
              "sec_filing_headers_sec_obb", "equity_fundamental_filings_tmx_obb", "equity_fundamental_filings_yfinance_obb"):
        check(w in out["columns"] and "show" in out["columns"][w], f"dashboards.json includes column spec for {w}")
    # review A3: TMX Fund Info describes an ETF; it does not follow the Canada stock picker
    ca_mkt = next((t for d in dashes if d["id"] == "d:eq-canada" for t in d["tabs"] if t["id"] == "market"), None)
    fi = next((it for it in (ca_mkt or {}).get("items", []) if "fund_info" in it["widgetId"]), None)
    check(fi is not None and fi["params"].get("symbol") == "XIU" and "Symbol" not in fi.get("groups", []), "TMX Fund Info pinned to XIU, not linked to the stock picker")
    bk_dash = next((d for d in dashes if d["id"] == "d:banking"), None)
    bk_rssd = next((g for g in (bk_dash or {}).get("groups", []) if g["name"] == "RSSD"), None)
    check(bk_rssd is not None and bk_rssd.get("syncFrom") == "Bank" and bk_rssd.get("syncUrl") == "/x/bank_rssd", "d:banking RSSD group carries syncFrom and syncUrl")

import dashboards as db_pkg  # noqa: E402
from dashboards import layouts as db_layouts, overrides as db_overrides  # noqa: E402

check(bd.CONCEPTS is db_overrides.CONCEPTS and bd.DASHBOARDS is db_layouts.DASHBOARDS,
      "W9: build_dashboards re-exports CONCEPTS and DASHBOARDS by identity from scripts/dashboards")
for _sym in ("CONCEPTS", "DASHBOARDS", "Index", "LAST_REPORT", "OPT_LABELS", "RENAMED_TABS",
             "REQUIRED_PARAMS", "SKIP", "TITLES", "build", "credentials", "load", "macro_key_provider", "slug"):
    check(hasattr(bd, _sym), f"W9: build_dashboards exports {_sym}")
check(db_pkg.CONCEPTS is db_overrides.CONCEPTS and db_pkg.DASHBOARDS is db_layouts.DASHBOARDS,
      "W9: dashboards package __init__ re-exports overrides and layouts")

done("test_generator")
