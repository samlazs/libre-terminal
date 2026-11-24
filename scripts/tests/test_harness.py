"""Self-tests for the shared fixture and fault-injection harness (scripts/tests/_fixtures.py) and the probe tool."""

import json
import math
import subprocess
import sys

from _common import ROOT, check, done
from _fixtures import FIXTURES, KINDS, fault, fixture, no_retry_delays, serve

import composite as C

V5_URL = C.V5 + "/equity/price/historical?symbol=AAPL"
EXT_URL = "https://example.org/data.csv"
NASDAQ_URL = "https://api.nasdaq.com/api/quote/AAPL/info"


def raises(fn):
    """Run fn; return the CompositeError it raised (None when it returned)."""
    try:
        fn()
    except C.CompositeError as e:
        return e
    return None


# ---- fixture(): sanitized files load, multi-currency inputs exist
aapl = fixture("v5", "historical_aapl")
sap = fixture("v5", "historical_sap_de")
check([r["close"] for r in aapl["results"]] == [251.0, 254.0, 250.0], "fixture loads the AAPL closes in order")
check(sap["currency"] == "EUR" and len(sap["results"]) == 3, "non-US fixture declares its currency")
check(fixture("fred", "dgs10_tail")["observations"][1][1] == ".", "FRED fixture keeps the '.' missing-value marker")
for f in FIXTURES.rglob("*.json"):
    raw = f.read_bytes()
    check(not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw, f"{f.name}: UTF-8 without BOM, LF endings")
    low = raw.lower()
    check(b"@" not in raw and b"users\\" not in low and b"/home/" not in low and b"apikey" not in low and b"api_key" not in low,
          f"{f.name}: no e-mail, profile path or key material")

with no_retry_delays():
    # ---- timeout: retried (3 attempts) on a normal URL, once on a no-retry provider, transient error
    with fault("timeout") as rec:
        e = raises(lambda: C.http_json(V5_URL, timeout=5))
    check(e is not None and e.transient and "did not answer in time" in str(e), "timeout -> transient 'did not answer in time'")
    check(rec.calls == 3, f"timeout is retried twice (3 attempts), saw {rec.calls}")
    with fault("timeout") as rec:
        e = raises(lambda: C.http_json(NASDAQ_URL, timeout=5))
    check(e is not None and rec.calls == 1, "a no-retry provider (Nasdaq) is asked once")
    with fault("timeout") as rec:
        e = raises(lambda: C._download(EXT_URL, timeout=5))
    check(e is not None and e.transient and rec.calls == 2 and "example.org" in str(e), "_download timeout: 2 attempts, names the host")

    # ---- refused
    with fault("refused") as rec:
        e = raises(lambda: C.http_json(V5_URL, timeout=5))
    check(e is not None and e.transient and "OpenBB V5 backend is not reachable" in str(e) and rec.calls == 3,
          "refused backend -> transient 'not reachable' after 3 attempts")

    # ---- 429: never retried, transient, Retry-After visible to the caller's transport
    with fault("http429", retry_after=30) as rec:
        e = raises(lambda: C.http_json(V5_URL, timeout=5))
    check(e is not None and e.transient and rec.calls == 1 and "Too many requests" in str(e), "429 -> one attempt, transient, detail kept")
    from _fixtures import make_answer
    import urllib.request
    try:
        make_answer("http429", retry_after=30)(urllib.request.Request(EXT_URL), 5)
        check(False, "http429 fault must raise")
    except Exception as exc:  # noqa: BLE001
        check(getattr(exc, "code", None) == 429 and exc.headers.get("Retry-After") == "30", "429 carries Retry-After: 30")
    with fault("http429") as rec:
        e = raises(lambda: C._download(EXT_URL, timeout=5))
    check(e is not None and e.transient and rec.calls == 1, "_download 429: one attempt, transient")

    # ---- 5xx: 503 retried and transient; 500 not retried and not transient
    with fault("http5xx") as rec:
        e = raises(lambda: C.http_json(V5_URL, timeout=5))
    check(e is not None and e.transient and rec.calls == 3 and "HTTP 503" in str(e), "503 -> retried, transient, 'HTTP 503'")
    with fault("http5xx", code=500, body={"detail": "Unexpected error."}) as rec:
        e = raises(lambda: C.http_json(V5_URL, timeout=5))
    check(e is not None and e.kind == "upstream" and rec.calls == 1 and str(e) == "Unexpected error.", "500 -> one attempt, upstream, backend detail kept")

    # ---- 404
    with fault("http404") as rec:
        e = raises(lambda: C.http_json(V5_URL, timeout=5))
    check(e is not None and not e.transient and rec.calls == 1 and str(e) == "not found", "404 -> not transient, one attempt")

    # ---- empty body means 'no data', not an error
    with fault("empty_body", status=204) as rec:
        out = C.http_json(V5_URL, timeout=5)
    check(out == {"results": []} and rec.calls == 1, "empty body -> {'results': []}")
    with fault("empty_body"):
        check(C._download(EXT_URL, timeout=5) == b"", "_download empty body -> b''")

    # ---- HTML body: a transient CompositeError, never a raw ValueError
    with fault("html_body") as rec:
        try:
            C.http_json(V5_URL, timeout=5)
            e = None
        except C.CompositeError as exc:
            e = exc
        except ValueError:
            e = "raw ValueError"
    check(isinstance(e, C.CompositeError) and e.transient and "HTML page" in str(e), "HTML body -> transient 'answered with an HTML page'")
    with serve({r"historical": b'{"results": [1, 2'}):
        e = raises(lambda: C.http_json(V5_URL, timeout=5))
    check(e is not None and "not JSON" in str(e), "truncated JSON -> 'a reply that is not JSON'")

    # ---- NaN / Infinity never become floats
    with fault("nan_values"):
        out = C.http_json(V5_URL, timeout=5)
    rows = out["results"]
    check(rows[0]["close"] is None and rows[0]["volume"] is None and rows[1]["volume"] is None, "NaN/Infinity/-Infinity -> None")
    check(rows[1]["close"] == 101.5 and not any(isinstance(v, float) and not math.isfinite(v) for r in rows for v in r.values()),
          "finite values kept, no non-finite float left")

    # ---- partial: matching URLs fail, the rest answer
    with fault("partial", ok=aapl, match=r"symbol=SAP") as rec:
        good = C.http_json(V5_URL, timeout=5)
        e = raises(lambda: C.http_json(C.V5 + "/equity/price/historical?symbol=SAP.DE", timeout=5))
    check(good["results"][1]["close"] == 254.0 and e is not None and e.transient, "partial: AAPL answers, SAP.DE fails (503)")
    with fault("partial") as rec:
        first = C.http_json(EXT_URL + "?a", timeout=5)
        e = raises(lambda: C.http_json(EXT_URL + "?b", timeout=5))
    check(first["results"] and e is not None, "partial default: every second request fails")

    # ---- serve(): canned answers by URL, unmatched -> 404, fault names usable as answers
    with serve({r"symbol=AAPL": aapl, r"symbol=SAP\.DE": sap, r"symbol=BAD": "http5xx"}) as rec:
        a = C.api(C.V5, "equity/price/historical", symbol="AAPL")
        s = C.api_full(C.V5, "equity/price/historical", symbol="SAP.DE")
        e404 = raises(lambda: C.http_json(C.V5 + "/other", timeout=5))
        e5 = raises(lambda: C.http_json(C.V5 + "/x?symbol=BAD", timeout=5))
    check(a[-1]["close"] == 250.0 and s["currency"] == "EUR", "serve(): routes by URL regex (US and EUR inputs)")
    check(e404 is not None and not e404.transient and e5 is not None and e5.transient, "serve(): unmatched -> 404, fault name -> fault")
    check(any("SAP.DE" in u for u in rec.urls), "Recorder keeps the requested URLs")

