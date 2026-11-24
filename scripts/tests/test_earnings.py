"""Company > Estimates: the earnings scorecard, EPS surprise history and consensus table.

Two moments of one report are replayed with hand-made data shaped like the live answers of 2026-11-17 (NVDA):
the report day before Yahoo posts the actual (results pending, after-hours reaction), and the day after (Yahoo's
history has the actual, its estimates have rolled to the next quarter, its calendar still shows the old date).
Expected values are worked out by hand."""

import datetime as dt

from _common import check, done

import composite as c

D = "2026-11-17"
HIST = [{"date": "2026-08-26", "eps_actual": 2.22, "eps_estimated": 2.09}, {"date": "2026-05-20", "eps_actual": 1.87, "eps_estimated": 1.77},
        {"date": "2026-02-25", "eps_actual": 1.62, "eps_estimated": 1.54}, {"date": "2025-11-19", "eps_actual": 1.30, "eps_estimated": 1.26},
        {"date": "2013-05-09", "eps_actual": None, "eps_estimated": None}]
EPS = [{"period": "0q", "mean": 2.47313, "low": 2.25754, "high": 2.7, "number_of_analysts": 41, "year_ago_eps": 1.3, "growth": 0.9024, "currency": "USD"},
       {"period": "+1q", "mean": 2.7613, "low": 2.44049, "high": 3.117, "number_of_analysts": 43, "year_ago_eps": 1.62, "growth": 0.7045, "currency": "USD"},
       {"period": "0y", "mean": 9.33622, "low": 8.78387, "high": 10.1, "number_of_analysts": 52, "year_ago_eps": 4.77, "growth": 0.9573, "currency": "USD"},
       {"period": "+1y", "mean": 15.86485, "low": 11.85, "high": 18.746, "number_of_analysts": 53, "year_ago_eps": 9.33622, "growth": 0.6993, "currency": "USD"}]
REV = [{"period": "0q", "mean": 108.71e9, "low": 100.481e9, "high": 116.251e9, "number_of_analysts": 42, "growth": 0.907, "currency": "USD"},
       {"period": "+1q", "mean": 124.887e9, "low": 111.913e9, "high": 138.49e9, "number_of_analysts": 43, "growth": 0.8331, "currency": "USD"},
       {"period": "0y", "mean": 411.95e9, "low": 390.23e9, "high": 426.885e9, "number_of_analysts": 55, "growth": 0.9077, "currency": "USD"},
       {"period": "+1y", "mean": 688.737e9, "low": 506.483e9, "high": 778.471e9, "number_of_analysts": 57, "growth": 0.6719, "currency": "USD"}]
TREND = [{"period": "0q", "current": 2.47313, "days_ago_7": 2.4737, "days_ago_30": 2.47325, "days_ago_60": 2.47356, "days_ago_90": 2.35462},
         {"period": "+1q", "current": 2.7613, "days_ago_7": 2.74979, "days_ago_30": 2.74622, "days_ago_60": 2.74295, "days_ago_90": 2.66539},
         {"period": "0y", "current": 9.33622, "days_ago_7": 9.31674, "days_ago_30": 9.31242, "days_ago_60": 9.30713, "days_ago_90": 8.96377},
         {"period": "+1y", "current": 15.86485, "days_ago_7": 15.92806, "days_ago_30": 15.9146, "days_ago_60": 15.68263, "days_ago_90": 12.85483}]
REVS = [{"period": "0q", "up_last_7_days": 0, "up_last_30_days": 0, "down_last_7_days": 1, "down_last_30_days": 1},
        {"period": "+1q", "up_last_7_days": 0, "up_last_30_days": 0, "down_last_7_days": 1, "down_last_30_days": 1},
        {"period": "0y", "up_last_7_days": 2, "up_last_30_days": 2, "down_last_7_days": 0, "down_last_30_days": 0},
        {"period": "+1y", "up_last_7_days": 3, "up_last_30_days": 7, "down_last_7_days": 0, "down_last_30_days": 0}]
CAL = {"earnings_date": D, "earnings_average": 2.47313, "earnings_low": 2.25754, "earnings_high": 2.7,
       "revenue_average": 108.71e9, "revenue_low": 100.481e9, "revenue_high": 116.251e9}
INC = [{"period_ending": "2026-07-26", "total_revenue": 96.221e9}, {"period_ending": "2026-04-26", "total_revenue": 81.615e9}]
CLOSES = [("2025-11-18", 100.0), ("2025-11-19", 100.0), ("2025-11-20", 104.0), ("2026-02-24", 150.0), ("2026-02-25", 151.0), ("2026-02-26", 141.0),
          ("2026-05-19", 160.0), ("2026-05-20", 160.0), ("2026-05-21", 168.0), ("2026-08-25", 180.0), ("2026-08-26", 181.0), ("2026-08-27", 172.0),
          ("2026-11-16", 156.14), ("2026-11-17", 155.46)]
