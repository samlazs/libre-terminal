"""Country curve metadata and official central-bank / MoF / OECD curve fetchers."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

import composite as C

# ---------------------------------------------------------------- sovereign curves
# key -> (label, curve source, unused, ISO2 for BIS policy rates)
# Daily official curves where a keyless official source exists; everywhere else the OECD's monthly 3-month and 10-year
# rates, shown as two points and labelled as such (no curve is drawn through them).
COUNTRIES = {
    "united_states": ("United States", "fed", None, "US"),
    "euro_area": ("Euro area (AAA govt)", "ecb", None, "XM"),
    "germany": ("Germany", "buba", None, "XM"),
    "united_kingdom": ("United Kingdom", "boe", None, "GB"),
    "japan": ("Japan", "mof", None, "JP"),
    "canada": ("Canada", "boc", None, "CA"),
    "sweden": ("Sweden", "riks", None, "SE"),
    "brazil": ("Brazil", "tesouro", None, "BR"),
    "france": ("France", "oecd", None, "XM"),
    "italy": ("Italy", "oecd", None, "XM"),
    "spain": ("Spain", "oecd", None, "XM"),
    "netherlands": ("Netherlands", "oecd", None, "XM"),
    "belgium": ("Belgium", "oecd", None, "XM"),
    "austria": ("Austria", "oecd", None, "XM"),
    "portugal": ("Portugal", "oecd", None, "XM"),
    "ireland": ("Ireland", "oecd", None, "XM"),
    "greece": ("Greece", "oecd", None, "XM"),
    "finland": ("Finland", "oecd", None, "XM"),
    "switzerland": ("Switzerland", "oecd", None, "CH"),
    "norway": ("Norway", "oecd", None, "NO"),
    "denmark": ("Denmark", "oecd", None, "DK"),
    "poland": ("Poland", "oecd", None, "PL"),
    "czech_republic": ("Czechia", "oecd", None, "CZ"),
    "hungary": ("Hungary", "oecd", None, "HU"),
    "australia": ("Australia", "oecd", None, "AU"),
    "new_zealand": ("New Zealand", "oecd", None, "NZ"),
    "south_korea": ("South Korea", "oecd", None, "KR"),
    "china": ("China", "oecd", None, "CN"),
    "india": ("India", "oecd", None, "IN"),
    "mexico": ("Mexico", "oecd", None, "MX"),
    "chile": ("Chile", "oecd", None, "CL"),
    "colombia": ("Colombia", "oecd", None, "CO"),
    "israel": ("Israel", "oecd", None, "IL"),
    "south_africa": ("South Africa", "oecd", None, "ZA"),
}

CURVE_SOURCES = {"fed": "Federal Reserve (Treasury par curve)", "ecb": "ECB (AAA-rated euro-area government curve)", "buba": "Deutsche Bundesbank (Bund curve, Svensson)",
                 "boe": "Bank of England (gilt nominal spot curve)", "mof": "Japan Ministry of Finance (JGB yields)", "boc": "Bank of Canada (benchmark bonds and T-bills)",
                 "riks": "Sveriges Riksbank (T-bills and government bonds)", "tesouro": "Tesouro Direto (fixed-rate bonds, buy rates)", "oecd": "OECD (monthly 3-month and 10-year rates)",
                 "fred": "FRED (Treasury constant-maturity yields, Fed H.15)"}

CURVE_URLS = {"fed": "https://www.federalreserve.gov/releases/h15/", "ecb": "https://data.ecb.europa.eu/data/datasets/YC",
              "buba": "https://www.bundesbank.de/en/statistics/money-and-capital-markets/interest-rates-and-yields/term-structure-of-interest-rates",
              "boe": "https://www.bankofengland.co.uk/statistics/yield-curves", "mof": "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/index.htm",
              "boc": "https://www.bankofcanada.ca/rates/interest-rates/", "riks": "https://www.riksbank.se/en-gb/statistics/interest-rates-and-exchange-rates/",
              "tesouro": "https://www.tesourodireto.com.br/", "oecd": "https://data-explorer.oecd.org/", "fred": "https://fred.stlouisfed.org/series/DGS10"}

CURVE_SHORT = {"fed": "Federal Reserve", "ecb": "ECB", "buba": "Bundesbank", "boe": "Bank of England", "mof": "Japan MoF", "boc": "Bank of Canada",
               "riks": "Riksbank", "tesouro": "Tesouro Direto", "oecd": "OECD", "fred": "FRED"}

# What each curve is: par yields and zero-coupon (spot) yields differ by tens of bp at the long end of a steep curve, so a
# chart mixing them says so. Fed and FRED: Treasury par curve (H.15). ECB: spot rates (the backend's default
# yield_curve_type). Bundesbank BBSIS ZST: "method by Svensson", a fitted zero-coupon curve. BoE: nominal spot curve.
# The rest are yields of actual bonds (MoF JGB, benchmark bonds, Tesouro buy rates); OECD: monthly averages.
CURVE_KIND = {"fed": "par", "fred": "par", "ecb": "zero-coupon", "buba": "zero-coupon", "boe": "zero-coupon", "mof": "bond yields",
              "boc": "bond yields", "riks": "bond yields", "tesouro": "bond yields", "oecd": "monthly averages"}


def curve_label(code):
    """'Bundesbank (zero-coupon)': the source and what kind of curve it publishes."""
    return f"{CURVE_SHORT.get(code, code)} ({CURVE_KIND[code]})" if code in CURVE_KIND else CURVE_SHORT.get(code, code)


FRED_CURVE = [("DGS1MO", 1 / 12), ("DGS3MO", 0.25), ("DGS6MO", 0.5), ("DGS1", 1.0), ("DGS2", 2.0), ("DGS3", 3.0), ("DGS5", 5.0), ("DGS7", 7.0),
              ("DGS10", 10.0), ("DGS20", 20.0), ("DGS30", 30.0)]


def curve_source(code, country_label=""):
    detail = CURVE_SOURCES.get(code, code) + (f": {country_label}" if country_label else "")
    if code == "fred":
        detail += " (series " + ", ".join(i for i, _ in FRED_CURVE) + ")"
    return C.src(CURVE_SHORT.get(code, code), detail, CURVE_URLS.get(code, ""))


def _src_fred_us():
    """U.S. Treasury curve from FRED's constant-maturity series (the Fed's H.15 numbers): the alternative to the Fed route."""
    ser, _ = C._fred([i for i, _ in FRED_CURVE], C._curve_since().isoformat())
    out = {}
    for sid, yrs in FRED_CURVE:
        for d, v in ser.get(sid, []):
            out.setdefault(d, []).append((yrs, v))
    return {d: sorted(p) for d, p in out.items() if len(p) >= 6}


COUNTRY_OPTIONS = [{"label": v[0] + (" (monthly, 3M and 10Y only)" if v[1] == "oecd" else ""), "value": k} for k, v in COUNTRIES.items()]

PLAIN_COUNTRY_OPTIONS = [{"label": "Euro area" if k == "euro_area" else v[0], "value": k} for k, v in COUNTRIES.items()]

TENORS = [("m3", 0.25, "3M"), ("y1", 1, "1Y"), ("y2", 2, "2Y"), ("y5", 5, "5Y"), ("y10", 10, "10Y"), ("y30", 30, "30Y")]

CURVE_DAYS = 420                     # history kept per source: enough for "1 year ago" comparisons


def _curve_since():
    return dt.date.today() - dt.timedelta(days=CURVE_DAYS)


def _curve_fetch(code, ttl, run):
    """cached_disk for a curve source, where a file that changed shape (a renamed sheet, a new date format, a
    maintenance page instead of JSON) counts as the source failing, like a timeout: the last saved curves are served
    and curves() goes on with the other countries. Without this a parser's StopIteration or ValueError escaped
    curves() and blanked every country."""
    who = CURVE_SHORT.get(code, code)

    def guarded():
        try:
            return run()
        except C.CompositeError:
            raise
        except (KeyError, IndexError, ValueError, TypeError, AttributeError, StopIteration, zipfile.BadZipFile, ET.ParseError, csv.Error) as e:
            raise C.CompositeError(f"{who} answered in an unexpected format ({type(e).__name__}); its file may have changed.") from e
    return C.cached_disk(f"curve_{code}", ttl, guarded)


def _src_buba():
    """Germany: Bundesbank daily Svensson zero-coupon curve for listed Federal securities (0.5 to 30 years)."""
    keep = {0.5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30}

    def run():
        url = ("https://api.statistiken.bundesbank.de/rest/data/BBSIS/D.I.ZST.ZI.EUR.S1311.B.A604..R.A.A._Z._Z.A"
               f"?format=csv&lang=en&startPeriod={C._curve_since().isoformat()}")
        rows = list(csv.reader(io.StringIO(C._download(url, 90).decode("utf-8-sig", "replace"))))
        if not rows:
            raise C.CompositeError("Bundesbank returned nothing.")
        cols = {}
        for i, k in enumerate(rows[0]):
            m = re.search(r"\.A604\.R(\d+)(X|XX)\.", k)
            if m and not k.endswith("_FLAGS"):
                yrs = int(m.group(1)) if m.group(2) == "XX" else int(m.group(1)) / 10
                if yrs in keep:
                    cols[i] = float(yrs)
        out = {}
        for r in rows[1:]:
            if r and re.match(r"\d{4}-\d{2}-\d{2}$", r[0]):
                pts = [(y, v) for i, y in cols.items() if i < len(r) and (v := C._num_or_none(r[i])) is not None]
                if pts:
                    out[r[0]] = sorted(pts)
        if not out:
            raise C.CompositeError("Bundesbank returned no curve data.")
        return C._curve_store("buba", out)
    return C._curve_fetch("buba", 6 * 3600, run)


def _src_mof():
    """Japan: Ministry of Finance JGB yields by maturity (1 to 40 years), daily."""
    def run():
        out = {}
        base = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
        since = C._curve_since().isoformat()
        for url in (base + "historical/jgbcme_all.csv", base + "jgbcme.csv"):
            try:
                text = C._download(url, 60).decode("utf-8", "replace")
            except C.CompositeError:
                if url.endswith("all.csv"):
                    continue
                raise
            head = None
            for r in csv.reader(io.StringIO(text)):
                if r and r[0] == "Date":
                    head = [float(h[:-1]) if h.endswith("Y") and h[:-1].isdigit() else None for h in r]
                    continue
                if not head or not r or not re.match(r"\d{4}/\d{1,2}/\d{1,2}$", r[0]):
                    continue
                y, m, d = (int(x) for x in r[0].split("/"))
                iso = dt.date(y, m, d).isoformat()
                if iso < since:
                    continue
                pts = [(head[i], v) for i in range(1, min(len(r), len(head))) if head[i] and (v := C._num_or_none(r[i])) is not None]
                if pts:
                    out[iso] = sorted(pts)
        if not out:
            raise C.CompositeError("Japan's Ministry of Finance returned no JGB yields.")
        return C._curve_store("mof", out)
    return C._curve_fetch("mof", 6 * 3600, run)


BOC_SERIES = {"V80691342": 1 / 12, "V80691344": 0.25, "V80691345": 0.5, "V80691346": 1.0, "BD.CDN.2YR.DQ.YLD": 2.0, "BD.CDN.3YR.DQ.YLD": 3.0,
              "BD.CDN.5YR.DQ.YLD": 5.0, "BD.CDN.7YR.DQ.YLD": 7.0, "BD.CDN.10YR.DQ.YLD": 10.0, "BD.CDN.LONG.DQ.YLD": 30.0}


def _src_boc():
    """Canada: Bank of Canada Valet API, Government of Canada T-bills (1M-1Y) and benchmark bonds (2Y-long)."""
    def run():
        j = json.loads(C._download("https://www.bankofcanada.ca/valet/observations/" + ",".join(BOC_SERIES) + f"/json?start_date={C._curve_since().isoformat()}", 60))
        out, bills = {}, {}
        for o in sorted(j.get("observations") or [], key=lambda o: o["d"]):
            pts = {y: v for s, y in BOC_SERIES.items() if (v := C._num_or_none((o.get(s) or {}).get("v"))) is not None}
            for y in [y for y in pts if y < 2]:
                bills[y] = (o["d"], pts[y])
            if any(y >= 2 for y in pts):               # a bond day; T-bills are published a day later, so take the latest within 5 days
                for y, (bd, v) in bills.items():
                    if y not in pts and (dt.date.fromisoformat(o["d"]) - dt.date.fromisoformat(bd)).days <= 5:
                        pts[y] = v
                out[o["d"]] = sorted(pts.items())
        if not out:
            raise C.CompositeError("Bank of Canada returned no bond yields.")
        return C._curve_store("boc", out)
    return C._curve_fetch("boc", 6 * 3600, run)


RIKS_SERIES = {"SETB1MBENCHC": 1 / 12, "SETB3MBENCH": 0.25, "SETB6MBENCH": 0.5, "SETB12MBENCH": 1.0, "SEGVB2YC": 2.0, "SEGVB5YC": 5.0, "SEGVB7YC": 7.0, "SEGVB10YC": 10.0}


def _src_riks():
    """Sweden: Riksbank SWEA API, Treasury bills (1M-12M) and government bonds (2Y-10Y)."""
    def run():
        by_date = {}
        since = (dt.date.today() - dt.timedelta(days=364)).isoformat()      # the API serves at most one year per call
        for group in (6, 7):                               # two calls: the keyless API allows only a few per minute
            for r in json.loads(C._download(f"https://api.riksbank.se/swea/v1/Observations/ByGroup/{group}/{since}", 60)) or []:
                y = RIKS_SERIES.get(r.get("seriesId"))
                if y is not None and r.get("value") is not None:
                    by_date.setdefault(r["date"], []).append((y, float(r["value"])))
        out = {d: sorted(p) for d, p in by_date.items() if any(y >= 2 for y, _ in p)}
        if not out:
            raise C.CompositeError("Riksbank returned no yields.")
        return C._curve_store("riks", out)
    return C._curve_fetch("riks", 6 * 3600, run)


def _xlsx_rows(zf, sheet_name):
    """[(row number, {column letters: value})] of one sheet of an .xlsx (standard library only)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    sid = next((s.get("{%s}id" % ns["r"]) for s in wb.find("m:sheets", ns) if s.get("name") == sheet_name), None)
    if not sid:
        raise C.CompositeError(f"Sheet '{sheet_name}' not found.")
    path = "xl/" + target[sid].lstrip("/").replace("xl/", "")
    strings = []
    if "xl/sharedStrings.xml" in zf.namelist():
        strings = ["".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])) for si in ET.fromstring(zf.read("xl/sharedStrings.xml"))]
    out = []
    for row in ET.fromstring(zf.read(path)).find("m:sheetData", ns):
        cells = {}
        for c in row:
            v = c.find("m:v", ns)
            if v is None or v.text is None:
                continue
            col = re.match(r"[A-Z]+", c.get("r")).group(0)
            cells[col] = strings[int(v.text)] if c.get("t") == "s" else v.text
        out.append((int(row.get("r")), cells))
    return out


