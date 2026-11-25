/* Terminal UI: sections, dashboards, tabs, parameter groups, widget library, persistence. */
(function (T) {
  "use strict";
  var el = T.el, $ = function (id) { return document.getElementById(id); };
  var HOME = "d:markets";
  var STATUS = ["d:explorers", "data-status"];          // where "what is down / which keys are missing" lives
  // The product name is set in one place, PRODUCT_NAME in terminal/server.py, and arrives with /api/config.
  T.product = "Libre Terminal";
  T.version = "";
  function paintProduct() {
    document.title = T.product;
    $("brand-name").textContent = T.product;
    if (T.version) $("brand-name").title = "v" + T.version;
  }

  var S = {
    backends: [], byId: {}, widgets: {},
    sections: [], generated: [], sourceApps: [], concepts: {}, conceptIx: null,
    state: { v: 4, dashboards: {}, last: null, ui: { collapsed: {} } },
    cur: null, tabId: null, cards: [], grid: null, lib: null, epIndex: null, areaOf: null
  };
  T.S = S;

  // ---------- small dialogs ----------
  function dialog(title, body, okLabel, danger, getValue) {
    return new Promise(function (resolve) {
      var done = false, m;
      function finish(v) { if (done) return; done = true; m.close(); resolve(v); }
      var box = el("div", { class: "modal", style: { width: "min(420px, 92vw)" } }, [
        el("div", { class: "modal-head" }, [typeof title === "string" ? el("b", null, title) : title, body]),
        el("div", { style: { display: "flex", gap: "8px", justifyContent: "flex-end", padding: "10px 14px" } }, [
          el("button", { class: "btn", type: "button", onclick: function () { finish(null); } }, "Cancel"),
          el("button", { class: "btn " + (danger ? "danger" : "primary"), type: "button", onclick: function () { finish(getValue()); } }, okLabel)
        ])
      ]);
      m = T.modal(box, function () { if (!done) { done = true; resolve(null); } });
      if (body && body.tagName === "INPUT") {
        body.addEventListener("keydown", function (e) { if (e.key === "Enter") finish(getValue()); });
        body.focus(); body.select();
      }
    });
  }
  function ask(title, value, okLabel) {
    var input = el("input", { type: "text", value: value || "", style: { width: "100%", fontSize: "14px", padding: "7px 9px" } });
    return dialog(title, input, okLabel || "OK", false, function () { return input.value.trim() || null; });
  }
  function confirmBox(text, okLabel) {
    return dialog(el("span", null, text), null, okLabel || "Delete", true, function () { return true; }).then(function (v) { return !!v; });
  }
  T.ask = ask; T.confirm = confirmBox;

  // ---------- persistence ----------
  // One save at a time, each based on the revision last read from the server. If another window saved in between,
  // the server refuses (409) and sends its copy: this window adopts it rather than overwriting the other one's work.
  var saving = false, saveAgain = false;
  /**
   * What is written to disk. Built-in dashboards keep their ticker / country choices only in state.groups (one source
   * of truth), so the group values inside a saved copy of a built-in dashboard are left out.
   */
  function hasOwn(o, k) { return !!o && Object.prototype.hasOwnProperty.call(o, k); }
  function cloneVal(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
  function normJson(v) {
    if (v === undefined || v === null) return "null";
    if (Array.isArray(v)) return "[" + v.map(normJson).join(",") + "]";
    if (typeof v === "object") {
      return "{" + Object.keys(v).sort().filter(function (k) { return v[k] !== undefined; }).map(function (k) {
        return JSON.stringify(k) + ":" + normJson(v[k]);
      }).join(",") + "}";
    }
    return JSON.stringify(v);
  }
  function baseWid(it) {
    if (it && it.choices && it.choices.length) return "lib|" + (it.choices[0].w || it.title || "");
    return String((it && it.widgetId) || "").replace(/_[a-z0-9]+_obb$/, "");
  }
  function baseWidStr(sig) {
    sig = String(sig || "");
    return sig.indexOf("lib|") === 0 ? sig : sig.split("|")[0];
  }
  function cardSig(it) {
    if (!it) return "";
    if (it.choices && it.choices.length) return "lib|" + (it.choices[0].w || it.title || "");
    var p = it.params || {};
    var key = [p.preset, p.code, p.symbols, p.group, p.fact, p.duration, p.symbol].filter(function (x) { return x != null && x !== ""; }).join("|");
    return baseWid(it) + "|" + key;
  }
  T.cardSig = cardSig;

  function serializeDash(id, d) {
    if (!d) return d;
    if (d.mode === "overrides") {
      var out = { id: d.id || id, mode: "overrides" };
      if (d.overrides && Object.keys(d.overrides).length) {
        var ov = {};
        Object.keys(d.overrides).forEach(function (tid) {
          var t = d.overrides[tid], c = {};
          if (!t) return;
          if (t.name) c.name = t.name;
          if (t.cards && Object.keys(t.cards).length) c.cards = t.cards;
          if (t.hidden && t.hidden.length) c.hidden = t.hidden;
          if (t.added && t.added.length) c.added = t.added.map(savedItem);
          if (Object.keys(c).length) ov[tid] = c;
        });
        if (Object.keys(ov).length) out.overrides = ov;
      }
      if (d.tabs && d.tabs.length) {
        out.tabs = d.tabs.map(function (t) { return Object.assign({}, t, { items: (t.items || []).map(savedItem) }); });
      }
      if (d.hiddenTabs && d.hiddenTabs.length) out.hiddenTabs = d.hiddenTabs;
      return out;
    }
    if (id.indexOf("d:") === 0 && Array.isArray(d.groups)) {
      d = Object.assign({}, d, { groups: d.groups.map(function (g) { var c = Object.assign({}, g); delete c.value; return c; }) });
    }
    // a card's source (routed by ticker or picked on the card) lives in state.srcsel, not in the saved layout:
    // write each card as the layout had it, without the page's own working fields
    if (Array.isArray(d.tabs)) d = Object.assign({}, d, { tabs: d.tabs.map(function (t) { return Object.assign({}, t, { items: (t.items || []).map(savedItem) }); }) });
    return d;
  }
  function serializeState() {
    var out = Object.assign({}, S.state), dash = {};
    Object.keys(S.state.dashboards || {}).forEach(function (id) {
      dash[id] = serializeDash(id, S.state.dashboards[id]);
    });
    out.dashboards = dash;
    var last = lastSeen();
    if (last) out.last = last;
    return JSON.stringify(out);
  }
  T.serializeState = serializeState;
  function savedItem(it) {
    var o = Object.assign({}, it, it._orig || {}, it._origLib || {});
    if (it._orig && it._orig._noChart && !it._origLib) delete o.chart;          // a source pick added these; the layout had none
    if (it._orig && it._orig._noGroups && !it._origLib) delete o.groups;
    if (it._origLib && it._choiceBase) {
      if (normJson(it.params || {}) !== normJson(it._choiceBase.params || {})) o.params = cloneVal(it.params || {});
      if (!it._guessedChart && normJson(it.chart || null) !== normJson(it._choiceBase.chart || null)) {
        if (it.chart == null) delete o.chart; else o.chart = cloneVal(it.chart);
      }
    }
    if (it._guessedChart) delete o.chart;
    // view preferences live in state.cardview; the layout keeps the card's own (shipped) view settings
    if (it._vbase) VIEW_KEYS.forEach(function (k) { if (it._vbase[k] === undefined) delete o[k]; else o[k] = it._vbase[k]; });
    Object.keys(o).forEach(function (k) { if (k.charAt(0) === "_" || k === "src") delete o[k]; });
    if (it._orig && !it._orig.needsKey) delete o.needsKey;
    return o;
  }
  T.savedItem = savedItem;                     // exposed for the offline checks
  /** The saved layout cannot be used: show the defaults, say why, and never save over the file in this window. */
  function stateUnusable(detail) {
    if (S.stateBroken) return;
    S.stateBroken = true;
    notice((detail || "Your saved layout could not be read.") + " The default layout is shown and nothing is saved in this window.");
    $("save-status").textContent = "Not saved (saved layout unreadable)";
  }
  function putState() {
    if (S.stateBroken) { $("save-status").textContent = "Not saved (saved layout unreadable)"; return; }
    if (saving) { saveAgain = true; return; }
    saving = true;
    $("save-status").textContent = "Saving…";
    T.request("/api/state", { method: "PUT", headers: { "Content-Type": "application/json" }, body: serializeState() })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (r.ok) { S.state._rev = j.rev; $("save-status").textContent = "All changes saved"; return; }
          if (r.status === 409 && j.state) { adoptState(j.state); return; }
          if (r.status === 503 && j.state_unreadable) { stateUnusable(j.detail); return; }
          $("save-status").textContent = r.status === 400 && j.detail ? String(j.detail).slice(0, 120) : "Save failed (" + r.status + ")";
        });
      })
      .catch(function () { $("save-status").textContent = "Save failed: UI server not reachable"; })
      .then(function () { saving = false; if (saveAgain) { saveAgain = false; putState(); } });
  }
  var saveNow = T.debounce(putState, 700);
  function adoptState(st) {
    S.state = Object.assign({ v: 4, dashboards: {}, last: null }, st);
    S.state.ui = S.state.ui || {}; S.state.ui.collapsed = S.state.ui.collapsed || {};
    saveAgain = false;
    $("save-status").textContent = "Layout updated from another window";
    T.toast("Your layout was changed in another window; showing the latest saved version.");
    var at = S.cur ? [S.cur.id, S.tabId] : null;
    S.cur = null;
    if (at && openDashboard(at[0], at[1])) return;
    openDefault();
  }
  T.saveState = saveNow;
  /** The user changed the layout or a widget: built-in dashboards save per-card overrides, others keep a full copy. */
  function touch() {
    if (S.cur) {
      if (S.cur.id.indexOf("d:") === 0) {
        var tpl = S.generated.filter(function (x) { return x.id === S.cur.id; })[0];
        if (tpl) {
          var ov = diffDashboard(tpl, S.cur, S.state.dashboards[S.cur.id]);
          S.cur.tabs.forEach(function (t) {
            t.edited = !!(ov && ((ov.overrides && ov.overrides[t.id]) || (ov.tabs || []).some(function (ut) { return ut.id === t.id; })));
          });
          S.cur.edited = !!ov;
          if (ov) S.state.dashboards[S.cur.id] = ov;
          else delete S.state.dashboards[S.cur.id];
        } else {
          S.cur.edited = true;
          S.state.dashboards[S.cur.id] = S.cur;
        }
      } else {
        var t = S.cur.tabs.filter(function (x) { return x.id === S.tabId; })[0];
        if (t) t.edited = !sameAsDefault(t);
        S.cur.edited = S.cur.source === "user" || S.cur.tabs.some(function (x) { return x.edited; }) || (S.cur.hiddenTabs || []).length > 0;
        if (S.cur.edited) S.state.dashboards[S.cur.id] = S.cur;
      }
    }
    saveNow();
    paintSidebar();
  }
  T.touch = touch;
  /** A built-in tab whose cards, positions and settings still match the shipped layout is not a user edit. */
  // Flip, chart/table view and the settings bar are view preferences (state.cardview), not layout edits.
  var EDIT_KEYS = ["uid", "widgetId", "backend", "x", "y", "w", "h", "params", "title"];
  var VIEW_KEYS = ["flip", "view", "compact"];
  function sameAsDefault(tab, dashId, keys) {
    dashId = dashId || (S.cur && S.cur.id);
    if (!dashId || dashId.indexOf("d:") !== 0) return false;
    var tpl = S.generated.filter(function (d) { return d.id === dashId; })[0];
    if (userTab(tab, tpl)) return false;
    var tt = tpl && tpl.tabs.filter(function (x) { return x.id === tab.id; })[0];
    if (!tt || tt.items.length !== tab.items.length || (tab.name && tab.name !== tt.name)) return false;
    var ref = JSON.parse(JSON.stringify(tt.items));
    T.grid.compact(ref, null);                  // the grid packs the layout when a tab opens
    function norm(v) { return JSON.stringify(v == null || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length) ? null : v); }
    return tab.items.every(function (it, i) { var s = savedItem(it); return (keys || EDIT_KEYS).every(function (k) { return norm(s[k]) === norm(ref[i][k]); }); });
  }
  /** View preferences of a card (state.cardview[dashboard][card] = {flip, view, compact}), applied over its layout. */
  function applyPrefs(item, dashId) {
    var d = S.state.cardview && S.state.cardview[dashId || (S.cur && S.cur.id)], p = d && d[item.uid];
    if (!item._vbase) { item._vbase = {}; VIEW_KEYS.forEach(function (k) { item._vbase[k] = item[k]; }); }
    VIEW_KEYS.forEach(function (k) { var v = p && Object.prototype.hasOwnProperty.call(p, k) ? p[k] : item._vbase[k]; if (v === undefined) delete item[k]; else item[k] = v; });
  }
  function setPref(item, key, value) {
    if (!S.cur || VIEW_KEYS.indexOf(key) < 0) return;
    if (!item._vbase) applyPrefs(item);
    item[key] = value;
    S.state.cardview = S.state.cardview || {};
    var d = S.state.cardview[S.cur.id] = S.state.cardview[S.cur.id] || {}, p = d[item.uid] = d[item.uid] || {};
    if (value === item._vbase[key] || (value == null && item._vbase[key] == null) || (!value && !item._vbase[key] && key === "compact")) delete p[key]; else p[key] = value;
    if (!Object.keys(p).length) delete d[item.uid];
    if (!Object.keys(d).length) delete S.state.cardview[S.cur.id];
    saveNow();
  }
  /**
   * One-time move (2026-11-16): flips, chart/table switches and "Hide settings" used to mark a built-in tab as edited, so
   * it stopped receiving new default layouts. A tab whose only differences from the default are those view keys is
   * released (its view choices move to state.cardview); tabs with real edits stay as they are. Returns what it did.
   */
  function migrateViewPrefs() {
    var out = { released: [], kept: [] };
    if ((S.state.v || 0) >= 3) return out;
    Object.keys(S.state.dashboards || {}).forEach(function (id) {
      var d = S.state.dashboards[id], tpl = S.generated.filter(function (x) { return x.id === id; })[0];
      if (!d || !tpl || id.indexOf("d:") !== 0 || !Array.isArray(d.tabs) || d.mode === "overrides") return;
      d.tabs.forEach(function (t) {
        if (!t.edited) return;
        var name = tpl.name + " › " + t.name;
        if (!sameAsDefault(t, id, EDIT_KEYS)) { out.kept.push(name); return; }
        var tt = tpl.tabs.filter(function (x) { return x.id === t.id; })[0], byUid = {};
        tt.items.forEach(function (x) { byUid[x.uid] = x; });
        t.items.forEach(function (it) {
          var base = byUid[it.uid] || {}, p = {};
          VIEW_KEYS.forEach(function (k) { if (JSON.stringify(it[k]) !== JSON.stringify(base[k]) && !(k === "compact" && !it[k] && !base[k])) p[k] = it[k] === undefined ? null : it[k]; });
          if (Object.keys(p).length) {
            S.state.cardview = S.state.cardview || {};
            (S.state.cardview[id] = S.state.cardview[id] || {})[it.uid] = p;
          }
        });
        t.edited = false;
        out.released.push(name);
      });
      if (!d.tabs.some(function (t) { return t.edited; }) && !(d.hiddenTabs || []).length && d.source !== "user") delete S.state.dashboards[id];
    });
    S.state.v = 3;
    return out;
  }
  T.migrateViewPrefs = migrateViewPrefs;
  /** Tabs the user created: "u-…" ids (since 2026-11-15); older ones "t<base36 time>" when no built-in tab has that id. */
  function userTab(t, tpl) {
    var id = t.id || "";
    if (/^u-[a-z0-9]+$/.test(id)) return true;
    return /^t[a-z0-9]{6,}$/.test(id) && !(tpl && tpl.tabs.some(function (x) { return x.id === id; }));
  }
  /** Saved copies from before per-tab marks: a tab counts as edited only if it shows a real user edit. */
  function markLegacy(tpl, saved) {
    if (!saved || !Array.isArray(saved.tabs) || saved.tabs.some(function (t) { return t.edited; })) return;
    saved.tabs.forEach(function (t) {
      var known = tpl.tabs.some(function (x) { return x.id === t.id; });
      t.edited = known ? (t.items || []).some(function (it) { return /^w[a-z0-9]{6,}/.test(it.uid || "") || it.flip != null; }) : userTab(t, tpl);
    });
  }

  function diffParams(baseP, curP) {
    var bp = baseP || {}, cp = curP || {}, out = {};
    Object.keys(cp).forEach(function (k) {
      if (cp[k] === undefined) return;
      if (!hasOwn(bp, k) || normJson(cp[k]) !== normJson(bp[k])) out[k] = cloneVal(cp[k]);
    });
    Object.keys(bp).forEach(function (k) {
      if (bp[k] !== undefined && bp[k] !== null && !hasOwn(cp, k)) out[k] = null;
    });
    return Object.keys(out).length ? out : null;
  }
  function compactedYs(list) {
    var clones = list.map(function (it) {
      return { uid: it.uid, x: it.x != null ? it.x : 0, y: it.y != null ? it.y : 0, w: it.w != null ? it.w : 20, h: it.h != null ? it.h : 14 };
    });
    T.grid.compact(clones, null);
    var ys = {};
    clones.forEach(function (c) { ys[c.uid] = c.y; });
    return ys;
  }
  function diffTab(tplTab, curTab, prevTabOv) {
    var tByUid = {};
    (tplTab.items || []).forEach(function (ti) { tByUid[ti.uid] = ti; });
    var curClean = (curTab.items || []).map(savedItem);
    var presentByUid = {}, added = [];
    curClean.forEach(function (ci) {
      if (tByUid[ci.uid]) presentByUid[ci.uid] = ci;
      else added.push(ci);
    });
    var curY = compactedYs(curClean);
    var baseList = [];
    (tplTab.items || []).forEach(function (ti) {
      var ci = presentByUid[ti.uid];
      if (!ci) return;
      baseList.push({
        uid: ti.uid,
        x: ci.x != null ? ci.x : (ti.x != null ? ti.x : 0),
        y: ti.y != null ? ti.y : 0,
        w: ci.w != null ? ci.w : (ti.w != null ? ti.w : 20),
        h: ci.h != null ? ci.h : (ti.h != null ? ti.h : 14)
      });
    });
    var baseY = compactedYs(baseList);
    var cards = {};
    (tplTab.items || []).forEach(function (ti) {
      var ci = presentByUid[ti.uid];
      if (!ci) { cards[ti.uid] = { hidden: true, sig: cardSig(ti) }; return; }
      var co = {};
      var cx = ci.x != null ? ci.x : 0, tx = ti.x != null ? ti.x : 0;
      var cw = ci.w != null ? ci.w : 20, tw = ti.w != null ? ti.w : 20;
      var ch = ci.h != null ? ci.h : 14, th = ti.h != null ? ti.h : 14;
      if (cx !== tx) co.x = cx;
      if (cw !== tw) co.w = cw;
      if (ch !== th) co.h = ch;
      if (curY[ti.uid] !== baseY[ti.uid]) co.y = curY[ti.uid];
      var dp = diffParams(ti.params, ci.params);
      if (dp) co.params = dp;
      var ct = ci.title == null ? null : ci.title, tt = ti.title == null ? null : ti.title;
      if (ct !== tt) co.title = ct;
      if (normJson(ci.chart || null) !== normJson(ti.chart || null)) co.chart = ci.chart ? cloneVal(ci.chart) : null;
      if (Object.keys(co).length) { co.sig = cardSig(ti); cards[ti.uid] = co; }
    });
    if (prevTabOv && prevTabOv.cards) {
      var tplSigs = {};
      (tplTab.items || []).forEach(function (ti) { tplSigs[cardSig(ti)] = 1; });
      Object.keys(prevTabOv.cards).forEach(function (k) {
        if (tByUid[k] || cards[k]) return;
        var pco = prevTabOv.cards[k];
        if (pco && !tplSigs[pco.sig]) cards[k] = cloneVal(pco);
      });
    }
    var tov = {};
    if (curTab.name && curTab.name !== tplTab.name) tov.name = curTab.name;
    if (Object.keys(cards).length) tov.cards = cards;
    if (added.length) tov.added = added;
    return Object.keys(tov).length ? tov : null;
  }
  function diffDashboard(tpl, cur, prevSaved) {
    var prevOv = prevSaved && prevSaved.mode === "overrides" ? prevSaved : null;
    var tplMap = {}, curMap = {}, renamed = tpl.renamed || {};
    (tpl.tabs || []).forEach(function (tt) { tplMap[tt.id] = tt; });
    (cur.tabs || []).forEach(function (ct) { curMap[ct.id] = ct; });
    var overrides = {}, userTabs = [], hiddenTabs = [];
    (tpl.tabs || []).forEach(function (tt) {
      if (!curMap[tt.id]) { hiddenTabs.push(tt.id); return; }
      var tov = diffTab(tt, curMap[tt.id], prevOv && prevOv.overrides && prevOv.overrides[tt.id]);
      if (tov) overrides[tt.id] = tov;
    });
    (cur.tabs || []).forEach(function (ct) {
      if (!tplMap[ct.id]) userTabs.push(Object.assign({}, ct, { edited: true, items: (ct.items || []).map(savedItem) }));
    });
    if (prevOv && prevOv.overrides) {
      Object.keys(prevOv.overrides).forEach(function (tid) {
        if (!tplMap[tid] && !renamed[tid] && !overrides[tid] && !curMap[tid]) overrides[tid] = cloneVal(prevOv.overrides[tid]);
      });
    }
    if (!Object.keys(overrides).length && !userTabs.length && !hiddenTabs.length) return null;
    var out = { id: tpl.id, mode: "overrides" };
    if (Object.keys(overrides).length) out.overrides = overrides;
    if (userTabs.length) out.tabs = userTabs;
    if (hiddenTabs.length) out.hiddenTabs = hiddenTabs;
    return out;
  }
  T.diffDashboard = diffDashboard;

  function uidNumSuffix(uid) {
    var m = /-(\d+)$/.exec(String(uid || ""));
    return m ? Number(m[1]) : -1;
  }
  /** Convert a legacy v2/v3 full-copy built-in dashboard into a v4 per-card override record without mutating `saved`. */
  function legacyToOverrides(tpl, saved, collectMeta) {
    if (!saved || !tpl) return null;
    if (saved.mode === "overrides") return saved;
    var copy = {
      tabs: (saved.tabs || []).map(function (t) { return Object.assign({}, t); }),
      hiddenTabs: (saved.hiddenTabs || []).slice()
    };
    markLegacy(tpl, copy);
    var renamed = tpl.renamed || {}, tplMap = {}, tplGrp = {}, parts = {}, orphans = [];
    (tpl.tabs || []).forEach(function (tt) { tplMap[tt.id] = tt; });
    (tpl.groups || []).forEach(function (g) { if (g && g.name && g.paramName) tplGrp[g.name] = g; });
    copy.tabs.forEach(function (t) {
      if (!t.edited) return;
      var direct = !!tplMap[t.id], id = direct ? t.id : renamed[t.id];
      if (id && tplMap[id]) (parts[id] = parts[id] || []).push({ t: t, direct: direct });
      else orphans.push(t);
    });
    var overrides = {}, userTabs = [];
    Object.keys(parts).forEach(function (targetId) {
      var ps = parts[targetId].sort(function (a, b) { return (b.direct ? 1 : 0) - (a.direct ? 1 : 0); });
      var gt = tplMap[targetId], gItems = gt.items || [], gByUid = {};
      gItems.forEach(function (gi) { gByUid[gi.uid] = gi; });
      var sItems = [], seenUids = {};
      ps.forEach(function (p) {
        (p.t.items || []).forEach(function (si) {
          if (seenUids[si.uid]) return;
          seenUids[si.uid] = 1;
          sItems.push(si);
        });
      });
      var claimed = {}, matches = [], rem = [];
      // Pass 1: exact uid + exact cardSig
      sItems.forEach(function (si) {
        var gi = gByUid[si.uid];
        if (gi && !claimed[gi.uid] && cardSig(si) === cardSig(gi)) { claimed[gi.uid] = 1; matches.push({ si: si, gi: gi, shifted: false }); }
        else rem.push(si);
      });
      // Pass 2: exact uid + same baseWid when no other unclaimed gi has exact cardSig(si)
      var rem2 = [];
      rem.forEach(function (si) {
        var gi = gByUid[si.uid], sSig = cardSig(si);
        var exactElsewhere = gItems.some(function (x) { return !claimed[x.uid] && cardSig(x) === sSig; });
        if (gi && !claimed[gi.uid] && baseWid(si) === baseWid(gi) && !exactElsewhere) { claimed[gi.uid] = 1; matches.push({ si: si, gi: gi, shifted: false }); }
        else rem2.push(si);
      });
      // Pass 3: unclaimed gi with exact cardSig
      var rem3 = [];
      rem2.forEach(function (si) {
        var sSig = cardSig(si), hit = gItems.filter(function (x) { return !claimed[x.uid] && cardSig(x) === sSig; })[0];
        if (hit) { claimed[hit.uid] = 1; matches.push({ si: si, gi: hit, shifted: true }); }
        else rem3.push(si);
      });
      // Pass 4: unclaimed gi with same baseWid
      var unmatchedS = [];
      rem3.forEach(function (si) {
        var bw = baseWid(si), hit = gItems.filter(function (x) { return !claimed[x.uid] && baseWid(x) === bw; })[0];
        if (hit) { claimed[hit.uid] = 1; matches.push({ si: si, gi: hit, shifted: true }); }
        else unmatchedS.push(si);
      });
      if (collectMeta) {
        matches.forEach(function (m) {
          if (m.si.uid !== m.gi.uid) collectMeta.uidMap[m.si.uid] = m.gi.uid;
        });
      }
      var restructured = ps.length > 1 || matches.some(function (m) { return m.shifted; })
        || unmatchedS.some(function (si) { return !/^w[a-z0-9]{6,}/.test(si.uid || ""); });
      var added = [];
      unmatchedS.forEach(function (si) {
        if (/^w[a-z0-9]{6,}/.test(si.uid || "")) added.push(savedItem(si));
        else if (collectMeta && restructured) collectMeta.droppedUids[si.uid] = 1;
      });
      var curY = {}, baseY = {};
      if (!restructured && matches.length) {
        var cleanCur = [], cleanBase = [];
        matches.forEach(function (m) {
          var ci = savedItem(m.si), gi = m.gi;
          cleanCur.push({ uid: gi.uid, x: ci.x != null ? ci.x : (gi.x != null ? gi.x : 0), y: ci.y != null ? ci.y : 0, w: ci.w != null ? ci.w : (gi.w != null ? gi.w : 20), h: ci.h != null ? ci.h : (gi.h != null ? gi.h : 14) });
          cleanBase.push({ uid: gi.uid, x: ci.x != null ? ci.x : (gi.x != null ? gi.x : 0), y: gi.y != null ? gi.y : 0, w: ci.w != null ? ci.w : (gi.w != null ? gi.w : 20), h: ci.h != null ? ci.h : (gi.h != null ? gi.h : 14) });
        });
        added.forEach(function (ai) {
          cleanCur.push({ uid: ai.uid, x: ai.x != null ? ai.x : 0, y: ai.y != null ? ai.y : 0, w: ai.w != null ? ai.w : 20, h: ai.h != null ? ai.h : 14 });
        });
        curY = compactedYs(cleanCur);
        baseY = compactedYs(cleanBase);
      }
      var cards = {};
      matches.forEach(function (m) {
        var raw = m.si, ci = savedItem(raw), gi = m.gi, co = {};
        var dp = diffParams(gi.params, ci.params);
        if (dp && gi.groups && gi.groups.length) {
          gi.groups.forEach(function (gn) {
            var g = tplGrp[gn];
            if (g && hasOwn(dp, g.paramName) && normJson(dp[g.paramName]) === normJson(g.value)) delete dp[g.paramName];
          });
          if (!Object.keys(dp).length) dp = null;
        }
        if (dp) co.params = dp;
        if (ci.chart && normJson(ci.chart) !== normJson(gi.chart || null)) co.chart = cloneVal(ci.chart);
        if (!restructured) {
          var cx = ci.x != null ? ci.x : 0, tx = gi.x != null ? gi.x : 0;
          var cw = ci.w != null ? ci.w : 20, tw = gi.w != null ? gi.w : 20;
          var ch = ci.h != null ? ci.h : 14, th = gi.h != null ? gi.h : 14;
          if (cx !== tx) co.x = cx;
          if (cw !== tw) co.w = cw;
          if (ch !== th) co.h = ch;
          if (curY[gi.uid] !== baseY[gi.uid]) co.y = curY[gi.uid];
        }
        if (Object.keys(co).length) { co.sig = cardSig(gi); cards[gi.uid] = co; }
        if (collectMeta) {
          VIEW_KEYS.forEach(function (k) {
            if (raw[k] != null && raw[k] !== gi[k] && !(k === "compact" && !raw[k] && !gi[k])) {
              (collectMeta.viewPrefs[gi.uid] = collectMeta.viewPrefs[gi.uid] || {})[k] = raw[k];
            }
          });
        }
      });
      if (!restructured && matches.length) {
        var maxIdx = matches.reduce(function (mx, m) { return Math.max(mx, uidNumSuffix(m.gi.uid)); }, -1);
        gItems.forEach(function (gi) {
          if (claimed[gi.uid]) return;
          var idx = uidNumSuffix(gi.uid);
          if (idx >= 0 && idx < maxIdx) cards[gi.uid] = { hidden: true, sig: cardSig(gi) };
        });
      }
      var tov = {};
      if (ps[0].direct && ps[0].t.name && ps[0].t.name !== gt.name) tov.name = ps[0].t.name;
      if (Object.keys(cards).length) tov.cards = cards;
      if (added.length) tov.added = added;
      if (Object.keys(tov).length) overrides[targetId] = tov;
    });
    orphans.forEach(function (t) {
      if (userTab(t, tpl)) {
        userTabs.push(Object.assign({}, t, { edited: true, items: (t.items || []).map(savedItem) }));
        return;
      }
      var keep = Object.assign({}, t, { id: "u-" + String(t.id).replace(/[^a-z0-9]/g, "") + "old", edited: true, items: (t.items || []).map(savedItem) });
      if (!/\(from an older layout\)$/.test(keep.name || "")) keep.name = (t.name || t.id) + " (from an older layout)";
      userTabs.push(keep);
    });
    var hiddenTabs = [], hiddenSeen = {};
    copy.hiddenTabs.forEach(function (id) {
      var target = tplMap[id] ? id : (renamed[id] || id);
      if (tplMap[target] && !overrides[target] && !hiddenSeen[target]) {
        hiddenSeen[target] = 1;
        hiddenTabs.push(target);
      }
    });
    if (!Object.keys(overrides).length && !userTabs.length && !hiddenTabs.length) return null;
    var out = { id: tpl.id, mode: "overrides" };
    if (Object.keys(overrides).length) out.overrides = overrides;
    if (userTabs.length) out.tabs = userTabs;
    if (hiddenTabs.length) out.hiddenTabs = hiddenTabs;
    return out;
  }

  /**
   * One-time move (v3 -> v4): convert saved built-in dashboard full copies into per-card overrides so newly shipped
   * cards, fixes and layout updates flow through to edited tabs while keeping the user's card changes, added cards and
   * view choices. Untouched `app:*` copies are released after keeping their group picks. Idempotent.
   */
  function migrateOverrides() {
    var out = { migrated: [], released: [], kept: [] };
    if ((S.state.v || 0) >= 4) return out;
    Object.keys(S.state.dashboards || {}).forEach(function (id) {
      var d = S.state.dashboards[id];
      if (!d) return;
      if (id.indexOf("app:") === 0) {
        if (d.source !== "user" && !d.edited && !(d.tabs || []).some(function (t) { return t.edited; }) && !(d.hiddenTabs || []).length) {
          if (Array.isArray(d.groups) && d.groups.length) {
            S.state.groups = S.state.groups || {};
            var gkeep = S.state.groups[id] = S.state.groups[id] || {};
            d.groups.forEach(function (g) { if (!(g.name in gkeep) && g.value != null) gkeep[g.name] = g.value; });
          }
          delete S.state.dashboards[id];
          out.released.push(id);
        } else {
          out.kept.push(id);
        }
        return;
      }
      if (id.indexOf("d:") !== 0) return;
      if (d.mode === "overrides") { out.kept.push(id); return; }
      var tpl = S.generated.filter(function (x) { return x.id === id; })[0];
      if (!tpl) { out.kept.push(id); return; }
      if (Array.isArray(d.groups) && d.groups.length) {
        S.state.groups = S.state.groups || {};
        var keep = S.state.groups[id] = S.state.groups[id] || {};
        d.groups.forEach(function (g) { if (!(g.name in keep) && g.value != null) keep[g.name] = g.value; });
      }
      var meta = { uidMap: {}, viewPrefs: {}, droppedUids: {} };
      var ov = legacyToOverrides(tpl, d, meta);
      Object.keys(meta.viewPrefs).forEach(function (uid) {
        S.state.cardview = S.state.cardview || {};
        var dm = S.state.cardview[id] = S.state.cardview[id] || {};
        var cur = dm[uid] = dm[uid] || {};
        Object.keys(meta.viewPrefs[uid]).forEach(function (k) {
          if (!(k in cur)) cur[k] = meta.viewPrefs[uid][k];
        });
      });
      var shiftedOld = Object.assign({}, meta.droppedUids);
      Object.keys(meta.uidMap).forEach(function (oldU) { shiftedOld[oldU] = 1; });
      if (Object.keys(shiftedOld).length) {
        ["cardview", "srcsel", "viewsel", "colsel", "libsel"].forEach(function (pk) {
          var bag = S.state[pk] && S.state[pk][id];
          if (!bag) return;
          var next = {};
          Object.keys(bag).forEach(function (u) {
            if (!shiftedOld[u]) next[u] = bag[u];
          });
          Object.keys(meta.uidMap).forEach(function (oldU) {
            var newU = meta.uidMap[oldU];
            if (hasOwn(bag, oldU) && !(newU in next)) next[newU] = bag[oldU];
          });
          if (Object.keys(next).length) S.state[pk][id] = next;
          else delete S.state[pk][id];
        });
      }
      if (ov) { S.state.dashboards[id] = ov; out.migrated.push(tpl.name || id); }
      else { delete S.state.dashboards[id]; out.released.push(tpl.name || id); }
    });
    S.state.v = 4;
    return out;
  }
  T.migrateOverrides = migrateOverrides;

  /**
   * Built-in dashboard + the user's overrides (or legacy saved copy): shipped cards and fixes flow through while
   * per-card overrides, hidden cards, added cards and user tabs are applied on top.
   */
  function mergeDashboard(tpl, saved) {
    var d = cloneVal(tpl);
    if (!saved) return d;
    var ov = saved.mode === "overrides" ? saved : legacyToOverrides(tpl, saved);
    if (!ov) return d;
    var renamed = tpl.renamed || {}, tabMap = {}, ovParts = {}, extraTabs = [];
    d.tabs.forEach(function (t) { tabMap[t.id] = t; });
    Object.keys(ov.overrides || {}).forEach(function (tid) {
      var tabOv = ov.overrides[tid];
      if (!tabOv) return;
      var direct = !!tabMap[tid], target = direct ? tid : renamed[tid];
      if (target && tabMap[target]) (ovParts[target] = ovParts[target] || []).push({ ov: tabOv, direct: direct });
      else if (tabOv.added && tabOv.added.length) {
        var nm = tabOv.name || tid;
        if (!/\(from an older layout\)$/.test(nm)) nm += " (from an older layout)";
        extraTabs.push({ id: "u-" + String(tid).replace(/[^a-z0-9]/g, "") + "old", name: nm, items: cloneVal(tabOv.added), edited: true });
      }
    });
    Object.keys(ovParts).forEach(function (tid) {
      var tt = tabMap[tid], parts = ovParts[tid].sort(function (a, b) { return (b.direct ? 1 : 0) - (a.direct ? 1 : 0); });
      var allItems = tt.items || [], usedUids = {};
      allItems.forEach(function (it) { usedUids[it.uid] = 1; });
      parts.forEach(function (part) {
        var tabOv = part.ov;
        if (part.direct && tabOv.name) tt.name = tabOv.name;
        var cards = tabOv.cards || {}, hiddenUids = {};
        (tabOv.hidden || []).forEach(function (u) { hiddenUids[u] = 1; });
        var claimedIdx = {}, matched = [], remUids = [];
        // Pass A: exact uid with matching sig (or same baseWid when no other unclaimed item has exact sig)
        Object.keys(cards).forEach(function (uid) {
          var co = cards[uid], idx = -1;
          for (var i = 0; i < allItems.length; i++) {
            if (!claimedIdx[i] && allItems[i].uid === uid) { idx = i; break; }
          }
          if (idx >= 0 && co) {
            var s = cardSig(allItems[idx]);
            if (!co.sig || s === co.sig) { claimedIdx[idx] = 1; matched.push({ idx: idx, co: co, shifted: false }); return; }
            var exactOther = allItems.some(function (x, j) { return !claimedIdx[j] && cardSig(x) === co.sig; });
            if (!exactOther && baseWid(allItems[idx]) === baseWidStr(co.sig)) { claimedIdx[idx] = 1; matched.push({ idx: idx, co: co, shifted: false }); return; }
          }
          remUids.push(uid);
        });
        // Pass B: fallback by sig, then unique baseWid
        remUids.forEach(function (uid) {
          var co = cards[uid];
          if (!co || !co.sig) return;
          var idx = -1;
          for (var i = 0; i < allItems.length; i++) {
            if (!claimedIdx[i] && cardSig(allItems[i]) === co.sig) { idx = i; break; }
          }
          if (idx < 0) {
            var bw = baseWidStr(co.sig), cands = [];
            for (var j = 0; j < allItems.length; j++) {
              if (!claimedIdx[j] && baseWid(allItems[j]) === bw) cands.push(j);
            }
            if (cands.length === 1) idx = cands[0];
          }
          if (idx >= 0) { claimedIdx[idx] = 1; matched.push({ idx: idx, co: co, shifted: true }); }
        });
        matched.forEach(function (m) {
          var it = allItems[m.idx], co = m.co;
          if (co.hidden) { hiddenUids[it.uid] = 1; return; }
          if (!m.shifted) {
            if (typeof co.x === "number") it.x = co.x;
            if (typeof co.y === "number") it.y = co.y;
          }
          if (typeof co.w === "number") it.w = co.w;
          if (typeof co.h === "number") it.h = co.h;
          if (co.params && typeof co.params === "object") {
            it.params = Object.assign({}, it.params || {}, cloneVal(co.params));
          }
          if (hasOwn(co, "title")) {
            if (co.title == null) delete it.title; else it.title = co.title;
          }
          if (hasOwn(co, "chart")) {
            if (co.chart == null) delete it.chart; else it.chart = cloneVal(co.chart);
          }
        });
        tt.items = tt.items.filter(function (it) { return !hiddenUids[it.uid]; });
        (tabOv.added || []).forEach(function (ait) {
          var c = cloneVal(ait);
          while (usedUids[c.uid]) c.uid += "n";
          usedUids[c.uid] = 1;
          tt.items.push(c);
        });
        tt.edited = true;
      });
    });
    (ov.tabs || []).forEach(function (ut) {
      if (!tabMap[ut.id]) {
        d.tabs.push(Object.assign({}, cloneVal(ut), { edited: true }));
        tabMap[ut.id] = d.tabs[d.tabs.length - 1];
      }
    });
    extraTabs.forEach(function (et) {
      if (!tabMap[et.id]) { d.tabs.push(et); tabMap[et.id] = et; }
    });
    var hid = [], hSeen = {};
    (ov.hiddenTabs || []).forEach(function (id) {
      var t = renamed[id] || id;
      if (!hSeen[t]) { hSeen[t] = 1; hid.push(t); }
    });
    d.hiddenTabs = hid;
    d.tabs = d.tabs.filter(function (t) { return hid.indexOf(t.id) < 0; });
    d.edited = d.tabs.some(function (t) { return t.edited; }) || d.hiddenTabs.length > 0;
    return d;
  }
  T.mergeDashboard = mergeDashboard;          // exposed for the offline checks in scripts/tests/
  /** Ticker / country choices are remembered per dashboard without turning it into a private copy. */
  function rememberGroup(g) {
    if (!S.cur) return;
    S.state.groups = S.state.groups || {};
    (S.state.groups[S.cur.id] = S.state.groups[S.cur.id] || {})[g.name] = g.value;
    saveNow();
  }
  /**
   * Where the user is. Kept in this browser (localStorage) and written to the saved state only together with a real
   * change: opening a tab must not rewrite the layout file (viewing never changes the saved state).
   */
  var LAST_KEY = "terminal.last";
  function remember() {
    var v = S.cur ? { id: S.cur.id, tab: S.tabId } : null;
    try { localStorage.setItem(LAST_KEY, JSON.stringify(v)); } catch (e) { /* private mode: keep it in memory */ }
    S.lastSeen = v;
  }
  function lastSeen() {
    if (S.lastSeen !== undefined) return S.lastSeen;
    try { var v = JSON.parse(localStorage.getItem(LAST_KEY) || "null"); if (v && v.id) return v; } catch (e) { /* ignore */ }
    return S.state.last || null;
  }

  // ---------- backends ----------
  function fetchT(url, ms) { return T.request(url, { timeout: ms }); }
  /** A dashboard shipped by an OpenBB extension (grouped by data provider). */
  function normalizeApp(app, bid) {
    var groups = (app.groups || []).filter(function (g) { return g && g.paramName; }).map(function (g) {
      return { name: g.name, paramName: g.paramName, value: g.defaultValue == null ? null : g.defaultValue, widgetIds: g.widgetIds || null };
    });
    var tabs = Object.keys(app.tabs || {}).map(function (k) {
      var t = app.tabs[k];
      return {
        id: t.id || k, name: t.name || t.id || k,
        items: (t.layout || []).map(function (it, i) {
          var gs = Array.isArray(it.groups) ? it.groups.slice() : typeof it.groups === "string" ? [it.groups] : [];
          groups.forEach(function (g) { if (g.widgetIds && g.widgetIds.indexOf(it.i) >= 0 && gs.indexOf(g.name) < 0) gs.push(g.name); });
          return { uid: (t.id || k) + "-" + i, widgetId: it.i, backend: bid, x: it.x || 0, y: it.y || 0, w: it.w || 20, h: it.h || 12, params: Object.assign({}, it.state && it.state.params), groups: gs };
        })
      };
    });
    groups.forEach(function (g) { delete g.widgetIds; });
    return { id: "app:" + bid + ":" + app.name, name: app.name, description: app.description || "", source: "app", section: "sources", backend: bid, groups: groups, tabs: tabs };
  }
  /**
   * Column formats the backend gets wrong. Yahoo now reports the dividend yield of a stock in percent (2.5 = 2.5%)
   * while the OpenBB widget still formats it as a fraction (x100), which showed NVDA at 61%. ETF yields are still fractions.
   */
  var PCT_ALREADY = { dividend_yield: /^equity_(profile|fundamental_metrics)_yfinance/ };
  var REQ_PARAMS = {
    sec_filing_viewer_sec_obb: ["document_url"],
    sec_filing_headers_sec_obb: ["url"],
    regulators_sec_filing_headers_sec_obb: ["url"],
    sec_htm_file_sec_obb: ["url"],
    regulators_sec_htm_file_sec_obb: ["url"]
  };
  var CLICK_FILING_URL = { equity_fundamental_filings_tmx_obb: true, equity_fundamental_filings_yfinance_obb: true };
  function fixDefs(defs) {
    Object.keys(defs).forEach(function (id) {
      var d = defs[id], cols = d && d.data && d.data.table && d.data.table.columnsDefs;
      (cols || []).forEach(function (c) { var rx = PCT_ALREADY[c.field]; if (rx && rx.test(id) && c.formatterFn === "normalizedPercent") c.formatterFn = "percent"; });
      var req = REQ_PARAMS[id];
      if (req && d && Array.isArray(d.params)) d.params.forEach(function (p) { if (req.indexOf(p.paramName) >= 0) p.optional = false; });
      if (id === "equity_fundamental_filings_tmx_obb" && d && Array.isArray(d.params) && !d.params.some(function (p) { return p.paramName === "symbol"; })) {
        d.params.unshift({ paramName: "symbol", type: "text", label: "Symbol", optional: true, show: true });
      }
      // USDA PSD circulars (review B3): the widget ships without its required "urls" picker, so the card could only
      // fail (HTTP 500 "urls Field required"). The list comes from /usda/psd_report_urls (latest circular per commodity);
      // as a file selector the card opens the first circular and POSTs the pick, like the Weather & Crop Bulletin.
      if (id === "commodity_psd_report_usda_obb" && d && Array.isArray(d.params) && !d.params.some(function (p) { return p.paramName === "urls"; })) {
        d.params.unshift({ paramName: "urls", type: "endpoint", label: "Circular", optionsEndpoint: "/api/v1/usda/psd_report_urls", show: false, multiSelect: true, roles: ["fileSelector"] });
      }
      if (CLICK_FILING_URL[id] && Array.isArray(cols)) {
        cols.forEach(function (c) {
          if (c.field === "report_url" && !c.renderFn) {
            c.renderFn = "cellOnClick";
            c.renderFnParams = { actionType: "groupBy", groupBy: { paramName: "url", valueField: "report_url" } };
          }
        });
      }
    });
    return defs;
  }
  T.fixDefs = fixDefs;
  async function loadBackend(b) {
    try {
      var r = await fetchT(b.url + "/widgets.json", 60000);
      if (!r.ok) throw new Error("HTTP " + r.status);
      S.widgets[b.id] = fixDefs(await r.json());
      b.count = Object.keys(S.widgets[b.id]).length;
      b.up = true;
      S.sourceApps = S.sourceApps.filter(function (t) { return t.backend !== b.id; });
      if (b.url) {
        try {
          var a = await fetchT(b.url + "/apps.json", 30000);
          if (a.ok) { var apps = await a.json(); (Array.isArray(apps) ? apps : []).forEach(function (app) { if (app && app.name && app.tabs) S.sourceApps.push(normalizeApp(app, b.id)); }); }
        } catch (e) { /* apps are optional */ }
      }
      S.lib = S.epIndex = S.areaOf = S.conceptIx = S.conceptIxSrc = null;
      aliasCache = {};
    } catch (e) {
      b.up = false;
      watchBackend(b);
    }
    paintStatus();
    if (b.up) backendArrived(b);
  }
  /**
   * A backend answered after the page was drawn: repaint the sidebar (its source dashboards) and redraw the open
   * dashboard if any of its cards waited for this backend (sources and dataset picks are applied again then).
   */
  var arrivedTimer = null;
  function applyBackendArrival(b) {
    paintSidebar();
    if (!S.cur) return openDefault();
    var ss = S.state.srcsel && S.state.srcsel[S.cur.id];
    var replaced = false;
    S.cards.slice().forEach(function (c) {
      var uid = c.item && c.item.uid;
      var savedSrc = uid && ss && typeof ss[uid] === "string" && ss[uid].indexOf(b.id + ":") === 0 &&
        ss[uid] !== c.item.backend + ":" + c.item.widgetId && validSource(c.item, ss[uid]);
      if (savedSrc) {
        applySource(c.item, ss[uid]);
        routeItem(c.item, false);
        replaceCard(c.item);
        replaced = true;
      } else if (c.missing && c.backend === b.id) {
        routeItem(c.item, false);
        replaceCard(c.item);
        replaced = true;
      } else if (c.item && routeItem(c.item, false)) {
        replaceCard(c.item);
        replaced = true;
      } else if (c.backend === b.id || (c.item && c.item.backend === b.id)) {
        if (c.isFrame || (c.def && (c.def.type === "iframe" || c.def.type === "html"))) c.refresh(true);
        else c.retryIfFailed();
      }
    });
    if (replaced) { paintGroups(); paintEmpty(); }
  }
  function backendArrived(b) {
    if (!S.booted) return;
    clearTimeout(arrivedTimer);
    arrivedTimer = setTimeout(function () { applyBackendArrival(b); }, 250);
  }
  T.backendArrived = backendArrived;
  T.applyBackendArrival = applyBackendArrival;
  function stopWatchBackend(b) {
    if (!b) return;
    b._watchGen = (b._watchGen || 0) + 1;
    if (b._watchTimer) { clearTimeout(b._watchTimer); b._watchTimer = null; }
    b.watching = false;
  }
  /**
   * A backend stopped answering (crash, restart, not started yet): say so once in the banner, poll it, and when it
   * answers again reload what it serves (cards that failed retry; cards it never described are rebuilt).
   */
  function watchBackend(b) {
    if (!b || b.watching || S.restarting) return;
    b.watching = true;
    var gen = (b._watchGen = (b._watchGen || 0) + 1);
    var tries = 0;
    /** Is the launcher still restarting this backend? "stopped" = its supervisor gave up: stop polling and say so. */
    function supervised() {
      if (tries % 4 !== 1) return Promise.resolve(b.service || "running");
      return fetchT("/api/services", 4000).then(function (r) { return r.ok ? r.json() : {}; }).then(function (j) {
        if (!b.watching || b._watchGen !== gen || S.restarting) return b.service || "running";
        var s = j && j[b.id];
        b.service = s ? s.state : "running"; b.serviceWhy = s && s.why || "";
        return b.service;
      }, function () { return b.service || "running"; });
    }
    (function poll() {
      b._watchTimer = setTimeout(function () {
        b._watchTimer = null;
        if (!b.watching || b._watchGen !== gen || S.restarting) return;
        tries++;
        fetchT(b.url + "/", 4000).then(function (r) { return r.ok || r.status < 500; }, function () { return false; }).then(function (ok) {
          if (!b.watching || b._watchGen !== gen || S.restarting) return;
          if (!ok) {
            var before = b.service;
            return supervised().then(function (state) {
              if (!b.watching || b._watchGen !== gen || S.restarting) return;
              if (state === "not started" && before !== state && S.cur && S.cards.some(function (c) { return c.missing && c.backend === b.id; })) openTab(S.tabId);
              if (state === "stopped") {                              // the launcher gave up: no more polling
                b.watching = false; paintStatus();
                if (S.cur && S.cards.some(function (c) { return c.missing && c.backend === b.id; })) openTab(S.tabId);
                return;
              }
              paintStatus();
              poll();
            });
          }
          b.service = "running";
          var hadDefs = !!(S.widgets[b.id] && Object.keys(S.widgets[b.id]).length);
          (hadDefs ? Promise.resolve() : loadBackend(b)).then(function () {
            if (b._watchGen !== gen || S.restarting) return;
            b.watching = false;
            if (hadDefs) { b.up = true; paintStatus(); backendArrived(b); }      // loadBackend repaints and redraws itself
            else if (!b.up) watchBackend(b);
          });
        });
      }, b.service === "not started" ? 60000 : tries < 12 ? 5000 : 15000);
    })();
  }
  function backendDown(bid) {
    var b = S.byId[bid];
    if (!b || !b.url) return;
    if (b.up !== false) { b.up = false; paintStatus(); }
    watchBackend(b);
  }
  function paintStatus() {
    var box = $("backend-status"); box.textContent = "";
    box.className = "be-wrap"; box.title = "Data status: sources that are down upstream and API keys that are not set";
    box.onclick = function () { openDashboard(STATUS[0], STATUS[1]); };
    var down = [], stopped = [], absent = [], restarting = [];
    S.backends.forEach(function (b) {
      var st = b.up ? b.count + " widgets" : b.up === false ? (b.service === "restarting" ? "restarting…" : b.service === "stopped" ? "stopped" : b.service === "not started" ? "not started" : "offline") : "connecting…";
      box.appendChild(el("div", { class: "be " + (b.up ? "up" : b.up === false ? "down" : ""), title: (b.url || "this server") + (b.serviceWhy ? " · " + b.serviceWhy : "") }, [el("i"), b.name, el("span", { class: "n" }, st)]));
      if (b.up === false) (b.service === "restarting" ? restarting : b.service === "stopped" ? stopped : b.service === "not started" ? absent : down).push(b.name);
    });
    var ban = $("banner"), parts = (S.notices || []).map(function (t) { return el("div", null, t); });
    function are(list) { return list.join(" and ") + (list.length > 1 ? " are" : " is"); }
    if (restarting.length) parts.push(el("div", null, "Restarting data services… Cards reload automatically when ready."));
    if (down.length) parts.push(el("div", null, [are(down) + " not answering (starting or restarting). Its cards load by themselves when it is back; if it stays down for minutes, double-click ", el("code", null, "Start.cmd"), " (or start.sh). Logs: local/logs."]));
    if (stopped.length) parts.push(el("div", null, [are(stopped) + " stopped: it crashed repeatedly and the launcher no longer restarts it, so this page has stopped waiting for it. Look at local/logs, then start the terminal again with ", el("code", null, "Start.cmd"), " (or start.sh) and reload this page."]));
    if (absent.length) parts.push(el("div", null, [are(absent) + " not running (not started by the launcher). Its cards stay empty; start the terminal with ", el("code", null, "Start.cmd"), " (or start.sh)."]));
    ban.classList.toggle("hidden", !parts.length);
    ban.textContent = "";
    parts.forEach(function (p) { ban.appendChild(p); });
  }
  /** A message that stays in the banner for this page (unreadable layout file, restored copy, dashboards missing). */
  function notice(text) {
    S.notices = (S.notices || []).concat([text]);
    paintStatus();
  }
  function paintWarm(w) {
    S.warm = w || null;
    var ban = $("warm-banner"), st = $("warm-status");
    if (!ban || !st) return;
    if (!w || w.phase === "idle" || w.phase === "ready") {
      ban.classList.add("hidden"); ban.textContent = "";
      st.classList.add("hidden"); st.textContent = "";
      return;
    }
    if (w.phase === "landing") {
      ban.classList.remove("hidden"); ban.textContent = "";
      ban.appendChild(el("div", { class: "spin sm" }));
      ban.appendChild(el("span", null, "Preparing initial market data and charts (" + (w.done || 0) + "/" + (w.total || 0) + " ready" + (w.current ? " · " + w.current : "") + "). Cards and graphs appear automatically as they finish."));
      st.classList.remove("hidden"); st.textContent = "";
      st.appendChild(el("div", { class: "spin sm" }));
      st.appendChild(el("span", null, "Warming initial data (" + (w.done || 0) + "/" + (w.total || 0) + ")…"));
      return;
    }
    ban.classList.add("hidden"); ban.textContent = "";
    st.classList.remove("hidden"); st.textContent = "";
    st.appendChild(el("div", { class: "spin sm" }));
    st.appendChild(el("span", null, "Warming background caches (" + (w.bg_done || 0) + "/" + (w.bg_total || 0) + ")…"));
  }
  T.paintWarm = paintWarm;
  function pollWarmStatus() {
    fetchT("/api/warm", 4000).then(function (r) { return r.ok ? r.json() : null; }).then(function (w) {
      if (!w) return;
      paintWarm(w);
      if (w.phase === "landing" || w.phase === "background") setTimeout(pollWarmStatus, 2000);
    }).catch(function () {});
  }

  // ---------- widget index (same data function across providers / backends) ----------
  function isChartVariant(d) { return d.type === "chart" && d.params.some(function (p) { return p.paramName === "chart"; }); }
  function epKey(bid, d) { return (bid === "x" ? "x:" + d.endpoint : String(d.endpoint).replace("/api/v1/", "")) + (isChartVariant(d) ? "#chart" : ""); }
  function providerLabel(bid, d) {
    return T.providerName(d) || "Default";
  }
  function buildIndex() {
    S.epIndex = {};
    S.backends.forEach(function (b) {
      var defs = S.widgets[b.id] || {};
      Object.keys(defs).forEach(function (id) { var k = epKey(b.id, defs[id]); (S.epIndex[k] = S.epIndex[k] || []).push({ backend: b.id, widgetId: id }); });
    });
    S.areaOf = {};
    S.generated.forEach(function (d) {
      var sec = S.sections.filter(function (s) { return s.id === d.section; })[0];
      d.tabs.forEach(function (t) {
        t.items.forEach(function (it) {
          var def = resolveDef(it.backend, it.widgetId);
          if (!def) return;
          var k = epKey(it.backend, def).replace("#chart", "");
          var where = { section: d.section, text: (sec ? sec.name : "") + " › " + d.name + " › " + t.name };
          if (!S.areaOf[k]) S.areaOf[k] = where;
          (it.choices || []).forEach(function (c) {
            var cd = resolveDef(c.b, c.w);
            if (!cd) return;
            var ck = epKey(c.b, cd).replace("#chart", "");
            if (!S.areaOf[ck]) S.areaOf[ck] = { section: d.section, text: where.text + (it.title ? " › " + it.title : "") };
          });
        });
      });
    });
  }
  function siblings(item, def) {
    if (!S.epIndex) buildIndex();
    var all = (S.epIndex[epKey(item.backend, def)] || []).map(function (s) {
      var d = S.widgets[s.backend][s.widgetId];
      return { label: providerLabel(s.backend, d), value: s.backend + ":" + s.widgetId, backend: s.backend, widgetId: s.widgetId, current: d === def };
    });
    // a card built for some sources only (a commodity curve: Yahoo, not Cboe's VX or Deribit's crypto curves; review C3)
    if (item.srcOnly && item.srcOnly.length) all = all.filter(function (s) { return s.current || item.srcOnly.some(function (p) { return s.widgetId.indexOf("_" + p + "_") >= 0; }); });
    // the same provider in both data services is one choice (the main one), unless the legacy copy is the one shown
    return all.filter(function (s) { return s.current || s.backend !== "v4" || !all.some(function (o) { return o.backend === "v5" && o.label === s.label; }); });
  }

  // ---------- one card, several sources ("concept siblings", built by build_dashboards.py) ----------
  function conceptIndex() {
    if (S.conceptIx && S.conceptIxSrc === S.concepts) return S.conceptIx;
    S.conceptIxSrc = S.concepts;
    S.conceptIx = {};
    Object.keys(S.concepts || {}).forEach(function (id) {
      (S.concepts[id].members || []).forEach(function (m) {
        if (m.alt) return;
        var k = m.b + ":" + m.w;
        (S.conceptIx[k] = S.conceptIx[k] || []).push({ id: id, member: m });
      });
    });
    return S.conceptIx;
  }
  function paramScore(params, p) {
    var score = 0;
    Object.keys(p || {}).forEach(function (k) {
      if (!params || params[k] == null) return;
      score += String(params[k]) === String(p[k]) ? 1 : -1;
    });
    return score;
  }
  function bestByParam(list, params, getP) {
    var best = list[0], bs = paramScore(params, getP(best));
    for (var i = 1; i < list.length; i++) {
      var s = paramScore(params, getP(list[i]));
      if (s > bs) { best = list[i]; bs = s; }
    }
    return best;
  }
  function findMember(cid, item) {
    var ms = (S.concepts[cid] && S.concepts[cid].members) || [], k = item.backend + ":" + item.widgetId;
    var hits = ms.filter(function (m) { return m.b + ":" + m.w === k; });
    return hits.length <= 1 ? (hits[0] || null) : bestByParam(hits, item.params, function (m) { return m.p; });
  }
  function memberOf(item) {
    if (item._concept && S.concepts && S.concepts[item._concept]) {
      return { id: item._concept, member: findMember(item._concept, item) };
    }
    var base = item._orig || item;
    var cands = conceptIndex()[(base.backend || item.backend) + ":" + (base.widgetId || item.widgetId)] || [];
    if (!cands.length) return null;
    var hit = cands.length === 1 ? cands[0] : bestByParam(cands, base.params, function (e) { return e.member.p; });
    return { id: hit.id, member: base === item ? hit.member : findMember(hit.id, item) };
  }
  function selOf(key, item) { var d = S.cur && S.state[key] && S.state[key][S.cur.id]; return d ? d[item.uid] : undefined; }
  function symbolOf(item, def) {
    var g = groupOf(item, "symbol");
    if (g && g.value) return Array.isArray(g.value) ? g.value[0] : g.value;
    if (item.params && item.params.symbol) return item.params.symbol;
    var p = def && def.params.filter(function (x) { return x.paramName === "symbol"; })[0];
    return p ? p.value : "";
  }
  /** Every source a card can switch to: its parameter-driven sources, its concept siblings, and same-route providers. */
  function alternatives(item, def, cur) {
    var out = [], seen = {};
    if (def.sourceParam) {
      def.sourceParam.options.forEach(function (o) { out.push({ label: o.label, value: "param:" + o.value, current: String(cur.source || "auto") === String(o.value), note: def.sourceParam.note || "" }); });
      return out;
    }
    var mo = memberOf(item), mkt = T.marketOf(cur.symbol || "");
    if (mo) {
      S.concepts[mo.id].members.forEach(function (m) {
        if (!resolveDef(m.b, m.w)) return;
        var k = m.b + ":" + m.w;
        seen[k] = 1;
        out.push({ label: m.l + (m.k ? " (needs a key)" : ""), value: k, current: mo.member ? m === mo.member : k === item.backend + ":" + item.widgetId,
          note: m.mk ? m.mk.map(function (x) { return T.MARKET_NAMES[x] || x; }).join(", ") : "", dim: !!(m.mk && mkt && m.mk.indexOf(mkt) < 0) });
      });
    }
    // a same-route provider that a concept member already offers under a fuller label ("TMX" next to "TMX (Toronto)") is not listed twice
    var named = out.map(function (o) { return o.label; });
    siblings(item, def).forEach(function (s) {
      if (seen[s.value] || (!s.current && named.some(function (l) { return l === s.label || l.indexOf(s.label + " (") === 0; }))) return;
      seen[s.value] = 1; out.push({ label: s.label, value: s.value, current: s.current });
    });
    return out.length > 1 ? out : [];
  }
  /** Whether a saved source selection belongs to this card's concept or same data route (guards against cross-concept picks). */
  function validSource(item, value) {
    var i = value.indexOf(":"), b = value.slice(0, i), w = value.slice(i + 1);
    if (i <= 0) return false;
    var mo = memberOf(item);
    if (mo && (S.concepts[mo.id].members || []).some(function (m) { return m.b + ":" + m.w === value; })) return true;
    var od = resolveDef(item.backend, item.widgetId), nd = resolveDef(b, w);
    if (!nd) return true;
    if (od) {
      var ok = epKey(item.backend, od), nk = epKey(b, nd);
      if (ok === nk) return true;
      return !!(mo && (S.concepts[mo.id].members || []).some(function (m) {
        var md = resolveDef(m.b, m.w);
        return md && epKey(m.b, md) === nk;
      }));
    }
    var hits = conceptIndex()[value] || [];
    return !hits.length || !!(mo && hits.some(function (h) { return h.id === mo.id; }));
  }
  /** Point a card at another source: shared settings carried over (renamed where the source names them differently). */
  function applySource(item, value, dashGroups) {
    var i = value.indexOf(":"), b = value.slice(0, i), w = value.slice(i + 1), nd = resolveDef(b, w);
    if (!nd) return false;
    var cur = memberOf(item), cid = cur && cur.id, m = null;
    if (cid && S.concepts[cid]) {
      m = (S.concepts[cid].members || []).filter(function (x) { return x.b + ":" + x.w === value; })[0] || null;
    } else {
      var hits = conceptIndex()[value] || [];
      if (hits.length === 1) { cid = hits[0].id; m = hits[0].member; }
    }
    var names = nd.params.map(function (x) { return x.paramName; }), keep = {};
    if (!item._orig) {           // the layout's own card, which savedItem() writes back: a source pick is a preference (srcsel)
      item._orig = { widgetId: item.widgetId, backend: item.backend, params: Object.assign({}, item.params), needsKey: item.needsKey };
      if (Object.prototype.hasOwnProperty.call(item, "chart")) item._orig.chart = cloneVal(item.chart); else item._orig._noChart = true;
      if (Array.isArray(item.groups)) item._orig.groups = item.groups.slice(); else item._orig._noGroups = true;
    }
    if (cid) item._concept = cid;
    Object.keys(item.params || {}).forEach(function (k) {
      if (k === "provider" || (m && m.dp && m.dp.indexOf(k) >= 0)) return;
      var nk = (m && m.rn && m.rn[k]) || k;
      if (names.indexOf(nk) >= 0) keep[nk] = item.params[k];
    });
    if (m && m.p) Object.keys(m.p).forEach(function (k) { keep[k] = m.p[k]; });
    item.widgetId = w; item.backend = b; item.params = keep;
    if (cid && S.concepts[cid] && S.concepts[cid].members.some(function (x) { return x.mk; })) {
      var gs = dashGroups || (S.cur && S.cur.groups) || [];
      item.groups = item.groups || [];
      gs.forEach(function (g) {
        if (g.paramName === "symbol" && names.indexOf("symbol") >= 0 && !(g.paramName in keep) && item.groups.indexOf(g.name) < 0) item.groups.push(g.name);
      });
    }
    delete item.chart; delete item.view; delete item.flip; delete item._nocover;
    if (m && m.k) item.needsKey = m.k; else delete item.needsKey;
    return true;
  }
  /** Ticker-driven cards take the source that covers the ticker's market (Cboe for AAPL, TMX for SU.TO, Deribit for BTC). */
  function routeItem(item, onGroupChange) {
    var mo = memberOf(item), had = !!item._nocover;
    delete item._nocover;
    if (!mo) return had;
    var c = S.concepts[mo.id];
    if (!c.members.some(function (m) { return m.mk; })) return had;
    var sym = symbolOf(item, resolveDef(item.backend, item.widgetId));
    if (!sym && S.cur && Array.isArray(S.cur.groups)) {
      var sg = S.cur.groups.filter(function (g) { return g.paramName === "symbol"; })[0];
      if (sg && sg.value) sym = Array.isArray(sg.value) ? sg.value[0] : sg.value;
    }
    var mkt = T.marketOf(sym);
    function covers(m) { return !!(m && (!m.mk || !mkt || m.mk.indexOf(mkt) >= 0)); }
    var pick = selOf("srcsel", item), pm = pick && c.members.filter(function (m) { return m.b + ":" + m.w === pick; })[0];
    // On tab open, a user's explicit source pick stands even if they chose another market's source for a U.S. ticker
    // (TMX for dual-listed ENB). A listing abroad (SU.TO, SHEL.L, BTC-USD) is routed to a source that covers it, for
    // display only: the saved pick is kept and comes back with a U.S. ticker.
    var pickStands = pm && (covers(pm) || mkt === "us" || !mkt);
    if (!onGroupChange && pick === item.backend + ":" + item.widgetId && pickStands && resolveDef(pm.b, pm.w)) return had;
    if (covers(mo.member) && (!(mo.member && mo.member.paid) || pick === item.backend + ":" + item.widgetId)) return had;
    // automatic routing never lands on a paid plan; the user can still pick it
    if (pm && (covers(pm) || (!onGroupChange && pickStands)) && resolveDef(pm.b, pm.w)) { applySource(item, pm.b + ":" + pm.w); return true; }
    if (onGroupChange && pick && S.cur && S.state.srcsel && S.state.srcsel[S.cur.id]) {
      delete S.state.srcsel[S.cur.id][item.uid];
      saveNow();
    }
    var target = c.members.filter(function (m) { return covers(m) && !m.paid && resolveDef(m.b, m.w); })[0];
    if (target) { applySource(item, target.b + ":" + target.w); return true; }
    item._nocover = { text: (c.none || "No source on this card covers {sym}.").replace("{sym}", String(sym).toUpperCase()),
      hint: c.members.filter(function (m) { return m.mk; }).map(function (m) { return m.l + ": " + m.mk.map(function (x) { return T.MARKET_NAMES[x] || x; }).join(", "); }).join(" · ") + (c.hint ? ". " + c.hint : ".") };
    return true;
  }
  /** A source picked on the card: remembered per card (state.srcsel), not a layout edit. */
  function pickSource(item, a) {
    S.state.srcsel = S.state.srcsel || {};
    (S.state.srcsel[S.cur.id] = S.state.srcsel[S.cur.id] || {})[item.uid] = a.value;
    saveNow();
    if (a.value.indexOf("param:") === 0) {
      S.cards.forEach(function (c) { if (c.item === item) { c.redrawParams(); c.refresh(false); } });
    } else {
      applySource(item, a.value);
      replaceCard(item);
      paintGroups();
    }
    T.toast("Source: " + a.label.replace(/ \(needs a key\)$/, ""));
  }
  /** Chart strip choices and parameter-driven source picks, applied on top of the card's settings. */
  function overrides(item) {
    var out = Object.assign({}, selOf("viewsel", item) || {}), s = selOf("srcsel", item);
    if (typeof s === "string" && s.indexOf("param:") === 0) out.source = s.slice(6);
    return out;
  }
  function setView(item, param, value) {
    S.state.viewsel = S.state.viewsel || {};
    var d = S.state.viewsel[S.cur.id] = S.state.viewsel[S.cur.id] || {}, v = d[item.uid] = d[item.uid] || {};
    if (value == null) delete v[param]; else v[param] = value;
    if (!Object.keys(v).length) delete d[item.uid];
    saveNow();
    S.cards.forEach(function (c) {
      if (c.item !== item) return;
      if (param.charAt(0) === "_" && c.rerender) c.rerender();
      else { c.redrawParams(); c.refresh(false); }
    });
  }
  /** Columns the user picked for a list (state.colsel[dashboard][card]); like source and view picks, not a layout edit. */
  function colsel(item) {
    var d = S.cur && S.state.colsel && S.state.colsel[S.cur.id];
    return d && Array.isArray(d[item.uid]) ? d[item.uid] : null;
  }
  function setCols(item, fields) {
    if (!S.cur) return;
    S.state.colsel = S.state.colsel || {};
    var d = S.state.colsel[S.cur.id] = S.state.colsel[S.cur.id] || {};
    if (fields && fields.length) d[item.uid] = fields; else delete d[item.uid];
    if (!Object.keys(d).length) delete S.state.colsel[S.cur.id];
    if (!Object.keys(S.state.colsel).length) delete S.state.colsel;
    saveNow();
  }
  /** Which dashboard groups (ticker, country...) a widget should follow. */
  function assignGroups(item, def, groups) {
    item.groups = [];
    var pnames = (def.params || []).filter(function (p) { return p.show !== false; }).map(function (p) { return p.paramName; });
    (groups || []).forEach(function (g) {
      if (item.groups.some(function (n) { return groups.some(function (o) { return o.name === n && o.paramName === g.paramName; }); })) return;
      if (g.syncFrom) {
        var parent = groups.filter(function (o) { return o.name === g.syncFrom; })[0];
        if (parent && pnames.indexOf(parent.paramName) >= 0) return;
      }
      if (pnames.indexOf(g.paramName) >= 0) item.groups.push(g.name);
    });
  }
  T.assignGroups = assignGroups;
  function replaceCard(item) {
    for (var i = 0; i < S.cards.length; i++) {
      if (S.cards[i].item !== item) continue;
      S.cards[i].destroy();
      var card = makeCard(item);
      S.cards[i] = card;
      S.grid.replace(item, card.node, card.head, card.resize);
      card.check();
      return card;
    }
    return null;
  }
  /** Library card: show another dataset of its family. The choice is remembered without making the tab a private copy. */
  function swapChoice(item, idx, transient) {
    var c = item.choices && item.choices[idx], nd = c && resolveDef(c.b, c.w);
    if (!nd) return null;
    applyChoice(item, c, nd, S.cur.groups);
    if (!transient) {                       // "Go to" a dataset shows it without changing the remembered pick
      S.state.libsel = S.state.libsel || {};
      (S.state.libsel[S.cur.id] = S.state.libsel[S.cur.id] || {})[item.uid] = c.w;
      if (S.state.srcsel && S.state.srcsel[S.cur.id]) delete S.state.srcsel[S.cur.id][item.uid];      // a new dataset starts on its own source
      saveNow();
    }
    var card = replaceCard(item);
    paintGroups();
    return card;
  }
  /** Point a library card at one of its datasets, with that dataset's working defaults (built by build_dashboards.py). */
  function applyChoice(item, c, nd, groups) {
    if (!item._origLib) item._origLib = {
      widgetId: item.widgetId, backend: item.backend,
      params: cloneVal(item.params || {}),
      chart: item.chart === undefined ? undefined : cloneVal(item.chart)
    };
    item._choiceBase = {
      params: Object.assign({}, c.p || {}),
      chart: c.c ? Object.assign({}, c.c) : undefined
    };
    item.widgetId = c.w; item.backend = c.b; item.params = Object.assign({}, c.p || {}); delete item._orig; delete item._concept; delete item._nocover;
    delete item.chart; delete item.view; delete item.flip; delete item._guessedChart; delete item._vbase;
    if (c.c) { item.chart = Object.assign({}, c.c); item.view = "chart"; }      // a dataset that reads best plotted (Beveridge curve)
    if (c.r) item.require = c.r; else delete item.require;
    if (c.k) item.needsKey = c.k; else delete item.needsKey;
    assignGroups(item, nd, groups);
  }
  // ---------- sidebar ----------
  function templates() { return S.generated.concat(S.sourceApps); }
  function userDashboards() {
    return Object.keys(S.state.dashboards).map(function (k) { return S.state.dashboards[k]; }).filter(function (d) { return d.source === "user"; })
      .sort(function (a, b) { return a.name.localeCompare(b.name); });
  }
  function collapsed(id) {
    var c = S.state.ui.collapsed[id];
    return c == null ? id === "sources" : c;
  }
  function paintSidebar() {
    var box = $("dash-list"), q = $("dash-filter").value.trim().toLowerCase();
    box.textContent = "";
    function row(dash) {
      var modified = dash.source === "app" && S.state.dashboards[dash.id];
      box.appendChild(el("div", { class: "dash-item" + (S.cur && S.cur.id === dash.id ? " active" : ""), title: dash.description || dash.name, onclick: function () { openDashboard(dash.id); } },
        [el("span", { class: "nm" }, dash.name), modified ? el("span", { class: "dot", title: "You customised this dashboard" }) : null]));
    }
    function match(d) { return !q || d.name.toLowerCase().indexOf(q) >= 0 || (d.description || "").toLowerCase().indexOf(q) >= 0; }

    var mine = userDashboards().filter(match);
    box.appendChild(el("div", { class: "dash-sec" }, [el("span", null, "My dashboards"), el("button", { class: "icon-btn", type: "button", title: "New dashboard", onclick: newDashboard }, "+")]));
    mine.forEach(row);
    if (!mine.length && !q) box.appendChild(el("div", { class: "muted small", style: { padding: "2px 8px 6px" } }, "None yet. Press + to build one."));

    S.sections.forEach(function (sec) {
      var list = templates().filter(function (d) { return d.section === sec.id && match(d); });
      if (sec.id === "sources") list.sort(function (a, b) { return a.name.localeCompare(b.name); });
      if (!list.length) return;
      var open = q ? true : !collapsed(sec.id) || (S.cur && S.cur.section === sec.id && S.state.ui.collapsed[sec.id] == null && sec.id !== "sources");
      var head = el("div", { class: "dash-sec click", title: open ? "Collapse" : "Expand" }, [el("i", { class: "caret" }, open ? "▾" : "▸"), el("span", null, sec.name), el("em", null, String(list.length))]);
      head.addEventListener("click", function () { S.state.ui.collapsed[sec.id] = open; saveNow(); paintSidebar(); });
      box.appendChild(head);
      if (open) list.forEach(row);
    });
  }

  // ---------- dashboards ----------
  function findDashboard(id) {
    var t = templates().filter(function (x) { return x.id === id; })[0];
    var saved = S.state.dashboards[id];
    S.state.groups = S.state.groups || {};
    // Copies made only because a ticker or country was typed (older versions did that) would hide
    // layout updates: keep the choices, drop the copy.
    if (saved && t && saved.mode !== "overrides" && !saved.edited) {
      var keep = S.state.groups[id] = S.state.groups[id] || {};
      (saved.groups || []).forEach(function (g) { if (!(g.name in keep) && g.value != null) keep[g.name] = g.value; });
      if (id.indexOf("d:") === 0) {
        markLegacy(t, saved);                    // a copy from before per-tab marks may still hold real edits
        if (!(saved.tabs || []).some(function (x) { return x.edited; }) && !(saved.hiddenTabs || []).length) saved = null;
      } else if (!(saved.tabs || []).some(function (x) { return x.edited; }) && !(saved.hiddenTabs || []).length) {
        saved = null;
        delete S.state.dashboards[id];
      }
    }
    var d;
    if (saved && t && id.indexOf("d:") === 0) {
      d = mergeDashboard(t, saved);
      if (saved.mode !== "overrides") {
        var ov = diffDashboard(t, d);
        if (ov) S.state.dashboards[id] = ov; else delete S.state.dashboards[id];
      } else if (!d.edited) {
        delete S.state.dashboards[id];
      }
    } else {
      if (!saved && t && S.state.dashboards[id]) delete S.state.dashboards[id];
      d = saved || (t ? JSON.parse(JSON.stringify(t)) : null);
    }
    // a remembered pick that is empty (null, "", []) means "no pick": the dashboard's default ticker or country stays
    if (d && S.state.groups[id]) d.groups.forEach(function (g) {
      var v = S.state.groups[id][g.name];
      if (g.name in S.state.groups[id] && v != null && v !== "" && !(Array.isArray(v) && !v.length)) g.value = v;
    });
    var sel = d && S.state.libsel && S.state.libsel[id];
    if (sel) d.tabs.forEach(function (t) {
      t.items.forEach(function (it) {
        var c = it.choices && sel[it.uid] && it.choices.filter(function (x) { return x.w === sel[it.uid]; })[0];
        var cd = c && resolveDef(c.b, c.w);
        if (cd && it.widgetId !== c.w) applyChoice(it, c, cd, d.groups);
      });
    });
    var ss = d && S.state.srcsel && S.state.srcsel[id];
    if (ss) d.tabs.forEach(function (t) {
      t.items.forEach(function (it) {
        var v = ss[it.uid];
        if (typeof v !== "string" || v.indexOf("param:") === 0 || v === it.backend + ":" + it.widgetId) return;
        if (validSource(it, v)) applySource(it, v, d.groups);
        else if (resolveDef(it.backend, it.widgetId)) delete ss[it.uid];
      });
    });
    // edited tabs are the user's saved copies: give their cards the source hint the current layout has for them
    var tpl = t && id.indexOf("d:") === 0 ? t : null;
    if (tpl && d) {
      var hint = {};
      tpl.tabs.forEach(function (tt) { tt.items.forEach(function (it) { if (it.src) hint[it.uid + "|" + it.widgetId] = it.src; }); });
      d.tabs.forEach(function (tt) { tt.items.forEach(function (it) { if (!it.src && hint[it.uid + "|" + it.widgetId]) it.src = hint[it.uid + "|" + it.widgetId]; }); });
    }
    return d;
  }
  T.memberOf = memberOf; T.alternatives = alternatives; T.applySource = applySource; T.routeItem = routeItem; T.pickSource = pickSource; T.findDashboard = findDashboard;
  function openDashboard(id, tabId) {
    var d = findDashboard(id);
    if (!d) return false;
    S.cur = d;
    if (!d.tabs.length) d.tabs.push({ id: "main", name: "Main", items: [] });
    var sec = S.sections.filter(function (s) { return s.id === d.section; })[0];
    var title = $("dash-title");
    title.textContent = "";
    if (sec && d.source !== "user") title.appendChild(el("span", { class: "crumb" }, sec.name + " › "));
    title.appendChild(document.createTextNode(d.name));
    title.title = d.description || d.name;
    paintSidebar();
    openTab(tabId && d.tabs.some(function (t) { return t.id === tabId; }) ? tabId : d.tabs[0].id);
    return true;
  }
  function openDefault() {
    var last = lastSeen();
    if (last && openDashboard(last.id, last.tab)) return;
    if (openDashboard(HOME)) return;
    var first = templates()[0] || userDashboards()[0];
    if (first) openDashboard(first.id); else paintEmpty();
  }
  function curTab() { return S.cur ? S.cur.tabs.filter(function (t) { return t.id === S.tabId; })[0] : null; }

  function paintTabs() {
    var nav = $("tabs"); nav.textContent = "";
    if (!S.cur) return;
    S.cur.tabs.forEach(function (t) {
      var x = el("span", { class: "x", title: "Delete tab" }, "✕");
      var tab = el("div", { class: "tab" + (t.id === S.tabId ? " active" : ""), title: "Double-click to rename" }, [t.name, S.cur.tabs.length > 1 ? x : null]);
      tab.addEventListener("click", function (e) { if (e.target !== x) openTab(t.id); });
      tab.addEventListener("dblclick", function () { ask("Rename tab", t.name, "Rename").then(function (v) { if (v) { t.name = v; touch(); paintTabs(); } }); });
      x.addEventListener("click", function () {
        (t.items.length ? confirmBox('Delete tab "' + t.name + '" and its ' + t.items.length + " widgets?") : Promise.resolve(true)).then(function (ok) {
          if (!ok) return;
          S.cur.tabs = S.cur.tabs.filter(function (o) { return o !== t; });
          var tpl = S.cur.id.indexOf("d:") === 0 && S.generated.filter(function (g) { return g.id === S.cur.id; })[0];
          var builtIn = tpl ? (tpl.tabs || []).some(function (bt) { return bt.id === t.id; }) : !userTab(t, null);
          if (builtIn) (S.cur.hiddenTabs = S.cur.hiddenTabs || []).push(t.id);      // a removed built-in tab stays removed
          touch();
          openTab(S.cur.tabs[0].id);
        });
      });
      nav.appendChild(tab);
    });
    nav.appendChild(el("div", { class: "tab add", title: "Add tab", onclick: addTab }, "+"));
    // tabs wider than the window (Company at 1440 px clipped "Sho…"; review G2): arrows at both ends, the wheel scrolls
    // sideways; the arrows show only while there is more to see on their side
    var step = function (d) { nav.scrollLeft += d * Math.max(120, nav.clientWidth * 0.6); tabArrows(nav); };
    nav.insertBefore(el("div", { class: "tab-arrow l", title: "Earlier tabs", onclick: function () { step(-1); } }, "‹"), nav.firstChild);
    nav.appendChild(el("div", { class: "tab-arrow r", title: "More tabs", onclick: function () { step(1); } }, "›"));
    if (!nav._scrollWired) {
      nav._scrollWired = true;
      nav.addEventListener("wheel", function (e) {
        if (nav.scrollWidth <= nav.clientWidth || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
        nav.scrollLeft += e.deltaY; e.preventDefault();
      }, { passive: false });
      nav.addEventListener("scroll", function () { tabArrows(nav); });
      window.addEventListener("resize", function () { tabArrows(nav); });
    }
    var active = nav.querySelector(".tab.active");
    if (active && active.scrollIntoView) active.scrollIntoView({ block: "nearest", inline: "nearest" });
    tabArrows(nav);
  }
  function tabArrows(nav) {
    nav.classList.toggle("more-l", nav.scrollLeft > 2);
    nav.classList.toggle("more-r", nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2);
  }
  function addTab() {
    ask("New tab name", "New tab", "Add").then(function (v) {
      if (!v || !S.cur) return;
      var id = "u-" + Date.now().toString(36);
      S.cur.tabs.push({ id: id, name: v, items: [], edited: true });
      touch();
      openTab(id);
    });
  }

  function groupOf(item, paramName) {
    if (!S.cur || !item.groups || !item.groups.length) return null;
    for (var i = 0; i < S.cur.groups.length; i++) { var g = S.cur.groups[i]; if (g.paramName === paramName && item.groups.indexOf(g.name) >= 0) return g; }
    return null;
  }
  function groupsOf(item) {
    return S.cur ? S.cur.groups.filter(function (g) { return (item.groups || []).indexOf(g.name) >= 0; }) : [];
  }
  var syncSeq = {};
  function setGroup(g, v, sourceWin) {
    if (typeof v === "string") { v = v.trim(); if (g.paramName === "symbol") v = v.toUpperCase(); }
    if (Array.isArray(v) && !v.length) v = null;
    g.value = v === "" ? null : v;
    rememberGroup(g);
    // a group that belongs to another (Report to Symbol) starts over when that one changes: a new company opens on its latest report
    var reset = [];
    if (S.cur) S.cur.groups.forEach(function (o) { if (o !== g && o.resetWith === g.name && o.value != null && o.value !== "") { o.value = null; rememberGroup(o); reset.push(o.name); } });
    if (S.cur && S.cur.id.indexOf("d:") !== 0 && S.state.dashboards[S.cur.id]) S.state.dashboards[S.cur.id] = S.cur;
    paintGroups();
    if (S.cur && g.value != null && g.value !== "") {
      S.cur.groups.forEach(function (o) {
        if (o === g || o.syncFrom !== g.name || !o.syncUrl) return;
        var mySeq = (syncSeq[o.name] = (syncSeq[o.name] || 0) + 1), dashId = S.cur.id;
        T.request(o.syncUrl + "?" + encodeURIComponent(g.paramName) + "=" + encodeURIComponent(g.value))
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) {
            if (mySeq !== syncSeq[o.name] || !S.cur || S.cur.id !== dashId) return;
            if (j && j[o.paramName] != null && String(j[o.paramName]) !== String(o.value || "")) setGroup(o, String(j[o.paramName]));
          })
          .catch(function () {});
      });
    }
    S.cards.slice().forEach(function (c) {
      if (reset.length && (c.item.groups || []).some(function (n) { return reset.indexOf(n) >= 0; }) && (c.item.groups || []).indexOf(g.name) < 0) {
        if (c.def) { c.redrawParams(); c.refresh(false); }
        return;
      }
      var inGrp = (c.item.groups || []).indexOf(g.name) >= 0;
      var routedConcept = !inGrp && g.paramName === "symbol" && (function () {
        var mo = memberOf(c.item), co = mo && S.concepts[mo.id];
        return !!(co && co.members.some(function (m) { return m.mk; }));
      })();
      if (!inGrp && !routedConcept) {
        if (g.paramName === "symbol" && c.clearLive && c.clearLive()) c.refresh(false);
        return;
      }
      if (sourceWin && c.ownsFrame && c.ownsFrame(sourceWin)) return;
      if (routeItem(c.item, true)) { replaceCard(c.item); return; }       // the new ticker trades where another source covers it
      if (c.def) { c.redrawParams(g.paramName); c.refresh(false); }
    });
  }
  /** Options of a group whose choices depend on other groups ($symbol = the value of the group on "symbol"). */
  function groupOptions(g) {
    return function () {
      var q = new URLSearchParams();
      Object.keys(g.optionsParams || {}).forEach(function (k) {
        var v = g.optionsParams[k];
        if (typeof v === "string" && v.charAt(0) === "$") {
          var src = S.cur && S.cur.groups.filter(function (o) { return o.paramName === v.slice(1); })[0];
          v = src ? src.value : null;
        }
        if (v != null && v !== "") q.set(k, v);
      });
      return T.request(g.optionsEndpoint + "?" + q.toString()).then(function (r) { return r.ok ? r.json() : { results: [] }; })
        .then(function (j) { return Array.isArray(j) ? j : (j && j.results) || []; }).catch(function () { return []; });
    };
  }
  function paintGroups() {
    var box = $("groups"); box.textContent = "";
    var tab = curTab(); if (!tab) return;
    function used(g) { return tab.items.some(function (it) { return (it.groups || []).indexOf(g.name) >= 0; }); }
    S.cur.groups.forEach(function (g) {
      var show = used(g) || S.cur.groups.some(function (o) { return o.syncFrom === g.name && used(o); });
      if (!show) return;
      var control;
      if (g.optionsEndpoint) {
        // choices that depend on another group (Report: the reports of the company in Symbol), asked of the terminal
        control = T.dropdown({ value: g.value == null ? "" : g.value, options: groupOptions(g), allowFree: false, width: 320, placeholder: "latest",
          onChange: function (v) { setGroup(g, v); } });
        control.classList.add("group-dd");
      } else if (g.options && g.options.length) {
        control = T.dropdown({ value: g.value, options: g.options, multi: !!g.multi, allowFree: true, width: 280, placeholder: "choose…", onChange: function (v) { setGroup(g, v); } });
        control.classList.add("group-dd");
      } else {
        var isSym = g.paramName === "symbol";
        control = el("input", { type: "text", value: g.value == null ? "" : Array.isArray(g.value) ? g.value.join(",") : g.value, spellcheck: "false", autocomplete: "off",
          placeholder: isSym ? "ticker or name" : "", title: isSym ? "Type a ticker or a company name (toyota, 7203.T, SHEL.L) and pick a match. Every linked widget on this dashboard follows." : "Type a value and press Enter. Every linked widget on this dashboard follows." });
        if (isSym) {
          // names are looked up as you type; Enter takes the best match
          T.sec.suggest(control, {
            onPick: function (r) { control.value = r.symbol; T.sec.remember(r.symbol, r.name, r.type); control.blur(); if (r.symbol !== g.value) setGroup(g, r.symbol); },
            onRaw: function (q) { control.blur(); setGroup(g, q); }
          });
        }
        control.addEventListener("keydown", function (e) { if (e.key === "Enter") control.blur(); });
        control.addEventListener("change", function () { var v = control.value.trim(); if ((isSym ? v.toUpperCase() : v) !== (g.value || "")) setGroup(g, v); });
      }
      box.appendChild(el("div", { class: "group", title: "Drives every linked widget on this dashboard" }, [el("label", null, g.name), control]));
    });
    paintJumps(box);
  }
  T.paintGroups = paintGroups;
  /**
   * Company jump buttons (HANDOFF P3-4): Options, Filings, Bonds, News for the company in Symbol, each opening its
   * dashboard with the company in the form that destination needs. /x/company_links decides which destinations can
   * serve the company (a button it leaves out is not drawn). Opening one is navigation, never a layout edit.
   */
  var jumpCache = {};
  function paintJumps(box) {
    if (!S.cur || S.cur.id !== "d:eq-company") return;
    var g = S.cur.groups.filter(function (x) { return x.paramName === "symbol"; })[0];
    var sym = g && typeof g.value === "string" ? g.value.trim().toUpperCase() : "";
    if (!sym) return;
    var holder = el("div", { class: "jumps", title: "Open this company elsewhere" });
    box.appendChild(holder);
    function draw(j) {
      if (!holder.isConnected) return;
      holder.textContent = "";
      ((j && j.results) || []).forEach(function (l) {
        var b = el("button", { class: "btn jump", type: "button", title: "Open " + l.label + " for " + (l.value || sym) }, l.label + " ↗");
        b.addEventListener("click", function () { jumpTo(l); });
        holder.appendChild(b);
      });
    }
    if (!jumpCache[sym]) jumpCache[sym] = T.request("/x/company_links?symbol=" + encodeURIComponent(sym), { timeout: 20000 }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
    jumpCache[sym].then(draw);
  }
  function jumpTo(l) {
    if (l.group) {
      // the value is set before the dashboard opens, so its cards load it straight away (as a ticker search does)
      S.state.groups = S.state.groups || {};
      var sg = (S.state.groups[l.dash] = S.state.groups[l.dash] || {}), more = l.groups || {}, before = sg[l.group];
      sg[l.group] = l.value;
      Object.keys(more).forEach(function (k) { sg[k] = more[k]; });      // a CUSIP: Issuer and Bond together
      saveNow();
      if (!openDashboard(l.dash, l.tab)) return;
      var g = S.cur.groups.filter(function (x) { return x.name === l.group; })[0];
      if (g && g.value !== l.value) setGroup(g, l.value);
      else if (g && before !== l.value) {             // set before opening: a group that starts over with this one does so now
        S.cur.groups.forEach(function (o) { if (o.resetWith === g.name && !(o.name in more) && o.value != null && o.value !== "") setGroup(o, null); });
      }
      Object.keys(more).forEach(function (k) {
        var o = S.cur.groups.filter(function (x) { return x.name === k; })[0];
        if (o && o.value !== more[k]) setGroup(o, more[k]);
      });
      return;
    }
    if (!openDashboard(l.dash, l.tab)) return;
    var hit = false;
    var also = l.also || {};                 // a CUSIP also fills the cards that take one (raw TRACE prices, bond history)
    S.cards.forEach(function (c) {
      if (!c.def) return;
      if (c.hasParam(l.param)) { c.setParamValue(l.param, l.value); hit = true; }
      // not on a card filtered by issuer as well (the raw TRACE card): CUSIP and issuer together would match nothing
      Object.keys(also).forEach(function (k) { if (!c.hasParam(l.param) && !c.hasParam("issuer_name") && c.hasParam(k)) { c.setParamValue(k, also[k]); hit = true; } });
    });
    // a saved layout from before the bond lookup has no card that takes the company: say so instead of doing nothing
    T.toast(hit ? l.label + ": " + l.value : "No card on this tab takes a company (" + l.value + "). If you hid the bond lookup card, search for \"Bond Lookup\" or use \"Reset to default layout\" in the dashboard menu.");
  }
  T.jumpTo = jumpTo;
  /** A 9-character CUSIP with a valid check digit (modulus 10, double-add-double); same rule as terminal/bonds.py. */
  function cusipOk(s) {
    s = String(s || "").trim().toUpperCase();
    if (!/^[0-9A-Z*@#]{8}[0-9]$/.test(s)) return false;
    var total = 0;
    for (var i = 0; i < 8; i++) {
      var ch = s.charAt(i), v = /[0-9]/.test(ch) ? +ch : ch === "*" ? 36 : ch === "@" ? 37 : ch === "#" ? 38 : ch.charCodeAt(0) - 55;
      if (i % 2) v *= 2;
      total += Math.floor(v / 10) + v % 10;
    }
    return (10 - total % 10) % 10 === +s.charAt(8);
  }
  T.cusipOk = cusipOk;
  /** A table cell asked to set `param` elsewhere (click a ticker, a country, a filing…). */
  function broadcast(from, param, value) {
    if (param === "__open") { openSecurity(value); return true; }
    if (value == null || value === "") return false;
    var tab = curTab();
    var gOnTab = S.cur && S.cur.groups.filter(function (x) { return x.paramName === param && tab && tab.items.some(function (it) { return (it.groups || []).indexOf(x.name) >= 0; }); })[0];
    if (gOnTab) { setGroup(gOnTab, value); T.toast(gOnTab.name + " set to " + value); return true; }
    var gOther = S.cur && S.cur.groups.filter(function (x) { return x.paramName === param; })[0];
    if (gOther) {
      var raw = String(value).trim();
      if (S.cur.id === "d:eq-nordic" && param === "symbol") {
        if (/^\^|=X$|=F$|\.OL$/i.test(raw)) { T.openSecurity(raw); return true; }
        value = T.symbolFor ? T.symbolFor(raw, "nordic") : raw;
      }
      var dest = (S.cur.tabs || []).filter(function (t) { return (t.items || []).some(function (it) { return (it.groups || []).indexOf(gOther.name) >= 0; }); })[0];
      setGroup(gOther, value);
      if (dest && dest !== tab) { openTab(dest.id); T.toast("Opened " + value + " in " + dest.name); }
      else T.toast(gOther.name + " set to " + value);
      return true;
    }
    var hit = false, targets = [];
    var isSecUrl = /^https?:\/\/([^/]+\.)?sec\.gov\//i.test(String(value));
    var secUrlWidgets = /^(regulators_)?sec_(filing_headers|htm_file|filing_viewer)_sec_obb$/;
    // row picks drive the other cards for now; they are not layout edits, so nothing is saved
    S.cards.forEach(function (c) {
      if (c.item === from || !c.def) return;
      if (param === "url") {
        if (secUrlWidgets.test(c.def.widgetId) && !isSecUrl) return;
        if (c.def.widgetId === "tmx_equity_filings_viewer_obb" && isSecUrl) return;
      }
      var tp = c.hasParam(param) ? param : (param === "url" && c.hasParam("document_url") ? "document_url" : null);
      if (!tp) return;
      c.setParamValue(tp, value);
      hit = true;
      var lbl = c.item.title || (T.titleOf && T.titleOf(c.def.widgetId)) || c.def.name;
      if (lbl && targets.indexOf(lbl) < 0) targets.push(lbl);
    });
    if (hit && targets.length) T.toast((/url$/i.test(param) ? "Opened in " : "Updated ") + (targets.length > 2 ? targets.slice(0, 2).join(", ") + " +" + (targets.length - 2) : targets.join(", ")));
    // nothing on this tab takes the ticker (screeners, movers, calendars): open it as the search box would (review D1)
    if (!hit && param === "symbol" && /^[A-Za-z0-9^=.\-]{1,15}$/.test(String(value).trim())) { openSecurity(value); return true; }
    if (!hit && /^(cusip|crd|rssd_id|bill_id)$/.test(param)) {
      T.toast("No card on this tab takes " + ({ cusip: "a CUSIP", crd: "a CRD number", rssd_id: "an RSSD id", bill_id: "a bill id" })[param] + " (" + value + ")");
      return true;
    }
    return hit;
  }
  T.broadcast = broadcast; T.setGroup = setGroup;
  T.setView = setView; T.setCols = setCols; T.setPref = setPref;      // exposed for the offline checks (viewing never edits)

  /** Some stock dashboards name a widget slightly differently from the backend; find the closest real one. */
  var aliasCache = {};
  function resolveDef(bid, id) {
    var defs = S.widgets[bid] || {};
    if (defs[id]) return defs[id];
    var key = bid + ":" + id;
    if (key in aliasCache) return aliasCache[key] ? defs[aliasCache[key]] : null;
    var toks = String(id).replace(/_obb$/, "").split("_"), ns = toks[0], ids = Object.keys(defs), found = null;
    if (!ids.length) return null;
    for (var drop = 0; drop <= 2 && !found; drop++) {
      var core = toks.slice(1, toks.length - drop).join("_");
      if (core.split("_").length < 2) break;
      var c = ids.filter(function (x) { return x.indexOf(core) >= 0 && (drop === 0 || x.indexOf(ns) >= 0); });
      if (c.length) found = c.sort(function (a, b) { return a.length - b.length; })[0];
    }
    aliasCache[key] = found;
    return found ? defs[found] : null;
  }
  function makeCard(item) {
    var b = S.byId[item.backend], def = resolveDef(item.backend, item.widgetId);
    function remove() {
      var tab = curTab();
      tab.items = tab.items.filter(function (x) { return x !== item; });
      S.cards = S.cards.filter(function (c) { if (c.item === item) { c.destroy(); return false; } return true; });
      S.grid.remove(item);
      touch(); paintGroups(); paintEmpty();
    }
    if (!def || !b) return T.missingWidget(item, remove, b);
    return T.widget({
      item: item, def: def, backend: b, scroller: $("canvas-wrap"),
      groupOf: groupOf, groupsOf: groupsOf, setGroup: setGroup, broadcast: broadcast, siblings: siblings, swapChoice: swapChoice, backendDown: backendDown, setPref: setPref,
      alternatives: alternatives, pickSource: pickSource, overrides: overrides, setView: setView, colsel: colsel, setCols: setCols, member: function (it) { var m = memberOf(it); return m ? m.member : null; },
      hasFreeOnTab: function (it) { var tab = curTab(); return !!(tab && (tab.items || []).some(function (o) { return o !== it && !o.needsKey && !o._nocover && !o._keyBlocked; })); },
      conceptTitle: function (it) { var m = memberOf(it); return m ? S.concepts[m.id].title || null : null; },
      onChange: touch, onRemove: remove,
      relayout: function () { S.grid.layout(false); }
    });
  }
  function openTab(tabId) {
    S.tabId = tabId;
    S.cards.forEach(function (c) { c.destroy(); });
    S.cards = [];
    S.grid.clear();
    $("canvas-wrap").scrollTop = 0;
    var tab = curTab();
    paintTabs(); paintGroups();
    if (!tab) return paintEmpty();
    tab.items.forEach(function (item) {
      routeItem(item);
      applyPrefs(item);
      var card = makeCard(item);
      S.cards.push(card);
      S.grid.add(item, card.node, card.head, card.resize);
    });
    S.grid.layout(true);
    paintEmpty();
    remember();
    checkAll();
  }
  /** Let each card decide whether it is close enough to the viewport to load. */
  function checkAll() { S.cards.forEach(function (c) { c.check(); }); }
  function paintEmpty() {
    var e = $("empty"), tab = curTab();
    var isEmpty = !tab || !tab.items.length;
    e.classList.toggle("hidden", !isEmpty);
    if (!isEmpty) return;
    e.textContent = "";
    if (!S.cur) {
      e.appendChild(el("h2", null, "Waiting for the data backends"));
      e.appendChild(el("div", null, "Dashboards appear here as soon as a backend answers."));
    } else {
      e.appendChild(el("h2", null, "This tab is empty"));
      e.appendChild(el("div", null, "Add any of the " + S.backends.reduce(function (n, b) { return n + (b.count || 0); }, 0).toLocaleString() + " data widgets."));
      e.appendChild(el("button", { class: "btn primary", type: "button", onclick: function () { openLibrary(); } }, "+ Widget"));
    }
  }

  async function newDashboard() {
    var name = await ask("New dashboard name", "My dashboard", "Create");
    if (!name) return;
    var d = { id: "user:" + Date.now().toString(36), name: name, description: "", source: "user", groups: [{ name: "Symbol", paramName: "symbol", value: "AAPL" }], tabs: [{ id: "main", name: "Main", items: [] }] };
    S.state.dashboards[d.id] = d;
    saveNow();
    openDashboard(d.id);
  }
  function moreMenu() {
    var d = S.cur, items = [{ label: "New dashboard…", onClick: newDashboard }];
    if (d) {
      items.push({ label: "Duplicate as my dashboard…", onClick: function () {
        ask("Name for the copy", d.name + " (mine)", "Duplicate").then(function (v) {
          if (!v) return;
          var c = JSON.parse(JSON.stringify(d));
          c.id = "user:" + Date.now().toString(36); c.name = v; c.source = "user"; delete c.section; delete c.rev;
          delete c.mode; delete c.overrides; delete c.edited; delete c.hiddenTabs; delete c.renamed;
          S.state.dashboards[c.id] = c; saveNow(); openDashboard(c.id);
        });
      } });
      items.push({ label: "Add tab…", onClick: addTab });
      items.push({ label: "Browse all data in this area…", onClick: function () { openLibrary(d.section); } });
      if (d.source === "user") {
        items.push({ label: "Rename dashboard…", onClick: renameDashboard });
        items.push("sep");
        items.push({ label: "Delete dashboard", danger: true, onClick: function () {
          confirmBox('Delete dashboard "' + d.name + '"? This cannot be undone.').then(function (ok) {
            if (!ok) return;
            delete S.state.dashboards[d.id]; S.cur = null; S.state.last = null; saveNow(); openDefault();
          });
        } });
      } else if (S.state.dashboards[d.id]) {
        items.push("sep");
        items.push({ label: "Reset to default layout", danger: true, onClick: function () {
          confirmBox('Discard your changes to "' + d.name + '" and restore the default layout?', "Reset").then(function (ok) {
            if (!ok) return;
            delete S.state.dashboards[d.id]; if (S.state.groups) delete S.state.groups[d.id]; saveNow(); openDashboard(d.id);
          });
        } });
      }
    }
    items.push("sep");
    items.push({ label: "Export all my layouts (JSON)", onClick: function () { T.download(T.product.toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-layouts.json", JSON.stringify(S.state, null, 1), "application/json"); } });
    T.menu($("btn-more"), items);
  }
  function renameDashboard() {
    if (!S.cur || S.cur.source !== "user") return;
    ask("Rename dashboard", S.cur.name, "Rename").then(function (v) { if (v) { S.cur.name = v; $("dash-title").textContent = v; touch(); } });
  }

  // ---------- widget library ----------
  function buildLib() {
    if (!S.epIndex) buildIndex();
    var out = [];
    S.backends.forEach(function (b) {
      var defs = S.widgets[b.id] || {};
      Object.keys(defs).forEach(function (id) {
        var d = defs[id];
        var src = Array.isArray(d.source) ? d.source.join(" ") : d.source || "";
        var area = S.areaOf[epKey(b.id, d).replace("#chart", "")] || null;
        out.push({ b: b, d: d, src: src, area: area, hay: [d.name, d.category, d.subCategory, src, id, area ? area.text : "", d.description].join(" ").toLowerCase(), name: (d.name || id).toLowerCase() });
      });
    });
    out.sort(function (x, y) { return x.name.localeCompare(y.name); });
    return out;
  }
  function addWidget(entry) {
    if (!S.cur) return;
    var tab = curTab(), d = entry.d;
    var item = { uid: "w" + Date.now().toString(36) + Math.floor(Math.random() * 1e4), widgetId: d.widgetId, backend: entry.b.id, x: 0, y: S.grid.bottom(), w: Math.min(40, (d.gridData && d.gridData.w) || 20), h: Math.min(30, (d.gridData && d.gridData.h) || 14), params: {}, groups: [] };
    S.cur.groups.forEach(function (g) {
      if (item.groups.some(function (n) { return S.cur.groups.some(function (o) { return o.name === n && o.paramName === g.paramName; }); })) return;
      if (d.params.some(function (p) { return p.paramName === g.paramName && p.show !== false; })) item.groups.push(g.name);
    });
    tab.items.push(item);
    var card = makeCard(item);
    S.cards.push(card);
    S.grid.add(item, card.node, card.head, card.resize);
    S.grid.layout(true);
    touch(); paintGroups(); paintEmpty();
    card.node.scrollIntoView({ block: "center" });
    checkAll();
    T.toast("Added “" + d.name + "”");
  }
  function openLibrary(sectionId) {
    if (!S.cur) return;
    if (!S.lib) S.lib = buildLib();
    var be = "", type = "", area = typeof sectionId === "string" && sectionId !== "sources" ? sectionId : "", hl = 0, shown = [];
    var input = el("input", { type: "search", placeholder: "Search " + S.lib.length.toLocaleString() + " widgets: income statement, yield curve, CDS, CPI, options…", autocomplete: "off" });
    var chips = el("div", { class: "chips" }), chips2 = el("div", { class: "chips" }), list = el("div", { class: "lib-list" }), foot = el("div", { class: "modal-foot" });
    var box = el("div", { class: "modal" }, [el("div", { class: "modal-head" }, [input, chips2, chips]), list, foot]);
    var m = T.modal(box);
    function chip(label, on, fn) { return el("button", { class: "chip" + (on ? " on" : ""), type: "button", onclick: fn }, label); }
    function drawChips() {
      chips2.textContent = "";
      chips2.appendChild(chip("All areas", !area, function () { area = ""; draw(); }));
      S.sections.forEach(function (s) { if (s.id !== "sources") chips2.appendChild(chip(s.name, area === s.id, function () { area = s.id; draw(); })); });
      chips.textContent = "";
      chips.appendChild(chip("All sources", !be, function () { be = ""; draw(); }));
      S.backends.forEach(function (b) { if (b.up) chips.appendChild(chip(b.name, be === b.id, function () { be = b.id; draw(); })); });
      chips.appendChild(el("span", { style: { width: "10px" } }));
      [["", "Any type"], ["table", "Tables"], ["chart", "Charts"], ["newsfeed", "News"], ["markdown", "Text"], ["multi_file_viewer", "Documents"], ["iframe", "Interactive"]].forEach(function (t) {
        chips.appendChild(chip(t[1], type === t[0], function () { type = t[0]; draw(); }));
      });
    }
    function draw() {
      drawChips();
      var terms = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      var total = 0; shown = [];
      for (var i = 0; i < S.lib.length; i++) {
        var e = S.lib[i];
        if (be && e.b.id !== be) continue;
        if (area && (!e.area || e.area.section !== area)) continue;
        if (type && e.d.type !== type && !(type === "iframe" && e.d.type === "html")) continue;
        if (!terms.every(function (t) { return e.hay.indexOf(t) >= 0; })) continue;
        total++;
        if (shown.length < 250) shown.push(e);
      }
      if (terms.length) {
        var q = terms.join(" ");
        shown.sort(function (a, b) { return (b.name.indexOf(q) >= 0) - (a.name.indexOf(q) >= 0) || a.name.length - b.name.length; });
      } else if (area) {
        shown.sort(function (a, b) { return a.area.text.localeCompare(b.area.text) || a.name.localeCompare(b.name); });
      }
      if (hl >= shown.length) hl = 0;
      list.textContent = "";
      shown.forEach(function (e, i) {
        var row = el("div", { class: "lib-item" + (i === hl ? " hl" : "") }, [
          el("span", { class: "nm" }, e.d.name),
          el("span", { class: "tg" }, [el("span", { class: "tag" }, e.d.type === "multi_file_viewer" ? "docs" : e.d.type), e.src ? el("span", { class: "tag" }, e.src) : null, el("span", { class: "tag be-" + e.b.id }, e.b.id === "x" ? "terminal" : e.b.id === "v4" ? "legacy" : "main")]),
          el("span", { class: "ds" }, [e.area ? el("b", { class: "area" }, e.area.text) : null, (e.area ? " · " : "") + (e.d.description || [e.d.category, e.d.subCategory].filter(Boolean).join(" › "))])
        ]);
        row.addEventListener("click", function () { m.close(); addWidget(e); });
        list.appendChild(row);
      });
      foot.textContent = total.toLocaleString() + " matching widgets" + (total > shown.length ? " (showing first " + shown.length + ", type to narrow)" : "") + " · Enter adds the highlighted one · Esc closes";
    }
    function moveHl(next) {
      if (list.children[hl]) list.children[hl].classList.remove("hl");
      hl = next;
      if (list.children[hl]) { list.children[hl].classList.add("hl"); list.children[hl].scrollIntoView({ block: "nearest" }); }
    }
    input.addEventListener("input", function () { hl = 0; draw(); list.scrollTop = 0; });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" && shown.length) { moveHl(Math.min(hl + 1, shown.length - 1)); e.preventDefault(); }
      else if (e.key === "ArrowUp" && shown.length) { moveHl(Math.max(hl - 1, 0)); e.preventDefault(); }
      else if (e.key === "Enter" && shown[hl]) { m.close(); addWidget(shown[hl]); }
    });
    draw();
    input.focus();
  }

  // ---------- "Go to": jump to a ticker, dashboard, tab or widget ----------
  function buildGoto() {
    var out = [], seen = {};
    function secName(d) { var s = S.sections.filter(function (x) { return x.id === d.section; })[0]; return s ? s.name : "My dashboards"; }
    userDashboards().concat(templates()).forEach(function (d) {
      var sn = secName(d);
      out.push({ kind: "Dashboard", label: d.name, where: sn, dash: d.id, hay: (d.name + " " + (d.description || "") + " " + sn).toLowerCase(), rank: 0 });
      if (d.section === "sources") return;
      d.tabs.forEach(function (t) {
        out.push({ kind: "Tab", label: d.name + " › " + t.name, where: sn, dash: d.id, tab: t.id, hay: (d.name + " " + t.name).toLowerCase(), rank: 1 });
        t.items.forEach(function (it) {
          var def = resolveDef(it.backend, it.widgetId);
          if (!def) return;
          var label = it.title || def.name;
          if (!it.title && def.titleParam && it.params && it.params[def.titleParam]) {
            var tp = def.params.filter(function (p) { return p.paramName === def.titleParam; })[0];
            var o = tp && (tp.options || []).filter(function (x) { return String(x.value) === String(it.params[def.titleParam]); })[0];
            if (o) label = (def.titlePrefix || "") + o.label.replace(/^[^·]+ · /, "");
            if (o && def.titleParam2 && it.params[def.titleParam2]) {
              var p2 = def.params.filter(function (p) { return p.paramName === def.titleParam2; })[0];
              var o2 = p2 && (p2.options || []).filter(function (x) { return String(x.value) === String(it.params[def.titleParam2]); })[0];
              if (o2) label += ": " + o2.label;
            }
          }
          var where = sn + " › " + d.name + " › " + t.name, k = label + "|" + where;
          if (seen[k]) return;
          seen[k] = 1;
          var rank = d.source === "user" ? 4 : t.id === STATUS[1] ? 3 : 2;
          if (it.choices && it.choices.length) {
            it.choices.forEach(function (c, ci) {
              var cd = resolveDef(c.b, c.w);
              out.push({ kind: "Data", label: c.l, where: where + " › " + (it.title || "Library"), dash: d.id, tab: t.id, uid: it.uid, choice: ci, rank: rank,
                hay: (c.l + " " + (c.g || "") + " " + where + " " + (it.title || "") + " " + (cd && cd.description || "")).toLowerCase() });
            });
            return;
          }
          out.push({ kind: "Data", label: label, where: where, dash: d.id, tab: t.id, uid: it.uid, rank: rank,
            hay: (label + " " + (def.subCategory || "") + " " + (Array.isArray(def.source) ? def.source.join(" ") : def.source || "") + " " + where + " " + (def.description || "")).toLowerCase() });
        });
      });
    });
    return out;
  }
  /**
   * "AAPL <GO>": a stock opens in Company, an ETF or fund in ETFs & Funds, an index, currency, future or coin in the
   * Markets Summary chart. Already on that dashboard: stay on the current tab if it follows the ticker.
   */
  function openSecurity(sym, type, name) {
    sym = String(sym || "").trim().toUpperCase();
    if (!sym) return;
    T.sec.remember(sym, name, type);
    var kind = T.sec.kind(sym, type);
    var target = kind === "market" ? ["d:markets", "chart", "Symbol"] : type === "mutualfund" ? ["d:etfs", "mutual-funds", "Fund"]
      : kind === "fund" ? ["d:etfs", "etf-profile", "Symbol"] : ["d:eq-company", "overview", "Symbol"];
    if (!S.cur || S.cur.id !== target[0]) {
      // set the ticker first so the cards load it straight away
      S.state.groups = S.state.groups || {};
      (S.state.groups[target[0]] = S.state.groups[target[0]] || {})[target[2]] = sym;
      saveNow();
      if (!openDashboard(target[0], target[1])) return;
    }
    var g = S.cur.groups.filter(function (x) { return x.name === target[2]; })[0] || S.cur.groups.filter(function (x) { return x.paramName === "symbol"; })[0];
    if (!g) return;
    var tab = curTab();
    if (target[1] && !(tab && tab.items.some(function (it) { return (it.groups || []).indexOf(g.name) >= 0; }))) openTab(target[1]);
    if (g.value !== sym) setGroup(g, sym);
  }
  T.openSecurity = openSecurity;
  T.openDashboard = openDashboard;
  function goTo(e) {
    if (e.jump) return jumpTo(e.jump);
    if (e.sec) return openSecurity(e.sec.symbol, e.sec.type, e.sec.name);
    if (e.ticker) return openSecurity(e.ticker);
    openDashboard(e.dash, e.tab);
    if (!e.uid) return;
    // The index lists the shipped layout. Built-in card ids are positional ("policy-expectations-3"), so in a tab the
    // owner edited before a newer layout the same id can be another card: match on what the card is, not its id.
    var tpl = S.generated.filter(function (x) { return x.id === e.dash; })[0];
    var tt = tpl && tpl.tabs.filter(function (x) { return x.id === e.tab; })[0];
    var src = tt && tt.items.filter(function (x) { return x.uid === e.uid; })[0];
    var card = T.findCardFor(S.cards.map(function (c) { return c.item; }), src || { uid: e.uid }, e.uid);
    card = card && S.cards.filter(function (c) { return c.item === card; })[0];
    if (!card) {
      var tab = curTab();
      if (!src || !tab || !S.cur || S.cur.id !== e.dash || S.tabId !== e.tab) return;
      // the owner's copy of this tab predates the card: say so and let them add it (looking alone changes nothing)
      confirmBox("“" + (e.label || src.title || "This card") + "” is hidden on your “" + tt.name + "” tab. Restore it to this tab?", "Restore").then(function (ok) {
        if (!ok) return;
        var item = JSON.parse(JSON.stringify(src));
        if (tab.items.some(function (x) { return x.uid === item.uid; })) {
          while (tab.items.some(function (x) { return x.uid === item.uid; })) item.uid += "n";
          item.x = 0; item.y = S.grid.bottom();
        }
        tab.items.push(item);
        var c = makeCard(item);
        S.cards.push(c);
        S.grid.add(item, c.node, c.head, c.resize);
        S.grid.layout(true);
        touch(); paintGroups(); paintEmpty();
        showCard(c);
      });
      return;
    }
    if (e.choice != null && card.item.choices) card = swapChoice(card.item, e.choice, true) || card;
    showCard(card);
  }
  function showCard(card) {
    card.node.scrollIntoView({ block: "center" });
    checkAll();
    card.node.classList.add("flash");
    setTimeout(function () { card.node.classList.remove("flash"); }, 1800);
  }
  /** The card in a tab that is the shipped card `src`: same id and same widget/preset, else the same widget/preset. */
  T.findCardFor = function (items, src, uid) {
    if (!src.widgetId) return items.filter(function (it) { return it.uid === uid; })[0] || null;
    var s = cardSig(src), same = items.filter(function (it) { return cardSig(it) === s; });
    var hit = same.filter(function (it) { return it.uid === uid; })[0] || same[0];
    if (hit) return hit;
    // a library card the owner switched to another choice is still that library card
    return src.choices ? items.filter(function (it) { return it.uid === uid && it.choices; })[0] || null : null;
  };
  /**
   * One box for everything: securities by ticker or company name (looked up as you type, worldwide listings),
   * recent securities, and every dashboard, tab and dataset by name.
   */
  function initGoto() {
    var input = $("goto"), index = null, shown = [], hl = 0, box = null, secRows = [], secFor = null, seq = 0, wantPick = false;
    function close() { if (box) T.closePop(); box = null; }
    function secEntry(r, kind) {
      return { kind: kind || r.type_label || "Security", label: r.symbol, name: r.name || "", where: [r.exchange, "→ " + T.sec.where(r.symbol, r.type)].filter(Boolean).join("  "), sec: r };
    }
    function draw() {
      var q = input.value.trim(), ql = q.toLowerCase(), foot;
      if (!q) {
        var rec = T.sec.recent();
        if (!rec.length) return close();
        shown = rec.map(function (r) { return secEntry({ symbol: r.s, name: r.n, type: r.t }, "Recent"); });
        foot = "Recent · type a ticker, a company name, or the name of a dashboard or dataset";
      } else {
        if (!index) index = buildGoto();
        var terms = ql.split(/\s+/);
        var hits = index.filter(function (e) { return terms.every(function (t) { return e.hay.indexOf(t) >= 0; }); });
        var nq = ql.replace(/[-_]+/g, " ");                        // "high yield" finds "High-yield and CCC spreads"
        var at = function (e) { return e.label.toLowerCase().replace(/[-_]+/g, " ").indexOf(nq); };
        hits.sort(function (a, b) {
          var as = at(a), bs = at(b);
          return (as < 0) - (bs < 0) || a.rank - b.rank || (as > 0) - (bs > 0) || a.label.length - b.label.length;
        });
        var secs = secFor === q ? secRows.slice(0, 7).map(function (r) { return secEntry(r); }) : [];
        // FRED:<series id> opens the series on the Markets Summary chart; a check-digit-valid CUSIP opens the bond lookup
        var direct = /^FRED:[A-Za-z0-9_.]+$/i.test(q) ? [{ kind: "FRED", label: q.toUpperCase(), name: "FRED series", where: "→ Markets Summary › Chart", ticker: q.toUpperCase() }]
          : cusipOk(q) ? [{ kind: "CUSIP", label: q.toUpperCase(), name: "bond (FINRA TRACE)", where: "→ Credit › Bond Lookup",
            jump: { label: "Bond", dash: "d:credit", tab: "bond-lookup-trace", group: "Issuer", value: q.toUpperCase(), groups: { Bond: q.toUpperCase() } } }] : [];
        if (secFor === q && !secs.length && /^[A-Za-z0-9.^=\-]{1,12}$/.test(q)) secs = [{ kind: "Ticker", label: q.toUpperCase(), name: "open as typed", where: "→ " + T.sec.where(q), ticker: q.toUpperCase() }];
        // data first when the words clearly name a dashboard or dataset (cpi, sofr, options), securities first otherwise
        // and when no security matched (only the "open as typed" guess: "wasde") or a phrase is in a dataset's name ("high yield")
        var fallbackOnly = secs.length === 1 && !!secs[0].ticker;
        var dataFirst = hits.some(function (e) { var i = at(e); return i === 0 || (i > 0 && /\s/.test(nq)); })
          || (fallbackOnly && hits.length > 0);
        var local = hits.slice(0, secs.length ? 9 : 14);
        // an equity CUSIP (037833100) is found by the security search too: the listing first, the bond lookup after it
        var equityCusip = direct.length && direct[0].kind === "CUSIP" && secs.length && !fallbackOnly;
        shown = equityCusip ? secs.concat(direct, local) : direct.concat(dataFirst ? local.concat(secs) : secs.concat(local));
        foot = (secFor === q ? "" : "Looking up securities… · ") + hits.length + " dashboards and datasets · ↑↓ move · Enter opens";
      }
      if (hl >= shown.length) hl = 0;
      var list = el("div", { class: "pop-list goto-list" });
      shown.forEach(function (e, i) {
        var row = el("div", { class: "opt" + (i === hl ? " hl" : "") }, [el("span", { class: "tag" + (e.sec || e.ticker ? " sec" : "") }, e.kind),
          el("span", { class: "gl" + (e.sec || e.ticker ? " mono" : "") }, e.label), e.name != null ? el("span", { class: "gn" }, e.name) : null, el("span", { class: "gw" }, e.where)]);
        row.addEventListener("mousedown", function (ev) { ev.preventDefault(); pick(e); });
        list.appendChild(row);
      });
      if (!shown.length) list.appendChild(el("div", { class: "pop-foot" }, secFor === q ? "Nothing matches. Try fewer words." : "Searching…"));
      var nb = el("div", null, [list, el("div", { class: "pop-foot" }, foot)]);
      T.popover(input, nb, { width: 680, onClose: function () { if (box === nb) box = null; } });
      box = nb;
    }
    function lookup() {
      var q = input.value.trim(), my = ++seq;
      if (!q) return;
      T.sec.search(q).then(function (rows) {
        if (my !== seq || input.value.trim() !== q) return;
        secRows = rows; secFor = q;
        if (wantPick) { wantPick = false; draw(); if (shown[0]) pick(shown[0]); return; }
        if (document.activeElement === input) draw();
      });
    }
    var lookupLater = T.debounce(lookup, 140);
    function pick(e) { close(); input.value = ""; input.blur(); wantPick = false; goTo(e); }
    function moveHl(next) {
      var listEl = box && box.querySelector(".pop-list");
      if (!listEl) { hl = next; return draw(); }
      if (listEl.children[hl]) listEl.children[hl].classList.remove("hl");
      hl = next;
      if (listEl.children[hl]) {
        listEl.children[hl].classList.add("hl");
        if (listEl.children[hl].scrollIntoView) listEl.children[hl].scrollIntoView({ block: "nearest" });
      }
    }
    input.addEventListener("input", function () { hl = 0; wantPick = false; draw(); lookupLater(); });
    input.addEventListener("focus", function () { index = null; draw(); });
    input.addEventListener("blur", function () { setTimeout(function () { if (document.activeElement !== input) { wantPick = false; close(); } }, 120); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" && shown.length) { moveHl(Math.min(hl + 1, shown.length - 1)); e.preventDefault(); }
      else if (e.key === "ArrowUp" && shown.length) { moveHl(Math.max(hl - 1, 0)); e.preventDefault(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        var q = input.value.trim();
        if (!q || secFor === q || (shown[hl] && (shown[hl].jump || /^FRED:/i.test(shown[hl].label)))) { if (shown[hl]) pick(shown[hl]); }
        else { wantPick = hl === 0; if (!wantPick && shown[hl]) pick(shown[hl]); else lookup(); }   // wait for the lookup, then take the best match
      }
      else if (e.key === "Escape") { input.value = ""; close(); input.blur(); }
    });
    document.addEventListener("keydown", function (e) { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "g") { e.preventDefault(); input.focus(); input.select(); } });
  }

  // ---------- boot ----------
  /** Dashboards from the first version of this terminal are superseded; keep any the user customised as their own. */
  function migrate() {
    ["Company Research Terminal", "Global Macro, Rates & Credit"].forEach(function (n) {
      var k = "app:v5:" + n, d = S.state.dashboards[k];
      if (!d) return;
      delete S.state.dashboards[k];
      d.id = "user:old-" + n.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      d.source = "user"; d.name = n + " (old layout)"; delete d.section;
      S.state.dashboards[d.id] = d;
      if (S.state.last && S.state.last.id === k) S.state.last = null;
    });
  }
  /** Short in-app guide (top bar "?", or the ? key). */
  function openHelp() {
    var el = T.el;
    function sec(title, rows) {
      return el("div", { class: "help-sec" }, [el("h3", null, title)].concat(rows.map(function (r) {
        return el("div", { class: "help-row" }, [el("span", { class: "help-k" }, r[0]), el("span", null, r[1])]);
      })));
    }
    var box = el("div", { class: "modal help-box" }, [
      el("div", { class: "help-head" }, [el("b", null, (T.product || "Libre Terminal") + (T.version ? " v" + T.version : "") + ": quick guide"), el("button", { class: "icon-btn", type: "button", title: "Close (Esc)", onclick: function () { m.close(); } }, "✕")]),
      el("div", { class: "help-body" }, [
        sec("Keyboard", [["/ or Ctrl+G", "Go to a ticker, company name, dashboard, tab or dataset"], ["Ctrl+K", "Add a widget from the library"],
          ["[  ]", "Previous / next tab"], ["Esc", "Close a maximized card, menu or dialog"], ["?", "This guide"]]),
        sec("Find things", [["Search", "Type a ticker (AAPL, 7203.T, SHEL.L) or company name (toyota) for Company; an SEC CIK (0000320193) or an equity CUSIP (037833100) for its listing; a topic (cpi, sofr, options) for that card; a bond CUSIP (037833CJ7) for Bond Lookup; or FRED:<series> (FRED:DGS10) for the Compare chart."],
          ["Pickers", "Symbol, Country and Bank boxes at the top drive linked cards. Clicking a ticker, country, CUSIP, CRD, RSSD or bill id in a table fills them too."]]),
        sec("Work with cards", [["Source: X ▾", "Click the header source to switch providers for the same data or open the source's page."],
          ["↻ Refresh", "Card ↻ bypasses server and browser cache for that card; top-bar ↻ reloads all cards on the current tab; Restart (next to Refresh) restarts the data services if charts or data stop responding."],
          ["Dataset", "Library cards (EIA, BLS, USDA, regional Feds, FFIEC) hold a family of datasets and remember your pick."],
          ["Columns", "Lists open with key columns. ⋯ › Columns shows or hides the rest; Default columns resets."],
          ["⤓ Export", "Copy a table for Excel, download CSV, or save a chart as PNG (exports include source and date)."],
          ["⋯ menu", "Plot a table, flip rows and columns (compact tables up to 60 rows), show settings, or remove the card. Drag header to move, double-click header (or ⤢) to maximize, drag corner to resize."]]),
        sec("Desks & comparisons", [["Watchlist & Comps", "Markets Summary › Overview watchlists (click a name to chart); Company › Overview peer comps with peer median."],
          ["Valuation & Risk", "Company › Growth & Ratios (Valuation bands over 3/5/10Y across P/E, EV/EBITDA, EV/Sales, P/S, P/B, plus DuPont ROE & ROIC) and Company › Price & Peers (risk vs peers)."],
          ["Compare chart", "Markets Summary › Chart and Company › Price & Peers: overlay tickers or FRED: series as % change, ratio or spread, with moving averages."],
          ["Credit & Rates", "Credit › Bond Lookup (TRACE by issuer, ticker or CUSIP), Credit › Spreads (CFTC index CDS & OAS), Rates & Curves › Policy Expectations (OIS-implied policy paths)."],
          ["News & Banking", "News › Markets situation room (newest-first monitor & official releases); Money & Banking (credit quality, FDIC failures, FFIEC Bank Lookup); Indices › Valuation (S&P 500 multiples)."]]),
        sec("API keys & services", [["Keyless & free keys", "Runs keyless by default on the main V5 data service. Optional free FRED, BLS, EIA and Congress.gov keys unlock extra cards (Setup-Keys.cmd or local/api_keys.env, then restart)."],
          ["Data status", "Data Explorers › Data status lists configured keys, card counts, sign-up links, runtime fixes, and any source down upstream."]]),
        sec("Python & AI assistants", [["Python", "The same data from scripts: run .venv-v5\\Scripts\\python.exe, then from openbb import obb; obb.equity.price.historical(\"AAPL\", provider=\"yfinance\").to_df()."],
          ["MCP", "Assistants that support MCP can query the same data service through openbb-mcp: copy config/mcp.example.json into the assistant's MCP settings and set the folder path in it."]])
      ])
    ]);
    var m = T.modal(box);
  }

  async function restartServices() {
    if (S.restarting) return;
    S.restarting = true;
    var btn = $("btn-restart");
    if (btn) { btn.classList.add("busy"); btn.disabled = true; btn.textContent = "Restarting…"; }
    function restoreBtn() {
      S.restarting = false;
      if (btn) { btn.classList.remove("busy"); btn.disabled = false; btn.textContent = "Restart"; }
    }
    if (T.clearAllCaches) T.clearAllCaches();
    T.toast("Restarting data services…");
    var active = S.backends.filter(function (b) { return b.id !== "x" && b.url; });
    active.forEach(function (b) { stopWatchBackend(b); b.up = false; b.service = "restarting"; });
    paintStatus();
    try {
      var res = await T.request("/api/services/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: "backends" })
      });
      var j = await res.json().catch(function () { return {}; }) || {};
      if (!res.ok) throw T.httpError(res.status, JSON.stringify(j));
      var seq = j.restart_seq || 0;
      var deadline = Date.now() + 90000;
      var started = Date.now();
      var lastInfo = null;
      var completed = false;
      while (Date.now() < deadline) {
        await new Promise(function (r) { setTimeout(r, 1500); });
        var sr = await fetchT("/api/services", 4000).catch(function () { return null; });
        var sj = sr && sr.ok ? await sr.json().catch(function () { return null; }) : null;
        if (!sj) continue;
        lastInfo = sj;
        var done = sj.restart && !sj.restart.restarting && (!seq || sj.restart.seq >= seq);
        var fallbackReady = !sj.restart && (Date.now() - started >= 3000) && sj.v5 && sj.v5.reachable;
        if (done || fallbackReady) { completed = true; break; }
      }
      if (lastInfo && lastInfo.restart && lastInfo.restart.last_ok === false) {
        restoreBtn();
        T.toast("Restart failed: " + (lastInfo.restart.error || "check local/logs"), true);
        active.forEach(function (b) { b.service = "stopped"; watchBackend(b); });
        paintStatus();
        return;
      }
      var failedBackends = [];
      var loadedFresh = {};
      await Promise.all(active.map(async function (b) {
        var ok = await fetchT(b.url + "/", 4000).then(function (r) { return r.ok || r.status < 500; }, function () { return false; });
        if (!ok) {
          b.up = false;
          b.service = "offline";
          failedBackends.push(b);
          return;
        }
        b.service = "running";
        var hadDefs = !!(S.widgets[b.id] && Object.keys(S.widgets[b.id]).length);
        if (!hadDefs) {
          await loadBackend(b);
          if (b.up) { loadedFresh[b.id] = true; }
          else { b.service = "offline"; failedBackends.push(b); }
        } else {
          b.up = true;
        }
      }));
      restoreBtn();
      failedBackends.forEach(watchBackend);
      paintStatus();
      if (failedBackends.length) {
        T.toast(completed
          ? "Some data services are still offline — waiting for them to come back"
          : "Restart timed out — waiting for data services to come back", true);
        if (failedBackends.length < active.length) {
          S.cards.slice().forEach(function (c) {
            if (!c.missing && !loadedFresh[c.backend]) c.refresh(true);
          });
        }
        return;
      }
      if (!completed) {
        T.toast("Restart status check timed out — data services are responding, reloading widgets", true);
      } else {
        T.toast("Data services restarted — reloading widgets");
      }
      S.cards.slice().forEach(function (c) {
        if (!c.missing && !loadedFresh[c.backend]) c.refresh(true);
      });
    } catch (e) {
      restoreBtn();
      T.toast("Restart failed: " + T.userText(e, { source: "The terminal" }) + " Details are in local/logs.", true);
      active.forEach(function (b) { b.up = false; b.service = "offline"; watchBackend(b); });
      paintStatus();
    }
  }
  T.restartServices = restartServices;

  async function boot() {
    S.grid = T.grid($("canvas"), $("canvas-wrap"), { onChange: function () { touch(); checkAll(); } });
    var lazy = T.debounce(checkAll, 80);
    $("canvas-wrap").addEventListener("scroll", lazy, { passive: true });
    window.addEventListener("resize", lazy);
    $("btn-add").addEventListener("click", function () { openLibrary(); });
    $("btn-refresh").addEventListener("click", function () { S.cards.forEach(function (c) { c.refresh(true); }); T.toast("Reloading " + S.cards.length + " widgets"); });
    var rstBtn = $("btn-restart");
    if (rstBtn) rstBtn.addEventListener("click", restartServices);
    $("btn-more").addEventListener("click", moreMenu);
    initGoto();
    T.lists.changed = function () { S.cards.forEach(function (c) { if (c.def && c.def.symbolList) { c.redrawParams(); c.refresh(false); } }); };
    $("dash-filter").addEventListener("input", paintSidebar);
    $("dash-title").addEventListener("dblclick", renameDashboard);
    $("btn-collapse").addEventListener("click", function () { $("sidebar").classList.add("collapsed"); $("btn-expand").classList.remove("hidden"); });
    $("btn-expand").addEventListener("click", function () { $("sidebar").classList.remove("collapsed"); $("btn-expand").classList.add("hidden"); });
    $("btn-help").addEventListener("click", openHelp);
    document.addEventListener("keydown", function (e) {
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || "") || (e.target && e.target.isContentEditable);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (!document.querySelector(".modal-bg")) openLibrary(); }
      else if (e.key === "Escape") { var mx = document.querySelector(".w.max"); if (mx && !document.querySelector(".modal-bg")) { mx.classList.remove("max"); S.grid.layout(false); } }
      else if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey && !document.querySelector(".modal-bg")) {
        if (e.key === "?") { e.preventDefault(); openHelp(); }
        else if (e.key === "/") { e.preventDefault(); var g = $("goto"); if (g) { g.focus(); g.select(); } }
        else if ((e.key === "[" || e.key === "]") && S.cur && S.cur.tabs.length > 1) {      // previous / next tab
          var ts = S.cur.tabs, i = ts.map(function (t) { return t.id; }).indexOf(S.tabId);
          openTab(ts[(i + (e.key === "]" ? 1 : -1) + ts.length) % ts.length].id);
        }
      }
    });
    // narrow windows: the sidebar folds away below 1200 px (the » button brings it back); the grid reflows (grid.js)
    var wasNarrow = null;
    function fitSidebar() {
      var narrow = window.innerWidth < 1200;
      if (narrow === wasNarrow) return;
      wasNarrow = narrow;
      $("sidebar").classList.toggle("collapsed", narrow);
      $("btn-expand").classList.toggle("hidden", !narrow);
    }
    fitSidebar();
    window.addEventListener("resize", T.debounce(fitSidebar, 150));
    try {
      var resps = await Promise.all([T.request("/api/config"), T.request("/api/state"), T.request("/api/dashboards", { timeout: 60000 })]);
      var cfgr = resps[0], sr = resps[1], gr = resps[2];
      var parsed = await Promise.all([
        cfgr.json(),
        sr.json().catch(function () { return {}; }),
        gr.json().catch(function () { return {}; })
      ]);
      var cfg = parsed[0], st = parsed[1], gen = parsed[2];
      T.version = cfg.version || "";
      T.setBudgets(cfg.budgets);
      if (cfg.product) T.product = cfg.product;
      paintProduct();
      S.backends = cfg.backends;
      S.backends.forEach(function (b) { S.byId[b.id] = b; });
      window.addEventListener("message", function (event) {
        var d = event && event.data;
        if (!d || d.type !== "openbb:widget-params:update" || !d.params || !d.params.symbol) return;
        var okOrigin = event.origin === window.location.origin || S.backends.some(function (b) {
          if (!b.url) return false;
          try { return new URL(b.url).origin === event.origin; } catch (e) { return false; }
        });
        if (!okOrigin || !S.cur || !Array.isArray(S.cur.groups)) return;
        var sym = String(d.params.symbol).trim().toUpperCase();
        var g = S.cur.groups.filter(function (x) { return x.paramName === "symbol"; })[0];
        if (sym && g && g.value !== sym) setGroup(g, sym, event.source);
      });
      if (!sr.ok) stateUnusable(st && st.detail);                                  // 503: unreadable, nothing restored
      else if (st && st.dashboards) S.state = Object.assign({ v: 4, dashboards: {}, last: null }, st);
      var restored = sr.headers.get("X-State-Notice");
      if (restored) { try { notice(decodeURIComponent(restored)); } catch (e) { /* malformed header */ } }
      S.state.ui = S.state.ui || {}; S.state.ui.collapsed = S.state.ui.collapsed || {};
      if (!gr.ok) notice((gen && gen.detail) || "The built-in dashboards could not be loaded.");
      S.generated = gen.dashboards || [];
      S.concepts = gen.concepts || {};
      S.columns = gen.columns || {};
      T.colSpec = function (wid) { return S.columns[wid] || null; };
      S.titles = gen.titles || {};
      T.titleOf = function (wid) { return S.titles[wid] || null; };     // plain-language card names (build_dashboards TITLES)
      S.sections = (gen.sections || []).concat([{ id: "sources", name: "By data source" }]);
      migrate();
    } catch (e) {
      $("banner").classList.remove("hidden");
      $("banner").textContent = "The terminal UI server is not reachable. Restart it with Start.cmd (or start.sh).";
      return;
    }
    function safely(fn) {
      try { fn(); return true; } catch (e) {
        // A saved layout this page cannot use must not leave a blank page: show the defaults, and do not save over the file.
        if (S.stateBroken) throw e;
        if (window.console) console.error(e);
        S.state = { v: 4, dashboards: {}, last: null, ui: { collapsed: {} }, groups: {}, libsel: {} };
        S.cur = null;
        stateUnusable("Your saved layout could not be used by this page (local/state/state.json is untouched; daily copies: state.YYYYMMDD.bak.json in the same folder).");
        fn();
        return false;
      }
    }
    safely(function () {
      var m = migrateViewPrefs();
      var m2 = migrateOverrides();
      T.lastMigration = { viewPrefs: m, overrides: m2 };
      if (m.released.length || m2.migrated.length || m2.released.length) saveNow();
    });
    // The page is drawn at once from the dashboards; the terminal's own views (this server) come first, and each data
    // backend fills its cards as soon as it answers (status per backend in the sidebar). Remote backends start loading
    // concurrently and are given a bounded grace window so initial views/charts do not flash "starting" before opening.
    safely(function () { paintStatus(); paintSidebar(); paintEmpty(); });
    var local = S.backends.filter(function (b) { return !b.url; });
    var remote = S.backends.filter(function (b) { return b.url; });
    var remoteP = Promise.all(remote.map(loadBackend));
    await Promise.all(local.map(loadBackend));
    if (remote.length) {
      await Promise.race([
        remoteP,
        new Promise(function (r) { setTimeout(r, 4000); })
      ]);
    }
    S.booted = true;
    safely(function () { paintSidebar(); openDefault(); pollWarmStatus(); });
  }
  boot();
})(window.T);
