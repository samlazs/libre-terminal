/*
 * Offline checks for the browser-side helpers (error classifier, redaction, relative dates, CSV cells, layout merge).
 * test_frontend.py runs it in headless Edge (part of `launcher.py test`); it can also be pasted into the console of an
 * open Libre Terminal page (it swaps T.S.state briefly for the view-preference checks and puts it back; nothing is saved).
 * It prints "frontend checks: N passed" or throws on the first failure.
 */
window.__fcPromise = (async function (T) {
  "use strict";
  var n = 0;
  function ok(cond, what) { if (!cond) throw new Error("FAILED: " + what); n++; }
  function cls(msg, ctx) { return T.classifyError(msg, 500, ctx || { source: "FMP", provider: "fmp" }); }

  // ---- redaction (H2)
  ok(T.redact("https://x.com/a?apikey=abcd1234efgh&b=1").indexOf("abcd1234efgh") < 0, "apikey in URL is redacted");
  ok(T.redact('{"api_key": "abcd1234efgh"}').indexOf("abcd1234efgh") < 0, "api_key in JSON is redacted");
  ok(T.redact("token=zzzzzzzzzz") === "token=REDACTED", "token= is redacted");
  ok(T.redact("monkey=banana") === "monkey=banana", "words ending in 'key' are left alone");

  // ---- classifier: one message per category, never raw text
  ok(cls("Missing credential 'benzinga_api_key'. Check https://www.benzinga.com").cls === "key", "missing key");
  ok(/paid Benzinga/.test(cls("Missing credential 'benzinga_api_key'.").text), "paid-only provider says paid");
  ok(/free FRED/.test(cls("Missing credential 'fred_api_key'.").text), "free key says free");
  ok(cls("Unauthorized FMP request -> 402 -> Premium Query Parameter").cls === "paid", "402 is a paid plan");
  ok(cls("cusip: Field required").cls === "input", "pydantic missing field asks for input");
  ok(/Enter Symbol/.test(cls("Error: Symbol is a required field for yFinance.").text), "'X is a required field' asks for X");
  ok(cls("OECD rate limit reached (HTTP 429)").cls === "ratelimit", "429 is a rate limit");
  ok(cls("404 Client Error: Not Found for url: https://mba.tuck.dartmouth.edu/x.zip", { source: "Fama-French" }).cls === "upstream", "upstream 404 is source down, not 'not covered'");
  ok(cls("('Connection aborted.', ConnectionResetError(10054, 'reset'))").cls === "upstream", "connection reset is source down");
  ok(cls("EIA is slow to answer the first request for this dataset. It keeps loading in the background").cls === "slow", "bounded EIA wait");
  ok(cls("No options found for SU.TO", { source: "Cboe", symbol: "SU.TO" }).cls === "covered", "not covered");
  ok(cls("Error: 403", { source: "TreasuryDirect" }).cls === "denied", "403 is access denied");
  ok(cls("Token Required", { source: "IMF", provider: "imf" }).cls !== "key", "keyless provider never invents imf_api_key from Token Required");
  ok(cls("IMF PortWatch upstream server is not answering properly right now (ArcGIS error 499: upstream dataset is restricted)", { source: "IMF", provider: "imf" }).cls === "upstream", "ArcGIS 499 classifies as upstream");
  var bug = cls("KeyError -> 'SIC Code'", { source: "SEC" });
  ok(bug.cls === "bug" && /ref [a-z0-9]{1,6}/.test(bug.text) && bug.text.indexOf("KeyError") < 0, "our bug shows a short ref, not the exception");
  ok(cls("Type a second symbol in 'Compare with'", { userMessage: true }).cls === "message", "terminal views explain themselves");
  ["Missing credential 'x_api_key'", "HTTPError -> 402 for url: https://a.b/c?apikey=SECRETSECRET", "KeyError -> 'x'", "404 Client Error: Not Found for url: https://a.b/c"].forEach(function (m) {
    var c = cls(m);
    ok(!/https?:\/\//.test(c.text + (c.hint || "")), "no URL on a card: " + m);
    ok(!/SECRETSECRET/.test(c.text + (c.hint || "")), "no key on a card: " + m);
  });

  // ---- relative dates in defaults (CFTC last business day, bounded Treasury ranges)
  ok(T.resolveParam("@bday:-1", "2026-11-15T17:00:00Z") === "2026-11-13", "Sunday -> previous Friday");
  ok(T.resolveParam("@bday:-1", "2026-11-17T17:00:00Z") === "2026-11-16", "Tuesday -> Monday");
  (function () {   // review B3: the bulletin's file list asks the provider the card uses (usda), not the widget's stale literal
    var q = T.optionsQuery({ year: "$year", provider: "government_us" }, { year: 2026, provider: "usda" });
    ok(q.year === 2026 && q.provider === "usda", "options query: fixed provider follows the card's provider (B3)");
    var q2 = T.optionsQuery({ symbol: "$symbol", provider: "sec" }, { symbol: "AAPL" });
    ok(q2.symbol === "AAPL" && q2.provider === "sec", "options query: fixed provider kept when the card has none");
  })();
  ok(T.resolveParam("@days:-90", "2026-11-15T17:00:00Z") === "2026-08-17", "90 days back");
  ok(T.resolveParam("AAPL") === "AAPL", "plain values untouched");
  // the sources behind relative dates are U.S. (Treasury, CFTC, Nasdaq calendars): days follow New York, not the PC clock (audit C1)
  ok(T.resolveParam("@bday:-1", "2026-11-17T03:00:00Z") === "2026-11-13", "22:00 Monday in New York (03:00 UTC Tuesday) -> Friday");
  ok(T.resolveParam("@days:0", "2026-11-17T03:00:00Z") === "2026-11-16", "today = New York's date");
  ok(T.resolveParam("@days:0", "2026-07-01T03:30:00Z") === "2026-06-30" && T.marketDate("2026-11-17T05:30:00Z") === "2026-11-17", "summer and winter time");

  // ---- CSV formula injection (L1)
  ok(T.csvCell("=HYPERLINK(1)", ",") === "'=HYPERLINK(1)", "formula text is neutralised");
  ok(T.csvCell("-0.52", ",") === "-0.52" && T.csvCell(-0.52, ",") === "-0.52", "numbers stay numbers");
  ok(T.csvCell("@SUM(A1)", ",") === "'@SUM(A1)", "@ formulas neutralised");

  // ---- layout merge (M7): edited tabs survive a rename via tpl.renamed; unknown edited tabs are kept, marked
  var tpl = { id: "d:x", tabs: [{ id: "oil-gas", name: "Oil & Gas", items: [] }, { id: "power", name: "Power", items: [] }], groups: [], renamed: { oil: "oil-gas" } };
  var saved = { id: "d:x", tabs: [{ id: "oil", name: "Oil", items: [{ uid: "wabc123", x: 0 }], edited: true }, { id: "gone", name: "Gone", items: [{ uid: "wq" }], edited: true },
    { id: "u-k3j2", name: "Mine", items: [], edited: true }], groups: [] };
  var d = T.mergeDashboard(tpl, saved);
  ok(d.tabs[0].id === "oil-gas" && d.tabs[0].items.length === 1, "renamed tab keeps the user's edits");
  ok(d.tabs.some(function (t) { return /from an older layout/.test(t.name); }), "orphaned edited tab is kept and marked");
  ok(d.tabs.some(function (t) { return t.id === "u-k3j2"; }), "user tab kept");
  ok(d.tabs.filter(function (t) { return t.id === "power"; }).length === 1, "untouched template tab present once");

  // ---- sources (package 2): plain provider names, compact header label, one source line for charts and exports
  ok(T.providerName({ params: [{ paramName: "provider", value: "yfinance" }], source: ["yFinance"] }) === "Yahoo Finance", "yfinance shows as Yahoo Finance");
  ok(T.providerName({ params: [], source: ["UN Global Platform; [IMF PortWatch](portwatch.imf.org)"] }) === "IMF PortWatch", "markdown and prefixes stripped");
  ok(T.providerName({ params: [], source: ["Custom"], endpoint: "/api/v1/fred/economy/release_table" }) === "FRED", "'Custom' falls back to the route's namespace");
  ok(T.sourceLabel([{ name: "FRED" }, { name: "FRED" }, { name: "U.S. EIA" }]) === "FRED · U.S. EIA", "header label: distinct names");
  ok(T.sourceLabel([{ name: "A" }, { name: "B" }, { name: "C" }, { name: "D" }]) === "A · B +2", "header label: long lists shortened");
  ok(T.sourceLine([{ name: "FRED", detail: "UNRATE: Unemployment Rate" }, { name: "FRED", detail: "U6RATE" }]) === "Source: FRED (UNRATE, U6RATE)", "source line lists series ids");
  // ---- market routing (options, ETF holdings, short interest)
  ok(T.marketOf("AAPL") === "us" && T.marketOf("BRK.B") === "us" && T.marketOf("SU.TO") === "ca" && T.marketOf("BTC-USD") === "crypto" && T.marketOf("SAP.DE") === "other", "markets");
  // any coin quoted against a currency or a coin is crypto, not a U.S. listing routed to Cboe/SEC (audit C3); share classes stay U.S.
  ok(T.marketOf("LTC-USD") === "crypto" && T.marketOf("ETH-EUR") === "crypto" && T.marketOf("AVAX-USDC") === "crypto" && T.marketOf("ETH") === "crypto", "crypto pairs beyond the major coins");
  ok(T.marketOf("BRK-B") === "us" && T.marketOf("BF-B") === "us" && T.marketOf("MOG-A") === "us" && T.marketOf("SPY") === "us", "share classes and plain tickers stay U.S.");
  ok(T.symbolFor("RY.TO", "tmx") === "RY" && T.symbolFor("VOLV-B.ST", "nordic") === "VOLV B" && T.symbolFor("NOKIA.HE", "nordic") === "NOKIA" && T.symbolFor("BTC-USD", "crypto_base") === "BTC" && T.symbolFor("AAPL") === "AAPL", "symbol as each source wants it");

  // ---- package 3: column formats, default columns, merged tabs
  function settled(field, vals) { var c = { field: field, type: "number", def: { field: field } }; T.settleColumn(c, vals.map(function (v) { var r = {}; r[field] = v; return r; })); return c.def; }
  // units are declared (terminal/columns.py UNITS), never guessed from the day's values (audit A1)
  function declared(field, f, vals) { var c = { field: field, type: "number", def: Object.assign({ field: field }, f) }; T.settleColumn(c, vals.map(function (v) { var r = {}; r[field] = v; return r; })); return c.def; }
  var frac = declared("ytd_return", { formatterFn: "normalizedPercent", decimalPlaces: 2 }, [2.0762, 0.7874]);
  ok(T.fmtNum(2.0762, frac) === "207.62%" && T.fmtNum(0.7874, frac) === "78.74%", "declared fraction: 2.0762 reads 207.62%, 0.7874 reads 78.74%");
  ok(T.fmtNum(2.08, frac) === "208.00%" && T.fmtNum(0.79, frac) === "79.00%", "declared fraction above 150% stays a percent");
  var usd = declared("eps_surprise", { decimalPlaces: 2 }, [-0.05, 0.25]);
  ok(T.fmtNum(-0.05, usd) === "-0.05" && T.fmtNum(0.25, usd) === "0.25", "EPS surprise in dollars: no % sign");
  var pct = declared("percent_change", { formatterFn: "percent", decimalPlaces: 2 }, [0.3, 15.21]);
  ok(T.fmtNum(0.3, pct) === "0.30%" && T.fmtNum(15.21, pct) === "15.21%", "declared percent: 0.3 reads 0.30%");
  var dayA = settled("ytd_return", [2.0762, 1.9598, 1.5704]), dayB = settled("ytd_return", [0.45, 0.31, -0.12]);
  ok(!dayA.formatterFn && !dayB.formatterFn && T.fmtNum(2.0762, dayA) === "2.08" && T.fmtNum(0.45, dayB) === "0.4500", "undeclared column: same unit (raw) whatever the values");
  ok(T.fmtNum(-0.05, settled("eps_surprise", [-0.05, 0.25, -0.06])).indexOf("%") < 0, "undeclared surprise is never a percent");
  ok(T.fmtNum(-0.18, settled("revenue_growth", [-0.18, 0.0253, 0.31])) === "-0.1800", "undeclared growth: raw number, no % sign");
  ok(!settled("high_yield", [8.66, 8.71]).suffix && T.fmtNum(8.71, settled("high_yield", [8.66, 8.71])) === "8.71", "undeclared yield: no % added from the values");
  // a hidden column marked showIfAny appears only when some row fills it (board "Last trade" for stale rows, audit A17)
  var sdefs = [{ field: "a", cellDataType: "number" }, { field: "stale", cellDataType: "text", hide: true, showIfAny: true }, { field: "note", cellDataType: "text", hide: true }];
  var quiet = T.table(document.createElement("div"), { data: [{ a: 1, note: "x" }, { a: 2 }], colDefs: sdefs, curated: true });
  var loud = T.table(document.createElement("div"), { data: [{ a: 1, stale: "2026-10-20", note: "x" }, { a: 2 }], colDefs: sdefs, curated: true });
  ok(quiet.shownFields().indexOf("stale") < 0 && loud.shownFields().indexOf("stale") >= 0, "showIfAny: shown only when a row has a value");
  ok(loud.shownFields().indexOf("note") < 0, "a plain hidden column stays hidden");
  // Stage 3 T1-2: a calendar with nothing from today on keeps its past rows but says the source has not moved on
  var calDefs = [{ field: "auction_date", cellDataType: "date" }, { field: "security_term", cellDataType: "text" }], lastSeen = null, upSeen = null;
  T.table(document.createElement("div"), { data: [{ auction_date: "2020-10-22", security_term: "4-Week" }, { auction_date: "2020-10-29", security_term: "7-Year" }],
    colDefs: calDefs, calendar: true, onNoUpcoming: function (d) { lastSeen = d; } });
  T.table(document.createElement("div"), { data: [{ auction_date: "2020-10-22", security_term: "4-Week" }, { auction_date: "2999-01-01", security_term: "7-Year" }],
    colDefs: calDefs, calendar: true, onNoUpcoming: function (d) { upSeen = d; } });
  ok(lastSeen === "2020-10-29" && upSeen === null, "calendar with no upcoming row reports the latest listed date; one with an upcoming row does not");
  // review A3: TMX statements start with a "Reported Currency: CAD" row; the year column is still amounts, in one scale
  var stDiv = document.createElement("div");
  T.table(stDiv, { data: [{ Item: "Reported Currency", "2024-12-31": "CAD" }, { Item: "Net Income", "2024-12-31": 375000000 },
    { Item: "Depreciation", "2024-12-31": 5041000000 }, { Item: "Capex", "2024-12-31": -4581000000 }] });
  var stText = stDiv.textContent;
  ok(stText.indexOf("0.38B") >= 0 && stText.indexOf("5.04B") >= 0 && stText.indexOf("375,000,000") < 0 && stText.indexOf("CAD") >= 0,
    "statement column with a currency row: one scale (0.38B beside 5.04B), currency text kept");
  // review A5: OECD states the unit per row (y/y fraction x100 vs index level)
  var oDiv = document.createElement("div");
  T.table(oDiv, { data: [{ date: "2025-11-01", value: 0.009, unit: "Year-over-year (YOY) percent change", unit_multiplier: 100 },
    { date: "2025-12-01", value: 0.007919967, unit: "Year-over-year (YOY) percent change", unit_multiplier: 100 }] });
  ok(oDiv.textContent.indexOf("0.79%") >= 0 && oDiv.textContent.indexOf("0.0079") < 0, "OECD y/y row: 0.0079 shown as 0.79%");
  var iDiv = document.createElement("div");
  T.table(iDiv, { data: [{ date: "2025-12-01", value: 121.4451, unit: "Index", unit_multiplier: 1 }] });
  ok(iDiv.textContent.indexOf("121.45") >= 0 && iDiv.textContent.indexOf("%") < 0, "OECD index row: a level, no percent");
  var big = settled("market_cap", [3.2e8, 1.57e9, 2.4e9, 9e9]);
  ok(T.fmtNum(3.2e8, big) === "0.32B" && T.fmtNum(1.57e9, big) === "1.57B", "one unit per column (bn)");
  ok(T.fmtNum(1558059, settled("eventid", [1558059, 1558060])) === "1558059", "ids without separators");
  ok(T.fmtNum(-0.18, settled("change", [-0.18, 11.5, 0.63])) === "-0.18", "price changes at 2 decimals");
  ok(T.prettyField("percent_change") === "Change %" && T.prettyField("ma_200") === "200-day MA" && T.prettyField("year_10") === "10Y", "readable headers");
  var rows = []; for (var i = 0; i < 12; i++) rows.push({ date: "2026-01-" + (10 + i), symbol: "X", a: i, b: i * 2, c: i, d: i, e: i, f: i, g: i, h: i, k: i, l: i, url: "https://x.com/" + i });
  var tv = T.table(document.createElement("div"), { data: rows });
  ok(tv.shownFields().length === 10 && tv.shownFields().indexOf("symbol") < 0 && tv.shownFields().indexOf("url") < 0, "generic list: ten columns, no constant or link columns");
  ok(tv.toCSV(",").split("\r\n")[1].indexOf("2026-01-21") === 0, "dated list opens newest first");
  tv.setColumns(["date", "url"]); ok(tv.shownFields().join() === "date,url", "user column pick");
  var crv = T.table(document.createElement("div"), { data: [{ expiration: "2027-03", price: 3 }, { expiration: "2026-12", price: 1 }, { expiration: "2027-01", price: 2 }] });
  ok(crv.toCSV(",").split("\r\n")[1].indexOf("2026-12") === 0, "curve opens by expiry, nearest first");
  // ---- a column whose rows carry different units (board: returns in %, yield rows in bp; audit C1)
  var BPF = { formatterFn: "int", suffix: " bp", decimalPlaces: null, heatScale: 25 };
  var bdefs = [{ field: "name", headerName: "Name", cellDataType: "text" }, { field: "m1", headerName: "1M", cellDataType: "number", formatterFn: "normalizedPercent", renderFn: "heat" },
    { field: "chg", headerName: "Chg", cellDataType: "number" }];
  var brows = [{ name: "S&P 500", m1: -0.042, chg: -60.1 }, { name: "US 10Y", m1: 12, chg: 3, _fmt: { m1: BPF, chg: BPF } },
    { name: "Gold", m1: 0.031, chg: 12.5 }, { name: "US 2Y", m1: -30, chg: -1, _fmt: { m1: BPF, chg: BPF } }, { name: "Oil", m1: null, chg: 0.4 }];
  function names(tv) { return tv.toCSV(",").split("\r\n").slice(1).map(function (l) { return l.split(",")[0]; }).join("|"); }
  var bdesc = T.table(document.createElement("div"), { data: brows, colDefs: bdefs, curated: true, sort: "m1:desc" });
  ok(names(bdesc) === "Gold|S&P 500|US 10Y|US 2Y|Oil", "sort desc: percent rows ranked together, then the bp rows, empties last (was 10Y +12 bp above Gold +3.1%)");
  ok(names(T.table(document.createElement("div"), { data: brows, colDefs: bdefs, curated: true, sort: "m1:asc" })) === "S&P 500|Gold|US 2Y|US 10Y|Oil", "sort asc keeps the unit groups");
  var mb = ["tips_gt_6_years_lt_11_years", "tips_gt_11_years", "coupons_lte_2_years", "tips_lt_2_years"].map(T.prettyField).join("|");
  ok(mb === "TIPS 6-11Y|TIPS >11Y|Coupons <=2Y|TIPS <2Y", "dealer maturity buckets: " + mb);
  // review H1: two-key default sort (EFA: newest month first, largest holders first within it)
  var hdefs = [{ field: "date", headerName: "Date", cellDataType: "date" }, { field: "country", headerName: "Country", cellDataType: "text" }, { field: "h", headerName: "Holdings", cellDataType: "number" }];
  var hrows = [{ date: "2026-08-01", country: "Japan", h: 9 }, { date: "2026-09-01", country: "Belgium", h: 2 }, { date: "2026-09-01", country: "Japan", h: 10 }, { date: "2026-09-01", country: "China", h: 7 }];
  var hcsv = T.table(document.createElement("div"), { data: hrows, colDefs: hdefs, sort: "date:desc,h:desc" }).toCSV(",").split("\r\n").slice(1).map(function (l) { return l.split(",")[1]; }).join("|");
  ok(hcsv === "Japan|China|Belgium|Japan", "two-key sort: newest date, then holdings desc: " + hcsv);
  var bcsv = bdesc.toCSV(",").split("\r\n");
  ok(bcsv[0] === "Name,1M (%),Chg", "CSV header names a column's unit");
  ok(bcsv[1] === "Gold,3.1,12.5" && bcsv[3] === "US 10Y,12 bp,3 bp" && bcsv[4] === "US 2Y,'-30 bp,'-1 bp", "CSV: a cell in another unit than its column says so");
  ok(T.table(document.createElement("div"), { data: [{ a: "x", s: 25 }], colDefs: [{ field: "a", cellDataType: "text" }, { field: "s", headerName: "Spread (bp)", cellDataType: "number", suffix: " bp" }] })
    .toCSV(",").split("\r\n")[0].split(",")[1] === "Spread (bp)", "unit already in the header is not repeated");
  var allbp = T.table(document.createElement("div"), { data: [{ name: "US 10Y", m1: 7, _fmt: { m1: BPF } }, { name: "US 2Y", m1: -3, _fmt: { m1: BPF } }], colDefs: bdefs.slice(0, 2), curated: true });
  var abc = allbp.toCSV(",").split("\r\n");
  ok(abc[0] === "Name,1M (bp)" && abc[1] === "US 10Y,7" && abc[2] === "US 2Y,-3", "a column whose every row is in bp (U.S. rates board) exports as a bp column");
  var abp = T.chartPlan(allbp.cols, allbp.baseRows, { x: "name", ys: ["m1"], split: "", type: "bar" });
  ok(abp.units.join() === "bp" && abp.series[0].y.join() === "-3,7" && !abp.msg && abp.series[0].name === "1M (bp)", "... and charts on a bp axis, nothing left out");
  var bp1 = T.chartPlan(bdesc.cols, brows, { x: "name", ys: ["m1"], split: "", type: "bar" });
  var by = bp1.series[0].y;      // x sorted by name: Gold, Oil, S&P 500, US 10Y, US 2Y
  ok(Math.abs(by[0] - 3.1) < 1e-9 && by[1] === null && Math.abs(by[2] + 4.2) < 1e-9 && by[3] === null && by[4] === null, "quick chart: fraction drawn in percent, bp rows left out");
  ok(bp1.units.join() === "%" && /2 rows/.test(bp1.msg) && /bp/.test(bp1.msg) && bp1.series[0].name === "1M (%)", "quick chart: the left-out rows are named in the note, unit in the series name");
  var mt = T.table(document.createElement("div"), { data: [{ d: "2026-01-02", ret: 0.05, cp: 5, px: 101.5, sp: 30 }, { d: "2026-01-05", ret: -0.01, cp: -1, px: 99, sp: 31 }],
    colDefs: [{ field: "d", cellDataType: "date" }, { field: "ret", headerName: "Return", cellDataType: "number", formatterFn: "normalizedPercent" },
      { field: "cp", headerName: "Change %", cellDataType: "number", formatterFn: "percent" }, { field: "px", headerName: "Price", cellDataType: "number" },
      { field: "sp", headerName: "Spread", cellDataType: "number", formatterFn: "int", suffix: " bp" }] });
  var mp = T.chartPlan(mt.cols, mt.baseRows, { x: "d", ys: ["ret", "cp"], split: "", type: "line" });
  ok(Math.abs(mp.series[0].y[0] - 5) < 1e-9 && Math.abs(mp.series[0].y[1] + 1) < 1e-9 && mp.series[1].y[0] === 5, "fraction and percent columns on one percent axis (were 0.05 vs 5)");
  ok(mp.units.join() === "%" && mp.series.every(function (s) { return s.yaxis === "y"; }) && mp.series[1].name === "Change %", "one axis for one unit; '%' already in the name");
  var mp2 = T.chartPlan(mt.cols, mt.baseRows, { x: "d", ys: ["ret", "px", "sp"], split: "", type: "line" });
  ok(mp2.units.join() === "%," && mp2.series.length === 2 && mp2.series[1].yaxis === "y2" && /Spread/.test(mp2.msg), "a second unit takes the right axis, a third is left out with a note");
  var tplM = { id: "d:y", tabs: [{ id: "big", name: "Big", items: [{ uid: "big-0", widgetId: "w1", params: {}, x: 0, y: 0, w: 20, h: 4 }, { uid: "big-1", widgetId: "w2", params: {}, x: 0, y: 4, w: 20, h: 4 }] }], groups: [], renamed: { small: "big" } };
  var savedM = { id: "d:y", tabs: [{ id: "big", name: "Big", edited: true, items: [{ uid: "big-0", widgetId: "w1", params: {}, x: 0, y: 0, w: 20, h: 6 }] }], groups: [] };
  var dm = T.mergeDashboard(tplM, savedM);
  ok(dm.tabs[0].items.length === 2 && dm.tabs[0].items[0].h === 6 && dm.tabs[0].items[1].uid === "big-1", "migrated tab keeps resize and inherits newly shipped card");
  dm.tabs[0].items = dm.tabs[0].items.slice(0, 1);
  var ovM = T.diffDashboard(tplM, dm);
  ok(ovM.overrides.big.cards["big-1"] && ovM.overrides.big.cards["big-1"].hidden === true, "removing a card records a per-card hidden override");
  var tplM2 = { id: "d:y", tabs: [{ id: "big", name: "Big", items: tplM.tabs[0].items.concat([{ uid: "big-2", widgetId: "w3", params: {}, x: 0, y: 8, w: 20, h: 4 }]) }], groups: [], renamed: { small: "big" } };
  var dm2 = T.mergeDashboard(tplM2, ovM);
  ok(dm2.tabs[0].items.length === 2 && dm2.tabs[0].items[0].h === 6 && dm2.tabs[0].items[1].uid === "big-2", "hidden card stays hidden while newly shipped card flows in");
  // review H3: a split with more than 8 series keeps the 8 largest (generation by fuel: wind and solar, not storage types)
  var fcols = [{ field: "date", type: "date", name: "Date" }, { field: "fuel", type: "text", name: "Fuel" }, { field: "v", type: "number", name: "V", def: {} }];
  var frows = []; ["a", "b", "c", "d", "e", "f", "g", "h", "Wind", "Solar"].forEach(function (k, i) { frows.push({ date: "2026-11-19", fuel: k, v: k === "Wind" ? 900 : k === "Solar" ? 800 : i + 1 }); });
  var fplan = T.chartPlan(fcols, frows, { x: "date", ys: ["v"], split: "fuel" });
  var fnames = fplan.series.map(function (s) { return s.name; }).join(",");
  ok(fplan.series.length === 8 && /Wind/.test(fnames) && /Solar/.test(fnames) && !/(^|,)a(,|$)/.test(fnames) && !/(^|,)b(,|$)/.test(fnames), "8 largest series kept: " + fnames);
  // owner 2026-11-19: search found "FOMC documents" but scrolled to the policy path: in an older edited tab the same
  // positional id is another card. The card is matched by widget/preset; none -> null (the app offers to add it).
  var owned = [{ uid: "pe-3", widgetId: "policy_path_x_obb", params: { preset: "fed" } }, { uid: "pe-7", widgetId: "sep_federal_reserve_obb", params: {} }];
  var fomc = { uid: "pe-3", widgetId: "federal_reserve_fomc_documents_download_federal_reserve_obb", params: {} };
  ok(T.findCardFor(owned, fomc, "pe-3") === null, "same id, other card: not taken for the searched card");
  ok(T.findCardFor(owned, { uid: "pe-1", widgetId: "sep_federal_reserve_obb", params: {} }, "pe-1") === owned[1], "found by what it is when its id moved");
  ok(T.findCardFor(owned, { uid: "pe-3" }, "pe-3") === owned[0], "user cards (no shipped widget): by id");

  // ---- audit 2026-11-16: financial statements (line order, visibility, periods, units)
  var yf = [
    { period_ending: "2025-09-30", tax_provision: 20, total_revenue: 400e9, operating_revenue: 400e9, gross_profit: 180e9, cost_of_revenue: 220e9, net_income: 100e9,
      diluted_earnings_per_share: 6.1, weighted_average_diluted_shares_outstanding: 15.1e9, interest_income: 3e9, interest_income_non_operating: 3e9,
      some_new_line: 5e9, normalized_income: 99e9, empty_line: null, total_pre_tax_income: 120e9, operating_income: 125e9 },
    { period_ending: "2026-09-30", tax_provision: 27, total_revenue: 478e9, operating_revenue: 478e9, gross_profit: 233e9, cost_of_revenue: 245e9, net_income: 132e9,
      diluted_earnings_per_share: 8.98, weighted_average_diluted_shares_outstanding: 14.7e9, interest_income: null, interest_income_non_operating: null,
      some_new_line: -2e9, normalized_income: 132e9, empty_line: null, total_pre_tax_income: 159e9, operating_income: 158e9 }];
  var L = T.statementLines("income", "yfinance", yf, []), f = L.map(function (l) { return l.field; });
  ok(f.indexOf("operating_revenue") < f.indexOf("total_revenue") && f.indexOf("total_revenue") < f.indexOf("cost_of_revenue") && f.indexOf("gross_profit") < f.indexOf("operating_income") &&
    f.indexOf("operating_income") < f.indexOf("total_pre_tax_income") && f.indexOf("total_pre_tax_income") < f.indexOf("net_income"), "yfinance lines in statement order whatever the field order sent");
  ok(f.indexOf("empty_line") < 0, "a line without data in any period is not shown");
  ok(f.indexOf("interest_income") < 0 && f.indexOf("interest_income_non_operating") >= 0, "a line repeated under another name is shown once");
  ok(f[f.length - 1] === "some_new_line" && L[L.length - 1].section === "Other items", "lines the map does not know are kept, under Other items");
  ok(L.filter(function (l) { return l.field === "total_revenue"; })[0].em === "tot" && L.filter(function (l) { return l.field === "operating_revenue"; })[0].ind, "totals emphasised, details indented");
  ok(L.filter(function (l) { return l.field === "diluted_earnings_per_share"; })[0].kind === "eps" && L.filter(function (l) { return l.field === "weighted_average_diluted_shares_outstanding"; })[0].kind === "shares", "per-share and share-count lines");
  var sec = [{ period_ending: "2026-09-26", fiscal_year: 2026, fiscal_period: "FY", reported_currency: "USD", net_income: 1, net_income_continuing: 1, net_cash_from_operating_activities: 2 }];
  var SL = T.statementLines("cash", "sec", sec, []).map(function (l) { return l.field; });
  ok(SL.join() === "net_income,net_cash_from_operating_activities", "SEC: a continuing-operations line only next to a discontinued one; keys are not lines");
  var nd = T.statementLines("income", "nasdaq", [{ period_ending: "2026-06-30", revenue: 5, gross_profit: 2, net_income: 1, extra: 3 }], ["period_ending", "revenue", "gross_profit", "net_income"]);
  ok(nd.map(function (l) { return l.field; }).join() === "revenue,gross_profit,net_income,extra", "a source without a map keeps its declared order, then the rest");
  var P = T.statementPeriods(yf, "annual");
  ok(P[0].label === "FY2026" && P[1].label === "FY2025", "annual periods newest first, FY labels");
  ok(T.statementPeriods([{ period_ending: "2026-06-27", fiscal_period: "Q3", fiscal_year: 2026 }, { period_ending: "2026-03-28" }], "quarterly").map(function (p) { return p.label; }).join() === "Q3 2026,Mar 2026", "quarterly labels");
  // audit B6: SEC's fiscal_year is the filing's year (ORCL's FY ending 2024-05-31 carries 2026): SEC columns are labelled from the period end
  ok(T.statementPeriods([{ period_ending: "2024-05-31", fiscal_period: "FY", fiscal_year: 2026 }], "annual", "sec")[0].label === "FY2024", "SEC annual: FY from the period end");
  ok(T.statementPeriods([{ period_ending: "2024-08-31", fiscal_period: "Q1", fiscal_year: 2026 }], "quarterly", "sec")[0].label === "Aug 2024", "SEC quarterly: month of the period end");
  // audit B7: a rate's unit comes from the source, never from the value (an effective tax rate of 130% is 1.3 as a fraction)
  var rates = [{ period_ending: "2026-09-30", tax_rate_for_calcs: 1.3, net_income: 5 }, { period_ending: "2025-09-30", tax_rate_for_calcs: 0.21, net_income: 4 }];
  var rv = T.statement(document.createElement("div"), { data: rates, kind: "income", provider: "yfinance", period: "annual" });
  var rrow = rv.baseRows.filter(function (r) { return /tax rate/i.test(r.line || ""); })[0];
  ok(rrow && rrow._fmt.p0.formatterFn === "normalizedPercent" && rrow._fmt.p1.formatterFn === "normalizedPercent" && T.fmtNum(1.3, rrow._fmt.p0) === "130.0%",
     "Yahoo tax rate: a fraction in every period (1.3 reads 130.0%, not 1.3%)");
  var rx = T.statement(document.createElement("div"), { data: rates, kind: "income", provider: "intrinio", period: "annual" });
  var xrow = rx.baseRows.filter(function (r) { return /tax rate/i.test(r.line || ""); })[0];
  ok(xrow && !xrow._fmt.p1.formatterFn && T.fmtNum(0.21, xrow._fmt.p1).indexOf("%") < 0, "a source whose rate unit is not known: the number as delivered, no % sign");
  ok(T.statementUnit(478e9).label === "mn" && T.statementUnit(478e9).dp === 0 && T.statementUnit(5e6).dp === 1, "one money unit per statement");
  ok(T.fmtNum(-1234e6, { scale: 1e6, scaleUnit: "", decimalPlaces: 0, paren: true }) === "(1,234)", "negatives in brackets");
  var host = document.createElement("div"), stv = T.statement(host, { data: yf, kind: "income", provider: "yfinance", period: "annual", periodOptions: [{ value: "annual" }, { value: "quarter" }] });
  var csv = stv.toCSV(",").split("\r\n");
  ok(stv.statement && csv[0] === "mn (reporting currency),FY2026,FY2025", "statement header: unit once, periods across");
  ok(csv[1].indexOf("Operating revenue,478000000000,400000000000") === 0, "first line, newest period first, raw values in exports");
  ok(host.querySelectorAll(".stmt-bar > .chip").length === 2 && host.querySelector(".stmt-bar .chip.on").textContent === "Annual", "Annual / Quarterly switch");
  ok(!stv.canTranspose, "a statement is not flipped");
  var many = []; for (var qq = 0; qq < 30; qq++) many.push({ period_ending: (2000 + qq) + "-12-31", fiscal_year: 2000 + qq, net_income: qq });
  var mhost = document.createElement("div"), mtv = T.statement(mhost, { data: many, kind: "income", provider: "sec", period: "annual" });
  ok(mtv.periods.length === 10 && mtv.periods[0].label === "FY2029" && mtv.lines.map(function (l) { return l.field; }).join() === "net_income", "latest 10 years first; fiscal year is not a line");
  [].filter.call(mhost.querySelectorAll(".chip"), function (c) { return /All 30/.test(c.textContent); })[0].click();
  ok(mtv.periods.length === 30 && mtv.toCSV(",").split("\r\n")[0].split(",").length === 31, "All periods on request, same table object");
  [].filter.call(mhost.querySelectorAll(".chip"), function (c) { return /Latest/.test(c.textContent); })[0].click();
  ok(mtv.periods.length === 10, "and back to the latest");
  // Stage 3 8c: common size and YoY (hand-computed: 30/200 = 15%; revenue 200/160 = +25%; base -10 or current -10 = n/m)
  var cs = [{ period_ending: "2026-12-31", total_revenue: 200, net_income: 30, diluted_earnings_per_share: 2 },
            { period_ending: "2025-12-31", total_revenue: 160, net_income: -10, diluted_earnings_per_share: -1 },
            { period_ending: "2024-12-31", total_revenue: 100, net_income: 20, diluted_earnings_per_share: 1.5 }];
  function lineOf(tv, re) { return tv.baseRows.filter(function (r) { return re.test(r.line || ""); })[0] || {}; }
  var shown = null, chost = document.createElement("div");
  var ctv = T.statement(chost, { data: cs, kind: "income", provider: "yfinance", period: "annual", show: "common", onShow: function (v) { shown = v; } });
  ok(ctv.show === "common" && Math.abs(lineOf(ctv, /^Net income$/).p0 - 15) < 1e-9 && Math.abs(lineOf(ctv, /^Total revenue$/).p0 - 100) < 1e-9, "common size: net income 30 of revenue 200 = 15%");
  ok(lineOf(ctv, /earnings per share/i).p0 == null && ctv.toCSV(",").split(String.fromCharCode(13, 10))[0].indexOf("% of revenue") === 0, "per-share lines blank in common size; header names the base");
  var ytv = T.statement(document.createElement("div"), { data: cs, kind: "income", provider: "yfinance", period: "annual", show: "yoy" });
  var rv = lineOf(ytv, /^Total revenue$/), ni = lineOf(ytv, /^Net income$/);
  ok(Math.abs(rv.p0 - 25) < 1e-9 && Math.abs(rv.p1 - 60) < 1e-9 && rv.p2 == null, "YoY revenue +25%, +60%, oldest blank");
  ok(ni.p0 === "n/m" && ni.p1 === "n/m", "YoY n/m when the base is negative or the sign flips");
  [].filter.call(chost.querySelectorAll(".chip"), function (c) { return /YoY/.test(c.textContent); })[0].click();
  ok(shown === "yoy", "view chip reports the choice to the card");
  var btv = T.statement(document.createElement("div"), { data: [{ period_ending: "2026-12-31", cash_and_cash_equivalents: 5 }], kind: "balance", provider: "yfinance", period: "annual", show: "common" });
  ok(btv.show === "values", "no total assets: common size falls back to values (chip disabled)");
  ok(T.statementBase("income", { net_interest_income: 60, total_noninterest_income: 40 }) === 100 && T.statementBase("income", { net_interest_income: 60 }) == null,
    "bank base: net interest + non-interest income only when both exist");
  ok(T.statementBase("cash", { total_revenue: 100, operating_cash_flow: 20 }) == null, "cash flow: no common-size base");
  ok(T.statementYearAgo(T.statementPeriods([{ period_ending: "2026-09-30" }, { period_ending: "2026-06-30" }, { period_ending: "2025-09-27" }], "quarter"), { period_ending: "2026-09-30" }).period_ending === "2025-09-27",
    "quarterly YoY: four quarters back by date, not the previous column");

  // ---- tables: declared key columns only -> the data columns are shown; ids without separators; narrow boards
  var wide = [], dk = {}; for (var q = 0; q < 5; q++) { var rr = { period_ending: "2026-0" + (q + 1) + "-01", fiscal_period: "Q" + q, fiscal_year: 2026 }; for (var z = 0; z < 12; z++) rr["item_" + z] = q * z + 1; wide.push(rr); }
  var dtv = T.table(document.createElement("div"), { data: wide, colDefs: [{ field: "period_ending" }, { field: "fiscal_period" }, { field: "fiscal_year" }] });
  ok(dtv.shownFields().filter(function (x) { return /^item_/.test(x); }).length >= 5, "declared period keys alone do not hide the line items");
  ok(T.fmtNum(334111, settled("naics", [334111, 334112])) === "334111" && T.NO_GROUP.test("isin") && T.NO_GROUP.test("orderbook_id") && T.NO_GROUP.test("uuid"), "ids without thousands separators");
  var brd = []; for (var b = 0; b < 4; b++) brd.push({ name: "N" + b, price: 1 + b, spark: [1, 2, 3], m1: 0.1, m3: 0.2, ytd: 0.3, symbol: "S" + b });
  var btv = T.table(document.createElement("div"), { data: brd, colDefs: [{ field: "name" }, { field: "price" }, { field: "spark", renderFn: "sparkline" }, { field: "m1" }, { field: "m3" }, { field: "ytd" }, { field: "symbol" }], curated: true });
  ok(btv.shownNow().indexOf("m3") >= 0, "wide board shows 3M");
  btv.setNarrow(true); ok(btv.shownNow().indexOf("m3") < 0 && btv.shownNow().indexOf("symbol") < 0 && btv.shownNow().indexOf("ytd") >= 0, "narrow board drops 3M and the ticker");

  // ---- sources: no "TMX (TMX)"; readable names in "Data used"
  ok(T.sourceLine([{ name: "TMX", detail: "TMX Equity Quote" }]) === "Source: TMX", "provider name is not taken for a series id");
  var ds = T.defSources({ params: [{ paramName: "provider", value: "tmx" }], endpoint: "/api/v1/equity/price/quote", name: "" }, { id: "v5" })[0];
  ok(ds.detail === "equity › price › quote" && ds.route === "equity/price/quote", "Data used shows a readable name; the route only as a tooltip");

  // ---- refresh: one card's forced reload forgets only its own cached answer
  T._dataCache.set("u:a", { t: Date.now(), json: 1 }); T._dataCache.set("u:b", { t: Date.now(), json: 2 });
  T.dropCached("u:a");
  ok(!T._dataCache.has("u:a") && T._dataCache.has("u:b"), "only that card's cache entry is dropped");
  T._dataCache.delete("u:b");

  // ---- view preferences are not layout edits; one-time release of tabs edited only by flips / views / settings bar
  var keepS = T.S.state, keepG = T.S.generated;
  try {
    var gtpl = { id: "d:t", name: "T", tabs: [{ id: "a", name: "A", items: [{ uid: "a-0", widgetId: "w1", backend: "v5", x: 0, y: 0, w: 10, h: 5, params: {}, compact: true }] },
      { id: "b", name: "B", items: [{ uid: "b-0", widgetId: "w2", backend: "v5", x: 0, y: 0, w: 10, h: 5, params: {} }] }], groups: [] };
    T.S.generated = [gtpl];
    T.S.state = { v: 2, dashboards: { "d:t": { id: "d:t", tabs: [
      { id: "a", name: "A", edited: true, items: [{ uid: "a-0", widgetId: "w1", backend: "v5", x: 0, y: 0, w: 10, h: 5, params: {}, compact: false, flip: true }] },
      { id: "b", name: "B", edited: true, items: [{ uid: "b-0", widgetId: "w2", backend: "v5", x: 0, y: 0, w: 12, h: 5, params: {} }] }], groups: [] } }, groups: {} };
    var mg = T.migrateViewPrefs();
    ok(mg.released.join() === "T › A" && mg.kept.join() === "T › B", "tab with only view changes released, tab with a resized card kept");
    ok(T.S.state.cardview["d:t"]["a-0"].flip === true && T.S.state.cardview["d:t"]["a-0"].compact === false, "its view choices moved to state.cardview");
    ok(!T.S.state.dashboards["d:t"].tabs[0].edited && T.S.state.v === 3, "released tab no longer edited; runs once");
    ok(T.migrateViewPrefs().released.length === 0, "second run does nothing");
    var it = { uid: "a-0", widgetId: "w1", view: "chart", flip: true, _vbase: { view: undefined, flip: undefined, compact: true } };
    var sv = T.savedItem(it);
    ok(sv.view === undefined && sv.flip === undefined && sv.compact === true, "saved layout keeps the shipped view settings, not the preference");
  } finally { T.S.state = keepS; T.S.generated = keepG; }

  // ---- concept source switching when one widget serves multiple concepts (e.g. Nasdaq / TMX movers)
  var keepC = T.S.concepts, keepW = T.S.widgets, keepEp = T.S.epIndex, keepCIx = T.S.conceptIx, keepBe = T.S.backends;
  try {
    var mkW = function (id, ep, ps) {
      return { widgetId: id, endpoint: ep, params: (ps || []).map(function (p) { return { paramName: p }; }), source: ["Test"] };
    };
    T.S.backends = [{ id: "v4", name: "V4" }, { id: "v5", name: "V5" }];
    T.S.widgets = {
      v4: {
        equity_discovery_gainers_yfinance_obb: mkW("equity_discovery_gainers_yfinance_obb", "/api/v1/equity/discovery/gainers", ["limit"]),
        equity_discovery_losers_yfinance_obb: mkW("equity_discovery_losers_yfinance_obb", "/api/v1/equity/discovery/losers", ["limit"]),
        equity_discovery_active_yfinance_obb: mkW("equity_discovery_active_yfinance_obb", "/api/v1/equity/discovery/active", ["limit"])
      },
      v5: {
        nasdaq_markets_movers_nasdaq_obb: mkW("nasdaq_markets_movers_nasdaq_obb", "/api/v1/nasdaq/markets/movers", ["category", "limit"]),
        tmx_markets_movers_tmx_obb: mkW("tmx_markets_movers_tmx_obb", "/api/v1/tmx/markets/movers", ["category", "exchange", "limit"])
      }
    };
    T.S.epIndex = null; T.S.conceptIx = null;
    T.S.concepts = {
      "movers.gainers": { title: "Top gainers", members: [
        { b: "v4", w: "equity_discovery_gainers_yfinance_obb", l: "Yahoo Finance (U.S.)" },
        { b: "v5", w: "nasdaq_markets_movers_nasdaq_obb", l: "Nasdaq (most advanced)", p: { category: "most_advanced" } },
        { b: "v5", w: "tmx_markets_movers_tmx_obb", l: "TMX (Toronto)", p: { category: "gainers", exchange: "tsx" } }
      ] },
      "movers.losers": { title: "Top losers", members: [
        { b: "v4", w: "equity_discovery_losers_yfinance_obb", l: "Yahoo Finance (U.S.)" },
        { b: "v5", w: "nasdaq_markets_movers_nasdaq_obb", l: "Nasdaq (most declined)", p: { category: "most_declined" } },
        { b: "v5", w: "tmx_markets_movers_tmx_obb", l: "TMX (Toronto)", p: { category: "losers", exchange: "tsx" } }
      ] },
      "movers.active": { title: "Most active", members: [
        { b: "v4", w: "equity_discovery_active_yfinance_obb", l: "Yahoo Finance (U.S.)" },
        { b: "v5", w: "nasdaq_markets_movers_nasdaq_obb", l: "Nasdaq (most active, shares)", p: { category: "most_active_share_volume" } }
      ] }
    };
    var loserIt = { uid: "movers-1", backend: "v4", widgetId: "equity_discovery_losers_yfinance_obb", params: {}, sort: "percent_change:asc" };
    ok(T.applySource(loserIt, "v5:nasdaq_markets_movers_nasdaq_obb") && loserIt.params.category === "most_declined" &&
      T.memberOf(loserIt).id === "movers.losers", "switching Top losers to Nasdaq keeps movers.losers and sets most_declined");
    var alts = T.alternatives(loserIt, T.S.widgets.v5.nasdaq_markets_movers_nasdaq_obb, loserIt.params);
    ok(alts.some(function (a) { return a.value === "v4:equity_discovery_losers_yfinance_obb"; }) &&
      !alts.some(function (a) { return a.value === "v4:equity_discovery_gainers_yfinance_obb"; }), "alternatives on switched Top losers stay on losers");
    ok(T.applySource(loserIt, "v5:tmx_markets_movers_tmx_obb") && loserIt.params.category === "losers" &&
      T.memberOf(loserIt).id === "movers.losers", "switching Top losers to TMX keeps movers.losers and sets losers");
    ok(T.applySource(loserIt, "v4:equity_discovery_losers_yfinance_obb") && loserIt.widgetId === "equity_discovery_losers_yfinance_obb" &&
      T.memberOf(loserIt).id === "movers.losers", "switching back returns to Yahoo Finance losers");

    var activeIt = { uid: "movers-2", backend: "v4", widgetId: "equity_discovery_active_yfinance_obb", params: {} };
    ok(T.applySource(activeIt, "v5:nasdaq_markets_movers_nasdaq_obb") && activeIt.params.category === "most_active_share_volume" &&
      T.memberOf(activeIt).id === "movers.active", "switching Most active to Nasdaq keeps movers.active");

    ok(T.memberOf({ backend: "v5", widgetId: "tmx_markets_movers_tmx_obb", params: { category: "losers" } }).id === "movers.losers",
      "direct multi-concept widget resolves concept from pinned params");

    T.S.generated = [{ id: "d:eq-screen", name: "Screeners & Movers", groups: [], tabs: [{ id: "movers", name: "Movers", items: [
      { uid: "movers-0", backend: "v4", widgetId: "equity_discovery_gainers_yfinance_obb", params: {}, sort: "percent_change:desc" },
      { uid: "movers-1", backend: "v4", widgetId: "equity_discovery_losers_yfinance_obb", params: {}, sort: "percent_change:asc" }
    ] }] }];
    T.S.state = { v: 3, dashboards: {}, groups: {}, srcsel: { "d:eq-screen": { "movers-1": "v5:nasdaq_markets_movers_nasdaq_obb" } } };
    var fd1 = T.findDashboard("d:eq-screen");
    ok(fd1.tabs[0].items[1].params.category === "most_declined" && T.memberOf(fd1.tabs[0].items[1]).id === "movers.losers",
      "findDashboard restores Nasdaq losers cleanly");
    T.S.state = { v: 3, dashboards: {}, groups: {}, srcsel: { "d:eq-screen": { "movers-1": "v4:equity_discovery_gainers_yfinance_obb" } } };
    var fd2 = T.findDashboard("d:eq-screen");
    ok(fd2.tabs[0].items[1].widgetId === "equity_discovery_losers_yfinance_obb" && !T.S.state.srcsel["d:eq-screen"]["movers-1"],
      "findDashboard rejects stale cross-concept srcsel entry");

    var mv = T.table(document.createElement("div"), { data: [{ symbol: "A", change_percent: 0.1 }, { symbol: "B", change_percent: -0.2 }], sort: "percent_change:asc" });
    ok(mv.toCSV(",").split("\r\n")[1].indexOf("B,") === 0, "percent_change sort falls back to change_percent on Nasdaq/TMX movers");
  } finally {
    T.S.concepts = keepC; T.S.widgets = keepW; T.S.epIndex = keepEp; T.S.conceptIx = keepCIx; T.S.backends = keepBe;
    T.S.state = keepS; T.S.generated = keepG;
  }

  // ---- SEC filings: clickable URL columns, broadcast url -> document_url, fileSelector isolation, ticker clear, non-US & XML errors
  var longSecUrl = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/" + "a".repeat(90) + ".htm";
  var fDefs = [{ field: "filing_date", cellDataType: "dateString" }, { field: "report_type" }, { field: "c1" }, { field: "c2" },
    { field: "c3" }, { field: "c4" }, { field: "c5" }, { field: "c6" }, { field: "c7" }, { field: "c8" }, { field: "c9" },
    { field: "filing_detail_url" },
    { field: "report_url", headerName: "Document", renderFn: "cellOnClick", renderFnParams: { actionType: "groupBy", groupBy: { paramName: "url", valueField: "report_url" } } }];
  var fRows = [];
  for (var fi = 0; fi < 5; fi++) {
    fRows.push({ filing_date: "2026-10-0" + (fi + 1), report_type: "10-K", c1: fi, c2: fi, c3: fi, c4: fi, c5: fi, c6: fi, c7: fi, c8: fi, c9: fi,
      filing_detail_url: "https://www.sec.gov/detail/" + fi, report_url: longSecUrl });
  }
  var clicked = [];
  var ftvHost = document.createElement("div");
  var ftv = T.table(ftvHost, { data: fRows, colDefs: fDefs, onAction: function (p, v) { clicked.push([p, v]); return true; } });
  ok(ftv.shownFields().indexOf("report_url") >= 0 && ftv.shownFields().indexOf("filing_detail_url") < 0,
    "cellOnClick url column survives ID_HIDE, constant/length checks and 10-col cap; non-clickable link stays hidden");
  var clickCell = ftvHost.querySelector("td.click");
  ok(clickCell && clickCell.textContent === "Open ↗", "cellOnClick URL renders as Open link");
  clickCell.click();
  ok(clicked.length === 1 && clicked[0][0] === "url" && clicked[0][1] === longSecUrl, "clicking cellOnClick row fires onAction with url");

  var hDefs = [{ field: "sequence" }, { field: "type" }, { field: "filename" }, { field: "description" }, { field: "content" },
    { field: "url", headerName: "Document", renderFn: "cellOnClick", renderFnParams: { actionType: "groupBy", groupBy: { paramName: "document_url", valueField: "url" } } }];
  var hHost = document.createElement("div");
  var hActions = [];
  var htv = T.table(hHost, { data: [{ sequence: 1, type: "144", filename: "primary_doc.xml", description: "Form 144", content: "XML", url: "https://www.sec.gov/Archives/edgar/data/1/1/primary_doc.xml" }],
    colDefs: hDefs, onAction: function (p, v) { hActions.push([p, v]); return true; } });
  ok(htv.isTransposed() && hHost.querySelector("td.click"), "transposed single-row Filing Headers keeps cellOnClick cell");
  hHost.querySelector("td.click").click();
  ok(hActions.length === 1 && hActions[0][0] === "document_url" && /primary_doc\.xml$/.test(hActions[0][1]), "transposed Filing Headers click fires document_url");

  // P4-04: /x/ errors render from error.kind; under the whole fault matrix no card text shows a URL, a traceback,
  // "Error:" or a Python exception name.
  (function () {
    var FORBID = /Traceback|Error:|https?:\/\/|\b[A-Z][A-Za-z]*(?:Error|Exception)\b/;
    var MSGS = [
      "OpenBB V5 backend is not reachable (<urlopen error [WinError 10061] No connection could be made because the target machine actively refused it>).",
      "OpenBB V5 backend did not answer in time.", "HTTP 503 from http://127.0.0.1:6900/api/v1/equity/price/quote",
      "Too many requests", "not found", "OpenBB V5 backend answered with an HTML page instead of data.",
      "Traceback (most recent call last):\n  File \"x.py\", line 1\nKeyError: 'close'", "ValueError: could not convert string to float: 'n/a'",
      "Internal error in the terminal (ref a1b2c3; details in local/logs/ui.log).", "SHEL.L was not found in the Cboe options directory (see https://www.cboe.com/).",
      "Click a bond's CUSIP in the table or the curve above (or type a CUSIP in the settings)."];
    var KINDS = ["input", "not_covered", "upstream", "rate_limited", "timeout", "unavailable", "internal"];
    var seen = 0;
    KINDS.forEach(function (k) {
      MSGS.forEach(function (m) {
        [true, false].forEach(function (um) {
          var c = T.classifyKind(k, m, { source: "Yahoo Finance", symbol: "SAP.DE", userMessage: um }, { retryAfter: 30 });
          var shown = (c.text || "") + " " + (c.hint || "");
          ok(c.text && !FORBID.test(shown), "kind " + k + " never shows technical text: " + shown.slice(0, 120));
          seen++;
        });
      });
    });
    ok(seen === KINDS.length * MSGS.length * 2, "fault matrix covered (" + seen + ")");
    ok(T.classifyKind("rate_limited", "Too many requests", { source: "Nasdaq" }, { retryAfter: 30 }).hint === "Try again in about 30 seconds (press ↻).", "Retry-After 30 s reaches the hint");
    ok(/about 3 minutes/.test(T.classifyKind("rate_limited", "x", { source: "Nasdaq" }, { retryAfter: 150 }).hint), "Retry-After 150 s -> about 3 minutes");
    ok(/about 10 minutes/.test(T.classifyKind("rate_limited", "OECD limit", { source: "OECD" }, {}).hint), "no Retry-After from OECD -> 10 minutes");
    var ic = T.classifyKind("internal", MSGS[8], {}, {});
    ok(ic.cls === "bug" && ic.kind === "err" && ic.ref === "a1b2c3" && /ref a1b2c3/.test(ic.text), "internal keeps the server's ref");
    var inp = T.classifyKind("input", MSGS[10], { userMessage: true }, {});
    ok(inp.cls === "input" && inp.text === MSGS[10], "input shows the view's prompt as written");
    var nc = T.classifyKind("not_covered", MSGS[9], { source: "Cboe", symbol: "SHEL.L" }, {});
    ok(nc.cls === "covered" && nc.text === "SHEL.L was not found in the Cboe options directory.", "not covered keeps the message, drops the URL");
    var up = T.classifyKind("upstream", MSGS[0], { source: "Yahoo Finance", userMessage: true }, {});
    ok(up.cls === "upstream" && up.text === "Yahoo Finance is not answering properly right now.", "an unreachable source gets the plain upstream text");
    var own = T.classifyKind("upstream", "Yahoo Finance has no bid/ask quotes for SPX right now, so strategies cannot be priced.", { source: "Cboe", userMessage: true }, {});
    ok(own.cls === "message" && /no bid\/ask quotes/.test(own.text), "a view's own clean explanation is shown as is");
    ok(T.classifyKind("timeout", "V5 is slow to answer the first request for this dataset. It keeps loading in the background", {}, {}).cls === "slow", "slow first request keeps its auto-retry class");
    ok(T.cleanMessage("Traceback (most recent call last)") === null && T.cleanMessage("x (HTTPError 503)") === "x", "cleanMessage drops technical tails or refuses");
  })();

  // P4-06: one error renderer for every card type, fed the server's own error envelopes for each fault kind
  (function () {
    var FORBID = /Traceback|Error:|https?:\/\/|\b[A-Z][A-Za-z]*(?:Error|Exception)\b/;
    var src = [{ name: "Yahoo Finance", detail: "quotes", url: "https://finance.yahoo.com/" }];
    function env(kind, msg, status, extra) {
      return [status, JSON.stringify(Object.assign({ error: { kind: kind, message: msg, retryable: kind !== "input" && kind !== "not_covered" && kind !== "internal" }, detail: msg, sources: src, user_message: true }, extra || {}))];
    }
    var FAULTS = {
      timeout: env("timeout", "OpenBB V5 backend did not answer in time.", 504),
      refused: env("upstream", "OpenBB V5 backend is not reachable (<urlopen error [WinError 10061] No connection could be made>).", 502),
      http429: env("rate_limited", "Too many requests", 429, { error: { kind: "rate_limited", message: "Too many requests", retryable: true, retry_after: 30 } }),
      http5xx: env("upstream", "HTTP 503 from http://127.0.0.1:6900/api/v1/equity/price/quote", 502),
      http404: env("not_covered", "Yahoo Finance has nothing for 'ZZZZQ9'. Check the ticker.", 404),
      html_body: env("upstream", "OpenBB V5 backend answered with an HTML page instead of data.", 502),
      internal: env("internal", "Internal error in the terminal (ref q1w2e3; details in local/logs/ui.log).", 500),
      raw_html: [502, "<!DOCTYPE html><html><body>Bad Gateway</body></html>"]
    };
    var TYPES = ["chart", "table", "news", "frame", "statements"], drawn = 0;
    TYPES.forEach(function (ty) {
      Object.keys(FAULTS).forEach(function (fk) {
        var err = T.httpError(FAULTS[fk][0], FAULTS[fk][1]);
        var box = document.createElement("div");
        var c = T.renderError(box, err, { source: "Yahoo Finance", symbol: "SAP.DE", userMessage: err.userMessage, cardType: ty });
        ok(box.textContent && !FORBID.test(box.textContent), ty + "/" + fk + ": no raw text in the card (" + box.textContent.slice(0, 100) + ")");
        if (fk !== "raw_html") ok(box.firstChild.getAttribute("data-error-kind") === JSON.parse(FAULTS[fk][1]).error.kind, ty + "/" + fk + ": drawn by error.kind");
        ok(c && c.text, ty + "/" + fk + ": classification returned");
        drawn++;
      });
    });
    ok(drawn === TYPES.length * Object.keys(FAULTS).length, "every card type x fault kind drawn (" + drawn + ")");
    var e429 = T.httpError(429, FAULTS.http429[1]);
    ok(e429.errorKind === "rate_limited" && e429.retryAfter === 30 && e429.sources.length === 1 && e429.userMessage, "httpError reads kind, retry_after, sources");
    ok(T.httpError(502, "<html>").rawBackend === true && !T.httpError(502, "<html>").errorKind, "a raw non-envelope answer is marked rawBackend");
    ok(!FORBID.test(T.userText(T.httpError(500, FAULTS.internal[1]))) && /ref q1w2e3/.test(T.userText(T.httpError(500, FAULTS.internal[1]))), "userText keeps the ref, no raw text");
    var tE = new Error("x"); tE.timedOut = true;
    ok(T.classifyFailure(tE, { source: "SEC" }).text === "SEC did not answer in time.", "a client-side timeout reads as timeout");
    ok(T.classifyFailure(new TypeError("Failed to fetch"), {}).text === "The terminal is busy or restarting.", "a dropped connection reads as unavailable");

    // footer: data as-of from the envelope, generic notes and status
    ok(T.asOfText({ as_of: "2026-01-05" }, "10:00").text === "data as of 2026-01-05", "as_of date in the footer");
    ok(T.asOfText({ as_of: "2026-02-03T15:30:00Z" }, "10:00").text === "data as of 2026-02-03 15:30 UTC", "as_of datetime in UTC");
    var na = T.asOfText({ as_of_na: "This answer has no dated data points." }, "10:00");
    ok(na.text === "updated 10:00" && /no dated/.test(na.title), "as_of_na: fetch time, with the reason as the title");
    ok(T.asOfText(null, "10:00").text === "updated 10:00", "a raw backend answer keeps the fetch time");
    var f1 = document.createElement("div");
    T.renderTrust(f1, { stale: "Old copy", notes: ["Old copy", "Euro area rate substituted for Germany"], status: "stale" });
    ok((f1.textContent.match(/Old copy/g) || []).length === 1 && /ⓘ Euro area rate substituted for Germany/.test(f1.textContent), "notes drawn once each, generically");
    var f2 = document.createElement("div");
    T.renderTrust(f2, { status: "partial" });
    ok(/partial answer/.test(f2.textContent), "status partial without a note gets a badge");
    var f3 = document.createElement("div");
    T.renderTrust(f3, { status: "ok", notes: [] });
    ok(f3.textContent === "", "status ok draws nothing");
  })();

  ok(T.classifyError("CIK not found for symbol SAP.DE", 400, { source: "SEC", provider: "sec", symbol: "SAP.DE" }).cls === "covered", "CIK not found classifies as not covered");
  var xmlErr = T.classifyError("Invalid URL. Please a SEC URL that directs specifically to a HTM or HTML file.", 400, { source: "SEC", provider: "sec" });
  ok(xmlErr.cls === "input" && xmlErr.kind === "info", "sec/htm_file XML rejection classifies as input info, not bug");
  ok(/SEC covers U\.S\.-listed/.test(T.nonUSNote({ endpoint: "/api/v1/sec/standardized_statement", source: null, params: [] }, "SAP.DE", "CIK not found for symbol SAP.DE", false) || ""),
    "nonUSNote falls back to providerName when def.source is null");
  ok(/Nasdaq covers U\.S\.-listed/.test(T.nonUSNote({ endpoint: "/api/v1/nasdaq/equity/filings_viewer", source: ["Custom"], params: [] }, "SAP.DE", "No documents available for these settings.", false) || ""),
    "nonUSNote resolves Custom source via endpoint namespace");
  ok(T.nonUSNote({ endpoint: "/api/v1/sec/company_filings", source: ["SEC"], params: [] }, "BRK.B", "No data", false) === null, "U.S. share class BRK.B is not flagged non-US");

  var keepCur = T.S.cur, keepTab = T.S.tabId, keepCards = T.S.cards, keepSt = T.S.state, keepBroken = T.S.stateBroken;
  var grpBox = document.getElementById("groups"), keepGrpNodes = grpBox ? Array.prototype.slice.call(grpBox.childNodes) : [];
  var secStage = document.createElement("div");
  secStage.style.cssText = "width:900px;height:700px;overflow:auto;";
  document.body.appendChild(secStage);
  try {
    T.S.stateBroken = true;
    T.S.state = { v: 3, dashboards: {}, groups: {}, ui: { collapsed: {} } };
    var symG = { name: "Symbol", paramName: "symbol", value: "AAPL" };
    var filingsIt = { uid: "sec-0", widgetId: "sec_company_filings_sec_obb", backend: "v5", params: {}, groups: ["Symbol"] };
    var headersIt = { uid: "sec-1", widgetId: "sec_filing_headers_sec_obb", backend: "v5", params: {}, groups: [], require: ["url"] };
    var viewerIt = { uid: "sec-2", widgetId: "sec_filing_viewer_sec_obb", backend: "v5", params: {}, groups: [], require: ["document_url"] };
    var nasdaqIt = { uid: "sec-3", widgetId: "nasdaq_equity_filings_viewer_custom_obb", backend: "v5", params: { document_url: ["https://pdf.example/aapl.pdf"] }, groups: ["Symbol"] };
    T.S.cur = { id: "d:test-sec", name: "Test SEC", groups: [symG], tabs: [{ id: "t", name: "Filings", items: [filingsIt, headersIt, viewerIt, nasdaqIt] }] };
    T.S.tabId = "t";
    var wCtx = function (it, def) {
      return {
        item: it, def: def, backend: { id: "v5", name: "Test", url: "" }, scroller: secStage,
        groupOf: function (item, p) { return (item.groups || []).indexOf("Symbol") >= 0 && p === "symbol" ? symG : null; },
        groupsOf: function (item) { return (item.groups || []).indexOf("Symbol") >= 0 ? [symG] : []; },
        setGroup: T.setGroup, broadcast: T.broadcast, siblings: function () { return []; },
        onChange: function () {}, onRemove: function () {}, relayout: function () {}
      };
    };
    var filingsDef = { widgetId: "sec_company_filings_sec_obb", name: "Company Filings", type: "table", endpoint: "/__test_filings",
      params: [{ paramName: "symbol", type: "text", show: true }], source: ["SEC"] };
    var headersDef = { widgetId: "sec_filing_headers_sec_obb", name: "Filing Headers", type: "table", endpoint: "/__test_headers",
      params: [{ paramName: "url", type: "text", label: "Filing URL", show: true, optional: true }, { paramName: "document_url", type: "text", show: false, optional: true }], source: ["SEC"] };
    var viewerDef = { widgetId: "sec_filing_viewer_sec_obb", name: "Filing Viewer", type: "iframe", endpoint: "/__test_viewer",
      params: [{ paramName: "document_url", type: "text", label: "Document URL", show: true, optional: true }], source: ["SEC"] };
    var fixedDefs = T.fixDefs({
      sec_filing_viewer_sec_obb: { params: [{ paramName: "document_url", optional: true }] },
      sec_filing_headers_sec_obb: { params: [{ paramName: "url", optional: true }, { paramName: "use_cache", optional: true }] },
      regulators_sec_filing_headers_sec_obb: { params: [{ paramName: "url", optional: true }] },
      sec_htm_file_sec_obb: { params: [{ paramName: "url", optional: true }] },
      regulators_sec_htm_file_sec_obb: { params: [{ paramName: "url", optional: true }] },
      equity_fundamental_filings_tmx_obb: { params: [{ paramName: "provider", value: "tmx" }], data: { table: { columnsDefs: [{ field: "filing_date" }, { field: "report_url" }] } } },
      equity_fundamental_filings_yfinance_obb: { params: [{ paramName: "symbol" }], data: { table: { columnsDefs: [{ field: "report_url" }] } } },
      commodity_psd_report_usda_obb: { params: [{ paramName: "provider", value: "usda", show: false }] }
    });
    var psdUrls = fixedDefs.commodity_psd_report_usda_obb.params[0];
    ok(psdUrls.paramName === "urls" && (psdUrls.roles || []).indexOf("fileSelector") >= 0 && psdUrls.optionsEndpoint === "/api/v1/usda/psd_report_urls" && fixedDefs.commodity_psd_report_usda_obb.params.length === 2,
      "USDA PSD circulars get their required picker (B3)");
    ok(fixedDefs.sec_filing_viewer_sec_obb.params[0].optional === false &&
      fixedDefs.sec_filing_headers_sec_obb.params[0].optional === false &&
      fixedDefs.sec_filing_headers_sec_obb.params[1].optional === true &&
      fixedDefs.regulators_sec_filing_headers_sec_obb.params[0].optional === false &&
      fixedDefs.sec_htm_file_sec_obb.params[0].optional === false &&
      fixedDefs.regulators_sec_htm_file_sec_obb.params[0].optional === false,
      "fixDefs marks SEC filing viewer/headers/htm URL params non-optional");
    ok(fixedDefs.equity_fundamental_filings_tmx_obb.params[0].paramName === "symbol" &&
      fixedDefs.equity_fundamental_filings_tmx_obb.data.table.columnsDefs[1].renderFn === "cellOnClick" &&
      fixedDefs.equity_fundamental_filings_tmx_obb.data.table.columnsDefs[1].renderFnParams.groupBy.paramName === "url" &&
      fixedDefs.equity_fundamental_filings_yfinance_obb.data.table.columnsDefs[0].renderFn === "cellOnClick",
      "fixDefs adds symbol & clickable report_url to TMX/Yahoo company filings");
    T.fixDefs({ sec_filing_viewer_sec_obb: viewerDef });
    var noReqViewerIt = { uid: "sec-2b", widgetId: "sec_filing_viewer_sec_obb", backend: "v5", params: {}, groups: [] };
    var noReqViewerCard = T.widget(wCtx(noReqViewerIt, viewerDef));
    noReqViewerCard.node.classList.add("max");
    secStage.appendChild(noReqViewerCard.node);
    noReqViewerCard.check();
    ok(!noReqViewerCard.node.querySelector("iframe") && /Pick a filing/.test((noReqViewerCard.node.querySelector(".msg") || {}).textContent || ""),
      "fixDefs-processed Filing Viewer blocks iframe with no item.require (saved edited tab / + Widget)");
    noReqViewerCard.destroy();
    noReqViewerCard.node.remove();
    var nasdaqDef = { widgetId: "nasdaq_equity_filings_viewer_custom_obb", name: "Nasdaq SEC Filings", type: "multi_file_viewer", endpoint: "/__test_nasdaq",
      params: [{ paramName: "symbol", type: "text", value: "AAPL" }, { paramName: "document_url", type: "endpoint", show: false, roles: ["fileSelector"], optionsParams: { symbol: "$symbol" } }], source: ["Custom"] };
    var filingsCard = T.widget(wCtx(filingsIt, filingsDef));
    var headersCard = T.widget(wCtx(headersIt, headersDef));
    var viewerCard = T.widget(wCtx(viewerIt, viewerDef));
    var nasdaqCard = T.widget(wCtx(nasdaqIt, nasdaqDef));
    viewerCard.node.classList.add("max");
    secStage.appendChild(headersCard.node);
    secStage.appendChild(viewerCard.node);
    secStage.appendChild(nasdaqCard.node);
    viewerCard.check();
    var initMsg = viewerCard.node.querySelector(".msg") ? viewerCard.node.querySelector(".msg").textContent : "";
    ok(initMsg.indexOf("Pick a filing in a list on this tab") >= 0 && initMsg.indexOf("field above") >= 0 && initMsg.indexOf("⚙") < 0 && !viewerCard.node.querySelector("iframe"),
      "Filing Viewer waits for selection with field-above hint and loads no default Apple Form 4");
    var tmxIt = { uid: "sec-4", widgetId: "tmx_equity_filings_viewer_obb", backend: "v5", params: {}, groups: ["Symbol"] };
    var tmxDef = { widgetId: "tmx_equity_filings_viewer_obb", name: "TMX Company Filings", type: "multi_file_viewer", endpoint: "/__test_tmx_view",
      params: [{ paramName: "url", type: "endpoint", show: false, multiSelect: true, roles: ["fileSelector"], optionsParams: { symbol: "$symbol" } }, { paramName: "symbol", type: "text", value: "AC" }], source: ["TMX"] };
    var tmxCard = T.widget(wCtx(tmxIt, tmxDef));
    var tmxUpdates = [];
    var origTmxSet = tmxCard.setParamValue;
    tmxCard.setParamValue = function (p, v) { tmxUpdates.push([p, v]); return origTmxSet(p, v); };
    ok(!nasdaqCard.hasParam("document_url") && viewerCard.hasParam("document_url") && headersCard.hasParam("url") && tmxCard.hasParam("url"), "hasParam excludes fileSelector except TMX viewer url");

    var nasdaqUpdates = [];
    var origNasdaqSet = nasdaqCard.setParamValue;
    nasdaqCard.setParamValue = function (p, v) { nasdaqUpdates.push([p, v]); return origNasdaqSet(p, v); };
    T.S.cards = [filingsCard, headersCard, viewerCard, nasdaqCard, tmxCard];

    var filingUrl = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-10k.htm";
    ok(T.broadcast(filingsIt, "url", filingUrl), "broadcasting filing url succeeds");
    ok(headersCard.node.querySelector(".w-params input").value === filingUrl, "Filing Headers receives url in Filing URL param");
    var ifr1 = viewerCard.node.querySelector("iframe");
    ok(ifr1 && ifr1.getAttribute("src").indexOf("document_url=" + encodeURIComponent(filingUrl)) >= 0, "Filing Viewer iframe loads clicked filing via document_url");
    ok(nasdaqUpdates.length === 0 && tmxUpdates.length === 0, "SEC filing url does not touch Nasdaq or TMX viewers");

    var sedarUrl = "https://app.quotemedia.com/quotetools/getFilingPDF.go?webmasterId=101020&fid=12345";
    ok(T.broadcast({ uid: "tmx-fil", widgetId: "equity_fundamental_filings_tmx_obb", backend: "v5", params: {}, groups: ["Symbol"] }, "url", sedarUrl), "broadcasting TMX SEDAR url succeeds");
    ok(tmxUpdates.length === 1 && tmxUpdates[0][0] === "url" && tmxUpdates[0][1] === sedarUrl, "TMX SEDAR viewer receives clicked SEDAR PDF url");
    ok(headersCard.node.querySelector(".w-params input").value === filingUrl && viewerCard.node.querySelector("iframe").getAttribute("src").indexOf(encodeURIComponent(filingUrl)) >= 0,
      "TMX SEDAR url broadcast does not corrupt SEC Filing Headers or SEC Filing Viewer");

    var exhibitUrl = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/ex21.htm";
    ok(T.broadcast(headersIt, "document_url", exhibitUrl), "broadcasting Filing Headers document_url succeeds");
    ok(headersCard.node.querySelector(".w-params input").value === filingUrl, "Filing Headers is not overwritten by its own broadcast");
    var ifr2 = viewerCard.node.querySelector("iframe");
    ok(ifr2 && ifr2.getAttribute("src").indexOf("document_url=" + encodeURIComponent(exhibitUrl)) >= 0, "Filing Viewer iframe updates to clicked exhibit document_url");
    ok(nasdaqUpdates.length === 0, "Nasdaq SEC Filings fileSelector is untouched by document_url broadcast");

    viewerCard.node.classList.remove("max");
    secStage.style.display = "none";   // simulate off-screen cards when ticker switches
    T.setGroup(symG, "NVDA");
    ok(!viewerCard.node.querySelector("iframe") && /Pick a filing/.test(viewerCard.node.querySelector(".msg").textContent),
      "switching ticker clears off-screen Filing Viewer iframe back to prompt");
    ok(headersCard.node.querySelector(".w-params input").value === "" && /Pick a filing/.test(headersCard.node.querySelector(".msg").textContent),
      "switching ticker clears live selection and prompt on non-grouped Filing Headers");
    ok(nasdaqIt.params.document_url === undefined, "switching ticker clears dependent fileSelector param on Nasdaq SEC Filings");
    ok(!T.S.cur.tabs[0].edited, "viewing filings and switching tickers does not mark tab edited");

    // Manual source pick to out-of-market provider (e.g., picking TMX on dual-listed ENB) must stick, attach Symbol, and survive tab open;
    // changing ticker across markets (RY.TO -> AAPL) auto-routes back to SEC.
    var keepW = T.S.widgets.v5, keepC = T.S.concepts, keepGrid = T.S.grid, keepEp = T.S.epIndex;
    try {
      T.S.widgets.v5 = {
        sec_company_filings_sec_obb: filingsDef,
        equity_fundamental_filings_tmx_obb: { widgetId: "equity_fundamental_filings_tmx_obb", name: "TMX Filings", type: "table", endpoint: "/__test_tmx_fil", params: [{ paramName: "symbol", type: "text" }, { paramName: "provider", value: "tmx" }], source: ["TMX"] },
        sec_filing_viewer_sec_obb: viewerDef,
        tmx_equity_filings_viewer_obb: tmxDef
      };
      T.S.concepts = {
        "company.filings": { label: "Company filings", members: [
          { b: "v5", w: "sec_company_filings_sec_obb", l: "SEC (EDGAR)", mk: ["us"] },
          { b: "v5", w: "equity_fundamental_filings_tmx_obb", l: "TMX (SEDAR+ Canada)", mk: ["ca"], sym: "tmx" }
        ] },
        "company.filing_viewer": { label: "Filing viewer", members: [
          { b: "v5", w: "sec_filing_viewer_sec_obb", l: "SEC (EDGAR)", mk: ["us"] },
          { b: "v5", w: "tmx_equity_filings_viewer_obb", l: "TMX (SEDAR+)", mk: ["ca"], sym: "tmx", alt: true }
        ] }
      };
      T.S.epIndex = null;
      T.S.grid = { replace: function () {} };
      symG.value = "ENB";
      var manFil = { uid: "m-fil", widgetId: "sec_company_filings_sec_obb", backend: "v5", params: {}, groups: ["Symbol"] };
      var manView = { uid: "m-view", widgetId: "sec_filing_viewer_sec_obb", backend: "v5", params: {}, groups: [] };
      T.S.cur = { id: "d:test-sec", name: "Test SEC", groups: [symG], tabs: [{ id: "t", name: "Filings", items: [manFil, manView] }] };
      T.S.cards = [
        { item: manFil, def: filingsDef, destroy: function () {} },
        { item: manView, def: viewerDef, destroy: function () {} }
      ];
      T.pickSource(manFil, { value: "v5:equity_fundamental_filings_tmx_obb", label: "TMX (SEDAR+ Canada)" });
      ok(manFil.widgetId === "equity_fundamental_filings_tmx_obb" && !T.routeItem(manFil), "picking TMX on ENB does not snap back to SEC and survives tab reopen");
      T.pickSource(manView, { value: "v5:tmx_equity_filings_viewer_obb", label: "TMX (SEDAR+)" });
      ok(manView.widgetId === "tmx_equity_filings_viewer_obb" && manView.groups.indexOf("Symbol") >= 0 && !T.routeItem(manView), "picking TMX viewer on ENB attaches Symbol group and persists");
      T.setGroup(symG, "RY.TO");
      ok(manFil.widgetId === "equity_fundamental_filings_tmx_obb" && manView.widgetId === "tmx_equity_filings_viewer_obb", "switching ENB -> RY.TO keeps TMX selection");
      T.setGroup(symG, "AAPL");
      ok(manFil.widgetId === "sec_company_filings_sec_obb" && manView.widgetId === "sec_filing_viewer_sec_obb", "switching RY.TO -> AAPL clears stale TMX pick and routes back to SEC");
    } finally {
      T.S.widgets.v5 = keepW; T.S.concepts = keepC; T.S.grid = keepGrid; T.S.epIndex = keepEp;
    }
  } finally {
    secStage.remove();
    if (grpBox) { grpBox.textContent = ""; keepGrpNodes.forEach(function (n) { grpBox.appendChild(n); }); }
    T.S.cur = keepCur; T.S.tabId = keepTab; T.S.cards = keepCards; T.S.state = keepSt; T.S.stateBroken = keepBroken;
  }

  // ---- P4: readable month sort, bounded per-card timeouts, and 3-tier board widths at 1440 px
  ok(T.monthKey("Nov 2026") === 202611 && T.monthKey("Oct 2026") < T.monthKey("Nov 2026"), "readable recommendation months sort chronologically");
  ok(T.requestTimeout("/x/board?preset=us_equity", false) === 30000
    && T.requestTimeout("/api/v1/yfinance/estimates/recommendations?symbol=AAPL", false) === 30000
    && T.requestTimeout("/x/chart?symbol=AAPL", false) === 60000
    && T.requestTimeout("/api/v1/cboe/options/surface?symbol=SPX", false) === 70000
    && T.requestTimeout("/api/v1/derivatives/options/chains?provider=cboe&symbol=SPX", false) === 70000
    && T.requestTimeout("/x/policy_path?currency=USD", false) === 70000
    && T.requestTimeout("/x/global_yields?countries=g10", false) === 70000
    && T.requestTimeout("/api/v1/oecd/gdp?country=US", false) === 70000
    && T.requestTimeout("/api/v1/sec/filing_headers", true) === 70000
    && T.requestTimeout("/api/v1/custom/endpoint", false) === 45000,
    "bounded per-card timeouts by route/provider");
  ok(T.boardTier(1200) === 0 && T.boardTier(700) === 1 && T.boardTier(520) === 2 && T.boardTier(400) === 3 && T.boardTier(0) === 0,
    "board width tiers step cleanly");

  var brdFull = [{ name: "N1", quote: "Q1", price: 10, chg: 1, chg_pct: 0.1, spark: [1, 2, 3], w1: 0.02, m1: 0.05, m3: 0.1, ytd: 0.2, y1: 0.3, range52: 50, symbol: "S1" }];
  var bDefs = [
    { field: "name" }, { field: "quote" }, { field: "price" }, { field: "chg" }, { field: "chg_pct" },
    { field: "spark", renderFn: "sparkline" }, { field: "w1" }, { field: "m1" }, { field: "m3" }, { field: "ytd" }, { field: "y1" },
    { field: "range52", renderFn: "range" }, { field: "symbol" }
  ];
  var tFull = T.table(document.createElement("div"), { data: brdFull, colDefs: bDefs, curated: true, narrow: 0 });
  ok(tFull.shownNow().length === 13, "tier 0 keeps all 13 board columns");
  tFull.setNarrow(1);
  ok(tFull.shownNow().join(",") === "name,price,chg_pct,spark,w1,m1,ytd,y1,range52", "tier 1 drops quote/chg/m3/symbol");
  tFull.setNarrow(2);
  ok(tFull.shownNow().join(",") === "name,price,chg_pct,spark,ytd,y1", "tier 2 drops w1/m1/range52 too");
  tFull.setNarrow(3);
  ok(tFull.shownNow().join(",") === "name,price,chg_pct,w1,ytd", "tier 3 fits tight 1440 px slot with 5 columns");

  var brdYield = [{ name: "US 10Y", price: 4.2, chg: 5, chg_pct: 0.01, spark: [4.1, 4.2], w1: 2, m1: -3, m3: 10, ytd: 25, y1: 30, range52: 60, symbol: "TNX", _fmt: { chg: { suffix: " bp" } } }];
  var tYield = T.table(document.createElement("div"), { data: brdYield, colDefs: bDefs, curated: true, narrow: 3 });
  ok(tYield.shownNow().join(",") === "name,price,chg,chg_pct,ytd", "tier 3 yield board keeps bp chg");
  var tColsOverride = T.table(document.createElement("div"), { data: brdFull, colDefs: bDefs, curated: true, narrow: 3, cols: ["name", "price", "m3"] });
  ok(tColsOverride.shownNow().join(",") === "name,price,m3", "explicit cols override ignores width tier");
  var tQuote = T.table(document.createElement("div"), { data: [{ symbol: "AAPL", name: "Apple", price: 1, currency: "USD", ytd: 2 }, { symbol: "SHEL.L", name: "Shell", price: 2, currency: "GBp", ytd: 3 }],
    colDefs: [{ field: "symbol" }, { field: "name" }, { field: "price" }, { field: "currency" }, { field: "ytd" }], narrow: 3 });
  ok(tQuote.shownNow().indexOf("currency") >= 0, "tier 3 leaves a non-board table's currency column");

  // ---- UX fixes: TradingView FRED guard, FX/Auction newest-first sort, Nordics Market -> Company click-through
  var tvFred = T.frameUnsupported({ endpoint: "/api/v1/yfinance/tv_widget/view" }, { symbol: "FRED:DGS10" });
  ok(tvFred && /market tickers only/i.test(tvFred.text) && /FRED:DGS10/.test(tvFred.hint), "TradingView FRED id shows clear notice");
  ok(T.frameUnsupported({ endpoint: "/api/v1/yfinance/tv_widget/view" }, { symbol: "AAPL" }) === null, "TradingView AAPL allowed");
  ok(T.frameUnsupported({ endpoint: "/api/v1/sec/filing_viewer_app" }, { symbol: "FRED:DGS10" }) === null, "other frames untouched");

  var fxRows = [
    { execution_timestamp: "2026-08-25T10:00:00Z", pair: "EURUSD", expiration_date: "2099-09-01", strike: 1.1 },
    { execution_timestamp: "2026-11-20T12:00:00Z", pair: "EURUSD", expiration_date: "2099-12-01", strike: 1.12 }
  ];
  var fxTv = T.table(document.createElement("div"), { data: fxRows, colDefs: [{ field: "execution_timestamp", cellDataType: "date" }, { field: "pair" }, { field: "expiration_date", cellDataType: "date" }, { field: "strike" }] });
  ok(fxTv.toCSV(",").split("\r\n")[1].indexOf("2026-11-20") === 0, "trade log leading with execution_timestamp sorts newest first despite future expiration_date");
  var crvRows = [{ expiration_date: "2099-12-01", strike: 100 }, { expiration_date: "2099-09-01", strike: 95 }];
  var crvTv = T.table(document.createElement("div"), { data: crvRows, colDefs: [{ field: "expiration_date", cellDataType: "date" }, { field: "strike" }] });
  ok(crvTv.toCSV(",").split("\r\n")[1].indexOf("2099-09-01") === 0, "curve leading with expiration_date still sorts soonest first");

  var keepCur2 = T.S.cur, keepTab2 = T.S.tabId, keepCards2 = T.S.cards, keepSt2 = T.S.state, keepOpenSec = T.openSecurity, keepG2 = T.S.generated;
  var grpBox2 = document.getElementById("groups"), keepGrp2 = grpBox2 ? [].slice.call(grpBox2.childNodes) : [];
  try {
    var nSymG = { name: "Symbol", paramName: "symbol", type: "symbol", value: "VOLV B" };
    var nTabM = { id: "market", name: "Market", items: [{ uid: "m-0", widgetId: "x_board", backend: "x", params: {}, groups: [] }] };
    var nTabC = { id: "company", name: "Company", items: [{ uid: "c-0", widgetId: "nasdaq_nordic_info_nasdaq_obb", backend: "v5", params: {}, groups: ["Symbol"] }] };
    var nDash = { id: "d:eq-nordic", name: "Nordics", groups: [nSymG], tabs: [nTabM, nTabC] };
    T.S.generated = [nDash];
    T.S.state = { v: 2, dashboards: {}, groups: {} };
    T.S.cur = nDash; T.S.tabId = "market"; T.S.cards = [];
    var secOpened = [];
    T.openSecurity = function (s) { secOpened.push(s); };
    T.broadcast(null, "symbol", "NOVO-B.CO");
    ok(nSymG.value === "NOVO B" && T.S.tabId === "company", "Nordics Market click on NOVO-B.CO switches to Company tab with NOVO B");
    T.S.tabId = "market";
    T.broadcast(null, "symbol", "MAERSK A");
    ok(nSymG.value === "MAERSK A" && T.S.tabId === "company", "Nordics Market click on MAERSK A switches to Company tab");
    T.S.tabId = "market";
    T.broadcast(null, "symbol", "^OMX");
    ok(secOpened[0] === "^OMX" && T.S.tabId === "market", "Nordics Market click on index ^OMX stays on openSecurity");
  } finally {
    T.S.cur = keepCur2; T.S.tabId = keepTab2; T.S.cards = keepCards2; T.S.state = keepSt2; T.openSecurity = keepOpenSec; T.S.generated = keepG2;
    if (grpBox2) { grpBox2.textContent = ""; keepGrp2.forEach(function (n) { grpBox2.appendChild(n); }); }
  }

  // ---- W11: request limiting, grid compaction, formatting & CSV edge cases
  (function () {
    var rs = T._reqSlots, host = "http://127.0.0.1:59999";
    delete rs.slots[host];
    for (var ri = 0; ri < rs.max; ri++) rs.acquire(host);
    ok(rs.slots[host].n === 5 && rs.slots[host].q.length === 0, "request limiter fills up to MAX_PARALLEL (5)");
    var ac = new AbortController();
    var pQueued = rs.acquire(host), pAborted = rs.acquire(host, ac.signal);
    pAborted.catch(function () {});
    ok(rs.slots[host].q.length === 2, "6th and 7th requests queue");
    ac.abort();
    ok(rs.slots[host].q.length === 1 && rs.slots[host].n === 5, "aborting a queued request drops it without leaking a slot");
    rs.release(host);
    ok(rs.slots[host].n === 5 && rs.slots[host].q.length === 0, "release hands the slot directly to the queued waiter");
    for (var rj = 0; rj < rs.max; rj++) rs.release(host);
    ok(rs.slots[host].n === 0, "all slots released back to 0");
    delete rs.slots[host];
    ok(rs.isRateLimited("/api/v1/oecd/gdp?country=US") && !rs.isRateLimited("/api/v1/economy/gdp/nominal?provider=fred"), "OECD is rate-limited, FRED is not");
    ok(rs.isRateLimited("/api/v1/economy/calendar?provider=nasdaq") && rs.isRateLimited("/api/v1/nasdaq/markets/movers")
      && rs.rateLimitKey("/api/v1/oecd/gdp?country=US") === "oecd"
      && rs.rateLimitKey("/api/v1/economy/calendar?provider=nasdaq") === "nasdaq"
      && rs.rateLimitKey("/api/v1/nasdaq/markets/movers") === "nasdaq"
      && rs.rateLimitKey("/api/v1/economy/gdp/nominal?provider=fred") === "",
      "isRateLimited matches Nasdaq and rateLimitKey separates oecd vs nasdaq");
    var foNorm = rs.fetchOptions(false, ac.signal), foForce = rs.fetchOptions(true, ac.signal);
    ok(rs.keepFor === undefined && !foNorm.headers && foForce.headers && foForce.headers["X-Terminal-Refresh"] === "1",
      "browser Cache API retired (no keepFor); forced refresh sends X-Terminal-Refresh: 1 while normal fetches send no extra header");

    var gItems = [
      { uid: "g0", x: 0, y: 10, w: 20, h: 4 },
      { uid: "g1", x: 0, y: 10, w: 20, h: 4 },
      { uid: "g2", x: 35, y: 2, w: 20, h: 5 }
    ];
    T.grid.compact(gItems, gItems[1]);
    ok(gItems[1].y === 0 && gItems[0].y === 4 && gItems[2].x === 20 && gItems[2].y === 0, "grid.compact packs gaps, lets first win ties, clamps x to 40 cols");

    ok(T.fmtNum(null) === "null" && T.fmtNum(NaN) === "NaN" && T.fmtNum(Infinity) === "Infinity", "fmtNum handles non-finite inputs");
    ok(T.fmtNum(1.5e12, null, 1e9) === "1.50T" && T.fmtNum(2.4e9, null, 1e9) === "2.40B" && T.fmtNum(3.5e6, null, 1e6) === "3.50M", "fmtNum abbreviates T/B/M");
    ok(T.fmtNum(-120, { decimalPlaces: 0, paren: true, prefix: "$", suffix: "x" }) === "(120)", "fmtNum accounting parens");
    ok(T.csvCell("+CMD()", ",") === "'+CMD()" && T.csvCell("-cmd", ",") === "'-cmd" && T.csvCell("\t1+1", ",") === "'\t1+1", "csvCell neutralizes + / - / tab formulas");
    ok(T.csvCell('a, "b"', ",") === '"a, ""b"""' && T.csvCell({ value: 42, footnote: "x" }, ",") === "42", "csvCell quotes commas/quotes and unwraps footnote cell");
  })();

  // ---- W6: per-card state overrides, diff/merge round-trip & v3 -> v4 migration
  (function () {
    var keepS = T.S.state, keepG = T.S.generated, keepCur3 = T.S.cur, keepTab3 = T.S.tabId, keepBroken3 = T.S.stateBroken, keepCards3 = T.S.cards;
    try {
      T.S.stateBroken = true;
      var tplW = {
        id: "d:w6test", name: "W6 Test",
        groups: [{ name: "Symbol", paramName: "symbol", value: "AAPL" }],
        renamed: { "old-tab": "main" },
        tabs: [
          { id: "main", name: "Main", items: [
            { uid: "main-0", widgetId: "x_compare", backend: "x", x: 0, y: 0, w: 20, h: 10, params: { group: "hyperscalers", fact: "capex", period: "ttm" } },
            { uid: "main-1", widgetId: "x_compare", backend: "x", x: 20, y: 0, w: 20, h: 10, params: { group: "hyperscalers", fact: "debt" } },
            { uid: "main-2", widgetId: "derivatives_futures_curve_yfinance_obb", backend: "v5", x: 0, y: 12, w: 20, h: 12, params: { symbol: "CL" }, title: "WTI" },
            { uid: "main-3", widgetId: "derivatives_futures_curve_yfinance_obb", backend: "v5", x: 20, y: 12, w: 20, h: 12, params: { symbol: "GC" }, title: "Gold" }
          ] },
          { id: "sec", name: "Second", items: [
            { uid: "sec-0", widgetId: "w_sec", backend: "v5", x: 0, y: 5, w: 40, h: 10, params: { a: 1 } }
          ] }
        ]
      };
      ok(T.cardSig(tplW.tabs[0].items[0]) !== T.cardSig(tplW.tabs[0].items[1]), "cardSig distinguishes x_compare cards by group/fact");
      ok(T.cardSig(tplW.tabs[0].items[2]) !== T.cardSig(tplW.tabs[0].items[3]), "cardSig distinguishes futures curves by symbol");

      // Unopened/uncompacted tab produces zero overrides
      var curUnopened = T.mergeDashboard(tplW, null);
      ok(T.diffDashboard(tplW, curUnopened) === null, "unmodified dashboard (even with y=5 gap on sec-0) diffs to null");

      // Resize main-0 pushing down main-2: only main-0 gets h override (no spurious y override on main-2)
      var curResized = T.mergeDashboard(tplW, null);
      curResized.tabs[0].items[0].h = 16;
      curResized.tabs[0].items[2].y = 16;
      var ovResize = T.diffDashboard(tplW, curResized);
      ok(ovResize.overrides.main.cards["main-0"].h === 16 && !ovResize.overrides.main.cards["main-2"], "resizing main-0 does not create spurious y override on pushed-down main-2");

      // Reorder main-2 above main-0 round-trips cleanly
      var curSwap = T.mergeDashboard(tplW, null);
      curSwap.tabs[0].items[2].y = 0; curSwap.tabs[0].items[0].y = 12;
      var ovSwap = T.diffDashboard(tplW, curSwap);
      var reSwap = T.mergeDashboard(tplW, ovSwap);
      ok(reSwap.tabs[0].items[2].y === 0 && reSwap.tabs[0].items[0].y === 12, "vertical reorder round-trips via diff/merge");

      // Param delta + newly shipped param flow-through + explicit null clear + revert
      var curParam = T.mergeDashboard(tplW, null);
      curParam.tabs[0].items[0].params.period = "annual";
      curParam.tabs[0].items[0].params.group = null;
      curParam.tabs[0].items[0].title = "Custom Title";
      curParam.tabs[0].items[1].chart = { x: "date", ys: ["v"], type: "line" };
      curParam.tabs[0].items[2].chart = { x: "exp", ys: ["p"], type: "line" };
      curParam.tabs[0].items[2]._guessedChart = true;
      var ovParam = T.diffDashboard(tplW, curParam);
      ok(!ovParam.overrides.main.cards["main-2"], "auto-guessed table plot (_guessedChart) is not saved as layout edit");
      ok(ovParam.overrides.main.cards["main-0"].params.period === "annual" && ovParam.overrides.main.cards["main-0"].params.group === null && !("fact" in ovParam.overrides.main.cards["main-0"].params), "diffParams stores only changed/cleared keys");
      var tplWNew = JSON.parse(JSON.stringify(tplW));
      tplWNew.tabs[0].items[0].params.currency = "USD";
      var mergedNew = T.mergeDashboard(tplWNew, ovParam);
      ok(mergedNew.tabs[0].items[0].params.period === "annual" && mergedNew.tabs[0].items[0].params.group === null && mergedNew.tabs[0].items[0].params.currency === "USD" && mergedNew.tabs[0].items[0].title === "Custom Title" && mergedNew.tabs[0].items[1].chart.type === "line", "newly shipped params flow through alongside user param/title/chart overrides");

      // Shifted uids: inserting a new card at index 0 preserves overrides & hidden by sig without applying stale x/y
      var tplShift = JSON.parse(JSON.stringify(tplW));
      tplShift.tabs[0].items.unshift({ uid: "main-0", widgetId: "new_intro", backend: "x", x: 0, y: 0, w: 40, h: 6, params: {} });
      tplShift.tabs[0].items[1].uid = "main-1"; tplShift.tabs[0].items[2].uid = "main-2"; tplShift.tabs[0].items[3].uid = "main-3"; tplShift.tabs[0].items[4].uid = "main-4";
      var mergedShift = T.mergeDashboard(tplShift, ovParam);
      ok(mergedShift.tabs[0].items[0].widgetId === "new_intro" && !mergedShift.tabs[0].items[0].title && mergedShift.tabs[0].items[1].params.period === "annual" && mergedShift.tabs[0].items[1].title === "Custom Title", "override follows card by sig when template shifts uids, leaving new card untouched");

      // Tab rename, user tab deletion (not hiddenTabs), built-in tab deletion (hiddenTabs), added card, and libsel/srcsel non-leak
      var curTabs = T.mergeDashboard(tplW, null);
      curTabs.tabs[0].name = "Renamed Main";
      curTabs.tabs[0].items.push({ uid: "wuser123", widgetId: "custom_w", backend: "x", x: 0, y: 24, w: 20, h: 10, params: {} });
      curTabs.tabs[0].items[0]._orig = { widgetId: "x_compare", backend: "x", params: { group: "hyperscalers", fact: "capex", period: "ttm" } };
      curTabs.tabs[0].items[0].widgetId = "alt_compare";
      curTabs.tabs[0].items[1]._origLib = { widgetId: "x_compare", backend: "x", params: { group: "hyperscalers", fact: "debt" }, chart: undefined };
      curTabs.tabs[0].items[1]._choiceBase = { params: { ds: "a" } };
      curTabs.tabs[0].items[1].widgetId = "lib_choice_a"; curTabs.tabs[0].items[1].params = { ds: "a" };
      curTabs.tabs.push({ id: "u-custom1", name: "Mine", items: [], edited: true });
      curTabs.tabs = curTabs.tabs.filter(function (t) { return t.id !== "sec"; });
      var ovTabs = T.diffDashboard(tplW, curTabs);
      ok(ovTabs.overrides.main.name === "Renamed Main" && ovTabs.overrides.main.added.length === 1 && !ovTabs.overrides.main.cards, "srcsel/libsel wrappers do not leak into diffDashboard; added card and tab rename kept");
      ok(ovTabs.hiddenTabs.join() === "sec" && ovTabs.tabs.length === 1 && ovTabs.tabs[0].id === "u-custom1", "hidden built-in tab and user tab preserved");
      var reTabs = T.mergeDashboard(tplW, ovTabs);
      ok(reTabs.tabs.length === 2 && reTabs.tabs[0].name === "Renamed Main" && reTabs.tabs[0].items.length === 5 && reTabs.tabs[1].id === "u-custom1", "merged dashboard reflects renamed tab, added card, user tab and hidden built-in tab");
      reTabs.tabs = reTabs.tabs.filter(function (t) { return t.id !== "u-custom1"; });
      var ovNoUserTab = T.diffDashboard(tplW, reTabs);
      ok(!ovNoUserTab.tabs && ovNoUserTab.hiddenTabs.join() === "sec", "deleting a user tab drops it without adding to hiddenTabs");
      curParam.tabs[0].items[0].params.period = "ttm"; curParam.tabs[0].items[0].params.group = "hyperscalers";
      delete curParam.tabs[0].items[0].title; curParam.tabs[0].items[0].title = undefined;
      delete curParam.tabs[0].items[1].chart;
      ok(T.diffDashboard(tplW, curParam) === null, "reverting all edits returns null override record");
      var orphanMerge = T.mergeDashboard(tplW, { id: "d:w6test", mode: "overrides", overrides: { "removed-tab": { name: "Old", added: [{ uid: "wabc1234", widgetId: "x", backend: "x" }] } } });
      ok(orphanMerge.tabs.some(function (t) { return /from an older layout/.test(t.name) && t.items.length === 1; }), "added cards on a removed built-in tab survive as (from an older layout)");

      // touch() + setGroup() never overwrite override record with live S.cur
      T.S.generated = [tplW];
      T.S.state = { v: 4, dashboards: {}, groups: {}, ui: { collapsed: {} } };
      T.S.cur = T.mergeDashboard(tplW, null);
      T.S.tabId = "main";
      T.S.cards = [];
      T.S.cur.tabs[0].items[0].params.period = "annual";
      T.touch();
      ok(T.S.state.dashboards["d:w6test"].mode === "overrides" && T.S.state.dashboards["d:w6test"].overrides.main.cards["main-0"].params.period === "annual", "touch() saves compact override record for d:*");
      T.setGroup(T.S.cur.groups[0], "MSFT");
      ok(T.S.state.dashboards["d:w6test"].mode === "overrides" && T.S.state.groups["d:w6test"].Symbol === "MSFT", "setGroup() keeps override record intact instead of overwriting with S.cur");
      var ser = JSON.parse(T.serializeState());
      ok(ser.dashboards["d:w6test"].mode === "overrides" && !ser.dashboards["d:w6test"].groups, "serializeState emits compact v4 override record");

      // v3 -> v4 migration: restructured bond-lookup-trace + policy-expectations + viewPrefs + pref uid remap + app:* cleanup + idempotency
      var tplCredit = {
        id: "d:credit", name: "Credit", groups: [],
        tabs: [{ id: "bond-lookup-trace", name: "Bond Lookup (TRACE)", items: [
          { uid: "bond-lookup-trace-0", widgetId: "x_bond_lookup", backend: "x", x: 0, y: 0, w: 24, h: 18, params: {} },
          { uid: "bond-lookup-trace-1", widgetId: "x_bond_curve", backend: "x", x: 24, y: 0, w: 16, h: 18, params: {} },
          { uid: "bond-lookup-trace-2", widgetId: "finra_fixedincome_bond_historical_finra_obb", backend: "v5", x: 0, y: 18, w: 20, h: 16, params: {}, view: "chart", chart: { x: "date", ys: ["price"], type: "line" } },
          { uid: "bond-lookup-trace-3", widgetId: "fixedincome_corporate_bond_prices_finra_obb", backend: "v5", x: 20, y: 18, w: 20, h: 16, params: { issuer_name: "APPLE INC", limit: 1000 } }
        ] }]
      };
      var tplRates = {
        id: "d:rates", name: "Rates", groups: [],
        tabs: [{ id: "policy-expectations", name: "Policy Expectations", items: [
          { uid: "policy-expectations-0", widgetId: "x_policy_path", backend: "x", x: 0, y: 0, w: 20, h: 16, params: {} },
          { uid: "policy-expectations-1", widgetId: "economy_fomc_documents_federal_reserve_obb", backend: "v5", x: 20, y: 0, w: 20, h: 16, params: {} },
          { uid: "policy-expectations-2", widgetId: "sf_term_premium_federal_reserve_obb", backend: "v5", x: 0, y: 16, w: 20, h: 16, params: { start_date: "2015-01-01" }, view: "chart" }
        ] }]
      };
      T.S.generated = [tplCredit, tplRates];
      T.S.state = {
        v: 3,
        dashboards: {
          "d:credit": { id: "d:credit", edited: true, groups: [{ name: "G", value: "V1" }], tabs: [{ id: "bond-lookup-trace", name: "Bond Lookup (TRACE)", edited: true, items: [
            { uid: "bond-lookup-trace-0", widgetId: "fixedincome_corporate_bond_prices_finra_obb", backend: "v5", x: 0, y: 0, w: 40, h: 18, params: { issuer_name: "Take Two Interactive", limit: 1000 }, title: "Old Title" },
            { uid: "bond-lookup-trace-1", widgetId: "finra_fixedincome_bond_historical_finra_obb", backend: "v5", x: 0, y: 18, w: 40, h: 16, params: {}, flip: true }
          ] }] },
          "d:rates": { id: "d:rates", edited: true, tabs: [{ id: "policy-expectations", name: "Policy Expectations", edited: true, items: [
            { uid: "policy-expectations-0", widgetId: "x_policy_path", backend: "x", x: 0, y: 0, w: 20, h: 16, params: { currency: ["USD"] } },
            { uid: "policy-expectations-1", widgetId: "sf_term_premium_federal_reserve_obb", backend: "v5", x: 0, y: 16, w: 20, h: 16, params: { start_date: "2015-01-01" } },
            { uid: "policy-expectations-1n", widgetId: "economy_fomc_documents_federal_reserve_obb", backend: "v5", x: 0, y: 32, w: 40, h: 20, params: {} }
          ] }] },
          "app:v5:TMX": { id: "app:v5:TMX", source: "app", groups: [{ name: "Symbol", value: "RY" }], tabs: [{ id: "overview", items: [] }] },
          "user:mine": { id: "user:mine", source: "user", tabs: [{ id: "main", items: [{ uid: "u1" }] }] }
        },
        cardview: { "d:credit": { "bond-lookup-trace-0": { compact: true } } },
        colsel: { "d:credit": { "bond-lookup-trace-1": ["date", "price"] } }
      };
      var m1 = T.migrateOverrides();
      ok(T.S.state.v === 4 && m1.migrated.length === 2 && !T.S.state.dashboards["app:v5:TMX"] && T.S.state.groups["app:v5:TMX"].Symbol === "RY" && T.S.state.dashboards["user:mine"],
        "v3->v4 migration converts built-in dashboards, drops untouched app:* keeping groups, preserves user:*");
      var crM = T.mergeDashboard(tplCredit, T.S.state.dashboards["d:credit"]);
      ok(crM.tabs[0].items.length === 4 && crM.tabs[0].items[0].widgetId === "x_bond_lookup" && crM.tabs[0].items[2].view === "chart" && crM.tabs[0].items[2].chart.ys[0] === "price" && crM.tabs[0].items[3].params.issuer_name === "Take Two Interactive" && crM.tabs[0].items[3].title !== "Old Title",
        "migrated d:credit inherits new bond lookup/curve + chart view while keeping issuer_name");
      ok(T.S.state.cardview["d:credit"]["bond-lookup-trace-2"].flip === true && T.S.state.cardview["d:credit"]["bond-lookup-trace-2"].view === undefined && T.S.state.colsel["d:credit"]["bond-lookup-trace-2"].join() === "date,price",
        "migrated viewPrefs/colsel remap to shifted uid without suppressing newly shipped view:chart");
      var raM = T.mergeDashboard(tplRates, T.S.state.dashboards["d:rates"]);
      ok(raM.tabs[0].items.length === 3 && raM.tabs[0].items.filter(function (x) { return x.widgetId === "economy_fomc_documents_federal_reserve_obb"; }).length === 1 && raM.tabs[0].items[0].params.currency[0] === "USD",
        "migrated d:rates deduplicates manually added FOMC 1n against shipped card and keeps currency param");
      var m2 = T.migrateOverrides();
      ok(m2.migrated.length === 0 && m2.released.length === 0, "migrateOverrides is idempotent");
      var corruptOk = T.mergeDashboard(tplCredit, { id: "d:credit", mode: "overrides", overrides: { "gone-tab": { cards: {} } } });
      ok(corruptOk.tabs[0].items.length === 4, "partial/unknown override tab merges cleanly without throwing");
    } finally {
      T.S.state = keepS; T.S.generated = keepG; T.S.cur = keepCur3; T.S.tabId = keepTab3; T.S.stateBroken = keepBroken3; T.S.cards = keepCards3;
    }
  })();

  // ---- D2 / D3: derived RSSD group, paintGroups visibility, transitive clearDepParams, iframe .frame-wait lifecycle
  (function () {
    var bankGroups = [
      { name: "Bank", paramName: "symbol", value: "JPM" },
      { name: "RSSD", paramName: "rssd_id", value: "1039502", syncFrom: "Bank", syncUrl: "/x/bank_rssd" }
    ];
    var bhcprIt = { uid: "b1", params: {}, groups: [] };
    T.assignGroups(bhcprIt, { params: [{ paramName: "rssd_id", type: "text" }, { paramName: "section", type: "endpoint" }] }, bankGroups);
    ok(bhcprIt.groups.join() === "RSSD", "assignGroups binds RSSD to rssd_id-only card");
    var callIt = { uid: "b2", params: {}, groups: [] };
    T.assignGroups(callIt, { params: [{ paramName: "symbol", type: "text" }, { paramName: "rssd_id", type: "text" }] }, bankGroups);
    ok(callIt.groups.join() === "Bank", "assignGroups prefers primary Bank group when card accepts symbol");
    var bankTpl = {
      id: "d:banking", name: "Money & Banking", groups: bankGroups,
      tabs: [{ id: "regulators-reports", name: "Regulators & Reports", items: [{ uid: "regulators-reports-3", widgetId: "ffiec_bhcpr_federal_reserve_obb", backend: "v5", params: {}, groups: ["RSSD"] }] }]
    };
    var bankOld = {
      id: "d:banking", edited: true,
      tabs: [{ id: "regulators-reports", name: "Regulators & Reports", edited: true, items: [{ uid: "fed-banks-3", widgetId: "ffiec_bhcpr_federal_reserve_obb", backend: "v5", params: { rssd_id: "1039502" } }] }]
    };
    ok(!T.mergeDashboard(bankTpl, bankOld).edited, "legacy default param now driven by a new group (RSSD=1039502) releases tab cleanly");

    var keepCur4 = T.S.cur, keepTab4 = T.S.tabId;
    var grpBox4 = document.getElementById("groups"), keepGrp4 = grpBox4 ? [].slice.call(grpBox4.childNodes) : [];
    try {
      T.S.cur = { id: "d:banking", name: "Money & Banking", groups: bankGroups, tabs: [{ id: "reg", name: "Reg", items: [bhcprIt] }] };
      T.S.tabId = "reg";
      T.paintGroups();
      var labels = [].map.call(grpBox4.querySelectorAll(".group label"), function (l) { return l.textContent; });
      ok(labels.indexOf("Bank") >= 0 && labels.indexOf("RSSD") >= 0, "paintGroups shows both Bank and derived RSSD when tab uses RSSD");
    } finally {
      T.S.cur = keepCur4; T.S.tabId = keepTab4;
      if (grpBox4) { grpBox4.textContent = ""; keepGrp4.forEach(function (n) { grpBox4.appendChild(n); }); }
    }

    var stage4 = document.createElement("div");
    stage4.style.cssText = "width:600px;height:400px;overflow:auto;";
    document.body.appendChild(stage4);
    try {
      var depIt = { uid: "d-dep", widgetId: "ffiec_fin_rep", backend: "v5", params: { report_type: "FR Y-9C", section: "HI", period: "20251231" }, groups: ["RSSD"] };
      var depDef = {
        widgetId: "ffiec_fin_rep", name: "FFIEC Financial Report", type: "table", endpoint: "/__test_ffiec",
        params: [
          { paramName: "rssd_id", type: "text" },
          { paramName: "report_type", type: "endpoint", optionsEndpoint: "/__rt", optionsParams: { rssd_id: "$rssd_id" } },
          { paramName: "section", type: "endpoint", optionsEndpoint: "/__sec", optionsParams: { rssd_id: "$rssd_id", report_type: "$report_type" } },
          { paramName: "period", type: "endpoint", optionsEndpoint: "/__per", optionsParams: { report_type: "$report_type" } }
        ]
      };
      var rssdG = { name: "RSSD", paramName: "rssd_id", value: "1039502" };
      var depCard = T.widget({
        item: depIt, def: depDef, backend: { id: "v5", name: "Test", url: "" }, scroller: stage4,
        groupOf: function (it, p) { return p === "rssd_id" ? rssdG : null; },
        groupsOf: function () { return [rssdG]; },
        setGroup: function () {}, broadcast: function () {}, siblings: function () { return []; },
        onChange: function () {}, onRemove: function () {}, relayout: function () {}
      });
      depCard.redrawParams("rssd_id");
      ok(depIt.params.report_type === undefined && depIt.params.section === undefined && depIt.params.period === undefined,
        "transitive clearDepParams clears report_type, section and period when rssd_id changes");
      depCard.destroy();

      var frIt = { uid: "fr-1", widgetId: "tmx_equity_asset_info_obb", backend: "v5", params: { symbol: "RY", theme: "dark" }, groups: [] };
      var frDef = { widgetId: "tmx_equity_asset_info_obb", name: "TMX Asset Info", type: "iframe", endpoint: "/__test_tmx_frame", params: [{ paramName: "symbol", type: "text" }], source: ["TMX"] };
      var frCard = T.widget({
        item: frIt, def: frDef, backend: { id: "v5", name: "Test", url: "" }, scroller: stage4,
        groupOf: function () { return null; }, groupsOf: function () { return []; },
        setGroup: function () {}, broadcast: function () {}, siblings: function () { return []; },
        onChange: function () {}, onRemove: function () {}, relayout: function () {}
      });
      frCard.node.classList.add("max");
      stage4.appendChild(frCard.node);
      frCard.check();
      var waitEl = frCard.node.querySelector(".frame-wait"), ifrEl = frCard.node.querySelector("iframe");
      ok(!!waitEl && !!ifrEl && ifrEl.style.visibility === "hidden", "iframe mounts with .frame-wait overlay and hidden visibility");
      ifrEl.dispatchEvent(new Event("load"));
      ok(!frCard.node.querySelector(".frame-wait") && ifrEl.style.visibility === "", "iframe load event removes .frame-wait and reveals iframe");
      frCard.refresh(true);
      var ifrErr = frCard.node.querySelector("iframe");
      ifrErr.dispatchEvent(new Event("error"));
      ok(!frCard.node.querySelector(".frame-wait") && /did not load/i.test((frCard.node.querySelector(".msg.err") || {}).textContent || ""), "iframe error event removes wait overlay and shows error message");
      frCard.destroy();

      // ---- Phase 3A: SWR footer vs failed-fetch stale, E1 honest alternative copy, and ? Help modal accuracy
      var footSwr = document.createElement("div");
      T.renderTrust(footSwr, { results: [1], cached: "Cached 2026-11-21 19:50 (10 min old) · refreshing", delayed: "Delayed prices from Yahoo Finance" });
      ok(footSwr.querySelector(".cache-note") && /Cached .* · refreshing/.test(footSwr.querySelector(".cache-note").textContent)
        && !footSwr.querySelector(".stale-note") && footSwr.textContent.indexOf("Source not answering") < 0 && footSwr.textContent.indexOf("⚠") < 0,
        "SWR cached note renders calmly without warning or 'Source not answering'");
      var footStale = document.createElement("div");
      T.renderTrust(footStale, { results: [1], stale: "Source not answering: showing the copy saved 2026-11-20 12:00 (30 h old)" });
      ok(footStale.querySelector(".stale-note") && /⚠ Source not answering: showing the copy saved/.test(footStale.querySelector(".stale-note").textContent)
        && !footStale.querySelector(".cache-note"),
        "failed-fetch stale note renders warning without .cache-note");

      // a substituted source is named on the card, list answers included (Cboe 403, 2026-11-21)
      var subList = [{ strike: 100 }]; subList.fallback = "Cboe refused its feed. Yahoo chain";
      var footSub = document.createElement("div");
      T.renderTrust(footSub, subList);
      ok(footSub.textContent.indexOf("Yahoo chain") >= 0, "a list answer shows its substitute note");
      var subSrc = T.substituteSources("Cboe, Yahoo Finance", "note");
      ok(T.sourceLabel(subSrc) === "Cboe · Yahoo Finance" && subSrc[1].url === "https://finance.yahoo.com/", "substitute sources are named and linked");
      var subQuote = T.classifyError("Yahoo Finance has no bid/ask quotes for SPX right now, so strategies cannot be priced.", 400, { source: "Cboe" });
      ok(subQuote.cls === "message" && /no bid\/ask/.test(subQuote.text), "missing live quotes is explained, not reported as a bug");

      var e1NoFree = T.classifyError("Missing credential 'eia_api_key'", 400, { source: "U.S. EIA", provider: "eia", hasFreeOnTab: false });
      var e1WithFree = T.classifyError("Missing credential 'eia_api_key'", 400, { source: "U.S. EIA", provider: "eia", hasFreeOnTab: true });
      var e1PaidNoFree = T.classifyError("402 Payment Required", 402, { source: "FMP", provider: "fmp", hasFreeOnTab: false });
      var e1PaidWithFree = T.classifyError("402 Payment Required", 402, { source: "FMP", provider: "fmp", hasFreeOnTab: true });
      ok(e1NoFree.hint.indexOf("Other cards") < 0 && e1PaidNoFree.hint.indexOf("Other cards") < 0,
        "E1: no 'Other cards on this tab' promise when tab has no free card");
      ok(/Other cards on this tab work without one\./.test(e1WithFree.hint) && /Other cards on this tab work without a paid plan\./.test(e1PaidWithFree.hint)
        && e1WithFree.hint.indexOf("cover the same data") < 0 && e1PaidWithFree.hint.indexOf("cover the same data") < 0,
        "E1: honest alternative copy only when tab has free cards, never 'cover the same data'");
      ok(T.classifyError("No options found for SAP.DE", 400, { source: "Cboe", provider: "cboe", symbol: "SAP.DE" }).hint.indexOf("selector at the top") < 0,
        "covered error omits source selector promise when no alternative exists");
      ok(T.classifyError("No answer after 45 seconds. The source is slow or not responding.", 0, { source: "OECD" }).cls === "upstream",
        "client request timeout classifies as upstream, never 'our bug'");

      // needsKey card honors hasFreeOnTab + free-only alternatives even before scrolling into view
      var nkIt = { uid: "nk-1", widgetId: "eia_test", backend: "v5", params: {}, groups: [], needsKey: "eia_api_key" };
      var nkDef = { widgetId: "eia_test", name: "EIA Test", type: "table", endpoint: "/__eia", params: [], source: ["U.S. EIA"] };
      var hasFree = false;
      var nkCard = T.widget({
        item: nkIt, def: nkDef, backend: { id: "v5", name: "Test", url: "" }, scroller: stage4,
        groupOf: function () { return null; }, groupsOf: function () { return []; },
        setGroup: function () {}, broadcast: function () {}, siblings: function () { return []; },
        alternatives: function () { return [{ label: "U.S. EIA", value: "v5:eia_test", current: true }, { label: "Cboe (needs a key)", value: "v5:x", current: false }]; },
        hasFreeOnTab: function () { return hasFree; },
        onChange: function () {}, onRemove: function () {}, relayout: function () {}
      });
      stage4.appendChild(nkCard.node);
      nkCard.check();
      ok(!nkCard.node.querySelector(".spin") && /Needs the free EIA API key/.test(nkCard.node.textContent || ""),
        "needsKey card renders immediately on check() without waiting on spinner when off-screen");
      var nkHint1 = (nkCard.node.querySelector(".msg .hint") || {}).textContent || "";
      ok(nkHint1.indexOf("Other cards") < 0 && nkHint1.indexOf("Switch to a free source") < 0,
        "needsKey card omits tab & source promises when no free card or free alternative exists");
      hasFree = true;
      nkCard.refresh(true);
      var nkHint2 = (nkCard.node.querySelector(".msg .hint") || {}).textContent || "";
      ok(/Other cards on this tab work without one\./.test(nkHint2),
        "needsKey card mentions other cards on tab when a free card exists");
      nkCard.destroy();

      // Help (?) modal reflects current features & V5-only default
      var helpBtn = document.getElementById("btn-help");
      ok(!!helpBtn, "help button present");
      helpBtn.click();
      var helpText = (document.querySelector(".help-box") || {}).textContent || "";
      ok(/CUSIP/.test(helpText) && /FRED:/.test(helpText) && /Bond Lookup/.test(helpText) && /CDS/.test(helpText)
        && /Policy/.test(helpText) && /situation room/.test(helpText) && /Money & Banking/.test(helpText)
        && /S&P 500/.test(helpText) && /↻ Refresh/.test(helpText) && /V5/.test(helpText) && /60 rows/.test(helpText),
        "? Help modal documents Search/CUSIP/FRED:, Bond Lookup, CDS, Policy, situation room, Money & Banking, S&P 500, V5, Refresh, and Flip limit");
      var closeHelp = document.querySelector(".help-box .icon-btn");
      if (closeHelp) closeHelp.click();

      // v0.6.0 UX checks: Stage 3 requestTimeout 70s, / focuses goto, double-click header maximizes, chart footer note helper
      ok(T.requestTimeout("/x/valuation_bands?symbol=TTWO") === 70000
        && T.requestTimeout("/x/roic_dupont?symbol=AMZN") === 70000
        && T.requestTimeout("/x/gex_strike?symbol=SPY") === 70000, "Stage 3 composite routes get 70s requestTimeout");
      ok(T.chartFooterNote({ warning: "Test warning", convention: "5Y window" }) === "⚠ Test warning · 5Y window"
        && T.chartFooterNote({ convention: "5Y window" }) === "5Y window"
        && T.chartFooterNote({ convention: "5Y window", layout: { annotations: [{ text: "5Y window" }] } }) === ""
        && T.chartFooterNote({ warning: "Test warning", convention: "5Y window", layout: { annotations: [{ text: "5Y window" }] } }) === "⚠ Test warning"
        && T.chartFooterNote({}) === "", "chartFooterNote formats warning + convention without duplicating in-chart annotations");

      var gotoEl = document.getElementById("goto");
      if (gotoEl) {
        gotoEl.blur();
        document.dispatchEvent(new KeyboardEvent("keydown", { key: "/", bubbles: true, cancelable: true }));
        ok(document.activeElement === gotoEl, "/ shortcut focuses #goto search bar");
        gotoEl.blur();
      }

      var chartDef = {
        widgetId: "x_test_chart_foot", name: "Test chart", endpoint: "/x/test_chart_foot",
        type: "chart", params: [], source: ["SEC"], raw: true, refetch: 0
      };
      var chartItem = { uid: "tc1", backend: "x", widgetId: "x_test_chart_foot", x: 0, y: 0, w: 6, h: 6, params: {} };
      var relaid = 0;
      var chartCard = T.widget({
        item: chartItem, def: chartDef, backend: { id: "x", url: "http://127.0.0.1:6950" }, scroller: stage4,
        groupOf: function () { return null; }, groupsOf: function () { return []; }, overrides: function () { return {}; },
        setGroup: function () {}, broadcast: function () {}, siblings: function () { return []; }, alternatives: function () { return []; },
        hasFreeOnTab: function () { return true; }, onChange: function () {}, onRemove: function () {}, relayout: function () { relaid++; }
      });
      stage4.appendChild(chartCard.node);
      var hd = chartCard.node.querySelector(".w-head");
      ok(!chartCard.node.classList.contains("max"), "card not maximized initially");
      hd.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      ok(chartCard.node.classList.contains("max") && relaid === 1, "double-clicking .w-head maximizes card");
      hd.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      ok(!chartCard.node.classList.contains("max") && relaid === 2, "double-clicking .w-head restores card");
      var subEl = chartCard.node.querySelector(".w-sub");
      subEl.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
      ok(!chartCard.node.classList.contains("max") && relaid === 2, "double-clicking .w-sub source label does not maximize card");
      chartCard.destroy();
    } finally {
      stage4.remove();
    }
  })();

  // ---- UX pre-warm banner, slow-source retry backoff & non-disruptive backend arrival
  (function () {
    ok(Array.isArray(T.SLOW_RETRY_DELAYS) && T.SLOW_RETRY_DELAYS.length >= 3 && T.SLOW_RETRY_DELAYS[0] >= 10, "slow-source auto-retry schedule exported");
    ok(/15 s/.test(T.slowRetryMessage(15)), "slowRetryMessage formats countdown clearly");

    var keepWarm = T.S.warm;
    var wBan = document.getElementById("warm-banner"), wSt = document.getElementById("warm-status");
    try {
      T.paintWarm({ phase: "landing", done: 2, total: 4, current: "Market news" });
      ok(wBan && !wBan.classList.contains("hidden") && /2\/4/.test(wBan.textContent) && wSt && !wSt.classList.contains("hidden"), "paintWarm shows banner & sidebar status during landing phase");
      T.paintWarm({ phase: "background", bg_done: 1, bg_total: 4 });
      ok(wBan.classList.contains("hidden") && !wSt.classList.contains("hidden") && /1\/4/.test(wSt.textContent), "paintWarm hides banner and shows subtle sidebar pill in background phase");
      T.paintWarm({ phase: "ready" });
      ok(wBan.classList.contains("hidden") && wSt.classList.contains("hidden"), "paintWarm clears indicators when ready");
      T.paintWarm({ phase: "idle" });
      ok(wBan.classList.contains("hidden") && wSt.classList.contains("hidden"), "paintWarm clears indicators when idle");
    } finally {
      T.paintWarm(keepWarm || null);
    }

    var canvasEl = document.getElementById("canvas");
    var keepCurW = T.S.cur, keepTabW = T.S.tabId, keepCardsW = T.S.cards, keepWidgetsW = T.S.widgets,
      keepStateW = T.S.state, keepByIdW = T.S.byId, keepGridW = T.S.grid;
    var keepCanvasChildren = canvasEl ? [].slice.call(canvasEl.childNodes) : [];
    try {
      var missingItem = { uid: "m1", backend: "v5", widgetId: "w_rec", x: 0, y: 0, w: 6, h: 6, manual: "Click to load", params: {} };
      var healthyItem = { uid: "h1", backend: "v5", widgetId: "w_ok", x: 6, y: 0, w: 6, h: 6, params: {} };
      var missingCard = T.missingWidget(missingItem, function () {}, { id: "v5", name: "V5", up: false });
      var healthyNode = document.createElement("div");
      healthyNode.className = "w";
      var healthyRetried = 0;
      var replacedUids = [];
      var healthyCard = {
        node: healthyNode, item: healthyItem, def: null, missing: false, backend: "v5",
        retryIfFailed: function () { healthyRetried++; }, destroy: function () {}
      };
      canvasEl.appendChild(missingCard.node);
      canvasEl.appendChild(healthyNode);
      T.S.cur = { id: "d:test", name: "Test", groups: [], tabs: [{ id: "t1", name: "T1", items: [missingItem, healthyItem] }] };
      T.S.tabId = "t1";
      T.S.state = { v: 4, dashboards: {}, groups: {}, view: {}, libsel: {}, srcsel: {}, colsel: {}, cardview: {}, ui: {} };
      T.S.byId = Object.assign({}, keepByIdW, { v5: { id: "v5", name: "V5", url: "http://127.0.0.1:6900", up: true } });
      T.S.grid = {
        replace: function (it, node) {
          replacedUids.push(it.uid);
          if (missingCard.node.parentNode) missingCard.node.parentNode.replaceChild(node, missingCard.node);
        },
        remove: function () {},
        layout: function () {}
      };
      T.S.cards = [missingCard, healthyCard];
      T.S.widgets = Object.assign({}, keepWidgetsW, {
        v5: { w_rec: { widgetId: "w_rec", name: "Recovered", endpoint: "/api/v1/none", type: "table", params: [] } }
      });
      T.applyBackendArrival({ id: "v5", name: "V5", up: true });
      var replacedMissing = T.S.cards.filter(function (c) { return c.item && c.item.uid === "m1"; })[0];
      ok(replacedUids.join() === "m1" && replacedMissing && !replacedMissing.missing, "applyBackendArrival replaces only previously missing card");
      ok(healthyNode.parentNode === canvasEl && healthyRetried === 1, "applyBackendArrival preserves healthy card DOM without tearing it down");
    } finally {
      (T.S.cards || []).forEach(function (c) { if (c && c.destroy) try { c.destroy(); } catch (e) {} });
      T.S.cur = keepCurW; T.S.tabId = keepTabW; T.S.cards = keepCardsW; T.S.widgets = keepWidgetsW;
      T.S.state = keepStateW; T.S.byId = keepByIdW; T.S.grid = keepGridW;
      if (canvasEl) { canvasEl.textContent = ""; keepCanvasChildren.forEach(function (n) { canvasEl.appendChild(n); }); }
    }
  })();

  // ---- v0.7.0 checks: 3-state text sort, transposed filter, sparkline zero safety, arrival routing & instant _stmtview
  (function () {
    var sortBox = document.createElement("div");
    T.table(sortBox, {
      data: [{ name: "Banana", v: 2 }, { name: "Apple", v: 1 }, { name: "Cherry", v: 3 }],
      colDefs: [{ field: "name", headerName: "Name", cellDataType: "text" }, { field: "v", headerName: "Val", cellDataType: "number" }]
    });
    var th0 = function () { return sortBox.querySelector("th"); };
    var firstCell = function () { return sortBox.querySelector("tbody td").textContent; };
    ok(firstCell() === "Banana" && th0().getAttribute("aria-sort") === "none", "text column starts unsorted");
    th0().click();
    ok(firstCell() === "Apple" && th0().getAttribute("aria-sort") === "ascending", "1st click sorts text ascending");
    th0().click();
    ok(firstCell() === "Cherry" && th0().getAttribute("aria-sort") === "descending", "2nd click sorts text descending");
    th0().click();
    ok(firstCell() === "Banana" && th0().getAttribute("aria-sort") === "none", "3rd click restores original order (3-state text sort)");

    var tfBox = document.createElement("div"), tfCount = "";
    var tfTbl = T.table(tfBox, {
      data: [{ price: 10, eps: 2, margin: 0.2 }],
      colDefs: [
        { field: "price", headerName: "Price", cellDataType: "number" },
        { field: "eps", headerName: "EPS", cellDataType: "number" },
        { field: "margin", headerName: "Operating Margin", cellDataType: "number" }
      ],
      onCount: function (c) { tfCount = c; }
    });
    tfTbl.setTranspose(true);
    tfTbl.setFilter("margin");
    ok(tfBox.querySelectorAll("tbody tr").length === 1 && /1 of 3 fields/.test(tfCount), "transposed table filters fields cleanly");

    var spBox = document.createElement("div");
    T.table(spBox, {
      data: [{ s: "ZERO", hist: [0, 5, 10] }],
      colDefs: [{ field: "s", cellDataType: "text" }, { field: "hist", renderFn: "sparkline" }]
    });
    var spTip = spBox.querySelector("td.spark title");
    ok(spTip && !/Infinity|NaN/.test(spTip.textContent) && /\(–\)/.test(spTip.textContent), "sparkline zero base handles tooltip without Infinity/NaN");

    var canvasEl = document.getElementById("canvas");
    var keepCurR = T.S.cur, keepTabR = T.S.tabId, keepCardsR = T.S.cards, keepWidgetsR = T.S.widgets,
      keepStateR = T.S.state, keepByIdR = T.S.byId, keepGridR = T.S.grid, keepConceptsR = T.S.concepts;
    var keepChildrenR = canvasEl ? [].slice.call(canvasEl.childNodes) : [];
    try {
      T.S.concepts = Object.assign({}, keepConceptsR, {
        "company.overview": {
          label: "Company overview",
          members: [
            { b: "v5", w: "sec_company_overview_sec_obb", l: "SEC", mk: ["us"] },
            { b: "v5", w: "tmx_equity_asset_info_obb", l: "TMX", mk: ["ca"], sym: "tmx" }
          ]
        }
      });
      var caItem = { uid: "ca1", backend: "v5", widgetId: "sec_company_overview_sec_obb", x: 0, y: 0, w: 6, h: 6, params: {}, groups: ["Symbol"] };
      var caMissing = T.missingWidget(caItem, function () {}, { id: "v5", name: "V5", up: false });
      canvasEl.appendChild(caMissing.node);
      T.S.cur = { id: "d:route-test", name: "Route", groups: [{ name: "Symbol", paramName: "symbol", value: "SU.TO" }], tabs: [{ id: "t1", name: "T1", items: [caItem] }] };
      T.S.tabId = "t1";
      T.S.state = { v: 4, dashboards: {}, groups: {}, view: {}, libsel: {}, srcsel: {}, colsel: {}, cardview: {}, ui: {} };
      T.S.byId = Object.assign({}, keepByIdR, { v5: { id: "v5", name: "V5", url: "http://127.0.0.1:6900", up: true } });
      T.S.grid = { replace: function () {}, remove: function () {}, layout: function () {} };
      T.S.cards = [caMissing];
      T.S.widgets = Object.assign({}, keepWidgetsR, {
        v5: {
          sec_company_overview_sec_obb: { widgetId: "sec_company_overview_sec_obb", name: "SEC Overview", endpoint: "/api/v1/sec", type: "table", params: [{ paramName: "symbol" }] },
          tmx_equity_asset_info_obb: { widgetId: "tmx_equity_asset_info_obb", name: "TMX Overview", endpoint: "/api/v1/tmx", type: "table", params: [{ paramName: "symbol" }] }
        }
      });
      T.applyBackendArrival({ id: "v5", name: "V5", up: true });
      ok(caItem.widgetId === "tmx_equity_asset_info_obb", "applyBackendArrival routes missing Canadian card to TMX on backend recovery");
    } finally {
      (T.S.cards || []).forEach(function (c) { if (c && c.destroy) try { c.destroy(); } catch (e) {} });
      T.S.cur = keepCurR; T.S.tabId = keepTabR; T.S.cards = keepCardsR; T.S.widgets = keepWidgetsR;
      T.S.state = keepStateR; T.S.byId = keepByIdR; T.S.grid = keepGridR; T.S.concepts = keepConceptsR;
      if (canvasEl) { canvasEl.textContent = ""; keepChildrenR.forEach(function (n) { canvasEl.appendChild(n); }); }
    }
  })();

  // ---- reliability hardening: exports, empty plotFigure, frame arrival & postMessage origin, hidden resize, awaited getJSON & restartServices
  ok(typeof T.clearAllCaches === "function", "T.clearAllCaches is exported");
  ok(typeof T.getJSON === "function", "T.getJSON is exported");
  ok(document.getElementById("btn-restart") !== null, "index.html contains #btn-restart");
  ok(typeof T.restartServices === "function", "app.js defines and exposes restartServices for #btn-restart");

  (function () {
    var pfNode = document.createElement("div");
    T.plotFigure(pfNode, { data: [], layout: {} });
    ok(/No chart data returned/.test(pfNode.textContent || ""), "plotFigure renders clear message for empty data: []");

    var stageF = document.createElement("div");
    stageF.style.cssText = "position:fixed;left:-9999px;top:0;width:600px;height:400px";
    document.body.appendChild(stageF);
    try {
      var makeStubCtx = function (def) {
        return {
          item: { uid: "f_" + def.widgetId, backend: "v5", widgetId: def.widgetId, params: {} },
          def: def, backend: { id: "v5", url: "http://127.0.0.1:6900" }, scroller: stageF,
          groupOf: function () { return null; }, groupsOf: function () { return []; }, overrides: function () { return {}; },
          setGroup: function () {}, broadcast: function () {}, siblings: function () { return []; }, alternatives: function () { return []; },
          hasFreeOnTab: function () { return false; }, onChange: function () {}, onRemove: function () {}, relayout: function () {}
        };
      };
      var wIframe = T.widget(makeStubCtx({ widgetId: "tv_i", name: "TV Iframe", endpoint: "/api/v1/tv_i", type: "iframe", params: [] }));
      var wHtml = T.widget(makeStubCtx({ widgetId: "tv_h", name: "TV Html", endpoint: "/api/v1/tv_h", type: "html", params: [] }));
      var wTable = T.widget(makeStubCtx({ widgetId: "tbl_i", name: "Tbl", endpoint: "/api/v1/tbl", type: "table", params: [] }));
      ok(wIframe.isFrame === true && wHtml.isFrame === true && wTable.isFrame === false, "T.widget exposes isFrame for iframe/html cards only");

      // postMessage origin check & cleanup on destroy()
      stageF.appendChild(wIframe.node);
      wIframe.refresh(true);
      var fr2 = wIframe.node.querySelector("iframe");
      ok(fr2 && wIframe.node.querySelector(".frame-wait") !== null, "iframe card renders loading waitBox before ready message");
      window.dispatchEvent(new MessageEvent("message", { data: { type: "openbb-chart-ready" }, origin: "http://evil.example", source: fr2.contentWindow }));
      ok(wIframe.node.querySelector(".frame-wait") !== null, "onChartMsg rejects postMessage from unexpected origin");
      window.dispatchEvent(new MessageEvent("message", { data: { type: "openbb-chart-ready" }, origin: "http://127.0.0.1:6900", source: fr2.contentWindow }));
      ok(wIframe.node.querySelector(".frame-wait") === null && fr2.style.visibility === "", "onChartMsg accepts postMessage from iframe origin and reveals chart");

      stageF.appendChild(wHtml.node);
      wHtml.refresh(true);
      var frH = wHtml.node.querySelector("iframe");
      var frHWin = frH && frH.contentWindow;
      wHtml.destroy();
      window.dispatchEvent(new MessageEvent("message", { data: { type: "openbb-chart-ready" }, origin: "http://127.0.0.1:6900", source: frHWin }));
      ok(wHtml.node.querySelector(".frame-wait") === null, "destroy() cleans up frame body and chart message listener cleanly");

      wIframe.destroy();
      wTable.destroy();
    } finally {
      stageF.remove();
    }

    // applyBackendArrival refreshes iframe/html cards with force=true and calls retryIfFailed on data cards
    var canvasEl = document.getElementById("canvas");
    var keepCurF = T.S.cur, keepTabF = T.S.tabId, keepCardsF = T.S.cards, keepWidgetsF = T.S.widgets,
      keepStateF = T.S.state, keepByIdF = T.S.byId, keepGridF = T.S.grid;
    try {
      var refIframe = [], refHtml = [], refTbl = 0, retriedTbl = 0;
      var itIf = { uid: "if1", backend: "v5", widgetId: "w_if", params: {} };
      var itHt = { uid: "ht1", backend: "v5", widgetId: "w_ht", params: {} };
      var itTb = { uid: "tb1", backend: "v5", widgetId: "w_tb", params: {} };
      T.S.cur = { id: "d:frame-arr", name: "FrameArr", groups: [], tabs: [{ id: "t1", name: "T1", items: [itIf, itHt, itTb] }] };
      T.S.tabId = "t1";
      T.S.state = { v: 4, dashboards: {}, groups: {}, view: {}, libsel: {}, srcsel: {}, colsel: {}, cardview: {}, ui: {} };
      T.S.byId = Object.assign({}, keepByIdF, { v5: { id: "v5", name: "V5", url: "http://127.0.0.1:6900", up: true } });
      T.S.grid = { replace: function () {}, remove: function () {}, layout: function () {} };
      T.S.widgets = Object.assign({}, keepWidgetsF, {
        v5: {
          w_if: { widgetId: "w_if", endpoint: "/api/v1/if", type: "iframe", params: [] },
          w_ht: { widgetId: "w_ht", endpoint: "/api/v1/ht", type: "html", params: [] },
          w_tb: { widgetId: "w_tb", endpoint: "/api/v1/tb", type: "table", params: [] }
        }
      });
      T.S.cards = [
        { item: itIf, def: T.S.widgets.v5.w_if, isFrame: true, backend: "v5", refresh: function (f) { refIframe.push(f); }, retryIfFailed: function () {}, destroy: function () {} },
        { item: itHt, def: T.S.widgets.v5.w_ht, isFrame: false, backend: "v5", refresh: function (f) { refHtml.push(f); }, retryIfFailed: function () {}, destroy: function () {} },
        { item: itTb, def: T.S.widgets.v5.w_tb, isFrame: false, backend: "v5", refresh: function () { refTbl++; }, retryIfFailed: function () { retriedTbl++; }, destroy: function () {} }
      ];
      T.applyBackendArrival({ id: "v5", name: "V5", up: true });
      ok(refIframe.length === 1 && refIframe[0] === true && refHtml.length === 1 && refHtml[0] === true
        && refTbl === 0 && retriedTbl === 1,
        "applyBackendArrival calls refresh(true) on iframe and html cards and retryIfFailed() on data cards");
    } finally {
      T.S.cur = keepCurF; T.S.tabId = keepTabF; T.S.cards = keepCardsF; T.S.widgets = keepWidgetsF;
      T.S.state = keepStateF; T.S.byId = keepByIdF; T.S.grid = keepGridF;
    }
  })();

  // Wait for initial UI boot() before running awaited fetch-stubbed integration checks
  for (var bw = 0; bw < 80 && !T.S.booted; bw++) {
    await new Promise(function (r) { setTimeout(r, 50); });
  }
  ok(!!T.S.booted, "UI booted before async integration checks");

  // P4-06: T.request bounds every wait and relays the caller's abort
  await (async function () {
    var saved = window.fetch;
    window.fetch = function (u, init) {
      return new Promise(function (res, rej) { init.signal.addEventListener("abort", function () { var a = new Error("aborted"); a.name = "AbortError"; rej(a); }); });
    };
    try {
      var te = null;
      try { await T.request("/never", { timeout: 40 }); } catch (e) { te = e; }
      ok(te && te.timedOut && te.name === "TimeoutError" && te.errorKind === "timeout", "T.request times out with a typed error");
      var outer = new AbortController(), ae = null;
      var pr = T.request("/never", { timeout: 0, signal: outer.signal });
      outer.abort();
      try { await pr; } catch (e) { ae = e; }
      ok(ae && ae.name === "AbortError" && !ae.timedOut, "the caller's abort is relayed, not reported as a timeout");
    } finally {
      window.fetch = saved;
    }
  })();


  // Awaited getJSON retry, non-OK preservation, OECD no-retry & hidden-tab needPlotResize checks
  var origFetch = window.fetch;
  try {
    T.clearAllCaches();
    var calls503 = 0;
    window.fetch = async function (u, opts) {
      if (String(u).indexOf("/api/v1/test_503_") >= 0) {
        calls503++;
        if (calls503 === 1) return { ok: false, status: 503, headers: new Headers(), text: async function () { return "Service Unavailable"; } };
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"ok":true}'; } };
      }
      return origFetch(u, opts);
    };
    var res503 = await T.getJSON("http://127.0.0.1:6950/api/v1/test_503_" + Date.now(), true);
    ok(calls503 === 2 && res503 && res503.ok === true, "getJSON retries once on HTTP 503");

    var callsTrunc = 0;
    window.fetch = async function (u, opts) {
      if (String(u).indexOf("/api/v1/test_trunc_") >= 0) {
        callsTrunc++;
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"broken":'; } };
      }
      return origFetch(u, opts);
    };
    var truncErr = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/test_trunc_" + Date.now(), true); } catch (e) { truncErr = e; }
    ok(callsTrunc === 2 && truncErr && /incomplete response/i.test(truncErr.message), "getJSON retries once on truncated JSON before failing cleanly");
    ok(T.classifyError(truncErr.message, 200, { source: "Yahoo Finance" }).cls === "upstream", "incomplete response classifies as upstream (not bug)");

    // Empty-body retry returning HTTP 500 throws HTTP error instead of returning null
    var callsYfErr = 0;
    window.fetch = async function (u, opts) {
      if (String(u).indexOf("/api/v1/yfinance/test_empty_500") >= 0) {
        callsYfErr++;
        if (callsYfErr === 1) return { ok: true, status: 200, headers: new Headers(), text: async function () { return ""; } };
        return { ok: false, status: 500, headers: new Headers(), text: async function () { return JSON.stringify({ detail: "Yahoo upstream failed" }); } };
      }
      return origFetch(u, opts);
    };
    var yfErr = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/yfinance/test_empty_500", true); } catch (e) { yfErr = e; }
    ok(callsYfErr === 2 && yfErr && yfErr.status === 500 && /Yahoo upstream failed/.test(yfErr.message), "getJSON throws HTTP 500 after empty-body retry instead of returning null");

    // Truncated-JSON retry returning HTTP 502 throws HTTP error instead of incomplete-response
    var callsTrunc502 = 0;
    window.fetch = async function (u, opts) {
      if (String(u).indexOf("/api/v1/test_trunc_502") >= 0) {
        callsTrunc502++;
        if (callsTrunc502 === 1) return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"broken":'; } };
        return { ok: false, status: 502, headers: new Headers(), text: async function () { return JSON.stringify({ detail: "Bad Gateway" }); } };
      }
      return origFetch(u, opts);
    };
    var trunc502Err = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/test_trunc_502", true); } catch (e) { trunc502Err = e; }
    ok(callsTrunc502 === 2 && trunc502Err && trunc502Err.status === 502 && /Bad Gateway/.test(trunc502Err.message), "getJSON throws HTTP 502 after truncated-JSON retry");

    // OECD URLs never retry on 503 or truncated JSON, and 429 arms limitedUntil
    var callsOecd503 = 0, callsOecdTrunc = 0, callsOecd429 = 0;
    window.fetch = async function (u, opts) {
      var su = String(u);
      if (su.indexOf("test_oecd_503") >= 0) {
        callsOecd503++;
        return { ok: false, status: 503, headers: new Headers(), text: async function () { return "Service Unavailable"; } };
      }
      if (su.indexOf("/api/v1/oecd/test_trunc") >= 0) {
        callsOecdTrunc++;
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"broken":'; } };
      }
      if (su.indexOf("/api/v1/oecd/test_429") >= 0) {
        callsOecd429++;
        if (callsOecd429 === 1) return { ok: false, status: 429, headers: new Headers(), text: async function () { return JSON.stringify({ detail: "Too Many Requests" }); } };
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"ok":true}'; } };
      }
      return origFetch(u, opts);
    };
    var oecdErr503 = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/economy/gdp?provider=oecd&t=test_oecd_503", true); } catch (e) { oecdErr503 = e; }
    ok(callsOecd503 === 1 && oecdErr503 && oecdErr503.status === 503, "getJSON never retries OECD URL on 503");

    var oecdErrTrunc = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/oecd/test_trunc", true); } catch (e) { oecdErrTrunc = e; }
    ok(callsOecdTrunc === 1 && oecdErrTrunc && /incomplete response/i.test(oecdErrTrunc.message), "getJSON never retries OECD URL on truncated JSON");

    var oecdErr429a = null, oecdErr429b = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/oecd/test_429", true); } catch (e) { oecdErr429a = e; }
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/oecd/test_429", true); } catch (e) { oecdErr429b = e; }
    ok(callsOecd429 === 1 && oecdErr429a && oecdErr429b && /Too Many Requests/i.test(oecdErr429b.message),
      "OECD 429 sets limitedUntil cooldown so next call skips fetch");
    T.clearAllCaches();
    var oecdOk = await T.getJSON("http://127.0.0.1:6950/api/v1/oecd/test_429", true);
    ok(callsOecd429 === 2 && oecdOk && oecdOk.ok === true, "T.clearAllCaches clears OECD limitedUntil cooldown");

    // Nasdaq URLs never retry on 503 or truncated JSON, and per-provider cooldown does not block OECD
    var callsNasdaq503 = 0, callsNasdaqTrunc = 0, callsNasdaq400 = 0, callsNasdaq429 = 0, callsOecdWhileNasdaq = 0, callsNasdaqOther = 0;
    window.fetch = async function (u, opts) {
      var su = String(u);
      if (su.indexOf("test_nasdaq_503") >= 0) {
        callsNasdaq503++;
        return { ok: false, status: 503, headers: new Headers(), text: async function () { return "Service Unavailable"; } };
      }
      if (su.indexOf("/api/v1/nasdaq/test_trunc") >= 0) {
        callsNasdaqTrunc++;
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"broken":'; } };
      }
      if (su.indexOf("/api/v1/nasdaq/test_400_rl") >= 0) {
        callsNasdaq400++;
        if (callsNasdaq400 === 1) return { ok: false, status: 400, headers: new Headers(), text: async function () { return JSON.stringify({ detail: "Nasdaq rate limit reached" }); } };
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"ok":true}'; } };
      }
      if (su.indexOf("/api/v1/nasdaq/test_429") >= 0) {
        callsNasdaq429++;
        if (callsNasdaq429 === 1) return { ok: false, status: 429, headers: new Headers(), text: async function () { return JSON.stringify({ detail: "Too Many Requests" }); } };
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"ok":true}'; } };
      }
      if (su.indexOf("/api/v1/nasdaq/test_other_card") >= 0) {
        callsNasdaqOther++;
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"other":true}'; } };
      }
      if (su.indexOf("/api/v1/oecd/test_unblocked") >= 0) {
        callsOecdWhileNasdaq++;
        return { ok: true, status: 200, headers: new Headers(), text: async function () { return '{"oecd":true}'; } };
      }
      return origFetch(u, opts);
    };
    var nasdaqErr503 = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/economy/calendar?provider=nasdaq&t=test_nasdaq_503", true); } catch (e) { nasdaqErr503 = e; }
    ok(callsNasdaq503 === 1 && nasdaqErr503 && nasdaqErr503.status === 503, "getJSON never retries Nasdaq URL on 503");

    var nasdaqErrTrunc = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/nasdaq/test_trunc", true); } catch (e) { nasdaqErrTrunc = e; }
    ok(callsNasdaqTrunc === 1 && nasdaqErrTrunc && /incomplete response/i.test(nasdaqErrTrunc.message), "getJSON never retries Nasdaq URL on truncated JSON");

    var nasdaqErr400a = null, nasdaqErr400b = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/nasdaq/test_400_rl", true); } catch (e) { nasdaqErr400a = e; }
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/nasdaq/test_400_rl", true); } catch (e) { nasdaqErr400b = e; }
    var oecdUnblocked = await T.getJSON("http://127.0.0.1:6950/api/v1/oecd/test_unblocked", true);
    ok(callsNasdaq400 === 1 && nasdaqErr400a && nasdaqErr400b && /Nasdaq rate limit/i.test(nasdaqErr400b.message)
      && callsOecdWhileNasdaq === 1 && oecdUnblocked && oecdUnblocked.oecd === true,
      "Nasdaq 400 rate limit arms Nasdaq cooldown without blocking OECD URLs");

    T.clearAllCaches();
    var nasdaqErr429a = null, nasdaqErr429b = null;
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/nasdaq/test_429", true); } catch (e) { nasdaqErr429a = e; }
    try { await T.getJSON("http://127.0.0.1:6950/api/v1/nasdaq/test_429", true); } catch (e) { nasdaqErr429b = e; }
    ok(callsNasdaq429 === 1 && nasdaqErr429a && nasdaqErr429b && /Too Many Requests/i.test(nasdaqErr429b.message),
      "Nasdaq 429 arms Nasdaq cooldown so next call skips fetch");
    var nasdaqOther = await T.getJSON("http://127.0.0.1:6950/api/v1/nasdaq/test_other_card", true);
    ok(callsNasdaqOther === 1 && nasdaqOther && nasdaqOther.other === true,
      "Nasdaq cooldown covers only the failed URL; other Nasdaq cards still reach the backend (its cache serves them)");
    T.clearAllCaches();
    var nasdaqOk = await T.getJSON("http://127.0.0.1:6950/api/v1/nasdaq/test_429", true);
    ok(callsNasdaq429 === 2 && nasdaqOk && nasdaqOk.ok === true, "T.clearAllCaches clears Nasdaq cooldown");
    ok(T.classifyError("Nasdaq rate limit reached (temporarily blocked). Cooling down for ~120s.", 400, { source: "Nasdaq", provider: "nasdaq" }).cls === "ratelimit"
      && T.classifyError("Nasdaq rate limit reached (HTTP 403).", 400, { source: "Nasdaq", provider: "nasdaq" }).cls === "ratelimit",
      "Nasdaq rate-limit and temporary-block errors classify as ratelimit (never missing Nasdaq Data Link key)");
    ok(/about 8 minutes/.test(T.classifyError("Nasdaq rate limit active (HTTP 429). Cooling down for ~480s.", 400, { source: "Nasdaq", provider: "nasdaq" }).hint)
      && /about 45 seconds/.test(T.classifyError("Nasdaq rate limit active (HTTP 429). Cooling down for ~45s.", 400, { source: "Nasdaq", provider: "nasdaq" }).hint)
      && /about 2 minutes/.test(T.classifyError("Too Many Requests", 429, { source: "Nasdaq", provider: "nasdaq" }).hint),
      "rate-limit hint uses the backend's ~Ns cooldown when given (480s -> 8 minutes, 45s -> 45 seconds)");

    // Hidden-tab needPlotResize: ResizeObserver while 0x0 defers Plotly.Plots.resize until visible check()
    var rsStage = document.createElement("div");
    rsStage.style.cssText = "position:fixed;left:0;top:0;width:520px;height:360px;visibility:hidden";
    document.body.appendChild(rsStage);
    var origPlotResize = window.Plotly && window.Plotly.Plots && window.Plotly.Plots.resize;
    var rsCalls = 0;
    try {
      window.fetch = async function (u, opts) {
        if (String(u).indexOf("/__plot_resize_test") >= 0) {
          return {
            ok: true, status: 200, headers: new Headers(),
            text: async function () { return JSON.stringify({ data: [{ type: "scatter", x: ["2026-01-01", "2026-01-02"], y: [10, 11] }], layout: {} }); }
          };
        }
        return origFetch(u, opts);
      };
      var rsCard = T.widget({
        item: { uid: "rs1", backend: "x", widgetId: "x_rs", params: {} },
        def: { widgetId: "x_rs", name: "RS Chart", endpoint: "/__plot_resize_test", type: "chart", params: [] },
        backend: { id: "x", url: "http://127.0.0.1:6950" }, scroller: rsStage,
        groupOf: function () { return null; }, groupsOf: function () { return []; }, overrides: function () { return {}; },
        setGroup: function () {}, broadcast: function () {}, siblings: function () { return []; }, alternatives: function () { return []; },
        hasFreeOnTab: function () { return false; }, onChange: function () {}, onRemove: function () {}, relayout: function () {}
      });
      rsStage.appendChild(rsCard.node);
      rsCard.node.style.height = "300px";
      rsCard.check();
      await new Promise(function (r) { setTimeout(r, 80); });
      var plotEl = rsCard.node.querySelector(".plot");
      ok(plotEl && plotEl.data && plotEl.data.length === 1, "chart rendered for hidden-tab resize test");
      if (window.Plotly && window.Plotly.Plots) {
        window.Plotly.Plots.resize = function (gd) { rsCalls++; return origPlotResize.call(window.Plotly.Plots, gd); };
      }
      rsStage.style.display = "none";
      await new Promise(function (r) { setTimeout(r, 200); });
      var callsWhileHidden = rsCalls;
      rsStage.style.display = "";
      rsCard.check();
      ok(callsWhileHidden === 0 && rsCalls >= 1, "hidden-tab 0x0 ResizeObserver defers Plotly resize until visible check()");
      rsCard.destroy();
    } finally {
      if (window.Plotly && window.Plotly.Plots && origPlotResize) window.Plotly.Plots.resize = origPlotResize;
      rsStage.remove();
    }

    // restartServices() UI flow: error detail, failed restart, offline backend probe, and recovery with missingWidget + /x/ reload
    var canvasEl2 = document.getElementById("canvas");
    var keepBackends = T.S.backends, keepByIdS = T.S.byId, keepWidgetsS = T.S.widgets, keepCardsS = T.S.cards,
      keepCurS = T.S.cur, keepTabS = T.S.tabId, keepStateS = T.S.state, keepGridS = T.S.grid,
      keepRestarting = T.S.restarting, keepBooted = T.S.booted, keepToast = T.toast;
    var keepCanvas2 = canvasEl2 ? [].slice.call(canvasEl2.childNodes) : [];
    var btnRst = document.getElementById("btn-restart");
    var toasts = [];
    T.toast = function (msg, isErr) { toasts.push({ msg: String(msg), err: !!isErr }); };
    try {
      T.S.booted = true;
      var fakeV5 = { id: "v5", name: "Main data service", url: "http://127.0.0.1:6900", up: true, count: 0 };
      var fakeX = { id: "x", name: "Terminal views", url: "", up: true, count: 1 };
      T.S.backends = [fakeV5, fakeX];
      T.S.byId = { v5: fakeV5, x: fakeX };
      T.S.widgets = { x: { x_test: { widgetId: "x_test", name: "X", endpoint: "/x/test", type: "table", params: [] } } };
      T.S.cards = [];
      T.S.cur = null;

      // Case 1: 400 with {"detail": ...}
      window.fetch = async function (u, opts) {
        if (String(u) === "/api/services/restart") {
          return { ok: false, status: 400, headers: new Headers(), json: async function () { return { detail: "Legacy V4 backend is not active." }; } };
        }
        return origFetch(u, opts);
      };
      await T.restartServices();
      ok(!T.S.restarting && !btnRst.disabled && toasts.some(function (t) { return t.err && /Legacy V4 backend is not active/.test(t.msg); }),
        "restartServices surfaces server JSON detail on error and restores button");

      // Case 2: restart worker reports last_ok: false -> error toast, no 'restarted' toast
      toasts.length = 0;
      window.fetch = async function (u, opts) {
        var su = String(u);
        if (su === "/api/services/restart") return { ok: true, status: 202, headers: new Headers(), json: async function () { return { ok: true, restart_seq: 1 }; } };
        if (su === "/api/services") return { ok: true, status: 200, headers: new Headers(), json: async function () { return { v5: { state: "stopped" }, restart: { restarting: false, seq: 1, last_ok: false, error: "boom restart" } }; } };
        return origFetch(u, opts);
      };
      await T.restartServices();
      ok(!T.S.restarting && !btnRst.disabled && toasts.some(function (t) { return t.err && /boom restart/.test(t.msg); })
        && !toasts.some(function (t) { return /reloading widgets/i.test(t.msg); }),
        "restartServices aborts cleanly with error toast when restart worker fails");

      // Case 3: backend probe fails after restart -> never toasts 'Data services restarted'
      toasts.length = 0;
      window.fetch = async function (u, opts) {
        var su = String(u);
        if (su === "/api/services/restart") return { ok: true, status: 202, headers: new Headers(), json: async function () { return { ok: true, restart_seq: 2 }; } };
        if (su === "/api/services") return { ok: true, status: 200, headers: new Headers(), json: async function () { return { v5: { state: "running", reachable: true }, restart: { restarting: false, seq: 2, last_ok: true, error: "" } }; } };
        if (su === "http://127.0.0.1:6900/") throw new Error("offline");
        return origFetch(u, opts);
      };
      await T.restartServices();
      ok(!T.S.restarting && !toasts.some(function (t) { return /Data services restarted/i.test(t.msg); })
        && toasts.some(function (t) { return t.err && /offline|waiting/i.test(t.msg); }),
        "restartServices never toasts success when backend probe fails");

      // Case 4: successful recovery when S.widgets.v5 is empty -> loadBackend recovers missingWidget and reloads /x/ card once
      toasts.length = 0;
      var xRefreshes = 0;
      var replacedRestartUids = [];
      var mRstItem = { uid: "mr1", backend: "v5", widgetId: "w_rec", x: 0, y: 0, w: 6, h: 6, manual: "Click", params: {} };
      var xRstItem = { uid: "xr1", backend: "x", widgetId: "x_test", x: 6, y: 0, w: 6, h: 6, params: {} };
      var mRstCard = T.missingWidget(mRstItem, function () {}, { id: "v5", name: "Main data service", up: false });
      var xRstCard = { node: document.createElement("div"), item: xRstItem, def: T.S.widgets.x.x_test, missing: false, backend: "x", refresh: function () { xRefreshes++; }, retryIfFailed: function () {}, destroy: function () {} };
      canvasEl2.appendChild(mRstCard.node);
      canvasEl2.appendChild(xRstCard.node);
      T.S.cur = { id: "d:rst", name: "Rst", groups: [], tabs: [{ id: "t1", name: "T1", items: [mRstItem, xRstItem] }] };
      T.S.tabId = "t1";
      T.S.state = { v: 4, dashboards: {}, groups: {}, view: {}, libsel: {}, srcsel: {}, colsel: {}, cardview: {}, ui: {} };
      T.S.grid = {
        replace: function (it, node) { replacedRestartUids.push(it.uid); if (mRstCard.node.parentNode) mRstCard.node.parentNode.replaceChild(node, mRstCard.node); },
        remove: function () {}, layout: function () {}
      };
      T.S.cards = [mRstCard, xRstCard];
      fakeV5.watching = false; fakeV5._watchGen = 0;
      window.fetch = async function (u, opts) {
        var su = String(u);
        if (su === "/api/services/restart") return { ok: true, status: 202, headers: new Headers(), json: async function () { return { ok: true, restart_seq: 3 }; } };
        if (su === "/api/services") return { ok: true, status: 200, headers: new Headers(), json: async function () { return { v5: { state: "running", reachable: true }, restart: { restarting: false, seq: 3, last_ok: true, error: "" } }; } };
        if (su === "http://127.0.0.1:6900/") return { ok: true, status: 200, headers: new Headers(), text: async function () { return "ok"; } };
        if (su === "http://127.0.0.1:6900/widgets.json") return { ok: true, status: 200, headers: new Headers(), json: async function () { return { w_rec: { widgetId: "w_rec", name: "Recovered", endpoint: "/api/v1/none", type: "table", params: [] } }; } };
        if (su === "http://127.0.0.1:6900/apps.json") return { ok: true, status: 200, headers: new Headers(), json: async function () { return []; } };
        return origFetch(u, opts);
      };
      await T.restartServices();
      await new Promise(function (r) { setTimeout(r, 320); });
      var recCardNow = T.S.cards.filter(function (c) { return c.item && c.item.uid === "mr1"; })[0];
      ok(!T.S.restarting && !btnRst.disabled && fakeV5.up === true
        && replacedRestartUids.join() === "mr1" && recCardNow && !recCardNow.missing
        && xRefreshes === 1 && toasts.some(function (t) { return !t.err && /Data services restarted/i.test(t.msg); }),
        "restartServices loads missing backend defs, recovers missingWidget, and reloads /x/ cards once");
    } finally {
      (T.S.cards || []).forEach(function (c) { if (c && c.destroy) try { c.destroy(); } catch (e) {} });
      T.S.backends = keepBackends; T.S.byId = keepByIdS; T.S.widgets = keepWidgetsS; T.S.cards = keepCardsS;
      T.S.cur = keepCurS; T.S.tabId = keepTabS; T.S.state = keepStateS; T.S.grid = keepGridS;
      T.S.restarting = keepRestarting; T.S.booted = keepBooted; T.toast = keepToast;
      if (btnRst) { btnRst.classList.remove("busy"); btnRst.disabled = false; btnRst.textContent = "Restart"; }
      if (canvasEl2) { canvasEl2.textContent = ""; keepCanvas2.forEach(function (n) { canvasEl2.appendChild(n); }); }
    }
  } finally {
    window.fetch = origFetch;
  }

  // ---- long session (P1-09): 200 tab switches through the real tab bar leave no window/document listener, interval
  // or DOM growth; the browser heap after 200 switches is within 20% of the value after 20; the popover anchor check
  // runs only while a popover is open and the page is visible
  await (async function () {
    var proto = EventTarget.prototype, addL = proto.addEventListener, remL = proto.removeEventListener;
    var setI = window.setInterval, clrI = window.clearInterval, origFetch = window.fetch;
    var entries = [], live = new Set();
    function capOf(o) { return typeof o === "boolean" ? o : !!(o && o.capture); }
    function find(t, type, fn, c) {
      for (var i = 0; i < entries.length; i++) { var e = entries[i]; if (e.t === t && e.type === type && e.fn === fn && e.c === c) return i; }
      return -1;
    }
    function drop(t, type, fn, c) { var i = find(t, type, fn, c); if (i >= 0) entries.splice(i, 1); }
    var keepCurId = T.S.cur && T.S.cur.id, keepTab = T.S.tabId, keepDefs = T.S.widgets.x;
    var tabsEl = document.getElementById("tabs");
    try {
      proto.addEventListener = function (type, fn, o) {
        var t = this, c = capOf(o);
        if ((t === window || t === document) && fn && !(o && o.once) && find(t, type, fn, c) < 0) {
          entries.push({ t: t, type: type, fn: fn, c: c });
          if (o && o.signal) addL.call(o.signal, "abort", function () { drop(t, type, fn, c); });
        }
        return addL.apply(this, arguments);
      };
      proto.removeEventListener = function (type, fn, o) {
        if (this === window || this === document) drop(this, type, fn, capOf(o));
        return remL.apply(this, arguments);
      };
      window.setInterval = function () { var id = setI.apply(window, arguments); live.add(id); return id; };
      window.clearInterval = function (id) { live.delete(id); return clrI.apply(window, arguments); };
      var probe = function () {};
      window.addEventListener("resize", probe);
      var counted = entries.length;
      window.removeEventListener("resize", probe);
      ok(counted === 1 && entries.length === 0, "the listener count sees a window listener come and go");

      // the popover anchor check: no interval while nothing is open or the page is hidden
      var anchor = document.createElement("button");
      document.body.appendChild(anchor);
      var before = live.size;
      T.popover(anchor, document.createElement("div"));
      ok(live.size === before + 1, "an open popover runs one anchor check");
      Object.defineProperty(document, "hidden", { configurable: true, get: function () { return true; } });
      document.dispatchEvent(new Event("visibilitychange"));
      ok(live.size === before, "the anchor check pauses while the page is hidden");
      delete document.hidden;
      document.dispatchEvent(new Event("visibilitychange"));
      ok(live.size === before + 1, "it resumes when the page is visible again with the popover still open");
      anchor.remove();
      document.dispatchEvent(new Event("visibilitychange"));
      ok(!document.querySelector("#pop-root .pop") && live.size === before, "a popover whose anchor is gone closes and its check stops");
      T.closePop();
      ok(live.size === before, "closing a popover leaves no interval");

      // a dashboard with four identical tabs of real cards: two tables (one with auto-refresh) and one failing source
      T.S.widgets.x = Object.assign({}, keepDefs, {
        x_soak_a: { widgetId: "x_soak_a", name: "Soak A", endpoint: "/x/soak_a", type: "table", params: [], source: ["SEC"], refetchInterval: 60000 },
        x_soak_b: { widgetId: "x_soak_b", name: "Soak B", endpoint: "/x/soak_b", type: "table", params: [], source: ["FRED"] },
        x_soak_c: { widgetId: "x_soak_c", name: "Soak C", endpoint: "/x/soak_c", type: "table", params: [], source: ["ECB"] }
      });
      var rows = JSON.stringify([{ date: "2026-11-20", value: 1.5 }, { date: "2026-11-21", value: 1.6 }]);
      window.fetch = async function (u, opts) {
        var su = String(u);
        if (su.indexOf("/x/soak_c") >= 0) return { ok: false, status: 502, headers: new Headers({ "Content-Type": "application/json" }), text: async function () { return JSON.stringify({ error: { kind: "upstream", message: "ECB did not answer" }, sources: ["ECB"] }); } };
        if (su.indexOf("/x/soak_") >= 0) return { ok: true, status: 200, headers: new Headers({ "Content-Type": "application/json" }), text: async function () { return rows; } };
        return origFetch(u, opts);
      };
      var tabs = [0, 1, 2, 3].map(function (i) {
        return { id: "s" + i, name: "S" + i, items: ["a", "b", "c"].map(function (k, j) { return { uid: "s" + i + k, backend: "x", widgetId: "x_soak_" + k, x: j * 4, y: 0, w: 4, h: 6, params: {} }; }) };
      });
      T.S.state.dashboards["user:soak"] = { id: "user:soak", name: "Soak", description: "", source: "user", groups: [], tabs: tabs };
      ok(T.openDashboard("user:soak", "s0"), "the long-session dashboard opens");
      var settle = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
      var heap = function () {
        if (window.gc) { window.gc(); window.gc(); }
        return performance.memory ? performance.memory.usedJSHeapSize : null;
      };
      var measure = function () {
        return { listeners: entries.length, intervals: live.size, nodes: document.getElementsByTagName("*").length, heap: heap() };
      };
      var first = null, at20 = null;
      for (var k = 1; k <= 200; k++) {
        var tabNodes = [].slice.call(tabsEl.querySelectorAll(".tab:not(.add)"));
        tabNodes[k % 4].dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await settle(k === 1 || k === 20 || k === 200 ? 60 : 4);
        if (k === 1) first = measure();
        if (k === 20) at20 = measure();
      }
      // switch 200 lands on tab 0 and switch 1 on tab 1: one more switch compares the same tab
      [].slice.call(tabsEl.querySelectorAll(".tab:not(.add)"))[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await settle(60);
      var last = measure();
      ok(T.S.tabId === "s1" && T.S.cards.length === 3 && document.querySelectorAll("#canvas .w").length >= 3, "the cards of the last tab are on the page (" + T.S.cards.length + ")");
      ok(last.listeners === first.listeners, "window/document listeners after 200 switches equal those after the first (" + first.listeners + " -> " + last.listeners + ")");
      ok(last.intervals === first.intervals && first.intervals >= 1, "live intervals after 200 switches equal those after the first (" + first.intervals + " -> " + last.intervals + ")");
      ok(last.nodes === first.nodes, "DOM nodes after 200 switches equal those after the first (" + first.nodes + " -> " + last.nodes + ")");
      ok(!!performance.memory && at20.heap > 0, "the browser reports its heap");
      var growth = (last.heap - at20.heap) / at20.heap;
      ok(growth <= 0.2, "heap after 200 switches within 20% of the value after 20 (" + at20.heap + " -> " + last.heap + ")");
      window.__fcNotes = (window.__fcNotes || []).concat(["long session: listeners " + first.listeners + "->" + last.listeners + ", intervals " + first.intervals + "->" + last.intervals
        + ", DOM nodes " + first.nodes + "->" + last.nodes + ", heap after 20 " + (at20.heap / 1048576).toFixed(2) + " MB, after 200 " + (last.heap / 1048576).toFixed(2)
        + " MB (" + (growth >= 0 ? "+" : "") + (growth * 100).toFixed(1) + "%)" + (window.gc ? "" : ", no forced GC")]);
    } finally {
      proto.addEventListener = addL; proto.removeEventListener = remL;
      window.setInterval = setI; window.clearInterval = clrI; window.fetch = origFetch;
      delete T.S.state.dashboards["user:soak"];
      T.S.widgets.x = keepDefs;
      if (keepCurId && T.openDashboard(keepCurId, keepTab)) { /* back where the checks started */ }
      else { (T.S.cards || []).forEach(function (c) { try { c.destroy(); } catch (e) { /* already gone */ } }); }
    }
  })();

  console.log("frontend checks: " + n + " passed");
  return n;
})(window.T);
