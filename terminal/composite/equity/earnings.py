"""Earnings model, consensus history, implied move, and 8-K release views."""

from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import composite as C

# One report as a desk follows it: the consensus going in (EPS and revenue: mean, range, analysts), what was reported,
# the surprise, the press release, the market's reaction, the consensus for the quarter the company will guide, and the
# track record. A surprise is only ever computed from one source's consensus and reported figure: Yahoo Finance gives
# both on the same (adjusted) basis. Nasdaq's EPS history is on another basis (NVDA Feb 2026: 1.57 vs Yahoo's 1.62), so
# Nasdaq is used only for the report's timing and fiscal quarter. After a report Yahoo moves its consensus to the next
# quarter, so the consensus seen before each report is saved (local cache) to set revenue against it the day after.
EARN_PERIODS = (("0q", "Current quarter"), ("+1q", "Next quarter"), ("0y", "Current fiscal year"), ("+1y", "Next fiscal year"))

_STORE_LOCK = threading.Lock()


def _est_raw(sym):
    """Yahoo Finance answers for one company (EPS history, calendar, estimates, revisions, quarterly revenue), 10 minutes."""
    jobs = {"hist": ("equity/fundamental/historical_eps", {"provider": "yfinance"}), "cal": ("yfinance/company_calendar", {}),
            "eps": ("yfinance/estimates/earnings", {"provider": "yfinance"}), "rev": ("yfinance/estimates/revenue", {"provider": "yfinance"}),
            "trend": ("yfinance/estimates/eps_trend", {"provider": "yfinance"}), "revs": ("yfinance/estimates/eps_revisions", {"provider": "yfinance"}),
            "inc": ("equity/fundamental/income", {"provider": "yfinance", "period": "quarter", "limit": 4})}

    def one(k):
        path, kw = jobs[k]
        try:
            rows = C.api(C.V5, path, symbol=sym, **kw)
        except C.CompositeError:
            return k, None                        # did not answer (Yahoo's "Invalid Crumb" right after a start, throttling)
        return k, rows if isinstance(rows, list) else [rows] if rows else []

    def run():
        with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
            out = dict(ex.map(one, jobs))
        if any(v is None for v in out.values()):  # once more, one at a time: Yahoo's session usually recovers within a second
            time.sleep(1.0)
            out.update(dict(one(k) for k, v in list(out.items()) if v is None))
        failed = [k for k, v in out.items() if v is None]
        out = {k: v or [] for k, v in out.items()}
        out["cal"] = out["cal"][0] if out["cal"] else {}
        out["_failed"] = failed
        if not (out["hist"] or out["eps"] or out["cal"]):
            if failed:
                raise C.CompositeError("Yahoo Finance did not answer just now (it may be throttling). Press reload in a minute.")
            raise C.CompositeError(f"Yahoo Finance has no estimates for '{sym}'. Non-U.S. listings need their exchange suffix (7203.T, SAP.DE).")
        # a partial answer is kept one minute, not ten: never taken for the whole picture
        return C.Partial(out, [f"Yahoo Finance estimates ({k})" for k in failed]) if failed else out
    return C.cached(("est_raw", sym), 600, run)


def _report_info(sym, date):
    """Timing ('after-hours' / 'pre-market') and fiscal quarter (YYYY-MM) of one report, from Nasdaq's earnings calendar
    for that day (U.S. listings); {} when Nasdaq does not list it."""
    def run():
        try:
            return C.api(C.V5, "equity/calendar/earnings", provider="nasdaq", start_date=date, end_date=date) or []
        except C.CompositeError as e:
            C.partial_from(e, "Nasdaq earnings calendar")
            return []
    r = next((x for x in C.cached(("nasdaq_earn_cal", date), 3600, run) if str(x.get("symbol") or "").upper() == sym), None)
    if not r:
        return {}
    t = str(r.get("reporting_time") or "").lower()
    return {"timing": "after-hours" if "after" in t else "pre-market" if "pre" in t else None, "period": str(r.get("period_ending") or "")[:7] or None}


def _timing_from_acceptance(accepted, date):
    """'after-hours' / 'pre-market' from the SEC acceptance time of the earnings 8-K (UTC), in New York time, when it was
    accepted on the report date; None otherwise. Nasdaq's calendar says "not-supplied" once a report is past (NVDA
    2026-11-17, accepted 21:20:51Z = 16:20 ET), and the reaction then used the close before the report day."""
    s = str(accepted or "")
    if len(s) < 16 or "T" not in s:
        return None
    try:
        from zoneinfo import ZoneInfo
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is None:
            return None
        ny = t.astimezone(ZoneInfo("America/New_York"))
    except (ValueError, KeyError):
        return None
    if ny.date().isoformat() != str(date)[:10]:
        return None
    hm = ny.strftime("%H:%M")
    return "after-hours" if hm >= "16:00" else "pre-market" if hm < "09:30" else None


def _earnings_release(sym, date):
    """The SEC 8-K with Item 2.02 (results of operations: the earnings release) for the report on `date`: filed from the
    day before to three days after it. None for companies outside SEC EDGAR."""
    def run():
        try:
            rows = C.api(C.V5, "equity/fundamental/filings", provider="sec", symbol=sym, form_type="8-K", limit=60) or []
        except C.CompositeError as e:
            C.partial_from(e, "SEC 8-K filings")
            return []
        return [{"url": r.get("filing_detail_url") or r.get("report_url"), "doc": r.get("report_url"), "filed": str(r.get("accepted_date") or r.get("filing_date") or ""),
                 "date": str(r.get("filing_date") or "")[:10]} for r in rows if "2.02" in str(r.get("items") or "")]
    d = dt.date.fromisoformat(date)
    lo, hi = (d - dt.timedelta(days=1)).isoformat(), (d + dt.timedelta(days=3)).isoformat()
    return next((r for r in sorted(C.cached(("8k_202", sym), 900, run), key=lambda r: r["date"]) if lo <= r["date"] <= hi), None)


