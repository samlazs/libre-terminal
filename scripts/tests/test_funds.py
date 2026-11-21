"""Fund profile numbers (review A2): Yahoo sends portfolio P/E, P/B, P/S, P/CF as yields (run_backend.fund_multiples)."""

import importlib.util

from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

# VFIAX recorded live 2026-11-18: 0.04262, 0.19588, 0.28176, 0.06114 -> 1/x by hand: 23.46, 5.11, 3.55, 16.36
vfiax = {"symbol": "VFIAX", "price_to_earnings": 0.04262, "price_to_book": 0.19588, "price_to_sales": 0.28176,
         "price_to_cashflow": 0.06114, "three_year_earnings_growth": 18.51}
out = rb.fund_multiples(vfiax)
check(out["price_to_earnings"] == 23.46 and out["price_to_book"] == 5.11 and out["price_to_sales"] == 3.55
      and out["price_to_cashflow"] == 16.36, "VFIAX yields become multiples 23.46 / 5.11 / 3.55 / 16.36")
check(out["three_year_earnings_growth"] == 18.51 and out["symbol"] == "VFIAX", "other fields untouched")
check(vfiax["price_to_earnings"] == 0.04262, "input row not mutated")
check(rb.fund_multiples({"price_to_earnings": 21.4})["price_to_earnings"] == 21.4, "a value already a multiple is kept")
check(rb.fund_multiples({"price_to_earnings": -0.01, "price_to_book": 0})["price_to_earnings"] is None
      and rb.fund_multiples({"price_to_book": 0})["price_to_book"] is None, "negative or zero earnings yield: blank, not a negative multiple")
check(rb.fund_multiples({"price_to_earnings": None})["price_to_earnings"] is None
      and rb.fund_multiples({"price_to_earnings": float("nan")})["price_to_earnings"] != rb.fund_multiples({"price_to_earnings": float("nan")})["price_to_earnings"],
      "missing values pass through")

# TMX XIU holdings recorded 2026-11-18: TD 1,609,180,279 / 9,246,037 = 174.04 (TD.TO CAD 174.21) but stamped USD
xiu = [{"symbol": "RY", "market_value": 2213096360.0, "currency": "CAD"}, {"symbol": "TD", "market_value": 1609180279.0, "currency": "USD"}]
fixed = rb.holdings_in_fund_currency(xiu, "cad")
check([r["currency"] for r in fixed] == ["CAD", "CAD"] and fixed[1]["market_value"] == 1609180279.0, "TMX holdings take the fund's currency")
check(xiu[1]["currency"] == "USD", "input rows not mutated")
check([r["currency"] for r in rb.holdings_in_fund_currency(xiu, None)] == [None, None]
      and rb.holdings_in_fund_currency([], "CAD") == [], "unknown fund currency: blank, never a guessed one")

# review A3: TMX movers pool (most active by volume, as recorded 2026-11-18) ranked by % change per category
pool = [{"symbol": "CNQ", "percentChange": -0.971578}, {"symbol": "HNU", "percentChange": 4.0995609},
        {"symbol": "QBR.B", "percentChange": -12.643678}, {"symbol": "X", "percentChange": 0.5}, {"symbol": "Z", "percentChange": 0.0},
        {"symbol": "N", "percentChange": None}]
check([r["symbol"] for r in rb.rank_movers(pool, "gainers", 10)] == ["HNU", "X"], "gainers: only rises, largest first")
check([r["symbol"] for r in rb.rank_movers(pool, "losers", 10)] == ["QBR.B", "CNQ"], "losers: only falls, largest fall first")
check([r["symbol"] for r in rb.rank_movers(pool, "losers", 1)] == ["QBR.B"] and rb.rank_movers([], "gainers", 5) == [], "cut to the limit")

# review A2: fund allocation labels (AGG recorded 2026-11-18: BB, AA, AAA, A, OTHER, B, BBB, BELOW_B, US_GOVERNMENT)
agg = [{"breakdown": "asset_class", "category": "Bond"}] + [{"breakdown": "bond_rating", "category": c} for c in
       ("BB", "AA", "AAA", "A", "OTHER", "B", "BBB", "BELOW_B", "US_GOVERNMENT")] + [{"breakdown": "sector", "category": "Realestate"}]
