"""Shared pieces of the headless-browser checks (test_frontend, test_viewing): the browser lookup and the scripts
that capture a page's errors and report its result into the DOM."""

import os
import shutil
from pathlib import Path


def find_browser():
    env = os.environ.get("TERMINAL_TEST_BROWSER", "").strip()
    cands = [env] if env else []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            cands += [str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                      str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")]
    cands += [shutil.which(n) or "" for n in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chromium-browser")]
    cands.append("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    return next((c for c in cands if c and Path(c).is_file()), None)



CAPTURE = """
(function () {
  window.addEventListener("error", function (e) { window.__fcError = window.__fcError || ("uncaught " + e.message + " (" + String(e.filename || "").split("/").pop() + ":" + e.lineno + ")"); });
  window.addEventListener("unhandledrejection", function (e) { var r = e && e.reason; window.__fcError = window.__fcError || ("unhandled rejection: " + String(r && r.message || r)); });
  var log = console.log;
  console.log = function (m) { if (/^frontend checks: \\d+ passed$/.test(String(m))) window.__fc = String(m); return log.apply(console, arguments); };
})();
"""
REPORT = """
(function () {
  function emit() {
    if (document.getElementById("frontend-result")) return;
    var out = document.createElement("pre"); out.id = "frontend-result";
    out.textContent = window.__fcError ? "FRONTEND FAILED: " + window.__fcError : (window.__fc || "FRONTEND FAILED: no result");
    document.body.appendChild(out);
    var notes = document.createElement("pre"); notes.id = "frontend-notes";
    notes.textContent = (window.__fcNotes || []).join(" | ");
    document.body.appendChild(notes);
  }
  var p = window.__fcPromise;
  if (p && typeof p.then === "function") {
    p.then(function () { setTimeout(emit, 0); }, function (e) {
      window.__fcError = window.__fcError || String(e && e.message || e);
      setTimeout(emit, 0);
    });
  } else {
    emit();
  }
})();
"""
