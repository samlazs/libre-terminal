"""In-memory and disk TTL caches, backoff, stale-note context, and curve store."""

from __future__ import annotations

import concurrent.futures as _cf
import contextvars
import datetime as dt
import hashlib
import json
import math
import os
import re
import threading
from collections import OrderedDict
import time
import local_paths

import composite as C

CACHE_DIR = local_paths.CACHE_DIR

# ---------------------------------------------------------------- caching
# In-memory entries are stamped with time.monotonic() (immune to sleep/resume and clock changes); wall time
# (time.time()) is kept only for disk records and the ages shown to the user. _mono() converts a disk stamp.
STATE_CAP = 2000                       # most entries any process-lifetime map below may hold (least recently used go first)
NEG_TTL = 10.0                         # seconds a failed fetch is answered from memory instead of being run again
PARTIAL_HOLD = 60.0                    # seconds an incomplete answer (Partial) is held before the sources are asked again


# ---------------------------------------------------------------- completeness: an answer with parts missing
# A cached fn returns Partial(value, missing=[...]) when some of its sources failed, or calls partial_from(e, what) where it
# swallows a failure. A Partial is never written to disk, is held at most partial_ttl seconds, and every request it serves
# answers status=partial with a note naming what is missing. A cache whose fn used a partial answer is partial itself
# (the inner notes are collected while fn runs), so nesting caches never turns an incomplete answer into a complete one.
class Partial:
    """value with parts missing. missing names them for the reader ("FRED DGS10", "Germany"); an empty list only holds
    the answer short without reporting it (a background refresh is already on its way)."""

    __slots__ = ("value", "missing")

    def __init__(self, value, missing=()):
        self.value = value
        self.missing = list(dict.fromkeys(str(m) for m in (missing or ()) if m))

    def __repr__(self):
        return f"Partial(missing={self.missing!r})"


_PARTIAL_NOTES = contextvars.ContextVar("partial_notes", default=None)


def note_partial(missing):
    """Record, for the request (or the cache fetch) running now, that these parts of its answer are missing."""
    notes = C._PARTIAL_NOTES.get()
    if notes is not None:
        for m in missing or ():
            if m and m not in notes:
                notes.append(str(m))


def partial_from(e, what):
    """Where a failure is swallowed and the answer goes on without `what`: mark the answer partial, unless the source
    simply has nothing for this input (not_covered / input), which is a complete answer. Returns None."""
    from .contract import kind_of
    if kind_of(e) not in ("not_covered", "input"):
        C.note_partial([what])


def partial_text(missing):
    names = ", ".join(missing[:6]) + (f" and {len(missing) - 6} more" if len(missing) > 6 else "")
    return f"Partial answer: no data from {names} just now (asked again within a minute)"


def _collect(fn):
    """Run fn and return its answer as a Partial when fn, or any cache it used, reported a missing part."""
    inner = []
    token = C._PARTIAL_NOTES.set(inner)
    try:
        val = fn()
    finally:
        C._PARTIAL_NOTES.reset(token)
    if isinstance(val, Partial):
        if inner:
            val.missing = list(dict.fromkeys(val.missing + inner))
        return val
    return Partial(val, inner) if inner else val


def _serve(val):
    """A cached value as callers see it: a Partial is unwrapped and its missing parts noted on the request."""
    if isinstance(val, Partial):
        C.note_partial(val.missing)
        return val.value
    return val


class LRUDict(OrderedDict):
    """A dict that keeps at most `cap` entries, dropping the least recently written or read."""

    def __init__(self, cap=STATE_CAP):
        super().__init__()
        self.cap = cap

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.cap:
            self.popitem(last=False)

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
            return super().__getitem__(key)
        return default


class LRUSet:
    """A set that keeps at most `cap` members (the oldest go first)."""

    def __init__(self, cap=STATE_CAP):
        self._d = LRUDict(cap)

    def add(self, item):
        self._d[item] = True

    def discard(self, item):
        self._d.pop(item, None)

    def clear(self):
        self._d.clear()

    def __contains__(self, item):
        return item in self._d

    def __len__(self):
        return len(self._d)

    def __iter__(self):
        return iter(list(self._d))


def _mono(wall_t):
    """A wall-clock stamp (disk record) as a monotonic one, so it can sit in the in-memory cache."""
    return time.monotonic() - max(0.0, time.time() - float(wall_t))


