"""Widget definitions for the Terminal's own composite views (same schema the OpenBB backends use)."""

import cds
import columns
import cryptovol
import multpl
import news
import policy_path
from composite import MEASURE_OPTIONS, BOARD_OPTIONS, COUNTRY_OPTIONS, FACT_OPTIONS, FX_MATRIX, GROUP_OPTIONS, MACRO_OPTIONS, PLAIN_COUNTRY_OPTIONS, POLICY_OPTIONS

PERIODS = [{"label": p, "value": p} for p in ("1D", "1W", "1M", "3M", "YTD", "1Y")]
LONG_PERIODS = [{"label": p, "value": p} for p in ("1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y")]
COMPARE = [{"label": p + " ago", "value": p} for p in ("1M", "3M", "6M", "1Y", "2Y", "5Y")]
YAHOO = ["Yahoo Finance"]
CURVE_SOURCE_NAMES = ["Federal Reserve", "ECB", "Bundesbank", "Bank of England", "Japan MoF", "Bank of Canada", "Riksbank", "Tesouro Direto", "OECD"]
# Control strip chips (always visible on the card, remembered per card without editing the tab). "range" options come from
# the answer's controls (they depend on the frequency of the series shown); a param strip uses the parameter's options.
RANGE_STRIP = {"param": "range", "kind": "range"}
YES_NO = [{"label": "Hide", "value": "no"}, {"label": "Show", "value": "yes"}]
BOND_PARAMS = [{"paramName": "company", "label": "Company / CUSIP", "value": "AAPL", "type": "text", "optional": True, "show": True,
                "description": "Ticker, company name or 9-character CUSIP; resolved to FINRA's issuer name through the SEC company name."},
               {"paramName": "issuer", "label": "FINRA issuer name", "value": None, "type": "text", "optional": True, "show": True,
                "description": "Exact part of FINRA's issuer name (for example FORD MTR CR CO); overrides the company."},
               {"paramName": "all_types", "label": "MTN, convertible, perpetual", "value": "no", "type": "text", "optional": True, "show": True,
                "description": "Medium-term-note programmes, convertibles and perpetuals are hidden by default.", "options": YES_NO},
               {"paramName": "include_old", "label": "Not traded in 45 days", "value": "no", "type": "text", "optional": True, "show": True,
                "description": "Bonds without a reported sale in the last 45 days are hidden by default.", "options": YES_NO}]
TRANSFORM_STRIP = {"param": "transform", "kind": "transform"}
PERIOD_STRIP = {"param": "period", "kind": "param"}
VIEW_STRIP = {"param": "view", "kind": "param"}
# one chip that switches a parameter between two values
SHADE_STRIP = {"param": "shade", "kind": "toggle", "on": "rec", "off": "none", "label": "Recessions",
               "tip": "Shade U.S. recessions as dated by the NBER (FRED series USREC, monthly)."}
SHADE_PARAM = {"paramName": "shade", "value": "none", "show": False}
RATE_SOURCES = {"param": "source", "options": [{"label": "OECD (FRED if OECD refuses)", "value": "auto"}, {"label": "OECD only", "value": "oecd"},
                                                {"label": "FRED (OECD series republished)", "value": "fred"}]}
US_CURVE_SOURCES = {"param": "source", "options": [{"label": "Federal Reserve (FRED if it fails)", "value": "auto"}, {"label": "FRED (H.15 constant maturities)", "value": "fred"}],
                    "note": "Applies to the U.S. curve; other countries use their official source."}
SRC_PARAM = {"paramName": "source", "value": "auto", "show": False}


def _col(field, name, kind="number", tip="", **kw):
    d = {"field": field, "headerName": name, "cellDataType": kind, "headerTooltip": tip or name, "formatterFn": None}
    d.update(kw)
    return d


def _pct(field, name, tip, heat=True):
    return _col(field, name, tip=tip, formatterFn="normalizedPercent", renderFn="heat" if heat else "greenRed")


def _bp(field, name, tip):
    return _col(field, name, tip=tip, formatterFn="int", suffix=" bp", renderFn="greenRed")


def _yld(field, name, tip):
    return _col(field, name, tip=tip, decimalPlaces=2, suffix="%")


CLICK_SYMBOL = {"renderFn": "cellOnClick", "renderFnParams": {"actionType": "groupBy", "groupBy": {"paramName": "symbol", "valueField": "symbol"}}}
OPEN_SYMBOL = {"renderFn": "cellOnClick", "renderFnParams": {"actionType": "groupBy", "groupBy": {"paramName": "__open", "valueField": "symbol"}}}
CLICK_COUNTRY ={"renderFn": "cellOnClick", "renderFnParams": {"actionType": "groupBy", "groupBy": {"paramName": "country", "valueField": "key"}}}
CLICK_REPORT = {"renderFn": "cellOnClick", "renderFnParams": {"actionType": "groupBy", "groupBy": {"paramName": "report", "valueField": "date"}}}
# which earnings report the scorecard and the release show: the latest, or one picked from the company's own list
REPORT_PARAM = {"paramName": "report", "label": "Report", "value": "", "type": "endpoint", "optionsEndpoint": "/x/earnings_reports",
                "optionsParams": {"symbol": "$symbol"}, "optional": True, "show": True,
                "description": "Which report: the latest, or an earlier one (a click on a date in EPS surprise history does the same)."}


def _w(wid, name, desc, typ, endpoint, params, sub, source, w=20, h=14, cols=None, refetch=300000, **extra):
    d = {
        "name": name, "description": desc, "category": "Markets", "subCategory": sub, "type": typ, "widgetId": wid,
        "params": params, "endpoint": endpoint, "runButton": False, "gridData": {"w": w, "h": h}, "source": source,
        "data": {"dataKey": "results" if typ == "table" else "", "table": {"showAll": True}}, "refetchInterval": refetch,
    }
    if cols:
        d["data"]["table"]["columnsDefs"] = cols
    d.update(extra)
    return d


def _p(name, label, value, desc="", typ="text", **kw):
    d = {"paramName": name, "label": label, "value": value, "description": desc or label, "type": typ, "optional": True, "show": True}
    d.update(kw)
    return d


RET_TIP = ("The line under the table says which: 'Total return' = distributions reinvested on the ex-date (boards of stocks and "
           "funds only); 'Price return' = distributions left out (any board with an index, future, currency or coin). "
           "Yields: change in basis points.")


