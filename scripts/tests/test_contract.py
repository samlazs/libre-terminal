"""The response envelope (terminal/composite/core/contract.py): every /x/* route under success, CompositeError and a
defect inside the view, through the real UI server handler; parameter checks; one error object on every /api/* error."""

import http.client
import json
import os
import socket
import threading

from _common import check, done

os.environ["TERMINAL_STRICT_CONTRACT"] = "1"
import composite as C  # noqa: E402
import server  # noqa: E402
from composite.core import contract as K  # noqa: E402

# ---- unit: as_of derivation (hand-picked newest dates)
check(K.derive_as_of({"as_of": "2026-01-06"}) == "2026-01-06", "own as_of wins")
check(K.derive_as_of({"results": [{"date": "2026-01-02"}, {"date": "2026-01-06"}, {"date": "2025-12-31"}]}) == "2026-01-06", "newest row date")
check(K.derive_as_of({"rows": [{"as_of": "2026-01-02"}, {"period": "2026-03"}]}) == "2026-03", "month periods count")
check(K.derive_as_of({"data": [{"x": ["2025-01-02", "2026-02-03"], "y": [1, 2]}]}) == "2026-02-03", "plotly trace x values")
check(K.derive_as_of({"figure": {"data": [{"x": ["2026-02-03T15:30:00Z"]}]}}) == "2026-02-03T15:30:00Z", "datetime x values in a figure")
check(K.derive_as_of({"results": [{"date": "n/a"}, {"date": 20260101}]}) is None, "non-ISO values are ignored")
check(K.derive_as_of({"results": [{"name": "Apple"}]}) is None and K.derive_as_of([1]) is None, "nothing dated -> None")

# ---- unit: finish never mutates its input and fills as_of / as_of_na / status / notes
src = [C.src("Yahoo Finance", "quotes", C.YAHOO_URL)]
raw = {"results": [{"date": "2026-01-02", "v": 1}], "sources": src}
done_ = K.finish(raw)
check("as_of" not in raw and done_["as_of"] == "2026-01-02" and done_["status"] == "ok", "finish adds as_of and status on a copy")
links = K.finish({"results": [{"label": "IR site"}], "sources": src})
check(links["as_of_na"] == K.NO_DATE and "as_of" not in links, "undated answer gets an as_of_na reason")
st = K.finish({"results": [], "sources": src, "stale": "Showing data from 2 h ago", "delayed": "Delayed"})
check(st["status"] == "stale" and st["notes"] == ["Showing data from 2 h ago", "Delayed"], "stale answer: status stale + notes")
check(K.finish(done_) is done_, "an answer already complete is returned as is")

# ---- unit: violations
check(K.violations(done_) == [], "complete answer has no violation")
check(K.violations({"as_of": "2026-01-02"}) == ["sources missing or empty"], "missing sources")
check(K.violations({"sources": [{"name": "x"}], "as_of": "2026-01-02"}) == ["a source is not {name, detail, url}"], "malformed source")
check(K.violations({"sources": src}) == ["neither an ISO as_of nor an as_of_na reason"], "missing as_of")
check(K.violations({"sources": src, "as_of": "yesterday"}) == ["neither an ISO as_of nor an as_of_na reason"], "non-ISO as_of")
check(K.violations({"sources": src, "as_of_na": "x", "status": "weird"})[0].startswith("status 'weird'"), "unknown status")
check(K.violations({"sources": src, "as_of_na": "x", "notes": "text"}) == ["notes is not a list of text"], "notes must be a list")
check(K.violations([]) == ["answer is a list, not an object"], "non-object answer")
try:
    K.enforce("demo", {"as_of": "2026-01-02"})
    check(False, "strict mode must raise")
except K.ContractViolation as e:
    check("view demo" in str(e), "strict mode raises with the view name")

# ---- unit: error object
e = K.error_body("rate_limited", "Too many requests", retry_after=29.6, sources=src)
check(e == {"error": {"kind": "rate_limited", "message": "Too many requests", "retryable": True, "retry_after": 30},
            "detail": "Too many requests", "sources": src}, "error_body shape with retry_after rounded")
check(K.error_body("bogus", "x")["error"]["kind"] == "internal", "unknown kind -> internal")
check(K.error_body("input", "x")["error"]["retryable"] is False and K.error_body("timeout", "x")["error"]["retryable"] is True, "retryable from kind")
w = K.with_error(409, {"detail": "changed", "state": {"a": 1}})
check(w["error"] == {"kind": "input", "message": "changed", "retryable": False} and w["state"] == {"a": 1} and w["sources"] == [],
      "with_error keeps extra fields")