def _filing_documents(url):
    """[{"type": "EX-99.1", "url": ...}, ...]: the documents of one SEC filing (from its primary document's URL), a day."""
    def run():
        try:
            res = C.api(C.V5, "sec/filing_headers", url=url)
        except C.CompositeError as e:
            C.partial_from(e, "SEC filing documents")
            return []
        res = res[0] if isinstance(res, list) and res else res if isinstance(res, dict) else {}
        return [{"type": str(x.get("type") or "").upper(), "url": x.get("url")} for x in res.get("document_urls") or [] if x.get("url")]
    return C.cached(("filing_docs", url), 86400, run)


def _last_price(sym):
    """(exchange-time ISO, price) of the latest 5-minute bar including pre-market and after-hours trading, else None."""
    def run():
        try:
            rows = C.api(C.V5, "equity/price/historical", symbol=sym, provider="yfinance", interval="5m", extended_hours=True,
                       start_date=(dt.date.today() - dt.timedelta(days=4)).isoformat()) or []
        except C.CompositeError as e:
            C.partial_from(e, "Yahoo Finance intraday price")
            return None
        rows = [(str(r.get("date"))[:19], c) for r in rows if (c := C.num(r.get("close"))) is not None]
        return rows[-1] if rows else None
    return C.cached(("last_price", sym), 60, run)


def _consensus_store(sym, date=None, entry=None):
    """Consensus seen before each report, {report date: {"eps": [mean, low, high, n], "rev": [...], "seen": iso}}, saved in the
    local cache so the reported quarter can be set against it after Yahoo has moved on. With date and entry: save it."""
    path = C.CACHE_DIR / "earnings_consensus.json"
    with C._STORE_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        mine = data.get(sym, {})
        if date and entry and {k: mine.get(date, {}).get(k) for k in entry} != entry:
            mine[date] = dict(entry, seen=dt.date.today().isoformat())
            data[sym] = dict(sorted(mine.items())[-8:])
            C._write_json(path, data)
        return dict(data.get(sym, {}))


def _f(v):
    return C.num(v)


def _surprise(actual, est):
    return (actual - est) / abs(est) if actual is not None and est else None


def _eps_surprise(actual, est, what=None):
    """EPS surprise as a fraction, inside its plausible range (+-500%: a near-zero estimate makes it meaningless)."""
    return C.banded("eps_surprise_pct", C._surprise(actual, est), what)


def _cons(row):
    row = row or {}
    return {"est": C._f(row.get("mean")), "low": C._f(row.get("low")), "high": C._f(row.get("high")), "n": row.get("number_of_analysts")}


def _two_day(closes, d):
    """Close before the report day -> close of the first session after it, minus 1: one window for reports made before
    the open and after the close."""
    before = [c for x, c in closes if x < d]
    after = [c for x, c in closes if x > d]
    move = after[0] / before[-1] - 1 if before and after and before[-1] else None
    return C.banded("move_2d_pct", move, f"the {d} report")      # -80%..+300%: outside it is a bad print


def _yahoo_q0_is(reported, d, eps, saved=None):
    """Is Yahoo's "current quarter" (0q) still the quarter reported at `d`? Yahoo rolls its estimates to the next quarter
    around a report, before or after it posts the actual (NVDA 2026-11-17: rolled about 90 minutes after the release,
    with the actual not yet posted). The reported quarter's year-ago EPS is the EPS reported about a year before `d`;
    after the roll, 0q's year-ago EPS is a quarter later. Without a year-ago report: 0q still within 2% of the consensus
    saved for `d` (if any) counts as not rolled."""
    year_ago = next((r["actual"] for r in reported if r["date"] < d and 330 <= (dt.date.fromisoformat(d) - dt.date.fromisoformat(r["date"])).days <= 400), None)
    q0 = eps.get("0q") or {}
    ya = C._f(q0.get("year_ago_eps"))
    if year_ago is not None and ya is not None:
        return abs(ya - year_ago) < 0.005
    m0, s0 = C._f(q0.get("mean")), ((saved or {}).get("eps") or [None])[0]
    return not (m0 is not None and s0 and abs(m0 / s0 - 1) > 0.02)


def _saved_cons(saved):
    se, sr = (saved or {}).get("eps") or [None] * 4, (saved or {}).get("rev") or [None] * 4
    return ({"est": se[0], "low": se[1], "high": se[2], "n": se[3]}, {"est": sr[0], "low": sr[1], "high": sr[2], "n": sr[3]})


