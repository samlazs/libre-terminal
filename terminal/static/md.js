/* Shared helpers + a small, safe Markdown renderer.
   All remote text is HTML-escaped first; only a fixed set of tags is ever emitted. */
window.T = window.T || {};
(function (T) {
  "use strict";

  T.esc = function (s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };

  /** el("div", {class:"x", onclick:fn, title:"…"}, [children | strings]) */
  T.el = function (tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) {
      for (var k in attrs) {
        var v = attrs[k];
        if (v == null || v === false) continue;
        if (k === "class") e.className = v;
        else if (k === "text") e.textContent = v;
        else if (k.slice(0, 2) === "on" && typeof v === "function") e.addEventListener(k.slice(2), v);
        else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
        else e.setAttribute(k, v === true ? "" : v);
      }
    }
    if (kids != null) {
      (Array.isArray(kids) ? kids : [kids]).forEach(function (c) {
        if (c == null || c === false) return;
        e.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
      });
    }
    return e;
  };

  T.debounce = function (fn, ms) {
    var t;
    return function () {
      var a = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, a); }, ms);
    };
  };

  /** Decode HTML entities / strip tags from untrusted text without ever inserting it as HTML. */
  T.plain = function (s) {
    if (s == null) return "";
    s = String(s);
    if (!/[<&]/.test(s)) return s;
    var doc = new DOMParser().parseFromString(s, "text/html");
    return doc.body ? doc.body.textContent || "" : s;
  };

  T.safeUrl = function (u) {
    u = String(u || "").trim();
    return /^https?:\/\//i.test(u) ? u : null;
  };

  function inline(s) {
    // s is already HTML-escaped
    var codes = [];
    s = s.replace(/`([^`]+)`/g, function (_, c) { codes.push(c); return "\u0000" + (codes.length - 1) + "\u0000"; });
    // images ![alt](url): shown as a link, so opening a card never contacts third-party image hosts
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, function (m, alt, url) {
      var u = T.safeUrl(url.replace(/&amp;/g, "&"));
      return u ? '<a target="_blank" rel="noopener noreferrer" href="' + T.esc(u) + '">[image' + (alt ? ": " + alt : "") + "] ↗</a>" : alt;
    });
    // links [text](url)
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, function (m, txt, url) {
      var u = T.safeUrl(url.replace(/&amp;/g, "&"));
      return u ? '<a target="_blank" rel="noopener noreferrer" href="' + T.esc(u) + '">' + txt + "</a>" : txt;
    });
    // bare urls
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, function (m, pre, url) {
      var u = T.safeUrl(url.replace(/&amp;/g, "&"));
      return u ? pre + '<a target="_blank" rel="noopener noreferrer" href="' + T.esc(u) + '">' + url + "</a>" : m;
    });
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return "<code>" + codes[+i] + "</code>"; });
    return s;
  }

  function cells(line) {
    var t = line.trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function (c) { return c.trim(); });
  }

  /** Render markdown text to a safe HTML string. */
  T.md = function (src) {
    if (src == null) return "";
    var lines = T.esc(String(src).replace(/\r\n?/g, "\n")).split("\n");
    var out = [], i = 0, para = [], open = 0;
    function flush() { if (para.length) { out.push("<p>" + inline(para.join(" ")) + "</p>"); para = []; } }
    while (i < lines.length) {
      var ln = lines[i];
      var m;
      if (/^\s*```/.test(ln)) {
        flush();
        var buf = []; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
        i++;
        out.push("<pre><code>" + buf.join("\n") + "</code></pre>");
        continue;
      }
      // Collapsible sections some providers send as raw HTML (OECD dataflow parameters): the escaped
      // <details>/<summary> lines become real ones, generated here; anything else in them stays escaped text.
      if (/^\s*&lt;details( open)?&gt;\s*$/i.test(ln)) { flush(); out.push("<details>"); open++; i++; continue; }
      if (/^\s*&lt;\/details&gt;\s*$/i.test(ln)) { flush(); if (open) { out.push("</details>"); open--; } i++; continue; }
      if ((m = /^\s*&lt;summary&gt;(.*)&lt;\/summary&gt;\s*$/i.exec(ln)) && open) {
        flush();
        out.push("<summary>" + inline(m[1].replace(/&lt;(b|strong)&gt;(.*?)&lt;\/\1&gt;/gi, "**$2**")) + "</summary>");
        i++; continue;
      }
      if (!ln.trim()) { flush(); i++; continue; }
      if ((m = /^(#{1,6})\s+(.*)$/.exec(ln))) {
        flush();
        var lv = Math.min(m[1].length, 4);
        out.push("<h" + lv + ">" + inline(m[2].replace(/\s+#+\s*$/, "")) + "</h" + lv + ">");
        i++; continue;
      }
      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(ln)) { flush(); out.push("<hr>"); i++; continue; }
      // table: header line followed by |---|---|
      if (ln.indexOf("|") >= 0 && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
        flush();
        var head = cells(ln); i += 2;
        var rows = [];
        while (i < lines.length && lines[i].indexOf("|") >= 0 && lines[i].trim()) rows.push(cells(lines[i++]));
        out.push("<table><thead><tr>" + head.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>" +
          rows.map(function (r) { return "<tr>" + r.map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table>");
        continue;
      }
      if (/^\s*&gt;\s?/.test(ln)) {
        flush();
        var q = [];
        while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*&gt;\s?/, ""));
        out.push("<blockquote>" + inline(q.join(" ")) + "</blockquote>");
        continue;
      }
      if ((m = /^\s*([-*+]|\d+[.)])\s+/.exec(ln))) {
        flush();
        var ordered = /\d/.test(m[1]);
        var items = [];
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          var item = lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, "");
          while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) item += " " + lines[i++].trim();
          items.push("<li>" + inline(item) + "</li>");
        }
        out.push((ordered ? "<ol>" : "<ul>") + items.join("") + (ordered ? "</ol>" : "</ul>"));
        continue;
      }
      para.push(ln.trim());
      i++;
    }
    flush();
    while (open-- > 0) out.push("</details>");
    return out.join("\n");
  };
})(window.T);