al = rb.fund_allocation_rows(agg)
check([r["category"] for r in al] == ["Bond", "Real Estate", "AAA", "AA", "A", "BBB", "BB", "B", "Below B", "U.S. Government", "Other"],
      "allocation: plain names, asset class, sectors, then ratings AAA down")
check(agg[-1]["category"] == "Realestate", "input not mutated")

# review A3: TMX Asset Info escaped values twice ("Media &amp;amp; Telecommunications" in the HTML)
try:
    from openbb_tmx.utils import asset_info as _tai
    _applied = []
    rb.install_tmx_asset_info_escape_fix(_applied)
    _html = _tai._stat("Sector", _tai._plain("Media & Telecommunications"))
    check("Media &amp; Telecommunications<" in _html and "&amp;amp;" not in _html, "TMX Asset Info escapes once")
    check("&lt;b&gt;" in _tai._stat("X", "<b>"), "raw markup is still escaped")
except ImportError:
    pass                                                    # the TMX extension is not in this environment

# review A4: rescales decided against known values (NVDA current ratio 390.53 -> 3.9053; SU short 0.000167 -> 0.0167 = 1.67%)
class _M:
    def __init__(self, **kw):
        self.__dict__.update(kw)
m = rb.rescale_fields(_M(current_ratio=390.52638, quick_ratio=None, cash_ratio=194.49678), ("current_ratio", "quick_ratio", "cash_ratio"), 0.01)
check(abs(m.current_ratio - 3.9052638) < 1e-9 and m.quick_ratio is None and abs(m.cash_ratio - 1.9449678) < 1e-9, "Nasdaq ratios: percent -> multiple")
d = rb.rescale_fields({"short_interest_percent": 0.000167}, ("short_interest_percent",), 100)
check(abs(d["short_interest_percent"] - 0.0167) < 1e-12, "TMX short interest: 0.000167 -> 0.0167 (1.67%)")
check({r[0].split(".")[0] for r in rb.RESCALES} == {"openbb_nasdaq", "openbb_tmx", "openbb_federal_reserve"}, "rescale table lists only decided sources")
# review A5: Fed MMF holdings in $ millions (U.S. Government security repo 2026-07-31: 2,754,760 = $2.75476T); columns
# are named by the data, so every numeric field is scaled and the date and text stay as they are
class _D(_M):
    def model_dump(self):
        return dict(self.__dict__)
mmf = rb.rescale_fields(_D(date="2026-07-31", country="US", **{"U.S. Government security repo": 2754760.0, "Other repo": 169497.0, "Other": None}), None, 1e6)
check(getattr(mmf, "U.S. Government security repo") == 2.75476e12 and getattr(mmf, "Other repo") == 1.69497e11 and mmf.date == "2026-07-31"
      and mmf.country == "US" and mmf.Other is None, "MMF holdings: $ millions -> dollars, every numeric column")
# review A5: OECD GDP unit text from the record's codes (France, probed 2026-11-18)
nan = float("nan")
real = {"FREQ": "Q", "ADJUSTMENT": "Y", "REF_AREA_label": "France", "UNIT_MEASURE": "USD_PPP", "PRICE_BASE": "LR", "TRANSFORMATION": "LA",
        "REF_YEAR_PRICE": 2020, "BASE_PER": nan}
nominal = dict(real, PRICE_BASE="V", REF_YEAR_PRICE=nan)
eo = {"FREQ": "A", "REF_AREA_label": "France", "MEASURE": "GDPV_USD", "UNIT_MEASURE": "USD_EXC", "PRICE_BASE": "L", "BASE_PER": 2021, "ADJUSTMENT": nan}
check(rb.oecd_unit_text(real) == "USD at PPPs, chain-linked volume, 2020 prices, seasonally adjusted, annual rate", "OECD real GDP unit")
check(rb.oecd_unit_text(nominal) == "USD at PPPs, current prices, seasonally adjusted, annual rate", "OECD nominal GDP unit")
check(rb.oecd_unit_text(eo) == "USD at constant exchange rates, chain-linked volume, 2021 prices", "OECD EO forecast unit")
check(rb.oecd_unit_text({"UNIT_MEASURE": "PC", "PRICE_BASE": "_Z"}) is None, "growth / percent: no USD unit text")
# review A6: USAspending pages; the installed templates' script is replaced, anything else is left alone
try:
    import openbb_government_us as _ogu
    REAL_SITE = __import__("pathlib").Path(_ogu.__file__).parent.parent
