# Architecture and developer notes

This page is for people who change the code. The [README](../README.md) covers installing and using the terminal.

## Processes

Two local processes by default, plus an optional third (legacy) backend with `--with-v4`. All of them bind to `127.0.0.1`.

| Piece | Default port | What it is |
|---|---|---|
| Terminal UI | 6950 | `terminal/server.py` (standard library only) serves the page in `terminal/static/` (plain HTML, CSS and JavaScript, no build step), the built dashboards, saved state and the views the terminal computes itself (`/x/*`). |
| Data backend V5 | 6900 | OpenBB Platform 5.0 in `.venv-v5`, the default backend. |
| Legacy backend V4 *(opt-in)* | 6901 | OpenBB 4.7 in `.venv-v4`, kept for sources V5 dropped (FMP, EconDB, Finviz, Intrinio, Polygon, Tiingo and others). Opt-in with `install --with-v4` and `start --with-v4`. `--skip-v4` forces V5-only, even with `--with-v4`. |

**V5-only is the default; V4 is opt-in.** `.venv-v4` adds about 480 MB (about 1.5 GB in total, against about 1 GB for V5-only) and a second backend process for 39 legacy cards (FMP, Intrinio, Finviz, EconDB, WSJ, including the legacy cards on `Segments & KPIs`). S&P 500 valuation multiples (`multpl.com`) come from `terminal/multpl.py` (`/x/sp500_multiples`). `Screeners & Movers > Screener` uses V5's Yahoo Finance screener, and `ETFs & Mutual Funds > ETF Profile` stays complete with 7 keyless V5 cards. With `--with-v4` the generator includes the legacy cards and exposes V4 options in source pickers. By default it builds without them, so nothing shows broken.

**Platform support.** Windows 10/11 is the officially supported and tested platform. The root `.sh` scripts (`install.sh`, `start.sh`, `stop.sh`, `setup-keys.sh`) share `scripts/launcher.py` and are marked experimental and untested.

The browser calls the backends directly for their own widgets and calls the UI server for `/x/*` views.
`scripts/launcher.py` installs, starts (each service under a supervisor), stops and checks everything. The root `.cmd` and `.sh` files are thin wrappers around it. Ports come from `--ui-port/--v5-port/--v4-port`, the `TERMINAL_UI_PORT/TERMINAL_V5_PORT/TERMINAL_V4_PORT` environment variables, or `local/run/ports.json` (the last start).

