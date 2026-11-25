"""FRED, EIA, Yahoo/derived macro series fetchers and source descriptions."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path
import fred_public

import composite as C

def _fred(ids, start):
    """({id: [[date, value], ...]}, {id: metadata}) from FRED via the V5 backend, kept 3 hours on disk.

    The disk copy is named by start *year*, so it holds the whole year (fetched from 1 January) and each answer is cut
    to the asked start: a 1Y range chip (2025-11-16) and "since 2025" then share one copy without either getting the
    other's start."""
    ids = list(dict.fromkeys(ids))
    name = C.disk_name("fred_", "_".join(ids), start[:4])

    def run():
        j = C.api_full(C.V5, "economy/fred_series", symbol=",".join(ids), provider="fred", start_date=start[:4] + "-01-01")
        out = {i: [] for i in ids}
        for r in j.get("results") or []:
            d = str(r.get("date"))[:10]
            for i in ids:
                vf = C.num(r.get(i))
                if vf is not None:
                    out[i].append([d, vf])
        if not any(out.values()):
            raise C.CompositeError("FRED returned no data for: " + ", ".join(ids))
        meta = (j.get("extra") or {}).get("results_metadata") or {}
        return {"series": out, "meta": {i: {k: (meta.get(i) or {}).get(k) for k in ("title", "units", "frequency")} for i in ids}}
    got = C.cached_disk(name, 3 * 3600, run)
    cut = start[:10]
    return {i: [p for p in pts if p[0] >= cut] for i, pts in got["series"].items()}, got["meta"]


def _eia_key():
    try:
        k = json.loads((Path.home() / ".openbb_platform" / "user_settings.json").read_text(encoding="utf-8")).get("credentials", {}).get("eia_api_key")
    except (OSError, ValueError):
        k = None
    k = k or os.environ.get("EIA_API_KEY")
    if not k:
        raise C.CompositeError("Missing credential 'eia_api_key'. Add the free EIA key with Setup-Keys.cmd (or setup-keys.sh).")
    return k


