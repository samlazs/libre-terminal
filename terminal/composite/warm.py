"""Background cache warmer for initial UI landing views."""

from __future__ import annotations

import threading

import composite as C

def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


def warm():
    """Pre-load the boards used on the Summary dashboard so the first paint is quick."""
    def run():
        for p in ("us_equity", "us_sectors", "fx", "commodities", "global_markets", "fixed_income", "crypto", "global_indices"):
            try:
                C.board(p)
            except Exception:  # noqa: BLE001
                pass
        try:
            C.watchlist()                                # starter watchlist + per-symbol metadata ready on first paint
        except Exception:  # noqa: BLE001
            pass
        try:                                           # energy and geopolitics charts: fill the disk caches
            ids = [s for k, v in C.MACRO_SETS.items() if C.MACRO_CATEGORY.get(k, "").startswith(("Energy", "Geopolitics")) for _, s, _ in v[3]]
            eia = sorted({s.partition("@")[0][4:] for s in ids if s.startswith("EIA:")})
            with C.cf.ThreadPoolExecutor(max_workers=3) as ex:
                list(ex.map(lambda s: C._safe(C._eia_one, s), eia))
            C._safe(C._gpr); C._safe(C._chokepoints)
        except Exception:  # noqa: BLE001
            pass
        for fn in (C.global_yields, C.policy_rates):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=run, daemon=True).start()