except ImportError:
    REAL_SITE = None
for _tpl in ("award_info.html", "recipient_info.html"):
    _p = REAL_SITE / "openbb_government_us" / "treasury" / "assets" / _tpl if REAL_SITE else None
    if _p and _p.exists():
        _out = rb.usaspending_html(_p.read_text(encoding="utf-8"))
        check(rb.USASPENDING_SCRIPT in _out and _out.count("<script>") == _p.read_text(encoding="utf-8").count("<script>"), f"{_tpl}: formatter replaced once")
check(rb.usaspending_html("<p>x</p><script>other()</script>") == "<p>x</p><script>other()</script>", "other pages unchanged")
# review A6: CDS index spreads per print notation (live 2026-11-18: 0.006112474 notation 3 = 61.12 bp)
check(rb.imf_title_without_scale("RESERVE ASSETS (APPROXIMATE MARKET VALUE) (US Dollars, Millions)") == "RESERVE ASSETS (APPROXIMATE MARKET VALUE) (US Dollars)"
      and rb.imf_title_without_scale("Gold (Fine Troy Ounces)") == "Gold (Fine Troy Ounces)", "IMF header: scale word dropped, other units kept")
# review F3: OECD dataflow list (probed 2026-11-18)
_df = [{"value": "OECD.TAD.ARP@DF_AEI2023_DASHBOARD", "label": "Agri-Environmental indicators: 2023 Dashboard Archived", "topic_name": ""},
       {"value": "OECD.X@DF_T2SEV4EYSJTJMT3HVC8I", "label": "T2SEV4EYSJTJMT3HVC8I", "topic_name": ""},
       {"value": "OECD.SDD@DF_TEST_MEI", "label": "TEST - Main Economic Indicators Publication", "topic_name": "Economy"},
       {"value": "OECD.Y@DF_DD_HCL_TEST", "label": "DD HCL order hierarcy test", "topic_name": ""},
       {"value": "OECD.Z@DF_SDG_GLC", "label": "SDG Country Global Dataflow", "topic_name": ""},
       {"value": "OECD.SDD.TPS@DF_PRICES_ALL", "label": "Consumer price indices (CPIs, HICPs), COICOP 1999", "topic_name": "Economy"},
       {"value": "OECD.EDU@DF_PISA", "label": "PISA latest scores", "topic_name": "Education"},
       {"value": "OECD.SDD.NAD@DF_NAAG", "label": "NAAG", "topic_name": ""}]
check([e["value"].split("@")[1] for e in rb.oecd_listed_dataflows(_df)] == ["DF_PRICES_ALL", "DF_PISA", "DF_SDG_GLC", "DF_NAAG", "DF_AEI2023_DASHBOARD"],
      "OECD dataflows: placeholder and test flows dropped; categorised first, uncategorised, then archived")
try:
    from openbb_tmx.models.equity_quote import TmxEquityQuoteFetcher as _TQ
    rb.install_tmx_quote_symbol_order([])
    _q = _TQ.transform_query({"symbol": "SU.TO,RY.TO"})
    _rows = _TQ.transform_data(_q, [{"symbol": "RY", "name": "Royal Bank"}, {"symbol": "SU", "name": "Suncor"}])
    check([r.symbol for r in _rows] == ["SU", "RY"], "TMX quotes for SU.TO,RY.TO: no KeyError, rows in the order asked (review C2)")
except ImportError:
    pass
try:
    from openbb_fred.models.bond_indices import FredBondIndicesFetcher as _FB
    rb.install_fred_bond_index_unit([])
    _u = {it: [getattr(r, "unit", None) for r in _FB.transform_data(_FB.transform_query({"category": "us", "index": "corporate", "index_type": it}),
                                                                       {"data": [{"date": "2026-11-17"}]}).result]
          for it in ("yield", "oas", "total_return")}
    check(_u == {"yield": ["percent (effective yield)"], "oas": ["percent (option-adjusted spread)"], "total_return": ["index level (total return)"]},
          "FRED bond indices: unit from index_type (yield 8.71 % vs total-return index 641.24)")
except ImportError:
    pass
