# Architecture and developer notes

This page is for people who change the code. The [README](../README.md) covers installing and using
the terminal.

## Processes

Two local processes by default (plus an optional third legacy backend via `--with-v4`), all bound to `127.0.0.1`:

| Piece | Default port | What it is |
|---|---|---|
| Terminal UI | 6950 | `terminal/server.py` (standard library only) serves the page in `terminal/static/` (plain HTML, CSS and JavaScript, no build step), the dashboards, saved state and the views the terminal computes itself (`/x/*`). |
| Data backend V5 | 6900 | OpenBB Platform 5 in `.venv-v5`: about 20 data sources (default). |
| Legacy backend V4 *(opt-in)* | 6901 | OpenBB 4.7 in `.venv-v4`, kept for sources V5 dropped (FMP, EconDB, Finviz, Intrinio, Polygon, Tiingo and others). Opt-in via `install --with-v4` and `start --with-v4` (`--skip-v4` accepted as a backward-compatible alias). |

**V5-only is the default; V4 is opt-in.** `.venv-v4` adds about 480 MB (~1.5 GB total vs ~1 GB V5-only) and a second backend process for 39 legacy cards (FMP, Intrinio, Finviz, EconDB, WSJ, including the legacy cards on `Segments & KPIs`). S&P 500 valuation multiples (`multpl.com`) are owned directly by `terminal/multpl.py` (`/x/sp500_multiples`), `Screeners & Movers > Screener` uses V5's Yahoo Finance screener, and `ETFs & Mutual Funds > ETF Profile` stays complete with 7 keyless V5 cards. With `--with-v4`, the generator includes the legacy cards and exposes V4 options in source pickers; by default (`V5-only`), it builds without them so nothing shows broken.

**Platform support.** Windows 10/11 is the officially supported and tested platform. The root `.sh` scripts (`install.sh`, `start.sh`, `stop.sh`, `setup-keys.sh`) share `scripts/launcher.py` and are kept clearly marked experimental/untested.

The browser calls the backends directly for their own widgets and calls the UI server for `/x/*` views.
`scripts/launcher.py` installs, starts (each service under a supervisor), stops and checks everything;
the root `.cmd` and `.sh` files are thin wrappers around it. Ports come from `--ui-port/--v5-port/--v4-port`,
`TERMINAL_UI_PORT/TERMINAL_V5_PORT/TERMINAL_V4_PORT`, or `local/run/ports.json` (the last start).

