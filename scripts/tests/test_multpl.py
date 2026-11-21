"""Offline tests for terminal/multpl.py: HTML table parsing, unit declarations, dual-axis layout, and W5 envelope."""

import datetime as dt

from _common import check, done

import composite as c
import multpl

SAMPLE_PE_HTML = """
<html><body>
<table id="datatable">
  <tr><th>Date</th><th>Value</th></tr>
  <tr><td>Nov 20, 2026</td><td>&#x2002; † 23.55</td></tr>
  <tr><td>Nov 1, 2026</td><td>&#x2002; 25.73</td></tr>
  <tr><td>Nov 1, 2026</td><td>&#x2002; 99.99</td></tr>
  <tr><td>bad date</td><td>12.3</td></tr>
  <tr><td>Oct 1, 2026</td><td>n/a</td></tr>
  <tr><td>Jan 1, 1871</td><td>&#x2002; 11.10</td></tr>
</table>
</body></html>
"""

SAMPLE_YIELD_HTML = """
<html><body>
<table id="datatable">
  <tr><th>Date</th><th>Value</th></tr>
  <tr><td>Nov 20, 2026</td><td>† 1.17%</td></tr>
  <tr><td>Dec 31, 2020</td><td>-1,234.50%</td></tr>
  <tr><td>Jan 1, 1900</td><td>$4,500.25</td></tr>
</table>
</body></html>
"""

rows = multpl.parse_table(SAMPLE_PE_HTML)
check(rows == [("1871-01-01", 11.10, False), ("2026-11-01", 25.73, False), ("2026-11-20", 23.55, True)],
      f"parse_table sorts ascending, dedups dates, strips en-space and detects dagger: {rows}")

yrows = multpl.parse_table(SAMPLE_YIELD_HTML)
check(yrows == [("1900-01-01", 4500.25, False), ("2020-12-31", -1234.50, False), ("2026-11-20", 1.17, True)],
      f"parse_table handles %, $, commas, negatives: {yrows}")

check(multpl.parse_table("<html><body><p>no table</p></body></html>") == [], "missing datatable returns empty list")

FAKE_DATA = {
    "pe_month": [("1871-01-01", 11.10, False), ("2020-01-01", 22.0, False), ("2026-11-20", 23.55, True)],
    "shiller_pe_month": [("1871-02-01", 10.92, False), ("2020-01-01", 30.5, False), ("2026-11-20", 36.44, False)],
    "dividend_yield_month": [("2020-01-01", 1.85, False), ("2026-11-20", 1.17, True), ("2026-11-30", 1.08, True)],
    "earnings_month": [("2020-01-01", 140.0, False), ("2026-06-30", 298.03, False)],
}

saved_fetch = multpl.fetch_series
try:
    multpl.fetch_series = lambda k: FAKE_DATA[k]

    # Single series
    one = multpl.chart(series_name="shiller_pe_month", range="MAX", today=dt.date(2026, 11, 21))
    check(len(one["data"]) == 1 and one["data"][0]["x"][0] == "1871-02-01" and one["layout"]["yaxis"]["ticksuffix"] == "x",
          "single series uses MAX back to 1871 and multiple axis")
    check(one["as_of"] == "2026-11-20" and one["sources"][0]["name"] == "multpl.com", "W5 as_of and multpl.com source")
    check("Shiller CAPE" in one["convention"] and "(x)" in one["convention"], "W5 convention declares series and unit")

    # Default dual multiple series -> single y-axis
    dual = multpl.chart(today=dt.date(2026, 11, 21))
    check(len(dual["data"]) == 2 and "yaxis2" not in dual["layout"] and dual["controls"]["range"] == "50Y",
          "default pe_month,shiller_pe_month shares primary y-axis")
    check("(est.)" in dual["data"][0]["name"] and "† current estimate" in dual["layout"]["annotations"][0]["text"],
          "dagger estimate flagged in legend and note")

    # Mixed 2-unit series (x + %) -> secondary y2 axis, and future month-end row filtered out
    mixed = multpl.chart(series_name="pe_month,dividend_yield_month", range="10Y", today=dt.date(2026, 11, 21))
    check(len(mixed["data"]) == 2 and mixed["data"][1].get("yaxis") == "y2"
          and mixed["layout"]["yaxis"]["ticksuffix"] == "x"
          and mixed["layout"]["yaxis2"]["ticksuffix"] == "%"
          and mixed["data"][1]["x"] == ["2020-01-01", "2026-11-20"]
          and mixed["data"][1]["y"] == [1.85, 1.17]
          and mixed["as_of"] == "2026-11-20",
          "2-unit chart assigns second unit (%) to yaxis2 and drops future month-end rows")
    check(mixed["data"][0]["x"] == ["2020-01-01", "2026-11-20"], "10Y range filters out 1871")

    # 3-unit rejection
    three_err = False
    try:
        multpl.chart(series_name="pe_month,dividend_yield_month,earnings_month")
    except c.CompositeError:
        three_err = True
    check(three_err, "rejects >2 distinct units with CompositeError")

    # Unknown series rejection
    unk_err = False
    try:
        multpl.chart(series_name="nope_series")
    except c.CompositeError:
        unk_err = True
    check(unk_err, "rejects unknown series with CompositeError")

    # composite dispatch + static_sources integration
    dispatched = c.dispatch("sp500_multiples", {"series_name": "pe_month,shiller_pe_month", "range": "5Y"})
    check(dispatched["sources"] and dispatched["sources"][0]["name"] == "multpl.com" and dispatched["as_of"] == "2026-11-20",
          "composite.dispatch('sp500_multiples') works end-to-end")
    ss = c.static_sources("sp500_multiples", {"series_name": "dividend_yield_month"})
    check(len(ss) == 1 and ss[0]["name"] == "multpl.com" and "s-p-500-dividend-yield" in ss[0]["url"],
          "composite.static_sources('sp500_multiples') resolves chosen series URL")
finally:
    multpl.fetch_series = saved_fetch

done("test_multpl")
