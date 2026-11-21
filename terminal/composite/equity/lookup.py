"""Yahoo symbol search, metadata cache, and company snapshot (DES) view."""

from __future__ import annotations

import datetime as dt
import json
import re
import time

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


def search(query="", **_):
    """Securities matching a ticker or a company name (Yahoo Finance: U.S. and international listings, ETFs, indices, FX, futures, crypto)."""
    q = str(query or "").strip()[:60]
    if not q:
        return {"results": []}

    def run():
        rows = C.api(C.V5, "yfinance/search", query=q) or []
        out, seen = [], set()
        for r in rows:
            s = str(r.get("symbol") or "").upper()
            t = str(r.get("asset_type") or "").lower()
            if not s or s in seen or t not in TYPE_NAMES:
                continue
            seen.add(s)
            out.append({"symbol": s, "name": re.sub(r"\s+", " ", str(r.get("name") or "")).strip(), "exchange": r.get("exchange") or "", "type": t, "type_label": TYPE_NAMES[t]})
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
        try:
            raw = json.loads(disk_path.read_text(encoding="utf-8"))
            disk = raw if isinstance(raw, dict) else {}
        except (OSError, ValueError, TypeError):
            disk = {}
        return disk

    for s in symbols:
        with C._guard:
            hit = C._cache.get(("msym", s))
        if hit and now - hit[0] < C.META_TTL:
            out[s] = hit[1]
            continue
        rec = read_disk().get(s)
        if isinstance(rec, dict) and rec.get("v") and rec.get("t"):
            if now - float(rec["t"]) < C.META_TTL:
                with C._guard:
                    C._cache[("msym", s)] = (float(rec["t"]), rec["v"])
                out[s] = rec["v"]
                continue
            fallback[s] = rec["v"]
        need.append(s)

    if need:
        fresh = {}
        for i in range(0, len(need), 25):
            try:
                rows = C.api(C.V5, "equity/price/quote", _timeout=25, symbol=",".join(need[i:i + 25]), provider="yfinance")
            except C.CompositeError:
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
            merged = dict(read_disk())
            with C._guard:
                for sym, info in fresh.items():
                    C._cache[("msym", sym)] = (ts, info)
                    merged[sym] = {"t": ts, "v": info}
            C._write_json(disk_path, merged)
        for s in need:
            if s not in out and s in fallback:
                out[s] = fallback[s]
    return out


# ---------------------------------------------------------------- company snapshot (DES)
def _first(path, sym, **kw):
    try:
        rows = C.api(C.V5, path, symbol=sym, **kw)
    except C.CompositeError:
        return {}
    return (rows[0] if isinstance(rows, list) and rows else rows if isinstance(rows, dict) else {}) or {}


def snapshot(symbol="AAPL", **_):
    """One-screen company description (Bloomberg DES): what it does, key valuation, profitability, street view, next earnings."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]

    def run():
        jobs = {"profile": ("equity/profile", {"provider": "yfinance"}), "metrics": ("equity/fundamental/metrics", {"provider": "yfinance"}),
                "quote": ("equity/price/quote", {"provider": "yfinance"}), "street": ("equity/estimates/consensus", {"provider": "yfinance"}),
                "cal": ("yfinance/company_calendar", {})}
        with C.cf.ThreadPoolExecutor(max_workers=5) as ex:
            got = dict(zip(jobs, ex.map(lambda k: C._first(jobs[k][0], sym, **jobs[k][1]), jobs)))
        p, m, q, st, cal = (got[k] for k in ("profile", "metrics", "quote", "street", "cal"))
        if not p and not q:
            raise C.CompositeError(f"Yahoo Finance has nothing for '{sym}'. Check the ticker: non-U.S. listings need their exchange suffix (7203.T, SHEL.L, RHM.DE). Type a company name in the search box at the top.")
        ccy = q.get("currency") or p.get("currency") or ""
        last, prev = q.get("last_price"), q.get("prev_close")
        chg = f" ({(last / prev - 1) * 100:+.2f}%)" if isinstance(last, (int, float)) and prev else ""
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
        dy = m.get("dividend_yield")
        try:                                               # EV rebuilt in the trading currency (see _ev_parts)
            e_ = C._ev_parts(m, ccy, C._usd_rates({ccy, m.get("currency")} - {None}))
        except C.CompositeError:
            e_ = C._ev_parts(dict(m, currency=None), ccy, {})
        m = dict(m, enterprise_value=e_["ev_q"], enterprise_to_ebitda=e_["ev_ebitda"], enterprise_to_revenue=e_["ev_sales"], price_to_book=e_["pb"])
        lo, hi = q.get("year_low"), q.get("year_high")
        e_date, e_end = C._date_span(cal.get("earnings_date"))
        past = bool(e_date) and (e_end or e_date) < dt.date.today().isoformat()
        e_txt = (e_date + (f" – {e_end} (expected window, date not confirmed)" if e_end else "")
                 + (" (last reported; next not yet announced)" if past else "")) if e_date else "–"
        e_for = " for that report" if past else ""
        tgt, cur = st.get("target_consensus"), st.get("current_price") or last
        t_ccy = str(st.get("currency") or "")      # audit C9: Yahoo states the target's currency (ADRs: USD; SHEL.L: GBp); no upside across currencies
        same = bool(t_ccy) and t_ccy == ccy
        upside = (f" ({(tgt / cur - 1) * 100:+.0f}%)" if isinstance(tgt, (int, float)) and cur else "") if same else \
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
            ("Dividend yield", C._num(dy, "{:,.2f}", "%") if isinstance(dy, (int, float)) else "–", "1-year price return", C._pc(m.get("price_return_1y"), True)),
        ]
        md = [head, " · ".join(bits), "", desc, "",
              "| Price & valuation | | Profitability & growth | |", "|---|---:|---|---:|"]
        md += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
        md += ["", "| Street & calendar | |", "|---|---|",
               f"| Next earnings | {e_txt} |",
               f"| EPS estimate{e_for} (avg, low – high) | {C._num(cal.get('earnings_average'))} ({C._num(cal.get('earnings_low'))} – {C._num(cal.get('earnings_high'))}) |" if cal.get("earnings_average") is not None else "| EPS estimate | – |",
               f"| Revenue estimate{e_for} (avg) | {C._big(cal.get('revenue_average'))} |" if cal.get("revenue_average") is not None else "| Revenue estimate | – |",
               f"| Analysts | {st.get('number_of_analysts') or '–'} · consensus **{rec or '–'}** ({C._num(st.get('recommendation_mean'), '{:.1f}')} on 1 = strong buy … 5 = sell) |",
               f"| Price target (mean, low – high) | {C._num(tgt)}{(' ' + t_ccy) if t_ccy and tgt is not None else ''}{upside} ({C._num(st.get('target_low'))} – {C._num(st.get('target_high'))}) |",
               f"| Ex-dividend date | {str(cal.get('ex_dividend_date') or '–')[:10]} |"]
        out = {"content": "\n".join(md)}
        if not (p and m and q):
            out["_partial"] = True          # Yahoo dropped a part (throttling): not kept for 15 minutes (P0-6)
        return out
    out = C.cached_complete(("snapshot", sym), 900, run)
    return {k: v for k, v in out.items() if k != "_partial"}