RELEASE = {"url": "https://www.sec.gov/Archives/edgar/data/1045810/0001045810-26-000088-index.htm", "filed": "2026-11-17T21:20:51.000Z", "date": D}

state = {}


def install(raw, closes, last, release=RELEASE, chart=None, chain=None):
    c._est_raw = lambda sym: raw
    c._report_info = lambda sym, date: {"timing": "after-hours", "period": "2026-10"} if date == D else {}
    c._earnings_release = lambda sym, date: release if release and release["date"] == date else None
    c._last_price = lambda sym: last
    c.history = lambda syms, start, interval="1d": c.PriceHistory({s: list(closes) for s in syms})
    c._yahoo_quarter_chart = chart if callable(chart) else (lambda sym: chart)
    c._options_chain = chain if callable(chain) else (lambda sym: chain)


saved = c._est_raw, c._report_info, c._earnings_release, c._last_price, c.history, c._today
saved_chart = c._yahoo_quarter_chart
saved_chain = c._options_chain
try:
    c._cache.clear()
    # ---- report day, results not yet in Yahoo's history
    install({"hist": HIST, "cal": CAL, "eps": EPS, "rev": REV, "trend": TREND, "revs": REVS, "inc": INC}, CLOSES, ("2026-11-17T17:35:00", 161.5))
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 17))
    lt = m["latest"]
    check(lt["date"] == D and lt["pending"] and lt["timing"] == "after-hours" and lt["quarter"] == "2026-10", "report day: the latest report is today's, results pending")
    check((lt["eps"]["est"], lt["eps"]["low"], lt["eps"]["high"], lt["eps"]["n"], lt["eps"]["actual"]) == (2.47313, 2.25754, 2.7, 41, None), "EPS: consensus 2.47 (2.26-2.70, 41), no actual yet")
    check((lt["rev"]["est"], lt["rev"]["n"], lt["rev"]["actual"]) == (108.71e9, 42, None), "revenue: consensus 108.71B (42), no actual yet")
    check(lt["release"]["url"] == RELEASE["url"], "press release: today's 8-K (Item 2.02)")
    r = lt["reaction"]
    check(r["base_date"] == D and r["base"] == 155.46 and r["price"] == 161.5 and abs(r["move"] - (161.5 / 155.46 - 1)) < 1e-12 and r["when"] == "after hours 17:35",
          "reaction: after-hours 161.50 vs 155.46 close of the report day = +3.89%")
    nq = m["next_quarter"]
    check((nq["eps"]["est"], nq["rev"]["est"], nq["rev"]["n"]) == (2.7613, 124.887e9, 43), "next quarter (the one the company guides): EPS 2.76, revenue 124.89B")
    check(m["next"]["date"] is None, "next report date: not announced yet")
    tr = m["track"]
    check(tr["n"] == 4 and tr["beats"] == 4, "track record: 4 of 4 reports above consensus")
    want = ((2.22 - 2.09) / 2.09 + (1.87 - 1.77) / 1.77 + (1.62 - 1.54) / 1.54 + (1.30 - 1.26) / 1.26) / 4
    check(abs(tr["avg_surprise"] - want) < 1e-12 and abs(want - 0.050598) < 1e-6, "average surprise +5.06%")
    moves = [172 / 180 - 1, 168 / 160 - 1, 141 / 150 - 1, 104 / 100 - 1]
    check(abs(tr["avg_abs_move"] - sum(abs(x) for x in moves) / 4) < 1e-12, "average 2-day move (close before -> day after), absolute")
    c._today = lambda: dt.date(2026, 11, 17)
    md = c.earnings(symbol="NVDA")["content"]
    for frag in ("2.47", "see release", "+3.9%", "Press release", "Next quarter", "124.9B", "4 of 4",
                 "(https://www.marketbeat.com/stocks/NYSE/NVDA/earnings/)"):
        check(frag in md, f"scorecard shows {frag!r}")
    check("marketbeat.com/stocks/NYSE/BRK-B/earnings/" in c.earnings_call_link("BRK-B") and "finance.yahoo.com/quote/SHEL.L/earnings-calls/" in c.earnings_call_link("SHEL.L"),
          "U.S. tickers -> MarketBeat, other listings -> Yahoo")
    store = c._consensus_store("NVDA")
    check(store.get(D, {}).get("rev", [None])[0] == 108.71e9 and store[D]["eps"][0] == 2.47313, "the pre-report consensus is saved for the day after")

    # ---- what happened live on 2026-11-17: Yahoo rolled its estimates to the next quarter ~90 minutes after the release,
    # before posting the actual (0q is now the January quarter: year-ago EPS 1.62, not the 1.30 reported 2025-11-19)
    eps_rolled = [dict(EPS[1], period="0q"), dict(EPS[1], period="+1q", mean=3.20634, year_ago_eps=1.87)] + EPS[2:]
    rev_rolled = [dict(REV[1], period="0q"), dict(REV[1], period="+1q", mean=142.859e9)] + REV[2:]
    cal_rolled = dict(CAL, earnings_average=2.7613, revenue_average=124.887e9)
    install({"hist": HIST, "cal": cal_rolled, "eps": eps_rolled, "rev": rev_rolled, "trend": TREND, "revs": REVS, "inc": INC}, CLOSES, ("2026-11-17T17:55:00", 160.9))
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 17))
    lt = m["latest"]
    check(lt["pending"] and lt["eps"]["est"] == 2.47313 and lt["eps"]["n"] == 41 and lt["rev"]["est"] == 108.71e9,
          "rolled before the actual: the consensus going in is the saved 2.47 / 108.71B, not Yahoo's new 2.76 / 124.89B")
    check(m["next_quarter"]["eps"]["est"] == 2.7613 and m["next_quarter"]["rev"]["est"] == 124.887e9, "next quarter: Yahoo's new current quarter (2.76 / 124.89B)")
    check(c._consensus_store("NVDA")[D]["eps"][0] == 2.47313, "the saved consensus is not overwritten by the rolled numbers")
    md = c.earnings(symbol="NVDA")["content"]
    check("2.47" in md and "108.7B" in md and "see release" in md and "2.76" in md, "scorecard: 2.47 going in, actual in the release, next quarter 2.76")

    # ---- the day after: Yahoo has the actual, estimates rolled to the next quarter, calendar still on the old date
    hist2 = [{"date": D, "eps_actual": 2.53, "eps_estimated": 2.47}] + HIST
    eps2 = [dict(EPS[1], period="0q"), dict(EPS[1], period="+1q", mean=3.0)] + EPS[2:]
    rev2 = [dict(REV[1], period="0q"), dict(REV[1], period="+1q", mean=140e9)] + REV[2:]
    cal2 = dict(CAL, earnings_average=2.7613, revenue_average=124.887e9)
    inc2 = [{"period_ending": "2026-10-25", "total_revenue": 110.0e9}] + INC
    closes2 = CLOSES + [("2026-11-18", 160.0)]
    install({"hist": hist2, "cal": cal2, "eps": eps2, "rev": rev2, "trend": TREND, "revs": REVS, "inc": inc2}, closes2, ("2026-11-18T16:00:00", 160.0))
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 18))
    lt = m["latest"]
    check(lt["date"] == D and not lt["pending"], "day after: the latest report comes from Yahoo's history")
    check(lt["eps"]["est"] == 2.47 and lt["eps"]["actual"] == 2.53 and abs(lt["eps"]["surprise"] - (2.53 - 2.47) / 2.47) < 1e-12, "EPS 2.53 vs 2.47 = +2.43%")
    check(lt["rev"]["est"] == 108.71e9 and lt["rev"]["actual"] == 110.0e9 and abs(lt["rev"]["surprise"] - (110.0 / 108.71 - 1)) < 1e-12,
          "revenue 110.0B vs the saved pre-report consensus 108.71B = +1.19%")
    check(abs(lt["reaction"]["move"] - (160.0 / 155.46 - 1)) < 1e-12 and lt["reaction"]["base_date"] == D, "reaction: 160.00 close vs 155.46 = +2.92%")
    check(m["next"]["date"] is None and m["next_quarter"]["eps"]["est"] == 2.7613, "calendar still on the reported date: next date not announced; consensus = Yahoo's new current quarter")
    # Nasdaq says "not-supplied" once the report is past (live 2026-11-18): the 8-K accepted 21:20:51Z = 16:20 ET says
    # after the close, so the base stays the 11-17 close 155.46, not the 11-16 close 156.14
    _ri = c._report_info
    c._report_info = lambda sym, date: {"timing": None, "period": "2026-10"} if date == D else {}
    c._cache.clear()
    m2 = c._earn_model("NVDA", today=dt.date(2026, 11, 18))
    check(m2["latest"]["reaction"]["base_date"] == D and m2["latest"]["reaction"]["base"] == 155.46, "timing from the SEC acceptance time: base = report-day close")
    c._report_info = _ri
    check(c._timing_from_acceptance("2026-11-17T21:20:51.000Z", D) == "after-hours" and c._timing_from_acceptance("2026-11-12T13:52:41.000Z", "2026-11-12") == "pre-market"
          and c._timing_from_acceptance("2026-07-30T20:05:00Z", "2026-07-30") == "after-hours" and c._timing_from_acceptance("2026-11-17T17:00:00Z", D) is None
          and c._timing_from_acceptance("2026-11-18T02:00:00Z", D) == "after-hours" and c._timing_from_acceptance("2026-11-17", D) is None,
          "acceptance times: 16:20 EST after, 08:52 EST pre, 16:05 EDT after, 12:00 in session none, 21:00 ET same day after, date only none")
    check(m["track"]["n"] == 5 and m["track"]["beats"] == 5, "track record now counts this report")

    # ---- the actual is posted but Yahoo's estimates have not rolled yet (0q year-ago EPS 1.30 = EPS reported 2025-11-19)
    install({"hist": hist2, "cal": cal2, "eps": EPS, "rev": REV, "trend": TREND, "revs": REVS, "inc": inc2}, closes2, None)
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 18))
    check(m["next_quarter"]["eps"]["est"] == 2.7613 and m["next_quarter"]["rev"]["est"] == 124.887e9,
          "estimates not rolled yet: the next quarter is Yahoo's +1q, not the quarter just reported")

    # ---- a symbol whose next report is ahead: no reaction, the next date and its consensus
    cal3 = dict(CAL, earnings_date="2027-02-25")
    install({"hist": hist2, "cal": cal3, "eps": eps2, "rev": rev2, "trend": TREND, "revs": REVS, "inc": inc2}, closes2, None)
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2026, 12, 15))
    check(m["next"]["date"] == "2027-02-25" and m["next"]["eps"]["est"] == 2.7613, "next report announced: its date and the current-quarter consensus")
    check(m["latest"]["reaction"]["move"] is not None and m["latest"]["reaction"]["when"] == "2-day move", "an older report shows its 2-day move, not a live price")

    # ---- the Report picker: an earlier report, its own figures and no "next report"; an unknown date falls back, said so
    m = c._earn_model("NVDA", today=dt.date(2026, 12, 15), report="2026-05-20")
    lt = m["latest"]
    check(not m["is_latest"] and lt["date"] == "2026-05-20" and lt["eps"]["est"] == 1.77 and lt["eps"]["actual"] == 1.87, "picked report: 1.87 vs 1.77")
    check(lt["release"] is None and abs(lt["reaction"]["move"] - (168 / 160 - 1)) < 1e-12, "its own release (none in this fixture) and its 2-day move +5.0%")
    c._today = lambda: dt.date(2026, 12, 15)
    md = c.earnings(symbol="NVDA", report="2026-05-20")["content"]
    check("Report: 2026-05-20" in md and "Next report" not in md and "Next quarter" not in md, "an earlier report: no next-quarter block")
    m = c._earn_model("NVDA", today=dt.date(2026, 12, 15), report="2020-01-01")
    check(m["is_latest"] and m["latest"]["date"] == D and "2020-01-01" in m["note"], "unknown date: the latest, with a note")
    opts = c.earnings_reports(symbol="NVDA")["results"]
    check(opts[0] == {"label": "Latest report", "value": ""} and opts[1]["value"] == D and "2.53 vs 2.47" in opts[1]["label"], "picker: Latest, then each report with its outcome")

    # ---- the release card: the picked report's 8-K exhibit 99.1 in the SEC viewer; a message when there is none
    saved_docs = c._filing_documents
    c._filing_documents = lambda url: [{"type": "8-K", "url": url}, {"type": "EX-99.1", "url": "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000088/q3fy27pr.htm"}]
    try:
        out = c.earnings_release(symbol="NVDA")
        check(out["_frame"].startswith("/sec/filing_viewer_app?document_url=") and "q3fy27pr.htm" in out["_frame"], "latest report: the viewer opens exhibit 99.1")
        out = c.earnings_release(symbol="NVDA", report="2026-05-20")
        check("_frame" not in out and "No SEC earnings release" in out["_message"], "no release for that report: a message, not someone else's release")
    finally:
        c._filing_documents = saved_docs

    # ---- EPS surprise history: newest first, same-source surprise, 2-day move
    install({"hist": hist2, "cal": cal3, "eps": eps2, "rev": rev2, "trend": TREND, "revs": REVS, "inc": inc2}, closes2, None)
    rows = c.earnings_history(symbol="NVDA")["results"]
    check([r["date"] for r in rows] == [D, "2026-08-26", "2026-05-20", "2026-02-25", "2025-11-19"], "reported quarters only, newest first")
    r0, r1 = rows[0], rows[1]
    check(abs(r1["surprise"] - (2.22 - 2.09) / 2.09) < 1e-12 and abs(r1["move_2d"] - (172 / 180 - 1)) < 1e-12, "2026-08-26: +6.22% surprise, 2-day move -4.44%")
    check(abs(r0["move_2d"] - (160 / 156.14 - 1)) < 1e-12, "latest: 2-day move from the close before the report day (156.14) to the day after (160.00)")

    # ---- consensus and revisions: one row per period, revisions as % changes of the consensus
    rows = {r["period"]: r for r in c.consensus(symbol="NVDA")["results"]}
    q = rows["Current quarter"]
    check(set(rows) == {"Current quarter", "Next quarter", "Current fiscal year", "Next fiscal year"}, "four periods, in words")
    y1 = rows["Next fiscal year"]
    check(abs(y1["eps_chg_90d"] - (15.86485 / 12.85483 - 1)) < 1e-12 and y1["up_30d"] == 7 and y1["down_30d"] == 0, "next FY: EPS +23.4% in 90 days, 7 up / 0 down in 30 days")
    check(q["eps"] == 2.7613 and q["rev"] == 124.887e9 and q["eps_n"] == 43, "current quarter after the roll: 2.76, 124.89B")
    check(abs(rows["Current fiscal year"]["eps_growth"] - 0.9573) < 1e-12, "growth as Yahoo gives it (fraction)")

    # ---- Yahoo's statement still lacks the quarter days after the release (live NVDA 2026-11-20: statement row
    # 2026-10-31 with EPS but no revenue; earnings summary 3Q2026 revenue 115.844B, EPS 2.68): the summary's figure,
    # only when tied to the report (its EPS = the reported EPS, the quarter before = the statement's latest revenue)
    CHART = {"currency": "USD", "quarters": [("1Q2026", 81.615e9, 1.87), ("2Q2026", 96.221e9, 2.22), ("3Q2026", 115.844e9, 2.53)]}
    lag = {"hist": hist2, "cal": cal2, "eps": eps2, "rev": rev2, "trend": TREND, "revs": REVS, "inc": INC}
    install(lag, closes2, None, chart=CHART)
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 20))
    lt = m["latest"]
    check(lt["rev"]["actual"] == 115.844e9 and lt["rev_from"] == "summary" and abs(lt["rev"]["surprise"] - 0.065624) < 1e-6,
          "statement lagging: revenue 115.844B from the earnings summary vs saved 108.71B = +6.56%")
    c._today = lambda: dt.date(2026, 11, 20)
    out = c.earnings(symbol="NVDA")
    check("115.8B" in out["content"] and "+6.6%" in out["content"] and "earnings summary" in out["content"]
          and "earnings summary" in out["sources"][0]["detail"], "scorecard and sources say where the revenue comes from")
    bad = {"eps 2.50, not the reported 2.53": dict(CHART, quarters=CHART["quarters"][:2] + [("3Q2026", 115.844e9, 2.50)]),
           "quarter before 95.0B, not the statement's 96.221B": dict(CHART, quarters=[CHART["quarters"][0], ("2Q2026", 95.0e9, 2.22), CHART["quarters"][2]]),
           "financials in EUR, card in USD": dict(CHART, currency="EUR"), "summary not available": None}
    for why, ch in bad.items():
        install(lag, closes2, None, chart=ch)
        c._cache.clear()
        m = c._earn_model("NVDA", today=dt.date(2026, 11, 20))
        check(m["latest"]["rev"]["actual"] is None and m["latest"]["rev_from"] is None, f"summary not tied ({why}): revenue blank")
    install(lag, closes2, None, chart=CHART)
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2027, 1, 5))
    check(m["latest"]["rev"]["actual"] is None, "49 days after the report: no summary fallback")
    c._today = lambda: dt.date(2027, 1, 5)
    md = c.earnings(symbol="NVDA")["content"]
    check("not yet in Yahoo's data" in md and "earnings summary" not in md, "blank revenue says so, no summary note")

    def boom(sym):
        raise AssertionError("summary asked although the statement has the quarter")
    install(dict(lag, inc=inc2), closes2, None, chart=boom)
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 20))
    check(m["latest"]["rev"]["actual"] == 110.0e9 and m["latest"]["rev_from"] == "statement", "statement has the quarter: its 110.0B, summary not asked")

    # ---- rolled before the actual, and nothing was saved before the roll: "not recorded", never the next quarter's numbers
    (c.CACHE_DIR / "earnings_consensus.json").unlink()
    install({"hist": HIST, "cal": cal_rolled, "eps": eps_rolled, "rev": rev_rolled, "trend": TREND, "revs": REVS, "inc": INC}, CLOSES, None)
    c._cache.clear()
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 17))
    check(m["latest"]["eps"]["est"] is None and m["latest"]["rev"]["est"] is None, "no saved consensus: blank, not 2.76")
    check(D not in c._consensus_store("NVDA"), "and the rolled numbers are not saved as this report's consensus")
