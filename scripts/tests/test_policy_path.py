"""HANDOFF P3-3 policy path (terminal/policy_path.py & run_backend.build_ois_policy_path): periods, fresh pricing, fixings, chart & table."""

import datetime as dt
import importlib.util

from _common import ROOT, check, done

import composite as c
import policy_path as pp

TODAY = dt.date(2026, 11, 19)


def row(start, end, rate, date, trades=10):
    return {"start_date": start, "end_date": end, "rate": rate, "num_trades": trades, "as_of_date": date, "date": date}


_DEFAULT_UNDERLIERS = {
    "USD": "USD-Federal Funds-OIS Compound",
    "EUR": "EUR-EuroSTR-OIS Compound",
    "GBP": "GBP-SONIA-OIS Compound",
    "JPY": "JPY-TONA-OIS Compound",
    "CAD": "CAD-CORRA-OIS Compound",
    "AUD": "AUD-AONIA-OIS Compound",
}


def dtcc_rec(eff, exp, rate, day, ccy="USD", underlier=None):
    u = _DEFAULT_UNDERLIERS.get(ccy, "") if underlier is None else underlier
    r = {
        "UPI FISN": f"NA/Swap OIS {ccy}",
        "Action type": "NEWT",
        "Event type": "TRAD",
        "Notional currency-Leg 1": ccy,
        "Effective Date": eff,
        "Expiration Date": exp,
        "Fixed rate-Leg 1": str(rate),
        "Notional amount-Leg 1": "100,000,000",
        "Dissemination Timestamp": f"{day}T00:00:00Z",
    }
    if u:
        r["UPI Underlier Name"] = u
    return r


PATHS = {   # (currency, date param) -> backend rows
    ("USD", None): [row("2027-01-28", "2027-03-18", 3.50, "2026-11-18"), row("2026-12-10", "2027-01-28", 3.62, "2026-11-18", 50),
                    row("2027-04-29", "2027-06-17", 3.40, "2026-11-18"),
                    row("2026-12-21", "2027-01-21", 3.70, "2026-11-17", 3)],      # bespoke period over a meeting one: dropped
    ("USD", "2026-11-12"): [row("2026-12-10", "2027-01-28", 3.70, "2026-11-12"), row("2027-01-29", "2027-03-18", 3.55, "2026-11-12")],
    ("USD", "2026-10-19"): [row("2026-10-29", "2026-12-10", 3.90, "2026-10-17"), row("2026-12-11", "2027-01-28", 3.80, "2026-10-17"),
                            row("2027-01-28", "2027-03-18", 3.62, "2026-10-17"), row("2027-04-20", "2027-06-17", 3.45, "2026-10-17")],
    ("EUR", None): [row("2026-12-23", "2027-02-10", 2.681, "2026-11-18")],
    ("JPY", None): [row("2026-12-21", "2027-01-22", 1.42, "2026-11-18", 80), row("2027-01-25", "2027-03-18", 1.47, "2026-11-18", 30)],
    ("CAD", None): [row("2026-12-10", "2027-01-28", 2.71, "2026-11-18", 60), row("2027-01-28", "2027-03-04", 2.83, "2026-11-18", 25)],
    ("AUD", None): [row("2026-12-09", "2027-02-10", 4.86, "2026-11-18", 30), row("2027-02-10", "2027-03-24", 4.80, "2026-11-18", 40)],
}
FRED = {"EFFR": ("2026-11-18", 3.60), "DFEDTARL": ("2026-11-18", 3.50), "DFEDTARU": ("2026-11-18", 3.75)}
CFTC_FIX = {
    "EFFR": [{"date": "2026-11-18", "rate": 3.61}],
    "TONA": [{"date": "2026-11-18", "rate": 1.227}],
    "CORRA": [{"date": "2026-11-18", "rate": 2.54}],
    "AONIA": [{"date": "2026-11-18", "rate": 4.85}],
}
calls = []


def fake_api(base, path, currency=None, date=None, index=None, **_):
    calls.append((path, currency, date, index))
    if path == "cftc/historical_fixings":
        return CFTC_FIX.get(index, [])
    return PATHS.get((currency, date), [])


