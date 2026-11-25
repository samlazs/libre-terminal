"""Project snapshots, snapshot diffs, and pre-flight / release-hygiene checks (stdlib only).

    python scripts/project_check.py check [--live-sources]
    python scripts/project_check.py snapshot [--label NAME] [--force]
    python scripts/project_check.py diff [SNAPSHOT] [--stat] [PATH ...]
    python scripts/project_check.py release-zip [-o PATH]
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import difflib
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
import local_paths  # noqa: E402
import redact  # noqa: E402
from version import VERSION  # noqa: E402

PUBLISHABLE_DIRS = ("terminal", "scripts", "config", "docs", ".github")
PUBLISHABLE_ROOT_FILES = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "ruff.toml",
    ".gitignore",
    ".gitattributes",
    "Install.cmd",
    "Start.cmd",
    "Stop.cmd",
    "Setup-Keys.cmd",
    "install.sh",
    "start.sh",
    "stop.sh",
    "setup-keys.sh",
)
EXPECTED_IGNORED_ROOT = {
    "local",
    ".venv-v4",
    ".venv-v5",
    ".git",
    ".mcp.json",
    "." + "cla" + "ude",
    "__pycache__",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    ".vscode",
    ".idea",
}
SKIP_DIR_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
CRLF_EXTS = {".cmd", ".bat", ".ps1"}
LF_EXTS = {".py", ".js", ".css", ".html", ".json", ".md", ".sh", ".toml", ".in", ".env", ".txt"}
TEXT_EXTS = CRLF_EXTS | LF_EXTS

RELEASE_ZIP_NAME = "Libre-Terminal-Windows.zip"
RELEASE_ZIP_TOP_DIR = "Libre-Terminal"
WINDOWS_RELEASE_EXCLUDE_FILES = {
    "install.sh",
    "start.sh",
    "stop.sh",
    "setup-keys.sh",
    ".gitignore",
    ".gitattributes",
    "ruff.toml",
    "README.md",
}
WINDOWS_RELEASE_EXCLUDE_PREFIXES = ("docs/", "scripts/tests/", ".github/")
WINDOWS_README_TEXT = f"""\
Libre Terminal v{VERSION} - Windows Quick Guide
===========================================

You need Windows 10 or 11, an internet connection and about 1 GB of free space.
No Python, coding tools, or accounts are required.

1. EXTRACT FIRST
   If you are looking at this file inside the ZIP window, close it first:
   right-click Libre-Terminal-Windows.zip, choose "Extract All...", click Extract,
   and open the extracted Libre-Terminal folder.
   Tip: a short folder such as C:\\Libre-Terminal works best (not OneDrive).

2. INSTALL (once, 2 to 5 minutes)
   Double-click  Install.cmd
   - If Windows shows "Windows protected your PC", click "More info", then "Run anyway".
   - A black window opens and shows download progress. Leave it open.
   - Done when it says "Install finished". Close that window.

3. START
   Double-click  Start.cmd
   - The first start takes about a minute. Then your browser opens the terminal
     at http://127.0.0.1:6950 (you can bookmark it).
   - The black window can be closed; the terminal keeps running in the background.
   - After restarting your computer, double-click Start.cmd again.

4. STOP
   Double-click  Stop.cmd  when you are finished.

5. OPTIONAL FREE API KEYS
   Double-click  Setup-Keys.cmd  to add free keys (FRED, EIA, BLS, Congress.gov).
   Most dashboards work without any keys.

IF SOMETHING GOES WRONG
   - Read the red or yellow lines in the black window: they say what to do.
   - Install stops partway: usually a company proxy, firewall, VPN or antivirus.
     Try another network, then double-click Install.cmd again (it carries on).
   - Blank cards on the first start: wait a minute, then click Refresh in the top bar.
   - Logs are in the local\\logs folder inside Libre-Terminal.
   - More help: https://github.com/samlazs/libre-terminal#updating-and-troubleshooting

UPDATING FROM AN EARLIER VERSION
   Double-click Stop.cmd in the old folder. To keep your saved layouts and API keys,
   copy the "local" folder from the old folder into this new folder, then
   double-click Install.cmd and Start.cmd.

UNINSTALL
   Double-click Stop.cmd, then delete the Libre-Terminal folder.

