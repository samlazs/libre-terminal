"""Yield-curve source parsers on small recorded samples (Bundesbank, Japan MoF, Tesouro Direto) and the curve store."""

import datetime as dt
import json

from _common import check, done

import composite as c

today = dt.date.today()
d1, d2 = (today - dt.timedelta(days=3)).isoformat(), (today - dt.timedelta(days=2)).isoformat()
SAMPLES = {
    # Bundesbank: tenor codes R005X = 0.5 years, R01XX = 1 year; _FLAGS columns and tenors not kept are skipped
    "bundesbank": ("Date,BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R005X.R.A.A._Z._Z.A,BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R005X.R.A.A._Z._Z.A_FLAGS,"
                   "BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R01XX.R.A.A._Z._Z.A,BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R11XX.R.A.A._Z._Z.A,"
                   "BBSIS.D.I.ZST.ZI.EUR.S1311.B.A604.R10XX.R.A.A._Z._Z.A\n"
                   "unit,,,,,\n"
                   f"{d1},1.91,x,1.95,2.4,2.61\n{d2},.,,1.97,2.41,2.63\n"),
    # Japan MoF: header row "Date,1Y,...", dates as YYYY/M/D, "-" for no value
    "mof": ("Interest Rate,,,\nDate,1Y,2Y,10Y,40Y\n"
            f"{today.year - 20}/1/4,0.1,0.2,1.5,\n"
            f"{(today - dt.timedelta(days=3)).strftime('%Y/%m/%d').replace('/0', '/')},0.71,0.92,1.69,-\n"),
    # Tesouro Direto: semicolons, dd/mm/yyyy, comma decimals; only fixed-rate ("Prefixado") bonds not yet matured
    "tesouro": ("Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha\n"
                f"Tesouro Prefixado;01/01/{today.year + 3};{(today - dt.timedelta(days=3)).strftime('%d/%m/%Y')};13,45;13,57\n"
                f"Tesouro Prefixado com Juros Semestrais;01/01/{today.year + 10};{(today - dt.timedelta(days=3)).strftime('%d/%m/%Y')};;13,90\n"
                f"Tesouro Selic;01/03/{today.year + 2};{(today - dt.timedelta(days=3)).strftime('%d/%m/%Y')};0,05;0,10\n"
                f"Tesouro Prefixado;01/01/2020;01/01/2020;9,0;9,1\n"),
}
saved = c._download, c.cached_disk
try:
    c.cached_disk = lambda name, ttl, fn: fn()
    c._download = lambda url, *a, **k: SAMPLES["bundesbank"].encode()
    buba = c._src_buba()
    check(buba[d1] == [(0.5, 1.91), (1.0, 1.95), (10.0, 2.61)], "Bundesbank: 0.5Y and 1Y codes, 11Y dropped, flags skipped")
    check(buba[d2] == [(1.0, 1.97), (10.0, 2.63)], "Bundesbank: missing value '.' skipped")

    c._download = lambda url, *a, **k: SAMPLES["mof"].encode()
    mof = c._src_mof()
    day = (today - dt.timedelta(days=3)).isoformat()
    check(list(mof) == [day], "MoF: rows older than the curve window dropped")
    check(mof[day] == [(1.0, 0.71), (2.0, 0.92), (10.0, 1.69)], "MoF: '-' skipped, tenors from the header")

    c._download = lambda url, *a, **k: SAMPLES["tesouro"].encode("latin-1")
    tes = c._src_tesouro()
    check(list(tes) == [day], "Tesouro: matured bonds dropped")
    pts = dict(tes[day])
    base = today - dt.timedelta(days=3)
    yrs = [round((dt.date(today.year + n, 1, 1) - base).days / 365.25, 2) for n in (3, 10)]
    check(sorted(pts) == yrs, "Tesouro: maturities in years from the base date")
    check(pts[min(pts)] == 13.45 and pts[max(pts)] == 13.90, "Tesouro: comma decimals; sell rate when the buy rate is empty; Selic bonds ignored")
finally:
    c._download, c.cached_disk = saved

