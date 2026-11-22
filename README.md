# Libre Terminal

A free, open-source market research terminal that runs on your own computer and opens in your browser.
Equities, rates and credit, FX, commodities and energy, crypto, macro and government data are organised
by asset class in 28 dashboards, built on public sources (Yahoo Finance, SEC, Federal Reserve, ECB, BIS,
IMF, OECD, EIA, CFTC and others) through the open-source
[OpenBB Platform](https://github.com/OpenBB-finance/OpenBB). It works without an account or API key;
free keys from FRED, BLS, EIA, Congress.gov and FMP add more.

## Download & Run (Windows 10 / 11)

> **[Download Libre Terminal for Windows (.zip)](https://github.com/samlazs/libre-terminal/releases/latest/download/Libre-Terminal-Windows.zip)** &nbsp;·&nbsp; [Latest release notes](https://github.com/samlazs/libre-terminal/releases/latest)

No Python, coding tools, or accounts needed — just an internet connection and ~1 GB of free space:

1. **Download** [`Libre-Terminal-Windows.zip`](https://github.com/samlazs/libre-terminal/releases/latest/download/Libre-Terminal-Windows.zip) above.
2. **Extract it** — right-click the downloaded ZIP → **Extract All…** → **Extract**. *(Important: do not open files from inside the ZIP.)*
3. **Install (once)** — open the extracted `Libre-Terminal` folder and double-click **`Install.cmd`** (takes 2–5 minutes).
   - If Windows shows *"Windows protected your PC"*, click **More info** → **Run anyway** (only needed once).
4. **Launch** — double-click **`Start.cmd`**. Wait 15–60 seconds on the first launch while initial data and charts warm up; your browser opens at `http://127.0.0.1:6950`.
   - Use **`Stop.cmd`** to shut it down cleanly, and **`Setup-Keys.cmd`** later if you want optional free API keys.

> Unofficial project, not affiliated with OpenBB or any data provider. Research tool only: data can be
> delayed, incomplete or wrong, and nothing here is investment advice. See
> [License and disclaimer](#license-and-disclaimer).

![Libre Terminal — Markets Summary](docs/markets-summary.jpg)

| | |
|---|---|
| ![Company overview](docs/company-overview.jpg) | ![Comparable companies](docs/company-comps.jpg) |
| ![Rates by country](docs/rates-by-country.jpg) | ![Energy overview](docs/energy-overview.jpg) |

![News situation room](docs/news-situation-room.jpg)

## Quick start (updating, options & macOS / Linux)

**Updating from an earlier Windows download:** double-click **`Stop.cmd`** in your old folder, download and extract the new ZIP above, copy your old `local` folder into the new folder if you want to keep saved layouts and API keys, then run **`Install.cmd`** and **`Start.cmd`**.

**Start options (optional):** from a Command Prompt in the `Libre-Terminal` folder, `Start.cmd --no-wait` opens the browser immediately while charts finish warming in the background; `Install.cmd --with-v4` and `Start.cmd --with-v4` add the optional legacy V4 backend (+480 MB for FMP, Finviz, EconDB, Intrinio).

**macOS / Linux (experimental, untested — official support is Windows 10/11 only)**

```sh
git clone https://github.com/samlazs/libre-terminal.git && cd libre-terminal
sh install.sh
sh start.sh          # opens http://127.0.0.1:6950 ; stop with: sh stop.sh
```

The first start pre-warms initial dashboard data and TradingView chart caches before opening the
browser (usually 15–60 s on a cold cache; pass `--no-wait` to open immediately while warming
continues in the background). Everything listens on `127.0.0.1` only; nothing leaves your
computer except the data requests themselves.

## What is inside

28 dashboards, 118 tabs and 766 cards by default on V5 (929 data functions); opting into the legacy V4 backend (`--with-v4`) adds ~43 legacy cards (FMP, Intrinio, Finviz, EconDB, WSJ; 28 dashboards, 119 tabs, 809 cards, 990 functions total).

| Section | Dashboards |
|---|---|
| Overview | **Markets Summary**: cross-asset boards with returns over five horizons, normalized performance, global yields, style box, movers, risk and geopolitics, news and calendar |
| Equities | **Company** (overview, financials, estimates, price and peers, ratios, ownership, events, segments, filings, shorts, options), Screeners & Movers, Calendars & Events, Canada (TMX), Nordics |
| Indices, ETFs & Funds | Indices, ETFs & Mutual Funds |
| Rates & Credit | **Rates & Curves** (global, by country, U.S., euro area, money markets, swaps and OIS, policy expectations), Credit, Government Finance |
| Derivatives | Equity Options, Futures & Positioning |
| Currencies | Currencies (board, cross-rate matrix, history, forwards, volatility, positioning) |
| Commodities & Energy | Commodities, **Energy** (oil, gas and LNG, power and uranium, supply and geopolitics, outlooks, data library), Agriculture & Food |
| Crypto | Crypto (boards, Deribit market and options) |
| Macro & Economy | Economy by Country (Country Monitor for 17 economies), U.S. Economy, Money & Banking, Trade & Shipping (chokepoints, ports, tariffs), Data Explorers |
| Government | U.S. Congress, Federal Spending |
| News & Research | News, SEC Filings & Enforcement, Factors & Styles |

## Features

- **Search** (Ctrl+G): a ticker or a company name worldwide (`toyota`, `rheinmetall`, `SHEL.L`). A
  stock opens in Company with the ticker applied, an ETF in ETFs & Funds, an index, currency, future or
  coin in the Markets chart. The same box finds dashboards, tabs and datasets (`cpi`, `sofr`, `wasde`).
- **Watchlists**: a quote board for your own lists (price, change, five return horizons, 3-month trend,
  52-week range, in the trading currency). Add symbols by name.
- **Compare chart**: overlay up to eight symbols as price, % change, ratio or spread; candles, volume,
  50/200-day averages, log scale. FRED series can sit next to prices (`FRED:DGS10`).
- **Comparable companies**: valuation multiples, margins, growth, leverage, yield and 1-year return for a
  company and its peers, with the median. Works for U.S. and non-U.S. listings; peers are editable and
  kept per company.
- **Source on every card**: the header says where the data comes from ("Source: SEC ▾"). The menu
  switches to another source of the same data and links to the original. Ticker-driven cards pick a
  source that covers the ticker on their own: U.S. option chains for AAPL come from Yahoo Finance or
  Nasdaq (with Cboe analytics when its delayed-quotes CDN is available), Canadian options for SU.TO from
  the Montreal Exchange (TMX), and crypto options for BTC from Deribit; ETF holdings for SPY come from
  Nasdaq and for XIU.TO from TMX.
- **Chart strip**: range (1M to Max), transform (level, year-over-year, indexed, seasonal against the
  5-year range) and period, remembered per card.
- **Columns**: tables open with a short, readable set of columns in consistent units; **⋯ > Columns**
  shows or hides the others, remembered per card. **⋯** also has Flip, Plot and Settings.
- **Export** (⤓): copy the table, download CSV (with the source line), save a chart as PNG.
- **Library cards**: families of similar datasets (EIA, BLS tables, each regional Fed, FFIEC) are one
  card with a Dataset picker.
- **Your own dashboards** with `+`; built-in tabs you change keep your edits (orange dot; "Reset to
  default layout" restores them).
- **Help** (`?` button or key) lists the shortcuts; `[` and `]` switch tabs.
- **Data status** (Data Explorers): sources broken upstream right now, and which API keys are set.

## Rates by country

Rates & Curves > **By Country** shows one or more countries' curves now against one month and one year
ago, the same data as a table with changes in basis points, the policy-rate path and rate history; one
country picker drives every card. **Global** has a yields matrix for about 45 countries and policy rates
for about 40 central banks (BIS). Curves come from official keyless sources:

| Country | Tenors | Source |
|---|---|---|
| United States | 1M to 30Y, daily | Federal Reserve (Treasury par curve) |
| Euro area | 3M to 30Y, daily | ECB (AAA-rated government curve) |
| Germany | 6M to 30Y, daily | Deutsche Bundesbank |
| United Kingdom | 6M to 40Y, daily | Bank of England |
| Japan | 1Y to 40Y, daily | Ministry of Finance |
| Canada | 1M to 1Y bills, 2Y to long bond, daily | Bank of Canada |
| Sweden | 1M to 6M bills, 2Y to 10Y, daily | Riksbank |
| Brazil | about 2M to 10Y, daily | Tesouro Direto fixed-rate bonds |
| France, Italy, Spain, Switzerland, Australia and about 30 others | 3-month and 10-year, monthly | OECD |

## Optional: free API keys

The first tab of most dashboards works without keys. Cards that need a key say so, sit after the keyless
cards, and Data Explorers > Data status lists which keys are missing.

| Key | What it adds | Sign up |
|---|---|---|
| FRED | macro series, credit spreads, commodity spot prices, the U.S. Economy dashboard (many overview, rates and macro charts work keyless via FRED's public CSV endpoint) | https://fredaccount.stlouisfed.org/apikeys |
| EIA | the Energy dashboard's U.S. data and data library | https://www.eia.gov/opendata/register.php |
| BLS | inflation, jobs and productivity tables | https://data.bls.gov/registrationEngine/ |
| Congress.gov | bills, votes, members, committee reports | https://api.congress.gov/sign-up/ |
| FMP | fundamentals, estimates, transcripts (free tier: 250 calls a day; requires optional `--with-v4`) | https://site.financialmodelingprep.com/developer/docs |

Double-click **`Setup-Keys.cmd`** (or `sh setup-keys.sh`), paste the keys into the file that opens, save,
then stop and start the terminal. Keys stay on your computer.

## Known limits

- **Delayed prices.** Quotes come from Yahoo Finance: fine for research, often delayed, not a trading
  feed. No streaming, order entry or alerts.
- **Earnings.** Consensus and reported EPS come from Yahoo Finance (one source, one basis); reported EPS and
  revenue appear when Yahoo posts them, usually hours to a day after the release, which is linked from SEC
  meanwhile. Yahoo drops a quarter's revenue consensus once it is reported, so past revenue surprises are shown
  only where the terminal saw the consensus before the report. No call transcripts.
- **Returns.** Boards made only of stocks and funds show total returns (distributions reinvested on the
  ex-date); boards that include an index, future, currency or coin show price returns, since a price index
  carries no dividends. The line under each board says which.
- **No single-name CDS**; credit default swap data is index-level trade reports (CFTC). **Bond pricing is
  limited** to what FINRA TRACE publishes; U.S. Treasury security prices are not available.
- **Country curves**: daily for the eight countries above; elsewhere monthly 3-month and 10-year rates
  only, so euro sovereign spreads (BTP-Bund, OAT-Bund) are monthly.
- **Sources break upstream.** About 30 functions fail at the source right now (moved pages, retired
  endpoints); Data Explorers > Data status lists them with the reason. Cboe's delayed-quotes CDN can block
  options analytics requests upstream with HTTP 403 (U.S. option chains remain available via Yahoo Finance
  and Nasdaq). OECD limits how often it can be queried: its cards then say "rate limit reached" and recover
  later. Government sites sometimes fail a single request: reload the card.
- **Outside the U.S.** prices, statements, estimates, holders and comps cover listings worldwide; SEC
  filings, FINRA short interest and U.S. option chains/analytics (Yahoo, Nasdaq, Cboe) are U.S.-only, and
  those cards say so.
- **Actuals, no consensus**: released macro figures and IMF forecasts; no survey consensus, no PMIs.
- **Free tiers** have daily caps (FMP 250 calls a day); scraped sources can change without notice.
- **Platforms**: **Windows 10/11 only** is officially supported and tested; the `.sh` scripts for macOS/Linux share the same launcher but are experimental and untested.

## Your data: the `local/` folder

Everything personal lives in `local/`, created on the first start and never part of the project you share:

| In `local/` | What it is |
|---|---|
| `api_keys.env` | your optional API keys |
| `state/` | your layouts, watchlists, peers and picks (`state.json` and one backup a day) |
| `logs/` | `ui.log`, `v5.log`, `v4.log`, and `*-freeze-*.txt` if a backend froze |
| `dashboards.json`, `cache/`, `backends/`, `run/` | generated dashboards, caches, backend settings, process ids |

**Backup**: copy `local/`. **Restore** or move to another computer: stop the terminal and put the folder
back. **Start fresh**: stop the terminal and delete `local/`. `TERMINAL_LOCAL_DIR` moves it elsewhere.

## Troubleshooting

| Problem | What to do |
|---|---|
| `"Windows protected your PC"` | Click **More info** → **Run anyway** (only needed once; `Install.cmd` unblocks the other `.cmd` files automatically). |
| `File won't run / "inside the ZIP"` | Right-click the ZIP → **Extract All…**, then open the extracted `Libre-Terminal` folder. |
| `Install fails inside OneDrive / long folder path` | Move the extracted `Libre-Terminal` folder to a simple local path like `C:\Libre-Terminal` and double-click `Install.cmd` again. |
| Install stops with an error | Usually a proxy, firewall or antivirus. Fix it and run the install again; it continues where it stopped. |
| Port already in use | `Start.cmd --ui-port 7950 --v5-port 7900` (remembered for later starts; add `--v4-port 7901` if using `--with-v4`). |
| A service is not responding | The first start can take minutes. See `local/logs/`. A backend that stops or freezes is restarted automatically (up to 5 times in 10 minutes); the freeze report is in `local/logs/*-freeze-*.txt`. |
| A card says it needs an API key | Add the free key (above), then stop and start. |
| Want the default layout back | `⋯` on the dashboard, "Reset to default layout". |
| Legacy V4 sources (FMP, Finviz, EconDB, Intrinio) | Single-backend V5 is the default (~1 GB). Run `Install.cmd --with-v4` then `Start.cmd --with-v4` to enable the optional V4 backend (`--skip-v4` is accepted as a backward-compatible alias). |
| Uninstall | Stop it and delete the folder (keep a copy of `local/` if you want your layouts). |

**Updating** a git clone: stop, `git pull`, run the install again. `local/` is not touched.

## For developers

Two local processes by default: the UI server (`terminal/`, Python standard library, no front-end build step) and
the OpenBB V5 data backend (plus an optional legacy V4 backend via `--with-v4`). `python scripts/launcher.py test` runs the offline checks in a few seconds, and
`python scripts/launcher.py check` runs encoding, syntax, lint and hygiene checks. The structure, the source
contract, saved-state keys, generator conventions, runtime patches and tests are described in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Contributions are welcome under the project's license; keep
server/launcher code in the standard library (no new dependencies beyond what `.venv-v5` installs) and never
commit `local/`.

## License and disclaimer

Licensed under the **GNU Affero General Public License v3.0 or later** ([LICENSE](LICENSE)); third-party
notices are in [NOTICE](NOTICE). The OpenBB Platform and other packages are installed from PyPI under their
own licenses and are not redistributed here.

Unofficial project, not affiliated with, endorsed by or sponsored by OpenBB or any data provider; names
identify compatible software and data sources only. Data comes from third parties under their own terms,
which you are responsible for respecting, and may be delayed, incomplete or wrong. Nothing here is
investment, financial, legal or tax advice. The software is provided "as is", without warranty of any kind.
