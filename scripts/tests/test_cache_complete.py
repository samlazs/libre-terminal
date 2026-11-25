"""Cache completeness contract: a partial answer is never saved or held as complete, and a failure is never cached as
empty data. Faults go through the one outbound seam (_fixtures); views answer through composite.dispatch."""

import json
import time

from _common import check, done
from _fixtures import fault, no_retry_delays, serve

import composite as C
import bonds
import valuation

SRC = [C.src("Test", "two test sources", "https://example.test/")]
ANSWER = {"results": [{"date": "2026-01-02", "close": 100.0}]}


def two_sources(**_):
    """A view built from two sources, cached on disk for an hour: one may fail (partial), both may fail (error)."""
    def run():
        out, first = {}, None
        for k in ("a", "b"):
            try:
                out[k] = C.http_json(f"https://example.test/{k}", timeout=5)
            except C.CompositeError as e:
                C.partial_from(e, f"source {k}")
                first = first or e
        if not out:
            raise first
        return {"results": [{"source": k, "date": "2026-01-02"} for k in out], "sources": SRC}
    return C.cache(("two",), 3600, run, disk="t_two")


C.ROUTES["t_two"] = two_sources
DISK = C.CACHE_DIR / "t_two.json"


def reset():
    C.reset_runtime_caches()
    DISK.unlink(missing_ok=True)


# ---- (a) 1 of 2 sources fails: status=partial naming it, nothing on disk, asked again after the hold
reset()
with no_retry_delays(), fault("partial", ok=ANSWER, match=r"/b$") as rec:
    out = C.dispatch("t_two", {})
    check(out["status"] == "partial" and [r["source"] for r in out["results"]] == ["a"], f"partial answer served: {out['status']}")
    check(any("source b" in n for n in out["notes"]) and not any("source a" in n for n in out["notes"]), f"note names what is missing: {out['notes']}")
    check(not DISK.exists(), "a partial answer is never written to disk")
    n = rec.calls
    again = C.dispatch("t_two", {})
    check(rec.calls == n and again["status"] == "partial", "within the hold: served from memory, still reported partial")
    hit = C._cache[("disk", "t_two")]
    C._cache[("disk", "t_two")] = (hit[0] - C.PARTIAL_HOLD - 1, hit[1])          # the hold is over
    C.dispatch("t_two", {})
    check(rec.calls > n, f"after the hold the sources are asked again ({n} -> {rec.calls} requests)")

with serve({r"example\.test": ANSWER}):
    C._cache.pop(("disk", "t_two"), None)
    full = C.dispatch("t_two", {})
check(full["status"] == "ok" and "notes" not in full and DISK.exists(), "complete answer: status ok and saved to disk")

# ---- (b) every source fails, a saved copy exists: status=stale with its age in the notes
rec_ = json.loads(DISK.read_text(encoding="utf-8"))
rec_["t"] -= 5 * 3600                                                           # saved 5 h ago: past its 1 h ttl
DISK.write_text(json.dumps(rec_), encoding="utf-8")
C.reset_runtime_caches()
with no_retry_delays(), fault("http5xx"):
    st = C.dispatch("t_two", {})
check(st["status"] == "stale" and any("5 h old" in n for n in st["notes"]), f"stale copy with its age: {st['status']} {st.get('notes')}")
check([r["source"] for r in st["results"]] == ["a", "b"], "the whole saved answer is served")

# ---- (c) every source fails, no saved copy: an upstream error, nothing cached as empty
reset()
with no_retry_delays(), fault("http5xx"):
    try:
        C.dispatch("t_two", {})
        err = None
    except C.CompositeError as e:
        err = e
check(err is not None and C.kind_of(err) == "upstream", f"error.kind=upstream ({err and C.kind_of(err)})")
check(not DISK.exists(), "nothing saved")
with no_retry_delays(), fault("http5xx"):
    try:
        C.dispatch("t_two", {})
        err2 = None
    except C.CompositeError as e:
        err2 = e
check(err2 is not None and C.kind_of(err2) == "upstream", "during the backoff the error keeps its kind")
reset()

# ---- nesting: a cache built from a partial inner answer is partial itself (memory and disk)
calls = {"inner": 0, "outer": 0}


def inner():
    calls["inner"] += 1
    return C.Partial([1, 2], ["inner source"])


def outer():
    calls["outer"] += 1
    return {"v": C.cache(("inner",), 900, inner)}


got = C._collect(lambda: C.cache(("outer",), 900, outer, disk="t_outer"))
check(isinstance(got, C.Partial) and got.missing == ["inner source"] and got.value == {"v": [1, 2]}, f"partial flows outward: {got!r}")
check(not (C.CACHE_DIR / "t_outer.json").exists(), "outer partial not on disk either")
got2 = C._collect(lambda: C.cache(("outer",), 900, outer, disk="t_outer"))
check(calls == {"inner": 1, "outer": 1} and isinstance(got2, C.Partial), "a memory hit of the outer answer is still reported partial")
check(C.cache(("plain",), 900, lambda: [3]) == [3] and not isinstance(C._collect(lambda: C.cache(("plain",), 900, lambda: [4])), C.Partial),
      "a complete answer is plain")
check(C.Partial([], ["x", "x", ""]).missing == ["x"], "missing names are unique and non-empty")
quiet = C._collect(lambda: C.cache(("quiet",), 900, lambda: C.Partial({"a": 1})))
check(quiet == {"a": 1} and isinstance(C._cache[("quiet",)][1], C.Partial), "an unnamed Partial is held short without being reported")
C.reset_runtime_caches()