def _eia_one(sid):
    def run():
        url = f"https://api.eia.gov/v2/seriesid/{sid}?api_key={C._eia_key()}&sort[0][column]=period&sort[0][direction]=desc&length=5000"
        last = None
        for attempt in range(3):                       # EIA's gateway answers 504 on a cold query, then serves it fast
            try:
                j = C.http_json(url, timeout=40)
                break
            except C.CompositeError as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        else:
            raise C.CompositeError(f"EIA did not answer for {sid} ({last}).")
        pts, title, units = [], "", ""
        for r in (j.get("response") or {}).get("data") or []:
            v = C.num(r.get("value"))
            if v is None:
                continue
            pts.append([C._period_date(r.get("period")), v])
            title = title or r.get("series-description") or r.get("seriesDescription") or ""
            units = units or r.get("unit") or r.get("units") or ""
        if not pts:
            raise C.CompositeError(f"EIA returned no data for {sid}.")
        pts.sort()
        meta = {"title": title, "units": units}
        try:
            total = int((j.get("response") or {}).get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total > len((j.get("response") or {}).get("data") or []):
            meta["capped_from"] = pts[0][0]          # one request returns the newest 5,000 rows (HANDOFF P2: said, not hidden)
        return {"pts": pts, "meta": meta}
    return C.cached_disk("eia_" + re.sub(r"[^A-Za-z0-9]+", "_", sid), 3 * 3600, run)


CALCS = {   # name -> (Yahoo tickers, formula on their closes)
    "CRACK321": (("RB=F", "HO=F", "CL=F"), lambda rb, ho, cl: (2 * rb * 42 + ho * 42) / 3 - cl),
    "DIESEL_CRACK": (("HO=F", "BZ=F"), lambda ho, bz: ho * 42 - bz),
    "GASOLINE_CRACK": (("RB=F", "BZ=F"), lambda rb, bz: rb * 42 - bz),
    "BRENT_WTI": (("BZ=F", "CL=F"), lambda bz, cl: bz - cl),
    "TTF_USD": (("TTF=F", "EURUSD=X"), lambda ttf, fx: ttf * fx / 3.412),      # EUR/MWh -> $/MMBtu
}


def _series(ids, start):
    """Fetch any mix of series ids (see above) -> ({id: [[date, value]]}, {id: meta})."""
    data, meta = {}, {}

    def put(i, pts, m=None, default_ma=None):
        mod = i.partition("@")[2]
        days = int(mod[2:]) if mod.startswith("ma") and mod[2:].isdigit() else default_ma
        if days and pts:
            pts = C._rolling(pts, days)
        data[i] = [p for p in pts if p[0] >= start]
        meta[i] = m or {}
        if meta[i].get("capped_from") and meta[i]["capped_from"] > start:
            meta[i] = dict(meta[i], truncated=f"EIA sends the latest 5,000 observations: {i.partition('@')[0][4:]} starts {meta[i]['capped_from']}")

    base = {i: i.partition("@")[0] for i in ids}
    plain = [i for i in ids if ":" not in base[i]]
    diffs = [i for i in ids if base[i].startswith("DIFF:")]          # DIFF:A-B = FRED series A minus B (e.g. BTP-Bund)
    fred_ids = list(dict.fromkeys([base[i] for i in plain] + [p for i in diffs for p in base[i][5:].split("-", 1)]))
    if fred_ids:
        d, m = C._fred_any(fred_ids, start)
        for i in plain:
            put(i, d.get(base[i], []), m.get(base[i]))
        for i in diffs:
            a, b = base[i][5:].split("-", 1)
            mb = dict(d.get(b, []))
            put(i, [[x, v - mb[x]] for x, v in d.get(a, []) if x in mb])
    yf = [i for i in ids if base[i].startswith("YF:")]
    calc = [i for i in ids if base[i].startswith("CALC:")]
    for i in calc:
        if base[i][5:] not in CALCS:
            raise C.CompositeError(f"Unknown derived series '{base[i]}'.")
    need = {base[i][3:] for i in yf} | {s for i in calc for s in CALCS[base[i][5:]][0]}
    if need:
        h = C.history(sorted(need), dt.date.fromisoformat(start) - dt.timedelta(days=10))
        for i in yf:
            put(i, [[d, c] for d, c in h.get(base[i][3:], [])], {"title": base[i][3:]})
        for i in calc:
            syms, fn = CALCS[base[i][5:]]
            maps = [dict(h.get(s, [])) for s in syms]
            dates = sorted(set.intersection(*(set(m) for m in maps))) if all(maps) else []
            put(i, [[d, fn(*(m[d] for m in maps))] for d in dates])
    eia = [i for i in ids if base[i].startswith("EIA:")]
    if eia:
        with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
            got = dict(zip(eia, ex.map(lambda i: C._eia_one(base[i][4:]), eia)))
        for i in eia:
            put(i, got[i]["pts"], got[i]["meta"])
    for i in ids:
        if base[i].startswith("GPR:"):
            put(i, C._gpr().get(base[i][4:], []))
        elif base[i].startswith("PW:"):
            put(i, C._chokepoints().get(base[i][3:], []), default_ma=7)
    return data, meta


def _fred_any(ids, start):
    """FRED series through the backend (needs the free FRED key); without a key, FRED's public CSV download instead."""
    try:
        return C._fred(ids, start)
    except C.CompositeError as e:
        first = e
    try:
        got = {ids[0]: C._fred_csv(ids[0], start)}             # one probe first: a network that blocks FRED fails once, not per series
    except C.CompositeError as e:
        if "Missing credential" in str(first) or "api_key" in str(first):
            raise C.CompositeError(str(e)) from None
        raise first from None
    keyless = "Missing credential" in str(first) or "api_key" in str(first)
    C.note_fallback("FRED's public download", "no FRED key is set" if keyless else "the data service's FRED route did not answer")
    with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
        def one(sid):
            try:
                return C._fred_csv(sid, start)
            except C.CompositeError as e:
                C.partial_from(e, f"FRED {sid}")
                return []
        got.update(zip(ids[1:], ex.map(one, ids[1:])))
    return got, {}


def _eia_url(sid):
    parts = sid.split(".")
    if len(parts) == 3 and parts[0] == "PET":
        return f"https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={parts[1]}&f={parts[2]}"
    if len(parts) == 3 and parts[0] == "NG":
        return f"https://www.eia.gov/dnav/ng/hist/{parts[1].lower()}{parts[2].lower()}.htm"
    if parts[0] == "STEO":
        return "https://www.eia.gov/outlooks/steo/"
    if parts[0] == "INTL":
        return "https://www.eia.gov/international/data/world"
    return "https://www.eia.gov/opendata/"


def series_source(sid, meta=None):
    """{name, detail, url} for one series id of a curated chart (see the id syntax above MACRO_SETS)."""
    base, _, mod = str(sid).partition("@")
    ma = f", {mod[2:]}-day average" if mod.startswith("ma") and mod[2:].isdigit() else ""
    title = str((meta or {}).get("title") or "").strip()
    if base.startswith("EIA:"):
        return C.src("U.S. EIA", base[4:] + (f": {title}" if title else "") + ma, C._eia_url(base[4:]))
    if base.startswith("YF:"):
        return C.yahoo_source([base[3:]], "daily close" + ma)
    if base.startswith("CALC:"):
        syms = CALCS.get(base[5:], ((),))[0]
        return C.src("Yahoo Finance", "calculated from " + ", ".join(syms) + ": " + C.CALC_TEXT.get(base[5:], base[5:]), C.YAHOO_URL)
    if base.startswith("DIFF:"):
        a, _, b = base[5:].partition("-")
        return C.src("FRED", f"calculated: {a} minus {b}", f"https://fred.stlouisfed.org/series/{a}")
    if base.startswith("GPR:"):
        return C.src("Caldara & Iacoviello", f"Geopolitical Risk Index ({base[4:]})" + ma, "https://www.matteoiacoviello.com/gpr.htm")
    if base.startswith("PW:"):
        cp, _, field = base[3:].rpartition(":")
        return C.src("IMF PortWatch", f"{cp}: {field.replace('_', ' ')}, 7-day average", "https://portwatch.imf.org/")
    return C.src("FRED", base + (f": {title}" if title else "") + ma, f"https://fred.stlouisfed.org/series/{base}")


def _fred_csv(sid, start):
    """A FRED series without an API key (FRED's public CSV download, terminal/fred_public.py). The download starts
    on January 1 of the start year (one cache file per series and year) and is cut to `start` here."""
    def run():
        try:
            rows = fred_public.observations(sid, start=start[:4] + "-01-01")
        except fred_public.FredPublicError as e:
            raise C.CompositeError(f"FRED has no series '{sid}'." if e.missing else str(e),
                                   kind="not_covered" if e.missing else e.kind, retry_after=e.retry_after) from None
        return [[d, x] for d, v in rows if (x := C.num(v)) is not None]
    pts = C.cached_disk("fredcsv_" + re.sub(r"[^A-Za-z0-9]+", "_", sid) + "_" + start[:4], 3 * 3600, run)
    return [p for p in pts if p[0] >= start[:10]]


def _fred_points(sid, start):
    try:
        d, _ = C._fred([sid], start)
        if d.get(sid):
            return d[sid]
    except C.CompositeError:
        pass
    try:
        return C._fred_csv(sid, start)
    except C.CompositeError:
        raise C.CompositeError(f"FRED series {sid} could not be loaded: no FRED key is set and FRED's public download did not answer. Add the free FRED key with Setup-Keys.cmd (or setup-keys.sh).") from None
