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

done("test_contract")