_cache: dict = {}
_neg = LRUDict()                       # key -> (monotonic time until which the failure is repeated, kind, message, retry_after)

_locks: dict = {}

_guard = threading.Lock()


def _neg_check(key):
    """Raise the remembered failure of `key` while it is fresh (no upstream call)."""
    with C._guard:
        neg = C._neg.get(key)
    if neg and time.monotonic() < neg[0]:
        left = neg[0] - time.monotonic()
        raise C.CompositeError(neg[2], kind=neg[1], retry_after=neg[3] if neg[3] is not None else left)


def _neg_store(key, e):
    """Remember a failed fetch for NEG_TTL seconds (a rate limit: for as long as the source asked)."""
    from .contract import kind_of
    kind = kind_of(e)
    ra = getattr(e, "retry_after", None)
    hold = float(ra) if kind == "rate_limited" and ra else C.NEG_TTL
    with C._guard:
        C._neg[key] = (time.monotonic() + max(0.0, min(hold, 600.0)), kind, str(e), ra)


def cache(key, ttl, fn, *, disk=False, partial_ttl=None):
    """The one caching primitive. fn() returns a value or Partial(value, missing=[...]).

    ttl      seconds an answer is good (disk: a callable value -> seconds may choose it per answer)
    disk     False: memory only. True or a file name: memory -> local/cache/<name>.json -> fetch, and when the fetch
             fails the last saved copy is served however old (status=stale with its age)
    A Partial is never written to disk and is held at most partial_ttl seconds (PARTIAL_HOLD by default); a failure
    (CompositeError) is remembered NEG_TTL seconds in memory, or backs off the source when on disk."""
    if disk:
        name = disk if isinstance(disk, str) else C.disk_name("c_", repr(key))
        return C.cached_disk(name, ttl, fn, partial_ttl=partial_ttl)
    return C.cached(key, ttl, fn, partial_ttl=partial_ttl)


def cached(key, ttl, fn, partial_ttl=None):
    """fn() once per key per ttl seconds; concurrent callers wait for the one fetch. A failure (CompositeError) is
    remembered for NEG_TTL seconds, so callers queued on a failing key do not each re-run it. A Partial answer is held
    partial_ttl seconds (PARTIAL_HOLD by default) and reported on every request it serves."""
    hold = C.PARTIAL_HOLD if partial_ttl is None else partial_ttl

    def fresh(hit):                              # a Partial is good for at most `hold` seconds after it was fetched
        return hit and time.monotonic() - hit[0] < (min(ttl, hold) if isinstance(hit[1], Partial) else ttl)

    with C._guard:
        hit = C._cache.get(key)
        if fresh(hit):
            return _serve(hit[1])
        lock = C._locks.setdefault(key, threading.Lock())
    _neg_check(key)
    with lock:                                   # one fetch per key at a time
        with C._guard:
            hit = C._cache.get(key)
            if fresh(hit):
                return _serve(hit[1])
        _neg_check(key)
        try:
            val = _collect(fn)
        except C.CompositeError as e:
            _neg_store(key, e)
            raise
        with C._guard:
            C._neg.pop(key, None)
            C._cache[key] = (time.monotonic(), val)
            if len(C._cache) > 1200:
                for k in sorted(C._cache, key=lambda k: C._cache[k][0])[:200]:
                    held = C._locks.get(k)
                    if held is not None and held.locked():
                        continue                 # someone is refreshing it: dropping its lock would allow a second fetch
                    C._cache.pop(k, None)
                    C._locks.pop(k, None)
        return _serve(val)


_backoff = LRUDict()                   # dataset name -> (monotonic time the fetch may run again, message[, error kind])


def disk_name(prefix, ident, suffix=""):
    """A safe, unique cache file name: readable start plus a hash of the full identifier (two long id lists that
    share their first 100 characters must not share a file)."""
    readable = re.sub(r"[^A-Za-z0-9]+", "_", ident)[:80]
    digest = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{readable}_{digest}" + (f"_{suffix}" if suffix else "")


# ---------------------------------------------------------------- trust signal: a saved copy served because the source failed
# (HANDOFF P2). cached_disk records which of its datasets are being served from an old copy; dispatch() collects the ones a
# view touched during its own request (a context variable: the thread pools below copy the context into their workers)
# and answers {"stale": "..."}, which the card footer shows.
_STALE_SINCE = LRUDict()                      # dataset name -> wall time of the saved copy now served after a failed fetch

