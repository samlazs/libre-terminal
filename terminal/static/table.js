/* Data table (sort / filter / lazy rows / transpose / CSV) and quick charts. */
(function (T) {
  "use strict";
  var el = T.el;

  // ---------- formatting ----------
  var nfCache = {};
  function nf(min, max, group) {
    var k = min + "|" + max + "|" + group;
    return nfCache[k] || (nfCache[k] = new Intl.NumberFormat("en-US", { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: group }));
  }
  var NO_GROUP = /(^|_)(year|cik|id|code|number|sic|zip|lei|cusip|isin|figi|naics|fy|rssd|fips|crd|key|uuid)($|_)/i;
  T.NO_GROUP = NO_GROUP;
  var DATE_RE = /^\d{4}-\d{2}-\d{2}/;

  T.fmtNum = function (v, def, abbrevMin) {
    if (typeof v !== "number" || !isFinite(v)) return String(v);
    def = def || {};
    if (def.paren && v < 0) return "(" + T.fmtNum(-v, Object.assign({}, def, { paren: false, prefix: "", suffix: "" }), abbrevMin) + ")";   // accounting negatives
    var f = def.formatterFn, dp = def.decimalPlaces, out;
    if (f === "percent") out = nf(dp == null ? 2 : dp, dp == null ? 2 : dp, true).format(v) + "%";
    else if (f === "normalizedPercent") out = nf(dp == null ? 2 : dp, dp == null ? 2 : dp, true).format(v * 100) + "%";
    else if (f === "int") out = nf(0, 0, true).format(v);
    else if (f === "none" || def.kind === "id" || NO_GROUP.test(def.field || "")) out = Number.isInteger(v) ? String(v) : nf(0, dp == null ? 4 : dp, false).format(v);
    else if (def.scale) out = nf(dp == null ? 2 : dp, dp == null ? 2 : dp, true).format(v / def.scale) + def.scaleUnit;   // one unit per column
    else {
      var a = Math.abs(v);
      if (dp != null) out = nf(dp, dp, true).format(v);
      else if (abbrevMin && a >= abbrevMin) {
        if (a >= 1e12) out = nf(2, 2, true).format(v / 1e12) + "T";
        else if (a >= 1e9) out = nf(2, 2, true).format(v / 1e9) + "B";
        else out = nf(2, 2, true).format(v / 1e6) + "M";
      } else if (Number.isInteger(v)) out = nf(0, 0, true).format(v);
      else if (a >= 100) out = nf(2, 2, true).format(v);
      else if (a >= 1) out = nf(2, 4, true).format(v);
      else if (a >= 0.01 || a === 0) out = nf(2, 4, true).format(v);
      else out = nf(2, 6, true).format(v);
    }
    return (def.prefix || "") + out + (def.suffix || "");
  };

  /**
   * One CSV / clipboard cell. Text that a spreadsheet would run as a formula (=, +, -, @ at the start: news titles,
   * company names and filing descriptions come from providers) gets a leading apostrophe; numbers stay numbers.
   */
  T.csvCell = function (v, sep) {
    if (v && typeof v === "object" && !Array.isArray(v) && "value" in v && Object.keys(v).length <= 3) v = v.value;   // {value, footnote} cells
    if (v == null) return "";
    var s = typeof v === "object" ? JSON.stringify(v) : String(v);
    if (typeof v === "string" && /^[=+\-@\t\r]/.test(s) && !/^[+\-]?(\d[\d,]*\.?\d*|\.\d+)(e[+\-]?\d+)?%?$/i.test(s)) s = "'" + s;
    return s.indexOf(sep || ",") >= 0 || /["\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };

  // Every time is shown in Montreal time (owner, 2026-11-19), whatever the browser's own zone. A time that carries its
  // zone (Z, +01:00) is converted; a time with no zone is already Montreal/New York time at the source (U.S. feeds;
  // Yahoo intraday bars and Nasdaq Nordic notices are converted on the backend). Midnight UTC is a date, not a time.
  T.TZ = "America/Toronto";
  var MTL = new Intl.DateTimeFormat("en-CA", { timeZone: T.TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  var ZONED = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})$/i;
  /** Date -> "YYYY-MM-DD HH:MM:SS" in Montreal time. */
  T.mtlParts = function (d) {
    var p = {};
    MTL.formatToParts(d).forEach(function (x) { p[x.type] = x.value; });
    return p.year + "-" + p.month + "-" + p.day + " " + (p.hour === "24" ? "00" : p.hour) + ":" + p.minute + ":" + p.second;
  };
  /** A zoned ISO time -> the same instant as a Montreal wall-clock string; anything else unchanged. */
  T.toMtl = function (s) {
    if (typeof s !== "string") return s;
    var m = ZONED.exec(s.trim());
    if (!m) return s;
    if (m[2] === "00" && m[3] === "00" && (m[4] || "00") === "00" && /^(Z|[+-]00:?00)$/i.test(m[5])) return m[1];   // a date sent as midnight UTC
    var d = new Date(s.trim().replace(" ", "T").replace(/([+-]\d{2})(\d{2})$/, "$1:$2"));
    return isNaN(d) ? s : T.mtlParts(d);
  };
  /** "HH:MM" now, in Montreal time (card footers). */
  T.mtlNow = function () { return T.mtlParts(new Date()).slice(11, 16); };
  T.fmtDate = function (s) {
    s = T.toMtl(String(s));
    var m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(:\d{2})?/.exec(s);
    if (!m) return s.length > 10 && DATE_RE.test(s) ? s.slice(0, 10) : s;
    return m[2] === "00:00" ? m[1] : m[1] + " " + m[2];
  };

  // Readable headers for columns the source did not describe (same rules as terminal/columns.py header()).
  var WORDS = { pe: "P/E", eps: "EPS", ytd: "YTD", mtd: "MTD", qtd: "QTD", usd: "USD", id: "ID", cik: "CIK", cusip: "CUSIP", isin: "ISIN", lei: "LEI", figi: "FIGI",
    sic: "SIC", etf: "ETF", nav: "NAV", aum: "AUM", ebitda: "EBITDA", ebit: "EBIT", roe: "ROE", roa: "ROA", roic: "ROIC", gdp: "GDP", cpi: "CPI", pce: "PCE",
    iv: "IV", oi: "OI", fx: "FX", ttm: "TTM", yoy: "y/y", qoq: "q/q", url: "URL", sec: "SEC", otc: "OTC", pct: "%", percent: "%", num: "No.", avg: "Avg",
    fcf: "FCF", sga: "SG&A", rd: "R&D", ceo: "CEO", ois: "OIS", us: "US", uk: "UK", aaa: "AAA", aa: "AA", bbb: "BBB", bb: "BB", ccc: "CCC", ma: "MA", nsa: "NSA", sa: "SA", tips: "TIPS" };
  function pretty(field) {
    var f = String(field), m;
    if ((m = /^sven(y|py|f)(\d\d)$/i.exec(f))) return +m[2] + "Y " + { y: "zero-coupon", py: "par", f: "inst. forward" }[m[1].toLowerCase()];
    if ((m = /^s?ma_?(\d+)d?$/i.exec(f))) return m[1] + "-day MA";
    if ((m = /^(?:tenor_)?(\d+[dwmy])$/i.exec(f))) return m[1].toUpperCase();
    if ((m = /^(day|week|month|year)_(\d+)$/i.exec(f))) return m[2] + m[1].charAt(0).toUpperCase();
    // maturity buckets (Fed primary dealer statistics): tips_gt_6_years_lt_11_years -> TIPS 6-11Y, coupons_lte_2_years -> Coupons <=2Y
    f = f.replace(/_gt_(\d+)_years?_lt_(\d+)_years?/i, "_$1-$2Y").replace(/_gt_(\d+)_years?$/i, "_>$1Y")
      .replace(/_lte_(\d+)_years?$/i, "_<=$1Y").replace(/_lt_(\d+)_years?$/i, "_<$1Y");
    var s = f.replace(/([a-z])([A-Z])/g, "$1_$2").split(/_+/).filter(Boolean).map(function (t) {
      var lo = t.toLowerCase();
      if (WORDS[lo]) return WORDS[lo];
      if (/^\d+[dwmyq]$/.test(lo)) return lo.toUpperCase();
      return t.charAt(0).toUpperCase() + t.slice(1);
    }).join(" ");
    return s.replace(/^% (Change|Chg)\b/, "$1 %");
  }
  T.prettyField = pretty;

  /**
   * One scale and one number of decimals per column the source left unformatted: big amounts -> all in M, B or T;
   * otherwise 0, 2, 4 or 6 decimals by the column's typical size. Dates held as numbers and ids keep their digits.
   * A unit (percent, fraction, %) is never decided here: it comes from the source or from terminal/columns.py UNITS.
   * An undeclared column shows the number as delivered, so it cannot change unit with the day's data.
   */
  function settle(c, scan) {
    var d = c.def;
    if (c.type !== "number" || d.renderFn === "sparkline" || d.renderFn === "range" || d.renderFn === "heat") return;
    if (d.formatterFn || d.decimalPlaces != null || d.kind === "id" || NO_GROUP.test(c.field)) return;
    if (/id$/i.test(c.field) && !/(bid|mid|paid|valid|liquid|grid)$/i.test(c.field)) { c.def = Object.assign({}, d, { kind: "id" }); return; }   // eventid, portid
    if (/(^|_)(date|period|time|timestamp)$/i.test(c.field)) { c.def = Object.assign({}, d, { formatterFn: "none" }); return; }
    var vals = [], allInt = true;
    for (var i = 0; i < scan.length; i++) {
      var v = scan[i][c.field];
      if (typeof v !== "number" || !isFinite(v) || (scan[i]._fmt && scan[i]._fmt[c.field])) continue;
      vals.push(Math.abs(v)); if (!Number.isInteger(v)) allInt = false;
    }
    if (!vals.length) return;
    vals.sort(function (a, b) { return a - b; });
    var med = vals[Math.floor(vals.length / 2)], p90 = vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.9))];
    if (p90 >= 1e6 && med >= 1e5) {
      var u = p90 >= 1e12 ? [1e12, "T"] : p90 >= 1e9 ? [1e9, "B"] : [1e6, "M"];
      c.def = Object.assign({}, d, { scale: u[0], scaleUnit: u[1], decimalPlaces: 2 });
      return;
    }
    c.def = Object.assign({}, d, { decimalPlaces: allInt ? 0 : p90 >= 1 || p90 === 0 ? 2 : p90 >= 0.01 ? 4 : 6 });
  }
  T.settleColumn = settle;

  function typeOf(v) {
    if (typeof v === "number") return "number";
    if (typeof v === "boolean") return "boolean";
    if (typeof v === "string" && DATE_RE.test(v) && v.length <= 35) return "date";
    return "text";
  }

  /** Any JSON payload -> array of flat row objects. */
  T.normalizeRows = function (data) {
    if (data == null) return [];
    if (typeof data === "string") { try { data = JSON.parse(data); } catch (e) { return [{ value: data }]; } }
    if (Array.isArray(data)) {
      if (!data.length) return [];
      if (typeof data[0] !== "object" || data[0] === null) return data.map(function (v) { return { value: v }; });
      return data;
    }
    if (typeof data === "object") {
      var keys = Object.keys(data);
      // columnar {a:[..], b:[..]}
      if (keys.length && keys.every(function (k) { return Array.isArray(data[k]); }) && data[keys[0]].length) {
        var n = data[keys[0]].length, rows = [];
        if (keys.every(function (k) { return data[k].length === n; })) {
          for (var i = 0; i < n; i++) { var r = {}; keys.forEach(function (k) { r[k] = data[k][i]; }); rows.push(r); }
          return rows;
        }
      }
      return [data];
    }
    return [{ value: data }];
  };

  function buildColumns(rows, defs, spec) {
    var cols = [], seen = {}, H = (spec && spec.h) || {}, F = (spec && spec.f) || {};
    (defs || []).forEach(function (d) {
      if (!d || !d.field || seen[d.field]) return;
      seen[d.field] = 1;
      if (F[d.field]) {                       // build-time format overrides (terminal/columns.py): units, percent, no colour on levels
        d = Object.assign({}, d, F[d.field]);
        if (d.renderFn === null) delete d.renderFn;
      }
      cols.push({
        field: d.field, name: H[d.field] || d.headerName || pretty(d.field), tip: d.headerTooltip || "", def: d,
        type: d.cellDataType === "number" ? "number" : (d.cellDataType === "date" || d.cellDataType === "dateString") ? "date" : d.cellDataType === "boolean" ? "boolean" : "text",
        // showIfAny: a hidden column that appears only when some row fills it (a board's "Last trade" for stale rows)
        hide: !!d.hide && !(d.showIfAny && rows.some(function (r) { return r && r[d.field] != null && r[d.field] !== ""; })),
        pin: d.pinned === "left", sort: d.sort || null, fromDef: true
      });
    });
    var sample = rows.slice(0, 400);
    sample.forEach(function (r) {
      for (var k in r) if (!seen[k] && k.charAt(0) !== "_") { seen[k] = 1; cols.push({ field: k, name: H[k] || pretty(k), tip: "", def: Object.assign({ field: k }, F[k]), type: null, hide: false, pin: false }); }
    });
    // infer types + drop all-empty columns
    var scan = rows.length > 3000 ? rows.slice(0, 3000) : rows;
    cols = cols.filter(function (c) {
      // the type most cells have: a statement whose first row is "Reported Currency: CAD" is still a column of amounts,
      // so it gets one scale (review A3: TMX showed 320,000,000 beside 1.57B in one row)
      var has = false, t = null, other = null, nums = 0, others = 0;
      for (var i = 0; i < scan.length; i++) {
        var v = scan[i][c.field];
        if (v != null && v !== "") { has = true; var tv = typeOf(v); if (tv === "number") nums++; else { others++; if (!other) other = tv; } }
      }
      if (has) t = nums > others ? "number" : other;
      if (!c.type) c.type = t || "text";
      else if (has && c.type === "number" && t && t !== "number") c.type = t;
      return has;
    });
    cols.forEach(function (c) { c.def = Object.assign({ field: c.field }, c.def); settle(c, scan); });
    cols.sort(function (a, b) { return (b.pin ? 1 : 0) - (a.pin ? 1 : 0); });
    return cols;
  }

  // ids, links and keys say nothing in a list view; they stay available under "Columns" (and in the flipped record view)
  var ID_HIDE = /(^|_)(id|cik|cusip|isin|figi|lei|uuid|guid|key|url|link|hash|timezone|orderbook_id|sequence_number|mic_code|upi_fisn|unique_product_identifier)$/i;
  var EXPIRY = /(^|_)(expiration|expiry|expiration_date|maturity|maturity_date|delivery|settlement_date|contract_month)$/i;
  var DATEISH = /(date|timestamp)$|(^|_)(period|period_ending|time|month|quarter)$/i;
  /** The columns a list shows by default: preset (user pick, card, endpoint), else a generic choice without clutter. */
  function defaultColumns(baseCols, rows, opts, spec) {
    var avail = baseCols.filter(function (c) { return !c.hide; });
    if (!avail.length) avail = baseCols;
    var by = {}; baseCols.forEach(function (c) { by[c.field] = c; });
    function pick(list) { var out = []; (list || []).forEach(function (f) { if (by[f] && out.indexOf(by[f]) < 0) out.push(by[f]); }); return out; }
    if (opts.cols && opts.cols.length) { var u = pick(opts.cols); if (u.length) return u; }
    var want = opts.itemCols || (spec && spec.show);
    if (want) { var p = pick(want); if (p.length >= 2) return p; }
    if (opts.curated || rows.length <= 3 || rows.some(function (r) { return r && r._section; })) return avail;
    // the columns the source declared, unless they are only keys (period, fiscal year...) while the data has the rest
    var declared = avail.filter(function (c) { return c.fromDef; });
    var declaredData = declared.filter(function (c) { return !DATEISH.test(c.field) && !/^fiscal_|_(year|id)$|^(symbol|name)$/.test(c.field); });
    // rows sampled across the whole set, not the first 400: long panels (Fama-French, 21,654 rows) list one portfolio
    // for hundreds of rows, and "the same value on every row" then hid the Portfolio column (review F1)
    // (the first 200 rows as they come, plus every step-th after: a stride alone aliases on panels, e.g. every 4th row of
    // Richmond Fed's 4 adjustment x horizon rows per date is always the same pair)
    var step = Math.max(1, Math.ceil(rows.length / 200)), scan = rows.filter(function (r, k) { return k < 200 || k % step === 0; });
    var numeric = function (c) { return scan.some(function (r) { return r && typeof r[c.field] === "number"; }); };
    // declared keys only (Richmond Fed: date, adjustment, horizon) while the numbers come in undeclared columns: open on the data
    var keysOnly = !declared.some(numeric) && avail.filter(function (c) { return !c.fromDef && numeric(c); }).length >= 2;
    var pool = declared.length >= 3 && declaredData.length >= 2 && !keysOnly ? declared : avail;
    pool = pool.filter(function (c, i) {
      var isClick = c.def && c.def.renderFn === "cellOnClick";
      if (!isClick && ID_HIDE.test(c.field)) return false;
      var first = null, same = true, n = 0, len = 0;
      for (var k = 0; k < scan.length; k++) {
        var v = scan[k][c.field];
        if (v == null || v === "") continue;
        var s = typeof v === "object" ? JSON.stringify(v) : String(v);
        n++; len += s.length;
        if (first === null) first = s; else if (s !== first) same = false;
      }
      // the same value on every row (symbol, currency, provider); a unit column stays: it is what the numbers are
      if (!isClick && n >= 4 && same && n === scan.length && !/^units?$/i.test(c.field)) return false;
      if (!isClick && n && len / n > 120 && i > 1) return false;                // long text reads better in the flipped record view
      return true;
    });
    // years as columns (USDA farm income: 1910 ... 2026 across): the latest years first, after the labels (review F1)
    var yearCols = pool.filter(function (c) { return /^(1[89]|20)\d\d$/.test(c.field); });
    if (yearCols.length >= 5) {
      yearCols.sort(function (a, b) { return Number(b.field) - Number(a.field); });
      pool = pool.filter(function (c) { return yearCols.indexOf(c) < 0; }).concat(yearCols);
    }
    // a curve (1M ... 30Y across) keeps its whole term structure; other lists open with ten columns
    var tenors = pool.filter(function (c) { return /^\d+(-\d+)?[DWMY]\b/.test(c.name); }).length;
    var cap = tenors >= 5 ? 18 : 10;
    var chosen = (pool.length ? pool : avail).slice(0, cap);
    pool.forEach(function (c) {
      if (!(c.def && c.def.renderFn === "cellOnClick") || chosen.indexOf(c) >= 0) return;
      for (var k = chosen.length - 1; k >= 0 && chosen.length >= cap; k--) {
        if (!(chosen[k].def && chosen[k].def.renderFn === "cellOnClick")) { chosen.splice(k, 1); break; }
      }
      chosen.push(c);
    });
    // a panel (several rows per date: Richmond Fed by adjustment and horizon) keeps the short text columns that tell
    // those rows apart, even when the source marks them hidden; without them the rows read as duplicates (review F1)
    var lead = chosen[0];
    if (lead && DATEISH.test(lead.field)) {
      var seenD = {}, repeats = scan.some(function (r) { var k = String(r && r[lead.field]); if (seenD[k]) return true; seenD[k] = 1; return false; });
      if (repeats) {
        baseCols.forEach(function (c) {
          if (chosen.indexOf(c) >= 0) return;
          var vals = {}, n = 0, ok = true;
          scan.forEach(function (r) { var v = r && r[c.field]; if (v == null || v === "") return; if (typeof v !== "string" || v.length > 40) ok = false; vals[v] = 1; n++; });
          var k = Object.keys(vals).length;
          if (ok && n === scan.length && k >= 2 && k <= 12) chosen.splice(1, 0, c);
        });
        if (chosen.length > cap + 2) chosen = chosen.slice(0, cap + 2);
      }
    }
    return chosen;
  }

  // "2026 September" / "September 2026" / "Sep 2026": month periods sort by date, not alphabetically (review F2: USDA ERS
  // Ag Trade read 2026 September, May, March, June, July)
  var MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
  function monthKey(s) {
    var m = /^\s*(\d{4})\s+([A-Za-z]{3})[a-z]*\.?\s*$/.exec(s) || /^\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{4})\s*$/.exec(s);
    if (!m) return null;
    var y = /^\d/.test(m[1]) ? m[1] : m[2], mon = MONTHS[(/^\d/.test(m[1]) ? m[2] : m[1]).toLowerCase()];
    return mon ? Number(y) * 100 + mon : null;
  }
  T.monthKey = monthKey;
  // a cell sent as {value, footnote} (BLS CPI/PPI release labels: "Services less rent of shelter(1)" with its note) is
  // its value; the note goes in the tooltip (review F3: the table printed the raw JSON)
  function noted(v) { return v && typeof v === "object" && !Array.isArray(v) && "value" in v && Object.keys(v).length <= 3 && (v.value == null || typeof v.value !== "object"); }
  function plain(v) { return noted(v) ? v.value : v; }
  T.cellPlain = plain;
  function cmp(a, b) {
    a = plain(a); b = plain(b);
    if (a == null || a === "") return b == null || b === "" ? 0 : 1;
    if (b == null || b === "") return -1;
    if (typeof a === "number" && typeof b === "number") return a - b;
    var ka = typeof a === "string" ? monthKey(a) : null, kb = ka != null && typeof b === "string" ? monthKey(b) : null;
    if (ka != null && kb != null) return ka - kb;
    return String(a).localeCompare(String(b), undefined, { numeric: true });
  }

  /**
   * The unit a number is shown in, from its format: {key, factor}. Percent columns are "%" (fractions × 100, so a
   * fraction and a percent column compare and plot alike); a suffix names its unit ("bp", "pp", "USD/JPY"); "" = as
   * delivered. A row may carry its own unit for a column (a yield's change in bp on a board of returns): sorting,
   * exporting and charting compare numbers only within one unit.
   */
  function unitOf(d) {
    d = d || {};
    if (d.formatterFn === "normalizedPercent") return { key: "%", factor: 100 };
    if (d.formatterFn === "percent") return { key: "%", factor: 1 };
    return { key: String(d.suffix || "").trim(), factor: 1 };
  }
  T.unitOf = unitOf;
  function rowDef(r, field, base) { return r && r._fmt && r._fmt[field] ? Object.assign({}, base, r._fmt[field]) : base || {}; }
  /** A column's unit for these rows: the declared one, unless every number in it carries the same other unit (a board of yields: all bp). */
  function colUnit(def, field, rows) {
    var own = unitOf(def).key, other = null;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], v = r && r[field];
      if (typeof v !== "number" || !isFinite(v)) continue;
      var k = unitOf(rowDef(r, field, def)).key;
      if (k === own) return own;
      if (other === null) other = k; else if (other !== k) return own;
    }
    return other === null ? own : other;
  }
  /** " (%)" / " (bp)" for a header, unless the name already says it. */
  function unitTag(name, key) { return key && String(name).indexOf(key) < 0 ? " (" + key + ")" : ""; }

  var HEADER_KEYS = ["period_ending", "date", "fiscal_period", "symbol", "name", "country", "title"];

  /** Tiny inline line chart for a table cell. Direction is also in the adjacent return columns, so colour is not the only cue. */
  function sparkline(vals, bp, pctOnly) {
    var W = 84, H = 18, ns = "http://www.w3.org/2000/svg";
    var nums = vals.filter(function (v) { return typeof v === "number" && isFinite(v); });
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", W); svg.setAttribute("height", H); svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("preserveAspectRatio", "none");          // narrow cards draw it 56 px wide (app.css), same height
    if (nums.length < 2) return svg;
    var lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums), span = hi - lo || 1;
    var pts = nums.map(function (v, i) { return (i / (nums.length - 1) * (W - 2) + 1).toFixed(1) + "," + (H - 2 - (v - lo) / span * (H - 4)).toFixed(1); }).join(" ");
    var line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", pts); line.setAttribute("fill", "none"); line.setAttribute("stroke-width", "1.5"); line.setAttribute("vector-effect", "non-scaling-stroke");
    line.setAttribute("stroke", nums[nums.length - 1] >= nums[0] ? "#0ca30c" : "#e66767");
    svg.appendChild(line);
    var t = document.createElementNS(ns, "title");
    var pctDelta = nums[0] !== 0 ? ((nums[nums.length - 1] / nums[0] - 1) * 100).toFixed(1) + "%" : "–";
    t.textContent = pctOnly ? "3 months: " + pctDelta + " against the dollar" : "3 months: " + T.fmtNum(nums[0]) + " → " + T.fmtNum(nums[nums.length - 1]) + " (" + (bp ? ((nums[nums.length - 1] - nums[0]) * 100 >= 0 ? "+" : "") + Math.round((nums[nums.length - 1] - nums[0]) * 100) + " bp" : pctDelta) + ")";
    svg.appendChild(t);
    return svg;
  }

  /**
   * T.table(host, {data, colDefs, onAction(param, value) -> bool, onCount(text)})
   */
  /**
   * A unit the source states on the row itself: OECD sends a y/y change as a fraction with unit "Year-over-year (YOY)
   * percent change" and unit_multiplier 100 (France CPI 2025-12: 0.0079 = 0.79%), and an index level with multiplier 1,
   * on the same endpoint (review A5). Declared by the row, never guessed from the value.
   */
  T.rowUnits = function (r) {
    if (r && typeof r === "object" && r.unit_multiplier === 100 && typeof r.value === "number" && /percent|%/i.test(String(r.unit || "")))
      return Object.assign({}, r, { _fmt: Object.assign({}, r._fmt, { value: { formatterFn: "normalizedPercent", decimalPlaces: 2 } }) });
    return r;
  };

  T.table = function (host, opts) {
    var baseRows = T.normalizeRows(opts.data).map(T.rowUnits);
    var baseCols = buildColumns(baseRows, opts.colDefs, opts.spec);
    var state = { filter: "", sortField: null, sortDir: 0, transposed: false };
    var view = { rows: [], cols: [] };
    var wrap = el("div", { class: "tv" });
    var table = el("table"), thead = el("thead"), tbody = el("tbody");
    table.appendChild(thead); table.appendChild(tbody); wrap.appendChild(table);
    host.appendChild(wrap);
    var rendered = 0, CHUNK = 120;

    var hasSections = baseRows.some(function (r) { return r && r._section; });
    var defaultVis = defaultColumns(baseCols, baseRows, Object.assign({}, opts, { cols: null }), opts.spec);
    var visCols = defaultColumns(baseCols, baseRows, opts, opts.spec);
    function pinFirst(list) {
      list = list.filter(function (c) { return c.pin; }).concat(list.filter(function (c) { return !c.pin; }));
      // a wide list keeps its first column (name, ticker, date) in view while scrolling sideways
      if (list.length > 5 && !list[0].pin && list[0].type !== "number") list[0].pin = true;
      return list;
    }
    visCols = pinFirst(visCols);
    // _hl marks the subject row (highlighted); _pin marks summary rows (median...) that stay at the bottom whatever the sort
    var hasMarks = baseRows.some(function (r) { return r && (r._hl || r._pin); });
    var dataCount = baseRows.filter(function (r) { return !r._section && !r._pin; }).length;
    var special = visCols.some(function (c) { return /sparkline|heat|range/.test(String(c.def.renderFn)); });
    var canTranspose = !opts.noTranspose && !hasSections && !special && baseRows.length >= 1 && baseRows.length <= 60 && visCols.length >= 3;
    // Responsive boards (P4): step columns down across 3 width tiers so boards avoid horizontal scroll at 1440 px
    // while keeping the full column set in the column picker.
    var tier = opts.narrow === true ? 1 : Number(opts.narrow || 0);
    var NARROW_1 = /^(m3|symbol|currency|quote)$/;
    var NARROW_2 = /^(m3|symbol|currency|quote|range52|w1|m1)$/;
    var NARROW_3 = /^(m3|symbol|currency|quote|range52|w1|m1|spark|y1)$/;
    var chgRedundant = baseRows.every(function (r) { return r._section || r.chg == null || (typeof r.chg_pct === "number" && !(r._fmt && r._fmt.chg && r._fmt.chg.suffix)); });
    // a narrow board without a sparkline (three cards across at 1280 px with the sidebar open, about 300 px) sheds the
    // secondary columns instead of clipping YTD at the right edge; the column picker still has them. Only boards (a
    // w1 / m1 / range52 column): a quote, comps or fund table keeps its currency column at any width.
    var NARROW_PLAIN = /^(currency|w1|m1|m3|range52)$/;
    var isBoard = visCols.some(function (c) { return /^(w1|m1|range52)$/.test(c.field); });
    function shownCols() {
      if (tier >= 3 && isBoard && !(opts.cols && opts.cols.length) && !visCols.some(function (c) { return c.def.renderFn === "sparkline"; })) {
        var keep = visCols.filter(function (c) { return !NARROW_PLAIN.test(c.field); });
        return keep.length >= 3 ? keep : visCols;
      }
      if (!tier || (opts.cols && opts.cols.length) || !visCols.some(function (c) { return c.def.renderFn === "sparkline"; })) return visCols;
      var drop = tier >= 3 ? NARROW_3 : tier === 2 ? NARROW_2 : NARROW_1;
      var kept = visCols.filter(function (c) {
        if (tier >= 3 && c.field === "w1" && chgRedundant) return true;
        return !drop.test(c.field) && !(c.field === "chg" && chgRedundant);
      });
      return kept.length >= 3 ? kept : visCols;
    }
    // only a record (one to three rows) opens flipped; a schedule of 4+ rows reads down, as sent
    state.transposed = canTranspose && !hasMarks && baseRows.length <= 3 && visCols.length >= 6;
    if (opts.dense) wrap.classList.add("dense");

    function upcomingOnly(sc) {
      var today = T.marketDate ? T.marketDate() : new Date().toISOString().slice(0, 10);   // calendars list U.S. dates: New York's today
      var upcoming = baseRows.filter(function (r) { return String(r[sc.field] || "").slice(0, 10) >= today; });
      if (upcoming.length) { baseRows = upcoming; dataCount = baseRows.filter(function (r) { return !r._section && !r._pin; }).length; }
      else if (baseRows.length && opts.onNoUpcoming) {
        // nothing from today on: the past rows stay, but the card says the source has not moved on (never silently)
        var last = baseRows.map(function (r) { return String(r[sc.field] || "").slice(0, 10); }).filter(Boolean).sort().pop();
        if (last) opts.onNoUpcoming(last);
      }
    }
    var initSort = baseCols.filter(function (c) { return c.sort; })[0];
    if (initSort) { state.sortField = initSort.field; state.sortDir = initSort.sort === "desc" ? -1 : 1; }
    // a card may open sorted its own way ("date:asc") and, for calendars, from today on ("since": "today")
    if (opts.sort) {
      // "date:desc,holdings:desc": the second key orders rows that tie on the first (the newest month, largest first)
      var sorts = String(opts.sort).split(","), sp = sorts[0].split(":"), sf = sp[0];
      if (sorts[1]) {
        var tp = sorts[1].split(":"), tc = baseCols.filter(function (c) { return c.field === tp[0]; })[0];
        if (tc) state.then = { field: tc.field, dir: tp[1] === "desc" ? -1 : 1 };
      }
      var sc = baseCols.filter(function (c) { return c.field === sf; })[0]
        || (sf === "percent_change" ? baseCols.filter(function (c) { return c.field === "change_percent"; })[0] : null);
      if (sc) {
        state.sortField = sc.field; state.sortDir = sp[1] === "desc" ? -1 : 1;
        if (opts.since === "today" && sc.type === "date") upcomingOnly(sc);
      }
    } else if ((!initSort || opts.calendar) && !opts.curated && !hasSections && !hasMarks && baseRows.length > 1) {
      // dated lists open newest first; curves by expiry (nearest first); calendars from today on, soonest first
      var lead = visCols.slice(0, 4);
      // an expiry-like column is a curve only when most of its dates lie ahead AND the table doesn't already lead
      // with a trade/auction date column (FX Option Trades, Treasury Auctions open newest first; review F2)
      var leadDate = lead[0] && (lead[0].type === "date" || DATEISH.test(lead[0].field)) && !EXPIRY.test(lead[0].field);
      var today0 = T.marketDate ? T.marketDate() : new Date().toISOString().slice(0, 10);
      var ex = leadDate ? null : lead.filter(function (c) {
        if (!EXPIRY.test(c.field)) return false;
        var vals = baseRows.map(function (r) { return String(r[c.field] || "").slice(0, 10); }).filter(function (v) { return DATE_RE.test(v); });
        return !vals.length || vals.filter(function (v) { return v >= today0; }).length * 2 >= vals.length;
      })[0];
      var dc = ex || lead.filter(function (c) { return c.type === "date" || DATEISH.test(c.field); })[0];
      if (dc) {
        var asc = !!ex || !!opts.calendar;
        state.sortField = dc.field; state.sortDir = asc ? 1 : -1;
        if (opts.calendar && dc.type === "date") upcomingOnly(dc);
      }
    }

    function filtered() {
      var q = state.filter.trim().toLowerCase();
      if (!q) return baseRows;
      var terms = q.split(/\s+/);
      return baseRows.filter(function (r) {
        if (r._section) return false;
        if (r._pin) return true;
        var hay = "";
        for (var i = 0; i < visCols.length; i++) { var v = r[visCols[i].field]; if (v != null) hay += " " + (typeof v === "object" ? JSON.stringify(v) : v); }
        hay = hay.toLowerCase();
        return terms.every(function (t) { return hay.indexOf(t) >= 0; });
      });
    }

    function compute() {
      var rows = state.transposed && canTranspose ? baseRows : filtered();
      if (state.transposed && canTranspose) {
        var hk = null;
        for (var i = 0; i < HEADER_KEYS.length && !hk; i++) {
          var k = HEADER_KEYS[i];
          if (rows.length && rows.every(function (r) { return r[k] != null && r[k] !== ""; })) {
            var uniq = {}; rows.forEach(function (r) { uniq[r[k]] = 1; });
            if (Object.keys(uniq).length === rows.length) hk = k;
          }
        }
        var src = rows.slice();
        if (hk && src.length && DATE_RE.test(String(src[0][hk]))) src.sort(function (a, b) { return cmp(a[hk], b[hk]); });
        var cols = [{ field: "__f", name: hk ? pretty(hk) : "Field", type: "text", pin: true, def: {} }];
        src.forEach(function (r, j) {
          cols.push({ field: "c" + j, name: hk ? T.fmtDate(r[hk]) : (src.length > 1 ? "#" + (j + 1) : "Value"), type: "mixed", def: {}, perRow: true });
        });
        var out = [];
        visCols.forEach(function (c) {
          if (c.field === hk) return;
          var row = { __f: c.name, __col: c };
          var any = false;
          src.forEach(function (r, j) { row["c" + j] = r[c.field]; if (r[c.field] != null && r[c.field] !== "") any = true; });
          if (any) out.push(row);
        });
        var outTotal = out.length, q = state.filter.trim().toLowerCase();
        if (q) {
          var terms = q.split(/\s+/);
          out = out.filter(function (r) {
            var hay = String(r.__f || "");
            for (var j = 0; j < src.length; j++) { var rv = r["c" + j]; if (rv != null) hay += " " + (typeof rv === "object" ? JSON.stringify(rv) : rv); }
            hay = hay.toLowerCase();
            return terms.every(function (t) { return hay.indexOf(t) >= 0; });
          });
        }
        view = { rows: out, cols: cols, transposed: true, totalFields: outTotal };
      } else {
        var sorted = rows;
        visCols.forEach(function (c) {
          if (c.def.renderFn !== "heat") return;
          var vals = [];
          rows.forEach(function (r) { var v = r[c.field]; if (typeof v === "number" && isFinite(v) && !(r._fmt && r._fmt[c.field])) vals.push(Math.abs(v)); });
          vals.sort(function (a, b) { return a - b; });
          c.heatMax = vals.length ? vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.9))] || vals[vals.length - 1] : 0;   // 90th percentile, so one outlier does not wash out the rest
        });
        if (state.sortField && state.sortDir) {
          var f = state.sortField, d = state.sortDir;
          var sc = baseCols.filter(function (c) { return c.field === f; })[0], sdef = (sc && sc.def) || {}, skey = colUnit(sdef, f, rows);
          // rows in the column's own unit first, then each other unit as its own block (bp after %), empties last
          var keyed = rows.filter(function (r) { return !r._section && !r._pin; }).map(function (r) {
            var v = r[f], u = unitOf(rowDef(r, f, sdef)), num = typeof v === "number" && isFinite(v);
            return { r: r, empty: v == null || v === "", g: num && u.key !== skey ? "1" + u.key : "0", v: num ? v * u.factor : v };
          });
          sorted = keyed.sort(function (a, b) {
            if (a.empty || b.empty) return a.empty === b.empty ? 0 : a.empty ? 1 : -1;
            if (a.g !== b.g) return a.g < b.g ? -1 : 1;
            var t = state.then;
            return d * cmp(a.v, b.v) || (t && t.field !== f ? t.dir * cmp(a.r[t.field], b.r[t.field]) : 0);
          }).map(function (k) { return k.r; }).concat(rows.filter(function (r) { return r._pin; }));
        }
        view = { rows: sorted, cols: shownCols(), transposed: false };
      }
    }

    function cell(row, c) {
      var v = row[c.field];
      var colMeta = c.perRow ? row.__col : c;       // transposed: format by the original column
      var def = (colMeta && colMeta.def) || {};
      // a row may carry its own unit for a column (a yield row on a board shows changes in bp, not percent)
      var rowFmt = !c.perRow && row._fmt && row._fmt[c.field];
      if (rowFmt) def = Object.assign({}, def, rowFmt);
      var type = c.perRow ? (colMeta.type === "number" || typeof v === "number" ? "number" : colMeta.type) : c.type;
      var td = document.createElement("td");
      var cls = c.pin ? "pin" : "";
      if (v == null || v === "") { td.className = cls + " null"; td.textContent = c.field === "__f" ? "" : "–"; return td; }
      if (def.renderFn === "sparkline" && Array.isArray(v)) { td.className = cls + " spark"; td.appendChild(sparkline(v, row._fmt && row._fmt.spark && row._fmt.spark.bp, row._fmt && row._fmt.spark && row._fmt.spark.pct)); return td; }
      if (def.renderFn === "range" && typeof v === "number") {
        td.className = cls + " rng";
        td.title = row._rangeTip || "52-week low " + T.fmtNum(row.low52) + " · high " + T.fmtNum(row.high52) + " · position " + v + "%";
        td.appendChild(el("span", { class: "rng-track" }, el("i", { style: { left: Math.max(0, Math.min(100, v)) + "%" } })));
        return td;
      }
      if (def.renderFn === "heat" && typeof v === "number") {
        var mx = (rowFmt && rowFmt.heatScale) || (c.perRow ? 0 : c.heatMax) || Math.abs(v) || 1;
        var a = (0.10 + 0.55 * Math.min(1, Math.abs(v) / mx)).toFixed(2);
        td.className = cls + " n heat";
        td.style.background = v > 0 ? "rgba(12,163,12," + a + ")" : v < 0 ? "rgba(208,59,59," + a + ")" : "";
        td.textContent = (v > 0 ? "+" : "") + T.fmtNum(v, def);
        td.title = String(v);
        return td;
      }
      if (typeof v === "number") {
        cls += " n";
        td.textContent = (def.signed && v > 0 ? "+" : "") + T.fmtNum(v, def, view.transposed ? 1e6 : 1e9);
        if (def.paren && v < 0) cls += " neg";                  // statements: (1,234) in red
        td.title = String(v);
        var rf = def.renderFn;
        if (rf === "greenRed" || (Array.isArray(rf) && rf.indexOf("greenRed") >= 0)) cls += v > 0 ? " pos" : v < 0 ? " neg" : "";
        if (rf === "columnColor" && def.renderFnParams && def.renderFnParams.colorRules) {
          var rules = def.renderFnParams.colorRules;
          for (var i = 0; i < rules.length; i++) {
            var r = rules[i].range || {};
            if (rules[i].condition === "between" && v >= r.min && v <= r.max) { td.style.background = rules[i].color; td.style.color = "#fff"; break; }
          }
        }
      } else if (typeof v === "boolean") {
        td.textContent = v ? "Yes" : "No";
      } else if (noted(v)) {
        td.textContent = v.value == null ? "" : String(v.value);
        td.title = String(v.footnote || v.tooltip || v.note || "");
      } else if (typeof v === "object") {
        var js = JSON.stringify(v);
        td.textContent = js.length > 140 ? js.slice(0, 140) + "…" : js;
        td.title = js.slice(0, 2000);
      } else {
        var s = String(v);
        var url = T.safeUrl(s);
        var act = def.renderFn === "cellOnClick" && def.renderFnParams;
        if (act) {
          cls += " click";
          td.textContent = url ? "Open ↗" : s;
          td.title = s;
          td.addEventListener("click", function () {
            var p = act.groupByParamName || (act.groupBy && act.groupBy.paramName);
            var vf = act.groupBy && act.groupBy.valueField;
            var val = vf && !c.perRow ? row[vf] : v;
            var handled = p && opts.onAction ? opts.onAction(p, val) : false;
            if (!handled && url) window.open(url, "_blank", "noopener");
          });
        } else if (url) {
          var a = document.createElement("a");
          a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
          var tail = url.replace(/[?#].*$/, "").replace(/\/+$/, "").split("/").pop();
          a.textContent = (tail && tail.length < 40 ? tail : "link") + " ↗";
          a.title = url;
          td.appendChild(a);
        } else {
          var isDate = type === "date" || DATE_RE.test(s);
          s = isDate ? T.fmtDate(s) : s;
          if (s.length > 160) { td.title = s.slice(0, 3000); s = s.slice(0, 160) + "…"; }
          td.textContent = s;
          if (def.renderFn === "hoverCard" && def.renderFnParams && def.renderFnParams.hoverCard && def.renderFnParams.hoverCard.markdown && !c.perRow) {
            var tip = String(def.renderFnParams.hoverCard.markdown).replace(/\{(\w+)\}/g, function (_, k) { return row[k] == null ? "" : row[k]; }).trim();
            if (tip) td.title = tip;
          }
        }
      }
      td.className = cls;
      return td;
    }

    function appendChunk() {
      var end = Math.min(rendered + CHUNK, view.rows.length);
      var frag = document.createDocumentFragment();
      for (var i = rendered; i < end; i++) {
        var tr = document.createElement("tr");
        if (view.rows[i]._section) {
          tr.className = "sec";
          tr.appendChild(el("td", { colspan: view.cols.length }, el("span", null, view.rows[i]._section)));
        } else {
          if (view.rows[i]._hl) tr.className = "hl";
          else if (view.rows[i]._pin) tr.className = "pinrow";
          else if (view.rows[i]._em) tr.className = view.rows[i]._em;          // statements: "sub" (subtotal) or "tot" (total)
          for (var j = 0; j < view.cols.length; j++) tr.appendChild(cell(view.rows[i], view.cols[j]));
          if (view.rows[i]._ind && tr.firstChild) tr.firstChild.classList.add("ind");
        }
        frag.appendChild(tr);
      }
      tbody.appendChild(frag);
      rendered = end;
    }

    function render() {
      compute();
      thead.textContent = ""; tbody.textContent = ""; rendered = 0;
      var tr = document.createElement("tr");
      view.cols.forEach(function (c) {
        var th = el("th", { class: (c.pin ? "pin " : "") + (c.type === "number" || c.perRow ? "n" : ""), title: c.tip || c.name }, c.name);
        if (!view.transposed && !opts.noSort) {
          var activeSort = state.sortField === c.field && state.sortDir;
          th.setAttribute("aria-sort", activeSort ? (state.sortDir > 0 ? "ascending" : "descending") : "none");
          if (activeSort) th.appendChild(el("span", { class: "s" }, state.sortDir > 0 ? "▲" : "▼"));
          th.addEventListener("click", function () {
            state.then = null;                    // the owner's own sort replaces the card's two-key default
            var numOrDate = c.type === "number" || c.type === "date";
            if (state.sortField !== c.field) { state.sortField = c.field; state.sortDir = numOrDate ? -1 : 1; }
            else state.sortDir = numOrDate
              ? (state.sortDir === -1 ? 1 : state.sortDir === 1 ? 0 : -1)
              : (state.sortDir === 1 ? -1 : state.sortDir === -1 ? 0 : 1);
            if (state.sortDir === 0) state.sortField = null;
            render();
          });
        }
        tr.appendChild(th);
      });
      thead.appendChild(tr);
      appendChunk();
      wrap.scrollTop = 0;
      if (opts.onCount) {
        var total = dataCount, shown = view.transposed ? view.rows.length : view.rows.filter(function (r) { return !r._section && !r._pin; }).length;
        var nAll = baseCols.length, asOf = "";
        // as-of: the latest date in the list's date column (the footer's "updated" is only the fetch time)
        var dcol = visCols.filter(function (c) { return c.type === "date" && !EXPIRY.test(c.field) && !opts.calendar; })[0];
        if (dcol && total > 1) baseRows.forEach(function (r) { var v = String(r[dcol.field] || "").slice(0, 10); if (DATE_RE.test(v) && v > asOf) asOf = v; });
        var countText = view.transposed && state.filter.trim()
          ? nf(0, 0, true).format(shown) + " of " + nf(0, 0, true).format(view.totalFields) + " fields"
          : (state.filter ? nf(0, 0, true).format(shown) + " of " : "") + nf(0, 0, true).format(total) + (total === 1 ? " row" : " rows");
        opts.onCount((asOf ? "latest " + asOf + " · " : "") + countText + " · " +
          (visCols.length < nAll ? visCols.length + " of " + nAll + " columns" : visCols.length + " columns"));
      }
    }

    wrap.addEventListener("scroll", function () {
      if (rendered < view.rows.length && wrap.scrollTop + wrap.clientHeight > wrap.scrollHeight - 400) appendChunk();
    });

    render();

    return {
      el: wrap,
      baseRows: baseRows,
      cols: baseCols,
      visCols: visCols,
      /** Columns shown now / by default, and a new set (null: back to the default). */
      shownFields: function () { return visCols.map(function (c) { return c.field; }); },
      defaultFields: function () { return defaultVis.map(function (c) { return c.field; }); },
      setColumns: function (fields) {
        var by = {}; baseCols.forEach(function (c) { by[c.field] = c; });
        var next = (fields || []).map(function (f) { return by[f]; }).filter(Boolean);
        visCols = pinFirst(next.length ? next : defaultVis);
        this.visCols = visCols;
        render();
      },
      canTranspose: canTranspose,
      shownNow: function () { compute(); return view.cols.map(function (c) { return c.field; }); },
      setNarrow: function (v) { var t = v === true ? 1 : Number(v || 0); if (tier === t) return; tier = t; render(); },
      isTransposed: function () { return state.transposed && canTranspose; },
      setTranspose: function (b) { state.transposed = !!b; render(); },
      setFilter: function (q) { state.filter = q || ""; render(); },
      filteredRows: filtered,
      /** The table as shown (filter, sort, flip), raw values; sep "\t" gives text that pastes into Excel as cells. */
      toCSV: function (sep) {
        compute();
        sep = sep || ",";
        var q = function (v) { return T.csvCell(v, sep); };
        // numbers as shown (percent columns in percent), without float noise; sparkline columns are left out. The header
        // names the column's unit; a cell in another unit than its column (a yield's bp on a board of returns) is
        // written with its unit ("12 bp"), so no number can be read in the wrong unit.
        var cols = view.cols.filter(function (c) { return c.def.renderFn !== "sparkline"; });
        var units = {};
        cols.forEach(function (c) { if (!c.perRow) units[c.field] = colUnit(c.def, c.field, view.rows); });
        function val(r, c) {
          var v = r[c.field], cd = ((c.perRow ? r.__col : c) || {}).def || {}, d = cd;
          if (c.field === "__f" && r.__col) return v + unitTag(v, unitOf(r.__col.def).key);  // flipped table: the unit goes with the row name
          if (!c.perRow) d = rowDef(r, c.field, cd);
          if (c.perRow && d.renderFn === "sparkline") return "";
          if (typeof v === "number" && isFinite(v)) {
            var u = unitOf(d);
            if (u.key !== (c.perRow ? unitOf(cd).key : units[c.field])) return T.fmtNum(v, Object.assign({}, d, { paren: false }));
            v = v * u.factor;
            return Math.abs(v) >= 1e15 ? v : +v.toFixed(6);
          }
          return Array.isArray(v) ? v.join(" ") : v;
        }
        var lines = [cols.map(function (c) { return q(c.perRow ? c.name : c.name + unitTag(c.name, units[c.field])); }).join(sep)];
        view.rows.forEach(function (r) { if (!r._section) lines.push(cols.map(function (c) { return q(val(r, c)); }).join(sep)); });
        return lines.join("\r\n");
      }
    };
  };

  // ---------- charts ----------
  T.PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
  var INK = { text: "#f0efec", text2: "#c3c2b7", muted: "#898781", grid: "#2c2c2a", axis: "#383835", surface: "#1a1a19" };

  function baseLayout(multi) {
    return {
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', size: 11, color: INK.muted },
      margin: { l: 52, r: 14, t: multi ? 26 : 10, b: 34 },
      colorway: T.PALETTE,
      hovermode: "x unified",
      hoverlabel: { bgcolor: "#222220", bordercolor: INK.axis, font: { color: INK.text, size: 12 } },
      showlegend: multi,
      legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { color: INK.text2, size: 11 }, bgcolor: "rgba(0,0,0,0)" },
      xaxis: { showgrid: false, linecolor: INK.axis, tickcolor: INK.axis, zeroline: false, automargin: true },
      yaxis: { gridcolor: INK.grid, gridwidth: 1, zerolinecolor: INK.axis, linecolor: "rgba(0,0,0,0)", automargin: true }
    };
  }

  T.plot = function (node, data, layout) {
    if (!window.Plotly) { node.textContent = "Chart library failed to load."; return; }
    return window.Plotly.react(node, data, layout, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] });
  };

  /** Render a Plotly figure produced by the backend, re-skinned for this surface. */
  T.plotFigure = function (node, fig) {
    if (typeof fig === "string") { try { fig = JSON.parse(fig); } catch (e) { node.textContent = "Invalid chart payload."; return; } }
    if (!fig || !Array.isArray(fig.data) || !fig.data.length) { node.textContent = "No chart data returned for these settings."; return; }
    var layout = Object.assign({}, fig.layout || {});
    layout.paper_bgcolor = "rgba(0,0,0,0)";
    layout.plot_bgcolor = "rgba(0,0,0,0)";
    layout.font = Object.assign({}, layout.font, { color: INK.text2 });
    layout.autosize = true;
    delete layout.width; delete layout.height;
    // 3D figures (implied-volatility surfaces) came with the light template's white scene walls (review G1)
    if (layout.scene || fig.data.some(function (t) { return /3d|surface|mesh|cone|isosurface|volume/.test(String(t.type || "")); })) {
      var ax3 = function (a) {
        return Object.assign({}, a, { backgroundcolor: INK.surface, showbackground: true, gridcolor: INK.axis, zerolinecolor: INK.axis, linecolor: INK.axis,
          color: INK.text2, tickfont: Object.assign({}, (a || {}).tickfont, { color: INK.text2 }) });
      };
      var sc = layout.scene || {};
      layout.scene = Object.assign({}, sc, { bgcolor: "rgba(0,0,0,0)", xaxis: ax3(sc.xaxis), yaxis: ax3(sc.yaxis), zaxis: ax3(sc.zaxis) });
      fig.data.forEach(function (t) { if (t.colorbar) t.colorbar = Object.assign({}, t.colorbar, { tickfont: { color: INK.text2 }, outlinecolor: INK.axis }); });
    }
    // one hover for all series, with a crosshair, on every time-series chart (legend clicks toggle series: Plotly's default)
    var xs = (fig.data[0] && fig.data[0].x) || [];
    if (xs.length && DATE_RE.test(String(xs[0])) && fig.data[0].type !== "bar") {
      layout.xaxis = Object.assign({}, layout.xaxis, { showspikes: true, spikemode: "across", spikesnap: "cursor", spikethickness: 1, spikedash: "dot", spikecolor: INK.muted });
      layout.yaxis = Object.assign({}, layout.yaxis, { showspikes: true, spikemode: "across", spikesnap: "cursor", spikethickness: 1, spikedash: "dot", spikecolor: INK.axis });
      if (!layout.hovermode || layout.hovermode === "closest") layout.hovermode = layout.hovermode || "x unified";
      layout.spikedistance = -1;
    }
    return T.plot(node, fig.data, layout);
  };

  /** The data behind a rendered Plotly chart as CSV: one row per x value, one column per series. */
  T.figureCSV = function (gd, sep) {
    sep = sep || ",";
    var cols = [], rows = {}, order = [];
    function put(key, col, v) {
      if (!(key in rows)) { rows[key] = {}; order.push(key); }
      if (cols.indexOf(col) < 0) cols.push(col);
      rows[key][col] = v;
    }
    (gd && gd.data || []).forEach(function (t, i) {
      var name = String(t.name || "Series " + (i + 1)).replace(/\s{2,}.*$/, "");      // legends carry the last value after two spaces
      if (t.labels && t.values) { t.labels.forEach(function (l, j) { put(l, name, t.values[j]); }); return; }
      var horiz = t.orientation === "h", keys = horiz ? t.y : t.x;
      var fields = t.type === "candlestick" || t.type === "ohlc" ? ["open", "high", "low", "close"] : [horiz ? "x" : "y"];
      fields.forEach(function (f) {
        var vals = t[f] || [];
        for (var j = 0; j < vals.length; j++) put(keys ? keys[j] : j, fields.length > 1 ? name + " " + f : name, vals[j]);
      });
    });
    var q = function (v) { return T.csvCell(v, sep); };
    var dated = order.length && order.every(function (k) { return DATE_RE.test(String(k)); });
    if (dated) order.sort();
    var lines = [[dated ? "date" : "x"].concat(cols).map(q).join(sep)];
    order.forEach(function (k) { lines.push([k].concat(cols.map(function (c) { return rows[k][c]; })).map(q).join(sep)); });
    return lines.join("\r\n");
  };
  /** Save a rendered chart as a PNG on the dark card background, with the card title on top. */
  T.exportPNG = function (gd, title, file, sourceLine) {
    if (!window.Plotly || !gd || !gd.data) return;
    var lay = JSON.parse(JSON.stringify(gd.layout || {}));
    lay.paper_bgcolor = INK.surface; lay.plot_bgcolor = INK.surface;
    lay.margin = Object.assign({ l: 52, r: 16, b: 34, t: 10 }, lay.margin);
    lay.margin.t = (lay.margin.t || 10) + 36;
    lay.title = { text: T.esc(title || ""), x: 0.01, xanchor: "left", y: 0.985, yanchor: "top", font: { color: INK.text, size: 15 } };
    if (sourceLine) {
      // the source goes under the chart, below whatever the tick labels took (the drawn bottom margin), so it is never cut off
      var drawnB = (gd._fullLayout && gd._fullLayout._size && gd._fullLayout._size.b) || lay.margin.b || 34;
      lay.margin.b = drawnB + 26;
      lay.xaxis = Object.assign({}, lay.xaxis, { automargin: false });
      lay.annotations = (lay.annotations || []).concat([{ text: T.esc(sourceLine), xref: "paper", yref: "paper", x: 0, y: 0, xanchor: "left", yanchor: "top", yshift: -(drawnB + 4),
        showarrow: false, align: "left", font: { color: INK.muted, size: 11 } }]);
    }
    var w = Math.max(gd.offsetWidth || 0, 900), h = Math.max((gd.offsetHeight || 0) + 36 + (sourceLine ? 26 : 0), 480);
    lay.width = w; lay.height = h;
    window.Plotly.toImage({ data: JSON.parse(JSON.stringify(gd.data)), layout: lay }, { format: "png", width: w, height: h, scale: 2 })
      .then(function (url) { T.downloadURL(file, url); T.toast("Chart saved as " + file); })
      .catch(function (e) { T.toast("Could not save the chart: " + (e && e.message || e)); });
  };

  function uniq(rows, f, cap) {
    var s = {}, out = [];
    for (var i = 0; i < rows.length; i++) { var v = rows[i][f]; if (v != null && !s[v]) { s[v] = 1; out.push(v); if (out.length > cap) break; } }
    return out;
  }

  T.guessChart = function (cols, rows) {
    var vis = cols.filter(function (c) { return !c.hide; });
    var dates = vis.filter(function (c) { return c.type === "date"; });
    var texts = vis.filter(function (c) { return c.type === "text"; });
    var nums = vis.filter(function (c) { return c.type === "number" && !NO_GROUP.test(c.field) && !/index$/.test(c.field); });
    var x = dates[0] || texts[0] || null;
    var pref = ["close", "value", "rate", "last_price", "price", "yield", "total"];
    var y = null;
    pref.some(function (p) { y = nums.filter(function (c) { return c.field === p; })[0]; return !!y; });
    y = y || nums[0] || null;
    var split = null;
    ["symbol", "country", "series_id", "maturity", "name", "title", "label"].some(function (k) {
      var c = texts.filter(function (t) { return t.field === k && (!x || t.field !== x.field); })[0];
      if (!c) return false;
      var n = uniq(rows, k, 40).length;
      if (n >= 2 && n <= 40 && n < rows.length) { split = c; return true; }
      return false;
    });
    return { x: x ? x.field : "", ys: y ? [y.field] : [], split: split ? split.field : "", type: x && x.type === "date" ? "line" : "bar" };
  };

  /**
   * What a quick chart draws: {series: [{name, x, y, yaxis}], units, msg}. Every value is in the unit it is shown in
   * (fraction columns × 100, so a fraction and a percent column share one percent axis). A row that carries its own
   * unit for a column (a yield's bp on a board of returns) is left out of that line and counted in the note. The first
   * unit gets the left axis, a second the right one; a column in a third unit is left out, named in the note.
   */
  T.chartPlan = function (cols, rows, cfg) {
    var xc = cols.filter(function (c) { return c.field === cfg.x; })[0];
    if (xc) rows = rows.slice().sort(function (a, b) { return cmp(a[cfg.x], b[cfg.x]); });
    var series = [], units = [], notes = [], dropped = {};
    function colOf(f) { return cols.filter(function (c) { return c.field === f; })[0]; }
    function trace(name, rs, yf, tagUnit) {
      var c = colOf(yf), cdef = (c && c.def) || {}, cu = { key: colUnit(cdef, yf, rows) }, ax = units.indexOf(cu.key);
      if (ax < 0) {
        if (units.length >= 2) { notes.push((c ? c.name : yf) + " left out: a third unit on one chart"); return; }
        units.push(cu.key); ax = units.length - 1;
      }
      var y = rs.map(function (r) {
        var v = r[yf];
        if (typeof v !== "number" || !isFinite(v)) return null;
        var u = unitOf(rowDef(r, yf, cdef));
        if (u.key !== cu.key) { (dropped[yf] = dropped[yf] || { n: 0, units: {} }).n++; dropped[yf].units[u.key || "another unit"] = 1; return null; }
        return v * u.factor;
      });
      series.push({ name: tagUnit ? name + unitTag(name, cu.key) : name, x: rs.map(function (r, i) { return xc ? T.toMtl(r[cfg.x]) : i + 1; }), y: y, yaxis: ax ? "y2" : "y" });
    }
    if (cfg.split) {
      var keys = uniq(rows, cfg.split, 400);
      if (keys.length > 8) {
        // the 8 largest series (sum of |Y|), not the first 8 met: generation by fuel kept storage types and lost wind and solar
        var size = {};
        rows.forEach(function (r) { var v = r[cfg.ys[0]]; if (typeof v === "number" && isFinite(v)) size[r[cfg.split]] = (size[r[cfg.split]] || 0) + Math.abs(v); });
        var big = keys.slice().sort(function (a, b) { return (size[b] || 0) - (size[a] || 0); }).slice(0, 8);
        notes.push("Showing the 8 largest of " + keys.length + " series (filter the table to choose others).");
        keys = keys.filter(function (k) { return big.indexOf(k) >= 0; });
      }
      if (cfg.ys.length > 1) notes.push("Split uses the first Y column only.");
      keys.forEach(function (k) { trace(String(k), rows.filter(function (r) { return r[cfg.split] === k; }), cfg.ys[0], false); });
    } else {
      cfg.ys.forEach(function (yf) { var c = colOf(yf); trace(c ? c.name : yf, rows, yf, true); });
    }
    Object.keys(dropped).forEach(function (f) {
      var c = colOf(f), n = dropped[f].n;
      notes.push(n + (n === 1 ? " row" : " rows") + " left out of " + (c ? c.name : f) + ": in " + Object.keys(dropped[f].units).join(", ") + ", not " + (colUnit((c && c.def) || {}, f, rows) || "the column's unit") + ".");
    });
    return { series: series, units: units, msg: notes.join(" ") };
  };

  /** Quick chart built from table rows. cfg = {x, ys[], split, type}. Returns {redraw, note}. */
  T.quickChart = function (host, tv, cfg, onChange) {
    var cols = tv.cols.filter(function (c) { return !c.hide; });
    var bar = el("div", { class: "chart-cfg" });
    var plot = el("div", { class: "plot" });
    var note = el("span", { class: "muted small" });
    host.appendChild(bar); host.appendChild(plot);

    function sel(label, value, options, fn) {
      var s = el("select", { onchange: function () { fn(s.value); } });
      options.forEach(function (o) { var op = el("option", { value: o[0] }, o[1]); if (o[0] === value) op.selected = true; s.appendChild(op); });
      bar.appendChild(el("label", null, label)); bar.appendChild(s);
    }
    function build() {
      bar.textContent = "";
      sel("X", cfg.x, [["", "(row)"]].concat(cols.map(function (c) { return [c.field, c.name]; })), function (v) { cfg.x = v; changed(); });
      var numOpts = cols.filter(function (c) { return c.type === "number"; }).map(function (c) { return { value: c.field, label: c.name }; });
      bar.appendChild(el("label", null, "Y"));
      bar.appendChild(T.dropdown({
        value: cfg.ys, options: numOpts, multi: true, placeholder: "choose…",
        onChange: function (v) { cfg.ys = (v || []).slice(0, 8); changed(); }
      }));
      sel("Split by", cfg.split, [["", "(none)"]].concat(cols.filter(function (c) { return c.type === "text"; }).map(function (c) { return [c.field, c.name]; })), function (v) { cfg.split = v; changed(); });
      sel("Type", cfg.type, [["line", "Line"], ["step", "Step"], ["bar", "Bar"], ["area", "Area"], ["scatter", "Dots"]], function (v) { cfg.type = v; changed(); });
      bar.appendChild(note);
    }
    function changed() { if (onChange) onChange(cfg); draw(); }

    function draw() {
      var xc = cols.filter(function (c) { return c.field === cfg.x; })[0];
      if (!cfg.ys.length) { note.textContent = "Pick at least one numeric column for Y."; window.Plotly && window.Plotly.purge(plot); return; }
      var plan = T.chartPlan(cols, tv.filteredRows(), cfg), msg = plan.msg;
      var series = plan.series.map(function (p) {
        var t = { name: p.name, x: p.x, y: p.y };
        if (p.yaxis === "y2") t.yaxis = "y2";
        if (cfg.type === "bar") { t.type = "bar"; }
        else if (cfg.type === "scatter") { t.type = "scatter"; t.mode = "markers"; t.marker = { size: 8 }; }
        else { t.type = "scatter"; t.mode = "lines"; t.line = { width: 2 }; if (cfg.type === "area") { t.fill = "tozeroy"; t.fillcolor = undefined; } t.connectgaps = true; }
        if (cfg.type === "step") { t.line.shape = "hv"; t.mode = "lines+markers"; t.marker = { size: 5 }; }   // a policy path holds between meetings
        return t;
      });
      series.forEach(function (s, i) {
        var col = T.PALETTE[i % T.PALETTE.length];
        if (s.type === "bar") { s.marker = { color: col, line: { color: INK.surface, width: 1 } }; if (plan.units.length > 1) s.offsetgroup = String(i); }   // side by side across both axes
        else if (s.mode === "markers") s.marker = { size: 8, color: col, line: { color: INK.surface, width: 2 } };
        else { s.line.color = col; if (s.fill) s.fillcolor = col + "22"; }
      });
      var layout = baseLayout(series.length > 1);
      // one axis per unit (plan.units: left, then right); values are already in the unit shown. SI suffixes only for
      // big plain numbers; percent axes read "%", other units carry their suffix ("bp")
      plan.units.forEach(function (u, i) {
        var peak = 0;
        series.forEach(function (s) { if ((s.yaxis === "y2") === (i === 1)) s.y.forEach(function (v) { if (typeof v === "number" && isFinite(v)) peak = Math.max(peak, Math.abs(v)); }); });
        var ax = i ? (layout.yaxis2 = Object.assign({}, layout.yaxis, { overlaying: "y", side: "right", showgrid: false, zeroline: false })) : layout.yaxis;
        if (u === "%") { ax.tickformat = ",.2f"; ax.ticksuffix = "%"; }
        else { ax.tickformat = peak >= 1e5 ? "~s" : peak >= 100 ? ",.0f" : ""; if (u) ax.ticksuffix = " " + u; }
      });
      // a numeric x (tenor in years, unemployment rate) keeps its spacing; text x is a category in row order
      if (xc && xc.type !== "date") { layout.xaxis.type = xc.type === "number" ? "linear" : "category"; layout.hovermode = "closest"; }
      if (cfg.type === "bar") { layout.bargap = 0.25; layout.barmode = "group"; }
      if (cfg.type === "scatter") layout.hovermode = "closest";
      note.textContent = msg;
      T.plot(plot, series, layout);
    }

    build(); draw();
    return { redraw: draw, plot: plot };
  };
})(window.T);