saved = c.api, c._policy_series, pp._fred_last
try:
    c.api = fake_api
    pp._fred_last = FRED.get
    c._policy_series = lambda: {
        "US": [("2026-11-01", 3.875), ("2026-11-10", 3.625)],
        "XM": [("2026-11-10", 2.0)],
        "JP": [("2026-11-10", 1.25)],
        "CA": [("2026-11-10", 2.50)],
        "AU": [("2026-11-10", 4.85)],
    }
    c._cache.clear()

    # ---- matching: same start, else nearest within 7 days
    past = pp.nodes_from_rows(PATHS[("USD", "2026-10-19")])[0]
    check(pp.match({"start": "2026-12-10"}, past)["rate"] == 3.80, "match: 1 day off is the same period")
    check(pp.match({"start": "2027-04-29"}, past) is None, "match: 9 days off is not the same period")
    check(pp.match({"start": "2027-01-28"}, []) is None, "match: no earlier path")

    # ---- meeting path: overlapping bespoke periods drop out, a missing meeting stays a gap
    mp = pp.meeting_path(pp.nodes_from_rows(PATHS[("USD", None)])[0])
    check([n["start"] for n in mp] == ["2026-12-10", "2027-01-28", "2027-04-29"], f"meeting path: {[n['start'] for n in mp]}")
    two_thin = [{"start": "2027-01-01", "end": "2027-02-01", "rate": 1, "trades": 4}, {"start": "2027-02-01", "end": "2027-03-01", "rate": 1, "trades": 4},
                {"start": "2027-01-10", "end": "2027-02-20", "rate": 1, "trades": 9}]
    check([n["trades"] for n in pp.meeting_path(two_thin)] == [9], "meeting path: more trades wins over two thin overlapping periods (9 > 4 + 4)")
    check(pp.priced_move(-12) == -48 and pp.priced_move(30) == 120 and pp.priced_move(25.39) == 102
          and pp.priced_move(16.46) == 66 and pp.priced_move(20.50) == 82 and pp.priced_move(1.59) == 6
          and pp.priced_move(-12.5) == -50 and str(pp.priced_move(-0.05)) == "0" and pp.priced_move(None) is None,
          "priced move: unrounded step / 25 bp, halves away from zero, no -0")
    check(pp.meeting_label("2026-12-10") == "10 Dec 2026", f"meeting label: {pp.meeting_label('2026-12-10')}")
    check([n["start"] for n in pp.ahead([{"start": "2026-11-01", "end": "2026-12-01"}, {"start": "2026-10-01", "end": "2026-11-19"}], TODAY)] == ["2026-11-19"],
          "ahead: a begun period starts today, a finished one is dropped")

    # ---- table: rows hand-computed
    t = pp.table(currency="usd", today=TODAY)
    r = t["results"]
    check(r[0] == {"meeting": "Policy rate (3.50–3.75%)", "start": None, "end": None, "rate": 3.625, "as_of": "2026-11-10"},
          f"table: first row is the BIS anchor with the Fed range: {r[0]}")
    check(r[1] == {"meeting": "Now (EFFR fixing)", "start": "2026-11-19", "end": "2026-12-10", "rate": 3.60, "vs_policy": -3, "as_of": "2026-11-18"},
          f"table: current period at the EFFR fixing: {r[1]}")
    check([x["start"] for x in r[2:]] == ["2026-12-10", "2027-01-28", "2027-04-29"], "table: meeting periods sorted by start, bespoke one gone")
    a, b, d = r[2:]
    check([(x["meeting"], x["step"], x["priced"], x["cum_bp"]) for x in (a, b, d)]
          == [("10 Dec 2026", 2, 8, 2), ("28 Jan 2027", -12, -48, -10), ("29 Apr 2027", None, None, -20)],
          f"table: step, priced move, cum_bp: {[(x['meeting'], x['step'], x['priced'], x['cum_bp']) for x in (a, b, d)]}")
    check((a["rate_1w"], a["chg_1w"], a["rate_1m"], a["chg_1m"], a["vs_policy"]) == (3.70, -8, 3.80, -18, -1), f"table row 1: {a}")
    check((b["rate_1w"], b["chg_1w"], b["rate_1m"], b["chg_1m"], b["vs_policy"]) == (3.55, -5, 3.62, -12, -13), f"table row 2: {b}")
    check((d["rate_1w"], d["chg_1w"], d["rate_1m"], d["chg_1m"], d["vs_policy"]) == (None, None, None, None, -23), f"table row 3 (no match): {d}")
    check(a["trades"] == 50 and a["as_of"] == "2026-11-18", "table: trades and as-of carried")
    check(("cftc/ois_policy_path", "USD", "2026-11-12", None) in calls and ("cftc/ois_policy_path", "USD", "2026-10-19", None) in calls,
          "table: 1W = today - 7 days, 1M = same day last month")
    check("stale" not in t and [s["name"] for s in t["sources"]] == ["CFTC", "BIS", "FRED"] and "EFFR" in t["sources"][0]["detail"], "table: sources, not stale")

    check(pp.bp(3.624, 3.62) == 0 and str(pp.bp(3.616, 3.62)) == "0" and pp.bp(3.625, 3.62) == 1 and pp.bp(3.615, 3.62) == -1,
          "bp: whole numbers, halves away from zero, no -0")

    # ---- JPY / CAD / AUD: fixings from cftc/historical_fixings give first-meeting step, priced move (unrounded), cum_bp, and current chart leg
    for ccy, exp_step, exp_priced, exp_cum in (("JPY", 19, 77, 19), ("CAD", 17, 68, 17), ("AUD", 1, 4, 1)):
        tj = pp.table(currency=ccy, today=TODAY)["results"]
        check(tj[0]["meeting"] == "Policy rate" and "fixing)" in tj[1]["meeting"], f"{ccy}: policy rate + fixing row")
        check((tj[2]["step"], tj[2]["priced"], tj[2]["cum_bp"]) == (exp_step, exp_priced, exp_cum),
              f"{ccy}: first meeting step={tj[2]['step']} priced={tj[2]['priced']} cum_bp={tj[2]['cum_bp']}")
    cj_jpy = pp.chart(currency="JPY", today=TODAY)
    check(cj_jpy["data"][0]["customdata"][1] == ["21 Dec 2026 → 22 Jan 2027", " · +19.3 bp (+77% of 25 bp)"],
          f"JPY chart hover shows 1-decimal step matching priced move: {cj_jpy['data'][0]['customdata'][1]}")
    cj = pp.chart(currency="CAD", today=TODAY)
    check(cj["data"][0]["x"][:2] == ["2026-11-19", "2026-12-10"] and cj["data"][0]["y"][:2] == [2.54, 2.71],
          "CAD chart: starts at CORRA fixing")
    check(cj["layout"]["annotations"][0]["y"] == 0 and cj["layout"]["annotations"][0]["x"] == 1,
          "rising chart places note bottom-right")

    # ---- USD fallback to cftc/historical_fixings when FRED fails or is stale -> status=fallback (not partial) and credited to NY Fed
    c._cache.clear()
    pp._fred_last = lambda sid: (None if sid == "EFFR" else FRED.get(sid))
    # simulate FRED error recording a partial note before returning None
    def fred_err_effr(sid):
        if sid == "EFFR":
            c.partial_from(c.CompositeError("FRED down", kind="upstream"), "FRED EFFR (latest overnight fixing)")
            return None
        return FRED.get(sid)
    pp._fred_last = fred_err_effr
    t_fb = c.dispatch("policy_path_table", {"currency": "USD", "today": TODAY})
    check(t_fb["status"] == "fallback" and any("NY Fed" in n for n in t_fb.get("notes", []))
          and not any("Partial answer" in n for n in t_fb.get("notes", [])),
          f"USD FRED error fallback is status=fallback (not partial): status={t_fb.get('status')} notes={t_fb.get('notes')}")
    check([s["name"] for s in t_fb["sources"]] == ["CFTC", "BIS", "FRED", "NY Fed"]
          and "EFFR" not in next(s["detail"] for s in t_fb["sources"] if s["name"] == "FRED"),
          f"USD fallback credits NY Fed for EFFR and FRED only for target range: {t_fb['sources']}")
    check(t_fb["results"][1]["rate"] == 3.61 and t_fb["results"][2]["step"] == 1 and t_fb["results"][2]["priced"] == 4,
          f"USD fallback values from cftc/historical_fixings: {t_fb['results'][1:3]}")

    # stale FRED (>10 days old) also falls back to fresh cftc/historical_fixings
    c._cache.clear()
    pp._fred_last = lambda sid: (("2026-11-01", 3.55) if sid == "EFFR" else FRED.get(sid))
    t_st_fb = c.dispatch("policy_path_table", {"currency": "USD", "today": TODAY})
    check(t_st_fb["status"] == "fallback" and t_st_fb["results"][1]["rate"] == 3.61,
          f"USD stale FRED falls back to fresh CFTC fixing: {t_st_fb['results'][1]}")
    pp._fred_last = FRED.get

    # ---- step chart and steps_meta
    mp_with_cur = pp._with_current(mp, (3.60, "2026-11-18", "EFFR"), TODAY)
    xs, ys = pp.steps(mp)
    check(xs == ["2026-12-10", "2027-01-28", "2027-03-18", "2027-03-18", "2027-04-29", "2027-06-17"], f"steps x: {xs}")
    check(ys == [3.62, 3.50, 3.50, None, 3.40, 3.40], f"steps y: {ys}")
    xm, ym, sz, cd = pp.steps_meta(mp_with_cur)
    check(xm == ["2026-11-19", "2026-12-10", "2027-01-28", "2027-03-18", "2027-03-18", "2027-04-29", "2027-06-17"], f"steps_meta x: {xm}")
    check(ym == [3.60, 3.62, 3.50, 3.50, None, 3.40, 3.40], f"steps_meta y: {ym}")
    check(sz == [5, 5, 5, 0, 0, 5, 0], f"steps_meta sizes: {sz}")
    check(cd[0] == ["Now → 10 Dec 2026", " · current fixing"]
          and cd[1] == ["10 Dec 2026 → 28 Jan 2027", " · +2.0 bp (+8% of 25 bp)"]
          and cd[2] == ["28 Jan 2027 → 18 Mar 2027", " · -12.0 bp (-48% of 25 bp)"]
          and cd[5] == ["29 Apr 2027 → 17 Jun 2027", ""], f"steps_meta customdata: {cd}")
    _, _, _, cd_zero = pp.steps_meta([{"start": "2026-12-10", "end": "2027-01-28", "rate": 3.6002}], base_rate=3.6004)
    check(cd_zero[0] == ["10 Dec 2026 → 28 Jan 2027", " · 0.0 bp (0% of 25 bp)"],
          f"steps_meta zero move formats without sign: {cd_zero[0]}")

    c._cache.clear()
    ch = pp.chart(currency="USD", today=TODAY)
    names = [tr["name"] for tr in ch["data"]]
    check(names == ["Today (2026-11-18)", "1W ago (2026-11-12)", "1M ago (2026-10-17)", "Policy rate 3.625% (target 3.50–3.75%) (BIS, 2026-11-10)"], f"chart names: {names}")
    pol = ch["data"][-1]
    check(pol["x"] == ["2026-11-19", "2027-06-17"] and pol["y"] == [3.625, 3.625], "chart: policy line spans every path, from today")
    check(ch["data"][0]["x"][:2] == ["2026-11-19", "2026-12-10"] and ch["data"][0]["y"][:2] == [3.60, 3.62], "chart: today's path starts today at the fixing")
    check(ch["data"][0]["mode"] == "lines+markers" and ch["data"][0]["marker"]["size"] == [5, 5, 5, 0, 0, 5, 0], "chart: today trace has markers on period starts")
    check(ch["data"][2]["x"][0] == "2026-11-19", "chart: the 1M path is clipped to periods still ahead (starts today)")
    check(ch["layout"]["yaxis"]["tickformat"] == ".2f" and ch["layout"]["annotations"][0]["y"] == 1 and ch["layout"]["annotations"][0]["x"] == 1,
          "falling chart: fixed tick format, note at top-right")
    check(all(tr["line"]["shape"] == "hv" for tr in ch["data"][:3]), "chart: step lines")

    multi = pp.chart(currency="USD,EUR,XXX", today=TODAY)
    check([tr["name"] for tr in multi["data"]] == ["USD (policy 3.625%)", "EUR (policy 2.000%)"], "overlay: one trace per known currency")
    check("EFFR, ESTR" in multi["sources"][0]["detail"], "overlay: indices named in the source")

    # ---- meeting-day (orig_start == today) keeps step/priced/cum_bp in both table and chart when fixing is prior day
    c._cache.clear()
    PATHS[("USD", None)] = [row("2026-11-19", "2026-12-10", 3.85, "2026-11-18", 40)] + PATHS[("USD", None)]
    tm = pp.table(currency="USD", today=TODAY)["results"]
    check(tm[1]["meeting"] == "Now (EFFR fixing)" and tm[1]["end"] == "2026-11-19"
          and (tm[2]["step"], tm[2]["priced"], tm[2]["cum_bp"]) == (25, 100, 25),
          f"meeting-day keeps first step from prior-day fixing: {tm[1:3]}")
    cm = pp.chart(currency="USD", today=TODAY)
    check(cm["data"][0]["x"][0] == "2026-11-19"
          and cm["data"][0]["customdata"][0] == ["19 Nov 2026 → 10 Dec 2026", " · +25.0 bp (+100% of 25 bp)"],
          f"meeting-day chart hover includes first-meeting step from baseline fixing: {cm['data'][0]['customdata'][0]}")
    PATHS[("USD", None)] = PATHS[("USD", None)][1:]

    # ---- past 1W/1M failure degrades cleanly to partial instead of raising
    c._cache.clear()

    def api_past_fail(base, path, currency=None, date=None, index=None, **_):
        if path == "cftc/ois_policy_path" and date is not None:
            raise c.CompositeError("DTCC archive timeout", kind="timeout")
        return fake_api(base, path, currency=currency, date=date, index=index)

    c.api = api_past_fail
    tp = c.dispatch("policy_path_table", {"currency": "USD", "today": TODAY})
    check(tp["status"] == "partial" and any("1W ago" in n for n in tp.get("notes", [])) and tp["results"][2]["rate_1w"] is None,
          f"1W/1M failure degrades cleanly to partial: status={tp.get('status')} notes={tp.get('notes')}")
    c.api = fake_api

    # ---- stale: freshest print more than a week old
    c._cache.clear()
    st = pp.table(currency="USD", today=dt.date(2026, 12, 1))
    check(st.get("stale") == "Latest OIS prints are from 2026-11-18 (13 days old)", f"stale: {st.get('stale')}")
    check(st["results"][1]["chg_1w"] is None, "stale day: no earlier path, change blank")

    # ---- errors and the source contract
    try:
        pp.chart(currency="XXX", today=TODAY)
        check(False, "unknown currency raises")
    except c.CompositeError:
        check(True, "unknown currency raises")
    check([s["name"] for s in c.static_sources("policy_path_table", {"currency": "XXX"})] == ["CFTC", "BIS", "FRED"], "static sources never raise")
    check([s["name"] for s in c.static_sources("policy_path_table", {"currency": "CAD"})] == ["CFTC", "BIS", "Bank of Canada"],
          "CAD static sources name Bank of Canada for CORRA fixing")
    check("ESTR" in c.static_sources("policy_path", {"currency": "EUR"})[0]["detail"], "static sources follow the currency")
    c._cache.clear()
    PATHS[("GBP", None)] = []
    try:
        c.dispatch("policy_path_table", {"currency": "GBP"})
        check(False, "empty path raises")
    except c.CompositeError as e:
        check("GBP" in str(e), "empty path: says which currency")
    check(c.ROUTES["policy_path"] and c.ROUTES["policy_path_table"], "routes registered")