_STALE_NOTES = contextvars.ContextVar("stale_notes", default=None)
_SWR_NOTES = contextvars.ContextVar("swr_notes", default=None)


class _CtxPool(_cf.ThreadPoolExecutor):
    def submit(self, fn, /, *args, **kwargs):
        return super().submit(contextvars.copy_context().run, fn, *args, **kwargs)


class _CfShim:
    """concurrent.futures as this module uses it, with pools that carry the request context into their threads."""
    ThreadPoolExecutor = _CtxPool

    def __getattr__(self, name):
        return getattr(_cf, name)


cf = _CfShim()


def _note_stale(name):
    t = C._STALE_SINCE.get(name)
    notes = C._STALE_NOTES.get()
    if t and notes is not None:
        notes.append(t)


def _note_stale_ts(t):
    notes = C._STALE_NOTES.get()
    if t and notes is not None:
        notes.append(t)


def _note_swr_ts(t):
    notes = C._SWR_NOTES.get()
    if t and notes is not None:
        notes.append(t)


def _age_text(t):
    hours = max(0.0, time.time() - t) / 3600
    return f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} days"


def stale_text(times):
    """'Source not answering: showing the copy saved 2026-11-18 14:05 (19 h old)' for the oldest copy used."""
    t = min(times)
    return f"Source not answering: showing the copy saved {C.mtl_time(t)} ({_age_text(t)} old)"


def cached_text(times):
    """'Cached 2026-11-18 14:05 (10 min old) · refreshing' for normal SWR serving while background refresh runs."""
    t = min(times)
    return f"Cached {C.mtl_time(t)} ({_age_text(t)} old) · refreshing"


