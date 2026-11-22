"""Composite market views for the Terminal UI.

Everything here is computed from the two local OpenBB backends (V5, default
port 6900; V4, default 6901) plus a few keyless public sources (BIS policy
rates, FRED's public CSV download, GPR, IMF PortWatch).
Standard library only. Results are cached in memory for a few minutes; slow or
rate-limited sources (OECD, BIS, FRED, EIA, SEC facts, curves) also on disk in local/cache/."""

from __future__ import annotations

import time as time

from .core.http import (  # noqa: F401
    V5, V4, CompositeError, http_json, _which, api_full, api, src, uniq_sources, YAHOO_URL, yahoo_source,
    _SSL, _ssl_context, BROWSER_UA, _download,
)
from .core.cache import (  # noqa: F401
    CACHE_DIR, _cache, _locks, _guard, cached, _backoff, disk_name, _STALE_SINCE, _STALE_NOTES, _SWR_NOTES, _CtxPool,
    _CfShim, cf, _note_stale, _note_stale_ts, _note_swr_ts, _age_text, stale_text, cached_text, mtl_time, cached_disk,
    _write_json, cached_complete, _curve_store,
)
from .core.dates import (  # noqa: F401
    minus_months, HORIZON_MONTHS, horizon_date, RANGE_YEARS, DAILY_RANGES, MONTHLY_RANGES,
    QUARTERLY_RANGES, ANNUAL_RANGES, range_start, freq_of, FREQ_RANGES, _month_start, _period_date,
    _next_month, _quarter, _date_span, _today,
)
from .core.series import (  # noqa: F401
    _at_or_before, _base, _ret, _diff_bp, total_return, _num_or_none, _nearest, _pick, _interp, _latest,
    _split, _yoy, yoy_difference, transform_unit, _rolling, _sma, drawdown, _big, _num, _pc,
)
from .core.figures import (  # noqa: F401
    PALETTE, INK, UP, DOWN, _layout, _note, RECESSION_FILL, recession_periods, _recessions,
    add_recession_shading, SHADE_OPTIONS,
)
from .markets.prices import (  # noqa: F401
    PriceHistory, _fetch_history, HIST_TTL, HIST_STALE_TTL, PARTIAL_TTL, _HIST_REVALIDATING,
    _sym_hist_path, _sym_hist_load, _sym_hist_save, _sym_hist_get, _bg_refresh_history, history,
    PRICE_ONLY, is_security, returns_basis, CONVENTION, CONVENTION_SHORT, YIELD_SYMBOLS, is_yield,
    fx_pair, fx_pair_label, _invert, _usd_rates, _ohlc,
)
from .markets.boards import (  # noqa: F401
    BOARDS, BOARD_OPTIONS, _resolve_board, board, null_warning, STALE_ROW_DAYS, _mark_stale,
    _with_convention, BP_FMT, HORIZONS, currency_view, FUTURES_UNITS, price_decimals, _quote_row,
    _labels, performance, perf_bars, STYLE_BOX, style_box, BOARD_LABELS, STARTER_WATCHLIST, watchlist,
    FX_MATRIX, fx_matrix,
)
from .markets.chart import CHART_VIEWS, chart  # noqa: F401
from .rates.curve_sources import (  # noqa: F401
    COUNTRIES, CURVE_SOURCES, CURVE_URLS, CURVE_SHORT, CURVE_KIND, curve_label, FRED_CURVE, curve_source,
    _src_fred_us, COUNTRY_OPTIONS, PLAIN_COUNTRY_OPTIONS, TENORS, CURVE_DAYS, _curve_since, _curve_fetch,
    _src_buba, _src_mof, BOC_SERIES, _src_boc, RIKS_SERIES, _src_riks, _xlsx_rows, _src_boe,
    _src_tesouro, _src_oecd, HIST_SOURCES, _backend_curve,
)
from .rates.curves import (  # noqa: F401
    curves, comparison_date, _max_gap, compare_target, _us_fallback, yield_curves, yield_curve_table,
    _curve_table, G10_KEYS, global_yields,
)
from .rates.policy import (  # noqa: F401
    ISO_NAMES, POLICY_OPTIONS, EURO_MEMBERS, BIS_SOURCE, _bis_rates, _bis_policy, _bis_policy_daily,
    DAILY_POLICY_MAX_AGE, _policy_series, _policy_row, policy_rates, _policy_history_points,
    policy_rate_history,
)
from .rates.oecd import (  # noqa: F401
    _oecd_series, _oecd_latest, OECD_ALIAS, DURATIONS, _oecd_key, FRED_MEI, FRED_MEI_PREFIX, oecd_source,
    _fred_mei, FALLBACK_OECD, FALLBACK_US_CURVE, rate_history, _month_ago, STALE_MONTHS, LEG_NAMES,
    _rate_rows, rates_by_country,
)
from .macro.sets import MACRO_SETS, MACRO_CATEGORY, MACRO_OPTIONS, SOURCE_NAMES, CALC_TEXT  # noqa: F401
from .macro.sources import (  # noqa: F401
    _fred, _fred_csv, _fred_points, _eia_key, _eia_one, CALCS, _series, _fred_any, _eia_url,
    series_source,
)
from .macro.views import (  # noqa: F401
    seasonal_bucket, _seasonal, macro_chart, MONITOR, MONITOR_LABELS, _imf_cpi, IMF_ISO3, _imf_key,
    _oecd_all, _oecd_for, country_monitor, IMF_CPI_SOURCE, cpi_history,
)
from .geo.gpr import _gpr, GPR_NAMES, gpr_table  # noqa: F401
from .geo.shipping import PORTWATCH_FIELDS, _chokepoints, CHOKEPOINT_ORDER, chokepoint_monitor  # noqa: F401
from .equity.lookup import TYPE_NAMES, MINOR_UNITS, _syms, search, META_TTL, _meta, _first, snapshot  # noqa: F401
from .equity.comps import (  # noqa: F401
    _slug, _norm_name, _profiles, _yahoo_recs, _peers, COMP_FIELDS, _METRICS, _ev_parts, comps,
)
from .equity.earnings import (  # noqa: F401
    EARN_PERIODS, _STORE_LOCK, _est_raw, _report_info, _timing_from_acceptance, _earnings_release,
    _filing_documents, _last_price, _consensus_store, _f, _surprise, _cons, _two_day, _yahoo_q0_is,
    _saved_cons, _yahoo_quarter_chart, _summary_revenue, _opt_leg_price, _parse_option_chain,
    _implied_move, _options_chain, _earn_model, _eps_txt, _pct_txt, _range_txt, _earn_sources,
    earnings_call_link, earnings, earnings_reports, earnings_release, earnings_history, consensus,
)
from .equity.filings import (  # noqa: F401
    COMPARE_GROUPS, GROUP_OPTIONS, FACTS, FACT_OPTIONS, _facts, DERIVED, MEASURES, MEASURE_OPTIONS,
    _period_pts, ratio_pct, combine, yoy_pct, compare,
)
from .status import BACKEND_NAMES, _patch_status_md, data_status  # noqa: F401
from .warm import _safe, warm  # noqa: F401

