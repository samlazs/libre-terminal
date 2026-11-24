"""Offline checks for the release gate (scripts/gate.py): G1 record and tolerance math, log scan, live G4 probes against
an in-process UI server, exit-code rule, evidence file."""

import datetime as dt
import http.server
import json
import socket
import subprocess
import sys
import threading

from _common import ROOT, TMP_LOCAL, check, done

sys.path.insert(0, str(ROOT / "scripts"))
import gate as G  # noqa: E402
import server  # noqa: E402


def fig(i, region="US", **kw):
    base = {"id": f"f{i}", "region": region, "view": "snapshot", "params": {"symbol": "AAPL"},
            "select": {"rows": "results", "match": {"label": "Price"}, "field": "value"},
            "expected": 100.0, "tol_pct": 1.0, "independent_source": {"name": "exchange page", "url": "https://example.org"}}
    base.update(kw)
    return base


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- G1 record validation
good = {"figures": [fig(i, "US" if i < 6 else ["DE", "JP", "GB", "CA"][i - 6]) for i in range(10)]}
check(G.g1_validate(good) == [], "10 figures with 4 non-US is a valid record")
check(G.g1_validate({"figures": good["figures"][1:]}) == ["9 figures (need 10)"], "9 figures is not enough")
three = {"figures": [fig(i, "US" if i < 7 else "DE") for i in range(10)]}
check(G.g1_validate(three) == ["3 non-US figures (need 4)"], "3 non-US figures is not enough")
check(G.g1_validate([]) == ["not an object with a 'figures' list"], "a list is not a record")
bad = {"figures": [fig(0, expected="100")] + good["figures"][1:]}
check(any("numeric expected" in p for p in G.g1_validate(bad)), "a string expected value is refused")
notol = {"figures": [dict(fig(0), tol_pct=None)] + good["figures"][1:]}
check(any("tol_abs or tol_pct" in p for p in G.g1_validate(notol)), "a figure without tolerance is refused")

# ---- extraction
body = {"results": [{"label": "Price", "value": 101.0}, {"label": "Market cap", "value": 3.1e12}], "as_of": "2026-01-06"}
check(G.extract(body, {"rows": "results", "match": {"label": "Market cap"}, "field": "value"}) == 3.1e12, "rows/match/field")
check(G.extract(body, {"pointer": "/results/0/value"}) == 101.0, "pointer into a list")
check(G.extract(body, {"pointer": "/results/5/value"}) is None and G.extract(body, {"pointer": "/as_of/x"}) is None, "missing pointer -> None")
check(G.extract(body, {"rows": "results", "match": {"label": "EPS"}, "field": "value"}) is None, "unmatched row -> None")

# ---- tolerance (hand-computed)
check(G.within(101.0, 100.0, {"tol_pct": 1.0})[0], "101 vs 100 is 1% -> within 1%")
check(not G.within(101.5, 100.0, {"tol_pct": 1.0})[0], "101.5 vs 100 is 1.5% -> outside 1%")
check(G.within(4.21, 4.18, {"tol_abs": 0.05})[0] and not G.within(4.25, 4.18, {"tol_abs": 0.05})[0], "absolute tolerance 0.05 (0.03 in, 0.07 out)")
check(not G.within(-3.0, 3.0, {"tol_pct": 50})[0], "a sign flip is outside a 50% band")
check(not G.within("101", 100.0, {"tol_pct": 1})[0] and not G.within(True, 1.0, {"tol_abs": 0})[0] and not G.within(None, 1.0, {"tol_abs": 1})[0],
      "strings, booleans and None never pass")

# ---- G1 live, against an in-process stand-in for the UI server
answers = {"AAPL": 101.0, "SAP.DE": 204.0}


class FakeUI(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        import urllib.parse as up
        q = dict(up.parse_qsl(self.path.split("?", 1)[1] if "?" in self.path else ""))
        sym = q.get("symbol", "")
        if sym not in answers:
            raw, code = json.dumps({"detail": "not found"}).encode(), 502
        else:
            raw, code = json.dumps({"results": [{"label": "Price", "value": answers[sym]}], "sources": [{"name": "x"}]}).encode(), 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)


