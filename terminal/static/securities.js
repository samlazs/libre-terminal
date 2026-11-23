/* Securities: lookup by ticker or company name, recent securities, watchlists and peer lists (saved in state). */
(function (T) {
  "use strict";
  var el = T.el;
  var found = {};          // query -> Promise<rows>
  var names = {};          // symbol -> name, from searches and boards (labels only)
  function st() { return T.S.state; }
  function save() { if (T.saveState) T.saveState(); }

  // ---------- lookup ----------
  T.sec = {};
  /** Securities matching a ticker or a name (Yahoo Finance: worldwide stocks, ETFs, indices, FX, futures, crypto). */
  T.sec.search = function (q) {
    q = String(q || "").trim();
    if (!q) return Promise.resolve([]);
    var k = q.toLowerCase();
    if (!found[k]) {
      found[k] = fetch("/x/search?query=" + encodeURIComponent(q))
        .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
        .then(function (j) {
          var rows = (j && j.results) || [], up = q.toUpperCase();
          rows.forEach(function (r) { if (r.name) names[r.symbol] = r.name; });
          // an exact ticker goes first
          return rows.filter(function (r) { return r.symbol === up; }).concat(rows.filter(function (r) { return r.symbol !== up; }));
        })
        .catch(function () { delete found[k]; return []; });
    }
    return found[k];
  };
  T.sec.nameOf = function (sym) { return names[sym] || ""; };
  T.sec.learn = function (sym, name) { if (sym && name && name !== sym) names[sym] = name; };
  /** Where a symbol opens: "company" (a stock), "fund" (ETF, mutual fund) or "market" (index, FX, future, crypto, FRED series). */
  T.sec.kind = function (sym, type) {
    if (type) return type === "equity" ? "company" : type === "etf" || type === "mutualfund" ? "fund" : "market";
    return /^\^|=X$|=F$|-(USD|EUR|USDT|BTC)$|^FRED:/i.test(String(sym)) ? "market" : "company";
  };
  T.sec.where = function (sym, type) { var k = T.sec.kind(sym, type); return k === "company" ? "Company" : k === "fund" ? "ETFs & Funds" : "Markets chart"; };
  T.sec.recent = function () { return (st().recent || []).slice(); };
  T.sec.remember = function (sym, name, type) {
    var list = st().recent || [], old = list.filter(function (r) { return r.s === sym; })[0] || {};
    list = list.filter(function (r) { return r.s !== sym; });
    list.unshift({ s: sym, n: name || old.n || names[sym] || "", t: type || old.t || "" });
    st().recent = list.slice(0, 12);
    save();
  };

  /**
   * Search-as-you-type on a text input. opts: { onPick(row), onRaw(text) (Enter with nothing found), recents: bool, width }.
   * Enter picks the highlighted match; if the search has not answered yet it waits for it.
   */
  T.sec.suggest = function (input, opts) {
    var shown = [], shownFor = null, hl = 0, seq = 0, open = false, wantPick = false;
    function close() { if (open) { T.closePop(); open = false; } }
    function rowsFor(q) {
      if (q) return null;
      return opts.recents ? T.sec.recent().map(function (r) { return { symbol: r.s, name: r.n, type: r.t, type_label: "Recent", exchange: "" }; }) : [];
    }
    function draw(rows) {
      shown = rows;
      if (hl >= rows.length) hl = 0;
      if (!rows.length) return close();
      var list = el("div", { class: "pop-list goto-list" });
      rows.forEach(function (r, i) {
        var row = el("div", { class: "opt" + (i === hl ? " hl" : "") }, [el("span", { class: "tag" }, r.type_label || ""), el("span", { class: "gl mono" }, r.symbol), el("span", { class: "gn" }, r.name || ""), el("span", { class: "gw" }, r.exchange || "")]);
        row.addEventListener("mousedown", function (e) { e.preventDefault(); pick(r); });
        list.appendChild(row);
      });
      T.popover(input, el("div", null, [list, el("div", { class: "pop-foot" }, "↑↓ move · Enter picks · type a ticker or a company name (toyota, rheinmetall, shell)")]), { width: opts.width || 560, onClose: function () { open = false; } });
      open = true;
    }
    function pick(r) { close(); wantPick = false; opts.onPick(r); }
    function run() {
      var q = input.value.trim(), my = ++seq;
      var direct = rowsFor(q);
      if (direct) { shownFor = q; hl = 0; return draw(direct); }
      T.sec.search(q).then(function (rows) {
        if (my !== seq) return;
        shownFor = q; hl = 0;
        if (wantPick) { wantPick = false; if (rows[0]) pick(rows[0]); else { close(); if (opts.onRaw) opts.onRaw(q); } return; }
        if (document.activeElement === input) draw(rows);
      });
    }
    var later = T.debounce(run, 150);
    input.addEventListener("input", function () { wantPick = false; later(); });
    input.addEventListener("focus", function () { if (!input.value.trim() && opts.recents) run(); });
    input.addEventListener("blur", function () { setTimeout(function () { if (document.activeElement !== input) close(); }, 120); });
    input.addEventListener("keydown", function (e) {
      if ((e.key === "ArrowDown" || e.key === "ArrowUp") && open && shown.length) {
        hl = e.key === "ArrowDown" ? Math.min(hl + 1, shown.length - 1) : Math.max(hl - 1, 0);
        draw(shown); e.preventDefault(); e.stopImmediatePropagation();
      } else if (e.key === "Enter") {
        var q = input.value.trim();
        if (!q) return;
        e.preventDefault(); e.stopImmediatePropagation();
        if (open && shownFor === q && shown[hl]) pick(shown[hl]);
        else { wantPick = true; run(); }
      }
    });
    return { close: close };
  };

  // ---------- watchlists and peer lists ----------
  // Starter list for a fresh install (same symbols as STARTER_WATCHLIST in terminal/composite/markets/boards.py).
  var DEFAULT_LISTS = [{ id: "main", name: "My watchlist", symbols: ["AAPL", "MSFT", "NVDA", "ASML", "7203.T", "SHEL.L", "RHM.DE", "^GSPC", "EURUSD=X", "GC=F", "BTC-USD"] }];
  T.lists = {};
  T.lists.all = function () { var w = st().watchlists; return w && w.length ? w : DEFAULT_LISTS; };
  function own() {           // the first edit turns the starter list into the user's own saved list
    if (!st().watchlists || !st().watchlists.length) st().watchlists = JSON.parse(JSON.stringify(DEFAULT_LISTS));
    return st().watchlists;
  }
  T.lists.get = function (id) { var all = T.lists.all(); return all.filter(function (l) { return l.id === id; })[0] || all[0]; };
  T.lists.options = function () { return T.lists.all().map(function (l) { return { label: l.name + " (" + l.symbols.length + ")", value: l.id }; }); };
  // Which list a card shows is remembered per card without turning the tab into a private copy (like library picks).
  function selKey(item) { return (T.S.cur ? T.S.cur.id : "") + ":" + item.uid; }
  T.lists.current = function (item) { return T.lists.get((st().wlsel || {})[selKey(item)] || (item.params || {}).list); };
  T.lists.select = function (item, id) { st().wlsel = st().wlsel || {}; st().wlsel[selKey(item)] = id; save(); };
  /** Put the list's symbols into a card's request (the server keeps no lists; the URL changes when the list does). */
  T.lists.inject = function (def, item, out) {
    if (def.symbolList === "watchlist") {
      var l = T.lists.current(item);
      out.list = l.id;
      out.symbols = l.symbols.join(",");
    } else if (def.symbolList === "peers") {
      var p = (st().peers || {})[String(out.symbol || "").toUpperCase()];
      out.peers = p && p.length ? p.join(",") : null;
    }
  };
  T.lists.changed = function () {};      // set by the app: refresh the cards that show lists

  function parseSymbols(text) { return String(text || "").split(/[\s,;]+/).map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean); }

  /** Shared editor body: an add box with name search, and the list with remove / move buttons. */
  function listEditor(get, set, hint) {
    var input = el("input", { type: "text", placeholder: "Add: type a ticker or a company name, Enter (several: AAPL, MSFT, 7203.T)", spellcheck: "false", autocomplete: "off", class: "le-add" });
    var rows = el("div", { class: "le-rows" });
    function add(list) {
      var cur = get().slice(), n = 0;
      list.forEach(function (s) { if (s && cur.indexOf(s) < 0) { cur.push(s); n++; } });
      if (n) set(cur);
      input.value = "";
      paint();
      input.focus();
    }
    T.sec.suggest(input, {
      width: 520,
      onPick: function (r) { T.sec.learn(r.symbol, r.name); add([r.symbol]); },
      onRaw: function (q) { add(parseSymbols(q)); }
    });
    input.addEventListener("keydown", function (e) {     // a pasted list of tickers ("AAPL, MSFT" or "AAPL MSFT 7203.T") is added as is
      var v = input.value.trim();
      if (e.key === "Enter" && (/[,;]/.test(v) || (/\s/.test(v) && v === v.toUpperCase()))) { e.preventDefault(); e.stopImmediatePropagation(); T.closePop(); add(parseSymbols(v)); }
    }, true);
    function move(i, d) { var cur = get().slice(), j = i + d; if (j < 0 || j >= cur.length) return; var t = cur[i]; cur[i] = cur[j]; cur[j] = t; set(cur); paint(); }
    function paint() {
      rows.textContent = "";
      var cur = get();
      if (!cur.length) rows.appendChild(el("div", { class: "muted", style: { padding: "14px 4px" } }, hint || "Empty. Add symbols above."));
      cur.forEach(function (s, i) {
        rows.appendChild(el("div", { class: "le-row" }, [
          el("span", { class: "mono le-sym" }, s), el("span", { class: "le-nm" }, T.sec.nameOf(s)),
          el("button", { class: "icon-btn", type: "button", title: "Move up", onclick: function () { move(i, -1); } }, "↑"),
          el("button", { class: "icon-btn", type: "button", title: "Move down", onclick: function () { move(i, 1); } }, "↓"),
          el("button", { class: "icon-btn", type: "button", title: "Remove " + s, onclick: function () { set(get().filter(function (x) { return x !== s; })); paint(); } }, "✕")
        ]));
      });
    }
    paint();
    return { input: input, rows: rows, paint: paint };
  }
  function dialog(title, parts, foot, onClose) {
    var box = el("div", { class: "modal le", style: { width: "min(560px, 94vw)" } }, [el("div", { class: "modal-head" }, [title].concat(parts.head)), parts.body, el("div", { class: "le-foot" }, foot)]);
    return T.modal(box, onClose);
  }
  function ask(label, value, ok) {
    if (T.ask) return T.ask(label, value, ok);
    var v = window.prompt(label, value || "");
    return Promise.resolve(v == null ? null : v.trim() || null);
  }
  function confirmBox(text, ok) { return T.confirm ? T.confirm(text, ok) : Promise.resolve(window.confirm(text)); }

  /** Watchlist editor: pick, create, rename, delete lists; add and remove symbols. onDone(listId) when closed. */
  T.lists.editWatchlist = function (listId, onDone) {
    var cur = T.lists.get(listId).id, m;
    var pickBox = el("div", { class: "le-bar" });
    function list() { return T.lists.get(cur); }
    function commit() { save(); T.lists.changed(); }
    var ed = listEditor(function () { return list().symbols; }, function (syms) { own(); var l = own().filter(function (x) { return x.id === cur; })[0] || own()[0]; l.symbols = syms; commit(); paintBar(); });
    function paintBar() {
      pickBox.textContent = "";
      var dd = T.dropdown({ value: cur, options: T.lists.options(), width: 300, onChange: function (v) { if (v) { cur = v; paintBar(); ed.paint(); } } });
      dd.classList.add("le-dd");
      pickBox.appendChild(el("label", { class: "muted small" }, "List"));
      pickBox.appendChild(dd);
      // a new list is named in place, in this window (no second dialog on top of it)
      pickBox.appendChild(el("button", { class: "btn", type: "button", onclick: function () {
        pickBox.textContent = "";
        var nameIn = el("input", { type: "text", class: "le-new", "data-own-esc": "1", value: "Watchlist " + (T.lists.all().length + 1), "aria-label": "Name of the new watchlist" });
        function create() {
          var n = nameIn.value.trim();
          if (!n) return nameIn.focus();
          var id = "l" + Date.now().toString(36);
          own().push({ id: id, name: n, symbols: [] });
          cur = id; commit(); paintBar(); ed.paint(); ed.input.focus();
        }
        nameIn.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); create(); }
          else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); paintBar(); }      // cancels the name, not the window
        });
        pickBox.appendChild(el("label", { class: "muted small" }, "New list"));
        pickBox.appendChild(nameIn);
        pickBox.appendChild(el("button", { class: "btn primary", type: "button", onclick: create }, "Create"));
        pickBox.appendChild(el("button", { class: "btn", type: "button", onclick: function () { paintBar(); } }, "Cancel"));
        nameIn.focus(); nameIn.select();
      } }, "New list"));
      pickBox.appendChild(el("button", { class: "btn", type: "button", onclick: function () {
        ask("Rename watchlist", list().name, "Rename").then(function (n) {
          if (!n) return;
          own(); own().filter(function (x) { return x.id === cur; })[0].name = n; commit(); paintBar();
        });
      } }, "Rename"));
      if (T.lists.all().length > 1) pickBox.appendChild(el("button", { class: "btn danger", type: "button", onclick: function () {
        confirmBox('Delete the watchlist "' + list().name + '" and its ' + list().symbols.length + " symbols?").then(function (ok) {
          if (!ok) return;
          own(); st().watchlists = own().filter(function (x) { return x.id !== cur; });
          cur = st().watchlists[0].id; commit(); paintBar(); ed.paint();
        });
      } }, "Delete"));
    }
    paintBar();
    m = dialog(el("b", null, "Watchlists"), { head: [pickBox, ed.input], body: ed.rows },
      [el("span", { class: "muted small" }, "Saved automatically. Click a name on the board to chart it, a ticker to open it."), el("button", { class: "btn primary", type: "button", onclick: function () { m.close(); } }, "Done")],
      function () { if (onDone) onDone(cur); });
    ed.input.focus();
  };

  /** Peer-list editor for one company. auto: the peers found automatically (shown until the user changes them). */
  T.lists.editPeers = function (sym, auto, onDone) {
    sym = String(sym || "").toUpperCase();
    var m, mine = function () { return (st().peers || {})[sym]; };
    var note = el("div", { class: "muted small" });
    function paintNote() { note.textContent = mine() ? "Your own peer list for " + sym + "." : "Found automatically: same industry, similar size, stocks investors also watch. Any change makes it your list for " + sym + "."; }
    var ed = listEditor(function () { return mine() || auto || []; }, function (syms) {
      st().peers = st().peers || {};
      st().peers[sym] = syms.filter(function (s) { return s !== sym; });
      save(); paintNote(); T.lists.changed();
    }, "No peers. Add some above.");
    paintNote();
    m = dialog(el("b", null, "Peers of " + sym), { head: [note, ed.input], body: ed.rows },
      [el("button", { class: "btn", type: "button", onclick: function () { if (st().peers) delete st().peers[sym]; save(); paintNote(); ed.paint(); T.lists.changed(); } }, "Reset to automatic"),
       el("button", { class: "btn primary", type: "button", onclick: function () { m.close(); } }, "Done")],
      function () { if (onDone) onDone(); });
    ed.input.focus();
  };
})(window.T);