def _yahoo_quarter_chart(sym):
    """Yahoo's earnings summary (quoteSummary module "earnings"): per quarter label, oldest first, (label, revenue from
    financialsChart, reported EPS from earningsChart), and the currency of its financials. Yahoo fills it with the
    release, while its quarterly statement can lag for days (NVDA 2026-11-17: statement revenue still empty on 11-20,
    the summary had 115.844B). No OpenBB route exposes it and Yahoo answers 401 without a session crumb, so it goes
    through the session of yfinance (installed in this environment for the backends). 10 minutes; None when it is not
    available (kept one minute)."""
    def run():
        try:
            import yfinance as yf
            j = yf.Ticker(sym)._data.get_raw_json(f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{urllib.parse.quote(sym)}",
                                                  params={"modules": "earnings"}, timeout=20)
            e = ((((j or {}).get("quoteSummary") or {}).get("result") or [{}])[0] or {}).get("earnings") or {}
            raw = lambda x: C._f(x.get("raw")) if isinstance(x, dict) else None  # noqa: E731
            eps = {q.get("date"): raw(q.get("actual")) for q in (e.get("earningsChart") or {}).get("quarterly") or []}
            qs = [(q.get("date"), raw(q.get("revenue")), eps.get(q.get("date")))
                  for q in (e.get("financialsChart") or {}).get("quarterly") or [] if q.get("date")]
        except Exception:                         # noqa: BLE001  (module missing, network, throttling, odd JSON: no figure)
            return C.Partial(None)                # no figure: kept one minute (not reported: the views say what is blank)
        return {"currency": e.get("financialCurrency"), "quarters": qs} if qs else C.Partial(None)
    return C.cached(("yahoo_qchart", sym), 600, run)


def _summary_revenue(chart, reported_eps, statement, ccy):
    """Revenue of the latest report from Yahoo's earnings summary, for use while Yahoo's quarterly statement lacks the
    quarter, and only when the figure is tied to that report: the summary's last quarter carries the EPS Yahoo reports
    for it (to the cent), the quarter before carries the revenue of the latest quarter in the statement (within 0.1%),
    and the summary's financials are in the card's currency. `statement`: [(period end, revenue)], newest first.
    None otherwise (the card then says the figure is not in Yahoo's data yet)."""
    qs = (chart or {}).get("quarters") or []
    known = [v for _, v in statement if v is not None]
    if len(qs) < 2 or reported_eps is None or not known or not known[0] or not ccy or chart.get("currency") != ccy:
        return None
    (_, rev, eps), (_, prev, _) = qs[-1], qs[-2]
    if rev is None or eps is None or prev is None or abs(eps - reported_eps) >= 0.005 or abs(prev / known[0] - 1) > 0.001:
        return None
    return rev


def _opt_leg_price(bid, ask, mark, last):
    """Best honest price for one option leg -> (price, basis) or (None, None)."""
    b, a, m, ls = C._f(bid), C._f(ask), C._f(mark), C._f(last)
    if b is not None and a is not None and b > 0 and a >= b:
        mid = (b + a) / 2
        if mid > 0 and (a - b) / mid <= 0.50:
            return mid, "mid"
    if ls is not None and ls > 0:
        return ls, "last"
    if m is not None and m > 0 and (a is None or a <= 0 or m <= a):
        return m, "mark"
    return None, None


def _parse_option_chain(raw):
    """Normalize Cboe options chain (columnar dict or list of row dicts) -> (rows, spot, as_of)."""
    if isinstance(raw, dict):
        exps = raw.get("expiration")
        strikes = raw.get("strike")
        types = raw.get("option_type")
        if not (isinstance(exps, list) and isinstance(strikes, list) and isinstance(types, list)):
            return [], None, None
        n = min(len(exps), len(strikes), len(types))
        bids = raw.get("bid") or []
        asks = raw.get("ask") or []
        marks = raw.get("mark") or []
        lasts = raw.get("last_trade_price") or []
        spots = raw.get("underlying_price") or []
        eods = raw.get("eod_date") or []
        at = lambda lst, i: lst[i] if isinstance(lst, list) and i < len(lst) else None  # noqa: E731
        rows = [
            {
                "expiration": str(exps[i] or "")[:10],
                "strike": C._f(strikes[i]),
                "option_type": str(types[i] or "").strip().lower(),
                "bid": at(bids, i),
                "ask": at(asks, i),
                "mark": at(marks, i),
                "last": at(lasts, i),
            }
            for i in range(n)
        ]
        spot = next((v for v in (C._f(s) for s in spots) if v is not None and v > 0), None) if isinstance(spots, list) else None
        as_of = next((str(e)[:10] for e in eods if e), None) if isinstance(eods, list) else None
        return rows, spot, as_of
    if isinstance(raw, list):
        rows = [
            {
                "expiration": str(r.get("expiration") or "")[:10],
                "strike": C._f(r.get("strike")),
                "option_type": str(r.get("option_type") or "").strip().lower(),
                "bid": r.get("bid"),
                "ask": r.get("ask"),
                "mark": r.get("mark"),
                "last": r.get("last_trade_price"),
            }
            for r in raw
            if isinstance(r, dict)
        ]
        spot = next((v for v in (C._f(r.get("underlying_price")) for r in raw if isinstance(r, dict)) if v is not None and v > 0), None)
        as_of = next((str(r.get("eod_date"))[:10] for r in raw if isinstance(r, dict) and r.get("eod_date")), None)
        return rows, spot, as_of
    return [], None, None


