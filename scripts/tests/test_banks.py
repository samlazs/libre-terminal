"""Money & Banking (2026-11-19): FDIC failures and bank credit quality views, new curated charts, bank-regulator monitor."""

from _common import check, done

import composite as c
import banks
import news

# ---- FDIC failures: thousands of dollars -> $ millions, M/D/YYYY -> ISO, newest first
raw = [{"NAME": "SMALL BUSINESS BANK", "CITY": "LENEXA", "PSTALP": "KS", "FAILDATE": "7/17/2026", "QBFASSET": 72947, "QBFDEP": 70120, "COST": 5671,
        "BIDNAME": "", "RESTYPE1": "PA"},
       {"NAME": "KENTLAND FS&LA", "CITY": "KENTLAND", "PSTALP": "IN", "FAILDATE": "7/10/2026", "QBFASSET": 3808, "COST": None, "BIDNAME": "first bank"},
       {"NAME": "BROKEN", "FAILDATE": "not a date"}]
rows = banks.failure_rows(raw)
check(len(rows) == 2 and rows[0] == {"failed": "2026-07-17", "bank": "Small Business Bank", "location": "Lenexa, KS", "assets_mn": 72.9, "deposits_mn": 70.1,
                                     "est_loss_mn": 5.7, "acquirer": None, "resolution": "PA"}, "72,947 thousand = $72.9M; cost 5,671 thousand = $5.7M; no acquirer = blank")
check(rows[1]["est_loss_mn"] is None and rows[1]["acquirer"] == "First Bank", "missing cost stays blank, not zero")
saved = c.cached_disk
try:
    c.cached_disk = lambda name, ttl, fn: list(reversed(raw[:2]))
    out = banks.bank_failures()
    check([r["failed"] for r in out["results"]] == ["2026-07-17", "2026-07-10"] and out["sources"][0]["name"] == "FDIC", "newest first, FDIC named")
finally:
    c.cached_disk = saved

# ---- quarterly ratios: YYYYMMDD -> ISO, oldest first, non-numbers skipped
fin = [{"REPDTE": "20260630", "NCLNLSR": 0.7792763, "NTLNLSR": 0.6143, "RBC1AAJ": 7.4947, "ROA": 1.5852},
       {"REPDTE": "20260331", "NCLNLSR": 0.8463, "NTLNLSR": None, "RBC1AAJ": 7.6257, "ROA": 1.4389}]
s = banks.quality_series(fin)
check(s["NCLNLSR"] == [["2026-03-31", 0.846], ["2026-06-30", 0.779]] and s["NTLNLSR"] == [["2026-06-30", 0.614]], "oldest first, rounded to 3, blank skipped")

# ---- ticker -> holding company -> lead bank -> chart; unknown ticker says what to type
saved = c.api, c.cached_disk, banks.lead_bank, banks._fdic
try:
    c.cached_disk = lambda name, ttl, fn: fn()
    c.api = lambda base, path, **kw: [{"rssd_id": "1120754", "legal_name": "WELLS FARGO & COMPANY"}] if kw.get("ticker") == "WFC" else []
    banks.lead_bank = lambda rssd: {"CERT": 3511, "NAME": "Wells Fargo Bank, National Association"} if rssd == "1120754" else None
    banks._fdic = lambda path, **p: fin
    ch = banks.bank_quality(symbol="wfc")
    names = [t["name"] for t in ch["data"]]
    check(names[0] == "Noncurrent loans  0.78%" and len(names) == 4, "legend carries the latest value in percent")
    check([t["visible"] for t in ch["data"]] == [True, True, "legendonly", "legendonly"], "credit lines shown, capital and ROA in the legend")
    check("Wells Fargo Bank, National Association (FDIC cert 3511), the largest bank of WELLS FARGO & COMPANY; quarter ended 2026-06-30" in ch["layout"]["annotations"][0]["text"],
          "the chart names the bank, the holding company and the quarter")
    try:
        banks.bank_quality(symbol="AAPL")
        check(False, "a non-bank ticker must fail")
    except c.CompositeError as e:
        check("No U.S. bank holding company found for 'AAPL'" in str(e), "non-bank ticker: plain message")
finally:
    c.api, c.cached_disk, banks.lead_bank, banks._fdic = saved

# ---- curated charts: units and scales declared
ms = c.MACRO_SETS
check(ms["charge_offs"][1] == "%" and [x[1] for x in ms["charge_offs"][3]][-1] == "CORALACBS", "charge-offs in percent")
check(ms["bank_funding"][1] == " $tn" and all(x[2] == 1e-3 for x in ms["bank_funding"][3]), "H.8 $bn -> $tn")
check(ms["securitization"][1] == " $tn" and all(x[2] == 1e-6 for x in ms["securitization"][3]), "Z.1 $mn -> $tn")
check([x[1] for x in ms["problem_loans"][3]] == ["QBPLNTLNNCUR", "DRALACBS"], "noncurrent (FDIC) beside delinquent (Fed)")

# ---- bank_rssd: holding company lookup prefers is_bank_holding_company, copies cached dict, unknown ticker fails
saved = c.api, c.cached_disk
try:
    c.cached_disk = lambda name, ttl, fn: fn()
    c.api = lambda base, path, **kw: (
        [{"rssd_id": "999", "legal_name": "SUB BANK", "is_bank_holding_company": False},
         {"rssd_id": "1120754", "legal_name": "WELLS FARGO & COMPANY", "is_bank_holding_company": True}]
        if kw.get("ticker") == "WFC" else []
    )
    r = banks.bank_rssd(symbol="wfc")
    check(r["symbol"] == "WFC" and r["rssd_id"] == "1120754" and r["name"] == "WELLS FARGO & COMPANY"
          and r["sources"] == [banks.FFIEC_SRC], "bank_rssd prefers BHC row and returns FFIEC source")
    try:
        banks.bank_rssd(symbol="AAPL")
        check(False, "non-bank ticker must fail bank_rssd")
    except c.CompositeError as e:
        check("No U.S. bank holding company found for 'AAPL'" in str(e), "bank_rssd non-bank error")
finally:
    c.api, c.cached_disk = saved

# ---- bank supervisors' monitor: its own feed set, kept out of the general official monitor, every line shown
check(set(news.BANK_REGULATORS) == {"fed_bankreg", "fed_enforce", "occ", "fsb", "ecb_ssm", "eba"} and not set(news.BANK_REGULATORS) & set(news.OFFICIAL), "regulator feeds")
check(news.REGIONS["banks"][1] == news.BANK_REGULATORS and any(o["value"] == "banks" for o in news.FEED_OPTIONS), "monitor choice offered")
check("bank_failures" in c.ROUTES and "bank_quality" in c.ROUTES and "bank_rssd" in c.ROUTES
      and c.static_sources("bank_quality")[1]["name"] == "FDIC"
      and c.static_sources("bank_rssd")[0]["name"] == "FFIEC NIC", "routes and static sources")
done("test_banks")
