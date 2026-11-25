"""Yahoo symbol search, metadata cache, and company snapshot (DES) view."""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from collections import OrderedDict

import composite as C

# Everything here works without an API key (Yahoo Finance through the V5 backend, Yahoo's public peer list,
# FRED's public CSV when no FRED key is set).
TYPE_NAMES = {"equity": "Equity", "etf": "ETF", "mutualfund": "Fund", "index": "Index", "currency": "FX", "cryptocurrency": "Crypto", "future": "Future"}

MINOR_UNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ILA": ("ILS", 0.01), "ZAc": ("ZAR", 0.01)}


def _syms(value, cap=40):
    out = []
    for s in C._split(value if isinstance(value, (list, tuple)) else str(value or "").replace(";", ",")):
        s = s.strip().upper()
        if s and s not in out:
            out.append(s)
    return out[:cap]


def cik_of(q):
    """The 10-digit SEC CIK typed as such: ten digits (0000320193) or 'CIK' with up to ten digits. Shorter bare numbers
    are tickers on some exchanges (7203 for Toyota), so they are not taken as a CIK."""
    m = re.fullmatch(r"(?i)\s*(cik\s*:?\s*)?(\d{1,10})\s*", str(q or ""))
    if not m or (not m.group(1) and len(m.group(2)) != 10):
        return None
    return m.group(2).zfill(10)


def _cik_symbol(cik):
    """Ticker for an SEC CIK (SEC company tickers list via sec/symbol_map), kept a day; None when unknown."""
    def run():
        r = C.api(C.V5, "sec/symbol_map", query=cik)
        r = r[0] if isinstance(r, list) and r else r
        return str((r or {}).get("symbol") or "").upper() or None
    return C.cached(("cik_symbol", cik), 86400, run)


def search(query="", **_):
    """Securities matching a ticker or a company name (Yahoo Finance: U.S. and international listings, ETFs, indices, FX, futures, crypto)."""
    q = str(query or "").strip()[:60]
    if not q:
        return {"results": []}

    def run():
        query, via = q, ""
        cik = cik_of(q)
        if cik:                                    # an SEC CIK (0000320193, CIK 320193): its ticker, then that ticker's listings
            sym = C._safe(lambda: _cik_symbol(cik), missing="SEC ticker list")
            if not sym:
                return []
            query, via = sym, f" (SEC CIK {cik})"
        rows = C.api(C.V5, "yfinance/search", query=query) or []
        out, seen = [], set()
        for r in rows:
            s = str(r.get("symbol") or "").upper()
            t = str(r.get("asset_type") or "").lower()
            if not s or s in seen or t not in TYPE_NAMES:
                continue
            seen.add(s)
            out.append({"symbol": s, "name": re.sub(r"\s+", " ", str(r.get("name") or "")).strip() + (via if s == query.upper() else ""), "exchange": r.get("exchange") or "", "type": t, "type_label": TYPE_NAMES[t]})
        if via:                                    # the CIK's own ticker first
            out.sort(key=lambda x: x["symbol"] != query.upper())
        return out[:12]
    return {"results": C.cached(("search", q.lower()), 900, run)}


META_TTL = 24 * 3600


