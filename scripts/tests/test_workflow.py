"""Offline tests for developer workflow tooling: project_check, launcher subcommands, run_all filtering, temp cleanup."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from _common import ROOT, TMP_LOCAL, check, done, make_tmp_dir

import launcher
import project_check as pc

sys.path.insert(0, str(ROOT / "scripts" / "tests"))
import run_all  # noqa: E402

# ---- 1. _common.make_tmp_dir isolates scratch dirs inside TMP_LOCAL
scratch = make_tmp_dir("wf-scratch-")
check(scratch.is_dir() and scratch.parent == TMP_LOCAL, "make_tmp_dir creates dir inside TMP_LOCAL")

# ---- 2. publishable_files excludes local/, .venv*, __pycache__ and includes core publishable files
pub = {p.as_posix() for p in pc.publishable_files(ROOT)}
check("terminal/server.py" in pub and "scripts/launcher.py" in pub and "ruff.toml" in pub, "publishable manifest includes code and ruff.toml")
check(not any(p.startswith(("local/", ".venv", "__pycache__")) or "/__pycache__/" in p for p in pub), "publishable manifest excludes local/, .venv*, __pycache__")

# ---- 3. Synthetic mini-project for snapshots, diffs, EOL/BOM, and hygiene
mini = make_tmp_dir("mini-proj-")
snaps = make_tmp_dir("mini-snaps-")
(mini / "terminal").mkdir()
(mini / "scripts").mkdir()
(mini / "docs").mkdir()
(mini / "config").mkdir()
(mini / "local" / "cache").mkdir(parents=True)
(mini / ".venv-v5").mkdir()
(mini / "terminal" / "__pycache__").mkdir()


def w_lf(rel: str, text: str) -> Path:
    p = mini / rel
    p.write_bytes(text.encode("utf-8"))
    return p


def w_crlf(rel: str, text: str) -> Path:
    p = mini / rel
    p.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    return p


w_lf("terminal/app.py", "x = 1\n")
w_lf("scripts/tool.py", "y = 2\n")
w_lf("README.md", "# Demo\n")
w_crlf("Start.cmd", "@echo off\r\necho hi\r\n")
w_lf("start.sh", "#!/bin/sh\necho hi\n")
w_lf("local/cache/ignore.json", "{}\n")
w_lf(".venv-v5/ignore.py", "z = 3\n")
(mini / "terminal" / "__pycache__" / "app.pyc").write_bytes(b"\x00\x01")
(mini / "docs" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n1234")

mini_pub = {p.as_posix() for p in pc.publishable_files(mini)}
check(mini_pub == {"README.md", "Start.cmd", "docs/shot.png", "scripts/tool.py", "start.sh", "terminal/app.py"},
      f"mini publishable_files clean: {sorted(mini_pub)}")

# Initial snapshot
s1 = pc.create_snapshot(mini, snaps, today="2026-11-21")
check(s1["created"] and s1["path"].name == "project-2026-11-21.zip" and s1["files"] == 6, "initial snapshot created")

# Unchanged snapshot skips duplicate
s_dup = pc.create_snapshot(mini, snaps, today="2026-11-21")
check(not s_dup["created"] and s_dup["reason"] == "unchanged" and s_dup["path"] == s1["path"], "identical snapshot skipped")

# Diff against unmodified tree is empty
d0 = pc.diff_snapshot(mini, s1["path"])
check(not d0["added"] and not d0["deleted"] and not d0["modified"] and not d0["hunks"], "clean tree has empty diff")

# Modify text, modify binary, add file, delete file -> diff catches all with +/- counts & hunks
time.sleep(0.02)
w_lf("terminal/app.py", "x = 1\nx = 2\n")
w_lf("scripts/added.py", "a = 10\n")
(mini / "scripts" / "tool.py").unlink()
(mini / "docs" / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n123456")

d1 = pc.diff_snapshot(mini, s1["path"])
check([x["path"] for x in d1["added"]] == ["scripts/added.py"] and d1["added"][0]["ins"] == 1, "diff detects added file")
check([x["path"] for x in d1["deleted"]] == ["scripts/tool.py"] and d1["deleted"][0]["del"] == 1, "diff detects deleted file")
mod_map = {x["path"]: x for x in d1["modified"]}
check(set(mod_map) == {"docs/shot.png", "terminal/app.py"} and mod_map["terminal/app.py"]["ins"] == 1 and mod_map["docs/shot.png"]["bytes"] == 2,
      "diff detects text and binary modifications")
hunk_txt = "\n".join(d1["hunks"])
check("+x = 2" in hunk_txt and "Binary files a/docs/shot.png and b/docs/shot.png differ" in hunk_txt, "diff includes unified hunks and binary notice")
check(len(pc.diff_snapshot(mini, s1["path"], paths=["scripts"])["modified"]) == 0, "diff path filter scopes output")

# Modified snapshot gets non-clobbering -2 suffix; labelled snapshot gets -lbl suffix
s2 = pc.create_snapshot(mini, snaps, today="2026-11-21")
check(s2["created"] and s2["path"].name == "project-2026-11-21-2.zip", f"non-clobbering snapshot name: {s2['path'].name}")
time.sleep(0.02)
s_lbl = pc.create_snapshot(mini, snaps, label="pre p5!", force=True, today="2026-11-21")
check(s_lbl["created"] and s_lbl["path"].name == "project-2026-11-21-pre-p5.zip", f"labelled snapshot name: {s_lbl['path'].name}")
check(pc.latest_snapshot(snaps) == s_lbl["path"], "latest_snapshot returns newest snapshot")

# ---- 4. EOL & BOM checks
check(pc.check_eol_and_bom(mini, include_notes=False) == [], "compliant mini repo passes EOL/BOM checks")
(mini / "scripts" / "added.py").write_bytes(b"\xef\xbb\xbfa = 10\n")
w_crlf("start.sh", "#!/bin/sh\necho hi\n")
w_lf("Start.cmd", "@echo off\necho hi\n")
eol_bad = "\n".join(pc.check_eol_and_bom(mini, include_notes=False))
check("UTF-8 BOM" in eol_bad and "start.sh: expected LF" in eol_bad and "Start.cmd: expected CRLF" in eol_bad,
      f"EOL/BOM check rejects BOM, CRLF in .sh, and LF in .cmd: {eol_bad}")

# Restore compliant EOLs for hygiene checks
w_lf("scripts/added.py", "a = 10\n")
w_lf("start.sh", "#!/bin/sh\necho hi\n")
w_crlf("Start.cmd", "@echo off\r\necho hi\r\n")

# ---- 5. Hygiene / G4 checks (negative + allowlist)
w_lf(".gitignore", "." + "cla" + "ude/\n")
w_lf("terminal/news.py", 'ORGS = ("' + "Ant" + "hropic" + '", "' + "Open" + "AI" + '")\n')
check(pc.check_hygiene(mini, username="testuser99", secret_values=["MYSECRETKEY99"]) == [], "allowlisted .gitignore and news.py pass hygiene")

w_lf("terminal/bad_path.py", 'P = r"C:\\Use' + 'rs\\testuser99\\file"\n')
w_lf("terminal/bad_attr.py", "# Co-" + "Authored-By: " + "Cla" + "ude\n")
w_lf("terminal/bad_sec.py", 'K = "MYSECRETKEY99"\n')
w_lf("stray_root.txt", "oops\n")
hyg = "\n".join(pc.check_hygiene(mini, username="testuser99", secret_values=["MYSECRETKEY99"]))
check("bad_path.py:1: personal/machine path" in hyg
      and "bad_attr.py:1: attribution/tool wording" in hyg
      and "bad_sec.py:1: contains configured secret value" in hyg
      and "MYSECRETKEY99" not in hyg
      and "stray_root.txt: unexpected root entry" in hyg,
      f"hygiene catches paths, attribution, redacted secrets, and stray root entries: {hyg}")

# ---- 6. Real workspace passes project_check.run_checks cleanly (read-only)
ok_real, res_real = pc.run_checks(ROOT, live_sources=False)
failed_real = [(n, st, iss) for n, st, iss in res_real if st == "fail"]
check(ok_real and not failed_real, f"project_check.run_checks passes on real workspace: {failed_real}")

# ---- 7. launcher service target resolution & subcommand CLI wiring
check(launcher.resolve_service_targets("all") == ["ui", "v5", "v4"], "resolve all")
check(launcher.resolve_service_targets("backends") == ["v5", "v4"], "resolve backends")
check(launcher.resolve_service_targets("ui") == ["ui"], "resolve ui")
bad_target = False
try:
    launcher.resolve_service_targets("nope")
except ValueError:
    bad_target = True
check(bad_target, "resolve unknown target raises ValueError")

for sub in ("stop", "restart", "test", "check", "snapshot", "diff", "logs"):
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "launcher.py"), sub, "--help"], capture_output=True, text=True, timeout=15)
    check(r.returncode == 0, f"launcher.py {sub} --help exits 0")

# ---- 8. run_all.select_tests filtering & --fast
sample_paths = [Path("test_alpha.py"), Path("test_beta.py"), Path("test_frontend.py")]
check([p.name for p in run_all.select_tests(sample_paths, [], fast=True)] == ["test_alpha.py", "test_beta.py"], "select_tests --fast skips test_frontend")
check([p.name for p in run_all.select_tests(sample_paths, ["BETA"])] == ["test_beta.py"], "select_tests filters case-insensitively")

# ---- 9. create_release_zip packages clean Windows folder layout
import zipfile as _zf  # noqa: E402

rel_zip_dst = make_tmp_dir("rel-zip-") / pc.RELEASE_ZIP_NAME
rel_res = pc.create_release_zip(ROOT, rel_zip_dst)
check(rel_res["path"].is_file() and rel_res["bytes"] > 100_000 and len(rel_res["sha256"]) == 64, "create_release_zip builds zip and returns sha256")
with _zf.ZipFile(rel_zip_dst, "r") as zf:
    names = set(zf.namelist())
    readme_raw = zf.read("Libre-Terminal/READ-ME-FIRST.txt")
check(
    {"Libre-Terminal/Install.cmd", "Libre-Terminal/Start.cmd", "Libre-Terminal/Stop.cmd",
     "Libre-Terminal/Setup-Keys.cmd", "Libre-Terminal/READ-ME-FIRST.txt",
     "Libre-Terminal/LICENSE", "Libre-Terminal/NOTICE",
     "Libre-Terminal/terminal/server.py", "Libre-Terminal/scripts/launcher.py",
     "Libre-Terminal/config/requirements-v5.lock.txt"} <= names,
    "release zip includes Windows launchers, READ-ME-FIRST, license, runtime code, and v5 lockfile",
)
check(b"\r\n" in readme_raw and b"\n" not in readme_raw.replace(b"\r\n", b""), "READ-ME-FIRST.txt uses Windows CRLF")
check(
    not any(
        n.endswith(".sh")
        or n.startswith(("Libre-Terminal/docs/", "Libre-Terminal/scripts/tests/", "Libre-Terminal/local/", "Libre-Terminal/.venv"))
        or n in {"Libre-Terminal/README.md", "Libre-Terminal/.gitignore", "Libre-Terminal/.gitattributes", "Libre-Terminal/ruff.toml"}
        or not n.startswith("Libre-Terminal/")
        for n in names
    ),
    "release zip wraps cleanly in Libre-Terminal/ and excludes dev/macOS/test clutter",
)

done("test_workflow")
