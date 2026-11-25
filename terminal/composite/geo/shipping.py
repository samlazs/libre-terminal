"""IMF PortWatch chokepoint shipping volume fetcher and monitor view."""

from __future__ import annotations

import datetime as dt

import composite as C

# vessels_*: ships per day; capacity_tanker: estimated tanker cargo in metric tons per day (openbb_imf model, audit C10), not ships: kept for a tonnage view, shown nowhere yet
PORTWATCH_FIELDS = ("vessels_total", "vessels_tanker", "vessels_container", "vessels_dry_bulk", "capacity_tanker")


def _chokepoints():
    """{"<chokepoint>:<field>": [[date, ships per day]]} since 2023 from IMF PortWatch, kept 12 hours."""
    def run():
        rows = C.api(C.V5, "economy/shipping/chokepoint_volume", provider="imf", start_date="2023-01-01")
        out = {}
        for r in rows or []:
            name, d = r.get("chokepoint"), str(r.get("date"))[:10]
            for f in PORTWATCH_FIELDS:
                v = C.num(r.get(f))
                if name and v is not None:
                    out.setdefault(f"{name}:{f}", []).append([d, v])
        for k in out:
            out[k].sort()
        if not out:
            raise C.CompositeError("IMF PortWatch returned no chokepoint data.")
        return out
    return C.cached_disk("chokepoints", 12 * 3600, run)


# ---------------------------------------------------------------- monitors
CHOKEPOINT_ORDER = ["Strait of Hormuz", "Bab el-Mandeb Strait", "Suez Canal", "Cape of Good Hope", "Bosporus Strait", "Panama Canal", "Malacca Strait", "Taiwan Strait",
                    "Gibraltar Strait", "Dover Strait", "Oresund Strait", "Korea Strait"]


def chokepoint_monitor(**_):
    """Ships per day through each chokepoint: last 30 days vs a year earlier and vs before the Red Sea attacks (Jan-Oct 2023)."""
    c = C._chokepoints()
    names = sorted({k.split(":")[0] for k in c}, key=lambda n: (CHOKEPOINT_ORDER.index(n) if n in CHOKEPOINT_ORDER else 99, n))
    rows = []
    for n in names:
        row = {"chokepoint": n}
        for f, lab in (("vessels_total", "total"), ("vessels_tanker", "tanker"), ("vessels_container", "container"), ("vessels_dry_bulk", "dry_bulk")):
            pts = c.get(f"{n}:{f}", [])
            if not pts:
                continue
            end = dt.date.fromisoformat(pts[-1][0])

            def avg(a, b):
                vals = [v for d, v in pts if a < d <= b]
                return sum(vals) / len(vals) if vals else None
            now = avg((end - dt.timedelta(days=30)).isoformat(), end.isoformat())
            ya = avg((end - dt.timedelta(days=395)).isoformat(), (end - dt.timedelta(days=365)).isoformat())
            row[lab] = round(now, 1) if now is not None else None
            row[lab + "_1y"] = (now / ya - 1) if now is not None and ya else None
            if lab in ("total", "tanker"):
                pre = avg("2022-12-31", "2023-10-31")
                row[lab + "_pre"] = (now / pre - 1) if now is not None and pre else None
            row["as_of"] = max(row.get("as_of") or "", pts[-1][0])     # the newest series of the row, not the last one read
        rows.append(row)
    return {"results": rows}