def _meta(symbols):
    """{symbol: {name, currency, type}} from Yahoo quotes, cached per symbol in memory and local/cache/yahoo_meta.json
    for 24 hours (stale copy served if Yahoo refuses). Only non-empty records are saved."""
    symbols = sorted(set(s for s in symbols if s))
    if not symbols:
        return {}
    now = time.time()
    out, need, fallback = {}, [], {}
    disk_path = C.CACHE_DIR / "yahoo_meta.json"
    disk = None

    def read_disk():
        nonlocal disk
        if disk is not None:
            return disk
        with _META_LOCK:
            try:
                raw = json.loads(disk_path.read_text(encoding="utf-8"))
                disk = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError, TypeError):
                disk = {}
        return disk

    for s in symbols:
        with C._guard:
            hit = C._cache.get(("msym", s))
        if hit and time.monotonic() - hit[0] < C.META_TTL:
            out[s] = hit[1]
            continue
        rec = read_disk().get(s)
        if isinstance(rec, dict) and rec.get("v") and rec.get("t"):
            if now - float(rec["t"]) < C.META_TTL:
                with C._guard:
                    C._cache[("msym", s)] = (C._mono(rec["t"]), rec["v"])
                out[s] = rec["v"]
                continue
            fallback[s] = (rec["v"], float(rec["t"]))
        need.append(s)

    if need:
        fresh, failed = {}, None
        for i in range(0, len(need), 25):
            try:
                rows = C.api(C.V5, "equity/price/quote", _timeout=25, symbol=",".join(need[i:i + 25]), provider="yfinance")
            except C.CompositeError as e:
                failed = e                       # symbols left without names: their saved copy (stale) or partial, below
                continue
            for r in rows or []:
                sym = r.get("symbol")
                if sym:
                    info = {"name": re.sub(r"\s+", " ", str(r.get("name") or "")).strip(), "currency": r.get("currency"),
                            "type": str(r.get("asset_type") or "").lower()}
                    fresh[sym] = info
                    out[sym] = info
        if fresh:
            ts = time.time()
            with _META_LOCK:
                try:
                    raw = json.loads(disk_path.read_text(encoding="utf-8"))
                    merged = dict(raw) if isinstance(raw, dict) else {}
                except (OSError, ValueError, TypeError):
                    merged = {}
                with C._guard:
                    for sym, info in fresh.items():
                        C._cache[("msym", sym)] = (C._mono(ts), info)
                        merged[sym] = {"t": ts, "v": info}
                C._write_json(disk_path, merged)
        for s in need:
            if s in out:
                continue
            if s in fallback:
                out[s] = fallback[s][0]
                C._note_stale_ts(fallback[s][1])          # an expired name/currency is served flagged, never as fresh
            elif failed is not None:
                C.partial_from(failed, "Yahoo Finance quotes")
    return out


# ---------------------------------------------------------------- company snapshot (DES)
def _first(path, sym, **kw):
    try:
        rows = C.api(C.V5, path, symbol=sym, **kw)
    except C.CompositeError as e:
        C.partial_from(e, f"{path.rsplit('/', 1)[-1].replace('_', ' ')} ({kw.get('provider') or 'data service'})")
        return {}
    return (rows[0] if isinstance(rows, list) and rows else rows if isinstance(rows, dict) else {}) or {}


FDIC_WAIT = 15


def _fdic_rows(sym):
    """(rows or None, missing): the lead bank's FDIC ratios, waited for FDIC_WAIT seconds; missing = not answered in time
    (the snapshot is then not kept 15 minutes, so the next load can carry them)."""
    import banks as _banks                          # imported late: banks.py imports composite
    ex = C.cf.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_banks.snapshot_rows, sym)
    ex.shutdown(wait=False)
    try:
        return fut.result(timeout=max(0.0, C.time_left(FDIC_WAIT))), False
    except C.cf.TimeoutError:
        return None, True
    except Exception:                               # noqa: BLE001  (not in the FFIEC directory, FDIC down: no bank rows)
        return None, False