def _src_boe():
    """United Kingdom: Bank of England nominal spot (zero-coupon) gilt curve, daily, from its current-month file;
    earlier dates come from the saved history and, for 5, 10 and 20 years, from the Bank's statistical database."""
    want_short, want_long = {0.25, 0.5}, {1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40}

    def sheet(zf, name, wanted):
        rows = C._xlsx_rows(zf, name)
        years = next((cells for n, cells in rows if (cells.get("A") or "").strip().lower() == "years:"), {})
        cols = {}
        for col, v in years.items():
            y = C._num_or_none(v)
            if col != "A" and y is not None and any(abs(y - w) < 1e-3 for w in wanted):
                cols[col] = round(y * 4) / 4 if y >= 1 else round(y, 2)
        out = {}
        for n, cells in rows:
            serial = C._num_or_none(cells.get("A"))
            if serial is None or serial < 30000:
                continue
            iso = (dt.date(1899, 12, 30) + dt.timedelta(days=int(serial))).isoformat()
            pts = [(y, v) for col, y in cols.items() if (v := C._num_or_none(cells.get(col))) is not None]
            if pts:
                out[iso] = pts
        return out

    def run():
        out = {}
        try:      # partial history: 5, 10 and 20-year zero-coupon yields (Bank of England database)
            start = C._curve_since().strftime("%d/%b/%Y")
            text = C._download("https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?csv.x=yes&Datefrom=" + start +
                             "&Dateto=now&SeriesCodes=IUDSNZC,IUDMNZC,IUDLNZC&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N", 60).decode("utf-8", "replace")
            for r in csv.reader(io.StringIO(text)):
                if len(r) == 4 and re.match(r"\d{2} \w{3} \d{4}$", r[0]):
                    iso = dt.datetime.strptime(r[0], "%d %b %Y").date().isoformat()
                    out[iso] = [(y, v) for y, s in zip((5.0, 10.0, 20.0), r[1:]) if (v := C._num_or_none(s)) is not None]
        except C.CompositeError as e:
            C.partial_from(e, "Bank of England database (days before this month)")
        z = zipfile.ZipFile(io.BytesIO(C._download("https://www.bankofengland.co.uk/-/media/boe/files/statistics/yield-curves/latest-yield-curve-data.zip", 60)))
        name = next(n for n in z.namelist() if "Nominal daily" in n)
        x = zipfile.ZipFile(io.BytesIO(z.read(name)))
        short, long_ = sheet(x, "3. spot, short end", want_short), sheet(x, "4. spot curve", want_long)
        for d in set(short) | set(long_):
            out[d] = sorted(dict(short.get(d, []) + long_.get(d, [])).items())
        if not out:
            raise C.CompositeError("Bank of England returned no curve data.")
        return C._curve_store("boe", out)
    return C._curve_fetch("boe", 6 * 3600, run)


