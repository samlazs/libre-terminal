"""Where this computer's personal data lives: one folder, `local/`, never shared.

Everything the terminal writes at run time goes there: API keys, saved layouts and
watchlists, backend settings, logs, caches and the generated dashboards. The rest of
the project folder is the program itself and is never written to while it runs.
Set TERMINAL_LOCAL_DIR to keep the folder somewhere else (a relative path is taken
from the project folder).

Every script and the UI server take their paths from this module, standard library
only, importable from any Python 3.9+.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL = PROJECT / "local"


def _local_dir() -> Path:
    raw = os.environ.get("TERMINAL_LOCAL_DIR", "").strip()
    if not raw:
        return DEFAULT_LOCAL
    p = Path(raw).expanduser()
    return (p if p.is_absolute() else PROJECT / p).resolve()


LOCAL = _local_dir()
KEYS_FILE = LOCAL / "api_keys.env"              # optional API keys (template: config/api_keys.example.env)
STATE_DIR = LOCAL / "state"                     # saved dashboards, watchlists, picks + daily backups
STATE_FILE = STATE_DIR / "state.json"
BACKENDS_DIR = LOCAL / "backends"               # per-backend settings written by OpenBB (apps.json, widgets.json)
RUN_DIR = LOCAL / "run"                         # pid files and the ports of the last start
PORTS_FILE = RUN_DIR / "ports.json"
LOGS_DIR = LOCAL / "logs"
CACHE_DIR = LOCAL / "cache"                     # disk cache of slow or rate-limited sources (OECD, BIS, EIA, ...)
DASHBOARDS_FILE = LOCAL / "dashboards.json"     # rebuilt by scripts/build_dashboards.py on every start
KEYS_TEMPLATE = PROJECT / "config" / "api_keys.example.env"

README_TEXT = """\
PERSONAL DATA OF THIS COMPUTER - NEVER SHARE OR PUBLISH THIS FOLDER.

It holds your API keys, saved dashboards and watchlists, logs and caches.
Everything outside this folder is the program itself.

  api_keys.env      your optional API keys (edit with Setup-Keys.cmd / setup-keys.sh)
  state/            saved layouts, watchlists and picks (state.json + daily backups)
  backends/         settings the two data backends write for themselves
  dashboards.json   the built-in dashboards, rebuilt on every start
  cache/, logs/, run/   caches, log files, process ids (safe to delete while stopped)

