"""The one outbound client (composite/core/http.py fetch + PROVIDERS): policies per provider, 429 cooldowns that send
nothing, Retry-After cap, per-provider concurrency, Google News pacing, no-retry providers, outage gate."""

import threading
import time

from _common import ROOT, check, done
from _fixtures import FakeResponse, fault, no_retry_delays, serve

import composite as C
from composite.core import http as H

# ---- provider of a URL
for url, want in ((C.V5 + "/equity/price/quote?symbol=AAPL&provider=yfinance", "backend"),
                  (C.V5 + "/economy/gdp/real?provider=oecd&country=japan", "oecd"),
                  (C.V5 + "/oecd/nominal", "oecd"), ("https://sdmx.oecd.org/public/rest/data/x", "oecd"),
                  ("https://api.nasdaq.com/api/calendar/earnings", "nasdaq"), (C.V5 + "/equity/calendar/earnings?provider=nasdaq", "nasdaq"),
                  ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", "fred"), ("https://www.multpl.com/s-p-500-pe-ratio/table/by-month", "multpl"),
                  ("https://news.google.com/rss/search?q=x", "google_news"), ("https://www.ecb.europa.eu/rss/press.html", "default")):
    check(H.provider_of(url) == want, f"provider_of {url[:60]} -> {want}")
FIELDS = {"timeout", "attempts", "retry_on", "max_concurrency", "min_interval", "cooldown_on_429", "down_for", "ua"}
check(all(set(p) - {"backend_gate"} == FIELDS for p in H.PROVIDERS.values()), "every policy states every field")
check([k for k, p in H.PROVIDERS.items() if "backend_gate" in p] == ["nasdaq"], "only Nasdaq declares the data service's own gate")
check(H.PROVIDERS["oecd"]["attempts"] == 1 and H.PROVIDERS["nasdaq"]["attempts"] == 1, "OECD and Nasdaq are never retried")
check(429 not in H.PROVIDERS["fred"]["retry_on"] and not any(429 in p["retry_on"] for p in H.PROVIDERS.values()), "no provider retries a 429")
check(H.PROVIDERS["multpl"]["attempts"] == 1 and H.PROVIDERS["multpl"]["down_for"] == 120,
      "multpl: one try, and an unreachable site is skipped for 2 min (a view asks for several series)")
check(H.PROVIDERS["google_news"]["min_interval"] >= 2.5 and H.PROVIDERS["google_news"]["max_concurrency"] == 1, "Google News: one at a time, 2.5 s apart")
import fred_public as fp  # noqa: E402
check(H.PROVIDERS["fred"]["timeout"] == fp.TIMEOUTS and H.PROVIDERS["fred"]["ua"] == fp.UA and H.PROVIDERS["fred"]["down_for"] == fp.DOWN_FOR,
      "fred policy matches fred_public's documented timeouts, UA and outage window")

# ---- the only socket-opening call in terminal/ is in core/http.py
hits = [f"{p.relative_to(ROOT)}:{n}" for p in (ROOT / "terminal").rglob("*.py") for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "urlopen(" in line]
check(hits and all(h.replace("\\", "/").startswith("terminal/composite/core/http.py:") for h in hits), f"urlopen( only in core/http.py ({hits})")