**Supervision.** A service that exits abnormally (any exit code other than 0, including the watchdog's 75) is restarted after pauses of 2, 5, 15 and 30 seconds. After 5 abnormal exits within 10 minutes the supervisor gives up and writes `local/run/<name>.stopped.json`; `/api/services` reports it and the page stops waiting. `stop` kills only processes this terminal started (pid files in `local/run/`), never another program on the same port. `start` applies `local/api_keys.env` when it has changed since the last apply (`local/run/keys-applied.json`).

## File map

```
scripts/launcher.py          install / start / stop / restart / status / logs / keys / smoke / health / dashboards / test / check / snapshot / diff / run
scripts/install.ps1          Windows installer behind Install.cmd: makes sure uv is present, then runs launcher.py install
scripts/run_backend.py       starts a backend with the runtime patches below and the freeze watchdog
scripts/build_dashboards.py  compact dashboard generator engine -> local/dashboards.json
scripts/dashboards/          pure dashboard data & spec DSL (overrides.py, layouts.py)
scripts/apply_keys.py        local/api_keys.env -> ~/.openbb_platform/user_settings.json
scripts/check_sources.py     source-contract check (--offline or live; checks built-in + v4 saved state)
scripts/project_check.py     publishable snapshots, snapshot diffs, and encoding/lint/hygiene checks
scripts/smoke_test.py        live data checks against the running backends
scripts/health_check.py      paced live card health runner -> local/notes/health-YYYY-MM-DD.csv & candidates JSON
scripts/gate.py              release gate G1-G5 (launcher.py gate) -> local/notes/gates-YYYY-MM-DD.json
scripts/probe.py             stdlib live probe of a running terminal: status counts, p50/p95 latency, envelope checks per view
scripts/tests/               offline checks (run_all.py is the parallel entry point) + frontend_checks.js
terminal/server.py           UI server: static page, dashboards, saved state, service status and restart, /x/* views (see UI server)
terminal/version.py          release version constant (VERSION) shared by server.py, /api/config and project_check.py
terminal/composite/          the terminal's own views, split by topic (core/, markets/, rates/, macro/, geo/, equity/); warm.py is the two-phase cache warmer
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
terminal/static/             request.js (T.request: the only fetch() caller, bounded waits; T.httpError and
                             T.renderError: one error renderer keyed on error.kind; T.asOfText), app.js (layout,
                             state, routing, startup), widgets.js (cards, errors, sources; every card footer shows
                             sources, data as-of, status and notes from the envelope), table.js, statements.js
                             (financial statements), grid.js, controls.js, md.js, securities.js (search), app.css
config/                      key template, lock files (requirements-v5/v4), MCP example
ruff.toml                    static bug-check configuration for terminal/ and scripts/
```

## Dashboards and the generator

`scripts/dashboards/` holds the plain structure and override data (`overrides.py`, `layouts.py`). `scripts/build_dashboards.py` is the generator engine: it re-exports all public data and DSL symbols and writes `local/dashboards.json` on every start. `launcher.py dashboards` rebuilds it, and `scripts/build_dashboards.py --report` prints the layout and coverage.

Counts from `--report`:

- Default V5-only build: 28 dashboards, 120 tabs, 786 cards, 30 concept families, and 941/941 data functions placed, none by fallback.
- With `--with-v4`: 825 cards (39 of them legacy) and 43 concept families across the same 120 tabs, with 1,002/1,002 functions placed.

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

The whole envelope is defined and checked in `terminal/composite/core/contract.py`:

- **Success:** the view's payload plus `sources` (non-empty), `as_of` (ISO date or datetime of the newest data
  point) or `as_of_na` (why there is none), `status` (`ok`, `partial`, `stale` or `fallback`) and `notes` (plain
  text: stale, cached, delayed). `dispatch` derives `as_of` from the answer's own `as_of`, its row dates or its chart's
  x values, and validates the result: a violation raises when `TERMINAL_STRICT_CONTRACT=1` (every offline test) and
  is logged once per view otherwise.
- **Error (every non-2xx, `/x/*` and `/api/*`):** `{error: {kind, message, retryable, retry_after?}, detail,
  sources}`; `detail` repeats the message for older pages. Kinds: `input`, `not_covered`, `upstream`,
  `rate_limited`, `timeout`, `unavailable`, `internal`. `server._json` adds the object to any error body built
  elsewhere, from the status code.
- **Kinds are set once:** the HTTP layer tags 429 `rate_limited` (with `retry_after` from Retry-After), timeouts
  `timeout`, 5xx/refused/non-JSON `upstream`, 404 `not_covered`; `contract.kind_of` reads an untyped view error once.
  The `/x/*` status follows the kind (400, 404, 429, 502, 503, 504, 500). The page renders from `error.kind`
  (`T.classifyKind`, text passed through `T.cleanMessage`); `T.classifyError` regexes remain only for raw `/api/v1`
  backend answers. `test_errors.py` holds the fault-kind to error-kind table.
- **Parameters** are checked against the view's signature (a lazy wrapper's `_target`) before the call: an unknown
  or missing parameter is 400 `input`, an unknown view 404; a TypeError inside a view is a defect (500 with a ref).
- `scripts/tests/test_contract.py` runs every route under success, CompositeError and a defect through the real
  handler, and every `/api/*` write route under malformed, oversize and cross-origin requests.

## UI server (`terminal/server.py`)

Admission control: at most 24 views (`/x/*`, `TERMINAL_X_SLOTS`) run at once; a request that waits more than 5 s
for a slot answers 503 (`unavailable`, `retry_after` 5, `Retry-After` header). The page, static files, state,
config and service status never wait for a slot. HEAD on `/x/*` answers 405 without running the view.
`/api/services` reports `ui_threads` and the free view slots.

