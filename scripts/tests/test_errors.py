"""Typed error kinds from source to card: each fault kind of the harness maps to exactly one error.kind, at the HTTP
layer and through the real UI server; Retry-After reaches the answer; the server classifies untyped errors once."""

import email.utils
import http.client
import json
import socket
import threading
import time

from _common import check, done
from _fixtures import KINDS, fault, no_retry_delays

import composite as C
import server
from composite.core import contract as K
from composite.core import http as H

server.log = lambda _msg: None

# ---- the table: harness fault kind -> error.kind (None: not an error, the view answers with what arrived)
TABLE = {
    "timeout": "timeout", "hang": "timeout", "refused": "upstream", "http429": "rate_limited", "http5xx": "upstream", "http404": "not_covered",
    "html_body": "upstream", "empty_body": None, "nan_values": None, "partial": None,
}
check(set(TABLE) == set(KINDS), "the table covers every fault kind of the harness")
STATUS = {"timeout": 504, "upstream": 502, "rate_limited": 429, "not_covered": 404, None: 200}
URL = C.V5 + "/equity/price/historical?symbol=AAPL"


def through_layer(kind, fn):
    try:
        with fault(kind, retry_after=30) if kind == "http429" else fault(kind, match=r"symbol=AAPL|a.csv") if kind == "partial" else fault(kind):
            fn()
    except C.CompositeError as e:
        return e
    return None


with no_retry_delays():
    for fk, want in TABLE.items():
        t = 1 if fk == "hang" else 5                          # a hanging source costs the whole timeout: keep it short
        for name, fn in (("http_json", lambda: C.http_json(URL, timeout=t)), ("_download", lambda: C._download("https://example.org/a.csv", timeout=t))):
            if name == "_download" and fk in ("nan_values", "html_body", "empty_body"):
                continue                                  # _download returns raw bytes; parsing is the caller's
            e = through_layer(fk, fn)
            got = e.kind if e else None
            exp = "upstream" if fk == "partial" else want      # partial: this URL is the failing one
            check(got == exp, f"{fk} via {name} -> {exp} (got {got})")
            if e:
                check(e.transient == (got in K.RETRYABLE_KINDS), f"{fk} via {name}: transient follows the kind")
                check(K.kind_of(e) == got, f"{fk}: the server keeps the layer's kind")

# ---- Retry-After: delta-seconds and HTTP-date
check(H._retry_after({"Retry-After": "30"}) == 30.0 and H._retry_after({}) is None and H._retry_after({"Retry-After": "soon"}) is None,
      "Retry-After seconds / absent / unreadable")
future = email.utils.formatdate(time.time() + 120, usegmt=True)
ra = H._retry_after({"Retry-After": future})
check(ra is not None and 110 <= ra <= 121, f"Retry-After HTTP date -> ~120 s ({ra})")
with no_retry_delays(), fault("http429", retry_after=45):
    try:
        C.http_json(URL, timeout=5)
        e = None
    except C.CompositeError as exc:
        e = exc
check(e is not None and e.kind == "rate_limited" and e.retry_after == 45.0, "429 carries retry_after 45 from the header")

# ---- untyped errors (raised by views with only a message) are classified once on the server
for msg, kind in (("Click a bond's CUSIP in the table or the curve above (or type a CUSIP in the settings).", "input"),
                  ("Enter a symbol to load this card.", "input"),
                  ("SHEL.L was not found in the Cboe options directory.", "not_covered"),
                  ("SEC company facts cover U.S. filers only; SAP.DE has no XBRL facts.", "not_covered"),
                  ("Yahoo Finance has nothing for 'ZZZZQ9'. Check the ticker.", "not_covered"),
                  ("OECD rate limit reached; try again in ~600 s", "rate_limited"),
                  ("Nasdaq did not answer in time.", "timeout"),
                  ("FRED changed its download format.", "upstream")):
    check(K.kind_of(C.CompositeError(msg)) == kind, f"kind_of({msg[:40]!r}) -> {kind}")
check(K.kind_of(C.CompositeError("SAP.DE not found", kind="upstream")) == "upstream", "an explicit kind always wins")
check(C.CompositeError("x", transient=True).transient and not C.CompositeError("x", kind="input", transient=True).transient,
      "transient is derived from the kind when there is one")

# ---- through the real UI server: status and error.kind per fault
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv = server.ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def get(path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = json.loads(r.read())
    c.close()
    return r.status, body


def stub_view(symbol="AAPL", **_):
    rows = C.api(C.V5, "equity/price/historical", symbol=symbol)
    return {"results": rows if isinstance(rows, list) else [], "sources": [C.yahoo_source([symbol])]}


saved = dict(C.ROUTES)
C.ROUTES["snapshot"] = stub_view
C._cache.clear()
try:
    budgets = K.ROUTE_BUDGETS["views"]
    saved_budget, budgets["snapshot"] = budgets["snapshot"], 3      # a hanging source costs the view's whole budget: keep it short
    with no_retry_delays():
        for fk, want in TABLE.items():
            kw = {"retry_after": 30} if fk == "http429" else {"match": "symbol=ZZZZQ9"} if fk == "partial" else {}
            t0 = time.monotonic()
            with fault(fk, **kw):
                status, body = get("/x/snapshot?symbol=AAPL")
            check(time.monotonic() - t0 < 3.5, f"{fk}: answered within the view's 3 s budget ({time.monotonic() - t0:.1f} s)")
            check(status == STATUS[want], f"{fk}: /x/ status {STATUS[want]} (got {status})")
            if want:
                check(body["error"]["kind"] == want and body["error"]["retryable"] == (want in K.RETRYABLE_KINDS) and body["sources"],
                      f"{fk}: error.kind {want} with sources ({body.get('error')})")
            else:
                check("error" not in body and K.violations(body) == [], f"{fk}: a 200 inside the envelope")
            if fk == "http429":
                check(body["error"].get("retry_after") == 30, "429 -> error.retry_after 30")
        with fault("partial", match="symbol=ZZZZQ9"):
            status, body = get("/x/snapshot?symbol=ZZZZQ9")
        check(status == 502 and body["error"]["kind"] == "upstream", "partial: the failing symbol answers upstream")
    budgets["snapshot"] = saved_budget
    C.ROUTES["bond_pricer"] = lambda **_: (_ for _ in ()).throw(C.CompositeError("Click a bond's CUSIP in the table or the curve above."))
    status, body = get("/x/bond_pricer")
    check(status == 400 and body["error"]["kind"] == "input" and body["user_message"] is True, "a prompt for input answers 400 input")
finally:
    C.ROUTES.clear()
    C.ROUTES.update(saved)
srv.shutdown()

done("test_errors")
