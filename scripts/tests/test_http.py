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
check(all(set(p) == {"timeout", "attempts", "retry_on", "max_concurrency", "min_interval", "cooldown_on_429", "down_for", "ua"}
          for p in H.PROVIDERS.values()), "every policy states every field")
check(H.PROVIDERS["oecd"]["attempts"] == 1 and H.PROVIDERS["nasdaq"]["attempts"] == 1, "OECD and Nasdaq are never retried")
check(429 not in H.PROVIDERS["fred"]["retry_on"] and not any(429 in p["retry_on"] for p in H.PROVIDERS.values()), "no provider retries a 429")
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
