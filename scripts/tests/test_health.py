"""Offline tests for scripts/health_check.py and `launcher.py health` (zero external network)."""

from __future__ import annotations

import csv
import datetime as dt
import http.server
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "terminal"))
sys.path.insert(0, str(ROOT / "scripts"))
import health_check as H  # noqa: E402
import redact  # noqa: E402

PASSED = 0


def check(cond: bool, msg: str) -> None:
    global PASSED
    if not cond:
        raise AssertionError("FAIL: " + msg)
    PASSED += 1


def main() -> int:
    # ------------------------------------------------------------------ 1. NY market dates & relative date params
    t_summer = dt.datetime(2026, 7, 15, 16, 0, tzinfo=dt.timezone.utc)
    check(H.market_date(t_summer) == dt.date(2026, 7, 15), "summer NY market_date")
    check(H._ny_date_fallback(t_summer) == dt.date(2026, 7, 15), "summer NY fallback date")
    check(H.resolve_param("@days:-90", now=t_summer) == "2026-04-16", "@days:-90 from 2026-07-15")
    check(H.resolve_param("@bday:-1", now=t_summer) == "2026-07-14", "@bday:-1 from Wed")
    check(H.resolve_param("@bday:-3", now=t_summer) == "2026-07-10", "@bday:-3 skips weekend")

    t_mon = dt.datetime(2026, 7, 13, 16, 0, tzinfo=dt.timezone.utc)
    check(H.resolve_param("@bday:-1", now=t_mon) == "2026-07-10", "@bday:-1 from Monday -> Friday")

    t_edt_night = dt.datetime(2026, 7, 16, 1, 30, tzinfo=dt.timezone.utc)   # 21:30 EDT on 2026-07-15
    t_est_night = dt.datetime(2026, 1, 16, 2, 30, tzinfo=dt.timezone.utc)   # 21:30 EST on 2026-01-15
    check(H.market_date(t_edt_night) == dt.date(2026, 7, 15), "EDT evening stays on NY day before UTC rollover")
    check(H._ny_date_fallback(t_edt_night) == dt.date(2026, 7, 15), "EDT fallback stays on NY day")
    check(H.market_date(t_est_night) == dt.date(2026, 1, 15), "EST evening stays on NY day before UTC rollover")
    check(H._ny_date_fallback(t_est_night) == dt.date(2026, 1, 15), "EST fallback stays on NY day")
    check(H.resolve_param("2026-01-01", now=t_summer) == "2026-01-01", "plain date untouched")
    check(H.resolve_param("@unknown:5", now=t_summer) == "@unknown:5", "unknown @ token untouched")
    check(H.resolve_param(None, now=t_summer) is None, "None untouched")
    check(H.resolve_param(42, now=t_summer) == 42, "int untouched")

    # ------------------------------------------------------------------ 2. symbol_for, country_for, assign_groups, endpoint_key
    check(H.symbol_for("ry.to", "tmx") == "RY", "symbol_for tmx strips .TO")
    check(H.symbol_for("VOLV-B.ST", "nordic") == "VOLV B", "symbol_for nordic normalizes dash and suffix")
    check(H.symbol_for("BTC-USDT", "crypto_base") == "BTC", "symbol_for crypto_base strips -USDT")

    country_p = {"paramName": "country", "options": [{"value": "korea"}, {"value": "united_states"}]}
    check(H.country_for(country_p, "south_korea,united_states") == "korea,united_states", "country_for maps alias CSV")
    check(H.country_for(country_p, ["south_korea"]) == ["korea"], "country_for maps alias list")

    bank_groups = [
        {"name": "Bank", "paramName": "symbol", "value": "JPM"},
        {"name": "RSSD", "paramName": "rssd_id", "value": "1039502", "syncFrom": "Bank", "syncUrl": "/x/bank_rssd"},
    ]
    w_has_both = {"params": [{"paramName": "symbol"}, {"paramName": "rssd_id"}]}
    w_rssd_only = {"params": [{"paramName": "rssd_id"}]}
    check(H.assign_groups(w_has_both, bank_groups) == ["Bank"], "assign_groups skips derived syncFrom when parent symbol present")
    check(H.assign_groups(w_rssd_only, bank_groups) == ["RSSD"], "assign_groups attaches derived RSSD when parent absent")

    check(H.endpoint_key("v5", "/api/v1/federal_reserve/atlanta/gdpnow") == "federal_reserve/atlanta/gdpnow", "endpoint_key v5")
    check(H.endpoint_key("x", "/x/bank_rssd") == "x:bank_rssd", "endpoint_key x")

    # ------------------------------------------------------------------ 3. cards() & request_for()
    bases = {"x": "http://127.0.0.1:6950", "v5": "http://127.0.0.1:6900", "v4": "http://127.0.0.1:6901"}
    sample_D = {
        "concepts": {"c1": {"members": [{"b": "v5", "w": "w_tmx", "sym": "tmx"}]}},
        "dashboards": [
            {
                "name": "TestDash",
                "groups": [{"name": "Ticker", "paramName": "symbol", "value": "RY.TO"}],
                "tabs": [
                    {
                        "name": "Main",
                        "items": [
                            {
                                "uid": "lib-1",
                                "title": "Family",
                                "backend": "v5",
                                "widgetId": "w_tmx",
                                "params": {},
                                "groups": ["Ticker"],
                                "choices": [
                                    {"b": "v5", "w": "w_tmx", "l": "TMX Quote"},
                                    {"b": "v5", "w": "w_c2", "l": "Choice 2", "p": {"start_date": "@days:-30"}, "k": "fred"},
                                    {"b": "v5", "w": "w_req", "l": "Needs Input", "r": ["cusip"]},
                                    {"b": "v4", "w": "w_v4", "l": "Legacy V4"},
                                ],
                            },
                            {"uid": "bad-def", "title": "Bad Def", "backend": "v5", "widgetId": "w_missing_req", "params": {}, "groups": []},
                        ],
                    },
                    {
                        "name": "Data status",
                        "items": [
                            {"uid": "ds-1", "title": "Broken", "backend": "v5", "widgetId": "w_broken", "manual": True, "params": {}, "groups": []},
                        ],
                    },
                ],
            }
        ],
    }
    sample_W = {
        "x": {},
        "v5": {
            "w_tmx": {"name": "TMX", "endpoint": "/api/v1/equity/price/quote", "type": "table", "params": [{"paramName": "symbol", "value": "AAPL"}]},
            "w_c2": {"name": "C2", "endpoint": "/api/v1/economy/cpi", "type": "table", "params": [{"paramName": "start_date", "value": ""}, {"paramName": "provider", "value": "fred"}]},
            "w_req": {"name": "Req", "endpoint": "/api/v1/finra/fixedincome/bond_historical", "type": "table", "params": [{"paramName": "cusip", "value": ""}]},
            "w_missing_req": {"name": "MissReq", "endpoint": "/api/v1/test/miss", "type": "table", "params": [{"paramName": "query", "value": "", "optional": False}]},
            "w_broken": {"name": "BrokenEp", "endpoint": "/api/v1/federal_reserve/atlanta/gdpnow", "type": "table", "params": []},
        },
        "v4": {},
    }
    rows_v4_down = list(H.cards(sample_D, sample_W, bases, include_broken=False, v4_up=False, now=t_summer))
    check(len(rows_v4_down) == 5, f"Data status excluded by default (got {len(rows_v4_down)})")
    by_choice = {r["title"]: r for r in rows_v4_down}
    check("symbol=RY" in (by_choice["TMX Quote"]["url"] or ""), "concept member rewrites RY.TO -> RY")
    check("start_date=2026-06-15" in (by_choice["Choice 2"]["url"] or ""), "library choice applies c['p'] & resolves @days:-30")
    check(by_choice["Choice 2"]["needsKey"] == "fred", "library choice propagates c['k'] needsKey")
    check(H.classify(by_choice["Needs Input"], None)[0] == "skipped", "explicit require missing -> skipped")
    check(H.classify(by_choice["Bad Def"], None)[0] == "bad-default", "undeclared required optional:False -> bad-default")
    check(H.classify(by_choice["Legacy V4"], None)[0] == "skipped", "v4_up=False marks V4 card skipped, not our-bug")

    rows_with_status = list(H.cards(sample_D, sample_W, bases, include_broken=True, v4_up=False, now=t_summer))
    check(len(rows_with_status) == 6 and rows_with_status[-1]["is_status_tab"] is True, "include_broken includes Data status card with is_status_tab=True")

    # ------------------------------------------------------------------ 4. measure()
    check(H.measure({"type": "table"}, json.dumps({"results": [{"a": 1}, {"a": 2}]})) == (2, None, False, "list"), "measure table list")
    check(H.measure({"type": "table"}, json.dumps({"results": []})) == (0, None, True, "list"), "measure empty table")
    check(H.measure({"type": "table"}, json.dumps({"a": 1})) == (1, None, False, "dict"), "measure dict")
    check(H.measure({"type": "table"}, json.dumps(None)) == (0, None, True, "null"), "measure null")
    check(H.measure({"type": "chart"}, json.dumps({"results": {"content": {"data": [{"x": [1, 2, 3], "y": [4, 5, 6]}]}}},)) == (0, None, True, "chart?"), "chart without dataKey returns chart?")
    check(H.measure({"type": "chart", "dataKey": "results"}, json.dumps({"results": {"content": {"data": [{"x": [1, 2, 3]}]}}})) == (3, 1, False, "figure"), "chart wrapped content figure")
    check(H.measure({"type": "chart", "dataKey": "results"}, json.dumps({"results": {"data": [{"y": []}]}})) == (0, 1, True, "figure"), "chart empty trace -> empty=True")
    check(H.measure({"type": "markdown", "dataKey": "results"}, json.dumps({"results": "## Hi"})) == (1, None, False, "md"), "markdown str")
    check(H.measure({"type": "markdown", "dataKey": "results"}, json.dumps({"results": "   "})) == (0, None, True, "md"), "markdown blank")
    check(H.measure({"type": "iframe"}, "<div>ok</div>") == (1, None, False, "html"), "iframe html")
    check(H.measure({"type": "iframe"}, "  \n ") == (0, None, True, "html"), "iframe blank")

    # ------------------------------------------------------------------ 5. classify()
    local_card = {"backend": "v5", "url": "http://127.0.0.1:6900/api/v1/foo", "provider": "imf", "type": "table"}
    x_card = {"backend": "x", "url": "http://127.0.0.1:6950/x/foo", "provider": "terminal", "type": "table"}
    check(H.classify(local_card, {"status": 200, "secs": 1.2, "empty": False})[0] == "ok", "classify ok")
    check(H.classify(local_card, {"status": 200, "secs": 9.2, "empty": False})[0] == "slow", "classify slow > 8s")
    check(H.classify(local_card, {"status": 200, "secs": 0.5, "empty": True})[0] == "empty", "classify empty")
    check(H.classify(local_card, {"untested": True})[0] == "rate-limited", "classify OECD untested")
    check(H.classify(local_card, {"status": 429, "etext": "Too Many Requests"})[0] == "rate-limited", "classify 429")
    check(H.classify(local_card, {"status": 402, "etext": "Premium Endpoint"})[0] == "paid-plan", "classify paid-plan")
    check(H.classify(local_card, {"status": 401, "etext": "Missing credential 'fred_api_key'"}) == ("needs-key", "fred_api_key"), "classify needs-key")
    check(H.classify(local_card, {"status": 400, "etext": "Could not find CIK for SAP.DE"})[0] == "not-covered", "classify not-covered")
    check(H.classify(local_card, {"status": 422, "etext": "1 validation error for QueryParams\ncusip\nField required"})[0] == "bad-default", "classify QueryParams Field required")
    check(H.classify(local_card, {"status": 0, "etext": "timeout after 90 s"})[0] == "upstream-error", "classify timeout")
    check(H.classify(local_card, {"status": 502, "etext": "HTTP Error 502: Bad Gateway"})[0] == "upstream-error", "classify 502 upstream-error")
    check(H.classify(local_card, {"status": 500, "etext": "Worksheet named 'Data' not found"})[0] == "upstream-error", "classify Worksheet named upstream-error")
    check(H.classify(local_card, {"status": -1, "etext": "connection failed"})[0] == "our-bug", "classify backend -1 our-bug")
    check(H.classify(local_card, {"status": 404, "etext": "Not Found"})[0] == "our-bug", "classify bare 404 Not Found our-bug")
    check(H.classify(local_card, {"status": 500, "etext": "ValueError: I/O operation on closed file"})[0] == "our-bug", "classify closed file our-bug")
    check(H.classify(x_card, {"status": 500, "etext": "KeyError: 'close'"})[0] == "our-bug", "classify unhandled x view internal error our-bug")

    # ------------------------------------------------------------------ 6. is_oecd & build_candidates
    check(H.is_oecd("http://127.0.0.1:6900/api/v1/economy/cpi?provider=oecd", "w1"), "is_oecd provider=oecd")
    check(H.is_oecd("http://127.0.0.1:6901/api/v1/economy/gdp?provider=econdb", "w2"), "is_oecd provider=econdb")
    check(H.is_oecd("http://127.0.0.1:6950/x/country_monitor", "x_country_monitor"), "is_oecd x_country_monitor")
    check(not H.is_oecd("http://127.0.0.1:6900/api/v1/economy/cpi?provider=imf", "w3"), "is_oecd false for imf")

    existing_health = {"federal_reserve/atlanta/gdpnow": "Atlanta Fed moved pages"}
    prior_hist = {"dallas/broken_ep": {"last_ok": "2026-11-10", "last_seen": "2026-11-10", "last_cls": "ok", "last_status": 200}}
    secret_raw = "Error fetching https://api.example.com/v1?api_key=TESTSECRET987654321"
    test_rows = [
        {
            "dashboard": "Macro", "tab": "Main", "title": "Dallas", "ep_key": "dallas/broken_ep",
            "cls": "upstream-error", "status": 502, "cause": "source failed", "error": redact.redact(secret_raw),
            "rows": 0, "secs": 1.1, "err_has_key": redact.contains_secret(secret_raw), "err_has_url": True,
        },
        {
            "dashboard": "Macro", "tab": "Data status", "title": "GDPNow", "ep_key": "federal_reserve/atlanta/gdpnow",
            "cls": "ok", "status": 200, "cause": "", "error": "", "rows": 12, "secs": 0.4, "err_has_key": False, "err_has_url": False,
        },
        {
            "dashboard": "Rates", "tab": "Main", "title": "Broken X", "ep_key": "x:broken_view",
            "cls": "our-bug", "status": 500, "cause": "internal error", "error": "boom", "rows": 0, "secs": 0.1, "err_has_key": False, "err_has_url": False,
        },
    ]
    cands, new_hist = H.build_candidates(test_rows, existing_health, prior_hist, "2026-11-21")
    check(len(cands["add"]) == 1 and cands["add"][0]["endpoint"] == "dallas/broken_ep", "build_candidates detects new upstream-error add candidate")
    check(cands["add"][0]["last_seen_ok"] == "2026-11-10", "build_candidates preserves last_seen_ok from history")
    check("TESTSECRET987654321" not in json.dumps(cands) and "REDACTED" in cands["add"][0]["error"], "candidates JSON redacts secrets")
    check(len(cands["recovered"]) == 1 and cands["recovered"][0]["seen_ok_on"] == "2026-11-21", "build_candidates detects recovered health.json endpoint")
    check(len(cands["x_errors"]) == 1 and cands["x_errors"][0]["endpoint"] == "x:broken_view", "x:* errors reported separately in x_errors")
    check(new_hist["federal_reserve/atlanta/gdpnow"]["last_ok"] == "2026-11-21", "history updates last_ok on ok row")
    check(new_hist["dallas/broken_ep"]["last_ok"] == "2026-11-10", "history keeps prior last_ok when failing")

    # ------------------------------------------------------------------ 7. End-to-end mock HTTP server test
    hits: list[str] = []

    class MockHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            hits.append(path)
            if path == "/api/v1/ok":
                body = json.dumps({"results": [{"v": 1}, {"v": 2}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/v1/broken":
                body = json.dumps({"detail": "HTTP Error 502 from https://upstream.example/data?api_key=MYSECRET123456"}).encode("utf-8")
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/v1/oecd/one":
                body = json.dumps({"detail": "Too Many Requests (rate limit)"}).encode("utf-8")
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/v1/oecd/two":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"results": [{"v": 9}]}')
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"detail": "Not Found"}')

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        mock_D = {
            "dashboards": [
                {
                    "name": "Mock",
                    "groups": [],
                    "tabs": [
                        {
                            "name": "Main",
                            "items": [
                                {"uid": "m-oecd1", "title": "OECD 1", "backend": "v5", "widgetId": "wo1", "params": {}, "groups": []},
                                {"uid": "m-oecd2", "title": "OECD 2", "backend": "v5", "widgetId": "wo2", "params": {}, "groups": []},
                                {"uid": "m-ok", "title": "OK Card", "backend": "v5", "widgetId": "wok", "params": {}, "groups": []},
                                {"uid": "m-brk", "title": "Broken Card", "backend": "v5", "widgetId": "wbrk", "params": {}, "groups": []},
                            ],
                        }
                    ],
                }
            ]
        }
        mock_W = {
            "x": {},
            "v4": {},
            "v5": {
                "wo1": {"name": "OECD 1", "endpoint": "/api/v1/oecd/one", "type": "table", "params": [{"paramName": "provider", "value": "oecd"}]},
                "wo2": {"name": "OECD 2", "endpoint": "/api/v1/oecd/two", "type": "table", "params": [{"paramName": "provider", "value": "oecd"}]},
                "wok": {"name": "OK", "endpoint": "/api/v1/ok", "type": "table", "params": []},
                "wbrk": {"name": "Broken", "endpoint": "/api/v1/broken", "type": "table", "params": []},
            },
        }
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            out_d = td_p / "notes"
            cache_d = td_p / "cache"
            dry = H.run_health(
                ui_port=port, v5_port=port, v4_port=port, dry_run=True,
                out_dir=out_d, cache_dir=cache_d, catalogs=(mock_D, mock_W, False),
                health_file=td_p / "missing_health.json", run_date="2026-11-21",
            )
            check(dry["dry_run"] and dry["normal_urls"] == 2 and dry["oecd_urls"] == 2, "dry-run counts normal & oecd without hitting endpoints")
            check(len(hits) == 0, "dry-run sent zero HTTP requests")

            res = H.run_health(
                ui_port=port, v5_port=port, v4_port=port, gap=0.0, oecd_gap=0.0,
                out_dir=out_d, cache_dir=cache_d, catalogs=(mock_D, mock_W, False),
                health_file=td_p / "missing_health.json", run_date="2026-11-21",
            )
            check(hits == ["/api/v1/ok", "/api/v1/broken", "/api/v1/oecd/one"], f"normal ran before oecd and 429 stopped 2nd oecd (hits={hits})")
            check(res["exit_code"] == 1, "leaked secret sets exit_code=1 (err_has_key > 0)")
            check(res["summary"]["counts"].get("ok") == 1 and res["summary"]["counts"].get("upstream-error") == 1 and res["summary"]["counts"].get("rate-limited") == 2, "summary bucket counts accurate")

            csv_bytes = Path(res["csv_path"]).read_bytes()
            cand_bytes = Path(res["candidates_path"]).read_bytes()
            check(not csv_bytes.startswith(b"\xef\xbb\xbf") and not cand_bytes.startswith(b"\xef\xbb\xbf"), "CSV & candidates JSON have no UTF-8 BOM")
            check(b"MYSECRET123456" not in csv_bytes and b"MYSECRET123456" not in cand_bytes, "raw secret never written to CSV or candidates JSON")
            with Path(res["csv_path"]).open("r", encoding="utf-8", newline="") as f:
                csv_rows = list(csv.DictReader(f))
            check(len(csv_rows) == 4 and any(r["err_has_key"] == "True" and "REDACTED" in r["error"] for r in csv_rows), "CSV flags err_has_key and stores redacted error")

            # Resume should make 0 additional HTTP calls
            hits_before_resume = len(hits)
            res2 = H.run_health(
                ui_port=port, v5_port=port, v4_port=port, gap=0.0, oecd_gap=0.0, resume=True,
                out_dir=out_d, cache_dir=cache_d, catalogs=(mock_D, mock_W, False),
                health_file=td_p / "missing_health.json", run_date="2026-11-21",
            )
            check(len(hits) == hits_before_resume and res2["summary"]["total_cards"] == 4, "--resume makes 0 HTTP requests for already-done URLs")
    finally:
        srv.shutdown()
        srv.server_close()

    # ------------------------------------------------------------------ 8. launcher.py health --help CLI wiring
    hlp = subprocess.run([sys.executable, str(ROOT / "scripts" / "launcher.py"), "health", "--help"], capture_output=True, text=True, timeout=15)
    check(hlp.returncode == 0 and "--dry-run" in hlp.stdout and "--oecd-gap" in hlp.stdout and "--filter" in hlp.stdout, "launcher.py health --help lists expected flags")

    print(f"test_health: {PASSED} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
