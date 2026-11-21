"""Bank views for Money & Banking (owner request 2026-11-19; sources probed that day, facts in HANDOFF "Money & Banking").

- bank_failures: FDIC failed-bank list (BankFind API, keyless), newest first.
- bank_quality: one bank holding company by ticker -> FFIEC NIC RSSD id (backend `federal_reserve/ffiec/institutions`)
  -> its largest FDIC-insured bank (BankFind `RSSDHCR`) -> quarterly credit quality and capital ratios from the FDIC
  call-report data: noncurrent loans, net charge-offs, leverage ratio, return on assets (all in percent).
"""

import datetime as dt
import json
import urllib.parse

import composite as C

FDIC = "https://banks.data.fdic.gov/api/"
FDIC_SRC = C.src("FDIC", "BankFind Suite API (failures, institutions, quarterly financials)", "https://banks.data.fdic.gov/docs/")
FFIEC_SRC = C.src("FFIEC NIC", "holding company identified by ticker (RSSD id)", "https://www.ffiec.gov/npw")
# FDIC field -> (label, what it is). FDIC reports these ratios in percent.
QUALITY = [("NCLNLSR", "Noncurrent loans", "loans 90+ days past due or nonaccrual, % of loans"),
           ("NTLNLSR", "Net charge-offs", "net charge-offs, % of average loans, annualized year to date"),
           ("RBC1AAJ", "Leverage ratio", "tier 1 capital, % of average assets"),
           ("ROA", "Return on assets", "net income, % of average assets, annualized year to date")]


def _fdic(path, **params):
    url = FDIC + path + "?" + urllib.parse.urlencode(params)
    j = json.loads(C._download(url, timeout=40, ua=C.BROWSER_UA))
    return [r.get("data", r) for r in j.get("data") or []]


def _date_us(s):
    """FDIC failure dates come as M/D/YYYY."""
    try:
        m, d, y = (int(x) for x in str(s).split("/"))
        return dt.date(y, m, d).isoformat()
    except (TypeError, ValueError):
        return None


def _title(s):
    """FDIC names are upper case: title case, but abbreviations with "&" stay as written ("KENTLAND FS&LA" -> "Kentland FS&LA")."""
    return " ".join(w if "&" in w else w.capitalize() for w in str(s or "").split())


def failure_rows(raw):
    """FDIC failure records -> card rows. Amounts: FDIC sends thousands of dollars; shown in $ millions."""
    out = []
    for r in raw:
        def mn(k):
            v = r.get(k)
            return round(float(v) / 1000, 1) if isinstance(v, (int, float)) else None
        out.append({"failed": _date_us(r.get("FAILDATE")), "bank": _title(r.get("NAME")),
                    "location": ", ".join(x for x in (str(r.get("CITY") or "").title(), r.get("PSTALP")) if x),
                    "assets_mn": mn("QBFASSET"), "deposits_mn": mn("QBFDEP"), "est_loss_mn": mn("COST"),
                    "acquirer": _title(r.get("BIDNAME")) or None, "resolution": r.get("RESTYPE1") or r.get("RESTYPE")})
    return [r for r in out if r["failed"]]


def bank_failures(limit="30", **_):
    """Most recent U.S. bank failures (FDIC), newest first."""
    try:
        n = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        n = 30

    def run():
        return _fdic("failures", sort_by="FAILDATE", sort_order="DESC", limit=n,
                     fields="NAME,CITY,PSTALP,FAILDATE,QBFASSET,QBFDEP,COST,BIDNAME,RESTYPE1,RESTYPE")
    rows = failure_rows(C.cached_disk(f"fdic_failures_{n}", 12 * 3600, run))
    rows.sort(key=lambda r: r["failed"], reverse=True)
    return {"results": rows, "sources": [FDIC_SRC]}


def lead_bank(holding_rssd):
    """The largest active FDIC-insured bank of a holding company: {CERT, NAME, ASSET} or None."""
    rows = _fdic("institutions", filters=f"RSSDHCR:{holding_rssd} AND ACTIVE:1", fields="NAME,CERT,ASSET,FED_RSSD",
                 sort_by="ASSET", sort_order="DESC", limit=1)
    return rows[0] if rows else None