# ---- partial_from: a source with nothing for this input is a complete answer, a failure is not
notes = []
tok = C._PARTIAL_NOTES.set(notes)
try:
    C.partial_from(C.CompositeError("No data for ZZZZ", kind="not_covered"), "nothing")
    C.partial_from(C.CompositeError("Bad parameters", kind="input"), "bad")
    C.partial_from(C.CompositeError("The data service answered HTTP 502", kind="upstream"), "upstream part")
    C.partial_from(C.CompositeError("HTTP 429 Too Many Requests"), "throttled part")
finally:
    C._PARTIAL_NOTES.reset(tok)
check(notes == ["upstream part", "throttled part"], f"only failures are partial: {notes}")

# ---- _safe: a source failure becomes None (named when asked); a defect is not swallowed
check(C._safe(lambda: (_ for _ in ()).throw(C.CompositeError("down", kind="upstream"))) is None, "_safe: CompositeError -> None")
try:
    C._safe(lambda: 1 / 0)
    defect = None
except ZeroDivisionError as e:
    defect = e
check(defect is not None, "_safe lets a defect (ZeroDivisionError) through to the 500-with-ref path")
try:
    C._safe(lambda x: x["missing"], {})
    defect = None
except KeyError as e:
    defect = e
check(defect is not None, "_safe lets KeyError through")
named = C._collect(lambda: C._safe(lambda: (_ for _ in ()).throw(C.CompositeError("down", kind="timeout")), missing="OECD real GDP"))
check(isinstance(named, C.Partial) and named.missing == ["OECD real GDP"], f"_safe(missing=...) marks the answer partial: {named!r}")

# ---- maturity wall: a full SEC outage is an error (never 'no maturities' for 12 h); one tag down is partial
saved_api = C.api


def sec_down(base, path, **kw):
    raise C.CompositeError("The data service answered HTTP 503", kind="unavailable")


def sec_one_down(base, path, fact=None, **kw):
    if fact == bonds.WALL[2][0]:
        raise C.CompositeError("The data service did not answer in time", kind="timeout")
    return [{"period_ending": "2025-09-27", "reported_date": "2025-10-31", "value": 1e9, "form": "10-K", "cik": "1", "fiscal_period": "FY"}]


wall_file = C.CACHE_DIR / (C.disk_name("debtwall_", "TSTW") + ".json")
try:
    C.api = sec_down
    try:
        bonds.maturity_wall("TSTW")
        err = None
    except C.CompositeError as e:
        err = e
    check(err is not None and C.kind_of(err) == "unavailable" and not wall_file.exists(), f"SEC outage: error, nothing saved ({err})")
    C.reset_runtime_caches()
    C.api = sec_one_down                       # one of the tags times out: partial, the tag named, nothing saved
    res = C._collect(lambda: bonds.maturity_wall("TSTW"))
    check(isinstance(res, C.Partial) and res.missing == [f"SEC {bonds.WALL[2][0]}"], f"the missing tag is named: {res!r}")
    check(not wall_file.exists(), "one tag down: nothing saved")
    ys = res.value["data"][0]["y"] if isinstance(res, C.Partial) else []
    check(len(ys) == len(bonds.WALL) and ys[2] is None and ys[0] == 1.0, f"answered tags drawn ($1bn), the missing one blank: {ys}")
finally:
    C.api = saved_api
C.reset_runtime_caches()

# ---- splits: a failed fetch is never saved as 'no splits'; the saved list is served instead
split_file = C.CACHE_DIR / (C.disk_name("splits_", "TSTS") + ".json")
split_file.unlink(missing_ok=True)


def splits_down(base, path, **kw):
    raise C.CompositeError("HTTP 429 Too Many Requests", kind="rate_limited")


def splits_none(base, path, **kw):
    raise C.CompositeError("No data found for TSTS", kind="not_covered")


def splits_ok(base, path, **kw):
    return [{"date": "2024-06-10", "numerator": 10.0, "denominator": 1.0}]


try:
    C.api = splits_down
    try:
        valuation._splits("TSTS")
        err = None
    except C.CompositeError as e:
        err = e
    check(err is not None and not split_file.exists(), "split fetch fails, no copy: an error, nothing saved")
    C.reset_runtime_caches()
    C.api = splits_ok
    check(valuation._splits("TSTS") == [("2024-06-10", 10.0)], "splits fetched and saved")
    rec_ = json.loads(split_file.read_text(encoding="utf-8"))
    rec_["t"] -= 8 * 86400
    split_file.write_text(json.dumps(rec_), encoding="utf-8")
    C.reset_runtime_caches()
    C.api = splits_down
    stale_notes = []
    tok = C._STALE_NOTES.set(stale_notes)
    try:
        got = valuation._splits("TSTS")
    finally:
        C._STALE_NOTES.reset(tok)
    check([tuple(x) for x in got] == [("2024-06-10", 10.0)] and stale_notes, "split fetch fails: the saved list is served, flagged stale")
    C.reset_runtime_caches()
    split_file.unlink(missing_ok=True)
    C.api = splits_none
    check(valuation._splits("TSTS") == [] and split_file.exists(), "no splits for this symbol (not covered): a complete, saved answer")
finally:
    C.api = saved_api
    split_file.unlink(missing_ok=True)
    C.reset_runtime_caches()

C.ROUTES.pop("t_two", None)
time.sleep(0)
done("test_cache_complete")
