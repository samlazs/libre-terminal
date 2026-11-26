"""Valuation bands and return decomposition for one company (Stage 3 8d).

/x/valuation_bands: P/E (TTM), EV/EBITDA (TTM) or P/B, daily over 5 years, with the median and the 10th-90th percentile
band of that window and today's percentile.
- U.S. filers: SEC XBRL company facts (backend `equity/compare/company_facts`). No lookahead: each figure counts from the
  earlier of its filing date and the SEC deadline for its form (10-Q: 45 days after the quarter, 10-K: 90 days after the
  year, 20-F/40-F: 120 days), so a figure is never used before it could be known. TTM = the last four reported quarters
  (fiscal Q4 = annual figure minus the three quarters). Share counts: diluted weighted average of the quarter, restated
  for each stock split dated after the filing that reported it (Yahoo split list), so they match Yahoo's split-adjusted closes.
- Others (and U.S. names SEC cannot match): Yahoo annual statements (about four years); Yahoo gives no filing date, so each
  fiscal year counts from 90 days after its end. Figures in the financial currency are converted at today's rate when the
  listing trades in another currency (said in the note).
- Market cap = close x shares; EV = market cap + debt - cash; EBITDA = operating income + depreciation & amortization.
  Earnings, EBITDA or book value <= 0: no multiple (a gap), never a negative one. Banks: P/E and P/B only.
/x/roic_dupont: per fiscal year, ROE = net margin x asset turnover x equity multiplier (year-end balances) and
  ROIC = NOPAT / (debt + equity - cash), NOPAT = operating income x (1 - tax rate), tax rate = tax expense / pre-tax income
  clipped to [0, 35%] (0 when pre-tax income <= 0). Banks: DuPont only.
"""

import bisect
import datetime as dt

import composite as C

MEASURES = {"pe": "P/E (TTM)", "ev_ebitda": "EV / EBITDA (TTM)", "ev_sales": "EV / Sales (TTM)", "ps": "P / S (TTM)", "pb": "P / B"}
OPTIONS = [{"label": v.replace(" (TTM)", ""), "value": k} for k, v in MEASURES.items()]
WINDOW_OPTIONS = [{"label": f"{y}Y", "value": str(y)} for y in (3, 5, 10)]
YEARS = 5
YAHOO_LAG = 90
DEADLINE = {"10-Q": 45, "10-QT": 45, "10-K": 90, "10-KT": 90, "20-F": 120, "40-F": 120, "DEF 14A": 90}
VALID_FORMS = ("10-Q", "10-QT", "10-K", "10-KT", "20-F", "40-F", "DEF 14A")
ANNUAL_FORMS = ("10-K", "10-KT", "20-F", "40-F", "DEF 14A")
MAX_MULT = {"pe": 250.0, "ev_ebitda": 150.0, "ev_sales": 100.0, "ps": 100.0, "pb": 100.0}
MAX_AGE = 520
TRANSIENT_ERRS = ("timed out", "did not answer in time", "not reachable", "connection refused", "http 429", "http 502", "http 503", "http 504", "rate limit")

