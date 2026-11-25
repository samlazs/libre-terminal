"""ETF holdings overlap from SEC N-PORT (Stage 3 13a).

/x/etf_overlap: the latest N-PORT portfolio of each fund (backend `etf/nport_disclosure`, provider sec). Holdings are
matched by CUSIP, then ISIN, then normalised name. Weight overlap = sum over common holdings of the smaller of the two
weights (the share of each fund's assets held in common); count overlap = common holdings against each fund's count.
N-PORT is quarterly and made public about 60 days after the quarter: the period end is stated, never today's portfolio.
"""

import re

import composite as C

SOURCE = C.src("SEC", "Form N-PORT portfolio holdings (quarterly, public about 60 days after the quarter end)", "https://www.sec.gov/edgar/search/")


def _ident(v, n):
    v = str(v or "").strip().upper()
    return v if len(v) == n and v.strip("0") and v not in ("N/A", "NONE") else None


def _name(v):
    s = re.sub(r"[^A-Z0-9 ]", " ", str(v or "").upper())
    s = re.sub(r"\b(INC|CORP|CORPORATION|CO|LTD|PLC|CLASS [A-C]|CL [A-C]|THE|SA|NV|AG)\b", " ", s)
    return " ".join(s.split()) or None


def holdings(sym):
    """(rows [{name, cusip, isin, weight %}], metadata) for one fund, kept 12 hours on disk."""
    def run():
        j = C.api_full(C.V5, "etf/nport_disclosure", provider="sec", symbol=sym, _timeout=90) or {}
        rows = j.get("results") or []
        meta = (j.get("extra") or {}).get("results_metadata") or {}
        out = [{"name": r.get("name") or r.get("title"), "cusip": _ident(r.get("cusip"), 9), "isin": _ident(r.get("isin"), 12),
                "weight": float(r["weight"]) * 100} for r in rows if isinstance(r.get("weight"), (int, float))]
        if not out:
            raise C.CompositeError(f"SEC N-PORT has no holdings for {sym} (only registered funds file it; a UIT or a non-U.S. fund does not).")
        return {"rows": out, "meta": {k: meta.get(k) for k in ("fund_name", "period_ending")}}
    return C.cached_disk(C.disk_name("nport_", sym), 12 * 3600, run)


def overlap(a_rows, b_rows):
    """(common [(name, weight a %, weight b %)], weight overlap %, matched count)."""
    keys_b = {}
    for i, r in enumerate(b_rows):
        for k in (("c", r["cusip"]), ("i", r["isin"]), ("n", _name(r["name"]))):
            if k[1]:
                keys_b.setdefault(k, i)
    used, common = set(), {}
    for r in a_rows:
        j = next((keys_b[k] for k in (("c", r["cusip"]), ("i", r["isin"]), ("n", _name(r["name"]))) if k[1] and k in keys_b and keys_b[k] not in used), None)
        if j is None:
            continue
        used.add(j)
        common[j] = (r["name"], r["weight"], b_rows[j]["weight"])
    rows = sorted(common.values(), key=lambda x: -min(x[1], x[2]))
    return rows, sum(min(a, b) for _, a, b in rows), len(rows)


def etf_overlap(symbol="SPY", symbol2="QQQ", **_):
    a, b = (C._syms(symbol, 1) or ["SPY"])[0], (C._syms(symbol2, 1) or ["QQQ"])[0]
    if a == b:
        raise C.CompositeError("Pick two different funds.")
    with C.cf.ThreadPoolExecutor(max_workers=2) as ex:
        ha, hb = ex.map(holdings, (a, b))
    rows, w, n = overlap(ha["rows"], hb["rows"])
    na, nb = len(ha["rows"]), len(hb["rows"])
    pa, pb = ha["meta"].get("period_ending"), hb["meta"].get("period_ending")
    out = [{"name": nm, "weight_a": round(x, 3), "weight_b": round(y, 3), "overlap": round(min(x, y), 3)} for nm, x, y in rows]
    asof = f"as of {pa}" if pa == pb else f"{a} as of {pa}, {b} as of {pb}"
    conv = (f"{a} vs {b}: weight overlap {w:.1f}% · {n} common holdings ({n / na * 100:.0f}% of {a}'s {na}, {n / nb * 100:.0f}% of {b}'s {nb}) · "
            f"SEC N-PORT {asof} (quarterly, public about 60 days after the quarter; filing date not in the feed)")
    res = {"results": out, "sources": [SOURCE], "convention": conv, "columns": {"weight_a": f"{a} weight", "weight_b": f"{b} weight"}}
    as_of = C.newest_date([pa, pb])                  # the newer of the two funds' portfolio dates
    if as_of:
        res["as_of"] = as_of
    else:
        res["as_of_na"] = f"SEC N-PORT gave no portfolio date for {a} or {b}."
    return res


def static_sources(name, p):
    return [SOURCE]
