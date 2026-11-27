/* One request wrapper for the whole page (the only place fetch() is called), the error object every non-2xx answer
   carries ({error: {kind, message, retryable, retry_after}, detail, sources}), and the one error renderer. */
window.T = window.T || {};
(function (T) {
  "use strict";
  var DEFAULT_TIMEOUT = 30000;

  /**
   * fetch() with a bounded wait. opts: {method, body, headers, timeout (ms; 0 = the caller bounds it), signal, mode,
   * cache, credentials}. Resolves to the Response; a timeout rejects with name "TimeoutError" (e.timedOut = true),
   * an abort from opts.signal with name "AbortError".
   */
  T.request = function (url, opts) {
    opts = opts || {};
    var ms = opts.timeout == null ? DEFAULT_TIMEOUT : +opts.timeout;
    var ctl = new AbortController(), timedOut = false, timer = null, outer = opts.signal;
    function relay() { ctl.abort(); }
    if (outer) { if (outer.aborted) ctl.abort(); else outer.addEventListener("abort", relay); }
    if (ms > 0) timer = setTimeout(function () { timedOut = true; ctl.abort(); }, ms);
    var init = { method: opts.method || "GET", signal: ctl.signal };
    ["body", "headers", "mode", "cache", "credentials"].forEach(function (k) { if (opts[k] != null) init[k] = opts[k]; });
    return fetch(url, init).catch(function (e) {
      if (timedOut) {
        var t = new Error("No answer after " + Math.round(ms / 1000) + " seconds. The source is slow or not responding.");
        t.name = "TimeoutError"; t.timedOut = true; t.errorKind = "timeout";
        throw t;
      }
      throw e;
    }).finally(function () {
      if (timer) clearTimeout(timer);
      if (outer) outer.removeEventListener("abort", relay);
    });
  };

  /** The raw text of an error for logs and bug reports only (never shown as is). */
  T.errDetail = function (e) { return String(e && e.message != null ? e.message : e); };

  /** The footer's date: the data's own as_of from the envelope, else when it was fetched. {text, title} */
  T.asOfText = function (env, fetchedAt) {
    var a = env && typeof env.as_of === "string" ? env.as_of.trim() : "";
    var m = /^(\d{4}-\d{2}(?:-\d{2})?)(?:[T ](\d{2}:\d{2}))?/.exec(a);
    if (m) return { text: "data as of " + m[1] + (m[2] ? " " + m[2] + (/Z$|[+-]\d{2}:?\d{2}$/.test(a) ? " UTC" : "") : ""), title: "Fetched " + fetchedAt };
    var na = env && typeof env.as_of_na === "string" ? env.as_of_na : "";
    return { text: "updated " + fetchedAt, title: na || "When this answer was fetched" };
  };

  /** An Error for a non-2xx answer, carrying the envelope's kind, retry_after, sources and user_message flag. */
  T.httpError = function (status, bodyTxt, message) {
    var j = null;
    try { j = JSON.parse(bodyTxt); } catch (e) { /* not JSON */ }
    var msg = message || (j && j.error && j.error.message) || (j && typeof j.detail === "string" && j.detail) || ("HTTP " + status);
    var err = new Error(String(msg));
    err.status = status;
    if (j && typeof j === "object") {
      err.userMessage = j.user_message === true;
      if (Array.isArray(j.sources)) err.sources = j.sources;
      if (j.error && typeof j.error.kind === "string") { err.errorKind = j.error.kind; err.retryAfter = +j.error.retry_after || 0; err.retryable = j.error.retryable === true; }
    }
    if (!err.errorKind && status >= 400 && !(j && j.error)) err.rawBackend = true;
    return err;
  };

  /** GET/POST and parse JSON; a non-2xx answer throws T.httpError. An empty body is null. */
  T.requestJSON = function (url, opts) {
    return T.request(url, opts).then(function (r) {
      return r.text().then(function (txt) {
        if (!r.ok) throw T.httpError(r.status, txt);
        if (!txt || !txt.trim()) return null;
        return JSON.parse(txt);
      });
    });
  };

  /** How a failed request reads to the user: by error.kind when the answer had one, else by the message classifier. */
  T.classifyFailure = function (e, ctx) {
    ctx = ctx || {};
    var msg = e && e.message != null ? e.message : String(e);
    if (e && e.timedOut) return T.classifyKind("timeout", msg, ctx, e);
    if (e && e.errorKind) return T.classifyKind(e.errorKind, msg, ctx, e);
    if (/Failed to fetch|NetworkError|Load failed/i.test(String(msg))) return T.classifyKind("unavailable", msg, ctx, e);
    return T.classifyError(msg, e && e.status, ctx);
  };

  /** Plain text for a failed request (toasts, menus): never a URL, a traceback or an exception name. */
  T.userText = function (e, ctx) {
    var c = T.classifyFailure(e, Object.assign({ userMessage: true }, ctx || {}));
    return c.text + (c.hint && c.kind === "err" ? " " + c.hint : "");
  };

  /**
   * The one error renderer for any non-2xx answer: classifies by error.kind and, when `target` is an element, draws
   * the standard error block into it (text, hint). Returns the classification so a card can act on it (retry, key hint).
   */
  T.renderError = function (target, e, ctx) {
    var c = T.classifyFailure(e, ctx);
    if (target && target.appendChild) {
      target.textContent = "";
      var box = document.createElement("div");
      box.className = "msg " + (c.kind === "err" ? "err" : "info");
      box.setAttribute("data-error-kind", (e && e.errorKind) || c.cls || "");
      var b = document.createElement("b"); b.textContent = c.text; box.appendChild(b);
      if (c.hint) { var h = document.createElement("div"); h.className = "hint"; h.textContent = c.hint; box.appendChild(h); }
      target.appendChild(box);
    }
    return c;
  };
})(window.T);