fport = free_port()
fake = http.server.ThreadingHTTPServer(("127.0.0.1", fport), FakeUI)
threading.Thread(target=fake.serve_forever, daemon=True).start()
ctx = G.Ctx(False, fport, 1)
notes = TMP_LOCAL / "notes"
notes.mkdir(parents=True, exist_ok=True)
rec_path = notes / "g1-spotcheck.json"
check(G.gate_g1(ctx) == ("fail", ["local/notes/g1-spotcheck.json is missing (manifest P3-07 records it)"]), "G1 without a record fails")
figs = [fig(i) for i in range(6)] + [fig(i, "DE", params={"symbol": "SAP.DE"}, expected=204.0, tol_abs=0.5, tol_pct=None) for i in range(6, 10)]
rec_path.write_text(json.dumps({"figures": figs}), encoding="utf-8")
st, ev = G.gate_g1(ctx)
check(st == "pass" and ev[0] == "10/10 figures within tolerance", f"G1 passes when every figure is within tolerance ({ev[:2]})")
answers["SAP.DE"] = 205.0            # 1.0 away with tol_abs 0.5
st, ev = G.gate_g1(ctx)
check(st == "fail" and ev[0] == "6/10 figures within tolerance" and any(e.startswith("BAD f6") for e in ev), "G1 fails on a drifted figure")
del answers["SAP.DE"]
st, ev = G.gate_g1(ctx)
check(st == "fail" and any("HTTP 502" in e for e in ev), "G1 fails when the view errors")
st, ev = G.gate_g1(G.Ctx(True, fport, 1))
check(st == "manual" and "10 figures" in ev[0], "G1 offline only validates the record (manual)")
fake.shutdown()

# ---- log scan uses the redaction detector
logs = TMP_LOCAL / "logs"
logs.mkdir(parents=True, exist_ok=True)
(logs / "ui.log").write_text("[x] started\n[x] GET /x/board 200\n", encoding="utf-8")
check(G.scan_logs(logs) == [], "a clean log has no key material")
(logs / "v5.log").write_text("ok\nGET https://api.example.org/v1?apikey=Zq8xL0pT4kR2mN7vB9cW1yE6\n", encoding="utf-8")
check(G.scan_logs(logs) == ["v5.log:2"], f"a key in a URL is found with its line ({G.scan_logs(logs)})")
check(G.scan_logs(TMP_LOCAL / "nope") == [], "no logs folder -> nothing found")

# ---- live G4 probes against the real UI server handler
uport = free_port()
srv = server.ThreadingHTTPServer(("127.0.0.1", uport), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
res = G.live_g4_probes(f"http://127.0.0.1:{uport}", f"127.0.0.1:{uport}")
check(len(res) == 4 and all(ok for ok, _ in res), f"real UI server passes the G4 probes: {[w for ok, w in res if not ok]}")
srv.shutdown()


class Leaky(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


lport = free_port()
leaky = http.server.ThreadingHTTPServer(("127.0.0.1", lport), Leaky)
threading.Thread(target=leaky.serve_forever, daemon=True).start()
res = G.live_g4_probes(f"http://127.0.0.1:{lport}", f"127.0.0.1:{lport}")
check(not any(ok for ok, _ in res), "a server without Host/Origin/CSP guards fails every G4 probe")
leaky.shutdown()

# ---- exit-code rule and gate selection
check(G.exit_code({"G1": {"status": "pass"}, "G2": {"status": "manual"}}, True) == 0, "pass + manual -> 0")
check(G.exit_code({"G1": {"status": "pass"}, "G3": {"status": "fail"}}, True) == 1, "any fail -> 1")
check(G.exit_code({"G1": {"status": "pass"}}, False) == 1, "a changed state.json -> 1")
check(G.parse_only("g1, G4") == ["G1", "G4"] and G.parse_only(None) == list(G.GATES), "--only parsing")
try:
    G.parse_only("G6")
    check(False, "G6 must be refused")
except SystemExit as e:
    check("owner" in str(e), "G6 is never automated")

# ---- the evidence file (offline run: G1 manual with the record above, G4 hygiene + log scan)
(logs / "v5.log").write_text("clean\n", encoding="utf-8")
code = G.main(["--offline", "--only", "G1,G5"])
out = json.loads((notes / f"gates-{dt.date.today().isoformat()}.json").read_text(encoding="utf-8"))
check(out["offline"] is True and bool(out["version"]) and (len(out["commit"]) == 40 or out["commit"] == "unknown"), "record has commit and version")
check(set(out["gates"]) == {"G1", "G5"} and out["gates"]["G1"]["status"] == "manual", "only the selected gates are recorded")
check(all(set(g) == {"status", "evidence", "started", "finished"} for g in out["gates"].values()), "each gate has status/evidence/started/finished")
check(out["gates"]["G5"]["status"] == "manual" and out["state_json_unchanged"] is True and code == 0, "offline G5 is manual; exit 0 without a fail")
rec_path.unlink()
code = G.main(["--offline", "--only", "G1"])
out = json.loads((notes / f"gates-{dt.date.today().isoformat()}.json").read_text(encoding="utf-8"))
check(code == 1 and out["gates"]["G1"]["status"] == "fail" and out["gates"]["G5"]["status"] == "manual", "a failed gate -> exit 1; earlier gates kept")

# ---- launcher wiring
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "launcher.py"), "gate", "--help"], capture_output=True, text=True, timeout=60)
check(r.returncode == 0 and "--only" in r.stdout and "--offline" in r.stdout, "launcher.py gate --help")

done("test_gate")