finally:
    c.api, c._policy_series, pp._fred_last = saved
    c._cache.clear()


# review fix: a period under way starts at today after ahead() but still matches the earlier paths by its real start
_cur = pp.ahead([{"start": "2026-10-30", "end": "2026-12-11", "rate": 3.6}], dt.date(2026, 11, 23))[0]
check(_cur["start"] == "2026-11-23" and pp.match(_cur, [{"start": "2026-10-30", "end": "2026-12-11", "rate": 3.7}]) is not None,
      "match: the period under way keeps its real start for the 1W / 1M match")

# ---- backend patch: build_ois_policy_path (63d/56d kept, 90d/14d dropped, grouped by (start, end), imminent, fresh, 2-trade bridge)
_spec = importlib.util.spec_from_file_location("run_backend_pp", ROOT / "scripts" / "run_backend.py")
_rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rb)

_cdate = dt.date(2026, 10, 27)
_recs = (
    # 1. Imminent meeting (fwd=1, 42d) traded 8 days forward earlier -> kept; spot 1M (fwd=3, max_tfwd=3) -> dropped
    [dtcc_rec("2026-10-28", "2026-12-09", 0.0390, "2026-10-20")] * 3
    + [dtcc_rec("2026-10-30", "2026-11-30", 0.0405, "2026-10-27")] * 4
    # 2. 63-day meeting period -> kept (was dropped by 55-day cap) + stale vs fresh trades (0.0430 newest 3 beats 10 at 0.0450)
    + [dtcc_rec("2026-12-09", "2027-02-10", 0.0450, "2026-10-18")] * 10
    + [dtcc_rec("2026-12-09", "2027-02-10", 0.0430, "2026-10-27")] * 3
    # 3. 2-trade middle bridge between Dec->Feb and Mar->May -> kept; isolated 2-trade bespoke -> dropped
    + [dtcc_rec("2027-02-10", "2027-03-24", 0.0440, "2026-10-26")] * 2
    + [dtcc_rec("2027-02-15", "2027-03-15", 0.0499, "2026-10-26")] * 2
    # 4. 56-day period with two distinct expirations on same start -> kept separate, not merged
    + [dtcc_rec("2027-03-24", "2027-05-19", 0.0455, "2026-10-27")] * 4
    + [dtcc_rec("2027-03-24", "2027-04-28", 0.0465, "2026-10-27")] * 3
    # 5. Out-of-range tenors: 90-day quarterly and 14-day stub -> dropped
    + [dtcc_rec("2026-12-09", "2027-03-09", 0.0420, "2026-10-27")] * 5
    + [dtcc_rec("2026-12-09", "2026-12-23", 0.0400, "2026-10-27")] * 5
)
_built = _rb.build_ois_policy_path(_recs, "NA/Swap OIS USD", "USD", _cdate, min_trades=3)
_keys = [(n["start_date"].isoformat(), n["end_date"].isoformat()) for n in _built]
check(_keys == [
    ("2026-10-28", "2026-12-09"),
    ("2026-12-09", "2027-02-10"),
    ("2027-02-10", "2027-03-24"),
    ("2027-03-24", "2027-04-28"),
    ("2027-03-24", "2027-05-19"),
], f"build_ois_policy_path periods: {_keys}")
_n_63 = _built[1]
check(abs(_n_63["rate"] - 0.0430) < 1e-9 and abs(_n_63["min_rate"] - 0.0430) < 1e-9 and abs(_n_63["max_rate"] - 0.0430) < 1e-9
      and _n_63["num_trades"] == 13 and _n_63["tenor_days"] == 63 and _n_63["as_of_date"] == dt.date(2026, 10, 27),
      f"build_ois_policy_path fresh pricing + 63d + total trades: {_n_63}")
