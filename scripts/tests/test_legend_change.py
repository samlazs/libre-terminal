"""TradingView legend change, run in a headless browser: the three legend sites as run_backend rewrites them, fed bars
stamped the way the chart gets them (Xetra at Berlin midnight = 23:00 UTC the day before, NYSE at New York midnight,
hourly bars across midnight, business-day strings and objects). Expected values worked by hand."""

import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from _browser import find_browser
from _common import check, done, make_tmp_dir

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import run_backend as rb  # noqa: E402

browser = find_browser()
if not browser:
    print("test_legend_change: skipped: Microsoft Edge / Chrome not found (set TERMINAL_TEST_BROWSER to run it)")
    print("test_legend_change: 0 checks passed")
    sys.exit(0)

# pywry 2.0.6's three sites (02-helpers.js, 11-legend.js), inside functions that hand back what the legend prints
HARNESS = """
function paint(a, b) { return a >= b ? "up" : "down"; }
function site1(entry, mainKey, d) {
    var _tvLegendColorize = paint;
        var chg = Number(d.close) - Number(d.open);
        var chgPct = Number(d.open) !== 0 ? ((chg / Number(d.open)) * 100) : 0;
        var clr = _tvLegendColorize(d.close, d.open);
    return [chg, chgPct, clr];
}
function site2(entry) {
    var colorize = paint, rows = entry._seriesRawData.main, last = rows[rows.length - 1];
            var chg = Number(last.close) - Number(last.open);
            var chgPct = Number(last.open) !== 0 ? ((chg / Number(last.open)) * 100) : 0;
            var clr = colorize(last.close, last.open);
    return [chg, chgPct, clr];
}
function site3(entry, sKey, d) {
    var colorize = paint;
                var chg    = d.close - d.open;
                var chgPct = d.open !== 0 ? ((chg / d.open) * 100) : 0;
                var clr    = colorize(d.close, d.open);
    return [chg, chgPct, clr];
}
function bar(t, o, c) { return {time: t, open: o, high: Math.max(o, c), low: Math.min(o, c), close: c}; }
function series(rows) { return {_seriesRawData: {main: rows}}; }
var out = {};
// EUNL.DE, Xetra: daily bars at Berlin midnight (CET = UTC+1): 2026-11-23 00:00 CET = 1795388400
var eunl = [bar(1795388400, 123.03, 126.645), bar(1795474800, 126.635, 124.565), bar(1795561200, 124.525, 122.24)];
out.eunl_last = site2(series(eunl));
out.eunl_hover = site1(series(eunl), "main", eunl[1]);
// SPY, NYSE Arca: New York midnight (EST = UTC-5): 2026-11-24 00:00 EST = 1795496400
var spy = [bar(1795496400, 721.0, 718.78), bar(1795582800, 703.2, 717.18)];
out.spy_hover = site3(series(spy), "main", {time: 1795582800, open: 703.2, high: 718.0, low: 700.1, close: 717.18});
// hourly bars across midnight UTC: 2026-11-25 22:00, 23:00, 2026-11-26 00:00
var night = [bar(1795644000, 99.8, 100), bar(1795647600, 100, 101), bar(1795651200, 101.2, 99.5)];
out.night = site2(series(night));
out.night_hover = site1(series(night), "main", night[1]);
// business days as strings and as objects
out.days = site3(series([bar("2026-11-24", 49, 50), bar("2026-11-25", 52, 51)]), "main", bar("2026-11-25", 52, 51));
out.objs = site1(series([bar({year: 2026, month: 11, day: 24}, 49, 50), bar({year: 2026, month: 11, day: 25}, 52, 51)]), "main",
                 bar({year: 2026, month: 11, day: 25}, 52, 51));
// the first bar, and a point that is not a raw bar (Heikin Ashi): the open, as before
out.first = site2(series([bar(1795561200, 124.525, 122.24)]));
out.heikin = site1(series(eunl), "main", bar(1795561200, 123.5, 122.9));
document.getElementById("out").textContent = JSON.stringify(out);
"""
page, n = rb.legend_change_from_previous_close("<!doctype html><html><head><meta charset='utf-8'></head><body><pre id='out'>none</pre>"
                                              "<script>" + HARNESS + "</script></body></html>")
check(n == 3, "the harness carries pywry's three sites and all three are rewritten")
tmp = make_tmp_dir("legend-")
(tmp / "legend.html").write_text(page, encoding="utf-8")
profile = tempfile.mkdtemp(prefix="terminal-edge-")
cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
       f"--user-data-dir={profile}", "--dump-dom", (tmp / "legend.html").as_uri()]
try:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
finally:
    shutil.rmtree(profile, ignore_errors=True)
m = re.search(r"<pre id=\"out\">([^<]*)</pre>", r.stdout or "")
check(m is not None and m.group(1) != "none", "the browser ran the legend code: " + (m.group(1)[:200] if m else (r.stderr or "")[-200:]))
got = json.loads(m.group(1).replace("&quot;", '"'))


def same(key, chg, pct, clr):
    c, p, k = got[key]
    return abs(c - chg) < 1e-9 and abs(p - pct) < 1e-4 and k == clr


# 122.24 - 124.565 = -2.325; / 124.565 = -1.86650%   (the open gave 122.24 - 124.525 = -2.285, -1.835%)
check(same("eunl_last", -2.325, -1.86650, "down"), f"Xetra fund, last bar: -2.325 (-1.8665%) from the previous close ({got['eunl_last']})")
# 124.565 - 126.645 = -2.08; / 126.645 = -1.64238%
check(same("eunl_hover", -2.08, -1.64238, "down"), f"Xetra fund, hovered bar: from the bar before it ({got['eunl_hover']})")
# 717.18 - 718.78 = -1.60; / 718.78 = -0.22260%   (the open gave +13.98, +1.988%, in green)
check(same("spy_hover", -1.6, -0.22260, "down"), f"U.S. fund: -1.60 (-0.2226%), red, not +13.98 from a gap-down open ({got['spy_hover']})")
# 99.5 - 101 = -1.5; / 101 = -1.48515%   (open 101.2 gave -1.70 / -1.680%); hovered 23:00: 101 - 100 = +1, +1%
check(same("night", -1.5, -1.48515, "down") and same("night_hover", 1.0, 1.0, "up"), f"bars across midnight UTC ({got['night']}, {got['night_hover']})")
# 51 - 50 = +1, +2%, green (the candle is red: 52 -> 51)
check(same("days", 1.0, 2.0, "up") and same("objs", 1.0, 2.0, "up"), f"business-day strings and objects ({got['days']}, {got['objs']})")
check(same("first", 122.24 - 124.525, (122.24 / 124.525 - 1) * 100, "down"), "the first bar: from its open, as before")
check(same("heikin", 122.9 - 123.5, (122.9 / 123.5 - 1) * 100, "down"), "a point that is not a raw bar keeps its open")
done("test_legend_change")