Backup: copy this whole folder. Restore: stop the terminal, put it back.
Reset everything: stop the terminal and delete this folder; it is recreated empty.
"""

# Folders of the layout used before local/ existed -> where they live now.
_OLD_LAYOUT = [
    ("config/api_keys.env", "api_keys.env"),
    ("workspace/terminal", "state"),
    ("workspace/v5", "backends/v5"),
    ("workspace/v4", "backends/v4"),
    ("workspace/run", "run"),
    ("logs", "logs"),
    ("terminal/cache", "cache"),
    ("terminal/dashboards.json", "dashboards.json"),
]


def replace_file(tmp: Path | str, path: Path | str, tries: int = 10) -> None:
    """os.replace(tmp, path), retried for a moment: on Windows the replace fails (PermissionError, WinError 5) while any
    process has the old file open for reading, and pid files, ports.json and dashboards.json are read all the time
    (the UI's service status, `launcher.py status`, a page load). Raises the last error after about 2.75 s."""
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def backend_dir(name: str) -> Path:
    return BACKENDS_DIR / name


def _migrate_old_layout() -> list:
    """Move personal data left in the old layout (workspace/, logs/, config/api_keys.env, ...) into local/.

    Runs once, only for the default local/ folder and only while that folder does not exist yet,
    so a scratch TERMINAL_LOCAL_DIR never takes the owner's data. Returns the paths moved.
    """
    if LOCAL != DEFAULT_LOCAL or LOCAL.exists():
        return []
    found = [(PROJECT / old, LOCAL / new) for old, new in _OLD_LAYOUT if (PROJECT / old).exists()]
    if not found:
        return []
    try:
        LOCAL.mkdir(parents=True)               # fails if another process got here first: it migrates
    except FileExistsError:
        return []
    moved = []
    for src, dst in found:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(src.relative_to(PROJECT).as_posix())
    old_ws = PROJECT / "workspace"
    if old_ws.is_dir() and not any(old_ws.iterdir()):
        old_ws.rmdir()
    return moved


def ensure() -> Path:
    """Create local/ with its sub-folders and README (migrating an old layout first). Safe to call often."""
    moved = _migrate_old_layout()
    for d in (STATE_DIR, backend_dir("v5"), backend_dir("v4"), RUN_DIR, LOGS_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    readme = LOCAL / "README.txt"
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")
    if moved:
        print(f"Moved personal data into {LOCAL}: {', '.join(moved)}", flush=True)
    return LOCAL


def saved_ports() -> dict:
    """Ports remembered by the last `launcher.py start` ({'v5': 6900, 'v4': 6901, 'ui': 6950}), or {}."""
    try:
        raw = json.loads(PORTS_FILE.read_text(encoding="utf-8"))
        out = {}
        for k, v in raw.items():
            if k in ("v5", "v4", "ui") and not isinstance(v, bool):
                p = int(v)
                if 1 <= p <= 65535:
                    out[k] = p
        return out
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


PROTECTED_CACHE = ("curvestore_", "cdsday_", "catalog-", "earnings_consensus.json", "health-history.json", "yahoo_meta.json")


def _is_protected_cache(path: Path) -> bool:
    name = path.name
    return any(name.startswith(p) or name == p for p in PROTECTED_CACHE)


def prune_local(now: float | None = None, max_cache_mb: float = 150.0) -> int:
    """Disk caches and freeze dumps do not grow for ever: drop cache files untouched for 30 days (the history stores
    curvestore_* and cdsday_*, catalog snapshots and earnings/health history stay), backend cache entries after 14 days, freeze dumps
    after 14; then trim non-protected files under local/cache/ oldest-first to stay within max_cache_mb."""
    now = now or time.time()
    gone = 0
    rules = [(CACHE_DIR, "*.json", 30), (CACHE_DIR / "backend", "**/*.pkl.gz", 14), (LOGS_DIR, "*-freeze-*.txt", 14)]
    for folder, pattern, days in rules:
        if not folder.exists():
            continue
        for f in folder.glob(pattern):
            if folder == CACHE_DIR and _is_protected_cache(f):
                continue
            try:
                if f.is_file() and now - f.stat().st_mtime > days * 86400:
                    f.unlink()
                    gone += 1
            except OSError:
                pass
    for stale_dir in (CACHE_DIR, STATE_DIR, RUN_DIR):
        if stale_dir.exists():
            for f in stale_dir.rglob("*.tmp*"):
                try:
                    if f.is_file() and now - f.stat().st_mtime > 86400:
                        f.unlink()
                        gone += 1
                except OSError:
                    pass
    upd_dir = LOCAL / "upd"
    if upd_dir.is_dir():
        for d in upd_dir.iterdir():
            try:
                if now - d.stat().st_mtime > 86400:
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=True)
                    else:
                        d.unlink(missing_ok=True)
                    gone += 1
            except OSError:
                pass
        try:
            if not any(upd_dir.iterdir()):
                upd_dir.rmdir()
        except OSError:
            pass
    cap = int(max_cache_mb * 1024 * 1024)
    if cap > 0 and CACHE_DIR.exists():
        evictable = []
        total = 0
        for f in CACHE_DIR.rglob("*"):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
                total += st.st_size
                if not _is_protected_cache(f):
                    evictable.append((st.st_mtime, st.st_size, f))
            except OSError:
                pass
        if total > cap:
            evictable.sort(key=lambda x: x[0])
            for _, size, f in evictable:
                if total <= cap:
                    break
                try:
                    f.unlink()
                    total -= size
                    gone += 1
                except OSError:
                    pass
    return gone