check(abs(_built[3]["rate"] - 0.0465) < 1e-9 and abs(_built[4]["rate"] - 0.0455) < 1e-9,
      "same effective_date with distinct expirations kept separate")

# Finding 1 regression: 1 freshest trade vs 10 older-day trades must not be swamped by the older day
_f1_one = (
    [dtcc_rec("2027-01-27", "2027-03-17", 0.0450, "2026-11-26")] * 10
    + [dtcc_rec("2027-01-27", "2027-03-17", 0.0430, "2026-11-27")]
)
_n_f1_one = _rb.build_ois_policy_path(_f1_one, "NA/Swap OIS USD", "USD", dt.date(2026, 11, 25), min_trades=3)[0]
check(abs(_n_f1_one["rate"] - 0.0440) < 1e-9, f"1 freshest + 10 prior-day -> midpoint 0.0440 (not 0.0450): {_n_f1_one['rate']}")
_f1_two = _f1_one + [dtcc_rec("2027-01-27", "2027-03-17", 0.0431, "2026-11-27")]
_n_f1_two = _rb.build_ois_policy_path(_f1_two, "NA/Swap OIS USD", "USD", dt.date(2026, 11, 25), min_trades=3)[0]
check(abs(_n_f1_two["rate"] - 0.0431) < 1e-9, f"2 freshest (0.0430, 0.0431) + 10 prior-day -> 0.0431: {_n_f1_two['rate']}")

