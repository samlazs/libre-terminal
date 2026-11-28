"""Dashboard spec DSL helpers, shared group parameters, sections, and DASHBOARDS tree."""

from __future__ import annotations

# ------------------------------------------------------------------ spec DSL
def W(ep, w=None, h=None, provider=None, chart=False, params=None, title=None, groups=None, compact=None, view=None, manual=None, sort=None, since=None, cols=None, plot=None, wid=None, only=None,
      tparam=None):
    """sort="date:asc" opens the table sorted that way; since="today" (with sort on a date) hides rows before today.
    tparam: {"param": name, "labels": {VALUE: label}}: the title ends with the parameter's current value, e.g. "(E-mini S&P 500)"
    for ES and "(CL)" once the owner types CL (a fixed "(E-mini S&P 500)" was wrong after a change; review F3).
    cols: the columns this card shows first (overrides the endpoint preset in terminal/columns.py).
    plot: open the table as a chart, {"x": field, "ys": [fields], "split": field, "type": line|step|bar|area|scatter}.
    wid: pin a specific backend widgetId when multiple widgets share an endpoint.
    only: the providers the card's Source menu offers (a commodity curve is not on Cboe or Deribit)."""
    return {"ep": ep, "w": w, "h": h, "provider": provider, "chart": chart, "params": params or {}, "title": title, "groups": groups, "compact": compact,
            "view": "chart" if plot else view, "manual": manual, "sort": sort, "since": since, "cols": cols, "plot": plot, "wid": wid, "only": only, "tparam": tparam}


def CURVE(symbol, title, w=20, h=16):
    """A futures curve drawn as a curve: price by expiry, nearest contract first."""
    return W("derivatives/futures/curve", w, h, provider="yfinance", params={"symbol": symbol}, title=title, plot={"x": "expiration", "ys": ["price"], "split": "", "type": "line"},
             only=["yfinance"])          # the endpoint's Cboe (VX) and Deribit (crypto) curves do not list commodities (review C3)

# The economic calendar people read: event names, consensus, previous, actual; upcoming events first, from today.
ECON_CAL = dict(provider="nasdaq", sort="date:asc", since="today")


def M(preset, w=20, h=16, **kw):
    """Curated economic chart (a named set of FRED series)."""
    return W("x:macro_chart", w, h, params=dict(preset=preset, **kw))

def C(group, fact, w=20, h=18, period=None, **kw):
    """One reported figure across a peer group, from SEC filings."""
    return W("x:compare", w, h, params=dict(group=group, fact=fact, **({"period": period} if period else {}), **kw))


def LIB(title, selectors, w=40, h=26, groups=None, first=None):
    """One card holding a whole family of datasets, with a picker (instead of pages of near-identical cards).

    A selector prefixed "rest:" ("rest:usda/*") only takes what this dashboard has not placed yet.
    `first` names the endpoint the card opens on (the most useful dataset of the family, not the alphabetical first)."""
    spec = W("lib:" + title, w, h, title=title, groups=groups)
    spec["library"] = selectors
    spec["first"] = first
    return spec


def METRICS(symbols, title, w=20, h=16):
    """Valuation, growth and margin comparison for a list of tickers."""
    return W("equity/fundamental/metrics", w, h, provider="yfinance", params={"symbol": symbols}, title=title, groups=[])


# analyst actions (upgrades, downgrades, target changes): newest first, the columns a desk reads
ANALYST_ACTIONS = dict(sort="published_date:desc", cols=["published_date", "analyst_firm", "rating_current", "rating_previous", "price_target",
                                                        "price_target_previous", "price_target_action"])


def MOVERS(h):
    """U.S. movers from Nasdaq (V5 has no Yahoo losers / most-active lists; its only gainers list is TMX's): gainers,
    losers and most active, each with the session (regular, pre-market, after-hours) on the card."""
    return [W("nasdaq/markets/movers", w, h, provider="nasdaq", params={"category": cat, "asset_class": "stocks", "session": "market", "limit": 25}, title=title)
            for w, cat, title in ((13, "most_advanced", "Top gainers (Nasdaq)"), (14, "most_declined", "Top losers (Nasdaq)"),
                                  (13, "most_active_share_volume", "Most active (Nasdaq, shares)"))]


def X(name, w=None, h=None, title=None, groups=None, cols=None, **params):
    return W("x:" + name, w, h, params=params, title=title, groups=groups, cols=cols)


def NEWS(w=None, h=None, title=None, groups=None, compact=None, **params):
    """Ranked or monitor headlines (terminal/news.py): subject="symbol" follows the dashboard ticker, "country" its country,
    "market" the whole market; mode="monitor" lists newest first (the situation room)."""
    return W("x:news", w, h, params=params, title=title, groups=groups, compact=compact)


def OVB(preset, h):
    """A Markets Summary > Overview board: five columns fit a third of the screen (14 of 17 clipped Symbol/Ccy; review F1).
    Yields keep Chg (basis points), currencies their quote convention."""
    cols = ["name", "pair_last", "chg_pct", "ytd"] if preset == "fx" else \
        ["name", "price", "chg", "chg_pct", "ytd"] if preset == "fixed_income" else ["name", "price", "chg_pct", "w1", "ytd"]
    return W("x:board", None, h, params={"preset": preset}, cols=cols)


def WATCH(w=None, h=None):
    """The user's own watchlist (lists are kept in the saved state, edited on the card)."""
    return X("watchlist", w, h, groups=[])


HYPERSCALERS = "MSFT,GOOGL,AMZN,META,ORCL"


def T(name, rows=None, auto=None, columns=None, cols=2, groups=None, h=None):
    return {"name": name, "rows": rows or [], "auto": auto or [], "columns": columns, "cols": cols, "groups": groups, "h": h}


def D(did, name, section, desc, tabs, groups=None, more=None):
    return {"id": did, "name": name, "section": section, "desc": desc, "tabs": tabs, "groups": groups or [], "more": more or []}


SYMBOL = {"name": "Symbol", "paramName": "symbol", "value": "AAPL"}
# Company > Estimates: which earnings report the scorecard and the release show (latest unless picked); the choices are the
# company's own reports, and a new company starts again on its latest
REPORT = {"name": "Report", "paramName": "report", "value": "", "optionsEndpoint": "/x/earnings_reports", "optionsParams": {"symbol": "$symbol"},
          "resetWith": "Symbol"}
COUNTRY = {"name": "Country", "paramName": "country", "value": "united_states", "options": "countries"}
MACRO_COUNTRY = {"name": "Country", "paramName": "country", "value": "united_states", "options": "macro"}

SECTIONS = [
    ("overview", "Overview"), ("equities", "Equities"), ("funds", "Indices, ETFs & Funds"), ("rates", "Rates & Credit"), ("derivatives", "Derivatives"),
    ("fx", "Currencies"), ("commodities", "Commodities & Energy"), ("crypto", "Crypto"), ("macro", "Macro & Economy"), ("policy", "Government & Regulation"),
    ("research", "News & Research"),
]

PERF = lambda syms, period="YTD", **kw: X("performance", symbols=syms, period=period, **kw)  # noqa: E731