def _implied_move(chain, target_date, spot=None, today=None, timing=None):
    """ATM straddle / spot from the first valid expiry covering `target_date`."""
    if not chain or not target_date:
        return None
    t_str = str(target_date)[:10]
    try:
        td = dt.date.fromisoformat(t_str)
    except ValueError:
        return None
    now_d = today or C._today()
    if td < now_d or (td - now_d).days > 120:
        return None
    rows, chain_spot, as_of = C._parse_option_chain(chain)
    spot_from = "chain" if chain_spot else "close"
    spot = chain_spot or C._f(spot)
    if not spot or spot <= 0:
        return None
    need_after = (timing == "after-hours" and td.weekday() == 4)
    by_exp = {}
    for r in rows:
        exp, k, ot = r["expiration"], r["strike"], r["option_type"]
        if not exp or k is None or ot not in ("call", "put"):
            continue
        if (exp <= t_str) if need_after else (exp < t_str):
            continue
        px, basis = C._opt_leg_price(r["bid"], r["ask"], r["mark"], r["last"])
        if px is None:
            continue
        by_exp.setdefault(exp, {"call": {}, "put": {}})[ot][k] = (px, basis)
    for exp in sorted(by_exp):
        calls, puts = by_exp[exp]["call"], by_exp[exp]["put"]
        common = [k for k in calls if k in puts]
        if not common:
            continue
        strike = min(common, key=lambda k: (abs(k - spot), k))
        if abs(strike - spot) / spot > 0.15:
            return None
        c_px, c_basis = calls[strike]
        p_px, p_basis = puts[strike]
        straddle = c_px + p_px
        pricing = "mid" if (c_basis == "mid" and p_basis == "mid") else ("last" if "last" in (c_basis, p_basis) else "mark")
        dte = (dt.date.fromisoformat(exp) - now_d).days
        return {
            "expiry": exp,
            "dte": dte,
            "strike": strike,
            "spot": spot,
            "spot_from": spot_from,                  # "close" when the chain had no underlying price (flagged by the caller)
            "straddle": straddle,
            "move": straddle / spot,
            "pricing": pricing,
            "as_of": as_of,
            "target_date": t_str,
        }
    return None


def _options_chain(sym):
    """Delayed options chain for U.S.-listed stocks (Nasdaq -> Yahoo Finance -> Cboe), cached 15 minutes. A miss on
    every provider is cached for 1 minute so concurrent cards for the same symbol share it instead of re-running the chain."""
    if not sym or re.search(r"\.[A-Z]{2,4}$|[=^]", sym):
        return None, None
    key = ("opt_chain", sym)

    def _fresh(hit):
        return hit and time.monotonic() - hit[0] < (900 if hit[1][0] is not None else 60)

    with C._guard:
        hit = C._cache.get(key)
        if _fresh(hit):
            return hit[1]
        lock = C._locks.setdefault(key, threading.Lock())
    with lock:
        with C._guard:
            hit = C._cache.get(key)
            if _fresh(hit):
                return hit[1]
        for prov in ("nasdaq", "yfinance", "cboe"):
            try:
                res = C.api(C.V5, "derivatives/options/chains", _timeout=15, symbol=sym, provider=prov)
            except C.CompositeError:
                continue
            has_rows = bool(res) if isinstance(res, list) else (isinstance(res, dict) and bool(res.get("expiration")))
            if has_rows:
                val = (res, prov)
                with C._guard:
                    C._cache[key] = (time.monotonic(), val)
                return val
        with C._guard:
            C._cache[key] = (time.monotonic(), (None, None))
        return None, None


