"""Comparable companies selection, EV components, and peer table view."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import composite as C

# ---------------------------------------------------------------- comparable companies
def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")


def _norm_name(n):
    n = re.sub(r"[^a-z0-9 ]+", " ", str(n or "").lower())
    n = re.sub(r"\b(the|inc|incorporated|corp|corporation|co|company|plc|ag|se|sa|nv|n v|ltd|limited|holdings?|group|class [abc]|ord|adr)\b", " ", n)
    return " ".join(n.split())[:24]


def _profiles(symbols):
    """{symbol: {name, sector, industry, market_cap, currency, type}} from Yahoo profiles, kept 7 days."""
    symbols = sorted(set(symbols))
    if not symbols:
        return {}

    def run():
        out = {}
        chunks = [symbols[i:i + 30] for i in range(0, len(symbols), 30)]

        def one(ch):
            try:
                return C.api(C.V5, "equity/profile", symbol=",".join(ch), provider="yfinance") or []
            except C.CompositeError:
                return []
        with C.cf.ThreadPoolExecutor(max_workers=3) as ex:
            for rows in ex.map(one, chunks):
                for r in rows:
                    if r.get("symbol"):
                        out[r["symbol"]] = {"name": r.get("name") or "", "sector": r.get("sector") or "", "industry": r.get("industry_category") or "",
                                            "market_cap": r.get("market_cap"), "currency": r.get("currency"), "type": str(r.get("issue_type") or "").upper()}
        if not out:
            raise C.CompositeError("Yahoo Finance returned no company profiles.")
        return out
    return C.cached_disk("prof_" + hashlib.sha1(",".join(symbols).encode()).hexdigest()[:16], 7 * 86400, run)


def _yahoo_recs(sym):
    """Yahoo Finance's 'people also watch' list for a symbol (public, no key)."""
    def run():
        j = json.loads(C._download(f"https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{urllib.parse.quote(sym)}", timeout=20))
        res = ((j.get("finance") or {}).get("result") or [{}])[0] or {}
        return [r["symbol"] for r in res.get("recommendedSymbols") or [] if r.get("symbol")]
    return C.cached_disk("recs_" + re.sub(r"[^A-Za-z0-9]+", "_", sym), 7 * 86400, run)


def _peers(sym):
    """Peers for a company: same industry (Yahoo classification), from the stocks investors also watch and the
    largest companies of its sector, ranked by closeness in size. Kept 7 days."""
    def run():
        subj = (C._profiles([sym]) or {}).get(sym)
        if not subj:
            raise C.CompositeError(f"Yahoo Finance has no company profile for {sym}, so peers cannot be found. Press Peers on the card to type them.")
        r1 = C._safe(C._yahoo_recs, sym) or []
        with C.cf.ThreadPoolExecutor(max_workers=5) as ex:
            r2 = [s for lst in ex.map(lambda s: C._safe(C._yahoo_recs, s) or [], r1) for s in lst]
        pool = []
        if subj["sector"]:
            sk = C._slug(subj["sector"])
            for path, extra in (("yfinance/sectors/top_companies", {}), ("yfinance/industry/top_performing", {"industry": C._slug(subj["industry"])}),
                                ("yfinance/industry/top_growth", {"industry": C._slug(subj["industry"])})):
                rows = C._safe(lambda: C.api(C.V5, path, sector=sk, provider="yfinance", **extra)) or []
                pool += [r.get("symbol") for r in rows if r.get("symbol")]
        cand = [s for s in dict.fromkeys(r1 + r2 + pool) if s and s != sym]
        prof = C._profiles(cand)
        fx = C._usd_rates({p.get("currency") for p in prof.values()} | {subj.get("currency")})

        def usd_cap(p):
            mc, rate = p.get("market_cap"), fx.get(p.get("currency"))
            # Yahoo reports market cap in the major unit even when prices are quoted in pence
            if mc and rate and p.get("currency") in C.MINOR_UNITS:
                rate = rate / C.MINOR_UNITS[p["currency"]][1]
            return mc * rate if mc and rate else None
        base = usd_cap(subj)

        def dist(s):
            c = usd_cap(prof[s])
            return abs(math.log(c / base)) if c and base and c > 0 and base > 0 else 9.0
        own = C._norm_name(subj["name"])
        seen_names = {own}

        def ok(s):
            p = prof.get(s)
            return p and p["type"] in ("", "EQUITY") and C._norm_name(p["name"]) not in seen_names
        rank = {s: (0 if s in r1 else 1 if s in r2 else 2) for s in cand}
        band = math.log(50)                    # within 50x of the company's size either way
        same_ind = sorted([s for s in cand if ok(s) and subj["industry"] and prof[s]["industry"] == subj["industry"] and dist(s) <= band], key=lambda s: (rank[s], dist(s)))
        out = []

        def take(s):
            if ok(s) and s not in out:
                seen_names.add(C._norm_name(prof[s]["name"]))
                out.append(s)
        for s in same_ind:
            if len(out) >= 10:
                break
            take(s)
        if len(out) < 5:                       # thin industry: same sector of similar size, then what investors also watch
            for s in sorted([s for s in cand if prof.get(s) and prof[s]["sector"] == subj["sector"] and dist(s) <= band], key=lambda s: (rank[s], dist(s))):
                if len(out) >= 8:
                    break
                take(s)
            for s in r1:
                if len(out) >= 8:
                    break
                if prof.get(s) and dist(s) <= band:
                    take(s)
        if not out:
            raise C.CompositeError(f"No peers found for {sym}. Press Peers on the card to type them.")
        return out
    return C.cached_disk("peers_" + re.sub(r"[^A-Za-z0-9]+", "_", sym), 7 * 86400, run)


