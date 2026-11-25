"""Yahoo Finance price history, per-symbol SWR cache, FX pairs, and OHLC."""

from __future__ import annotations

import concurrent.futures as _cf
import datetime as dt
import json
import math
import re
import threading
import time

import composite as C

# ---------------------------------------------------------------- price history
class PriceHistory(dict):
    """{symbol: [(iso_date, close), ...]} (closes adjusted for splits only), plus .divs = {symbol: {iso_date: cash
    distribution}} on ex-dates as Yahoo reports them, for total returns (total_return)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.divs = {}
        self.stale_ts = None
        self.swr_ts = None

    def merge(self, part):
        self.update(part)
        self.divs.update(getattr(part, "divs", {}))


def _fetch_history(symbols, start, interval, _retry=True):
    try:
        rows = C.api(C.V5, "equity/price/historical", _timeout=25, symbol=",".join(symbols), provider="yfinance", start_date=start, interval=interval)
    except C.CompositeError:
        rows = None
    if not rows and _retry:
        # Yahoo sometimes answers a multi-ticker request with nothing (throttling, or one bad ticker): go one by one.
        if len(symbols) == 1:
            time.sleep(0.8)
            return C._fetch_history(symbols, start, interval, _retry=False)
        out = C.PriceHistory()
        with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
            for part in ex.map(lambda s: C._fetch_history([s], start, interval, _retry=False), symbols):
                out.merge(part)
        return out
    out = C.PriceHistory({s: [] for s in symbols})
    single = symbols[0] if len(symbols) == 1 else None
    for r in rows or []:
        sym = r.get("symbol") or single
        c = r.get("close")
        d = str(r.get("date") or "")[:10]
        if sym in out and isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and len(d) == 10:
            out[sym].append((d, float(c)))
            div = r.get("dividend")
            if isinstance(div, (int, float)) and not isinstance(div, bool) and math.isfinite(div) and div > 0:
                out.divs.setdefault(sym, {})[d] = float(div)
    for s in out:
        out[s].sort()
    missing = [s for s, v in out.items() if not v]
    if _retry and len(symbols) > 1 and missing and len(missing) < len(symbols):
        # a multi-symbol answer without some symbols: ask for those alone (otherwise they show "no data" for 5 minutes)
        with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
            for part in ex.map(lambda s: C._fetch_history([s], start, interval, _retry=False), missing[:20]):
                out.update({k: v for k, v in part.items() if v})
                out.divs.update(getattr(part, "divs", {}))
    return out


HIST_TTL = 300

HIST_STALE_TTL = 24 * 3600

PARTIAL_TTL = 30

_HIST_REVALIDATING = C.LRUSet()            # (symbol, interval) being refreshed in the background (bounded)


def _sym_hist_path(sym):
    prefix = "h1d-ny17" if C.fx_pair(sym) else "h1d"          # FX copies saved before 2026-11-22 hold opening rates as closes
    return C.CACHE_DIR / f"{C.disk_name(prefix, sym)}.json"


def _sym_hist_load(sym, interval):
    with C._guard:
        hit = C._cache.get(("hsym", sym, interval))
    if hit is not None:
        return hit[1]
    if interval != "1d":
        return None
    try:
        saved = json.loads(C._sym_hist_path(sym).read_text(encoding="utf-8"))
        if isinstance(saved, dict) and saved.get("rows") and saved.get("start") and saved.get("t"):
            with C._guard:
                C._cache[("hsym", sym, interval)] = (C._mono(saved["t"]), saved)
            return saved
    except (OSError, ValueError, TypeError):
        pass
    return None


def _sym_hist_save(sym, interval, start_iso, rows, divs, now=None):
    """Persist one symbol's history only when non-empty (W4 complete-answer rule); never shrink an earlier start."""
    if not rows:
        return
    ts = time.time() if now is None else float(now)
    old = C._sym_hist_load(sym, interval)
    if old and old.get("rows") and old.get("start") and old["start"] < start_iso:
        merged_rows = [[p[0], float(p[1])] for p in old["rows"] if p[0] < start_iso] + [[d, float(v)] for d, v in rows]
        merged_divs = {d: float(v) for d, v in (old.get("divs") or {}).items() if d < start_iso}
        merged_divs.update({d: float(v) for d, v in (divs or {}).items()})
        merged_start = old["start"]
    else:
        merged_rows = [[d, float(v)] for d, v in rows]
        merged_divs = {d: float(v) for d, v in (divs or {}).items()}
        merged_start = start_iso
    entry = {"t": ts, "start": merged_start, "rows": merged_rows, "divs": merged_divs}
    with C._guard:
        C._cache[("hsym", sym, interval)] = (C._mono(ts), entry)
    if interval == "1d":
        C._write_json(C._sym_hist_path(sym), entry)