TAGS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "RevenuesNetOfInterestExpense"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
    "pretax": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
    "tax": ["IncomeTaxExpenseBenefit"],
}
NET_INCOME_FALLBACK = ["ProfitLoss"]
DA_DEP_TAGS = ["Depreciation"]
DA_AMORT_TAGS = ["AmortizationOfIntangibleAssets"]
INSTANT_SHARES_TAGS = ["CommonStockSharesOutstanding"]
INSTANT = {
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "debt": ["DebtLongtermAndShorttermCombinedAmount", "LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
}
SEC_SRC = C.src("SEC", "XBRL company facts (quarterly and annual figures with their filing dates)", "https://www.sec.gov/edgar/search/")
YAHOO_SRC = C.src("Yahoo Finance", "daily closes (split-adjusted), stock splits, profile", "https://finance.yahoo.com/")
YAHOO_FIN_SRC = C.src("Yahoo Finance", "annual statements (no filing date: each year counted from 90 days after its end)", "https://finance.yahoo.com/")


def _d(iso):
    return dt.date.fromisoformat(str(iso)[:10])


def available(end, filed, form, span):
    """The date a figure could first be known: its filing date, or the form's deadline if that is earlier (a figure carried
    by a later filing as a comparative keeps the date it was first due)."""
    days = DEADLINE.get(str(form or "").split("/")[0], 90 if span == "annual" else 45)
    due = _d(end) + dt.timedelta(days=days)
    return min(due, _d(filed)) if filed else due


def quarters(rows):
    """SEC rows (one tag) -> [(end, value, available)] per fiscal quarter, Q4 derived from the annual figure."""
    q = {r["end"]: (r.get("begin") or "", r["value"], available(r["end"], r.get("filed"), r.get("form"), "quarter")) for r in rows if r["span"] == "quarter"}
    for r in rows:
        if r["span"] != "annual" or r["end"] in q or not r.get("begin"):
            continue
        inside = sorted((e, v) for e, (b, v, _) in q.items() if b >= r["begin"] and e <= r["end"])
        if len(inside) == 3 and (_d(r["end"]) - _d(inside[-1][0])).days > 60:
            q[r["end"]] = (inside[-1][0], r["value"] - sum(v for _, v in inside), available(r["end"], r.get("filed"), r.get("form"), "annual"))
    return sorted((e, v, a) for e, (_, v, a) in q.items())


def ttm(qs):
    """[(end, value, available)] quarters -> trailing-twelve-month sums of four contiguous quarters (60-120 d gaps, span < 300 d)."""
    out = []
    for i in range(3, len(qs)):
        w = qs[i - 3:i + 1]
        if all(60 <= (_d(w[j][0]) - _d(w[j - 1][0])).days <= 120 for j in range(1, 4)) and (_d(w[-1][0]) - _d(w[0][0])).days < 300:
            out.append((w[-1][0], sum(x[1] for x in w), max(x[2] for x in w)))
    return out


def _versioned_avail(rows, span_kind):
    """Assign point-in-time availability dates per period end from each filing's reported date (deadline only if missing)."""
    by_end = {}
    for r in rows:
        by_end.setdefault(r["end"], []).append(r)
    out = []
    for end, vers in by_end.items():
        vers = sorted(vers, key=lambda r: r.get("filed") or "")
        for r in vers:
            kind = span_kind(r) if callable(span_kind) else span_kind
            avail = _d(r["filed"]) if r.get("filed") else available(end, None, r.get("form"), kind)
            out.append((r, avail))
    return out


def _latest_end(pts):
    return max((p[0] for p in pts), default="")


def _distinct_ends(pts):
    return len({p[0] for p in pts})


def ttm_pit(rows):
    """Point-in-time TTM series [(end, value, available)] preserving original filings and applying restatements only from their filing date."""
    q_vers = _versioned_avail([r for r in rows if r.get("span") == "quarter"], "quarter")
    a_vers = _versioned_avail([r for r in rows if r.get("span") == "annual" and r.get("begin")], "annual")
    events = {}
    for r, avail in q_vers:
        events.setdefault(avail, []).append(("q", r["end"], r.get("begin") or "", float(r["value"])))
    for r, avail in a_vers:
        events.setdefault(avail, []).append(("a", r["end"], r.get("begin") or "", float(r["value"])))
    cur_q, cur_a, derived_q, out = {}, {}, {}, []
    for t in sorted(events):
        new_a = []
        for kind, end, begin, val in events[t]:
            if kind == "q":
                cur_q[end] = (begin, val)
                derived_q.pop(end, None)
            else:
                cur_a[end] = (begin, val)
                new_a.append(end)
        for a_end in new_a:
            if a_end in cur_q:
                continue
            a_begin, a_val = cur_a[a_end]
            if not a_begin:
                continue
            inside = sorted((e, v) for e, (b, v) in cur_q.items() if b >= a_begin and e <= a_end)
            if len(inside) == 3 and (_d(a_end) - _d(inside[-1][0])).days > 60:
                derived_q[a_end] = (inside[-1][0], a_val - sum(v for _, v in inside))
        eff_q = {**derived_q, **cur_q}
        w = sorted(eff_q)[-4:]
        if len(w) == 4 and all(60 <= (_d(w[j]) - _d(w[j - 1])).days <= 120 for j in range(1, 4)) and (_d(w[-1]) - _d(w[0])).days < 300:
            total = sum(eff_q[e][1] for e in w)
            if not out or out[-1][0] != w[-1] or out[-1][1] != total:
                out.append((w[-1], total, t))
    return out


def instants(rows):
    return sorted((r["end"], r["value"], available(r["end"], r.get("filed"), r.get("form"), "annual" if str(r.get("form", "")).startswith(ANNUAL_FORMS) else "quarter"))
                  for r in rows if r["span"] == "instant")


def instants_pit(rows):
    """Point-in-time instant balance-sheet series [(end, value, available)] with restatements effective on their filing date."""
    vers = _versioned_avail([r for r in rows if r.get("span") == "instant"],
                            lambda r: "annual" if str(r.get("form", "")).startswith(ANNUAL_FORMS) else "quarter")
    return sorted(((r["end"], float(r["value"]), avail) for r, avail in vers), key=lambda p: (p[2], p[0]))


class AsOf:
    """The latest-period figure known on a date: among the figures available by then, the one with the latest period end."""

    def __init__(self, pts):
        self.pts = sorted(pts, key=lambda p: (p[2], p[0]))
        self.keys = [p[2] for p in self.pts]
        self.best = []
        b = None
        for p in self.pts:
            b = p if b is None or p[0] >= b[0] else b
            self.best.append(b)

    def at(self, day, max_age=MAX_AGE):
        i = bisect.bisect_right(self.keys, day)
        if not i:
            return None
        p = self.best[i - 1]
        if max_age and p[2] != dt.date.min and (day - _d(p[0])).days > max_age:
            return None
        return p


def split_factor(splits, filed):
    """Product of the split ratios dated after `filed` (a share count reported then is on the pre-split basis)."""
    f = 1.0
    for d, ratio in splits:
        if d > str(filed)[:10] and ratio and ratio > 0:
            f *= ratio
    return f


def quantile(sorted_vals, q):
    """Linear interpolation between closest ranks (numpy's default)."""
    if not sorted_vals:
        return None
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def percentile_of(vals, x):
    """Share of the values at or below x, in percent."""
    return 100.0 * sum(1 for v in vals if v <= x) / len(vals) if vals else None


def ordinal(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def multiples(closes, shares, figures, measure):
    """[(day, close)] + AsOf share counts + {name: AsOf} figures -> [(day, multiple or None)]."""
    out = []
    for day, px in closes:
        d = _d(day)
        sh = shares.at(d)
        if not sh or not px:
            out.append((day, None))
            continue
        mcap = px * sh[1]
        val = None
        if measure == "pe":
            ni = figures["net_income"].at(d)
            val = mcap / ni[1] if ni and ni[1] > 0 else None
        elif measure == "pb":
            eq = figures["equity"].at(d)
            val = mcap / eq[1] if eq and eq[1] > 0 else None
        elif measure == "ps":
            rev = figures["revenue"].at(d)
            val = mcap / rev[1] if rev and rev[1] > 0 else None
        elif measure == "ev_sales":
            rev, debt, cash = (figures[k].at(d) for k in ("revenue", "debt", "cash"))
            if rev and debt and cash and rev[1] > 0:
                val = (mcap + debt[1] - cash[1]) / rev[1]
        else:
            oi = figures["operating_income"].at(d) if "operating_income" in figures else None
            da = figures["da"].at(d) if "da" in figures else None
            eb_val = oi[1] + da[1] if (oi and da) else None
            if eb_val is None and "ebitda" in figures:
                eb = figures["ebitda"].at(d)
                if eb:
                    eb_val = eb[1]
            debt, cash = figures["debt"].at(d), figures["cash"].at(d)
            if eb_val is not None and debt and cash and eb_val > 0:
                val = (mcap + debt[1] - cash[1]) / eb_val
        out.append((day, val))
    return out


# ---------------------------------------------------------------- data
def _parse_sec_tag_rows(rows, instant, point_in_time):
    tag_rows = {}
    for r in rows or []:
        base_form = str(r.get("form") or "").split("/")[0]
        if base_form and base_form not in VALID_FORMS:
            continue
        span = "instant" if instant else r.get("span")
        if not instant and span == "annual" and base_form and base_form not in ANNUAL_FORMS:
            continue
        if r.get("value") is None or not r.get("period_ending") or span not in ("quarter", "annual", "instant") or str(r.get("unit") or "USD") not in ("USD", "shares"):
            continue
        key = (span, str(r["period_ending"])[:10])
        filed = str(r.get("reported_date") or "")[:10]
        rec = {
            "span": span, "end": key[1], "begin": str(r.get("period_beginning") or "")[:10],
            "value": float(r["value"]), "filed": filed, "form": str(r.get("form") or ""),
        }
        if point_in_time:
            tag_rows.setdefault(key, {})[filed] = rec
        else:
            tag_rows.setdefault(key, {}).setdefault("", rec)
    return tag_rows


def _sec_rows(sym, tags, instant=False, cache_key=None, point_in_time=False):
    """[{span, end, begin, value, filed, form}] for one line item; 12 hours on disk."""
    def run():
        merged, seen_keys, by_tag, err = {}, set(), [], None
        single_tag = (tags == INSTANT["debt"])
        for tag in tags:
            try:
                api_kw = {"point_in_time": "true"} if point_in_time else {}
                rows = C.api(C.V5, "equity/compare/company_facts", provider="sec", symbol=sym, fact=tag,
                             instantaneous="true" if instant else None, **api_kw)
            except C.CompositeError as e:
                if getattr(e, "transient", False) or any(w in str(e).lower() for w in TRANSIENT_ERRS):
                    raise
                err = e
                continue
            tag_rows = _parse_sec_tag_rows(rows, instant, point_in_time)
            if single_tag:
                if tag_rows:
                    by_tag.append(tag_rows)
                continue
            for key, vers in tag_rows.items():
                if key not in seen_keys:
                    seen_keys.add(key)
                    merged[key] = list(vers.values())
        if single_tag and by_tag:
            max_end = max(k[1] for tr in by_tag for k in tr)
            chosen = next(tr for tr in by_tag if (_d(max_end) - _d(max(k[1] for k in tr))).days <= 60)
            merged = {key: list(vers.values()) for key, vers in chosen.items()}
            covered = {k[1] for k in chosen}
            cov_sorted = sorted(covered)

            def _in_era_gap(end):
                if end in covered:
                    return False
                prv = max((c for c in cov_sorted if c < end), default=None)
                nxt = min((c for c in cov_sorted if c > end), default=None)
                return prv is None or nxt is None or (_d(nxt) - _d(prv)).days > 400

            while True:
                # Backfill uncovered historical eras (including multi-year gaps behind stray old filings on the current tag)
                # without patching single missing quarters inside an already-covered era from a different debt convention.
                pre_tags = [{k: v for k, v in tr.items() if _in_era_gap(k[1])} for tr in by_tag]
                pre_tags = [pt for pt in pre_tags if pt]
                if not pre_tags:
                    break
                pre_max = max(k[1] for pt in pre_tags for k in pt)
                pre_chosen = next(pt for pt in pre_tags if (_d(pre_max) - _d(max(k[1] for k in pt))).days <= 60)
                for key, vers in pre_chosen.items():
                    merged[key] = list(vers.values())
                    covered.add(key[1])
                cov_sorted = sorted(covered)
        if not merged and err is not None and "CIK" in str(err):
            raise err
        flat = [rec for vers in merged.values() for rec in vers]
        return sorted(flat, key=lambda r: (r["end"], r["span"], r["filed"]))
    prefix = "secval8_pit_" if point_in_time else "secval6_"
    return C.cached_disk(C.disk_name(prefix, sym + "_" + (cache_key or tags[0])), 12 * 3600, run)


class SplitsUnavailable(C.CompositeError):
    """The split history did not answer and no saved copy exists: SEC per-share history cannot be split-adjusted, so a
    per-share figure (and every band built on it) is left blank with this note instead of being drawn on a wrong basis."""


def _splits(sym):
    def run():
        try:
            rows = C.api(C.V5, "equity/fundamental/historical_splits", provider="yfinance", symbol=sym) or []
        except C.CompositeError as e:
            if C.kind_of(e) not in ("not_covered", "input"):
                raise                                 # a failed fetch is never saved as "no splits" (the saved list is served)
            rows = []
        return [(str(r.get("date"))[:10], float(r["numerator"]) / float(r["denominator"])) for r in rows
                if r.get("numerator") and r.get("denominator")]
    return C.cached_disk(C.disk_name("splits_", sym), 7 * 86400, run)


def _fill_da(sym, da_ttm, oi_rows, point_in_time=True):
    """Fill missing or stale combined D&A periods with SEC Depreciation + AmortizationOfIntangibleAssets."""
    oi_latest = max((r["end"] for r in oi_rows or ()), default="")
    da_latest = _latest_end(da_ttm)
    if da_ttm and (not oi_latest or (_d(oi_latest) - _d(da_latest)).days <= 180):
        return da_ttm
    with C.cf.ThreadPoolExecutor(max_workers=2) as ex:
        dep_f = ex.submit(_sec_rows, sym, DA_DEP_TAGS, False, "da_dep", point_in_time)
        amo_f = ex.submit(_sec_rows, sym, DA_AMORT_TAGS, False, "da_amort", point_in_time)
        dep_ttm = ttm_pit(dep_f.result()) if point_in_time else ttm(quarters(dep_f.result()))
        amo_ttm = ttm_pit(amo_f.result()) if point_in_time else ttm(quarters(amo_f.result()))
    have_ends = {e for e, _, _ in da_ttm}
    events = {}
    for e, v, a in dep_ttm:
        if e not in have_ends:
            events.setdefault(a, []).append(("d", e, v))
    for e, v, a in amo_ttm:
        if e not in have_ends:
            events.setdefault(a, []).append(("a", e, v))
    dep_ends = {e for e, _, _ in dep_ttm}
    amo_ends = {e for e, _, _ in amo_ttm}
    cur_d, cur_a = {}, {}
    extra = []
    for t in sorted(events):
        touched = set()
        for kind, e, v in events[t]:
            (cur_d if kind == "d" else cur_a)[e] = v
            touched.add(e)
        for e in sorted(touched):
            if (e in dep_ends and e not in cur_d) or (e in amo_ends and e not in cur_a):
                continue
            extra.append((e, cur_d.get(e, 0.0) + cur_a.get(e, 0.0), t))
    return sorted(list(da_ttm) + extra, key=lambda p: (p[2], p[0]))


def _share_points(rows, splits):
    """Split-adjusted share count [(end, value, available)] preserving filing versions while preferring 10-Q/10-QT quarters."""
    by_end = {}
    for r in rows:
        if r["value"] <= 0:
            continue
        is_q = r["span"] == "quarter" and not r["form"].startswith(ANNUAL_FORMS)
        is_a = r["span"] == "annual"
        is_i = r["span"] == "instant"
        if not (is_q or is_a or is_i):
            continue
        by_end.setdefault(r["end"], []).append((0 if is_q else 1 if is_i else 2, r))
    chosen = []
    for vers in by_end.values():
        best_rank = min(rank for rank, _ in vers)
        chosen.extend(r for rank, r in vers if rank == best_rank)
    ver_pts = _versioned_avail(chosen, lambda r: "annual" if r["form"].startswith(ANNUAL_FORMS) else "quarter")
    return [(r["end"], r["value"] * split_factor(splits, r["filed"] or r["end"]), avail) for r, avail in ver_pts]


def sec_figures(sym, items=None):
    """{name: [(end, value, available)]}: TTM flows, instants, quarterly/instant share counts (split-restated). Raises when SEC has no CIK."""
    all_names = list(TAGS) + list(INSTANT)
    names = [k for k in all_names if items is None or k in items]
    with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
        got = dict(zip(names, ex.map(lambda k: _sec_rows(sym, INSTANT.get(k) or TAGS[k], k in INSTANT, point_in_time=True), names)))
    if not any(got.values()):
        raise C.CompositeError(f"SEC has no XBRL figures for {sym}.")
    out = {}
    for k in ("revenue", "net_income", "operating_income"):
        if k in got:
            out[k] = ttm_pit(got[k])
    if "da" in got:
        out["da"] = _fill_da(sym, ttm_pit(got["da"]), got.get("operating_income"), point_in_time=True)
    for k in INSTANT:
        if k in got:
            out[k] = instants_pit(got[k])
    if "net_income" in got:
        ref_end = max([_latest_end(pts) for k, pts in out.items() if k != "net_income" and isinstance(pts, list) and pts]
                      + [r["end"] for k, rs in got.items() if k != "net_income" for r in rs], default="")
        ni_end = _latest_end(out["net_income"])
        if not out["net_income"] or (ref_end and (_d(ref_end) - _d(ni_end)).days > 180):
            pl_rows = _sec_rows(sym, NET_INCOME_FALLBACK, cache_key="net_income_pl", point_in_time=True)
            pl_ttm = ttm_pit(pl_rows)
            if pl_ttm and (not out["net_income"] or _latest_end(pl_ttm) > ni_end):
                out["net_income"] = pl_ttm
                got["net_income"] = pl_rows
                out["_pl"] = True
    if "shares" in got:
        try:
            splits = _splits(sym)                     # a saved list is served (flagged stale) when the fetch fails
        except C.CompositeError as e:
            raise SplitsUnavailable(
                f"Bands not drawn: Yahoo Finance's split history for {sym} did not answer and no saved copy exists, so SEC "
                "share counts cannot be split-adjusted (a wrong per-share basis would shift the bands). Reload in a minute.",
                kind=C.kind_of(e) if C.kind_of(e) in ("rate_limited", "timeout", "unavailable") else "upstream",
                retry_after=e.retry_after) from None
        # the backend derives a fiscal Q4 "quarter" as annual minus three quarters, which is meaningless for an average share
        # count (AAPL Q4 2026: -30 million): quarters from 10-Q/10-QT filings only, and the annual average at the fiscal year end
        sh_pts = _share_points(got["shares"], splits)
        latest_other = max((_latest_end(pts) for k, pts in out.items() if isinstance(pts, list) and pts), default="")
        sh_latest = _latest_end(sh_pts)
        if not sh_pts or (latest_other and (_d(latest_other) - _d(sh_latest)).days > 180):
            have_ends = {p[0] for p in sh_pts}
            inst_rows = [r for r in _sec_rows(sym, INSTANT_SHARES_TAGS, instant=True, cache_key="shares_inst", point_in_time=True)
                         if r["end"] not in have_ends]
            sh_pts.extend(_share_points(inst_rows, splits))
        out["shares"] = sorted(sh_pts, key=lambda p: (p[2], p[0]))
    out["_annual"] = got
    return out


def _yahoo(sym, kind):
    try:
        return C.api(C.V5, f"equity/fundamental/{kind}", provider="yfinance", symbol=sym, period="annual", limit=5) or []
    except C.CompositeError as e:
        C.partial_from(e, f"Yahoo Finance {kind.replace('_', ' ')}")
        return []


def _num(r, *keys):
    for k in keys:
        v = r.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def yahoo_figures(sym, rate=1.0):
    """The same figures from Yahoo annual statements, each counted from 90 days after the fiscal year end; amounts x rate."""
    inc, bal = _yahoo(sym, "income"), _yahoo(sym, "balance")
    if not inc and not bal:
        raise C.CompositeError(f"Yahoo Finance has no annual statements for {sym}.")
    out = {k: [] for k in ("net_income", "operating_income", "da", "ebitda", "equity", "debt", "cash", "shares", "assets", "revenue", "pretax", "tax")}
    for rows, fields in ((inc, {"net_income": ("net_income_common_stockholders", "net_income"), "operating_income": ("operating_income", "ebit"),
                                "da": ("reconciled_depreciation",), "ebitda": ("ebitda", "normalized_ebitda"),
                                "revenue": ("total_revenue", "operating_revenue"), "pretax": ("total_pre_tax_income",), "tax": ("tax_provision",)}),
                         (bal, {"equity": ("common_stock_equity", "stockholders_equity"), "debt": ("total_debt",), "cash": ("cash_and_cash_equivalents",),
                                "assets": ("total_assets",), "shares": ("ordinary_shares_number", "share_issued")})):
        for r in rows:
            end = str(r.get("period_ending") or "")[:10]
            if not end:
                continue
            avail = _d(end) + dt.timedelta(days=YAHOO_LAG)
            for k, keys in fields.items():
                v = _num(r, *keys)
                if v is not None:
                    out[k].append((end, v if k == "shares" else v * rate, avail))
    for k in out:
        out[k].sort()
    return out


def _profile(sym):
    try:
        return (C._profiles([sym]) or {}).get(sym) or {}
    except C.CompositeError as e:
        C.partial_from(e, "Yahoo Finance profile")
        return {}


def _subject(symbol):
    sym = (C._syms(symbol, 1) or ["AAPL"])[0]
    prof = _profile(sym)
    return sym, prof, C.is_bank(prof.get("industry"))


def _adr_scale(yfigs, prof, px_ccy, last_px):
    """Detect ADR / share-class mismatches (market cap vs Yahoo ordinary shares) and return scale factor."""
    mc = (prof or {}).get("market_cap")
    sh = yfigs.get("shares") or []
    if not (mc and last_px and sh and sh[-1][1] > 0):
        return 1.0
    minor = C.MINOR_UNITS.get(px_ccy)
    px_major = last_px * minor[1] if minor else last_px
    if px_major <= 0:
        return 1.0
    ratio = (mc / px_major) / sh[-1][1]
    return ratio if (ratio <= 0.55 or ratio >= 1.8) else 1.0


def _yahoo_pack(sym, px_ccy, prof=None, last_px=None):
    fin_ccy = (C._first("equity/fundamental/metrics", sym, provider="yfinance") or {}).get("currency")
    rate, conv = 1.0, ""
    if fin_ccy and px_ccy and fin_ccy != px_ccy:
        r = C._usd_rates({fin_ccy, px_ccy})
        if not r.get(fin_ccy) or not r.get(px_ccy):
            raise C.CompositeError(f"{sym} reports in {fin_ccy} and trades in {px_ccy}; no exchange rate answered, so no multiple is computed.")
        rate = r[fin_ccy] / r[px_ccy]
        conv = f"; {fin_ccy} figures converted to {px_ccy} at today's rate"
    yfigs = yahoo_figures(sym, rate)
    scale = _adr_scale(yfigs, prof, px_ccy, last_px)
    if scale != 1.0:
        yfigs["shares"] = [(e, v * scale, a) for e, v, a in yfigs["shares"]]
        conv += "; ADR/share-class adjusted"
    return yfigs, "Yahoo annual statements (each year from 90 days after its end)" + conv, [YAHOO_FIN_SRC, YAHOO_SRC]


def _sec_failed(e):
    """An SEC failure for a U.S. company whose answer goes on from Yahoo's statements: said as a substitute source,
    unless SEC simply has nothing for this input (not_covered / input), where Yahoo is the usual basis. The note names
    the kind of failure only: the error's own text (backend URLs, a provider's detail) never reaches a card's notes."""
    kind = C.kind_of(e)
    if kind not in ("not_covered", "input"):
        C.note_fallback("Yahoo annual statements", f"SEC EDGAR did not answer just now ({kind.replace('_', ' ')})")


def _figures(sym, px_ccy, prof=None, items=None, last_px=None):
    """(figures, basis text, sources, is_sec): SEC for U.S. filers, else Yahoo (converted to trading currency and share class)."""
    if "." not in sym and not sym.startswith("^"):
        try:
            figs = sec_figures(sym, items=items)
            return figs, "SEC filings (point-in-time by filing date)", [SEC_SRC, YAHOO_SRC], True
        except SplitsUnavailable:
            raise                                     # blank with the note, never a silent switch of basis
        except C.CompositeError as e:
            _sec_failed(e)                            # a switch of basis is said (status fallback), never silent
    yfigs, basis, sources = _yahoo_pack(sym, px_ccy, prof=prof, last_px=last_px)
    return yfigs, basis, sources, False


# ---------------------------------------------------------------- views
def _denom_ok(figs, measure):
    if measure == "pe":
        return bool(figs.get("net_income")), [figs.get("net_income") or []]
    if measure == "pb":
        return bool(figs.get("equity")), [figs.get("equity") or []]
    if measure in ("ps", "ev_sales"):
        return bool(figs.get("revenue")), [figs.get("revenue") or []]
    if figs.get("operating_income") and figs.get("da"):
        return True, [figs["operating_income"], figs["da"]]
    if figs.get("ebitda"):
        return True, [figs["ebitda"]]
    return False, []


def _sec_needs_fallback(figs, measure, today, closes_len):
    if not figs.get("shares"):
        return True
    ok, series_list = _denom_ok(figs, measure)
    if not ok:
        return True
    cash_need = [figs.get("cash") or []] if measure in ("ev_ebitda", "ev_sales") else []
    if any(not s for s in cash_need):
        return True
    all_req = [figs["shares"]] + series_list + cash_need
    if any((today - _d(_latest_end(s))).days > MAX_AGE for s in all_req):
        return True
    if closes_len > 100 and any(_distinct_ends(s) < 4 for s in series_list + [figs["shares"]]):
        return True
    return False


def _nm_warning(measure, cur_day, last_d, as_of, shares_as_of, raw_at_end):
    label = MEASURES[measure]
    if not shares_as_of.at(last_d):
        return f"Share count stopped after {cur_day} — switch to another source or period."
    if measure == "pe":
        p = as_of["net_income"].at(last_d)
        if not p:
            return f"TTM net income stopped after {cur_day} — switch to EV / Sales, P / S or P / B."
        if p[1] <= 0:
            return f"TTM net income is negative/zero since {cur_day} — switch to EV / Sales, P / S or P / B."
    elif measure == "ev_ebitda":
        oi = as_of["operating_income"].at(last_d) if "operating_income" in as_of else None
        da = as_of["da"].at(last_d) if "da" in as_of else None
        eb = oi[1] + da[1] if (oi and da) else (as_of["ebitda"].at(last_d)[1] if "ebitda" in as_of and as_of["ebitda"].at(last_d) else None)
        if eb is None:
            return f"TTM EBITDA figures stopped after {cur_day} — switch to EV / Sales, P / S or P / B."
        if eb <= 0:
            return f"TTM EBITDA is negative/zero since {cur_day} — switch to EV / Sales, P / S or P / B."
    elif measure == "pb":
        p = as_of["equity"].at(last_d)
        if not p:
            return f"Book equity stopped after {cur_day} — switch to EV / Sales or P / S."
        if p[1] <= 0:
            return f"Book equity is negative/zero since {cur_day} — switch to EV / Sales or P / S."
    elif measure in ("ps", "ev_sales"):
        p = as_of["revenue"].at(last_d)
        if not p or p[1] <= 0:
            return f"TTM revenue unavailable/non-positive after {cur_day}."
    if raw_at_end is not None and raw_at_end > MAX_MULT[measure]:
        return f"{label} exceeded {MAX_MULT[measure]:.0f}x (near-zero denominator; last shown {cur_day}) — switch to EV / Sales, P / S or P / B."
    return f"No valid {label} after {cur_day}."


def bands(symbol="AAPL", measure="pe", years="5", today=None, **_):
    sym, prof, bank = _subject(symbol)
    measure = measure if measure in MEASURES else "pe"
    yrs = int(years) if str(years) in ("3", "5", "10") else YEARS
    if bank and measure in ("ev_ebitda", "ev_sales"):
        raise C.CompositeError(f"{MEASURES[measure]} carries no meaning for a bank (deposits are its raw material, not debt): pick P/E, P/S or P/B.")
    today = today or dt.date.today()
    start = C.minus_months(today, 12 * yrs)
    closes = (C.history([sym], start) or {}).get(sym) or []
    if not closes:
        raise C.CompositeError(f"Yahoo Finance returned no prices for {sym}.")
    req_map = {"pe": ["net_income"], "pb": ["equity"], "ps": ["revenue"],
               "ev_sales": ["revenue", "debt", "cash"], "ev_ebitda": ["operating_income", "da", "debt", "cash"]}
    need = list(req_map[measure])
    figs, basis, sources, from_sec = _figures(sym, prof.get("currency"), prof=prof, items=need + ["shares"], last_px=closes[-1][1])
    if from_sec and _sec_needs_fallback(figs, measure, today, len(closes)):
        try:
            yfigs, ybasis, ysources = _yahoo_pack(sym, prof.get("currency"), prof=prof, last_px=closes[-1][1])
            y_ok, y_ser = _denom_ok(yfigs, measure)
            s_ok, s_ser = _denom_ok(figs, measure)
            y_cash = bool(yfigs.get("cash")) if measure in ("ev_ebitda", "ev_sales") else True
            y_latest = _latest_end(y_ser[0]) if y_ok else ""
            s_latest = _latest_end(s_ser[0]) if s_ok else ""
            y_fresh = (y_ok and bool(yfigs.get("shares")) and y_cash
                       and (today - _d(y_latest)).days <= MAX_AGE
                       and (today - _d(_latest_end(yfigs["shares"]))).days <= MAX_AGE)
            sec_shares_bad = (not figs.get("shares")) or (today - _d(_latest_end(figs["shares"]))).days > MAX_AGE
            sec_denom_bad = (not s_ok) or any((today - _d(_latest_end(s))).days > MAX_AGE for s in s_ser)
            sec_cash_bad = measure in ("ev_ebitda", "ev_sales") and (not figs.get("cash") or (today - _d(_latest_end(figs["cash"]))).days > MAX_AGE)
            if y_fresh and (sec_shares_bad or sec_denom_bad or sec_cash_bad or _distinct_ends(y_ser[0]) > _distinct_ends(s_ser[0]) or y_latest > s_latest):
                figs, basis, sources = yfigs, ybasis, ysources
        except C.CompositeError:
            pass
    if measure == "ev_ebitda" and figs.get("ebitda") and not (figs.get("operating_income") and figs.get("da")):
        need = ["ebitda", "debt", "cash"]
    no_debt = measure in ("ev_ebitda", "ev_sales") and not figs.get("debt")
    if no_debt:                               # no debt line filed: EV = market cap - cash
        figs = dict(figs, debt=[("0001-01-01", 0.0, dt.date.min)])
    missing = [k for k in need + ["shares"] if not figs.get(k)]
    if missing:
        raise C.CompositeError(f"{MEASURES[measure]} for {sym}: no {', '.join(m.replace('_', ' ') for m in missing)} figures in {basis.split(' (')[0]}.")
    as_of_map = {k: AsOf(figs[k]) for k in need}
    sh_as_of = AsOf(figs["shares"])
    raw_series = multiples(closes, sh_as_of, as_of_map, measure)
    cap = MAX_MULT[measure]
    clipped = neg_days = miss_days = 0

    def _has_inputs(day):
        d = _d(day)
        if not sh_as_of.at(d):
            return False
        if measure in ("ev_ebitda", "ev_sales") and not (as_of_map["debt"].at(d) and as_of_map["cash"].at(d)):
            return False
        if measure == "ev_ebitda" and "operating_income" in as_of_map:
            return bool(as_of_map["operating_income"].at(d) and as_of_map["da"].at(d))
        key = {"pe": "net_income", "pb": "equity", "ps": "revenue", "ev_sales": "revenue", "ev_ebitda": "ebitda"}[measure]
        return bool(as_of_map[key].at(d))

    series = []
    for d, v in raw_series:
        if v is not None and v > cap:
            clipped += 1
            series.append((d, None))
        else:
            if v is None:
                if _has_inputs(d):
                    neg_days += 1
                else:
                    miss_days += 1
            series.append((d, v))
    pts = [(d, v) for d, v in series if v is not None]
    if not pts:
        raise C.CompositeError(f"{MEASURES[measure]} for {sym}: no positive denominator in the last {yrs} years (negative earnings or book value give no multiple).")
    vals = sorted(v for _, v in pts)
    p10, med, p90 = (quantile(vals, q) for q in (0.1, 0.5, 0.9))
    cur_day, cur = pts[-1]
    pct = percentile_of(vals, cur)
    xs = [d for d, _ in series]
    label = MEASURES[measure]
    stale_now = (_d(series[-1][0]) - _d(cur_day)).days > 14
    if stale_now:
        cur_name = f"{label}  n/m now (last {cur:.1f}x on {cur_day})"
        summary = {"current": None, "median": round(med, 3), "p10": round(p10, 3), "p90": round(p90, 3),
                   "percentile": None, "last_valid": round(cur, 3), "last_valid_date": cur_day}
        warning = _nm_warning(measure, cur_day, _d(series[-1][0]), as_of_map, sh_as_of, raw_series[-1][1])
    else:
        cur_name = f"{label}  {cur:.1f}x ({ordinal(round(pct))} percentile)"
        summary = {"current": round(cur, 3), "median": round(med, 3), "p10": round(p10, 3), "p90": round(p90, 3), "percentile": round(pct, 1)}
        warning = None
    flat = lambda v: [round(v, 3)] * len(xs)  # noqa: E731
    traces = [
        {"type": "scatter", "mode": "lines", "name": "10th percentile", "x": xs, "y": flat(p10), "line": {"width": 0}, "showlegend": False, "hoverinfo": "skip"},
        {"type": "scatter", "mode": "lines", "name": f"10th-90th percentile  {p10:.1f}x - {p90:.1f}x", "x": xs, "y": flat(p90), "line": {"width": 0},
         "fill": "tonexty", "fillcolor": "rgba(57,135,229,0.13)", "hoverinfo": "skip"},
        {"type": "scatter", "mode": "lines", "name": f"Median  {med:.1f}x", "x": xs, "y": flat(med), "line": {"width": 1.5, "dash": "dash", "color": C.PALETTE[2]}, "hoverinfo": "skip"},
        {"type": "scatter", "mode": "lines", "name": cur_name, "x": xs, "y": [None if v is None else round(v, 3) for _, v in series],
         "line": {"width": 2, "color": C.PALETTE[0]}, "connectgaps": False, "hovertemplate": "%{y:.1f}x<extra>" + label + "</extra>"},
    ]
    lay = C._layout(True)
    lay["yaxis"]["ticksuffix"] = "x"
    notes = ([f"{neg_days} days without a positive denominator left blank"] if neg_days else []) + \
            ([f"{miss_days} days without required filings left blank"] if miss_days else []) + \
            ([f"{clipped} days above {cap:.0f}x clipped"] if clipped else [])
    pl_tail = " · net income incl. noncontrolling interests (ProfitLoss)" if figs.get("_pl") else ""
    tail = (" · no debt in filings (taken as 0)" if no_debt else "") + pl_tail + ((" · " + " · ".join(notes)) if notes else "")
    lay["annotations"] = [C._note(f"{sym} · {label} ({yrs}Y) to {cur_day} · {basis}{tail}")]
    results = [{"date": d, "multiple": None if v is None else round(v, 3), "close": round(px, 2)} for (d, v), (_, px) in zip(reversed(series), reversed(closes))]
    out = {"data": traces, "layout": lay, "sources": sources, "results": results, "summary": summary,
           "convention": f"{yrs}Y window · {basis}{tail}"}
    if warning:
        out["warning"] = warning
    return out


def _annual(pts):
    return {e: v for e, v, _ in pts}


def _round4(v):
    """Floats rounded to 4 places, NaN/Inf floats as None; anything that is not a float is left as it is."""
    if not isinstance(v, float):
        return v
    x = C.finite(v)
    return None if x is None else round(x, 4)


def dupont_rows(f, bank=False, years=YEARS):
    """{name: {fiscal-year end: value}} annual figures -> rows newest first (ratios in percent / times)."""
    ends = sorted(f["net_income"])[-years:]
    no_debt = not f.get("debt")
    rows = []
    for e in reversed(ends):
        ni, rev, assets, eq = (f[k].get(e) for k in ("net_income", "revenue", "assets", "equity"))
        r = {"fiscal_year_end": e, "net_margin": None, "asset_turnover": None, "equity_multiplier": None, "roe": None,
             "tax_rate": None, "nopat": None, "invested_capital": None, "roic": None}
        if ni is not None and rev and rev > 0:
            r["net_margin"] = ni / rev * 100
        if rev is not None and assets and assets > 0:
            r["asset_turnover"] = rev / assets
        if assets is not None and eq and eq > 0:
            r["equity_multiplier"] = assets / eq
            r["roe"] = ni / eq * 100 if ni is not None else None
        oi, pre, tax, debt, cash = (f[k].get(e) for k in ("operating_income", "pretax", "tax", "debt", "cash"))
        if not bank and oi is not None and tax is not None and pre is not None:
            t = min(max(tax / pre, 0.0), 0.35) if pre > 0 else 0.0
            r["tax_rate"] = t * 100
            r["nopat"] = oi * (1 - t)
            d_val = 0.0 if (debt is None and no_debt) else debt
            if eq is not None and cash is not None and d_val is not None:
                ic = d_val + eq - cash
                r["invested_capital"] = ic
                r["roic"] = r["nopat"] / ic * 100 if ic > 0 else None
        rows.append({k: _round4(v) for k, v in r.items()})
    return rows


def roic_dupont(symbol="AAPL", **_):
    sym, prof, bank = _subject(symbol)
    if "." not in sym and not sym.startswith("^"):
        try:
            names = list(TAGS) + list(INSTANT)
            with C.cf.ThreadPoolExecutor(max_workers=4) as ex:
                got = dict(zip(names, ex.map(lambda k: _sec_rows(sym, INSTANT.get(k) or TAGS[k], k in INSTANT), names)))
            f = {k: {r["end"]: r["value"] for r in got[k] if r["span"] == ("instant" if k in INSTANT else "annual")} for k in names}
            ni_end = max(f["net_income"], default="")
            other_end = max((max(f[k]) for k in f if k != "net_income" and f[k]), default="")
            pl_used = False
            if not ni_end or (other_end and (_d(other_end) - _d(ni_end)).days > 180):
                pl_rows = _sec_rows(sym, NET_INCOME_FALLBACK, cache_key="net_income_pl")
                pl_ann = {r["end"]: r["value"] for r in pl_rows if r["span"] == "annual"}
                if pl_ann and max(pl_ann) > ni_end:
                    f["net_income"] = pl_ann
                    pl_used = True
            # balances at the fiscal year end only (a 10-K instant matches an annual flow's end)
            if f["net_income"] and (dt.date.today() - _d(max(f["net_income"]))).days <= MAX_AGE:
                basis = "SEC 10-K figures (USD; net income incl. noncontrolling interests via ProfitLoss)" if pl_used else "SEC 10-K figures (USD)"
                return _dupont_out(sym, dupont_rows(f, bank), bank, basis, [SEC_SRC], "USD")
        except C.CompositeError as e:
            _sec_failed(e)
    y = yahoo_figures(sym)
    f = {k: _annual(v) for k, v in y.items()}
    ccy = (C._first("equity/fundamental/metrics", sym, provider="yfinance") or {}).get("currency")
    return _dupont_out(sym, dupont_rows(f, bank), bank, f"Yahoo annual statements ({ccy or 'currency not named'})", [YAHOO_FIN_SRC], ccy)


def _dupont_out(sym, rows, bank, basis, sources, ccy=None):
    if not rows:
        raise C.CompositeError(f"No annual net income figures for {sym}.")
    rows = [dict(r, **{k: None if r[k] is None else round(r[k] / 1e6, 1) for k in ("nopat", "invested_capital")}) for r in rows]   # millions
    note = (f"{sym} · {basis} · ROE = net margin x asset turnover x equity multiplier (year-end balances)"
            + (" · bank: ROIC not computed (deposits are not invested capital)" if bank else
               " · ROIC = operating income x (1 - tax rate) / (debt + equity - cash); tax rate clipped to 0-35%"))
    return {"results": rows, "sources": sources, "note": note, "convention": note, **({"currency": ccy} if ccy else {}),
            "as_of": C.newest_date(r["fiscal_year_end"] for r in rows)}      # the latest fiscal year shown


def static_sources(name, p):
    sym = str((p or {}).get("symbol") or "AAPL").upper()
    us = "." not in sym and not sym.startswith("^")
    if name == "roic_dupont":
        return [SEC_SRC] if us else [YAHOO_FIN_SRC]
    return [SEC_SRC, YAHOO_SRC] if us else [YAHOO_FIN_SRC, YAHOO_SRC]
