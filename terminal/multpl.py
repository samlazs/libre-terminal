"""S&P 500 valuation multiples and fundamentals back to 1871 from multpl.com (W3).

Replaces the legacy V4 `openbb_multpl` dependency with an stdlib-only HTML table
fetcher cached on disk (`C.cached_disk`, 12 h TTL). Units are declared explicitly
per series (`x`, `%` as percentage points, or `USD`) and never inferred from values.
"""

from __future__ import annotations

import datetime as dt
import html
from html.parser import HTMLParser
import math

import composite as C

BASE_URL = "https://www.multpl.com/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# key -> (label, url_slug, unit, kind, convention)
# Most-used monthly/quarterly valuation ratios come first for the UI picker.
SERIES: dict[str, tuple[str, str, str, str, str]] = {
    "pe_month": ("P/E Ratio (monthly)", "s-p-500-pe-ratio/table/by-month", "x", "multiple", "S&P 500 price divided by trailing 12-month earnings"),
    "shiller_pe_month": ("Shiller CAPE (monthly)", "shiller-pe/table/by-month", "x", "multiple", "Cyclically adjusted P/E using 10-year inflation-adjusted earnings"),
    "dividend_yield_month": ("Dividend Yield (monthly)", "s-p-500-dividend-yield/table/by-month", "%", "percent", "Trailing 12-month S&P 500 dividend yield in %"),
    "earnings_yield_month": ("Earnings Yield (monthly)", "s-p-500-earnings-yield/table/by-month", "%", "percent", "Trailing 12-month S&P 500 earnings yield in %"),
    "price_to_sales_quarter": ("Price / Sales (quarterly)", "s-p-500-price-to-sales/table/by-quarter", "x", "multiple", "S&P 500 price divided by trailing 12-month sales per share"),
    "price_to_book_value_quarter": ("Price / Book (quarterly)", "s-p-500-price-to-book/table/by-quarter", "x", "multiple", "S&P 500 price divided by book value per share"),
    "earnings_month": ("Earnings per Share (monthly)", "s-p-500-earnings/table/by-month", "USD", "usd", "Trailing 12-month S&P 500 earnings per share (USD)"),
    "dividend_month": ("Dividend per Share (monthly)", "s-p-500-dividend/table/by-month", "USD", "usd", "Trailing 12-month S&P 500 dividend per share (USD)"),
    "sales_quarter": ("Sales per Share (quarterly)", "s-p-500-sales/table/by-quarter", "USD", "usd", "Trailing 12-month S&P 500 sales per share (USD)"),
    "real_sales_quarter": ("Real Sales per Share (quarterly)", "s-p-500-real-sales/table/by-quarter", "USD", "usd", "Inflation-adjusted S&P 500 sales per share (USD)"),
    "book_value_quarter": ("Book Value per Share (quarterly)", "s-p-500-book-value/table/by-quarter", "USD", "usd", "S&P 500 book value per share (USD)"),
    "real_price_month": ("S&P 500 Price (monthly)", "s-p-500-historical-prices/table/by-month", "USD", "usd", "Nominal S&P 500 index price level"),
    "inflation_adjusted_price_month": ("Real S&P 500 Price (monthly)", "inflation-adjusted-s-p-500/table/by-month", "USD", "usd", "CPI-adjusted S&P 500 index price level"),
    "earnings_growth_quarter": ("Earnings Growth (quarterly)", "s-p-500-earnings-growth/table/by-quarter", "%", "percent", "Year-over-year S&P 500 earnings growth in %"),
    "real_earnings_growth_quarter": ("Real Earnings Growth (quarterly)", "s-p-500-real-earnings-growth/table/by-quarter", "%", "percent", "Inflation-adjusted YoY S&P 500 earnings growth in %"),
    "dividend_growth_quarter": ("Dividend Growth (quarterly)", "s-p-500-dividend-growth/table/by-quarter", "%", "percent", "Year-over-year S&P 500 dividend growth in %"),
    "sales_growth_quarter": ("Sales Growth (quarterly)", "s-p-500-sales-growth/table/by-quarter", "%", "percent", "Year-over-year S&P 500 sales growth in %"),
    "real_sales_growth_quarter": ("Real Sales Growth (quarterly)", "s-p-500-real-sales-growth/table/by-quarter", "%", "percent", "Inflation-adjusted YoY S&P 500 sales growth in %"),
    "pe_year": ("P/E Ratio (annual)", "s-p-500-pe-ratio/table/by-year", "x", "multiple", "S&P 500 price divided by trailing 12-month earnings"),
    "shiller_pe_year": ("Shiller CAPE (annual)", "shiller-pe/table/by-year", "x", "multiple", "Cyclically adjusted P/E using 10-year inflation-adjusted earnings"),
    "dividend_yield_year": ("Dividend Yield (annual)", "s-p-500-dividend-yield/table/by-year", "%", "percent", "Trailing 12-month S&P 500 dividend yield in %"),
    "earnings_yield_year": ("Earnings Yield (annual)", "s-p-500-earnings-yield/table/by-year", "%", "percent", "Trailing 12-month S&P 500 earnings yield in %"),
    "price_to_sales_year": ("Price / Sales (annual)", "s-p-500-price-to-sales/table/by-year", "x", "multiple", "S&P 500 price divided by trailing 12-month sales per share"),
    "price_to_book_value_year": ("Price / Book (annual)", "s-p-500-price-to-book/table/by-year", "x", "multiple", "S&P 500 price divided by book value per share"),
    "earnings_year": ("Earnings per Share (annual)", "s-p-500-earnings/table/by-year", "USD", "usd", "Trailing 12-month S&P 500 earnings per share (USD)"),
    "dividend_year": ("Dividend per Share (annual)", "s-p-500-dividend/table/by-year", "USD", "usd", "Trailing 12-month S&P 500 dividend per share (USD)"),
    "sales_year": ("Sales per Share (annual)", "s-p-500-sales/table/by-year", "USD", "usd", "Trailing 12-month S&P 500 sales per share (USD)"),
    "real_sales_year": ("Real Sales per Share (annual)", "s-p-500-real-sales/table/by-year", "USD", "usd", "Inflation-adjusted S&P 500 sales per share (USD)"),
    "book_value_year": ("Book Value per Share (annual)", "s-p-500-book-value/table/by-year", "USD", "usd", "S&P 500 book value per share (USD)"),
    "real_price_year": ("S&P 500 Price (annual)", "s-p-500-historical-prices/table/by-year", "USD", "usd", "Nominal S&P 500 index price level"),
    "inflation_adjusted_price_year": ("Real S&P 500 Price (annual)", "inflation-adjusted-s-p-500/table/by-year", "USD", "usd", "CPI-adjusted S&P 500 index price level"),
    "earnings_growth_year": ("Earnings Growth (annual)", "s-p-500-earnings-growth/table/by-year", "%", "percent", "Year-over-year S&P 500 earnings growth in %"),
    "real_earnings_growth_year": ("Real Earnings Growth (annual)", "s-p-500-real-earnings-growth/table/by-year", "%", "percent", "Inflation-adjusted YoY S&P 500 earnings growth in %"),
    "dividend_growth_year": ("Dividend Growth (annual)", "s-p-500-dividend-growth/table/by-year", "%", "percent", "Year-over-year S&P 500 dividend growth in %"),
    "sales_growth_year": ("Sales Growth (annual)", "s-p-500-sales-growth/table/by-year", "%", "percent", "Year-over-year S&P 500 sales growth in %"),
    "real_sales_growth_year": ("Real Sales Growth (annual)", "s-p-500-real-sales-growth/table/by-year", "%", "percent", "Inflation-adjusted YoY S&P 500 sales growth in %"),
}