check(rb.bill_id_from_package("BILLS-119hr4930enr") == "119-hr-4930" and rb.bill_id_from_package("BILLS-119s1071is") == "119-s-1071"
      and rb.bill_id_from_package("BILLS-118hjres7enr") == "118-hjres-7" and rb.bill_id_from_package("BILLS-119sres12ats") == "119-sres-12"
      and rb.bill_id_from_package("CREC-2026-11-17") is None and rb.bill_id_from_package(None) is None, "GovInfo bill package -> bill id (review D2)")
_opts = [{"option": s} for s in ("SPY261120C00710000", "NVDA1261120P00150000", "B1SPY261120C00710000", "SPXW261120C06000000", "", None)]
check([o["option"] for o in rb.cboe_standard_contracts(_opts)] == ["SPY261120C00710000", "NVDA1261120P00150000", "SPXW261120C06000000"],
      "Cboe chains: OCC symbols kept (adjusted NVDA1 too), B1SPY... and blanks dropped (review C2)")
check(rb.cds_spread_bp(0.006112474, "3") == 61.1247 and rb.cds_spread_bp(257, "4") == 257.0, "CDS spread: decimal x 10,000; bp as is")
check(rb.cds_spread_bp(99.5, "1") is None and rb.cds_spread_bp(None, "3") is None and rb.cds_spread_bp(0.01, "") is None, "price or unknown notation: no bp")
check("_terminal_cache_rev" in (ROOT / "scripts" / "run_backend.py").read_text(encoding="utf-8").split("def install_cds_spread_bp")[1].split("\ndef ")[0]
      and "_terminal_cache_rev" in (ROOT / "scripts" / "run_backend.py").read_text(encoding="utf-8").split("async def fetch_data(cls, params")[1][:1500],
      "a patched CFTC transform bumps the disk-cache key (answers saved before it lack spread_bp)")

# review B3: USDA PSD circulars are a GET route reading "urls" from a JSON body; the terminal's document POST is taken as GET
check(rb.route_method("POST", "/api/v1/commodity/psd_report") == "GET", "PSD circular POST answered as the GET route")
check(rb.route_method("POST", "/api/v1/commodity/weather_bulletins_download") == "POST"
      and rb.route_method("GET", "/api/v1/commodity/psd_report") == "GET", "other routes and methods untouched")
# review H3: COT search aliases and the main contract first
check(rb.cot_search_plan("10-year") == ("UST 10Y", ["043602"]) and rb.cot_search_plan(" Crude ")[1][0] == "067651"
      and rb.cot_search_plan("copper") == ("copper", []), "COT aliases: 10-year -> UST 10Y; crude pins WTI; others unchanged")
_rows = [{"cftc_contract_market_code": c} for c in ("037021", "06765T", "067651", "06742T")]
check([r["cftc_contract_market_code"] for r in rb.cot_pin_first(_rows, ["067651", "06765T"])] == ["067651", "06765T", "037021", "06742T"]
      and rb.cot_pin_first(_rows, []) == _rows, "pinned contracts first, the rest in CFTC order")
# review H4: funding APR from the 8-hour rate in percent (BTC 0.000198 -> 0.2168; ETH -0.0015 -> -1.6425)
check(rb.funding_apr(0.000198) == 0.2168 and rb.funding_apr(-0.0015) == -1.6425 and rb.funding_apr(None) is None, "funding APR = 8h x 3 x 365")
# review G2: order book as a ladder per instrument (source order: bids then asks, level 1 = best)
_book = [{"symbol": s, "side": sd, "price": p} for s, sd, p in
         [("BTC", "bid", 100.0), ("BTC", "bid", 99.5), ("BTC", "ask", 100.5), ("BTC", "ask", 101.0), ("ETH", "bid", 3.0), ("ETH", "ask", 3.1)]]
check([(r["symbol"], r["price"]) for r in rb.book_ladder(_book, get=lambda r, k: r[k])]
      == [("BTC", 101.0), ("BTC", 100.5), ("BTC", 100.0), ("BTC", 99.5), ("ETH", 3.1), ("ETH", 3.0)], "order book: asks high-low above bids, per instrument")