Standard library only. It serves the page and its static files, the built dashboards and saved state. It never proxies market data: the browser calls the two backends directly.

| Method and path | What it returns |
|---|---|
| `GET /`, `/index.html` | The page, sent with the Content-Security-Policy described below |
| `GET /api/config` | The version and settings the page needs |
| `GET /api/dashboards` | The built dashboards from `local/dashboards.json`; `503` until the first build |
| `GET /api/state`, `PUT /api/state` | The saved layout (see Saved state below); a `PUT` with a stale `_rev` gets `409` |
| `GET /x/*` | The terminal's own views, same origin only |
| `GET /widgets.json` | The terminal's own view definitions (`xwidgets.py`) |
| `GET /api/services` | Each backend as running, stopped (with the reason) or not started, plus restart progress |
| `GET /api/warm` | Warm-up status of the composite views |
| `GET /vendor/plotly.min.js` | The Plotly bundle from the installed `plotly` package |
| `POST /api/services/restart` | Optional JSON body `{"target": "backends"}`, or `"v5"` / `"v4"`. Answers `202` at once, resets the composite runtime caches, runs `launcher.py restart` in the background and warms the views afterwards. The top-bar **Restart** button (`#btn-restart`) calls it. |
| `POST /api/clientlog` | A card reports an error; it is written to `local/logs/ui.log` under a reference code |

Static files and the vendor bundle answer with an ETag (`304` when unchanged). `/api/state`, `/api/dashboards` and eligible text responses are gzip-compressed when the browser accepts gzip.

**Security.**

