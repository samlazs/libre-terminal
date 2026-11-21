"""Company jump buttons (HANDOFF P3-4): where a company can be opened next (Options, Filings, Bonds, News), each with
the company in the form the destination needs, and only where the destination can serve it.

- Options: the Equity Options dashboard covers U.S. listings (Cboe / Yahoo chains); a share class written BRK.B or
  BRK-B is a U.S. listing, an exchange suffix (.L, .DE, .T, .TO...) is not.
- Filings: SEC filings when the ticker is an SEC filer (exact match in the SEC company list, which includes ADRs such
  as SHEL); Canadian listings (.TO, .V) go to the Canada dashboard (SEDAR+ / TMX filings) with the bare symbol (RY).
- Bonds: the FINRA TRACE lookup (terminal/bonds.py), shown only when it finds the issuer, with what found it (the
  ticker for an SEC filer, else the company name).
- News: the News dashboard ranks headlines for any ticker.
"""

import re

import composite as C
import bonds

CANADA = {"TO", "V", "CN", "NE"}
NON_US = CANADA | {"L", "IL", "DE", "F", "BE", "DU", "HM", "MU", "SG", "PA", "AS", "BR", "MI", "MC", "LS", "SW", "ST", "OL", "CO", "HE",
                   "IC", "VI", "WA", "PR", "AT", "IR", "T", "HK", "SS", "SZ", "KS", "KQ", "TW", "TWO", "SI", "AX", "NZ", "NS", "BO",
                   "JK", "KL", "BK", "SA", "MX", "BA", "SN", "JO", "TA", "SR", "QA", "AE", "IS", "CR", "CL"}
SOURCES = [C.src("SEC", "company search: is the ticker an SEC filer", "https://www.sec.gov/edgar/search/"),
           C.src("Yahoo Finance", "company name for a listing outside the U.S.", C.YAHOO_URL), bonds.SOURCE_FINRA]


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


def _yahoo_name(sym):
    def run():
        try:
            rows = C.api(C.V5, "equity/profile", provider="yfinance", symbol=sym) or []
        except C.CompositeError:
            return None
        return next((r.get("name") or r.get("long_name") for r in rows if r.get("name") or r.get("long_name")), None)
    return C.cached(("yahoo_name", sym), 86400, run)


def _bond_value(sym, sec_name, name):
    """What makes the bond lookup find this issuer (the ticker for an SEC filer, else the company name), or None."""
    for value in ([sym] if sec_name else []) + ([name] if name else []):
        try:
            rows, _ = bonds.resolve(company=value)
        except C.CompositeError:
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
        name = sec_name or _yahoo_name(sym)
        out = []
        if where == "us":
            out.append({"key": "options", "label": "Options", "dash": "d:options", "tab": "chain", "group": "Symbol", "value": sym})
        if sec_name:
            out.append({"key": "filings", "label": "Filings", "dash": "d:filings", "tab": "search-read", "group": "Symbol", "value": sym})
        elif where == "ca":
            out.append({"key": "filings", "label": "Filings", "dash": "d:eq-canada", "tab": "company", "group": "Symbol", "value": base})
        bond = _bond_value(sym, sec_name, name)
        if bond:
            out.append({"key": "bonds", "label": "Bonds", "dash": "d:credit", "tab": "bond-lookup-trace", "param": "company", "value": bond})
        out.append({"key": "news", "label": "News", "dash": "d:news", "tab": "company-world", "group": "Symbol", "value": sym})
        return {"results": out, "name": name, "sources": SOURCES}
    return C.cached(("company_links", sym), 3600, run)