def _src_tesouro():
    """Brazil: rates of the fixed-rate Treasury bonds sold to the public (Tesouro Direto prefixado LTN / NTN-F), daily.
    The file holds all history (about 15 MB), so it is fetched at most once a day."""
    def run():
        text = C._download("https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3/resource/"
                         "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/precotaxatesourodireto.csv", 120, ua=C.BROWSER_UA).decode("latin-1", "replace")
        # (open data under ODbL; the portal's firewall resets connections from non-browser user agents)
        since = C._curve_since()
        out = {}
        for r in csv.reader(io.StringIO(text), delimiter=";"):
            if len(r) < 5 or not r[0].startswith("Tesouro Prefixado"):
                continue
            try:
                venc = dt.datetime.strptime(r[1], "%d/%m/%Y").date()
                base = dt.datetime.strptime(r[2], "%d/%m/%Y").date()
            except ValueError:
                continue
            if base < since or venc <= base:
                continue
            rate = C._num_or_none(r[3]) or C._num_or_none(r[4])
            if rate:
                out.setdefault(base.isoformat(), {})[round((venc - base).days / 365.25, 2)] = rate
        out = {d: sorted(p.items()) for d, p in out.items()}
        if not out:
            raise C.CompositeError("Tesouro Direto returned no fixed-rate bond rates.")
        return C._curve_store("tesouro", out)
    return C._curve_fetch("tesouro", 20 * 3600, run)