def widgets():
    board_cols = [
        _col("name", "Name", "text", "Click a name to load it in the charts on this dashboard.", pinned="left", **CLICK_SYMBOL),
        _col("quote", "Quote", "text", "Currencies: the market quote shown under Last (USD/JPY = yen per dollar). The changes and returns are those of the "
             "currency named in the row against the dollar (+ = that currency gained), whichever way the pair is quoted."),
        _col("price", "Last", tip="Latest price (today's running bar during market hours). Yields in percent."),
        _col("chg", "Chg", tip="Change from the previous close (yields: in basis points; currencies: + = the named currency gained).", renderFn="greenRed"),
        _pct("chg_pct", "1D %", "Change from the previous close, in percent (yields: see Chg, in basis points)."),
        _col("spark", "3M trend", "text", "Closing prices over the last three months (currencies: value of the named currency, so up = stronger).", renderFn="sparkline"),
        _pct("w1", "1W", f"Over the last 7 days. {RET_TIP}"),
        _pct("m1", "1M", f"Over the last month. {RET_TIP}"),
        _pct("m3", "3M", f"Over the last three months. {RET_TIP}"),
        _pct("ytd", "YTD", f"Since the last close of the previous year. {RET_TIP}"),
        _pct("y1", "1Y", f"Over the last twelve months. {RET_TIP}"),
        _col("range52", "52W range", tip="Where the last price sits between the lowest (0) and highest (100) daily close of the last 52 weeks.", renderFn="range"),
        _col("symbol", "Ticker", "text", "Yahoo Finance symbol. Click to open it (a company opens in Company, anything else in the Markets chart).", **OPEN_SYMBOL),
        _col("stale", "Last trade", "text", "Shown when a row's last price is more than 4 days older than the rest of the board (halted, delisted or "
             "market closed): its date. That row's 1D change is left blank.", hide=True, showIfAny=True),
        _col("pair_last", "Last", tip="Currencies board: the market quote and its pair (1.43 USD/CAD = 1.43 Canadian dollars per U.S. dollar).", hide=True),
        _col("pair_pct", "Quote 1D", tip="Currencies board: change of the quote as written under Last. USD/CAD up = the Canadian dollar fell. "
             "Not coloured, because up is not always good for the currency named.", formatterFn="normalizedPercent", signed=True, hide=True),
        _col("pair_ytd", "Quote YTD", tip="Currencies board: this year's change of the quote as written under Last.", formatterFn="normalizedPercent", signed=True, hide=True),
        dict(_pct("usd_pct", "vs USD 1D", "Currencies board: the named currency against the U.S. dollar since the previous 17:00 New York close "
                  "(+ = currency stronger). Empty for crosses and the dollar index."), hide=True),
        dict(_pct("usd_ytd", "vs USD YTD", "Currencies board: the named currency against the U.S. dollar this year (+ = currency stronger)."), hide=True),
        _col("low52", "52W low (close)", hide=True), _col("high52", "52W high (close)", hide=True), _col("as_of", "As of", "dateString", hide=True), _col("note", "Note", "text", hide=True),
    ]
    watch_cols = board_cols[:2] + [_col("currency", "Ccy", "text", "Trading currency (GBp = pence).")] + board_cols[2:]
    mult = dict(decimalPlaces=1, suffix="x")
    comps_cols = [
        _col("name", "Company", "text", "Click a company to make it the subject of this dashboard.", pinned="left", **CLICK_SYMBOL),
        _col("symbol", "Ticker", "text", "Yahoo Finance symbol."),
        _col("mcap_usd", "Mkt cap $bn", tip="Market capitalisation in billions of U.S. dollars (converted at the latest exchange rate).", decimalPlaces=1),
        _col("ev_usd", "EV $bn", tip="Enterprise value in billions of U.S. dollars.", decimalPlaces=1),
        _col("pe", "P/E", tip="Price over trailing 12-month earnings. Empty when earnings are negative.", **mult),
        _col("fwd_pe", "Fwd P/E", tip="Price over next fiscal year's consensus earnings.", **mult),
        _col("ev_ebitda", "EV/EBITDA", tip="Enterprise value over trailing EBITDA.", **mult),
        _col("ev_sales", "EV/Sales", tip="Enterprise value over trailing revenue.", **mult),
        _col("pb", "P/B", tip="Price over book value.", **mult),
        _pct("gross_margin", "Gross m.", "Gross margin, trailing 12 months.", heat=False),
        _pct("ebitda_margin", "EBITDA m.", "EBITDA margin, trailing 12 months.", heat=False),
        _pct("op_margin", "Op. m.", "Operating margin, trailing 12 months.", heat=False),
        _pct("net_margin", "Net m.", "Net profit margin, trailing 12 months.", heat=False),
        _pct("rev_growth", "Rev. growth", "Revenue growth, latest quarter against a year earlier.", heat=False),
        _pct("eps_growth", "EPS growth", "Earnings growth, latest quarter against a year earlier.", heat=False),
        _pct("roe", "ROE", "Return on equity, trailing 12 months.", heat=False),
        _col("nd_ebitda", "ND/EBITDA", tip="Net debt over EBITDA, derived from Yahoo's enterprise value: (EV - market cap) / EBITDA. Negative = net cash.", **mult),
        _pct("div_yield", "Div. yield", "Dividend yield.", heat=False),
        _col("beta", "Beta", tip="Beta against the local market (Yahoo).", decimalPlaces=2),
        _pct("ret_1y", "1Y return", "Price return over the last twelve months.", heat=False),
        _col("currency", "Ccy", "text", "Trading currency.", hide=True), _col("note", "Note", "text"),
    ]
    out = [
        _w("x_watchlist", "Watchlist", "Your own lists of symbols as a live quote board: last price, change, returns, 3-month trend and 52-week range. Press Edit to create lists and add or remove symbols (search by name or ticker); click a name to chart it.",
           "table", "/x/watchlist",
           [_p("list", "List", "", "Which of your watchlists this card shows."),
            {"paramName": "symbols", "value": None, "show": False}],
           "Boards", YAHOO, w=14, h=16, cols=watch_cols, symbolList="watchlist"),
        _w("x_chart", "Chart", "Chart any Yahoo Finance symbol (stocks worldwide, indices, FX, futures, crypto) or FRED series (FRED:DGS10): candles with volume and 50/200-day averages, several symbols on one chart, percent change from the start, the ratio or spread of two symbols, log scale.",
           "chart", "/x/chart",
           [_p("symbol", "Symbol", "^GSPC", "Main symbol: a Yahoo Finance ticker (AAPL, 7203.T, ^GSPC, EURUSD=X, CL=F, BTC-USD) or FRED:<series id>."),
            _p("compare", "Compare with", None, "Other symbols, comma-separated (for example ^NDX,GLD or FRED:DGS10). Ratio and Spread use the first one.", multiple=True),
            _p("view", "Show", "price", "Price, percent change from the start of the range, the ratio / spread of the main symbol and the first comparison, "
               "or the drawdown: percent below the running high within the range (prices only).", typ="tabs",
               options=[{"label": "Price", "value": "price"}, {"label": "% change", "value": "pct"}, {"label": "Ratio", "value": "ratio"}, {"label": "Spread", "value": "spread"},
                        {"label": "Drawdown", "value": "dd"}]),
            SHADE_PARAM,
            _p("period", "Range", "1Y", "Time range.", typ="tabs", options=[{"label": p, "value": p} for p in ("1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y")] + [{"label": "Max", "value": "MAX"}]),
            _p("studies", "Studies", "candles,volume,ma50,ma200", "Candles, volume, moving averages and log scale.", multiSelect=True,
               options=[{"label": "Candles", "value": "candles"}, {"label": "Volume", "value": "volume"}, {"label": "50-day average", "value": "ma50"},
                        {"label": "200-day average", "value": "ma200"}, {"label": "Log scale", "value": "log"}])],
           "Charts", ["Yahoo Finance", "FRED"], w=28, h=22, raw=True, strip=[PERIOD_STRIP, VIEW_STRIP, SHADE_STRIP], dynamicSource=True),
        _w("x_comps", "Comparable Companies", "The selected company against its peers: market value, multiples (P/E, forward P/E, EV/EBITDA, EV/Sales, P/B), margins, growth, returns, leverage and yield, with the peer median. Peers are found automatically (same industry, similar size, stocks investors also watch); press Peers to change them.",
           "table", "/x/comps",
           [_p("symbol", "Symbol", "AAPL", "Subject company (Yahoo Finance ticker)."), {"paramName": "peers", "value": None, "show": False}],
           "Fundamentals", YAHOO, w=40, h=16, cols=comps_cols, symbolList="peers", refetch=3600000),
        _w("x_snapshot", "Company Snapshot", "The company on one screen: what it does, price and valuation, profitability and growth, analyst consensus and target, next earnings.",
           "markdown", "/x/snapshot", [_p("symbol", "Symbol", "AAPL", "Yahoo Finance ticker.")], "Fundamentals", YAHOO, w=24, h=24, refetch=900000),
        _w("x_earnings", "Earnings: expected vs reported", "The latest report (or the one picked) as a desk follows it: EPS and revenue consensus (mean, range, "
           "analysts) against what was reported and the surprise, the press release, the market's reaction (after hours on the day), the consensus for "
           "the quarter the company guides for, and the track record.",
           "markdown", "/x/earnings", [_p("symbol", "Symbol", "AAPL", "Yahoo Finance ticker."), REPORT_PARAM], "Estimates", ["Yahoo Finance", "Nasdaq", "SEC"],
           w=20, h=18, refetch=120000),
        _w("x_earnings_release", "Earnings release", "The press release of the report shown in the scorecard (Form 8-K, exhibit 99.1), in the SEC filing viewer. "
           "Follows the company and the Report picker.", "iframe", "/x/earnings_release", [_p("symbol", "Symbol", "AAPL", "Yahoo Finance ticker."), REPORT_PARAM],
           "Estimates", ["SEC"], w=20, h=18, refetch=900000),
        _w("x_earnings_history", "EPS surprise history", "EPS consensus against reported EPS for the last 12 reports (same source, same basis), the surprise, "
           "and the share price move around each report. Click a date to open that report in the scorecard and its release.",
           "table", "/x/earnings_history", [_p("symbol", "Symbol", "AAPL", "Yahoo Finance ticker.")],
           "Estimates", YAHOO, w=20, h=16, refetch=900000,
           cols=[_col("date", "Reported", "text", "Report date. Click to open that report in the scorecard and its release.", pinned="left", **CLICK_REPORT),
                 _col("eps_est", "Consensus", tip="EPS consensus before the report.", decimalPlaces=2),
                 _col("eps_actual", "Reported", tip="Reported EPS, on the consensus' (adjusted) basis.", decimalPlaces=2),
                 _pct("surprise", "Surprise", "Reported minus consensus, in percent of the consensus.", heat=False),
                 _pct("move_2d", "Price move", "Share price from the close before the report day to the close of the first session after it "
                      "(one window for reports before the open and after the close).", heat=False)]),
        _w("x_consensus", "Consensus and revisions", "Analysts' consensus for this and the next quarter and fiscal year: EPS (mean, range, analysts, growth), "
           "revenue, and how the EPS consensus has moved over 7, 30 and 90 days with the number of upward and downward revisions.",
           "table", "/x/consensus", [_p("symbol", "Symbol", "AAPL", "Yahoo Finance ticker.")], "Estimates", YAHOO, w=20, h=12, refetch=900000,
           cols=[_col("period", "Period", "text", "Yahoo's periods: the current quarter is the next one to be reported (on a report day, until Yahoo posts "
                      "the results, the quarter being reported).", pinned="left"),
                 _col("eps", "EPS", tip="Consensus EPS (mean).", decimalPlaces=2), _col("eps_n", "Analysts", tip="Analysts in the EPS consensus.", decimalPlaces=0),
                 _pct("eps_growth", "EPS y/y", "Consensus EPS against the same period a year earlier.", heat=False),
                 _pct("eps_chg_30d", "EPS 30d", "Change of the EPS consensus over 30 days.", heat=False),
                 _pct("eps_chg_90d", "EPS 90d", "Change of the EPS consensus over 90 days.", heat=False),
                 _col("up_30d", "Up 30d", tip="Analysts who raised their EPS estimate in the last 30 days.", decimalPlaces=0),
                 _col("down_30d", "Down 30d", tip="Analysts who cut their EPS estimate in the last 30 days.", decimalPlaces=0),
                 _col("rev", "Revenue", tip="Consensus revenue (mean).", scale=1e9, scaleUnit="B", decimalPlaces=1),
                 _pct("rev_growth", "Revenue y/y", "Consensus revenue against the same period a year earlier.", heat=False),
                 _col("eps_low", "EPS low", hide=True, decimalPlaces=2), _col("eps_high", "EPS high", hide=True, decimalPlaces=2),
                 _pct("eps_chg_7d", "EPS 7d", "Change of the EPS consensus over 7 days.", heat=False) | {"hide": True},
                 _col("up_7d", "Up 7d", hide=True, decimalPlaces=0), _col("down_7d", "Down 7d", hide=True, decimalPlaces=0),
                 _col("rev_low", "Revenue low", hide=True, scale=1e9, scaleUnit="B", decimalPlaces=1),
                 _col("rev_high", "Revenue high", hide=True, scale=1e9, scaleUnit="B", decimalPlaces=1), _col("rev_n", "Revenue analysts", hide=True, decimalPlaces=0)]),
        _w("x_board", "Market Board", "Quote board for a market: last price, daily change, returns over five horizons, a 3-month sparkline and the 52-week range. Pick a ready-made board or type your own tickers.",
           "table", "/x/board",
           [_p("preset", "Board", "us_equity", "Which market to show.", options=BOARD_OPTIONS),
            _p("symbols", "Custom tickers", None, "Comma-separated Yahoo Finance tickers. Overrides the board when set.", multiple=True)],
           "Boards", YAHOO, w=14, h=16, cols=board_cols, titleParam="preset"),
        _w("x_performance", "Normalized Performance", "Percent change of several instruments from a common start date, on one axis.", "chart", "/x/performance",
           [_p("symbols", "Tickers", "^GSPC,^NDX,^DJI", "Up to eight comma-separated Yahoo Finance tickers.", multiple=True),
            _p("period", "Period", "YTD", "Look-back window.", typ="tabs", options=LONG_PERIODS)],
           "Performance", YAHOO, w=20, h=18, raw=True, strip=[PERIOD_STRIP], titlePeriod="period"),
        _w("x_perf_bars", "Performance Ranking", "Members of a board ranked by return over the chosen period.", "chart", "/x/perf_bars",
           [_p("preset", "Board", "us_sectors", "Which market to rank.", options=BOARD_OPTIONS),
            _p("period", "Period", "1D", "Return window.", typ="tabs", options=PERIODS)],
           "Performance", YAHOO, w=14, h=16, raw=True, titleParam="preset", titlePrefix="Ranking: ", strip=[PERIOD_STRIP], titlePeriod="period"),
        _w("x_style_box", "U.S. Equity Style Box", "Returns of U.S. large, mid and small caps split into value, core and growth (Russell ETFs).", "table", "/x/style_box",
           [_p("period", "Period", "1D", "Return window.", typ="tabs", options=PERIODS)],
           "Performance", YAHOO, w=10, h=9, strip=[PERIOD_STRIP], titlePeriod="period",
           cols=[_col("size", "", "text", pinned="left"), _pct("value", "Value", "Russell value ETF total return (distributions reinvested)."), _pct("core", "Core", "Russell broad ETF total return (distributions reinvested)."),
                 _pct("growth", "Growth", "Russell growth ETF total return (distributions reinvested).")]),
        _w("x_global_yields", "Global Yields", "Government bond yields by country and maturity, with the 2s10s slope, the change in the 10-year yield and the central-bank policy rate. Daily curves where available, monthly OECD rates elsewhere.",
           "table", "/x/global_yields", [{"paramName": "countries", "value": "", "show": False}], "Rates", CURVE_SOURCE_NAMES + ["BIS"], w=24, h=18, refetch=900000, dynamicSource=True,
           cols=[_col("country", "Country", "text", "Click a country to load it in the country widgets on this dashboard.", pinned="left", **CLICK_COUNTRY),
                 _yld("m3", "3M", "3-month government (bill) yield from the daily curve. Rows marked monthly show the OECD's 3-month interbank rate instead (see Note): not a government yield."), _yld("y1", "1Y", "1-year yield."), _yld("y2", "2Y", "2-year yield."), _yld("y5", "5Y", "5-year yield."),
                 _yld("y10", "10Y", "10-year yield."), _yld("y30", "30Y", "30-year yield."),
                 _bp("slope_2s10s", "2s10s", "10-year minus 2-year yield, in basis points."),
                 _bp("y10_chg_1m", "10Y 1M chg", "Change in the 10-year yield over one month, in basis points."),
                 _bp("y10_chg_1y", "10Y 1Y chg", "Change in the 10-year yield over one year, in basis points."),
                 _yld("policy", "Policy rate", "Central-bank policy rate (BIS)."),
                 _col("as_of", "As of", "text", "Date of the curve."), _col("freq", "Data", "text", "daily = full curve from an official source; monthly = OECD 3M and 10Y only."),
                 _col("source", "Source", "text", "Where the curve comes from and what it is: par yields, zero-coupon (spot) yields, yields of actual bonds, or "
                                                   "monthly averages. Par and zero-coupon yields differ by tens of basis points at the long end of a steep curve."),
                 _col("note", "Note", "text"), _col("key", "Key", "text", hide=True)]),
        _w("x_yield_curves", "Yield Curves by Country", "Government yield curves for the countries you pick, optionally against the same curves at earlier dates (dotted). "
           "Daily official curves for the U.S., euro area, Germany, U.K., Japan, Canada, Sweden and Brazil; other countries show the OECD's monthly 3-month and 10-year rates as two points.", "chart", "/x/yield_curves",
           [_p("country", "Countries", "united_states,euro_area,united_kingdom,japan", "One or more countries.", options=COUNTRY_OPTIONS, multiSelect=True),
            _p("compare", "Compare with", "", "Add the curve as it was this long ago.", options=COMPARE, multiSelect=True), SRC_PARAM],
           "Rates", CURVE_SOURCE_NAMES, w=24, h=18, raw=True, refetch=900000, dynamicSource=True, sourceParam=US_CURVE_SOURCES),
        _w("x_yield_curve_table", "Yield Curve Table", "One country's curve by maturity: today, earlier dates, and the change in basis points.", "table", "/x/yield_curve_table",
           [_p("country", "Country", "united_states", "Country.", options=COUNTRY_OPTIONS),
            _p("compare", "Compare with", "1M,1Y", "Earlier dates to compare against.", options=COMPARE, multiSelect=True), SRC_PARAM],
           "Rates", CURVE_SOURCE_NAMES, w=16, h=18, refetch=900000, dynamicSource=True, sourceParam=US_CURVE_SOURCES,
           cols=[_col("maturity", "Maturity", "text", pinned="left"), _col("yield", "Yield", decimalPlaces=3, suffix="%"),
                 *[c for h in ("1m", "3m", "6m", "1y", "2y", "5y") for c in (_col(f"y_{h}", f"{h.upper()} ago", decimalPlaces=3, suffix="%", headerTooltip=f"Yield on the curve closest to {h.upper()} ago (date in the next column but one); left out when no curve lies within 20 days (45 for monthly data)."),
                                                                             _bp(f"chg_{h}", f"Chg {h.upper()}", f"Change versus {h.upper()} ago, in basis points."),
                                                                             _col(f"d_{h}", f"{h.upper()} date", "text", f"Date of the curve used as '{h.upper()} ago'."))],
                 _col("as_of", "As of", "text"), _col("source", "Source", "text"), _col("maturity_years", "Years", hide=True)]),
        _w("x_policy_rates", "Central Bank Policy Rates", "Policy rate of about 40 central banks with the size and date of the latest move and the change over one year.", "table", "/x/policy_rates",
           [], "Rates", ["BIS"], w=20, h=18, refetch=3600000,
           cols=[_col("country", "Central bank", "text", pinned="left"), _col("rate", "Policy rate", decimalPlaces=2, suffix="%"),
                 _bp("last_move_bp", "Last move", "Size of the most recent change, in basis points."),
                 _col("last_move", "Moved on", "text", "Day of the most recent change (BIS daily series); a month (YYYY-MM) where only month-end values exist."),
                 _col("previous", "Before", decimalPlaces=2, suffix="%"), _bp("chg_1y", "1Y chg", "Change over twelve months, in basis points."),
                 _bp("vs_us", "vs Fed", "Policy-rate differential against the U.S. Federal Reserve, in basis points (positive = higher than the Fed)."),
                 _col("as_of", "As of", "text", "Latest day of the BIS daily series (about a week behind), or the month of a month-end value."),
                 _col("note", "Note", "text", "'month-end value': the BIS daily series is not available for this central bank; a decision taken since is not shown."),
                 _col("code", "Code", "text", hide=True)]),
        _w("x_policy_rate_history", "Policy Rate History", "Policy-rate paths of the selected central banks.", "chart", "/x/policy_rate_history",
           [_p("central_bank", "Central banks", "US,XM,GB,JP", "Up to eight central banks.", options=POLICY_OPTIONS, multiSelect=True),
            _p("start", "Since (year)", "2015", "First year shown."),
            {"paramName": "country", "value": None, "show": False}],
           "Rates", ["BIS"], w=20, h=18, raw=True, refetch=3600000, strip=[RANGE_STRIP]),
        _w("x_policy_path", "Policy Path (OIS)", "Policy rate priced by meeting-dated overnight index swaps for six central banks (CFTC swap-repository prints, 14-day window priced from freshest trades): "
           "one bank today against one week and one month ago, or several banks side by side, with the BIS policy rate and latest overnight fixing as anchors.", "chart", "/x/policy_path",
           [_p("currency", "Central bank", "USD", "One central bank: today vs 1W and 1M ago. Pick several to compare today's paths side by side.", options=policy_path.OPTIONS, multiSelect=True)],
           "Rates", ["CFTC", "BIS"], w=22, h=18, raw=True, refetch=1800000),
        _w("x_policy_path_table", "Policy Path by Meeting", "OIS-implied overnight rate for each period between policy meetings (effective date / maintenance-period start), the step priced at each meeting "
           "(and as a share of a 25 bp move), cumulative move from the overnight fixing, the change over one week and one month, and the gap to the current policy rate.", "table", "/x/policy_path_table",
           [_p("currency", "Central bank", "USD", "Currency of the overnight index swaps.", options=policy_path.OPTIONS)],
           "Rates", ["CFTC", "BIS"], w=18, h=18, refetch=1800000,
           cols=[_col("meeting", "Meeting", "text", "Effective date of the policy-rate change or maintenance-period start (e.g. ECB reserve maintenance periods start a few days after the decision). First rows: the current BIS policy rate and the current period at the latest official overnight fixing (EFFR, ESTR, SONIA, TONA, CORRA, AONIA).", pinned="left"),
                 _col("rate", "Implied rate", tip="Expected average overnight rate (EFFR, ESTR, SONIA, TONA, CORRA or AONIA) over the period, in percent, priced from freshest trades.", decimalPlaces=3, suffix="%"),
                 _bp("step", "Step", "Implied rate minus the previous period's, in basis points: the move priced at this meeting. The first meeting is measured from the official overnight fixing so the index basis cancels."),
                 _col("priced", "Priced move", tip="Unrounded step as a percent of a 25 bp move: +100 = one full 25 bp hike priced, -50 = half a cut, -150 = one cut and half of a second.",
                      formatterFn="int", suffix="%", renderFn="greenRed"),
                 _bp("cum_bp", "Cum. move", "Cumulative move priced from the current overnight fixing to this meeting period, in basis points (the overnight index basis cancels)."),
                 _bp("chg_1w", "Chg 1W", "Change in the implied rate for the same period against the path one week ago, in basis points."),
                 _bp("chg_1m", "Chg 1M", "Change in the implied rate for the same period against the path one month ago, in basis points."),
                 _bp("vs_policy", "vs policy rate", "Implied rate minus the current policy rate (BIS), in basis points. Includes the usual gap between the overnight index and the policy rate, so a few basis points are not a priced move."),
                 _col("start", "Period start", "text", "Start of the period between two policy meetings (forward start of the swap); the current fixing period starts today."),
                 _col("end", "Period end", "text", "End of the period: the next meeting or maintenance-period end. One period per meeting: bespoke forward periods overlapping them are left out."),
                 _col("rate_1w", "1W ago", tip="Implied rate for the same period on the path one week ago (blank when that path had no node within a week of this start).", decimalPlaces=3, suffix="%"),
                 _col("rate_1m", "1M ago", tip="Implied rate for the same period on the path one month ago.", decimalPlaces=3, suffix="%"),
                 _col("trades", "Trades (14d)", tip="Total trades for this period across the 14-day window (the implied rate is priced from the freshest trading day(s), not all 14 days pooled).", formatterFn="int"),
                 _col("as_of", "As of", "text", "Freshest day the period's trades or rate were disseminated on.")]),
        _w("x_cds_indices", "CDS Indices", "Credit default swap indices (CDX IG, HY, EM; iTraxx Main, Crossover), 5-year on-the-run: the day's notional-weighted level "
           "from swap-repository prints, with its range, trade count and changes. Index CDS only; no single-name CDS.", "table", "/x/cds_indices",
           [], "Credit", ["CFTC"], w=20, h=14, refetch=1800000,
           cols=[_col("index", "Index", "text", pinned="left"),
                 _col("level", "Level", tip="Notional-weighted average of the day's new trades in the on-the-run series (the 5Y maturity with the most notional). "
                      "Spread in basis points for IG, iTraxx Main and Crossover; price in points of par for HY and EM (never shown in bp).", decimalPlaces=2),
                 _col("unit", "Unit", "text", "bp = spread in basis points; points = price per 100 of notional."),
                 _col("chg_1d", "Chg 1D", tip="Change against the previous day with prints at the same maturity, in the row's unit. Blank when that day had no print in this series (a roll).", decimalPlaces=2, renderFn="greenRed"),
                 _col("chg_1w", "Chg 1W", tip="Change against about one week earlier, same maturity, in the row's unit.", decimalPlaces=2, renderFn="greenRed"),
                 _col("chg_1m", "Chg 1M", tip="Change against about one month earlier, same maturity, in the row's unit.", decimalPlaces=2, renderFn="greenRed"),
                 _col("low", "Low", tip="Lowest print of the day in the series, in the row's unit.", decimalPlaces=2),
                 _col("high", "High", tip="Highest print of the day in the series, in the row's unit.", decimalPlaces=2),
                 _col("trades", "Trades", tip="New trades in the series that day.", formatterFn="int"),
                 _col("notional_mn", "Notional (mn)", tip="Sum of disseminated notionals, millions, in the currency of the next column. Notionals above the reporting cap are disseminated at the cap, so this is a floor.", formatterFn="int"),
                 _col("currency", "Ccy", "text", "Currency of the notional."),
                 _col("maturity", "Series maturity", "text", "Maturity date of the on-the-run series (the data carries no series number)."),
                 _col("date", "Date", "text", "Dissemination date of the prints (UTC)."),
                 _col("dropped", "Off-market", tip="Prints left out as off-market: more than 50% (spreads) or 20% (prices) away from the day's notional-weighted median in the series, "
                      "which are misreported conventions (seen: iTraxx Main 642,125 'bp').", formatterFn="int"),
                 _col("note", "Note", "text", "A roll: the series did not trade in the five days before."),
                 _col("family", "Family", "text", hide=True)]),
        _w("x_cds_history", "CDS Index History", "Daily on-the-run level of credit default swap indices from swap-repository prints: spreads in bp, HY and EM prices in points on their own axis.",
           "chart", "/x/cds_history",
           [_p("family", "Indices", "CDX.NA.IG,CDX.NA.HY", "Spread-quoted indices on the left axis (bp), price-quoted on the right (points).",
               options=cds.OPTIONS, multiSelect=True)],
           "Credit", ["CFTC"], w=20, h=16, raw=True, refetch=1800000, strip=[RANGE_STRIP]),
        _w("x_crypto_vol", "Implied vs Realised Volatility (crypto)", "Deribit DVOL (30-day implied volatility) against 30-day realised volatility "
           "from daily closes (close-to-close log returns, annualised x sqrt(365)), and the gap between them. Complete UTC days only.",
           "chart", "/x/crypto_vol",
           [_p("currency", "Coin", "BTC", "Bitcoin or ether (the coins with a DVOL index).", options=cryptovol.OPTIONS)],
           "Crypto", ["Deribit", "Yahoo Finance"], w=20, h=18, raw=True, refetch=1800000, strip=[RANGE_STRIP]),
        _w("x_bond_lookup", "Corporate Bonds (TRACE)", "A company's bonds with the last reported sale from FINRA TRACE: coupon, maturity, rating, price and last-sale yield. "
           "Type a ticker, a company name, a FINRA issuer name or a CUSIP.", "table", "/x/bond_lookup", BOND_PARAMS,
           "Credit", ["FINRA", "SEC", "FRED"], w=24, h=18, refetch=900000,
           cols=[_col("issuer", "Issuer (FINRA)", "text", "Issuer name as FINRA records it (abbreviated).", pinned="left"),
                 _col("coupon", "Coupon", tip="Annual coupon rate, percent.", decimalPlaces=3, suffix="%"),
                 _col("maturity", "Maturity", "text"),
                 _col("rating", "Rating", "text", "Moody's / S&P ('-' = not rated by that agency)."),
                 _col("price", "Last price", tip="Price of the last reported sale, per 100 of par.", decimalPlaces=3),
                 _col("yield", "Last-sale yield", tip="FINRA last-sale yield; yield-to-worst on callables. Percent. Blank where FINRA sends its 999999.99 placeholder.", decimalPlaces=3, suffix="%"),
                 _col("last_trade", "Last trade", "text", "Date of the last reported sale (TRACE)."),
                 _col("callable", "Callable", "text", "Callable, with the next call date where FINRA states it."),
                 _col("grade", "Grade", "text", "TRACE grade: investment grade or high yield."),
                 _col("cusip", "CUSIP", "text", "Click to load this bond's trade history below.", renderFn="cellOnClick", renderFnParams={"groupByParamName": "cusip"}),
                 _col("special", "MTN / conv. / perp.", "text", "Medium-term note programme, convertible or perpetual (hidden unless shown with the toggle)."),
                 _col("ytm", "Yield (ours)", tip="Our yield to maturity from the last-sale price: semiannual, 30/360, settled on the last trade date. Callables to maturity (call ignored). Percent.", decimalPlaces=3, suffix="%"),
                 _col("g_spread", "G-spread (bp)", tip="Our yield minus the Fed's par Treasury curve (H.15 constant maturities) of the last trade date, interpolated at the remaining maturity. Basis points.", decimalPlaces=0),
                 _col("benchmark", "Benchmark", "text", "Nearest on-the-run Treasury tenor to the remaining maturity."),
                 _col("mod_duration", "Mod. duration", tip="Modified duration at our yield (years); to maturity.", decimalPlaces=2),
                 _col("convexity", "Convexity", tip="Convexity at our yield; to maturity.", decimalPlaces=1),
                 _col("dv01", "DV01", tip="Price change for a 1 bp yield move, per 100 par (dirty price).", decimalPlaces=4),
                 _col("price_chg", "Price chg", tip="Change in the last-sale price against the previous sale (FINRA), points per 100 par.", decimalPlaces=3),
                 _col("coupon_type", "Coupon type", "text", "FINRA coupon type (Fixed Plain Vanilla, Floating ...); yield math for fixed rate only."),
                 _col("is_144a", "144A", "text", "Rule 144A private placement (institutional buyers only)."),
                 _col("risk_basis", "Risk basis", "text", "How the yield, duration and convexity are computed.", hide=True),
                 _col("isin", "ISIN", "text", "US ISIN derived from the CUSIP (check digit computed).", hide=True),
                 _col("moodys_date", "Moody's date", "text", "Date of the Moody's rating.", hide=True),
                 _col("sp_date", "S&P date", "text", "Date of the S&P rating.", hide=True)]),
        _w("x_bond_curve", "Issuer Yield Curve (TRACE)", "FINRA last-sale yield (yield-to-worst on callables) against maturity for a company's bonds, one colour per issuer name. TRACE last-sale data, not live quotes.",
           "chart", "/x/bond_curve", BOND_PARAMS, "Credit", ["FINRA", "SEC"], w=16, h=18, raw=True, refetch=900000),
        _w("x_macro", "Data Chart", "A curated chart: credit, rates, growth, housing, oil and gas fundamentals (EIA), refining margins, geopolitical risk, shipping chokepoints and more. Pick a chart, or type FRED series IDs. Inventories can be shown against their 5-year range.",
           "chart", "/x/macro_chart",
           [_p("preset", "Chart", "credit_spreads", "Which set of series to show.", options=MACRO_OPTIONS),
            _p("series", "Custom FRED IDs", None, "Comma-separated FRED series IDs (for example UNRATE,PAYEMS). Overrides the chart when set.", multiple=True),
            _p("transform", "Show as", None, "Level, year-over-year change, indexed to 100 at the start, or this year against the 5-year range.", options=[{"label": "Level", "value": "level"}, {"label": "Year over year %", "value": "yoy"}, {"label": "Indexed (start = 100)", "value": "index"}, {"label": "Seasonal (vs 5-year range)", "value": "seasonal"}]),
            _p("since", "Since (year)", "2015", "First year shown."), SHADE_PARAM],
           "Economy", ["FRED", "U.S. EIA", "Yahoo Finance", "IMF PortWatch", "Caldara & Iacoviello"], w=20, h=16, raw=True, refetch=3600000, titleParam="preset",
           dynamicSource=True, strip=[RANGE_STRIP, TRANSFORM_STRIP, SHADE_STRIP]),
        _w("x_compare", "Company Comparison", "One reported figure (capital expenditure, revenue, debt, buybacks...) across several companies over time, taken from their SEC filings. Pick a peer group or type tickers.",
           "chart", "/x/compare",
           [_p("group", "Peer group", "hyperscalers", "A ready-made peer group.", options=GROUP_OPTIONS),
            _p("symbols", "Custom tickers", None, "Up to eight U.S.-listed tickers. Overrides the peer group when set.", multiple=True),
            _p("fact", "Figure", "capex", "Which reported figure to compare.", options=FACT_OPTIONS),
            _p("measure", "Show as", "level", "Level in USD bn; the figure as % of revenue; or its change on the same period a year earlier (%). "
               "Blank where the denominator is missing or not above zero.", options=MEASURE_OPTIONS),
            _p("period", "Period", "quarter", "Quarterly, trailing twelve months, or fiscal years.", typ="tabs", options=[{"label": "Quarterly", "value": "quarter"}, {"label": "Trailing 12M", "value": "ttm"}, {"label": "Annual", "value": "annual"}]),
            _p("since", "Since (year)", "2019", "First year shown.")],
           "Fundamentals", ["SEC"], w=20, h=18, raw=True, refetch=3600000, titleParam="fact", titleParam2="group", strip=[PERIOD_STRIP]),
        _w("x_chokepoints", "Chokepoint Monitor", "Ships per day through the world's shipping chokepoints (Hormuz, Bab el-Mandeb, Suez, Cape of Good Hope, Bosporus, Panama, Taiwan Strait...): last 30 days against a year earlier and against Jan-Oct 2023, before the Red Sea attacks.",
           "table", "/x/chokepoint_monitor", [], "Shipping", ["IMF PortWatch"], w=24, h=18, refetch=3600000,
           cols=[_col("chokepoint", "Chokepoint", "text", pinned="left"), _col("total", "All ships/day", decimalPlaces=1), _pct("total_1y", "vs 1Y ago", "All ships: change against the same 30 days a year earlier."),
                 _pct("total_pre", "vs 2023", "All ships: change against Jan-Oct 2023, before the Red Sea attacks."), _col("tanker", "Tankers/day", decimalPlaces=1),
                 _pct("tanker_1y", "Tankers vs 1Y", "Tankers: change against a year earlier."), _pct("tanker_pre", "Tankers vs 2023", "Tankers: change against Jan-Oct 2023."),
                 _col("container", "Containers/day", decimalPlaces=1), _pct("container_1y", "Containers vs 1Y", "Container ships: change against a year earlier."),
                 _col("dry_bulk", "Dry bulk/day", decimalPlaces=1), _pct("dry_bulk_1y", "Dry bulk vs 1Y", "Dry bulk: change against a year earlier."),
                 _col("as_of", "As of", "text", "Last day in IMF PortWatch's weekly release (ends a few days back; the 30-day windows end on this day, not today). "
                      "Two copies 9.5 hours apart matched day for day (2026-11-19); later revisions of the newest days are not ruled out.")]),
        _w("x_gpr_table", "Geopolitical Risk by Country", "Caldara-Iacoviello geopolitical risk for 44 countries (share of news articles about geopolitical tension involving the country), against its own last 12 months and its average since 2000.",
           "table", "/x/gpr_table", [], "Geopolitics", ["Caldara & Iacoviello"], w=16, h=18, refetch=3600000,
           cols=[_col("country", "Country", "text", pinned="left"), _col("latest", "Latest", decimalPlaces=2), _col("avg_3m", "3-month avg", decimalPlaces=2),
                 _pct("vs_12m", "vs 12M avg", "Latest month against the previous 12 months."), _pct("vs_long_run", "vs since 2000", "3-month average against the average since 2000."),
                 _col("as_of", "Month", "text", "Latest month of the monthly file (complete months only). The daily index (Geopolitical Risk charts) runs to a few days ago "
                      "and is a separate series, not the monthly value to date: October 2026 daily average 164.2 against monthly 150.5.")]),
        _w("x_news", "News", "Headlines that matter for the subject: official releases (central banks, statistics offices, regulators, the issuer's own "
           "filings), wires and financial press, and regional press in the home language; duplicates folded into one line with a source count. "
           "'Top' ranks by materiality, source and recency; 'Monitor' lists everything newest first. A label (Fed, oil...) marks a driver of the "
           "subject rather than the subject itself. Paywalled outlets are headline and link only.",
           "newsfeed", "/x/news",
           [_p("subject", "Subject", "symbol", "What the headlines are about: the dashboard's symbol or country, the whole market, or a commodity, "
               "currency pair or coin.", options=news.SUBJECT_OPTIONS),
            _p("mode", "View", "top", "Top: ranked, most material first. Monitor: newest first, every curated feed.", typ="tabs",
               options=[{"label": "Top", "value": "top"}, {"label": "Monitor", "value": "monitor"}]),
            _p("feeds", "Feeds (monitor)", "all", "Which feeds the monitor reads.", options=news.FEED_OPTIONS),
            _p("hours", "Window (hours)", "", "How far back: 48 h for Top, 24 h for the monitor (7 days for official releases) unless set."),
            {"paramName": "symbol", "value": "", "show": False}, {"paramName": "country", "value": "", "show": False},
            {"paramName": "listing", "value": "", "show": False}],
           "News", ["Terminal news"], w=20, h=16, refetch=300000, dynamicSource=True,
           data={"dataKey": "results", "table": {"showAll": True}}),
        _w("x_bank_failures", "U.S. Bank Failures (FDIC)", "Failed FDIC-insured banks, newest first: assets and deposits at failure, the FDIC's estimated loss to "
           "the Deposit Insurance Fund, and who took the bank over.", "table", "/x/bank_failures", [], "Banking", ["FDIC"], w=20, h=16, refetch=3600000,
           cols=[_col("failed", "Failed", "dateString", pinned="left"), _col("bank", "Bank", "text"), _col("location", "Location", "text"),
                 _col("assets_mn", "Assets", decimalPlaces=1, prefix="$", suffix="M", headerTooltip="Total assets at failure, $ millions (FDIC sends thousands)."),
                 _col("deposits_mn", "Deposits", decimalPlaces=1, prefix="$", suffix="M", headerTooltip="Total deposits at failure, $ millions."),
                 _col("est_loss_mn", "Est. loss to DIF", decimalPlaces=1, prefix="$", suffix="M", headerTooltip="FDIC's estimated cost to the Deposit Insurance Fund, $ millions; revised as the receivership winds down."),
                 _col("acquirer", "Acquirer", "text"), _col("resolution", "Resolution", "text", "FDIC resolution type (PA = purchase and assumption, PO = payout).")]),
        _w("x_bank_quality", "Bank Credit Quality and Capital", "For a U.S. bank holding company ticker: its largest FDIC-insured bank's noncurrent loans and net "
           "charge-offs (shown), leverage ratio and return on assets (in the legend), quarterly from the call reports, in percent.",
           "chart", "/x/bank_quality", [_p("symbol", "Bank", "JPM", "Ticker of a U.S. bank holding company or of a foreign bank with U.S. operations (JPM, WFC, PNC, RY).")],
           "Banking", ["FDIC", "FFIEC NIC"], w=20, h=18, raw=True, refetch=3600000),
        _w("x_rate_history", "Rate History by Country", "Monthly history of overnight, 3-month or 10-year rates for the countries you pick. Read from a local copy refreshed twice a day, so changing country is instant.",
           "chart", "/x/rate_history",
           [_p("country", "Countries", "united_states", "One or more countries (OECD members and partners).", options=COUNTRY_OPTIONS, multiSelect=True),
            _p("duration", "Rate", "long", "Which rate.", typ="tabs", options=[{"label": "Overnight", "value": "immediate"}, {"label": "3-month", "value": "short"}, {"label": "10-year", "value": "long"}]),
            _p("start", "Since (year)", "2000", "First year shown."), SRC_PARAM],
           "Rates", ["OECD"], w=20, h=16, raw=True, refetch=3600000, dynamicSource=True, sourceParam=RATE_SOURCES, strip=[RANGE_STRIP]),
        _w("x_rates_by_country", "Interest Rates by Country", "Overnight, 3-month and 10-year rates for about 45 countries, with the 10Y-3M slope and 10-year changes (monthly).", "table", "/x/rates_by_country",
           [SRC_PARAM], "Rates", ["OECD"], w=20, h=18, refetch=3600000, dynamicSource=True, sourceParam=RATE_SOURCES,
           cols=[_col("country", "Country", "text", pinned="left"), _yld("overnight", "Overnight", "Immediate (overnight interbank) rate."), _yld("m3", "3M", "3-month interbank rate."),
                 _yld("y10", "10Y", "10-year government bond yield."), _bp("slope", "10Y-3M", "10-year minus 3-month, in basis points."),
                 _bp("y10_chg_1m", "10Y 1M chg", "Change in the 10-year yield over one month."), _bp("y10_chg_1y", "10Y 1Y chg", "Change in the 10-year yield over one year."),
                 _col("as_of", "As of", "text", "Month of the oldest rate shown in the row."),
                 _col("note", "Note", "text", "A rate whose series stopped more than 3 months before the newest month is left blank and named here with its last month.")]),
        _w("x_fx_matrix", "Currency Cross Rates", "Cross rates of the major currencies (like Bloomberg FXC): each row is one unit of that currency priced in the column currency, with its change against the dollar today and this year.",
           "table", "/x/fx_matrix", [], "Boards", YAHOO, w=24, h=12,
           cols=[_col("ccy", "1 unit of", "text", pinned="left")] + [_col(c, c, tip=f"Price in {c}.") for c in FX_MATRIX]
                + [_pct("chg_1d", "vs USD 1D", "Change of the row currency against the U.S. dollar since the previous close."),
                   _pct("ytd", "vs USD YTD", "Change of the row currency against the U.S. dollar this year."), _col("as_of", "As of", "dateString", hide=True)]),
        _w("x_fx_moves", "Currency strength vs. pair move", "Separates a currency's own move from the dollar's. Own strength: the average move (geometric, equal weights, "
           "not trade weighted) against the other seven majors. Against USD only: that one exchange rate, signed so + = currency stronger. Under each currency: the pair as quoted.",
           "chart", "/x/fx_moves", [_p("period", "Period", "1D", "Change window.", typ="tabs", options=PERIODS)],
           "Performance", YAHOO, w=20, h=20, raw=True, strip=[PERIOD_STRIP], titlePeriod="period"),
        _w("x_fx_trend", "Currency strength over time", "One panel per major, on one scale: its own strength against the other seven majors, and its move against the U.S. dollar "
           "alone (up = currency stronger). Where only the second line moves, it was the dollar that moved.",
           "chart", "/x/fx_trend", [_p("period", "Period", "3M", "Look-back window.", typ="tabs", options=[p for p in LONG_PERIODS if p["value"] in ("1M", "3M", "6M", "YTD", "1Y")])],
           "Performance", YAHOO, w=20, h=20, raw=True, strip=[PERIOD_STRIP], titlePeriod="period"),
        _w("x_country_monitor", "Country Monitor", "Latest actuals for the main economies (like Bloomberg ECST, without consensus): real GDP growth, consumer-price inflation, unemployment, "
           "policy rate, 3-month and 10-year rates. Click a country to load it in the country widgets on this dashboard.",
           "table", "/x/country_monitor", [], "Economy", ["OECD", "IMF", "BIS"], w=40, h=16, refetch=3600000, dynamicSource=True,
           cols=[_col("country", "Country", "text", "Click a country to load it in the country widgets on this dashboard.", pinned="left", **CLICK_COUNTRY),
                 _pct("gdp_yoy", "GDP y/y", "Real GDP, change against the same quarter a year earlier (OECD).", heat=False), _col("gdp_q", "Quarter", "text"),
                 _pct("cpi_yoy", "CPI y/y", "Consumer prices, change over twelve months (IMF; euro area HICP).", heat=False), _col("cpi_m", "Month", "text"),
                 _pct("unemp", "Unemployment", "Unemployment rate (OECD, harmonised). Empty where the OECD has no monthly series.", heat=False), _col("unemp_m", "Month ", "text"),
                 _yld("policy", "Policy rate", "Central-bank policy rate (BIS)."), _col("policy_d", "Rate date", "text", "Date of the policy rate shown (BIS series)."),
                 _yld("m3", "3M", "3-month rate: bill yield from the daily curve, else the OECD's 3-month interbank rate."),
                 _yld("y10", "10Y", "10-year government bond yield."), _bp("slope", "10Y-3M", "10-year yield minus 3-month bill yield, in basis points. Only where both come from the daily government curve (a 10-year bond minus an interbank rate is not a curve slope)."),
                 _col("rates_as_of", "Rates as of", "text"), _col("rates_src", "Rates data", "text", "daily = official daily curve; monthly = OECD."), _col("key", "Key", "text", hide=True)]),
        _w("x_cpi_history", "Inflation by Country", "Consumer-price inflation (year over year) for the countries you pick, from the IMF; the euro area is the harmonised index (HICP).",
           "chart", "/x/cpi_history",
           [_p("country", "Countries", "united_states,euro_area,united_kingdom,japan", "One or more countries.", options=PLAIN_COUNTRY_OPTIONS, multiSelect=True),
            _p("start", "Since (year)", "2015", "First year shown.")],
           "Economy", ["IMF"], w=20, h=16, raw=True, refetch=3600000, strip=[RANGE_STRIP]),
        _w("x_sp500_multiples", "S&P 500 Valuation Multiples", "Historical S&P 500 P/E, Shiller CAPE, dividend and earnings yields, price-to-sales, price-to-book and per-share fundamentals back to 1871 (multpl.com).",
           "chart", "/x/sp500_multiples",
           [_p("series_name", "Series", "pe_month,shiller_pe_month", "One or more S&P 500 valuation series (up to four, at most two units).", options=multpl.OPTIONS, multiSelect=True)],
           "Valuation", ["multpl.com"], w=40, h=18, raw=True, refetch=3600000, dynamicSource=True, strip=[RANGE_STRIP]),
        _w("x_data_status", "Data status", "Which data sources are broken upstream right now, which API keys are set and how many cards use each, and the known limits of free data.",
           "markdown", "/x/data_status", [], "Reference", ["Terminal"], w=40, h=20, refetch=3600000),
    ]
    out.extend(_stage3())
    return columns.stamp_x_units({w["widgetId"]: w for w in out})      # every number column declares its unit


