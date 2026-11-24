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
import time
import local_paths

import composite as C

CACHE_DIR = local_paths.CACHE_DIR

# ---------------------------------------------------------------- caching
_cache: dict = {}

_locks: dict = {}

_guard = threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with C._guard:
        hit = C._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        lock = C._locks.setdefault(key, threading.Lock())
    with lock:                                   # one fetch per key at a time
        with C._guard:
            hit = C._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with C._guard:
            C._cache[key] = (time.time(), val)
            if len(C._cache) > 1200:
                for k in sorted(C._cache, key=lambda k: C._cache[k][0])[:200]:
                    held = C._locks.get(k)
                    if held is not None and held.locked():
                        continue                 # someone is refreshing it: dropping its lock would allow a second fetch
                    C._cache.pop(k, None)
                    C._locks.pop(k, None)
        return val


_backoff: dict = {}


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
_STALE_SINCE: dict = {}                       # dataset name -> time of the saved copy now served after a failed fetch

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


def cached_disk(name, ttl, fn):
    """Memory -> disk -> fetch. If the fetch fails, serve the last saved copy however old it is.

    Used for sources that change slowly and limit how often they may be called (OECD, BIS).
    """
    mem_ttl = min(ttl, 1800)
    key = ("disk", name)

    def run():
        path = C.CACHE_DIR / f"{name}.json"
        saved = None
        now = time.time()
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if now - saved["t"] < ttl:
                with C._guard:
                    C._STALE_SINCE.pop(name, None)
                return saved["v"], min(mem_ttl, max(1.0, ttl - (now - saved["t"])))
        except (OSError, ValueError, KeyError):
            saved = None
        with C._guard:
            blocked = C._backoff.get(name)
        if blocked and now < blocked[0]:
            if saved is not None:
                with C._guard:
                    C._STALE_SINCE[name] = saved["t"]
                return saved["v"], min(mem_ttl, max(0.0, blocked[0] - now))
            raise C.CompositeError(blocked[1])
        try:
            val = fn()
        except C.CompositeError as e:
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
            until = time.time() + wait
            with C._guard:
                C._backoff[name] = (until, msg)
                if saved is not None:
                    C._STALE_SINCE[name] = saved["t"]
            if saved is not None:
                return saved["v"], min(mem_ttl, float(wait))
            raise
        with C._guard:
            C._backoff.pop(name, None)
            C._STALE_SINCE.pop(name, None)
        C._write_json(path, {"t": time.time(), "v": val})
        return val, float(mem_ttl)

    now = time.time()
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
        return val

    with lock:
        now = time.time()
        with C._guard:
            hit = C._cache.get(key)
            if hit and now - hit[0] < mem_ttl:
                val = hit[1]
                hit_ok = True
            else:
                hit_ok = False
        if hit_ok:
            C._note_stale(name)
            return val
        val, eff_ttl = run()
        stored_at = time.time() - max(0.0, float(mem_ttl) - float(eff_ttl))
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
        return val


def reset_runtime_caches() -> dict:
    """Clear in-memory caches, backoff timers, and stale markers; return cleared counts."""
    with C._guard:
        counts = {
            "cache": len(C._cache),
            "backoff": len(C._backoff),
            "stale": len(C._STALE_SINCE),
        }
        C._cache.clear()
        C._backoff.clear()
        C._STALE_SINCE.clear()
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
    """cached(), except that an answer flagged "_partial" (a dict key or a .partial attribute) is kept short seconds:
    a throttled, incomplete answer must not stand for the full ttl (INFRASTRUCTURE W4)."""
    val = C.cached(key, ttl, fn)
    part = val.get("_partial") if isinstance(val, dict) else False
    if part or getattr(val, "partial", False):
        with C._guard:
            hit = C._cache.get(key)
            if hit and time.time() - hit[0] > short:
                C._cache.pop(key, None)
        val = C.cached(key, ttl, fn)
    return val


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