check(K.with_error(503, {"detail": "down"})["error"]["kind"] == "unavailable", "503 -> unavailable")
check(K.with_error(500, e) is e, "an error object is never wrapped twice")

# ---- every route has an explicit static source; the fallthrough is empty (so this check catches a missing entry)
for name in C.ROUTES:
    s = C.static_sources(name, {})
    check(bool(s) and all(x.get("name") for x in s), f"{name}: explicit static sources")
check(C.static_sources("no_such_view", {}) == [], "an unknown name falls through to []")

# ---- parameter check against the real signatures (wrappers resolve to their targets)
check(K.bad_params(C.ROUTES["board"], {"preset": "us_equity", "anything": "1"}) is None, "a view taking **_ accepts extra parameters")
check(K.target(C.ROUTES["valuation_bands"]).__name__ == "bands" and K.target(C.ROUTES["news"]).__name__ == "news", "wrappers resolve to targets")


def fixed(symbol="AAPL"):
    return {"results": [{"date": "2026-01-02", "v": 1}]}


check("unexpected keyword" in (K.bad_params(fixed, {"bogus": "1"}) or ""), "an unknown parameter is reported")

# ---- through the real handler: every route x {success, CompositeError, defect}
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
server.log = lambda _msg: None                 # the defect case logs a traceback by design
srv = server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
HOST = f"127.0.0.1:{port}"


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"Host": HOST}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def ok_error(status, body, kind, what):
    good = (isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("kind") == kind
            and isinstance(body["error"].get("message"), str) and isinstance(body["error"].get("retryable"), bool)
            and body.get("detail") == body["error"]["message"] and isinstance(body.get("sources"), list))
    check(good, f"{what}: {status} error object with kind {kind} ({str(body)[:160]})")


def success(**_):
    return {"results": [{"date": "2026-01-02", "value": 1.0}, {"date": "2026-01-05", "value": 2.0}]}


def upstream(**_):
    raise C.CompositeError("The source did not answer.", transient=True)


def defect(**_):
    return {"x": 1}["missing"] if _ else None + 1          # TypeError inside the view


saved = dict(C.ROUTES)
counts = {200: 0, 500: 0, 502: 0}
try:
    for name in saved:
        for case, fn in (("success", success), ("composite", upstream), ("defect", defect)):
            C.ROUTES[name] = fn
            status, body = req("GET", f"/x/{name}")
            if case == "success":
                check(status == 200 and K.violations(body) == [] and body["as_of"] == "2026-01-05" and body["status"] == "ok",
                      f"{name}: success inside the envelope ({str(body)[:120]})")
            else:
                want = 502 if case == "composite" else 500
                check(status == want, f"{name}/{case}: status {status}")
                ok_error(status, body, "upstream" if case == "composite" else "internal", f"{name}/{case}")
                check(bool(body.get("sources")), f"{name}/{case}: the error names the view's sources")
            counts[status] = counts.get(status, 0) + 1
    # 400: a parameter the view does not take (checked before the call, no traceback heuristic)
    C.ROUTES["board"] = fixed
    status, body = req("GET", "/x/board?bogus=1")
    check(status == 400, f"unknown parameter -> 400 ({status})")
    ok_error(status, body, "input", "unknown parameter")
    check(bool(body["sources"]) and body.get("user_message") is True, "400 names sources and is written for the user")
    status, body = req("GET", "/x/board?symbol=MSFT")
    check(status == 200 and body["as_of"] == "2026-01-02", "a known parameter still works")
finally:
    C.ROUTES.clear()
    C.ROUTES.update(saved)
check(counts[200] == counts[500] == counts[502] == len(saved), f"every route answered all three cases ({counts}, {len(saved)} routes)")
status, body = req("GET", "/x/no_such_view")
check(status == 404, "unknown view -> 404")
ok_error(status, body, "input", "unknown view")

# ---- /api/*: one error object on every non-2xx (malformed, oversize, cross-origin, unknown path, missing build)
for method, path in (("POST", "/api/clientlog"), ("POST", "/api/services/restart"), ("PUT", "/api/state")):
    status, body = req(method, path, b"{not json", {"Content-Type": "application/json"})
    check(status in (200, 400), f"{method} {path} malformed -> {status}")
    if status != 200:                                   # clientlog accepts any text by design
        ok_error(status, body, "input", f"{method} {path} malformed")
    status, body = req(method, path, b"{}", {"Content-Type": "application/json", "Content-Length": str(64 * 1024 * 1024)})
    check(status == 413, f"{method} {path} oversize -> 413 ({status})")
    ok_error(status, body, "input", f"{method} {path} oversize")
    status, body = req(method, path, b"{}", {"Content-Type": "application/json", "Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"})
    check(status == 403, f"{method} {path} cross-origin -> 403 ({status})")
    ok_error(status, body, "input", f"{method} {path} cross-origin")
