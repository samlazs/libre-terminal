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
        return {k: int(v) for k, v in json.loads(PORTS_FILE.read_text(encoding="utf-8")).items() if k in ("v5", "v4", "ui")}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}