def _earn_model(sym, today=None, report=None, need_implied=True):
    """Everything the Estimates views show, from one set of answers (see the section comment). `report` (a report date)
    picks an earlier report; empty or unknown means the latest."""
    today = today or C._today()
    t_iso = today.isoformat()
    raw = C._est_raw(sym)
    reported = sorted(({"date": str(r.get("date"))[:10], "est": C._f(r.get("eps_estimated")), "actual": C._f(r.get("eps_actual"))}
                       for r in raw["hist"] if C._f(r.get("eps_actual")) is not None and str(r.get("date") or "")[:10] <= t_iso),
                      key=lambda r: r["date"], reverse=True)
    eps, rev = ({str(r.get("period")): r for r in raw[k]} for k in ("eps", "rev"))
    cal = raw["cal"] or {}
    cal_d = str(cal.get("earnings_date") or "")[:10] or None
    last_d = reported[0]["date"] if reported else None
    # results due (today or in the last week) but not yet in Yahoo's history
    pending = bool(cal_d) and cal_d <= t_iso and (last_d is None or cal_d > last_d) and (today - dt.date.fromisoformat(cal_d)).days <= 7
    store = C._consensus_store(sym)
    want = str(report or "")[:10] or None
    missing = {"cal": "earnings calendar", "hist": "EPS history", "eps": "EPS estimates", "rev": "revenue estimates", "inc": "quarterly revenue"}
    gone = [missing[k] for k in raw.get("_failed", []) if k in missing]
    note = (f"Yahoo Finance did not answer for its {', '.join(gone)} just now; retried within a minute. Until then this card can miss "
            "a report made today or show blanks.") if gone else None
    if want and want not in {r["date"] for r in reported} and not (pending and want == cal_d):
        note, want = " ".join(x for x in (note, f"No report on {want} in Yahoo's data for {sym}: showing the latest.") if x), None
    is_latest = want is None or want == (cal_d if pending else last_d)
    if pending and is_latest:
        d = cal_d
        if C._yahoo_q0_is(reported, d, eps, store.get(d)):
            e_c = C._cons(eps.get("0q")) if eps.get("0q") else {"est": C._f(cal.get("earnings_average")), "low": C._f(cal.get("earnings_low")), "high": C._f(cal.get("earnings_high")), "n": None}
            r_c = C._cons(rev.get("0q")) if rev.get("0q") else {"est": C._f(cal.get("revenue_average")), "low": C._f(cal.get("revenue_low")), "high": C._f(cal.get("revenue_high")), "n": None}
            nq_key = "+1q"
            C._consensus_store(sym, d, {"eps": [e_c[k] for k in ("est", "low", "high", "n")], "rev": [r_c[k] for k in ("est", "low", "high", "n")]})
        else:                                     # Yahoo has moved on: the consensus going in is the one saved before the roll
            e_c, r_c = C._saved_cons(store.get(d))
            nq_key = "0q"
        e_act = None
    elif reported:
        d = want or last_d
        top = next(r for r in reported if r["date"] == d)
        se, r_c = C._saved_cons(store.get(d))
        e_c = dict(se, est=top["est"])            # consensus and actual from Yahoo's history, ranges as saved before the report
        e_act = top["actual"]
        pending = False
        nq_key = "+1q" if d == last_d and C._yahoo_q0_is(reported, d, eps, store.get(d)) and eps.get("+1q") else "0q"
    else:
        raise C.CompositeError(f"Yahoo Finance has no reported earnings for '{sym}' yet.")
    nxt_date = cal_d if cal_d and cal_d > t_iso and (last_d is None or cal_d > last_d) else None
    next_q = {"eps": C._cons(eps.get(nq_key)), "rev": C._cons(rev.get(nq_key))}
    if nxt_date:                                  # an announced report ahead: save its consensus as it stands
        C._consensus_store(sym, nxt_date, {"eps": [next_q["eps"][k] for k in ("est", "low", "high", "n")], "rev": [next_q["rev"][k] for k in ("est", "low", "high", "n")]})
    info = C._report_info(sym, d)
    # reported revenue: Yahoo's quarterly statement for the quarter Nasdaq names, else a quarter ending up to 80 days before
    lo = (dt.date.fromisoformat(d) - dt.timedelta(days=80)).isoformat()
    quarters = sorted(((str(r.get("period_ending") or "")[:10], C._f(r.get("total_revenue"))) for r in raw["inc"]), reverse=True)
    r_act = next((v for p, v in quarters if v is not None and (p[:7] == info["period"] if info.get("period") else lo <= p <= d)), None)
    ccy = next((r.get("currency") for r in raw["eps"] if r.get("currency")), None) or ""
    rev_from = None if r_act is None else "statement"
    if r_act is None and not pending and d == last_d and (today - dt.date.fromisoformat(d)).days <= 45:
        r_act = C._summary_revenue(C._yahoo_quarter_chart(sym), e_act, quarters, ccy)   # the statement lags the release
        rev_from = "summary" if r_act is not None else None
    release = C._earnings_release(sym, d)
    # reaction: live (extended hours) for three days after the report, from the right close; afterwards the 2-day move
    ser = C.history([sym], today - dt.timedelta(days=3 * 365 + 120)).get(sym) or []
    closes = [(str(x)[:10], cl) for x, cl in ser]
    timing = info.get("timing") or C._timing_from_acceptance((release or {}).get("filed"), d)
    reaction = {"base_date": None, "base": None, "price": None, "move": None, "when": None}
    live = (today - dt.date.fromisoformat(d)).days <= 3 and (not pending or release is not None)
    lp = None
    if live:
        base_rows = [p for p in closes if (p[0] <= d if timing == "after-hours" else p[0] < d)]
        lp = C._last_price(sym)
        if base_rows and (lp or closes):
            reaction["base_date"], reaction["base"] = base_rows[-1]
            if lp and (not closes or lp[0][:10] >= closes[-1][0]):
                hm = lp[0][11:16]
                us = "." not in sym and "=" not in sym and "-" not in sym
                session = ("after hours" if hm >= "16:00" else "pre-market" if hm < "09:30" else "at") if us and hm else "at"
                reaction.update(price=lp[1], when=f"{session} {hm}".strip())
            else:
                reaction.update(price=closes[-1][1], when=f"close {closes[-1][0]}")
            reaction["move"] = reaction["price"] / reaction["base"] - 1 if reaction["base"] else None
    elif not pending:
        reaction.update(move=C._two_day(closes, d), when="2-day move")
    hist_rows = [dict(r, surprise=C._eps_surprise(r["actual"], r["est"], f"the {r['date']} report"), move_2d=(mv := C._two_day(closes, r["date"])),
                      move_end=next((x for x, _ in closes if x > r["date"]), None) if mv is not None else None)    # the close the move ends on
                 for r in reported[:12]]
    last8 = hist_rows[:8]
    moves = [abs(r["move_2d"]) for r in last8 if r["move_2d"] is not None]
    surp = [r["surprise"] for r in last8 if r["surprise"] is not None]
    track = {"n": len(last8), "beats": sum(1 for r in last8 if r["est"] is not None and r["actual"] > r["est"]),
             "avg_surprise": sum(surp) / len(surp) if surp else None, "avg_abs_move": sum(moves) / len(moves) if moves else None}
    implied = None
    if need_implied and is_latest:
        imp_target = d if (pending and d >= t_iso and not release) else nxt_date
        imp_timing = timing if (pending and d >= t_iso and not release) else None
        if imp_target:
            spot_fallback = closes[-1][1] if closes else None
            got = C._options_chain(sym)
            chain, opt_prov = got if isinstance(got, tuple) else (got, "cboe")
            implied = C._implied_move(chain, imp_target, spot=spot_fallback, today=today, timing=imp_timing)
            if implied and implied.get("spot_from") == "close":
                C.note_fallback("the last daily close", "the options chain has no underlying price for the implied move")
            if implied:
                implied["provider"] = opt_prov or "nasdaq"
                if moves:
                    implied["within"] = sum(1 for x in moves if x <= implied["move"])
                    implied["hist_n"] = len(moves)
    # the newest data point used: the last close or live price, and the newest report already made
    as_of = C.newest_date(([closes[-1][0]] if closes else []) + ([lp[0]] if lp else []) + [r["date"] for r in reported])
    return {"symbol": sym, "currency": ccy, "track": track, "history": hist_rows, "raw": raw, "is_latest": is_latest, "note": note,
            "implied": implied, "as_of": as_of,
            "latest": {"date": d, "pending": pending, "timing": timing, "quarter": info.get("period"), "release": release, "reaction": reaction,
                       "eps": dict(e_c, actual=e_act, surprise=C._eps_surprise(e_act, e_c["est"], f"the {d} report")),
                       "rev": dict(r_c, actual=r_act, surprise=C._surprise(r_act, r_c["est"])), "rev_from": rev_from},
            "next": {"date": nxt_date, **next_q}, "next_quarter": next_q}