COMP_FIELDS = ["mcap_usd", "ev_usd", "pe", "fwd_pe", "ev_ebitda", "ev_sales", "pb", "gross_margin", "ebitda_margin", "op_margin", "net_margin",
               "rev_growth", "eps_growth", "roe", "nd_ebitda", "div_yield", "beta", "ret_1y"]

_METRICS = {}                                        # symbol -> (time fetched, Yahoo key metrics); see comps()


def _ev_parts(m, ccy, fx):
    """Enterprise-value figures for one company, currency-consistent.

    Yahoo adds the market cap (trading currency) to net debt (reporting currency, metrics["currency"]) without converting,
    so for a company that reports in another currency (ADRs, London listings reporting in dollars) its enterprise value and
    every EV multiple are wrong. Net debt, EBITDA and sales are recovered in the reporting currency and recombined.
    fx: USD per unit, keyed by currency code as quoted (minor units such as GBp included). Amounts returned in USD billions.
    """
    def pos(v):
        return v if isinstance(v, (int, float)) and v > 0 else None
    q = C.MINOR_UNITS.get(ccy, (ccy,))[0]
    rq = fx.get(ccy)
    if rq and ccy in C.MINOR_UNITS:
        rq = rq / C.MINOR_UNITS[ccy][1]               # market cap and EV are in the major unit (GBP, not pence)
    f = m.get("currency")
    f = C.MINOR_UNITS.get(f, (f,))[0] if f else None
    mc, ev = pos(m.get("market_cap")), m.get("enterprise_value")
    ev = ev if isinstance(ev, (int, float)) else None
    eve, evs, pb = pos(m.get("enterprise_to_ebitda")), pos(m.get("enterprise_to_revenue")), pos(m.get("price_to_book"))
    out = {"mcap_usd": mc * rq / 1e9 if mc and rq else None, "ev_usd": None, "ev_ebitda": None, "ev_sales": None, "pb": None,
           "nd_ebitda": None, "note": None, "ev_q": None}
    if mc and ev is not None and eve:
        nd = (ev - mc) / (ev / eve) if ev else None   # net debt and EBITDA are both in the reporting currency: ratio is right as is
        out["nd_ebitda"] = nd if nd is not None and abs(nd) < 30 else None
    if not f or f == q:                              # one currency (or unknown): Yahoo's figures stand, with the old sanity guard
        if mc and ev and not 0.2 < ev / mc < 5:
            out["nd_ebitda"] = None
            out["note"] = "EV and P/B left out: Yahoo reports them in the home currency"
            return out
        out.update(ev_usd=ev * rq / 1e9 if ev and rq else None, ev_ebitda=eve, ev_sales=evs, pb=pb, ev_q=ev)
        return out
    rf = fx.get(f)
    if not (mc and ev is not None and rq and rf):
        out["note"] = f"EV and P/B left out: reported in {f}, no exchange rate"
        return out
    k = rq / rf                                      # reporting-currency units per trading-currency unit
    whole = False                                    # Yahoo sometimes gives the whole EV in the reporting currency (TSM: TWD)
    if ev > 0 and not 0.5 <= k <= 2:
        whole = abs(math.log(ev / mc / k)) < abs(math.log(ev / mc))
    blank = dict(nd_ebitda=None, note=f"EV and P/B left out: Yahoo's enterprise value for this listing is not usable (reported in {f})")
    # Neither reading is believable (share-count mismatches on some ADRs, e.g. TSM, ASML): show nothing rather than a guess.
    if (whole and not 0.5 <= ev / mc / k <= 3) or (not whole and not 0.2 < (mc * rq + (ev - mc) * rf) / (mc * rq) < 5):
        out.update(blank)
        return out
    if whole:
        nd = (ev - mc * k) / (ev / eve) if eve else None
        out.update(ev_usd=ev * rf / 1e9, ev_q=ev / k, ev_ebitda=eve, ev_sales=evs,
                   nd_ebitda=nd if nd is not None and abs(nd) < 30 else None,
                   note=f"P/B left out: book value is reported in {f}")
        return out
    ev_usd = mc * rq + (ev - mc) * rf                # market cap in dollars + net debt converted from the reporting currency
    ev_f = ev_usd / rf
    out.update(ev_usd=ev_usd / 1e9, ev_q=ev_usd / rq,
               ev_ebitda=pos(ev_f / (ev / eve)) if eve and ev else None,
               ev_sales=pos(ev_f / (ev / evs)) if evs and ev else None,
               note=f"P/B left out: book value is reported in {f}")
    return out