finally:
    c._est_raw, c._report_info, c._earnings_release, c._last_price, c.history, c._today = saved
    c._yahoo_quarter_chart = saved_chart
    c._cache.clear()

# ---- Yahoo not answering (live 2026-11-17 just after a restart: "Invalid Crumb"): retry once, never cache a partial answer for long
fails, calls = {"cal": 1}, {}
ANSWERS = {"yfinance/company_calendar": [CAL], "equity/fundamental/historical_eps": HIST, "yfinance/estimates/earnings": EPS, "yfinance/estimates/revenue": REV,
           "yfinance/estimates/eps_trend": TREND, "yfinance/estimates/eps_revisions": REVS, "equity/fundamental/income": INC}


def fake_api(base, path, **kw):
    calls[path] = calls.get(path, 0) + 1
    if path == "yfinance/company_calendar" and fails["cal"] > 0:
        fails["cal"] -= 1
        raise c.CompositeError("Yahoo: 401 Invalid Crumb")
    return ANSWERS.get(path, [])


saved_api = c.api
try:
    c.api = fake_api
    c._cache.clear()
    raw = c._est_raw("ZZZ")
    check(raw["cal"]["earnings_date"] == D and raw["_failed"] == [] and calls["yfinance/company_calendar"] == 2, "a part that did not answer is asked once more")
    fails["cal"], calls = 5, {}
    c._cache.clear()
    raw = c._est_raw("ZZZ")
    check(raw["_failed"] == ["cal"] and raw["cal"] == {} and raw["eps"] == EPS, "still not answering: marked as missing, the rest kept")
    k = ("est_raw", "ZZZ")
    t0, v0 = c._cache[k]
    c._cache[k] = (t0 - 61, v0)                   # a minute later
    fails["cal"] = 0
    raw = c._est_raw("ZZZ")
    check(raw["_failed"] == [] and raw["cal"]["earnings_date"] == D, "a partial answer is replaced after a minute, not kept ten")
