"""Montreal time everywhere (owner, 2026-11-19): backend conversions of naive non-Montreal times, server-side stamps.
Expected values worked by hand: Montreal is UTC-4 (EDT) until 2026-11-01 06:00 UTC, UTC-5 (EST) after; CET is UTC+1,
CEST UTC+2 (until 2026-10-25)."""

import datetime as dt
import pathlib
import sys

import pandas as pd

from _common import check, done

import composite as c

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_backend as rb  # noqa: E402

# ---- UTC wall clock -> Montreal, on both sides of the 2026-11-01 change
got = list(rb.montreal_bar_times(pd.Series(["2026-11-19 16:25:00", "2026-10-30 13:30:00", "2026-11-01 05:30:00", "2026-11-01 06:30:00"])))
check(got == ["2026-11-19 11:25:00", "2026-10-30 09:30:00", "2026-11-01 01:30:00", "2026-11-01 01:30:00"],
      "16:25 UTC = 11:25 EST; 13:30 UTC = 09:30 EDT; 05:30 UTC = 01:30 EDT and 06:30 UTC = 01:30 EST (the repeated hour)")

# ---- the Yahoo download wrapper: intraday asks for zone-aware bars and converts; daily untouched
import openbb_yfinance.utils.helpers as yh  # noqa: E402
import openbb_nasdaq.models.nordic_news as nn  # noqa: E402

saved_dl, saved_parse = yh.yf_download, nn.parse_release_time
seen = []
try:
    def fake(symbol, start_date=None, end_date=None, interval="1d", *a, **kw):
        seen.append((interval, kw.get("ignore_tz", True)))
        if interval == "1d":
            return pd.DataFrame({"date": ["2026-11-19"], "open": [1.0]})
        return pd.DataFrame({"date": ["2026-11-19 16:25:00"], "open": [1.0]})   # what OpenBB returns once bars are zone-aware: UTC
    yh.yf_download = fake
    rb.install_montreal_times([])
    d1 = yh.yf_download("SAP.DE", interval="1d")
    d5 = yh.yf_download("SAP.DE", "2026-11-18", None, "5m")
    check(list(d1["date"]) == ["2026-11-19"] and seen[0] == ("1d", True), "daily bars untouched (dates)")
    check(list(d5["date"]) == ["2026-11-19 11:25:00"] and seen[1] == ("5m", False), "5-minute bar: zone-aware request, Montreal time (17:25 Frankfurt = 11:25)")
    # ---- Nasdaq Nordic: CET release time gets its offset
    w = nn.parse_release_time("2026-11-19 20:08:00")
    s = nn.parse_release_time("2026-07-01 10:00:00")
    check(w.utcoffset() == dt.timedelta(hours=1) and s.utcoffset() == dt.timedelta(hours=2), "Nordic notice: CET +01:00 in November, CEST +02:00 in July")
    check(w.astimezone(dt.timezone.utc).strftime("%H:%M") == "19:08", "20:08 CET = 19:08 UTC (= 14:08 in Montreal)")
finally:
    yh.yf_download, nn.parse_release_time = saved_dl, saved_parse

# ---- server-side stamps (stale copies, build time) in Montreal time whatever the machine's zone
t = dt.datetime(2026, 11, 19, 19, 20, tzinfo=dt.timezone.utc).timestamp()
check(c.mtl_time(t) == "2026-11-19 14:20", "19:20 UTC = 14:20 Montreal")
check(c.mtl_time(dt.datetime(2026, 7, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()) == "2026-07-01 08:00", "summer: UTC-4")
check("copy saved 2026-11-19 14:20" in c.stale_text([t]), "stale note in Montreal time")
check("copy saved 2026-11-19 14:20" in rb.stale_message("EIA", t), "backend stale note in Montreal time")
done("test_timezone")
