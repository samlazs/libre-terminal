"""Company jump buttons (HANDOFF P3-4): where a company can be opened next (Options, Filings, Bonds, News), each with
the company in the form the destination needs, and only where the destination can serve it.

- Options: the Equity Options dashboard covers U.S. listings (Cboe / Yahoo chains); a share class written BRK.B or
  BRK-B is a U.S. listing, an exchange suffix (.L, .DE, .T, .TO...) is not.
- Filings: SEC filings when the ticker is an SEC filer (exact match in the SEC company list, which includes ADRs such
  as SHEL); Canadian listings (.TO, .V) go to the Canada dashboard (SEDAR+ / TMX filings) with the bare symbol (RY).
- Bonds: the FINRA TRACE lookup (terminal/bonds.py), shown only when it finds the issuer, with what found it (the
  ticker for an SEC filer, else the company name).
- Bank: Money & Banking > Bank Lookup (FDIC credit quality, UBPR peer ratios, FFIEC call report) for a ticker the FFIEC
  directory knows as a bank holding company (JPM, WFC; RY for RY.TO).
- News: the News dashboard ranks headlines for any ticker.
"""

import re

import composite as C
import banks
import bonds

CANADA = {"TO", "V", "CN", "NE"}
NON_US = CANADA | {"L", "IL", "DE", "F", "BE", "DU", "HM", "MU", "SG", "PA", "AS", "BR", "MI", "MC", "LS", "SW", "ST", "OL", "CO", "HE",
                   "IC", "VI", "WA", "PR", "AT", "IR", "T", "HK", "SS", "SZ", "KS", "KQ", "TW", "TWO", "SI", "AX", "NZ", "NS", "BO",
                   "JK", "KL", "BK", "SA", "MX", "BA", "SN", "JO", "TA", "SR", "QA", "AE", "IS", "CR", "CL"}
SOURCES = [C.src("SEC", "company search: is the ticker an SEC filer", "https://www.sec.gov/edgar/search/"),
           C.src("Yahoo Finance", "company name for a listing outside the U.S.", C.YAHOO_URL), bonds.SOURCE_FINRA, banks.FFIEC_SRC]


def listing(symbol):
    """('us' | 'ca' | 'other', bare symbol) from the exchange suffix."""
    s = str(symbol or "").strip().upper()
    if "." in s:
        base, suf = s.rsplit(".", 1)
        if suf in CANADA:
            return "ca", base
        if suf in NON_US:
            return "other", base
    return "us", s


def _yahoo_profile(sym):
    def run():
        try:
            rows = C.api(C.V5, "equity/profile", provider="yfinance", symbol=sym) or []
        except C.CompositeError as e:
            C.partial_from(e, "Yahoo Finance profile")
            return {}
        r = rows[0] if rows else {}
        return {"name": r.get("name") or r.get("long_name") or "", "sector": r.get("sector") or "", "industry": r.get("industry_category") or ""}
    return C.cached(("yahoo_profile", sym), 86400, run)


def _bank_value(sym, base, sector=None):
    """(ticker the FFIEC directory knows the bank holding company by (JPM; RY for RY.TO) or None, whether a lookup failed).
    Skips FFIEC when Yahoo's sector is known and non-financial; unknown-ticker 400s cache 24h while outages cache 5 minutes."""
    if sector and "financial" not in str(sector).lower():
        return None, False

    def run():
        failed = False
        for t in dict.fromkeys([base, sym] if "." in sym else [sym]):
            try:
                inst = C.api(C.V5, "federal_reserve/ffiec/institutions", ticker=t) or []
            except C.CompositeError as e:
                if "could not resolve ticker" not in str(e).lower():
                    failed = True
                continue
            if any(r.get("rssd_id") for r in inst):
                return {"value": t, "_partial": False}
        return {"value": None, "_partial": ["FFIEC bank directory"] if failed else False}
    got = C.cached_complete(("bank_value", sym), 86400, run, short=300)
    return got["value"], bool(got["_partial"])


def _bond_value(sym, sec_name, name):
    """What makes the bond lookup find this issuer (the ticker for an SEC filer, else the company name), or None."""
    for value in ([sym] if sec_name else []) + ([name] if name else []):
        try:
            rows, _ = bonds.resolve(company=value)
        except C.CompositeError as e:
            C.partial_from(e, "bond issuer lookup")
            continue
        if rows:
            return value
    return None


def links(symbol="AAPL", **_):
    sym = str(symbol or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", sym):
        raise C.CompositeError("A company ticker is needed (indices, currencies and futures have no company links).")

    def run():
        where, base = listing(sym)
        sec_name = bonds._sec_name(sym) if where == "us" else None
        yprof = _yahoo_profile(sym) if (not sec_name or where in ("us", "ca")) else {}
        name = sec_name or yprof.get("name") or None
        out, partial = [], False
        if where == "us":
            out.append({"key": "options", "label": "Options", "dash": "d:options", "tab": "chain", "group": "Symbol", "value": sym})
        if sec_name:
            out.append({"key": "filings", "label": "Filings", "dash": "d:filings", "tab": "search-read", "group": "Symbol", "value": sym})
        elif where == "ca":
            out.append({"key": "filings", "label": "Filings", "dash": "d:eq-canada", "tab": "company", "group": "Symbol", "value": base})
        bond = _bond_value(sym, sec_name, name)
        if bond:
            out.append({"key": "bonds", "label": "Bonds", "dash": "d:credit", "tab": "bond-lookup-trace", "group": "Issuer", "value": bond})
        if where in ("us", "ca"):                  # the FFIEC directory lists bank holding companies only
            bank, failed = _bank_value(sym, base, yprof.get("sector"))
            partial = partial or failed
            if bank:
                out.append({"key": "bank", "label": "Bank", "dash": "d:banking", "tab": "bank-lookup", "group": "Bank", "value": bank})
        out.append({"key": "news", "label": "News", "dash": "d:news", "tab": "company-world", "group": "Symbol", "value": sym})
        return {"results": out, "name": name, "sources": SOURCES, "_partial": partial}
    got = C.cached_complete(("company_links", sym), 3600, run, short=300)
    return {k: v for k, v in got.items() if k != "_partial"}
