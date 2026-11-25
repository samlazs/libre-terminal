"""The backend patch registry (scripts/run_backend.py PATCHES): unique ids, every installer registered once, version
guards, per-patch status records, critical failures surfaced on Data status and in the release gate."""

import ast
import importlib.util
import json
import subprocess
import sys

from _common import ROOT, TMP_LOCAL, check, done

spec = importlib.util.spec_from_file_location("run_backend_reg", ROOT / "scripts" / "run_backend.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

# ---- registry shape
ids = [p.id for p in rb.PATCHES]
check(len(ids) == len(set(ids)) == 55, f"55 patches with unique ids ({len(ids)})")
titles = [p.title for p in rb.PATCHES]
check(len(titles) == len(set(titles)) and all(t.strip() for t in titles), "titles are unique and non-empty")
tree = ast.parse((ROOT / "scripts" / "run_backend.py").read_text(encoding="utf-8"))
defs = sorted(n.name for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("install_"))
registered = [getattr(p.install, "_wraps", None) or p.install.__name__ for p in rb.PATCHES]
check(sorted(registered) == defs, f"every install_* function is registered exactly once ({set(defs) ^ set(registered)})")
check(all(set(p.targets) <= set(rb.VERIFIED) and p.targets for p in rb.PATCHES), "every patch names the packages it was verified against")
crit = {p.id for p in rb.PATCHES if p.critical}
check(crit == {"thread_offload", "fetch_cache", "blocking_route_offload", "yfinance_crumb_fix", "nasdaq_rate_limit_fix", "guard"},
      f"critical patches: thread offload, fetch cache, route offload, crumb fix, Nasdaq rate limit, guard ({crit})")
pos = {pid: i for i, pid in enumerate(ids)}
check(pos["fx_session_closes"] < pos["montreal_times"] and pos["tvchart_fix"] < pos["fx_chart_fix"] and ids[0] == "thread_offload",
      "install order kept where it matters")
src = (ROOT / "scripts" / "run_backend.py").read_text(encoding="utf-8")
af = src[src.index("def apply_fixes():"):src.index("def install_blocking_route_offload")]
check("_not_applied(patch.title, e)" in af and af.count("_not_applied(") == 1 and "install_" not in af.replace("_install_guard", ""),
      "apply_fixes only iterates the registry; the failure name is the registered title")

# ---- version ranges
for ver, spec_, want in (("2.0.1", ">=2.0,<2.1", True), ("2.1.0", ">=2.0,<2.1", False), ("1.9.9", ">=2.0,<2.1", False),
                         ("1.8.0", ">=1.8,<1.9", True), ("0.162.7", ">=0.162,<0.163", True), ("0.163.0", ">=0.162,<0.163", False),
                         ("3.0.0rc1", ">=3.0,<3.1", True), ("10.0", ">=9", True), ("2.0", "==2.0.0", True)):
    check(rb.version_in(ver, spec_) is want, f"version_in({ver}, {spec_}) is {want}")

# ---- apply_fixes with a stand-in registry: status records, unverified, critical failure
saved = rb.PATCHES, rb._dist_version
calls = []


def ok_patch(applied):
    calls.append("ok")
    applied.append("fix A in place")


def bad_patch(applied):
    calls.append("bad")
    raise ImportError("no module", name="openbb_gone")


try:
    rb._dist_version = lambda d: {"yfinance": "1.9.2", "openbb-core": "2.0.1"}.get(d)
    rb.PATCHES = [rb.Patch("a", "fix A", ok_patch, targets=rb._targets("openbb-core")),
                  rb.Patch("b", "fix B", bad_patch, critical=True, targets=rb._targets("openbb-core")),
                  rb.Patch("c", "fix C", ok_patch, targets=rb._targets("yfinance"))]
    rb.FAILED_PATCHES.clear()
    applied = rb.apply_fixes()
    check(calls == ["ok", "bad", "ok"] and applied == ["fix A in place", "fix A in place"], "every patch is tried, in order")
    r = {x["id"]: x for x in rb.PATCH_RESULTS}
    check(r["a"]["applied"] and r["a"]["versions"] == {"openbb-core": "2.0.1"} and "unverified" not in r["a"], "applied, inside its range")
    check(not r["b"]["applied"] and r["b"]["critical"] and "openbb_gone" in r["b"]["why"], "a failed critical patch records why")
    check(r["c"]["applied"] and r["c"]["unverified"] == "unverified for yfinance 1.9.2", "outside its range -> 'unverified for yfinance 1.9.2'")
    check(rb.FAILED_PATCHES[-1]["patch"] == "fix B", "the failure is listed under the registered title")
    rb.write_patch_status("v5", applied)
    st = json.loads((TMP_LOCAL / "run" / "patches-v5.json").read_text(encoding="utf-8"))
    check(st["critical_failed"] == ["b"] and len(st["patches"]) == 3 and st["applied"] == applied, "patches-v5.json lists every patch and the critical failures")
    check(all({"id", "title", "applied", "why", "critical", "versions"} <= set(x) for x in st["patches"]), "each record: id, title, applied, why, critical, versions")
finally:
    rb.PATCHES, rb._dist_version = saved

# ---- Data status: red banner for a critical failure, unverified rows
import composite as C  # noqa: E402
out = C.data_status()
check(out["content"].startswith("> **🔴 A critical backend fix is not applied:** fix B (Main data service (V5))"), "Data status opens with a red banner")
check(out["status"] == "partial" and out["notes"] == ["Critical backend fix not applied: fix B"], "and says so in the envelope (status + note)")
check("| Main data service (V5) | fix C | applied, unverified for yfinance 1.9.2 |" in out["content"], "unverified patches are listed")
sys.path.insert(0, str(ROOT / "scripts"))
import gate  # noqa: E402
check(gate.critical_patch_failures(TMP_LOCAL / "run") == ["v5:b"], "the release gate sees the critical failure (G2 fails)")
(TMP_LOCAL / "run" / "patches-v5.json").write_text(json.dumps({"applied": ["x"], "failed": [], "patches": [], "critical_failed": []}), encoding="utf-8")
check(gate.critical_patch_failures(TMP_LOCAL / "run") == [] and "🔴" not in C.data_status()["content"], "no banner when every critical patch applied")

# ---- the real registry on the installed .venv-v5: every patch applies, none unverified
code = ("import importlib.util,json,sys\n"
        f"spec=importlib.util.spec_from_file_location('rb',{str(ROOT / 'scripts' / 'run_backend.py')!r})\n"
        "rb=importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)\n"
        "rb.say=lambda m: None\n"
        "applied=rb.apply_fixes()\n"
        "print(json.dumps({'applied': len(applied), 'failed': [f['patch'] for f in rb.FAILED_PATCHES],"
        " 'unverified': [r['id'] for r in rb.PATCH_RESULTS if r.get('unverified')], 'n': len(rb.PATCH_RESULTS)}))\n")
r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300, cwd=ROOT)
line = next((ln for ln in reversed((r.stdout or "").splitlines()) if ln.startswith("{")), None)
res = json.loads(line) if line else {}
check(res.get("n") == 55 and res.get("failed") == [], f"all 55 patches apply on .venv-v5 ({res}; {(r.stderr or '')[-300:]})")
check(res.get("applied") == 58, f"58 applied lines (57 before the registry, plus the legend day-change fix) ({res.get('applied')})")
check(res.get("unverified") == [], f"no patch is unverified on .venv-v5 ({res.get('unverified')})")

done("test_patch_registry")