def dispatch(name, params):
    fn = ROUTES.get(name)
    if fn is None:
        raise CompositeError(f"Unknown view '{name}'.")
    notes, swr_notes = [], []
    token = _STALE_NOTES.set(notes)
    swr_token = _SWR_NOTES.set(swr_notes)
    try:
        out = fn(**params)
    finally:
        _SWR_NOTES.reset(swr_token)
        _STALE_NOTES.reset(token)
    if isinstance(out, dict) and not out.get("sources"):
        out = dict(out, sources=static_sources(name, params))       # the cached object itself is never changed
    if isinstance(out, dict) and notes:
        out = dict(out, stale=stale_text(notes))
    elif isinstance(out, dict) and swr_notes:
        out = dict(out, cached=cached_text(swr_notes))
    if isinstance(out, dict) and name in DELAYED_VIEWS and "delayed" not in out and not str(params.get("symbol") or "").upper().startswith("FRED:"):
        out = dict(out, delayed=DELAYED_YAHOO)          # HANDOFF P2: live prices from a free feed say they are delayed
    return out

DELAYED_VIEWS = {"board", "watchlist", "fx_matrix", "chart", "snapshot", "perf_bars", "performance", "comps"}

DELAYED_YAHOO = "Delayed prices from Yahoo Finance (free feed, not real time)"

