"""Corporate bond lookup from FINRA TRACE (HANDOFF P3-1): a company's bonds with the last reported sale.

Data: `fixedincome/corporate/bond_prices` (provider finra), filtered server side by issuer name or CUSIP. FINRA facts
(probed, HANDOFF P3-1): issuer names are abbreviated ("FORD MTR CO DEL") and matched by substring; an empty answer is
HTTP 204; a last-sale yield of 999999.99 is a placeholder (shown blank); medium-term-note programmes appear inside the
issuer name. A company (ticker or name) is resolved to FINRA's name by trying its SEC legal name in FINRA's
abbreviations, then shorter forms; results are kept only where the tried words appear as whole words.
Conventions: coupon and yield in percent; price per 100 of par; the yield is FINRA's last-sale yield (yield to worst on
callable bonds); dates are TRACE dates. Last-sale data, not live quotes.
"""

import datetime as dt
import re

import composite as C

STALE_TRADE_DAYS = 45
YIELD_PLACEHOLDER = 999999
FINRA_URL = "https://www.finra.org/finra-data/fixed-income"
SOURCE_FINRA = C.src("FINRA", "TRACE corporate bonds: last reported sale (price, yield, date), ratings", FINRA_URL)
SOURCE_SEC = C.src("SEC", "company name for the issuer lookup (company search)", "https://www.sec.gov/edgar/search/")
DELAYED = "TRACE last-sale data, not live quotes"
# FINRA's abbreviations of common words in issuer names (as seen in TRACE: FORD MTR CO, BANK AMER CORP)
ABBREV = {"MOTOR": "MTR", "MOTORS": "MTRS", "AMERICA": "AMER", "AMERICAN": "AMERN", "CREDIT": "CR", "CORPORATION": "CORP",
          "COMPANY": "CO", "INTERNATIONAL": "INTL", "FINANCIAL": "FINL", "HOLDINGS": "HLDGS", "HOLDING": "HLDG",
          "INDUSTRIES": "INDS", "MANAGEMENT": "MGMT", "SERVICES": "SVCS", "ELECTRIC": "ELEC", "RESOURCES": "RES",
          "PETROLEUM": "PETE", "PHARMACEUTICALS": "PHARMACEUTICALS", "COMMUNICATIONS": "COMMUNICATIONS", "LIMITED": "LTD",
          "INCORPORATED": "INC", "GROUP": "GROUP", "NATIONAL": "NATL", "SYSTEMS": "SYS", "TECHNOLOGIES": "TECHNOLOGIES"}
SUFFIX = {"INC", "CORP", "CO", "LTD", "PLC", "LLC", "LP", "SA", "NV", "AG", "SE", "DEL", "THE"}
STOP = {"OF", "THE", "AND", "&"}
GENERIC = {"BANK", "FIRST", "AMERICAN", "AMERN", "GENERAL", "UNITED", "NATIONAL", "NATL", "INTERNATIONAL", "INTL", "GLOBAL",
           "CAPITAL", "ENERGY", "FINANCIAL", "FINL", "HEALTH", "TRUST", "NEW", "WESTERN", "SOUTHERN", "NORTHERN", "EASTERN"}
_CUSIP_VAL = {**{str(i): i for i in range(10)}, **{chr(65 + i): 10 + i for i in range(26)}, "*": 36, "@": 37, "#": 38}


def cusip_ok(s):
    """True for a 9-character CUSIP whose check digit is right (modulus 10, double-add-double)."""
    s = str(s or "").strip().upper()
    if len(s) != 9 or not s[8].isdigit() or any(ch not in _CUSIP_VAL for ch in s[:8]):
        return False
    total = 0
    for i, ch in enumerate(s[:8]):
        v = _CUSIP_VAL[ch] * (2 if i % 2 else 1)
        total += v // 10 + v % 10
    return (10 - total % 10) % 10 == int(s[8])