with no_retry_delays():
    # ---- 429: cooldown honours Retry-After, sends nothing while it lasts, is scoped to the upstream provider
    yf = C.V5 + "/equity/price/quote?symbol=AAPL&provider=yfinance"
    with fault("http429", retry_after=30) as rec:
        for _ in range(3):
            try:
                C.http_json(yf, timeout=5)
            except C.CompositeError as e:
                last = e
        check(rec.calls == 1, f"one 429, then nothing sent during the cooldown ({rec.calls} requests)")
        check(last.kind == "rate_limited" and 28 <= last.retry_after <= 30 and "~" in str(last), f"cooldown answer: rate_limited, ~30 s left ({last.retry_after})")
        try:
            C.http_json(C.V5 + "/equity/price/quote?symbol=AAPL&provider=fmp", timeout=5)
        except C.CompositeError:
            pass
        check(rec.calls == 2, "another upstream provider behind the backend is still asked")
    with fault("http429", retry_after=5000):
        try:
            C.fetch("https://api.nasdaq.com/api/x", parse="json")
        except C.CompositeError:
            pass
        left = H._cool["nasdaq"] - time.monotonic()
    check(590 <= left <= 600, f"Retry-After 5000 s is capped at 600 s ({left:.0f})")
    with fault("http429"):
        try:
            C.fetch("https://api.nasdaq.com/api/x", parse="json")
        except C.CompositeError:
            pass
        left = H._cool["nasdaq"] - time.monotonic()
    check(110 <= left <= 120, f"no Retry-After: the policy's 120 s ({left:.0f})")
    with fault("http429", retry_after=30) as rec:
        for u in ("https://a.example.org/x", "https://a.example.org/y", "https://b.example.org/x"):
            try:
                C._download(u, timeout=5)
            except C.CompositeError:
                pass
    check(rec.calls == 2, "unknown hosts cool down one host at a time")

    # ---- no retry for OECD / Nasdaq, direct or through the backend
    for u in (C.V5 + "/economy/gdp/real?provider=oecd", "https://sdmx.oecd.org/public/rest/data/x", C.V5 + "/equity/calendar/earnings?provider=nasdaq",
              "https://api.nasdaq.com/api/x"):
        with fault("http5xx") as rec:
            try:
                C.fetch(u, parse="json")
            except C.CompositeError:
                pass
        check(rec.calls == 1, f"never retried: {u[:50]}")

    # ---- outage gate (fred): after every attempt failed, nothing is sent for down_for seconds
    with fault("timeout") as rec:
        for sid in ("DGS10", "DGS2"):
            try:
                fp.observations(sid)
            except fp.FredPublicError as e:
                err = e
    check(rec.calls == 2 and "did not answer" in str(err) and not err.missing, f"fred: two attempts, then the outage gate ({rec.calls})")
    with fault("http404") as rec:
        try:
            fp.observations("NOSUCH")
        except fp.FredPublicError as e:
            err = e
    check(rec.calls == 1 and err.missing, "fred 404: no such series, one request, no outage gate")

# ---- a timeout cut short by a view's own deadline is not an outage (gatekeeper P1-a). Hand-computed: set_deadline(2.5)
# leaves time_left = 2.5 - DEADLINE_RESERVE_S (1.0) = 1.5 s, so FRED's first attempt gets ~1.49 s instead of its own 15 s;
# that timeout says nothing about FRED, so the next view (FRED healthy) must be answered, not refused for 120 s.
from composite.core import contract as K  # noqa: E402
import http.client  # noqa: E402

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
seen = []


def times_out(req, timeout, context=None):
    seen.append(timeout)
    raise TimeoutError("timed out")


def healthy(req, timeout, context=None):
    seen.append(timeout)
    return FakeResponse(b"DATE,DGS10\n2026-11-24,4.1\n")


def in_view(seconds, fn):
    tok = K.set_deadline(seconds)
    try:
        return fn()
    finally:
        K._DEADLINE.reset(tok)


def outcome(fn):
    try:
        return fn()
    except C.CompositeError as e:
        return e


