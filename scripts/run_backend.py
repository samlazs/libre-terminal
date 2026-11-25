"""Run an OpenBB backend (openbb-api) with a freeze watchdog and the terminal's runtime fixes.

Why this exists: OpenBB runs some data fetchers directly on the server's event
loop, silences output by swapping the process-wide sys.stdout/sys.stderr, prints
provider URLs (with API keys) in error messages and answers any web page that
asks (CORS "*"). Nothing inside the virtual environments is edited; everything
below is applied at start-up, before the API is imported.

This wrapper starts the normal `openbb-api` entry point and adds:
  * a watchdog thread that pings the backend; if it stops answering it writes
    every thread's stack to local/logs/<name>-freeze-<time>.txt and exits with
    code 75 (scripts/launcher.py restarts it after any abnormal exit);
  * output that cannot be lost: stdout/stderr are wrapped so a closed or swapped
    stream never raises, and every line is passed through terminal/redact.py;
  * the fixes in apply_fixes() and the request guard in install_guard().

Usage: python scripts/run_backend.py <name> [openbb-api arguments...]
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import faulthandler
import itertools
import json
import math
import os
import re
import sys
import threading
import time
import urllib.request
import weakref
from typing import NamedTuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "terminal"))        # appended, so no module there can shadow one OpenBB imports
import local_paths  # noqa: E402
from redact import redact  # noqa: E402

LOGS = local_paths.LOGS_DIR
RESTART_CODE = 75
FROZEN_AFTER = float(os.environ.get("OPENBB_FROZEN_AFTER", "45"))     # seconds without any answer
STARTUP_GRACE = float(os.environ.get("OPENBB_STARTUP_GRACE", "240"))   # seconds allowed before the first answer
UI_PORT = os.environ.get("TERMINAL_UI_PORT") or "6950"
BACKEND_CACHE = local_paths.CACHE_DIR / "backend"


def _arg(argv, flag, default):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else default


# ---------------------------------------------------------------- output that cannot break
class _GuardedStream:
    """Wraps the real stdout/stderr: writes are redacted, never raise, and the stream cannot be closed by accident."""

    def __init__(self, inner):
        self._inner = inner

    def write(self, s):
        try:
            return self._inner.write(redact(s) if isinstance(s, str) else s)
        except (ValueError, OSError, AttributeError):
            return 0

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        try:
            self._inner.flush()
        except (ValueError, OSError, AttributeError):
            pass

    def close(self):            # OpenBB code paths close what they swapped in; never the real stream
        pass

    @property
    def closed(self):
        return False

    def isatty(self):
        try:
            return self._inner.isatty()
        except (ValueError, OSError, AttributeError):
            return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


REAL = {}


def guard_output():
    """Install the guarded streams, make contextlib's redirects interleave safely, and keep logging alive.

    The crash this prevents (C1, 2026-11-15): openbb_yfinance wraps an await in redirect_stdout/redirect_stderr
    to os.devnull. Two overlapping futures-curve requests restored each other's devnull after it had been
    closed, so sys.stderr pointed at a closed file for good: every later log line raised "I/O operation on
    closed file", asyncio's exception handler raised too, the event loop died and the backend exited (code 1).
    """
    import contextlib
    import logging

    sys.stdout = REAL["stdout"] = _GuardedStream(sys.stdout)
    sys.stderr = REAL["stderr"] = _GuardedStream(sys.stderr)

    class _SafeRedirect(contextlib._RedirectStream):  # noqa: SLF001
        """Like contextlib's, but restoring never re-installs a closed stream and never undoes a later redirect."""

        def __exit__(self, *exc):
            old = self._old_targets.pop()
            cur = getattr(sys, self._stream)
            if getattr(old, "closed", False):
                old = REAL.get(self._stream, getattr(sys, "__" + self._stream + "__"))
            if cur is self._new_target or getattr(cur, "closed", False):
                setattr(sys, self._stream, old)

    class redirect_stdout(_SafeRedirect):  # noqa: N801
        _stream = "stdout"

    class redirect_stderr(_SafeRedirect):  # noqa: N801
        _stream = "stderr"

    contextlib.redirect_stdout = redirect_stdout
    contextlib.redirect_stderr = redirect_stderr

    # Loggers without a handler of their own (asyncio, yfinance) end in logging.lastResort, which writes to
    # whatever sys.stderr is at that moment: pin it to the real stream, and never let a logging error escape.
    logging.lastResort = logging.StreamHandler(REAL["stderr"])
    logging.lastResort.setLevel(logging.WARNING)
    logging.raiseExceptions = False

    # Redact every log record at creation (message, arguments and traceback text), whatever handler prints it.
    make = logging.getLogRecordFactory()

    def factory(*a, **k):
        rec = make(*a, **k)
        try:
            msg = rec.getMessage()
            clean = redact(msg)
            if clean != msg:
                rec.msg, rec.args = clean, ()
            if rec.exc_info and not rec.exc_text:
                import traceback
                rec.exc_text = redact("".join(traceback.format_exception(*rec.exc_info)).rstrip("\n"))
        except Exception:  # noqa: BLE001
            pass
        return rec

    logging.setLogRecordFactory(factory)

    # A client that closes its connection (tab switch, reload) is not an error worth a traceback, and nothing the
    # event loop logs may ever take the loop down.
    import asyncio.base_events as be

    orig_handler = be.BaseEventLoop.call_exception_handler

    def call_exception_handler(self, context):
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        try:
            orig_handler(self, context)
        except Exception:  # noqa: BLE001
            pass

    be.BaseEventLoop.call_exception_handler = call_exception_handler

    orig_thread_hook = getattr(threading, "excepthook", None)

    def _redacted_thread_hook(args):
        try:
            import traceback
            msg = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip("\n")
            REAL.get("stderr", sys.stderr).write(f"Exception in thread {getattr(args.thread, 'name', '?')}:\n{redact(msg)}\n")
        except Exception:  # noqa: BLE001
            if orig_thread_hook is not None:
                orig_thread_hook(args)

    if hasattr(threading, "excepthook"):
        threading.excepthook = _redacted_thread_hook


def say(msg: str):
    try:
        REAL.get("stdout", sys.__stdout__).write(msg + "\n")
        REAL.get("stdout", sys.__stdout__).flush()
    except Exception:  # noqa: BLE001
        pass


def _freeze_exit(name: str, why: str):
    """Write every thread's stack to <name>-freeze-*.txt and exit with RESTART_CODE (the supervisor restarts us)."""
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        dump = LOGS / f"{name}-freeze-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        with open(dump, "w", encoding="utf-8") as f:
            f.write(f"{name} backend {why}. Thread stacks:\n\n")
            f.flush()
            faulthandler.dump_traceback(file=f, all_threads=True)
        say(f"\n[watchdog] {name} backend {why}; stacks in {dump}; restarting.")
    finally:
        os._exit(RESTART_CODE)      # always reached, whatever happened to the output streams


def watchdog(name: str, host: str, port: str, startup_grace: float | None = None):
    """Restart a backend that stops answering, and one that never answers at all: startup (building the API) may take
    up to STARTUP_GRACE seconds; a startup that hangs longer (an import or lock deadlock) is dumped and restarted too."""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]", "") else host
    url = f"http://{probe_host}:{port}/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    grace = STARTUP_GRACE if startup_grace is None else float(startup_grace)
    started = time.time()
    poll = min(3.0, max(0.2, grace / 4))
    while True:
        time.sleep(poll)
        try:
            opener.open(url, timeout=5).read(1)
            break
        except Exception:  # noqa: BLE001
            if time.time() - started >= grace:
                _freeze_exit(name, f"gave no first answer within {int(grace)}s of starting")
            continue
    last_ok = time.time()
    misses = 0
    while True:
        time.sleep(5)
        try:
            opener.open(url, timeout=5).read(1)
            last_ok = time.time()
            misses = 0
        except Exception:  # noqa: BLE001
            if time.time() - last_ok < FROZEN_AFTER:
                continue
            misses += 1
            if misses < 2:
                continue
            _freeze_exit(name, f"stopped answering for {int(time.time() - last_ok)}s")


class _OecdRateLimited(Exception):
    """OECD answered HTTP 429. Deliberately not an HTTPError/OpenBBError (see apply_fixes)."""


# ---------------------------------------------------------------- slow sources: disk cache, one fetch per query, bounded wait
# provider package -> (fresh for seconds, answer within seconds, attempts, name shown in messages). "Answer within" is never
# unbounded and never longer than the slowest route budget that reaches the provider (core/contract.py ROUTE_BUDGETS, 65 s):
# past it the request is told the dataset keeps loading in the background (the fetch itself is not repeated).
CACHE_POLICY = {
    "openbb_us_eia": (6 * 3600, 55, 1, "EIA"),  # paginate() already retries transient EIA timeouts up to 3 times
    "openbb_famafrench": (24 * 3600, 45, 3, "The Fama-French data library (Dartmouth)"),
    "openbb_government_us": (3 * 3600, 45, 2, "U.S. Treasury FiscalData"),
    "openbb_cftc": (3 * 3600, 45, 2, "CFTC"),
    "openbb_bls": (6 * 3600, 55, 2, "BLS"),
    "openbb_usda": (12 * 3600, 45, 2, "USDA"),
    "openbb_imf": (12 * 3600, 55, 2, "IMF"),
    "openbb_oecd": (12 * 3600, 55, 1, "OECD"),         # strict hourly quota: 12 h TTL, never retry
    "openbb_ecb": (6 * 3600, 45, 2, "ECB"),
    "openbb_federal_reserve": (3 * 3600, 45, 2, "Federal Reserve"),
    "openbb_fred": (3 * 3600, 45, 2, "FRED"),
    "openbb_jodi": (12 * 3600, 45, 2, "JODI"),
}
STALE_FOR = 14 * 86400          # a failed refresh serves the last good answer up to this old