**Supervision.** A service that exits abnormally (any exit code other than 0, including the watchdog's 75) is
restarted, at most 5 times in 10 minutes. `stop` kills only processes this terminal started (pid files in
`local/run/`), never another program on the same port.

## File map

```
scripts/launcher.py          install / start / stop / restart / status / logs / keys / smoke / health / dashboards / test / check / snapshot / diff / run
scripts/run_backend.py       starts a backend with the runtime patches below and the freeze watchdog
scripts/build_dashboards.py  compact dashboard generator engine -> local/dashboards.json
scripts/dashboards/          pure dashboard data & spec DSL (overrides.py, layouts.py)
scripts/apply_keys.py        local/api_keys.env -> ~/.openbb_platform/user_settings.json
scripts/check_sources.py     source-contract check (--offline or live; checks built-in + v4 saved state)
scripts/project_check.py     publishable snapshots, snapshot diffs, and encoding/lint/hygiene checks
scripts/smoke_test.py        live data checks against the running backends
scripts/health_check.py      paced live card health runner -> local/notes/health-YYYY-MM-DD.csv & candidates JSON
scripts/tests/               offline checks (run_all.py is the parallel entry point) + frontend_checks.js
terminal/server.py           UI server: static files (ETag/304, gzip), /api/config, /api/warm, /api/dashboards, /api/state, /x/*
terminal/version.py          release version constant (VERSION) shared by server.py, /api/config and project_check.py
terminal/composite/          the terminal's own views package (boards, curves, policy rates, macro charts, comps, earnings...) and two-phase cache warmer (warm.py)
terminal/news.py             the /x/news view: curated official, wire and regional feeds, subject matching, ranking, duplicate folding
terminal/banks.py            FDIC BankFind + FFIEC holding-company / RSSD sync views (/x/bank_failures, /x/bank_quality, /x/bank_rssd)
terminal/bonds.py            FINRA TRACE corporate bond lookup and yield-by-maturity curve (/x/bond_lookup, /x/bond_curve), bond pricer and debt maturity wall (/x/bond_pricer, /x/debt_maturity_wall)
terminal/bond_math.py        bond yield, duration, convexity, DV01, G-spread and ISIN-from-CUSIP math for the TRACE views
terminal/cds.py              CFTC swap-repository CDS index levels and history (/x/cds_indices, /x/cds_history)
terminal/policy_path.py      CFTC OIS-implied policy paths anchored on BIS policy rates (/x/policy_path, /x/policy_path_table)
terminal/cryptovol.py        Deribit DVOL against 30-day realised volatility (/x/crypto_vol)
terminal/links.py            company jump buttons across filings, options, bonds, sector-gated FFIEC bank check and news (/x/company_links)
terminal/multpl.py           S&P 500 valuation multiples and fundamentals from multpl.com (/x/sp500_multiples)
terminal/valuation.py        3/5/10-year valuation bands (P/E, EV/EBITDA, EV/Sales, P/S, P/B) from point-in-time SEC (with D&A/share fallbacks) or ADR/FX-scaled Yahoo figures, ROIC and DuPont (/x/valuation_bands, /x/roic_dupont)
terminal/risk.py             cross-asset correlation, risk statistics and the FX 2-year rate differential (/x/correlation, /x/risk_stats, /x/fx_rate_diff)
terminal/etfs.py             ETF holdings overlap from SEC N-PORT filings (/x/etf_overlap)
terminal/optionsx.py         gamma exposure by strike with Max Pain, and the realised-volatility cone against implied (/x/gex_strike, /x/vol_cone)
terminal/fred_public.py      keyless FRED public CSV series fetcher and cache helper
terminal/xwidgets.py         widget definitions of those views
terminal/columns.py          table column layer: headers, formats, default column sets per endpoint
terminal/option_math.py      implied volatility and Greeks for substitute option chains
terminal/redact.py           strips key material from logs and error text
terminal/local_paths.py      where local/ is and what goes in it (the only place those paths are defined)
terminal/health.json         data functions broken upstream (shown in Data Explorers > Data status)
terminal/static/             app.js (layout, state, routing, startup), widgets.js (cards, errors, sources),
                             table.js, statements.js (financial statements), grid.js, controls.js, md.js,
                             securities.js (search), app.css
config/                      key template, lock files (requirements-v5/v4), MCP example
ruff.toml                    static bug-check configuration for terminal/ and scripts/
```

## Dashboards and the generator

`scripts/dashboards/` holds the plain structure and override data (`overrides.py`, `layouts.py`) while
`scripts/build_dashboards.py` is the compact generator engine (re-exporting all public data/DSL symbols) that writes
`local/dashboards.json` on every start (`launcher.py dashboards` rebuilds it; `--report` prints the layout).
Current default V5-only build: 28 dashboards, 120 tabs, 786 cards, 30 concept families; every data function placed, none by fallback (`--with-v4` adds 39 legacy cards across the same 120 tabs, 43 concept families, and 1,002 data functions).

Conventions:

- Helpers: `W(endpoint)` a backend widget, `X(view)` a terminal view, `M(preset)` a curated macro chart,
  `C(group, fact)` an SEC figure across peers, `LIB(title, selectors)` one card with a Dataset picker for a
  family of similar datasets, `CURVE()` a futures curve as a line chart, `METRICS`, `PERF`, `WATCH`.
  `W(cols=..., plot=..., sort=..., since=...)` set the default column set, chart, sort and start.
- Every data function must be placed somewhere; the build reports what is missing or placed by fallback.
  Functions broken upstream (`terminal/health.json`) are parked in Data Explorers > Data status, as
  click-to-load cards (never auto-loaded).
- The first tab of each dashboard is keyless where possible; cards that need a key the owner has not set sort
  last and carry `needsKey`.
- `RENAMED_TABS` maps old tab ids to the tab that absorbed them, so a saved layout keeps its edits after a
  merge. Tab and card ids are stable across builds.
- `CONCEPTS` groups the same data from different sources (options by exchange, ETF holdings, short interest,
  movers...). Members carry `mk` (markets covered: us, ca, nordic, crypto, other), `sym` (ticker rewrite),
  `rn`/`dp`/`p` (parameter renames, drops, fixed values) and `paid`. Duplicates listed in `SKIP` are reached
  through a card's source picker instead of being placed twice.
- `TITLES` gives plain card titles; `SORTS` fixes list order; `terminal/columns.py` `PRESETS` choose the
  default columns per endpoint.
- Column units are declared, never inferred from the values: the source's own format, `columns.classify()`
  (unit stated in the source's tooltip) or `columns.UNITS` (per widget: fraction, percent, number, integer, or
  raw with a note). `test_columns.py` fails when a recorded catalog has a number column named like a percent or
  a yield with no declared unit; the table shows undeclared columns as delivered, without a % sign.
- Date defaults may be relative: `"@days:-90"`, `"@bday:-1"` (resolved when the card loads).

## Source contract

Every `/x/*` answer carries `sources: [{name, detail, url}]` (`composite.src`, `series_source`,
`static_sources`; `dispatch` fills them in, error bodies included). The card header shows "Source: X" with a
menu to switch source and links to the originals; chart footers, PNG and CSV exports carry the same source
line. `scripts/check_sources.py` checks this live; `scripts/tests/test_sources.py` offline.

## Saved state (`local/state/state.json`)

One JSON document, saved by the page with a revision number (`_rev`): the server refuses a stale write (409),
so two open tabs cannot overwrite each other. A daily backup sits next to it.

| Key | What it holds | Edits the tab? |
|---|---|---|
| `dashboards` | user dashboards (`user:*` / edited `app:*`) and v4 per-card override records (`mode: "overrides"`) for built-in `d:*` dashboards (merged over fresh defaults on load; legacy v2/v3 copies auto-migrate once to `v: 4`) | yes |
| `groups` | ticker / country per dashboard | no |
| `watchlists`, `wlsel` | watchlists and the selected list | no |
| `peers` | peer lists per company (comps) | no |
| `libsel` | dataset picked on a library card | no |
| `srcsel` | source picked on a card (`b:widgetId` or `param:<value>`) | no |
| `viewsel` | chart strip choices (range, transform, period) | no |
| `colsel` | columns picked on a table | no |
| `cardview` | per-card view preferences: flip, chart/table, settings bar shown or hidden | no |
| `ui`, `last` | sidebar and similar preferences, last tab | no |

Only adding, removing, moving or resizing cards, changing their settings or titles, renaming tabs, or user tabs mark a
built-in tab as edited (orange dot); flips, chart/table switches and the settings bar are view preferences (`cardview`).
A state.json that cannot be parsed is never overwritten: the server moves it aside as `state.corrupt-<time>.json` and
restores the newest valid daily copy (the page says so), or answers 503 and the page shows the defaults without saving.
Writes go through a temporary file and an atomic replace. `/api/state`, `/api/dashboards`, and static/vendor files answer with an ETag (`304`), and eligible HTTP 200 text/JSON responses are gzip-compressed when the client sends `Accept-Encoding: gzip`.
In-page cleanups must change `T.S.state` and then save; otherwise a pending save overwrites them.

## Routing by ticker

For a card in a concept family, `app.js routeItem` picks the member that covers the ticker's market
(`T.marketOf`): AAPL goes to Cboe, SU.TO to TMX, BTC to Deribit, SPY holdings to Nasdaq, XIU.TO to TMX.
Automatic routing never lands on a paid plan; a source the user picked wins when it covers the ticker. When
nothing covers it the card says so and names what each source covers.

## Runtime patches (`scripts/run_backend.py`)

Applied in memory at start; no installed file is modified.

- Synchronous fetchers run in worker threads, so one slow source cannot freeze the event loop.
- Yahoo's daily FX "close" is that date's 00:00 London rate (the open). Histories through `yf_download` close at 17:00
  New York from hourly bars (last 2 years), older days on the next bar's open, days before 2010-08-04 on Yahoo's own
  close, which was real then (`fx_daily_bars`, `test_fx.py`). The TradingView datafeed is routed through the same
  bars, and its header counts from the previous of those closes (`install_fx_chart_fix`).
- Output streams are guarded (no "I/O operation on closed file" crash), and log records are redacted.
- Disk cache for slow providers (EIA, FiscalData, Fama-French, CFTC, USDA, BLS, IMF) in `local/cache/backend/`,
  with a bounded wait and background fill.
- OECD fails fast on HTTP 429 with a readable message.
- EIA returns the latest 5,000 rows unless a limit is set; at most 4 EIA calls in flight, 3 retries.
- SEC company facts: standalone quarters of year-to-date items (cash flows) are derived per fiscal year, chained by
  period start rather than by the filing's `fy` (which mixes years and dropped most quarters).
- Yahoo analyst actions: newest first, and no 0.00 price target where a firm published none.
- Yahoo futures curves: only months that trade (3-month average volume of at least 10 contracts a day), priced at the
  bid/ask mid when both sides are quoted within 1%, else the previous close (a `basis` column says which); for
  precious metals a month more than 3% off a robust line through the curve is left out (stale or off-market quotes).
  The stock fetcher halved one-sided quotes and drew 0.00 for unquoted months. CBOT 30-day Fed funds (`ZQ`) rows
  also carry `implied_rate` = 100 - price, in percent (`FUT_RATE_ROOTS`).
- Cboe `delayed_quotes` refused (HTTP 403; never worked around): `derivatives/options/chains?provider=cboe` and options
  analytics share a concurrent Yahoo chain loader at the regular-session price and time and calculate IV and Greeks
  (`terminal/option_math.py`: parity forward, Black-76, one IV per strike; Yahoo's own IV is a placeholder without
  live quotes; `^GSPC` aliases to `SPX`). The VIX curve and futures list come from Cboe's settlement file, U.S. index
  snapshots from Yahoo. `note_substitute` sends `X-Terminal-Source` and `X-Terminal-Fallback`, which the card shows
  as its Source and footer note. An async fetcher's `extract_data` must be the coroutine itself: `asyncio.run` inside
  it binds openbb_cboe's shared HTTP cache to a dead event loop.
- Request guard: Host allow-list (blocks DNS rebinding), Origin must be the UI (any local host name, case-insensitive),
  requests without an Origin are refused when `Sec-Fetch-Site` says cross-site, CORS names that origin only, error
  bodies redacted, unhandled pre-header exceptions return sanitized HTTP 500 JSON with CORS headers;
  `/_terminal/warm_chart` pre-warms TradingView bars/symbol metadata inside the guard without altering OpenAPI. The UI page carries a Content-Security-Policy (server.py `content_security_policy`).
- TradingView widget: `install_tvchart_fix` shares bounded LRU caches across `yf_symbol_info` (600 s) and `BarCache`
  (300 s), never caching empty results; strips non-finite `NaN`/`Inf` bars, repairs active-session daily bars from
  Yahoo metadata, forces `allow_nan=False` in `pywry` WebSocket frames, delays WebSocket room teardown across transient
  reloads, adds callback workers, and injects `#obb-tv-loading` (with retry/timeout recovery) / `openbb-chart-ready`
  into `tvchart_widget_html`.
- Reliability patches (`install_blocking_route_offload`, `install_session_leak_fix`, `install_yfinance_options_fix`,
  `install_tmx_gainers_fix`, `install_nasdaq_empty_json_fix`, `install_user_settings_cache`,
  `install_disable_mcp_subprocesses`) offload blocking routes to worker threads, reclaim `ClientSession` pools,
  tolerate empty HTTP 204 bodies and missing TMX string fields, and skip unused child MCP subprocesses.
- Each fix that does not apply logs one `[fixes] WARNING` line; `local/run/patches-<name>.json` feeds the
  "Backend fixes" table in Data Explorers > Data status.
- Watchdog: if the backend stops answering for 45 s it writes every thread's stack to
  `local/logs/<name>-freeze-*.txt` and exits with code 75; the supervisor restarts it.

## Tests

```
python scripts/launcher.py test [pattern...] [--fast]  # parallel offline checks (~5 s; --fast skips headless browser)
python scripts/launcher.py check                       # BOM/LF/CRLF, AST/JSON, ruff, hygiene/secrets, offline sources, state, notes
python scripts/launcher.py snapshot [--label NAME]     # zip publishable tree to local/snapshots/ (skips if unchanged)
python scripts/launcher.py diff [--stat]               # diff working tree against the latest snapshot
python scripts/launcher.py restart ui                  # fast (~2 s) UI-only restart after editing terminal/*.py
python scripts/launcher.py logs [ui|v5|v4] [--errors]  # tail logs, freeze dumps and backend patch counts
python scripts/launcher.py smoke                       # live data checks against the running backends
python scripts/launcher.py health --dry-run            # paced card health runner (add --phase/--filter/--limit/--resume)
python scripts/check_sources.py [--offline]            # source-contract check (live or against recorded catalogs)
```

Use the environment's Python (`.venv-v5\Scripts\python.exe` on Windows, `.venv-v5/bin/python` elsewhere).
Each `test_*.py` runs in its own process with a temporary `local/` folder cleaned up at exit:

| File | Covers |
|---|---|
| `test_numbers.py`, `test_fx.py`, `test_returns.py`, `test_rates.py`, `test_ev.py`, `test_peer_ratios.py`, `test_gauges.py` | FX direction, 17:00 NY FX closes, TradingView FX fix, currency strength, yield changes in bp, total/price returns, EV currency normalization, peer ratios, regional/Fed gauges, `country_monitor`, `_rolling`, `CALCS`, `chokepoint_monitor` |
| `test_curves.py`, `test_policy_path.py`, `test_cds.py`, `test_bonds.py`, `test_banks.py`, `test_cryptovol.py` | Sovereign curve parsers/store, CFTC OIS policy path, CDS index conventions, FINRA TRACE bond lookup, FDIC/FFIEC bank views, Deribit DVOL vs realised vol |
| `test_earnings.py`, `test_snapshot.py`, `test_sec_quarters.py`, `test_sec_growth.py`, `test_futures.py`, `test_funds.py`, `test_nasdaq_etf.py` | Earnings model & implied move, snapshot text, SEC YTD-derived quarters & growth units, futures curves, fund multiples/TMX holdings, Nasdaq ETF fallback |
| `test_columns.py`, `test_sources.py`, `test_trust.py`, `test_timezone.py`, `test_news.py`, `test_fred.py`, `test_links.py`, `test_multpl.py` | Column headers/formats/units, source contract & v4 saved-state check, stale/delayed/fallback trust signals, Montreal timezones, news ranking/dedup, keyless FRED CSV, company jump links, multpl.com S&P 500 valuation multiples |
| `test_medium.py`, `test_low.py`, `test_p4.py` | FRED cache slicing, weekly YoY, STEO forecast split, BIS daily policy, non-trading-day bases, SWR per-symbol history cache, cache eviction |
| `test_generator.py` | routing by ticker; full build replayed from the catalogs the last live build recorded in `local/cache/catalog-*.json`: coverage, unique and stable ids, renamed tabs, V5-only default build without V4 |
| `test_cboe_fallback.py` | Black-76 by hand, parity forward, blank-not-zero, substitute loader and chart builders |
| `test_valuation.py`, `test_risk.py`, `test_bond_math.py`, `test_etf_options.py` | Point-in-time multiples and percentile bands, ROIC/DuPont, return frequency, beta, drawdown and rate differentials, bond yield/duration/DV01/G-spread and maturity wall, N-PORT overlap matching, GEX, Max Pain, monthly expiry choice and the volatility cone |
| `test_redact.py`, `test_backend_wrapper.py` | key redaction, stream guard, log redaction, Host/Origin guard and sanitized 500s, TradingView bar sanitizing/daily-bar repair, NaN/Inf JSON scrubbing, TMX gainers fix |
| `test_launcher_server.py` | restart policy, stop scope, V5-only default / `--with-v4` / `--skip-v4` CLI, UI server Host/Origin/state/config rules, v4 override validation, corrupt state recovery, ETags, CSP, keys applied on start, cache pruning, `VERSION` in `/api/config`, strict finite JSON and NaN `PUT` rejection, port guards |
| `test_health.py`, `test_workflow.py` | relative NY dates, card/choice URL builder, error classifier, secret redaction, mock OECD-429 health run; publishable manifest, snapshots, snapshot diffs, EOL/BOM/hygiene checks, parallel test filter |
| `test_frontend.py` | runs `frontend_checks.js` (288 browser checks) in headless Edge (or Chrome) against a temporary UI server; skipped with a notice when no such browser is installed |

`frontend_checks.js` holds the browser-side checks (redaction, error classifier, relative dates, CSV formula
escaping, v4 per-card override diff/merge/migration, market routing, column formats, statements, view preferences,
request slot queuing): `test_frontend.py` runs it headless; it can also be pasted into the developer console of an
open Libre Terminal page.

`launcher.py start` applies `local/api_keys.env` when it changed since the last apply (`local/run/keys-applied.json`).
A supervisor that gives up writes `local/run/<name>.stopped.json`; `/api/services` reports it and the page stops waiting.

## Conventions

- **Dependencies:** Server and launcher code (`terminal/server.py`, `scripts/launcher.py`, `scripts/health_check.py`,
  `scripts/project_check.py`) stay standard-library only. Overall, no dependency beyond what `.venv-v5` already installs
  (such as Plotly's JS bundle, `xlrd`, and `ruff` for checks) and no front-end build step.
- **Frozen OpenBB policy:** `config/requirements-*.lock.txt` stay pinned; OpenBB packages are treated as a frozen
  catalogue. Critical-path views get thin, tested fetchers under `terminal/`, while broken long-tail OpenBB endpoints
  are parked in `terminal/health.json` rather than growing `scripts/run_backend.py`.
- **Encoding & line endings:** Files are UTF-8 without BOM. `.py`, `.js`, `.css`, `.html`, `.json`, `.md`, `.sh`,
  `.toml`, `.in`, `.env`, `.txt` use LF; `.cmd` and `.ps1` use CRLF (enforced by `.gitattributes` and `launcher.py check`).
- **Runtime isolation:** Never write outside `local/` at run time; never commit `local/` (git-ignored).
- **Restart scope:** After editing `terminal/*.py`, run `launcher.py restart ui` (~2 s); after editing `scripts/run_backend.py`,
  run `launcher.py restart backends` (or `stop` then `start`).

## Completed refactors

`terminal/composite/` (split from the 5,095-line `terminal/composite.py` alongside `news.py`, `banks.py`, `bonds.py`,
`cds.py`, `policy_path.py`, `cryptovol.py`, `links.py`, `multpl.py`, `fred_public.py`, and the later `valuation.py`, `risk.py`,
`bond_math.py`, `etfs.py`, `optionsx.py`) is the terminal's composite
views package, keeping the exact import name `composite` with zero behaviour change. `terminal/composite/__init__.py`
re-exports `dispatch`, `warm`, `CompositeError`, `ROUTES` (all 54 routes), the 9 option lists `xwidgets.py` imports by
reference, `time`, and 317 re-exported symbols (plus `dispatch`, wrappers, and `ROUTES`). Submodules import `composite as C` and look up cross-module calls
and patchable globals through `C.<name>` at runtime so offline tests can monkey-patch `composite` attributes directly.

```
terminal/composite/
  __init__.py          re-exports; dispatch, static_sources, sibling wrappers, consolidated ROUTES (54 routes)
  core/   http.py      backend URLs (one port reader), http_json, api, _download, SSL context, source helpers
          cache.py     cached, cached_disk, backoff, _curve_store, context-propagating pool, stale notes
          dates.py     horizons, range windows, period and quarter helpers
          series.py    returns, yoy, rolling, interpolation, number helpers
          figures.py   palette, layout, _note, NBER recession shading
  markets/ prices.py   history, per-symbol SWR disk cache, ohlc, FX conversion
           boards.py   BOARDS, quote rows, performance, style box, watchlist, FX matrix
           fx.py       currency strength: pair as quoted / vs USD / own strength (vs the other seven majors)
           chart.py    compare chart
  rates/   curve_sources.py  Bundesbank, MoF, BoC, Riksbank, BoE, Tesouro, OECD fetchers
           curves.py   countries, curves, curve table, global yields
           policy.py   BIS policy rates and history
           oecd.py     OECD series, rate history, rates by country
  macro/   sets.py     MACRO_SETS, categories, and MACRO_OPTIONS
           sources.py  FRED (backend + public CSV), EIA, calculated series
           views.py    macro chart, seasonal, CPI history, country monitor
  geo/     gpr.py, shipping.py
  equity/  lookup.py (search, snapshot), comps.py, earnings.py, filings.py (SEC facts, compare)
  status.py, warm.py
```

Phase 2B (W9 + W8) is also complete: `scripts/build_dashboards.py` data tables and spec DSL moved into
`scripts/dashboards/{overrides.py,layouts.py}` with the compact generator engine re-exporting all public symbols;
`scripts/run_backend.py` `CACHE_POLICY` is now the sole authoritative persistent cache (covering EIA, Fama-French,
FiscalData, CFTC, BLS, USDA, IMF, OECD 12 h / `attempts=1`, ECB 6 h, Federal Reserve 3 h, FRED 3 h, JODI 12 h with
`_is_cacheable_result()` and `X-Terminal-Refresh: 1` manual bypass), legacy browser Cache API `obb-slow-v1` is
retired (one-time `caches.delete("obb-slow-v1")` on boot), and `terminal/server.py` runs `local_paths.prune_local()`
every 24 h in a daemon thread (audit B9).

## Using the data from Python or MCP

```python
# with .venv-v5's Python
from openbb import obb
obb.sec.financial_statements(symbol="MSFT").to_df()
```

`config/mcp.example.json` shows how to register OpenBB's MCP server (`openbb-mcp`, installed in `.venv-v5`)
with a tool that supports MCP: copy it to `.mcp.json` in the project root (git-ignored, it holds an absolute
path) and fill in the path.