def _sym_hist_get(sym, start_iso, interval, now=None):
    """Returns (state, ts, rows, divs, cached_start) where state is fresh | stale | expired | miss."""
    entry = C._sym_hist_load(sym, interval)
    if not entry or not entry.get("rows") or not entry.get("start") or entry["start"] > start_iso:
        return "miss", None, None, None, None
    rows = [(p[0], float(p[1])) for p in entry["rows"] if p[0] >= start_iso]
    if not rows:
        return "miss", None, None, None, None
    divs = {d: float(v) for d, v in (entry.get("divs") or {}).items() if d >= start_iso}
    ts = float(entry["t"])
    age = (time.time() if now is None else float(now)) - ts
    state = "fresh" if age <= C.HIST_TTL else "stale" if age <= C.HIST_STALE_TTL else "expired"
    return state, ts, rows, divs, entry["start"]


def _bg_refresh_history(syms, start_iso, interval):
    with C._guard:
        todo = [s for s in syms if (s, interval) not in C._HIST_REVALIDATING]
        for s in todo:
            C._HIST_REVALIDATING.add((s, interval))
    if not todo:
        return

    def worker():
        try:
            chunks = [todo[i:i + 20] for i in range(0, len(todo), 20)]
            with _cf.ThreadPoolExecutor(max_workers=2) as ex:
                for part in ex.map(lambda ch: C._fetch_history(ch, start_iso, interval), chunks):
                    divs = getattr(part, "divs", {})
                    for s, rows in (part or {}).items():
                        if rows:
                            C._sym_hist_save(s, interval, start_iso, rows, divs.get(s))
        except Exception as e:  # noqa: BLE001
            import sys
            try:
                from redact import redact
                msg = redact(str(e))
            except Exception:  # noqa: BLE001
                msg = type(e).__name__
            print(f"[prices] bg refresh failed: {msg}", file=sys.stderr)
        finally:
            with C._guard:
                for s in todo:
                    C._HIST_REVALIDATING.discard((s, interval))

    threading.Thread(target=worker, daemon=True).start()


def history(symbols, start: dt.date, interval="1d"):
    """{symbol: [(iso_date, close), ...]} for the given symbols since `start` (per-symbol cache + SWR, W4)."""
    symbols = list(dict.fromkeys(s.strip() for s in symbols if s and s.strip()))
    if not symbols:
        raise C.CompositeError("No symbols given.")
    start_iso = start.isoformat()
    key = ("hist", tuple(sorted(symbols)), start_iso, interval)

    def run():
        out = C.PriceHistory({s: [] for s in symbols})
        need, stale_syms, fallback = [], [], {}
        swr_times, fallback_times = [], []
        bg_start = start_iso
        for s in symbols:
            st, ts, rows, divs, cstart = C._sym_hist_get(s, start_iso, interval)
            if st == "fresh":
                out[s] = rows
                if divs:
                    out.divs[s] = divs
            elif st == "stale":
                out[s] = rows
                if divs:
                    out.divs[s] = divs
                stale_syms.append(s)
                swr_times.append(ts)
                if cstart and cstart < bg_start:
                    bg_start = cstart
            else:
                need.append(s)
                if st == "expired":
                    fallback[s] = (ts, rows, divs)
        if stale_syms:
            C._bg_refresh_history(stale_syms, bg_start, interval)
        if need:
            chunks = [need[i:i + 20] for i in range(0, len(need), 20)]
            fetched = C.PriceHistory()
            with C.cf.ThreadPoolExecutor(max_workers=3) as ex:
                for part in ex.map(lambda ch: C._fetch_history(ch, start_iso, interval), chunks):
                    fetched.merge(part)
            fdivs = getattr(fetched, "divs", {})
            for s in need:
                rows = fetched.get(s) or []
                if rows:
                    out[s] = rows
                    if fdivs.get(s):
                        out.divs[s] = fdivs[s]
                    C._sym_hist_save(s, interval, start_iso, rows, fdivs.get(s))
                elif s in fallback:
                    ts, fb_rows, fb_divs = fallback[s]
                    out[s] = fb_rows
                    if fb_divs:
                        out.divs[s] = fb_divs
                    fallback_times.append(ts)
        if not any(out.values()):            # not cached: Yahoo answering nothing is usually momentary
            raise C.CompositeError("Yahoo Finance returned no prices just now (it may be throttling or restarting). Press reload in a minute.")
        if fallback_times:
            out.stale_ts = min(fallback_times)
        if swr_times:
            out.swr_ts = min(swr_times)
        # stale-while-revalidate or >50% empty: re-check bundle after PARTIAL_TTL (30 s) so background refresh lands.
        # Only the throttled case is reported (missing names the symbols); the SWR one already says "refreshing".
        thin = sum(1 for s in symbols if out.get(s)) * 2 < len(symbols)
        out.partial = bool(stale_syms) or thin
        out.missing = [f"Yahoo Finance prices for {s}" for s in symbols if not out.get(s)] if thin else []
        return out

    # more than half the symbols empty (throttling) or SWR: kept 30 s, not 5 minutes, so a retry/refresh lands cleanly
    out = C.cached_complete(key, C.HIST_TTL, run, C.PARTIAL_TTL)
    if getattr(out, "stale_ts", None):
        C._note_stale_ts(out.stale_ts)
    if getattr(out, "swr_ts", None):
        C._note_swr_ts(out.swr_ts)
    return out


