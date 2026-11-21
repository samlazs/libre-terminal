"""FRED without an API key: the public CSV download (fredgraph.csv) and the series page for titles and units.

Used by the terminal's own views (composite.py) and, through scripts/run_backend.py, by the backend's FRED
provider when no FRED key is set. Standard library only.

FRED's edge network answers scripts that say what they are (the default Python user agent) but stalls requests
that imitate a browser ("Mozilla/5.0 ..."): that is why the download used to time out. Requests are kept few
(at most four in flight), retried with a growing timeout, and a failure blocks the download for a short while so
a tab of FRED cards does not wait on a source that is not answering.
"""
from __future__ import annotations

import csv
import html
import io
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = f"Python-urllib/{sys.version_info[0]}.{sys.version_info[1]}"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
PAGE_URL = "https://fred.stlouisfed.org/series/"
TIMEOUTS = (15, 25)                 # one retry with a longer wait
DOWN_FOR = 120                      # seconds the download is skipped after it stopped answering

# FRED API parameters -> fredgraph.csv parameters
UNITS = {"lin", "chg", "ch1", "pch", "pc1", "pca", "cch", "cca", "log"}
FREQ = {"d": "Daily", "w": "Weekly", "wef": "Weekly, Ending Friday", "weth": "Weekly, Ending Thursday", "wew": "Weekly, Ending Wednesday",
        "wetu": "Weekly, Ending Tuesday", "wem": "Weekly, Ending Monday", "wesu": "Weekly, Ending Sunday", "wesa": "Weekly, Ending Saturday",
        "bwew": "Biweekly, Ending Wednesday", "bwem": "Biweekly, Ending Monday", "m": "Monthly", "q": "Quarterly", "sa": "Semiannual", "a": "Annual"}
AGG = {"avg", "sum", "eop"}

_gate = threading.BoundedSemaphore(4)
_lock = threading.Lock()
_down = [0.0, ""]


class FredPublicError(Exception):
    """The public download could not serve this request (missing series, or FRED not answering)."""

    def __init__(self, msg, missing=False):
        super().__init__(msg)
        self.missing = missing


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


def _get(url):
    with _lock:
        if time.time() < _down[0]:
            raise FredPublicError(_down[1])
    last = None
    for timeout in TIMEOUTS:
        try:
            with _gate:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/csv,text/html,*/*"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                raise FredPublicError("FRED has no such series.", missing=True) from None
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001  (timeouts, resets)
            last = type(e).__name__
        time.sleep(1.5)
    msg = f"FRED's public download did not answer ({last}). Try again in a minute, or add the free FRED key with Setup-Keys.cmd (or setup-keys.sh)."
    with _lock:
        _down[0], _down[1] = time.time() + DOWN_FOR, msg
    raise FredPublicError(msg)


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
    """Forget a recorded outage (tests)."""
    with _lock:
        _down[0], _down[1] = 0.0, ""
