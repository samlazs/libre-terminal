"""Bounded process-lifetime state, monotonic in-memory TTLs, and the short negative cache against failure stampedes."""

import threading
import time

from _common import check, done
from _fixtures import fault, no_retry_delays

import composite as C
import news

# ---- every process-lifetime map is capped (10,000 distinct keys in, at most 2,000 kept, newest kept)
maps = {"_backoff": C._backoff, "_STALE_SINCE": C._STALE_SINCE, "_neg": C._neg,
        "news._FEED_CACHE": news._FEED_CACHE, "news._FEED_FAIL": news._FEED_FAIL, "news._FEED_ERR": news._FEED_ERR}
for name, m in maps.items():
    for i in range(10000):
        m[("k", i)] = (float(i), "x")
    check(len(m) == C.STATE_CAP == 2000 and ("k", 9999) in m and ("k", 0) not in m, f"{name} keeps the newest 2,000 of 10,000")
    m.clear()
revalidating = C._HIST_REVALIDATING
for i in range(10000):
    revalidating.add(("SYM", i))
check(len(revalidating) == 2000 and ("SYM", 9999) in revalidating and ("SYM", 0) not in revalidating, "_HIST_REVALIDATING keeps the newest 2,000")
revalidating.discard(("SYM", 9999))
check(("SYM", 9999) not in revalidating and len(revalidating) == 1999, "discard works")
revalidating.clear()
lru = C.LRUDict(3)
lru["a"], lru["b"], lru["c"] = 1, 2, 3
lru.get("a")                                     # a read keeps an entry alive
lru["d"] = 4
check(list(lru) == ["c", "a", "d"], f"least recently used goes first ({list(lru)})")

# ---- in-memory TTLs run on the monotonic clock: a wall-clock jump neither expires nor revives an entry
calls = [0]


def fn():
    calls[0] += 1
    return {"v": calls[0]}


C._cache.clear()
C.cached(("mono",), 60, fn)
check(abs(C._cache[("mono",)][0] - time.monotonic()) < 5, "entries are stamped with time.monotonic()")
real_time = time.time
try:
    time.time = lambda: real_time() + 86400 * 3          # the wall clock jumps 3 days (sleep/resume, clock change)
    check(C.cached(("mono",), 60, fn) == {"v": 1} and calls[0] == 1, "a wall-clock jump does not expire a fresh entry")
finally:
    time.time = real_time
C._cache[("mono",)] = (time.monotonic() - 61, C._cache[("mono",)][1])
check(C.cached(("mono",), 60, fn) == {"v": 2}, "an entry older than its TTL on the monotonic clock is fetched again")
wall = real_time() - 3600
check(abs((time.monotonic() - C._mono(wall)) - 3600) < 2, "_mono converts a disk (wall) stamp to the monotonic clock")

# ---- negative cache: 20 concurrent callers of a failing key -> one upstream call
URL = "https://api.nasdaq.com/api/quote/ZZZZQ9/info"         # Nasdaq is never retried: one call is one request
upstream = [0]
errors = []


def failing():
    upstream[0] += 1
    time.sleep(0.2)                                  # the others queue on the per-key lock meanwhile
    return C.http_json(URL, timeout=5)


def caller():
    try:
        C.cached(("neg", "ZZZZQ9"), 600, failing)
    except C.CompositeError as e:
        errors.append(e)


with no_retry_delays(), fault("http404") as rec:
    ts = [threading.Thread(target=caller) for _ in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check(upstream[0] == 1 and rec.calls == 1, f"20 concurrent callers -> 1 upstream call ({upstream[0]} fn, {rec.calls} requests)")
    check(len(errors) == 20 and all(e.kind == "not_covered" for e in errors), "every caller gets the same typed failure")
    left = C._neg[("neg", "ZZZZQ9")][0] - time.monotonic()
    check(9 <= left <= 10, f"held for the default 10 s ({left:.1f})")
    C._neg[("neg", "ZZZZQ9")] = (time.monotonic() - 1,) + C._neg[("neg", "ZZZZQ9")][1:]      # 10 s later
    try:
        C.cached(("neg", "ZZZZQ9"), 600, failing)
    except C.CompositeError:
        pass
    check(upstream[0] == 2 and rec.calls == 2, "after the window the source is asked again")

with no_retry_delays(), fault("http429", retry_after=45):
    try:
        C.cached(("neg", "RL"), 600, lambda: C.http_json(URL.replace("ZZZZQ9", "RL"), timeout=5))
    except C.CompositeError:
        pass
    neg = C._neg[("neg", "RL")]
    check(neg[1] == "rate_limited" and 44 <= neg[0] - time.monotonic() <= 45 and neg[3] == 45.0, "a rate limit is held for its retry_after (45 s)")
    try:
        C.cached(("neg", "RL"), 600, lambda: 1 / 0)
        e = None
    except C.CompositeError as exc:
        e = exc
    check(e is not None and e.kind == "rate_limited" and e.retry_after == 45.0, "callers during the hold get rate_limited with retry_after")

C._neg.clear()
C._neg[("ok",)] = (time.monotonic() - 1, "upstream", "old", None)
check(C.cached(("ok",), 60, lambda: 5) == 5 and ("ok",) not in C._neg, "a success clears the remembered failure")
out = C.reset_runtime_caches()
check("negative" in out and len(C._neg) == 0, "a service restart clears the negative cache too")

done("test_cache")
