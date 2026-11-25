"""Keyless FRED (HANDOFF P1): FRED's public CSV download (terminal/fred_public.py), the backend's keyless answer
(scripts/run_backend.py fred_keyless_answer) and the terminal's order: backend first, public download without a key."""

import importlib.util
import urllib.error

from _common import ROOT, check, done

import composite as c
import fred_public as fp

# ---- parsing and URLs
txt = "observation_date,DGS10\n2026-11-13,4.12\n2026-11-14,.\n2026-11-16,\nnot a date,1\n2026-11-17,4.2\n"
check(fp.parse_csv(txt) == [("2026-11-13", 4.12), ("2026-11-14", None), ("2026-11-16", None), ("2026-11-17", 4.2)], "CSV: '.' and empty cells are missing values; other lines skipped")
u = fp.csv_url("DGS10", "2025-01-01", "2026-01-01", "pc1", "m", "eop")
check(all(x in u for x in ("id=DGS10", "cosd=2025-01-01", "coed=2026-01-01", "transformation=pc1", "fq=Monthly", "fam=eop")), "URL carries dates, transformation, frequency, aggregation")
check("transformation" not in fp.csv_url("X", units="lin") and "fam" not in fp.csv_url("X"), "levels and native frequency add nothing")
page = ('<meta property="og:title" content="10-Year Treasury &amp; More"><span class="series-meta-value-units">Percent</span>, </div> '
        '<div class="default-text">Not Seasonally Adjusted</div><span class="series-meta-value-frequency">Daily</span>')
check(fp.parse_meta(page) == {"title": "10-Year Treasury & More", "units": "Percent", "frequency": "Daily", "seasonal_adjustment": "Not Seasonally Adjusted"},
      "series page: title, units, frequency, seasonal adjustment")

# ---- one retry with a longer wait, then the outage is remembered (no request per card for two minutes)
calls = []
import urllib.request as _ur  # noqa: E402
saved_open = _ur.urlopen


def timeout_open(req, timeout=None, **_kw):
    calls.append(timeout)
    raise urllib.error.URLError("timed out")


def missing_open(req, timeout=None, **_kw):
    calls.append(timeout)
    raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)


try:
    _ur.urlopen = timeout_open
    fp.reset()
    err = None
    try:
        fp.observations("DGS10")
    except fp.FredPublicError as e:
        err = e
    check(calls == list(fp.TIMEOUTS) and err and not err.missing and "did not answer" in str(err), "two tries (15 s, then 25 s), then a plain message")
    calls.clear()
    try:
        fp.observations("DGS2")
    except fp.FredPublicError:
        pass
    check(calls == [], "during the outage window nothing is requested")
    fp.reset()
    _ur.urlopen = missing_open
    try:
        fp.observations("NOSUCH")
    except fp.FredPublicError as e:
        err = e
    check(calls == [15] and err.missing, "404: no such series, no retry")
    calls.clear()
    try:
        fp.observations("bad id!")
    except fp.FredPublicError as e:
        err = e
    check(calls == [] and err.missing, "an invalid id is refused before any request")
    try:
        fp.observations("DGS10", units="zzz")
    except fp.FredPublicError as e:
        err = e
    check(calls == [] and "transformation" in str(err), "a transformation FRED's download lacks is refused")
finally:
    _ur.urlopen = saved_open
    fp.reset()

# ---- the backend's keyless answer (what the FRED API would have returned)
spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)
seen = []


def obs(sid, start, end, units, freq, agg):
    seen.append((sid, start, end, units, freq, agg))
    return [("2026-01-01", 1.5), ("2026-02-01", None), ("2026-03-01", 2.0)]


j = rb.fred_keyless_answer("/fred/series/observations", {"series_id": "X", "api_key": "", "observation_start": "2026-01-01", "frequency": "m",
                                                          "aggregation_method": "eop", "sort_order": "desc", "limit": "2"}, obs, lambda s: {})
check([o["date"] for o in j["observations"]] == ["2026-03-01", "2026-02-01"] and j["observations"][1]["value"] == "." and j["count"] == 2,
      "observations newest first, limited, a missing value as '.' (the API's own convention)")
check(seen[-1] == ("X", "2026-01-01", None, None, "m", "eop"), "start, frequency and aggregation passed to the download")
check(rb.fred_keyless_answer("/fred/series", {"series_id": "X", "api_key": ""}, obs, lambda s: {"title": "T", "units": "U"}) == {"seriess": [{"title": "T", "units": "U", "id": "X"}]},
      "series metadata from the series page")