Project page: https://github.com/samlazs/libre-terminal
"""

_AI_TERMS = (
    "cla" + "ude",
    "ant" + "hropic",
    "chat" + "gpt",
    "open" + "ai",
    "gem" + "ini",
    "cop" + "ilot",
)
_AI_ATTR_RX = re.compile(
    r"(?i)\b(?:co-" + r"authored-by|generated\s+by\s+(?:ai|llm)|written\s+by\s+(?:ai|llm)|ai[- ](?:generated|assisted|authored)|"
    + "|".join(_AI_TERMS)
    + r")\b"
)
_PATH_RX = re.compile(r"(?i)(?:C:\\Use" + r"rs\\|/Use" + r"rs/|%TEMP%\\cla" + r"ude)")


def publishable_files(root: Path = ROOT) -> list[Path]:
    """Return sorted relative paths of every publishable file under root."""
    out: list[Path] = []
    for d in PUBLISHABLE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if any(part in SKIP_DIR_PARTS for part in rel.parts) or p.suffix in (".pyc", ".pyo", ".pyd"):
                continue
            out.append(rel)
    for name in PUBLISHABLE_ROOT_FILES:
        p = root / name
        if p.is_file():
            out.append(Path(name))
    return sorted(set(out), key=lambda x: x.as_posix())


def _file_hashes(root: Path, rels: list[Path]) -> dict[str, str]:
    return {rel.as_posix(): hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in rels}


def _zip_hashes(snap_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with zipfile.ZipFile(snap_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            out[info.filename] = hashlib.sha256(zf.read(info.filename)).hexdigest()
    return out


def list_snapshots(snap_dir: Path | None = None) -> list[Path]:
    snap_dir = snap_dir or (local_paths.LOCAL / "snapshots")
    if not snap_dir.is_dir():
        return []
    snaps = [p for p in snap_dir.glob("project-*.zip") if p.is_file()]
    return sorted(snaps, key=lambda p: (p.stat().st_mtime_ns, p.name))


def latest_snapshot(snap_dir: Path | None = None) -> Path | None:
    snaps = list_snapshots(snap_dir)
    return snaps[-1] if snaps else None


def _next_snapshot_path(snap_dir: Path, date_str: str, label: str = "") -> Path:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", (label or "").strip()).strip("-")
    stem = f"project-{date_str}" + (f"-{clean}" if clean else "")
    cand = snap_dir / f"{stem}.zip"
    if not cand.exists():
        return cand
    idx = 2
    while True:
        cand = snap_dir / f"{stem}-{idx}.zip"
        if not cand.exists():
            return cand
        idx += 1


def create_snapshot(
    root: Path = ROOT,
    snap_dir: Path | None = None,
    label: str = "",
    force: bool = False,
    today: str | None = None,
) -> dict:
    """Create a zip snapshot of publishable files; skips writing a duplicate if identical to latest."""
    snap_dir = snap_dir or (local_paths.LOCAL / "snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    rels = publishable_files(root)
    cur_hashes = _file_hashes(root, rels)

    prev = latest_snapshot(snap_dir)
    if prev is not None and not force:
        try:
            if _zip_hashes(prev) == cur_hashes:
                return {"created": False, "path": prev, "files": len(rels), "reason": "unchanged"}
        except (OSError, zipfile.BadZipFile):
            pass

    date_str = today or dt.date.today().isoformat()
    dst = _next_snapshot_path(snap_dir, date_str, label)
    tmp = dst.with_name(f"{dst.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for rel in rels:
                zf.write(root / rel, rel.as_posix())
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return {"created": True, "path": dst, "files": len(rels), "bytes": dst.stat().st_size}


RELEASE_BUILD_FILE = "BUILD-INFO.json"      # inside the zip's top folder: what the zip was built from (gate G5 reads it)


def build_info(root: Path, rels: list) -> dict:
    """The version and git commit a release zip is built from, and the packed files that differ from that commit
    (the zip is built from the working folder, so a dirty tree is not the commit; None when git cannot tell).
    A packed file is the commit's only when git tracks it and it does not differ from HEAD: an ignored or untracked file
    the folder walk packed is not. -z lists paths as UTF-8, unquoted (a non-ASCII name is not "\\303\\251" octal)."""
    def git(*args):
        try:
            r = subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    sha = (git("rev-parse", "HEAD") or "").strip() or "unknown"
    changed = git("diff", "-z", "--name-only", "--no-renames", "HEAD")
    tracked = git("ls-files", "-z")
    packed = {r.as_posix() for r in rels}
    if changed is None or tracked is None or sha == "unknown":
        dirty = None
    else:
        changed_set, tracked_set = set(changed.split("\0")) - {""}, set(tracked.split("\0")) - {""}
        dirty = sorted(p for p in packed if p in changed_set or p not in tracked_set)
    return {"version": VERSION, "commit": sha, "uncommitted_files": dirty,
            "built": dt.datetime.now().astimezone().isoformat(timespec="seconds")}


def create_release_zip(root: Path = ROOT, dst: Path | None = None) -> dict:
    """Package a clean, Windows-focused distribution zip wrapped in Libre-Terminal/."""
    dst = dst or (local_paths.LOCAL / "releases" / RELEASE_ZIP_NAME)
    dst.parent.mkdir(parents=True, exist_ok=True)
    rels = [
        rel
        for rel in publishable_files(root)
        if rel.as_posix() not in WINDOWS_RELEASE_EXCLUDE_FILES
        and not rel.as_posix().startswith(WINDOWS_RELEASE_EXCLUDE_PREFIXES)
    ]
    tmp = dst.with_name(f"{dst.name}.{os.getpid()}.tmp")
    readme_bytes = WINDOWS_README_TEXT.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")
    info = build_info(root, rels)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{RELEASE_ZIP_TOP_DIR}/READ-ME-FIRST.txt", readme_bytes)
            zf.writestr(f"{RELEASE_ZIP_TOP_DIR}/{RELEASE_BUILD_FILE}", json.dumps(info, indent=2) + "\n")
            for rel in rels:
                zf.write(root / rel, f"{RELEASE_ZIP_TOP_DIR}/{rel.as_posix()}")
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    sha256 = hashlib.sha256(dst.read_bytes()).hexdigest()
    return {"path": dst, "files": len(rels) + 2, "bytes": dst.stat().st_size, "sha256": sha256, "build": info}


def _matches_filter(rel_str: str, filters: list[str] | None) -> bool:
    if not filters:
        return True
    norm = [f.replace("\\", "/").strip("/") for f in filters if f.strip()]
    return any(rel_str == f or rel_str.startswith(f + "/") or f in rel_str for f in norm)


def diff_snapshot(
    root: Path = ROOT,
    snap_path: Path | None = None,
    paths: list[str] | None = None,
    stat_only: bool = False,
) -> dict:
    """Compare publishable files on disk against a snapshot zip."""
    snap_path = snap_path or latest_snapshot()
    if snap_path is None or not snap_path.exists():
        raise FileNotFoundError("No snapshot zip found; run `launcher.py snapshot` first.")

    rels = publishable_files(root)
    cur_map = {r.as_posix(): (root / r).read_bytes() for r in rels if _matches_filter(r.as_posix(), paths)}
    snap_map: dict[str, bytes] = {}
    with zipfile.ZipFile(snap_path, "r") as zf:
        for info in zf.infolist():
            if not info.is_dir() and _matches_filter(info.filename, paths):
                snap_map[info.filename] = zf.read(info.filename)

    all_keys = sorted(set(cur_map) | set(snap_map))
    added, deleted, modified, hunks = [], [], [], []
    for k in all_keys:
        in_cur, in_snap = k in cur_map, k in snap_map
        if in_cur and not in_snap:
            raw = cur_map[k]
            lines = len(raw.decode("utf-8", errors="replace").splitlines()) if Path(k).suffix in TEXT_EXTS else 0
            added.append({"path": k, "ins": lines, "del": 0, "bytes": len(raw)})
            if not stat_only and Path(k).suffix in TEXT_EXTS:
                hunks.extend(
                    difflib.unified_diff([], raw.decode("utf-8", errors="replace").splitlines(), fromfile="/dev/null", tofile="b/" + k, lineterm="")
                )
        elif in_snap and not in_cur:
            raw = snap_map[k]
            lines = len(raw.decode("utf-8", errors="replace").splitlines()) if Path(k).suffix in TEXT_EXTS else 0
            deleted.append({"path": k, "ins": 0, "del": lines, "bytes": len(raw)})
            if not stat_only and Path(k).suffix in TEXT_EXTS:
                hunks.extend(
                    difflib.unified_diff(raw.decode("utf-8", errors="replace").splitlines(), [], fromfile="a/" + k, tofile="/dev/null", lineterm="")
                )
        else:
            b_old, b_new = snap_map[k], cur_map[k]
            if b_old == b_new:
                continue
            if Path(k).suffix in TEXT_EXTS:
                l_old = b_old.decode("utf-8", errors="replace").splitlines()
                l_new = b_new.decode("utf-8", errors="replace").splitlines()
                diff_lines = list(difflib.unified_diff(l_old, l_new, fromfile="a/" + k, tofile="b/" + k, lineterm=""))
                ins = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
                dels = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
                modified.append({"path": k, "ins": ins, "del": dels, "bytes": len(b_new) - len(b_old)})
                if not stat_only:
                    hunks.extend(diff_lines)
            else:
                modified.append({"path": k, "ins": 0, "del": 0, "bytes": len(b_new) - len(b_old)})
                if not stat_only:
                    hunks.append(f"Binary files a/{k} and b/{k} differ ({len(b_old)} -> {len(b_new)} bytes)")
    return {"snapshot": snap_path, "added": added, "deleted": deleted, "modified": modified, "hunks": hunks}


def check_eol_and_bom(root: Path = ROOT, include_notes: bool = True) -> list[str]:
    """Verify UTF-8 without BOM and CRLF/LF rules matching .gitattributes."""
    issues: list[str] = []
    targets: list[tuple[Path, str]] = [(root / r, r.as_posix()) for r in publishable_files(root) if r.suffix in TEXT_EXTS]
    if include_notes:
        notes_dir = root / "local" / "notes"
        if notes_dir.is_dir():
            for p in sorted(notes_dir.glob("*.md")):
                targets.append((p, p.relative_to(root).as_posix()))
            plog = notes_dir / "archive" / "progress-log.md"
            if plog.is_file():
                targets.append((plog, plog.relative_to(root).as_posix()))
    for p, label in targets:
        raw = p.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            issues.append(f"{label}: contains UTF-8 BOM")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            issues.append(f"{label}: invalid UTF-8 ({e})")
            continue
        if p.suffix in CRLF_EXTS:
            bare_lf = len(re.findall(rb"(?<!\r)\n", raw))
            if bare_lf:
                issues.append(f"{label}: expected CRLF, found {bare_lf} bare LF line(s)")
        elif p.suffix in LF_EXTS:
            crlf = raw.count(b"\r\n")
            if crlf:
                issues.append(f"{label}: expected LF, found {crlf} CRLF line(s)")
    return issues


def check_syntax(root: Path = ROOT) -> list[str]:
    """Parse every publishable .py (ast.parse) and .json (json.loads) file."""
    issues: list[str] = []
    for rel in publishable_files(root):
        p = root / rel
        if rel.suffix == ".py":
            try:
                ast.parse(p.read_text(encoding="utf-8"), filename=rel.as_posix())
            except SyntaxError as e:
                issues.append(f"{rel.as_posix()}:{e.lineno}: SyntaxError: {e.msg}")
        elif rel.suffix == ".json":
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except ValueError as e:
                issues.append(f"{rel.as_posix()}: invalid JSON ({e})")
    return issues


def _find_ruff(root: Path = ROOT) -> Path | None:
    exe = "ruff.exe" if os.name == "nt" else "ruff"
    cands = [
        Path(sys.executable).resolve().parent / exe,
        root / ".venv-v5" / ("Scripts" if os.name == "nt" else "bin") / exe,
    ]
    for c in cands:
        if c.is_file():
            return c
    found = shutil.which("ruff")
    return Path(found) if found else None


def check_ruff(root: Path = ROOT) -> tuple[str, list[str]]:
    ruff = _find_ruff(root)
    cfg = root / "ruff.toml"
    if not ruff or not cfg.is_file():
        return "skipped", []
    r = subprocess.run(
        [str(ruff), "check", "--config", str(cfg), "--output-format", "concise", "terminal", "scripts"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if r.returncode == 0:
        return "ok", []
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip() and not ln.startswith("Found ")]
    return "fail", lines or [(r.stderr or "ruff failed").strip()]


def _allow_ai_line(rel_str: str, line: str) -> bool:
    if rel_str == ".gitignore" and ("." + "cla" + "ude/") in line:
        return True
    if rel_str == "terminal/news.py" and ('"' + "Ant" + "hropic" + '"') in line and ('"' + "Open" + "AI" + '"') in line:
        return True
    return False


def check_hygiene(
    root: Path = ROOT,
    username: str | None = None,
    secret_values: list[str] | None = None,
) -> list[str]:
    """Scan publishable files for personal paths, AI-authorship wording, configured secrets, and stray root entries."""
    issues: list[str] = []
    user = getpass.getuser() if username is None else username
    user_rx = re.compile(rf"(?i)(?:\\|/){re.escape(user)}(?:\\|/|$)") if user and len(user) >= 3 else None
    secrets = redact._key_values() if secret_values is None else [s for s in secret_values if s and len(s) >= 8]

    for rel in publishable_files(root):
        if rel.suffix not in TEXT_EXTS:
            continue
        rel_str = rel.as_posix()
        txt = (root / rel).read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(txt.splitlines(), 1):
            if _PATH_RX.search(line) or (user_rx and user_rx.search(line)):
                issues.append(f"{rel_str}:{i}: personal/machine path")
            if _AI_ATTR_RX.search(line) and not _allow_ai_line(rel_str, line):
                issues.append(f"{rel_str}:{i}: attribution/tool wording")
            for sec in secrets:
                if sec in line:
                    issues.append(f"{rel_str}:{i}: contains configured secret value")
                    break

    allowed_root = set(PUBLISHABLE_DIRS) | set(PUBLISHABLE_ROOT_FILES) | EXPECTED_IGNORED_ROOT
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if entry.name not in allowed_root:
            issues.append(f"{entry.name}: unexpected root entry (not in publishable manifest or .gitignore)")
    return issues


def check_sources_offline(root: Path = ROOT) -> tuple[str, list[str]]:
    dash_file = local_paths.DASHBOARDS_FILE
    cat_v5 = local_paths.CACHE_DIR / "catalog-v5.json"
    if not dash_file.is_file() or not cat_v5.is_file():
        return "skipped", []
    import check_sources as cs  # noqa: PLC0415

    defs = cs.load_catalogs(offline=True)
    built = json.loads(dash_file.read_text(encoding="utf-8"))
    state = json.loads(local_paths.STATE_FILE.read_text(encoding="utf-8")) if local_paths.STATE_FILE.is_file() else {}
    res = cs.check_all(defs, built, state, root=root)
    issues = [f"NO SOURCE {b}" for b in res["bad"]] + [f"'Source shown' in {p}" for p in res["code_hits"]]
    return ("fail" if issues else "ok"), issues


def check_owner_state() -> tuple[str, list[str]]:
    if not local_paths.STATE_FILE.is_file():
        return "skipped", []
    import server  # noqa: PLC0415

    try:
        obj = json.loads(local_paths.STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "fail", [f"local/state/state.json unreadable: {e}"]
    err = server.validate_state(obj)
    return ("fail", [f"local/state/state.json invalid: {err}"]) if err else ("ok", [])


def check_notes(root: Path = ROOT) -> tuple[str, list[str]]:
    start = root / "local" / "notes" / "START.md"
    if not start.is_file():
        return "skipped", []
    issues: list[str] = []
    size = start.stat().st_size
    if size > 10240:
        issues.append(f"local/notes/START.md is {size} B (> 10240 B limit)")
    txt = start.read_text(encoding="utf-8", errors="replace")
    in_map = False
    for i, line in enumerate(txt.splitlines(), 1):
        if line.startswith("## 4."):
            in_map = True
            continue
        if in_map and line.startswith("## "):
            break
        if in_map:
            for m in re.finditer(r"`((?:local/notes|docs)/[^`\s*]+)`", line):
                ref = root / m.group(1)
                if not ref.exists():
                    issues.append(f"local/notes/START.md:{i}: broken reading-map path `{m.group(1)}`")
    return ("fail" if issues else "ok"), issues


def run_checks(root: Path = ROOT, live_sources: bool = False) -> tuple[bool, list[tuple[str, str, list[str]]]]:
    """Run all static, hygiene, source-contract, state, and note checks without mutating local/."""
    results: list[tuple[str, str, list[str]]] = []

    eol = check_eol_and_bom(root, include_notes=True)
    results.append(("encoding/eol", "fail" if eol else "ok", eol))

    syn = check_syntax(root)
    results.append(("syntax/json", "fail" if syn else "ok", syn))

    rst, rissues = check_ruff(root)
    results.append(("ruff", rst, rissues))

    hyg = check_hygiene(root)
    results.append(("hygiene/g4", "fail" if hyg else "ok", hyg))

    if live_sources:
        import check_sources as cs  # noqa: PLC0415

        try:
            defs = cs.load_catalogs(offline=False, quiet=True)
            built = json.loads(local_paths.DASHBOARDS_FILE.read_text(encoding="utf-8"))
            state = json.loads(local_paths.STATE_FILE.read_text(encoding="utf-8")) if local_paths.STATE_FILE.is_file() else {}
            res = cs.check_all(defs, built, state, root=root)
            s_issues = [f"NO SOURCE {b}" for b in res["bad"]] + [f"'Source shown' in {p}" for p in res["code_hits"]]
            results.append(("sources(live)", "fail" if s_issues else "ok", s_issues))
        except Exception as e:  # noqa: BLE001
            results.append(("sources(live)", "fail", [str(e)]))
    else:
        sst, sissues = check_sources_offline(root)
        results.append(("sources(offline)", sst, sissues))

    ost, oissues = check_owner_state()
    results.append(("state.json", ost, oissues))

    nst, nissues = check_notes(root)
    results.append(("notes/START.md", nst, nissues))

    ok = all(st != "fail" for _, st, _ in results)
    return ok, results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Project snapshots, snapshot diffs, and pre-flight checks.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="run encoding, syntax, ruff, hygiene, source, state, and note checks")
    c.add_argument("--live-sources", action="store_true", help="fetch live /widgets.json instead of cached catalogs")

    s = sub.add_parser("snapshot", help="create a snapshot zip of publishable files in local/snapshots/")
    s.add_argument("--label", default="", help="optional suffix label (e.g. pre-p5)")
    s.add_argument("--force", action="store_true", help="write a snapshot even if identical to the latest one")

    d = sub.add_parser("diff", help="diff working tree against the latest (or given) snapshot zip")
    d.add_argument("snapshot", nargs="?", help="path to snapshot zip (defaults to newest in local/snapshots/)")
    d.add_argument("--stat", action="store_true", help="show summary counts only")
    d.add_argument("paths", nargs="*", help="optional path filters")

    rz = sub.add_parser("release-zip", help="build the clean Windows user release zip in local/releases/")
    rz.add_argument("-o", "--output", default="", help="optional output path (defaults to local/releases/Libre-Terminal-Windows.zip)")

    a = ap.parse_args(argv)
    if a.cmd == "release-zip":
        res = create_release_zip(ROOT, Path(a.output) if a.output else None)
        print(f"Release zip created: {res['path']} ({res['files']} files, {res['bytes']:,} bytes, sha256={res['sha256']})")
        b = res["build"]
        print(f"  built from version {b['version']}, commit {b['commit'][:12]}"
              + (f"; NOT the commit: {len(b['uncommitted_files'])} packed file(s) differ from it" if b["uncommitted_files"] else ""))
        return 0
    if a.cmd == "check":
        ok, results = run_checks(ROOT, live_sources=a.live_sources)
        for name, st, issues in results:
            tag = "[ok]" if st == "ok" else "[--]" if st == "skipped" else "[!!]"
            extra = f" ({len(issues)} issue{'s' if len(issues) != 1 else ''})" if issues else ""
            print(f"  {tag} {name:<18} {st}{extra}")
            for iss in issues[:25]:
                print(f"       {iss}")
            if len(issues) > 25:
                print(f"       ... and {len(issues) - 25} more")
        return 0 if ok else 1

    if a.cmd == "snapshot":
        res = create_snapshot(ROOT, label=a.label, force=a.force)
        if res["created"]:
            print(f"Snapshot created: {res['path']} ({res['files']} files, {res['bytes']:,} bytes)")
        else:
            print(f"Unchanged since latest snapshot: {res['path']} ({res['files']} files)")
        return 0

    snap_path = Path(a.snapshot) if a.snapshot else latest_snapshot()
    if snap_path is None:
        print("No snapshot zip found in local/snapshots/; run `launcher.py snapshot` first.")
        return 1
    res = diff_snapshot(ROOT, snap_path, paths=a.paths, stat_only=a.stat)
    print(f"Diff vs {res['snapshot'].name}:")
    total = len(res["added"]) + len(res["deleted"]) + len(res["modified"])
    if not total:
        print("  (no differences)")
        return 0
    for tag, items in (("A", res["added"]), ("M", res["modified"]), ("D", res["deleted"])):
        for it in items:
            stat = f"+{it['ins']} -{it['del']}" if (it["ins"] or it["del"]) else f"{it['bytes']:+d}B"
            print(f"  {tag}  {it['path']:<48} {stat}")
    if not a.stat and res["hunks"]:
        print("")
        for ln in res["hunks"]:
            print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