def comps(symbol="AAPL", peers=None, **_):
    """Comparable-company table for one ticker: valuation, margins, growth, returns, with the peer median."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]
    custom = [p for p in C._syms(peers, 25) if p != sym]
    plist = custom or C._peers(sym)
    syms = [sym] + plist

    # Kept per symbol, and only for symbols Yahoo actually answered: a throttled, partial answer used to be cached whole
    # for an hour, leaving peers on "no data". Missing symbols get one retry now and another on the next view.
    now = time.time()
    met = {s: C._METRICS[s][1] for s in syms if s in C._METRICS and now - C._METRICS[s][0] < 3600}
    for attempt in range(2):
        need = [s for s in syms if s not in met]
        if not need:
            break
        try:
            rows = C.api(C.V5, "equity/fundamental/metrics", symbol=",".join(need), provider="yfinance") or []
        except C.CompositeError:
            if attempt and not met:
                raise
            rows = []
        for r in rows:
            if r.get("symbol"):
                met[r["symbol"]] = r
                C._METRICS[r["symbol"]] = (now, r)
        if not attempt and len(met) < len(syms):
            time.sleep(1.5)                   # Yahoo's client sometimes drops symbols when several requests run at once
    meta = C._meta(syms)
    fx = C._usd_rates({(meta.get(s) or {}).get("currency") for s in syms} | {(met.get(s) or {}).get("currency") for s in syms} - {None})

    def pos(v):
        return v if isinstance(v, (int, float)) and v > 0 else None
    rows = []
    for s in syms:
        m, q = met.get(s), meta.get(s) or {}
        name = q.get("name") or s
        if not m:
            rows.append({"name": name, "symbol": s, "note": "no data on Yahoo Finance", "_hl": s == sym})
            continue
        ccy = q.get("currency")
        e = C._ev_parts(m, ccy, fx)                       # EV rebuilt in one currency (ADRs, dollar reporters listed abroad)
        nd = e["nd_ebitda"]
        dy = m.get("dividend_yield")
        rows.append({
            "name": name, "symbol": s, "currency": ccy,
            "mcap_usd": e["mcap_usd"], "ev_usd": e["ev_usd"],
            "pe": pos(m.get("pe_ratio")), "fwd_pe": pos(m.get("forward_pe")), "ev_ebitda": e["ev_ebitda"], "ev_sales": e["ev_sales"], "pb": e["pb"],
            "gross_margin": m.get("gross_margin"), "ebitda_margin": m.get("ebitda_margin"), "op_margin": m.get("operating_margin"), "net_margin": m.get("profit_margin"),
            "rev_growth": m.get("revenue_growth"), "eps_growth": m.get("earnings_growth"), "roe": m.get("return_on_equity"), "nd_ebitda": nd,
            "div_yield": dy / 100 if isinstance(dy, (int, float)) else None,     # Yahoo now reports the dividend yield in percent
            "beta": m.get("beta"), "ret_1y": m.get("price_return_1y"), "_hl": s == sym,
            "note": e["note"],
        })
    med = {"name": "Peer median", "symbol": "", "_pin": True}
    for f in COMP_FIELDS:
        vals = sorted(r[f] for r in rows[1:] if isinstance(r.get(f), (int, float)))
        if vals:
            k = len(vals) // 2
            med[f] = vals[k] if len(vals) % 2 else (vals[k - 1] + vals[k]) / 2
    return {"results": rows + [med], "peers": plist, "auto": not custom}
