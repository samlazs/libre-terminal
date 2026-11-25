"""Offline checks for the release gate (scripts/gate.py): G1 record and tolerance math, log scan, live G4 probes against
an in-process UI server, exit-code rule, evidence file."""

import datetime as dt
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from _common import ROOT, TMP_LOCAL, check, done, make_tmp_dir

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
chart = {"data": [{"name": "United States 4.10%", "x": ["2026-09-01", "2026-10-01"], "y": [4.2, 4.1]},
                  {"name": "Germany 2.65%", "x": ["2026-09-01", "2026-10-01T00:00:00"], "y": [2.7, 2.65]}]}
check(G.extract(chart, {"trace": "germany", "x": "2026-10-01"}) == 2.65, "a chart point pinned by trace name and date")
check(G.extract(chart, {"trace": 0, "x": "2026-09"}) == 4.2, "a chart point pinned by trace index and month")
check(G.extract({"data": [{"type": "candlestick", "x": ["2026-11-24"], "close": [1.0968]}]}, {"x": "2026-11-24", "y": "close"}) == 1.0968, "a candlestick close by date")
check(G.extract(chart, {"trace": "japan", "x": "2026-10-01"}) is None and G.extract(chart, {"trace": 0, "x": "2025-01-01"}) is None, "no such trace or date: nothing")
check(G.g1_validate({"figures": [dict(f, select={"trace": 0, "x": "2026-10-01"}) for f in good["figures"]]}) == [], "select.x is a valid selector")
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
STAMP = {"commit", "version", "offline", "uncommitted"}
check(all(set(g) == {"status", "evidence", "started", "finished"} | STAMP for g in out["gates"].values()),
      "each gate has status/evidence/started/finished and the commit, version, mode and uncommitted count it ran on")
check(all(g["commit"] == out["commit"] and g["offline"] is True for g in out["gates"].values()), "this run's gates carry this run's commit")
check(out["gates"]["G5"]["status"] == "manual" and out["state_json_unchanged"] is True and code == 0, "offline G5 is manual; exit 0 without a fail")
rec_path.unlink()
day_file = notes / f"gates-{dt.date.today().isoformat()}.json"
earlier = json.loads(day_file.read_text(encoding="utf-8"))
earlier["gates"]["G5"].update(commit="a" * 40, version="0.8.0", offline=False)     # as if G5 ran earlier on another commit
day_file.write_text(json.dumps(earlier), encoding="utf-8")
code = G.main(["--offline", "--only", "G1"])
out = json.loads(day_file.read_text(encoding="utf-8"))
check(code == 1 and out["gates"]["G1"]["status"] == "fail" and out["gates"]["G5"]["status"] == "manual", "a failed gate -> exit 1; earlier gates kept")
check(out["gates"]["G5"]["commit"] == "a" * 40 and out["gates"]["G5"]["version"] == "0.8.0" and out["gates"]["G5"]["offline"] is False
      and out["gates"]["G1"]["commit"] == out["commit"], "a gate kept from an earlier run keeps its own commit and version, never restamped")

# ---- G2 cold start (owner decision OD-2): 90 s, and the first card's first answer must be a full 200
check(G.COLD_START_LIMIT_S == 90.0, "cold-start limit is 90 s (OD-2)")
check(G.first_view_verdict(200, {"status": "ok", "results": [1]}) == (True, "first /x/board answer complete (200, status ok)"), "200 + status ok: complete")
for st_, body_, why_ in ((200, {"status": "partial", "notes": ["Partial answer: no data from Yahoo"]}, "200 but partial (Partial answer: no data from Yahoo)"),
                         (200, {"status": "fallback"}, "200 but fallback"), (200, {"results": []}, "200 but without a status"),
                         (503, {"error": {"kind": "unavailable"}}, "was 503, not 200"), ("TimeoutError", None, "was TimeoutError, not 200"),
                         (200, "<html>", "not a JSON object")):
    ok_v, why_v = G.first_view_verdict(st_, body_)
    check(not ok_v and why_ in why_v, f"first answer {st_} {str(body_)[:30]}: fails ({why_v})")

# ---- G5: the zip tested must be this version and commit, built from a clean tree
SHA = "b" * 40
good = {"version": "1.0.0", "commit": SHA, "uncommitted_files": [], "built": "2026-11-25T18:00:00+01:00"}
ok_z, why_z = G.zip_build_verdict(good, "1.0.0", SHA)
check(ok_z and why_z == "zip built from version 1.0.0, commit bbbbbbbbbbbb at 2026-11-25T18:00:00+01:00; matches this version and commit", f"matching zip ({why_z})")
for info_, want_ in ((dict(good, version="0.8.0"), "not this version (1.0.0)"), (dict(good, commit="c" * 40), "not this commit (bbbbbbbbbbbb)"),
                     (dict(good, uncommitted_files=["terminal/server.py"]), "1 packed file(s) differ from the commit (terminal/server.py)"),
                     (dict(good, uncommitted_files=None), "is unknown"), (None, "no BUILD-INFO.json")):
    ok_z, why_z = G.zip_build_verdict(info_, "1.0.0", SHA)
    check(not ok_z and want_ in why_z, f"zip refused: {want_}")
zdir = make_tmp_dir("gate-zip-")
with zipfile.ZipFile(zdir / "z.zip", "w") as zf:
    zf.writestr("Libre-Terminal/BUILD-INFO.json", json.dumps(good))
    zf.writestr("Libre-Terminal/scripts/BUILD-INFO.json", "{}")
check(G.zip_build_info(zdir / "z.zip") == good, "BUILD-INFO.json read from the zip's top folder")

