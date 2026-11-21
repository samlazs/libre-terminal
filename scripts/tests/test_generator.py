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
check(route("etf.sectors", "SPY") is None and route("etf.sectors", "XIU.TO") == "TMX", "automatic routing never lands on a paid plan")
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
    # keyless computer: cards that need a key sit behind the keyless ones in fixed columns and rows (Energy > Overview)
    real_creds = bd.credentials
    bd.credentials = lambda: set()
    try:
        keyless = replay(defs)
    finally:
        bd.credentials = real_creds
    check(bd.macro_key_provider("crude_stocks_seasonal") == "eia" and bd.macro_key_provider("crack_spreads") is None, "EIA-only curated charts need the EIA key")
    en = next(t for d in keyless["dashboards"] if d["id"] == "d:energy" for t in d["tabs"] if t["id"] == "overview")
    for col_x in sorted({i["x"] for i in en["items"] if i["y"] < 60}):
        col = sorted([i for i in en["items"] if i["x"] == col_x and i["w"] < 20], key=lambda i: i["y"])
        flags = [bool(i.get("needsKey")) for i in col]
        check(flags == sorted(flags), f"Energy > Overview column at x={col_x}: keyed cards after the keyless ones")
    en_ids = sorted(i["uid"] for i in en["items"])
    check(en_ids == sorted(i["uid"] for d in dashes if d["id"] == "d:energy" for t in d["tabs"] if t["id"] == "overview" for i in t["items"]), "same card ids with or without keys")
    check(any(i.get("needsKey") == "eia_api_key" and i["widgetId"] == "x_macro" for i in en["items"]), "EIA seasonal charts say they need the key")

    # SEC Filings & Data + Search & Read contracts: viewer requires document_url, variants & library placed, keyed Intrinio last
    ix = bd.Index(defs)
    check(ix.pick("sec/standardized_statement", wid="sec_income_statement_facts_sec_obb") == ("v5", "sec_income_statement_facts_sec_obb"), "Index.pick exact wid match")
    check(ix.pick("sec/standardized_statement", wid="nonexistent_widget") is None, "Index.pick returns None on unknown wid (no fallback)")
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
    check(all(m["alt"] for c in ("company.income", "company.balance", "company.cash") for m in out["concepts"][c]["members"][1:]), "non-SEC statement targets are marked alt so Financials tab keeps Yahoo")
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
done("test_generator")