def mtl_time(t):
    """Epoch seconds -> 'YYYY-MM-DD HH:MM' in Montreal time (every time shown is Montreal time, whatever the machine's zone)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.fromtimestamp(t, ZoneInfo("America/Toronto")).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001 - Python builds without tzdata still render UTC instead of 500
        return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def cached_disk(name, ttl, fn, partial_ttl=None):
    """Memory -> disk -> fetch. If the fetch fails, serve the last saved copy however old it is.

    Used for sources that change slowly and limit how often they may be called (OECD, BIS). ttl may be a callable
    (value -> seconds) when how long an answer stays good depends on the answer (an empty day vs. a full one). A Partial
    answer is not written to disk and is held in memory partial_ttl seconds (PARTIAL_HOLD by default).
    """
    ttl_of = ttl if callable(ttl) else (lambda _v: ttl)
    mem_ttl = 1800.0 if callable(ttl) else min(ttl, 1800)
    key = ("disk", name)

    def run():
        path = C.CACHE_DIR / f"{name}.json"
        saved = None
        now = time.time()
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            good = float(ttl_of(saved["v"]))
            if now - saved["t"] < good:
                with C._guard:
                    C._STALE_SINCE.pop(name, None)
                return saved["v"], min(mem_ttl, max(1.0, good - (now - saved["t"])))
        except (OSError, ValueError, KeyError, TypeError):
            saved = None
        with C._guard:
            blocked = C._backoff.get(name)
        mnow = time.monotonic()
        if blocked and mnow < blocked[0]:
            if saved is not None:
                with C._guard:
                    C._STALE_SINCE[name] = saved["t"]
                return saved["v"], min(mem_ttl, max(0.0, blocked[0] - mnow))
            raise C.CompositeError(blocked[1], kind=blocked[2] if len(blocked) > 2 else None)
        try:
            val = _collect(fn)
        except C.CompositeError as e:
            from .contract import kind_of
            msg = str(e)
            low = msg.lower()
            if (
                "429" in msg
                or "rate limit" in low
                or "too many requests" in low
                or "quota" in low
                or "oecd" in name.lower()
                or "oecd" in low
            ):
                wait = 600
            elif getattr(e, "transient", False):
                wait = 30 if saved is not None else 10
            else:
                wait = 120 if saved is not None else 30
            until = time.monotonic() + wait
            with C._guard:
                C._backoff[name] = (until, msg, kind_of(e))
                if saved is not None:
                    C._STALE_SINCE[name] = saved["t"]
            if saved is not None:
                return saved["v"], min(mem_ttl, float(wait))
            raise
        with C._guard:
            C._backoff.pop(name, None)
            C._STALE_SINCE.pop(name, None)
        if isinstance(val, Partial):             # never saved as complete; asked again after partial_ttl
            hold = C.PARTIAL_HOLD if partial_ttl is None else partial_ttl
            return val, min(mem_ttl, max(1.0, float(hold)))
        C._write_json(path, {"t": time.time(), "v": val})
        return val, min(mem_ttl, float(ttl_of(val)))

    now = time.monotonic()
    with C._guard:
        hit = C._cache.get(key)
        if hit and now - hit[0] < mem_ttl:
            val = hit[1]
            hit_ok = True
        else:
            hit_ok = False
            lock = C._locks.setdefault(key, threading.Lock())
    if hit_ok:
        C._note_stale(name)
        return _serve(val)

    with lock:
        now = time.monotonic()
        with C._guard:
            hit = C._cache.get(key)
            if hit and now - hit[0] < mem_ttl:
                val = hit[1]
                hit_ok = True
            else:
                hit_ok = False
        if hit_ok:
            C._note_stale(name)
            return _serve(val)
        val, eff_ttl = run()
        stored_at = time.monotonic() - max(0.0, float(mem_ttl) - float(eff_ttl))
        with C._guard:
            C._cache[key] = (stored_at, val)
            if len(C._cache) > 1200:
                for k in sorted(C._cache, key=lambda k: C._cache[k][0])[:200]:
                    held = C._locks.get(k)
                    if held is not None and held.locked():
                        continue                 # someone is refreshing it: dropping its lock would allow a second fetch
                    C._cache.pop(k, None)
                    C._locks.pop(k, None)
        C._note_stale(name)
        return _serve(val)


def reset_runtime_caches() -> dict:
    """Clear in-memory caches, backoff timers, and stale markers; return cleared counts."""
    with C._guard:
        counts = {
            "cache": len(C._cache),
            "backoff": len(C._backoff),
            "stale": len(C._STALE_SINCE),
            "negative": len(C._neg),
        }
        C._cache.clear()
        C._backoff.clear()
        C._STALE_SINCE.clear()
        C._neg.clear()
    C.reset_outbound()                            # 429 cooldowns and outage gates start over too
    return counts


def _clean_json(v):
    """Recursively replace non-finite floats with None so cache files are always valid RFC 8259 JSON."""
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _clean_json(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean_json(val) for val in v]
    return v


def _write_json(path, obj):
    """Write a cache file atomically: a temporary name of this process and thread, then a rename, so a reader never
    sees half a file and two writers (a second process on the same local/) never share a temporary file. Best effort:
    a cache that cannot be written is fetched again next time."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            text = json.dumps(obj, allow_nan=False)
        except (TypeError, ValueError):
            text = json.dumps(_clean_json(obj), allow_nan=False)
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def cached_complete(key, ttl, fn, short=60):
    """cache() for answers that flag themselves incomplete: a "_partial" dict key or a .partial attribute makes the
    answer a Partial held `short` seconds (a throttled, incomplete answer must not stand for the full ttl). What is
    missing is named by a list in "_partial" or a .missing attribute; without names the hold is not reported."""
    def run():
        val = fn()
        part = val.get("_partial") if isinstance(val, dict) else False
        if part or getattr(val, "partial", False):
            names = part if isinstance(part, (list, tuple)) else getattr(val, "missing", None) or ()
            return Partial(val, names)
        return val
    return C.cached(key, ttl, run, partial_ttl=short)


def _curve_store(name, fresh):
    """Merge freshly fetched curves into a file that keeps growing, so history builds up even where a source only
    publishes recent data (Bank of England's daily file covers the current month). Per maturity: the fresh answer is
    the newer vintage, so its value wins for every maturity it has (a corrected value replaces the saved one even when
    the answer is partial: Bank of England days older than the month file come back with 5/10/20 years only); a
    maturity it lacks keeps its saved value. Callers run under cached_disk's per-source lock: one writer per file."""
    path = C.CACHE_DIR / f"curvestore_{name}.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    for d, pts in fresh.items():
        merged = {float(y): v for y, v in old.get(d) or []}
        merged.update((float(y), v) for y, v in pts)
        old[d] = sorted(merged.items())
    cut = (dt.date.today() - dt.timedelta(days=800)).isoformat()
    old = {d: sorted(p) for d, p in old.items() if d >= cut and p}
    C._write_json(path, old)
    return old