# The seam is restored after every block
import composite.core.http as H  # noqa: E402
check(C._transport is H._transport, "composite._transport restored after the fault blocks")
check(H._RETRY_DELAYS == (0.4, 1.0), "retry delays restored")
check(set(KINDS) == {"timeout", "refused", "http429", "http5xx", "http404", "empty_body", "html_body", "nan_values", "partial"},
      "every fault kind from the manifest exists")

# ---- probe.py: stdlib only, parses its CLI, refuses to run with nothing to do
probe = ROOT / "scripts" / "probe.py"
src = probe.read_text(encoding="utf-8")
imports = {ln.split()[1].split(".")[0] for ln in src.splitlines() if ln.startswith(("import ", "from "))}
check(imports <= {"__future__", "argparse", "concurrent", "hashlib", "json", "os", "sys", "time", "urllib", "pathlib", "threading"},
      f"probe.py imports only the standard library ({sorted(imports)})")
check("PUT" not in src.replace("never sends PUT", ""), "probe.py has no PUT request")
r = subprocess.run([sys.executable, str(probe), "--help"], capture_output=True, text=True, timeout=30)
check(r.returncode == 0 and all(f in r.stdout for f in ("--view", "--inputs", "--concurrent", "--assert-envelope", "--all-routes", "--defaults", "--backend", "--url")),
      "probe.py --help lists every flag")
r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, timeout=30)
check(r.returncode == 2, "probe.py with nothing to probe exits 2")
sys.path.insert(0, str(ROOT / "scripts"))
import probe as P  # noqa: E402
check(P.parse_inputs(["symbol=AAPL", "symbol=SAP.DE,range=1y"]) == [{"symbol": "AAPL"}, {"symbol": "SAP.DE", "range": "1y"}], "--inputs parsing")
check(P.envelope_violations(200, {"results": [], "sources": [{"name": "Yahoo Finance"}]}) == [], "200 with sources is a valid envelope")
check(P.envelope_violations(200, {"results": []}) == ["200 without sources"], "200 without sources is a violation")
check(P.envelope_violations(502, {"detail": "x", "sources": []}) == [], "502 with detail is a valid error envelope")
check(P.envelope_violations(500, {"oops": 1}) == ["500 without detail"], "an error body without detail is a violation")
check(P.envelope_violations(200, "<html>") == ["200 body is not a JSON object"], "a non-object body is a violation")
check(P.percentile([1, 2, 3, 4, 100], 50) == 3 and P.percentile([1, 2, 3, 4, 100], 95) == 100 and P.percentile([], 50) is None, "percentiles")
check(P.redact_url("/api/v1/x?symbol=A&api_key=SECRET&token=T") == "/api/v1/x?symbol=A&api_key=***&token=***", "key material redacted from printed URLs")
check(json.loads(json.dumps(P.summary_line("snapshot", {200: 3, 502: 1}, [10.0, 20.0, 30.0, 40.0], 0)))
      .startswith("snapshot"), "summary line printable")

done("test_harness")
