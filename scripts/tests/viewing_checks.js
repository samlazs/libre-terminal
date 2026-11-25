/* P3-06: viewing never changes the saved layout. Loaded by test_viewing.py into the real page (real dashboards replayed
   from the recorded catalogs, an isolated copy of the saved state, no data). Opens every tab of every built-in dashboard,
   changes the dashboard ticker AAPL -> SAP.DE -> 7203.T, and makes a source, a view and a column pick; afterwards the
   saved dashboards and every tab's edited flag are what they were. Picks live in the preference keys (srcsel, viewsel,
   colsel, libsel, groups, cardview), never in the layout. */
window.__fcPromise = (async function (T) {
  var n = 0;
  function ok(c, what) { if (!c) throw new Error("FAILED: " + what); n++; }
  function settle(ms) { return new Promise(function (r) { setTimeout(r, ms || 0); }); }
  function canon(v) {                    // stable text of a value: sorted keys, runtime-only "_" keys left out
    if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
    if (v && typeof v === "object") return "{" + Object.keys(v).filter(function (k) { return k.charAt(0) !== "_"; }).sort()
      .map(function (k) { return JSON.stringify(k) + ":" + canon(v[k]); }).join(",") + "}";
    return JSON.stringify(v === undefined ? null : v);
  }
  function firstDiff(a, b) { var i = 0; while (i < a.length && a[i] === b[i]) i++; return "…" + a.slice(Math.max(0, i - 80), i + 60) + " | now …" + b.slice(Math.max(0, i - 80), i + 60); }
  function flags(id) { var d = T.findDashboard(id); return d ? d.tabs.map(function (t) { return t.id + ":" + !!t.edited; }).join(",") + "|" + !!d.edited : ""; }

  for (var bw = 0; bw < 400 && !T.S.booted; bw++) await settle(50);
  ok(!!T.S.booted, "the page booted");
  var gen = T.S.generated || [];
  var nTabs = gen.reduce(function (a, d) { return a + d.tabs.length; }, 0);
  ok(gen.length >= 28 && nTabs >= 120, "the built-in dashboards are loaded (" + gen.length + " dashboards, " + nTabs + " tabs)");
  var savedBefore = canon(JSON.parse(T.serializeState()).dashboards);
  var flagsBefore = {};
  gen.forEach(function (d) { flagsBefore[d.id] = flags(d.id); });
  var tabsEl = document.getElementById("tabs");
  var visited = 0, tickers = 0, srcPicks = 0, viewPicks = 0, colPicks = 0, drift = [];
  for (var di = 0; di < gen.length; di++) {
    var d = gen[di];
    if (!T.openDashboard(d.id)) continue;
    await settle(5);
    var before = canon(T.diffDashboard(d, T.S.cur, T.S.state.dashboards[d.id]) || null);
    var symG = (T.S.cur.groups || []).filter(function (g) { return g.paramName === "symbol"; })[0];
    var ids = (T.S.cur.tabs || []).filter(function (t) { return (T.S.cur.hiddenTabs || []).indexOf(t.id) < 0; }).map(function (t) { return t.id; });
    for (var ti = 0; ti < ids.length; ti++) {
      var tabNode = [].slice.call(tabsEl.querySelectorAll(".tab:not(.add)"))[ti];
      if (tabNode) tabNode.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await settle(5);
      if (T.S.tabId !== ids[ti]) continue;
      visited++;
      if (symG) {
        ["AAPL", "SAP.DE", "7203.T"].forEach(function (s) { T.setGroup(symG, s); });
        tickers++;
        await settle(5);
      }
      var cards = (T.S.cards || []).slice();
      for (var ci = 0; ci < cards.length; ci++) {
        var c = cards[ci];
        if (!c.def || !c.item) continue;
        var alts = [];
        try { alts = T.alternatives(c.item, c.def, {}) || []; } catch (e) { alts = []; }
        var other = alts.filter(function (a) { return !a.current && a.value; })[0];
        if (other && srcPicks < 400) { T.pickSource(c.item, other); srcPicks++; await settle(2); continue; }
        var strip = (c.def.strip || [])[0], opts = strip && (strip.options || []);
        if (opts && opts.length > 1) {
          var o = opts[opts.length - 1];
          T.setView(c.item, strip.param, o && typeof o === "object" ? o.value : o); viewPicks++;
        } else {
          T.setPref(c.item, "flip", true); viewPicks++;
        }
        if (c.def.type === "table") { T.setCols(c.item, ["date"]); colPicks++; }
      }
      await settle(2);
    }
    var after = canon(T.diffDashboard(d, T.S.cur, T.S.state.dashboards[d.id]) || null);
    if (after !== before) drift.push(d.id + ": " + firstDiff(before, after));
  }
  ok(visited >= 120, "every tab was opened (" + visited + ")");
  ok(tickers >= 20 && srcPicks > 0 && viewPicks > 0 && colPicks > 0, "ticker changes, source, view and column picks ran (" + [tickers, srcPicks, viewPicks, colPicks].join(", ") + ")");
  ok(!drift.length, "what an edit would save is unchanged by viewing: " + drift.slice(0, 3).join(" ;; "));
  var savedAfter = canon(JSON.parse(T.serializeState()).dashboards);
  ok(savedAfter === savedBefore, "the saved dashboards are deep-equal to before" + (savedAfter === savedBefore ? "" : " (" + firstDiff(savedBefore, savedAfter) + ")"));
  var flipped = gen.filter(function (d) { return flags(d.id) !== flagsBefore[d.id]; }).map(function (d) { return d.id; });
  ok(!flipped.length, "no tab's edited flag changed: " + flipped.join(", "));
  var st = JSON.parse(T.serializeState());
  ok(st.groups && st.srcsel && (st.viewsel || st.cardview) && st.colsel, "the picks were kept as preferences (groups, srcsel, viewsel/cardview, colsel)");
  window.__fcNotes = ["viewing: " + visited + " tabs, " + tickers + " ticker changes, " + srcPicks + " source, " + viewPicks + " view, " + colPicks + " column picks"];
  console.log("frontend checks: " + n + " passed");
})(window.T);