# Underlier filtering: USD mixes Federal Funds & SOFR/Term SOFR under NA/Swap OIS USD; AUD can have stray AUD-BBSW
_mixed_usd = (
    [dtcc_rec("2026-12-09", "2027-01-27", 0.04133, "2026-11-27", underlier="USD-Federal Funds-OIS Compound")] * 2
    + [dtcc_rec("2026-12-09", "2027-01-27", 0.04135, "2026-11-27", underlier="USD-Federal Funds-H.15-OIS-COMPOUND")] * 2
    + [dtcc_rec("2026-12-09", "2027-01-27", 0.04180, "2026-11-27", underlier="USD-SOFR-COMPOUND")] * 3
    + [dtcc_rec("2026-12-09", "2027-01-27", 0.04490, "2026-11-27", underlier="USD-SOFR CME Term")]
    + [dtcc_rec("2026-12-09", "2027-01-27", 0.04990, "2026-11-27", underlier="")] * 3
    + [dtcc_rec("2026-12-10", "2027-01-14", 0.04190, "2026-11-27", underlier="USD-SOFR-COMPOUND")] * 4
)
_usd_clean = _rb.build_ois_policy_path(_mixed_usd, "NA/Swap OIS USD", "USD", dt.date(2026, 11, 27), min_trades=3)
check(len(_usd_clean) == 1 and _usd_clean[0]["start_date"] == dt.date(2026, 12, 9)
      and _usd_clean[0]["end_date"] == dt.date(2027, 1, 27)
      and abs(_usd_clean[0]["rate"] - 0.04134) < 1e-9 and _usd_clean[0]["num_trades"] == 4,
      f"USD underlier filter keeps only Federal Funds swaps (drops SOFR, Term SOFR, blank): {_usd_clean}")