finally:
    c.api = saved_api
    c._cache.clear()
saved_raw = c._est_raw
try:
    c._est_raw = lambda sym: {"hist": HIST, "cal": {}, "eps": EPS, "rev": REV, "trend": TREND, "revs": REVS, "inc": INC, "_failed": ["cal"]}
    c._report_info, c._earnings_release, c._last_price = (lambda s, d: {}), (lambda s, d: None), (lambda s: None)
    c._options_chain = lambda s: None
    c.history = lambda syms, start, interval="1d": c.PriceHistory({s: list(CLOSES) for s in syms})
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 17))
    check(m["note"] and "earnings calendar" in m["note"] and "miss" in m["note"], "the scorecard says Yahoo's calendar did not answer, instead of quietly showing an older report as the latest")
finally:
    c._est_raw = saved_raw
    c._report_info, c._earnings_release, c._last_price, c.history, c._today = saved[1], saved[2], saved[3], saved[4], saved[5]
    c._yahoo_quarter_chart, c._options_chain = saved_chart, saved_chain
    c._cache.clear()

# ---- options-implied move vs. realised move around earnings reports
CHAIN_NVDA = {
    "expiration": ["2026-11-13", "2026-11-13", "2026-11-20", "2026-11-20", "2026-11-20", "2026-11-20", "2026-12-18", "2026-12-18"],
    "strike": [155.0, 155.0, 155.0, 155.0, 160.0, 160.0, 155.0, 155.0],
    "option_type": ["call", "put", "call", "put", "call", "put", "call", "put"],
    "bid": [1.0, 1.0, 6.20, 5.80, 3.80, 8.20, 8.00, 7.60],
    "ask": [1.2, 1.2, 6.60, 6.20, 4.20, 8.60, 8.40, 8.00],
    "mark": [1.1, 1.1, 6.40, 6.00, 4.00, 8.40, 8.20, 7.80],
    "last_trade_price": [1.1, 1.1, 6.35, 6.05, 4.05, 8.35, 8.10, 7.90],
    "underlying_price": [155.0] * 8,
    "eod_date": ["2026-11-17"] * 8,
}
try:
    c._cache.clear()
    c._today = lambda: dt.date(2026, 11, 17)
    # Pre-report day: pending=True and release=None -> uses report-day expiry 2026-11-20
    # 155 strike call mid = 6.40, put mid = 6.00 -> straddle = 12.40; 12.40 / 155.0 = 0.08 (8.0%)
    install({"hist": HIST, "cal": CAL, "eps": EPS, "rev": REV, "trend": TREND, "revs": REVS, "inc": INC},
            CLOSES, ("2026-11-17T14:00:00", 155.0), release=None, chain=(CHAIN_NVDA, "nasdaq"))
    m = c._earn_model("NVDA", today=dt.date(2026, 11, 17))
    imp = m["implied"]
    check(imp is not None and imp["expiry"] == "2026-11-20" and imp["strike"] == 155.0 and imp["pricing"] == "mid" and imp["provider"] == "nasdaq",
          "implied move picks first expiry >= target_date at closest ATM strike with mid quotes")
    check(abs(imp["straddle"] - 12.40) < 1e-9 and abs(imp["move"] - 0.08) < 1e-12, "implied straddle 12.40 / 155 = 8.0%")
    check(imp["within"] == 4 and imp["hist_n"] == 4 and imp["dte"] == 3, "all 4 prior 2-day moves (4.0%..6.0%) within 8.0% implied")
    out_earn = c.earnings(symbol="NVDA")
    md_earn = out_earn["content"]
    check("Options-implied move" in md_earn and "±8.0%" in md_earn and "±12.40" in md_earn and "4 of 4 within implied" in md_earn,
          "scorecard prints implied move, dollar straddle, and realised comparison")
    check(any(s["name"] == "Nasdaq" and "delayed options chain" in s["detail"] for s in out_earn["sources"]),
          "sources cite Nasdaq options chain when Nasdaq answered")

    # DTE > 14 includes time-to-expiry caveat
    cal_far = dict(CAL, earnings_date="2026-12-16")
    install({"hist": HIST, "cal": cal_far, "eps": EPS, "rev": REV, "trend": TREND, "revs": REVS, "inc": INC},
            CLOSES, ("2026-11-17T14:00:00", 155.0), release=None, chain=(CHAIN_NVDA, "nasdaq"))
    md_far = c.earnings(symbol="NVDA")["content"]
    check("31 DTE — includes time to expiry" in md_far, "DTE > 14 prints time-to-expiry note")

    # Zero-bid fallback -> last_trade_price
    chain_last = dict(CHAIN_NVDA, bid=[0.0] * 8, ask=[0.0] * 8)
    imp_last = c._implied_move(chain_last, "2026-11-17", spot=155.0, today=dt.date(2026, 11, 17))
    check(imp_last is not None and imp_last["pricing"] == "last" and abs(imp_last["straddle"] - 12.40) < 1e-9,
          "zero bid/ask falls back cleanly to last_trade_price")

    # Friday after-hours report skips same-day Friday expiry -> picks following Friday
    fri_chain = [
        {"expiration": "2026-11-20", "strike": 100.0, "option_type": "call", "bid": 1.0, "ask": 1.2, "underlying_price": 100.0},
        {"expiration": "2026-11-20", "strike": 100.0, "option_type": "put", "bid": 1.0, "ask": 1.2, "underlying_price": 100.0},
        {"expiration": "2026-12-18", "strike": 100.0, "option_type": "call", "bid": 4.0, "ask": 4.2, "underlying_price": 100.0},
        {"expiration": "2026-12-18", "strike": 100.0, "option_type": "put", "bid": 3.8, "ask": 4.0, "underlying_price": 100.0},
    ]
    imp_fri = c._implied_move(fri_chain, "2026-11-20", spot=100.0, today=dt.date(2026, 11, 16), timing="after-hours")
    check(imp_fri is not None and imp_fri["expiry"] == "2026-12-18" and imp_fri["dte"] == 32,
          "Friday after-hours skips same-day expiry and computes DTE=32")

    # Deep OTM (>15% off spot) rejected -> None
    far_chain = [
        {"expiration": "2026-11-20", "strike": 200.0, "option_type": "call", "bid": 1.0, "ask": 1.2, "underlying_price": 100.0},
        {"expiration": "2026-11-20", "strike": 200.0, "option_type": "put", "bid": 99.0, "ask": 100.0, "underlying_price": 100.0},
    ]
    check(c._implied_move(far_chain, "2026-11-20", spot=100.0, today=dt.date(2026, 11, 17)) is None, "strike >15% from spot rejected")

    # Fallback: nasdaq error -> yfinance succeeds & non-US symbol skips API completely
    seen_provs = []

    def fallback_api(base, path, **kw):
        seen_provs.append(kw.get("provider"))
        if kw.get("provider") == "nasdaq":
            raise c.CompositeError("Nasdaq 500")
        return fri_chain if kw.get("provider") == "yfinance" else []

    c.api = fallback_api
    c._cache.clear()
    c._options_chain = saved_chain
    check(c._options_chain("SAP.DE") == (None, None) and c._options_chain("RY.TO") == (None, None) and not seen_provs,
          "non-US listings skip options chain requests")
    got_chain, got_prov = c._options_chain("AAPL")
    check(got_prov == "yfinance" and seen_provs == ["nasdaq", "yfinance"] and got_chain == fri_chain,
          "nasdaq failure falls back cleanly to yfinance")
    install({"hist": HIST, "cal": cal_far, "eps": EPS, "rev": REV, "trend": TREND, "revs": REVS, "inc": INC},
            CLOSES, ("2026-11-17T14:00:00", 100.0), release=None, chain=saved_chain)
    c.api = fallback_api
    yf_srcs = c.earnings(symbol="AAPL")["sources"]
    check(any(s["name"] == "Yahoo Finance" and "delayed options chain" in s["detail"] for s in yf_srcs),
          "sources attribute delayed options chain to Yahoo Finance on fallback")

    # Lazy options chain: only earnings() calls _options_chain; reports/release/history do not
    opt_calls = [0]

    def counting_chain(sym):
        opt_calls[0] += 1
        return fri_chain, "nasdaq"

    install({"hist": HIST, "cal": cal_far, "eps": EPS, "rev": REV, "trend": TREND, "revs": REVS, "inc": INC},
            CLOSES, ("2026-11-17T14:00:00", 100.0), release=None, chain=counting_chain)
    c.earnings_reports(symbol="AAPL")
    c.earnings_release(symbol="AAPL")
    c.earnings_history(symbol="AAPL")
    check(opt_calls[0] == 0, "earnings_reports/release/history do not call _options_chain")
    c.earnings(symbol="AAPL")
    check(opt_calls[0] == 1, "earnings() calls _options_chain once")

    # Single-flight: 4 concurrent threads calling _options_chain("DELL") coalesce to 1 C.api call
    import threading
    import time

    sf_calls = [0]
    sf_lock = threading.Lock()
    sf_barrier = threading.Barrier(4)

    def slow_chain_api(base, path, **kw):
        with sf_lock:
            sf_calls[0] += 1
        time.sleep(0.05)
        return fri_chain

    c.api = slow_chain_api
    c._cache.clear()
    c._options_chain = saved_chain
    sf_results = []

    def sf_worker():
        sf_barrier.wait()
        sf_results.append(c._options_chain("DELL"))

    sf_threads = [threading.Thread(target=sf_worker) for _ in range(4)]
    for t in sf_threads:
        t.start()
    for t in sf_threads:
        t.join(timeout=5)
    check(sf_calls[0] == 1 and len(sf_results) == 4 and all(r == (fri_chain, "nasdaq") for r in sf_results),
          "4 concurrent _options_chain calls coalesce to 1 C.api call")

    # All providers fail: 4 concurrent callers share one pass (3 providers = 3 calls, not 12); the miss is cached 60 s
    fail_calls = [0]
    fail_barrier = threading.Barrier(4)

    def failing_chain_api(base, path, **kw):
        with sf_lock:
            fail_calls[0] += 1
        time.sleep(0.05)
        raise c.CompositeError("Nasdaq rate limit active")

    c.api = failing_chain_api
    c._cache.clear()
    fail_results = []

    def fail_worker():
        fail_barrier.wait()
        fail_results.append(c._options_chain("DELL"))

    fail_threads = [threading.Thread(target=fail_worker) for _ in range(4)]
    for t in fail_threads:
        t.start()
    for t in fail_threads:
        t.join(timeout=5)
    check(fail_calls[0] == 3 and fail_results == [(None, None)] * 4,
          f"failed chain runs once for 4 concurrent callers ({fail_calls[0]} provider calls)")
    hit = c._cache[("opt_chain", "DELL")]
    c._cache[("opt_chain", "DELL")] = (hit[0] - 61, hit[1])
    c._options_chain("DELL")
    check(fail_calls[0] == 6, "cached miss expires after 60 s and the chain is retried")
