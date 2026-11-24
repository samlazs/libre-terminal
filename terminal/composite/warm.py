"""Two-phase background cache warmer for initial UI landing views and charts."""

from __future__ import annotations

import datetime as dt
import threading
import time
import urllib.parse

import composite as C

_LANDING_STEPS = (
    ("overview_boards", "Market boards, watchlist & performance chart"),
    ("tv_chart", "Interactive chart (S&P 500)"),
    ("market_news", "Market news"),
    ("global_yields", "Global yields (G10)"),
)
_BG_STEPS = (
    ("global_indices", "Global equity indices"),
    ("rates_extra", "Yield curves & policy rates"),
    ("tv_chart_aapl", "Company chart (AAPL)"),
    ("geo_energy", "Energy & geopolitics caches"),
)

_warm_lock = threading.Lock()
_warm_started = False
_warm_thread: threading.Thread | None = None
_warm_gen = 0


def _initial_state() -> dict:
    steps = [
        {"id": sid, "label": label, "phase": "landing", "state": "pending", "ms": 0}
        for sid, label in _LANDING_STEPS
    ] + [
        {"id": sid, "label": label, "phase": "background", "state": "pending", "ms": 0}
        for sid, label in _BG_STEPS
    ]
    return {"phase": "idle", "landing_ready": False, "started_at": None, "steps": steps}


_warm_state = _initial_state()


def _reset_warm_state() -> None:
    """Reset module-level warm-up state (used by offline unit tests)."""
    global _warm_started, _warm_thread, _warm_gen, _warm_state
    with _warm_lock:
        _warm_gen += 1
        _warm_started = False
        _warm_thread = None
        _warm_state = _initial_state()


def warm_status() -> dict:
    """Return a snapshot of warm-up progress for GET /api/warm and the launcher."""
    with _warm_lock:
        steps = [dict(s) for s in _warm_state["steps"]]
        phase = _warm_state["phase"]
        landing_ready = bool(_warm_state["landing_ready"])
        started_at = _warm_state["started_at"]
    landing = [s for s in steps if s["phase"] == "landing"]
    bg = [s for s in steps if s["phase"] == "background"]
    done = sum(1 for s in landing if s["state"] in ("done", "failed"))
    bg_done = sum(1 for s in bg if s["state"] in ("done", "failed"))
    running = [s["label"] for s in steps if s["state"] == "running"]
    elapsed_s = round(max(0.0, time.time() - started_at), 1) if started_at is not None else 0.0
    return {
        "phase": phase,
        "landing_ready": landing_ready,
        "done": done,
        "total": len(landing),
        "bg_done": bg_done,
        "bg_total": len(bg),
        "current": ", ".join(running),
        "steps": steps,
        "elapsed_s": elapsed_s,
    }


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


def _set_step(gen: int, step_id: str, state: str, ms: int | None = None) -> None:
    with _warm_lock:
        if gen != _warm_gen:
            return
        for s in _warm_state["steps"]:
            if s["id"] == step_id:
                s["state"] = state
                if ms is not None:
                    s["ms"] = ms
                break


def _run_step(gen: int, step_id: str, fn) -> bool:
    _set_step(gen, step_id, "running")
    t0 = time.perf_counter()
    ok = True
    try:
        fn()
    except Exception:  # noqa: BLE001
        ok = False
    ms = int(round((time.perf_counter() - t0) * 1000))
    _set_step(gen, step_id, "done" if ok else "failed", ms=ms)
    return ok


def _warm_overview_boards() -> None:
    presets = ("us_equity", "us_sectors", "fx", "commodities", "global_markets", "fixed_income", "crypto")
    syms = list(C._syms(C.STARTER_WATCHLIST, 80))
    for p in presets:
        for _, items in C._resolve_board(p, None):
            for _, s in items:
                syms.append(s)
    for _, row in C.STYLE_BOX:
        syms.extend(row)
    syms.extend(["^GSPC", "^NDX", "^DJI", "^RUT"])
    deduped = list(dict.fromkeys(syms))
    C.history(deduped, dt.date.today() - dt.timedelta(days=410))
    _safe(C.watchlist)
    for p in presets:
        _safe(C.board, p)
    _safe(C.performance, "^GSPC,^NDX,^DJI,^RUT")
    _safe(C.style_box)


def _warm_tv_chart(symbol: str = "^GSPC") -> None:
    v5_root = C.V5.rsplit("/api/v1", 1)[0]
    res = C.http_json(f"{v5_root}/_terminal/warm_chart?symbol={urllib.parse.quote(symbol)}", timeout=35)
    if isinstance(res, dict) and res.get("ok") is False:
        raise RuntimeError(res.get("error") or f"chart warm failed for {symbol}")


def _warm_market_news() -> None:
    C.news_view(subject="market")


def _warm_global_yields() -> None:
    C.global_yields()


def _warm_global_indices() -> None:
    C.board("global_indices")


def _warm_rates_extra() -> None:
    _safe(C.policy_rates)
    _safe(C.yield_curves)


def _warm_geo_energy() -> None:
    _safe(C._gpr)
    _safe(C._chokepoints)
    try:
        has_eia = bool(C._eia_key())
    except Exception:  # noqa: BLE001
        has_eia = False
    if not has_eia:
        return
    ids = [s for k, v in C.MACRO_SETS.items() if C.MACRO_CATEGORY.get(k, "").startswith(("Energy", "Geopolitics")) for _, s, _ in v[3]]
    eia = sorted({s.partition("@")[0][4:] for s in ids if s.startswith("EIA:")})
    if eia:
        with C.cf.ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda s: C._safe(C._eia_one, s), eia))


def warm(force: bool = False):
    """Pre-load landing views & charts in parallel, then secondary caches in the background."""
    global _warm_started, _warm_thread, _warm_gen, _warm_state
    with _warm_lock:
        if _warm_started and not force:
            return _warm_thread
        if force:
            _warm_gen += 1
            _warm_state = _initial_state()
        _warm_started = True
        gen = _warm_gen
        _warm_state["phase"] = "landing"
        _warm_state["landing_ready"] = False
        _warm_state["started_at"] = time.time()

    def run():
        landing_jobs = (
            ("overview_boards", _warm_overview_boards),
            ("tv_chart", lambda: _warm_tv_chart("^GSPC")),
            ("market_news", _warm_market_news),
            ("global_yields", _warm_global_yields),
        )
        try:
            with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
                futs = [ex.submit(_run_step, gen, sid, fn) for sid, fn in landing_jobs]
                for f in futs:
                    f.result()
        finally:
            with _warm_lock:
                if gen == _warm_gen:
                    _warm_state["landing_ready"] = True
                    _warm_state["phase"] = "background"

        bg_jobs = (
            ("global_indices", _warm_global_indices),
            ("rates_extra", _warm_rates_extra),
            ("tv_chart_aapl", lambda: _warm_tv_chart("AAPL")),
            ("geo_energy", _warm_geo_energy),
        )
        try:
            for sid, fn in bg_jobs:
                with _warm_lock:
                    if gen != _warm_gen:
                        return
                _run_step(gen, sid, fn)
        finally:
            with _warm_lock:
                if gen == _warm_gen:
                    _warm_state["phase"] = "ready"

    t = threading.Thread(target=run, daemon=True, name="terminal-warm")
    with _warm_lock:
        if gen == _warm_gen:
            _warm_thread = t
    t.start()
    return t
