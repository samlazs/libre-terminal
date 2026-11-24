/* Popovers, searchable dropdowns, menus, modals, toasts. */
(function (T) {
  "use strict";
  var el = T.el;
  var openPop = null;

  T.closePop = function () {
    if (openPop) { openPop.node.remove(); if (openPop.onClose) openPop.onClose(); openPop = null; }
  };

  document.addEventListener("mousedown", function (e) {
    if (openPop && !openPop.node.contains(e.target) && !(openPop.anchor && openPop.anchor.contains(e.target))) T.closePop();
  }, true);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && openPop) { T.closePop(); e.stopPropagation(); } }, true);
  window.addEventListener("resize", function () { T.closePop(); });
  // a popover is placed once under its anchor: it closes when the page scrolls under it or its anchor is gone (dashboard
  // or tab switch), instead of floating mid-screen (review D3)
  document.addEventListener("scroll", function (e) {
    if (openPop && !(e.target && e.target.nodeType === 1 && openPop.node.contains(e.target))) T.closePop();
  }, true);
  setInterval(function () { if (openPop && openPop.anchor && !openPop.anchor.isConnected) T.closePop(); }, 400);

  /** Show `node` as a popover under `anchor`. */
  T.popover = function (anchor, node, opts) {
    T.closePop();
    opts = opts || {};
    node.classList.add("pop");
    if (opts.width) node.style.width = Math.min(opts.width, window.innerWidth - 16) + "px";
    document.getElementById("pop-root").appendChild(node);
    var r = anchor.getBoundingClientRect();
    var w = node.offsetWidth, h = node.offsetHeight;
    var left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    var top = r.bottom + 4;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
    node.style.left = left + "px"; node.style.top = top + "px";
    openPop = { node: node, anchor: anchor, onClose: opts.onClose };
    return node;
  };

  function normOpts(list) {
    return (list || []).map(function (o) {
      if (o != null && typeof o === "object") return { label: o.label == null ? String(o.value) : String(o.label), value: o.value, extra: o.extraInfo && o.extraInfo.description };
      return { label: String(o), value: o };
    });
  }

  /**
   * Searchable dropdown button.
   * cfg: { value, options: [] | () => Promise<[]>, multi, placeholder, allowFree, width, onChange(v) }
   */
  T.dropdown = function (cfg) {
    var value = cfg.value;
    var known = Array.isArray(cfg.options) ? normOpts(cfg.options) : null;
    var btn = el("button", { class: "dd-btn", type: "button" });

    function vals() { return value == null || value === "" ? [] : Array.isArray(value) ? value : cfg.multi ? String(value).split(",").filter(Boolean) : [value]; }
    function labelOf(v) {
      if (known) for (var i = 0; i < known.length; i++) if (String(known[i].value) === String(v)) return known[i].label;
      return String(v);
    }
    function paint() {
      var v = vals();
      btn.classList.toggle("empty", !v.length);
      btn.textContent = !v.length ? (cfg.placeholder || "—") : v.length === 1 ? labelOf(v[0]) : labelOf(v[0]) + " +" + (v.length - 1);
      btn.title = v.map(labelOf).join(", ");
    }
    paint();
    // options fetched on demand: name the current value once they arrive (Congress committees showed "hsju00" until the
    // list was opened; review H2). Late, so it does not compete with the card's own data request.
    if (!known && typeof cfg.options === "function" && vals().length) {
      setTimeout(function () {
        if (known || !btn.isConnected) return;
        Promise.resolve().then(cfg.options).then(function (o) { if (!known && Array.isArray(o)) { known = normOpts(o); paint(); } }).catch(function () { /* the raw value stays */ });
      }, 1500);
    }

    function open() {
      var search = el("input", { type: "search", placeholder: cfg.allowFree ? "Search or type a value, Enter to apply…" : "Search…", autocomplete: "off" });
      var list = el("div", { class: "pop-list" });
      var foot = el("div", { class: "pop-foot" });
      var box = el("div", null, [search, list, foot]);
      var opts = known || [], hl = 0, shown = [];

      function commit(v) { value = v; paint(); if (cfg.onChange) cfg.onChange(v); }
      function pick(o) {
        if (cfg.multi) {
          var cur = vals().map(String), s = String(o.value), i = cur.indexOf(s);
          if (i >= 0) cur.splice(i, 1); else cur.push(s);
          commit(cur);
          draw();
        } else { commit(o.value); T.closePop(); }
      }
      function draw() {
        var q = search.value.trim().toLowerCase(), cur = vals().map(String);
        var terms = q ? q.split(/\s+/) : [];
        shown = [];
        for (var i = 0; i < opts.length && shown.length < 200; i++) {
          var hay = (opts[i].label + " " + opts[i].value).toLowerCase();
          if (terms.every(function (t) { return hay.indexOf(t) >= 0; })) shown.push(opts[i]);
        }
        // exact value matches first
        if (q) shown.sort(function (a, b) { return (String(b.value).toLowerCase() === q) - (String(a.value).toLowerCase() === q); });
        list.textContent = "";
        if (hl >= shown.length) hl = 0;
        shown.forEach(function (o, i) {
          var on = cur.indexOf(String(o.value)) >= 0;
          var row = el("div", { class: "opt" + (on ? " sel" : "") + (i === hl ? " hl" : ""), title: o.extra || o.label }, [cfg.multi ? el("span", { class: "ck" }, on ? "✓" : "") : null, o.label]);
          row.addEventListener("mousedown", function (e) { e.preventDefault(); pick(o); });
          list.appendChild(row);
        });
        var total = opts.length;
        foot.textContent = "";
        foot.appendChild(el("span", null, loading ? "Loading options…" : err ? err : !total ? "No options" + (cfg.allowFree ? " — type a value and press Enter" : "") : shown.length + (shown.length < total ? " of " + total : "") + " options"));
        if (vals().length) {
          var clr = el("a", { href: "#", style: { marginLeft: "auto" } }, "Clear");
          clr.addEventListener("mousedown", function (e) { e.preventDefault(); commit(cfg.multi ? [] : null); if (!cfg.multi) T.closePop(); else draw(); });
          foot.appendChild(clr);
        }
      }
      var loading = false, err = "";
      search.addEventListener("input", function () { hl = 0; draw(); });
      search.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown") { hl = Math.min(hl + 1, shown.length - 1); draw(); e.preventDefault(); }
        else if (e.key === "ArrowUp") { hl = Math.max(hl - 1, 0); draw(); e.preventDefault(); }
        else if (e.key === "Enter") {
          e.preventDefault();
          var q = search.value.trim();
          var exact = shown.filter(function (o) { return String(o.value).toLowerCase() === q.toLowerCase(); })[0];
          if (exact) pick(exact);
          else if (cfg.allowFree && q && (!shown.length || e.ctrlKey || !known || !known.length)) {
            if (cfg.multi) { var cur = vals().map(String); q.split(",").forEach(function (p) { p = p.trim(); if (p && cur.indexOf(p) < 0) cur.push(p); }); commit(cur); search.value = ""; draw(); }
            else { commit(q); T.closePop(); }
          } else if (shown[hl]) pick(shown[hl]);
          else if (cfg.allowFree && q) { commit(cfg.multi ? vals().concat([q]) : q); if (!cfg.multi) T.closePop(); }
        }
      });
      T.popover(btn, box, { width: cfg.width || 320 });
      draw();
      search.focus();
      if (typeof cfg.options === "function") {
        loading = true; draw();
        Promise.resolve().then(cfg.options).then(function (res) { known = opts = normOpts(res); loading = false; paint(); if (document.body.contains(box)) draw(); })
          .catch(function (e) { loading = false; err = "Could not load options: " + T.userText(e); if (document.body.contains(box)) draw(); });
      }
    }
    btn.addEventListener("click", function () { if (openPop && openPop.anchor === btn) T.closePop(); else open(); });
    btn.setValue = function (v) { value = v; paint(); };
    return btn;
  };

  /** items: [{label, onClick, danger} | "sep"] */
  T.menu = function (anchor, items) {
    var box = el("div", { class: "menu" });
    var list = el("div", { class: "pop-list", style: { padding: "4px" } });
    items.forEach(function (it) {
      if (!it) return;
      if (it === "sep") { list.appendChild(el("div", { class: "sep" })); return; }
      var row = el("div", { class: "opt" }, it.label);
      if (it.danger) row.style.color = "var(--down)";
      row.addEventListener("click", function () { T.closePop(); it.onClick(); });
      list.appendChild(row);
    });
    box.appendChild(list);
    T.popover(anchor, box, { width: 230 });
  };

  // open modals, newest last: Escape closes only the one on top (a dialog over the watchlist editor used to close both)
  var modals = [];
  T.modal = function (node, onClose) {
    var bg = el("div", { class: "modal-bg" }, node);
    modals.push(bg);
    function close() {
      bg.remove(); document.removeEventListener("keydown", key, true);
      var i = modals.indexOf(bg); if (i >= 0) modals.splice(i, 1);
      if (onClose) onClose();
    }
    function key(e) {
      if (e.key !== "Escape" || openPop || e.defaultPrevented || modals[modals.length - 1] !== bg) return;
      if (e.target && e.target.closest && e.target.closest("[data-own-esc]")) return;     // a field that cancels itself on Escape
      e.stopPropagation(); e.preventDefault(); close();
    }
    bg.addEventListener("mousedown", function (e) { if (e.target === bg) close(); });
    document.addEventListener("keydown", key, true);
    document.getElementById("modal-root").appendChild(bg);
    return { close: close, el: bg };
  };

  var toastT;
  T.toast = function (msg) {
    var old = document.querySelector(".toast"); if (old) old.remove();
    var t = el("div", { class: "toast" }, msg);
    document.body.appendChild(t);
    clearTimeout(toastT);
    toastT = setTimeout(function () { t.remove(); }, 2600);
  };

  T.download = function (name, text, mime) {
    // a byte-order mark lets Excel open UTF-8 CSV files (accents, €, ×) correctly
    var body = /csv/.test(mime || "") ? "﻿" + text : text;
    var a = el("a", { href: URL.createObjectURL(new Blob([body], { type: mime || "text/plain" })), download: name });
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  };
  T.downloadURL = function (name, url) {
    var a = el("a", { href: url, download: name });
    document.body.appendChild(a); a.click();
    setTimeout(function () { a.remove(); }, 500);
  };
  /** Copy text to the clipboard; tab-separated text pastes into Excel or Sheets as cells. */
  T.copyText = function (text, what) {
    function ok() { T.toast((what || "Text") + " copied to the clipboard"); }
    function fallback() {
      var ta = el("textarea", { style: { position: "fixed", left: "-9999px", top: "0" } });
      ta.value = text; document.body.appendChild(ta); ta.select();
      var done = false;
      try { done = document.execCommand("copy"); } catch (e) { /* ignore */ }
      ta.remove();
      if (done) ok(); else T.toast("Copy failed: the browser refused clipboard access");
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(ok, fallback);
    else fallback();
  };
  /** A safe file name from a card title. */
  T.fileName = function (title, ext) {
    var d = new Date(), day = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    return (String(title || "data").replace(/[^\w\-. ]+/g, " ").replace(/\s+/g, "_").replace(/^_+|_+$/g, "").slice(0, 80) || "data") + "_" + day + "." + ext;
  };
})(window.T);