OPTIONS = [{"label": v[0], "value": k} for k, v in SERIES.items()]
DEFAULT_SERIES = ("pe_month", "shiller_pe_month")
RANGES = {"5Y": 60, "10Y": 120, "20Y": 240, "50Y": 600, "MAX": None}


class _TableParser(HTMLParser):
    """Extract rows from `<table id="datatable">` using stdlib html.parser."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.in_row = False
        self.in_cell = False
        self.cell: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr = dict(attrs)
        if tag == "table":
            if self.depth > 0 or attr.get("id") == "datatable":
                self.depth += 1
        elif self.depth > 0:
            if tag == "tr":
                self.in_row = True
                self.row = []
            elif tag in ("td", "th") and self.in_row:
                self.in_cell = True
                self.cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "table" and self.depth > 0:
            self.depth -= 1
        elif self.depth > 0:
            if tag in ("td", "th") and self.in_cell:
                self.in_cell = False
                self.row.append(html.unescape("".join(self.cell)))
            elif tag == "tr" and self.in_row:
                self.in_row = False
                if self.row:
                    self.rows.append(self.row)

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)

    def handle_entityref(self, name):
        if self.in_cell:
            self.cell.append(f"&{name};")

    def handle_charref(self, name):
        if self.in_cell:
            self.cell.append(f"&#{name};")


def _parse_date(raw: str) -> str | None:
    txt = " ".join(raw.replace("\u2002", " ").replace("\xa0", " ").split())
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(txt, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_value(raw: str) -> tuple[float | None, bool]:
    is_est = "†" in raw
    txt = raw.replace("†", "").replace("\u2002", "").replace("\xa0", "").replace("$", "").replace("%", "").replace(",", "").strip()
    if not txt:
        return None, is_est
    try:
        val = float(txt)
    except ValueError:
        return None, is_est
    return (val, is_est) if math.isfinite(val) else (None, is_est)


def parse_table(html_text: str) -> list[tuple[str, float, bool]]:
    """Parse multpl.com `<table id="datatable">` HTML into sorted `[(iso_date, value, is_est)]`."""
    p = _TableParser()
    p.feed(html_text or "")
    p.close()
    by_date: dict[str, tuple[float, bool]] = {}
    for row in p.rows:
        if len(row) < 2:
            continue
        iso = _parse_date(row[0])
        val, is_est = _parse_value(row[1])
        if iso is None or val is None:
            continue
        if iso not in by_date:
            by_date[iso] = (val, is_est)
    return [(d, by_date[d][0], by_date[d][1]) for d in sorted(by_date)]


def fetch_series(key: str) -> list[tuple[str, float, bool]]:
    """Fetch and disk-cache one multpl.com S&P 500 series (12 h TTL)."""
    if key not in SERIES:
        raise C.CompositeError(f"Unknown S&P 500 valuation series '{key}'.")
    slug = SERIES[key][1]
    url = BASE_URL + slug

    def load():
        try:
            body = C.fetch(url, provider="multpl", parse="text", headers={"Accept": "text/html,application/xhtml+xml"})
        except C.CompositeError as e:
            status = getattr(e, "http_status", None)
            msg = f"multpl.com returned HTTP {status} for {key}." if status else (
                "multpl.com did not answer in time." if e.kind == "timeout" else str(e))
            raise C.CompositeError(msg, kind=e.kind, retry_after=e.retry_after) from None
        rows = parse_table(body)
        if not rows:
            raise C.CompositeError(f"multpl.com returned no table rows for {key}.", kind="upstream")
        return [[d, v, e] for d, v, e in rows]

    raw = C.cached_disk(f"multpl_{key}", 12 * 3600, load)
    return [(str(r[0]), float(r[1]), bool(r[2])) for r in raw]


def _parse_keys(series_name) -> list[str]:
    if series_name is None:
        return list(DEFAULT_SERIES)
    if isinstance(series_name, (list, tuple)):
        parts = [str(x).strip() for x in series_name if str(x).strip()]
    else:
        parts = [x.strip() for x in str(series_name).split(",") if x.strip()]
    if not parts:
        return list(DEFAULT_SERIES)
    valid = [k for k in dict.fromkeys(parts) if k in SERIES]
    if not valid:
        raise C.CompositeError(f"Unknown S&P 500 series: {', '.join(parts)}.")
    return valid[:4]


def static_sources(series_name=None) -> list[dict]:
    try:
        keys = _parse_keys(series_name)
    except C.CompositeError:
        keys = list(DEFAULT_SERIES)
    return [C.src("multpl.com", f"{SERIES[k][0]} — {SERIES[k][4]}", BASE_URL + SERIES[k][1]) for k in keys]


def _fmt_val(val: float, unit: str) -> str:
    if unit == "x":
        return f"{val:.1f}x"
    if unit == "%":
        return f"{val:.2f}%"
    return f"${val:,.2f}"


def _axis_style(axis: dict, unit: str, label: str):
    if unit == "x":
        axis["ticksuffix"] = "x"
    elif unit == "%":
        axis["ticksuffix"] = "%"
    elif unit == "USD":
        axis["tickprefix"] = "$"
    axis["title"] = {"text": label, "font": {"size": 10}}


def chart(series_name="pe_month,shiller_pe_month", range="50Y", today=None, **_):
    """Build a Plotly chart + W5 envelope for up to 4 S&P 500 valuation series (at most 2 units)."""
    keys = _parse_keys(series_name)
    rng = str(range or "50Y").upper()
    rng = rng if rng in RANGES else "50Y"
    units = list(dict.fromkeys(SERIES[k][2] for k in keys))
    if len(units) > 2:
        raise C.CompositeError("Pick series with at most two different units (e.g. multiples in x and yields in %).")

    today = today or dt.date.today()
    today_iso = today.isoformat()
    months = RANGES[rng]
    cutoff = C.minus_months(today, months).isoformat() if months is not None else ""

    traces = []
    latest_dates = []
    any_est = False
    for i, key in enumerate(keys):
        label, _, unit, _, _ = SERIES[key]
        rows = fetch_series(key)
        pts = [(d, v, est) for d, v, est in rows if d <= today_iso and (not cutoff or d >= cutoff)]
        if not pts:
            continue
        last_d, last_v, last_est = pts[-1]
        latest_dates.append(last_d)
        any_est = any_est or any(est for _, _, est in pts)
        suffix = " (est.)" if last_est else ""
        trace = {
            "type": "scatter",
            "mode": "lines",
            "name": f"{label}  {_fmt_val(last_v, unit)}{suffix}",
            "x": [d for d, _, _ in pts],
            "y": [round(v, 4) for _, v, _ in pts],
            "line": {"width": 2, "color": C.PALETTE[i]},
            "hovertemplate": (
                "%{y:.2f}" + ("x" if unit == "x" else "%" if unit == "%" else "")
                if unit != "USD" else "$%{y:,.2f}"
            ) + f"<extra>{label}</extra>",
        }
        if len(units) == 2 and unit == units[1]:
            trace["yaxis"] = "y2"
        traces.append(trace)

    if not traces:
        raise C.CompositeError("No S&P 500 valuation data fell inside the selected range.")

    as_of = max(latest_dates)
    lay = C._layout(len(keys) > 1 or len(traces) > 1)
    unit_names = {"x": "multiple (x)", "%": "percent (%)", "USD": "USD"}
    _axis_style(lay["yaxis"], units[0], unit_names.get(units[0], units[0]))
    if len(units) == 2:
        y2 = {"overlaying": "y", "side": "right", "showgrid": False, "zeroline": True, "automargin": True}
        _axis_style(y2, units[1], unit_names.get(units[1], units[1]))
        lay["yaxis2"] = y2
    note = f"S&P 500 · {as_of}" + (" · † current estimate" if any_est else "")
    lay["annotations"] = [C._note(note)]

    return {
        "data": traces,
        "layout": lay,
        "sources": static_sources(",".join(keys)),
        "as_of": as_of,
        "convention": "; ".join(dict.fromkeys(f"{SERIES[k][0]} ({SERIES[k][2]}): {SERIES[k][4]}" for k in keys)),
        "controls": {"freq": "monthly", "ranges": list(RANGES), "range": rng},
    }