def _eps_txt(v):
    return "–" if v is None else f"{v:,.2f}"


def _pct_txt(v, digits=1):
    return "–" if v is None else f"{v * 100:+.{digits}f}%"


def _range_txt(c, fmt):
    return f"{fmt(c['low'])} – {fmt(c['high'])}" if c.get("low") is not None and c.get("high") is not None else "–"


def _earn_sources(m):
    rev_src = ", latest quarter's revenue from its earnings summary until the statement has it" if m["latest"].get("rev_from") == "summary" else ""
    out = [C.src("Yahoo Finance", "EPS consensus and reported EPS (same basis), revenue consensus and quarterly revenue" + rev_src + ", prices incl. extended hours",
               C.YAHOO_URL + "quote/" + urllib.parse.quote(m["symbol"]) + "/analysis")]
    if m["latest"].get("timing") or m["latest"].get("quarter"):
        out.append(C.src("Nasdaq", "report timing and fiscal quarter (earnings calendar)", "https://www.nasdaq.com/market-activity/earnings"))
    if m["latest"].get("release"):
        out.append(C.src("SEC", "earnings release: Form 8-K, Item 2.02", m["latest"]["release"]["url"]))
    if m.get("implied"):
        imp = m["implied"]
        prov = imp.get("provider") or "cboe"
        sym_q = urllib.parse.quote(m["symbol"])
        meta = {
            "nasdaq": ("Nasdaq", f"https://www.nasdaq.com/market-activity/stocks/{m['symbol'].lower()}/option-chain"),
            "yfinance": ("Yahoo Finance", f"{C.YAHOO_URL}quote/{sym_q}/options"),
            "cboe": ("Cboe", "https://www.cboe.com/delayed_quotes/"),
        }.get(prov, ("Cboe", "https://www.cboe.com/delayed_quotes/"))
        out.append(C.src(meta[0], f"delayed options chain (exp {imp['expiry']}, ATM straddle)", meta[1]))
    return out


def earnings_call_link(sym):
    """Where to hear or read the call (owner 2026-11-19). U.S. listings: MarketBeat's earnings page, each report with
    "Listen to Conference Call" and "Read Transcript", no sign-in; any exchange in the path redirects to the right one
    (NYSE/NVDA -> NASDAQ/NVDA, probed). MarketBeat has no earnings page for other listings (LON/SHEL lands on the quote
    page): those keep Yahoo's earnings-call page, which may ask for a sign-in. Pages, not cards: neither can be framed."""
    if "." not in sym and "=" not in sym and "^" not in sym:
        return f"[Earnings calls: listen or read (MarketBeat)](https://www.marketbeat.com/stocks/NYSE/{urllib.parse.quote(sym)}/earnings/)"
    return f"[Earnings calls (Yahoo; may ask you to sign in)]({C.YAHOO_URL}quote/{urllib.parse.quote(sym)}/earnings-calls/)"


