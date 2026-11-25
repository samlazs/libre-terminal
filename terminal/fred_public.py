"""FRED without an API key: the public CSV download (fredgraph.csv) and the series page for titles and units.

Used by the terminal's own views (terminal/composite/) and, through scripts/run_backend.py, by the backend's FRED
provider when no FRED key is set. Standard library only.

FRED's edge network answers scripts that say what they are (the default Python user agent) but stalls requests
that imitate a browser ("Mozilla/5.0 ..."): that is why the download used to time out. The requests go through the
terminal's one outbound client (composite.core.http.fetch, policy "fred"): at most four in flight, retried with a
growing timeout but never on a 429, and a failure blocks the download for two minutes so a tab of FRED cards does
not wait on a source that is not answering.
"""
from __future__ import annotations

import csv
import html
import io
import re
import sys
import urllib.parse

UA = f"Python-urllib/{sys.version_info[0]}.{sys.version_info[1]}"     # policy "fred" in composite/core/http.py
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
PAGE_URL = "https://fred.stlouisfed.org/series/"
TIMEOUTS = (15, 25)                 # one retry with a longer wait (policy "fred")
DOWN_FOR = 120                      # seconds the download is skipped after it stopped answering (policy "fred")

# FRED API parameters -> fredgraph.csv parameters
UNITS = {"lin", "chg", "ch1", "pch", "pc1", "pca", "cch", "cca", "log"}
FREQ = {"d": "Daily", "w": "Weekly", "wef": "Weekly, Ending Friday", "weth": "Weekly, Ending Thursday", "wew": "Weekly, Ending Wednesday",
        "wetu": "Weekly, Ending Tuesday", "wem": "Weekly, Ending Monday", "wesu": "Weekly, Ending Sunday", "wesa": "Weekly, Ending Saturday",
        "bwew": "Biweekly, Ending Wednesday", "bwem": "Biweekly, Ending Monday", "m": "Monthly", "q": "Quarterly", "sa": "Semiannual", "a": "Annual"}
AGG = {"avg", "sum", "eop"}

class FredPublicError(Exception):
    """The public download could not serve this request (missing series, or FRED not answering)."""

    def __init__(self, msg, missing=False, kind=None, retry_after=None):
        super().__init__(msg)
        self.missing = missing
        self.kind = kind                    # contract error kind when known (rate_limited for a 429)
        self.retry_after = retry_after


def valid_id(sid):
    return bool(re.fullmatch(r"[A-Za-z0-9_.]{1,40}", sid or ""))


def csv_url(sid, start=None, end=None, units=None, freq=None, agg=None):
    q = {"id": sid}
    if start:
        q["cosd"] = str(start)[:10]
    if end:
        q["coed"] = str(end)[:10]
    if units and units != "lin":
        q["transformation"] = units
    if freq:
        q["fq"] = FREQ[freq]
        q["fam"] = agg or "avg"
    return CSV_URL + "?" + urllib.parse.urlencode(q)


def parse_csv(text):
    """[(date, value or None), ...] from a fredgraph.csv body (empty cells and '.' are missing values)."""
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}$", row[0]):
            v = row[1].strip()
            try:
                out.append((row[0], float(v) if v not in ("", ".") else None))
            except ValueError:
                out.append((row[0], None))
    return out


def parse_meta(page):
    """{title, units, frequency, seasonal_adjustment} from a FRED series page; missing parts are None."""
    def one(pat):
        m = re.search(pat, page, re.S)
        return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip(" ,") if m else None
    return {"title": one(r'<meta property="og:title" content="([^"]*)"'),
            "units": one(r'series-meta-value-units">([^<]*)<'),
            "frequency": one(r'series-meta-value-frequency">([^<]*)<'),
            "seasonal_adjustment": one(r'series-meta-value-units">[^<]*</span>,\s*</div>\s*<div class="default-text">([^<]*)<')}


def _http():
    from composite.core import http      # imported on first use: this module also runs inside the backend process
    return http


def _get(url):
    H = _http()
    try:
        return H.fetch(url, provider="fred", parse="text", headers={"Accept": "text/csv,text/html,*/*"})
    except H.CompositeError as e:
        status = getattr(e, "http_status", None)
        if status in (400, 404):
            raise FredPublicError("FRED has no such series.", missing=True) from None
        if e.kind == "rate_limited":        # a 429, or its cooldown: not retried, not called an outage
            wait = int(e.retry_after or 0) + 1 if e.retry_after else None
            raise FredPublicError("FRED's public download is limiting requests" + (f"; try again in ~{wait} s." if wait else "; try again shortly.")
                                  + " The free FRED key (Setup-Keys.cmd or setup-keys.sh) avoids this limit.",
                                  kind="rate_limited", retry_after=e.retry_after) from None
        why = f"HTTP {status}" if status else "no answer in time" if e.kind == "timeout" else "not reachable"
        raise FredPublicError(f"FRED's public download did not answer ({why}). Try again in a minute, or add the free FRED key "
                              "with Setup-Keys.cmd (or setup-keys.sh).") from None


def observations(sid, start=None, end=None, units=None, freq=None, agg=None):
    """[(date, value or None), ...] for one series. Raises FredPublicError."""
    if not valid_id(sid):
        raise FredPublicError(f"'{sid}' is not a FRED series id.", missing=True)
    if units and units not in UNITS or freq and freq not in FREQ or agg and agg not in AGG:
        raise FredPublicError("FRED's public download does not offer this transformation.")
    rows = parse_csv(_get(csv_url(sid, start, end, units, freq, agg)))
    if not rows:
        raise FredPublicError(f"FRED has no series '{sid}'.", missing=True)
    return rows


def metadata(sid):
    """Title, units, frequency and seasonal adjustment from the series page ({} when it cannot be read)."""
    if not valid_id(sid):
        return {}
    try:
        return parse_meta(_get(PAGE_URL + urllib.parse.quote(sid)))
    except FredPublicError:
        return {}


def reset():
    """Forget a recorded outage or cooldown (tests)."""
    _http().reset_outbound("fred")
