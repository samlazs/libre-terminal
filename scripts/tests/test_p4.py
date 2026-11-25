"""P4 and Estimates readable months: recommendation months, pool isolation, per-symbol history/meta cache + SWR."""

import datetime as dt
import importlib.util
import json
import tempfile
import time
from pathlib import Path

from _common import ROOT, check, done

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

import composite as c  # noqa: E402


# ---- 1. Readable months on Recommendations by month (hand-computed)
d_nov = dt.date(2026, 11, 21)
check(rb.recommendation_month("0m", today=d_nov) == "Nov 2026", "0m on 2026-11-21 -> Nov 2026")
check(rb.recommendation_month("-1m", today=d_nov) == "Oct 2026", "-1m on 2026-11-21 -> Oct 2026")
check(rb.recommendation_month("-2m", today=d_nov) == "Sep 2026", "-2m on 2026-11-21 -> Sep 2026")
check(rb.recommendation_month("-3m", today=d_nov) == "Aug 2026", "-3m on 2026-11-21 -> Aug 2026")

d_jan = dt.date(2026, 1, 15)
check(rb.recommendation_month("-1m", today=d_jan) == "Dec 2025", "year boundary -1m on Jan -> Dec 2025")
check(rb.recommendation_month("-2m", today=d_jan) == "Nov 2025", "year boundary -2m on Jan -> Nov 2025")
check(rb.recommendation_month("0q", today=d_nov) == "0q" and rb.recommendation_month(None, today=d_nov) is None, "non-month or None untouched")

rows_in = [{"symbol": "AAPL", "period": "0m", "buy": 20}, {"symbol": "AAPL", "period": "-1m", "buy": 18}]
rows_out = rb.format_recommendation_periods(rows_in, today=d_nov)
check(rows_out[0]["period"] == "Nov 2026" and rows_out[1]["period"] == "Oct 2026" and rows_in[0]["period"] == "0m",
      "format_recommendation_periods rewrites periods without mutating input dicts")


# ---- 2. Pool isolation spec between direct UI cards ('ui') and /x/ views ('x')
check(rb.pool_slot_spec("openbb_yfinance", "ui") == (("openbb_yfinance", "ui"), 5)
      and rb.pool_slot_spec("openbb_yfinance", "x") == (("openbb_yfinance", "x"), 4),
      "yfinance has separate 5-slot (ui) and 4-slot (x) pools")
check(rb.pool_slot_spec("openbb_oecd", "ui") == (("openbb_oecd", "shared"), 2)
      and rb.pool_slot_spec("openbb_oecd", "x") == (("openbb_oecd", "shared"), 2),
      "OECD shares one 2-slot pool so rate limits are respected")
check(rb.pool_slot_spec("openbb_sec", "ui")[0] != rb.pool_slot_spec("openbb_sec", "x")[0],
      "SEC slots are isolated per pool")