DASHBOARDS = [
    # ============================================================ OVERVIEW
    D("markets", "Markets Summary", "overview", "Cross-asset overview: equities, index and Treasury futures, sectors, rates, credit, currencies, commodities, crypto, movers and news on one screen.", [
        T("Overview", columns=[
            (12, [W("x:watchlist", h=16, groups=[], cols=["name", "currency", "price", "chg_pct", "ytd"]), OVB("us_equity", 15), OVB("us_sectors", 17), OVB("fixed_income", 22)]),
            # review G2: the chart was ~610 px and Global Yields 42 rows while Market News showed two headlines
            (16, [NEWS(h=14, subject="market", title="Top market news", groups=[], compact=True),
                  PERF("^GSPC,^NDX,^DJI,^RUT", h=17, title="Normalized Performance: U.S. indices"),
                  W("yfinance/tv_widget/view", h=15, title="Chart"),
                  X("global_yields", h=16, countries="g10", title="Global Yields (G10)", cols=["country", "m3", "y2", "y10", "slope_2s10s", "policy"]),
                  X("style_box", h=9)]),
            # index and Treasury futures on top: 1D is the move since the last settlement (Sunday evening: since Friday's close).
            # Added in the last column so per-card overrides keyed by position (overview-4, overview-5) keep pointing at their cards.
            (12, [OVB("futures_brief", 12), OVB("fx", 15), OVB("global_markets", 26), OVB("commodities", 25), OVB("crypto", 20)]),
        ]),
        T("Chart", rows=[[W("yfinance/tv_widget/view", 28, 26), WATCH(12, 26)],
                         [X("chart", 28, 22, title="Compare chart",compare="^NDX,^RUT", view="pct", period="1Y", studies="ma50,ma200"), X("board", 12, 22, preset="megacaps")],
                         [X("board", 20, 18, preset="us_equity"), X("board", 20, 18, preset="global_indices")]]),
        T("Equities", rows=[[X("board", 15, 24, preset="global_indices"), PERF("^GSPC,^STOXX50E,^N225,^HSI,EEM", w=25, h=24, title="Normalized Performance: world equities")],
                            [X("perf_bars", 13, 18, preset="us_sectors", period="1M"), X("perf_bars", 14, 18, preset="global_markets", period="1M"),
                             X("perf_bars", 13, 18, preset="us_industries", period="1M")],          # one horizon per row (review G2)
                            [X("perf_bars", 14, 22, preset="themes", period="1M"), X("board", 26, 22, preset="themes")],
                            [X("board", 15, 18, preset="volatility"), PERF("^VIX,^MOVE,^OVX", "1Y", w=12, h=18, title="Normalized Performance: volatility"), X("board", 13, 18, preset="us_factors")],
                            MOVERS(16)]),
        T("Rates & Credit", rows=[[X("global_yields", 24, 20), X("yield_curves", 16, 20)],
                                  [X("policy_rates", 14, 18), X("policy_rate_history", 13, 18), X("board", 13, 18, preset="fixed_income")],
                                  [M("credit_spreads", 14, 16), M("treasury_yields", 13, 16), M("fed_liquidity", 13, 16)],
                                  [X("board", 20, 14, preset="us_rates"), X("cds_indices", 20, 14)]]),
        T("FX, Commodities & Crypto", rows=[[X("board", 15, 25, preset="fx", cols=["name", "pair_last", "chg_pct", "ytd"]),
                                             X("perf_bars", 12, 25, preset="fx", period="1M", title="Ranking: currencies vs USD"), X("fx_moves", 13, 25, period="1M")],
                                            [X("board", 15, 22, preset="commodities"), X("perf_bars", 12, 22, preset="commodities", period="1M"), PERF("CL=F,GC=F,HG=F,NG=F", "1Y", w=13, h=22, title="Normalized Performance: commodities")],
                                            [X("board", 15, 18, preset="crypto"), PERF("BTC-USD,ETH-USD,SOL-USD", "1Y", w=13, h=18, title="Normalized Performance: crypto"), X("crypto_vol", 12, 18, title="Bitcoin implied vs realised volatility")]]),
        T("Risk & Geopolitics", columns=[
            (14, [X("board", h=30, preset="risk_monitor")]),
            (13, [M("gpr_daily", h=15, since="2019"), M("oil_volatility", h=15, since="2021")]),
            (13, [M("policy_uncertainty", h=15, since="2019"), M("chokepoint_tankers", h=15, since="2023")]),
        ], rows=[[X("chokepoint_monitor", 24, 16), X("gpr_table", 16, 16)]]),
        T("News & Calendar", rows=[[NEWS(27, 22, subject="market", mode="monitor", title="Market monitor (newest first)", groups=[]), W("economy/calendar", 13, 22, title="Economic calendar (upcoming)", **ECON_CAL)],
                                   [W("equity/calendar/earnings", 20, 16, provider="nasdaq"), W("equity/calendar/ipo", 20, 16)]]),
    ], groups=[dict(SYMBOL, value="^GSPC")]),

    # ============================================================ EQUITIES
    D("eq-company", "Company", "equities", "Everything about one company: price, profile, statements, estimates, ownership, filings, options. Type a ticker once.", [
        # Tab order follows how an analyst works a name: DES, FA, EE/ANR, RV, HDS, EVTS, then the U.S.-only depth (SEC, filings, shorts, options).
        # DES: chart, snapshot and company news first, consensus and recent analyst actions beside them, comps below
        T("Overview", columns=[(24, [W("yfinance/tv_widget/view", h=22), X("snapshot", h=26)]),
                               (16, [NEWS(h=22, subject="symbol", title="Company news"), W("equity/estimates/consensus", h=12, title="Analyst consensus"),
                                     W("equity/estimates/price_target", h=14, title="Latest analyst actions", **ANALYST_ACTIONS)])],
          rows=[[X("comps", 40, 16, title="Comparable companies")]]),
        T("Financials", cols=1, h=16, auto=["equity/fundamental/income", "equity/fundamental/balance", "equity/fundamental/cash"]),
        # EE / ANR, earnings day first: the report against the consensus (with the reaction and the next quarter the company
        # guides for), how the consensus is moving, the record of surprises, what analysts did since; everything else in one library
        T("Estimates", rows=[
            [X("earnings", 18, 22, title="Earnings: expected vs reported"), X("earnings_release", 22, 22, title="Earnings release (SEC 8-K, exhibit 99.1)")],
            [X("earnings_history", 20, 16, title="EPS surprise history"), X("consensus", 20, 16, title="Consensus and revisions")],
            [W("equity/estimates/price_target", 20, 14, title="Analyst actions", **ANALYST_ACTIONS), W("equity/estimates/consensus", 20, 14, title="Analyst consensus")],
            [W("yfinance/estimates/eps_revisions", 20, 12, title="EPS estimate revisions: analysts raising vs cutting (7 and 30 days)"), LIB("More estimates", ["rest:yfinance/estimates/*", "rest:equity/estimates/*"], w=20, h=12)]]),
        T("Price & Peers", rows=[[X("chart", 40, 24, compare="^GSPC", view="pct", period="1Y")],
                                 [X("comps", 40, 18)], [W("equity/fundamental/metrics", 20, 14), W("equity/price/quote", 20, 14)],
                                 [W("equity/profile", 20, 14, title="Company profile"), W("x:risk_stats", 20, 14, wid="x_peer_risk")]],
          auto=["yfinance/asset_info/view", "equity/price/historical", "equity/compare/peers", "equity/compare/groups", "equity/price/nbbo"]),
        # Stage 3 8d: where the multiple sits in its own 3/5/10-year history, and what drives the return on equity
        T("Growth & Ratios", rows=[[X("valuation_bands", 24, 16, title="Valuation bands"), X("roic_dupont", 16, 16)]],
          auto=["equity/fundamental/income_growth", "equity/fundamental/balance_growth", "equity/fundamental/cash_growth", "equity/fundamental/ratios",
                                    "equity/historical_market_cap", "equity/fundamental/trailing_dividend_yield", "equity/price/performance"]),
        T("Ownership & Insiders", auto=["yfinance/ownership/major", "yfinance/ownership/institutional", "yfinance/ownership/mutual_fund", "equity/ownership/insider_trading",
                                         "equity/ownership/share_statistics", "yfinance/ownership/*", "equity/ownership/institutional", "equity/ownership/major_holders", "sec/beneficial_ownership",
                                         "equity/fundamental/management", "equity/fundamental/management_compensation", "sec/executive_compensation", "sec/management_profiles",
                                         "sec/pay_versus_performance"]),
        T("Dividends & Events", auto=["yfinance/company_calendar", "equity/fundamental/dividends", "equity/fundamental/historical_eps", "equity/fundamental/historical_splits",
                                       "equity/fundamental/transcript", "equity/calendar/events"]),
        # SEC segment revenue (keyless, U.S. filers) opens the tab; the KPI endpoints after it need legacy V4 (FMP / Intrinio)
        T("Segments & KPIs", rows=[[W("sec/section_markdown", 40, 20, wid="sec_segment_revenue_sec_obb", title="Segment revenue (SEC filings)")]], auto=["equity/fundamental/revenue_per_segment", "equity/fundamental/revenue_per_geography", "equity/fundamental/employee_count",
                                    "equity/fundamental/historical_attributes", "equity/fundamental/latest_attributes", "equity/fundamental/search_attributes", "equity/fundamental/esg_score"]),
        # filings & header drill-down into the viewer first, then overview/MD&A/sections/exhibits, then XBRL & as-filed statements
        T("Filings & SEC Data", rows=[
            [W("sec/company_filings", 20, 14), W("sec/filing_headers", 20, 14)],
            [W("sec/filing_viewer_app", 40, 24, groups=["Symbol"])],
            [W("sec/company_overview", 20, 16), W("equity/fundamental/management_discussion_analysis", 20, 16)],
            [LIB("SEC Filing Sections", [
                W("sec/section_markdown", wid="sec_risk_factors_sec_obb"),
                W("sec/section_markdown", wid="sec_segment_revenue_sec_obb"),
                W("sec/section_markdown", wid="sec_disclosures_sec_obb"),
                W("sec/section_markdown", wid="sec_legal_proceedings_sec_obb"),
            ], w=20, h=16, first="sec_risk_factors_sec_obb"), W("sec/exhibit", 20, 16)],
            [W("sec/standardized_statement", 20, 16, wid="sec_income_statement_facts_sec_obb"),
             W("sec/standardized_statement", 20, 16, wid="sec_balance_sheet_facts_sec_obb")],
            [W("sec/standardized_statement", 20, 16, wid="sec_cash_flow_facts_sec_obb"), W("sec/as_filed_statement", 20, 16)],
        ], auto=[W("nasdaq/equity/filings_viewer", 20, 16), W("equity/compare/company_facts", 20, 16), "equity/fundamental/reported_financials"]),
        T("Shorts & Dark Pool", auto=["equity/shorts/*", "equity/darkpool/otc"]),
        T("Options", rows=[[W("derivatives/options/chains", 40, 16, provider="yfinance", groups=["Symbol"])]], auto=["cboe/options/term_structure", "cboe/options/smile", "cboe/options/surface", "cboe/options/stats"]),
    ], groups=[SYMBOL, REPORT], more=["equity/fundamental/*"]),

    D("eq-screen", "Screeners & Movers", "equities", "Find stocks: screeners, daily movers, sectors and industries, relative rotation, symbol lookup.", [
        T("Screener", rows=[[W("yfinance/screener_builder/view", 40, 30)], [W("equity/screener", 40, 20, provider="finviz")]]),
        T("Movers", rows=[MOVERS(18)],
          auto=["re:equity/discovery/(?!filings|latest|gainers|losers|active).*", "equity/market_snapshots"]),
        T("Sectors & Industries", rows=[[X("perf_bars", 13, 18, preset="us_sectors"), X("board", 14, 18, preset="us_sectors"), X("board", 13, 18, preset="us_industries")]],
          auto=["yfinance/sectors/*", "yfinance/industry/*"]),
        T("Themes", rows=[[X("perf_bars", 13, 22, preset="themes", period="1M"), X("board", 27, 22, preset="themes")],
                          [X("board", 14, 24, preset="ai_complex"), X("board", 13, 24, preset="datacenter_power"), X("board", 13, 24, preset="private_markets")],
                          [C("hyperscalers", "capex", 20, 18, period="ttm"), C("hyperscalers", "debt", 20, 18)],
                          [METRICS(HYPERSCALERS, "Valuation and margins: AI hyperscalers"), METRICS("NVDA,AVGO,AMD,MU,INTC,QCOM,TXN", "Valuation and margins: semiconductors")],
                          [X("board", 16, 18, preset="defense"), PERF("ITA,EUAD,XAR,SPY", "3Y", w=12, h=18, title="Normalized Performance: defense vs market"), M("defense_spending", 12, 18, since="2005")],
                          [X("board", 20, 18, preset="banks"), M("tech_investment", 20, 18)]]),
        # lookups and the institutional / insider searches: reference tools, all on one tab
        T("Lookup & Reference", auto=[W("equity/search", params={"query": "apple"}), W("yfinance/search", params={"query": "apple"}), W("nasdaq/markets/quotes", params={"symbol": "AAPL,MSFT,NVDA,AMZN,GOOGL"}),
                                       "equity/ownership/form_13f", "sec/institutions_search", "equity/ownership/government_trades", "regulators/sec/institutions_search",
                                       "nasdaq/markets/status", W("sec/sic_search", params={"query": "software"}), "sec/symbol_map", "sec/cik_map", "yfinance/private_companies", "finra/equity/securities",
                                       "re:regulators/sec/(symbol_map|cik_map|sic_search)", "sec/schema_files"]),
    ], more=["equity/discovery/*", "equity/*"]),

    D("eq-calendar", "Calendars & Events", "equities", "The economic calendar, earnings, dividends, splits, IPOs and newly filed reports.", [
        T("Economic", rows=[[W("economy/calendar", 40, 24, title="Economic calendar (upcoming events)", **ECON_CAL)]],
          auto=["federal_reserve/release_calendar", "usda/report_calendar"]),
        # the week ahead: earnings first (full width, by date), then dividends, splits and IPOs
        T("Corporate", rows=[[W("equity/calendar/earnings", 40, 20, provider="nasdaq", title="Earnings calendar (next 7 days)", sort="report_date:asc")]],
          auto=["equity/calendar/dividend", "equity/calendar/splits", "equity/calendar/ipo", "nasdaq/markets/upcoming", "equity/calendar/*",
                "equity/discovery/latest_financial_reports", "equity/discovery/filings"]),
    ]),

    D("eq-canada", "Canada (TMX)", "equities", "Toronto Stock Exchange: company pages, fundamentals, screener, movers, insiders, short interest and news.", [
        T("Company", auto=["tmx/equity/asset_info/view", "tmx/equity/fundamental/*", "tmx/equity/filings_view", "tmx/equity/ownership/insider_transactions"]),
        # Fund Info describes an ETF: pinned to XIU, not the dashboard's stock picker (review A3: it ran on BCE)
        T("Market", auto=["tmx/equity/screener_builder/view", "tmx/markets/*", "tmx/equity/rankings", W("tmx/etf/fund_info/view", params={"symbol": "XIU"}, groups=[]), "tmx/index/document_view"]),
        # ranked news on the TMX ticker (RY -> RY.TO: Canadian press, TMX releases), the issuer's own releases beside it, other TMX feeds in a library
        T("News", rows=[[NEWS(24, 24, subject="symbol", listing="TO", title="Company news"), W("tmx/news/feed", 16, 24, title="Issuer releases (TMX)")],
                        [LIB("More TMX news feeds", ["tmx/news/market", "tmx/news/search", "tmx/news/blog"], 40, 20, first="tmx/news/market")]]),
    ], groups=[dict(SYMBOL, value="RY")], more=["tmx/equity/*", "tmx/etf/*", "tmx/index/*", "tmx/markets/*", "tmx/news/*"]),

    D("eq-nordic", "Nordics (Nasdaq Nordic)", "equities", "Stockholm, Copenhagen, Helsinki and Iceland: screener, fundamentals, movers, bonds and news. Tickers look like VOLV B or NOVO B.", [
        # an index board first (Yahoo, keyless), then the exchange's own lists
        T("Market", rows=[[X("board", 16, 26, preset="nordics", groups=[]), PERF("^OMX,^OMXC25,^OMXH25,OBX.OL", "1Y", w=24, h=26, title="Normalized Performance: Nordic indices")]],
          auto=["nasdaq/nordic/movers", "nasdaq/nordic/screener", "nasdaq/nordic/news"]),
        T("Company", auto=["nasdaq/nordic/info", "nasdaq/nordic/fundamentals", "nasdaq/nordic/historical", "nasdaq/nordic/historical_trades", "nasdaq/nordic/dividends"]),
        T("Rates & Derivatives", auto=["nasdaq/nordic/bond_yields", "nasdaq/nordic/mortgage_rates", "nasdaq/nordic/index_factors", "nasdaq/nordic/derivatives_volume", "nasdaq/nordic/knocked_out"]),
        T("Reference", auto=["nasdaq/nordic/*"]),
    ], groups=[dict(SYMBOL, value="VOLV B")]),

    # ============================================================ INDICES / ETFs / FUNDS
    D("indices", "Indices", "funds", "Index levels, history, constituents, valuation multiples and volatility.", [
        T("World Indices", rows=[[X("board", 16, 26, preset="global_indices"), PERF("^GSPC,^STOXX50E,^FTSE,^N225,^HSI", "1Y", w=24, h=26, title="Normalized Performance: world indices")],
                                 [X("board", 20, 16, preset="us_equity"), X("perf_bars", 20, 16, preset="global_indices", period="YTD")]]),
        T("History & Constituents", rows=[[W("index/price/historical", 40, 20, chart=True, params={"symbol": "^GSPC"}, title="S&P 500 price history")]],
          auto=["index/price/historical", "index/snapshots", W("index/constituents", title="Index constituents (FTSE 100; Cboe Europe indices)"),
                W("index/sectors", title="Index sector weights (S&P/TSX)"), "index/available", "index/search", "cboe/index/documents_viewer"]),
        T("Valuation", rows=[[PERF("RSP,SPY,QQQ,IWM", "3Y", w=22, h=18, title="Normalized Performance: equal weight vs cap weight"), X("board", 18, 18, preset="megacaps")],
                             [X("sp500_multiples", 40, 18)]],
          auto=["economy/share_price_index", "economy/risk_premium"]),
        T("Volatility", rows=[[X("board", 16, 16, preset="volatility"), PERF("^VIX,^VVIX,^MOVE", "1Y", w=24, h=16, title="Normalized Performance: volatility indices")],
                              [W("derivatives/futures/curve", 20, 16, provider="cboe", params={"symbol": "VX"}, title="VIX futures curve", plot={"x": "expiration", "ys": ["price"], "split": "", "type": "line"}),
                               W("cboe/futures/settlement_prices", 20, 16)]]),
    ], more=["index/*"]),

    D("etfs", "ETFs & Mutual Funds", "funds", "ETF profiles, holdings, exposures and flows; mutual-fund analytics; fund regulatory filings.", [
        # holdings, sector and country weights take the source that covers the ETF (Yahoo/SEC for SPY, TMX for XIU.TO)
        T("ETF Profile", rows=[[W("etf/historical", 24, 18, chart=True, title="ETF price chart"), W("etf/info", 16, 18)],
                               [W("etf/holdings", 20, 14, provider="tmx"), W("etf/sectors", 20, 14, sort="weight:desc")],
                               [W("yfinance/funds/performance", 20, 14, title="ETF performance"), W("yfinance/funds/risk", 20, 14, title="ETF risk vs benchmark")],
                               [W("etf/historical", 24, 14, title="ETF price history"), W("etf/countries", 16, 14)]],
          auto=["etf/price_performance"]),
        T("ETF Search & Movers", rows=[[X("board", 24, 18, preset="major_etfs", title="Major ETFs (USD)"), X("perf_bars", 16, 18, preset="us_sectors", period="1D", title="Sector ETFs: 1D ranking")],
                                       [W("etf/search", 22, 16, provider="tmx", title="ETF search"), W("etf/equity_exposure", 18, 16, params={"symbol": "AAPL"}, groups=[], title="ETF exposure by stock")]],
          auto=[W("etf/discovery/gainers", title="ETF movers (gainers, losers, most active)"), "etf/discovery/*"], groups=[]),
        T("Mutual Funds", rows=[[W("yfinance/funds/historical", 22, 18, title="Fund price chart", plot={"x": "date", "ys": ["close"], "split": "", "type": "line"}, view="chart"),
                                 W("yfinance/funds/info", 18, 18, title="Fund profile")],
                                [W("yfinance/funds/performance", 20, 14, title="Fund performance"), W("yfinance/funds/holdings", 20, 14, title="Top holdings")],
                                [W("yfinance/funds/allocation", 14, 14, title="Fund allocation"), W("yfinance/funds/risk", 13, 14, title="Risk vs benchmark"), W("yfinance/funds/ratings", 13, 14, title="Morningstar ratings")]],
          auto=["yfinance/funds/*"], groups=["Fund"]),
        T("Fund Filings (N-PORT)", rows=[[X("etf_overlap", 20, 18), W("sec/nport_fund_metrics", 20, 18, title="Fund performance & flows (N-PORT)")],
                                         [W("etf/nport_disclosure", 40, 18, title="Full portfolio holdings (N-PORT)", sort="weight:desc")]],
          groups=["Symbol"]),
    ], groups=[dict(SYMBOL, value="SPY"), {"name": "Fund", "paramName": "symbol", "value": "VFIAX"}], more=["etf/*"]),

    # ============================================================ RATES & CREDIT
    D("rates", "Rates & Curves", "rates", "Government yield curves by country, policy rates, money markets, swaps and policy expectations.", [
        T("Global", rows=[[X("global_yields", 24, 22), X("yield_curves", 16, 22)],
                          [X("policy_rates", 14, 20), X("policy_rate_history", 13, 20), X("rates_by_country", 13, 20)]], groups=[]),
        T("By Country", rows=[[X("yield_curves", 24, 20, compare="1M,1Y", title="Yield curve: now and earlier"), X("yield_curve_table", 16, 20)],
                              [W("x:policy_rate_history", 20, 16, params={"start": "2005"}, compact=True, title="Policy rate history"), X("rate_history", 20, 16, duration="long", title="10-Year Yield History (monthly)")],
                              [X("rate_history", 20, 14, duration="short", title="3-Month Rate History (monthly)"),
                               X("cpi_history", 20, 14, start="2015", title="Consumer Prices (year over year, IMF)")]], groups=["Country"]),
        T("United States", rows=[[W("fixedincome/government/yield_curve", 22, 18, chart=True, provider="federal_reserve"), W("fixedincome/government/treasury_rates", 18, 18)],
                                 [M("treasury_yields", 14, 16), M("curve_spreads", 13, 16), M("real_yields", 13, 16)],
                                 [X("board", 20, 14, preset="us_rates"), W("fixedincome/government/tips_yields", 20, 14)]],
          # auctions live once, in Government Finance > Auctions
          auto=["fixedincome/government/treasury_prices", "fixedincome/government/svensson_yield_curve", "fixedincome/spreads/*"], groups=[]),
        T("Euro Area", rows=[[W("fixedincome/government/yield_curve", 22, 18, chart=True, provider="ecb"), W("ecb/key_interest_rates", 18, 18)],
                             [X("rate_history", 20, 16, country="germany,france,italy,spain", duration="long", start="2015", title="Euro-area 10-year yields (monthly, OECD)", groups=[]),
                              M("euro_spreads", 20, 16, since="2015")]],
          auto=["fixedincome/rate/estr", "fixedincome/rate/ecb", W("ecb/mfi_interest_rates", params={"symbol": "household_loans_for_house_purchase"}), "ecb/eligible_assets"], groups=[]),
        # SOFR and fed funds as cards; every other money-market and policy rate in one library card
        T("Money Markets", rows=[[M("policy_corridor", 20, 16, since="2022"), M("fed_liquidity", 20, 16)], [M("fed_balance_sheet", 13, 16), M("cb_balance_sheets", 14, 16, since="2019"), M("money_supply", 13, 16)],
                                 [W("fixedincome/rate/sofr", 13, 18), W("fixedincome/rate/effr", 13, 18),
                                  LIB("Money-market and policy rates", ["fixedincome/rate/*"], 14, 18, first="fixedincome/rate/iorb")]],
          auto=["fixedincome/corporate/commercial_paper", W("federal_reserve/money_market_funds", title="Money market fund holdings (USD)")], groups=[]),
        # review G2: the curves open as curves (rate in percent by tenor in years), not as dense tables
        T("Swaps & OIS", auto=[W("cftc/ois_curve", plot={"x": "tenor_years", "ys": ["rate"], "split": "", "type": "line"}),
                               W("cftc/ois_forward_curve", plot={"x": "start_years", "ys": ["forward_rate"], "split": "", "type": "line"}), "cftc/ois_curve_history", W("cftc/ois_policy_path", sort="start_date:asc"), "cftc/swap_summary", "cftc/swap_valuation", "cftc/swap_trades", "cftc/historical_fixings"], groups=[]),
        # WIRP: the OIS-implied policy path first (keyless, CFTC swap reports), then market inflation and curve shape
        # HANDOFF P3-3: six central banks, today vs 1W / 1M ago, BIS policy rate as anchor (terminal/policy_path.py)
        # owner 2026-11-19: the FOMC documents (statements, minutes, projections, newest first) beside the market-implied path
        T("Policy Expectations", rows=[[X("policy_path", 22, 18), X("policy_path_table", 18, 18)],
                                        [W("federal_reserve/fomc_documents_download", 40, 26, title="FOMC documents: statements, minutes, projections (newest first)")],
                                        [W("federal_reserve/sf/term_premium", 20, 16, params={"start_date": "2015-01-01"}, title="10-year yield, expected short rate and term premium (SF Fed model, %)",
                                           plot={"x": "date", "ys": ["yield_zero_coupon", "expected_short_rate", "term_premium"], "split": "", "type": "line"}),
                                         W("federal_reserve/sf/proxy_funds_rate", 20, 16, params={"start_date": "2015-01-01"}, title="Proxy funds rate vs effective fed funds (SF Fed, %)",
                                           plot={"x": "date", "ys": ["proxy_funds_rate", "effective_funds_rate"], "split": "", "type": "line"})]],
          auto=["federal_reserve/atlanta/market_probability", "federal_reserve/atlanta/taylor_rule",
                                        "federal_reserve/sf/short_rate_path", "federal_reserve/kc/natural_rate", "federal_reserve/kc/policy_rate_uncertainty",
                                        "fixedincome/rate/effr_forecast"], groups=[]),
    ], groups=[COUNTRY], more=["fixedincome/government/*", "fixedincome/rate/*"]),

    D("credit", "Credit", "rates", "Credit spreads by rating and region, sovereign spreads, private credit and loans, corporate debt, bank lending, stress, CDS index trades and bond lookup.", [
        # Spreads first: credit ETFs (keyless) on top, then ICE BofA option-adjusted spreads (FRED) by rating, region and euro sovereigns
        T("Spreads", groups=[], rows=[[X("board", 14, 18, preset="fixed_income"), PERF("LQD,HYG,EMB,BKLN,TLT", "1Y", w=26, h=18, title="Normalized Performance: credit vs Treasuries")],
                           [M("credit_spreads", 14, 16, since="2019"), M("ig_by_rating", 13, 16, since="2023"), M("hy_by_rating", 13, 16, since="2023")],
                           [X("cds_indices", 14, 16), X("cds_history", 13, 16), M("credit_regions", 13, 16, since="2023")]],
          auto=["fixedincome/bond_indices", W("fixedincome/corporate/hqm", plot={"x": "maturity", "ys": ["rate"], "split": "", "type": "line"}), "fixedincome/corporate/spot_rates", "fixedincome/mortgage_indices", "cftc/cds_index_trades"]),
        T("Private Credit & Loans", groups=[], rows=[[X("board", 15, 26, preset="private_markets"), PERF("BIZD,BKLN,JAAA,HYG,PSP", "3Y", w=25, h=26, title="Normalized Performance: BDCs, loans, CLOs, high yield, listed PE")],
                                          [M("bank_lending", 14, 18), C("alt_managers", "revenue", 13, 18, period="annual"), METRICS("ARCC,OBDC,BXSL,FSK,MAIN,GBDC", "Valuation and yield: BDCs", 13, 18)],
                                          [METRICS("BX,KKR,APO,ARES,OWL,CG", "Valuation and margins: alternative managers", 20, 16), M("distressed_spreads", 20, 16)]]),
        T("Corporate Debt", groups=[], rows=[[M("corporate_yields", 14, 18), M("corporate_debt", 13, 18, since="2000"), C("hyperscalers", "debt", 13, 18)],
                                  [C("megacaps", "interest_expense", 14, 16, period="annual"), C("megacaps", "buybacks", 13, 16, period="ttm"), M("corporate_profits", 13, 16)]]),
        # bank lending and financial stress on one tab: charts first, the source tables in two library cards
        T("Lending & Stress", groups=[], rows=[[M("bank_lending", 14, 18), M("lending_standards", 13, 18), M("delinquencies", 13, 18)],
                                    [M("financial_conditions", 20, 16), W("federal_reserve/ny/corporate_bond_distress", 20, 16, params={"start_date": "2000-01-01"}, title="Corporate bond distress index (NY Fed; 0-1, percentile of history)",
                                                                         plot={"x": "date", "ys": ["market", "investment_grade", "high_yield"], "split": "", "type": "line"})],
                                    [LIB("Bank lending surveys and conditions", ["economy/survey/sloos", "federal_reserve/ny/consumer_credit_access", "federal_reserve/dallas/banking_conditions",
                                                                                  "ecb/mfi_interest_rates"], 20, 22),
                                     LIB("Financial stress indices (regional Feds)", ["federal_reserve/kc/financial_stress_index", "federal_reserve/chicago/financial_conditions",
                                                                                       "federal_reserve/cleveland/systemic_risk", "federal_reserve/ny/corporate_bond_distress",
                                                                                       "federal_reserve/kc/risk_index", "federal_reserve/stl/national_index"], 20, 22)]]),
        # HANDOFF P3-1: company -> FINRA issuer name, filtered and typed (terminal/bonds.py); the raw TRACE card stays last
        # Issuer drives the table and the curve together; clicking a CUSIP (table row or curve point) sets Bond, which drives
        # the trade history; a new issuer clears Bond so the history never shows the previous issuer's bond
        T("Bond Lookup (TRACE)", rows=[[X("bond_lookup", 24, 18, groups=["Issuer"]), X("bond_curve", 16, 18, groups=["Issuer"])],
                                       [X("bond_pricer", 20, 16, groups=["Bond"]), X("debt_maturity_wall", 20, 16, groups=["Issuer"])]], cols=1, h=18, auto=[
            W("finra/fixedincome/bond_historical", groups=["Bond"], plot={"x": "date", "ys": ["price"], "split": "", "type": "line"}, sort="date:desc", title="Bond price history (TRACE, the bond clicked above)"),
            W("fixedincome/corporate/bond_prices", provider="finra", only=["finra"], groups=[], params={"issuer_name": "APPLE INC", "limit": 1000}, title="TRACE bond prices (raw, by issuer name)"),
            W("finra/fixedincome/bonds", groups=[], manual="The full FINRA bond list is about 150 MB (300,000 bonds) and cannot be filtered. Use the issuer filter in the card above, or load everything:")],
          groups=["Issuer", "Bond"]),
    ], groups=[{"name": "Issuer", "paramName": "company", "value": "AAPL"},
               {"name": "Bond", "paramName": "cusip", "value": None, "resetWith": "Issuer",
                "prompt": "Click a CUSIP in the bonds table, or a point on the issuer yield curve, to load that bond's trade history."}],
       more=["fixedincome/*", "finra/fixedincome/*"]),

    D("govfin", "Government Finance", "rates", "U.S. public debt, the Treasury's daily cash position, auctions, Fed balance sheet and primary dealers.", [
        # Treasury's own daily data (keyless) first, then the FRED history charts
        T("Debt & Cash", rows=[[W("ustreasury/debt_to_penny", 20, 14), W("ustreasury/daily_statement", 20, 14, title="Treasury cash (TGA closing balance, Daily Treasury Statement)",
                                                                                     plot={"x": "date", "ys": ["Treasury General Account (TGA) Closing Balance (Open Today)"], "split": "", "type": "line"})],
                               [M("federal_debt", 13, 16, since="1990"), M("federal_deficit", 14, 16, since="1990"), M("federal_interest_tariffs", 13, 16, since="2000")],
                               [M("defense_spending", 20, 16, since="2000"), M("trade_uncertainty", 20, 16, since="2015")]],
          auto=["ustreasury/bulletin"]),
        T("Auctions", auto=[W("ustreasury/auction_results", sort="auction_date:desc"), "ustreasury/upcoming_auctions",
                            "ustreasury/weekly_bill_offerings", W("fixedincome/government/treasury_auctions", sort="auction_date:desc")]),
        # review H1: dealer fails open as the two totals over time (fails to deliver / to receive; the table is a click away)
        T("Fed Balance Sheet & Dealers", auto=["economy/central_bank_holdings", "economy/primary_dealer_positioning",
                                                W("economy/primary_dealer_fails", plot={"x": "date", "ys": ["FTD Total", "FTR Total"], "split": "", "type": "line"}),
                                                # review H1: the newest month, largest holders first
                                                W("federal_reserve/international_portfolio_investment",
                                                  sort="date:desc,Foreign residents' holdings of total U.S. long-term securities:desc")]),
    ]),

    # ============================================================ DERIVATIVES
    D("options", "Equity Options", "derivatives", "Option chains, volatility surfaces, smiles and term structure, and strategy pricing.", [
        # the chain with open interest and the term structure first; smile and surface on Volatility
        T("Chain", rows=[[W("derivatives/options/chains", 40, 22, provider="yfinance", groups=["Symbol"])], [W("cboe/options/stats", 20, 16), W("cboe/options/term_structure", 20, 16)]]),
        # Stage 3 10a: where dealer hedging concentrates, and today's realised and implied volatility against the past year
        T("Volatility", rows=[[X("gex_strike", 22, 16), X("vol_cone", 18, 16)]], auto=["cboe/options/smile", "cboe/options/surface"]),
        # one card per analysis; its source follows the ticker (Cboe for U.S. listings, TMX for .TO, Deribit for BTC/ETH).
        # The market-wide Intrinio scans need a paid key: last on the tab.
        T("Strategies", auto=["cboe/options/straddle", "cboe/options/strangle", "cboe/options/spreads", "cboe/options/payoff", "cboe/options/trade_optimizer",
                              "tmx/derivatives/options/covered_calls", W("derivatives/options/snapshots", groups=[]), W("derivatives/options/unusual", groups=[])]),
    ], groups=[dict(SYMBOL, value="AAPL")], more=["cboe/options/*", "derivatives/options/*"]),

    # Swaps (SDR) and FX derivatives live in Rates & Curves and Currencies; CDS index trades in Credit
    D("futures", "Futures & Positioning", "derivatives", "Front-month futures across equity indices, Treasuries, currencies, commodities and crypto; "
      "VIX and Fed funds curves; history, contract reference, and Commitments of Traders positioning.", [
        # cross-asset first: the 1D column is the move since the last settlement (Sunday evening: since Friday), the bars rank it
        T("Futures", columns=[
            (16, [X("board", h=32, preset="futures")]),
            (24, [X("perf_bars", h=14, preset="futures", period="1D", title="Ranking: futures moves"),
                  W("derivatives/futures/historical", None, 18, provider="yfinance", params={"symbol": "ES"}, title="Futures price history",
                    tparam={"param": "symbol", "labels": {"ES": "E-mini S&P 500", "NQ": "E-mini Nasdaq-100", "RTY": "E-mini Russell 2000", "ZN": "10-year T-note",
                                                          "ZB": "30-year T-bond", "6E": "Euro FX", "6J": "Japanese yen", "CL": "WTI crude", "GC": "Gold",
                                                          "BTC": "CME bitcoin"}})]),
        ], rows=[[W("derivatives/futures/curve", 14, 16, provider="cboe", params={"symbol": "VX"}, title="VIX futures curve (term structure)",
                    plot={"x": "expiration", "ys": ["price"], "split": "", "type": "line"}),
                  W("derivatives/futures/curve", 13, 16, provider="yfinance", params={"symbol": "ZQ"}, title="Fed funds futures: implied policy rate by month (%)",
                    plot={"x": "expiration", "ys": ["implied_rate"], "split": "", "type": "line"}, only=["yfinance"]),
                  CURVE("CL", "Crude oil futures curve (WTI)", 13, 16)]],
          auto=["cboe/futures/settlement_prices", "derivatives/futures/instruments"]),
        # the positioning index (spec score, zones) first; the raw report last. Commodity reports sit on Commodities, Energy, Agriculture.
        T("Positioning (COT)", rows=[[W("cftc/cot_index", 24, 18, title="COT positioning index (all markets)"), W("cftc/cot_movers", 16, 18)],
                                     [W("cftc/cot_search", 14, 16, params={"query": "S&P 500"}), W("cftc/cot", 26, 16, params={"code": "13874A"}, title="Commitments of Traders report",
                                                                                                                       tparam={"param": "code", "labels": {
                                                                                                                           "13874A": "13874A · E-mini S&P 500", "209742": "209742 · E-mini Nasdaq-100",
                                                                                                                           "043602": "043602 · 10-year T-note", "099741": "099741 · Euro FX",
                                                                                                                           "097741": "097741 · Japanese yen", "067651": "067651 · WTI crude"}})]],
          auto=[W("cftc/cot_positioning", plot={"x": "report_date", "ys": ["large_specs_net_pct_oi", "commercials_net_pct_oi", "small_specs_net_pct_oi"], "split": "", "type": "line"})]),
    ], more=["derivatives/futures/*", "cftc/*"]),

    # ============================================================ FX
    D("fx", "Currencies", "fx", "Spot boards, currency strength, cross-rate matrix, policy-rate differentials, history, real effective rates, reserves, forwards, implied volatility and positioning.", [
        T("Board", columns=[
            (20, [X("board", h=26, preset="fx", cols=["name", "pair_last", "pair_pct", "usd_pct", "usd_ytd"]),
                  X("perf_bars", h=18, title="Ranking: currencies vs USD", preset="fx", period="1M")]),
            (20, [X("fx_moves", h=22, period="1D"), X("fx_trend", h=22, period="3M")]),
        ], rows=[[X("fx_matrix", 40, 14)],
                 [X("policy_rates", 18, 16, title="Policy rates and differential vs the Fed"), X("fx_rate_diff", 22, 16)],
                 [NEWS(40, 14, subject="eurusd", title="Currency news", groups=[])]]),
        T("History & Reference", rows=[[W("currency/price/historical", 26, 20, chart=True, params={"symbol": "EURUSD"}), M("dollar", 14, 20, since="2006")],
                                       [M("reer", 20, 16, since="2010"), M("fx_reserves", 20, 16, since="2010")]],
          auto=["currency/reference_rates", "currency/snapshots", "currency/search"]),
        T("Forwards, Vol & Positioning", rows=[[W("cftc/cot_index", 40, 16, params={"asset_class": "currencies"}, title="Positioning: currencies (COT index, high = long the named currency)")]],
          auto=["cftc/fx_forward_curve", "cftc/fx_forward_points", "cftc/fx_implied_vol", "cftc/fx_forward_trades", "cftc/fx_option_trades"]),
    ], more=["currency/*"]),

    # ============================================================ COMMODITIES
    D("commodities", "Commodities", "commodities", "Futures boards, spot prices, curves, positioning and official outlooks.", [
        T("Board", rows=[[X("board", 16, 26, preset="commodities"), PERF("CL=F,BZ=F,NG=F,GC=F,HG=F", "1Y", w=24, h=26, title="Normalized Performance: key commodities")],
                         [X("perf_bars", 20, 20, preset="commodities", period="1M"), NEWS(20, 20, subject="gold", title="Commodity news", groups=[])]]),   # YTD bars: the 1M card's period chips
        T("Prices & Curves", rows=[[CURVE("CL", "Crude oil futures curve (WTI)"), CURVE("NG", "Natural gas futures curve (Henry Hub)")],
                                   [CURVE("GC", "Gold futures curve"), CURVE("HG", "Copper futures curve")]],
          auto=[M("oil_prices", since="2010"), M("gas_europe_us", since="2021"), M("uranium", since="2022"), "commodity/price/spot"]),
        T("Positioning", rows=[[W("cftc/cot_index", 24, 16, params={"asset_class": "hard_commodities"}, title="COT positioning index: energy and metals"),
                                 W("cftc/cot_movers", 16, 16, params={"asset_class": "hard_commodities"}, title="COT largest changes: energy and metals")],   # review F2/H3: was "all"
                               [W("cftc/cot_search", 14, 16, params={"query": "gold"}), W("cftc/cot", 26, 16, params={"code": "088691"}, title="Commitments of Traders report (gold)")]]),
    ], more=["commodity/*"]),

    D("energy", "Energy", "commodities", "Oil, gas, power and uranium: prices, inventories against their seasonal range, refining margins, global supply, shipping chokepoints, geopolitical risk and outlooks.", [
        T("Overview", columns=[
            (14, [X("board", h=30, preset="energy")]),
            (13, [M("oil_prices", h=15, since="2020"), M("crack_spreads", h=15, since="2022")]),
            (13, [M("gas_europe_us", h=15, since="2021"), M("chokepoint_tankers", h=15, since="2023")]),
        ], rows=[
            [M("crude_stocks_seasonal", 20, 16), M("natgas_storage_seasonal", 20, 16)],
            [M("oil_volatility", 14, 16, since="2021"), CURVE("CL", "Crude oil futures curve (WTI)", 13, 16),
             NEWS(13, 16, subject="crude", title="Oil news", groups=[])],
        ]),        # the gas curve stays on Natural Gas & LNG
        T("Oil", rows=[[M("oil_prices", 20, 16, since="2015"), M("brent_wti", 20, 16, since="2020")],
                       [M("crack_spreads", 14, 16, since="2021"), M("pump_prices", 13, 16, since="2020"), M("refinery_utilization", 13, 16)],
                       [M("crude_stocks", 14, 16, since="2015"), M("cushing_seasonal", 13, 16), M("us_oil_flows", 13, 16, since="2018")],
                       [M("gasoline_stocks_seasonal", 14, 16), M("distillate_stocks_seasonal", 13, 16), M("oil_demand", 13, 16, since="2018")],
                       [CURVE("CL", "Crude oil futures curve (WTI)", 20, 16),
                        W("cftc/cot", 20, 16, params={"code": "067651"}, title="Positioning: WTI crude (Commitments of Traders)")],
                       [W("commodity/petroleum_status_report", 40, 18, title="EIA Weekly Petroleum Status Report")]]),
        T("Natural Gas & LNG", rows=[[M("gas_europe_us", 20, 16, since="2019"), M("natgas_storage_seasonal", 20, 16)],
                                     [M("lng_exports", 14, 16, since="2016"), M("gas_supply", 13, 16, since="2016"), M("natgas_price", 13, 16, since="2015")],
                                     [CURVE("NG", "Natural gas futures curve (Henry Hub)", 20, 16),
                                      W("cftc/cot", 20, 16, params={"code": "023651"}, title="Positioning: natural gas (Commitments of Traders)")]]),
        T("Power & Uranium", rows=[[X("board", 14, 22, preset="datacenter_power"), M("electricity_prices", 13, 22, since="2015"), M("power_output", 13, 22, since="2015")],
                                   [M("construction", 20, 16, since="2015"), M("uranium", 20, 16, since="2022")],
                                   # review H3: the lower 48 as a whole, plotted; fuels by name (type_name), not EIA codes (BAT, COL)
                                   [W("eia/electricity_grid/demand", 20, 16, title="U.S. lower-48 demand, forecast and generation (hourly, MWh)", params={"respondent": "united_states_lower_48"},
                                      plot={"x": "date", "ys": ["value"], "split": "series_type_name", "type": "line"}),
                                    W("eia/electricity_grid/generation_by_fuel", 20, 16, title="U.S. lower-48 generation by fuel (hourly, MWh)", params={"respondent": "united_states_lower_48"},
                                      plot={"x": "date", "ys": ["value"], "split": "type_name", "type": "line"})],
                                   [W("eia/nuclear_outages/us_nuclear_outages", 20, 14, title="U.S. nuclear outages")]]),
        T("Global Supply & Geopolitics", rows=[[X("chokepoint_monitor", 24, 18), M("chokepoint_tankers", 16, 18, since="2023")],
                                               [M("oil_producers", 20, 16, since="2019"), M("world_oil_balance", 20, 16, since="2019")],
                                               [M("red_sea_diversion", 20, 16, since="2023"), M("gpr_daily", 20, 16, since="2019")],
                                               [X("board", 14, 18, preset="shipping"), W("imf/portwatch/disruption_events", 26, 18, title="Current shipping and port disruptions")],
                                               [W("jodi/oil/production", 20, 16, title="Crude production by country (JODI)"), W("jodi/oil/exports", 20, 16, title="Crude exports by country (JODI)")]]),
        T("Outlooks & Reports", rows=[[M("oil_price_forecast", 14, 16, since="2022"), M("oecd_oil_stocks", 13, 16, since="2019"), M("gas_price_forecast", 13, 16, since="2022")]],
          auto=["commodity/short_term_energy_outlook", "eia/aeo", "eia/ieo", "eia/rss_feed", "federal_reserve/dallas/energy_survey", "federal_reserve/dallas/breakeven_prices",
                "re:federal_reserve/kc/.*energy.*"]),
        T("Data Library", rows=[[LIB("Petroleum (EIA)", ["eia/petroleum/*"], 20, 26, first="eia/petroleum/prices/weekly_retail_gasoline_and_diesel_prices"),
                                 LIB("Natural gas (EIA)", ["eia/natural_gas/*"], 20, 26, first="eia/natural_gas/storage/weekly_working_gas_in_underground_storage")],
                                [LIB("Electricity, grid and nuclear (EIA)", ["eia/electricity_grid/*", "eia/electricity/*", "eia/nuclear_outages/*", "eia/state_electricity_profiles/*"], 20, 26),
                                 LIB("Coal and biomass (EIA)", ["eia/coal/*", "eia/densified_biomass/*"], 20, 26)],
                                [LIB("World oil and gas statistics (JODI)", ["jodi/oil/*", "jodi/gas/*"], 20, 26),
                                 LIB("EIA overview, international and data browsers", ["eia/total_energy", "eia/international", "eia/seds", "eia/crude_oil_imports", "eia/*@Data Browsers", "eia/*@Reference"], 20, 26)]]),
    ], more=["eia/*", "jodi/*"]),
    D("agri", "Agriculture & Food", "commodities", "Grain, softs and livestock futures, positioning, world supply and demand (WASDE/PSD), and USDA and Federal Reserve farm data in library cards.", [
        T("Markets", rows=[[X("board", 16, 30, preset="agriculture"), PERF("ZC=F,ZW=F,ZS=F,KC=F,CC=F,LE=F", "1Y", w=24, h=30, title="Normalized Performance: corn, wheat, soybeans, coffee, cocoa, cattle")],
                           [CURVE("ZS", "Soybean futures curve", 20, 16),
                            CURVE("KC", "Coffee futures curve", 20, 16)],
                           [W("cftc/cot_index", 24, 16, params={"asset_class": "soft_commodities"}, title="COT positioning index: agriculture and softs"),
                            W("cftc/cot", 16, 16, params={"code": "002602"}, title="Commitments of Traders report (corn)")]]),
        T("Supply & Demand", auto=["commodity/psd_data", "commodity/psd_report", "usda/bell_report", "usda/report_calendar", "usda/publication_documents", "usda/ers_chart", "commodity/weather_bulletins_download"]),
        # USDA ERS publishes ~60 similar datasets: one library card per topic instead of tabs of cards
        T("USDA Data Library", rows=[
            [LIB("Crops, oilseeds, sugar, bioenergy and fertilizer", ["re:usda/(wheat_data|rice_yearbook|feed_grains_database|oil_crops_yearbook|cotton_wool_and_textile_data|sugar_sweeteners_yearbook|fruit_and_tree_nuts_data|vegetables_and_pulses|season_average_price_forecasts|adoption_of_genetically_engineered_crops|us_bioenergy_statistics|fertilizer_use_and_price)"], 20, 26, first="usda/feed_grains_database"),
             LIB("Livestock, meat and dairy", ["re:usda/(livestock.*|dairy_data|milk_cost_of_production|meat_price_spreads)"], 20, 26)],
            [LIB("Agricultural trade and world markets", ["re:usda/(agricultural_trade.*|state_agricultural_trade|us_food_imports|international_.*|global_food_assessment|agricultural_exchange_rates)"], 20, 26),
             LIB("Food prices, spending and consumption", ["re:usda/(food_.*|price_spreads|fruit_and_vegetable_prices|purchase_to_plate|normalized_prices|cost_estimates_of_foodborne_illnesses|eating_and_health_module_atus|resource_requirements_of_food_demand|snap_policy_data_sets)"], 20, 26, first="usda/food_price_outlook")],
            [LIB("Farm income, costs, productivity and land", ["re:usda/(farm_.*|commodity_costs_and_returns|agricultural_productivity|agricultural_and_food_rd_expenditures|major_land_uses)"], 20, 26, first="usda/farm_income_and_wealth_statistics"),
             LIB("Rural economy and other USDA data", ["rest:usda/*"], 20, 26, first="usda/rural_urban_continuum_codes")],
            [LIB("Farm credit and farmland surveys (regional Feds)", ["re:federal_reserve/(kc/ag_.*|chicago/(ag_credit_conditions|farm_loan_rates|farmland_values)|dallas/agricultural_survey|minneapolis/.*ag.*)"], 40, 26)]]),
    ]),

    # ============================================================ CRYPTO
    D("crypto", "Crypto", "crypto", "Coin boards and history, plus Deribit order books, options, funding and volatility.", [
        # coins, then funding and implied volatility (what a crypto desk reads next to price)
        T("Board", rows=[[X("board", 16, 20, preset="crypto"), PERF("BTC-USD,ETH-USD,SOL-USD,XRP-USD", "1Y", w=24, h=20, title="Normalized Performance: major coins")],
                         [W("crypto/price/historical", 24, 20, chart=True, params={"symbol": "BTCUSD"}, title="Bitcoin price chart"), NEWS(16, 20, subject="btc", title="Crypto news", groups=[])]],
          auto=[X("crypto_vol", 20, 18), "deribit/volatility/*", "deribit/rates/*"]),     # review H4: DVOL against realised
        T("Market (Deribit)", auto=["deribit/market/ticker", "deribit/market/book_summary", "deribit/market/order_book", "deribit/market/trades", "deribit/market/*"]),
        T("Options (Deribit)", auto=["deribit/options/surface", "deribit/options/smile", "deribit/options/term_structure", "deribit/options/stats", "deribit/options/*"]),
        T("Indices & Reference", auto=["deribit/index/*", W("derivatives/futures/info", params={"symbol": "BTC-PERPETUAL"}, title="Perpetual contract details (BTC)"),
                                       W("crypto/search", params={"query": "bitcoin"}), "deribit/reference/*"]),
    ], more=["crypto/*", "deribit/*"]),

    # ============================================================ MACRO
    D("macro-country", "Economy by Country", "macro", "Growth, prices, jobs, rates and external accounts for any country, side by side.", [
        # ECST-style: every main economy on one screen first (keyless, one OECD request per 12 hours), then the picked countries
        T("Country Monitor", rows=[[X("country_monitor", 40, 16, groups=[])],
                                   [X("cpi_history", 20, 16, start="2018", title="Consumer prices, y/y % (IMF)"), W("x:policy_rate_history", 20, 16, params={"start": "2018"}, compact=True, title="Policy rate history")],
                                   [X("yield_curves", 20, 16, compare="1Y", title="Government yield curve: now and a year ago"), X("rate_history", 20, 16, duration="long", start="2010", title="10-year yields (monthly, OECD)")],
                                   [W("economy/indicators", 24, 16, provider="imf", params={"symbol": "WEO::NGDP_RPCH"}, title="Real GDP growth with IMF forecasts (World Economic Outlook, annual)"),
                                    NEWS(16, 16, subject="country", title="Country news")]]),
        T("Snapshot", auto=["economy/gdp/real", "economy/gdp/nominal", "economy/gdp/forecast", "economy/cpi", "economy/unemployment", "economy/interest_rates",
                            "economy/composite_leading_indicator", "economy/house_price_index", "economy/share_price_index", "economy/country_profile"]),
        T("External Accounts", auto=[W("economy/balance_of_payments", provider="ecb", groups=[], title="Euro-area balance of payments (ECB)"), "economy/direction_of_trade",
                                     W("economy/export_destinations", sort="value:desc"), W("federal_reserve/international_portfolio_investment", groups=[])]),
        # rates by country live in Rates & Curves > Global and By Country
        T("Geopolitical Risk", rows=[[X("gpr_table", 16, 22), M("gpr_countries", 24, 22, since="2020")], [M("gpr_daily", 20, 16, since="2015"), M("policy_uncertainty", 20, 16, since="2015")]], groups=[]),
        T("IMF & World Data", rows=[[W("economy/indicators", 40, 18, provider="imf", params={"symbol": "WEO::PCPIPCH"}, groups=["Country"],
                                       title="IMF World Economic Outlook: inflation with forecasts (any IMF symbol in settings)",
                                       plot={"x": "date", "ys": ["value"], "split": "country", "type": "line"})]],
          auto=[W("economy/available_indicators", provider="imf", params={"query": "inflation"}),
                "usda/international_macroeconomic_data_set", "re:federal_reserve/dallas/global_.*"], groups=[]),
    ], groups=[MACRO_COUNTRY]),

    D("macro-us", "U.S. Economy", "macro", "Growth nowcasts, inflation, jobs, productivity, consumers and housing, business surveys and every regional Federal Reserve bank.", [
        T("Overview", rows=[[M("gdpnow", 13, 16, since="2019"), M("inflation", 14, 16, since="2019"), M("unemployment", 13, 16, since="2015")],
                            [M("jobless_claims", 13, 16, since="2021"), M("retail_sales", 14, 16, since="2019"), M("consumer_sentiment", 13, 16, since="2015")],
                            [M("industry", 13, 16, since="2015"), M("corporate_profits", 14, 16, since="2015"), M("policy_uncertainty", 13, 16, since="2019")]]),
        T("Growth & Activity", rows=[[W("federal_reserve/philadelphia/gdpplus", 20, 16), W("federal_reserve/chicago/national_activity", 20, 16)],
                                     [M("tech_investment", 20, 16), W("federal_reserve/ny/supply_chain_pressure", 20, 16, params={"start_date": "2015-01-01"}, title="Global supply chain pressure index (NY Fed; standard deviations from average)",
                                                                          plot={"x": "date", "ys": ["gscpi"], "split": "", "type": "line"})],
                                     [LIB("Nowcasts, activity and recession indicators, forecaster surveys", [
                                         "federal_reserve/atlanta/gdpnow", "re:federal_reserve/ny/.*(nowcast|weekly_economic).*", "federal_reserve/richmond/recession_indicator",
                                         "federal_reserve/chicago/brave_butters_kelley", "federal_reserve/ny/supply_chain_pressure", "federal_reserve/sf/news_sentiment",
                                         "re:federal_reserve/philadelphia/(anxious_index|survey_professional_forecasters|livingston_survey|partisan_conflict)",
                                         "federal_reserve/atlanta/business_uncertainty", "federal_reserve/stl/fred_md", "federal_reserve/stl/fred_qd", "economy/total_factor_productivity"], 40, 24)]]),
        T("Inflation", rows=[[M("inflation", 20, 16, since="2015"), M("breakevens", 20, 16, since="2015")],
                             [LIB("CPI release tables (BLS)", ["bls/cpi/*", "economy/retail_prices"], 20, 24, first="bls/cpi/t3_special_aggregates"), LIB("PPI and import/export prices (BLS)", ["bls/ppi/*", "bls/import_export/*"], 20, 24)],
                             [W("federal_reserve/cleveland/inflation_nowcast", 20, 22, params={"frequency": "year"}, sort="date:desc", title="Inflation nowcast, year over year % (Cleveland Fed)",
                                cols=["date", "CPI Inflation", "Core CPI Inflation", "PCE Inflation", "Core PCE Inflation"]),
                              LIB("Underlying inflation and expectations (regional Feds, surveys)", ["federal_reserve/dallas/trimmed_mean_pce", "economy/pce",
                                 "re:federal_reserve/(cleveland/(inflation_.*|median_cpi.*)|atlanta/(sticky_cpi|business_inflation_expectations)|sf/(cyclical_pce|supply_demand_pce)|ny/core_trend_inflation|minneapolis/regional_cpi)",
                                 "economy/survey/inflation_expectations", "economy/survey/university_of_michigan"], 20, 22, first="federal_reserve/dallas/trimmed_mean_pce")]]),
        T("Jobs", rows=[[M("unemployment", 20, 16, since="2015"), M("jobless_claims", 20, 16, since="2021")], [M("continuing_claims", 20, 16, since="2021"), M("job_openings", 20, 16, since="2015")],
                        [LIB("Employment Situation tables (BLS)", ["bls/employment_situation/*", "economy/survey/nonfarm_payrolls", "bls/real_earnings/*"], 20, 24, first="bls/employment_situation/summary_establishment"), LIB("Job openings and turnover (JOLTS)", ["bls/jolts/*"], 20, 24, first="bls/jolts/beveridge_curve")],
                        [LIB("Labour-market indicators (regional Feds)", ["federal_reserve/atlanta/wage_growth",
                             "re:federal_reserve/(minneapolis/(job_openings_hiring|labor_force_participation|quits_rate|regional_employment|regional_unemployment)|kc/.*lmci.*|chicago/labor_market|richmond/non_employment_index|ny/consumer_labor_market)"], 20, 24),
                         LIB("Productivity and costs (BLS)", ["bls/productivity/*"], 20, 24)]]),
        T("Consumers & Housing", rows=[[M("consumer_health", 13, 16), M("consumer_sentiment", 14, 16), M("retail_sales", 13, 16)],
                                       [M("mortgage_rates", 13, 16), M("housing_activity", 14, 16), M("home_prices", 13, 16)],
                                       [M("cre_prices", 20, 16), M("delinquencies", 20, 16)],
                                       [LIB("Consumer, household and housing surveys (Fed banks)", ["re:federal_reserve/ny/(consumer_.*|household.*|sce.*)", "re:federal_reserve/.*(housing|home|retail).*"], 40, 24)]]),
        T("Business Surveys", rows=[[LIB("Manufacturing surveys (regional Feds)", ["re:federal_reserve/.*manufactur.*"], 20, 24),
                                     LIB("Services, CFO and other business surveys", ["re:federal_reserve/.*(service|business|cfo|survey|outlook|economic_conditions|economic_indicators).*"], 20, 24)]],
          auto=["economy/survey/*"]),
        # gauges whose regional-Fed pages moved or changed layout (parked in Data status), from their FRED copies
        T("Regional Feds", rows=[[M("sticky_cpi", 10, 16, since="2019"), M("wage_tracker", 10, 16, since="2015"), M("texas_manufacturing", 10, 16, since="2019"),
                                  M("global_activity", 10, 16, since="2015")],
                                 [LIB("New York Fed", ["federal_reserve/ny/*"], 20, 22, first="federal_reserve/ny/empire_state_manufacturing"), LIB("Atlanta Fed", ["federal_reserve/atlanta/*"], 20, 22)],
                                 [LIB("Chicago Fed", ["federal_reserve/chicago/*"], 20, 22, first="federal_reserve/chicago/national_activity"),
                                  LIB("Philadelphia Fed", ["federal_reserve/philadelphia/*"], 20, 22, first="federal_reserve/philadelphia/manufacturing_outlook")],
                                 [LIB("Dallas Fed", ["federal_reserve/dallas/*"], 20, 22, first="federal_reserve/dallas/weekly_economic_index"),
                                  LIB("Kansas City Fed", ["federal_reserve/kc/*"], 20, 22, first="federal_reserve/kc/financial_stress_index")],
                                 [LIB("Cleveland Fed", ["federal_reserve/cleveland/*"], 20, 22), LIB("Richmond Fed", ["federal_reserve/richmond/*"], 20, 22, first="federal_reserve/richmond/manufacturing_survey")],
                                 [LIB("San Francisco Fed", ["federal_reserve/sf/*"], 20, 22),
                                  LIB("Minneapolis, St. Louis and Boston Feds", ["federal_reserve/minneapolis/*", "federal_reserve/stl/*", "federal_reserve/boston/*"], 20, 22)]]),
    ], more=["bls/*"]),
    # owner 2026-11-19: built around what bank supervisors and credit analysts read; the Bank picker drives the Bank Lookup tab only
    D("banking", "Money & Banking", "macro", "Bank credit quality, loans and deposits, securitization, one bank against its peers, what bank supervisors publish, money and central-bank balance sheets.", [
        T("Credit Quality", rows=[[M("delinquencies", 14, 16, since="2007"), M("charge_offs", 13, 16, since="2007"), M("problem_loans", 13, 16, since="2007")],
                                  [W("federal_reserve/ny/household_debt", 20, 16, params={"table": "flow_into_serious_delinquency_by_loan_type"},
                                     title="Households: % of balances moving into 90+ day delinquency, by loan type (NY Fed, annualized)",
                                     plot={"x": "date", "ys": ["CC", "AUTO", "STUDENT LOAN", "MORTGAGE", "HELOC", "ALL"], "split": "", "type": "line"}),
                                   X("bank_failures", 20, 16)]], groups=[]),
        T("Loans & Deposits", rows=[[M("bank_lending", 14, 16, since="2015"), M("bank_funding", 13, 16, since="2015"), M("lending_standards", 13, 16, since="2007")],
                                    [M("securitization", 20, 16, since="2000"), M("cre_prices", 20, 16, since="2007")]], groups=[]),
        # one bank: its own credit quality history (FDIC), its ratios against its peer group (UBPR), the FFIEC executive summary
        T("Bank Lookup", rows=[[X("bank_quality", 20, 18), W("federal_reserve/ffiec/ubpr", 20, 18, title="Ratios against the peer group (UBPR: bank, peer group, percentile)")],
                               [W("federal_reserve/ffiec/executive_summary", 24, 20, title="Executive summary (FFIEC call report, last eight quarters)"),
                                W("federal_reserve/ffiec/large_holding_companies", 16, 20, groups=[], title="Largest U.S. bank holding companies")]], groups=["Bank"]),
        # what supervisors publish (newest first), the Fed release calendar, then the regional Fed and FFIEC report libraries
        T("Regulators & Reports", rows=[[NEWS(24, 24, subject="market", mode="monitor", feeds="banks", title="Bank supervisors: releases, rules and enforcement", groups=[]),
                                         W("federal_reserve/release_calendar", 16, 24)],
                                        [LIB("Regional Fed publications", ["federal_reserve/*/regional_publications_download"], 20, 26),
                                         LIB("Bank and holding-company reports (FFIEC)", ["rest:federal_reserve/ffiec/*"], 20, 26, groups=["RSSD"])]], groups=[]),
        T("Money", rows=[[M("fed_balance_sheet", 20, 16), M("fed_liquidity", 20, 16)], [M("money_supply", 13, 16), M("policy_corridor", 14, 16, since="2022"), M("cb_balance_sheets", 13, 16, since="2019")]],
          auto=["economy/money_measures", W("federal_reserve/money_market_funds", title="Money market fund holdings (USD)"), "economy/central_bank_holdings"], groups=[]),
        T("ECB", auto=["ecb/key_interest_rates", "ecb/eligible_assets", "ecb/release_document", "ecb/dataflow_information", W("ecb/mfi_interest_rates", params={"symbol": "household_loans_for_house_purchase"})], groups=[]),
    ], groups=[{"name": "Bank", "paramName": "symbol", "value": "JPM"},
               {"name": "RSSD", "paramName": "rssd_id", "value": "1039502", "syncFrom": "Bank", "syncUrl": "/x/bank_rssd"}]),

    D("trade", "Trade & Shipping", "macro", "Shipping chokepoints (Hormuz, Red Sea, Suez, Black Sea, Panama, Taiwan Strait), disruptions, ports, freight and shipping stocks, trade flows, tariffs and trade-policy uncertainty.", [
        T("Chokepoints", rows=[[X("chokepoint_monitor", 24, 18), M("chokepoint_tankers", 16, 18, since="2023")],
                               [M("red_sea_diversion", 20, 16, since="2023"), M("asia_straits", 20, 16, since="2023")],
                               [X("board", 16, 20, preset="shipping"), PERF("BWET,BDRY,ZIM,FRO,SPY", "1Y", w=24, h=20, title="Normalized Performance: freight and shipping")]],
          auto=["economy/shipping/chokepoint_info", "economy/shipping/chokepoint_volume"]),
        T("Disruptions", auto=["imf/portwatch/disruption_events", W("imf/portwatch/disruptions_map", chart=True), "imf/portwatch/disruption_sankey"]),
        T("Ports & Trade Volumes", auto=["economy/shipping/port_volume", W("economy/shipping/port_info", sort="vessel_count_total:desc"), "imf/portwatch/*"]),
        T("Trade Flows & Tariffs", rows=[[M("trade_balance", 13, 16, since="2015"), M("federal_interest_tariffs", 14, 16, since="2000"), M("trade_uncertainty", 13, 16, since="2015")],
                                         [M("dollar", 20, 16, since="2006")]],
          auto=["economy/direction_of_trade", W("economy/export_destinations", params={"country": "united_states"}, sort="value:desc", title="Main export destinations (change the country in settings)"),
                "economy/balance_of_payments", "usda/agricultural_trade"]),
    ]),
    D("explorers", "Data Explorers", "macro", "Search and pull any series from FRED, BLS, EIA, IMF, OECD, ECB and the Federal Reserve. Data status: what is down upstream and which keys are missing.", [
        # every explorer opens on a working example; change the settings to pull anything else
        T("FRED & BLS", auto=["economy/fred_search", W("economy/fred_series", params={"symbol": "DGS10,DGS2"}, title="FRED series: 10-year and 2-year Treasury yields"),
                              W("economy/survey/bls_search", params={"category": "cpi", "query": "gasoline"}, title="BLS search: CPI, gasoline"),
                              W("economy/survey/bls_series", params={"symbol": "CUSR0000SA0", "category": "cpi"}, title="BLS series: CPI, all items"),
                              W("economy/fred_release_table", params={"release_id": "10"}, title="FRED release table: CPI"),
                              W("fred/economy/release_table", params={"release_id": "10"}),
                              W("economy/fred_regional", params={"symbol": "SMU56000000500000001"}, title="FRED regional data: payroll employment by state")]),
        T("IMF", rows=[[W("imf/presentation_table", 40, 22, params={"dataflow_group": "irfcl", "table": "irfcl_reserve_assets", "country": "JPN", "frequency": "M"},
                          title="IMF tables: Japan's official reserve assets (any dataflow, table and country in settings)")], [LIB("IMF dataflows, tables and dimensions", ["imf/list_dataflows", "imf/list_tables", "imf/get_dataflow_dimensions", "imf_utils/*"], 40, 22)]]),
        T("OECD", auto=["oecd/*"]),
        T("ECB", rows=[[W("ecb/presentation_table", 40, 22)],
                       [LIB("ECB dataflows, concepts and tables", ["ecb/list_dataflows", "ecb/search_dataflows", "ecb/topic_dataflows", "ecb/list_concepts", "ecb/list_tables", "ecb/get_dataflow_dimensions", "ecb/dataflow_information"], 40, 22)]]),
        # EIA's data browsers are in Energy > Data Library
        T("Federal Reserve", auto=["federal_reserve/fed_data", "federal_reserve/list_datasets", "federal_reserve/list_releases"]),
    ], more=["imf/*", "ecb/*"]),

    # ============================================================ GOVERNMENT
    D("congress", "U.S. Congress", "policy", "Bills, laws, amendments, votes, members and committees.", [
        # How To Use beside the cards it explains (review H2)
        T("Legislation", auto=["uscongress/how_to_use", "uscongress/bills", "uscongress/laws", "uscongress/amendments", "uscongress/search", "uscongress/bill_info", "uscongress/bill_text", "uscongress/amendment_info", "uscongress/amendment_text"]),
        T("Members & Committees", auto=["uscongress/members", "uscongress/member_info", "uscongress/member_legislation", "uscongress/member_votes", "uscongress/committee_members", "uscongress/committee_info"]),
        T("Calendars & Reports", auto=["uscongress/*"]),
    ]),
    D("spending", "Federal Spending", "policy", "Where U.S. federal money goes: agencies, awards, loans and recipients (USAspending).", [
        T("Spending & Awards", auto=["ustreasury/spending_explorer", W("ustreasury/award_search", sort="award_amount:desc"),
                                      W("ustreasury/recipient_search", sort="amount:desc"), "ustreasury/award_info", "ustreasury/recipient_info",
                                      "ustreasury/award", "ustreasury/recipient_awards"]),
    ], more=["ustreasury/*"]),

    # ============================================================ NEWS & RESEARCH
    D("news", "News", "research", "Market, economy and company news; regional exchange feeds.", [
        # the situation room: every curated feed newest first (duplicates folded), official releases beside it; then the ranked view
        T("Markets", rows=[[NEWS(26, 30, subject="market", mode="monitor", title="Market monitor (newest first)", groups=[]),
                            NEWS(14, 30, subject="market", mode="monitor", feeds="official", title="Official releases (central banks, statistics, regulators)", groups=[])],
                           [NEWS(20, 22, subject="market", title="Top market news (most material first)", groups=[]),
                            NEWS(20, 22, subject="market", mode="monitor", feeds="wires", title="Wires & financial press (newest first)", groups=[])]]),
        # company news follows the Symbol at the top (ranked: issuer filings, press, home-market press); world news needs a paid FMP plan (last)
        T("Company & World", rows=[[NEWS(24, 24, subject="symbol", title="Company news"), LIB("Raw news feeds (unfiltered)", ["news/company", "news/rss", "yfinance/news"], 16, 24, first="news/company")]],
          auto=["news/world"]),
        T("Regional", rows=[[NEWS(20, 26, subject="market", mode="monitor", feeds="ea", title="Regional news monitor", groups=[])]],
          auto=["nasdaq/nordic/news"], groups=[]),
    ], groups=[dict(SYMBOL, value="AAPL")], more=["news/*"]),
    # SEC filings, plus the SEC's enforcement and adviser data (folded in from the former SEC & Regulation dashboard)
    D("filings", "SEC Filings & Enforcement", "research", "Search and read SEC filings (full text, EDGAR, viewer, sections), enforcement actions and investment advisers.", [
        T("Search & Read", rows=[
            [W("sec/company_filings", 20, 14),
             W("sec/full_text_search", 20, 14, params={"query": "artificial intelligence"}, title='Full-text search: "artificial intelligence"')],
            [W("sec/latest_financial_reports", 20, 14), W("sec/filing_headers", 20, 14)],
            [W("sec/filing_viewer_app", 40, 24)],
            [W("sec/exhibit", 20, 16), W("sec/htm_file", 20, 16)],
        ], auto=[W("sec/section_markdown", 40, 16, wid="sec_disclosures_sec_obb"), "https://www.sec.gov/edgar/search/"]),
        T("Enforcement & Advisers", auto=["sec/rss_litigation", "regulators/sec/rss_litigation", "sec/adviser_*"], groups=[]),
    ], groups=[SYMBOL], more=["sec/*", "regulators/*"]),
    D("factors", "Factors & Styles", "funds",          # review G2: beside indices and ETFs, not under News & Research
        "Live factor, style and rotation views, plus Fama-French factor and portfolio returns.", [
        T("Live", rows=[[X("board", 16, 22, preset="us_factors"), X("perf_bars", 14, 22, preset="us_factors", period="YTD"), X("style_box", 10, 11)],
                        [PERF("MTUM,QUAL,USMV,VLUE,IWM,SPY", "1Y", w=20, h=22, title="Normalized Performance: factor ETFs vs S&P 500"), W("yfinance/rrg/view", 20, 22)]]),
        # Stage 3 3a: how the main assets move together, and their risk over the same window
        T("Correlation & Risk", rows=[[X("correlation", 20, 18), W("x:risk_stats", 20, 18, wid="x_risk_stats")]]),
        T("Fama-French", auto=["famafrench/factors", "famafrench/us_portfolio_returns", "famafrench/*"]),
    ]),
]