_mixed_aud = (
    [dtcc_rec("2026-12-09", "2027-02-10", 0.0486, "2026-11-27", ccy="AUD", underlier="AUD-AONIA-OIS Compound")] * 3
    + [dtcc_rec("2026-12-09", "2027-02-10", 0.0550, "2026-11-27", ccy="AUD", underlier="AUD-BBSW")]
    + [dtcc_rec("2026-12-10", "2027-01-14", 0.0520, "2026-11-27", ccy="AUD", underlier="AUD-BBSW")] * 3
)
_aud_clean = _rb.build_ois_policy_path(_mixed_aud, "NA/Swap OIS AUD", "AUD", dt.date(2026, 11, 27), min_trades=3)
check(len(_aud_clean) == 1 and abs(_aud_clean[0]["rate"] - 0.0486) < 1e-9 and _aud_clean[0]["num_trades"] == 3,
      f"AUD underlier filter drops AUD-BBSW: {_aud_clean}")

# Patch installer sets USD metadata benchmark to EFFR and preserves EUR=ESTR across idempotent calls
import openbb_cftc.models.ois_policy_path as _opp
_app_msgs = []
_rb.install_cftc_ois_policy_path_fix(_app_msgs)
_rb.install_cftc_ois_policy_path_fix(_app_msgs)
_q_usd = _opp.CftcOisPolicyPathFetcher.transform_query({"currency": "USD"})
_r_usd = _opp.CftcOisPolicyPathFetcher.transform_data(_q_usd, (_mixed_usd, "2026-11-27"))
check(_r_usd.metadata["benchmark"] == "EFFR" and _q_usd.lookback_days == 14
      and _opp.CftcOisPolicyPathFetcher._terminal_cache_rev == "policy_path_v3",
      f"USD metadata benchmark is EFFR and cache rev is policy_path_v3: {_r_usd.metadata}")
_q_eur = _opp.CftcOisPolicyPathFetcher.transform_query({"currency": "EUR"})
_r_eur = _opp.CftcOisPolicyPathFetcher.transform_data(
    _q_eur, ([dtcc_rec("2026-12-23", "2027-02-10", 0.02688, "2026-11-27", ccy="EUR", underlier="EUR-EuroSTR-OIS Compound")] * 3, "2026-11-27")
)
check(_r_eur.metadata["benchmark"] == "ESTR", f"EUR metadata benchmark stays ESTR: {_r_eur.metadata}")

done("test_policy_path")