def earnings(symbol="AAPL", report=None, **_):
    """Earnings scorecard: consensus vs reported for the latest (or the picked) report, the reaction, the next quarter's
    consensus, the record."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]
    m = C._earn_model(sym, report=report)
    lt, d, today = m["latest"], m["latest"]["date"], C._today().isoformat()
    when = {"after-hours": " after the close", "pre-market": " before the open"}.get(lt["timing"], "")
    if lt["pending"]:
        head = (f"Reported today{when}" if lt["release"] else f"Reports today{when}") if d == today else f"Reported {d}{when}: results not yet in Yahoo's data"
    else:
        head = f"{'Latest report' if m['is_latest'] else 'Report'}: {d}{when}"
    quarter = f" · fiscal quarter ended {dt.date.fromisoformat(lt['quarter'] + '-01').strftime('%b %Y')}" if lt.get("quarter") else ""
    rel = f" · [Press release (SEC 8-K)]({lt['release']['url']})" if lt["release"] else ""
    rel += " · " + C.earnings_call_link(sym)
    big = lambda v: "–" if v is None else C._big(v)                    # noqa: E731
    e, r = lt["eps"], lt["rev"]
    waiting = "see release" if lt["release"] else "pending"        # Yahoo posts the actual hours after the release
    e_rep = waiting if lt["pending"] and e["actual"] is None else C._eps_txt(e["actual"])
    r_rep = waiting if r["actual"] is None and (lt["pending"] or d >= today) else "not yet in Yahoo's data" if r["actual"] is None else big(r["actual"])
    e_cons = C._eps_txt(e["est"]) if e["est"] is not None else "not recorded"
    r_cons = big(r["est"]) if r["est"] is not None else "not recorded"
    md = ([f"*{m['note']}*", ""] if m["note"] else []) + [f"#### {sym}: {head}{quarter}{rel}", "",
          "| | Consensus | Range | Analysts | Reported | Surprise |", "|---|--:|--:|--:|--:|--:|",
          f"| EPS | {e_cons} | {C._range_txt(e, C._eps_txt)} | {e['n'] or '–'} | {e_rep} | {C._pct_txt(e['surprise'])} |",
          f"| Revenue | {r_cons} | {C._range_txt(r, big)} | {r['n'] or '–'} | {r_rep} | {C._pct_txt(r['surprise'])} |", ""]
    rc = lt["reaction"]
    if rc["move"] is not None and rc["price"] is not None:
        md.append(f"**Market reaction:** {rc['price']:,.2f} {rc['when']} vs {rc['base']:,.2f} close {rc['base_date']}: **{C._pct_txt(rc['move'])}**")
    elif rc["move"] is not None:
        md.append(f"**Move around the report** (close before → day after): **{C._pct_txt(rc['move'])}**")
    nq = m["next_quarter"]
    if m["is_latest"]:                            # what lies ahead belongs to the latest report only
        lead = "**Next quarter** (the one the company guides for)" if lt["pending"] else \
            f"**Next report: {m['next']['date'] or 'date not announced'}** · consensus"
        md += ["", f"{lead}: revenue {big(nq['rev']['est'])} ({C._range_txt(nq['rev'], big)}, {nq['rev']['n'] or '–'} analysts) · "
                   f"EPS {C._eps_txt(nq['eps']['est'])} ({C._range_txt(nq['eps'], C._eps_txt)}, {nq['eps']['n'] or '–'})"]
    t = m["track"]
    if t["n"]:
        mv = f" · average move around the report ±{t['avg_abs_move'] * 100:.1f}%" if t["avg_abs_move"] is not None else ""
        md += ["", f"**Track record, last {t['n']} reports:** EPS above consensus {t['beats']} of {t['n']} · average surprise {C._pct_txt(t['avg_surprise'])}{mv}"]
    imp = m.get("implied") if m["is_latest"] else None
    if imp:
        dte_txt = f", {imp['dte']} DTE — includes time to expiry" if imp["dte"] > 14 else ""
        px_txt = f" ({imp['pricing']} quotes)" if imp["pricing"] != "mid" else ""
        vs_txt = ""
        if t["avg_abs_move"] is not None:
            hist_n = imp.get("hist_n", t["n"])
            within_txt = f"; {imp['within']} of {hist_n} within implied" if hist_n else ""
            vs_txt = f" vs. **±{t['avg_abs_move'] * 100:.1f}%** average realised (last {hist_n} reports{within_txt})"
        md += ["", f"**Options-implied move** (exp {imp['expiry']}{dte_txt}, ATM straddle / spot at {imp['strike']:,.2f} strike{px_txt}): **±{imp['move'] * 100:.1f}%** (±{imp['straddle']:,.2f}){vs_txt}"]
    if lt.get("rev_from") == "summary":
        md += ["", "*Reported revenue: from Yahoo's earnings summary, as its quarterly statement does not have this quarter yet; "
                   "used because the summary shows this report's EPS and the previous quarter's revenue as in the statement.*"]
    md += ["", f"*{m['currency'] or 'Local currency'}. Consensus and reported EPS from Yahoo Finance on the same (adjusted) basis; reported revenue from Yahoo's "
               "quarterly statement once posted (hours to a day after the release: until then, the release beside this card). Yahoo moves its consensus to the next quarter around a report, so the consensus of a report "
               "once it is due is the one this terminal saw before the move. Pick an earlier report above or click a date in EPS surprise "
               "history. Prices delayed.*"]
    return {"content": "\n".join(md), "sources": C._earn_sources(m), **_dated(m)}


def earnings_reports(symbol="AAPL", **_):
    """Choices of the Report picker: the latest report, then each report in Yahoo's history, newest first."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]
    m = C._earn_model(sym, need_implied=False)
    opts = [{"label": "Latest report", "value": ""}]
    if m["latest"]["pending"]:
        opts.append({"label": f"{m['latest']['date']} · results pending", "value": m["latest"]["date"]})
    opts += [{"label": f"{r['date']} · EPS {C._eps_txt(r['actual'])} vs {C._eps_txt(r['est'])} ({C._pct_txt(r['surprise'])})", "value": r["date"]} for r in m["history"]]
    return {"results": opts, "sources": C._earn_sources(m)[:1], **_dated(m)}


def _dated(m):
    return {"as_of": m["as_of"]} if m.get("as_of") else {"as_of_na": "No report or price for this company yet."}


