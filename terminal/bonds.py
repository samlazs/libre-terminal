"""Corporate bond lookup from FINRA TRACE (HANDOFF P3-1): a company's bonds with the last reported sale.

Data: `fixedincome/corporate/bond_prices` (provider finra), filtered server side by issuer name or CUSIP. FINRA facts
(probed, HANDOFF P3-1): issuer names are abbreviated ("FORD MTR CO DEL") and matched by substring; an empty answer is
HTTP 204; a last-sale yield of 999999.99 is a placeholder (shown blank); medium-term-note programmes appear inside the
issuer name. A company (ticker or name) is resolved to FINRA's name by trying its SEC legal name in FINRA's
abbreviations, then shorter forms; results are kept only where the tried words appear as whole words.
Conventions: coupon and yield in percent; price per 100 of par; the yield is FINRA's last-sale yield (yield to worst on
callable bonds); dates are TRACE dates. Last-sale data, not live quotes.
"""

import bisect
import datetime as dt
import re

import bond_math as BM
import composite as C

STALE_TRADE_DAYS = 45
YIELD_PLACEHOLDER = 999999
FINRA_URL = "https://www.finra.org/finra-data/fixed-income"
SOURCE_FINRA = C.src("FINRA", "TRACE corporate bonds: last reported sale (price, yield, date), ratings", FINRA_URL)
SOURCE_SEC = C.src("SEC", "company name for the issuer lookup (company search)", "https://www.sec.gov/edgar/search/")
DELAYED = "TRACE last-sale data, not live quotes"
SOURCE_PAR = C.src("FRED", "H.15 Treasury constant maturities (par curve) for the G-spread", "https://www.federalreserve.gov/releases/h15/")
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
                    "cusip": r.get("cusip"), "special": "yes" if special(r) else None,
                    "coupon_type": r.get("coupon_type"), "is_144a": "yes" if r.get("is_144a") else "no",
                    "price_chg": r.get("price_change"), "isin": BM.isin_us(r.get("cusip")),
                    "moodys_date": str(r.get("moodys_rating_date") or "")[:10] or None, "sp_date": str(r.get("sp_rating_date") or "")[:10] or None})
    out.sort(key=lambda x: (x["maturity"] or "9999", x["coupon"] or 0))
    return out, hidden


def enrich(rows, curve=None):
    """Our own yield to maturity from the last-sale price (semiannual 30/360, settled on the last trade date), modified
    duration, convexity, DV01 (per 100 par), the nearest on-the-run Treasury tenor and the G-spread against the Fed's par
    curve (H.15 constant maturities) of the last trade date (or the latest day before it), interpolated at the remaining
    maturity. Fixed-rate bonds only; callables to maturity (call ignored)."""
    days = sorted(curve or {})
    for r in rows:
        r.update({"ytm": None, "mod_duration": None, "convexity": None, "dv01": None, "g_spread": None, "benchmark": None, "risk_basis": None})
        try:
            mat, settle = dt.date.fromisoformat(r["maturity"]), dt.date.fromisoformat(r["last_trade"])
        except (TypeError, ValueError):
            continue
        left = BM.years_between(settle, mat)
        bench = BM.benchmark_tenor(left)
        r["benchmark"] = f"{bench}Y" if bench else None
        if not str(r.get("coupon_type") or "").startswith("Fixed") or not isinstance(r.get("price"), (int, float)) or not isinstance(r.get("coupon"), (int, float)):
            r["risk_basis"] = "not fixed rate: not computed" if r.get("coupon_type") else None
            continue
        y = BM.ytm(float(r["price"]), float(r["coupon"]), settle, mat)
        if y is None:
            continue
        k = BM.risk(y, float(r["coupon"]), settle, mat)
        r.update({"ytm": round(y * 100, 3), "mod_duration": round(k["mod_duration"], 2), "convexity": round(k["convexity"], 1), "dv01": round(k["dv01"], 4),
                  "risk_basis": "to maturity; call ignored" if r.get("callable", "no") != "no" else "to maturity"})
        i = bisect.bisect_right(days, r["last_trade"])
        if i and (settle - dt.date.fromisoformat(days[i - 1])).days <= 7:
            g = BM.interp(curve[days[i - 1]], left)
            if g is not None:
                r["g_spread"] = round((y * 100 - g) * 100, 1)
    return rows


