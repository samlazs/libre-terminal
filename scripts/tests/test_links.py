"""HANDOFF P3-4 company jump buttons (terminal/links.py) and the CUSIP rule shared with the go-to box."""

import pathlib
import re

from _common import check, done

import composite as c
import links as L

SEC = {"AAPL": "APPLE INC", "F": "FORD MOTOR CO"}
YAHOO = {"SHEL.L": "Shell plc", "RY.TO": "Royal Bank of Canada", "BRK.B": "Berkshire Hathaway Inc.", "SAP.DE": "SAP SE"}
FINRA = ["APPLE INC", "SHELL INTL FIN B V", "BERKSHIRE HATHAWAY INC", "FORD MTR CO DEL"]
calls = []


def fake_api(base, path, **p):
    calls.append((path, p.get("symbol") or p.get("query") or p.get("issuer_name") or p.get("ticker")))
    if path == "equity/search":
        return [{"symbol": p["query"], "name": SEC[p["query"]]}] if p["query"] in SEC else [{"symbol": "AAPL.X", "name": "OTHER"}]
    if path == "equity/profile":
        return [{"symbol": p["symbol"], "name": YAHOO[p["symbol"]]}] if p["symbol"] in YAHOO else []
    if path == "federal_reserve/ffiec/institutions":     # bank holding companies by ticker
        return [{"rssd_id": 1039502, "is_bank_holding_company": True}] if p.get("ticker") in ("JPM", "RY") else []
    if path == "fixedincome/corporate/bond_prices":
        return [{"issuer_name": n, "coupon_rate": 4.0, "maturity_date": "2030-01-01", "last_trade_date": "2026-11-18"} for n in FINRA if p["issuer_name"] in n]
    raise AssertionError(path)


check([L.listing(s)[0] for s in ("AAPL", "BRK.B", "BRK-B", "SHEL.L", "SAP.DE", "7203.T", "RY.TO", "SHOP.V")] == ["us", "us", "us", "other", "other", "other", "ca", "ca"],
      "listing from the exchange suffix (a share class is U.S.)")
check(L.listing("RY.TO") == ("ca", "RY"), "Canada: bare symbol for the TMX dashboard")

saved = c.api
try:
    c.api = fake_api
    c._cache.clear()

    def keys(sym):
        return {l["key"]: l for l in L.links(symbol=sym)["results"]}

    a = keys("aapl")
    check(list(a) == ["options", "filings", "bonds", "news"], f"AAPL: every destination: {list(a)}")
    check(a["options"]["dash"] == "d:options" and a["options"]["value"] == "AAPL" and a["filings"]["dash"] == "d:filings", "AAPL: options and SEC filings by ticker")
    check(a["bonds"] == {"key": "bonds", "label": "Bonds", "dash": "d:credit", "tab": "bond-lookup-trace", "group": "Issuer", "value": "AAPL"}, f"AAPL bonds by ticker: {a['bonds']}")
    check(a["news"]["dash"] == "d:news" and a["news"]["group"] == "Symbol", "news by ticker")
    check(not any(p == "equity/profile" for p, _ in calls), "an SEC filer needs no Yahoo name")

    s = keys("SHEL.L")
    check(list(s) == ["bonds", "news"], f"SHEL.L: no U.S. options, no SEC filer under this ticker: {list(s)}")
    check(s["bonds"]["value"] == "Shell plc", "SHEL.L bonds through the company name (FINRA: SHELL INTL FIN B V)")
    check(("equity/search", "SHEL.L") not in calls, "a non-U.S. listing is not looked up in the SEC list")

    r = keys("RY.TO")
    check(list(r) == ["filings", "bank", "news"] and r["filings"]["dash"] == "d:eq-canada" and r["filings"]["value"] == "RY", f"RY.TO: Canadian filings with RY, no bonds found: {r}")
    check(r["bank"] == {"key": "bank", "label": "Bank", "dash": "d:banking", "tab": "bank-lookup", "group": "Bank", "value": "RY"}, f"RY.TO: Bank Lookup with the FFIEC ticker RY: {r['bank']}")

    b = keys("BRK.B")
    check(list(b) == ["options", "bonds", "news"] and b["bonds"]["value"] == "Berkshire Hathaway Inc.", f"BRK.B: U.S. options; not an SEC ticker match; bonds by name: {list(b)}")

    check(list(keys("SAP.DE")) == ["news"], "SAP.DE: only what can serve it (no FINRA issuer in the fixture)")
    n = len(calls)
    keys("AAPL")
    check(len(calls) == n, "answers cached")
    for bad in ("^GSPC", "EURUSD=X", ""):
        try:
            L.links(symbol=bad)
            check(False, f"{bad!r} raises")
        except c.CompositeError:
            check(True, f"{bad!r}: no company links")
    check([x["name"] for x in c.static_sources("company_links", {})] == ["SEC", "Yahoo Finance", "FINRA", "FFIEC NIC"], "static sources")
finally:
    c.api = saved
    c._cache.clear()

# ---- the go-to box uses the same CUSIP rule as terminal/bonds.py (no Node here: the JS is compared by its text and checked in the pane)
js = (pathlib.Path(__file__).resolve().parents[2] / "terminal" / "static" / "app.js").read_text(encoding="utf-8")
body = js[js.index("function cusipOk(s)"):js.index("T.cusipOk = cusipOk;")]
check("(10 - total % 10) % 10" in body and "if (i % 2) v *= 2" in body and "ch.charCodeAt(0) - 55" in body, "JS CUSIP rule: double every second digit, A = 10")
check(re.search(r"/\^FRED:\[A-Za-z0-9_\.\]\+\$/i", js) is not None, "go-to box recognises FRED:<id>")
check("paintJumps(box);" in js and '"/x/company_links?symbol="' in js, "Company group bar asks /x/company_links")


# review fix: an FFIEC outage hides the Bank link for 5 minutes, not a day
def down_api(base, path, **p):
    if path == "federal_reserve/ffiec/institutions":
        raise c.CompositeError("OpenBB V5 backend is not reachable (refused).")
    return fake_api(base, path, **p)

saved = c.api
try:
    c._cache.clear()
    c.api = down_api
    check(L._bank_value("JPM", "JPM") == (None, True), "outage: no bank, flagged failed")
    c.api = fake_api
    check(L._bank_value("JPM", "JPM") == (None, True), "outage answer held briefly")
    k = ("bank_value", "JPM")
    c._cache[k] = (c._cache[k][0] - 400, c._cache[k][1])
    check(L._bank_value("JPM", "JPM") == ("JPM", False), "after 5 minutes the bank is looked up again")
finally:
    c.api = saved
    c._cache.clear()

done("test_links")