def _src_oecd(country):
    """Monthly 3-month and 10-year rates for one country (OECD), as two-point 'curves' by month."""
    short, long_ = C._oecd_latest("short"), C._oecd_latest("long")
    by_key = {C._oecd_key(n): n for n in set(short) | set(long_)}
    name = by_key.get(C.OECD_ALIAS.get(country, country)) or by_key.get(country)
    out = {}
    for y, ser in ((0.25, short.get(name) or []), (10.0, long_.get(name) or [])):
        for d, v in ser:
            out.setdefault(d, []).append((y, v))
    return {d: sorted(p) for d, p in out.items()}


HIST_SOURCES = {"buba": _src_buba, "mof": _src_mof, "boc": _src_boc, "riks": _src_riks, "boe": _src_boe, "tesouro": _src_tesouro}


def _backend_curve(source, dates):
    d = ",".join(x.isoformat() for x in dates) if dates else None
    rows = C.api(C.V5, "fixedincome/government/yield_curve", provider="federal_reserve" if source == "fed" else "ecb", date=d)
    out = {}
    for r in rows or []:
        if r.get("rate") is not None and r.get("maturity_years") is not None:
            out.setdefault(str(r.get("date"))[:10], []).append((float(r["maturity_years"]), round(float(r["rate"]) * 100, 4)))
    return {k: sorted(v) for k, v in out.items()}