def clean(name):
    """Upper case, state tags such as /DE/ and punctuation removed: 'Bank of America Corp /DE/' -> 'BANK OF AMERICA CORP'."""
    s = re.sub(r"/[A-Z]{2,3}/", " ", str(name or "").upper())
    s = re.sub(r"[^A-Z0-9& ]", " ", s)
    return " ".join(s.split())


def candidates(name):
    """Issuer-name substrings to try, most specific first: FINRA abbreviations of the full name, the name as given, the
    name without legal suffixes, the first two words, and the first word unless it is generic ('BANK')."""
    words = clean(name).split()
    if not words:
        return []
    abbr = [ABBREV.get(w, w) for w in words if w not in STOP]
    core = [w for w in abbr if w not in SUFFIX] or abbr
    out = [" ".join(abbr), " ".join(words), " ".join(core), " ".join(core[:2])]
    if len(core[0]) >= 3 and core[0] not in GENERIC:
        out.append(core[0])
    return [c for c in dict.fromkeys(out) if c]


def whole_words(issuer, cand):
    """Every word of the tried name appears as a whole word in the issuer name (FORD matches FORD MTR CO DEL, not FORDHAM)."""
    iss = clean(issuer)
    return all(re.search(r"(?<![A-Z0-9&])" + re.escape(w) + r"(?![A-Z0-9&])", iss) for w in cand.split())


def _prices(**params):
    key = ("finra_bonds",) + tuple(sorted(params.items()))
    return C.cached(key, 900, lambda: C.api(C.V5, "fixedincome/corporate/bond_prices", provider="finra", **params) or [])


def _sec_name(symbol):
    """SEC company name for an exact ticker match, else None."""
    sym = symbol.upper()
    rows = C.cached(("sec_search", sym), 86400, lambda: C.api(C.V5, "equity/search", provider="sec", query=sym) or [])
    return next((r.get("name") for r in rows if str(r.get("symbol") or "").upper() == sym), None)


def resolve(company="", issuer="", cusip=""):
    """(rows, how) for the lookup: a CUSIP, an exact FINRA issuer name, or a company resolved through its SEC name."""
    company = str(company or "").strip()
    if cusip or cusip_ok(company):
        cu = (cusip or company).strip().upper()
        rows = _prices(cusip=cu)
        if not rows:
            raise C.CompositeError(f"No TRACE bond with CUSIP {cu} (an equity CUSIP has no bond prices).")
        return rows, f"CUSIP {cu}"
    if issuer:
        iss = clean(issuer)
        return [r for r in _prices(issuer_name=iss) if whole_words(r.get("issuer_name"), iss)], f"FINRA issuer name containing '{iss}'"
    if not company:
        raise C.CompositeError("Type a ticker, a company name, a FINRA issuer name or a CUSIP.")
    name = _sec_name(company) if re.fullmatch(r"[A-Za-z][A-Za-z0-9.\-]{0,6}", company) else None
    for cand in candidates(name or company):
        rows = [r for r in _prices(issuer_name=cand) if whole_words(r.get("issuer_name"), cand)]
        if rows:
            return rows, f"{company.upper()}" + (f" ({name})" if name else "") + f" -> FINRA issuer names containing '{cand}'"
    raise C.CompositeError(f"No TRACE bonds found for {company}" + (f" ({name})" if name else "") + ". Try the FINRA issuer name (for example FORD MTR CO DEL) or a CUSIP.")


def _yield(v):
    return None if not isinstance(v, (int, float)) or v >= YIELD_PLACEHOLDER else round(float(v), 3)


def _rating(r):
    m, s = r.get("moodys_rating"), r.get("sp_rating")
    return " / ".join(x or "-" for x in (m, s)) if (m or s) else None


def special(r):
    """MTN programme, convertible or perpetual: hidden by default (their yields do not sit on the issuer's curve)."""
    text = f"{r.get('issuer_name') or ''} {r.get('issue_description') or ''} {r.get('security_description') or ''}".upper()
    return bool(re.search(r"\bMTN\b|MEDIUM[- ]TERM|\bMTNS\b", text)) or bool(r.get("is_convertible")) or bool(r.get("is_perpetual")) or not r.get("maturity_date")