SYM = [_p("symbol", "Symbol", "AAPL", "Ticker (Yahoo Finance spelling; U.S. filers use SEC figures).")]


def _stage3():
    """Stage 3 views (valuation, risk, FX differential, bonds, ETFs, options analytics)."""
    import optionsx
    import risk
    import valuation
    rng = _p("range", "Window", "1Y", "Look-back window.", typ="tabs", options=[{"label": k, "value": k} for k in risk.RANGES])
    frq = _p("freq", "Frequency", "auto", "Auto: daily when every symbol closes in the same market, else Friday closes (mixed time zones, FX, crypto).", options=risk.FREQ_OPTIONS)
    risk_cols = [_col("symbol", "Symbol", "text", pinned="left", **OPEN_SYMBOL),
                 _col("vol", "Volatility", tip="Annualised standard deviation of returns at the stated frequency, percent.", decimalPlaces=1, suffix="%"),
                 _col("sharpe", "Sharpe", tip="Mean excess return over the average 3-month T-bill, annualised, divided by annualised volatility.", decimalPlaces=2),
                 _col("max_dd", "Max drawdown", tip="Largest fall from a running high in the window, daily closes, percent.", decimalPlaces=1, suffix="%"),
                 _col("beta", "Beta", tip="Covariance with the benchmark / variance of the benchmark, same returns.", decimalPlaces=2),
                 _col("ret_1y", "Return", tip="Price change over the window (distributions not included), percent.", decimalPlaces=1, suffix="%"),
                 _col("periods", "Periods", tip="Return periods used (common dates).", formatterFn="int", hide=True)]
    return [
        _w("x_correlation", "Cross-asset correlation", "Correlation of returns between the symbols over the window: daily on common dates when all close in one market, "
           "else Friday closes (mixed time zones, FX, crypto), as the note says.", "chart", "/x/correlation",
           [_p("symbols", "Symbols", risk.DEFAULT, "Yahoo Finance tickers, comma separated (up to 20)."), frq],
           "Risk", YAHOO, w=20, h=18, raw=True, refetch=3600000, strip=[RANGE_STRIP]),
        _w("x_risk_stats", "Risk statistics", "Annualised volatility, Sharpe ratio (vs the average 3-month T-bill), max drawdown, beta and return over the window.",
           "table", "/x/risk_stats", [_p("symbols", "Symbols", risk.DEFAULT, "Yahoo Finance tickers, comma separated."), _p("benchmark", "Benchmark", "SPY", "For beta."), rng, frq],
           "Risk", ["Yahoo Finance", "FRED"], w=20, h=18, refetch=3600000, cols=risk_cols),
        _w("x_peer_risk", "Risk vs peers", "The company and its peers (the comps list, or the peers you set there): annualised volatility, Sharpe ratio, "
           "max drawdown, beta vs the S&P 500 and return over the window.", "table", "/x/risk_stats",
           SYM + [{"paramName": "peers", "value": None, "show": False}, _p("benchmark", "Benchmark", "SPY", "For beta."), rng],
           "Risk", ["Yahoo Finance", "FRED"], w=20, h=14, refetch=3600000, cols=risk_cols, symbolList="peers"),
        _w("x_bond_pricer", "Bond pricer", "Price to yield (or yield to price) for the bond clicked above, settled today: accrued interest, yield to maturity, "
           "modified duration, convexity, DV01 and the price after -100, -50, +50 and +100 bp. Semiannual, 30/360, per 100 par; fixed-rate bonds; callables to maturity.",
           "markdown", "/x/bond_pricer",
           [_p("cusip", "CUSIP", None, "The bond (a click on a CUSIP above sets it)."), _p("price", "Clean price", None, "Price per 100 par; blank = last TRACE sale."),
            _p("ytm", "Yield (%)", None, "Yield to maturity in percent; overrides the price.")],
           "Credit", ["FINRA"], w=16, h=16, refetch=900000),
        _w("x_debt_maturity_wall", "Debt maturity wall", "Long-term debt principal due in each of the next five fiscal years and after, from the issuer's latest "
           "10-K (SEC XBRL), including debt schedule and principal maturities. U.S. filers only. The source menu links to its 10-K and 424B prospectus filings on EDGAR.",
           "chart", "/x/debt_maturity_wall", [_p("company", "Company", "AAPL", "U.S. ticker of the issuer.")],
           "Credit", ["SEC"], w=16, h=16, raw=True, refetch=3600000),
        _w("x_etf_overlap", "ETF overlap (N-PORT)", "Two funds' latest SEC N-PORT portfolios side by side: weight overlap (sum of the smaller weight of each common holding), "
           "the count in common, and the common holdings with both weights. Matched by CUSIP, ISIN, then name. N-PORT is quarterly and public about 60 days after the quarter.",
           "table", "/x/etf_overlap", [_p("symbol", "Fund 1", "SPY", "First fund (a registered fund that files N-PORT)."), _p("symbol2", "Fund 2", "QQQ", "Second fund.")],
           "Funds", ["SEC"], w=20, h=18, refetch=3600000,
           cols=[_col("name", "Holding", "text", pinned="left"),
                 _col("weight_a", "Fund 1 weight", tip="Weight in the first fund, percent of net assets (N-PORT).", decimalPlaces=2, suffix="%"),
                 _col("weight_b", "Fund 2 weight", tip="Weight in the second fund, percent of net assets (N-PORT).", decimalPlaces=2, suffix="%"),
                 _col("overlap", "Overlap", tip="The smaller of the two weights: what both funds hold in common, percent.", decimalPlaces=2, suffix="%")]),
        _w("x_gex_strike", "Gamma exposure by strike", "Net dealer gamma exposure (GEX) per strike ($ millions per 1% move) and options positioning, assuming dealers are long the calls and short the "
           "puts open; Black-Scholes gamma from Yahoo implied volatility. Spot and Max Pain marked. Nearest monthly expiry, or every expiry within 60 days.",
           "chart", "/x/gex_strike", SYM[:1] + [_p("expiry", "Expiries", "monthly", "Nearest monthly (third Friday) or all expiries within 60 days.", typ="tabs", options=optionsx.EXPIRY_OPTIONS)],
           "Options", YAHOO, w=22, h=16, raw=True, refetch=900000, strip=[VIEW_STRIP | {"param": "expiry"}]),
        _w("x_vol_cone", "Volatility cone", "Realised volatility (RV) cone over 10 to 126 trading days: today's value against the range of the last year (min, quartiles, max), "
           "with the at-the-money implied volatility (IV) term structure of each expiry on top.", "chart", "/x/vol_cone", SYM[:1],
           "Options", YAHOO, w=18, h=16, raw=True, refetch=900000),
        _w("x_fx_rate_diff", "Spot vs 2-year rate differential", "A currency pair against the 2-year government yield differential (base minus quote, bp): "
           "U.S. H.15 par yields, other legs from the central banks' daily curves (euro: Germany). About one year of daily data; the note gives the "
           "60-day correlation of daily changes.", "chart", "/x/fx_rate_diff",
           [_p("pair", "Pair", "EURUSD", "Currency pair.", options=risk.PAIR_OPTIONS)],
           "Rates", ["Yahoo Finance", "Bundesbank", "FRED"], w=22, h=16, raw=True, refetch=3600000, dynamicSource=True),
        _w("x_valuation_bands", "Valuation bands", "P/E (TTM), EV/EBITDA (TTM), EV/Sales (TTM), P/S (TTM) or P/B on daily closes over 3, 5 or 10 years, with the median and 10th-90th "
           "percentile band and today's percentile (historical multiples & ADR/share-class scaling). U.S. filers: SEC figures, each counted from its filing date (or deadline if earlier). "
           "Others: Yahoo annual statements, each year from 90 days after its end.",
           "chart", "/x/valuation_bands", SYM + [_p("measure", "Multiple", "pe", "Which multiple (banks: P/E, P/S and P/B only).", typ="tabs", options=valuation.OPTIONS),
                                                 _p("years", "Window", "5", "Look-back window in years.", typ="tabs", options=valuation.WINDOW_OPTIONS)],
           "Valuation", ["SEC", "Yahoo Finance"], w=24, h=16, raw=True, refetch=3600000, dynamicSource=True, strip=[VIEW_STRIP | {"param": "measure"}, VIEW_STRIP | {"param": "years"}]),
        _w("x_roic_dupont", "ROE (DuPont) and ROIC", "Per fiscal year: ROE DuPont decomposition (net margin x asset turnover x equity multiplier) and return on invested "
           "capital (ROIC) = operating income x (1 - tax rate) / (debt + equity - cash), tax rate clipped to 0-35%. Banks: DuPont only.",
           "table", "/x/roic_dupont", SYM, "Valuation", ["SEC", "Yahoo Finance"], w=16, h=16, refetch=3600000, dynamicSource=True,
           cols=[_col("fiscal_year_end", "Fiscal year end", "text", pinned="left"),
                 _col("net_margin", "Net margin", tip="Net income / revenue, percent.", decimalPlaces=1, suffix="%"),
                 _col("asset_turnover", "Asset turnover", tip="Revenue / year-end total assets, times.", decimalPlaces=2, suffix="x"),
                 _col("equity_multiplier", "Equity multiplier", tip="Year-end total assets / equity, times.", decimalPlaces=2, suffix="x"),
                 _col("roe", "ROE", tip="Net income / year-end equity = the three columns before multiplied, percent.", decimalPlaces=1, suffix="%"),
                 _col("tax_rate", "Tax rate", tip="Income tax expense / pre-tax income, clipped to 0-35% (0 when pre-tax income is negative), percent.", decimalPlaces=1, suffix="%"),
                 _col("nopat", "NOPAT (mn)", tip="Operating income x (1 - tax rate), millions of the reporting currency (named under the table).", decimalPlaces=0),
                 _col("invested_capital", "Invested capital (mn)", tip="Debt + equity - cash at the year end, millions of the reporting currency.", decimalPlaces=0),
                 _col("roic", "ROIC", tip="NOPAT / invested capital, percent; blank when invested capital is zero or negative, and for banks.", decimalPlaces=1, suffix="%")]),
    ]