check(rb.fred_keyless_answer("/fred/series", {"series_id": "X", "api_key": ""}, obs, lambda s: {"title": None}) == {}, "unreadable page: nothing (retried next time)")
for path, q in (("/fred/series/observations", {"series_id": "X", "api_key": "", "vintage_dates": "2020-01-01"}), ("/fred/series/search", {"search_text": "gdp", "api_key": ""})):
    try:
        rb.fred_keyless_answer(path, q, obs, lambda s: {})
        msg = ""
    except Exception as e:  # noqa: BLE001  (OpenBBError)
        msg = str(e)
    check("Missing credential 'fred_api_key'" in msg, f"{path} {sorted(q)}: what the download cannot serve asks for the free key")

# ---- the terminal: backend first, public download when the backend has no key
saved = c._fred, c._fred_csv
order = []


def no_key(ids, start):
    raise c.CompositeError("Missing credential 'fred_api_key'.")


def csv_ok(sid, start):
    order.append(sid)
    return [[start, 1.0]]


try:
    c._fred, c._fred_csv = no_key, csv_ok
    got, meta = c._fred_any(["A", "B", "C"], "2026-01-01")
    check(order[0] == "A" and sorted(order) == ["A", "B", "C"] and got["B"] == [["2026-01-01", 1.0]] and meta == {}, "no key: every series from the download, one probe first")

    def csv_down(sid, start):
        order.append(sid)
        raise c.CompositeError("FRED's public download did not answer (HTTP 503).")
    order.clear()
    c._fred_csv = csv_down
    try:
        c._fred_any(["A", "B"], "2026-01-01")
        msg = ""
    except c.CompositeError as e:
        msg = str(e)
    check(order == ["A"] and "did not answer" in msg, "download down: one probe, its message (not 'missing key')")
    c._fred = lambda ids, start: (_ for _ in ()).throw(c.CompositeError("FRED API error 500"))
    try:
        c._fred_any(["A"], "2026-01-01")
        msg = ""
    except c.CompositeError as e:
        msg = str(e)
    check("500" in msg, "backend failing for another reason, download down too: the backend's own error")
    order.clear()
    c._fred, c._fred_csv = (lambda ids, start: ({i: [["2026-01-01", 2.0]] for i in ids}, {})), csv_ok
    got, _m = c._fred_any(["A"], "2026-01-01")
    check(order == [] and got["A"] == [["2026-01-01", 2.0]], "with a key the backend answers; the download is not touched")
    c._fred, c._fred_csv = no_key, csv_down
    try:
        c._fred_points("A", "2026-01-01")
        msg = ""
    except c.CompositeError as e:
        msg = str(e)
    check("Setup-Keys" in msg, "a single series with neither: says how to add the free key")
finally:
    c._fred, c._fred_csv = saved

# ---- P2-04: a 429 from the public download is a rate limit: one request, no retry, a cooldown that honours Retry-After
from _fixtures import fault, no_retry_delays  # noqa: E402

for ra, want in ((45, 45.0), (5000, 600.0)):
    fp.reset()
    with no_retry_delays(), fault("http429", retry_after=ra) as rec:
        try:
            fp.observations("DGS10", start="2026-01-01")
            e1 = None
        except fp.FredPublicError as e:
            e1 = e
        n = rec.calls
        try:
            fp.observations("DGS2", start="2026-01-01")
            e2 = None
        except fp.FredPublicError as e:
            e2 = e
        from composite.core import http as _H  # noqa: E402
        left = _H._cool.get(_H._cool_key(fp.CSV_URL, "fred"), 0) - c.time.monotonic()      # (the harness clears it on exit)
    check(e1 is not None and e1.kind == "rate_limited" and n == 1, f"429 (Retry-After {ra}): rate_limited after one request, no retry ({n})")
    check("limiting requests" in str(e1) and "did not answer" not in str(e1), f"called a rate limit, not an outage: {e1}")
    check(rec.calls == 1 and e2 is not None and e2.kind == "rate_limited" and e2.retry_after and e2.retry_after <= want,
          "during the cooldown the next series asks nothing and is rate limited too")
    check(want - 2 <= left <= want, f"cooldown honours Retry-After {ra} (capped at 600): {left:.0f} s")
fp.reset()
saved_obs = fp.observations
try:
    fp.observations = lambda sid, start=None: (_ for _ in ()).throw(fp.FredPublicError("limited", kind="rate_limited", retry_after=40.0))
    c.reset_runtime_caches()
    try:
        c._fred_csv("DGS10", "2026-01-01")
        ce = None
    except c.CompositeError as e:
        ce = e
    check(ce is not None and c.kind_of(ce) == "rate_limited" and ce.retry_after == 40.0, "the terminal keeps the kind and retry hint")
finally:
    fp.observations = saved_obs
    c.reset_runtime_caches()

done("test_fred")