def select(rows, all_types=False, include_old=False, today=None):
    """(kept rows as table rows sorted by maturity, hidden counts {kind: n})."""
    today = today or dt.date.today()
    cutoff = (today - dt.timedelta(days=STALE_TRADE_DAYS)).isoformat()
    out, hidden = [], {"special": 0, "old": 0}
    for r in rows:
        if not all_types and special(r):
            hidden["special"] += 1
            continue
        last = str(r.get("last_trade_date") or "")[:10]
        if not include_old and (not last or last < cutoff):
            hidden["old"] += 1
            continue
        call = str(r.get("call_date") or "")[:10] or None
        out.append({"issuer": r.get("issuer_name"), "coupon": r.get("coupon_rate"), "maturity": str(r.get("maturity_date") or "")[:10] or None,
                    "rating": _rating(r), "price": r.get("price"), "yield": _yield(r.get("last_sale_yield")), "last_trade": last or None,
                    "callable": ("yes, from " + call if call else "yes") if r.get("is_callable") else "no", "grade": r.get("grade"),
                    "cusip": r.get("cusip"), "special": "yes" if special(r) else None})
    out.sort(key=lambda x: (x["maturity"] or "9999", x["coupon"] or 0))
    return out, hidden


def _flag(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "show")


def _hidden_text(hidden):
    parts = ([f"{hidden['special']} MTN / convertible / perpetual"] if hidden["special"] else []) + \
            ([f"{hidden['old']} not traded in {STALE_TRADE_DAYS} days"] if hidden["old"] else [])
    return ("hidden: " + ", ".join(parts)) if parts else ""


def lookup(company="AAPL", issuer="", cusip="", all_types="no", include_old="no", today=None, **_):
    """Table: one row per bond with the last reported sale."""
    rows, how = resolve(company, issuer, cusip)
    kept, hidden = select(rows, _flag(all_types), _flag(include_old), today)
    if not kept:
        raise C.CompositeError(f"{how}: {len(rows)} bonds, none shown ({_hidden_text(hidden)}). Use the toggles to show them.")
    return {"results": kept, "sources": [SOURCE_FINRA, SOURCE_SEC], "delayed": DELAYED}


def curve(company="AAPL", issuer="", cusip="", all_types="no", include_old="no", today=None, **_):
    """Chart: last-sale yield against maturity, one colour per issuer name."""
    rows, how = resolve(company, issuer, cusip)
    kept, hidden = select(rows, _flag(all_types), _flag(include_old), today)
    pts = [r for r in kept if r["yield"] is not None and r["maturity"]]
    if not pts:
        raise C.CompositeError(f"{how}: no bond with a last-sale yield to plot ({_hidden_text(hidden) or 'yields blank'}).")
    by = {}
    for r in pts:
        by.setdefault(r["issuer"], []).append(r)
    traces = []
    for i, (iss, rs) in enumerate(sorted(by.items(), key=lambda kv: -len(kv[1]))[:8]):
        traces.append({"type": "scatter", "mode": "markers", "name": f"{iss} ({len(rs)})", "x": [r["maturity"] for r in rs], "y": [r["yield"] for r in rs],
                       "marker": {"size": 8, "color": C.PALETTE[i % len(C.PALETTE)]},
                       "text": [f"{r['coupon']}% {r['maturity']} · price {r['price']} · last trade {r['last_trade']} · {r['cusip']}" for r in rs],
                       "hovertemplate": "%{y:.2f}%<br>%{text}<extra></extra>"})
    lay = C._layout(len(traces) > 1)
    lay["hovermode"] = "closest"
    lay["yaxis"]["ticksuffix"] = "%"
    lay["yaxis"]["title"] = {"text": "last-sale yield", "font": {"size": 10}}      # yield to worst on callables: the card's description and table say so
    note = "x: maturity" + (" · " + _hidden_text(hidden) if _hidden_text(hidden) else "")
    lay["annotations"] = [C._note(note)]
    return {"data": traces, "layout": lay, "sources": [SOURCE_FINRA, SOURCE_SEC], "delayed": DELAYED}
