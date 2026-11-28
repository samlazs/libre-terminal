/* Widget cards: parameters, data loading and per-type rendering. */
(function (T) {
  "use strict";
  var el = T.el;
  var dataCache = new Map();   // url -> {t, json}
  var optCache = new Map();    // url -> Promise<options>
  var TTL = 120000, MAX_INLINE_PARAMS = 5;

  function pick(obj, path) {
    if (!path) return obj;
    return path.split(".").reduce(function (o, k) { return o == null ? o : o[k]; }, obj);
  }
  function empty(v) { return v == null || v === "" || (Array.isArray(v) && !v.length); }
  function qs(params) {
    var u = new URLSearchParams();
    Object.keys(params).forEach(function (k) { var v = params[k]; if (!empty(v)) u.set(k, Array.isArray(v) ? v.join(",") : String(v)); });
    var s = u.toString();
    return s ? "?" + s : "";
  }
  function pretty(s) { return String(s).replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }); }

  /**
   * Relative dates in dashboard defaults, resolved when the card loads (the layout is built once per start, the page
   * may stay open for days): "@days:-90" = 90 days ago, "@bday:-1" = the last business day before today. "Today" is
   * New York's date, not the PC's: every source behind these defaults (Treasury, CFTC, Nasdaq calendars) is U.S.
   */
  function iso(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  var NY_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });
  T.marketDate = function (now) {
    var p = {};
    NY_DAY.formatToParts(now ? new Date(now) : new Date()).forEach(function (x) { p[x.type] = x.value; });
    return p.year + "-" + p.month + "-" + p.day;
  };
  T.resolveParam = function (v, now) {
    if (typeof v !== "string" || v.charAt(0) !== "@") return v;
    var m = /^@(days|bday):(-?\d+)$/.exec(v);
    if (!m) return v;
    var d = new Date(T.marketDate(now) + "T00:00:00Z"), n = Number(m[2]);      // calendar arithmetic in UTC: no DST hour can shift the day
    if (m[1] === "days") d.setUTCDate(d.getUTCDate() + n);
    else {
      var step = n < 0 ? -1 : 1, left = Math.abs(n);
      while (left > 0) { d.setUTCDate(d.getUTCDate() + step); if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) left--; }
    }
    return d.toISOString().slice(0, 10);
  };
  T.isoDate = iso;

  /** Key-looking values out of any text (the backends redact too; this is the last line of defence). */
  var SECRET_RX = /((?:^|[^a-z0-9])["']?(?:[a-z0-9]+[_-])*(?:api[_-]?key|apikey|api[_-]?token|access[_-]?token|auth[_-]?token|app[_-]?token|token|key|secret|client[_-]?secret|password|passwd|pwd)["']?\s*[=:]\s*["']?)(?!REDACTED)[^\s&"',;}\]<>)]{3,}/gi;
  T.redact = function (s) { return String(s == null ? "" : s).replace(SECRET_RX, "$1REDACTED"); };

  function errText(status, body) {
    var d = body;
    try { var j = JSON.parse(body); d = j.error && typeof j.error.message === "string" ? j.error.message : j.detail != null ? j.detail : j; } catch (e) { /* plain text */ }
    if (Array.isArray(d)) d = d.map(function (x) { return (x.loc ? x.loc.slice(1).join(".") + ": " : "") + (x.msg || JSON.stringify(x)); }).join("; ");
    else if (typeof d === "object" && d) d = d.loc && d.msg ? d.loc.slice(1).join(".") + ": " + d.msg : JSON.stringify(d);
    d = T.redact(String(d || "HTTP " + status)).replace(/Unexpected Error -> /g, "").replace(/\s*For further information visit \S+/g, "");
    return d.length > 700 ? d.slice(0, 700) + "…" : d;
  }

  /**
   * One classifier for every card error (also used by later views): what went wrong, in plain words, without URLs,
   * Python or JavaScript text. Returns {kind: "info" (expected / intentional) | "err" (needs attention), text, hint, cls}.
   *   cls: key | paid | input | covered | ratelimit | slow | upstream | backend | bug
   * ctx: {source: "FMP", provider: "fmp", symbol: "SAP.DE", param: name -> label}
   */
  var PAID_ONLY = { intrinio: "Intrinio", benzinga: "Benzinga", polygon: "Polygon", tiingo: "Tiingo", nasdaq: "Nasdaq Data Link", tradingeconomics: "Trading Economics", tradier: "Tradier" };
  var KEY_NAMES = { fred: "FRED", bls: "BLS", eia: "EIA", congress_gov: "Congress.gov", fmp: "FMP", polygon: "Polygon", tiingo: "Tiingo", intrinio: "Intrinio", benzinga: "Benzinga",
    alpha_vantage: "Alpha Vantage", tradier: "Tradier", biztoc: "BizToc", tradingeconomics: "Trading Economics", nasdaq: "Nasdaq Data Link" };
  T.errorRef = function (s) {           // short stable id for "our bug" messages (same text, same id)
    var h = 5381; s = String(s); for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
    return h.toString(36).slice(-6);
  };
  T.classifyError = function (msg, status, ctx) {
    ctx = ctx || {};
    var m = String(msg || ""), src = ctx.source || "The source";
    var keyHit = /Missing credential '([\w]+)'/.exec(m) || (/^(\w+) rejected the request: Missing or invalid credentials/.exec(m) ? [0, RegExp.$1.toLowerCase() + "_api_key"] : null)
      || (/(?:api[_ ]?key|token) (?:is )?(?:required|missing|not set)|No API key/i.test(m) && ctx.provider && (KEY_NAMES[ctx.provider] || PAID_ONLY[ctx.provider]) ? [0, ctx.provider + "_api_key"] : null);
    if (keyHit) {
      var prov = keyHit[1].replace(/_(api_key|token|app_token|api_token)$/, "");
      var nm = KEY_NAMES[prov] || prov.replace(/_/g, " ").toUpperCase();
      var tabNote = ctx.hasFreeOnTab ? " Other cards on this tab work without one." : "";
      if (PAID_ONLY[prov]) return { cls: "key", kind: "info", text: "Needs a paid " + PAID_ONLY[prov] + " subscription.", hint: "This card stays empty unless you have a " + PAID_ONLY[prov] + " key (add it with Setup-Keys.cmd or setup-keys.sh, then restart)." + tabNote };
      // only mention other cards on the tab when another keyless card actually exists on it (review E1)
      return { cls: "key", kind: "info", text: "Needs the free " + nm + " API key.", hint: "Add " + keyHit[1] + " with Setup-Keys.cmd (or setup-keys.sh), then stop and start the terminal." + tabNote };
    }
    if (/\b402\b|Payment Required|Premium Query Parameter|Restricted Endpoint|Special Endpoint|Legacy Endpoint|current subscription|upgrade your plan|not available under your (current )?(plan|subscription)/i.test(m))
      return { cls: "paid", kind: "info", text: "Needs a paid " + src + " plan.", hint: "The free " + src + " plan does not include this data." + (ctx.hasFreeOnTab ? " Other cards on this tab work without a paid plan." : "") };
    if (/\b429\b|Too Many Requests|rate.?limit|exceeded your rate|temporarily blocked/i.test(m)) {
      var secs = /~(\d+)\s*s\b/.exec(m), wait;
      if (secs) { secs = +secs[1]; wait = secs < 90 ? secs + " seconds" : Math.ceil(secs / 60) + " minutes"; }
      else wait = (/OECD/i.test(m + src) ? 10 : 2) + " minutes";
      return { cls: "ratelimit", kind: "info", text: src + " is limiting how often it can be asked right now.", hint: "Try again in about " + wait + " (press ↻)." };
    }
    if (/\b40[13]\b|Unauthorized|Forbidden|Invalid API key|invalid credentials|Access denied/i.test(m))
      return { cls: "denied", kind: "info", text: src + " refused the request (access denied).", hint: ctx.provider && KEY_NAMES[ctx.provider] ? "Check the " + KEY_NAMES[ctx.provider] + " API key in local/api_keys.env (Setup-Keys.cmd), then restart." : "The source blocks this kind of request at the moment. Try again later." };
    var req = /^(?:Error: )?(\w[\w ]*?) is a required field/i.exec(m);
    if (req) return { cls: "input", kind: "info", text: "Enter " + req[1] + " to load this card.", hint: null, param: null };
    if (/Invalid URL\..*HTM or HTML/i.test(m))
      return { cls: "input", kind: "info", text: "Open HTML only reads .htm/.html filings.", hint: "This filing is XML or another format — view it in Filing Viewer or pick a .htm document in Filing Headers.", param: "url" };
    if (/\b[45]\d\d (Client|Server) Error|HTTP Error [45]\d\d|status code [45]\d\d/i.test(m) && !/\b40[13]\b|Unauthorized|Forbidden/i.test(m))
      return { cls: "upstream", kind: "info", text: src + " is not answering properly right now (its server returned an error).", hint: "The problem is at the source, not in your settings. Try again later (press ↻)." };
    var need = /^(\w+): Field required/.exec(m) || /validation errors? for \w+\s+(\w+)\s+Field required/.exec(m) || /"loc":\s*\[[^\]]*"(\w+)"\][^}]*"type":\s*"missing"/.exec(m);
    if (need || /^Please (enter|select|provide)|^Provide |is required\.?$|must be provided|^Set \w/i.test(m)) {
      var label = need ? ((ctx.param && ctx.param(need[1])) || String(need[1]).replace(/_/g, " ")) : null;
      return { cls: "input", kind: "info", text: label ? "Enter " + label + " to load this card." : m.replace(/\s*\(.*$/, "").slice(0, 160), hint: null, param: need ? need[1] : null };
    }
    if (/slow to answer the first request|keeps loading in the background/i.test(m))
      return { cls: "slow", kind: "info", text: src + " is slow to answer the first request for this dataset.", hint: "It keeps loading in the background; press ↻ in a minute." };
    if (/No (options|data|results) (found|returned|available)|Could not find CIK|CIK not found|No .* found for|covers U\.S\.|is not (supported|covered)|Symbol .* was not found|no price history/i.test(m) && !/route|Not Found$/i.test(m))
      return { cls: "covered", kind: "info", text: "Not covered" + (ctx.symbol ? " for " + ctx.symbol : "") + " by " + src + ".", hint: "Try another symbol." };
    var noQuote = /Yahoo Finance (has no bid\/ask quotes|returned no option prices recent enough)[^"]*/.exec(m);
    if (noQuote) return { cls: "message", kind: "info", text: noQuote[0], hint: null };
    if (/failed after \d+ attempts|request failed|timed? ?out|Timeout|No answer after \d+ seconds|incomplete response|not answering|not responding|not reachable|did not answer|Connection (aborted|reset|refused)|ConnectionReset|RemoteDisconnected|Server disconnected|\b(502|503|504)\b|Bad Gateway|Service Unavailable|Internal Server Error|HTTPError|HTTP Error \d|Client Error|Error fetching data|Expecting value|framing layer|curl:|SSL|certificate|returned no|returned nothing|could not be read|moved the source|Worksheet named|no item named|not reachable/i.test(m))
      return { cls: "upstream", kind: "info", text: src + " is not answering properly right now.", hint: "The problem is at the source, not in your settings. Try again later (press ↻)." };
    if (ctx.userMessage)            // the terminal's own views explain themselves; only URLs are taken out
      return { cls: "message", kind: "info", text: m.replace(/\s*\(?https?:\/\/[^\s)]+\)?/g, "").slice(0, 300), hint: null };
    var ref = T.errorRef(m);
    return { cls: "bug", kind: "err", text: "This card failed inside the terminal (ref " + ref + ").", hint: "Press ↻ to retry. The details are written to local/logs/ui.log under that reference.", ref: ref, detail: m };
  };

  /**
   * The terminal's own views (/x/*) say what kind of error they hit: {error: {kind, message, retryable, retry_after}}.
   * Cards render from that kind; T.classifyError above stays only for raw /api/v1 backend answers.
   * Text shown never carries a URL, a traceback, "Error:" or a Python exception name (T.cleanMessage).
   */
  var TECH_TEXT = /Traceback|\bError:|https?:\/\/|\b[A-Z][A-Za-z]*(?:Error|Exception|Warning)\b|\bErrno\b|WinError|<urlopen|\bKeyError\b/;
  T.cleanMessage = function (m) {
    var t = String(m || "")
      .replace(/\s*\((?:[^()]*?(?:https?:\/\/|Error|Exception|Errno|urlopen|Traceback)[^()]*)\)/g, "")   // technical tails in parentheses
      .replace(/\s*\(?https?:\/\/[^\s)]+\)?/g, "").replace(/\s+/g, " ").trim();
    return !t || TECH_TEXT.test(t) || t.length > 300 ? null : t;
  };
  function waitText(secs, fallbackMin) {
    if (!(secs > 0)) return fallbackMin + " minutes";
    return secs < 90 ? Math.round(secs) + " seconds" : Math.ceil(secs / 60) + " minutes";
  }
  T.classifyKind = function (kind, msg, ctx, info) {
    ctx = ctx || {}; info = info || {};
    var src = ctx.source || "The source", clean = T.cleanMessage(msg), atSource = "The problem is at the source, not in your settings. Try again later (press ↻).";
    if (kind === "input") return { cls: "input", kind: "info", text: clean || "Choose the missing setting to load this card.", hint: null, param: null };
    if (kind === "not_covered") return { cls: "covered", kind: "info", text: clean || ("Not covered" + (ctx.symbol ? " for " + ctx.symbol : "") + " by " + src + "."), hint: "Try another symbol." };
    if (kind === "demo_unavailable") return { cls: "demo", kind: "info", text: clean || "Not included in this frozen preview.", hint: "Download Libre Terminal locally for live data across all 28 dashboards and any global ticker." };
    if (kind === "rate_limited") return { cls: "ratelimit", kind: "info", text: src + " is limiting how often it can be asked right now.", hint: "Try again in about " + waitText(info.retryAfter, /OECD/i.test(String(msg) + src) ? 10 : 2) + " (press ↻)." };
    if (kind === "timeout") {
      if (/keeps loading in the background/i.test(String(msg))) return { cls: "slow", kind: "info", text: src + " is slow to answer the first request for this dataset.", hint: "It keeps loading in the background; press ↻ in a minute." };
      return { cls: "upstream", kind: "info", text: src + " did not answer in time.", hint: atSource };
    }
    if (kind === "unavailable") {   // the server's own words (data services starting or restarting) when it marks them for the user
      var said = info && info.userMessage === true && clean;
      return { cls: "upstream", kind: "info", text: said || "The terminal is busy or restarting.", hint: said ? null : "Press ↻ in a moment." };
    }
    if (kind === "upstream") {
      var own = ctx.userMessage && clean && !/not reachable|did not answer|answered with|^HTTP \d{3}\b|not answering/i.test(clean);
      return { cls: own ? "message" : "upstream", kind: "info", text: own ? clean : src + " is not answering properly right now.", hint: own ? null : atSource };
    }
    var ref = (/\(ref (\w+)/.exec(String(msg)) || [])[1] || T.errorRef(msg);
    return { cls: "bug", kind: "err", text: "This card failed inside the terminal (ref " + ref + ").", hint: "Press ↻ to retry. The details are written to local/logs/ui.log under that reference.", ref: ref, detail: String(msg) };
  };

  /** What really answered, when the backend had to substitute the source asked (X-Terminal-Source: "Yahoo Finance"). */
  T.substituteSources = function (names, note) {
    return String(names || "").split(",").map(function (n) { return n.trim(); }).filter(Boolean).map(function (n) {
      var key = Object.keys(PROV).filter(function (k) { return PROV[k] === n; })[0];
      return { name: n, detail: note || "", url: PROV_URL[key] || "" };
    });
  };

  /** Footer trust signals: warning styling for failed-fetch fallbacks (`stale`, `fallback`, `truncated`), calm muted styling for background SWR (`cached`). */
  T.renderTrust = function (foot, lj) {
    lj = lj && typeof lj === "object" ? lj : {};
    var trust = typeof lj.stale === "string" ? lj.stale : "";
    if (trust) foot.appendChild(el("span", { class: "stale-note", title: trust }, "⚠ " + trust));
    else if (typeof lj.cached === "string" && lj.cached) foot.appendChild(el("span", { class: "cache-note", title: lj.cached }, lj.cached));
    [["fallback", "↻ "], ["truncated", "✂ "]].forEach(function (k) {
      if (typeof lj[k[0]] === "string" && lj[k[0]]) foot.appendChild(el("span", { class: "stale-note", title: lj[k[0]] }, k[1] + lj[k[0]]));
    });
    if (typeof lj.delayed === "string" && lj.delayed) foot.appendChild(el("span", { class: "delay-note", title: lj.delayed }, "delayed"));
    // the envelope's notes and status (core/contract.py), drawn generically: a view that adds a note needs no change here
    var said = [lj.stale, lj.cached, lj.fallback, lj.truncated, lj.delayed].filter(function (x) { return typeof x === "string" && x; });
    var extra = (Array.isArray(lj.notes) ? lj.notes : []).filter(function (n) { return typeof n === "string" && n && said.indexOf(n) < 0; });
    extra.forEach(function (n) { foot.appendChild(el("span", { class: "stale-note", title: n }, "ⓘ " + n)); });
    if ((lj.status === "partial" || lj.status === "fallback") && !extra.length && !lj.fallback)
      foot.appendChild(el("span", { class: "stale-note", title: "Part of this answer is missing or came from a substitute source." }, lj.status === "partial" ? "⚠ partial answer" : "↻ substitute source"));
  };
  T.chartFooterNote = function (d) {
    var hasAnnot = !!(d && d.layout && Array.isArray(d.layout.annotations) && d.layout.annotations.length);
    var conv = (!hasAnnot && d && typeof d.convention === "string") ? d.convention : "";
    var warn = (d && typeof d.warning === "string") ? d.warning : "";
    return (warn ? "⚠ " + warn + (conv ? " · " : "") : "") + conv;
  };

  // At most a few requests at a time per backend: bursts make upstream sources (Yahoo especially) return nothing.
  var slots = {}, MAX_PARALLEL = 5;
  function acquire(host, signal) {
    if (signal && signal.aborted) { var ab = new Error("aborted"); ab.name = "AbortError"; return Promise.reject(ab); }
    var s = slots[host] = slots[host] || { n: 0, q: [] };
    if (s.n < MAX_PARALLEL) { s.n++; return Promise.resolve(); }
    return new Promise(function (res, rej) {
      var entry = { run: function () { if (signal) signal.removeEventListener("abort", onAbort); res(); } };
      function onAbort() {
        var i = s.q.indexOf(entry);
        if (i >= 0) s.q.splice(i, 1);
        var ab2 = new Error("aborted"); ab2.name = "AbortError"; rej(ab2);
      }
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
      s.q.push(entry);
    });
  }
  function release(host) {
    var s = slots[host];
    var next = s.q.shift();
    if (next) next.run(); else s.n--;
  }
  function wait(ms, signal) {
    return new Promise(function (res, rej) {
      if (signal && signal.aborted) { var ab = new Error("aborted"); ab.name = "AbortError"; return rej(ab); }
      var tid = setTimeout(function () {
        if (signal) signal.removeEventListener("abort", onAbort);
        res();
      }, ms);
      function onAbort() {
        clearTimeout(tid);
        var ab2 = new Error("aborted"); ab2.name = "AbortError"; rej(ab2);
      }
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
    });
  }

  // OECD limits requests per hour and its data is monthly or slower: keep its answers in the browser for 12 hours.
  // Per-route timeouts come from the server (/api/config "budgets", core/contract.py ROUTE_BUDGETS): the server's budget for
  // the route plus a margin, so a view always answers (data, partial, or a typed timeout) before the card gives up.
  var REQUEST_TIMEOUT = 45000;                // only until /api/config has answered
  var budgets = null;
  T.setBudgets = function (b) {
    if (!b || typeof b !== "object") return;
    var pats = [];
    (Array.isArray(b.patterns) ? b.patterns : []).forEach(function (p) {
      try { pats.push([new RegExp(p[0]), +p[1]]); } catch (e) { /* a pattern this browser cannot read: skipped */ }
    });
    budgets = { def: +b.default_ms || REQUEST_TIMEOUT, files: +b.files_ms || REQUEST_TIMEOUT, views: b.views || {}, pats: pats };
    T.budgetTable = b;                        // the table as served (read-only use: diagnostics)
  };
  T.requestTimeout = function (url, isFiles) {
    if (!budgets) return REQUEST_TIMEOUT;
    if (isFiles) return budgets.files;
    var u = String(url || ""), m = /\/x\/([A-Za-z0-9_]+)/.exec(u);
    if (m && Object.prototype.hasOwnProperty.call(budgets.views, m[1])) return +budgets.views[m[1]];
    for (var i = 0; i < budgets.pats.length; i++) if (budgets.pats[i][0].test(u)) return budgets.pats[i][1];
    return budgets.def;
  };
  var limitedBy = {};                         // cooldowns: OECD per provider; Nasdaq per URL (the backend still serves its cache hits)
  T.clearAllCaches = function () { dataCache.clear(); limitedBy = {}; };
  function rateLimitKey(url) {
    var u = String(url || "");
    if (/[?&]provider=oecd(&|$)|\/oecd\//i.test(u)) return "oecd";
    if (/[?&]provider=nasdaq(&|$)|\/nasdaq\//i.test(u)) return "nasdaq";
    return "";
  }
  function isRateLimited(url) { return !!rateLimitKey(url); }
  function fetchOptions(force, signal) {
    // timeout 0: getJSON and postText bound the wait themselves (T.requestTimeout per route)
    return force ? { signal: signal, headers: { "X-Terminal-Refresh": "1" }, timeout: 0 } : { signal: signal, timeout: 0 };
  }
  T._reqSlots = { acquire: acquire, release: release, slots: slots, rateLimitKey: rateLimitKey, isRateLimited: isRateLimited, fetchOptions: fetchOptions, max: MAX_PARALLEL };
  // Server CACHE_POLICY is now the sole authoritative persistent cache; remove any legacy browser Cache API store once on boot.
  try {
    if (typeof caches !== "undefined" && caches && typeof caches.delete === "function") {
      caches.delete("obb-slow-v1").catch(function () {});
    }
  } catch (e) { /* cache storage unavailable */ }

  /** Forget one answer from the short in-memory cache; forced card reloads also send X-Terminal-Refresh: 1 to the backend. */
  function dropCached(url) {
    dataCache.delete(url);
  }
  T.dropCached = dropCached;
  T._dataCache = dataCache;                    // for the offline checks
  /**
   * POST (document viewers ask for files this way) under the same rules as getJSON: a few requests per backend at a
   * time and a 90 s limit. Returns the response text; throws with the readable error otherwise.
   */
  async function postText(url, bodyObj, outerSignal) {
    var host = /^https?:\/\/[^/]+/.exec(url); host = host ? host[0] : "self";
    await acquire(host, outerSignal);
    try {
      var r = await T.request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(bodyObj),
        signal: outerSignal, timeout: T.requestTimeout(url, true) });
      var txt = await r.text();
      if (!r.ok) throw T.httpError(r.status, txt, errText(r.status, txt));
      return txt;
    } finally {
      release(host);
    }
  }
  T._postText = postText;

  async function getJSON(url, force, outerSignal) {
    var c = dataCache.get(url);
    if (!force && c && Date.now() - c.t < TTL) return c.json;
    var rlKey = rateLimitKey(url), slow = !!rlKey;
    var limKey = rlKey === "nasdaq" ? "nasdaq|" + url : rlKey;
    var lim = rlKey && limitedBy[limKey];
    if (lim && Date.now() < lim.until) throw new Error(lim.msg);
    var host = /^https?:\/\/[^/]+/.exec(url); host = host ? host[0] : "self";
    await acquire(host, outerSignal);
    var timeoutMs = T.requestTimeout(url, false);
    var json;
    // Never wait forever: give up after the route's bounded timeout so the card shows a message and a retry instead of a spinner.
    var ctl = new AbortController(), timedOut = false, signal = ctl.signal;
    var timer = setTimeout(function () { timedOut = true; ctl.abort(); }, timeoutMs);
    function relay() { ctl.abort(); }
    if (outerSignal) { if (outerSignal.aborted) ctl.abort(); else outerSignal.addEventListener("abort", relay); }
    var fetchOpts = fetchOptions(force, signal);
    function throwHttpErr(resp, bodyTxt) {
      var err = T.httpError(resp.status, bodyTxt, errText(resp.status, bodyTxt));
      var subE = resp.headers && resp.headers.get("X-Terminal-Source");
      if (subE && !err.sources) err.sources = T.substituteSources(subE, resp.headers.get("X-Terminal-Fallback"));
      if (rlKey && (err.errorKind === "rate_limited" || resp.status === 429 || (!err.errorKind && /rate limit|too many requests|temporarily blocked/i.test(err.message)))) {
        var rlSecs = err.retryAfter || +((/~(\d+)\s*s\b/.exec(err.message) || [])[1] || 0);
        limitedBy[limKey] = { until: Date.now() + (rlSecs ? rlSecs * 1000 : (rlKey === "nasdaq" ? 120000 : 600000)), msg: err.message };
      }
      throw err;
    }
    try {
      if (signal && signal.aborted) { var ab = new Error("aborted"); ab.name = "AbortError"; throw ab; }
      var r;
      try { r = await T.request(url, fetchOpts); }
      catch (e) {
        // one automatic retry for a dropped connection, but only while the backend itself still answers (never for OECD)
        if (e && e.name === "AbortError") throw e;
        var base = /^https?:\/\/[^/]+/.exec(url);
        var alive = base ? await T.request(base[0] + "/", { mode: "no-cors", signal: signal, timeout: 5000 }).then(function () { return true; }, function () { return false; }) : true;
        if (!alive) { var down = new Error("BACKEND_DOWN"); down.backendDown = true; throw down; }
        if (slow) throw e;
        await wait(1500, signal);
        r = await T.request(url, fetchOpts);
      }
      if (!r.ok) {
        var etxt = await r.text();
        var isUserMsg = false;
        try { isUserMsg = JSON.parse(etxt).user_message === true; } catch (e0) { /* not JSON */ }
        // Yahoo's client library trips over itself when two requests land at once, or a transient gateway/upstream error occurred: one retry clears it.
        // Rate limits, OECD URLs, and explicit user messages are not retried (that only prolongs them).
        var shouldRetry = !slow && (/database is locked|RemoteDisconnected|Invalid Crumb|ConnectionReset|TimeoutError/i.test(etxt)
          || ((r.status === 502 || r.status === 503 || r.status === 504) && !isUserMsg && !/rate limit|too many requests/i.test(etxt)));
        if (shouldRetry) {
          await wait(1200 + Math.random() * 1200, signal);
          r = await T.request(url, fetchOpts);
          etxt = r.ok ? null : await r.text();
        }
      }
      if (!r.ok) throwHttpErr(r, etxt);
      // Very large answers (some sources return 100+ MB when unfiltered) would stall the browser: ask first.
      var size = Number((r.headers && r.headers.get("content-length")) || 0);
      if (size > 40e6 && !force) { ctl.abort(); var big = new Error("TOO_LARGE"); big.size = size; throw big; }
      var txt = await r.text();
      if (!txt && !slow && /[?&]provider=yfinance(&|$)|\/api\/v1\/yfinance\/|\/x\/chart\b/.test(url)) {     // "no content" from Yahoo: usually throttling, so try once more
        await wait(1200, signal);
        r = await T.request(url, fetchOpts);
        if (!r.ok) throwHttpErr(r, await r.text());
        txt = await r.text();
      }
      if (txt) {
        try {
          json = JSON.parse(txt);
        } catch (parseErr) {
          if (slow) throw new Error("The source returned an incomplete response. Try reloading this card.");
          await wait(800, signal);
          r = await T.request(url, fetchOpts);
          if (!r.ok) throwHttpErr(r, await r.text());
          txt = await r.text();
          if (!txt) {
            if (r.status === 204) json = null;
            else throw new Error("The source returned an incomplete response. Try reloading this card.");
          } else {
            try {
              json = JSON.parse(txt);
            } catch (parseErr2) {
              throw new Error("The source returned an incomplete response. Try reloading this card.");
            }
          }
        }
      } else {
        json = null;
      }
      // a backend answer served from a saved copy because the source failed (HANDOFF P2): kept with the answer in memory
      var staleH = r.headers && r.headers.get("X-Terminal-Stale");
      if (staleH && json && typeof json === "object" && !Array.isArray(json) && json.stale == null) json.stale = staleH;
      var subH = r.headers && r.headers.get("X-Terminal-Source");
      if (subH && json && typeof json === "object") {       // a list answer carries it too
        var subNote = (r.headers && r.headers.get("X-Terminal-Fallback")) || "";
        json.sources = T.substituteSources(subH, subNote);
        json.fallback = subNote;
      }
      var lateH = r.headers && r.headers.get("X-Terminal-Delayed");
      if (lateH && json && typeof json === "object" && !Array.isArray(json) && json.delayed == null) json.delayed = lateH;
    } catch (e2) {
      if (e2 && (e2.message === "TOO_LARGE" || e2.backendDown)) throw e2;
      if (timedOut) throw new Error("No answer after " + Math.round(timeoutMs / 1000) + " seconds. The source is slow or not responding.");
      throw e2;
    } finally {
      clearTimeout(timer);
      if (outerSignal) outerSignal.removeEventListener("abort", relay);
      release(host);
    }
    if (json != null) dataCache.set(url, { t: Date.now(), json: json });
    if (dataCache.size > 150) dataCache.delete(dataCache.keys().next().value);
    return json;
  }
  T.getJSON = getJSON;

  // ---------- sources: one plain name per provider, and where a card's data comes from ----------
  var PROV = { yfinance: "Yahoo Finance", cboe: "Cboe", tmx: "TMX", nasdaq: "Nasdaq", fred: "FRED", bls: "BLS", eia: "U.S. EIA", sec: "SEC", federal_reserve: "Federal Reserve",
    ecb: "ECB", oecd: "OECD", imf: "IMF", us_treasury: "U.S. Treasury", government_us: "U.S. Government", cftc: "CFTC", finra: "FINRA", deribit: "Deribit", usda: "USDA",
    jodi: "JODI", famafrench: "Fama-French", congress_gov: "Congress.gov", econdb: "EconDB", finviz: "Finviz", multpl: "multpl", wsj: "WSJ", fmp: "FMP", polygon: "Polygon",
    tiingo: "Tiingo", intrinio: "Intrinio", alpha_vantage: "Alpha Vantage", tradier: "Tradier", benzinga: "Benzinga", biztoc: "BizToc", tradingeconomics: "Trading Economics",
    stockgrid: "Stockgrid", seeking_alpha: "Seeking Alpha", ustreasury: "U.S. Treasury", uscongress: "Congress.gov", news: "News feeds" };
  var PROV_URL = { yfinance: "https://finance.yahoo.com/", cboe: "https://www.cboe.com/", tmx: "https://money.tmx.com/", nasdaq: "https://www.nasdaq.com/", fred: "https://fred.stlouisfed.org/",
    bls: "https://www.bls.gov/", eia: "https://www.eia.gov/", sec: "https://www.sec.gov/edgar/search/", federal_reserve: "https://www.federalreserve.gov/", ecb: "https://data.ecb.europa.eu/",
    oecd: "https://data-explorer.oecd.org/", imf: "https://data.imf.org/", us_treasury: "https://fiscaldata.treasury.gov/", ustreasury: "https://fiscaldata.treasury.gov/", cftc: "https://www.cftc.gov/",
    finra: "https://www.finra.org/finra-data", deribit: "https://www.deribit.com/", usda: "https://www.ers.usda.gov/", jodi: "https://www.jodidata.org/", congress_gov: "https://www.congress.gov/",
    uscongress: "https://www.congress.gov/", famafrench: "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html", fmp: "https://site.financialmodelingprep.com/",
    wsj: "https://www.wsj.com/market-data", finviz: "https://finviz.com/", multpl: "https://www.multpl.com/", econdb: "https://www.econdb.com/", government_us: "https://www.usda.gov/" };
  var RAW_NAMES = { yfinance: "Yahoo Finance", "congress gov": "Congress.gov", jodi: "JODI", famafrench: "Fama-French", "us treasury": "U.S. Treasury", "government us": "U.S. Government",
    biztoc: "BizToc", multpl: "multpl", rss: "RSS news feeds", eia: "U.S. EIA" };
  function cleanName(s) {
    s = String(s || "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").trim();
    if (/;/.test(s)) s = s.split(";").pop().trim();                 // "UN Global Platform; IMF PortWatch"
    return RAW_NAMES[s.toLowerCase()] || s;
  }
  /** The plain source name of a backend widget: its provider, else its declared source, else its route's namespace. */
  T.providerName = function (def) {
    var p = (def.params || []).filter(function (x) { return x.paramName === "provider"; })[0];
    if (p && p.value && PROV[p.value]) return PROV[p.value];
    var src = (Array.isArray(def.source) ? def.source : def.source ? [def.source] : []).map(cleanName).filter(function (x) { return x && !/^(custom|default|terminal views)$/i.test(x); });
    if (src.length) return src.join(" / ");
    var ns = String(def.endpoint || "").replace(/^\/api\/v1\//, "").replace(/^\/x\//, "").split("/")[0];
    return PROV[ns] || (ns ? ns.charAt(0).toUpperCase() + ns.slice(1) : "");
  };
  T.providerKey = function (def) {
    var p = (def.params || []).filter(function (x) { return x.paramName === "provider"; })[0];
    return p && p.value ? String(p.value) : String(def.endpoint || "").replace(/^\/api\/v1\//, "").split("/")[0];
  };
  /** Source entries of a backend card, from its definition (the terminal's own views send theirs with each answer). */
  T.defSources = function (def, backend) {
    var key = T.providerKey(def), route = String(def.endpoint || "").replace(/^\/api\/v1\//, "");
    // a readable name for "Data used" (the plain card title, else the function's name, else the route in words); the route itself only as a tooltip
    var name = (T.titleOf && T.titleOf(def.widgetId)) || def.name || route.split("/").filter(Boolean).map(function (s) { return s.replace(/_/g, " "); }).join(" › ");
    return [{ name: T.providerName(def), detail: name + (backend && backend.id === "v4" ? " (legacy data service)" : ""), route: route, url: PROV_URL[key] || "" }];
  };
  /** The header label on a narrow card: "Yahoo" for "Yahoo Finance" and the like (the full name is in the tooltip). */
  var SHORT_SRC = { "Yahoo Finance": "Yahoo", "U.S. Treasury": "Treasury", "Federal Reserve": "Fed", "Congress.gov": "Congress", "U.S. EIA": "EIA",
    "Trading Economics": "TE", "U.S. Government": "U.S. Gov", "Fama-French": "Fama-French", "Alpha Vantage": "AlphaV", "Seeking Alpha": "SA" };
  T.shortSource = function (label) {
    return String(label || "").split(" · ").map(function (n) { return SHORT_SRC[n] || n; }).join(" · ");
  };
  /** "FRED · EIA" for a header; more than three names: the first two and "+N". */
  T.sourceLabel = function (list) {
    var names = [];
    (list || []).forEach(function (s) { var n = s && (s.name || s); if (n && names.indexOf(n) < 0) names.push(n); });
    return names.length > 3 ? names.slice(0, 2).join(" · ") + " +" + (names.length - 2) : names.join(" · ");
  };
  /** One line for charts and exports: "Source: FRED (UNRATE, U6RATE) · U.S. EIA (PET.WCESTUS1.W)". */
  T.sourceLine = function (list) {
    var by = {}, order = [];
    (list || []).forEach(function (s) {
      if (!s || !s.name) return;
      if (!by[s.name]) { by[s.name] = []; order.push(s.name); }
      var id = String(s.detail || "").split(/[:,( ]/)[0];
      // a series id, not the provider's own name at the start of a title ("TMX Equity Quote" must not read "TMX (TMX)")
      var own = id.toUpperCase() === String(s.name).toUpperCase() || String(s.name).toUpperCase().split(/[\s.]+/).indexOf(id.toUpperCase()) >= 0;
      if (!own && /^[A-Z0-9][A-Z0-9_.@\-]{2,}$/.test(id) && /\d|[A-Z]{3,}/.test(id) && by[s.name].indexOf(id) < 0) by[s.name].push(id);
      else if (/^calculated/.test(s.detail || "") && by[s.name].indexOf("calculated") < 0) by[s.name].push("calculated");
    });
    if (!order.length) return "";
    return "Source: " + order.map(function (n) { var ids = by[n]; return n + (ids.length && ids.length <= 6 ? " (" + ids.join(", ") + ")" : ids.length ? " (" + ids.length + " series)" : ""); }).join(" · ");
  };
  /** Which market a ticker trades in, for choosing a source that covers it. */
  T.marketOf = function (sym) {
    var s = String(sym || "").toUpperCase().split(",")[0].trim();
    if (!s) return "";
    if (/^(BTC|ETH|SOL|XRP|PAXG|MATIC|BNB|ADA|DOGE)(-?USDT?)?$/.test(s)) return "crypto";
    if (/^[A-Z0-9]{2,10}-(USD|USDT|USDC|EUR|GBP|JPY|CAD|AUD|CHF|BTC|ETH)$/.test(s)) return "crypto";   // Yahoo's coin pairs (LTC-USD, ETH-EUR)
    if (/\.(TO|V|NE|CN)$/.test(s)) return "ca";
    if (/\.(ST|HE|CO|IC)$/.test(s) || /^[A-Z0-9]+ [A-Z]$/.test(s)) return "nordic";
    if (/^\^?[A-Z][A-Z0-9\-]{0,6}(\.[A-C])?$/.test(s)) return "us";
    return "other";
  };
  /** The ticker as a source expects it: TMX wants RY for RY.TO, Nasdaq Nordic VOLV B for VOLV-B.ST, Deribit the coin (BTC for BTC-USD). */
  T.symbolFor = function (sym, kind) {
    var s = String(sym || "").toUpperCase();
    if (kind === "tmx") return s.replace(/\.(TO|V|NE|CN)$/, "");
    if (kind === "nordic") return s.replace(/\.(ST|HE|CO|IC)$/, "").replace(/-/g, " ");
    if (kind === "crypto_base") return s.replace(/-?USDT?$/, "");
    return s;
  };
  T.MARKET_NAMES = { us: "U.S. listings", ca: "Canadian listings (.TO, .V)", crypto: "crypto (BTC, ETH)", nordic: "Nordic listings", other: "other markets" };

  /** U.S.-only sources (SEC filings, FINRA, Cboe, Nasdaq) asked about a ticker listed elsewhere (SAP.DE, 7203.T, SHEL.L). */
  var US_ONLY = /^(SEC|FINRA|Cboe|Nasdaq|Stockgrid|Government US)$/i;
  T.nonUSNote = function (def, sym, text, hasYahooSibling) {
    sym = String(sym || "").toUpperCase().split(",")[0];
    if (!/^[A-Z0-9\-&]+\.[A-Z]{1,4}$/.test(sym) || /^[A-Z]{1,5}\.[A-C]$/.test(sym)) return null;     // BRK.B, BF.B: U.S. share classes
    var rawSrc = (Array.isArray(def.source) ? def.source : [def.source || ""]).filter(function (s) {
      return s && !/^(custom|default|terminal views)$/i.test(s);
    });
    var pn = T.providerName(def);
    var src = rawSrc.length ? rawSrc : (pn ? [pn] : []);
    var ep = String((def && def.endpoint) || ""), isFundEp = /\/(etf\/|etf_|sec\/nport|yfinance\/funds\/)/.test(ep);
    var tail = hasYahooSibling ? "Switch this card to Yahoo Finance with the source selector, or use the company's U.S. listing (ADR) if it has one."
      : isFundEp ? "Yahoo Finance cards on ETF Profile and Mutual Funds cover " + sym + "."
      : "Use the company's U.S. listing (ADR) if it has one; Overview, Financials, Estimates, Price & Peers and Ownership use Yahoo Finance and cover " + sym + ".";
    if (/Premium Query Parameter|Special Endpoint/i.test(text || "")) return "FMP's free plan covers U.S. listings only for this data. " + tail;
    if (/Could not find CIK|CIK not found/i.test(text || "") || (src.length && src.every(function (s) { return US_ONLY.test(s); })))
      return (src.join(", ") || "This source") + " covers U.S.-listed " + (isFundEp ? "funds" : "companies") + " only, and " + sym + " trades outside the U.S. " + tail;
    return null;
  };

  // Query for a picker's options endpoint: "$name" takes the card's current value. A fixed provider follows the
  // card's own provider, so a card whose provider the build corrected (USDA Weather & Crop Bulletin: widget says
  // government_us, the backend accepts only usda) does not list its files from the provider it no longer uses.
  T.optionsQuery = function (optionsParams, cur) {
    var q = {};
    Object.keys(optionsParams || {}).forEach(function (k) {
      var v = optionsParams[k];
      q[k] = typeof v === "string" && v.charAt(0) === "$" ? cur[v.slice(1)] : (k === "provider" && cur.provider ? cur.provider : v);
    });
    return q;
  };

  T.frameUnsupported = function (def, params) {
    var ep = String((def && def.endpoint) || ""), sym = String((params && params.symbol) || "").trim();
    if (/tv_widget/.test(ep) && /^FRED:/i.test(sym)) {
      return { text: "TradingView charts market tickers only (not FRED series).", hint: "Use the Compare chart card on this dashboard to plot " + sym.toUpperCase() + "." };
    }
    return null;
  };

  T.SLOW_RETRY_DELAYS = [15, 20, 25, 30];
  T.slowRetryMessage = function (delayS) {
    return "Building in the background — checking again automatically in " + delayS + " s (or press ↻ now).";
  };

  T.widget = function (ctx) {
    var item = ctx.item, def = ctx.def, base = ctx.backend.url;
    item.params = item.params || {};
    var node = el("div", { class: "w" });
    var title = el("span", { class: "w-title", title: def.description || def.name }, def.name);
    function paintTitle() {
      var t = item.title || (ctx.conceptTitle && ctx.conceptTitle(item)) || (T.titleOf && T.titleOf(def.widgetId)) || def.name;
      if (def.symbolList === "watchlist" && !item.title) t = "Watchlist: " + T.lists.current(item).name;
      if (!item.title && def.titleParam) {
        var tp = def.params.filter(function (p) { return p.paramName === def.titleParam; })[0];
        var cur = Object.prototype.hasOwnProperty.call(item.params || {}, def.titleParam) ? item.params[def.titleParam] : tp && tp.value;
        var custom = item.params && item.params.symbols;
        var opt = tp && (tp.options || []).filter(function (o) { return String(o.value) === String(cur); })[0];
        t = (def.titlePrefix || "") + (custom && !def.titleParam2 ? String(custom) : opt ? opt.label.replace(/^[^·]+ · /, "") : def.name);
        if (def.titleParam2) {                       // e.g. "Capital expenditure: AI hyperscalers"
          var p2 = def.params.filter(function (p) { return p.paramName === def.titleParam2; })[0];
          var v2 = Object.prototype.hasOwnProperty.call(item.params || {}, def.titleParam2) ? item.params[def.titleParam2] : p2 && p2.value;
          var o2 = p2 && (p2.options || []).filter(function (o) { return String(o.value) === String(v2); })[0];
          t += ": " + (custom ? String(custom) : o2 ? o2.label : v2 || "");
        }
      }
      if (def.titlePeriod) {                         // rankings and returns always say over what period
        var per = eff()[def.titlePeriod], pp = def.params.filter(function (p) { return p.paramName === def.titlePeriod; })[0];
        var po = pp && (pp.options || []).filter(function (o) { return String(o.value) === String(per); })[0];
        if (per) t += " · " + (po ? po.label : per);
      }
      if (item.title && item.titleParam) {           // built title + what the card shows now ("Futures price history (CL)")
        var tpv = eff()[item.titleParam.param];
        if (tpv != null && tpv !== "") t += " (" + ((item.titleParam.labels || {})[String(tpv).toUpperCase()] || String(tpv)) + ")";
      }
      title.textContent = t;
    }
    // Where the data comes from: the answer's own "sources" (terminal views), else the widget definition. Before the first
    // answer a terminal view shows the hint the dashboard stored for it (item.src), never a list of every possible source.
    var answerSources = null;
    function sources() {
      if (answerSources && answerSources.length) return answerSources;
      if (ctx.backend.id !== "x") return T.defSources(def, ctx.backend);
      if (item.src && item.src.length) return item.src.map(function (n) { return { name: n, detail: "", url: "" }; });
      return def.dynamicSource ? [] : (Array.isArray(def.source) ? def.source : [def.source]).filter(Boolean).map(function (n) { return { name: n, detail: "", url: "" }; });
    }
    var sub = el("button", { class: "w-sub w-src", type: "button" });
    function paintSource() {
      var alts = ctx.alternatives ? ctx.alternatives(item, def, eff()) : [];
      var lab = T.sourceLabel(sources());
      sub.textContent = "";
      sub.appendChild(el("span", { class: "k" }, "Source: "));
      sub.appendChild(document.createTextNode((narrowNow ? T.shortSource(lab) : lab) || "…"));
      if (alts.length > 1) sub.appendChild(el("span", { class: "dd" }, " ▾"));
      sub.classList.toggle("has-alt", alts.length > 1);
      sub.title = alts.length > 1 ? "Data source: " + (lab || "loading") + ". Click to switch source (" + alts.length + " available) or see the series used." : "Data source: " + (lab || "loading") + ". Click for details and links.";
    }
    function openSources() {
      var alts = ctx.alternatives ? ctx.alternatives(item, def, eff()) : [];
      var box = el("div", { class: "src-pop" });
      if (alts.length > 1) {
        box.appendChild(el("div", { class: "src-h" }, "Source for this card"));
        alts.forEach(function (a) {
          var row = el("div", { class: "opt" + (a.current ? " sel" : "") + (a.dim ? " dim" : ""), title: a.note || a.label }, [el("span", { class: "ck" }, a.current ? "✓" : ""), el("span", { class: "nm" }, a.label), a.note ? el("span", { class: "nt" }, a.note) : null]);
          row.addEventListener("mousedown", function (e) { e.preventDefault(); T.closePop(); if (!a.current) ctx.pickSource(item, a); });
          box.appendChild(row);
        });
      }
      var list = sources();
      box.appendChild(el("div", { class: "src-h" }, list.length ? "Data used" : "Data used: shown once the card has loaded"));
      list.forEach(function (s) {
        var u = s.url && T.safeUrl(s.url);
        box.appendChild(el("div", { class: "src-e", title: s.route ? "Data function: " + s.route : "" }, [el("b", null, s.name), s.detail ? el("span", null, " " + s.detail) : null, u ? el("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, " open ↗") : null]));
      });
      T.popover(sub, box, { width: 420 });
    }
    sub.addEventListener("mousedown", function (e) { e.stopPropagation(); });      // not a drag of the card
    sub.addEventListener("click", function (e) { e.stopPropagation(); openSources(); });
    var tools = el("div", { class: "w-tools" });
    var head = el("div", { class: "w-head" }, [title, sub, tools]);
    function toggleMax() { node.classList.toggle("max"); ctx.relayout(); }
    head.addEventListener("dblclick", function (e) {
      if (e.target && e.target.closest && e.target.closest("button,input,select,a,.dd,.w-sub")) return;
      toggleMax();
    });
    var strip = el("div", { class: "w-strip" });
    var pbar = el("div", { class: "w-params" });
    var body = el("div", { class: "w-body" });
    var foot = el("div", { class: "w-foot" });
    var rz = el("div", { class: "w-resize", title: "Resize" });
    node.appendChild(head); node.appendChild(strip); node.appendChild(pbar); node.appendChild(body); node.appendChild(foot); node.appendChild(rz);

    var abort = null, visible = false, pending = true, alive = true, failed = false, lastJson = null, lastEnvelope = null, tv = null, plotNode = null, blobUrls = [], timer = null, frameTimer = null, frameHardTimer = null, chartMsgHandler = null, loadTimer = null, slowTimer = null, slowRetries = 0, showAllParams = false, seq = 0, filterVal = "";
    var fileParam = def.params.filter(function (p) { return (p.roles || []).indexOf("fileSelector") >= 0; })[0] || null;
    var lastFileSig = null;          // the other parameters the current document pick was made for
    var isFrame = def.type === "iframe" || def.type === "html";
    var isFiles = def.type === "multi_file_viewer" || def.type === "pdf";

    // ---------- params ----------
    /**
     * Data sources spell countries differently (OECD: korea, turkiye, euro_area_20_countries; others: south_korea, turkey,
     * euro_area). A dashboard country picker uses one spelling: translate it to the one this widget's options list uses.
     */
    var COUNTRY_ALIASES = { south_korea: ["korea"], korea: ["south_korea"], turkey: ["turkiye"], turkiye: ["turkey"], czech_republic: ["czechia"], czechia: ["czech_republic"],
      slovakia: ["slovak_republic"], euro_area: ["euro_area_20_countries", "euro_area_19_countries", "euro_area20", "ea20"], united_states: ["united_states_of_america"] };
    function countryFor(p, value) {
      var opts = (p.options || []).map(function (o) { return String(o.value); });
      if (!opts.length) return value;
      function one(c) {
        c = String(c);
        if (opts.indexOf(c) >= 0) return c;
        var alt = (COUNTRY_ALIASES[c] || []).filter(function (a) { return opts.indexOf(a) >= 0; })[0];
        return alt || c;
      }
      var list = Array.isArray(value) ? value : String(value).split(",");
      var mapped = list.map(one);
      return Array.isArray(value) ? mapped : mapped.join(",");
    }
    // Values set by clicking a row elsewhere on the tab (a filing, a bond): they drive this card for now, but are not
    // saved into the layout (viewing must not pin an NVIDIA filing on the Filings tab forever). Cleared when the
    // dashboard's ticker changes.
    var live = {};
    function own(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
    function pdef(name) { return def.params.filter(function (x) { return x.paramName === name; })[0]; }
    function cur(name, depth) {                    // the value a parameter has now: a row click, the saved pick, a group
      var p = pdef(name), g = ctx.groupOf(item, name);
      if (g && !empty(g.value)) return p && name === "country" ? countryFor(p, g.value) : g.value;
      if (own(live, name)) return live[name];
      if (own(item.params, name) && !masked(name, depth)) return item.params[name];
      return p ? p.value : undefined;
    }
    function same(a, b) {
      a = T.resolveParam(a); b = T.resolveParam(b);
      return (a == null ? "" : String(a).trim().toUpperCase()) === (b == null ? "" : String(b).trim().toUpperCase());
    }
    // A saved pick that belongs to another parameter's value (a strike or a target price to its symbol, an expiration to
    // the symbol it was listed for) applies only while that parameter still shows its saved value. Another dashboard
    // ticker or a row click hides it for this view; the layout keeps it, so viewing never edits a tab (P3-06).
    var loaded = {};                               // anchor values when the card was built: what an unanchored saved pick belongs to
    function moved(name, depth) {
      var now = cur(name, depth);
      if (own(item.params, name)) return !same(now, item.params[name]);
      if (!own(loaded, name)) loaded[name] = now;
      return !same(now, loaded[name]);
    }
    function anchors(name) {
      var p = pdef(name), out = PRICE_PARAMS.indexOf(name) >= 0 ? ["symbol"] : [];
      if (p && p.optionsParams) Object.keys(p.optionsParams).forEach(function (k) {
        var ref = p.optionsParams[k];
        if (typeof ref === "string" && ref.charAt(0) === "$" && ref.slice(1) !== name && out.indexOf(ref.slice(1)) < 0) out.push(ref.slice(1));
      });
      return out;
    }
    function masked(name, depth) {
      depth = (depth || 0) + 1;
      if (depth > 4) return false;
      return anchors(name).some(function (a) { return moved(a, depth); });
    }
    function eff() {
      var out = {};
      def.params.forEach(function (p) { out[p.paramName] = T.resolveParam(cur(p.paramName)); });
      // Some widget definitions omit a parameter the endpoint still needs (e.g. the ticker):
      // pass through values the dashboard pinned on the item, and the values of groups it follows.
      Object.keys(item.params).forEach(function (k) { if (!(k in out) && !empty(item.params[k]) && !masked(k)) out[k] = T.resolveParam(item.params[k]); });
      Object.keys(live).forEach(function (k) { if (!(k in out) && !empty(live[k])) out[k] = live[k]; });
      (ctx.groupsOf ? ctx.groupsOf(item) : []).forEach(function (g) { if (!(g.paramName in out) && !empty(g.value)) out[g.paramName] = g.value; });
      if (def.symbolList && T.lists) T.lists.inject(def, item, out);      // watchlist symbols / the user's peers, kept in saved state
      var ov = ctx.overrides ? ctx.overrides(item) : {};                 // chart strip and source picks (remembered per card)
      Object.keys(ov).forEach(function (k) {
        if (empty(ov[k]) || k.charAt(0) === "_") return;               // "_name": a view choice drawn in the page, never sent
        // a remembered pick from another source of this card ("quarter" vs "quarterly"): use this source's spelling
        var pd = def.params.filter(function (x) { return x.paramName === k; })[0], opts = pd && Array.isArray(pd.options) && pd.options.length ? pd.options.map(function (o) { return o && typeof o === "object" ? String(o.value) : String(o); }) : null;
        var v = String(ov[k]);
        if (opts && opts.indexOf(v) < 0) {
          var vl = v.toLowerCase();
          var near = opts.filter(function (o) {
            var ol = o && o.toLowerCase();
            return ol && (ol.indexOf(vl) === 0 || vl.indexOf(ol) === 0 || (k === "period" && ((vl === "fy" && ol === "annual") || (vl === "annual" && ol === "fy"))));
          })[0];
          if (near != null) { out[k] = near; return; }
          if (k === "period") return;
        }
        out[k] = ov[k];
      });
      var m = ctx.member ? ctx.member(item) : null;                      // TMX wants RY for RY.TO, Deribit BTC for BTC-USD
      if (m && m.sym && typeof out.symbol === "string") out.symbol = T.symbolFor(out.symbol, m.sym);
      return out;
    }
    function stripParams() { return (def.strip || []).map(function (s) { return s.param; }); }
    function clearDepParams(paramName, mode) {
      var cleared = false;
      if (!paramName) return false;
      var pending = [paramName], seen = {};
      while (pending.length) {
        var curParam = pending.shift();
        if (seen[curParam]) continue;
        seen[curParam] = 1;
        def.params.forEach(function (d) {
          if (d.optionsParams && Object.keys(d.optionsParams).some(function (k) { return d.optionsParams[k] === "$" + curParam; })) {
            if (Object.prototype.hasOwnProperty.call(item.params, d.paramName) && item.params[d.paramName] != null) cleared = true;
            if (Object.prototype.hasOwnProperty.call(live, d.paramName)) cleared = true;
            if (mode === "null") item.params[d.paramName] = null;
            else if (mode === "delete") delete item.params[d.paramName];
            delete live[d.paramName];
            pending.push(d.paramName);
          }
        });
      }
      return cleared;
    }
    // prices of the underlying (a target price, a strike) mean nothing for another symbol: AAPL's target 250 kept for ^SPX
    // gave +611,011% rows and a Cboe timeout (review B5)
    var PRICE_PARAMS = ["target_price", "strike"];
    function dropPriceParams() {
      var changed = false;
      PRICE_PARAMS.forEach(function (n) {
        if (item.params[n] != null && item.params[n] !== "") { delete item.params[n]; changed = true; }
        delete live[n];
      });
      return changed;
    }
    function setParam(p, v) {
      if (typeof v === "string") v = v.trim();
      if (p.paramName === "symbol" && typeof v === "string" && p.type !== "endpoint") v = v.toUpperCase();
      var g = ctx.groupOf(item, p.paramName);
      // a group pick is a preference: saved picks that depended on it are hidden for this view (masked), never deleted
      if (g) { clearDepParams(p.paramName, "live"); ctx.setGroup(g, v); return; }
      if (p.paramName === "symbol") dropPriceParams();
      // dependants (options driven by this param) are no longer valid: an edit of this card, so they are cleared in it
      clearDepParams(p.paramName, "null");
      // a pick that belongs to another value (a strike to its symbol) is saved with the value it belongs to
      anchors(p.paramName).forEach(function (a) { if (pdef(a) && moved(a)) item.params[a] = cur(a); });
      // the settings panel wins over the strip (and over a statement's Annual / Quarterly switch)
      if (ctx.setView && (stripParams().indexOf(p.paramName) >= 0 || (p.paramName === "period" && T.statementKind && T.statementKind(def)))) ctx.setView(item, p.paramName, null);
      delete live[p.paramName];
      item.params[p.paramName] = v;
      slowRetries = 0;
      ctx.onChange();
      drawParams();
      paintTitle();
      refresh(false);
    }
    function pickList(id) { T.lists.select(item, id); drawParams(); paintTitle(); refresh(false); }
    function optionsFetcher(p) {
      return function () {
        var q = T.optionsQuery(p.optionsParams, eff());
        var url = (/^https?:/.test(p.optionsEndpoint) ? "" : base) + p.optionsEndpoint + qs(q);
        if (!optCache.has(url)) {
          optCache.set(url, getJSON(url, false).then(function (j) { return Array.isArray(j) ? j : (j && j.results) || []; }).catch(function (e) {
            optCache.delete(url); throw e;
          }));
        }
        return optCache.get(url);
      };
    }
    function control(p, v) {
      var label = p.label || pretty(p.paramName);
      var wrap = el("div", { class: "p" + (p.optional === false ? " req" : ""), title: p.description || label });
      var lab = el("label", null, label);
      var c;
      if (p.type === "tabs" && p.options) {
        c = el("div", { class: "ptabs" });
        p.options.forEach(function (o) {
          c.appendChild(el("button", { type: "button", class: String(o.value) === String(v) ? "on" : "", onclick: function () { setParam(p, o.value); } }, o.label));
        });
        wrap.appendChild(c);
        return wrap;
      }
      if (p.type === "boolean") {
        c = el("input", { type: "checkbox", onchange: function () { setParam(p, c.checked); } });
        c.checked = v === true || v === "true";
        wrap.appendChild(c); wrap.appendChild(lab);
        return wrap;
      }
      wrap.appendChild(lab);
      if (def.symbolList === "watchlist" && p.paramName === "list") {
        c = T.dropdown({ value: T.lists.current(item).id, options: T.lists.options(), width: 280, onChange: function (nv) { if (nv) pickList(nv); } });
      } else if (p.options && p.options.length) {
        c = T.dropdown({ value: v, options: p.options, multi: p.multiSelect === true, placeholder: p.optional === false ? "select…" : "any", width: (p.style && p.style.popupWidth) || 300, onChange: function (nv) { setParam(p, nv); } });
      } else if (p.type === "endpoint" && p.optionsEndpoint) {
        c = T.dropdown({ value: v, options: optionsFetcher(p), multi: p.multiSelect === true, allowFree: true, placeholder: p.optional === false ? "select…" : "any", width: (p.style && p.style.popupWidth) || 340, onChange: function (nv) { setParam(p, nv); } });
      } else if (p.type === "date") {
        c = el("input", { type: "date", value: v ? String(v).slice(0, 10) : "", onchange: function () { setParam(p, c.value || null); } });
      } else if (p.type === "number") {
        c = el("input", { type: "number", value: v == null ? "" : v, step: "any", onchange: function () { setParam(p, c.value === "" ? null : Number(c.value)); } });
      } else {
        // a format hint, not "a,b,c": examples from the parameter's own options or description when it has them
        var ex = (p.options || []).slice(0, 2).map(function (o) { return o.value; }).filter(function (x) { return x != null && x !== ""; });
        var dm = !ex.length && /\b(?:e\.g\.|for example|such as)[:,]?\s*['"`]?([^'"`;)]{1,40})/i.exec(p.description || "");
        var hint = p.multiple ? (ex.length ? "comma-separated, e.g. " + ex.join(",") : dm ? "e.g. " + dm[1].trim() : "one or more, comma-separated") : "";
        c = el("input", { type: "text", value: v == null ? "" : Array.isArray(v) ? v.join(",") : v, placeholder: hint, spellcheck: "false",
          onchange: function () { setParam(p, c.value === "" ? null : c.value); },
          onkeydown: function (e) { if (e.key === "Enter") c.blur(); } });
      }
      wrap.appendChild(c);
      return wrap;
    }
    var TF_LABELS = { level: "Level", yoy: "YoY %", index: "Indexed", seasonal: "Seasonal" };
    function chip(label, on, fn, tip) { return el("button", { type: "button", class: on ? "on" : "", title: tip || label, onclick: fn }, label); }
    function drawStrip() {
      strip.textContent = "";
      if (!def.strip || !def.strip.length) return;
      var cur = eff(), ctl = (lastJson && lastJson.controls) || {};
      def.strip.forEach(function (s, i) {
        var opts = [];
        if (s.kind === "toggle") {                  // one chip switching a parameter between two values (recession shading)
          if (lastJson && ctl.ranges && !ctl.ranges.length) return;               // seasonal view: no date axis
          var isOn = String(cur[s.param] == null ? "" : cur[s.param]) === String(s.on);
          if (i && strip.children.length) strip.appendChild(el("span", { class: "sep" }));
          strip.appendChild(el("div", { class: "ptabs" }, [chip(s.label, isOn, function () { if (ctx.setView) ctx.setView(item, s.param, isOn ? s.off : s.on); }, s.tip)]));
          return;
        }
        if (s.kind === "range") {
          opts = (ctl.ranges || ["1Y", "3Y", "5Y", "10Y", "MAX"]).map(function (r) { return { label: r === "MAX" ? "Max" : r, value: r }; });
          if (lastJson && ctl.ranges && !ctl.ranges.length) return;               // seasonal view: always this year against five
        } else if (s.kind === "transform") {
          opts = (ctl.transforms || []).map(function (v) { return { label: TF_LABELS[v] || v, value: v }; });
          if (opts.length < 2) return;
        } else {
          var p = def.params.filter(function (x) { return x.paramName === s.param; })[0];
          opts = (p && p.options) || [];
        }
        var box = el("div", { class: "ptabs" });
        var val = s.kind === "transform" ? (cur.transform || ctl.transform) : s.kind === "range" ? cur.range : cur[s.param];
        if (s.kind === "range" && empty(val)) {
          var since = cur.since || cur.start;
          if (since) box.appendChild(chip("Since " + String(since).slice(0, 4), true, null, "The card's own start year (set in the settings); pick a range to override it."));
        }
        opts.forEach(function (o) {
          box.appendChild(chip(o.label, String(o.value).toUpperCase() === String(val == null ? "" : val).toUpperCase(), function () { if (ctx.setView) ctx.setView(item, s.param, o.value); }));
        });
        if (i && strip.children.length) strip.appendChild(el("span", { class: "sep" }));
        strip.appendChild(box);
      });
    }
    function drawParams() {
      drawStrip();
      pbar.textContent = "";
      if (item.choices && item.choices.length > 1 && ctx.swapChoice) {
        var cur = -1;
        item.choices.forEach(function (c, i) { if (c.w === item.widgetId && c.b === item.backend) cur = i; });
        var pick = T.dropdown({
          value: cur, width: 620, placeholder: "choose a dataset",
          options: item.choices.map(function (c, i) { return { label: (c.g ? c.g + " › " : "") + c.l, value: i }; }),
          onChange: function (v) { if (v != null && Number(v) !== cur) ctx.swapChoice(item, Number(v)); }
        });
        pick.classList.add("lib-dd");
        pbar.appendChild(el("div", { class: "p lib", title: item.choices.length + " datasets: pick one" }, [el("label", null, "Dataset (" + item.choices.length + ")"), pick]));
      }
      if (item.compact) return;
      var cur = eff();
      var vis = def.params.filter(function (p) { return (p.show !== false && p.paramName) || p === fileParam; });
      // grouped params are driven from the top bar; keep them out of the card unless required & empty
      vis = vis.filter(function (p) { return !ctx.groupOf(item, p.paramName) && stripParams().indexOf(p.paramName) < 0; });
      var shown = showAllParams ? vis : vis.slice(0, MAX_INLINE_PARAMS);
      shown.forEach(function (p) {
        var c = control(p === fileParam ? Object.assign({ label: "Document" }, p) : p, cur[p.paramName]);
        pbar.appendChild(c);
      });
      if (vis.length > MAX_INLINE_PARAMS) {
        pbar.appendChild(el("span", { class: "w-more", onclick: function () { showAllParams = !showAllParams; drawParams(); } }, showAllParams ? "fewer" : "+" + (vis.length - MAX_INLINE_PARAMS) + " more"));
      }
    }

    // ---------- body states ----------
    function clearBody() {
      if (frameTimer) { clearTimeout(frameTimer); frameTimer = null; }
      if (frameHardTimer) { clearTimeout(frameHardTimer); frameHardTimer = null; }
      if (chartMsgHandler) { window.removeEventListener("message", chartMsgHandler); chartMsgHandler = null; }
      if (loadTimer) { clearTimeout(loadTimer); loadTimer = null; }
      if (slowTimer) { clearTimeout(slowTimer); slowTimer = null; }
      if (plotNode && window.Plotly) { try { window.Plotly.purge(plotNode); } catch (e) { /* ignore */ } }
      plotNode = null; tv = null;
      Array.prototype.forEach.call(body.querySelectorAll("iframe"), function (f) { try { f.src = "about:blank"; } catch (e) { /* ignore */ } });
      blobUrls.forEach(function (u) { URL.revokeObjectURL(u); }); blobUrls = [];
      body.textContent = "";
    }
    function nonUSNote(text) {
      var yahoo = (ctx.siblings ? ctx.siblings(item, def) : []).some(function (s) { return /yahoo|yfinance/i.test(s.label) && !s.current; });
      return T.nonUSNote(def, eff().symbol, text, yahoo);
    }
    function message(kind, text, hint) {
      clearBody();
      if (kind !== "load") {
        var nu = nonUSNote(text);
        if (nu) { kind = "info"; text = "Not available for " + String(eff().symbol || "").toUpperCase().split(",")[0] + "."; hint = nu; }
      }
      var m = el("div", { class: "msg " + (kind === "err" ? "err" : "center") });
      if (kind === "load") m.appendChild(el("div", { class: "spin" }));
      if (kind === "err") m.appendChild(el("b", null, "Could not load"));
      m.appendChild(el("span", null, T.redact(text)));
      if (hint) m.appendChild(el("div", { class: "hint" }, hint));
      body.appendChild(m);
    }
    function srcName() {
      return T.sourceLabel(sources()) || "The data source";
    }
    function provName() {
      var p = def.params.filter(function (x) { return x.paramName === "provider"; })[0];
      return p && p.value ? String(p.value) : null;
    }
    /** Report a card error the classifier calls our bug, so local/logs/ui.log has the detail under the shown ref. */
    function reportBug(c) {
      try {
        var where = (T.S && T.S.cur ? T.S.cur.name + " › " + T.S.tabId + " › " : "") + (title.textContent || def.name) + " (" + def.widgetId + ")";
        var blob = new Blob([JSON.stringify({ ref: c.ref, where: where, detail: T.redact(c.detail).slice(0, 2000) })], { type: "application/json" });
        if (navigator.sendBeacon) navigator.sendBeacon("/api/clientlog", blob);
      } catch (e) { /* reporting is best effort */ }
    }
    /** Says which source answered empty (P4: a blank copper curve read as a fault here, not "Yahoo has no copper curve"). */
    function noData() {
      var pn = T.providerName(def);
      return (pn ? pn + " returned no data" : "No data returned") + " for these settings.";
    }
    function sibsHint(freeOnly) {
      var alts = ctx.alternatives ? ctx.alternatives(item, def, eff()) : null;
      var list = alts ? alts.filter(function (a) {
        return !a.current && !a.dim && (!freeOnly || !/\((?:needs a key|paid plan)\)/i.test(a.label || ""));
      }) : (ctx.siblings ? ctx.siblings(item, def).filter(function (s) { return !s.current; }) : []);
      if (!list.length) return null;
      return freeOnly
        ? "Switch to a free source with the selector at the top of this card."
        : "Try another source with the selector at the top of this card (" + (alts ? alts.length : list.length + 1) + " available).";
    }
    /** Every card type's footer: sources, status and notes from the envelope, and the data's own as-of date. */
    function stamp(extra) {
      foot.textContent = "";
      var env = lastEnvelope || lastJson;
      var line = T.sourceLine(sources());
      if (line) foot.appendChild(el("span", { class: "src-line", title: line }, line));      // part of the card, never clipped; PNG exports carry it too
      T.renderTrust(foot, env);
      if (extra) foot.appendChild(el("span", { class: "foot-note", title: extra }, extra));
      var asOf = T.asOfText(env, T.mtlNow());
      foot.appendChild(el("span", { class: "as-of", title: asOf.title, style: { marginLeft: "auto" } }, asOf.text));
    }

    // ---------- export (same ⤓ menu on every card that shows data) ----------
    function chartCSV(sep) {
      // prefer the full data the chart was built from; otherwise the series as plotted
      if (def.type === "chart" && lastJson && Array.isArray(lastJson.results) && lastJson.results.length) return T.table(el("div"), { data: lastJson.results }).toCSV(sep);
      if (item.view === "chart" && tv) return tv.toCSV(sep);
      return T.figureCSV(plotNode, sep);
    }
    function newsRows() {
      return T.normalizeRows(pick(lastJson, pick(def, "data.dataKey") || "")).map(function (r) {
        return { date: r.date, title: T.plain(r.title || r.headline || ""), source: T.plain(r.source || r.author || ""), url: r.url || r.link || "" };
      });
    }
    /** CSV and clipboard: the data, then a blank line and the source with the date (appended at the end, so pasting into Excel keeps the header row). */
    function withSource(text) {
      var line = T.sourceLine(sources()) || ("Source: " + T.sourceLabel(sources()));
      return line && line !== "Source: " ? text + "\r\n\r\n" + T.csvCell(line + "; retrieved " + T.isoDate(new Date()), ",") : text;
    }
    function exportItems() {
      var name = title.textContent || def.name, out = [], srcLine = T.sourceLine(sources());
      var chartShown = plotNode && ((def.type === "chart" && item.view !== "table") || item.view === "chart");
      if (chartShown) {
        out.push({ label: "Download chart (PNG)", onClick: function () { T.exportPNG(plotNode, name, T.fileName(name, "png"), srcLine); } });
        out.push({ label: "Download chart data (CSV)", onClick: function () { T.download(T.fileName(name, "csv"), withSource(chartCSV(",")), "text/csv"); } });
        out.push({ label: "Copy chart data", onClick: function () { T.copyText(withSource(chartCSV("\t")), "Chart data"); } });
      } else if (tv) {
        out.push({ label: "Copy table (paste into Excel)", onClick: function () { T.copyText(withSource(tv.toCSV("\t")), "Table"); } });
        out.push({ label: "Download CSV", onClick: function () { T.download(T.fileName(name, "csv"), withSource(tv.toCSV(",")), "text/csv"); } });
      } else if (def.type === "markdown" && lastJson != null && body.querySelector(".md")) {
        out.push({ label: "Copy text", onClick: function () { T.copyText(withSource(body.querySelector(".md").innerText), "Text"); } });
      } else if (def.type === "newsfeed" && lastJson != null && body.querySelector(".news")) {
        out.push({ label: "Copy headlines", onClick: function () { T.copyText(withSource(T.table(el("div"), { data: newsRows() }).toCSV("\t")), "Headlines"); } });
        out.push({ label: "Download headlines (CSV)", onClick: function () { T.download(T.fileName(name, "csv"), withSource(T.table(el("div"), { data: newsRows() }).toCSV(",")), "text/csv"); } });
      }
      return out;
    }

    // ---------- tools ----------
    function tool(text, tip, fn, on) { return el("button", { class: "icon-btn" + (on ? " on" : ""), type: "button", title: tip, onclick: fn }, text); }
    /** Flip, chart/table and the settings bar: a view preference of this card (state.cardview), not a layout edit. */
    function pref(k, v) { if (ctx.setPref) ctx.setPref(item, k, v); else { item[k] = v; ctx.onChange(); } }
    function drawTools() {
      tools.textContent = "";
      var isTable = def.type === "table" || (def.type === "chart" && item.view === "table");
      if (tv && isTable && item.view !== "chart" && !item.compact) {
        var f = el("input", { type: "search", class: "w-filter", placeholder: "filter…", value: filterVal, oninput: T.debounce(function () { filterVal = f.value; tv.setFilter(filterVal); }, 180) });
        tools.appendChild(f);
      }
      if (def.symbolList === "watchlist") {
        tools.appendChild(tool("Edit", "Create watchlists, add or remove symbols", function () {
          (lastJson && lastJson.results || []).forEach(function (r) { T.sec.learn(r.symbol, r.name); });
          T.lists.editWatchlist(T.lists.current(item).id, function (id) { if (id && id !== T.lists.current(item).id) pickList(id); else { paintTitle(); drawParams(); } });
        }));
      } else if (def.symbolList === "peers") {
        tools.appendChild(tool("Peers", "Change the peer group of this company", function () {
          (lastJson && lastJson.results || []).forEach(function (r) { T.sec.learn(r.symbol, r.name); });
          T.lists.editPeers(eff().symbol, lastJson && lastJson.auto ? lastJson.peers : null);
        }));
      }
      // the header keeps the title readable: export, reload and maximize stay visible, the rest sits in the "⋯" menu
      var more = [];
      if (def.type === "table" && tv && !tv.statement) {
        more.push({ label: (item.view === "chart" ? "✓ " : "") + "Plot this table", onClick: function () { pref("view", item.view === "chart" ? "table" : "chart"); renderData(); } });
        if (tv.canTranspose && item.view !== "chart") more.push({ label: (tv.isTransposed() ? "✓ " : "") + "Flip rows and columns", onClick: function () { pref("flip", !tv.isTransposed()); tv.setTranspose(item.flip); drawTools(); } });
      }
      // a flipped table lists its columns as rows: the picker still chooses which of them are shown
      if (tv && isTable && item.view !== "chart" && !tv.statement && tv.cols.length > 3) {
        more.push({ label: "Columns… (" + tv.shownFields().length + " of " + tv.cols.length + ")", onClick: function () { columnPicker(moreBtn); } });
      }
      if (def.type === "chart" && lastJson && lastJson.results) {
        tools.appendChild(tool(item.view === "table" ? "Chart" : "Table", "Switch between chart and data", function () { pref("view", item.view === "table" ? "chart" : "table"); renderData(); }));
      }
      paintSource();
      var ex = exportItems();
      // a narrow card keeps its title readable: export and maximize move into the "⋯" menu
      if (ex.length && narrowNow) more.unshift({ label: "Export…", onClick: function () { setTimeout(function () { T.menu(moreBtn, exportItems()); }, 0); } });
      else if (ex.length) {
        var exBtn = tool("⤓", "Export: copy the table, download CSV or the chart as PNG", function () { T.menu(exBtn, exportItems()); });
        tools.appendChild(exBtn);
      }
      if (def.params.some(function (p) { return p.show !== false && p.paramName && !ctx.groupOf(item, p.paramName) && stripParams().indexOf(p.paramName) < 0; })) {
        more.push({ label: item.compact ? "Show settings" : "Hide settings", onClick: function () { pref("compact", !item.compact); drawParams(); drawTools(); } });
      }
      tools.appendChild(tool("↻", "Reload", function () { refresh(true); }));
      if (narrowNow) more.push({ label: "Maximize (Esc to restore)", onClick: toggleMax });
      else tools.appendChild(tool("⤢", "Maximize / restore (double-click header, Esc)", toggleMax));
      more.push("sep", { label: "Remove from dashboard", danger: true, onClick: function () { ctx.onRemove(); } });
      var moreBtn = tool("⋯", "More: columns, flip, plot, settings, remove", function () { T.menu(moreBtn, more); });
      tools.appendChild(moreBtn);
    }
    /** Pick the columns of a list; the choice is remembered for this card (not a layout edit). */
    function columnPicker(anchor) {
      if (!tv) return;
      var shown = tv.shownFields(), box = el("div", { class: "menu col-pick" });
      var list = el("div", { class: "pop-list", style: { padding: "4px", maxHeight: "360px", overflowY: "auto" } });
      var order = shown.concat(tv.cols.map(function (c) { return c.field; }).filter(function (f) { return shown.indexOf(f) < 0; }));
      var name = {}; tv.cols.forEach(function (c) { name[c.field] = c.name; });
      function apply(next) {
        tv.setColumns(next);
        var isDefault = !next || JSON.stringify(next) === JSON.stringify(tv.defaultFields());
        if (ctx.setCols) ctx.setCols(item, isDefault ? null : next);
      }
      order.forEach(function (f) {
        var cb = el("input", { type: "checkbox" });
        cb.checked = shown.indexOf(f) >= 0;
        cb.addEventListener("change", function () {
          var now = tv.shownFields();
          if (cb.checked) now.push(f); else now = now.filter(function (x) { return x !== f; });
          if (!now.length) { cb.checked = true; return; }
          apply(now);
        });
        list.appendChild(el("label", { class: "opt col-opt" }, [cb, el("span", null, name[f] || f)]));
      });
      var foot = el("div", { class: "col-foot" }, [
        el("button", { class: "btn", type: "button", onclick: function () { T.closePop(); apply(null); } }, "Default columns"),
        el("button", { class: "btn", type: "button", onclick: function () { T.closePop(); apply(tv.cols.map(function (c) { return c.field; })); } }, "All")]);
      box.appendChild(list); box.appendChild(foot);
      T.popover(anchor, box, { width: 280 });
    }

    // ---------- renderers ----------
    function renderTable(data) {
      var sk = T.statementKind ? T.statementKind(def) : null;
      if (sk) {                                    // income statement, balance sheet, cash flow: line items down, periods across
        var pp = def.params.filter(function (x) { return x.paramName === "period"; })[0];
        tv = T.statement(body, {
          data: data, colDefs: pick(def, "data.table.columnsDefs"), kind: sk, provider: provName() || T.providerKey(def),
          period: eff().period, periodOptions: pp && pp.options, show: (ctx.overrides ? ctx.overrides(item) : {})._stmtview,
          onPeriod: function (v) { if (ctx.setView) ctx.setView(item, "period", v); else { item.params.period = v; ctx.onChange(); refresh(true); } },
          onShow: function (v) { if (ctx.setView) ctx.setView(item, "_stmtview", v === "values" ? null : v); },
          onCount: function (t) { stamp(t); }
        });
        if (!tv.baseRows.length) { message("info", noData()); tv = null; }
        return;
      }
      var noUpcoming = "";
      tv = T.table(body, {
        narrow: slimTier,
        onNoUpcoming: function (last) { noUpcoming = "No upcoming date in the source: the latest it lists is " + last + " (not updated since)"; },
        data: data, colDefs: pick(def, "data.table.columnsDefs"), dense: ctx.backend.id === "x", sort: item.sort, since: item.since,
        spec: T.colSpec ? T.colSpec(def.widgetId) : null, itemCols: item.cols, cols: ctx.colsel ? ctx.colsel(item) : null,
        curated: ctx.backend.id === "x", calendar: /calendar|upcoming/i.test(def.widgetId || ""),
        onAction: function (param, value, row) { return ctx.broadcast(item, param, value, row); },
        onCount: function (t) {
          var conv = lastJson && typeof lastJson.convention === "string" ? lastJson.convention : "";   // e.g. total return vs price change
          // a board that came back mostly empty says so (review C4), rather than a table of dashes
          var warn = (lastJson && typeof lastJson.warning === "string" ? lastJson.warning : "") || noUpcoming;
          stamp((warn ? "⚠ " + warn + " · " : "") + t + capNote() + (conv ? " · " + conv : ""));
        }
      });
      if (item.flip != null && tv.canTranspose) tv.setTranspose(item.flip);
      if (filterVal) tv.setFilter(filterVal);
      else if (capNote() && item.flip == null) tv.setFilter("");   // repaint so the footer can say the result was capped
      if (!tv.baseRows.length) { message("info", noData()); tv = null; }
    }
    function renderQuickChart(data) {
      var holder = el("div", { style: { display: "none" } });
      tv = T.table(holder, { data: data, colDefs: pick(def, "data.table.columnsDefs"), spec: T.colSpec ? T.colSpec(def.widgetId) : null });   // declared units, as in the table
      if (!tv.baseRows.length) { message("info", noData()); tv = null; return; }
      if (!item.chart) { item.chart = T.guessChart(tv.cols, tv.baseRows); item._guessedChart = true; }
      var qc = T.quickChart(body, tv, item.chart, function () { delete item._guessedChart; ctx.onChange(); });
      plotNode = qc.plot;
      stamp(tv.baseRows.length.toLocaleString() + " rows" + capNote());
    }
    /** The backend returns only the latest 5,000 rows of an EIA dataset unless a limit is set: tables and charts say so. */
    function capNote() {
      var eia = /\/api\/v1\/eia\//.test(def.endpoint) || String(eff().provider || "").toLowerCase() === "eia";
      return eia && tv && tv.baseRows.length === 5000 && empty(eff().limit) ? " · latest 5,000 rows; filter, set dates or a limit for more" : "";
    }
    function renderMarkdown(d) {
      var text;
      if (typeof d === "string") text = d;
      else if (Array.isArray(d)) text = d.map(function (x) { return typeof x === "string" ? x : x && (x.content || x.markdown || x.text) || "```\n" + JSON.stringify(x, null, 1) + "\n```"; }).join("\n\n---\n\n");
      else if (d && typeof d === "object") text = d.content || d.markdown || d.text || "```\n" + JSON.stringify(d, null, 1) + "\n```";
      else text = "";
      if (!text) return message("info", "Nothing to show.");
      var box = el("div", { class: "md" + (def.widgetId === "x_snapshot" ? " snap" : "") });
      box.innerHTML = T.md(text);   // T.md escapes all input and emits only whitelisted tags
      body.appendChild(box);
      stamp(d && d.period_ending ? "period ending " + d.period_ending : "");
    }
    function renderNews(d) {
      var rows = T.normalizeRows(d);
      // a terminal news view says what it is about, why it is empty, and whether its order is a ranking (kept) or time
      var meta0 = lastJson && typeof lastJson === "object" && !Array.isArray(lastJson) ? lastJson : {};
      if (!rows.length) return message("info", meta0.empty || "No articles found.");
      if (meta0.order !== "rank") rows.sort(function (a, b) { return String(b.date || "").localeCompare(String(a.date || "")); });
      var list = el("div", { class: "news" });
      rows.forEach(function (r) {
        var bodyBox = null;
        var it = el("div", { class: "news-item" });
        var t = el("div", { class: "t" }, T.plain(r.title || r.headline || "(untitled)"));
        var url = T.safeUrl(r.url || r.link);
        // spans, not bare text: adjacent text nodes form one flex item and the gap vanished ("03:33Top stories")
        var meta = el("div", { class: "m" }, [r.tag ? el("span", { class: "news-tag", title: r.tag === "Official" ? "Primary source: the institution's own release" : "Shown as a driver of the subject: " + r.tag }, T.plain(r.tag)) : null,
          r.date ? el("span", null, T.fmtDate(r.date)) : null, r.author || r.source ? el("span", null, T.plain(r.author || r.source)) : null]);
        if (url) meta.appendChild(el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, "open ↗"));
        it.appendChild(t); it.appendChild(meta);
        var ex = T.plain(r.excerpt || r.summary || r.text || "");
        if (ex) it.appendChild(el("div", { class: "e" }, ex));
        var full = r.body || r.content;
        t.addEventListener("click", function () {
          if (!full) { if (url) window.open(url, "_blank", "noopener"); return; }
          if (bodyBox) { bodyBox.remove(); bodyBox = null; return; }
          bodyBox = el("div", { class: "md" });
          bodyBox.innerHTML = T.md(full);
          it.appendChild(bodyBox);
        });
        list.appendChild(it);
      });
      body.appendChild(list);
      stamp((meta0.subject ? meta0.subject + " · " : "") + rows.length + (meta0.order ? " stories" : " articles") + (meta0.order === "rank" ? ", most material first" : ""));
    }
    function renderFiles(list) {
      if (!Array.isArray(list)) list = list ? [list] : [];
      list = list.filter(function (f) { return f && f.content != null; });
      if (!list.length) return message("info", "No document returned.");
      var bar = el("div", { class: "files-bar" }), view = el("div", { style: { flex: "1", minHeight: "0", display: "flex", flexDirection: "column" } });
      function show(i) {
        Array.prototype.forEach.call(bar.children, function (b, j) { b.classList.toggle("on", i === j); });
        view.textContent = "";
        var f = list[i], fmt = f.data_format || {}, type = String(fmt.data_type || "").toLowerCase();
        if (f.error_type) {
          var dc = T.classifyError(String(f.content), 0, { source: srcName() });
          view.appendChild(el("div", { class: "msg center" }, [el("span", null, dc.cls === "bug" ? "This document could not be opened." : dc.text), el("div", { class: "hint" }, dc.cls === "bug" ? "Try another document from the list." : dc.hint || "")]));
          return;
        }
        var mime = { pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif" }[type];
        if (mime) {
          var bin = atob(f.content), arr = new Uint8Array(bin.length);
          for (var k = 0; k < bin.length; k++) arr[k] = bin.charCodeAt(k);
          var u = URL.createObjectURL(new Blob([arr], { type: mime })); blobUrls.push(u);
          view.appendChild(type === "pdf" ? el("iframe", { src: u, title: fmt.filename || "document" }) : el("div", { class: "md" }, el("img", { src: u, alt: fmt.filename || "" })));
        } else {
          var box = el("div", { class: "md" });
          box.innerHTML = T.md(String(f.content));
          view.appendChild(box);
        }
      }
      list.forEach(function (f, i) { bar.appendChild(el("button", { type: "button", onclick: function () { show(i); } }, (f.data_format && f.data_format.filename) || "Document " + (i + 1))); });
      if (list.length > 1) body.appendChild(bar);
      body.appendChild(view);
      show(0);
      stamp(list.length + (list.length === 1 ? " document" : " documents"));
    }

    function renderData() {
      clearBody();
      var d = lastJson;
      try {
        if (def.type === "chart" && item.view !== "table") {
          var fig = pick(d, pick(def, "data.dataKey") || "");
          if (fig && fig.content && !fig.data) fig = fig.content;
          plotNode = el("div", { class: "plot" });
          body.appendChild(plotNode);
          T.plotFigure(plotNode, fig);
          // a figure whose points carry an id (layout.meta.clickParam, ids in customdata): a click sets that group,
          // as a table cell does (Issuer Yield Curve: a point loads the bond's trade history)
          var clickParam = fig && fig.layout && fig.layout.meta && fig.layout.meta.clickParam;
          if (clickParam && plotNode.on) plotNode.on("plotly_click", function (ev) {
            var pt = ev && ev.points && ev.points[0], v = pt && pt.customdata;
            if (v != null && v !== "") ctx.broadcast(item, clickParam, Array.isArray(v) ? v[0] : v);
          });
          stamp(T.chartFooterNote(d));
        } else if (def.type === "chart") {
          renderTable(d.results);
        } else if (def.type === "markdown") {
          renderMarkdown(pick(d, pick(def, "data.dataKey") || ""));
        } else if (def.type === "newsfeed") {
          renderNews(pick(d, pick(def, "data.dataKey") || ""));
        } else {
          var key = pick(def, "data.dataKey");
          var data = key ? pick(d, key) : (d && !Array.isArray(d) && d.results !== undefined ? d.results : d);
          if (item.view === "chart") renderQuickChart(data); else renderTable(data);
        }
      } catch (e) {
        var rc = T.classifyError("render: " + T.errDetail(e), 0, {});
        reportBug(rc);
        message("err", rc.text, rc.hint);
      }
      drawTools();
    }

    function missingParams(p) {
      var missing = def.params.filter(function (x) { return (x.optional === false || (item.require || []).indexOf(x.paramName) >= 0) && x !== fileParam && empty(p[x.paramName]); });
      (item.require || []).forEach(function (n) { if (!def.params.some(function (x) { return x.paramName === n; }) && empty(p[n])) missing.push({ paramName: n }); });
      return missing;
    }

    // ---------- loading ----------
    async function load(force, silent) {
      pending = false;
      if (force) slowRetries = 0;
      if (slowTimer) { clearTimeout(slowTimer); slowTimer = null; }
      var quiet = !!(silent && lastJson !== null && !isFrame);
      var my = ++seq;
      if (abort) abort.abort();
      abort = new AbortController();
      var p = eff();
      if (item._nocover) {
        drawTools(); foot.textContent = "";
        return message("info", item._nocover.text, item._nocover.hint);
      }
      // a group that waits for a pick from another card (Bond, set by clicking a CUSIP): say how to pick, fetch nothing
      var waiting = (ctx.groupsOf ? ctx.groupsOf(item) : []).filter(function (g) { return g.prompt && empty(g.value); })[0];
      if (waiting) { drawTools(); foot.textContent = ""; return message("info", waiting.prompt); }
      delete item._keyBlocked;
      if (item.needsKey) {                        // the dashboard builder knows this key is not set: say so without a doomed request
        item._keyBlocked = true;
        var kc = T.classifyError("Missing credential '" + item.needsKey + "'", 400, {
          source: srcName(), provider: provName(), hasFreeOnTab: ctx.hasFreeOnTab ? ctx.hasFreeOnTab(item) : false
        });
        var ksh = sibsHint(true);
        drawTools(); foot.textContent = "";
        return message("info", kc.text, ksh ? kc.hint + " " + ksh : kc.hint);
      }
      var missing = missingParams(p);
      if (missing.length) {
        drawTools();
        if (item.compact) { item.compact = false; drawParams(); }
        var names = missing.map(function (x) { return x.label || pretty(x.paramName); });
        var urlOnly = missing.every(function (x) { return /url$/i.test(x.paramName); });
        return message("info", urlOnly ? "Pick a filing in a list on this tab to open it here." : "Enter " + names.join(" and ") + " to load this card.",
          urlOnly ? "Or paste an SEC document URL in the field above." : "Clicking a row with that value in another card on this tab also fills it.");
      }
      var endpoint = (/^https?:/.test(def.endpoint) ? "" : base) + def.endpoint;
      try {
        if (isFrame) {
          var unsup = T.frameUnsupported(def, p);
          if (unsup) { clearBody(); drawTools(); foot.textContent = ""; return message("info", unsup.text, unsup.hint); }
          var frameSrc = endpoint + qs(p);
          if (ctx.backend.id === "x") {
            // a terminal view picks the document ({frame} or {message}); the frame then loads the backend's viewer directly,
            // since Firefox calls a redirected frame load cross-site and the backend's request guard refuses it
            message("load", "Loading…");
            var fr = await T.request(frameSrc, { headers: { Accept: "application/json" }, signal: abort.signal, timeout: T.requestTimeout(frameSrc, false) });
            var ftxt = await fr.text().catch(function () { return ""; }), fj = null;
            try { fj = JSON.parse(ftxt); } catch (fe) { /* not JSON */ }
            if (my !== seq || !alive) return;
            if (!fr.ok || !fj) throw T.httpError(fr.status, ftxt, fj ? null : "The terminal could not pick a document (HTTP " + fr.status + ").");
            lastEnvelope = fj;
            if (Array.isArray(fj.sources)) answerSources = fj.sources;
            var demoFrameOk = T.DEMO && /^\.\/demo\/frames\/[A-Za-z0-9._-]+\.html$/.test(fj.frame || "");
            if (!demoFrameOk && !/^http:\/\/(127\.0\.0\.1|localhost):\d+\/api\/v1\/sec\/filing_viewer_app\?/.test(fj.frame || "")) {
              clearBody(); drawTools(); stamp();
              return message("info", fj.message || "Nothing to show for this choice.");
            }
            frameSrc = fj.frame;
          }
          if (ctx.backend.id !== "x" && T.DEMO && typeof T.DEMO.resolveFrame === "function") {
            var demoFrame = T.DEMO.resolveFrame(frameSrc, def, p);
            if (!demoFrame) {
              clearBody(); drawTools(); foot.textContent = "";
              return message("info", "Not included in this frozen preview.", "Interactive embedded frames run locally. Download Libre Terminal for live data across all 28 dashboards and any global ticker.");
            }
            frameSrc = demoFrame;
          }
          clearBody();
          // sandboxed: the embedded page (a backend tool or a provider widget) cannot navigate the terminal itself
          // a frame shows nothing until its page has loaded (the rotation graph stayed black for 30 s+, review C4): say so
          var waitMsg = el("span", null, "Loading…");
          var waitBox = el("div", { class: "msg center frame-wait" }, [el("div", { class: "spin" }), waitMsg]);
          var fr2 = el("iframe", { title: def.name, referrerpolicy: "no-referrer", sandbox: "allow-scripts allow-same-origin allow-popups allow-forms" });
          fr2.style.visibility = "hidden";
          // the SEC filing viewer is a white document page on another origin: shown inverted to the dark palette (review G1)
          if (/filing_viewer_app/.test(frameSrc) || (T.DEMO && lastEnvelope && lastEnvelope.dark_doc)) fr2.classList.add("dark-doc");
          frameTimer = setTimeout(function () {
            if (!alive || my !== seq || !waitBox.isConnected) return;
            waitMsg.textContent = "Still loading this chart — fetching upstream data…";
            if (!waitBox.querySelector("button")) waitBox.appendChild(el("button", { class: "btn", type: "button", onclick: function () { refresh(true); } }, "Reload"));
          }, 12000);
          // a cold backend build can take longer than the 45 s request timeout: keep the frame loading, only reword the hint
          frameHardTimer = setTimeout(function () {
            if (!alive || my !== seq || !waitBox.isConnected) return;
            waitMsg.textContent = "This view is taking unusually long. It still appears here when ready, or press Reload.";
          }, 60000);
          var expectedOrigin = null;
          try { expectedOrigin = new URL(frameSrc, window.location.href).origin; } catch (e) { expectedOrigin = base || window.location.origin; }
          if (expectedOrigin === "null") expectedOrigin = null;
          var onChartMsg = function (ev) {
            if (!ev || !ev.data || ev.data.type !== "openbb-chart-ready" || !fr2 || ev.source !== fr2.contentWindow) return;
            if (expectedOrigin && ev.origin !== expectedOrigin) return;
            if (frameTimer) { clearTimeout(frameTimer); frameTimer = null; }
            if (frameHardTimer) { clearTimeout(frameHardTimer); frameHardTimer = null; }
            if (waitBox.parentNode) waitBox.parentNode.removeChild(waitBox);
            fr2.style.visibility = "";
            window.removeEventListener("message", onChartMsg);
            if (chartMsgHandler === onChartMsg) chartMsgHandler = null;
          };
          chartMsgHandler = onChartMsg;
          window.addEventListener("message", onChartMsg);
          if (abort && abort.signal) {
            abort.signal.addEventListener("abort", function () {
              if (frameTimer) { clearTimeout(frameTimer); frameTimer = null; }
              if (frameHardTimer) { clearTimeout(frameHardTimer); frameHardTimer = null; }
              window.removeEventListener("message", onChartMsg);
              if (chartMsgHandler === onChartMsg) chartMsgHandler = null;
              try { fr2.src = "about:blank"; } catch (e) { /* ignore */ }
            }, { once: true });
          }
          fr2.addEventListener("load", function () {
            window.removeEventListener("message", onChartMsg);
            if (chartMsgHandler === onChartMsg) chartMsgHandler = null;
            if (!alive || my !== seq) return;
            if (frameTimer) { clearTimeout(frameTimer); frameTimer = null; }
            if (frameHardTimer) { clearTimeout(frameHardTimer); frameHardTimer = null; }
            if (waitBox.parentNode) waitBox.parentNode.removeChild(waitBox);
            fr2.style.visibility = "";
          });
          fr2.addEventListener("error", function () {
            window.removeEventListener("message", onChartMsg);
            if (chartMsgHandler === onChartMsg) chartMsgHandler = null;
            if (!alive || my !== seq) return;
            if (base && ctx.backendDown && ctx.backend) {
              T.request(base + "/", { mode: "no-cors", timeout: 5000 }).catch(function () { ctx.backendDown(ctx.backend.id); });
            }
            clearBody();
            body.appendChild(el("div", { class: "msg err center" }, [el("b", null, "Could not load"), el("span", null, "This view did not load."),
              el("button", { class: "btn", type: "button", onclick: function () { refresh(true); } }, "Retry")]));
          });
          fr2.src = frameSrc;
          body.appendChild(waitBox);
          body.appendChild(fr2);
          drawTools(); stamp();                         // frames too: sources and as-of in the footer
          return;
        }
        if (item.manual && !force) {
          // the dashboard marked this dataset as too heavy to fetch automatically
          clearBody(); foot.textContent = ""; drawTools();
          var ask = el("div", { class: "msg center" }, [el("span", null, item.manual), el("button", { class: "btn", type: "button", onclick: function () { load(true); } }, "Load it")]);
          body.appendChild(ask);
          return;
        }
        if (!quiet) {
          message("load", "Loading…");
          loadTimer = setTimeout(function () {
            if (!alive || my !== seq) return;
            var sp = body.querySelector(".msg span");
            if (sp) sp.textContent = "Still loading — first build fetches from upstream…";
            loadTimer = setTimeout(function () {
              if (!alive || my !== seq) return;
              var sp2 = body.querySelector(".msg span");
              if (sp2) sp2.textContent = "Building in the background — slow datasets take a bit longer on first launch.";
            }, 10000);
          }, 5000);
        }
        if (isFiles) {
          var files;
          if (fileParam) {
            var sel = p[fileParam.paramName];
            // the document list depends on the other parameters (Bill ID): a pick made for another bill is dropped and
            // the new bill's first document opens (review B2: 119-hr-2306 kept showing BILLS-119hr1enr.pdf)
            var fileSig = JSON.stringify(Object.keys(p).filter(function (k) { return k !== fileParam.paramName; }).sort().map(function (k) { return [k, p[k]]; }));
            if (!empty(sel) && lastFileSig !== null && lastFileSig !== fileSig) { sel = null; delete live[fileParam.paramName]; }
            lastFileSig = fileSig;
            if (empty(sel)) {
              var opts = await optionsFetcher(fileParam)();
              if (my !== seq) return;
              // a source may list a placeholder with no document ("No text available for this amendment currently.",
              // value null): say it instead of sending a null URL (review B2: HTTP 422)
              var real = opts.filter(function (o) { return o && o.value != null && o.value !== ""; });
              if (!real.length) return message("info", (opts[0] && opts[0].label) || "No documents available for these settings.");
              sel = [real[0].value];
              live[fileParam.paramName] = sel;
              drawParams();
            }
            var selList = (Array.isArray(sel) ? sel : [sel]).filter(function (v) { return v != null && v !== ""; });
            if (!selList.length) return message("info", "No documents available for these settings.");
            var bodyObj = {}; bodyObj[fileParam.paramName] = selList;
            var ft = await postText(endpoint, bodyObj, abort.signal);
            if (!ft) throw new Error("The source returned nothing for this document. It may have moved; try another one from the list.");
            files = JSON.parse(ft);
          } else {
            files = await getJSON(endpoint + qs(p), force, abort.signal);
          }
          if (my !== seq) return;
          slowRetries = 0;
          clearBody(); renderFiles(files); drawTools();
          return;
        }
        var json = await getJSON(endpoint + qs(p), force, abort.signal);
        if (my !== seq || !alive) return;
        lastJson = json; lastEnvelope = json && typeof json === "object" && !Array.isArray(json) ? json : null;
        if (json && Array.isArray(json.sources)) answerSources = json.sources;
        else if (ctx.backend.id !== "x") answerSources = null;      // the named source answers again
        paintSource();
        if (def.strip) drawStrip();
        failed = false;
        slowRetries = 0;
        if (ctx.backend.id === "x" && json && Array.isArray(json.results)) json.results.forEach(function (r) { if (r && r.symbol && r.name) T.sec.learn(r.symbol, r.name); });   // names for recent tickers
        if (json == null) { drawTools(); foot.textContent = ""; return message("info", noData(), sibsHint()); }
        renderData();
      } catch (e) {
        if (e && e.name === "AbortError") return;
        if (my !== seq || !alive) return;
        if (e && Array.isArray(e.sources) && e.sources.length) { answerSources = e.sources; paintSource(); }
        var msg = e && e.message || String(e);
        if (quiet) { stamp("auto-refresh failed — showing previous data"); return; }
        if (msg === "TOO_LARGE") {
          foot.textContent = ""; drawTools();
          if (item.compact) { item.compact = false; drawParams(); }
          return message("info", "This result is very large (" + Math.round(e.size / 1e6) + " MB).", "Narrow it with the filters above, or press ↻ to load all of it anyway.");
        }
        lastJson = null;
        foot.textContent = ""; drawTools();
        var backendGone = e && e.backendDown;
        if (!backendGone && /Failed to fetch|NetworkError|Load failed/i.test(msg)) {
          var up = await T.request(base + "/", { mode: "no-cors", timeout: 5000 }).then(function () { return true; }, function () { return false; });
          if (my !== seq || !alive) return;
          backendGone = !up;
          if (up) msg = "upstream failed: the source did not answer (Bad Gateway)";
        }
        if (backendGone) {
          failed = true;
          if (ctx.backendDown) ctx.backendDown(ctx.backend.id);
          return message("info", "The data backend is restarting.", "This card reloads by itself as soon as it is back (usually within a minute).");
        }
        var cctx = {
          userMessage: !!(e && e.userMessage), source: srcName(), provider: provName(),
          hasFreeOnTab: ctx.hasFreeOnTab ? ctx.hasFreeOnTab(item) : false,
          symbol: def.params.some(function (p) { return p.paramName === "symbol"; }) ? String(eff().symbol || "").toUpperCase().split(",")[0] : null,
          param: function (n) { var np = def.params.filter(function (x) { return x.paramName === n; })[0]; return np ? (np.label || pretty(n)) : pretty(n); }
        };
        // the terminal's own views name the kind; only raw backend answers are still read by T.classifyError
        var c = e && e.errorKind ? T.classifyKind(e.errorKind, msg, cctx, e) : T.classifyError(msg, e && e.status, cctx);
        failed = true;
        if (c.cls === "slow" && slowRetries < T.SLOW_RETRY_DELAYS.length) {
          var delayS = T.SLOW_RETRY_DELAYS[slowRetries++];
          message("load", c.text || "This dataset is still building in the background.", T.slowRetryMessage(delayS));
          slowTimer = setTimeout(function () {
            slowTimer = null;
            if (alive && my === seq) load(false);
          }, delayS * 1000);
          return;
        }
        if (c.cls === "key" || c.cls === "paid") item._keyBlocked = true;
        if (c.cls === "input" && c.param && item.compact) { item.compact = false; drawParams(); }
        if (c.cls === "bug") reportBug(c);
        var sh = (c.cls === "covered" || c.cls === "upstream" || c.cls === "paid" || c.cls === "key") ? sibsHint(c.cls === "paid" || c.cls === "key") : null;
        message(c.kind, c.text, sh ? c.hint + " " + sh : c.hint);
      }
    }
    function measure() {
      var r = node.getBoundingClientRect(), s = ctx.scroller.getBoundingClientRect();
      // terminal views (/x/) answer from the server's warmed cache, so they load a screen ahead; data services, 300 px ahead
      var ahead = ctx.backend && ctx.backend.id === "x" ? 900 : 300;
      visible = node.classList.contains("max") || (r.width > 0 && r.bottom > s.top - ahead && r.top < s.bottom + ahead);
    }
    // A forced reload of a card that is off screen waits until it is near the viewport, and then still bypasses the
    // memory and browser caches for this card only (other cards keep their cached answers).
    var pendingForce = false;
    function refresh(force) {
      if (!alive) return;
      measure();
      if (!visible) {
        if (node.isConnected && (item.needsKey || item.manual || missingParams(eff()).length)) return load(false);
        pending = true;
        if (force) { pendingForce = true; dropCached((/^https?:/.test(def.endpoint) ? "" : base) + def.endpoint + qs(eff())); }
        return;
      }
      pendingForce = false;
      load(force);
    }

    // Lazy loading: only fetch once the card is near the viewport (checked by the app on layout / scroll).
    // Responsive boards (review P4 / 1440 px): step columns down across width tiers so boards avoid sideways scroll.
    T.boardTier = T.boardTier || function (w) { return !w || w >= 920 ? 0 : w >= 640 ? 1 : w >= 460 ? 2 : 3; };
    var narrowNow = false, slimTier = 0;
    function checkNarrow() {
      var w = node.getBoundingClientRect().width, n = w > 0 && w < 480, st = T.boardTier(w);
      node.classList.toggle("xs", w > 0 && w < 380);
      if (st !== slimTier) {
        slimTier = st;
        node.classList.toggle("slim", st >= 1);
        if (tv && tv.setNarrow) tv.setNarrow(st);
      }
      if (n === narrowNow) return;
      narrowNow = n;
      node.classList.toggle("narrow", n);
      drawTools();
    }
    var needPlotResize = false;
    function check() {
      if (!alive) return;
      checkNarrow();
      measure();
      if (visible && needPlotResize && plotNode && window.Plotly && plotNode.data) {
        try { window.Plotly.Plots.resize(plotNode); needPlotResize = false; } catch (e) { /* ignore */ }
      }
      if ((visible || item.needsKey || item.manual) && pending) { var f = pendingForce; pendingForce = false; load(f); }
    }
    var ro = new ResizeObserver(T.debounce(function () {
      if (!alive) return;
      checkNarrow();
      if (plotNode && window.Plotly && plotNode.data) {
        var br = body.getBoundingClientRect();
        if (br.width > 0 && br.height > 0) {
          try { window.Plotly.Plots.resize(plotNode); needPlotResize = false; } catch (e) { /* ignore */ }
        } else {
          needPlotResize = true;
        }
      }
    }, 120));
    ro.observe(body);
    if (typeof def.refetchInterval === "number" && def.refetchInterval >= 30000) timer = setInterval(function () { if (visible && !document.hidden) load(true, true); }, def.refetchInterval);

    function clearLive(changedParam) {
      var had = Object.keys(live).length > 0;
      live = {};
      filterVal = "";
      if (clearDepParams(changedParam, "live")) had = true;
      drawParams(); paintTitle(); paintSource();
      if (had) {
        if (abort) { abort.abort(); abort = null; }
        if (isFrame || lastJson !== null) { lastJson = null; message("load", "Waiting…"); }
      }
      return had;
    }

    drawParams(); drawTools(); paintTitle(); paintSource();
    message("load", "Loads when scrolled into view…");

    return {
      node: node, head: head, resize: rz, item: item, def: def, isFrame: isFrame,
      refresh: refresh,
      rerender: function () { if (alive && lastJson !== null && !isFrame) renderData(); else if (alive) refresh(false); },
      ownsFrame: function (win) { var fr = body.querySelector("iframe"); return !!(fr && fr.contentWindow === win); },
      check: check,
      /** Reload only if the last attempt failed (used when a backend comes back). */
      retryIfFailed: function () { if (failed && alive) { failed = false; pending = true; check(); } },
      backend: ctx.backend.id,
      redrawParams: function (changedParam) {      // the dashboard ticker changed: row picks no longer apply
        slowRetries = 0;
        clearLive(changedParam);             // saved picks of the old ticker are masked in eff(), the layout is not edited
      },
      clearLive: clearLive,
      effParams: function () { return eff(); },    // the parameters the next request sends (checks read them)
      hasParam: function (name) {
        return def.params.some(function (p) {
          if (p.paramName !== name) return false;
          if (p === fileParam || (p.roles && p.roles.indexOf("fileSelector") >= 0)) return def.widgetId === "tmx_equity_filings_viewer_obb" && name === "url";
          return true;
        });
      },
      /** A row clicked on another card sets this parameter for now (not saved in the layout). */
      setParamValue: function (name, value) {
        var pd = def.params.filter(function (p) { return p.paramName === name; })[0];
        live[name] = pd && pd.multiSelect === true && !Array.isArray(value) ? [value] : value;
        drawParams();
        if ((isFrame || isFiles) && node.isConnected) load(false); else refresh(false);
      },
      destroy: function () { alive = false; ++seq; if (abort) abort.abort(); ro.disconnect(); if (timer) clearInterval(timer); clearBody(); }
    };
  };

  /**
   * A card whose definition is not available: its backend is down or restarting (it comes back by itself), or the
   * running backends no longer offer that function. Never shows the raw widget id as a title.
   */
  T.missingWidget = function (item, onRemove, backend) {
    var down = backend && backend.up !== true;
    var name = item.title || item.name || String(item.widgetId || "Card").replace(/_(obb|custom_obb)$/, "").replace(/_/g, " ").replace(/^\w/, function (c) { return c.toUpperCase(); });
    var node = el("div", { class: "w" });
    var head = el("div", { class: "w-head" }, [el("span", { class: "w-title", title: name }, name), el("span", { class: "w-sub" }, down ? "waiting for the data backend" : ""),
      el("div", { class: "w-tools" }, el("button", { class: "icon-btn", type: "button", title: "Remove from dashboard", onclick: onRemove }, "✕"))]);
    node.appendChild(head);
    var stopped = down && (backend.service === "stopped" || backend.service === "not started");
    var msg = (!backend && item.backend === "v4")
      ? el("div", { class: "msg center" }, [el("span", null, "This card uses the optional legacy V4 backend."), el("div", { class: "hint" }, "Start with Start.cmd --with-v4 (after Install.cmd --with-v4), or remove the card with ✕.")])
      : stopped
      ? el("div", { class: "msg center" }, [el("span", null, backend.name + (backend.service === "stopped" ? " has stopped (it crashed repeatedly)." : " is not running.")), el("div", { class: "hint" }, "Start the terminal again with Start.cmd (or start.sh), then reload this page.")])
      : down
      ? el("div", { class: "msg center" }, [el("div", { class: "spin" }), el("span", null, "The data backend is starting or restarting."), el("div", { class: "hint" }, "This card loads by itself as soon as it is back (usually within a minute).")])
      : el("div", { class: "msg center" }, [el("span", null, "This data function is not offered by the running backends any more."), el("div", { class: "hint" }, "Remove the card with ✕, or reset the tab from the ⋯ menu.")]);
    node.appendChild(el("div", { class: "w-body" }, msg));
    var rz = el("div", { class: "w-resize" }); node.appendChild(rz);
    return { node: node, head: head, resize: rz, item: item, def: null, missing: true, isFrame: false, backend: item.backend, refresh: function () {}, check: function () {}, retryIfFailed: function () {},
      redrawParams: function () {}, hasParam: function () { return false; }, setParamValue: function () {}, destroy: function () {} };
  };
})(window.T);