def _transient(e: BaseException) -> bool:
    import asyncio
    import re as _re
    if isinstance(e, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return True
    return bool(_re.search(r"Connection (aborted|reset)|ConnectionReset|RemoteDisconnected|timed out|10054|Server disconnected|502 |503 |504 ", str(e)))


def _stale_chokepoint_fallback(cls, params, read, path_for):
    """When IMF's Daily_Chokepoints_Data ArcGIS layer is failing (429/499), slice any valid unfiltered chokepoint
    cache already on disk so single-chokepoint cards stay populated instead of failing while the unfiltered card works."""
    import hashlib
    import json
    from datetime import date as _date

    if getattr(cls, "__name__", "") != "ImfMaritimeChokePointVolumeFetcher":
        return None
    try:
        from openbb_imf.utils.constants import CHOKEPOINTS_ID_MAP, CHOKEPOINTS_NAME_TO_ID
    except ImportError:
        return None

    def _iso(v):
        return _date.fromisoformat(str(v)[:10]) if v else None

    start = _iso(params.get("start_date"))
    end = _iso(params.get("end_date"))
    raw_cp = params.get("chokepoint")
    items = raw_cp if isinstance(raw_cp, (list, tuple)) else ([x.strip() for x in str(raw_cp).split(",") if x.strip()] if raw_cp else [])
    wanted = {CHOKEPOINTS_ID_MAP.get(CHOKEPOINTS_NAME_TO_ID.get(c.lower(), c.lower()), c) for c in items}

    candidates = [{"end_date": None, "start_date": None}]
    if start and start >= _date(2023, 1, 1):
        candidates.append({"end_date": None, "start_date": "2023-01-01"})

    cls_id = f"{cls.__module__}.{cls.__qualname__}"
    now = time.time()
    for base_params in candidates:
        k = hashlib.sha1(f"{cls_id}|{json.dumps(base_params, sort_keys=True, default=str)}".encode()).hexdigest()[:24]
        t_all, saved_all = read(path_for(k, "openbb_imf"))
        if not isinstance(saved_all, list) or not saved_all or now - t_all >= STALE_FOR:
            continue
        rows = [
            r for r in saved_all
            if (not wanted or getattr(r, "chokepoint", None) in wanted)
            and (not start or getattr(r, "date", None) and r.date >= start)
            and (not end or getattr(r, "date", None) and r.date <= end)
        ]
        if rows:
            return rows
    return None


import contextvars as _contextvars  # noqa: E402

STALE_NOTES = _contextvars.ContextVar("terminal_stale_notes", default=None)     # set per HTTP request by the guard
CALLER_POOL = _contextvars.ContextVar("terminal_caller_pool", default="ui")     # "x" from composite views, "ui" from direct card calls
FORCE_REFRESH = _contextvars.ContextVar("terminal_force_refresh", default=False)
CACHE_STATUS = _contextvars.ContextVar("terminal_cache_status", default=None)
SUBSTITUTE = _contextvars.ContextVar("terminal_substitute", default=None)     # what really answered, when not the source asked


def note_substitute(sources: str, note: str, delayed: str = ""):
    """This answer came from `sources` (comma-separated): the guard tells the card, which shows it as its Source."""
    box = SUBSTITUTE.get()
    if box is not None:
        box.update(sources=sources, note=note, delayed=delayed)


def stale_message(label: str, saved_at: float, now: float | None = None) -> str:
    """'U.S. EIA not answering: showing the copy saved 2026-11-18 14:05 (19 h old)'."""
    hours = ((now or time.time()) - saved_at) / 3600
    age = f"{hours * 60:.0f} min" if hours < 1 else f"{hours:.0f} h" if hours < 48 else f"{hours / 24:.0f} days"
    name = str(label).split(" (")[0]
    from datetime import datetime
    from zoneinfo import ZoneInfo

    when = datetime.fromtimestamp(saved_at, ZoneInfo("America/Toronto")).strftime("%Y-%m-%d %H:%M")      # Montreal time, as every card
    return f"{name} not answering: showing the copy saved {when} ({age} old)"


def stale_header(notes: list) -> bytes:
    """The first note as a header value: ASCII only, one line, at most 300 characters."""
    import re

    return re.sub(r"[^\x20-\x7e]", "?", str(notes[0]))[:300].encode("ascii")


DELAYED_LABELS = {"yfinance": "Yahoo Finance", "cboe": "Cboe", "tmx": "TMX", "nasdaq": "Nasdaq", "intrinio": "Intrinio", "fmp": "FMP", "tiingo": "Tiingo"}
DELAYED_PARTS = ("/quote", "/snapshots", "/discovery/", "/options/chains", "/futures/curve", "/tmx/markets/", "/nasdaq/nordic/movers")


def delayed_note(path: str, query: bytes) -> str:
    """HANDOFF P2: a price snapshot from a free feed says it is delayed. Live-price paths only (quotes, snapshots, movers,
    chains, curves); Deribit's public API is real time and is never marked. The delay length is not stated: it differs
    by exchange and is not in the answers."""
    from urllib.parse import parse_qs

    if not any(part in path for part in DELAYED_PARTS) or "/deribit/" in path:
        return ""
    prov = (parse_qs(query.decode("latin-1")).get("provider") or [""])[0].lower()
    if not prov:
        prov = "tmx" if "/tmx/" in path else "nasdaq" if "/nasdaq/" in path else ""
    if prov == "deribit":
        return ""
    return f"Delayed prices from {DELAYED_LABELS.get(prov, 'the source')} (free feed, not real time)"


def _is_cacheable_result(value) -> bool:
    """Complete-answer rule for disk cache: skip None, empty containers, empty DataFrames, and empty AnnotatedResult."""
    if value is None:
        return False
    inner = value.result if hasattr(value, "result") and hasattr(value, "metadata") else value
    if inner is None:
        return False
    if getattr(inner, "empty", False) is True:
        return False
    if isinstance(inner, (list, tuple, dict, set, str, bytes)) and len(inner) == 0:
        return False
    return True


def install_fetch_cache(applied: list):
    import asyncio
    import gzip
    import hashlib
    import json
    import pickle

    from openbb_core.app.model.abstract.error import OpenBBError
    from openbb_core.provider.abstract.fetcher import Fetcher

    orig = Fetcher.fetch_data.__func__
    inflight: dict = {}

    def path_for(key, prov):
        return BACKEND_CACHE / prov / f"{key}.pkl.gz"

    def read(p):
        try:
            with gzip.open(p, "rb") as f:
                return os.path.getmtime(p), pickle.load(f)
        except Exception:  # noqa: BLE001
            return None, None

    def write(p, value):
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            blob = gzip.compress(pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL), compresslevel=5)
            if len(blob) > 60 * 1024 * 1024:
                return
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(blob)
            os.replace(tmp, p)
        except Exception:  # noqa: BLE001  (unpicklable result: just not cached)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    async def fetch_data(cls, params, credentials=None, **kwargs):
        prov = (cls.__module__ or "").split(".")[0]
        policy = CACHE_POLICY.get(prov)
        if not policy or getattr(cls, "is_streaming", False) is True:
            return await orig(cls, params, credentials, **kwargs)
        ttl, bound, attempts, label = policy
        try:
            ident = json.dumps(params, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001
            return await orig(cls, params, credentials, **kwargs)
        # a runtime patch that changes what a fetcher returns sets _terminal_cache_rev, so answers saved before it are not served
        rev = getattr(cls, "_terminal_cache_rev", "")
        key = hashlib.sha1(f"{cls.__module__}.{cls.__qualname__}{'@' + rev if rev else ''}|{ident}".encode()).hexdigest()[:24]
        p = path_for(key, prov)
        t, saved = await asyncio.to_thread(read, p)
        force = bool(FORCE_REFRESH.get())
        cache_status = CACHE_STATUS.get()
        if not force and saved is not None and time.time() - t < ttl:
            if cache_status is not None:
                cache_status[:] = ["hit"]
            return saved

        async def run():
            last = None
            for attempt in range(attempts):
                try:
                    value = await orig(cls, params, credentials, **kwargs)
                    if _is_cacheable_result(value):
                        await asyncio.to_thread(write, p, value)
                    return value
                except Exception as e:  # noqa: BLE001
                    last = e
                    if attempt + 1 < attempts and _transient(e):
                        await asyncio.sleep(2 * (attempt + 1))
                        continue
                    break
            if saved is not None and time.time() - t < STALE_FOR:
                # the source is failing: the last good answer is better than nothing, and the card says so (HANDOFF P2):
                # noted for this request, sent as the X-Terminal-Stale header by the guard
                notes = STALE_NOTES.get()
                if notes is not None:
                    notes.append(stale_message(label, t))
                if cache_status is not None:
                    cache_status[:] = ["stale"]
                return saved
            if prov == "openbb_imf":
                fallback = await asyncio.to_thread(_stale_chokepoint_fallback, cls, params, read, path_for)
                if fallback is not None:
                    if cache_status is not None:
                        cache_status[:] = ["stale"]
                    return fallback
            if _transient(last):
                raise OpenBBError(f"{label} is not answering right now (the connection was dropped or timed out). Try again later.") from None
            raise last

        task = inflight.get(key)
        if task is None or task.done():
            task = asyncio.ensure_future(run())          # keeps running if the browser gives up, so the next view is instant
            inflight[key] = task
            task.add_done_callback(lambda _t, k=key: inflight.pop(k, None) if inflight.get(k) is _t else None)
            task.add_done_callback(lambda _t: _t.exception() if not _t.cancelled() else None)    # never "exception was never retrieved"
        if bound is None:
            res = await asyncio.shield(task)
        else:
            try:
                res = await asyncio.wait_for(asyncio.shield(task), timeout=bound)
            except asyncio.TimeoutError:
                raise OpenBBError(f"{label} is slow to answer the first request for this dataset. It keeps loading in the background and is "
                                  f"kept for later: reload this card in a minute.") from None
        if cache_status is not None and not cache_status:
            cache_status[:] = ["refresh" if force else "miss"]
        return res

    Fetcher.fetch_data = classmethod(fetch_data)
    applied.append("slow sources cached on disk (" + ", ".join(v[3].split(" (")[0] for v in CACHE_POLICY.values()) + ")")


# ---------------------------------------------------------------- run sync work off the event loop, fairly
PROVIDER_SLOTS = {"openbb_oecd": 2, "openbb_yfinance": 6, "openbb_sec": 4, "openbb_us_eia": 4, "openbb_imf": 3, "openbb_federal_reserve": 4}
DEFAULT_SLOTS = 10
WORKERS = 48


def pool_slot_spec(prov: str, pool: str) -> tuple[tuple[str, str], int]:
    """Isolate direct UI card requests ('ui') from /x/ composite view requests ('x') per provider, except OECD which
    keeps a single shared 2-slot cap to avoid upstream 429 rate limits (P4 / review C4)."""
    if prov == "openbb_oecd":
        return (prov, "shared"), PROVIDER_SLOTS[prov]
    p = "x" if pool == "x" else "ui"
    if prov == "openbb_yfinance":
        return (prov, p), 5 if p == "ui" else 4
    if prov in PROVIDER_SLOTS:
        tot = PROVIDER_SLOTS[prov]
        half = (tot + 1) // 2
        return (prov, p), max(2, half if p == "ui" else tot - half)
    return (prov, p), max(2, DEFAULT_SLOTS // 2)


def install_thread_offload(applied: list):
    import asyncio
    import inspect
    import weakref
    from concurrent.futures import ThreadPoolExecutor

    from openbb_core.provider.abstract import fetcher as _fetcher

    loops: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def _provider(func):
        mod = getattr(func, "__module__", None) or getattr(getattr(func, "__func__", None), "__module__", "") or ""
        return mod.split(".")[0]

    async def maybe_coroutine(func, /, *args, **kwargs):
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        loop = asyncio.get_running_loop()
        loop_sems = loops.get(loop)
        if loop_sems is None:                     # a wider pool than asyncio's default (cpu + 4)
            loop_sems = {}
            loops[loop] = loop_sems
            loop.set_default_executor(ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="fetch"))
        prov = _provider(func)
        skey, lim = pool_slot_spec(prov, CALLER_POOL.get())
        sem = loop_sems.get(skey)
        if sem is None:
            sem = loop_sems[skey] = asyncio.Semaphore(lim)
        async with sem:                               # one slow provider or caller pool cannot take every worker thread
            return await asyncio.to_thread(func, *args, **kwargs)

    _fetcher.maybe_coroutine = maybe_coroutine
    try:
        from openbb_core.app import command_runner as _cr
        _cr.maybe_coroutine = maybe_coroutine        # router-level commands too
    except Exception as e:  # noqa: BLE001
        _not_applied("router-level commands off the event loop", e)
    applied.append(f"sync fetchers run off the event loop ({WORKERS} workers, per-provider limits)")


CURVE_TTL = 120          # seconds a Yahoo futures curve is reused (quotes are delayed snapshots already)


def install_futures_curve_fix(applied: list):
    """openbb_yfinance's curve builder redirects output around an await and runs a blocking download loop on the
    event loop. Run it in its own thread and event loop (at most two at a time), so it neither interleaves
    redirects with other requests nor blocks them."""
    import asyncio
    import collections
    import functools
    import zlib

    from openbb_yfinance.utils import helpers as yfh

    gate = threading.BoundedSemaphore(2)
    orig = yfh.get_futures_curve
    kept = collections.OrderedDict()
    locks = [threading.Lock() for _ in range(64)]
    meta = threading.Lock()

    def _sync(*a, **k):
        # P4 slow cards: a curve is one quote per listed month (Henry Hub: 38, about 18 s). The same curve asked again
        # within CURVE_TTL (a second card, a tab switch) is served from memory; concurrent asks wait for one fetch.
        key = repr((a, sorted(k.items())))
        lk = locks[zlib.crc32(key.encode("utf-8")) & 63]
        with lk:
            with meta:
                hit = kept.get(key)
                if hit and time.time() - hit[0] < CURVE_TTL:
                    kept.move_to_end(key)
                    return hit[1].copy()
            with gate:
                res = asyncio.run(orig(*a, **k))
            if res is not None and len(res):                 # an empty or failed curve is never kept
                with meta:
                    kept[key] = (time.time(), res.copy())
                    kept.move_to_end(key)
                    while len(kept) > 128:
                        kept.popitem(last=False)
            return res

    @functools.wraps(orig)
    async def get_futures_curve(*a, **k):
        return await asyncio.to_thread(_sync, *a, **k)

    yfh.get_futures_curve = get_futures_curve        # the fetcher imports it at call time
    applied.append(f"futures curves built in their own thread, the same curve reused for {CURVE_TTL} s")


def install_yfinance_losers_fix(applied: list):
    """openbb_yfinance's day-losers fetcher in V4 filters percentchange > -3 instead of < -3 (a copy-paste bug
    from gainers), which drops every stock down more than 3%. Query < -3, falling back to < 0 on flat days."""
    try:
        from openbb_yfinance.models import losers as yf_losers
    except ImportError:
        return                                      # not this backend's layout (V5): nothing to do

    from openbb_core.provider.utils.errors import EmptyDataError
    from openbb_yfinance.utils import helpers as yfh

    fetcher = yf_losers.YFLosersFetcher

    def _body(threshold: float) -> dict:
        return {
            "offset": 0,
            "size": 250,
            "sortField": "percentchange",
            "sortType": "asc",
            "quoteType": "equity",
            "query": {
                "operator": "and",
                "operands": [
                    {"operator": "gt", "operands": ["intradaymarketcap", 500000000]},
                    {
                        "operator": "or",
                        "operands": [
                            {"operator": "eq", "operands": ["exchange", "NMS"]},
                            {"operator": "eq", "operands": ["exchange", "NYQ"]},
                        ],
                    },
                    {"operator": "lt", "operands": ["percentchange", threshold]},
                    {"operator": "gt", "operands": ["intradayprice", 5]},
                ],
            },
            "userId": "",
            "userIdType": "guid",
        }

    async def aextract_data(query, credentials=None, **kwargs):
        try:
            return await yfh.get_custom_screener(body=_body(-3), limit=query.limit)
        except EmptyDataError:
            return await yfh.get_custom_screener(body=_body(0), limit=query.limit)

    fetcher.aextract_data = staticmethod(aextract_data)
    fetcher.extract_data = staticmethod(aextract_data)  # Fetcher.__init_subclass__ binds extract_data at definition time
    applied.append("Yahoo Finance top losers filters percentchange < -3")


def install_tmx_filings_fix(applied: list):
    """TMX/QuoteMedia SEDAR+ filings sometimes return description=None, while openbb_tmx's
    TmxCompanyFilingsData declares description: str. Coerce None descriptions before validating."""
    try:
        from openbb_tmx.models import company_filings as tcf
    except ImportError:
        return
    orig = tcf.TmxCompanyFilingsFetcher.transform_data

    def transform_data(query, data, **kwargs):
        cleaned = [{**d, "description": d.get("description") or ""} if isinstance(d, dict) and d.get("description") is None else d for d in (data or ())]
        return orig(query, cleaned, **kwargs)

    tcf.TmxCompanyFilingsFetcher.transform_data = staticmethod(transform_data)
    applied.append("TMX SEDAR+ filings tolerate missing descriptions")


FRED_KEY_MESSAGE = ("Missing credential 'fred_api_key'. This FRED dataset needs the free FRED key (series search, release tables and "
                    "regional maps are not in FRED's public download). Check https://fredaccount.stlouisfed.org/apikeys to get it.")


def install_yfinance_price_target_fix(applied: list):
    """Yahoo's analyst actions: a firm that published no price target comes with 0.0 (Oppenheimer cutting NVDA to Perform,
    2026-11-12), shown as a 0.00 target; blank it. And newest first: Yahoo sends them that way, the fetcher reverses them
    (the cards opened on 2018)."""
    try:
        from openbb_yfinance.models import price_target as ypt
    except ImportError:
        return
    orig = ypt.YFinancePriceTargetFetcher.transform_data

    def transform_data(query, data, **kwargs):
        rows = [{**d, **{k: None for k in ("price_target", "price_target_previous") if not d.get(k)}} if isinstance(d, dict) else d for d in (data or ())]
        rows.sort(key=lambda d: str(d.get("published_date") or "") if isinstance(d, dict) else "", reverse=True)
        return orig(query, rows, **kwargs)

    ypt.YFinancePriceTargetFetcher.transform_data = staticmethod(transform_data)
    applied.append("Yahoo analyst actions: newest first, no 0.00 price targets")


FUND_MULTIPLES = ("price_to_earnings", "price_to_book", "price_to_sales", "price_to_cashflow")


def fund_multiples(row: dict) -> dict:
    """Yahoo's fund holdings statistics carry the portfolio "Price/Earnings", "Price/Book", "Price/Sales" and
    "Price/Cashflow" as yields (earnings / price): VFIAX 0.0426, 0.1959, 0.2818, 0.0611 (2026-11-18), i.e. P/E 23.5,
    P/B 5.1, P/S 3.5, P/CF 16.4 for the S&P 500, the figures Yahoo's own fund page shows. A yield above 0 and below 1
    becomes the multiple 1/x; a value of 1 or more is already a multiple and is kept; zero or negative (no earnings)
    has no meaningful multiple and is blanked (review A2)."""
    out = dict(row)
    for k in FUND_MULTIPLES:
        v = out.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
            out[k] = None if v <= 0 else (round(1 / v, 2) if v < 1 else v)
    return out


FUND_CATEGORY_NAMES = {"Realestate": "Real Estate", "US_GOVERNMENT": "U.S. Government", "BELOW_B": "Below B", "OTHER": "Other"}
FUND_BREAKDOWN_ORDER = ("asset_class", "sector", "bond_rating")
BOND_RATING_ORDER = ("AAA", "AA", "A", "BBB", "BB", "B", "Below B", "U.S. Government", "Other")


def fund_allocation_rows(rows: list) -> list:
    """Yahoo fund allocation labels as written (VFIAX "Realestate", AGG "US_GOVERNMENT", "BELOW_B", "OTHER") and bond
    ratings in no order (AGG: BB, AA, AAA, A, OTHER...). Plain names; ratings from AAA down; breakdowns kept grouped
    in their source order otherwise (review A2)."""
    out = [{**r, "category": FUND_CATEGORY_NAMES.get(r.get("category"), r.get("category"))} if isinstance(r, dict) else r for r in rows or ()]

    def key(ir):
        i, r = ir
        if not isinstance(r, dict):
            return (len(FUND_BREAKDOWN_ORDER), 0, i)
        b = r.get("breakdown")
        bi = FUND_BREAKDOWN_ORDER.index(b) if b in FUND_BREAKDOWN_ORDER else len(FUND_BREAKDOWN_ORDER)
        ri = BOND_RATING_ORDER.index(r.get("category")) if b == "bond_rating" and r.get("category") in BOND_RATING_ORDER else len(BOND_RATING_ORDER)
        return (bi, ri if b == "bond_rating" else 0, i)

    return [r for _, r in sorted(enumerate(out), key=key)]


def install_yfinance_fund_multiples_fix(applied: list):
    try:
        from openbb_yfinance.models import fund_info as yfi
    except ImportError:
        return
    orig = yfi.YFinanceFundInfoFetcher.transform_data

    def transform_data(query, data, **kwargs):
        return orig(query, [fund_multiples(d) if isinstance(d, dict) else d for d in (data or ())], **kwargs)

    yfi.YFinanceFundInfoFetcher.transform_data = staticmethod(transform_data)
    applied.append("Yahoo fund P/E, P/B, P/S, P/CF as multiples (Yahoo sends yields)")
    from openbb_yfinance.models import fund_allocation as yfa
    orig_alloc = yfa.YFinanceFundAllocationFetcher.transform_data

    def transform_alloc(query, data, **kwargs):
        return orig_alloc(query, fund_allocation_rows(list(data or ())), **kwargs)

    yfa.YFinanceFundAllocationFetcher.transform_data = staticmethod(transform_alloc)
    applied.append("Yahoo fund allocation: plain category names, ratings AAA first")


_MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def recommendation_month(period, today=None):
    """Turn Yahoo relative recommendation offsets ('0m', '-1m', '-2m', '-3m') into readable calendar months ('Nov 2026')
    that table.js monthKey() also sorts chronologically."""
    import re
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if period is None:
        return None
    m = re.fullmatch(r"(-?\d+)m", str(period).strip())
    if not m:
        return period
    if today is None:
        today = datetime.now(ZoneInfo("America/Toronto")).date()
    y, m0 = divmod(today.year * 12 + (today.month - 1) + int(m.group(1)), 12)
    return f"{_MONTH_ABBR[m0]} {y}"


def format_recommendation_periods(rows, today=None):
    """Rewrite relative 'period' offsets on Yahoo recommendation rows (dicts or models) in place / shallow copy."""
    out = []
    for r in rows or ():
        if isinstance(r, dict):
            out.append({**r, "period": recommendation_month(r.get("period"), today=today)})
        else:
            p = getattr(r, "period", None)
            if p is not None and hasattr(r, "period"):
                try:
                    setattr(r, "period", recommendation_month(p, today=today))
                except Exception:  # noqa: BLE001
                    pass
            out.append(r)
    return out


def install_yfinance_recommendations_months(applied: list):
    try:
        from openbb_yfinance.models import analyst_recommendations as yfar
    except ImportError:
        return
    orig = yfar.YFinanceAnalystRecommendationsFetcher.transform_data

    def transform_data(query, data, **kwargs):
        return format_recommendation_periods(orig(query, data, **kwargs))

    yfar.YFinanceAnalystRecommendationsFetcher.transform_data = staticmethod(transform_data)
    applied.append("Yahoo recommendations periods formatted as readable months (e.g. Nov 2026)")


def holdings_in_fund_currency(rows: list, fund_currency) -> list:
    """TMX ETF holdings: the market values are in the fund's currency, but each row carries a "currency" that is not
    the one of its value (XIU 2026-11-18: TD 1,609,180,279 / 9,246,037 shares = 174.04, TD.TO's CAD close 174.21,
    stamped USD; SHOP likewise). Every row takes the fund's currency; unknown fund currency -> blank (review A2)."""
    cur = str(fund_currency).strip().upper() if fund_currency and str(fund_currency).strip() else None
    return [{**r, "currency": cur} if isinstance(r, dict) else r for r in (rows or [])]


def rescale_fields(row, fields, factor):
    """Multiply the given numeric fields of a dict or a model by factor (None stays None; fields None: every numeric
    field, for tables whose columns are named by the data). Returns the row."""
    if fields is None:
        fields = list(row) if isinstance(row, dict) else list(row.model_dump())
    for f in fields:
        v = row.get(f) if isinstance(row, dict) else getattr(row, f, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if isinstance(row, dict):
                row[f] = v * factor
            else:
                setattr(row, f, v * factor)
    return row


# (module, fetcher class, fields, factor, label). Each decided by a live value against a known one (review A4, 2026-11-18):
#  - Nasdaq ratios send current/quick/cash ratios in percent: NVDA FY2026 current ratio 390.53 = 3.91x (FY2023 351.56 = 3.52x).
#  - TMX short interest: SHORTINTERESTPCT is already a fraction and the model divides it by 100 again: SU 0.000167 shown
#    0.02%, while 19,185,345 short / 1,151,224,232 shares = 1.67%; BCE 0.000386 vs 35.94M / 932.5M = 3.85%.
RESCALES = [
    ("openbb_nasdaq.models.financial_ratios", "NasdaqFinancialRatiosFetcher", ("current_ratio", "quick_ratio", "cash_ratio"), 0.01,
     "Nasdaq current/quick/cash ratios as multiples"),
    ("openbb_tmx.models.equity_short_interest", "TmxShortInterestFetcher", ("short_interest_percent",), 100,
     "TMX short interest percent divided by 100 once, not twice"),
    # Fed money market fund holdings (Enhanced Financial Accounts): the series labels end "(millions)" (dollars), which
    # openbb_federal_reserve.utils.mmf strips; the table then showed U.S. Government security repo 2,754,760 as "2.75M"
    # beside other repo "169,497" (review A5). In dollars every column gets its own T/B scale: 2.75T, 169.50B.
    ("openbb_federal_reserve.models.money_market_funds", "FederalReserveMoneyMarketFundsFetcher", None, 1e6,
     "Fed money market fund holdings in dollars (the source's millions)"),
]


def install_rescales(applied: list):
    import importlib

    for mod_name, cls_name, fields, factor, label in RESCALES:
        try:
            cls = getattr(importlib.import_module(mod_name), cls_name)
        except (ImportError, AttributeError):
            continue
        orig = cls.transform_data

        def transform_data(query, data, _orig=orig, _fields=fields, _factor=factor, **kwargs):
            return [rescale_fields(r, _fields, _factor) for r in _orig(query, data, **kwargs)]

        cls.transform_data = staticmethod(transform_data)
        cls._terminal_cache_rev = f"rescale_{factor:g}"
        applied.append(label)


OCC_SYMBOL = __import__("re").compile(r"^[A-Z]+\d?\d{6}[CP]\d{8}$")


def cboe_standard_contracts(options: list) -> list:
    """Cboe chain records whose contract symbol is an OCC symbol (root, optional adjustment digit, YYMMDD, C/P, strike
    x1000). Cboe also lists symbols such as B1SPY261120C00710000 (224 of 12,446 SPY rows on 2026-11-18); openbb_cboe's
    parser read them as ticker "B", no expiration, type "SputY", and the whole chain failed (review C2: NVDA and SPY
    answered 500 "strptime() argument 1 must be str, not float")."""
    return [o for o in options or [] if OCC_SYMBOL.match(str((o or {}).get("option") or ""))]


def install_cboe_chain_symbols(applied: list):
    try:
        from openbb_cboe.models.options_chains import CboeOptionsChainsFetcher as F
        from openbb_core.app.model.abstract.error import OpenBBError
    except ImportError:
        return
    orig_transform = F.transform_data
    orig_aextract = F.aextract_data

    def transform_data(query, data, _orig=orig_transform, **kwargs):
        inner = (data or {}).get("data") if isinstance(data, dict) else None
        if isinstance(inner, dict) and isinstance(inner.get("options"), list):
            inner["options"] = cboe_standard_contracts(inner["options"])
        return _orig(query, data, **kwargs)

    _cboe_msg = (
        "Cboe refused the delayed options feed (HTTP 403). "
        "Switch Source to Yahoo Finance or Nasdaq (U.S.), TMX (Canada), or Deribit (crypto)."
    )

    async def aextract_data(query, credentials, _orig=orig_aextract, **kwargs):
        try:
            return await _orig(query, credentials, **kwargs)
        except Exception as e:  # noqa: BLE001
            if cboe_refused(e):
                raise OpenBBError(_cboe_msg) from None
            raise

    F.transform_data = staticmethod(transform_data)
    F.aextract_data = staticmethod(aextract_data)
    # the coroutine itself, never asyncio.run() around it: that ran each fetch on a throw-away event loop, openbb_cboe's
    # shared HTTP cache bound to it, and every later Cboe card failed "bound to a different event loop" (2026-11-21)
    F.extract_data = staticmethod(aextract_data)
    try:
        from openbb_cboe.utils import helpers as cboe_helpers
    except ImportError:
        cboe_helpers = None
    if cboe_helpers is not None and not getattr(cboe_helpers.response_callback, "_terminal_403_wrapped", False):
        orig_cb = cboe_helpers.response_callback

        async def response_callback(response, arg=None):
            if getattr(response, "status", None) == 403:
                raise OpenBBError("Cboe refused the request (HTTP 403).")
            return await orig_cb(response, arg)

        response_callback._terminal_403_wrapped = True
        cboe_helpers.response_callback = response_callback
    applied.append("Cboe option chains keep OCC symbols and turn upstream 403 HTML blocks into a readable error")


def cboe_refused(exc: BaseException) -> bool:
    """Cboe answered 403 with an HTML page (its delayed_quotes CDN, since 2026-11-21). It is never worked around:
    the cards below take the same figures from another published source and say so."""
    txt = str(exc)
    return "403" in txt or "Forbidden" in txt or "text/html" in txt


CBOE_RETRY = 300            # seconds before a refused Cboe feed is asked again
CHAIN_TTL = 300
CHAINS_KEPT = 16
YAHOO_OPTION_INDICES = {"SPX", "NDX", "RUT", "VIX", "XSP", "OEX", "DJX"}
CBOE_OPTION_ALIASES = {"GSPC": "SPX"}


def canonical_option_symbol(symbol: str) -> str:
    """Normalize a ticker for Cboe options and Yahoo option aliases (e.g. ^GSPC -> SPX)."""
    s = str(symbol or "").strip().upper().lstrip("^")
    return CBOE_OPTION_ALIASES.get(s, s)


def yahoo_option_symbol(symbol: str):
    """Yahoo's spelling of a Cboe underlying (^SPX, BRK-B); None for text that is not a ticker."""
    import re

    s = canonical_option_symbol(symbol)
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", s):
        return None
    return "^" + s if s in YAHOO_OPTION_INDICES else s.replace(".", "-")


def yahoo_retry(read, pause: float = 1.0):
    """read(), up to three times while it is empty or fails transiently. yfinance turns a refused crumb (several first
    requests at once after a start) into an empty answer or a TypeError: VIX read "no options" that way (2026-11-22).
    A rate limit is another exception and is never retried."""
    failed = None
    for attempt in range(3):
        if attempt:
            time.sleep(pause * attempt)
        try:
            out = read()
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "RateLimit" in type(e).__name__ or "429" in msg or "Too Many Requests" in msg:
                raise
            if not (isinstance(e, (TypeError, ValueError, KeyError, OSError, TimeoutError)) or _transient(e)):
                raise
            failed = e
            continue
        if out:
            return out
        failed = None
    if failed:
        raise failed
    return None


def yahoo_chain(symbol: str):
    """(rows, regular-session price, its time) or None without listed options. The regular price and its time, not the
    after-hours ones OpenBB's fetcher keeps: the option quotes stop at the close."""
    from concurrent.futures import ThreadPoolExecutor

    import pandas as pd
    import yfinance

    ticker = yfinance.Ticker(symbol)
    expirations = list(yahoo_retry(lambda: ticker.options) or ())
    if not expirations:
        return None

    def read_expiry(expiry):
        got = ticker.option_chain(expiry)
        return got if got.calls is not None else None

    def fetch_one(expiry):
        try:
            return expiry, yahoo_retry(lambda e=expiry: read_expiry(e)), None
        except Exception as exc:  # noqa: BLE001
            return expiry, None, exc

    with ThreadPoolExecutor(max_workers=min(4, len(expirations))) as pool:
        fetched = list(pool.map(fetch_one, expirations))

    frames, info, missing, errors = [], None, [], []
    for expiry, chain, err in fetched:
        if err is not None:
            errors.append((expiry, err))
            continue
        if chain is None:
            missing.append(expiry)
            continue
        info = info or chain.underlying
        for side, frame in (("call", chain.calls), ("put", chain.puts)):
            if frame is not None and len(frame):
                frames.append(frame.assign(option_type=side, expiration=pd.Timestamp(expiry).date()))

    rate_err = next((exc for _, exc in errors if "RateLimit" in type(exc).__name__ or "429" in str(exc) or "Too Many Requests" in str(exc)), None)
    if rate_err is not None:
        raise rate_err
    allowed_skip = max(1, len(expirations) // 10) if len(expirations) >= 5 else 0
    if not frames or len(missing) + len(errors) > allowed_skip:
        if errors:
            raise errors[0][1]
        if missing:
            raise ValueError(f"no answer for the {missing[0]} expiry")
        return None
    if missing or errors:
        say(f"[options] {symbol}: skipped {len(missing) + len(errors)}/{len(expirations)} unavailable expiries")

    info = info or getattr(ticker, "_underlying", None)
    spot, stamp = (info or {}).get("regularMarketPrice"), (info or {}).get("regularMarketTime")
    if not spot or not stamp:
        raise ValueError("no underlying price or time")
    rows = pd.concat(frames, ignore_index=True).rename(columns={
        "contractSymbol": "contract_symbol", "lastTradeDate": "last_trade_time", "lastPrice": "last_trade_price",
        "openInterest": "open_interest"})
    for col in ("open_interest", "volume"):
        rows[col] = rows[col].fillna(0).astype("int64") if col in rows else 0
    keep = ["contract_symbol", "expiration", "strike", "option_type", "bid", "ask", "last_trade_price", "last_trade_time",
            "open_interest", "volume"]
    return rows[keep], float(spot), pd.Timestamp(int(stamp), unit="s", tz="UTC").tz_convert("America/New_York")


_BILL_RATE = {"at": 0.0, "rate": None}


def bill_rate():
    """13-week T-bill yield as a fraction (Yahoo ^IRX, kept 6 h); None when it cannot be read."""
    import yfinance

    if _BILL_RATE["rate"] is None or time.time() - _BILL_RATE["at"] > 6 * 3600:
        try:
            value = float(yfinance.Ticker("^IRX").fast_info["lastPrice"])
        except Exception as e:  # noqa: BLE001
            say(f"[options] 13-week bill rate not read: {type(e).__name__}")
            return None
        if not 0 <= value < 25:
            return None
        _BILL_RATE.update(at=time.time(), rate=value / 100)
    return _BILL_RATE["rate"]


def build_substitute_chain(symbol: str, fetched, rate, today):
    """(chain the openbb_cboe chart builders accept, note for the card) from yahoo_chain()'s answer."""
    from openbb_core.app.model.abstract.error import OpenBBError
    from openbb_core.provider.standard_models.options_chains import OptionsChainsData
    from pydantic import PrivateAttr

    try:
        from openbb_cboe.models.options_chains import CboeOptionsChainsData
        base_chain = CboeOptionsChainsData
    except ImportError:
        base_chain = OptionsChainsData

    import option_math

    class SubstituteChain(base_chain):
        _frame = PrivateAttr(default=None)
        _views = PrivateAttr(default_factory=dict)

        @property
        def dataframe(self):        # OpenBB's version turns an all-blank ask column into a failure that loses DEX and GEX
            return self._frame.copy()

        def strategies(self, *args, **kwargs):
            if not self._frame["ask"].notna().any():
                raise OpenBBError(f"Yahoo Finance has no bid/ask quotes for {symbol} right now, so strategies cannot be "
                                  "priced. They return in market hours.")
            return super().strategies(*args, **kwargs)

    rows, spot, quoted_at = fetched
    df, basis = option_math.analyse_chain(rows, spot, quoted_at, rate or 0.0, today)
    if df.empty:
        return None
    df["underlying_symbol"] = symbol
    fields = ("underlying_symbol", "underlying_price", "contract_symbol", "expiration", "dte", "strike", "option_type",
              "open_interest", "volume", "last_trade_price", "bid", "ask", *option_math.CALCULATED)

    def model(part):
        part = part.reset_index(drop=True)
        values = part[list(fields)].astype(object)
        obj = SubstituteChain.model_validate(values.where(values.notna(), None).to_dict("list"))
        obj._frame = part
        return obj

    chain = model(df[df["main"]])
    chain._views["all"] = model(df)
    priced = option_math.priced_part(df, spot)
    if not priced.empty:
        chain._views["priced"] = model(priced)
    method = "mids" if basis == "mid" else "last trades"       # short: the footer also has to fit the Source line
    note = (f"Cboe blocked; Yahoo {quoted_at:%Y-%m-%d %H:%M} ET, IV calculated from {method}"
            + ("" if rate is not None else " at 0% rate"))
    return chain, note


def install_cboe_options_fallback(applied: list, fetch=yahoo_chain, get_rate=bill_rate):
    """Options chains and analytics (surface, smile, term structure, stats, strategies) when Cboe refuses its feed."""
    import asyncio
    import importlib

    try:
        from openbb_cboe.models.options_chains import CboeOptionsChainsFetcher
        from openbb_cboe.utils.options import data_handler
        from openbb_core.app.model.abstract.error import OpenBBError
        from openbb_core.provider.abstract.annotated_result import AnnotatedResult
        from openbb_core.provider.utils.errors import EmptyDataError
    except ImportError:         # V4's openbb-cboe has no options analytics
        return

    loaded: dict = {}           # symbol -> (time, chain, note or None, raw AnnotatedResult)
    flights: dict = {}
    refused_until = [0.0]
    yahoo_slots = threading.BoundedSemaphore(3)
    orig_fetch = CboeOptionsChainsFetcher.fetch_data

    def substitute(symbol: str, yahoo: str):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        with yahoo_slots:
            got = fetch(yahoo)
        return got and build_substitute_chain(symbol, got, get_rate(), datetime.now(ZoneInfo("America/New_York")).date())

    async def build(symbol: str):
        if time.time() >= refused_until[0]:
            try:
                got = await orig_fetch({"symbol": symbol.replace("-", ".")}, {})
                chain = getattr(got, "result", got)
                if not getattr(chain, "expirations", None):
                    raise OpenBBError(f"No options available for {symbol}.")
                return chain, None, got
            except EmptyDataError:
                raise OpenBBError(f"No options available for {symbol}.") from None
            except Exception as e:  # noqa: BLE001
                if not cboe_refused(e):
                    raise
                refused_until[0] = time.time() + CBOE_RETRY
        yahoo = yahoo_option_symbol(symbol)
        if not yahoo:
            raise OpenBBError(f"No options available for {symbol}.")
        try:
            built = await asyncio.to_thread(substitute, symbol, yahoo)
        except Exception as e:  # noqa: BLE001
            # not an OpenBBError: the chart routes would report that as "no options for this symbol"
            raise RuntimeError(f"Cboe refused its options feed and Yahoo Finance is not answering ({type(e).__name__}: "
                               f"{redact(str(e))[:120]})") from None
        if not built:
            raise OpenBBError(f"No options available for {symbol}.")
        chain, note = built
        full = chain._views.get("all", chain)
        raw = AnnotatedResult(
            result=full,
            metadata={
                "symbol": symbol,
                "current_price": float(chain.underlying_price[0]) if getattr(chain, "underlying_price", None) else None,
            },
        )
        return chain, note, raw

    async def _load(symbol: str, update: bool = False):
        symbol = canonical_option_symbol(symbol)
        hit = loaded.get(symbol)
        if update or FORCE_REFRESH.get() or not hit or time.time() - hit[0] > CHAIN_TTL:
            # the cards of a tab ask for the same chain at once: one download serves them all
            key = (id(asyncio.get_running_loop()), symbol)
            task = flights.get(key)
            if task is None:
                task = flights[key] = asyncio.ensure_future(build(symbol))
                task.add_done_callback(lambda done: (flights.pop(key, None), done.cancelled() or done.exception()))
            chain, note, raw = await asyncio.shield(task)
            hit = loaded[symbol] = (time.time(), chain, note, raw)
            data_handler.LOADED_SYMBOLS[symbol] = chain             # the expiry and strike menus read it
            while len(loaded) > CHAINS_KEPT:
                oldest = min(loaded, key=lambda k: loaded[k][0])
                loaded.pop(oldest)
                data_handler.LOADED_SYMBOLS.pop(oldest, None)
        return hit[1], hit[2], hit[3]

    async def load_symbol(symbol: str, update: bool = False):
        chain, note, _ = await _load(symbol, update=update)
        if note:
            note_substitute("Yahoo Finance", note)
        return chain

    @classmethod
    async def fetch_data(cls, params, credentials=None, **kwargs):
        p = params or {}
        sym = p.get("symbol", "")
        use_cache = True if p.get("use_cache") is None else bool(p.get("use_cache"))
        _, note, raw = await _load(sym, update=not use_cache)
        if note:
            note_substitute("Yahoo Finance", note, "Delayed prices from Yahoo Finance (free feed, not real time)")
        return raw

    def choices(orig):
        return lambda symbol: orig(canonical_option_symbol(symbol))

    orig_forward, orig_marks = data_handler._implied_forward, data_handler.get_chain_marks

    def implied_forward(records):
        return orig_forward([r for r in records if r.get("theoretical_price") == r.get("theoretical_price")])    # no NaN

    async def get_chain_marks(symbol, expiration):
        marks = await orig_marks(symbol, expiration)
        box = SUBSTITUTE.get()
        if box:                                     # the payoff's prices are Cboe's optimizer, its marks the substitute's
            box["sources"] = "Cboe, Yahoo Finance"
        for mark in marks["contracts"].values():
            for k, v in list(mark.items()):
                if v != v:                          # NaN is truthy: openbb_cboe would use it as a price
                    mark[k] = None
        return marks

    def view_of(data, name: str):
        views = getattr(data, "_views", None) if type(data).__name__ == "SubstituteChain" else None
        if views is None:
            return data
        if name not in views:
            raise OpenBBError("Yahoo Finance returned no option prices recent enough to calculate implied volatility.")
        return views[name]

    def use_view(module: str, pick):
        mod = importlib.import_module(f"openbb_cboe.utils.options.{module}")
        orig = getattr(mod, module)

        def builder(data, *args, **kwargs):
            name = pick(kwargs)
            return orig(view_of(data, name) if name else data, *args, **kwargs)

        setattr(mod, module, builder)               # the routes import the builder when they are called

    use_view("create_surface", lambda kw: "priced")
    use_view("create_smile", lambda kw: "priced")
    use_view("create_term_structure", lambda kw: "priced" if kw.get("metric", "iv") == "iv" else None)
    use_view("create_stats", lambda kw: "all")

    optimizer = importlib.import_module("openbb_cboe.utils.options.trade_optimizer")
    orig_info, orig_ranked = optimizer.get_symbol_info, optimizer.get_strategies

    async def get_symbol_info(symbol, *args, **kwargs):
        return await orig_info(symbol.replace("-", "."), *args, **kwargs)           # the terminal's BRK-B is Cboe's BRK.B

    async def get_strategies(symbol, *args, **kwargs):
        return await orig_ranked(symbol.replace("-", "."), *args, **kwargs)

    optimizer.get_symbol_info, optimizer.get_strategies = get_symbol_info, get_strategies
    CboeOptionsChainsFetcher.fetch_data = fetch_data
    data_handler.load_symbol = load_symbol
    data_handler._implied_forward = implied_forward
    data_handler.get_chain_marks = get_chain_marks
    data_handler.get_expirations = choices(data_handler.get_expirations)
    data_handler.get_strikes = choices(data_handler.get_strikes)
    applied.append("Cboe options chains and analytics use a labelled Yahoo chain (IV and Greeks calculated) while Cboe refuses its feed")


def vix_curve_from_settlements(rows: list) -> list:
    """Monthly VX contracts (VX/Z6; the weeklies are VX47/X6) of one session as VX1, VX2, ... by expiration."""
    import re

    monthly = sorted((r for r in rows if r.get("product") == "VX" and re.fullmatch(r"VX/[A-Z]\d+", str(r.get("symbol")))
                      and r.get("price") and r["price"] > 0), key=lambda r: str(r["expiration"]))
    return [{"expiration": str(r["expiration"])[:7], "price": float(r["price"]), "symbol": f"VX{i + 1}",
             "date": str(r["settlement_date"])} for i, r in enumerate(monthly)]


# only products whose name is certain; any other root shows its code with a blank name
CFE_NAMES = {"VX": "Cboe Volatility Index (VX) futures", "VXM": "Mini Cboe Volatility Index (VXM) futures",
             "VA": "S&P 500 variance futures", "IBHY": "iBoxx iShares $ High Yield Corporate Bond Index futures",
             "IBIG": "iBoxx iShares $ Investment Grade Corporate Bond Index futures"}


def cfe_instruments_from_settlements(rows: list) -> list:
    return [{"future_root": root, "name": CFE_NAMES.get(root)} for root in sorted({r["product"] for r in rows if r.get("product")})]


def install_cboe_settlement_date(applied: list):
    """openbb_cboe stamps the latest settlement file with today's date: Friday's prices read 2026-11-21 on Saturday.
    Asking by date, from today back, returns the session the file belongs to."""
    try:
        from openbb_cboe.utils import helpers

        orig = helpers.get_settlement_prices
    except (ImportError, AttributeError):
        return

    async def get_settlement_prices(settlement_date=None, options=False, archives=False, final_settlement=False, **kwargs):
        if settlement_date is None and not archives and not final_settlement:
            settlement_date = helpers.ny_today()
        return await orig(settlement_date=settlement_date, options=options, archives=archives,
                          final_settlement=final_settlement, **kwargs)

    helpers.get_settlement_prices = get_settlement_prices
    applied.append("Cboe futures settlements carry their own session date")


# Cboe symbol -> Yahoo's. No yield indices: Cboe quotes TNX at ten times the yield, Yahoo in percent.
US_INDEX_SUBSTITUTES = {"SPX": "^GSPC", "NDX": "^NDX", "DJI": "^DJI", "RUT": "^RUT", "OEX": "^OEX", "MID": "^MID",
                        "VIX": "^VIX", "VIX9D": "^VIX9D", "VIX3M": "^VIX3M", "VIX6M": "^VIX6M", "VVIX": "^VVIX",
                        "VXN": "^VXN", "SKEW": "^SKEW"}


def index_snapshot_row(symbol: str, info: dict):
    """One Cboe-shaped snapshot row from Yahoo's quote; change in percentage points (the transform divides by 100)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    last, prev = info.get("regularMarketPrice"), info.get("regularMarketPreviousClose")
    if not last:
        return None
    stamp = info.get("regularMarketTime")
    return {"symbol": symbol, "name": info.get("longName") or info.get("shortName") or symbol, "current_price": last,
            "open": info.get("regularMarketOpen"), "high": info.get("regularMarketDayHigh"),
            "low": info.get("regularMarketDayLow"), "prev_day_close": prev,
            "price_change": last - prev if prev else None,
            "price_change_percent": (last / prev - 1) * 100 if prev else None,
            "last_trade_time": datetime.fromtimestamp(stamp, ZoneInfo("America/New_York")).replace(tzinfo=None) if stamp else None}


def install_cboe_market_fallbacks(applied: list):
    """VIX curve, futures list and U.S. index snapshots when Cboe refuses delayed_quotes."""
    import asyncio

    try:
        from openbb_cboe.models.futures_curve import CboeFuturesCurveFetcher as Curve
        from openbb_cboe.models.futures_instruments import CboeFuturesInstrumentsFetcher as Instruments
        from openbb_cboe.models.index_snapshots import CboeIndexSnapshotsFetcher as Snapshots
        from openbb_cboe.utils import helpers
        from openbb_core.app.model.abstract.error import OpenBBError
    except ImportError:
        return

    orig_curve, orig_list, orig_snap = Curve.aextract_data, Instruments.aextract_data, Snapshots.aextract_data

    async def settlements(day=None):
        frame = await helpers.get_settlement_prices(settlement_date=day)
        return frame.astype(object).where(frame.notna(), None).to_dict("records")

    async def curve(query, credentials, _orig=orig_curve, **kwargs):
        try:
            return await _orig(query, credentials, **kwargs)
        except Exception as e:  # noqa: BLE001
            if not cboe_refused(e):
                raise
        if query.symbol == "VX_AM":
            raise OpenBBError("Cboe refused the mid-morning VX levels (HTTP 403). Choose VX_EOD for settlement prices.")
        from datetime import date as _date

        days = [_date.fromisoformat(d.strip()[:10]) for d in str(query.date).split(",")] if query.date else [None]
        points = []
        for day in days:
            points += vix_curve_from_settlements(await settlements(day))
        if not points:
            raise OpenBBError("Cboe refused the VX levels (HTTP 403) and published no settlement prices for that date.")
        sessions = ", ".join(sorted({p["date"] for p in points}))
        note_substitute("Cboe", f"Cboe quotes blocked; monthly settlements of {sessions}")
        return points

    async def instruments(query, credentials, _orig=orig_list, **kwargs):
        try:
            return await _orig(query, credentials, **kwargs)
        except Exception as e:  # noqa: BLE001  (list_futures reads the 403 page as text, then calls .get on it)
            if not cboe_refused(e) and not (isinstance(e, AttributeError) and "'str'" in str(e)):
                raise
        rows = cfe_instruments_from_settlements(await settlements())
        if not rows:
            raise OpenBBError("Cboe refused its futures directory (HTTP 403) and published no settlement prices.")
        note_substitute("Cboe", "Cboe directory blocked; products from the settlement file")
        return rows

    def quote(item):
        import yfinance

        try:
            return yahoo_retry(lambda: index_snapshot_row(item[0], yfinance.Ticker(item[1]).get_info()))
        except Exception as e:  # noqa: BLE001
            say(f"[index snapshots] {item[0]} not read: {type(e).__name__}")
            return None

    def quotes():
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(4) as pool:
            return [row for row in pool.map(quote, US_INDEX_SUBSTITUTES.items()) if row]

    async def snapshots(query, credentials, _orig=orig_snap, **kwargs):
        try:
            return await _orig(query, credentials, **kwargs)
        except Exception as e:  # noqa: BLE001
            if query.region != "us" or not cboe_refused(e):
                raise
        rows = await asyncio.to_thread(quotes)
        if not rows:
            raise OpenBBError("Cboe refused its index feed and Yahoo Finance is not answering.")
        note_substitute("Yahoo Finance", f"Cboe blocked; {len(rows)} major indices from Yahoo, not the full list",
                        "Delayed prices from Yahoo Finance (free feed, not real time)")
        return rows

    for cls, func in ((Curve, curve), (Instruments, instruments), (Snapshots, snapshots)):
        cls.aextract_data = staticmethod(func)
        cls.extract_data = staticmethod(func)
    applied.append("VIX curve, Cboe futures list and U.S. index snapshots have labelled substitutes for a refused Cboe feed")


def bill_id_from_package(package_id):
    """GovInfo bill package BILLS-119hr4930enr -> Congress.gov bill id 119-hr-4930 (the form Bill Info and the Bill
    Viewer take); None for other collections (review D2: search rows could not drive the bill cards)."""
    import re

    m = re.match(r"^BILLS-(\d+)(hconres|sconres|hjres|sjres|hres|sres|hr|s)(\d+)[a-z]*$", str(package_id or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def install_congress_search_bill_id(applied: list):
    try:
        from openbb_government_us.congress.models.congress_search import CongressSearchFetcher as F
    except ImportError:
        return
    orig = F.transform_data

    def transform_data(query, data, _orig=orig, **kwargs):
        out = _orig(query, data, **kwargs)
        for r in out or []:
            b = bill_id_from_package(getattr(r, "package_id", None))
            if b:
                setattr(r, "bill_id", b)
        return out

    F.transform_data = staticmethod(transform_data)
    applied.append("Congressional search rows of bills carry their bill id (119-hr-4930)")


FRED_BOND_INDEX_UNITS = {"yield": "percent (effective yield)", "yield_to_worst": "percent (yield to worst)",
                         "oas": "percent (option-adjusted spread)", "total_return": "index level (total return)"}


def install_fred_bond_index_unit(applied: list):
    """FRED (ICE BofA) bond indices: every column's unit follows the index_type parameter (high yield 8.71 as a yield,
    641.24 as a total-return index), so the rows say it in a `unit` column (START P0-1 remainder, review A6)."""
    try:
        from openbb_fred.models.bond_indices import FredBondIndicesFetcher as F
    except ImportError:
        return
    orig = F.transform_data

    def transform_data(query, data, _orig=orig, **kwargs):
        out = _orig(query, data, **kwargs)
        unit = FRED_BOND_INDEX_UNITS.get(str(getattr(query, "index_type", "") or ""))
        if unit:
            for r in getattr(out, "result", out) or []:
                setattr(r, "unit", unit)
        return out

    F.transform_data = staticmethod(transform_data)
    F._terminal_cache_rev = "bond_unit"
    applied.append("FRED bond indices say their unit (yield / OAS in percent, total return as an index level)")


MTL_TZ = "America/Toronto"
INTRADAY = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}


def montreal_bar_times(dates, tz=MTL_TZ):
    """UTC wall-clock strings 'YYYY-MM-DD HH:MM:SS' -> the same instants in Montreal time, same format."""
    from pandas import to_datetime

    return to_datetime(dates).dt.tz_localize("UTC").dt.tz_convert(tz).dt.strftime("%Y-%m-%d %H:%M:%S")


FX_SYMBOL = r"[A-Z]{3}(?:[A-Z]{3})?=X"
FX_DAILY_INTERVALS = ("1d", "5d", "1wk", "1mo", "3mo")
FX_OPEN_AS_CLOSE_SINCE = "2010-08-04"          # earlier daily bars carry a real close (probed 2026-11-22, per pair and month)
FX_HOURLY_SPANS = ((20, "1mo"), (80, "3mo"), (170, "6mo"), (350, "1y"))          # longer: 2y, all the hourly history Yahoo has


def fx_daily_bars(daily, hourly=None):
    """Daily FX bars that close at 17:00 New York (probed 2026-11-22, 19 pairs over 2 years).

    Yahoo stamps a daily FX bar with its London date but its "close" is that date's 00:00 rate, equal to the open: the
    last day's move is missing and every change is the day before's (USD/JPY Friday 2026-11-20: 159.707, market close
    158.512). So: where there are hourly bars (UTC, stamped at their start) a session runs 17:00 to 17:00 New York and
    closes on its last bar; older days close on the next weekday bar's open, the same instant to within two hours. A day
    with neither has no known close and is left out, as are ticks printed while the market is shut. Before 2010-08-04
    Yahoo's close was a real one and is kept: shifting there would pull in its bad opens of 2008 (USD/JPY 107.51 for 102.4)."""
    import pandas as pd

    days = {}
    if daily is not None and len(daily):
        d = daily[pd.to_datetime(daily["date"]).dt.weekday < 5].sort_values("date").to_dict("records")
        for i, bar in enumerate(d):
            day = str(bar["date"])[:10]
            if day < FX_OPEN_AS_CLOSE_SINCE:
                close = bar["close"]
            elif i + 1 < len(d):
                close = d[i + 1]["open"]
            else:
                continue
            days[day] = [bar["open"], max(bar["high"], close), min(bar["low"], close), close]
    if hourly is not None and len(hourly):
        h = hourly.dropna(subset=["open", "high", "low", "close"]).sort_values("date")
        ny = pd.to_datetime(h["date"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        session = (ny + pd.Timedelta(hours=7)).dt.normalize()          # 17:00 New York starts the next date
        trading = session.dt.weekday < 5                               # Friday 17:00 to Sunday 17:00: shut
        g = h[trading].groupby(session[trading].dt.strftime("%Y-%m-%d"))
        agg = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        for day, o, hi, lo, close in agg.itertuples(name=None):
            days[day] = [o, hi, lo, close]
    return pd.DataFrame([[day, *v, 0] for day, v in sorted(days.items())], columns=["date", "open", "high", "low", "close", "volume"])


def fx_resample(days, interval):
    """Weeks (Monday), months and quarters (first day) from corrected days: Yahoo's own carry the same wrong close."""
    import pandas as pd

    if interval == "1d" or not len(days):
        return days
    freq = {"5d": "W", "1wk": "W", "1mo": "M", "3mo": "Q"}[interval]
    g = days.groupby(pd.to_datetime(days["date"]).dt.to_period(freq))
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    out.insert(0, "date", out.index.start_time.strftime("%Y-%m-%d"))
    return out.reset_index(drop=True)


def install_fx_session_closes(applied: list):
    """Daily/weekly/monthly FX history served through yf_download (boards, /x/ charts, exchange-rate history) gets
    fx_daily_bars; install_fx_chart_fix routes the TradingView datafeed here too.
    Installed before the Montreal-time patch, so the hourly bars it asks for are still in UTC."""
    import datetime as dt
    import re

    import openbb_yfinance.utils.helpers as yh
    import pandas as pd

    orig = yh.yf_download

    def per_symbol(data, symbols):
        if "symbol" in data.columns:
            return {s: g for s, g in data.groupby("symbol")}
        return {symbols[0]: data} if len(symbols) == 1 else {}

    def yf_download(symbol, start_date=None, end_date=None, interval="1d", *args, _orig=orig, **kwargs):
        syms = str(symbol).upper().split(",")
        fx = [s for s in syms if re.fullmatch(FX_SYMBOL, s)]
        if str(interval) not in FX_DAILY_INTERVALS or not fx:
            return _orig(symbol, start_date, end_date, interval, *args, **kwargs)
        parts, others = [], [s for s in syms if s not in fx]
        if others:
            try:
                rest = _orig(",".join(others), start_date, end_date, interval, *args, **kwargs)
                parts.append(rest if "symbol" in rest.columns else rest.assign(symbol=others[0]))
            except Exception as e:  # noqa: BLE001  (no data for the others must not blank the pairs)
                print(f"fx closes: no bars for {','.join(others)} ({type(e).__name__})", flush=True)
        today = dt.date.today()
        start = pd.to_datetime(start_date).date() if start_date else None
        end = pd.to_datetime(end_date).date() if end_date else None
        # a past end is applied below (its close needs the following bar); today's or none keeps the running session
        until = end.isoformat() if end and end < today else "9999"
        since = start.isoformat() if start else ""
        try:
            daily = per_symbol(_orig(",".join(fx), start_date, None, "1d", *args, **kwargs), fx)
            age = (today - start).days if start else 20
            span = next((p for limit, p in FX_HOURLY_SPANS if age <= limit), "2y")
            try:
                hourly = per_symbol(_orig(",".join(fx), None, None, "1h", period=span, ignore_tz=False), fx)
            except Exception as e:  # noqa: BLE001  (the daily bars still give every close but the last)
                print(f"fx closes: no hourly bars for {','.join(fx)} ({type(e).__name__}); latest session left out", flush=True)
                hourly = {}
            for s in fx:
                days = fx_daily_bars(daily.get(s), hourly.get(s))
                days = days[(days["date"] >= since) & (days["date"] <= until)]
                if len(days):
                    parts.append(fx_resample(days, str(interval)).assign(symbol=s))
        except Exception:
            if not parts:          # nothing else to answer with
                raise
        if not parts:
            from openbb_core.provider.utils.errors import EmptyDataError
            raise EmptyDataError()
        out = pd.concat(parts, ignore_index=True)
        for col in ("dividends", "capital_gains", "stock_splits"):
            if col in out:
                out[col] = out[col].fillna(0)
        if len(syms) == 1:
            return out.drop(columns=["symbol"])
        return out.sort_values(["date", "symbol"], ignore_index=True)

    yh.yf_download = yf_download
    applied.append("Yahoo FX daily closes at 17:00 New York (were each day's opening rate)")


def fx_session_date(epoch) -> str | None:
    """The FX session a moment belongs to (17:00 to 17:00 New York, dated by its end); None while the market is shut."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    d = (dt.datetime.fromtimestamp(epoch, ZoneInfo("America/New_York")).replace(tzinfo=None) + dt.timedelta(hours=7)).date()
    return d.isoformat() if d.weekday() < 5 else None


def fx_decimals(v) -> int:
    a = abs(v or 0)          # as the boards (composite price_decimals)
    return 2 if a >= 50 else 4 if a >= 1 else 5 if a >= 0.01 else 6


# The chart front end writes every legend and axis price with two decimals (EUR/USD "1.10 -0.00"). Its two legend
# formatters (one inside a closure, so only reachable in the page text) read window.__terminalPriceDp instead, and the
# script keeps that and the axis at the boards' precision while the main series is a currency pair (the symbol can be
# changed inside the chart, so it is looked at again).
FX_LEGEND_FORMAT = (r"return Number\(v\)\.toLocaleString\(undefined, *\{ *minimumFractionDigits: 2, "
                    r"maximumFractionDigits: 2 *\}\);")
FX_LEGEND_FORMAT_DP = ("var dp = window.__terminalPriceDp || 2; "
                       "return Number(v).toLocaleString(undefined, {minimumFractionDigits: dp, maximumFractionDigits: dp});")
FX_PRECISION_SCRIPT = """<script>(function () {
  var done = null;
  setInterval(function () {
    var reg = window.__PYWRY_TVCHARTS__ || {};
    Object.keys(reg).forEach(function (id) {
      var e = reg[id], s = e && e.seriesMap && e.seriesMap.main;
      var info = e && e._resolvedSymbolInfo && e._resolvedSymbolInfo.main, bars = e && e._seriesRawData && e._seriesRawData.main;
      if (!s || !info || !bars || !bars.length) return;
      var a = Math.abs(bars[bars.length - 1].close), was = window.__terminalPriceDp || 2;
      var dp = info.type === "Currency" ? (a >= 50 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 6) : 2;
      if (dp === was && (dp === 2 || done === s)) return;
      window.__terminalPriceDp = dp; done = s;
      s.applyOptions({ priceFormat: { type: "price", precision: dp, minMove: Math.pow(10, -dp) } });
      window.dispatchEvent(new CustomEvent("pywry:legend-refresh", { detail: { chartId: id } }));
    });
  }, 300);
})();</script>"""


def install_fx_chart_fix(applied: list, tvdf=None, tvn=None):
    """TradingView chart of a currency pair. Its datafeed has three paths of its own, all wrong for FX: candles from
    Ticker.history (close = open), a header change from Yahoo's previous close with two decimals (EUR/USD "1.10 +0.00"),
    and "Market Open" all weekend (Yahoo calls FX 24x7). Candles now come through yf_download, the header counts from
    the previous 17:00 New York close, and ticks printed while the market is shut are ignored."""
    import datetime as dt
    import re
    from zoneinfo import ZoneInfo

    if tvdf is None:
        from openbb_yfinance.utils import tvchart_datafeed as tvdf, tvchart_native as tvn

    london = ZoneInfo("Europe/London")          # the zone the chart formats a pair's dates in
    intervals = {"1d": "1d", "1w": "1wk", "1M": "1mo"}
    state: dict = {}                            # symbol -> the running session and the close before it

    def is_fx(symbol):
        return bool(re.fullmatch(FX_SYMBOL, str(symbol or "").upper()))

    orig_history = tvdf.yf_fetch_full_history

    def yf_fetch_full_history(symbol, resolution):
        interval = intervals.get(tvdf.normalize_resolution(resolution))
        if not interval or not is_fx(symbol):
            return orig_history(symbol, resolution)
        import openbb_yfinance.utils.helpers as yh

        try:
            df = yh.yf_download(symbol, "1990-01-01", None, interval, auto_adjust=False)
        except Exception as e:  # noqa: BLE001  (no data is shown as no data, never as the uncorrected bars)
            print(f"fx chart: no {interval} bars for {symbol} ({type(e).__name__})", flush=True)
            return []
        raw_bars = []
        for day, o, h, lo, c in df[["date", "open", "high", "low", "close"]].itertuples(index=False, name=None):
            raw_bars.append({
                "time": int(dt.datetime.fromisoformat(str(day)[:10]).replace(tzinfo=london).timestamp()),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": 0,
            })
        return _sanitize_tv_bars(raw_bars)

    def emit_header(app, s):
        def put(slot, text, color=None):
            app.emit("toolbar:marquee-set-item", {"ticker": slot, "text": text, **({"styles": {"color": color}} if color else {})})

        if not (s.get("prev") and _safe_finite_float(s.get("last"))):
            return
        chg, dp = s["last"] - s["prev"], fx_decimals(s["last"])
        color = "#26a69a" if chg >= 0 else "#ef5350"
        put("ws-price", f"{s['last']:,.{dp}f}", color)
        put("ws-change", f"{'▲' if chg >= 0 else '▼'} {chg:+,.{dp}f} ({chg / s['prev'] * 100:+.2f}%)", color)
        for slot, key in (("ws-open", "open"), ("ws-high", "high"), ("ws-low", "low")):
            put(slot, f"{s[key]:,.{dp}f}")
        put("ws-volume", "—")          # spot FX has no consolidated volume
        put("ws-session", *session_label())

    def session_label():
        return ("Market Open", "#26a69a") if fx_session_date(time.time()) else ("Closed", "#787b86")

    orig_seed = tvdf._seed_marquee

    def _seed_marquee(app, symbol):
        orig_seed(app, symbol)
        if not is_fx(symbol):
            return
        bars = tvdf.BarCache().get(symbol, "1d")
        if len(bars) < 2:
            for slot in ("ws-price", "ws-change"):
                app.emit("toolbar:marquee-set-item", {"ticker": slot, "text": "—"})
            return
        b = bars[-1]
        s = state[symbol.upper()] = {"day": dt.datetime.fromtimestamp(b["time"], london).date().isoformat(), "prev": bars[-2]["close"],
                                     "last": b["close"], "open": b["open"], "high": b["high"], "low": b["low"]}
        emit_header(app, s)

    orig_update = tvdf.RealtimeStreamer._update_marquee

    def _update_marquee(self, symbol, msg):
        if not is_fx(symbol):
            return orig_update(self, symbol, msg)
        s, price = state.get(symbol.upper()), msg.get("price")
        if not s or price is None:
            return None
        try:
            at = float(msg.get("time") or time.time())
        except (TypeError, ValueError):
            at = time.time()
        day = fx_session_date(at / 1000 if at > 1e11 else at)          # Yahoo's stream stamps milliseconds
        if not day or day < s["day"]:
            return None
        px = float(price)
        if day > s["day"]:
            s.update(day=day, prev=s["last"], open=px, high=px, low=px)
        s.update(last=px, high=max(s["high"], px), low=min(s["low"], px))
        emit_header(self._app, s)
        return None

    orig_overnight, orig_label = tvdf.is_overnight, tvdf.current_session_label
    tvdf.yf_fetch_full_history = yf_fetch_full_history
    tvdf._seed_marquee = _seed_marquee
    tvdf.RealtimeStreamer._update_marquee = _update_marquee
    tvdf.is_overnight = lambda epoch, symbol: fx_session_date(epoch) is None if is_fx(symbol) else orig_overnight(epoch, symbol)
    tvdf.current_session_label = lambda symbol: session_label() if is_fx(symbol) else orig_label(symbol)
    if tvn is not None:
        orig_html = tvn.tvchart_widget_html

        async def tvchart_widget_html(symbol: str = "AAPL", interval: str = "1d", theme: str = "dark") -> str:
            html = re.sub(FX_LEGEND_FORMAT, lambda _: FX_LEGEND_FORMAT_DP, await orig_html(symbol, interval, theme))
            return html.replace("</body>", f"{FX_PRECISION_SCRIPT}</body>", 1) if "</body>" in html else html + FX_PRECISION_SCRIPT

        tvn.tvchart_widget_html = tvchart_widget_html
    applied.append("TradingView FX candles, header change and session use 17:00 New York closes")


def install_cot_canada(applied: list):
    """The curated COT universe has five currencies and not the Canadian dollar (CME contract 090741)."""
    import openbb_cftc.models.cot_index as ci
    import openbb_cftc.utils.cot_markets as cm

    code = "CFTC_090741"
    markets = list(cm.COT_MARKETS)
    if any(m["code"] == code for m in markets):
        return
    at = next((i + 1 for i, m in enumerate(markets) if m["label"] == "Franc"), len(markets))
    markets.insert(at, {"code": code, "label": "Canadian Dollar", "asset_class": "currencies", "contract": "CANADIAN DOLLAR"})
    cm.COT_MARKETS = tuple(markets)
    ci.CftcCotIndexFetcher._terminal_cache_rev = "cot_cad"          # CFTC answers are cached on disk
    applied.append("COT currencies include the Canadian dollar")


def install_montreal_times(applied: list):
    """Every time shown is Montreal time (owner, 2026-11-19). Two sources send naive times in another zone (probed
    2026-11-19): Yahoo intraday bars in the exchange's local time (SAP.DE 17:25 = Frankfurt) and Nasdaq Nordic notices in
    CET (the fetcher asks for timeZone=CET). Yahoo: ask yfinance for zone-aware bars (OpenBB then converts them to UTC)
    and express them in Montreal time; daily bars are dates and are left alone. Nordic: the CET time is given its zone,
    so the answer carries an offset and the browser converts it. yfinance is not disk-cached (not in CACHE_POLICY)."""
    import openbb_yfinance.utils.helpers as yh

    orig = yh.yf_download

    def yf_download(symbol, start_date=None, end_date=None, interval="1d", *args, _orig=orig, **kwargs):
        if str(interval) not in INTRADAY:
            return _orig(symbol, start_date, end_date, interval, *args, **kwargs)
        if len(args) >= 5:                                  # period, prepost, actions, progress, ignore_tz
            args = args[:4] + (False,) + args[5:]
        else:
            kwargs["ignore_tz"] = False
        data = _orig(symbol, start_date, end_date, interval, *args, **kwargs)
        if "date" in getattr(data, "columns", []) and len(data):
            data["date"] = montreal_bar_times(data["date"])
        return data

    yh.yf_download = yf_download                          # every caller imports it inside its function, at call time
    applied.append("Yahoo intraday bars in Montreal time (were the exchange's local time)")

    from zoneinfo import ZoneInfo

    import openbb_nasdaq.models.nordic_news as nn

    orig_parse = nn.parse_release_time
    cet = ZoneInfo("Europe/Stockholm")                       # CET/CEST, the zone Nasdaq Nordic is asked for

    def parse_release_time(value, _orig=orig_parse):
        d = _orig(value)
        return d.replace(tzinfo=cet) if d is not None and d.tzinfo is None else d

    nn.parse_release_time = parse_release_time
    applied.append("Nasdaq Nordic notices carry their CET offset")


def install_tmx_quote_symbol_order(applied: list):
    """TMX quotes: extract_data fetches normalize_symbol(s) (SU.TO -> SU) but transform_data orders the rows by the
    symbols as typed, so "SU.TO" raised KeyError 'SU' for the quote and for the option chain built on it (review C2:
    manual TMX on SU.TO answered 500). The rows are ordered by the normalized symbols instead."""
    try:
        from openbb_tmx.models.equity_quote import TmxEquityQuoteFetcher as F
        from openbb_tmx.utils.helpers import normalize_symbol
    except ImportError:
        return
    orig = F.transform_data

    def transform_data(query, data, _orig=orig, **kwargs):
        sym = getattr(query, "symbol", None)
        if isinstance(sym, str) and sym:
            norm = ",".join(normalize_symbol(s) for s in sym.split(","))
            if norm != sym:
                query = query.model_copy(update={"symbol": norm})
        return _orig(query, data, **kwargs)

    F.transform_data = staticmethod(transform_data)
    applied.append("TMX quotes accept SU.TO-style tickers (rows ordered by the normalized symbol)")


def cds_spread_bp(spread, notation):
    """A CDS index print's spread in basis points from its own notation (CFTC/DTCC "Spread notation-Leg 1"): 3 decimal
    (0.006112 = 61.12 bp), 4 basis points; 1 is a monetary amount or price (no spread in bp). The prints mix notations,
    so the raw column cannot carry one unit (review A6: "Spread 0.005938")."""
    if not isinstance(spread, (int, float)) or isinstance(spread, bool):
        return None
    n = str(notation or "").strip()
    if n == "3":
        return round(spread * 1e4, 4)
    if n == "4":
        return float(spread)
    return None


def install_cds_spread_bp(applied: list):
    try:
        from openbb_cftc.models.cds_index_trades import CftcCdsIndexTradesData as D, CftcCdsIndexTradesFetcher as F
        from pydantic.fields import FieldInfo
    except ImportError:
        return
    if "spread_bp" not in D.model_fields:
        # the model allows no extra field and the API serialises only declared ones: declare it on the provider's model
        # (before the platform builds its provider interface from these fields)
        D.__annotations__["spread_bp"] = float | None
        D.model_fields["spread_bp"] = FieldInfo(annotation=float | None, default=None, title="Spread (bp)",
                                                description="Spread in basis points, from `spread` and its notation (3: decimal x"
                                                            " 10,000; 4: basis points). Empty for a price or monetary amount.")
        D.model_rebuild(force=True)
    orig = F.transform_data

    def transform_data(query, data, _orig=orig, **kwargs):
        out = _orig(query, data, **kwargs)
        for r in getattr(out, "result", out) or []:
            r.spread_bp = cds_spread_bp(r.spread, r.spread_notation)
        return out

    F.transform_data = staticmethod(transform_data)
    F._terminal_cache_rev = "spread_bp"               # CFTC answers are cached on disk: older ones lack the column
    applied.append("CFTC CDS index prints carry the spread in bp from each print's notation")


# USAspending award / recipient pages: the server writes str(float) into data-value and the page's script only adds
# thousands separators, so amounts read "$8,600,000,000.0" and "$0.0" beside tables showing 50.28B (review A6).
# This script shows dollars with one scale letter like the tables (the exact amount stays in the tooltip).
USASPENDING_SCRIPT = """<script>
  document.querySelectorAll("[data-value]").forEach(function (el) {
    var v = parseFloat(el.getAttribute("data-value"));
    if (!isFinite(v)) return;
    var p = el.getAttribute("data-prefix"), a = Math.abs(v), s;
    if (p === "pct") { el.textContent = v.toFixed(2) + "%"; return; }
    if (p === "usd") {
      s = a >= 1e12 ? (a / 1e12).toFixed(2) + "T" : a >= 1e9 ? (a / 1e9).toFixed(2) + "B" : a >= 1e6 ? (a / 1e6).toFixed(2) + "M"
        : a.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
      el.textContent = (v < 0 ? "-$" : "$") + s;
      el.title = "$" + v.toLocaleString("en-US", { maximumFractionDigits: 2 });
      return;
    }
    el.textContent = v.toLocaleString("en-US", { maximumFractionDigits: 2 });
  });
</script>"""


def usaspending_html(page: str) -> str:
    """The page with its number-formatting script replaced (unchanged when the script is not found)."""
    import re

    out, n = re.subn(r'<script>\s*document\.querySelectorAll\("\[data-value\]"\).*?</script>', lambda _m: USASPENDING_SCRIPT, str(page), count=1, flags=re.S)
    return out if n else page


def install_usaspending_format(applied: list):
    try:
        from openbb_government_us.treasury.utils import award_render, recipient_render
    except ImportError:
        return
    done = 0
    for mod, name in ((award_render, "render_award_info"), (recipient_render, "render_recipient_info")):
        orig = getattr(mod, name, None)
        if orig is None:
            continue

        def render(*args, _orig=orig, **kwargs):
            page = _orig(*args, **kwargs)
            return usaspending_html(page) if isinstance(page, str) else page

        setattr(mod, name, render)
        done += 1
    if done:
        applied.append("USAspending award and recipient pages show $ amounts as 8.60B, not $8,600,000,000.0")


def imf_title_without_scale(title: str) -> str:
    """IMF presentation-table header '(US Dollars, Millions)' -> '(US Dollars)'. The table's numbers are OBS_VALUE in
    units: probed 2026-11-18 IRFCL reserve assets, Japan 2026-10 1,215,878,000,000 and USA 2026-07 252,708,091,800 under
    a header saying millions (review A6: "(US Dollars, Millions)" beside 1.22T)."""
    import re

    return re.sub(r"\(([^()]*?),\s*(Thousands|Millions|Billions)\)", r"(\1)", str(title))


def install_imf_table_scale_label(applied: list):
    try:
        from openbb_imf import imf_router
    except ImportError:
        return
    orig = getattr(imf_router, "_rewrap_indent", None)
    if orig is None:
        return

    def _rewrap_indent(title, *args, **kwargs):     # header rows only (rows with a level go through _indent_title)
        return imf_title_without_scale(orig(title, *args, **kwargs))

    imf_router._rewrap_indent = _rewrap_indent
    applied.append("IMF presentation tables: header no longer says millions for numbers given in units")


# review H3: what people type for the big COT contracts, and the contract they mean. Probed 2026-11-18: "crude" listed
# palm oil, Brent last day and TMX diffs before WTI (067651, commodity "CRUDE OIL"); "S&P 500" put E-mini (13874A)
# fifth; "10-year" found nothing (the 10-year note is "UST 10Y NOTE", 043602).
COT_ALIASES = {
    "crude": ("CRUDE", ["067651", "06765T"]), "crude oil": ("CRUDE", ["067651", "06765T"]), "oil": ("CRUDE", ["067651", "06765T"]),
    "wti": ("WTI", ["067651"]), "brent": ("BRENT", ["06765T"]),
    "s&p 500": ("S&P 500", ["13874A"]), "s&p": ("S&P 500", ["13874A"]), "sp500": ("S&P 500", ["13874A"]), "spx": ("S&P 500", ["13874A"]),
    "es": ("S&P 500", ["13874A"]), "nasdaq": ("NASDAQ", ["209742"]), "nasdaq 100": ("NASDAQ", ["209742"]),
    "10-year": ("UST 10Y", ["043602"]), "10 year": ("UST 10Y", ["043602"]), "10y": ("UST 10Y", ["043602"]),
    "2-year": ("UST 2Y", ["042601"]), "2y": ("UST 2Y", ["042601"]), "5-year": ("UST 5Y", ["044601"]), "5y": ("UST 5Y", ["044601"]),
    "ust": ("UST", ["043602", "042601", "044601"]), "treasury": ("UST", ["043602", "042601", "044601"]),
    "gold": ("GOLD", ["088691"]), "natural gas": ("NAT GAS", ["023651"]), "nat gas": ("NAT GAS", ["023651"]),
}


def cot_search_plan(query):
    """(search text sent to the CFTC, contract codes to list first) for what was typed."""
    q = str(query or "").strip()
    term, pins = COT_ALIASES.get(q.lower(), (q, []))
    return term, pins


def cot_pin_first(rows: list, pins: list) -> list:
    """Rows of the pinned contracts first, in the pins' order; the rest as the CFTC listed them."""
    rank = {p: i for i, p in enumerate(pins)}
    first = sorted((r for r in rows if r.get("cftc_contract_market_code") in rank), key=lambda r: rank[r["cftc_contract_market_code"]])
    return first + [r for r in rows if r.get("cftc_contract_market_code") not in rank]


def install_cot_search_aliases(applied: list):
    from openbb_cftc.models.cot_search import CftcCotSearchFetcher as F

    orig = F.aextract_data

    async def aextract_data(query, credentials, **kwargs):
        term, pins = cot_search_plan(query.query)
        if term != query.query:
            query = query.model_copy(update={"query": term})
        rows = list(await orig(query, credentials, **kwargs) or [])
        have = {r.get("cftc_contract_market_code") for r in rows}
        for p in pins:                  # the CFTC's "CRUDE" answer (20 rows) lacks WTI-PHYSICAL 067651: fetch it by its code
            if p not in have:
                try:
                    rows += [r for r in await orig(query.model_copy(update={"query": p}), credentials, **kwargs) or []
                             if r.get("cftc_contract_market_code") == p]
                except Exception:  # noqa: BLE001  (a pinned contract no longer reported: the search answer stands)
                    pass
        return cot_pin_first(rows, pins)

    # Fetcher.__init_subclass__ copies aextract_data to extract_data and fetch_data calls extract_data: patch both
    F.aextract_data = F.extract_data = staticmethod(aextract_data)
    F._terminal_cache_rev = "cot_aliases2"           # CFTC answers are cached on disk (HANDOFF trap)
    applied.append("COT search: '10-year', 'crude', 'S&P 500' and similar find the main contract first")


def install_sec_fact_any_tag(applied: list):
    """SEC compare/company_facts takes any us-gaap tag, not only its fixed list: the tags companies use now for capex
    (AMZN PaymentsToAcquireProductiveAssets, NVDA PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets) and the
    older revenue tag (SalesRevenueNet, AAPL before FY2018) answered 422 (Compare coverage, INFRASTRUCTURE W2). The SEC
    returns 404 for a tag a company never used, which the fetcher reports as no data."""
    from openbb_sec.models.compare_company_facts import SecCompareCompanyFactsQueryParams as Q
    from pydantic.fields import FieldInfo

    old = Q.model_fields["fact"]
    if old.annotation is not str:
        Q.__annotations__["fact"] = str
        Q.model_fields["fact"] = FieldInfo(annotation=str, default="Revenues", description=old.description,
                                           json_schema_extra=old.json_schema_extra)
        Q.model_rebuild(force=True)
    applied.append("SEC company facts: any us-gaap tag (capex and older revenue tags for Compare)")


def funding_apr(funding_8h):
    """Simple annualised funding in percent from the 8-hour rate in percent (x 3 periods a day x 365), not compounded."""
    return None if funding_8h is None else round(funding_8h * 3 * 365, 4)


def install_deribit_funding_apr(applied: list):
    from openbb_deribit.models.ticker import DeribitTickerData as D, DeribitTickerFetcher as F
    from pydantic.fields import FieldInfo

    if "funding_apr" not in D.model_fields:
        D.__annotations__["funding_apr"] = float | None
        D.model_fields["funding_apr"] = FieldInfo(annotation=float | None, default=None, title="Funding APR",
                                                  description="Funding 8H x 3 x 365, in percent: simple annual rate, not compounded.",
                                                  json_schema_extra={"x-unit_measurement": "percent", "x-widget_config": {"headerName": "Funding APR"}})
        D.model_rebuild(force=True)
    orig = F.transform_data

    def transform_data(query, data, _orig=orig, **kwargs):
        out = _orig(query, data, **kwargs)
        for r in getattr(out, "result", out) or []:
            r.funding_apr = funding_apr(r.funding_8h)
        return out

    F.transform_data = staticmethod(transform_data)
    F._terminal_cache_rev = "funding_apr"
    applied.append("Deribit perpetuals carry Funding APR (8-hour funding x 3 x 365)")


def book_ladder(rows, get=getattr):
    """An order book read as a ladder, per instrument: asks from the highest price down to the best ask, then bids from
    the best bid down, so the spread sits in the middle (the source sends all bids, then all asks, and a card's first
    rows were ten bids with the asks off-screen)."""
    groups = {}
    for r in rows:
        groups.setdefault(get(r, "symbol"), []).append(r)
    out = []
    for g in groups.values():
        def desc(side):
            return sorted((r for r in g if (get(r, "side") == "ask") == side), key=lambda r: -(get(r, "price") or 0.0))
        out += desc(True) + desc(False)
    return out


def aeo_identified(rows, get=getattr):
    """AEO rows a reader can identify: EIA sends a few with neither a series name nor a unit and a placeholder id
    (table 73 hydrogen: "NA_NA_NA_NA_NA_NA_NA_NA" = 1559.17); a number nobody can name is left out (review H3)."""
    return [r for r in rows if get(r, "series_name", None) or get(r, "unit", None)]


def install_eia_aeo_identified(applied: list):
    from openbb_us_eia.models.aeo import EiaAeoFetcher as F

    orig = F.transform_data

    def transform_data(query, data, _orig=orig, **kwargs):
        out = _orig(query, data, **kwargs)
        if hasattr(out, "result"):
            out.result = aeo_identified(out.result or [])
            return out
        return aeo_identified(out or [])

    F.transform_data = staticmethod(transform_data)
    F._terminal_cache_rev = "aeo_identified"
    applied.append("EIA AEO: rows with neither a series name nor a unit left out (placeholder ids NA_NA_...)")


GRID_KEYS = ("respondent", "series_type", "fuel_type", "subregion")


def grid_drop_partial(rows, get=getattr):
    """EIA-930 hourly series: the newest hours of an aggregate (US48, regions) are summed from the balancing authorities
    that have filed so far (US48 day-ahead forecast 2026-11-20 06-08 UTC: 205,294 / 78,252 / 52,263 MW after 424,617).
    Per series, trailing hours below half the same hour a day earlier are dropped (no grid halves in a day); a series
    with no value a day earlier is left as sent. Interchange (can be near zero or negative) is never touched."""
    import datetime as _dt

    series = {}
    for r in rows:
        if str(get(r, "series_type", "") or "") in ("TI",):
            continue
        series.setdefault(tuple(get(r, k, None) for k in GRID_KEYS), []).append(r)
    drop = set()
    for rs in series.values():
        rs = sorted(rs, key=lambda r: get(r, "date"))
        at = {get(r, "date"): get(r, "value") for r in rs}
        for r in reversed(rs):
            v, d = get(r, "value"), get(r, "date")
            prev = at.get(d - _dt.timedelta(days=1)) if isinstance(d, _dt.datetime) else None
            if v is None or not prev or prev <= 0 or v >= 0.5 * prev:
                break
            drop.add(id(r))
    return [r for r in rows if id(r) not in drop]


def install_eia_grid_hours(applied: list):
    """EIA-930 hourly grid data: 'hourly' periods are UTC (EIA's definition; 'local-hourly' is the local one), sent naive.
    They get their zone so the browser shows them in Montreal time, and partial trailing hours are dropped."""
    import datetime as _dt
    import importlib

    done = []
    for mod, cls in (("demand", "EiaElectricityGridDemandFetcher"), ("demand_by_subregion", "EiaElectricityGridDemandBySubregionFetcher"),
                     ("generation_by_fuel", "EiaElectricityGridGenerationByFuelFetcher"), ("interchange", "EiaElectricityGridInterchangeFetcher")):
        F = getattr(importlib.import_module(f"openbb_us_eia.models.electricity_grid.{mod}"), cls)
        orig = F.transform_data

        def transform_data(query, data, _orig=orig, **kwargs):
            out = _orig(query, data, **kwargs)
            if str(getattr(query, "frequency", None) or "hourly") != "hourly":
                return out
            rows = getattr(out, "result", out) or []
            for r in rows:
                d = getattr(r, "date", None)
                if isinstance(d, _dt.datetime) and d.tzinfo is None:
                    r.date = d.replace(tzinfo=_dt.timezone.utc)
            rows = grid_drop_partial(rows)
            if hasattr(out, "result"):
                out.result = rows
                return out
            return rows

        F.transform_data = staticmethod(transform_data)
        F._terminal_cache_rev = "grid_utc_partial"
        done.append(mod)
    applied.append("EIA hourly grid data in UTC (shown in Montreal time); partial newest hours of aggregates dropped (" + ", ".join(done) + ")")


def install_deribit_book_ladder(applied: list):
    from openbb_deribit.models.order_book import DeribitOrderBookFetcher as F

    orig = F.transform_data

    def transform_data(query, data, _orig=orig, **kwargs):
        out = _orig(query, data, **kwargs)
        if hasattr(out, "result"):
            out.result = book_ladder(out.result or [])
            return out
        return book_ladder(out or [])

    F.transform_data = staticmethod(transform_data)
    F._terminal_cache_rev = "book_ladder"
    applied.append("Deribit order book as a ladder (asks high to low above bids)")


def oecd_listed_dataflows(entries: list) -> list:
    """OECD dataflow list without the catalogue's placeholders and test flows, archived and uncategorised flows last.
    Probed 2026-11-18 (1,548 flows): DF_T2SEV4EYSJTJMT3HVC8I / DF_TIG8YUX3T8CSYX7HSAMZ are named after their own id,
    DF_TEST_MEI is "TEST - Main Economic Indicators Publication", DF_DD_HCL_TEST "DD HCL order hierarcy test";
    163 flows have no topic; the list opened on "…2023 Dashboard Archived" (review F3)."""
    import re

    out = []
    for e in entries:
        full = str(e.get("value") or "")
        short = full.split("@")[-1]
        label = str(e.get("label") or "").strip()
        bare = short[3:] if short.startswith("DF_") else short
        if not label or (label in (short, bare) and re.fullmatch(r"(?=.*\d)[A-Z0-9]{16,}", bare)):
            continue                                       # placeholder named after its own hash id (DF_NAAG "NAAG" stays)
        if re.search(r"(^|_)TEST(_|$)", short) or re.match(r"TEST\b", label) or re.search(r"\btest$", label):
            continue
        out.append(e)
    return sorted(out, key=lambda e: (bool(re.search(r"(?i)\barchived?\b", str(e.get("label") or ""))),
                                      not (e.get("topic_name") or e.get("topic"))))


def install_oecd_dataflow_list(applied: list):
    try:
        from openbb_oecd.utils.metadata._public_api_mixin import PublicApiMixin
    except ImportError:
        return
    orig = PublicApiMixin.list_dataflows

    def list_dataflows(self, topic=None):
        return oecd_listed_dataflows(orig(self, topic=topic))

    PublicApiMixin.list_dataflows = list_dataflows
    applied.append("OECD dataflow list: placeholder and test flows dropped, archived and uncategorised flows last")


def oecd_unit_text(rec: dict):
    """What an OECD GDP figure in USD is, from the record's own codes, or None when it is not a USD amount (growth,
    deflator, index). Probed 2026-11-18, France: DF_QNA real GDP USD_PPP / LR / REF_YEAR_PRICE 2020 / LA, 3.83T;
    DF_QNA_EXPENDITURE_USD nominal USD_PPP / V / LA, 4.60T; DF_EO GDPV_USD USD_EXC / L / BASE_PER 2021, 3.18T (2026).
    The Snapshot tab showed the three side by side with no unit (review A5)."""
    def ok(v):
        return v not in (None, "") and str(v).lower() != "nan"

    um, pb = str(rec.get("UNIT_MEASURE") or ""), str(rec.get("PRICE_BASE") or "")
    if not um.startswith("USD"):
        return None
    ref = next((int(float(rec[k])) for k in ("REF_YEAR_PRICE", "BASE_PER") if ok(rec.get(k))), None)
    volume = pb in ("L", "LR")
    parts = [{"USD_PPP": "USD at PPPs", "USD_EXC": "USD at constant exchange rates" if volume else "USD at exchange rates"}.get(um, um)]
    if volume:
        parts.append(f"chain-linked volume, {ref} prices" if ref else "chain-linked volume")
    elif pb == "V":
        parts.append("current prices")
    if str(rec.get("ADJUSTMENT") or "") == "Y":
        parts.append("seasonally adjusted")
    if str(rec.get("TRANSFORMATION") or "") == "LA" and str(rec.get("FREQ") or "") == "Q":
        parts.append("annual rate")
    return ", ".join(parts)


def install_oecd_gdp_units(applied: list):
    """OECD real, nominal and forecast GDP rows carry a `unit` column built from the source's codes (currency
    conversion, price base and reference year, adjustment), so 3.83T (PPP, 2020 prices) and 3.18T (exchange rates, 2021
    prices) can be told apart."""
    try:
        from openbb_oecd.models import gdp_forecast, gdp_nominal, gdp_real
    except ImportError:
        return
    done = 0
    for mod, name in ((gdp_real, "OECDGdpRealFetcher"), (gdp_nominal, "OECDGdpNominalFetcher"), (gdp_forecast, "OECDGdpForecastFetcher")):
        cls = getattr(mod, name, None)
        if cls is None:
            continue
        orig = cls.transform_data

        def transform_data(query, data, _orig=orig, **kwargs):
            out = _orig(query, data, **kwargs)
            units = {}
            for r in data or []:
                units.setdefault(str(r.get("REF_AREA_label") or r.get("REF_AREA") or ""), oecd_unit_text(r))
            single = next(iter(set(units.values()))) if len(set(units.values())) == 1 else None
            for m in out or []:
                u = units.get(str(getattr(m, "country", "") or "")) or single
                if u:
                    setattr(m, "unit", u)
            return out

        cls.transform_data = staticmethod(transform_data)
        cls._terminal_cache_rev = "gdp_unit"
        done += 1
    if done:
        applied.append("OECD GDP rows say their unit (PPP or exchange rates, price base, annual rate)")


def install_tmx_asset_info_escape_fix(applied: list):
    """TMX Asset Info: _plain() escapes a value and _stat() escapes it again, so BCE's sector read "Media &amp;
    Telecommunications" (review A3). _stat now escapes exactly once whatever it is given."""
    from html import unescape

    try:
        from openbb_tmx.utils import asset_info as tai
    except ImportError:
        return
    orig = tai._stat

    def _stat(label, value):
        return orig(label, unescape(value) if isinstance(value, str) else value)

    tai._stat = _stat
    applied.append("TMX Asset Info escapes values once")


def install_tmx_holdings_currency_fix(applied: list):
    try:
        from openbb_tmx.models import etf_holdings as teh
        from openbb_tmx.utils.helpers import get_all_etfs
    except ImportError:
        return
    orig = teh.TmxEtfHoldingsFetcher.aextract_data

    async def aextract_data(query, credentials=None, **kwargs):
        rows = await orig(query, credentials, **kwargs)
        if not rows:
            return rows
        etfs = await get_all_etfs(use_cache=getattr(query, "use_cache", True))
        fund = next((e for e in etfs or () if isinstance(e, dict) and str(e.get("symbol") or "").upper() == query.symbol), {})
        return holdings_in_fund_currency(rows, fund.get("currency"))

    # Fetcher.__init_subclass__ copied aextract_data onto extract_data, which is what fetch_data calls: replace both
    teh.TmxEtfHoldingsFetcher.aextract_data = teh.TmxEtfHoldingsFetcher.extract_data = staticmethod(aextract_data)
    applied.append("TMX ETF holdings in the fund's currency")


TMX_MOVERS_POOL = 100      # TMX's getMarketMovers answers at most the 100 most active names by volume


def rank_movers(rows: list, category: str, limit: int) -> list:
    """TMX market movers: the API returns the most active names by volume whatever sortOrder says (2026-11-18,
    gainers == losers: CNQ -0.97%, ENB, T... first), so "gainers" listed losers. Rank that pool by percentChange in
    the category's direction, keep only names that moved that way, cut to the limit (review A3)."""
    up = category != "losers"
    pool = [r for r in rows or () if isinstance(r, dict) and isinstance(r.get("percentChange"), (int, float))
            and (r["percentChange"] > 0 if up else r["percentChange"] < 0)]
    pool.sort(key=lambda r: r["percentChange"], reverse=up)
    return pool[:max(int(limit or 0), 0)]


def install_tmx_movers_fix(applied: list):
    try:
        from openbb_tmx.models import market_movers as tmm
    except ImportError:
        return
    orig = tmm.TmxMarketMoversFetcher.aextract_data

    async def aextract_data(query, credentials=None, **kwargs):
        from openbb_core.provider.utils.errors import EmptyDataError

        rows = await orig(query.model_copy(update={"limit": max(TMX_MOVERS_POOL, query.limit)}), credentials, **kwargs)
        ranked = rank_movers(rows, query.category, query.limit)
        if not ranked:
            raise EmptyDataError(f"No {query.category} among the {TMX_MOVERS_POOL} most active names on {query.exchange.upper()} today.")
        return ranked

    tmm.TmxMarketMoversFetcher.aextract_data = tmm.TmxMarketMoversFetcher.extract_data = staticmethod(aextract_data)   # see above
    applied.append("TMX gainers/losers ranked by % change among the most active")


def tmx_asset_info_cached(orig, ttl: float = 180.0):
    """Short in-memory cache + in-flight coalescing for TMX Asset Info HTML; complete answers only."""
    import asyncio
    import collections

    cache: collections.OrderedDict[tuple[str, str], tuple[float, str]] = collections.OrderedDict()
    inflight: dict[tuple[str, str], asyncio.Future] = {}

    async def wrapped(symbol: str = "AC", theme: str = "dark"):
        key = (str(symbol or "").strip().upper(), str(theme or "dark").lower())
        now = time.monotonic()
        hit = cache.get(key)
        if hit and now - hit[0] < ttl:
            cache.move_to_end(key)
            return hit[1]
        fut = inflight.get(key)
        if fut is not None:
            return await asyncio.shield(fut)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        inflight[key] = fut
        try:
            html = await orig(symbol, theme)
            if isinstance(html, str) and 'class="ai-wrap"' in html:
                cache[key] = (time.monotonic(), html)
                cache.move_to_end(key)
                while len(cache) > 128:
                    cache.popitem(last=False)
            if not fut.done():
                fut.set_result(html)
            return html
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            inflight.pop(key, None)

    return wrapped


def install_tmx_asset_info_cache(applied: list):
    try:
        from openbb_tmx.utils import asset_info as tai
    except ImportError:
        return
    tai.asset_info_html = tmx_asset_info_cached(tai.asset_info_html)
    applied.append("TMX asset info cached in memory (180 s)")


_TV_INFO_TTL = 600.0
_TV_BAR_TTL = 300.0
_TV_MAX_CACHE = 256


def _clean_json_val(obj):
    """Recursively replace NaN/Inf floats and numpy scalars with strict JSON-safe primitives."""
    import math

    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _clean_json_val(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_json_val(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (bytes, bytearray)):
        try:
            return _clean_json_val(obj.item())
        except Exception:  # noqa: BLE001
            return None
    return obj


def _safe_finite_float(val):
    import math

    if val is None or isinstance(val, bool):
        return None
    try:
        num = float(val)
    except (TypeError, ValueError, OverflowError):
        return None
    return num if math.isfinite(num) else None


def _safe_nonneg_int(val) -> int:
    num = _safe_finite_float(val)
    if num is None or num < 0:
        return 0
    return int(num)


def _sanitize_tv_bars(bars) -> list[dict]:
    """Keep only TradingView bars with valid timestamp and finite prices (dropping any NaN/Inf bar)."""
    out: list[dict] = []
    for b in bars or ():
        if not isinstance(b, dict):
            continue
        t = _safe_finite_float(b.get("time"))
        c = _safe_finite_float(b.get("close"))
        if t is None or c is None:
            continue
        ohlc = {}
        bad = False
        for k in ("open", "high", "low"):
            if k in b and b[k] is not None:
                fv = _safe_finite_float(b[k])
                if fv is None:
                    bad = True
                    break
                ohlc[k] = round(fv, 6)
        if bad:
            continue
        item = {"time": int(t), **ohlc, "close": round(c, 6)}
        if "open" in ohlc and "high" in ohlc and "low" in ohlc:
            item["high"] = round(max(ohlc["high"], ohlc["open"], item["close"]), 6)
            item["low"] = round(min(ohlc["low"], ohlc["open"], item["close"]), 6)
        if "volume" in b:
            item["volume"] = _safe_nonneg_int(b.get("volume", 0))
        out.append(item)
    return out


def _repair_daily_bar_from_meta(bars: list[dict], md: dict | None) -> list[dict]:
    """Repair or append today's daily bar when Yahoo returns NaN OHLC on an active/post-market bar."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    if not isinstance(md, dict) or not md:
        return bars
    price = _safe_finite_float(md.get("regularMarketPrice"))
    mkt_ts = _safe_finite_float(md.get("regularMarketTime"))
    if price is None or price <= 0 or mkt_ts is None:
        return bars
    tz_name = md.get("exchangeTimezoneName") or "America/New_York"
    try:
        zi = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        zi = ZoneInfo("UTC")
    mkt_dt = dt.datetime.fromtimestamp(int(mkt_ts), tz=zi)
    inst = str(md.get("instrumentType") or md.get("quoteType") or "").upper()
    if inst != "CRYPTOCURRENCY" and mkt_dt.weekday() >= 5:
        return bars
    mkt_date = mkt_dt.date()
    if bars:
        last_date = dt.datetime.fromtimestamp(int(bars[-1]["time"]), tz=zi).date()
        if last_date >= mkt_date:
            return bars
        prev_close = bars[-1]["close"]
    else:
        prev_close = _safe_finite_float(md.get("previousClose") or md.get("chartPreviousClose"))
    o = _safe_finite_float(md.get("regularMarketOpen")) or prev_close or price
    h = _safe_finite_float(md.get("regularMarketDayHigh")) or max(o, price)
    lo = _safe_finite_float(md.get("regularMarketDayLow")) or min(o, price)
    vol = _safe_nonneg_int(md.get("regularMarketVolume") or md.get("volume") or 0)
    day_ts = int(dt.datetime(mkt_dt.year, mkt_dt.month, mkt_dt.day, tzinfo=zi).timestamp())
    return list(bars) + [{
        "time": day_ts,
        "open": round(o, 6),
        "high": round(max(h, o, price), 6),
        "low": round(min(lo, o, price), 6),
        "close": round(price, 6),
        "volume": vol,
    }]


def install_tvchart_fix(applied: list):
    """Sanitize TradingView bars & WS JSON (prevents NaN crashes), bound/evict live chart streamers, parallel-enable
    PyWry callbacks, share cached bars/metadata, and show a loading/retry status overlay."""
    import collections
    import contextlib
    import functools
    import zlib

    try:
        from openbb_yfinance.utils import tvchart_datafeed as tvdf, tvchart_native as tvn
        from pywry import inline as pywry_inline
    except ImportError:
        return
    if getattr(tvdf, "_terminal_tvchart_patched", False):
        return

    meta_lock = threading.Lock()
    info_cache: collections.OrderedDict = collections.OrderedDict()
    bar_cache: collections.OrderedDict = collections.OrderedDict()
    info_locks = [threading.RLock() for _ in range(64)]
    bar_locks = [threading.RLock() for _ in range(64)]
    widget_streamers: dict = {}
    cleanup_timers: dict = {}

    def _bound_aux_caches(sym_key: str) -> None:
        for attr in ("info_cache", "tz_cache", "delay_cache", "reg_close_cache", "session_bounds_cache"):
            d = getattr(tvdf, attr, None)
            if isinstance(d, dict) and len(d) > 512:
                excess = len(d) - 256
                for k in list(d.keys()):
                    if k != sym_key:
                        d.pop(k, None)
                        excess -= 1
                        if excess <= 0:
                            break

    def _stop_streamer(streamer) -> None:
        if streamer is None:
            return
        with contextlib.suppress(Exception):
            streamer.stop()

    def _register_streamer(wid: str, streamer) -> None:
        if not wid or streamer is None:
            return
        with meta_lock:
            old_timer = cleanup_timers.pop(wid, None)
            if old_timer is not None:
                old_timer.cancel()
            widget_streamers[wid] = streamer
            active_widgets = getattr(pywry_inline._state, "widgets", {})
            connections = getattr(pywry_inline._state, "connections", {})
            wid_by_streamer = {id(st): w for w, st in widget_streamers.items()}
            lst = tvn._LIVE_CHARTS
            idx = 0
            while len(lst) > 12 and idx < len(lst):
                _app_old, st_old = lst[idx]
                old_wid = wid_by_streamer.get(id(st_old), "")
                if old_wid and old_wid in active_widgets and old_wid in connections:
                    idx += 1  # still on screen: keep streaming, evict the next oldest instead
                    continue
                lst.pop(idx)
                if old_wid and widget_streamers.get(old_wid) is st_old:
                    widget_streamers.pop(old_wid, None)
                _stop_streamer(st_old)
            try:
                from openbb_yfinance.utils import asset_info as yf_ai
                while len(yf_ai._LIVE_ASSET_INFO) > 12:
                    _, st_old = yf_ai._LIVE_ASSET_INFO.pop(0)
                    _stop_streamer(st_old)
            except Exception:  # noqa: BLE001
                pass

    def _finalize_disconnect(wid: str) -> None:
        streamer = None
        with meta_lock:
            cleanup_timers.pop(wid, None)
            if wid in getattr(pywry_inline._state, "connections", {}):
                return
            streamer = widget_streamers.pop(wid, None)
        _stop_streamer(streamer)
        orig_disconnect(wid, "client")

    orig_disconnect = getattr(pywry_inline, "_handle_widget_disconnect", None)
    if callable(orig_disconnect):
        def _handle_widget_disconnect(widget_id: str, reason: str = "unknown") -> None:
            if reason == "websocket_close":
                orig_disconnect(widget_id, reason)
                with meta_lock:
                    if widget_id in widget_streamers and widget_id not in cleanup_timers:
                        t = threading.Timer(20.0, _finalize_disconnect, args=(widget_id,))
                        t.daemon = True
                        cleanup_timers[widget_id] = t
                        t.start()
                return
            streamer = None
            with meta_lock:
                t = cleanup_timers.pop(widget_id, None)
                if t is not None:
                    t.cancel()
                streamer = widget_streamers.pop(widget_id, None)
            _stop_streamer(streamer)
            orig_disconnect(widget_id, reason)

        pywry_inline._handle_widget_disconnect = _handle_widget_disconnect

    if hasattr(pywry_inline, "_ws_sender_loop"):
        async def _ws_sender_loop(event_queue, websocket, widget_id: str) -> None:
            import asyncio

            try:
                while True:
                    event = await event_queue.get()
                    try:
                        payload = json.dumps(_clean_json_val(event), allow_nan=False, default=str)
                        await websocket.send_text(payload)
                    finally:
                        event_queue.task_done()
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass

        pywry_inline._ws_sender_loop = _ws_sender_loop

    orig_process = getattr(pywry_inline, "_process_callbacks", None)
    if callable(orig_process) and hasattr(pywry_inline, "_invoke_callback"):
        import io as _io
        import queue as _queue

        cb_workers = 4
        cb_queues = [_queue.Queue() for _ in range(cb_workers)]
        cb_started = threading.Event()
        cb_stdio_lock = threading.Lock()

        def _run_callback(item) -> None:
            callback, data, event_type, widget_id = item
            output_widget = None
            try:
                from pywry.state import is_deploy_mode

                store = pywry_inline._state.local_widgets if is_deploy_mode() else pywry_inline._state.widgets
                output_widget = (store.get(widget_id) or {}).get("output")
                if output_widget is None:
                    pywry_inline._invoke_callback(callback, data, event_type, widget_id)
                    return
                with cb_stdio_lock:
                    old_out, old_err = sys.stdout, sys.stderr
                    cap_out, cap_err = _io.StringIO(), _io.StringIO()
                    try:
                        sys.stdout, sys.stderr = cap_out, cap_err
                        pywry_inline._invoke_callback(callback, data, event_type, widget_id)
                    finally:
                        sys.stdout, sys.stderr = old_out, old_err
                if cap_out.getvalue():
                    output_widget.append_stdout(cap_out.getvalue())
                if cap_err.getvalue():
                    output_widget.append_stderr(cap_err.getvalue())
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    if output_widget is not None:
                        output_widget.append_stderr(f"[PyWry] Callback error: {e}\n")
                    else:
                        say(f"[tvchart] callback error ({event_type}): {type(e).__name__}: {redact(str(e))[:160]}")

        def _callback_worker(q) -> None:
            while True:
                item = q.get()
                try:
                    _run_callback(item)
                finally:
                    q.task_done()

        def _process_callbacks() -> None:
            # Single dispatcher; callbacks of one widget always go to the same worker, so they run in order,
            # while different widgets run in parallel. A second start (re-mount) is a no-op.
            if cb_started.is_set():
                return
            cb_started.set()
            for idx, q in enumerate(cb_queues):
                threading.Thread(target=_callback_worker, args=(q,), daemon=True, name=f"pywry-cb-{idx}").start()
            src = pywry_inline._state.callback_queue
            while True:
                try:
                    item = src.get(timeout=0.5)
                except _queue.Empty:
                    continue
                except Exception:  # noqa: BLE001
                    continue
                try:
                    wid = str(item[3] or "")
                except Exception:  # noqa: BLE001
                    wid = ""
                cb_queues[zlib.crc32(wid.encode("utf-8")) % cb_workers].put(item)

        pywry_inline._process_callbacks = _process_callbacks
    else:
        _not_applied("TradingView callback worker pool", RuntimeError("pywry callback internals not found"))

    orig_df_to_bars = getattr(tvdf, "_df_to_bars", None)
    if callable(orig_df_to_bars):
        def _df_to_bars(df) -> list[dict]:
            bars: list[dict] = []
            for ts, row in df.iterrows():
                t = _safe_finite_float(ts.timestamp())
                o = _safe_finite_float(row.get("Open") if hasattr(row, "get") else row["Open"])
                h = _safe_finite_float(row.get("High") if hasattr(row, "get") else row["High"])
                lo = _safe_finite_float(row.get("Low") if hasattr(row, "get") else row["Low"])
                c = _safe_finite_float(row.get("Close") if hasattr(row, "get") else row["Close"])
                if t is None or o is None or h is None or lo is None or c is None:
                    continue
                v = row.get("Volume", 0) if hasattr(row, "get") else row["Volume"]
                bars.append({
                    "time": int(t),
                    "open": round(o, 6),
                    "high": round(max(h, o, c), 6),
                    "low": round(min(lo, o, c), 6),
                    "close": round(c, 6),
                    "volume": _safe_nonneg_int(v),
                })
            return bars

        tvdf._df_to_bars = _df_to_bars
        tvdf._orig_df_to_bars = orig_df_to_bars

    orig_history = tvdf.yf_fetch_full_history

    @functools.wraps(orig_history)
    def yf_fetch_full_history(symbol: str, resolution: str) -> list[dict]:
        bars = _sanitize_tv_bars(orig_history(symbol, resolution))
        res = tvdf.normalize_resolution(resolution)
        if res == "1d" and getattr(tvdf, "_orig_df_to_bars", None) is not None:
            sym_key = str(symbol or "").upper()
            cached_info = getattr(tvdf, "info_cache", {}).get(sym_key)
            md = None
            if (
                isinstance(cached_info, dict)
                and _safe_finite_float(cached_info.get("regularMarketTime")) is not None
                and _safe_finite_float(cached_info.get("regularMarketPrice")) is not None
            ):
                md = cached_info
            else:
                with contextlib.suppress(Exception):
                    import yfinance as yf
                    fetched_md = yf.Ticker(symbol).get_history_metadata()
                    if isinstance(fetched_md, dict):
                        md = dict(fetched_md)
                        if isinstance(cached_info, dict) and cached_info.get("regularMarketOpen") is not None:
                            md.setdefault("regularMarketOpen", cached_info.get("regularMarketOpen"))
            bars = _repair_daily_bar_from_meta(bars, md)
        return bars

    tvdf.yf_fetch_full_history = yf_fetch_full_history

    orig_info = tvdf.yf_symbol_info

    @functools.wraps(orig_info)
    def yf_symbol_info(symbol: str):
        key = str(symbol or "").upper()
        lk = info_locks[zlib.crc32(key.encode("utf-8")) & 63]
        now = time.monotonic()
        with meta_lock:
            hit = info_cache.get(key)
            if hit and now - hit[0] < _TV_INFO_TTL:
                info_cache.move_to_end(key)
                return dict(hit[1])
        with lk:
            now = time.monotonic()
            with meta_lock:
                hit = info_cache.get(key)
                if hit and now - hit[0] < _TV_INFO_TTL:
                    info_cache.move_to_end(key)
                    return dict(hit[1])
                stale_hit = hit
            val = None
            for attempt in range(2):
                try:
                    val = orig_info(symbol)
                except Exception as e:  # noqa: BLE001
                    say(f"[tvchart] {symbol} info failed: {type(e).__name__}: {redact(str(e))[:160]}")
                    val = None
                if val is not None:
                    break
                if attempt == 0:
                    time.sleep(0.6)
            if val is not None:
                with meta_lock:
                    info_cache[key] = (time.monotonic(), dict(val))
                    info_cache.move_to_end(key)
                    while len(info_cache) > _TV_MAX_CACHE:
                        info_cache.popitem(last=False)
                    _bound_aux_caches(key)
                return dict(val)
            if stale_hit and stale_hit[1]:
                return dict(stale_hit[1])
            return None

    orig_invalidate = tvdf.BarCache.invalidate

    def get(self, symbol: str, resolution: str) -> list:
        res = tvdf.normalize_resolution(resolution)
        key = (str(symbol or "").upper(), res)
        with self._lock:
            if key in self._cache and self._cache[key]:
                return list(self._cache[key])
        now = time.monotonic()
        with meta_lock:
            hit = bar_cache.get(key)
            if hit and now - hit[0] < _TV_BAR_TTL:
                bar_cache.move_to_end(key)
                bars = list(hit[1])
                with self._lock:
                    self._cache[key] = list(bars)
                    while len(self._cache) > 64:
                        self._cache.pop(next(iter(self._cache)), None)
                return bars
            outer_stale = list(hit[1]) if hit and hit[1] else None
        derived_map = getattr(tvdf, "DERIVED_RESOLUTIONS", {})
        derived = derived_map.get(res)
        source_bars = None
        if derived:
            # Fetch the source resolution before taking this key's lock: holding one stripe while waiting
            # on another can deadlock two threads loading crossed derived/source keys.
            try:
                source_bars = self.get(symbol, derived["source"])
            except Exception as e:  # noqa: BLE001
                say(f"[tvchart] {symbol} ({res}) fetch failed: {type(e).__name__}: {redact(str(e))[:160]}")
                if outer_stale:
                    say(f"[tvchart] {symbol} ({res}) serving {len(outer_stale)} cached bars after upstream failure")
                    return list(outer_stale)
                return []
        lk = bar_locks[zlib.crc32(f"{key[0]}|{key[1]}".encode("utf-8")) & 63]
        with lk:
            now = time.monotonic()
            with meta_lock:
                hit = bar_cache.get(key)
                if hit and now - hit[0] < _TV_BAR_TTL:
                    bar_cache.move_to_end(key)
                    bars = list(hit[1])
                    with self._lock:
                        self._cache[key] = list(bars)
                    return bars
                stale_bars = list(hit[1]) if hit and hit[1] else None
            bars = []
            if derived:
                try:
                    bars = tvdf.aggregate_bars(source_bars, derived["factor"]) if source_bars else []
                except Exception as e:  # noqa: BLE001
                    say(f"[tvchart] {symbol} ({res}) fetch failed: {type(e).__name__}: {redact(str(e))[:160]}")
                    bars = []
                bars = _sanitize_tv_bars(bars)
            else:
                for attempt in range(2):
                    try:
                        bars = tvdf.yf_fetch_full_history(symbol, resolution)
                    except Exception as e:  # noqa: BLE001
                        say(f"[tvchart] {symbol} ({res}) fetch failed: {type(e).__name__}: {redact(str(e))[:160]}")
                        bars = []
                    bars = _sanitize_tv_bars(bars)
                    if bars or attempt == 1:
                        break
                    time.sleep(0.8)
            if bars:
                copied = list(bars)
                with meta_lock:
                    bar_cache[key] = (time.monotonic(), copied)
                    bar_cache.move_to_end(key)
                    while len(bar_cache) > _TV_MAX_CACHE:
                        bar_cache.popitem(last=False)
                with self._lock:
                    self._cache[key] = list(copied)
                    while len(self._cache) > 64:
                        self._cache.pop(next(iter(self._cache)), None)
                return list(copied)
            if stale_bars:
                say(f"[tvchart] {symbol} ({res}) serving {len(stale_bars)} cached bars after upstream failure")
                return list(stale_bars)
            return []

    def refresh(self, symbol: str, resolution: str) -> list:
        res = tvdf.normalize_resolution(resolution)
        key = (str(symbol or "").upper(), res)
        derived_map = getattr(tvdf, "DERIVED_RESOLUTIONS", {})
        try:
            if res in derived_map:
                derived = derived_map[res]
                source_bars = self.refresh(symbol, derived["source"])
                bars = tvdf.aggregate_bars(source_bars, derived["factor"]) if source_bars else []
            else:
                bars = tvdf.yf_fetch_full_history(symbol, res)
        except Exception as e:  # noqa: BLE001
            say(f"[tvchart] {symbol} ({res}) refresh failed: {type(e).__name__}: {redact(str(e))[:160]}")
            return []
        bars = _sanitize_tv_bars(bars)
        if bars:
            copied = list(bars)
            with meta_lock:
                bar_cache[key] = (time.monotonic(), copied)
                bar_cache.move_to_end(key)
                while len(bar_cache) > _TV_MAX_CACHE:
                    bar_cache.popitem(last=False)
            with self._lock:
                self._cache[key] = list(copied)
            return list(copied)
        return []

    def invalidate(self, symbol: str, resolution: str | None = None) -> None:
        orig_invalidate(self, symbol, resolution)
        sym = str(symbol or "").upper()
        with meta_lock:
            if resolution:
                bar_cache.pop((sym, tvdf.normalize_resolution(resolution)), None)
            else:
                for k in [k for k in bar_cache if k[0] == sym]:
                    bar_cache.pop(k, None)

    orig_show = getattr(tvn, "_show", None)
    if callable(orig_show):
        @functools.wraps(orig_show)
        def _tracked_show(app, symbol: str, resolution: str, **show_kwargs):
            before = len(tvn._LIVE_CHARTS)
            widget = orig_show(app, symbol, resolution, **show_kwargs)
            streamer = tvn._LIVE_CHARTS[-1][1] if len(tvn._LIVE_CHARTS) > before else None
            wid = str(getattr(widget, "label", "") or getattr(app, "label", "") or "")
            _register_streamer(wid, streamer)
            return widget

        tvn._show = _tracked_show

    try:
        from openbb_yfinance.utils import asset_info as yf_ai

        orig_ai_quotes = yf_ai._start_live_quotes

        @functools.wraps(orig_ai_quotes)
        def _tracked_ai_quotes(widget, symbol: str, info: dict):
            before = len(yf_ai._LIVE_ASSET_INFO)
            res = orig_ai_quotes(widget, symbol, info)
            streamer = yf_ai._LIVE_ASSET_INFO[-1][1] if len(yf_ai._LIVE_ASSET_INFO) > before else None
            wid = str(getattr(widget, "widget_id", "") or getattr(widget, "label", "") or "")
            _register_streamer(wid, streamer)
            return res

        yf_ai._start_live_quotes = _tracked_ai_quotes
    except ImportError as e:
        _not_applied("YFinance asset info stream cleanup", e)

    orig_html = tvn.tvchart_widget_html

    @functools.wraps(orig_html)
    async def tvchart_widget_html(symbol: str = "AAPL", interval: str = "1d", theme: str = "dark") -> str:
        html = await orig_html(symbol, interval, theme)
        # tvchart_bridge_register.js re-runs the chart's event registration, but
        # 10-events.js already did it when window.pywry existed at load. Twice-
        # registered data-response handlers race the chart rebuild on an interval
        # change and add a stray Line series beside the candles. Register only
        # once the page has loaded and no tvchart:create handler exists yet.
        html = html.replace(
            "window._tvRegisterEventHandlers(window.pywry);",
            "(function(w){function reg(){var h=w._handlers&&w._handlers['tvchart:create'];"
            "if(!(h&&h.length))window._tvRegisterEventHandlers(w);}"
            "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',reg);else reg();})(window.pywry);",
            1,
        )
        is_light = str(theme).lower() == "light"
        bg = "#ffffff" if is_light else "#1a1a19"
        fg = "#4b5563" if is_light else "#c3c2b7"
        ring = "#e5e7eb" if is_light else "#2c2c2a"
        btn_bg = "#f3f4f6" if is_light else "#252523"
        btn_bd = "#d1d5db" if is_light else "#3f3f3c"
        overlay = (
            f'<div id="obb-tv-loading" style="position:fixed;inset:0;z-index:9999;display:flex;flex-direction:column;'
            f'align-items:center;justify-content:center;gap:10px;background:{bg};color:{fg};'
            f'font:13px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;transition:opacity .15s ease">'
            f'<div id="obb-tv-spin" style="width:20px;height:20px;border:2px solid {ring};border-top-color:#3987e5;border-radius:50%;'
            f'animation:obbSpin .8s linear infinite"></div>'
            f'<span id="obb-tv-msg">Loading chart data…</span>'
            f'<button id="obb-tv-retry" type="button" style="display:none;margin-top:4px;padding:5px 12px;border-radius:4px;'
            f'border:1px solid {btn_bd};background:{btn_bg};color:inherit;font:inherit;cursor:pointer">Retry</button>'
            f'</div>'
            f'<style>@keyframes obbSpin{{to{{transform:rotate(360deg)}}}}</style>'
            '<script>(function(){'
            'var el=document.getElementById("obb-tv-loading"),sp=document.getElementById("obb-tv-spin"),'
            'msgEl=document.getElementById("obb-tv-msg"),btn=document.getElementById("obb-tv-retry"),hidden=false,closeTimer=null;'
            'if(btn){btn.addEventListener("click",function(){location.reload();});}'
            'function postReady(){try{if(window.parent&&window.parent!==window)window.parent.postMessage({type:"openbb-chart-ready"},"*");}catch(e){}}'
            'function hide(){if(hidden)return;hidden=true;if(closeTimer){clearTimeout(closeTimer);closeTimer=null;}'
            'if(el){el.style.opacity="0";setTimeout(function(){if(el&&el.parentNode)el.parentNode.removeChild(el);},160);}postReady();}'
            'function showErr(text){if(hidden)return;if(sp)sp.style.display="none";if(msgEl)msgEl.textContent=text||"Chart data unavailable.";if(btn)btn.style.display="inline-block";postReady();}'
            'function hasBars(){var reg=window.__PYWRY_TVCHARTS__;if(!reg)return false;for(var k in reg){var e=reg[k];'
            'if(e&&(e._seriesRawData&&e._seriesRawData.main&&e._seriesRawData.main.length||e._rawData&&e._rawData.length))return true;}return false;}'
            'var hooked=false,ticks=0;'
            'function wire(){if(hooked)return;if(hasBars()){requestAnimationFrame(hide);return;}'
            'var w=window.pywry,ready=(window.__obbTvBridgePatched||(ticks>25&&w&&typeof w.on==="function"));'
            'if(ready&&w&&typeof w.on==="function"){hooked=true;'
            '["tvchart:datafeed-history-response","tvchart:data-response"].forEach(function(ev){w.on(ev,function(d){'
            'if(hasBars()||(d&&d.bars&&d.bars.length)){requestAnimationFrame(hide);return;}'
            'if(d&&(d.error||d.noData||d.status==="no_data"||d.status==="error")){showErr(d.error||"No chart data available for this symbol.");}});});'
            'w.on("tvchart:datafeed-resolve-response",function(d){if(d&&d.error&&!hasBars())showErr(d.error);});'
            '["tvchart:error","error","ws:error"].forEach(function(ev){try{w.on(ev,function(d){'
            'if(hasBars()){requestAnimationFrame(hide);return;}'
            'var msg=(d&&typeof d==="object")?(d.error||d.message||d.reason):(typeof d==="string"?d:"");'
            'showErr(msg||"Chart connection error.");});}catch(e){}});'
            '["ws:close","close"].forEach(function(ev){try{w.on(ev,function(d){'
            'if(hasBars()){requestAnimationFrame(hide);return;}'
            'var msg=(d&&typeof d==="object")?(d.error||d.message||d.reason):(typeof d==="string"?d:"");'
            'if(!closeTimer){closeTimer=setTimeout(function(){closeTimer=null;if(!hidden&&!hasBars())showErr(msg||"Chart connection closed.");},4000);}});}'
            'catch(e){}});'
            'if(hasBars())requestAnimationFrame(hide);return;}'
            'ticks++;setTimeout(wire,20);}'
            'wire();setTimeout(function(){if(hasBars())hide();else if(!hidden)showErr("Chart data request timed out.");},35000);'
            '})();</script>'
        )
        return html.replace("</body>", f"{overlay}</body>", 1) if "</body>" in html else html + overlay

    tvdf.yf_symbol_info = yf_symbol_info
    tvdf.BarCache.get = get
    tvdf.BarCache.refresh = refresh
    tvdf.BarCache.invalidate = invalidate
    tvn.tvchart_widget_html = tvchart_widget_html
    tvdf._terminal_tvchart_patched = True
    applied.append("TradingView charts sanitize NaN bars, clean up streams, and show loading/retry status")


async def warm_tvchart(symbol: str = "^GSPC") -> dict:
    """Pre-mount PyWry's inline WS bridge and warm TradingView symbol metadata, bars, and PyWry assets."""
    import asyncio

    sym = str(symbol or "^GSPC").strip() or "^GSPC"
    try:
        from openbb_yfinance.utils import pywry_server, tvchart_datafeed
        from pywry import assets as pywry_assets, toolbar as pywry_toolbar
    except ImportError as e:
        return {"ok": False, "symbol": sym, "error": redact(str(e))[:200]}

    try:
        pywry_server.ensure_pywry_mounted()
        pywry_server.bind_loop()

        def _warm_sync():
            pywry_assets.get_pywry_css()
            pywry_assets.get_scrollbar_js()
            pywry_toolbar.get_toolbar_script(with_script_tag=True)
            tvchart_datafeed.yf_symbol_info(sym)
            bars = tvchart_datafeed.BarCache().get(sym, "1d")
            return len(bars or ())

        count = await asyncio.to_thread(_warm_sync)
        return {"ok": True, "symbol": sym, "bars": count}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "symbol": sym, "error": redact(str(e))[:200]}


FUT_MIN_AVG_VOLUME = 10     # contracts a day over three months: below this Yahoo's prices for a month are not a market
FUT_MAX_SPREAD = 0.01          # the bid/ask mid only when the spread is within 1% of it
FUT_RATE_ROOTS = {"ZQ"}        # interest-rate futures priced 100 - rate: the rows also carry the implied rate in percent


def futures_curve_points(quotes: list, expiration_of) -> list:
    """Yahoo futures quotes -> curve points [{expiration, price, basis}], in expiry order.

    The stock fetcher priced every listed month at (bid + ask) / 2: a one-sided quote (bid 0) halved the price and
    0/0 gave 0.00 (CL Dec-31 0.00 and Apr-30 26.35 among ~62s; GC Dec-29 2,339.9 after Jun-29 4,356.5), and months
    that hardly trade came with prices off the curve (GC Jul-28 4,741 between Jun 4,095 and Oct 4,158: no 3-month
    average volume, a 2-lot print). Here a month is shown only when it trades (Yahoo reports an average of at least
    10 contracts a day over three months); its price is the bid/ask mid when both sides are quoted within 1%, else
    the previous close, and the row says which ("basis"); a month with neither is left out."""
    out = []
    for q in quotes or ():
        if not isinstance(q, dict) or (q.get("volume_average") or 0) < FUT_MIN_AVG_VOLUME:
            continue
        bid, ask, prev = q.get("bid") or 0, q.get("ask") or 0, q.get("prev_close") or 0
        mid = (bid + ask) / 2
        if bid > 0 and ask >= bid and ask - bid <= FUT_MAX_SPREAD * mid:
            price, basis = round(mid, 4), "bid/ask mid"
        elif prev > 0:
            price, basis = prev, "previous close"
        else:
            continue
        row = {"expiration": expiration_of(q["symbol"]), "price": price, "basis": basis, "_root": str(q["symbol"])[:2]}
        if row["_root"] in FUT_RATE_ROOTS:          # CBOT 30-day Fed funds: price = 100 - the month's average rate (%)
            row["implied_rate"] = round(100 - price, 4)
        out.append(row)
    out.sort(key=lambda r: r["expiration"])
    if len(out) >= 5 and all(r["_root"] in FUT_NO_ARB for r in out):
        out = _drop_off_curve(out)
    for r in out:
        del r["_root"]
    return out


# Precious metals are stored and lent, so one month cannot sit far from the next: the spread is financing and storage,
# a fraction of a percent a month (selling a month 16% too dear and buying the next is free money). A month more than
# FUT_OFF_CURVE away from a robust straight line through the whole curve is bad data and is left out. Seasonal markets
# (natural gas, grains, power) are never screened: their months differ by design.
FUT_NO_ARB = {"GC", "SI", "PL", "PA"}
FUT_OFF_CURVE = 0.03


def _drop_off_curve(points: list) -> list:
    """Theil-Sen line (median of pairwise slopes; median intercept) through price by month; keep points within FUT_OFF_CURVE."""
    from statistics import median

    def month(e):
        y, m = str(e)[:7].split("-")
        return int(y) * 12 + int(m)
    xs, ys = [month(p["expiration"]) for p in points], [p["price"] for p in points]
    slope = median((ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] != xs[i])
    icpt = median(y - slope * x for x, y in zip(xs, ys))
    return [p for p, x, y in zip(points, xs, ys) if abs(y - (icpt + slope * x)) <= FUT_OFF_CURVE * abs(icpt + slope * x)]


def install_yfinance_futures_curve_fix(applied: list):
    """Futures curves (Yahoo): price each month with futures_curve_points instead of the stock (bid + ask) / 2."""
    try:
        from openbb_yfinance.utils import helpers as yh
    except ImportError:
        return
    if not hasattr(yh, "get_futures_quotes") or not hasattr(yh, "get_expiration_month"):
        raise RuntimeError("openbb_yfinance.utils.helpers no longer has get_futures_quotes / get_expiration_month")

    async def get_futures_quotes(symbols: list):
        import os
        from contextlib import redirect_stderr, redirect_stdout, suppress

        from aiohttp import ClientError
        from openbb_yfinance.models.equity_quote import YFinanceEquityQuoteFetcher
        from pandas import DataFrame

        data = []
        with open(os.devnull, "w") as devnull, redirect_stdout(devnull), redirect_stderr(devnull), suppress(ClientError):
            data = await YFinanceEquityQuoteFetcher().fetch_data(params={"symbol": ",".join(symbols)}, credentials={})
        rows = futures_curve_points([d.model_dump() for d in data or ()], yh.get_expiration_month)
        return DataFrame(rows, columns=["expiration", "price", "basis"] + (["implied_rate"] if any("implied_rate" in r for r in rows) else []))

    yh.get_futures_quotes = get_futures_quotes
    applied.append("Yahoo futures curves: traded months only, mid or previous close (labelled), no 0.00 or half prices")


def sec_standalone_quarters(records: list, frames) -> list:
    """Standalone quarters from SEC year-to-date chains, grouped by the period start (one fiscal year per chain).

    10-Q cash-flow items are filed year-to-date (Q1 3 months, H1, 9 months, FY). openbb_sec chains them by `fy`, which
    in SEC data is the fiscal year of the filing that last reported the fact, not of the period: chains of different
    years mix and most quarters are lost (ORCL capex showed only its August quarter each year; GOOGL only Q1 for
    2021-2024). Here each chain is the entries sharing one start date, sorted by end; a link of 60-120 days gives a
    quarter (the difference), so Q4 = FY - 9 months. A quarter already reported as three months is never derived again
    (matched by symbol, unit and period end); a link that is not about one quarter long derives nothing."""
    from collections import defaultdict
    from datetime import date, timedelta

    reported = {(r.get("symbol"), r.get("unit"), r.get("end")) for r in records if r.get("span") == "quarter" and r.get("end")}
    chains = defaultdict(list)
    for r in records:
        if r.get("span") in frames._CUMULATIVE_SPANS and r.get("start") and r.get("end") and r.get("val") is not None:
            chains[(r.get("symbol"), r.get("unit"), r["start"])].append(r)
    derived = []
    for (sym, unit, _start), entries in chains.items():
        prev = None                                   # (value, inclusive days, end) of the previous link
        for e in sorted(entries, key=lambda x: x["end"]):
            days = frames._span_days(e)
            if days is None:
                continue
            if prev is not None and e.get("span") != "quarter" and 60 <= days - prev[1] <= 120 and (sym, unit, e["end"]) not in reported:
                new = dict(e)
                new["val"] = e["val"] - prev[0]
                new["start"] = (date.fromisoformat(prev[2]) + timedelta(days=1)).isoformat()
                new["span"] = "quarter"
                if new.get("fp") == "FY":
                    new["fp"] = "Q4"                  # FY - 9 months is the fourth fiscal quarter, not the year
                new["calendar_year"], new["calendar_period"] = frames._nearest_quarter(e["end"])
                new.pop("frame", None)                # derived from a cumulative figure: not an SEC frame
                new["derived"] = True
                reported.add((sym, unit, e["end"]))
                derived.append(new)
            prev = (e["val"], days, e["end"])
    return derived


def install_sec_quarters_fix(applied: list):
    """Replace openbb_sec's year-to-date quarter derivation (grouped by the filing's fy) by sec_standalone_quarters."""
    try:
        from openbb_sec.utils import frames
    except ImportError:
        return
    if not hasattr(frames, "_derive_standalone_quarters"):
        return                                        # older openbb_sec (legacy backend): no derivation to fix
    frames._derive_standalone_quarters = lambda records: sec_standalone_quarters(records, frames)
    applied.append("SEC company facts: quarters derived from year-to-date chains per fiscal year")


def nasdaq_etf_path(path: str):
    """The same Nasdaq quote path asked as an ETF, or None when the path is not a per-symbol quote path asked as stocks.
    openbb_nasdaq sends assetclass=stocks for quotes, profiles, history and short interest; Nasdaq answers an ETF only
    with assetclass=etf ("Symbol not exists." for BWET or SPY as stocks; probed 2026-11-18)."""
    import re

    m = re.match(r"^(quote/[^/?]+/[^?]+\?(?:[^#]*&)?asset[cC]lass=)stocks(?=&|$)(.*)$", str(path))
    return m.group(1) + "etf" + m.group(2) if m else None


def install_nasdaq_etf_fallback(applied: list):
    """Nasdaq quote/profile/history/short-interest calls asked as stocks are asked again as an ETF when Nasdaq says the
    symbol does not exist or answers nothing (NYSE Arca ETFs such as BWET and SPY, and Nasdaq-listed ETFs such as QQQ,
    were empty on every Nasdaq equity card)."""
    try:
        from openbb_nasdaq.utils import helpers as nh
    except ImportError:
        return
    if not hasattr(nh, "get_nasdaq_data"):
        return
    orig = nh.get_nasdaq_data

    async def get_nasdaq_data(path, *args, **kwargs):
        alt = nasdaq_etf_path(path)
        if alt is None:
            return await orig(path, *args, **kwargs)
        try:
            data = await orig(path, *args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if "not exist" not in str(e).lower():
                raise
            return await orig(alt, *args, **kwargs)
        if data:
            return data
        try:
            return await orig(alt, *args, **kwargs) or data
        except Exception as e:  # noqa: BLE001  (not an ETF either: keep the stocks answer)
            if _is_nasdaq_rate_limit(e):
                raise
            return data

    nh.get_nasdaq_data = get_nasdaq_data
    applied.append("Nasdaq quotes, profiles, history and short interest also asked as an ETF when Nasdaq has no such stock")


def _iso_days(a, b) -> int:
    from datetime import date
    return (date.fromisoformat(str(a)[:10]) - date.fromisoformat(str(b)[:10])).days


_PCT_KEYS = ("period_ending", "fiscal_year", "fiscal_period", "calendar_year", "calendar_period", "tag", "label", "description",
             "parent", "sequence", "factor", "balance", "period_type")


def sec_pct_change(records: list, mode: str) -> list:
    """Growth records per tag, as fractions (0.25 = +25 %), the unit the SEC growth models declare (x-frontend_multiply 100).
      yoy: the same fiscal period of the prior fiscal year, which must end 340-390 days earlier (annual; quarterly y/y);
      pop: the quarter just before, which must end 70-110 days earlier (a missing quarter gives no figure, not a
           two-quarter change labelled q/q);
      ttm: the trailing-twelve-month window ending 340-390 days earlier (TTM growth is TTM against TTM a year before).
    openbb_sec multiplied q/q by 100 while y/y stayed a fraction (NVDA net income, quarter to 2025-10-26: 31.91B vs
    26.42B gave 20.77, shown as 2077 %), and answered period=ttm with q/q. A zero prior gives no figure."""
    from collections import defaultdict

    by_tag = defaultdict(list)
    for r in records:
        if r.get("value") is not None and r.get("period_ending"):
            by_tag[r["tag"]].append(r)
    out = []
    for recs in by_tag.values():
        recs.sort(key=lambda r: str(r["period_ending"]))
        same_fp = {(r.get("fiscal_year"), r.get("fiscal_period")): r for r in recs}
        for i, cur in enumerate(recs):
            if mode == "yoy":
                fy = cur.get("fiscal_year")
                prior = same_fp.get((fy - 1, cur.get("fiscal_period"))) if isinstance(fy, int) else None
                if prior is not None and not 340 <= _iso_days(cur["period_ending"], prior["period_ending"]) <= 390:
                    prior = None
            elif mode == "pop":
                prior = recs[i - 1] if i and 70 <= _iso_days(cur["period_ending"], recs[i - 1]["period_ending"]) <= 110 else None
            else:
                prior = next((p for p in reversed(recs[:i]) if 340 <= _iso_days(cur["period_ending"], p["period_ending"]) <= 390), None)
            if prior is None or not prior["value"]:
                continue
            pct = (cur["value"] - prior["value"]) / abs(prior["value"])
            rec = {k: cur.get(k) for k in _PCT_KEYS}
            rec.update(unit="percent", currency="", value=round(pct, 6), frequency=mode,
                       source=f"{mode}: ({cur['period_ending']}: {cur['value']} / {prior['period_ending']}: {prior['value']} - 1)"
                              f" = {round(pct * 100, 2)}%")
            out.append(rec)
    return out


def sec_ttm(records: list, shares_tags) -> list:
    """Trailing-twelve-month records from four consecutive quarters (each ending 70-110 days after the one before);
    durations summed, instants and share counts averaged, as openbb_sec does. openbb_sec took any four records in a
    row, so a missing quarter made a TTM of five quarters' span."""
    from collections import defaultdict

    by_tag = defaultdict(list)
    for r in records:
        if r.get("value") is not None and r.get("period_ending"):
            by_tag[r["tag"]].append(r)
    out = []
    for tag, recs in by_tag.items():
        recs.sort(key=lambda r: str(r["period_ending"]))
        avg = recs[0].get("period_type", "duration") == "instant" or tag in shares_tags
        for i in range(3, len(recs)):
            w = recs[i - 3:i + 1]
            if not all(70 <= _iso_days(w[j + 1]["period_ending"], w[j]["period_ending"]) <= 110 for j in range(3)):
                continue
            vals = [x["value"] for x in w]
            a = w[-1]
            rec = {k: a.get(k) for k in _PCT_KEYS}
            rec.update(fiscal_period="TTM", unit=a.get("unit"), currency=a.get("currency"), value=sum(vals) / 4 if avg else sum(vals),
                       source=f"TTM: {'avg' if avg else 'sum'}({' + '.join(str(x['period_ending']) for x in w)})", frequency="ttm")
            out.append(rec)
    return out


SEC_TTM_GROWTH = __import__("contextvars").ContextVar("sec_ttm_growth", default=False)


def install_sec_growth_fix(applied: list):
    """SEC income/balance/cash growth: q/q as a fraction like y/y, adjacent quarters only, contiguous TTM windows, and
    period=ttm answered with TTM growth (the models map both quarterly and ttm to q/q; a context flag set by the growth
    fetchers tells the resolver which was asked)."""
    try:
        from openbb_sec.models import balance_sheet_growth, cash_flow_growth, income_statement_growth
        from openbb_sec.utils import company_facts as cf
    except ImportError:
        return
    if not all(hasattr(cf, n) for n in ("_compute_pct_change", "_compute_ttm", "resolve_company_facts", "_STATEMENT_NAMES", "_SHARES_TAGS")):
        return                                        # older openbb_sec (legacy backend): no growth models to fix
    cf._compute_pct_change = lambda records, mode: sec_pct_change(records, mode)
    cf._compute_ttm = lambda records: sec_ttm(records, cf._SHARES_TAGS)
    orig_resolve = cf.resolve_company_facts

    def resolve_company_facts(facts_json, fiscal_years=None, period="both", pit_mode=False, include_preliminary=False):
        if period == "pop" and SEC_TTM_GROWTH.get():
            out = orig_resolve(facts_json, fiscal_years, "ttm", pit_mode, include_preliminary)
            for name in cf._STATEMENT_NAMES:
                lst = getattr(out, name)
                lst[:] = sec_pct_change(list(lst), "ttm")
            return out
        return orig_resolve(facts_json, fiscal_years, period, pit_mode, include_preliminary)

    cf.resolve_company_facts = resolve_company_facts
    for mod in (income_statement_growth, balance_sheet_growth, cash_flow_growth):
        fetcher = next(v for k, v in vars(mod).items() if k.endswith("Fetcher") and isinstance(v, type) and v.__module__ == mod.__name__)
        orig = fetcher.aextract_data

        async def aextract_data(query, credentials, _orig=orig, **kwargs):
            token = SEC_TTM_GROWTH.set(getattr(query, "period", None) == "ttm")
            try:
                return await _orig(query, credentials, **kwargs)
            finally:
                SEC_TTM_GROWTH.reset(token)

        fetcher.aextract_data = staticmethod(aextract_data)
        fetcher.extract_data = staticmethod(aextract_data)       # Fetcher.__init_subclass__ copied it there; fetch_data calls this
    applied.append("SEC growth: q/q as a fraction like y/y, adjacent quarters only, TTM growth for period=ttm")


def fred_keyless_answer(path: str, query: dict, fetch_obs, fetch_meta):
    """The JSON the FRED API would give for `path`/`query`, built from FRED's public download (no key).
    Only series observations and series metadata can be served; anything else raises the missing-key message.
    fetch_obs(sid, start, end, units, freq, agg) -> [(date, value|None)]; fetch_meta(sid) -> dict."""
    from openbb_core.app.model.abstract.error import OpenBBError

    sid = query.get("series_id") or ""
    if path.rstrip("/") == "/fred/series":
        meta = fetch_meta(sid)
        return {"seriess": [dict(meta, id=sid)]} if any(meta.values()) else {}      # {} is not cached: retried next time
    if path.rstrip("/") != "/fred/series/observations":
        raise OpenBBError(FRED_KEY_MESSAGE)
    known = {"series_id", "api_key", "file_type", "observation_start", "observation_end", "units", "frequency", "aggregation_method",
             "limit", "offset", "sort_order"}
    if set(query) - known:
        raise OpenBBError(FRED_KEY_MESSAGE)
    freq = query.get("frequency") or None
    rows = fetch_obs(sid, query.get("observation_start"), query.get("observation_end"), query.get("units") or None, freq,
                     (query.get("aggregation_method") or None) if freq else None)
    today = time.strftime("%Y-%m-%d")                 # the public download is today's vintage
    obs = [{"realtime_start": today, "realtime_end": today, "date": d, "value": "." if v is None else repr(v)} for d, v in rows]
    if query.get("sort_order") == "desc":
        obs.reverse()
    off = int(query.get("offset") or 0)
    lim = int(query.get("limit") or 100000)
    obs = obs[off:off + lim]
    return {"count": len(obs), "offset": off, "limit": lim, "observations": obs}


def install_fred_keyless(applied: list):
    """Without a FRED key, serve the FRED provider's series requests from FRED's public download (fixed-series cards
    such as commercial paper, bond indices, SLOOS, IORB, spot rates work keyless). With a key nothing changes."""
    import asyncio
    import urllib.parse

    try:
        from openbb_fred import fred_provider
        from openbb_fred.utils import rate_limiter as rl
    except ImportError:
        return                                      # not this backend's layout (V4): nothing to do
    import fred_public
    from openbb_core.app.model.abstract.error import OpenBBError

    orig = rl._fetch

    def obs(sid, start, end, units, freq, agg):
        try:
            return fred_public.observations(sid, start, end, units, freq, agg)
        except fred_public.FredPublicError as e:
            raise OpenBBError(f"FRED has no series '{sid}'." if e.missing else str(e)) from None

    async def _fetch(url, *a, **k):
        parts = urllib.parse.urlsplit(url)
        if parts.netloc == "api.stlouisfed.org":
            q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
            if "api_key" in q and not q["api_key"]:
                return await asyncio.to_thread(fred_keyless_answer, parts.path, q, obs, fred_public.metadata)
            auth = ((k.get("headers") or {}).get("Authorization") or "").strip()
            if "api_key" not in q and auth == "Bearer":
                raise OpenBBError(FRED_KEY_MESSAGE)
        return await orig(url, *a, **k)

    rl._fetch = _fetch
    for f in fred_provider.fetcher_dict.values():
        f.require_credentials = False               # the key is checked per request above instead
    applied.append("FRED series without a key (public download)")


FAILED_PATCHES: list = []


def _not_applied(patch: str, e: BaseException):
    """One clear line per runtime fix that did not apply (also listed in Data Explorers > Data status)."""
    why = f"module not in this backend ({e.name})" if isinstance(e, ImportError) and getattr(e, "name", None) else f"{type(e).__name__}: {e}"
    FAILED_PATCHES.append({"patch": patch, "why": redact(str(why))[:300]})
    say(f"[fixes] WARNING: not applied: {patch} ({why})")


def write_patch_status(name: str, applied: list):
    """local/run/patches-<name>.json: read by the terminal's Data status view."""
    try:
        local_paths.RUN_DIR.mkdir(parents=True, exist_ok=True)
        path = local_paths.RUN_DIR / f"patches-{name}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "applied": applied, "failed": FAILED_PATCHES,
                                   "patches": PATCH_RESULTS,
                                   "critical_failed": [r["id"] for r in PATCH_RESULTS if r["critical"] and not r["applied"]]}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        say(f"[fixes] could not write the patch status: {e}")


def install_oecd_fail_fast(applied: list):
    """OECD answers 429 when its hourly quota is used: fail fast with a plain message instead of retrying."""
    import inspect

    from openbb_oecd.utils import query_builder as qb

    def _make_request(url, headers=None, timeout=30):
        import requests

        req = requests.Request("GET", url, headers=headers or {})
        prepared = req.prepare()
        prepared.url = url                      # OECD needs the brackets un-encoded
        with requests.Session() as sess:
            resp = sess.send(prepared, timeout=min(timeout or 30, 60))
        if resp.status_code == 429:
            raise _OecdRateLimited(resp.headers.get("Retry-After") or "")
        resp.raise_for_status()
        return resp

    qb._make_request = _make_request
    builder = next(v for v in vars(qb).values() if inspect.isclass(v) and hasattr(v, "_fetch_with_multi_value_fallback") and hasattr(v, "fetch_data"))
    original = builder.fetch_data

    def fetch_data(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except _OecdRateLimited as e:
            wait = f" OECD asks to wait {e} seconds." if str(e).isdigit() else ""
            raise qb.OpenBBError("OECD rate limit reached (HTTP 429). OECD allows only a limited number of requests per hour." + wait
                                 + " Try again later, or switch source on this card.") from None

    builder.fetch_data = fetch_data
    applied.append("OECD fails fast on rate limit")


def install_eia_row_cap(applied: list):
    """EIA: cap rows unless a limit is set, retry its cold-cache 504s, at most four calls in flight."""
    # EIA (found 2026-11-14): every EIA dataset widget asks for *all* rows by default. One card could pull
    # 195,000 rows (67 MB) as 39 simultaneous page requests with a 10 s timeout each; a tab of them made EIA's API
    # time out. Unless the caller sets a limit, return the most recent rows only. EIA's gateway also answers 504
    # after 10 s on a cold query and then serves the same query quickly from its cache, so retry, and keep at most
    # four EIA calls in flight. The data browser imports paginate by name, so it is patched there too.
    from openbb_us_eia.utils import api_query as aq

    auto_rows = int(os.environ.get("OPENBB_EIA_AUTO_ROWS", "5000"))      # one page = one EIA request per card
    page_size = aq.PAGE_SIZE

    gate_box = []

    async def paginate(build_url, request, limit, amake_request, amake_requests, response_callback):
        import asyncio

        from openbb_core.app.model.abstract.error import OpenBBError
        from openbb_core.provider.utils.errors import EmptyDataError

        if not gate_box:
            gate_box.append(asyncio.Semaphore(4))

        async def get(url):
            last = None
            for attempt in range(3):
                try:
                    async with gate_box[0]:
                        return await amake_request(url, response_callback=response_callback, timeout=25)
                except (OpenBBError, EmptyDataError):
                    raise                       # a real API answer (bad filter, bad key): do not retry
                except Exception as e:  # noqa: BLE001  (timeout or EIA's 504 page)
                    last = e
                    await asyncio.sleep(1.5 * (attempt + 1))
            raise OpenBBError("EIA's API did not answer (timed out three times). It is usually slow on the first request for a dataset; try again in a minute.") from last

        req = dict(request)
        cap = limit
        if limit is None:
            cap = auto_rows
            req["sort"] = "desc"            # newest first, so the automatic cap keeps the latest data
        response = await get(build_url(req, offset=0, length=min(cap, page_size)))
        payload = response.get("response", {})
        rows = list(payload.get("data", []))
        total = int(payload.get("total") or 0)
        if total == 0 or not rows:
            raise EmptyDataError(await aq.empty_result_message(request, amake_request, response_callback))
        target = min(cap, total)
        if len(rows) < target:
            async def page(offset):
                r = await get(build_url(req, offset=offset, length=min(page_size, target - offset)))
                return r.get("response", {}).get("data", [])

            for part in await asyncio.gather(*(page(o) for o in range(len(rows), target, page_size))):
                rows.extend(part)
        return {"rows": rows[:target], "total": total, "date_format": payload.get("dateFormat")}

    aq.paginate = paginate
    try:
        from openbb_us_eia.models import data_browser as _db
        _db.paginate = paginate
    except Exception as e:  # noqa: BLE001
        _not_applied("EIA row cap in the EIA data browser", e)
    applied.append(f"EIA returns the latest {auto_rows:,} rows unless a limit is set")


class Patch(NamedTuple):
    """One runtime fix. install(applied) raises on failure (it may add plain-text lines to `applied`); critical fixes
    keep the backend safe or responsive, and a failure is shown in red on Data status and fails the release gate.
    targets: distribution -> version range the fix was verified against (">=2.0,<2.1"); outside it the fix is
    reported as unverified."""
    id: str
    title: str
    install: object
    critical: bool = False
    targets: dict = {}


# distribution -> version range every fix below was verified against (V5 environment, 2026-11-24)
VERIFIED = {
    "openbb-core": ">=2.0,<2.1", "openbb-platform-api": ">=2.0,<2.1", "openbb-mcp-server": ">=2.0,<2.1",
    "openbb-yfinance": ">=2.1,<2.2", "yfinance": ">=1.8,<1.9", "openbb-oecd": ">=2.0,<2.1", "openbb-us-eia": ">=2.0,<2.1",
    "openbb-fred": ">=2.0,<2.1", "openbb-nasdaq": ">=2.0,<2.1", "openbb-cboe": ">=2.0,<2.1", "openbb-tmx": ">=2.0,<2.1",
    "openbb-sec": ">=2.0,<2.1", "openbb-imf": ">=3.0,<3.1", "openbb-cftc": ">=2.0,<2.1", "openbb-deribit": ">=2.0,<2.1",
    "openbb-government-us": ">=2.0,<2.1", "openbb-federal-reserve": ">=2.0,<2.1", "openbb-congress-gov": ">=2.0,<2.1",
    "fastapi": ">=0.162,<0.163", "uvicorn": ">=0.54,<0.55", "aiohttp": ">=3.14,<3.15",
}


def _targets(*dists) -> dict:
    return {d: VERIFIED[d] for d in dists}


def _vtuple(v: str) -> tuple:
    out = []
    for part in str(v).split("."):
        m = re.match(r"\d+", part)
        if not m:
            break
        out.append(int(m.group(0)))
    return tuple(out)


def version_in(ver: str, spec: str) -> bool:
    """Is version `ver` inside a ">=a,<b" style range (also ==, <=, >)? Numeric parts only."""
    v = _vtuple(ver)
    for cond in [c.strip() for c in str(spec).split(",") if c.strip()]:
        m = re.match(r"(>=|<=|==|>|<)\s*(.+)$", cond)
        if not m:
            return False
        op, ref = m.group(1), _vtuple(m.group(2))
        n = max(len(v), len(ref))
        a, b = v + (0,) * (n - len(v)), ref + (0,) * (n - len(ref))
        if not {">=": a >= b, "<=": a <= b, "==": a == b, ">": a > b, "<": a < b}[op]:
            return False
    return True


def _dist_version(dist: str):
    try:
        import importlib.metadata as md
        return md.version(dist)
    except Exception:  # noqa: BLE001  (not installed in this environment)
        return None


PATCH_RESULTS: list = []
_GUARD_PORT = ["6900"]


def _install_guard_patch(applied: list):
    install_guard(_GUARD_PORT[0])


_install_guard_patch._wraps = "install_guard"


def apply_fixes():
    """Install every fix in PATCHES (order matters, see the registry) and record the outcome of each one in
    PATCH_RESULTS. Returns the plain-text lines the fixes added (the same list Data status shows)."""
    applied = []
    PATCH_RESULTS.clear()
    for patch in PATCHES:
        rec = {"id": patch.id, "title": patch.title, "critical": bool(patch.critical), "applied": False, "why": "", "versions": {}}
        unverified = []
        for dist, spec in patch.targets.items():
            ver = _dist_version(dist)
            rec["versions"][dist] = ver
            if ver and not version_in(ver, spec):
                unverified.append(f"unverified for {dist} {ver}")
        if unverified:
            rec["unverified"] = "; ".join(unverified)
        before = len(applied)
        try:
            patch.install(applied)
            rec["applied"] = True
            rec["detail"] = applied[before:]
        except Exception as e:  # noqa: BLE001
            _not_applied(patch.title, e)
            rec["why"] = FAILED_PATCHES[-1]["why"]
        PATCH_RESULTS.append(rec)
    say("[fixes] " + ("; ".join(applied) if applied else "none applied"))
    bad = [r["title"] for r in PATCH_RESULTS if r["critical"] and not r["applied"]]
    if bad:
        say("[fixes] CRITICAL fixes not applied: " + "; ".join(bad))
    return applied


def install_blocking_route_offload(applied: list):
    """Offload synchronous blocking work inside FastAPI async handlers so slow filings/PDFs/SDMX fetches cannot
    freeze the Uvicorn event loop (seen in v5-freeze dumps)."""
    import asyncio
    import functools

    offloaded: list[str] = []

    def _rebind_routes(router_obj, mapping: dict):
        for route in getattr(router_obj, "routes", ()) or ():
            ep = getattr(route, "endpoint", None)
            if ep in mapping:
                new_fn = mapping[ep]
                route.endpoint = new_fn
                dep = getattr(route, "dependant", None)
                if dep is not None and getattr(dep, "call", None) is ep:
                    dep.call = new_fn

    def _wrap_sync_coro(fn, *, empty_on_empty_error: bool = False):
        @functools.wraps(fn)
        async def _offloaded(*args, **kwargs):
            def _run_sync():
                coro = fn(*args, **kwargs)
                try:
                    val = coro.send(None)
                except StopIteration as stop:
                    return stop.value
                # Safety fallback if a future upstream version yields an awaitable inside one of these routes.
                coro.close()
                raise RuntimeError(f"Expected synchronous coroutine body in {fn.__name__}, got {type(val).__name__}")

            try:
                return await asyncio.to_thread(_run_sync)
            except Exception as exc:  # noqa: BLE001
                if empty_on_empty_error and type(exc).__name__ == "EmptyDataError":
                    return []
                raise

        return _offloaded

    # 1. SEC FinancialStatements thread-safety + blocking section_markdown / as_filed_widget offload
    try:
        from openbb_sec.models import sec_financials as sf
        from openbb_sec.utils import as_filed_widget as afw, section_markdown as sm

        fs_meta = threading.Lock()

        def _fs_lock(self):
            lk = getattr(self, "_terminal_fs_lock", None)
            if lk is None:
                with fs_meta:
                    lk = getattr(self, "_terminal_fs_lock", None)
                    if lk is None:
                        lk = threading.RLock()
                        self._terminal_fs_lock = lk
            return lk

        for mname in ("_ensure_xbrl", "get_statement", "get_item_sections"):
            orig_m = getattr(sf.FinancialStatements, mname, None)
            if callable(orig_m) and not getattr(orig_m, "_terminal_locked", False):
                @functools.wraps(orig_m)
                def _locked(self, *a, _orig=orig_m, **kw):
                    with _fs_lock(self):
                        return _orig(self, *a, **kw)

                _locked._terminal_locked = True
                setattr(sf.FinancialStatements, mname, _locked)

        if not getattr(sm.get_section_markdown, "_terminal_offloaded", False):
            async def get_section_markdown(
                symbol: str,
                section: str,
                calendar_year: str | None = None,
                calendar_period: str | None = None,
                use_cache: bool = True,
            ) -> str:
                from types import SimpleNamespace
                from openbb_sec.models.sec_financials import FinancialStatements, no_filing_message, resolve_section_url

                query = SimpleNamespace(
                    symbol=symbol, url=None, calendar_year=calendar_year, calendar_period=calendar_period, use_cache=use_cache,
                )
                annual_sections = {"company_overview", "risk_factors", "legal_proceedings"}
                url = await resolve_section_url(query, annual_default=section in annual_sections)
                if not url:
                    return no_filing_message(symbol)

                def _extract() -> str:
                    statements = FinancialStatements.from_url(url, use_cache)
                    with _fs_lock(statements):
                        if section == "company_overview":
                            return statements.business() or (
                                "No Business (Item 1) section was found in this filing. It is present"
                                " in annual reports (10-K) but not in quarterly reports (10-Q)."
                            )
                        if section == "risk_factors":
                            rendered = sm._join([(f.get("risk_factor"), f.get("text")) for f in statements.risk_factors()])
                            return rendered or (
                                "No Risk Factors (Item 1A) section was found in this filing. Item 1A"
                                " was not required before 2005, and some filings incorporate it by"
                                " reference to other sections."
                            )
                        if section == "segment_revenue":
                            return sm._join([(s.get("name"), sm._split_year_tables(s.get("text") or "")) for s in statements.segment_revenue()])
                        if section == "legal_proceedings":
                            item = statements.legal_proceedings()
                            return (item.get("text") or "") if item else ""
                        if section == "disclosures":
                            from openbb_sec.utils.filing_sections import reflow_plain_text
                            disclosures = statements.disclosures
                            is_xbrl = statements.is_xbrl
                            pairs = []
                            for key in disclosures:
                                info = disclosures[key]
                                if not (isinstance(info, dict) and (info.get("text") or "").strip()):
                                    continue
                                text_value = info["text"] if is_xbrl else reflow_plain_text(info["text"], force=True)
                                pairs.append((info.get("name") or key, text_value))
                            return sm._join(pairs)
                    return ""

                return await asyncio.to_thread(_extract)

            get_section_markdown._terminal_offloaded = True
            sm.get_section_markdown = get_section_markdown

        if not getattr(afw.get_as_filed_widget_rows, "_terminal_offloaded", False):
            orig_afw = afw.get_as_filed_widget_rows

            async def get_as_filed_widget_rows(
                symbol: str,
                statement_type: str,
                calendar_year: str | None = None,
                calendar_period: str | None = None,
                use_cache: bool = True,
            ) -> list[dict]:
                from types import SimpleNamespace
                from openbb_sec.models.sec_financials import FinancialStatements, resolve_section_url

                query = SimpleNamespace(
                    symbol=symbol, url=None, calendar_year=calendar_year, calendar_period=calendar_period, use_cache=use_cache,
                )
                url = await resolve_section_url(query, annual_default=False)
                if not url:
                    return []

                def _pivot_sync() -> list[dict]:
                    statements = FinancialStatements.from_url(url, use_cache)
                    with _fs_lock(statements):
                        data, _ = statements.get_statement(statement_type)
                    if data is None or data.empty:
                        return []
                    if {"label", "period_ending", "value"}.issubset(data.columns):
                        id_columns = ["order", "label"]
                        if "unit" in data.columns:
                            id_columns.append("unit")
                        df = data[
                            ~(data["label"].isna() & data.groupby("order")["value"].transform(lambda s: s.isna().all()))
                        ]
                        period_order = list(dict.fromkeys(df["period_ending"].astype(str).tolist()))
                        df = df.drop_duplicates(subset=[*id_columns, "period_ending"], keep="first")
                        df = df.pivot(index=id_columns, columns="period_ending", values="value").reset_index()
                        ordered_periods = [p for p in period_order if p in df.columns]
                        df = df[[*id_columns, *ordered_periods]].fillna("--")
                    else:
                        drop_columns = {
                            "tag", "parent_tag", "preferred_label", "balance", "weight", "decimals", "context_ref",
                            "period_beginning", "period_ending", "value", "taxonomy", "data_type", "period_type",
                            "description", "name",
                        }
                        df = data[[c for c in data.columns if str(c) not in drop_columns]]
                    # Same row shape as upstream get_as_filed_widget_rows: order index, "label" key, str keys, NaN -> None.
                    from pandas import isna

                    source_columns = list(df.columns)
                    if not source_columns:
                        return []
                    label_column = "label" if "label" in source_columns else source_columns[0]
                    rows: list[dict] = []
                    for idx, row in enumerate(df.itertuples(index=False), start=1):
                        out: dict = {"order": idx}
                        for column_name, value in zip(source_columns, list(row)):
                            if str(column_name) == "order":
                                out["order"] = value
                                continue
                            key = "label" if column_name == label_column else str(column_name)
                            if isinstance(value, str) and value == "--":
                                out[key] = "--"
                            else:
                                try:
                                    out[key] = None if isna(value) else value
                                except (TypeError, ValueError):
                                    out[key] = value
                        rows.append(out)
                    return rows

                return await asyncio.to_thread(_pivot_sync)

            get_as_filed_widget_rows._terminal_offloaded = True
            afw._orig_get_as_filed_widget_rows = orig_afw
            afw.get_as_filed_widget_rows = get_as_filed_widget_rows
        offloaded.append("SEC")
    except ImportError as e:
        _not_applied("event-loop offload (SEC)", e)

    # 2. YFinance Asset Info HTML builder (runs sync yf.get_info + 10y history)
    try:
        from openbb_yfinance.utils import asset_info as yf_ai

        if not getattr(yf_ai.asset_info_widget_html, "_terminal_offloaded", False):
            async def asset_info_widget_html(symbol: str = "AAPL", theme: str = "dark") -> str:
                import contextlib
                import uuid
                from pywry.inline import InlineWidget, _generate_widget_token
                from openbb_yfinance.utils.pywry_server import bind_loop, ensure_pywry_mounted

                ensure_pywry_mounted()
                bind_loop()

                def _build_sync():
                    payload = yf_ai._fetch(symbol)
                    fragment = yf_ai._render_fragment(symbol, payload)
                    widget_id = uuid.uuid4().hex
                    token = _generate_widget_token(widget_id)
                    doc = yf_ai._build_live_document(fragment, widget_id, token, theme, symbol, payload.get("info") or {})
                    return payload, widget_id, token, doc

                payload, widget_id, token, document = await asyncio.to_thread(_build_sync)
                widget = InlineWidget(html=document, widget_id=widget_id, browser_only=True, token=token)
                info = payload.get("info")
                if info:
                    with contextlib.suppress(Exception):
                        yf_ai._start_live_quotes(widget, symbol, info)
                return document

            asset_info_widget_html._terminal_offloaded = True
            yf_ai.asset_info_widget_html = asset_info_widget_html
        offloaded.append("YFinance asset_info")
    except ImportError as e:
        _not_applied("event-loop offload (YFinance asset_info)", e)

    # 3. Federal Reserve FOMC / regional publication download & choice routes + HTTP 204 fix
    try:
        from openbb_federal_reserve import federal_reserve_router as frr

        fr_map = {}
        for attr, empty_ok in (
            ("fomc_documents_download", False),
            ("fomc_documents_choices", True),
            ("regional_publications_download", False),
            ("regional_publications_choices", True),
            ("regional_reports_choices", True),
            ("market_probability_meetings", False),
        ):
            orig_ep = getattr(frr, attr, None)
            if orig_ep is not None and not getattr(orig_ep, "_terminal_offloaded", False):
                wrapped_ep = _wrap_sync_coro(orig_ep, empty_on_empty_error=empty_ok)
                wrapped_ep._terminal_offloaded = True
                setattr(frr, attr, wrapped_ep)
                fr_map[orig_ep] = wrapped_ep
        if fr_map:
            _rebind_routes(frr.router._api_router, fr_map)
            for _dr, *_ in getattr(frr, "_REGIONAL_DISTRICTS", ()):
                _rebind_routes(_dr.api_router, fr_map)
            offloaded.append("Fed")
    except ImportError as e:
        _not_applied("event-loop offload (Fed)", e)

    # 4. BLS CPI document routes
    try:
        from openbb_bls.routers import cpi as bls_cpi

        bls_map = {}
        for attr, empty_ok in (("document_choices", True), ("document_download", False)):
            orig_ep = getattr(bls_cpi, attr, None)
            if orig_ep is not None and not getattr(orig_ep, "_terminal_offloaded", False):
                wrapped_ep = _wrap_sync_coro(orig_ep, empty_on_empty_error=empty_ok)
                wrapped_ep._terminal_offloaded = True
                setattr(bls_cpi, attr, wrapped_ep)
                bls_map[orig_ep] = wrapped_ep
        if bls_map:
            _rebind_routes(bls_cpi.router.api_router, bls_map)
            offloaded.append("BLS CPI")
    except ImportError as e:
        _not_applied("event-loop offload (BLS CPI)", e)

    # 5. OECD custom SDMX/metadata routes (run sync inside async def)
    try:
        from openbb_oecd import oecd_router as oer

        oecd_map = {}
        for attr in (
            "list_topic_choices",
            "list_subtopic_choices",
            "list_dataflows",
            "list_dataflow_choices",
            "list_topics",
            "get_dataflow_parameters",
            "list_tables",
            "get_table_detail",
            "list_table_choices",
            "indicator_choices",
            "presentation_table_choices",
            "presentation_table_dim_choices",
            "presentation_table",
        ):
            orig_ep = getattr(oer, attr, None)
            if orig_ep is not None and not getattr(orig_ep, "_terminal_offloaded", False):
                wrapped_ep = _wrap_sync_coro(orig_ep)
                wrapped_ep._terminal_offloaded = True
                setattr(oer, attr, wrapped_ep)
                oecd_map[orig_ep] = wrapped_ep
        if oecd_map:
            _rebind_routes(oer.router.api_router, oecd_map)
            offloaded.append("OECD")
    except ImportError as e:
        _not_applied("event-loop offload (OECD)", e)

    if offloaded:
        applied.append("blocking async routes offloaded to worker threads (" + ", ".join(offloaded) + ")")


def install_session_leak_fix(applied: list):
    """Replace per-session atexit closures with one WeakSet cleanup handler (stops unbounded aiohttp session buildup)."""
    import atexit
    import functools
    import weakref

    try:
        from openbb_core.provider.utils import helpers as core_helpers
    except ImportError:
        return
    orig_get_session = getattr(core_helpers, "get_async_requests_session", None)
    if orig_get_session is None or getattr(orig_get_session, "_terminal_weak_sessions", False):
        return

    open_sessions: weakref.WeakSet = weakref.WeakSet()
    skip_atexit = threading.local()
    real_atexit_register = atexit.register

    def _cleanup_sessions():
        for sess in list(open_sessions):
            try:
                if not sess.closed:
                    core_helpers.run_async(sess.close)
            except Exception:  # noqa: BLE001
                pass

    real_atexit_register(_cleanup_sessions)

    def _guarded_register(fn, *args, **kwargs):
        if getattr(skip_atexit, "active", False) and getattr(fn, "__name__", "") == "at_exit":
            if args and args[0] is not None:
                open_sessions.add(args[0])
            return fn
        return real_atexit_register(fn, *args, **kwargs)

    atexit.register = _guarded_register

    @functools.wraps(orig_get_session)
    async def get_async_requests_session(*args, **kwargs):
        prev = getattr(skip_atexit, "active", False)
        skip_atexit.active = True
        try:
            sess = await orig_get_session(*args, **kwargs)
        finally:
            skip_atexit.active = prev
        if sess is not None:
            open_sessions.add(sess)
        return sess

    get_async_requests_session._terminal_weak_sessions = True
    core_helpers.get_async_requests_session = get_async_requests_session
    applied.append("provider ClientSessions tracked with WeakSet (no per-request atexit leak)")


def install_disable_mcp_subprocesses(applied: list):
    """Do not spawn orphaned FastMCP uvicorn subprocesses (e.g. port 7769) on /widgets.json unless opted in."""
    if os.environ.get("OPENBB_ENABLE_MCP", "").strip().lower() in ("1", "true", "yes"):
        return
    disabled = []
    for mod_name, label in (
        ("openbb_sec.utils.filing_viewer_mcp", "SEC"),
        ("openbb_yfinance.utils.mcp_app", "YFinance"),
    ):
        try:
            mod = __import__(mod_name, fromlist=["ensure_mcp_subprocess"])
        except ImportError:
            continue
        if hasattr(mod, "ensure_mcp_subprocess"):
            mod.ensure_mcp_subprocess = lambda: None
            disabled.append(label)
    if disabled:
        applied.append("background MCP subprocess spawning disabled (" + ", ".join(disabled) + ")")


def install_yfinance_crumb_fix(applied: list):
    """Prevent poisoned Yahoo Finance crumbs from persisting and recover once from 401 Invalid Crumb responses."""
    import contextlib
    import functools
    import threading
    import time

    try:
        import yfinance.data as yf_data
        from yfinance.config import YfConfig
        from yfinance.exceptions import YFRateLimitError
    except ImportError:
        return
    if getattr(yf_data.YfData, "_terminal_crumb_fixed", False):
        return

    with contextlib.suppress(Exception):
        net = getattr(YfConfig, "network", None)
        if net is not None and (getattr(net, "retries", 0) or 0) < 2:
            net.retries = 2

    def _is_bad_crumb(val) -> bool:
        if not isinstance(val, str):
            return True
        s = val.strip()
        if not s or len(s) > 64 or any(ch.isspace() for ch in val):
            return True
        low = s.lower()
        return (
            "<html" in low
            or s.startswith("{")
            or "too many requests" in low
            or "unauthorized" in low
            or "invalid" in low
        )

    def _wrap_crumb_getter(orig):
        @functools.wraps(orig)
        def _wrapped(self, *args, **kwargs):
            try:
                res = orig(self, *args, **kwargs)
            except YFRateLimitError:
                self._crumb = None
                raise
            if _is_bad_crumb(res):
                self._crumb = None
                return None
            return res

        return _wrapped

    orig_basic = yf_data.YfData._get_crumb_basic
    orig_csrf = yf_data.YfData._get_crumb_csrf
    orig_cookie_basic = getattr(yf_data.YfData, "_get_cookie_basic", None)
    orig_set_session = getattr(yf_data.YfData, "_set_session", None)
    orig_make_request = yf_data.YfData._make_request
    refresh_init_lock = threading.Lock()
    is_transient = getattr(yf_data, "_is_transient_error", lambda _exc: False)
    norm_proxy = getattr(yf_data, "_normalize_proxy", lambda p: p)
    yf_exception = getattr(yf_data, "YFException", ValueError)

    def _get_refresh_lock(self):
        refresh_lock = getattr(self, "_terminal_refresh_lock", None)
        if refresh_lock is None:
            with refresh_init_lock:
                refresh_lock = getattr(self, "_terminal_refresh_lock", None)
                if refresh_lock is None:
                    refresh_lock = threading.RLock()
                    self._terminal_refresh_lock = refresh_lock
        return refresh_lock

    def _reset_crumb_state(self):
        with _get_refresh_lock(self):
            lock = getattr(self, "_cookie_lock", None)
            with lock if lock is not None else contextlib.nullcontext():
                self._crumb = None
                self._cookie = None
                session = getattr(self, "_session", None)
                if not getattr(self, "_logged_in", False) and session is not None:
                    with contextlib.suppress(Exception):
                        session.cookies.clear()
                with contextlib.suppress(Exception):
                    from yfinance import cache as yf_cache

                    yf_cache.get_cookie_cache().store("curlCffi", None)
                cache_get = getattr(self, "cache_get", None)
                cache_clear = getattr(cache_get, "cache_clear", None)
                if callable(cache_clear):
                    with contextlib.suppress(Exception):
                        cache_clear()

    @functools.wraps(orig_make_request)
    def _make_request(self, url, request_method, body=None, params=None, timeout=30, data=None, headers=None):
        net_cfg = getattr(YfConfig, "network", None)
        proxy_cfg = getattr(net_cfg, "proxy", None)
        if proxy_cfg is not None and getattr(self, "_session", None) is not None:
            self._session.proxies = norm_proxy(proxy_cfg)

        req_params = dict(params) if params is not None else {}
        if "crumb" in req_params:
            raise yf_exception("Don't manually add 'crumb' to params dict, let data.py handle it")

        crumb_rate_limited = False
        try:
            crumb, _ = self._get_cookie_and_crumb()
        except YFRateLimitError:
            crumb = None
            crumb_rate_limited = True
        except Exception as e:
            if is_transient(e):
                crumb = None
            else:
                raise

        request_args = {
            "url": url,
            "params": {**req_params, **({"crumb": crumb} if crumb is not None else {})},
            "timeout": timeout,
        }
        if body:
            request_args["json"] = body
        if data:
            request_args["data"] = data
            request_args["headers"] = {"Content-Type": "application/json"}
        if headers:
            request_args["headers"] = {**request_args.get("headers", {}), **headers}

        def _active_method(fn):
            bound_self = getattr(fn, "__self__", None)
            cur_sess = getattr(self, "_session", None)
            mname = getattr(fn, "__name__", None)
            if bound_self is not None and cur_sess is not None and bound_self is not cur_sess and mname:
                return getattr(cur_sess, mname, fn)
            return fn

        max_retries = max(2, getattr(net_cfg, "retries", 0) or 0)
        response = None
        for attempt in range(max_retries + 1):
            try:
                response = _active_method(request_method)(**request_args)
            except Exception as e:
                if is_transient(e) and attempt < max_retries:
                    time.sleep(0.5 * (2**attempt))
                    continue
                raise
            if getattr(response, "status_code", None) in (502, 503, 504) and attempt < max_retries:
                time.sleep(0.5 * (2**attempt))
                continue
            break

        status = getattr(response, "status_code", None) if response is not None else None
        if status == 429:
            raise YFRateLimitError()

        text = (getattr(response, "text", "") or "").lower() if response is not None else ""
        is_crumb_err = response is not None and (
            (status == 401 and "unable to access this feature" not in text)
            or (isinstance(status, int) and status >= 400 and "invalid crumb" in text)
        )
        if not is_crumb_err:
            return response
        if crumb_rate_limited:
            raise YFRateLimitError()

        with _get_refresh_lock(self):
            if getattr(self, "_crumb", None) == crumb:
                _reset_crumb_state(self)
                new_crumb = None
                try:
                    new_crumb, _ = self._get_cookie_and_crumb(timeout)
                except YFRateLimitError:
                    raise
                except Exception as e:
                    if is_transient(e):
                        new_crumb = None
                    else:
                        raise
            else:
                new_crumb = getattr(self, "_crumb", None)

        if new_crumb is not None:
            request_args["params"]["crumb"] = new_crumb
        else:
            request_args["params"].pop("crumb", None)

        response = _active_method(request_method)(**request_args)
        if getattr(response, "status_code", None) == 429:
            raise YFRateLimitError()
        return response

    yf_data.YfData._get_crumb_basic = _wrap_crumb_getter(orig_basic)
    yf_data.YfData._get_crumb_csrf = _wrap_crumb_getter(orig_csrf)
    if callable(orig_cookie_basic):

        @functools.wraps(orig_cookie_basic)
        def _get_cookie_basic(self, timeout=30):
            if getattr(self, "_cookie", None) is not None:
                return True
            ok = orig_cookie_basic(self, timeout)
            if ok and getattr(self, "_cookie", None) is None:
                self._cookie = True
            return ok

        yf_data.YfData._get_cookie_basic = _get_cookie_basic
    if callable(orig_set_session):

        def _has_items(obj) -> bool:
            if not obj:
                return False
            try:
                return len(obj) > 0
            except TypeError:
                return bool(obj)

        @functools.wraps(orig_set_session)
        def _set_session(self, session):
            if session is None:
                return None
            cur = getattr(self, "_session", None)
            if cur is not None:
                if session is cur:
                    return orig_set_session(self, session)
                sess_cookies = getattr(session, "cookies", None)
                sess_proxies = getattr(session, "proxies", None)
                cur_cookies = getattr(cur, "cookies", None)
                has_auth = getattr(self, "_cookie", None) is not None or _has_items(cur_cookies)
                # yf.download() passes a throwaway empty new_session() on every call: keep the
                # existing authenticated session instead of wiping shared cookies mid-flight.
                if type(session) is type(cur) and has_auth and not _has_items(sess_cookies) and not sess_proxies:
                    net_p = getattr(getattr(YfConfig, "network", None), "proxy", None)
                    if net_p is not None:
                        lock = getattr(self, "_cookie_lock", None)
                        with lock if lock is not None else contextlib.nullcontext():
                            cur.proxies = norm_proxy(net_p)
                    return None
            res = orig_set_session(self, session)
            new_sess = getattr(self, "_session", None)
            if cur is not None and new_sess is not cur:
                if not getattr(self, "_logged_in", False) and not _has_items(getattr(new_sess, "cookies", None)):
                    lock = getattr(self, "_cookie_lock", None)
                    with lock if lock is not None else contextlib.nullcontext():
                        self._cookie = None
                        self._crumb = None
            return res

        yf_data.YfData._set_session = _set_session
    yf_data.YfData._terminal_reset_crumb = _reset_crumb_state
    yf_data.YfData._make_request = _make_request
    yf_data.YfData._terminal_crumb_fixed = True
    applied.append("Yahoo Finance requests guard against poisoned crumbs and recover from 401 Invalid Crumb")


def install_yfinance_options_fix(applied: list):
    """Turn Yahoo crumb 401 errors into a crumb refresh + single retry, and missing/empty chains into EmptyDataError."""
    import contextlib
    import functools

    try:
        from openbb_core.provider.utils.errors import EmptyDataError
        from openbb_yfinance.models import options_chains as yf_opt
    except ImportError:
        return
    orig = yf_opt.YFinanceOptionsChainsFetcher.aextract_data
    if getattr(orig, "_terminal_opt_fixed", False):
        return

    @functools.wraps(orig)
    async def aextract_data(query, credentials=None, **kwargs):
        sym = str(getattr(query, "symbol", "") or "").upper()
        for attempt in range(2):
            try:
                res = await orig(query, credentials, **kwargs)
                if not res or not res.get("chains") or res.get("underlying") is None:
                    raise EmptyDataError(f"No options found for {sym}")
                return res
            except Exception as exc:  # noqa: BLE001
                low = str(exc).lower()
                is_crumb_401 = (
                    "unable to access this feature" not in low
                    and "429" not in low
                    and "too many requests" not in low
                    and "rate limit" not in low
                    and ("invalid crumb" in low or "crumb" in low or "401" in low or "unauthorized" in low)
                )
                if attempt == 0 and is_crumb_401:
                    try:
                        import yfinance.data as yf_data
                        yd = yf_data.YfData()
                        reset_crumb = getattr(yd, "_terminal_reset_crumb", None)
                        if callable(reset_crumb):
                            reset_crumb()
                        else:
                            lock = getattr(yd, "_cookie_lock", None)
                            with lock if lock is not None else contextlib.nullcontext():
                                yd._crumb = None
                                yd._cookie = None
                    except Exception:  # noqa: BLE001
                        pass
                    continue
                if isinstance(exc, (KeyError, AttributeError, TypeError)):
                    raise EmptyDataError(f"No options found for {sym}") from exc
                raise

    aextract_data._terminal_opt_fixed = True
    yf_opt.YFinanceOptionsChainsFetcher.aextract_data = staticmethod(aextract_data)
    yf_opt.YFinanceOptionsChainsFetcher.extract_data = staticmethod(aextract_data)
    applied.append("Yahoo options chains recover from stale crumb 401 and empty chains")


def install_tmx_gainers_fix(applied: list):
    """Pop missing string fields (longName, etc.) instead of letting check_metric coerce None to 0."""
    try:
        from openbb_tmx.models import gainers as tg
    except ImportError:
        return
    orig_transform = tg.TmxGainersFetcher.transform_data
    if getattr(orig_transform, "_terminal_tmx_gainers_fixed", False):
        return

    def transform_data(query, data: list[dict], **kwargs):
        cleaned = []
        for raw in data or ():
            if not isinstance(raw, dict):
                continue
            sym = raw.get("symbol")
            if not isinstance(sym, str) or not sym.strip():
                continue
            row = dict(raw)
            for str_key in ("longName", "name", "exchange", "currency"):
                if str_key in row and (row[str_key] is None or row[str_key] == "-"):
                    row.pop(str_key, None)
            try:
                cleaned.append(tg.TmxGainersData.model_validate(row))
            except Exception:  # noqa: BLE001
                continue
        return cleaned

    transform_data._terminal_tmx_gainers_fixed = True
    tg.TmxGainersFetcher.transform_data = staticmethod(transform_data)
    applied.append("TMX gainers tolerate missing longName without HTTP 500 ValidationError")


NASDAQ_MAX_CONCURRENCY = 4
NASDAQ_MIN_GAP_SEC = 0.15
NASDAQ_COOLDOWN_SEC = 120.0
NASDAQ_RETRY_AFTER_MIN = 30.0
NASDAQ_RETRY_AFTER_MAX = 600.0
_NASDAQ_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Only an explicit block/throttle page trips the cooldown; an ordinary HTML 404/500 page (which also loads Akamai assets) does not.
_NASDAQ_BLOCK_MARKERS = (
    "access denied",
    "too many requests",
)
NASDAQ_DEFAULT_TIMEOUT_SEC = 10.0   # also the longest wait for a request slot when the request gives no timeout
# The gate's limits above are also declared in terminal/composite/core/http.py PROVIDERS["nasdaq"]["backend_gate"] (this
# process cannot import terminal/); test_nasdaq_etf asserts the two copies are equal.

_nasdaq_rl_lock = threading.Lock()
_nasdaq_blocked_until = 0.0
_nasdaq_blocked_reason = ""
_nasdaq_loop_gates: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
# Process-wide network gate: the cap and pacing hold across every event loop (worker-thread loops, asyncio.run callers).
_nasdaq_slot_lock = threading.Lock()
_nasdaq_inflight = 0
_nasdaq_next_at = 0.0
_nasdaq_queue: collections.deque = collections.deque()      # tickets of requests waiting for a slot, in arrival order
_nasdaq_last_start = 0.0                                     # when the last request actually went out
_nasdaq_seq_next = 0                                         # start order handed out with each slot (arrival order)
_nasdaq_turn = 0                                             # the start order whose turn it is
_nasdaq_abandoned: set = set()                               # start orders given up before starting (skipped)
_nasdaq_tickets = itertools.count(1)
_nasdaq_sessions_lock = threading.Lock()
_nasdaq_sessions: dict[int, dict] = {}
_nasdaq_classes_cache = None


def _nasdaq_error(msg: str) -> Exception:
    try:
        from openbb_core.app.model.abstract.error import OpenBBError

        return OpenBBError(msg)
    except Exception:  # noqa: BLE001
        return RuntimeError(msg)


def _check_nasdaq_cooldown() -> None:
    with _nasdaq_rl_lock:
        now = time.monotonic()
        if now < _nasdaq_blocked_until:
            rem = max(1, int(math.ceil(_nasdaq_blocked_until - now)))
            reason = _nasdaq_blocked_reason or "rate limited"
        else:
            return
    raise _nasdaq_error(
        f"Nasdaq rate limit active ({reason}). "
        f"Cooling down for ~{rem}s to avoid extending the block; "
        "try again shortly or switch source on this card."
    )


def _trip_nasdaq_cooldown(status: int, retry_after: str | None = None, reason: str = "") -> None:
    wait = NASDAQ_COOLDOWN_SEC
    if retry_after is not None:
        try:
            parsed = float(str(retry_after).strip())
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and math.isfinite(parsed):
            wait = max(NASDAQ_RETRY_AFTER_MIN, min(NASDAQ_RETRY_AFTER_MAX, parsed))
    if status == 429:
        detail = f"HTTP 429{f', {reason}' if reason else ''}"
    else:
        detail = reason or "temporarily blocked"
    with _nasdaq_rl_lock:
        global _nasdaq_blocked_until, _nasdaq_blocked_reason
        _nasdaq_blocked_until = max(_nasdaq_blocked_until, time.monotonic() + wait)
        _nasdaq_blocked_reason = detail
    raise _nasdaq_error(
        f"Nasdaq rate limit reached ({detail}). "
        f"Cooling down for ~{int(wait)}s to avoid extending the block; "
        "try again shortly or switch source on this card."
    )


def _get_nasdaq_loop_gate() -> dict:
    import asyncio

    loop = asyncio.get_running_loop()
    gate = _nasdaq_loop_gates.get(loop)
    if gate is None:
        gate = {"sess_lock": asyncio.Lock()}
        _nasdaq_loop_gates[loop] = gate
    return gate


@contextlib.asynccontextmanager
async def _nasdaq_network_slot(wait_s: float | None = None):
    """Hold one of NASDAQ_MAX_CONCURRENCY process-wide slots, spaced NASDAQ_MIN_GAP_SEC apart, across all event loops.

    Slots are given in arrival order (a ticket queue: a later request never overtakes a waiting one), and the wait is
    bounded by wait_s (default NASDAQ_DEFAULT_TIMEOUT_SEC): past it the request fails with a rate-limit message and a
    retry hint, never a hang or a generic timeout. A cooldown that starts while waiting ends the wait at once."""
    import asyncio

    global _nasdaq_inflight, _nasdaq_next_at, _nasdaq_last_start, _nasdaq_seq_next, _nasdaq_turn
    _check_nasdaq_cooldown()
    limit_s = float(wait_s) if wait_s and wait_s > 0 else NASDAQ_DEFAULT_TIMEOUT_SEC
    give_up = time.monotonic() + limit_s
    with _nasdaq_slot_lock:
        ticket = next(_nasdaq_tickets)
        _nasdaq_queue.append(ticket)
    try:
        while True:
            with _nasdaq_slot_lock:
                if _nasdaq_queue and _nasdaq_queue[0] == ticket and _nasdaq_inflight < NASDAQ_MAX_CONCURRENCY:
                    _nasdaq_queue.popleft()
                    _nasdaq_inflight += 1
                    seq, _nasdaq_seq_next = _nasdaq_seq_next, _nasdaq_seq_next + 1
                    now = time.monotonic()
                    slot_at = max(now, _nasdaq_next_at)          # start times in arrival order, NASDAQ_MIN_GAP_SEC apart
                    _nasdaq_next_at = slot_at + NASDAQ_MIN_GAP_SEC
                    wait = slot_at - now
                    break
                ahead = _nasdaq_queue.index(ticket) if ticket in _nasdaq_queue else 0
            if time.monotonic() >= give_up:
                raise _nasdaq_error(
                    f"Nasdaq rate limit: no request slot within {limit_s:.0f}s ({ahead} requests ahead, at most "
                    f"{NASDAQ_MAX_CONCURRENCY} at a time). Try again shortly or switch source on this card."
                )
            await asyncio.sleep(0.01)
            _check_nasdaq_cooldown()
    except BaseException:                         # timed out, cooled down or cancelled while queued: leave the queue
        with _nasdaq_slot_lock:
            with contextlib.suppress(ValueError):
                _nasdaq_queue.remove(ticket)
        raise
    try:
        started = False
        try:
            if wait > 0:
                await asyncio.sleep(wait)
            # starts go out in the same order, each at least NASDAQ_MIN_GAP_SEC after the previous one actually went out
            # (a timer that fired early, or a neighbour that started late, never shortens the gap)
            while True:
                with _nasdaq_slot_lock:
                    while _nasdaq_turn in _nasdaq_abandoned:
                        _nasdaq_abandoned.discard(_nasdaq_turn)
                        _nasdaq_turn += 1
                    now = time.monotonic()
                    # not our turn yet: at least one gap per start still ahead of us (no busy polling under load)
                    short = (_nasdaq_last_start + NASDAQ_MIN_GAP_SEC - now if _nasdaq_turn == seq
                             else max(0.005, (seq - _nasdaq_turn) * NASDAQ_MIN_GAP_SEC - (now - _nasdaq_last_start)))
                    if _nasdaq_turn == seq and short <= 0:
                        _nasdaq_last_start = now
                        _nasdaq_turn += 1
                        started = True
                        break
                await asyncio.sleep(max(0.002, short))
        except BaseException:
            if not started:
                with _nasdaq_slot_lock:
                    if seq >= _nasdaq_turn:
                        _nasdaq_abandoned.add(seq)       # cancelled before its turn: the ones behind it are not held up
            raise
        _check_nasdaq_cooldown()
        yield
    finally:
        with _nasdaq_slot_lock:
            _nasdaq_inflight = max(0, _nasdaq_inflight - 1)


def _is_nasdaq_rate_limit(exc: BaseException) -> bool:
    return "rate limit" in str(exc).lower()


def _nasdaq_session_kwargs() -> tuple[dict, dict]:
    """(connector kwargs, session kwargs) mirroring openbb_core get_async_requests_session: proxy, SSL, auth, timeout."""
    import ssl

    import aiohttp

    try:
        from openbb_core.provider.utils.helpers import get_python_request_settings

        settings = dict(get_python_request_settings())
    except Exception:  # noqa: BLE001
        settings = {}
    password = settings.pop("password", None)
    settings.pop("proxy_headers", None)
    proxy = settings.get("proxy")
    http_proxy = os.environ.get("HTTP_PROXY", os.environ.get("HTTPS_PROXY"))
    https_proxy = os.environ.get("HTTPS_PROXY", os.environ.get("HTTP_PROXY"))
    if not proxy and http_proxy:
        # Nasdaq is https; aiohttp would try to upgrade an https:// proxy URL, so talk plain HTTP to the proxy.
        proxy = (https_proxy or http_proxy).replace("https:", "http:")
    connector_kwargs: dict = {}
    if proxy or settings.get("verify_ssl") is False:
        connector_kwargs["ssl"] = False
    elif settings.get("certfile") or settings.get("cafile") or os.environ.get("REQUESTS_CA_BUNDLE"):
        ctx = ssl.create_default_context()
        ca = settings.get("cafile") or os.environ.get("REQUESTS_CA_BUNDLE")
        if ca:
            ctx.load_verify_locations(cafile=ca)
        if settings.get("certfile"):
            ctx.load_cert_chain(certfile=settings["certfile"], keyfile=settings.get("keyfile"), password=password)
        connector_kwargs["ssl"] = ctx
    try:
        total = float(settings.get("timeout") or NASDAQ_DEFAULT_TIMEOUT_SEC)
    except (TypeError, ValueError):
        total = NASDAQ_DEFAULT_TIMEOUT_SEC
    session_kwargs: dict = {"timeout": aiohttp.ClientTimeout(total=total)}
    if proxy:
        session_kwargs["proxy"] = proxy
    p_auth = settings.get("proxy_auth")
    if p_auth:
        session_kwargs["proxy_auth"] = aiohttp.BasicAuth(*p_auth) if isinstance(p_auth, (list, tuple)) else aiohttp.BasicAuth(p_auth)
    s_auth = settings.get("auth")
    if s_auth:
        session_kwargs["auth"] = aiohttp.BasicAuth(*s_auth) if isinstance(s_auth, (list, tuple)) else aiohttp.BasicAuth(s_auth)
    cookies = settings.get("cookies")
    if isinstance(cookies, dict) and cookies:
        session_kwargs["cookies"] = cookies
    return connector_kwargs, session_kwargs


def _apply_nasdaq_request_timeout(kwargs: dict) -> dict:
    """Honor the user's request_timeout preference (as amake_request does); the session default covers the rest."""
    prefs = kwargs.pop("preferences", None)
    if "timeout" not in kwargs and isinstance(prefs, dict) and prefs.get("request_timeout"):
        kwargs["timeout"] = prefs["request_timeout"]
    return kwargs


async def _nasdaq_cache_filter(response) -> bool:
    """Reject empty, non-JSON, or HTML block payloads before SQLite writes them. A valid JSON envelope is kept whatever
    its rCode, so misses such as "Symbol not exists." stay negatively cached instead of being re-fetched on every call."""
    if getattr(response, "status", 200) != 200:
        return False
    raw = await response.read()
    if not raw or not raw.strip():
        return False
    try:
        json.loads(raw.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _configure_nasdaq_cache_backend(cache) -> None:
    """Make aiosqlite worker threads daemon (prevents Python 3.12 exit deadlock) and filter bad 200 responses."""
    import inspect

    if cache is None:
        return
    for attr in ("responses", "redirects"):
        store = getattr(cache, attr, None)
        conn = getattr(store, "_connection", None)
        th = getattr(conn, "_thread", None)
        if th is not None and not th.is_alive():
            with contextlib.suppress(RuntimeError):
                th.daemon = True
    if not getattr(cache, "_terminal_nasdaq_filtered", False):
        orig_filter = getattr(cache, "filter_fn", None)

        async def _combined_filter(response) -> bool:
            if callable(orig_filter):
                ok = (
                    await orig_filter(response)
                    if inspect.iscoroutinefunction(orig_filter)
                    else orig_filter(response)
                )
                if not ok:
                    return False
            return await _nasdaq_cache_filter(response)

        cache.filter_fn = _combined_filter
        cache._terminal_nasdaq_filtered = True

        async def _delete_expired_responses():
            """Upstream calls filter_fn without awaiting it; with the async filter that would keep every entry."""
            keys = set()
            async for key in cache.responses.keys():
                resp = await cache.responses.read(key)
                if resp is None or getattr(resp, "is_expired", False) or not await _combined_filter(resp):
                    keys.add(key)
            await cache.bulk_delete(keys)

        cache.delete_expired_responses = _delete_expired_responses


def _stop_nasdaq_cache_sync(sess) -> None:
    """Stop any aiosqlite worker threads synchronously when an event loop is already closed or exiting."""
    cache = getattr(sess, "cache", None)
    if cache is None:
        return
    seen: set[int] = set()
    for attr in ("responses", "redirects"):
        store = getattr(cache, attr, None)
        if store is None:
            continue
        conn = getattr(store, "_connection", None)
        if conn is None or id(conn) in seen:
            continue
        seen.add(id(conn))
        th = getattr(conn, "_thread", None)
        is_alive = bool(th is not None and th.is_alive())
        has_db = getattr(conn, "_connection", None) is not None
        if (is_alive or has_db) and callable(getattr(conn, "stop", None)):
            with contextlib.suppress(Exception):
                conn.stop()
        with contextlib.suppress(Exception):
            store._closed = True
            store._connection = None


def _close_nasdaq_session_sync(sess) -> None:
    """Synchronously tear down connector and cache worker threads for a session whose loop is not running."""
    import asyncio

    _stop_nasdaq_cache_sync(sess)
    conn = getattr(sess, "connector", None)
    if conn is not None and not getattr(conn, "closed", True):
        with contextlib.suppress(Exception):
            res = conn.close()
            if asyncio.iscoroutine(res):
                res.close()


async def _decode_nasdaq_response(response, *, from_cache: bool = False, cache=None, url: str | None = None):
    status = getattr(response, "status", 200)
    if not from_cache and status in (403, 429):
        headers = getattr(response, "headers", None)
        retry_after = None
        if headers is not None:
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
        _trip_nasdaq_cooldown(status, retry_after=retry_after)
    if status == 204:
        return {}
    raw = await response.read()
    if not raw or not raw.strip():
        if from_cache and cache is not None and url:
            with contextlib.suppress(Exception):
                await cache.delete_url(url)
        if status < 400:
            return {}
        raise _nasdaq_error(f"Nasdaq answered HTTP {status} with an empty body.")
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        if from_cache and cache is not None and url:
            with contextlib.suppress(Exception):
                await cache.delete_url(url)
        lower = text.lower()
        if not from_cache and (
            status in (403, 429) or any(marker in lower for marker in _NASDAQ_BLOCK_MARKERS)
        ):
            _trip_nasdaq_cooldown(status or 403, reason="temporarily blocked")
        return {}


def _get_nasdaq_session_classes():
    global _nasdaq_classes_cache
    if _nasdaq_classes_cache is not None:
        return _nasdaq_classes_cache
    import aiohttp

    class _NasdaqThrottledClient(aiohttp.ClientSession):
        async def _request(self, method, str_or_url, *args, **kwargs):
            t = kwargs.get("timeout")
            if isinstance(t, (int, float)) and not isinstance(t, bool):
                kwargs["timeout"] = aiohttp.ClientTimeout(total=float(t))
            total = getattr(kwargs.get("timeout"), "total", None) or getattr(self.timeout, "total", None)
            async with _nasdaq_network_slot(total):      # the slot wait is bounded by the request's own timeout
                return await super()._request(method, str_or_url, *args, **kwargs)

    try:
        from aiohttp_client_cache.session import CacheMixin

        class _NasdaqCachedSession(CacheMixin, _NasdaqThrottledClient):
            pass
    except ImportError:
        _NasdaqCachedSession = None

    _nasdaq_classes_cache = (_NasdaqThrottledClient, _NasdaqCachedSession)
    return _nasdaq_classes_cache


def _prune_nasdaq_sessions_locked() -> list:
    dead = []
    for loop_id, entry in _nasdaq_sessions.items():
        loop = entry["loop_ref"]()
        if loop is None or loop.is_closed():
            dead.append(loop_id)
    pruned = []
    for loop_id in dead:
        entry = _nasdaq_sessions.pop(loop_id, None)
        if entry is not None:
            pruned.append(entry)
    return pruned


def _cleanup_pruned_sessions(entries: list) -> None:
    for entry in entries:
        for key in ("cached", "live"):
            sess = entry.get(key)
            entry[key] = None
            if sess is not None and not getattr(sess, "closed", True):
                _close_nasdaq_session_sync(sess)


def _get_nasdaq_session_entry(loop) -> dict:
    loop_id = id(loop)
    with _nasdaq_sessions_lock:
        pruned = _prune_nasdaq_sessions_locked()
        entry = _nasdaq_sessions.get(loop_id)
        if entry is None or entry["loop_ref"]() is not loop:
            entry = {
                "loop_ref": weakref.ref(loop),
                "live": None,
                "cached": None,
                "cache_fn": None,
            }
            _nasdaq_sessions[loop_id] = entry
    if pruned:
        _cleanup_pruned_sessions(pruned)
    return entry


async def _get_nasdaq_live_session():
    import asyncio
    import aiohttp

    loop = asyncio.get_running_loop()
    entry = _get_nasdaq_session_entry(loop)
    sess = entry.get("live")
    if sess is not None and not sess.closed:
        return sess
    async with _get_nasdaq_loop_gate()["sess_lock"]:
        sess = entry.get("live")
        if sess is not None and not sess.closed:
            return sess
        throttled_cls, _ = _get_nasdaq_session_classes()
        conn_kwargs, sess_kwargs = _nasdaq_session_kwargs()
        connector = aiohttp.TCPConnector(
            limit=NASDAQ_MAX_CONCURRENCY,
            limit_per_host=NASDAQ_MAX_CONCURRENCY,
            ttl_dns_cache=300,
            **conn_kwargs,
        )
        sess = throttled_cls(connector=connector, **sess_kwargs)
        entry["live"] = sess
        return sess


async def _get_nasdaq_cached_session(nh):
    import asyncio
    import aiohttp

    loop = asyncio.get_running_loop()
    entry = _get_nasdaq_session_entry(loop)
    cache_fn = getattr(nh, "get_cache_backend", None)
    sess = entry.get("cached")
    if sess is None or sess.closed or entry.get("cache_fn") is not cache_fn:
        async with _get_nasdaq_loop_gate()["sess_lock"]:
            sess = entry.get("cached")
            if sess is not None and (sess.closed or entry.get("cache_fn") is not cache_fn):
                entry["cached"] = None
                if not sess.closed:
                    with contextlib.suppress(Exception):
                        await sess.close()
                sess = None
            if sess is None:
                _, cached_cls = _get_nasdaq_session_classes()
                if cached_cls is None:
                    raise ImportError("aiohttp_client_cache is not available")
                conn_kwargs, sess_kwargs = _nasdaq_session_kwargs()
                connector = aiohttp.TCPConnector(
                    limit=NASDAQ_MAX_CONCURRENCY,
                    limit_per_host=NASDAQ_MAX_CONCURRENCY,
                    ttl_dns_cache=300,
                    **conn_kwargs,
                )
                cache_backend = nh.get_cache_backend()
                _configure_nasdaq_cache_backend(cache_backend)
                sess = cached_cls(cache=cache_backend, connector=connector, **sess_kwargs)
                entry["cached"] = sess
                entry["cache_fn"] = cache_fn
    locks = getattr(sess, "_locks", None)
    if locks is not None and len(locks) > 4096:
        for k in list(locks.keys()):
            lk = locks.get(k)
            if lk is None or not lk.locked():
                locks.pop(k, None)
    return sess


async def close_nasdaq_sessions(loop=None) -> None:
    import asyncio

    with _nasdaq_sessions_lock:
        pruned = _prune_nasdaq_sessions_locked()
        if loop is None:
            entries = list(_nasdaq_sessions.values())
            _nasdaq_sessions.clear()
        else:
            entry = _nasdaq_sessions.pop(id(loop), None)
            entries = [entry] if entry is not None else []
    if pruned:
        _cleanup_pruned_sessions(pruned)
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    for entry in entries:
        entry_loop = entry["loop_ref"]()
        for key in ("cached", "live"):
            sess = entry.get(key)
            entry[key] = None
            if sess is None or getattr(sess, "closed", True):
                continue
            if running is not None and entry_loop is running and not entry_loop.is_closed():
                with contextlib.suppress(Exception):
                    await sess.close()
            else:
                _close_nasdaq_session_sync(sess)


def reset_nasdaq_rate_limit_state() -> None:
    global _nasdaq_blocked_until, _nasdaq_blocked_reason, _nasdaq_inflight, _nasdaq_next_at, _nasdaq_last_start
    global _nasdaq_seq_next, _nasdaq_turn
    with _nasdaq_rl_lock:
        _nasdaq_blocked_until = 0.0
        _nasdaq_blocked_reason = ""
    with _nasdaq_slot_lock:
        _nasdaq_inflight = 0
        _nasdaq_next_at = 0.0
        _nasdaq_last_start = 0.0
        _nasdaq_seq_next = 0
        _nasdaq_turn = 0
        _nasdaq_abandoned.clear()
        _nasdaq_queue.clear()
    _nasdaq_loop_gates.clear()
    with _nasdaq_sessions_lock:
        entries = list(_nasdaq_sessions.values())
        _nasdaq_sessions.clear()
    for entry in entries:
        entry_loop = entry["loop_ref"]()
        for key in ("cached", "live"):
            sess = entry.get(key)
            entry[key] = None
            if sess is None or getattr(sess, "closed", True):
                continue
            if entry_loop is not None and not entry_loop.is_closed() and entry_loop.is_running():
                with contextlib.suppress(Exception):
                    entry_loop.create_task(sess.close())
            else:
                _close_nasdaq_session_sync(sess)


atexit.register(reset_nasdaq_rate_limit_state)


def install_nasdaq_empty_json_fix(applied: list):
    """Treat HTTP 204 / empty JSON bodies from Nasdaq cached requests as {} instead of raising JSONDecodeError."""
    import functools

    try:
        from openbb_nasdaq.utils import helpers as nh
    except ImportError:
        return
    orig_cached = getattr(nh, "_cached_request", None)
    if orig_cached is None or getattr(orig_cached, "_terminal_nasdaq_empty_fixed", False):
        return

    @functools.wraps(orig_cached)
    async def _cached_request(url: str, **kwargs):
        from aiohttp_client_cache.session import CachedSession

        async with CachedSession(cache=nh.get_cache_backend()) as session:
            response = await session.get(url, headers=nh.get_headers("json"), **kwargs)
            try:
                return await _decode_nasdaq_response(
                    response,
                    from_cache=bool(getattr(response, "from_cache", False)),
                )
            finally:
                release = getattr(response, "release", None)
                if callable(release):
                    release()

    _cached_request._terminal_nasdaq_empty_fixed = True
    nh._cached_request = _cached_request
    applied.append("Nasdaq cached requests treat HTTP 204 / empty JSON responses as empty")


def install_nasdaq_rate_limit_fix(applied: list):
    """Throttle, pool, stabilize UA, add 403/429 cooldown, and disable per-strike options detail fan-out for Nasdaq."""
    import functools

    try:
        from openbb_nasdaq.utils import helpers as nh
    except ImportError:
        return

    changed = False

    orig_agent = getattr(nh, "get_random_agent", None)
    if orig_agent is not None and not getattr(orig_agent, "_terminal_nasdaq_ua_fixed", False):
        def _stable_agent() -> str:
            return _NASDAQ_USER_AGENT

        _stable_agent._terminal_nasdaq_ua_fixed = True
        nh.get_random_agent = _stable_agent
        changed = True

    orig_cached = getattr(nh, "_cached_request", None)
    if orig_cached is not None and not getattr(orig_cached, "_terminal_nasdaq_rl_fixed", False):
        @functools.wraps(orig_cached)
        async def _cached_request(url: str, **kwargs):
            _apply_nasdaq_request_timeout(kwargs)
            session = await _get_nasdaq_cached_session(nh)
            response = await session.get(url, headers=nh.get_headers("json"), **kwargs)
            try:
                return await _decode_nasdaq_response(
                    response,
                    from_cache=bool(getattr(response, "from_cache", False)),
                    cache=getattr(session, "cache", None),
                    url=url,
                )
            finally:
                release = getattr(response, "release", None)
                if callable(release):
                    release()

        _cached_request._terminal_nasdaq_rl_fixed = True
        _cached_request._terminal_nasdaq_empty_fixed = True
        nh._cached_request = _cached_request
        changed = True

    orig_live = getattr(nh, "_live_request", None)
    if orig_live is not None and not getattr(orig_live, "_terminal_nasdaq_rl_fixed", False):
        @functools.wraps(orig_live)
        async def _live_request(url: str, **kwargs):
            _apply_nasdaq_request_timeout(kwargs)
            session = await _get_nasdaq_live_session()
            response = await session.get(url, headers=nh.get_headers("json"), **kwargs)
            try:
                return await _decode_nasdaq_response(response, from_cache=False)
            finally:
                release = getattr(response, "release", None)
                if callable(release):
                    release()

        _live_request._terminal_nasdaq_rl_fixed = True
        nh._live_request = _live_request
        changed = True

    orig_resolve = getattr(nh, "resolve_asset_class", None)
    if (
        orig_resolve is not None
        and hasattr(nh, "_PROBE_ORDER")
        and hasattr(nh, "_directory_asset_class")
        and not getattr(orig_resolve, "_terminal_nasdaq_rl_fixed", False)
    ):
        from async_lru import alru_cache

        @alru_cache(maxsize=2048)
        async def _resolve_asset_class(symbol: str) -> str:
            """Upstream memoizes "stocks" when every probe fails, which during a cooldown or outage pins an index, fund
            or crypto symbol as a stock until restart. Re-raise rate-limit and transport failures so nothing is cached."""
            ticker = symbol.strip().upper()
            listing = nh._directory_asset_class(ticker)
            if listing is not None:
                return listing
            transport_err = None
            for asset_class in nh._PROBE_ORDER:
                try:
                    await nh.get_nasdaq_data(f"quote/{ticker}/info?assetclass={asset_class}")
                except Exception as e:  # noqa: BLE001
                    if _is_nasdaq_rate_limit(e):
                        raise
                    if "nasdaq request failed" not in str(e).lower():
                        transport_err = e
                    continue
                return asset_class
            if transport_err is not None:
                raise transport_err
            return "stocks"

        _resolve_asset_class._terminal_nasdaq_rl_fixed = True
        nh.resolve_asset_class = _resolve_asset_class
        changed = True

    try:
        from openbb_nasdaq.models import options_chains as oc
    except ImportError:
        oc = None
    if oc is not None and hasattr(oc, "NasdaqOptionsChainsFetcher"):
        fetcher = oc.NasdaqOptionsChainsFetcher
        orig_aextract = getattr(fetcher, "aextract_data", None)
        if orig_aextract is not None and not getattr(orig_aextract, "_terminal_nasdaq_options_fixed", False):
            async def _aextract_data(query, credentials, **kwargs):
                from datetime import date, timedelta
                from openbb_core.provider.utils.errors import EmptyDataError
                from openbb_nasdaq.utils import helpers as nh_mod

                symbol = query.symbol.upper()
                asset_class = await nh_mod.resolve_asset_class(symbol)
                excode = oc.EXCHANGE_MAP[query.exchange]
                today = date.today()
                requested = {
                    d
                    for d in (
                        nh_mod.to_date(part) for part in (query.expiration or "").split(",") if part
                    )
                    if d is not None
                }
                chain_path = (
                    f"quote/{symbol}/option-chain?assetclass={asset_class}"
                    f"&fromdate={min(requested) if requested else today}"
                    f"&todate={max(requested) if requested else today + timedelta(days=1095)}"
                    f"&limit=20000&excode={excode}&callput=callput&money=all&type=all"
                )
                chain = await nh_mod.get_nasdaq_data(chain_path)
                if not chain:
                    raise EmptyDataError(f"No options chain was found for {symbol}.")

                front = oc._front_expiration(chain)
                fanout = requested or ({front} if front is not None else set())
                greeks: dict = {}
                for expiration in sorted(fanout):
                    try:
                        payload = await nh_mod.get_nasdaq_data(
                            f"quote/{symbol}/option-chain/greeks?assetclass={asset_class}"
                            f"&date={expiration}"
                        )
                    except Exception as e:  # noqa: BLE001
                        if _is_nasdaq_rate_limit(e):
                            raise
                        payload = {}
                    greeks.update(oc._greeks_by_contract(payload or {}))

                return {
                    "symbol": symbol,
                    "chain": chain,
                    "greeks": greeks,
                    "detail": {},
                    "expirations": requested,
                }

            _aextract_data._terminal_nasdaq_options_fixed = True
            fetcher.aextract_data = staticmethod(_aextract_data)
            fetcher.extract_data = staticmethod(_aextract_data)
            changed = True

    try:
        from openbb_nasdaq.utils import nordic as nn
    except ImportError:
        nn = None
    if nn is not None and hasattr(nn, "DIRECTORY_CONCURRENCY"):
        nn.DIRECTORY_CONCURRENCY = NASDAQ_MAX_CONCURRENCY
        changed = True

    if changed:
        applied.append(
            f"Nasdaq transport pooled/throttled (max {NASDAQ_MAX_CONCURRENCY}, gap {NASDAQ_MIN_GAP_SEC}s), "
            "stable UA, 403/429 cooldown, and options detail fan-out removed"
        )


def install_user_settings_cache(applied: list):
    """Cache UserService.read_from_file by (path, mtime_ns, size) to eliminate repeated disk reads."""
    try:
        from openbb_core.app.service import user_service as us
    except ImportError:
        return
    orig_read = us.UserService.read_from_file.__func__
    if getattr(orig_read, "_terminal_cached", False):
        return

    lock = threading.Lock()
    cached_entries: dict = {}

    @classmethod
    def read_from_file(cls, path=None):
        p = path or cls.USER_SETTINGS_PATH
        key = str(p)
        try:
            st = p.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        with lock:
            hit = cached_entries.get(key)
            if hit is not None and hit[0] == sig and sig is not None:
                return hit[1].model_copy(deep=True)
        val = orig_read(cls, path=path)
        if sig is not None:
            with lock:
                cached_entries[key] = (sig, val.model_copy(deep=True))
        return val

    orig_write = us.UserService.write_to_file.__func__

    @classmethod
    def write_to_file(cls, user_settings, path=None):
        orig_write(cls, user_settings, path=path)
        key = str(path or cls.USER_SETTINGS_PATH)
        with lock:
            cached_entries.pop(key, None)

    read_from_file._terminal_cached = True
    us.UserService.read_from_file = read_from_file
    us.UserService.write_to_file = write_to_file
    applied.append("OpenBB user_settings.json reads memoized by file mtime")


def install_imf_arcgis_fix(applied: list):
    """IMF PortWatch's ArcGIS FeatureServer returns quota/rate-limit errors (code 429) as HTTP 200 JSON with an
    ``error`` object and no ``features`` key. The stock pagination loop only checks HTTP status != 200, so a 429 on
    page 2+ silently stops and returns (and caches) just page 1 (1,000 rows ending 2021-09-26), while a 429 on
    page 1 crashes with KeyError: 'features'. Validate every ArcGIS JSON page and bound concurrent ArcGIS calls."""
    import asyncio
    import weakref
    from datetime import datetime
    from urllib.parse import quote

    from async_lru import alru_cache
    from openbb_core.app.model.abstract.error import OpenBBError
    from openbb_core.provider.utils import helpers as core_helpers
    from openbb_imf.utils import port_watch_helpers as pwh

    gates: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def _gate():
        loop = asyncio.get_running_loop()
        sem = gates.get(loop)
        if sem is None:
            sem = gates[loop] = asyncio.Semaphore(4)
        return sem

    def _check_payload(data):
        if not isinstance(data, dict):
            raise OpenBBError("IMF PortWatch (ArcGIS) returned an invalid response.")
        err = data.get("error")
        if err:
            if isinstance(err, dict):
                code = err.get("code")
                msg = err.get("message") or "ArcGIS query error"
                mcode = err.get("messageCode") or ""
                if code in (401, 403, 499) or mcode == "GWM_0003" or "token" in msg.lower():
                    raise OpenBBError(
                        f"IMF PortWatch upstream server is not answering properly right now "
                        f"(ArcGIS error {code}: upstream dataset is restricted or requires ArcGIS authorization)."
                    )
                details = "; ".join(str(x) for x in (err.get("details") or ()) if x)
                text = f"IMF PortWatch (ArcGIS) error {code}: {msg}" if code is not None else f"IMF PortWatch (ArcGIS) error: {msg}"
                if details:
                    text += f" ({details})"
            else:
                text = f"IMF PortWatch (ArcGIS) error: {err}"
            raise OpenBBError(text)
        if "features" not in data:
            raise OpenBBError("IMF PortWatch (ArcGIS) response missing 'features'.")
        return data

    async def _get_json(session, url: str) -> dict:
        async with _gate():
            async with await session.get(url) as response:
                if response.status != 200:
                    raise OpenBBError(f"Failed to fetch data: HTTP {response.status}")
                data = await response.json()
        return _check_payload(data)

    async def _paginate(
        base_url: str,
        where: str = "1=1",
        *,
        order_by: str | None = None,
        page_size: int = 1000,
        extra_params: str = "",
    ) -> list:
        encoded_where = quote(where, safe="")
        order_clause = f"&orderByFields={quote(order_by, safe='')}" if order_by else ""
        base_query = (
            f"where={encoded_where}&outFields=*{order_clause}"
            f"&returnZ=true&resultRecordCount={page_size}"
            f"&maxRecordCountFactor=5&outSR=&f=json{extra_params}"
        )
        rows: list = []
        offset = 0
        async with await core_helpers.get_async_requests_session(timeout=120) as session:
            while True:
                data = await _get_json(session, f"{base_url}{base_query}&resultOffset={offset}")
                features = data.get("features") or []
                for feat in features:
                    attrs = feat.get("attributes") or {}
                    if attrs:
                        rows.append(attrs)
                if not data.get("exceededTransferLimit") or not features:
                    break
                offset += len(features)
        return rows

    async def _fetch_daily(base_url: str, entity_id: str, start_date: str | None, end_date: str | None) -> list:
        if start_date is not None and end_date is None:
            end_date = datetime.now().strftime("%Y-%m-%d")
        if start_date is None and end_date is not None:
            start_date = "2019-01-01"
        where = f"portid = '{entity_id.upper()}'"
        if start_date is not None and end_date is not None:
            where += (
                f" AND date >= TIMESTAMP '{start_date} 00:00:00'"
                f" AND date <= TIMESTAMP '{end_date} 00:00:00'"
            )
        attrs_list = await _paginate(base_url, where=where, order_by="date", page_size=1000)
        return [
            {
                "date": datetime(int(attrs["year"]), int(attrs["month"]), int(attrs["day"])).strftime("%Y-%m-%d"),
                **{k: v for k, v in attrs.items() if k not in ("year", "month", "day", "date", "ObjectId")},
            }
            for attrs in attrs_list
        ]

    @alru_cache(maxsize=25)
    async def get_daily_chokepoint_data(chokepoint_id, start_date: str | None = None, end_date: str | None = None) -> list:
        return await _fetch_daily(pwh.CHOKEPOINTS_BASE_URL, chokepoint_id, start_date, end_date)

    @alru_cache(maxsize=125)
    async def get_daily_port_activity_data(port_id, start_date: str | None = None, end_date: str | None = None) -> list:
        if port_id is None:
            raise OpenBBError(ValueError("Either port_id or country_code must be provided."))
        return await _fetch_daily(pwh.DAILY_TRADE_BASE_URL, port_id, start_date, end_date)

    @alru_cache(maxsize=1)
    async def list_ports() -> list:
        url = (
            "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_ports_database/"
            "FeatureServer/0/query?where=1%3D1&outFields=countrynoaccents,portid,lon,lat,portname,ISO3,continent,fullname"
            "+&returnGeometry=false&orderByFields=vessel_count_total%20DESC&outSR=&f=json"
        )
        try:
            async with await core_helpers.get_async_requests_session() as session:
                data = await _get_json(session, url)
            return [feature.get("attributes", {}) for feature in data.get("features", [])]
        except OpenBBError:
            raise
        except Exception as e:
            raise OpenBBError(f"Error fetching ports data: {e} -> {e.args}") from e

    pwh.get_daily_chokepoint_data = get_daily_chokepoint_data
    pwh.get_daily_port_activity_data = get_daily_port_activity_data
    pwh.list_ports = list_ports
    if hasattr(pwh, "_arcgis_query"):
        pwh._arcgis_query = _paginate
    applied.append("IMF PortWatch checks ArcGIS error payloads (no silent page truncation)")

    # Top Container Ports: rows carry only PortWatch's id (PORT1065); show the port's name with it (Busan (PORT1065)), from the ports list
    try:
        from openbb_imf.models.container_metrics import ImfContainerMetricsFetcher as _CM
    except ImportError:
        return
    _cm_extract = _CM.aextract_data

    async def _cm_named(query, credentials=None, **kwargs):
        rows = await _cm_extract(query, credentials, **kwargs)
        try:
            # the ports list spells ids in lower case (port1065), the metrics in upper case (PORT1065)
            names = {str(p.get("portid")).upper(): p.get("portname") for p in await pwh.list_ports() if p.get("portid")}
        except Exception:                     # names are a label only: the numbers stand without them
            return rows
        rows = [dict(r) for r in rows]        # the source rows are cached: label copies, never the shared dicts
        for r in rows:
            n = names.get(str(r.get("portid") or "").upper())
            if n and r.get("portid"):
                r["portid"] = f"{n} ({r['portid']})"
        return rows

    _CM.aextract_data = staticmethod(_cm_named)
    _CM.extract_data = staticmethod(_cm_named)     # Fetcher copied aextract_data to extract_data when the class was made
    applied.append("IMF container metrics name each port (Busan (PORT1065))")


# ---------------------------------------------------------------- request guard (Host, Origin, error text)
def origins_for(port: str) -> set:
    """The pages that may call this backend: the terminal UI and the backend itself, under each local host name."""
    return {f"http://{h}:{p}" for h in ("127.0.0.1", "localhost", "[::1]") for p in (UI_PORT, port)}


def request_allowed(origin, fetch_site, origins: set) -> bool:
    """A browser request from another site is refused: with an Origin, it must be the terminal's; without one (a plain
    GET such as an <img> or <script> another page plants, which could spend keyed or rate-limited quotas), the browser's
    Sec-Fetch-Site must not say cross-site. Clients that send neither header (Python, curl, the launcher) are let in."""
    if origin is not None:
        return origin.strip().lower() in origins
    site = (fetch_site or "").strip().lower()
    return site in ("", "same-origin", "same-site", "none")


# Routes the platform declares as GET while reading their list parameter from a JSON body (a browser cannot send a
# body with GET). The terminal POSTs document picks; these paths take that POST as the GET it is (review B3:
# commodity/psd_report answered 405 to POST and 422 "urls Field required" to a query string; GET + body -> 200 PDF).
GET_WITH_BODY = {"/api/v1/commodity/psd_report"}


def route_method(method: str, path: str) -> str:
    return "GET" if method == "POST" and path in GET_WITH_BODY else method


# White HTML pages the terminal shows in a frame (review G1: ECB release/dataset, USAspending award/recipient). The
# terminal cannot restyle a frame from another origin, and a page copied into srcdoc inherits the terminal's CSP, which
# blocks the page's own number formatter; so the backend adds the dark palette to the page it serves.
DARK_HTML_PATHS = {"/api/v1/ecb/release_document", "/api/v1/ecb/dataflow_information", "/api/v1/ustreasury/award_info",
                   "/api/v1/ustreasury/recipient_info"}
DARK_STYLE = ("<style>html{color-scheme:dark}body{background:#1a1a19!important;color:#e8e6e1!important}"
              "body *:not(img):not(svg):not(canvas):not(video){background-color:transparent!important;color:inherit!important;"
              "border-color:#383835!important}a,a *{color:#8ab4f8!important}</style>")


def dark_html(page: str) -> str:
    """The page with the dark palette added at the start of its head (or of the document when it has none)."""
    import re

    out, n = re.subn(r"<head(\s[^>]*)?>", lambda m: m.group(0) + DARK_STYLE, page, count=1, flags=re.I)
    return out if n else DARK_STYLE + page


def frame_error_page(raw: str, status: int) -> str:
    """An error answered to a frame load, as a short dark page: the frame showed the raw JSON on white (review G1:
    'Federal Award Info' with a 400 dump). The text is escaped; key material was redacted before this."""
    import html as _h

    try:
        detail = json.loads(raw).get("detail")
    except Exception:  # noqa: BLE001
        detail = None
    if not isinstance(detail, str) or not detail.strip():
        detail = f"The source answered HTTP {status}."
    return dark_html("<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body style=\"font-family:sans-serif;padding:1.2rem\">"
                     f"<p>{_h.escape(detail[:600])}</p></body></html>")


def sanitize_error_body(text: str) -> str:
    """Strip raw aiohttp ContentTypeError upstream URLs while keeping signup links and existing redaction markers."""
    import re

    out = re.sub(
        r"Attempt to decode JSON with unexpected mimetype:\s*text/html[^'\"\\]*",
        "Source returned an HTML error page instead of JSON",
        text,
    )
    out = re.sub(r",\s*url=(?:URL\()?['\"]https?://[^'\"\\)]*['\"]\)?", "", out)
    return out


def install_guard(port: str):
    """Wrap the ASGI app uvicorn loads:
      * only Host 127.0.0.1 / localhost (this port) is answered (blocks DNS rebinding);
      * a request carrying an Origin other than the terminal UI or this backend itself is refused (other web pages
        can neither read data nor spend keyed quotas), and CORS answers name that origin instead of "*";
      * error bodies are passed through redact(), so no key reaches the browser.
    """
    import uvicorn.config as uc

    port = str(port)
    hosts = {"127.0.0.1", "localhost", "[::1]"}
    origins = origins_for(port)

    def host_ok(value: str) -> bool:
        value = value.strip().lower()
        if value.startswith("["):
            h, _, rest = value.partition("]")
            h, p = h + "]", rest[1:] if rest.startswith(":") else ""
        else:
            h, _, p = value.partition(":")
        return h in hosts and p in ("", port)

    class Guard:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") not in ("http", "websocket"):
                return await self.app(scope, receive, send)
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
            origin = headers.get("origin")
            if not host_ok(headers.get("host", "")) or not request_allowed(origin, headers.get("sec-fetch-site"), origins):
                # which check failed, so a refused card can be traced from v5.log (these headers carry no key material)
                print(f"Refused {scope.get('path', '')}: host={headers.get('host')!r} origin={origin!r} "
                      f"sec-fetch-site={headers.get('sec-fetch-site')!r} sec-fetch-dest={headers.get('sec-fetch-dest')!r}", flush=True)
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1008})
                body = b'{"detail":"Refused: this backend only answers the local terminal."}'
                await send({"type": "http.response.start", "status": 403, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                return await send({"type": "http.response.body", "body": body})
            if scope["type"] == "websocket":
                return await self.app(scope, receive, send)
            if scope.get("path") == "/_terminal/warm_chart":
                from urllib.parse import parse_qs

                qs = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
                sym = (qs.get("symbol") or ["^GSPC"])[0]
                res = await warm_tvchart(sym)
                body = json.dumps(res).encode("utf-8")
                hs = [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())]
                if origin:
                    hs.extend([(b"vary", b"Origin"), (b"access-control-allow-origin", origin.encode("latin-1"))])
                await send({"type": "http.response.start", "status": 200, "headers": hs})
                return await send({"type": "http.response.body", "body": body})
            if route_method(scope.get("method", ""), scope.get("path", "")) != scope.get("method"):
                scope = dict(scope, method="GET")
            state = {"start": None, "chunks": [], "sent": False}
            to_frame = headers.get("sec-fetch-dest") == "iframe"       # Firefox (90+) and Chrome send it on frame loads
            dark = scope.get("path") in DARK_HTML_PATHS
            stale_notes: list = []
            cache_status: list = []
            substitute: dict = {}
            sub_tok = SUBSTITUTE.set(substitute)
            stale_tok = STALE_NOTES.set(stale_notes)        # this request's context (each ASGI request runs in its own)
            pool_tok = CALLER_POOL.set("x" if headers.get("x-terminal-pool") == "x" else "ui")
            refresh_tok = FORCE_REFRESH.set(headers.get("x-terminal-refresh", "").strip().lower() in ("1", "true", "yes"))
            cache_tok = CACHE_STATUS.set(cache_status)

            async def send2(msg):
                if msg["type"] == "http.response.start":
                    state["sent"] = True
                    hs = []
                    for k, v in msg.get("headers") or []:
                        if k.lower() == b"access-control-allow-origin":
                            if not origin:
                                continue
                            v = origin.encode("latin-1")
                        hs.append((k, v))
                    expose = []
                    if stale_notes:
                        hs.append((b"x-terminal-stale", stale_header(stale_notes)))
                        expose.append(b"X-Terminal-Stale")
                    if cache_status:
                        hs.append((b"x-terminal-cache", cache_status[0].encode("ascii")))
                        expose.append(b"X-Terminal-Cache")
                    late = delayed_note(scope.get("path", ""), scope.get("query_string") or b"") if msg.get("status", 200) < 400 else ""
                    if substitute:
                        late = late and substitute["delayed"]           # never "delayed prices from Cboe" over another source's numbers
                        hs.append((b"x-terminal-source", stale_header([substitute["sources"]])))
                        hs.append((b"x-terminal-fallback", stale_header([substitute["note"]])))
                        expose += [b"X-Terminal-Source", b"X-Terminal-Fallback"]
                    if late:
                        hs.append((b"x-terminal-delayed", stale_header([late])))
                        expose.append(b"X-Terminal-Delayed")
                    if expose:
                        hs.append((b"access-control-expose-headers", b", ".join(expose)))
                    if origin:
                        hs.append((b"vary", b"Origin"))
                        if not any(k.lower() == b"access-control-allow-origin" for k, _ in hs):
                            # Starlette's 500 answer bypasses the CORS layer, so the page saw only "Failed to fetch" and
                            # could not say what failed. The origin was checked above.
                            hs.append((b"access-control-allow-origin", origin.encode("latin-1")))
                        if scope.get("method") == "OPTIONS":
                            allow_hdrs = [(i, v) for i, (k, v) in enumerate(hs) if k.lower() == b"access-control-allow-headers"]
                            if not allow_hdrs:
                                hs.append((b"access-control-allow-headers", b"X-Terminal-Refresh, Content-Type"))
                            else:
                                idx, cur_val = allow_hdrs[0]
                                parts = [p.strip().lower() for p in cur_val.decode("latin-1").split(",") if p.strip()]
                                if "*" not in parts and "x-terminal-refresh" not in parts:
                                    hs[idx] = (b"access-control-allow-headers", (cur_val.decode("latin-1") + ", X-Terminal-Refresh").encode("latin-1"))
                    msg = dict(msg, headers=hs)
                    if msg.get("status", 200) >= 400 or dark:
                        state["start"] = msg           # hold it: the body is redacted first (or the dark palette added)
                        return None
                    return await send(msg)
                if msg["type"] == "http.response.body" and state["start"] is not None:
                    state["chunks"].append(msg.get("body") or b"")
                    if msg.get("more_body"):
                        return None
                    raw = b"".join(state["chunks"])
                    start = state["start"]
                    status = start.get("status", 200)
                    ctype = b"".join(v for k, v in start.get("headers") or [] if k.lower() == b"content-type").lower()
                    html_out = False
                    try:
                        text = raw.decode("utf-8")
                        if status >= 400:
                            text = sanitize_error_body(redact(text))
                            if to_frame and b"json" in ctype:
                                text, html_out = frame_error_page(text, status), True
                        elif b"text/html" in ctype:
                            text = dark_html(text)
                        raw = text.encode("utf-8")
                    except UnicodeDecodeError:
                        pass
                    drop = (b"content-length", b"content-type") if html_out else (b"content-length",)
                    hs = [(k, v) for k, v in start.get("headers") or [] if k.lower() not in drop] + [(b"content-length", str(len(raw)).encode())]
                    if html_out:
                        hs.append((b"content-type", b"text/html; charset=utf-8"))
                    state["start"] = None
                    await send(dict(start, headers=hs))
                    return await send({"type": "http.response.body", "body": raw})
                return await send(msg)

            try:
                return await self.app(scope, receive, send2)
            except Exception as e:  # noqa: BLE001
                import asyncio as _asyncio

                if isinstance(e, _asyncio.CancelledError):
                    raise
                detail = sanitize_error_body(redact(f"{type(e).__name__}: {e}"))[:500]
                say(f"ERROR:    {scope.get('path')} -> {detail}")
                if state["sent"]:
                    return None
                raw_json = json.dumps({"detail": detail}).encode("utf-8")
                if to_frame:
                    raw = frame_error_page(raw_json.decode("utf-8"), 500).encode("utf-8")
                    ctype = b"text/html; charset=utf-8"
                else:
                    raw = raw_json
                    ctype = b"application/json; charset=utf-8"
                hs = [(b"content-type", ctype), (b"content-length", str(len(raw)).encode())]
                if origin:
                    hs.extend([(b"vary", b"Origin"), (b"access-control-allow-origin", origin.encode("latin-1"))])
                await send({"type": "http.response.start", "status": 500, "headers": hs})
                return await send({"type": "http.response.body", "body": raw})
            finally:
                SUBSTITUTE.reset(sub_tok)
                STALE_NOTES.reset(stale_tok)
                CALLER_POOL.reset(pool_tok)
                FORCE_REFRESH.reset(refresh_tok)
                CACHE_STATUS.reset(cache_tok)

    orig_load = uc.Config.load

    def load(self):
        orig_load(self)
        if not isinstance(self.loaded_app, Guard):
            self.loaded_app = Guard(self.loaded_app)

    uc.Config.load = load


# Every runtime fix, in the order it is installed (the order matters where noted on the installer).
PATCHES = [
    Patch("thread_offload", "sync fetchers off the event loop", install_thread_offload, critical=True, targets=_targets("openbb-core")),
    Patch("fetch_cache", "disk cache for slow sources", install_fetch_cache, critical=True, targets=_targets("openbb-core")),
    Patch("futures_curve_fix", "futures curves in their own thread", install_futures_curve_fix, targets=_targets("openbb-yfinance")),
    Patch("fred_keyless", "FRED series without a key", install_fred_keyless, targets=_targets("openbb-fred")),
    Patch("oecd_fail_fast", "OECD fails fast on rate limit", install_oecd_fail_fast, targets=_targets("openbb-oecd")),
    Patch("eia_row_cap", "EIA row cap and retries", install_eia_row_cap, targets=_targets("openbb-us-eia")),
    Patch("imf_arcgis_fix", "IMF PortWatch ArcGIS error checks", install_imf_arcgis_fix, targets=_targets("openbb-imf")),
    Patch("tmx_filings_fix", "TMX SEDAR+ filings missing description coercion", install_tmx_filings_fix, targets=_targets("openbb-tmx")),
    Patch("yfinance_losers_fix", "Yahoo Finance top losers filter", install_yfinance_losers_fix, targets=_targets("openbb-yfinance")),
    Patch("sec_quarters_fix", "SEC company facts quarters from year-to-date chains", install_sec_quarters_fix, targets=_targets("openbb-sec")),
    Patch("fx_session_closes", "Yahoo FX daily closes", install_fx_session_closes, targets=_targets("openbb-yfinance")),
    Patch("montreal_times", "Montreal time for Yahoo intraday bars and Nasdaq Nordic notices", install_montreal_times, targets=_targets("openbb-yfinance", "openbb-nasdaq")),
    Patch("nasdaq_etf_fallback", "Nasdaq ETF fallback for quotes, profiles, history and short interest", install_nasdaq_etf_fallback, targets=_targets("openbb-nasdaq")),
    Patch("sec_growth_fix", "SEC growth units, adjacent quarters and TTM growth", install_sec_growth_fix, targets=_targets("openbb-sec")),
    Patch("yfinance_price_target_fix", "Yahoo analyst actions order and empty price targets", install_yfinance_price_target_fix, targets=_targets("openbb-yfinance")),
    Patch("yfinance_futures_curve_fix", "Yahoo futures curve prices (one-sided quotes, untraded months)", install_yfinance_futures_curve_fix, targets=_targets("openbb-yfinance")),
    Patch("yfinance_fund_multiples_fix", "Yahoo fund multiples", install_yfinance_fund_multiples_fix, targets=_targets("openbb-yfinance")),
    Patch("yfinance_recommendations_months", "Yahoo recommendations readable months", install_yfinance_recommendations_months, targets=_targets("openbb-yfinance")),
    Patch("tmx_holdings_currency_fix", "TMX ETF holdings currency", install_tmx_holdings_currency_fix, targets=_targets("openbb-tmx")),
    Patch("tmx_asset_info_escape_fix", "TMX Asset Info escaping", install_tmx_asset_info_escape_fix, targets=_targets("openbb-tmx")),
    Patch("congress_search_bill_id", "Congressional search bill id", install_congress_search_bill_id, targets=_targets("openbb-congress-gov")),
    Patch("fred_bond_index_unit", "FRED bond index unit column", install_fred_bond_index_unit, targets=_targets("openbb-fred")),
    Patch("tmx_quote_symbol_order", "TMX quote symbol order", install_tmx_quote_symbol_order, targets=_targets("openbb-tmx")),
    Patch("cboe_chain_symbols", "Cboe chain contract symbols", install_cboe_chain_symbols, targets=_targets("openbb-cboe")),
    Patch("cboe_options_fallback", "Cboe options analytics fallback", install_cboe_options_fallback, targets=_targets("openbb-cboe")),
    Patch("cboe_settlement_date", "Cboe settlement session date", install_cboe_settlement_date, targets=_targets("openbb-cboe")),
    Patch("cboe_market_fallbacks", "Cboe market fallbacks", install_cboe_market_fallbacks, targets=_targets("openbb-cboe")),
    Patch("cds_spread_bp", "CFTC CDS spread in bp", install_cds_spread_bp, targets=_targets("openbb-cftc")),
    Patch("usaspending_format", "USAspending page amounts", install_usaspending_format, targets=_targets("openbb-government-us")),
    Patch("imf_table_scale_label", "IMF presentation table scale label", install_imf_table_scale_label, targets=_targets("openbb-imf")),
    Patch("oecd_dataflow_list", "OECD dataflow list", install_oecd_dataflow_list, targets=_targets("openbb-oecd")),
    Patch("cot_search_aliases", "COT search aliases", install_cot_search_aliases, targets=_targets("openbb-cftc")),
    Patch("sec_fact_any_tag", "SEC company facts tags", install_sec_fact_any_tag, targets=_targets("openbb-sec")),
    Patch("deribit_funding_apr", "Deribit funding APR", install_deribit_funding_apr, targets=_targets("openbb-deribit")),
    Patch("deribit_book_ladder", "Deribit order book ladder", install_deribit_book_ladder, targets=_targets("openbb-deribit")),
    Patch("eia_aeo_identified", "EIA AEO unnamed rows", install_eia_aeo_identified, targets=_targets("openbb-us-eia")),
    Patch("eia_grid_hours", "EIA grid hours", install_eia_grid_hours, targets=_targets("openbb-us-eia")),
    Patch("oecd_gdp_units", "OECD GDP unit column", install_oecd_gdp_units, targets=_targets("openbb-oecd")),
    Patch("rescales", "unit rescales (Nasdaq ratios, TMX short interest)", install_rescales, targets=_targets("openbb-nasdaq", "openbb-tmx")),
    Patch("blocking_route_offload", "blocking route event-loop offload", install_blocking_route_offload, critical=True, targets=_targets("fastapi", "openbb-platform-api")),
    Patch("session_leak_fix", "WeakSet ClientSession cleanup", install_session_leak_fix, targets=_targets("aiohttp")),
    Patch("disable_mcp_subprocesses", "disable background MCP subprocesses", install_disable_mcp_subprocesses, targets=_targets("openbb-mcp-server")),
    Patch("yfinance_crumb_fix", "Yahoo Finance crumb guard and 401 recovery", install_yfinance_crumb_fix, critical=True, targets=_targets("yfinance", "openbb-yfinance")),
    Patch("yfinance_options_fix", "YFinance options chains recovery", install_yfinance_options_fix, targets=_targets("yfinance", "openbb-yfinance")),
    Patch("tmx_gainers_fix", "TMX gainers missing longName fix", install_tmx_gainers_fix, targets=_targets("openbb-tmx")),
    Patch("nasdaq_empty_json_fix", "Nasdaq empty JSON recovery", install_nasdaq_empty_json_fix, targets=_targets("openbb-nasdaq")),
    Patch("nasdaq_rate_limit_fix", "Nasdaq rate-limit and transport guard", install_nasdaq_rate_limit_fix, critical=True, targets=_targets("openbb-nasdaq")),
    Patch("user_settings_cache", "user_settings.json mtime cache", install_user_settings_cache, targets=_targets("openbb-core")),
    Patch("tmx_movers_fix", "TMX market movers ranking", install_tmx_movers_fix, targets=_targets("openbb-tmx")),
    Patch("tmx_asset_info_cache", "TMX asset info cache", install_tmx_asset_info_cache, targets=_targets("openbb-tmx")),
    Patch("tvchart_fix", "TradingView chart cache & loading indicator", install_tvchart_fix, targets=_targets("openbb-platform-api", "openbb-yfinance")),
    Patch("fx_chart_fix", "TradingView FX closes", install_fx_chart_fix, targets=_targets("openbb-platform-api", "openbb-yfinance")),
    Patch("cot_canada", "COT Canadian dollar", install_cot_canada, targets=_targets("openbb-cftc")),
    Patch("guard", "request guard (Host and Origin checks, redacted errors)", _install_guard_patch, critical=True, targets=_targets("uvicorn")),
]


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "backend"
    args = sys.argv[2:]
    guard_output()
    try:
        faulthandler.enable(file=sys.__stderr__)
    except Exception:  # noqa: BLE001  (no console stream: the watchdog's dumps still work)
        pass
    port = _arg(args, "--port", "6900")
    _GUARD_PORT[0] = port
    write_patch_status(name, apply_fixes())
    threading.Thread(target=watchdog, args=(name, _arg(args, "--host", "127.0.0.1"), port), daemon=True, name="freeze-watchdog").start()
    sys.argv = ["openbb-api", *args]
    from openbb_platform_api.main import main as openbb_main
    openbb_main()


if __name__ == "__main__":
    main()