status, body = req("GET", "/api/nope")
ok_error(status, body, "input", "unknown /api path")
status, body = req("GET", "/api/config", headers={"Host": "evil.example"})
check(status == 403, "foreign Host -> 403")
ok_error(status, body, "input", "foreign Host")
status, body = req("GET", "/api/dashboards")
if status != 200:
    ok_error(status, body, "unavailable", "dashboards not built")
srv.shutdown()

# ---- P3-02: every number column of every /x/* table declares its unit (columns.X_UNITS or its own format)
import columns  # noqa: E402
import xwidgets  # noqa: E402

xw = xwidgets.widgets()
missing_units = {wid: columns.undeclared(w) for wid, w in xw.items() if columns.undeclared(w)}
check(missing_units == {}, f"undeclared() over every /x/* table is empty: {missing_units}")
n_num = sum(1 for w in xw.values() for c in (((w.get("data") or {}).get("table") or {}).get("columnsDefs") or [])
            if c.get("cellDataType") == "number")
check(n_num >= 190, f"the gate saw the /x/* number columns ({n_num})")

# ---- gatekeeper P1-b: a table whose amounts are 'ccy' / 'ccy mn' (columns.X_UNITS) names their currency, or they are blank
ccy_views = sorted(w[2:] for w, u in columns.X_UNITS.items() if {"ccy", "ccy mn"} & set(u.values()))
check(ccy_views == ["cds_indices", "consensus", "earnings_history", "roic_dupont"], f"the tables with amounts in a named currency ({ccy_views})")
hist_rows = [{"date": "2026-11-04", "eps_est": 56.38, "eps_actual": 65.28, "surprise": 0.1579, "move_2d": None}]
check(K.violations({"results": hist_rows, "sources": src, "as_of": "2026-11-04"}, "earnings_history")
      == ["amounts in eps_est, eps_actual but the answer names no currency"], "EPS with no currency named is a violation")
check(K.violations({"results": hist_rows, "sources": src, "as_of": "2026-11-04", "currency": "JPY"}, "earnings_history") == [], "EPS in JPY: no violation")
check(K.violations({"results": [dict(hist_rows[0], eps_est=None, eps_actual=None)], "sources": src, "as_of": "2026-11-04"}, "earnings_history") == [],
      "no amount shown: nothing to name")
check(K.violations({"results": [{"notional_mn": 1250, "currency": "EUR"}], "sources": src, "as_of": "2026-11-20"}, "cds_indices") == [],
      "'ccy mn': the row may name its own currency")
check(K.violations({"results": [{"notional_mn": 1250}], "sources": src, "as_of": "2026-11-20"}, "cds_indices") != [], "'ccy mn' with no currency anywhere")
check(K.violations({"results": [{"notional_mn": 1250}], "sources": src, "as_of": "2026-11-20", "currency": "yen"}, "cds_indices") != [],
      "a currency is a code (JPY), not a word")
os.environ["TERMINAL_STRICT_CONTRACT"] = "0"                 # production: the amounts are blanked with a note, never shown unlabelled
try:
    blanked = K.finish({"results": hist_rows, "sources": src, "as_of": "2026-11-04"}, "earnings_history")
finally:
    os.environ["TERMINAL_STRICT_CONTRACT"] = "1"
check(blanked["results"][0]["eps_est"] is None and blanked["results"][0]["eps_actual"] is None and blanked["results"][0]["surprise"] == 0.1579
      and blanked["notes"] == [K.CCY_BLANK_NOTE] and hist_rows[0]["eps_est"] == 56.38,
      "production: amounts blank, the unit-free surprise kept, a note said, the view's own rows untouched")

# through dispatch (strict): earnings_history and consensus for Toyota (JPY), Apple (USD) and an ADR-like split (EPS in USD,
# revenue in TWD -> revenue blank with a note). Figures are Yahoo's 2026-11-04 Toyota report: est 56.38, actual 65.28 (yen).
def _model(sym, ccy):
    return {"symbol": sym, "currency": ccy, "latest": {}, "history": [
        {"date": "2026-11-04", "est": 56.38 if sym == "7203.T" else 1.77, "actual": 65.28 if sym == "7203.T" else 1.85,
         "surprise": 0.1579 if sym == "7203.T" else 0.0452, "move_2d": None, "move_end": None}]}


def _est(eps_ccy, rev_ccy):
    return {"eps": [{"period": "0q", "mean": 79.66, "low": 75.01, "high": 84.3, "number_of_analysts": 2, "currency": eps_ccy}],
            "rev": [{"period": "0q", "mean": 13.83e12, "low": 13.13e12, "high": 14.41e12, "number_of_analysts": 8, "currency": rev_ccy}],
            "trend": [], "revs": [], "_failed": []}