def earnings_release(symbol="AAPL", report=None, **_):
    """The picked (or latest) report's press release, 8-K exhibit 99.1, in the SEC filing viewer; the 8-K itself when it
    has no 99.1. Answered as {"_frame": viewer path} (the UI server answers the backend viewer's URL and the card's frame loads it) or {"_message": text}."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]
    C.us_only(sym, "SEC EDGAR earnings releases (Form 8-K)")
    try:
        m = C._earn_model(sym, report=report, need_implied=False)
    except C.CompositeError as e:
        return {"_message": str(e), "sources": [C.src("SEC", "earnings releases: Form 8-K, Item 2.02", "https://www.sec.gov/edgar/search/")]}
    rel, d = m["latest"]["release"], m["latest"]["date"]
    if not rel:
        return {"_message": f"No SEC earnings release (Form 8-K, Item 2.02) for {sym} around {d}. SEC EDGAR covers companies filing in the U.S.; "
                            "for others, the company's investor-relations site has the release.",
                "sources": [C.src("SEC", "earnings releases: Form 8-K, Item 2.02", "https://www.sec.gov/edgar/search/")]}
    docs = C._filing_documents(rel.get("doc") or rel["url"])
    url = next((x["url"] for x in docs if x["type"].startswith("EX-99.1")), None) or rel.get("doc") or rel["url"]
    return {"_frame": "/sec/filing_viewer_app?" + urllib.parse.urlencode({"document_url": url}),
            "sources": [C.src("SEC", f"earnings release of {d}: Form 8-K" + (", exhibit 99.1" if url != (rel.get("doc") or rel["url"]) else ""), url)]}


def earnings_history(symbol="AAPL", **_):
    """EPS consensus vs reported for the last 12 reports, with the surprise and the 2-day price move around each."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]
    m = C._earn_model(sym, need_implied=False)
    rows = [{"date": r["date"], "eps_est": r["est"], "eps_actual": r["actual"], "surprise": r["surprise"], "move_2d": r["move_2d"]} for r in m["history"]]
    # the newest data point the table uses: a report date or the close a 2-day move ends on
    as_of = C.newest_date([r["date"] for r in m["history"]] + [r.get("move_end") for r in m["history"]])
    # the currency Yahoo names for this company's EPS (its estimates; the history is on the same basis). None named: the
    # contract envelope blanks the amounts with a note (core/contract.py ccy_unnamed)
    ccy = {"currency": m["currency"], "convention": f"EPS in {m['currency']} per share"} if m.get("currency") else {}
    return {"results": rows, "sources": C._earn_sources(m)[:1], **ccy,
            **({"as_of": as_of} if as_of else {"as_of_na": "No report for this company yet."})}


def _named_ccy(rows):
    """The one currency Yahoo names on a list of estimate rows ('' when none or more than one)."""
    named = {str(r.get("currency")) for r in rows if r.get("currency")}
    return named.pop() if len(named) == 1 else ""


def consensus(symbol="AAPL", **_):
    """Consensus by period (this and next quarter and fiscal year): EPS and revenue, and how EPS consensus has moved."""
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]
    raw = C._est_raw(sym)
    eps, rev, trend, revs = ({str(r.get("period")): r for r in raw[k]} for k in ("eps", "rev", "trend", "revs"))
    rows = []
    for key, label in EARN_PERIODS:
        e, r, t, v = eps.get(key) or {}, rev.get(key) or {}, trend.get(key) or {}, revs.get(key) or {}
        if not (e or r):
            continue
        cur = C._f(t.get("current"))

        def chg(k):
            old = C._f(t.get(k))
            return cur / old - 1 if cur is not None and old and old > 0 and cur > 0 else None
        rows.append({"period": label, "eps": C._f(e.get("mean")), "eps_low": C._f(e.get("low")), "eps_high": C._f(e.get("high")), "eps_n": e.get("number_of_analysts"),
                     "eps_growth": C._f(e.get("growth")), "rev": C._f(r.get("mean")), "rev_low": C._f(r.get("low")), "rev_high": C._f(r.get("high")),
                     "rev_n": r.get("number_of_analysts"), "rev_growth": C._f(r.get("growth")), "eps_chg_7d": chg("days_ago_7"), "eps_chg_30d": chg("days_ago_30"),
                     "eps_chg_90d": chg("days_ago_90"), "up_7d": v.get("up_last_7_days"), "down_7d": v.get("down_last_7_days"),
                     "up_30d": v.get("up_last_30_days"), "down_30d": v.get("down_last_30_days")})
    if not rows:
        if {"eps", "rev"} & set(raw.get("_failed", [])):
            raise C.CompositeError("Yahoo Finance did not answer for its estimates just now (it may be throttling). Press reload in a minute.")
        raise C.CompositeError(f"Yahoo Finance has no consensus estimates for '{sym}'.")
    # amounts in the currency Yahoo names: EPS from its EPS estimates, revenue from its revenue estimates. When the two
    # differ (or one names none), the side not in the answer's currency is left blank with a note, never shown unlabelled
    e_ccy, r_ccy = _named_ccy(raw["eps"]), _named_ccy(raw["rev"])
    ccy = e_ccy or r_ccy
    extra = {}
    for side, own, cols in (("EPS", e_ccy, ("eps", "eps_low", "eps_high")), ("Revenue", r_ccy, ("rev", "rev_low", "rev_high"))):
        if ccy and own != ccy and any(r[c] is not None for r in rows for c in cols):
            rows = [dict(r, **{c: None for c in cols}) for r in rows]
            extra.setdefault("notes", []).append(f"{side} estimates left blank: Yahoo Finance names "
                                                 + (f"them in {own}, not {ccy}." if own else "no currency for them."))
    if ccy:
        extra.update(currency=ccy, convention=f"EPS per share and revenue in {ccy}")
    return {"results": rows, **extra, "sources": [C.src("Yahoo Finance", "consensus estimates, EPS trend and revisions", C.YAHOO_URL + "quote/" + urllib.parse.quote(sym) + "/analysis")]}
