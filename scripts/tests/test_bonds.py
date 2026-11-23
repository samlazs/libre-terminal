"""HANDOFF P3-1 corporate bond lookup (terminal/bonds.py): FINRA name resolution, filters, placeholder yield, CUSIP check."""

import datetime as dt

from _common import check, done

import composite as c
import bonds as b
b._par_curve = lambda: {}          # offline: no FRED request for the G-spread curve

TODAY = dt.date(2026, 11, 19)


def bond(issuer, coupon, mat, y, last, **kw):
    return dict({"issuer_name": issuer, "coupon_rate": coupon, "maturity_date": mat, "price": 100.0, "last_sale_yield": y,
                 "last_trade_date": last, "cusip": f"X{coupon}"}, **kw)


FINRA = [
    bond("FORD MTR CO DEL", 6.1, "2032-08-19", 5.85, "2026-11-18", price=101.2, moodys_rating="Ba1", sp_rating="BBB-", is_callable=True,
         call_date="2032-05-19", grade="H"),
    bond("FORD MTR CO DEL", 4.75, "2043-01-15", 999999.99, "2026-11-10"),                       # placeholder yield
    bond("FORD MTR CO DEL MTN", 5.0, "2030-01-01", 5.5, "2026-11-18"),                          # MTN programme
    bond("FORD MTR CO DEL", 0.0, "2026-03-15", 4.0, "2026-11-18", is_convertible=True),         # convertible
    bond("FORD MTR CO DEL", 7.4, "2046-11-01", 6.2, "2026-09-01"),                              # last trade > 45 days ago
    bond("FORD MTR CR CO LLC", 5.8, "2029-03-05", 5.4, "2026-11-18"),
    bond("FORDHAM UNIV", 4.0, "2040-07-01", 4.4, "2026-11-18"),
    bond("APPLE INC", 2.85, "2061-08-05", 6.155, "2026-11-18", sp_rating="AA+"),
]
calls = []


def fake_api(base, path, **p):
    calls.append((path, tuple(sorted(p.items()))))
    if path == "equity/search":
        return [{"symbol": "FE", "name": "FIRSTENERGY CORP"}, {"symbol": "F", "name": "FORD MOTOR CO"}] if p.get("query") == "F" else []
    if p.get("cusip"):
        return [dict(FINRA[7], cusip=p["cusip"])] if p["cusip"] != "345370CR9" else []
    return [r for r in FINRA if p.get("issuer_name", "") in r["issuer_name"]]


check(b.cusip_ok("037833100") and b.cusip_ok("345370CR9"), "CUSIP check digit: Apple 037833100, Ford 345370CR9")
check(not b.cusip_ok("037833101") and not b.cusip_ok("AAPL") and not b.cusip_ok("03783310A"), "CUSIP: wrong check digit, too short, letter as check")
check(b.candidates("FORD MOTOR CO") == ["FORD MTR CO", "FORD MOTOR CO", "FORD MTR", "FORD"], f"candidates Ford: {b.candidates('FORD MOTOR CO')}")
check(b.candidates("BANK OF AMERICA CORP /DE/") == ["BANK AMER CORP", "BANK OF AMERICA CORP", "BANK AMER"], "candidates BofA: abbreviations, no bare 'BANK'")
check(b.candidates("Meta Platforms, Inc.") == ["META PLATFORMS INC", "META PLATFORMS", "META"], f"candidates Meta: {b.candidates('Meta Platforms, Inc.')}")
check(b.whole_words("FORD MTR CO DEL", "FORD") and not b.whole_words("FORDHAM UNIV", "FORD"), "whole words: FORD is not FORDHAM")
check(b.whole_words("AT&T INC", "AT&T") and not b.whole_words("AT&TX CORP", "AT&T"), "whole words keep '&'")

saved = c.api
try:
    c.api = fake_api
    c._cache.clear()
    t = b.lookup(company="f", today=TODAY)
    rows = t["results"]
    check([r["coupon"] for r in rows] == [6.1, 4.75], f"kept: the two plain, recently traded bonds, by maturity: {[r['coupon'] for r in rows]}")
    r = rows[0]
    check((r["issuer"], r["maturity"], r["rating"], r["price"], r["yield"], r["last_trade"], r["callable"], r["grade"]) ==
          ("FORD MTR CO DEL", "2032-08-19", "Ba1 / BBB-", 101.2, 5.85, "2026-11-18", "yes, from 2032-05-19", "H"), f"row: {r}")
    check(rows[1]["yield"] is None and rows[1]["rating"] is None and rows[1]["callable"] == "no", "999999.99 placeholder -> blank yield")
    check(t["delayed"] == "TRACE last-sale data, not live quotes" and [s["name"] for s in t["sources"]] == ["FINRA", "SEC", "FRED"], "footer and sources (FRED par curve for the G-spread)")
    check(("fixedincome/corporate/bond_prices", (("issuer_name", "FORD MTR CO"), ("provider", "finra"))) in calls, "first try: FINRA abbreviation of the SEC name")

    allr = b.lookup(company="F", all_types="yes", include_old="yes", today=TODAY)["results"]
    check(len(allr) == 5 and sum(1 for x in allr if x["special"]) == 2, "toggles show MTN, convertible and the old trade (FORD MTR CO matches 5)")

    named = b.lookup(company="Ford Motor", today=TODAY)["results"]
    check({x["issuer"] for x in named} == {"FORD MTR CO DEL", "FORD MTR CR CO LLC"}, "a name, not a ticker: 'FORD MTR' also finds Ford Credit")
    check(all(x["issuer"] != "FORDHAM UNIV" for x in b.lookup(issuer="ford", today=TODAY)["results"]), "issuer override, whole words")
    cu = b.lookup(company="037833100", today=TODAY)["results"]
    check(cu[0]["cusip"] == "037833100" and calls[-1][1] == (("cusip", "037833100"), ("provider", "finra")), "a valid CUSIP is looked up as a CUSIP")

    ch = b.curve(company="F", today=TODAY)
    check([tr["name"] for tr in ch["data"]] == ["FORD MTR CO DEL (1)"] and ch["data"][0]["y"] == [5.85], "curve: blank yields not plotted")
    check(ch["layout"]["annotations"][0]["text"] == "x: maturity · click a point for its history · hidden: 2 MTN / convertible / perpetual, 1 not traded in 45 days", "curve note counts hidden bonds")

    try:
        b.lookup(company="345370CR9", today=TODAY)
        check(False, "CUSIP without bonds raises")
    except c.CompositeError as e:
        check("No TRACE bond with CUSIP 345370CR9" in str(e), "CUSIP without bonds: plain message")
    for bad in ("ZZZZ", ""):
        try:
            b.lookup(company=bad, today=TODAY)
            check(False, f"'{bad}' raises")
        except c.CompositeError as e:
            check("CUSIP" in str(e), f"'{bad}': says what to type")
    check([s["name"] for s in c.static_sources("bond_curve", {})] == ["FINRA", "SEC"], "static sources")
finally:
    c.api = saved
    c._cache.clear()

done("test_bonds")