PRICE_ONLY = {"000001.SS", "FTSEMIB.MI", "OBX.OL"}          # indices that Yahoo lists without a caret


def is_security(symbol) -> bool:
    """A stock or fund: its holders receive distributions, so it has a total return. Indices (^GSPC is a price index),
    futures (=F), currencies (=X), coins (-USD) and the dollar index (DX-Y.NYB) are price-only."""
    s = str(symbol or "").upper()
    return bool(s) and not (s.startswith("^") or s.endswith(("=F", "=X", ".NYB")) or re.search(r"-(USD|USDT|EUR|GBP|BTC)$", s) or s in PRICE_ONLY)


def returns_basis(symbols) -> str:
    """'total' when every symbol is a stock or fund, else 'price': a column holds one convention, never both."""
    syms = [s for s in symbols if s]
    return "total" if syms and all(C.is_security(s) for s in syms) else "price"


CONVENTION = {"total": "Total return", "price": "Price return"}       # table footers (narrow): the column tips explain

CONVENTION_SHORT = {"total": "Total return (distributions reinvested)", "price": "Price return (distributions not included)"}

# Yahoo symbols whose price is a yield in percent (CBOE Treasury yield indices): changes are shown in basis points.
YIELD_SYMBOLS = {"^IRX", "^FVX", "^TNX", "^TYX"}


def is_yield(symbol) -> bool:
    return str(symbol or "").upper() in YIELD_SYMBOLS


# FX. Yahoo quotes "JPY=X" as USD/JPY (yen per dollar: up = yen weaker) and "EURUSD=X" as EUR/USD (up = euro stronger).
def fx_pair(symbol):
    """'JPY=X' -> ('USD', 'JPY'); 'EURUSD=X' -> ('EUR', 'USD'); anything else -> None."""
    s = str(symbol or "").upper()
    m = re.fullmatch(r"([A-Z]{3})=X", s)
    if m:
        return ("USD", m.group(1))
    m = re.fullmatch(r"([A-Z]{3})([A-Z]{3})=X", s)
    return (m.group(1), m.group(2)) if m else None


def fx_pair_label(symbol):
    p = C.fx_pair(symbol)
    return f"{p[0]}/{p[1]}" if p else None


def _opt_num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _invert(series):
    """A USD/XXX series as the value of XXX in dollars (same dates), so that up = currency stronger."""
    return [(d, 1.0 / c) for d, c in series if isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) and c > 0]


def _usd_rates(currencies):
    """{currency: USD per unit} for the currencies given (minor units such as GBp handled)."""
    out, need = {"USD": 1.0}, {}
    for c in currencies:
        if not c or c in out:
            continue
        major, mult = C.MINOR_UNITS.get(c, (c.upper(), 1.0))
        if major == "USD":
            out[c] = mult
        else:
            need[c] = (major, mult)
    if need:
        pairs = sorted({m + "USD=X" for m, _ in need.values()})
        hist = C._safe(C.history, pairs, dt.date.today() - dt.timedelta(days=14), missing="FX rates (Yahoo Finance)") or {}
        for c, (major, mult) in need.items():
            ser = hist.get(major + "USD=X") or []
            if ser:
                out[c] = ser[-1][1] * mult
    return out


# ---------------------------------------------------------------- chart
def _ohlc(symbol, start, interval):
    """[[date, open, high, low, close, volume], ...] from Yahoo, kept 5 minutes."""
    def run():
        rows = None
        for attempt in range(2):
            try:
                rows = C.api(C.V5, "equity/price/historical", symbol=symbol, provider="yfinance", start_date=start, interval=interval)
            except C.CompositeError:
                rows = None
            if rows:
                break
            time.sleep(0.8)
        out = []
        for r in rows or []:
            d = str(r.get("date") or "")[:10]
            c = _opt_num(r.get("close"))
            if len(d) == 10 and c is not None:
                out.append([d, _opt_num(r.get("open")), _opt_num(r.get("high")), _opt_num(r.get("low")), c, _opt_num(r.get("volume"))])
        out.sort(key=lambda x: x[0])
        if not out:
            raise C.CompositeError(f"Yahoo Finance has no price history for {symbol}. Non-U.S. listings need their exchange suffix (7203.T, SHEL.L, RHM.DE).")
        return out
    return C.cached(("ohlc", symbol, start, interval), 300, run)