def _par_curve():
    try:
        return C._src_fred_us()
    except C.CompositeError:
        return {}


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
    enrich(kept, _par_curve())
    return {"results": kept, "sources": [SOURCE_FINRA, SOURCE_SEC, SOURCE_PAR], "delayed": DELAYED}


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
                       "customdata": [r["cusip"] for r in rs],
                       "hovertemplate": "%{y:.2f}%<br>%{text}<extra></extra>"})
    lay = C._layout(len(traces) > 1)
    lay["hovermode"] = "closest"
    lay["meta"] = {"clickParam": "cusip"}                  # a click on a point loads that bond's history (widgets.js)
    lay["yaxis"]["ticksuffix"] = "%"
    lay["yaxis"]["title"] = {"text": "last-sale yield", "font": {"size": 10}}      # yield to worst on callables: the card's description and table say so
    note = "x: maturity · click a point for its history" + (" · " + _hidden_text(hidden) if _hidden_text(hidden) else "")
    lay["annotations"] = [C._note(note)]
    return {"data": traces, "layout": lay, "sources": [SOURCE_FINRA, SOURCE_SEC], "delayed": DELAYED}


# ---------------------------------------------------------------- bond pricer (Stage 3 item 4)
def _num_in(v):
    try:
        return float(str(v).replace(",", "")) if str(v or "").strip() else None
    except ValueError:
        return None


def pricer(cusip="", price="", ytm="", today=None, **_):
    """Price <-> yield for one fixed-rate bond (the one clicked in the lookup), settled today, with its risk measures and the
    price after parallel yield moves of -100, -50, +50 and +100 bp."""
    cu = str(cusip or "").strip().upper()
    if not cu:
        raise C.CompositeError("Click a bond's CUSIP in the table or the curve above (or type a CUSIP in the settings).")
    rows = _prices(cusip=cu)
    if not rows:
        raise C.CompositeError(f"No TRACE bond with CUSIP {cu}.")
    r = rows[0]
    coupon, mat = r.get("coupon_rate"), str(r.get("maturity_date") or "")[:10]
    if not str(r.get("coupon_type") or "").startswith("Fixed") or not isinstance(coupon, (int, float)) or not mat:
        raise C.CompositeError(f"{cu} is {r.get('coupon_type') or 'not a plain fixed-rate bond'}: the pricer handles fixed-rate bonds only.")
    settle, mat = today or dt.date.today(), dt.date.fromisoformat(mat)
    if mat <= settle:
        raise C.CompositeError(f"{cu} matured on {mat}.")
    y_in, p_in = _num_in(ytm), _num_in(price)
    _, acc = BM.price(0.05, coupon, settle, mat)
    if y_in is not None:
        y, how = y_in / 100, f"from the yield typed ({y_in:.3f}%)"
        clean = BM.price(y, coupon, settle, mat)[0] - acc
    else:
        clean = p_in if p_in is not None else r.get("price")
        how = "from the price typed" if p_in is not None else f"from the last TRACE sale price ({str(r.get('last_trade_date') or '')[:10]})"
        if not isinstance(clean, (int, float)):
            raise C.CompositeError(f"{cu} has no last-sale price: type a price or a yield.")
        y = BM.ytm(float(clean), coupon, settle, mat)
        if y is None:
            raise C.CompositeError(f"No yield solves price {clean} for {cu}.")
    k = BM.risk(y, coupon, settle, mat)
    left = BM.years_between(settle, mat)
    call = " · **callable: computed to maturity, call ignored**" if r.get("is_callable") else ""
    md = [f"### {r.get('issuer_name')} {coupon:g}% {mat.isoformat()}  `{cu}`",
          f"Settlement {settle.isoformat()} · {left:.2f} years to maturity · semiannual, 30/360, per 100 par · {how}{call}", "",
          "| Measure | Value |", "|---|---:|",
          f"| Clean price | {clean:,.3f} |", f"| Accrued interest | {acc:,.4f} |", f"| Dirty price | {clean + acc:,.3f} |",
          f"| Yield to maturity | {y * 100:.3f}% |", f"| Modified duration | {k['mod_duration']:.2f} |", f"| Convexity | {k['convexity']:.1f} |",
          f"| DV01 (per 100 par) | {k['dv01']:.4f} |", "", "| Yield move | Yield | Clean price | Change |", "|---:|---:|---:|---:|"]
    for bp in (-100, -50, 50, 100):
        p = BM.price(y + bp / 10000, coupon, settle, mat)[0] - acc
        md.append(f"| {bp:+d} bp | {y * 100 + bp / 100:.3f}% | {p:,.3f} | {p - clean:+,.3f} |")
    return {"content": "\n".join(md), "sources": [SOURCE_FINRA]}