def static_sources(name, params=None):
    """The sources a view uses for these parameters, worked out without fetching anything (for a card whose request failed,
    and for the offline check that every card names a real source)."""
    p = params or {}
    name = str(name)
    if name in ("board", "perf_bars"):
        syms = [s for _, items in _resolve_board(p.get("preset") or ("us_equity" if name == "board" else "us_sectors"), p.get("symbols")) for _, s in items]
        return [yahoo_source(syms)]
    if name == "performance":
        syms = list(_labels(p["preset"], None)) if p.get("preset") and not p.get("symbols") else _split(str(p.get("symbols") or "^GSPC,^NDX,^DJI").replace(";", ","))
        return [yahoo_source(syms)]
    if name == "style_box":
        return [yahoo_source([s for _, row in STYLE_BOX for s in row], "Russell style ETFs, delayed daily closes")]
    if name == "fx_matrix":
        return [yahoo_source([c + "USD=X" for c in FX_MATRIX if c != "USD"], "currency quotes")]
    if name == "watchlist":
        return [yahoo_source(_syms(p.get("symbols") or STARTER_WATCHLIST, 80))]
    if name == "earnings_release":
        return [src("SEC", "earnings releases: Form 8-K, Item 2.02", "https://www.sec.gov/edgar/search/")]
    if name in ("earnings", "earnings_history", "consensus", "earnings_reports"):
        return [src("Yahoo Finance", "consensus estimates, reported EPS and revenue, prices", YAHOO_URL)] + \
            ([src("Nasdaq", "report timing and fiscal quarter (earnings calendar)", "https://www.nasdaq.com/market-activity/earnings"),
              src("SEC", "earnings release: Form 8-K, Item 2.02", "https://www.sec.gov/edgar/search/")] if name == "earnings" else [])
    if name in ("comps", "snapshot", "search"):
        return [src("Yahoo Finance", "company profile, quote, valuation and consensus" if name != "search" else "symbol search", YAHOO_URL)]
    if name == "chart":
        syms = _syms(p.get("symbol") or "^GSPC", 1) + _syms(p.get("compare"), 7)
        out = [yahoo_source([s for s in syms if not s.startswith("FRED:")])] if any(not s.startswith("FRED:") for s in syms) else []
        return out + [series_source(s[5:]) for s in syms if s.startswith("FRED:")]
    if name == "macro_chart":
        custom = _split(p.get("series"))
        ids = [s if ":" in s else s.upper() for s in custom[:8]] if custom else [i[1] for i in MACRO_SETS.get(p.get("preset") or "credit_spreads", (0, 0, 0, []))[3]]
        if str(p.get("transform") or "").lower() == "seasonal" or (not custom and MACRO_SETS.get(p.get("preset") or "", (0, 0, None))[2] == "seasonal" and not p.get("transform")):
            ids = ids[:1]
        return uniq_sources(series_source(i) for i in ids)
    if name in ("yield_curves", "yield_curve_table"):
        cs = _split(p.get("country") or ("united_states,euro_area,united_kingdom,japan" if name == "yield_curves" else "united_states"))
        cs = cs[:4] if name == "yield_curve_table" else cs
        return uniq_sources(curve_source("fred" if (COUNTRIES[c][1] == "fed" and p.get("source") == "fred") else COUNTRIES[c][1], COUNTRIES[c][0]) for c in cs if c in COUNTRIES)
    if name == "global_yields":
        return [curve_source(c) for c in CURVE_SHORT if c != "fred"] + [BIS_SOURCE]
    if name in ("policy_rates", "policy_rate_history"):
        return [BIS_SOURCE]
    if name == "rate_history":
        d = p.get("duration") if p.get("duration") in DURATIONS else "long"
        if p.get("source") == "fred":
            return [src("FRED", f"{FRED_MEI_PREFIX[d]}xxM156N (OECD series republished by FRED)", "https://fred.stlouisfed.org/")]
        return [oecd_source(d)]
    if name == "rates_by_country":
        return [src("FRED", "OECD interest-rate series republished by FRED", "https://fred.stlouisfed.org/")] if p.get("source") == "fred" else [oecd_source(d) for d in DURATIONS]
    if name == "compare":
        fk = p.get("fact") or "capex"
        keys = list(DERIVED[fk][1:3]) if fk in DERIVED else [fk if fk in FACTS else "capex"]
        if p.get("measure") == "pct_revenue":
            keys.append("revenue")
        what = "; ".join(f"{FACTS[k][0].lower()} ({', '.join(FACTS[k][1])})" for k in dict.fromkeys(keys))
        return [src("SEC", f"XBRL company facts, {what}", "https://www.sec.gov/edgar/search/")]
    if name == "chokepoint_monitor":
        return [src("IMF PortWatch", "daily transit calls by chokepoint (AIS ship tracking)", "https://portwatch.imf.org/")]
    if name == "gpr_table":
        return [src("Caldara & Iacoviello", "Geopolitical Risk Index by country, monthly", "https://www.matteoiacoviello.com/gpr.htm")]
    if name == "cpi_history":
        return [IMF_CPI_SOURCE]
    if name == "country_monitor":
        return [src("OECD", "real GDP and unemployment, latest actuals", "https://data-explorer.oecd.org/"), IMF_CPI_SOURCE, BIS_SOURCE] + static_sources("global_yields")[:-1]
    if name == "company_links":
        import links as _links                    # imported late: links.py imports this module
        return _links.SOURCES
    if name in ("bond_lookup", "bond_curve"):
        import bonds as _bonds                    # imported late: bonds.py imports this module
        return [_bonds.SOURCE_FINRA, _bonds.SOURCE_SEC]
    if name in ("cds_indices", "cds_history"):
        import cds as _cds                        # imported late: cds.py imports this module
        return [_cds.SOURCE]
    if name == "crypto_vol":
        import cryptovol as _cv                   # imported late: cryptovol.py imports this module
        return _cv.SOURCES
    if name in ("policy_path", "policy_path_table"):
        import policy_path as _pp                 # imported late: policy_path.py imports this module
        return _pp.static_sources(p.get("currency"), name == "policy_path_table")
    if name == "bank_failures":
        return [src("FDIC", "BankFind Suite API (failures, institutions, quarterly financials)", "https://banks.data.fdic.gov/docs/")]
    if name == "bank_rssd":
        return [src("FFIEC NIC", "holding company identified by ticker (RSSD id)", "https://www.ffiec.gov/npw")]
    if name == "bank_quality":
        return [src("FFIEC NIC", "holding company identified by ticker (RSSD id)", "https://www.ffiec.gov/npw"),
                src("FDIC", "BankFind Suite API (failures, institutions, quarterly financials)", "https://banks.data.fdic.gov/docs/")]
    if name == "news":
        return [src("Terminal news", "official, wire and regional RSS feeds and Google News search, ranked by subject (feeds named per answer)", "")]
    if name == "data_status":
        return [src("Terminal", "last dashboard build and terminal/health.json", "")]
    if name == "sp500_multiples":
        import multpl as _mp                        # imported late: multpl.py imports this module
        return _mp.static_sources(p.get("series_name"))
    return []