saved = C._transport
try:
    with no_retry_delays():
        for pname, url in (("fred", FRED_URL), ("multpl", "https://www.multpl.com/s-p-500-pe-ratio/table/by-month")):
            C.reset_outbound()
            seen.clear()
            C._transport = times_out
            err = outcome(lambda: in_view(2.5, lambda: C.fetch(url, provider=pname, parse="text")))
            check(isinstance(err, C.CompositeError) and err.kind == "timeout" and "time budget" in str(err),
                  f"{pname}: a view-deadline timeout is a timeout naming the budget ({err})")
            check(len(seen) == H.PROVIDERS[pname]["attempts"] and all(1.0 <= t <= 1.5 for t in seen),
                  f"{pname}: each attempt got the view's ~1.5 s, not the policy's own timeout ({seen})")
            check(pname not in H._down, f"{pname}: not marked down by a timeout the view cut short")
            seen.clear()
            C._transport = healthy
            got = outcome(lambda: C.fetch(url, provider=pname, parse="text"))
            check(got == "DATE,DGS10\n2026-11-24,4.1\n" and len(seen) == 1, f"{pname}: the next view reaches the source at once ({got!r}, {seen})")
        # the same timeout with the provider's full per-attempt timeouts (15 s, then 25 s) is an outage: 2 attempts, then 120 s down
        C.reset_outbound()
        seen.clear()
        C._transport = times_out
        err = outcome(lambda: C.fetch(FRED_URL, provider="fred", parse="text"))
        check(seen == [15, 25] and err.kind == "timeout" and "time budget" not in str(err), f"fred, no view deadline: 15 s then 25 s ({seen})")
        left = H._down["fred"][0] - time.monotonic() if "fred" in H._down else 0
        check(118 <= left <= 120, f"fred: full timeouts mark it down for down_for = 120 s ({left:.0f})")
        seen.clear()
        C._transport = healthy
        err = outcome(lambda: C.fetch(FRED_URL, provider="fred", parse="text"))
        check(isinstance(err, C.CompositeError) and err.kind == "upstream" and seen == [], "fred down: refused without a request")

        # ---- a body the source cut off (IncompleteRead) is a network failure under the retry policy, never 'internal' (P2)
        def cut_then_ok(req, timeout, context=None):
            seen.append(timeout)
            if len(seen) == 1:
                raise http.client.IncompleteRead(b"DATE,DG", 40)
            return FakeResponse(b"DATE,DGS10\n2026-11-24,4.1\n")
        C.reset_outbound()
        seen.clear()
        C._transport = cut_then_ok
        got = outcome(lambda: C.fetch(FRED_URL, provider="fred", parse="text"))
        check(got == "DATE,DGS10\n2026-11-24,4.1\n" and seen == [15, 25], f"fred: a cut-off body is retried like a network error ({seen})")
        C.reset_outbound()

        def cut(req, timeout, context=None):
            raise http.client.IncompleteRead(b"<tab", 900)
        C._transport = cut
        err = outcome(lambda: C.fetch("https://www.multpl.com/x", provider="multpl", parse="text"))
        check(isinstance(err, C.CompositeError) and err.kind == "upstream" and err.transient and "cut its answer short" in str(err),
              f"multpl (no retry): a cut-off body is upstream and transient, not internal ({getattr(err, 'kind', err)})")

        # ---- review R5: a URL the client refuses to send (InvalidURL, an HTTPException) is our own error: internal, one
        # call, and FRED is not marked down for other views
        def bad_url(req, timeout, context=None):
            seen.append(timeout)
            raise http.client.InvalidURL("URL can't contain control characters")
        C.reset_outbound()
        seen.clear()
        C._transport = bad_url
        err = outcome(lambda: C.fetch(FRED_URL, provider="fred", parse="text"))
        check(isinstance(err, C.CompositeError) and err.kind == "internal" and len(seen) == 1 and "fred" not in H._down,
              f"fred: InvalidURL is internal, not retried, not an outage ({getattr(err, 'kind', err)}, {seen}, {list(H._down)})")
    # ---- review R3: with the real retry delay (0.4 s) FRED's own budget is 15 + 0.4 + 25 = 40.4 s, so attempt 2 still gets
    # its full 25 s: a real outage is never called "this request's time budget ran out", and _down keeps the outage's text
    # A fake clock for http.py only: each attempt uses up its whole timeout and the pause really passes (15 + 0.4 s), as
    # a real outage does; the old budget (15 + 25 = 40 s) then left attempt 2 only 24.6 s and called it "time budget"
    import types  # noqa: E402
    clock = [1000.0]

    def times_out_slowly(req, timeout, context=None):
        seen.append(round(timeout, 3))
        clock[0] += timeout
        raise TimeoutError("timed out")
    saved_delays, saved_time = H._RETRY_DELAYS, H.time
    try:
        H._RETRY_DELAYS = (0.4,)
        H.time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        C.reset_outbound()
        seen.clear()
        C._transport = times_out_slowly
        err = outcome(lambda: C.fetch(FRED_URL, provider="fred", parse="text"))
        H.time = saved_time
        down_txt = (H._down.get("fred") or (0, ""))[1]
        check(seen == [15, 25] and "time budget" not in str(err) and down_txt == "fred.stlouisfed.org did not answer in time.",
              f"fred, real delays, no view deadline: 15 s then 25 s, an outage said as such ({seen}, {err}, {down_txt!r})")
        # a 503 (outage), then attempt 2 cut by the view's deadline (2.5 - 1.0 reserve = 1.5 s; 0.4 s delay -> ~1.1 s):
        # the view gets the budget message, but FRED is marked down with the 503 that showed the outage
        import urllib.error  # noqa: E402

        def s503_then_slow(req, timeout, context=None):
            seen.append(timeout)
            if len(seen) == 1:
                raise urllib.error.HTTPError(FRED_URL, 503, "Service Unavailable", {}, None)
            raise TimeoutError("timed out")
        C.reset_outbound()
        seen.clear()
        C._transport = s503_then_slow
        err = outcome(lambda: in_view(2.5, lambda: C.fetch(FRED_URL, provider="fred", parse="text")))
        down_txt = (H._down.get("fred") or (0, ""))[1]
        check(len(seen) == 2 and "time budget" in str(err) and down_txt == "fred.stlouisfed.org is not reachable (HTTP Error 503: Service Unavailable).",
              f"fred 503 then a view-cut timeout: _down holds the 503, not the budget text ({seen}, {down_txt!r})")
    finally:
        H._RETRY_DELAYS, H.time = saved_delays, saved_time