- **Host allow-list.** Only `127.0.0.1` and `localhost` on this port are answered; any other `Host` gets 403. This blocks DNS-rebinding pages.
- **Same origin for state, views and writes.** `/x/*`, `PUT /api/state`, `POST /api/clientlog` and `POST /api/services/restart` refuse cross-site requests (`Sec-Fetch-Site`) and any `Origin` other than the page's own (403). Requests with neither header, such as scripts or curl, pass.
- **Content-Security-Policy on the page** (`content_security_policy()` in `server.py`): scripts and styles only from the page's own origin (Plotly is served from `/vendor`), data only from the two local backends, frames from those backends, https pages (provider widgets, SEC documents) and `blob:` (PDFs). Plugins (`object-src`) and base URLs are disabled, and the page can be framed only by its own origin.
- **Response headers:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer`, and `Cache-Control: no-cache` by default, so the browser always revalidates.
- **Cache cleanup.** A daemon thread runs `local_paths.prune_local()` every 24 hours. It removes cache files untouched for 30 days (backend entries after 14 days) and freeze dumps after 14 days, then trims `local/cache/` to 150 MB, oldest first.

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
Writes go through a temporary file and an atomic replace. In-page cleanups must change `T.S.state` and then save;
otherwise a pending save overwrites them.

## Routing by ticker

For a card in a concept family, `app.js routeItem` picks the member that covers the ticker's market
(`T.marketOf`): AAPL goes to Cboe, SU.TO to TMX, BTC to Deribit, SPY holdings to Nasdaq, XIU.TO to TMX.
Automatic routing never lands on a paid plan; a source the user picked wins when it covers the ticker. When
nothing covers it the card says so and names what each source covers.

## Runtime patches (`scripts/run_backend.py`)

Every fix is one entry of the `PATCHES` registry: `Patch(id, title, install, critical, targets)`. `apply_fixes`
installs them in registry order and records each outcome; `patches-<name>.json` (local/run/) lists id, title,
applied, why, critical, the installed versions of the target packages and, when a version is outside the range in
`VERIFIED`, `unverified for <pkg> <ver>`. Critical fixes (thread offload, fetch cache, route offload, Yahoo crumb
guard, Nasdaq rate limit, request guard) that fail turn Data status red with a banner and fail `launcher.py gate`
G2. `test_patch_registry.py` checks unique ids, that every `install_*` function is registered once, and that all
patches apply on the installed `.venv-v5`.

Applied in memory at start; no installed file is modified.

- Synchronous fetchers run in worker threads, so one slow source cannot freeze the event loop.
- Yahoo's daily FX "close" is that date's 00:00 London rate (the open). Histories through `yf_download` close at 17:00
  New York from hourly bars (last 2 years), older days on the next bar's open, days before 2010-08-04 on Yahoo's own
  close, which was real then (`fx_daily_bars`, `test_fx.py`). The TradingView datafeed is routed through the same
  bars, and its header counts from the previous of those closes (`install_fx_chart_fix`).
- Output streams are guarded (no "I/O operation on closed file" crash), and log records are redacted.
- **Disk cache for slow providers** (`CACHE_POLICY`, the only persistent cache policy; files in `local/cache/backend/`).
  Time to live: 3 h for FiscalData, CFTC, the Federal Reserve and FRED; 6 h for EIA, BLS and ECB; 12 h for USDA, IMF,
  JODI and OECD; 24 h for Fama-French. OECD gets one attempt because its hourly quota is strict. A failed refresh
  serves the last good answer for up to 14 days and marks it stale on the card. A first request waits at most 45 to 55 s
  (without a limit for BLS, IMF and OECD); after that the card says the data is still loading, and the fill continues in
  the background. Empty results are not stored. `X-Terminal-Refresh: 1` bypasses the cache.
- OECD fails fast on HTTP 429 with a readable message.
- EIA returns the latest 5,000 rows unless a limit is set; at most 4 EIA calls are in flight, with up to 3 attempts per request.
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
- **Backend request guard** (`install_guard`): Host allow-list (blocks DNS rebinding), Origin must be the UI (any local
  host name, case-insensitive), requests without an Origin are refused when `Sec-Fetch-Site` says cross-site, CORS names
  that origin only, error bodies redacted, unhandled pre-header exceptions return sanitized HTTP 500 JSON with CORS
  headers. `/_terminal/warm_chart` pre-warms TradingView bars and symbol metadata inside the guard without altering OpenAPI.
- Reliability hardening: `install_yfinance_crumb_fix` prevents `yfinance.data.YfData` from persisting error text into
  `_crumb` on non-200 responses and serializes concurrent-thread 401 recovery; TradingView uses stale-cache fallback and
  retry; composite endpoints apply HTTP retries, scoped disk-cache backoff, and aligned frontend/backend timeouts and retries.
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

Examples use `python` for the environment's interpreter: `.venv-v5\Scripts\python.exe` on Windows, `.venv-v5/bin/python` elsewhere.

```
python scripts/launcher.py test [pattern...] [--fast]  # parallel offline checks (~11 s; --fast skips the headless browser)
python scripts/launcher.py check                       # BOM/LF/CRLF, AST/JSON, ruff, hygiene/secrets, offline sources, state, notes
python scripts/launcher.py snapshot [--label NAME]     # zip publishable tree to local/snapshots/ (skips if unchanged)
python scripts/launcher.py diff [--stat]               # diff working tree against the latest snapshot
python scripts/launcher.py restart ui                  # fast (~2 s) UI-only restart after editing terminal/*.py
python scripts/launcher.py logs [ui|v5|v4] [--errors]  # tail logs, freeze dumps and backend patch counts
python scripts/launcher.py smoke                       # live data checks against the running backends
python scripts/launcher.py health --dry-run            # paced card health runner (add --phase/--filter/--limit/--resume)
python scripts/check_sources.py [--offline]            # source-contract check (live or against recorded catalogs)
python scripts/probe.py --view snapshot --inputs symbol=AAPL symbol=SAP.DE --concurrent 8 --assert-envelope
python scripts/launcher.py gate [--only G1,G4] [--offline]  # release gate G1-G5; exit 0 only when no gate failed
python scripts/probe.py --all-routes --defaults        # every /x/ view in /widgets.json with its default parameters
python scripts/probe.py --backend v5 --url /PATH       # raw /api/v1 calls: status counts and latency per URL
```

All outbound HTTP goes through `composite.core.http.fetch(url, provider=..., parse="json"|"text"|"bytes")`
(`http_json` and `_download` are thin wrappers; `fred_public`, `multpl`, `banks` and `news` call it too). Its
`PROVIDERS` table sets per provider the timeout per attempt, attempts, statuses worth a retry, concurrency cap,
pacing (`min_interval`, Google News 2.5 s), the cooldown after a 429 (Retry-After wins, capped at 600 s; calls during
it fail at once with `rate_limited` and send nothing; the backend cools down per upstream `provider=`, unknown hosts
per host), an outage gate (`down_for`, FRED 120 s) and the User-Agent. OECD and Nasdaq are never retried.
`reset_runtime_caches` (service restart) also clears cooldowns.

In-memory caches stamp entries with `time.monotonic()` (wall time only for disk records and shown ages; `_mono`
converts a disk stamp). Process-lifetime maps (`_backoff`, `_STALE_SINCE`, `_HIST_REVALIDATING`, the news feed maps,
the negative cache) are `LRUDict`/`LRUSet` capped at 2,000 entries. `cached()` remembers a failed fetch for 10 s (a
rate limit: its retry_after), so callers queued on a failing key get the same typed error without another upstream
call. `launcher.py gate` G2 ends with a 30-minute probe soak that must grow the UI process by less than 50 MB.

Shared fixtures and fault injection live in `scripts/tests/_fixtures.py`: `fault(kind)` for timeout, refused,
http429(retry_after), http5xx, http404, empty_body, html_body, nan_values and partial; `serve({url_regex: payload})`
for canned answers; `fixture(provider, name)` for the sanitized JSON in `scripts/tests/fixtures/`. They replace the
single outbound seam `composite._transport` (used by `http_json` and `_download`), so a test needs no knowledge of a
module's internals. `http_json` turns NaN/Infinity into None and a non-JSON body into a transient `CompositeError`.

A full `test` run reports **4,729 checks across 43 test files**, all passing. `test_frontend.py` contributes 315 browser checks.
The browser checks are skipped with a notice when no headless Edge or Chrome is installed.
Each `test_*.py` runs in its own process with a temporary `local/` folder cleaned up at exit:

| File | Covers |
|---|---|
| `test_numbers.py`, `test_fx.py`, `test_returns.py`, `test_rates.py`, `test_ev.py`, `test_peer_ratios.py`, `test_gauges.py` | Number formats, FX direction and closes, returns, rate changes, EV normalization, peer ratios, gauges and country monitor |
| `test_curves.py`, `test_policy_path.py`, `test_cds.py`, `test_bonds.py`, `test_banks.py`, `test_cryptovol.py` | Sovereign curve parsers, policy paths, CDS conventions, TRACE bonds, FDIC/FFIEC banks, Deribit DVOL |
| `test_earnings.py`, `test_snapshot.py`, `test_sec_quarters.py`, `test_sec_growth.py`, `test_futures.py`, `test_funds.py`, `test_nasdaq_etf.py` | Earnings model, snapshot text, SEC quarters and growth, futures curves, fund multiples, Nasdaq ETF fallback |
| `test_columns.py`, `test_sources.py`, `test_trust.py`, `test_timezone.py`, `test_news.py`, `test_fred.py`, `test_links.py`, `test_multpl.py` | Column formats and units, source contract, trust signals, Montreal time, news ranking, FRED CSV, jump links, multpl |
| `test_medium.py`, `test_low.py`, `test_p4.py` | Cache slicing and eviction, weekly YoY, forecast split, policy history, non-trading-day bases |
| `test_generator.py` | Routing by ticker, and a full build replayed from the recorded catalogs (coverage, stable ids, V5-only default) |
| `test_cboe_fallback.py` | Black-76, parity forward, substitute loader and charts |
| `test_valuation.py`, `test_risk.py`, `test_bond_math.py`, `test_etf_options.py` | Valuation bands, ROIC/DuPont, risk statistics, bond math, N-PORT overlap, GEX, Max Pain, volatility cone |
| `test_redact.py`, `test_backend_wrapper.py` | Key redaction, backend guard and sanitized 500s, TradingView bar repair, NaN/Inf scrubbing |
| `test_launcher_server.py` | Restart policy, stop scope, V5-only default and CLI flags, UI server Host/Origin/state rules, corrupt-state recovery, ETags, CSP, key application, port guards |
| `test_contract.py` | Response envelope for every route (success, CompositeError, defect), parameter checks, one error object on `/api/*` |
| `test_errors.py` | Fault kind to error kind table (HTTP layer and real server), Retry-After, server-side classification |
| `test_http.py` | Outbound client: provider policies, 429 cooldown without I/O, Retry-After cap, concurrency peak, pacing, outage gate |
| `test_patch_registry.py` | Backend patch registry: unique ids, every installer registered, version ranges, status records, critical banner |
| `test_admission.py` | 24 view slots, 503 after the wait, state/page never blocked, HEAD never runs a view, slots released after defects |
| `test_cache.py` | 2,000-entry caps, monotonic TTLs, negative cache (20 concurrent callers -> 1 upstream call) |
| `test_gate.py` | Release gate: G1 record and tolerance math, log scan, live G4 probes against an in-process server, exit rule, evidence file |
| `test_harness.py` | Every fault kind through the outbound seam, fixtures, probe CLI and envelope rules |
| `test_health.py`, `test_workflow.py` | Health runner and redaction, publishable manifest, snapshots, EOL/BOM/hygiene checks |
| `test_frontend.py` | Runs `frontend_checks.js` (315 browser checks) headless against a temporary UI server |

`frontend_checks.js` holds the browser-side checks (redaction, error classifier, relative dates, CSV formula
escaping, v4 per-card override diff/merge/migration, market routing, column formats, statements, view preferences,
request slot queuing). `test_frontend.py` runs it headless; it can also be pasted into the developer console of an
open Libre Terminal page.

## Conventions

- **Dependencies:** Server and launcher code (`terminal/server.py`, `scripts/launcher.py`, `scripts/health_check.py`,
  `scripts/project_check.py`) stay standard-library only. Overall, no dependency beyond what `.venv-v5` already installs
  (such as Plotly's JS bundle, `xlrd`, and `ruff` for checks) and no front-end build step.
- **Frozen OpenBB policy:** `config/requirements-*.lock.txt` stay pinned; OpenBB packages are treated as a frozen
  catalogue. Critical-path views get thin, tested fetchers under `terminal/`, while broken long-tail OpenBB endpoints
  are parked in `terminal/health.json` rather than growing `scripts/run_backend.py`.
- **Composite imports:** the `terminal/composite/` submodules import `composite as C` and look up cross-module calls and
  patchable globals through `C.<name>` at runtime, so offline tests can monkey-patch `composite` attributes directly.
- **Encoding & line endings:** Files are UTF-8 without BOM. `.py`, `.js`, `.css`, `.html`, `.json`, `.md`, `.sh`,
  `.toml`, `.in`, `.env`, `.txt` use LF; `.cmd` and `.ps1` use CRLF (enforced by `.gitattributes` and `launcher.py check`).
- **Runtime isolation:** Never write outside `local/` at run time; never commit `local/` (git-ignored).
- **Restart scope:** After editing `terminal/*.py`, run `launcher.py restart ui` (~2 s); after editing `scripts/run_backend.py`,
  run `launcher.py restart backends` (or `stop` then `start`).

## Using the data from Python or MCP

```python
# with .venv-v5's Python
from openbb import obb
obb.sec.financial_statements(symbol="MSFT").to_df()
```

`config/mcp.example.json` shows how to register OpenBB's MCP server (`openbb-mcp`, installed in `.venv-v5`)
with a tool that supports MCP: copy it to `.mcp.json` in the project root (git-ignored, it holds an absolute
path) and fill in the path.