# review H3: AEO rows without a series name and unit (placeholder ids) are left out; named or unit-bearing rows stay
_aeo = [{"series": "NA_NA_NA_NA_NA_NA_NA_NA", "series_name": None, "unit": None, "value": 1559.17},
        {"series": "NA_nom_elep_NA_hdg_NA_NA_ndlrpmbtu", "series_name": "Energy Prices : Nominal : Electric Power : Uranium", "unit": "nom $/MMBtu", "value": 20.74},
        {"series": "x", "series_name": None, "unit": "quads", "value": 1.0}]
check([r["value"] for r in rb.aeo_identified(_aeo, get=lambda r, k, d=None: r.get(k, d))] == [20.74, 1.0], "AEO: unnamed placeholder rows dropped")
# review H3: partial newest hours of EIA-930 aggregates dropped (below half the same hour a day earlier), per series
import datetime as _dtm
_t0 = _dtm.datetime(2026, 11, 19, 5)
def _g(st, h, v, resp="US48"):
    return {"respondent": resp, "series_type": st, "fuel_type": None, "subregion": None, "date": _t0 + _dtm.timedelta(hours=h), "value": v}
_grid = ([_g("DF", h, 420000.0) for h in range(0, 27)] + [_g("DF", 27, 205294.0), _g("DF", 28, 78252.0)]
         + [_g("D", 24, 450000.0), _g("D", 25, 200000.0)] + [_g("TI", 0, -4000.0), _g("TI", 24, -10.0)] + [_g("DF", 3, 1000.0, "SMALL")])
_kept = rb.grid_drop_partial(_grid, get=lambda r, k, d=None: r.get(k, d))
check(len(_kept) == len(_grid) - 2 and all(r["value"] not in (205294.0, 78252.0) for r in _kept),
      "grid: US48 forecast 205,294 and 78,252 dropped (half of 420,000 a day earlier); demand without a day-earlier value kept; interchange untouched")
# P4 slow cards: the same Yahoo futures curve asked twice within CURVE_TTL is fetched once; an empty curve is not kept
import asyncio as _aio
import pandas as _pd
from openbb_yfinance.utils import helpers as _yfh
_calls = []
async def _fake_curve(symbol, date=None):
    _calls.append(symbol)
    return _pd.DataFrame({"expiration": ["2026-12"], "price": [3.1]}) if symbol == "NG" else _pd.DataFrame()
_saved_curve = _yfh.get_futures_curve
try:
    _yfh.get_futures_curve = _fake_curve
    rb.install_futures_curve_fix([])
    _a = _aio.run(_yfh.get_futures_curve("NG"))
    _a.loc[0, "price"] = 99.0                                   # a caller changing its copy does not touch the kept one
    _b = _aio.run(_yfh.get_futures_curve("NG"))
    _aio.run(_yfh.get_futures_curve("HG")); _aio.run(_yfh.get_futures_curve("HG"))
finally:
    _yfh.get_futures_curve = _saved_curve
check(_calls == ["NG", "HG", "HG"] and float(_b.loc[0, "price"]) == 3.1, "curve fetched once within the TTL, copies served, empty curves refetched: " + str(_calls))
# P2: backend stale note and its header form
_m = rb.stale_message("U.S. EIA (energy data)", 1000.0, now=1000.0 + 19 * 3600)
check(_m.startswith("U.S. EIA not answering: showing the copy saved ") and _m.endswith("(19 h old)"), "stale message: " + _m)
check(rb.stale_header(["a – b\nc" + "x" * 400]) == ("a ? b?c" + "x" * 293).encode("ascii"), "header: ASCII, one line, 300 chars")# review G1: dark palette in the backend's framed HTML pages; errors to a frame as a short escaped page
_pg = rb.dark_html("<html><head><title>x</title></head><body>a</body></html>")
check(_pg.startswith("<html><head>" + rb.DARK_STYLE + "<title>") and rb.dark_html("<p>b</p>") == rb.DARK_STYLE + "<p>b</p>", "dark style at the head, or first")
_ep = rb.frame_error_page('{"detail":"Award <b>X</b> not found"}', 400)
check("Award &lt;b&gt;X&lt;/b&gt; not found" in _ep and rb.DARK_STYLE in _ep and "{" not in _ep.split("</style>")[1], "error detail escaped, no JSON")
check("HTTP 502" in rb.frame_error_page("<html>bad gateway</html>", 502), "no JSON detail: the status")

done("test_funds")