# ---- 3. Per-symbol history cache, short-window merge, complete-answer rule, SWR & fallback
_saved_fh = c._fetch_history
_saved_bg = c._bg_refresh_history
_saved_api = c.api
_saved_cd = c.CACHE_DIR
_tmp = tempfile.TemporaryDirectory()
try:
    c.CACHE_DIR = Path(_tmp.name)
    c._cache.clear()
    fetched = []

    def fake_fh(syms, start, interval):
        fetched.append((tuple(syms), start, interval))
        out = c.PriceHistory()
        for s in syms:
            if s == "BAD":
                out[s] = []
            elif start <= "2025-10-01":
                out[s] = [("2025-10-02", 90.0), ("2026-11-18", 100.0), ("2026-11-19", 101.0)]
                out.divs[s] = {"2026-11-18": 0.5}
            else:
                out[s] = [("2026-11-18", 100.0), ("2026-11-19", 101.0)]
        return out

    c._fetch_history = fake_fh
    h1 = c.history(["AAA", "BBB", "BAD"], dt.date(2025, 10, 1))
    check(len(fetched) == 1 and h1["AAA"][0] == ("2025-10-02", 90.0) and h1["BAD"] == [],
          "initial fetch populates AAA/BBB and leaves BAD empty")
    check(("hsym", "BAD", "1d") not in c._cache and not c._sym_hist_path("BAD").exists(),
          "complete-answer rule: empty series is never written to memory or disk")

    # Cross-bundle reuse: CCC is new, BBB hits per-symbol cache
    fetched.clear()
    h2 = c.history(["BBB", "CCC"], dt.date(2025, 10, 1))
    check(fetched == [(("CCC",), "2025-10-01", "1d")] and h2["BBB"][0] == ("2025-10-02", 90.0),
          "cross-bundle overlap only fetches CCC, reuses BBB")

    # Short-window merge: 14-day request served from 410-day cache and saving a shorter window never drops earlier bars
    fetched.clear()
    h_short = c.history(["AAA"], dt.date(2026, 11, 10))
    check(fetched == [] and h_short["AAA"] == [("2026-11-18", 100.0), ("2026-11-19", 101.0)],
          "shorter window served from longer per-symbol cache")
    c._sym_hist_save("AAA", "1d", "2026-11-10", [("2026-11-18", 100.0), ("2026-11-19", 102.0)], {})
    c._cache.clear()
    h_long_again = c.history(["AAA"], dt.date(2025, 10, 1))
    check(fetched == [] and h_long_again["AAA"][0] == ("2025-10-02", 90.0) and h_long_again["AAA"][-1] == ("2026-11-19", 102.0),
          "saving a short window merges without losing earlier 2025-10-02 bars")

    # Stale-while-revalidate: 2 h old disk copy serves immediately + queues background refresh + notes SWR time (not upstream failure)
    p_aaa = c._sym_hist_path("AAA")
    saved_aaa = json.loads(p_aaa.read_text(encoding="utf-8"))
    saved_aaa["t"] = time.time() - 7200
    p_aaa.write_text(json.dumps(saved_aaa), encoding="utf-8")
    c._cache.clear()
    bg_seen = []
    c._bg_refresh_history = lambda syms, start_iso, interval: bg_seen.append((tuple(syms), start_iso, interval))
    notes, swr_notes = [], []
    tok, swr_tok = c._STALE_NOTES.set(notes), c._SWR_NOTES.set(swr_notes)
    try:
        h_swr = c.history(["AAA"], dt.date(2026, 11, 10))
    finally:
        c._SWR_NOTES.reset(swr_tok)
        c._STALE_NOTES.reset(tok)
    check(fetched == [] and len(bg_seen) == 1 and bg_seen[0][0] == ("AAA",) and bg_seen[0][1] == "2025-10-01"
          and len(swr_notes) == 1 and notes == [] and h_swr.swr_ts is not None and h_swr.stale_ts is None,
          "SWR serves 2 h old disk copy without blocking, triggers background refresh, and emits swr_notes (never stale_notes)")

    # Expired (>24 h) + Yahoo fails -> serves expired disk copy with stale note (no SWR note)
    saved_aaa["t"] = time.time() - 30 * 3600
    p_aaa.write_text(json.dumps(saved_aaa), encoding="utf-8")
    c._cache.clear()
    c._fetch_history = lambda syms, start, interval: c.PriceHistory({s: [] for s in syms})
    notes2, swr_notes2 = [], []
    tok2, swr_tok2 = c._STALE_NOTES.set(notes2), c._SWR_NOTES.set(swr_notes2)
    try:
        h_fb = c.history(["AAA"], dt.date(2026, 11, 10))
    finally:
        c._SWR_NOTES.reset(swr_tok2)
        c._STALE_NOTES.reset(tok2)
    check(h_fb["AAA"][-1] == ("2026-11-19", 102.0) and len(notes2) == 1 and swr_notes2 == []
          and h_fb.stale_ts is not None and h_fb.swr_ts is None,
          "expired disk copy served as fallback with stale note when Yahoo returns empty")

    # dispatch() contract: SWR -> {"cached": "Cached ... · refreshing"} (no "stale"); failure fallback -> {"stale": "Source not answering..."}
    _saved_routes = dict(c.ROUTES)
    try:
        t_swr = time.time() - 600
        t_fail = time.time() - 30 * 3600
        c.ROUTES["__test_swr"] = lambda **_: (c._note_swr_ts(t_swr) or {"results": [{"a": 1, "date": "2026-11-20"}], "sources": [{"name": "Yahoo Finance", "detail": "d", "url": ""}]})
        c.ROUTES["__test_fail"] = lambda **_: (c._note_stale_ts(t_fail) or {"results": [{"a": 1, "date": "2026-11-20"}], "sources": [{"name": "Yahoo Finance", "detail": "d", "url": ""}]})
        c.ROUTES["__test_both"] = lambda **_: (c._note_swr_ts(t_swr) or c._note_stale_ts(t_fail) or {"results": [{"a": 1, "date": "2026-11-20"}], "sources": [{"name": "Yahoo Finance", "detail": "d", "url": ""}]})
        d_swr = c.dispatch("__test_swr", {})
        d_fail = c.dispatch("__test_fail", {})
        d_both = c.dispatch("__test_both", {})
        check("stale" not in d_swr and d_swr.get("cached", "").startswith("Cached ") and d_swr.get("cached", "").endswith("· refreshing")
              and "Source not answering" not in d_swr.get("cached", "") and "(10 min old)" in d_swr.get("cached", ""),
              "dispatch normal SWR emits calm 'Cached ... (10 min old) · refreshing' and omits 'stale'")
        check("cached" not in d_fail and d_fail.get("stale", "").startswith("Source not answering: showing the copy saved ")
              and "(30 h old)" in d_fail.get("stale", ""),
              "dispatch failed fetch emits 'Source not answering' stale note and omits 'cached'")
        check("stale" in d_both and "cached" not in d_both,
              "dispatch prefers 'stale' warning if both SWR and failed fallback occur in the same view")
    finally:
        c.ROUTES.clear()
        c.ROUTES.update(_saved_routes)

    # ---- 4. Per-symbol _meta cache + fallback + HTTP pool header
    meta_calls = []

    def fake_api(base, path, _timeout=45, **kw):
        meta_calls.append((path, kw.get("symbol"), _timeout))
        syms = [s for s in kw["symbol"].split(",") if s]
        return [{"symbol": s, "name": f"Co {s}", "currency": "USD", "asset_type": "equity"} for s in syms if s != "MISS"]

    c.api = fake_api
    c._cache.clear()
    m1 = c._meta(["AAPL", "MSFT", "MISS"])
    check(m1["AAPL"]["name"] == "Co AAPL" and "MISS" not in m1 and meta_calls[0][2] == 25,
          "_meta caches AAPL/MSFT with 25 s timeout and skips empty MISS")
    meta_calls.clear()
    c._cache.clear()  # clear memory to prove disk cache hits for AAPL while only NVDA is fetched
    m2 = c._meta(["AAPL", "NVDA"])
    check(meta_calls == [("equity/price/quote", "NVDA", 25)] and m2["AAPL"]["name"] == "Co AAPL" and m2["NVDA"]["name"] == "Co NVDA",
          "_meta disk cache reuses AAPL across bundles and fetches only NVDA")
finally:
    c._fetch_history = _saved_fh
    c._bg_refresh_history = _saved_bg
    c.api = _saved_api
    c.CACHE_DIR = _saved_cd
    c._cache.clear()
    _tmp.cleanup()

done("test_p4")
