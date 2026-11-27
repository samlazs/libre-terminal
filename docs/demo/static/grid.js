/* 40-column drag / resize grid with vertical compaction. */
(function (T) {
  "use strict";
  var COLS = 40, ROW = 22, GAP = 6, MIN_W = 6, MIN_H = 5;

  function hit(a, b) { return a !== b && a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y; }

  /** Pack items upwards without overlap. `first` (optional) wins ties. */
  function compact(items, first) {
    var order = items.slice().sort(function (a, b) {
      var ay = a === first ? a.y - 0.5 : a.y, by = b === first ? b.y - 0.5 : b.y;
      return ay - by || a.x - b.x;
    });
    var placed = [];
    order.forEach(function (it) {
      it.w = Math.max(1, Math.min(COLS, Math.round(it.w)));
      it.h = Math.max(1, Math.round(it.h));
      it.x = Math.max(0, Math.min(COLS - it.w, Math.round(it.x)));
      it.y = Math.max(0, Math.round(it.y));
      var probe = { x: it.x, w: it.w, h: it.h, y: it.y };
      while (probe.y > 0) {
        probe.y--;
        if (placed.some(function (p) { return hit(p, probe); })) { probe.y++; break; }
      }
      while (placed.some(function (p) { return hit(p, probe); })) probe.y++;
      it.y = probe.y;
      placed.push(it);
    });
  }

  T.grid = function (canvas, scroller, opts) {
    var items = [];          // [{item, node}]
    var ghost = null;

    function unit() { return canvas.clientWidth / COLS; }
    function rect(it) {
      var u = unit();
      return { left: it.x * u + GAP / 2, top: it.y * ROW + GAP / 2, width: Math.max(40, it.w * u - GAP), height: it.h * ROW - GAP };
    }
    /** Narrow canvas: cards flow into 2 columns (below 1000 px) or 1 (below 760 px), display only; the saved layout is untouched. */
    function flowCols() { var w = canvas.clientWidth; return w && w < 760 ? 1 : w && w < 1000 ? 2 : 0; }
    function placeFlow(n) {
      var W = canvas.clientWidth, colW = W / n, heights = [];
      for (var i = 0; i < n; i++) heights.push(0);
      items.slice().sort(function (a, b) { return a.item.y - b.item.y || a.item.x - b.item.x; }).forEach(function (e) {
        if (e.node.classList.contains("max")) return;
        var hpx = e.item.h * ROW, top, left, width;
        if (n === 1 || e.item.w >= 28) {
          top = Math.max.apply(null, heights); left = 0; width = W;
          for (var k = 0; k < n; k++) heights[k] = top + hpx;
        } else {
          var col = heights.indexOf(Math.min.apply(null, heights));
          top = heights[col]; left = col * colW; width = colW; heights[col] = top + hpx;
        }
        e.node.style.left = left + GAP / 2 + "px"; e.node.style.top = top + GAP / 2 + "px";
        e.node.style.width = width - GAP + "px"; e.node.style.height = hpx - GAP + "px";
      });
      canvas.style.height = Math.max.apply(null, heights) + 80 + "px";
    }
    function place() {
      var fc = flowCols();
      if (fc) return placeFlow(fc);
      var maxY = 0;
      items.forEach(function (e) {
        var r = rect(e.item);
        if (!e.node.classList.contains("max")) {
          e.node.style.left = r.left + "px"; e.node.style.top = r.top + "px";
          e.node.style.width = r.width + "px"; e.node.style.height = r.height + "px";
        }
        maxY = Math.max(maxY, e.item.y + e.item.h);
      });
      canvas.style.height = maxY * ROW + 80 + "px";
    }
    function showGhost(g) {
      if (!ghost) { ghost = T.el("div", { class: "ghost" }); canvas.appendChild(ghost); }
      var r = rect(g);
      ghost.style.left = r.left + "px"; ghost.style.top = r.top + "px"; ghost.style.width = r.width + "px"; ghost.style.height = r.height + "px";
    }
    function hideGhost() { if (ghost) { ghost.remove(); ghost = null; } }
    function all() { return items.map(function (e) { return e.item; }); }
    function commit(moved) { compact(all(), moved); place(); if (opts.onChange) opts.onChange(); }

    function bindDrag(entry, handle) {
      handle.addEventListener("pointerdown", function (ev) {
        if (ev.button !== 0 || entry.node.classList.contains("max") || flowCols()) return;     // no layout edits while reflowed
        if (ev.target.closest("button, input, select, a, .w-tools, .dd-btn")) return;
        var sx = ev.clientX, sy = ev.clientY, started = false, done = false;
        var o = rect(entry.item), st0 = scroller.scrollTop, target = null;
        function begin() {
          started = true;
          entry.node.classList.add("dragging");
          document.body.classList.add("grid-dragging");
          try { if (handle.setPointerCapture) handle.setPointerCapture(ev.pointerId); } catch (e) {}
        }
        function move(e) {
          var dx = e.clientX - sx, dy = e.clientY - sy + (scroller.scrollTop - st0);
          if (!started) { if (Math.abs(dx) + Math.abs(dy) < 5) return; begin(); }
          entry.node.style.left = o.left + dx + "px"; entry.node.style.top = o.top + dy + "px";
          var u = unit();
          target = {
            x: Math.max(0, Math.min(COLS - entry.item.w, Math.round((o.left + dx) / u))),
            y: Math.max(0, Math.round((o.top + dy) / ROW)), w: entry.item.w, h: entry.item.h
          };
          showGhost(target);
          var sr = scroller.getBoundingClientRect();
          if (e.clientY > sr.bottom - 40) scroller.scrollTop += 14;
          else if (e.clientY < sr.top + 40) scroller.scrollTop -= 14;
        }
        function up() {
          if (done) return;
          done = true;
          document.removeEventListener("pointermove", move);
          document.removeEventListener("pointerup", up);
          document.removeEventListener("pointercancel", up);
          window.removeEventListener("blur", up);
          try { if (handle.releasePointerCapture) handle.releasePointerCapture(ev.pointerId); } catch (e) {}
          document.body.classList.remove("grid-dragging");
          hideGhost();
          if (!started) return;
          entry.node.classList.remove("dragging");
          if (target) { entry.item.x = target.x; entry.item.y = target.y; }
          commit(entry.item);
        }
        document.addEventListener("pointermove", move);
        document.addEventListener("pointerup", up);
        document.addEventListener("pointercancel", up);
        window.addEventListener("blur", up);
      });
    }

    function bindResize(entry, handle) {
      handle.addEventListener("pointerdown", function (ev) {
        if (ev.button !== 0 || flowCols()) return;
        ev.preventDefault(); ev.stopPropagation();
        var sx = ev.clientX, sy = ev.clientY, o = rect(entry.item), target = null, done = false;
        entry.node.classList.add("dragging");
        document.body.classList.add("grid-dragging");
        try { if (handle.setPointerCapture) handle.setPointerCapture(ev.pointerId); } catch (e) {}
        function move(e) {
          var u = unit();
          var w = Math.max(MIN_W, Math.min(COLS - entry.item.x, Math.round((o.width + GAP + e.clientX - sx) / u)));
          var h = Math.max(MIN_H, Math.round((o.height + GAP + e.clientY - sy) / ROW));
          target = { x: entry.item.x, y: entry.item.y, w: w, h: h };
          entry.node.style.width = Math.max(120, o.width + e.clientX - sx) + "px";
          entry.node.style.height = Math.max(80, o.height + e.clientY - sy) + "px";
          showGhost(target);
        }
        function up() {
          if (done) return;
          done = true;
          document.removeEventListener("pointermove", move);
          document.removeEventListener("pointerup", up);
          document.removeEventListener("pointercancel", up);
          window.removeEventListener("blur", up);
          try { if (handle.releasePointerCapture) handle.releasePointerCapture(ev.pointerId); } catch (e) {}
          document.body.classList.remove("grid-dragging");
          hideGhost(); entry.node.classList.remove("dragging");
          if (target) { entry.item.w = target.w; entry.item.h = target.h; }
          commit(entry.item);
        }
        document.addEventListener("pointermove", move);
        document.addEventListener("pointerup", up);
        document.addEventListener("pointercancel", up);
        window.addEventListener("blur", up);
      });
    }

    var ro = new ResizeObserver(function () { place(); });
    ro.observe(canvas);

    return {
      clear: function () { items = []; canvas.textContent = ""; ghost = null; canvas.style.height = "0px"; },
      add: function (item, node, dragHandle, resizeHandle) {
        var entry = { item: item, node: node };
        items.push(entry);
        canvas.appendChild(node);
        if (dragHandle) bindDrag(entry, dragHandle);
        if (resizeHandle) bindResize(entry, resizeHandle);
      },
      /** Swap the card of an item in place (used when its data source changes). */
      replace: function (item, node, dragHandle, resizeHandle) {
        for (var i = 0; i < items.length; i++) {
          if (items[i].item !== item) continue;
          canvas.replaceChild(node, items[i].node);
          items[i] = { item: item, node: node };
          if (dragHandle) bindDrag(items[i], dragHandle);
          if (resizeHandle) bindResize(items[i], resizeHandle);
        }
        place();
      },
      remove: function (item) {
        items = items.filter(function (e) { if (e.item === item) { e.node.remove(); return false; } return true; });
        commit(null);
      },
      /** tidy = true packs the layout; false keeps positions as given. */
      layout: function (tidy) { if (tidy) compact(all(), null); place(); },
      bottom: function () { return all().reduce(function (m, it) { return Math.max(m, it.y + it.h); }, 0); },
      rowHeight: ROW
    };
  };
  T.grid.compact = compact;
})(window.T);