# ---------------------------------------------------------------- debt maturity wall (latest 10-K)
WALL = [("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", "Year 1"), ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo", "Year 2"),
        ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree", "Year 3"), ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour", "Year 4"),
        ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive", "Year 5"), ("LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive", "After year 5")]


def _k10(rows):
    return [x for x in rows or [] if str(x.get("form") or "").startswith("10-K") and x.get("value") is not None]


def wall_points(facts):
    """{tag: [rows]} -> (period end, filed, cik, [(label, value or None)]) for the latest 10-K period of the first-year tag."""
    first = _k10(facts.get(WALL[0][0]))
    ends = sorted({str(x["period_ending"])[:10] for tag, _ in WALL for x in _k10(facts.get(tag))})
    if not ends:
        return None
    end = max(str(x["period_ending"])[:10] for x in first) if first else ends[-1]
    pts, filed, cik = [], "", None
    for tag, label in WALL:
        row = next((x for x in _k10(facts.get(tag)) if str(x["period_ending"])[:10] == end), None)
        pts.append((label, float(row["value"]) if row else None))
        if row:
            filed, cik = max(filed, str(row.get("reported_date") or "")[:10]), row.get("cik") or cik
    return end, filed, cik, pts


def _wall_sources(sym, cik=None):
    edgar = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik or sym}"
    return [C.src("SEC", "XBRL LongTermDebtMaturitiesRepaymentsOfPrincipal... (latest 10-K)", edgar + "&type=10-K"),
            C.src("EDGAR 424B filings", "prospectus supplements of the issuer's bonds", edgar + "&type=424B")]


def maturity_wall(company="AAPL", **_):
    sym = str(company or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9\-]{0,6}", sym):
        raise C.CompositeError("The debt maturity wall uses SEC 10-K figures: type the U.S. ticker of the issuer (for example AAPL, T, F).")

    def run():
        out = {}
        for tag, _ in WALL:
            try:
                out[tag] = C.api(C.V5, "equity/compare/company_facts", provider="sec", symbol=sym, fact=tag, instantaneous="true") or []
            except C.CompositeError:
                out[tag] = []
        return out
    got = wall_points(C.cached_disk(C.disk_name("debtwall_", sym), 12 * 3600, run))
    if not got:
        raise C.CompositeError(f"No debt maturity schedule in {sym}'s SEC filings (non-U.S. filers and companies that do not tag it have none).")
    end, filed, cik, pts = got
    fy = int(end[:4])
    xs = [f"{lab} (FY{fy + i + 1})" if i < 5 else f"{lab} (after FY{fy + 5})" for i, (lab, _) in enumerate(pts)]
    ys = [None if v is None else round(v / 1e9, 3) for _, v in pts]
    trace = {"type": "bar", "x": xs, "y": ys, "name": "Principal due, $bn", "marker": {"color": [C.PALETTE[0]] * 5 + [C.PALETTE[3]]},
             "text": ["–" if v is None else f"{v:,.1f}" for v in ys], "textposition": "outside", "hovertemplate": "%{x}: $%{y:,.2f}bn<extra></extra>"}
    lay = C._layout(False)
    lay["yaxis"]["tickprefix"] = "$"
    lay["yaxis"]["ticksuffix"] = "bn"
    total = sum(v for v in ys if v is not None)
    lay["annotations"] = [C._note(f"{sym} long-term debt principal by year due, 10-K for the fiscal year ended {end} (filed {filed}) · "
                                  f"${total:,.1f}bn in all · a blank bar = not tagged · filings: source menu")]
    return {"data": [trace], "layout": lay, "sources": _wall_sources(sym, cik)}


def static_sources(name, p):
    if name == "bond_pricer":
        return [SOURCE_FINRA]
    return _wall_sources(str((p or {}).get("company") or "AAPL").upper())
