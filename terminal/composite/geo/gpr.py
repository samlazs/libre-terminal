"""Caldara-Iacoviello Geopolitical Risk Index fetcher and table view."""

from __future__ import annotations

import composite as C

def _gpr():
    """Caldara & Iacoviello geopolitical risk: daily (D_*) and monthly incl. 44 countries (M_*), kept 12 hours."""
    def run():
        import xlrd
        out = {}
        wb = xlrd.open_workbook(file_contents=C._download("https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"))
        sh = wb.sheet_by_index(0)
        idx = {str(h): i for i, h in enumerate(sh.row_values(0))}
        cols = [c for c in ("GPRD", "GPRD_ACT", "GPRD_THREAT", "GPRD_MA7", "GPRD_MA30") if c in idx]
        for c in cols:
            out["D_" + c] = []
        for r in range(1, sh.nrows):
            row = sh.row_values(r)
            day = str(row[idx["DAY"]]).split(".")[0]
            if len(day) != 8 or day < "20000101":
                continue
            d = f"{day[:4]}-{day[4:6]}-{day[6:]}"
            for c in cols:
                if isinstance(row[idx[c]], float):
                    out["D_" + c].append([d, round(row[idx[c]], 3)])
        for c in ("GPRD_ACT", "GPRD_THREAT"):
            if out.get("D_" + c):
                out[f"D_{c}_MA30"] = [[d, round(v, 3)] for d, v in C._rolling(out["D_" + c], 30)]
        wb = xlrd.open_workbook(file_contents=C._download("https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls"))
        sh = wb.sheet_by_index(0)
        hdr = [str(h) for h in sh.row_values(0)]
        keep = [i for i, h in enumerate(hdr) if h in ("GPR", "GPRT", "GPRA") or h.startswith("GPRC_")]
        for i in keep:
            out["M_" + hdr[i]] = []
        for r in range(1, sh.nrows):
            row = sh.row_values(r)
            if not isinstance(row[0], float):
                continue
            d = xlrd.xldate_as_datetime(row[0], wb.datemode).date()
            if d.year < 1985:
                continue
            for i in keep:
                if isinstance(row[i], float) and row[i] == row[i]:
                    out["M_" + hdr[i]].append([d.replace(day=1).isoformat(), round(row[i], 4)])
        if not out.get("D_GPRD"):
            raise C.CompositeError("The geopolitical risk index file could not be read.")
        return out
    return C.cached_disk("gpr", 12 * 3600, run)


GPR_NAMES = {"RUS": "Russia", "UKR": "Ukraine", "ISR": "Israel", "CHN": "China", "TWN": "Taiwan", "USA": "United States", "SAU": "Saudi Arabia", "TUR": "Turkey", "EGY": "Egypt",
             "IND": "India", "KOR": "South Korea", "JPN": "Japan", "VEN": "Venezuela", "GBR": "United Kingdom", "DEU": "Germany", "FRA": "France", "ITA": "Italy", "ESP": "Spain",
             "POL": "Poland", "FIN": "Finland", "SWE": "Sweden", "NOR": "Norway", "DNK": "Denmark", "NLD": "Netherlands", "BEL": "Belgium", "CHE": "Switzerland", "HUN": "Hungary",
             "PRT": "Portugal", "BRA": "Brazil", "MEX": "Mexico", "ARG": "Argentina", "CHL": "Chile", "COL": "Colombia", "PER": "Peru", "CAN": "Canada", "AUS": "Australia",
             "IDN": "Indonesia", "MYS": "Malaysia", "PHL": "Philippines", "THA": "Thailand", "VNM": "Vietnam", "HKG": "Hong Kong", "ZAF": "South Africa", "TUN": "Tunisia"}


def gpr_table(**_):
    """Geopolitical risk by country: latest month against its own recent and long-run levels."""
    g = C._gpr()
    rows = []
    for key in ["M_GPR"] + sorted(k for k in g if k.startswith("M_GPRC_")):
        pts = g.get(key) or []
        if len(pts) < 24:
            continue
        vals = [v for _, v in pts]
        last, m3 = vals[-1], sum(vals[-3:]) / 3
        y12 = sum(vals[-13:-1]) / 12
        lr = [v for d, v in pts if d >= "2000-01-01"]
        lr = sum(lr) / len(lr)
        rows.append({"country": "World (global index)" if key == "M_GPR" else GPR_NAMES.get(key[7:], key[7:]), "latest": last, "avg_3m": m3,
                     "vs_12m": last / y12 - 1 if y12 else None, "vs_long_run": m3 / lr - 1 if lr else None, "as_of": pts[-1][0][:7]})
    rows.sort(key=lambda r: (r["country"] != "World (global index)", -(r["vs_long_run"] or -9)))
    return {"results": rows}