def news_view(**params):
    """Headlines for a subject (ranked) or the situation-room monitor (newest first): terminal/news.py."""
    import news as _news                         # imported late: news.py itself imports this module
    return _news.news(**params)

def bank_failures_view(**params):
    import banks as _banks                       # imported late: banks.py itself imports this module
    return _banks.bank_failures(**params)

def bank_quality_view(**params):
    import banks as _banks
    return _banks.bank_quality(**params)

def bank_rssd_view(**params):
    import banks as _banks
    return _banks.bank_rssd(**params)

def policy_path_view(**params):
    """OIS-implied policy path, six central banks (HANDOFF P3-3): terminal/policy_path.py."""
    import policy_path as _pp
    return _pp.chart(**params)

def policy_path_table_view(**params):
    import policy_path as _pp
    return _pp.table(**params)

def cds_indices_view(**params):
    """Index CDS levels from swap-repository prints (HANDOFF P3-2): terminal/cds.py."""
    import cds as _cds
    return _cds.table(**params)

def cds_history_view(**params):
    import cds as _cds
    return _cds.history(**params)

def crypto_vol_view(**params):
    """DVOL against 30-day realised volatility (review H4): terminal/cryptovol.py."""
    import cryptovol as _cv
    return _cv.chart(**params)

def bond_lookup_view(**params):
    """Corporate bonds of a company from FINRA TRACE (HANDOFF P3-1): terminal/bonds.py."""
    import bonds as _bonds
    return _bonds.lookup(**params)

def bond_curve_view(**params):
    import bonds as _bonds
    return _bonds.curve(**params)

def company_links_view(**params):
    """Company jump buttons (HANDOFF P3-4): terminal/links.py."""
    import links as _links
    return _links.links(**params)

def sp500_multiples_view(**params):
    """S&P 500 valuation multiples from multpl.com (W3): terminal/multpl.py."""
    import multpl as _mp
    return _mp.chart(**params)


ROUTES = {
    'board': board, 'performance': performance, 'perf_bars': perf_bars, 'style_box': style_box,
    'yield_curves': yield_curves, 'yield_curve_table': yield_curve_table, 'global_yields': global_yields,
    'policy_rates': policy_rates, 'policy_rate_history': policy_rate_history,
    'rates_by_country': rates_by_country, 'rate_history': rate_history, 'macro_chart': macro_chart,
    'compare': compare, 'chokepoint_monitor': chokepoint_monitor, 'gpr_table': gpr_table,
    'search': search, 'watchlist': watchlist, 'chart': chart, 'comps': comps, 'snapshot': snapshot,
    'earnings': earnings, 'earnings_history': earnings_history, 'consensus': consensus,
    'earnings_reports': earnings_reports, 'earnings_release': earnings_release,
    'country_monitor': country_monitor, 'cpi_history': cpi_history, 'fx_matrix': fx_matrix,
    'data_status': data_status, 'news': news_view, 'bank_failures': bank_failures_view,
    'bank_quality': bank_quality_view, 'bank_rssd': bank_rssd_view, 'policy_path': policy_path_view,
    'policy_path_table': policy_path_table_view, 'cds_indices': cds_indices_view,
    'cds_history': cds_history_view, 'crypto_vol': crypto_vol_view, 'bond_lookup': bond_lookup_view,
    'bond_curve': bond_curve_view, 'company_links': company_links_view,
    'sp500_multiples': sp500_multiples_view,
}