def snapshot(symbol="AAPL", **_):
    """One-screen company description (Bloomberg DES): what it does, key valuation, profitability, street view, next earnings."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]

    def run():
        jobs = {"profile": ("equity/profile", {"provider": "yfinance"}), "metrics": ("equity/fundamental/metrics", {"provider": "yfinance"}),
                "quote": ("equity/price/quote", {"provider": "yfinance"}), "street": ("equity/estimates/consensus", {"provider": "yfinance"}),
                "cal": ("yfinance/company_calendar", {})}
        def parts():
            with C.cf.ThreadPoolExecutor(max_workers=5) as ex:
                return dict(zip(jobs, ex.map(lambda k: C._first(jobs[k][0], sym, **jobs[k][1]), jobs)))
        got = C._collect(parts)
        failed = got.missing if isinstance(got, C.Partial) else []
        got = got.value if isinstance(got, C.Partial) else got
        C.note_partial(failed)
        p, m, q, st, cal = (got[k] for k in ("profile", "metrics", "quote", "street", "cal"))
        if not p and not q and failed:                 # Yahoo did not answer: not the same as "nothing for this ticker"
            raise C.CompositeError("Yahoo Finance did not answer for the company profile and quote just now (it may be throttling). "
                                   "Press reload in a minute.", kind="upstream")
        if not p and not q:
            raise C.CompositeError(f"Yahoo Finance has nothing for '{sym}'. Check the ticker: non-U.S. listings need their exchange suffix (7203.T, SHEL.L, RHM.DE). Type a company name in the search box at the top.", kind="not_covered")
        ccy = q.get("currency") or p.get("currency") or ""
        last, prev = q.get("last_price"), q.get("prev_close")
        last_n, prev_n = C.num(last), C.num(prev)
        ok_last = last_n is not None
        ok_prev = prev_n is not None and prev_n != 0
        chg = f" ({(last_n / prev_n - 1) * 100:+.2f}%)" if ok_last and ok_prev else ""
        head = f"### {p.get('name') or q.get('name') or sym}  `{sym}`"
        bits = [b for b in (q.get("exchange") or p.get("stock_exchange"), ccy, " › ".join(x for x in (p.get("sector"), p.get("industry_category")) if x),
                            p.get("hq_country"), f"{p['employees']:,} employees" if isinstance(p.get("employees"), int) else None) if b]
        url = p.get("company_url")
        if url:
            host = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
            bits.append(f"[{host}]({url})")
        desc = str(p.get("long_description") or "").strip()
        if len(desc) > 560:
            cut = desc[:560].rfind(". ")
            desc = desc[:cut + 1] if cut > 200 else desc[:560] + "…"
        dy = C.num(m.get("dividend_yield"))
        try:                                               # EV rebuilt in the trading currency (see _ev_parts)
            e_ = C._ev_parts(m, ccy, C._usd_rates({ccy, m.get("currency")} - {None}))
        except C.CompositeError:
            e_ = C._ev_parts(dict(m, currency=None), ccy, {})
        m = dict(m, enterprise_value=e_["ev_q"], enterprise_to_ebitda=e_["ev_ebitda"], enterprise_to_revenue=e_["ev_sales"], price_to_book=e_["pb"])
        bank = C.is_bank(p.get("industry_category"))
        lo, hi = q.get("year_low"), q.get("year_high")
        e_date, e_end = C._date_span(cal.get("earnings_date"))
        past = bool(e_date) and (e_end or e_date) < dt.date.today().isoformat()
        e_txt = (e_date + (f" – {e_end} (expected window, date not confirmed)" if e_end else "")
                 + (" (last reported; next not yet announced)" if past else "")) if e_date else "–"
        e_for = " for that report" if past else ""
        tgt, cur = st.get("target_consensus"), st.get("current_price") or last
        t_ccy = str(st.get("currency") or "")      # audit C9: Yahoo states the target's currency (ADRs: USD; SHEL.L: GBp); no upside across currencies
        same = bool(t_ccy) and t_ccy == ccy
        tgt_n, cur_n = C.num(tgt), C.num(cur)
        ok_tgt = tgt_n is not None
        ok_cur = cur_n is not None and cur_n != 0
        upside = (f" ({(tgt_n / cur_n - 1) * 100:+.0f}%)" if ok_tgt and ok_cur else "") if same else \
            "" if tgt is None else f" (target in {t_ccy}, price in {ccy}: no upside computed)" if t_ccy else " (target currency not stated: no upside computed)"
        rec = str(st.get("recommendation") or "").replace("_", " ")
        rows = [
            ("Last", f"{C._num(last)} {ccy}{chg}", "Gross margin", C._pc(m.get("gross_margin"))),
            ("52-week range (intraday low – high)", f"{C._num(lo)} – {C._num(hi)}", "Operating margin", C._pc(m.get("operating_margin"))),   # audit C9: Yahoo quote extremes are intraday (AAPL 360.83 high vs 356.27 best close); boards use closes
            ("Market cap", C._big(m.get("market_cap") or p.get("market_cap"), MINOR_UNITS.get(ccy, (ccy,))[0]), "Net margin", C._pc(m.get("profit_margin"))),
            ("Enterprise value", C._big(m.get("enterprise_value"), MINOR_UNITS.get(ccy, (ccy,))[0]), "Revenue growth (y/y)", C._pc(m.get("revenue_growth"), True)),
            ("P/E (trailing / forward)", f"{C._num(m.get('pe_ratio'), '{:,.1f}')} / {C._num(m.get('forward_pe'), '{:,.1f}')}", "EPS growth (y/y)", C._pc(m.get("earnings_growth"), True)),
            ("EV / EBITDA", C._num(m.get("enterprise_to_ebitda"), "{:,.1f}", "x"), "Return on equity", C._pc(m.get("return_on_equity"))),
            ("EV / Sales", C._num(m.get("enterprise_to_revenue"), "{:,.1f}", "x"), "Debt / equity", C._num(m.get("debt_to_equity"), "{:,.0f}", "%")),
            ("Price / book", C._num(m.get("price_to_book"), "{:,.1f}", "x"), "Beta", C._num(m.get("beta") or p.get("beta"))),
            ("Dividend yield", C._num(dy, "{:,.2f}", "%") if dy is not None else "–", "1-year price return", C._pc(m.get("price_return_1y"), True)),
        ]
        fdic, fdic_missing = None, False
        if bank:                                           # a bank: EV, EBITDA, gross margin and debt/equity carry no meaning
            na = {"Gross margin", "Enterprise value", "EV / EBITDA", "EV / Sales", "Debt / equity"}
            rows = [(a, C.BANK_NA if a in na else b, c, C.BANK_NA if c in na else d) for a, b, c, d in rows]
            if str(p.get("hq_country") or "") == "United States" and "." not in sym:   # U.S. bank holding companies only (Stage 3 8a-ext)
                fdic, fdic_missing = _fdic_rows(sym)
        md = [head, " · ".join(bits), "", desc, "",
              "| Price & valuation | | Profitability & growth | |", "|---|---:|---|---:|"]
        md += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
        if fdic:
            md += ["", f"| {fdic['title']} | |", "|---|---:|"] + [f"| {a} | {b} |" for a, b in fdic["rows"]]
            md += [f"| Bank | {fdic['bank']} (FDIC cert {fdic['cert']}) |"]
        md += ["", "| Street & calendar | |", "|---|---|",
               f"| Next earnings | {e_txt} |",
               f"| EPS estimate{e_for} (avg, low – high) | {C._num(cal.get('earnings_average'))} ({C._num(cal.get('earnings_low'))} – {C._num(cal.get('earnings_high'))}) |" if cal.get("earnings_average") is not None else "| EPS estimate | – |",
               f"| Revenue estimate{e_for} (avg) | {C._big(cal.get('revenue_average'))} |" if cal.get("revenue_average") is not None else "| Revenue estimate | – |",
               f"| Analysts | {st.get('number_of_analysts') or '–'} · consensus **{rec or '–'}** ({C._num(st.get('recommendation_mean'), '{:.1f}')} on 1 = strong buy … 5 = sell) |",
               f"| Price target (mean, low – high) | {C._num(tgt)}{(' ' + t_ccy) if t_ccy and tgt is not None else ''}{upside} ({C._num(st.get('target_low'))} – {C._num(st.get('target_high'))}) |",
               f"| Ex-dividend date | {str(cal.get('ex_dividend_date') or '–')[:10]} |"]
        out = {"content": "\n".join(md)}
        stamp = C.newest_date([q.get("last_timestamp"), q.get("timestamp"), q.get("date")])          # a quote's own time, when the feed sends one
        if stamp:
            out["as_of"] = stamp
        else:
            out["as_of_na"] = "Yahoo Finance dates neither its quote nor its key statistics: these are its current figures (asked again every 15 minutes)."
        gone = [n for n, v in (("Yahoo Finance profile", p), ("Yahoo Finance key metrics", m), ("Yahoo Finance quote", q)) if not v]
        if gone or fdic_missing:            # Yahoo dropped a part (throttling): not kept for 15 minutes (P0-6)
            out["_partial"] = gone + (["FDIC call report"] if fdic_missing else [])
        return out
    out = C.cached_complete(("snapshot", sym), 900, run)
    # the Estimates tab's three views share one Yahoo burst (_est_raw, 7 requests): start it once the snapshot is in,
    # in the background, so a switch to Estimates finds it warm instead of queueing behind the Overview's own requests
    if PREFETCH_ESTIMATES:
        now = time.time()
        should_prefetch = False
        with C._guard:
            last_start = _EST_STARTED.get(sym)
            if last_start is None or now - last_start > 600:
                _EST_STARTED[sym] = now
                _EST_STARTED.move_to_end(sym)
                while len(_EST_STARTED) > 512:
                    _EST_STARTED.popitem(last=False)
                should_prefetch = True
        if should_prefetch:
            threading.Thread(target=C._safe, args=(C._est_raw, sym), daemon=True).start()
    return {k: v for k, v in out.items() if k != "_partial"}


_META_LOCK = threading.Lock()
_EST_STARTED: OrderedDict = OrderedDict()
PREFETCH_ESTIMATES = True          # offline tests turn it off (no background Yahoo requests)