finally:
    C._transport = saved
    C.reset_outbound()

# ---- concurrency: 20 fetches to a provider capped at 4 -> at most 4 in flight
state = {"now": 0, "peak": 0}
lock = threading.Lock()


def slow(req, timeout, context=None):
    with lock:
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
    time.sleep(0.05)
    with lock:
        state["now"] -= 1
    return FakeResponse(b"DATE,DGS10\n2026-01-02,4.18\n")


saved = C._transport
C._transport = slow
C.reset_outbound()
try:
    ts = [threading.Thread(target=lambda: C.fetch("https://fred.stlouisfed.org/graph/fredgraph.csv?id=X", provider="fred", parse="text")) for _ in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
finally:
    C._transport = saved
check(state["peak"] == 4, f"peak in flight for max_concurrency 4 is 4 ({state['peak']})")

# ---- pacing: one Google News query at a time, min_interval between one's end and the next one's start
spans = []


def timed(req, timeout, context=None):
    t0 = time.monotonic()
    time.sleep(0.02)
    spans.append((t0, time.monotonic()))
    return FakeResponse(b"<rss/>")


saved_gap = H.PROVIDERS["google_news"]["min_interval"]
H.PROVIDERS["google_news"]["min_interval"] = 0.25           # same rule, shorter gap to keep the test quick
C._transport = timed
C.reset_outbound()
try:
    ts = [threading.Thread(target=lambda: C.fetch("https://news.google.com/rss/search?q=x", parse="bytes")) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
finally:
    C._transport = saved
    H.PROVIDERS["google_news"]["min_interval"] = saved_gap
    C.reset_outbound()
spans.sort()
gaps = [b[0] - a[1] for a, b in zip(spans, spans[1:])]
check(len(spans) == 3 and all(g >= 0.24 for g in gaps), f"Google News requests spaced by min_interval ({[round(g, 3) for g in gaps]})")

# ---- the modules that used to open their own connections go through fetch (UA and parse mode from the policy)
with serve({r"fred\.stlouisfed\.org": "DATE,DGS10\n2026-01-02,4.18\n2026-01-05,.\n", r"multpl\.com": "<table></table>",
            r"banks\.data\.fdic\.gov": {"data": [{"data": {"NAME": "X"}}]}, r"news\.google\.com": "<rss/>"}) as rec:
    rows = fp.observations("DGS10")
    import banks  # noqa: E402
    import multpl  # noqa: E402
    import news  # noqa: E402
    out = banks._fdic("/failures", limit=1)
    try:
        multpl.fetch_series("pe_month")
    except C.CompositeError as e:
        merr = e
    news._get("https://news.google.com/rss/search?q=x", gnews=True)
check(rows == [("2026-01-02", 4.18), ("2026-01-05", None)], "fred_public through fetch parses the CSV")
check(out == [{"NAME": "X"}], "banks through fetch(parse='json')")
check("no table rows" in str(merr) and merr.kind == "upstream", "multpl through fetch: an empty table is an upstream error")
check(sum("fred" in u for u in rec.urls) == 1 and sum("multpl" in u for u in rec.urls) == 1 and sum("news.google" in u for u in rec.urls) == 1,
      "each module made exactly one request through the seam")

done("test_http")