finally:
    c.api = saved_api
    c._est_raw = saved_raw
    c._report_info, c._earnings_release, c._last_price, c.history, c._today = saved[1], saved[2], saved[3], saved[4], saved[5]
    c._yahoo_quarter_chart, c._options_chain = saved_chart, saved_chain
    c._cache.clear()

# ---- the analyst-actions patch on the real Yahoo fetcher: no 0.00 targets, newest first
import importlib.util  # noqa: E402

from _common import ROOT  # noqa: E402

spec = importlib.util.spec_from_file_location("run_backend", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)
try:
    from openbb_yfinance.models import price_target as ypt
except ImportError:
    ypt = None
if ypt is not None:
    orig = ypt.YFinancePriceTargetFetcher.transform_data
    try:
        applied = []
        rb.install_yfinance_price_target_fix(applied)
        rows = ypt.YFinancePriceTargetFetcher.transform_data(None, [
            {"published_date": dt.date(2026, 11, 10), "symbol": "NVDA", "analyst_firm": "Cantor Fitzgerald", "price_target": 250.0, "price_target_previous": 350.0},
            {"published_date": dt.date(2026, 11, 12), "symbol": "NVDA", "analyst_firm": "Oppenheimer", "price_target": 0.0, "price_target_previous": 0.0,
             "rating_current": "Perform", "rating_previous": "Outperform"}])
        check(applied and [r.analyst_firm for r in rows] == ["Oppenheimer", "Cantor Fitzgerald"], "analyst actions newest first")
        check(rows[0].price_target is None and rows[0].price_target_previous is None and rows[1].price_target == 250.0, "no target published: blank, not 0.00")
    finally:
        ypt.YFinancePriceTargetFetcher.transform_data = orig

done("test_earnings")