# ---- curve store, per maturity: the newest answer wins for every maturity it has (a corrected value replaces the
# saved one even when that answer is partial), maturities it lacks keep their saved value; history older than 800 days dropped
old_day = (today - dt.timedelta(days=900)).isoformat()
(c.CACHE_DIR).mkdir(parents=True, exist_ok=True)
(c.CACHE_DIR / "curvestore_t.json").write_text(json.dumps({d1: [[1, 2.0], [2, 2.1], [10, 2.5]], old_day: [[1, 1.0]]}), encoding="utf-8")
got = c._curve_store("t", {d1: [(1, 2.2)], d2: [(1, 2.3), (2, 2.4)]})
check([tuple(p) for p in got[d1]] == [(1, 2.2), (2, 2.1), (10, 2.5)], "partial answer: its corrected 1Y (2.0 -> 2.2) wins, the saved 2Y and 10Y stay")
check([tuple(p) for p in got[d2]] == [(1, 2.3), (2, 2.4)] and old_day not in got, "new days added, days older than 800 dropped")
got = c._curve_store("t", {d1: [(1, 2.2), (2, 2.3), (10, 2.6)]})
check([tuple(p) for p in got[d1]] == [(1, 2.2), (2, 2.3), (10, 2.6)], "a full answer overwrites every maturity")
check(json.loads((c.CACHE_DIR / "curvestore_t.json").read_text(encoding="utf-8"))[d1] == [[1, 2.2], [2, 2.3], [10, 2.6]], "saved to disk")
check(not list(c.CACHE_DIR.glob("curvestore_t*.tmp")), "no temporary file left behind")

# ---- a source file that changed shape is a source failure (audit C2): the last saved copy is served, else a plain
# CompositeError, so curves() goes on with the other countries instead of failing for all of them
import io
import zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("GLC Real daily data current month.xlsx", b"not the nominal file")      # BoE renamed or dropped the nominal file
saved_dl = c._download
try:
    c._download = lambda url, *a, **k: buf.getvalue() if url.endswith(".zip") else b""
    (c.CACHE_DIR / "curve_boe.json").write_text(json.dumps({"t": 0, "v": {d1: [[5.0, 4.1], [10.0, 4.6]]}}), encoding="utf-8")
    c._cache.clear(); c._backoff.clear()
    check(c._src_boe() == {d1: [[5.0, 4.1], [10.0, 4.6]]}, "BoE file without its nominal sheet: the saved curve is served")
    (c.CACHE_DIR / "curve_boe.json").unlink()
    c._cache.clear(); c._backoff.clear()
    try:
        c._src_boe()
        check(False, "BoE format change without a saved copy raises")
    except c.CompositeError as e:
        check("unexpected format" in str(e) and "Bank of England" in str(e), "... a CompositeError naming the source (was StopIteration)")
    page = b"<html><body>Valet is down for maintenance</body></html>"
    c._download = lambda url, *a, **k: page
    c._cache.clear(); c._backoff.clear()
    try:
        c._src_boc()
        check(False, "BoC maintenance page raises")
    except c.CompositeError as e:
        check("Bank of Canada" in str(e), "BoC maintenance page instead of JSON: CompositeError (was JSONDecodeError)")
    c._cache.clear(); c._backoff.clear()
    c._download = lambda url, *a, **k: page if "valet" in url else SAMPLES["bundesbank"].encode()
    got = c.curves(["canada", "germany"])
    check(not got["canada"] and got["germany"], "curves(): Canada's broken file leaves Germany's curve in place")
finally:
    c._download = saved_dl
    c._cache.clear(); c._backoff.clear()

# ---- interpolation between tenors, never extrapolation
pts = [(1.0, 2.0), (2.0, 3.0), (10.0, 4.0)]
check(abs(c._interp(pts, 1.5) - 2.5) < 1e-9 and c._interp(pts, 2.0) == 3.0, "interpolates between and hits exact tenors")
check(c._interp(pts, 30.0) is None and c._interp(pts, 0.25) is None, "never extrapolates")
done("test_curves")
