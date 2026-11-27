/* Static interactive preview shim: intercepts T.request and serves the frozen JSON snapshot from ./demo/data/. */
(function (T) {
  "use strict";
  if (!T || typeof T.request !== "function") return;

  var realRequest = T.request;
  var realAsOfText = T.asOfText;
  var DEMO_BASE = "./demo/data/";
  var VIRTUAL_V5 = "https://demo.libre-terminal.local";
  var DATA_ORIGINS = [VIRTUAL_V5, "http://127.0.0.1:6900", "http://127.0.0.1:6950", "http://127.0.0.1:7900", "http://127.0.0.1:7950"];
  var CASE_UPPER = { symbol: 1, symbols: 1, company: 1, cusip: 1 };
  var CASE_LOWER = { country: 1, provider: 1, period: 1, duration: 1, metric: 1, option_type: 1 };
  var VOLATILE_DATES = { start_date: 1, end_date: 1, date: 1 };
  var SHOWCASE_DASHES = { "d:markets": 1, "d:eq-company": 1, "d:credit": 1, "d:rates": 1, "d:options": 1, "d:energy": 1, "d:trade": 1 };
  var DEMO_ERR = {
    error: { kind: "demo_unavailable", message: "Not included in this frozen preview.", retryable: false },
    detail: "Not included in this frozen preview.",
    user_message: true,
    sources: []
  };

  var manifestP = null;
  var manifestObj = null;
  var staticCache = {};
  var packPromises = {};
  var stateRev = 1;
  var memState = null;

  function jsonResp(obj, status, extraHeaders) {
    var h = new Headers({ "Content-Type": "application/json; charset=utf-8" });
    if (extraHeaders && typeof extraHeaders === "object") {
      Object.keys(extraHeaders).forEach(function (k) {
        if (extraHeaders[k] != null && extraHeaders[k] !== "") h.set(k, String(extraHeaders[k]));
      });
    }
    var txt = obj === null || obj === undefined ? "" : JSON.stringify(obj);
    return new Response(txt, { status: status || 200, headers: h });
  }

  function loadStatic(relPath) {
    if (!staticCache[relPath]) {
      staticCache[relPath] = realRequest(DEMO_BASE + relPath, { timeout: 25000 }).then(function (r) {
        if (!r.ok) throw new Error("Failed to load preview asset " + relPath);
        return r.json();
      });
    }
    return staticCache[relPath];
  }

  function loadManifest() {
    if (!manifestP) {
      manifestP = loadStatic("manifest.json").then(function (m) {
        manifestObj = m || {};
        var dtEl = document.getElementById("demo-date");
        if (dtEl && manifestObj.snapshot_date) dtEl.textContent = manifestObj.snapshot_date;
        return manifestObj;
      });
    }
    return manifestP;
  }

  function loadPack(packFile) {
    if (!packPromises[packFile]) {
      packPromises[packFile] = loadStatic(packFile);
    }
    return packPromises[packFile];
  }

  function stripOrigin(url) {
    var s = String(url || "");
    for (var i = 0; i < DATA_ORIGINS.length; i++) {
      if (s.indexOf(DATA_ORIGINS[i]) === 0) return s.slice(DATA_ORIGINS[i].length) || "/";
    }
    try {
      var u = new URL(s, window.location.href);
      if (u.origin === window.location.origin) {
        var basePath = window.location.pathname.replace(/[^/]*$/, "");
        var p = u.pathname;
        if (basePath.length > 1 && p.indexOf(basePath) === 0) p = "/" + p.slice(basePath.length);
        return p + u.search;
      }
      return u.pathname + u.search;
    } catch (e) {
      return s;
    }
  }

  function canonicalKey(url, opts, dropDates) {
    opts = opts || {};
    var method = String(opts.method || "GET").toUpperCase();
    var rel = stripOrigin(url);
    var u = new URL(rel, "https://local.invalid");
    var pairs = [];
    u.searchParams.forEach(function (v, k) {
      if (v == null || v === "" || k === "_" || k === "t") return;
      if (dropDates && VOLATILE_DATES[k]) return;
      if (CASE_UPPER[k]) v = String(v).toUpperCase();
      else if (CASE_LOWER[k]) v = String(v).toLowerCase();
      else if (v === "True" || v === "False") v = v.toLowerCase();
      pairs.push([k, v]);
    });
    if (method === "POST" && opts.body) {
      try {
        var parsed = JSON.parse(opts.body);
        if (parsed && typeof parsed === "object") {
          Object.keys(parsed).sort().forEach(function (k) {
            var val = parsed[k];
            if (Array.isArray(val)) val = val.slice().sort().join(",");
            pairs.push(["__body_" + k, String(val)]);
          });
        }
      } catch (e) {
        pairs.push(["__body", String(opts.body)]);
      }
    }
    pairs.sort(function (a, b) { return a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0); });
    var q = pairs.map(function (kv) { return encodeURIComponent(kv[0]) + "=" + encodeURIComponent(kv[1]); }).join("&");
    return method + " " + u.pathname + (q ? "?" + q : "");
  }

  function defaultState(m) {
    var st = { v: 4, _rev: 1, dashboards: {}, last: { id: "d:markets", tab: "overview" }, ui: { collapsed: {} }, groups: {} };
    if (m && m.default_groups && typeof m.default_groups === "object") {
      st.groups = JSON.parse(JSON.stringify(m.default_groups));
    }
    return st;
  }

  function getDemoState(m) {
    if (memState) return memState;
    try {
      var raw = window.sessionStorage ? window.sessionStorage.getItem("lt_demo_state_v1") : null;
      if (raw) {
        var parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object" && parsed.v === 4) { memState = parsed; return memState; }
      }
    } catch (e) { /* ignore storage errors */ }
    memState = defaultState(m);
    return memState;
  }

  function saveDemoState(bodyTxt) {
    try {
      var obj = JSON.parse(bodyTxt);
      if (obj && typeof obj === "object") {
        obj._rev = ++stateRev;
        memState = obj;
        if (window.sessionStorage) window.sessionStorage.setItem("lt_demo_state_v1", JSON.stringify(obj));
      }
    } catch (e) { /* ignore */ }
    return jsonResp({ ok: true, rev: stateRev }, 200);
  }

  function searchDemo(url, m) {
    var rel = stripOrigin(url);
    var u = new URL(rel, "https://local.invalid");
    var q = String(u.searchParams.get("query") || "").trim().toLowerCase();
    var idx = (m && Array.isArray(m.search_index)) ? m.search_index : [];
    if (!q) return jsonResp({ results: [] }, 200);
    var terms = q.split(/\s+/);
    var hits = idx.filter(function (r) {
      var hay = (String(r.symbol || "") + " " + String(r.name || "") + " " + String(r.exchange || "")).toLowerCase();
      return terms.every(function (t) { return hay.indexOf(t) >= 0; });
    });
    hits.sort(function (a, b) {
      var ae = String(a.symbol || "").toLowerCase() === q ? 0 : String(a.symbol || "").toLowerCase().indexOf(q) === 0 ? 1 : 2;
      var be = String(b.symbol || "").toLowerCase() === q ? 0 : String(b.symbol || "").toLowerCase().indexOf(q) === 0 ? 1 : 2;
      return ae - be || (!!b.featured - !!a.featured) || String(a.symbol || "").localeCompare(String(b.symbol || ""));
    });
    return jsonResp({ results: hits.slice(0, 12) }, 200);
  }

  async function resolveRoute(url, opts) {
    var m = await loadManifest();
    var exact = canonicalKey(url, opts, false);
    var packName = (m.routes && m.routes[exact]) || null;
    var lookupKey = exact;

    if (!packName) {
      var nodate = canonicalKey(url, opts, true);
      if (m.fallback_routes && m.fallback_routes[nodate]) {
        lookupKey = m.fallback_routes[nodate];
        packName = m.routes[lookupKey];
      }
    }

    // Starter watchlist fallback: if user edits/removes symbols in demo mode, filter captured starter rows
    if (!packName && exact.indexOf("GET /x/watchlist?") === 0 && m.watchlist_default_key && m.routes[m.watchlist_default_key]) {
      var u = new URL(stripOrigin(url), "https://local.invalid");
      var syms = String(u.searchParams.get("symbols") || "").split(",").map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean);
      var wlPack = await loadPack(m.routes[m.watchlist_default_key]);
      var wlEntry = wlPack && wlPack.entries && wlPack.entries[m.watchlist_default_key];
      if (wlEntry && wlEntry.body && Array.isArray(wlEntry.body.results)) {
        var bySym = {};
        wlEntry.body.results.forEach(function (r) { if (r && r.symbol) bySym[String(r.symbol).toUpperCase()] = r; });
        if (syms.length && syms.every(function (s) { return !!bySym[s]; })) {
          var filtered = syms.map(function (s) { return bySym[s]; });
          return jsonResp(Object.assign({}, wlEntry.body, { results: filtered }), wlEntry.status || 200, wlEntry.headers);
        }
      }
    }

    if (!packName) return jsonResp(DEMO_ERR, 404);
    var pack = await loadPack(packName);
    var entry = pack && pack.entries && pack.entries[lookupKey];
    if (!entry) return jsonResp(DEMO_ERR, 404);
    return jsonResp(entry.body, entry.status || 200, entry.headers);
  }

  // Override navigator.sendBeacon so client error telemetry does not hit network
  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
    var origBeacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function (url, data) {
      if (/\/api\/clientlog$/.test(String(url || ""))) return true;
      return origBeacon(url, data);
    };
  }

  T.DEMO = {
    active: true,
    resolveFrame: function (frameSrc, def, params) {
      if (!manifestObj || !manifestObj.frames) return null;
      var ep = String((def && def.endpoint) || "");
      var sym = String((params && params.symbol) || "").toUpperCase();
      return manifestObj.frames[ep + "|" + sym] || manifestObj.frames[ep] || null;
    }
  };

  T.asOfText = function (env, fetchedAt) {
    var r = realAsOfText ? realAsOfText(env, fetchedAt) : { text: "", title: "" };
    if (r && r.text && r.text.indexOf("updated ") === 0) {
      var sd = (manifestObj && manifestObj.snapshot_date) || "preview";
      return { text: "snapshot " + sd, title: "Frozen preview captured " + sd };
    }
    return r;
  };

  T.request = function (url, opts) {
    opts = opts || {};
    var rel = stripOrigin(url);
    var path = rel.split("?")[0];
    var method = String(opts.method || "GET").toUpperCase();
    var isV5 = String(url || "").indexOf(VIRTUAL_V5) === 0;

    if (path === "/api/config") return loadStatic("config.json").then(function (d) { return jsonResp(d, 200); });
    if (path === "/api/dashboards") return loadStatic("dashboards.json").then(function (d) { return jsonResp(d, 200); });
    if (path === "/widgets.json" && !isV5) return loadStatic("widgets-x.json").then(function (d) { return jsonResp(d, 200); });
    if (path === "/widgets.json" && isV5) return loadStatic("widgets-v5.json").then(function (d) { return jsonResp(d, 200); });
    if (path === "/apps.json" && isV5) return Promise.resolve(jsonResp([], 200));
    if (path === "/" && isV5) return Promise.resolve(jsonResp({ ok: true }, 200));
    if (path === "/api/services") {
      return Promise.resolve(jsonResp({
        v5: { state: "running", reachable: true },
        v4: { state: "not started" },
        restart: { restarting: false, seq: 0 }
      }, 200));
    }
    if (path === "/api/warm") return Promise.resolve(jsonResp({ phase: "ready" }, 200));
    if (path === "/api/services/restart") {
      if (T.toast) T.toast("Preview mode: download Libre Terminal locally to run live services.");
      return Promise.resolve(jsonResp({ ok: false, demo: true }, 200));
    }
    if (path === "/api/clientlog") return Promise.resolve(new Response("", { status: 204 }));
    if (path === "/api/state") {
      if (method === "PUT") return Promise.resolve(saveDemoState(opts.body));
      return loadManifest().then(function (m) { return jsonResp(getDemoState(m), 200); });
    }
    if (path === "/x/search") return loadManifest().then(function (m) { return searchDemo(url, m); });
    if (path.indexOf("/x/") === 0 || path.indexOf("/api/v1/") === 0) return resolveRoute(url, opts);

    return realRequest(url, opts);
  };

  function syncDemoChrome() {
    var bar = document.getElementById("demo-bar");
    if (bar) {
      var updateBarH = function () {
        document.documentElement.style.setProperty("--demo-bar-h", (bar.offsetHeight || 38) + "px");
      };
      updateBarH();
      if (typeof ResizeObserver === "function") new ResizeObserver(updateBarH).observe(bar);
      window.addEventListener("resize", updateBarH);
    }

    var hashApplied = false;
    function syncActive() {
      var S = T.S;
      if (!S || !S.cur) return;
      var btns = document.querySelectorAll("#demo-bar .demo-jump");
      Array.prototype.forEach.call(btns, function (b) {
        var on = b.getAttribute("data-dash") === S.cur.id;
        b.classList.toggle("on", on);
      });
      var targetHash = "#" + S.cur.id + (S.tabId ? "/" + S.tabId : "");
      if (hashApplied && window.location.hash !== targetHash && window.history && window.history.replaceState) {
        try { window.history.replaceState(null, "", targetHash); } catch (e) { /* ignore */ }
      }
    }

    function markSidebar() {
      var list = document.getElementById("dash-list");
      if (!list) return;
      Array.prototype.forEach.call(list.querySelectorAll(".dash-item"), function (node) {
        if (node.querySelector(".demo-ready")) return;
        var nm = node.querySelector(".nm");
        var txt = nm ? nm.textContent : "";
        var S = T.S;
        var match = S && S.generated && S.generated.filter(function (d) { return d.name === txt && SHOWCASE_DASHES[d.id]; })[0];
        if (match) {
          var dot = document.createElement("span");
          dot.className = "demo-ready";
          dot.title = "Included in frozen interactive preview";
          node.appendChild(dot);
        }
      });
    }

    function openFromHash() {
      var raw = (window.location.hash || "").replace(/^#/, "");
      if (!raw || !T.openDashboard) return false;
      var parts = raw.split("/");
      if (parts[0] && SHOWCASE_DASHES[parts[0]] || /^d:/.test(parts[0])) {
        return !!T.openDashboard(parts[0], parts[1] || null);
      }
      return false;
    }

    var btns = document.querySelectorAll("#demo-bar .demo-jump");
    Array.prototype.forEach.call(btns, function (b) {
      b.addEventListener("click", function () {
        if (!T.S || !T.S.booted || !T.openDashboard) return;
        T.openDashboard(b.getAttribute("data-dash"), b.getAttribute("data-tab"));
        if (window.innerWidth < 1200) {
          var sb = document.getElementById("sidebar"), ex = document.getElementById("btn-expand");
          if (sb) sb.classList.add("collapsed");
          if (ex) ex.classList.remove("hidden");
        }
        syncActive();
      });
    });

    var poll = setInterval(function () {
      if (!T.S || !T.S.booted) return;
      clearInterval(poll);
      openFromHash();
      hashApplied = true;
      syncActive();
      markSidebar();

      var tabs = document.getElementById("tabs");
      var list = document.getElementById("dash-list");
      var title = document.getElementById("dash-title");
      var mo = new MutationObserver(function () { syncActive(); markSidebar(); });
      if (tabs) mo.observe(tabs, { childList: true, subtree: true });
      if (list) mo.observe(list, { childList: true });
      if (title) mo.observe(title, { childList: true });
      window.addEventListener("hashchange", function () { openFromHash(); syncActive(); });

      // Collapse sidebar cleanly on narrow screens when picking any dashboard
      if (list) {
        list.addEventListener("click", function (ev) {
          if (window.innerWidth < 1200 && ev.target && ev.target.closest(".dash-item")) {
            var sb = document.getElementById("sidebar"), ex = document.getElementById("btn-expand");
            if (sb) sb.classList.add("collapsed");
            if (ex) ex.classList.remove("hidden");
          }
        });
      }
    }, 50);
  }

  loadManifest().catch(function () {});
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", syncDemoChrome);
  else syncDemoChrome();
})(window.T);