def quality_series(raw):
    """FDIC quarterly financial rows -> {field: [[iso date, value], ...]} oldest first (REPDTE is YYYYMMDD)."""
    out = {k: [] for k, _, _ in QUALITY}
    for r in sorted(raw, key=lambda r: str(r.get("REPDTE"))):
        d = str(r.get("REPDTE") or "")
        if len(d) != 8:
            continue
        iso = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        for k, _, _ in QUALITY:
            v = r.get(k)
            if isinstance(v, (int, float)):
                out[k].append([iso, round(float(v), 3)])
    return out


def holding_company(symbol="JPM"):
    """Look up a bank holding company by ticker in the FFIEC NIC directory."""
    sym = str(symbol or "JPM").strip().upper().split(",")[0]
    try:
        inst = C.api(C.V5, "federal_reserve/ffiec/institutions", ticker=sym) or []
    except C.CompositeError:
        inst = []
    if not inst:
        raise C.CompositeError(f"No U.S. bank holding company found for '{sym}' in the FFIEC directory. Use the ticker of a "
                               "U.S.-listed bank or of a foreign bank with U.S. operations (JPM, WFC, PNC, RY, TD).")
    for r in inst:
        if r.get("is_bank_holding_company") and r.get("rssd_id"):
            return r
    return inst[0]


def bank_rssd(symbol="JPM", **_):
    """Resolve a bank holding company ticker to its FFIEC RSSD identifier."""
    sym = str(symbol or "").strip().upper().split(",")[0]
    if not sym:
        raise C.CompositeError("Enter a bank ticker (for example JPM, BAC, WFC, C, RY).")

    def run():
        hc = holding_company(sym)
        rssd = str(hc.get("rssd_id") or "").strip()
        if not rssd:
            raise C.CompositeError(f"No FFIEC RSSD identifier found for '{sym}'.")
        return {"symbol": sym, "rssd_id": rssd, "name": hc.get("legal_name") or hc.get("short_name") or sym}

    got = C.cached_disk("bank_rssd_" + sym.replace("/", "_"), 24 * 3600, run)
    return dict(got, sources=[FFIEC_SRC])


def bank_quality(symbol="JPM", **_):
    """Credit quality and capital of the bank behind a U.S.-listed bank holding company ticker, quarterly (FDIC)."""
    sym = str(symbol or "JPM").strip().upper().split(",")[0]

    def run():
        hc = holding_company(sym)
        bank = lead_bank(hc["rssd_id"])
        if not bank:
            raise C.CompositeError(f"{hc.get('legal_name') or sym} has no FDIC-insured bank (FDIC BankFind).")
        raw = _fdic("financials", filters=f"CERT:{bank['CERT']}", fields="REPDTE," + ",".join(k for k, _, _ in QUALITY),
                    sort_by="REPDTE", sort_order="DESC", limit=60)
        return {"holding": hc.get("legal_name") or sym, "bank": bank.get("NAME"), "cert": bank.get("CERT"), "series": quality_series(raw)}
    got = C.cached_disk("bank_quality_" + sym.replace("/", "_"), 12 * 3600, run)
    traces = []
    for i, (k, label, what) in enumerate(QUALITY):
        pts = got["series"].get(k) or []
        if pts:
            traces.append({"type": "scatter", "mode": "lines", "name": f"{label}  {pts[-1][1]:.2f}%", "x": [p[0] for p in pts], "y": [p[1] for p in pts],
                           "line": {"width": 2, "color": C.PALETTE[i % len(C.PALETTE)]}, "hovertemplate": "%{y:.2f}%<extra>" + what + "</extra>",
                           "visible": True if k in ("NCLNLSR", "NTLNLSR") else "legendonly"})
    if not traces:
        raise C.CompositeError(f"FDIC has no quarterly financials for {got['bank']}.")
    lay = C._layout(True)
    lay["yaxis"]["ticksuffix"] = "%"
    last = max(t["x"][-1] for t in traces)
    lay["annotations"] = [C._note(f"{got['bank']} (FDIC cert {got['cert']}), the largest bank of {got['holding']}; quarter ended {last}. "
                                  "Leverage ratio and ROA are in the legend.")]
    return {"data": traces, "layout": lay, "sources": [FFIEC_SRC, FDIC_SRC]}