# ---- scratch cleanup: a read-only file does not stop it; '' when the folder is gone
rt = make_tmp_dir("gate-rm-") / "scratch"
(rt / "sub").mkdir(parents=True)
(rt / "sub" / "ro.pyd").write_bytes(b"x")
os.chmod(rt / "sub" / "ro.pyd", 0o400)
# the process lookup and the kill are stubbed: the test never runs taskkill (review R1)
saved_sp, saved_kill = G.scratch_processes, G.kill_tree
killed_ = []
try:
    G.kill_tree = killed_.append
    G.scratch_processes = lambda p: []
    check(G.remove_tree(rt, tries=2) == "" and not rt.exists() and killed_ == [], "remove_tree clears read-only files and reports nothing left")
    rt.mkdir()
    G.scratch_processes = lambda p: [(4242, "python.exe")]
    check(G.remove_tree(rt, tries=1) == "" and killed_ == [4242], "remove_tree stops a process running from the folder first")
finally:
    G.scratch_processes, G.kill_tree = saved_sp, saved_kill

# ---- review R1/R8: scratch_processes finds the processes running from the folder, never its own query; a folder name with a
# typographic apostrophe (a PowerShell quote) and a non-ASCII letter is matched, not dropped
if os.name == "nt":
    sd = make_tmp_dir("gate-sp-") / "Zoë’s scratch"
    sd.mkdir()
    check(G.scratch_processes(sd) == [], f"an empty scratch folder: no process runs from it ({G.scratch_processes(sd)})")
    exe = sd / "pinger.exe"
    exe.write_bytes((Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE").read_bytes())
    base_py = getattr(sys, "_base_executable", None) or sys.executable
    p_exe = subprocess.Popen([str(exe), "-n", "30", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p_arg = subprocess.Popen([base_py, "-c", "import time; time.sleep(30)", str(sd / "marker.txt")])
    try:
        time.sleep(0.5)
        got_sp = G.scratch_processes(sd)
        check(sorted(p for p, _ in got_sp) == sorted([p_exe.pid, p_arg.pid]),
              f"exactly the two processes run from it: by executable and by command line ({got_sp}; {p_exe.pid}, {p_arg.pid})")
        sib = sd.parent / (sd.name + "x")              # a sibling whose name only starts with the folder's name
        check(G.scratch_processes(sib) == [], "a folder named like the start of another is not matched")
    finally:
        for p_ in (p_exe, p_arg):
            p_.kill()
            p_.wait(10)

# ---- review R2: uncommitted() counts every changed tracked file, not the last 8 lines of git's output
repo = make_tmp_dir("gate-git-")
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "core.autocrlf=false"]
subprocess.run(GIT + ["init", "-q"], cwd=repo, check=True)
for i in range(20):
    (repo / f"f{i:02d}.txt").write_text("a\n", encoding="utf-8")
(repo / "new.txt").write_text("untracked\n", encoding="utf-8")
subprocess.run(GIT + ["add", "--", *[f"f{i:02d}.txt" for i in range(20)]], cwd=repo, check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "base"], cwd=repo, check=True)
for i in range(17):
    (repo / f"f{i:02d}.txt").write_text("b\n", encoding="utf-8")
check(G.uncommitted(cwd=repo) == 17, f"17 changed tracked files -> 17 (the untracked file is not counted) ({G.uncommitted(cwd=repo)})")
check(G.uncommitted(cwd=make_tmp_dir("gate-nogit-")) is None, "not a git folder -> None (unknown), never 0")

# ---- G2 soak: memory read from tasklist, skipped with --soak-minutes 0
check(abs(G.parse_tasklist_kb('"python.exe","1234","Console","1","123,456 K"') - 120.5625) < 1e-6, "tasklist '123,456 K' -> 120.56 MB")
check(G.parse_tasklist_kb("INFO: No tasks are running which match the specified criteria.") is None, "no process -> None")
check(G.ui_pid() is None, "no ui.json -> no pid")
ok_, why_ = G.soak(G.Ctx(False, 1, 1, soak_minutes=0.01))
check(not ok_ and "could not be measured" in why_, "a soak without a UI process fails instead of passing")
import os as _os  # noqa: E402
rss = G.process_rss_mb(_os.getpid())
check(rss is not None and rss > 5, f"this process's RSS is readable ({rss})")
# the UI is measured as a process tree: launcher stub 10 (4 MB) -> interpreter 11 (200 MB) -> helper 12 (6 MB);
# 20 is another process and 13 a child of it
MB_ = 1024 * 1024
rows_ = [(10, 1, 4 * MB_), (11, 10, 200 * MB_), (12, 11, 6 * MB_), (20, 1, 50 * MB_), (13, 20, 9 * MB_)]
check(G.tree_rss_mb(rows_, 10) == 210.0, "tree RSS: 4 + 200 + 6 MB under the recorded PID")
check(G.tree_rss_mb(rows_, 11) == 206.0 and G.tree_rss_mb(rows_, 99) is None, "a subtree; a PID that is gone -> None")
check(G.tree_rss_mb([(5, 6, MB_), (6, 5, MB_)], 5) == 2.0, "a parent-PID cycle (PID reuse) is counted once")
tree = G.process_tree_rss_mb(_os.getpid())
check(tree is not None and tree >= rss * 0.5, f"this process's tree RSS is readable ({tree})")

# ---- launcher wiring
r = subprocess.run([sys.executable, str(ROOT / "scripts" / "launcher.py"), "gate", "--help"], capture_output=True, text=True, timeout=60)
check(r.returncode == 0 and "--only" in r.stdout and "--offline" in r.stdout, "launcher.py gate --help")

done("test_gate")