saved_em, saved_er = C._earn_model, C._est_raw
try:
    for sym, ccy in (("7203.T", "JPY"), ("AAPL", "USD")):
        C._cache.clear()
        C._earn_model = lambda s, **_kw: _model(s, ccy)
        out = C.dispatch("earnings_history", {"symbol": sym})
        r0 = out["results"][0]
        check(out["currency"] == ccy and out["convention"] == f"EPS in {ccy} per share" and r0["eps_est"] == _model(sym, ccy)["history"][0]["est"],
              f"earnings_history {sym}: amounts as given, in {ccy}, said on the card")
        C._est_raw = lambda s, **_kw: _est(ccy, ccy)
        out = C.dispatch("consensus", {"symbol": sym})
        r0 = out["results"][0]
        check(out["currency"] == ccy and out["convention"] == f"EPS per share and revenue in {ccy}" and r0["eps"] == 79.66 and r0["rev"] == 13.83e12,
              f"consensus {sym}: EPS and revenue in {ccy}")
    C._cache.clear()
    C._est_raw = lambda s, **_kw: _est("USD", "TWD")
    out = C.dispatch("consensus", {"symbol": "TSM"})
    r0 = out["results"][0]
    check(out["currency"] == "USD" and r0["eps"] == 79.66 and r0["rev"] is None and r0["rev_low"] is None and r0["rev_high"] is None
          and r0["rev_n"] == 8 and any("in TWD, not USD" in n for n in out["notes"]), "consensus: revenue in another currency is blank with a note")
    C._cache.clear()
    C._est_raw = lambda s, **_kw: _est(None, None)
    try:
        C.dispatch("consensus", {"symbol": "XYZ"})
        named = "answered"
    except K.ContractViolation as e:
        named = str(e)
    check("names no currency" in named, f"consensus with no currency named fails the contract (strict) ({named[:80]})")
    C._cache.clear()
    C._earn_model = lambda s, **_kw: _model(s, None)
    try:
        C.dispatch("earnings_history", {"symbol": "7203.T"})
        named = "answered"
    except K.ContractViolation as e:
        named = str(e)
    check("names no currency" in named, "earnings_history with no currency named fails the contract (strict)")
finally:
    C._earn_model, C._est_raw = saved_em, saved_er
    C._cache.clear()

# ---- P3-05: every route taking a symbol, company or currency declares its coverage, and the tests hold it to it
import inspect  # noqa: E402
import re  # noqa: E402

from _common import NON_US_CCY, ROOT  # noqa: E402

takes = {n for n in C.ROUTES if set(inspect.signature(K.target(C.ROUTES[n])).parameters) & set(K.COVERAGE_PARAMS)}
check(takes == set(K.COVERAGE), f"coverage declared for exactly the routes that take a symbol, company or currency: "
      f"undeclared {sorted(takes - set(K.COVERAGE))}, stale {sorted(set(K.COVERAGE) - takes)}")
check(set(K.COVERAGE.values()) <= {"global", "us_only", "set"}, "coverage is global, us_only or set")
markets, refusals = {}, {}
for f in sorted((ROOT / "scripts" / "tests").glob("test_*.py")):
    txt = f.read_text(encoding="utf-8")
    for r, m in re.findall(r"\bcovered\(\s*[\"']([a-z0-9_]+)[\"']\s*,\s*[\"']([a-z]+)[\"']", txt):
        markets.setdefault(r, set()).add(m)
    for r, k in re.findall(r"\brefused\(\s*[\"']([a-z0-9_]+)[\"'].*?[\"'](not_covered|input)[\"']\s*\)", txt):
        refusals.setdefault(r, set()).add(k)
lacking = {r: sorted(markets.get(r, set())) for r, c in K.COVERAGE.items() if c == "global" and not ("us" in markets.get(r, set()) and markets.get(r, set()) & set(NON_US_CCY))}
check(not lacking, f"global routes need a U.S. and a non-U.S. (EUR, GBp or JPY) fixture: {lacking}")
want = {"us_only": "not_covered", "set": "input"}
unrefused = sorted(r for r, c in K.COVERAGE.items() if c in want and want[c] not in refusals.get(r, set()))
check(not unrefused, f"us_only routes need a not_covered test for a listing elsewhere, set routes an input test: {unrefused}")
check(not (set(markets) - {r for r, c in K.COVERAGE.items() if c == "global"}), f"covered() only on global routes: {sorted(set(markets) - {r for r, c in K.COVERAGE.items() if c == 'global'})}")

done("test_contract")
