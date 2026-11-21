"""Shared setup for the offline checks in scripts/tests/ (plain asserts, standard library only).

Run one:   .venv-v5\\Scripts\\python.exe scripts\\tests\\test_numbers.py
Run all:   .venv-v5\\Scripts\\python.exe scripts\\tests\\run_all.py
None of them needs the backends, the network or the owner's local/ folder (a temporary local/ is used).
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The real local/ folder, read-only and only for recorded inputs (backend catalogs saved by the last dashboard build)
_raw = os.environ.get("TERMINAL_LOCAL_DIR", "").strip()
REAL_LOCAL = (ROOT / Path(_raw).expanduser()).resolve() if _raw else ROOT / "local"
TMP_LOCAL = Path(tempfile.mkdtemp(prefix="terminal-tests-"))
atexit.register(shutil.rmtree, TMP_LOCAL, ignore_errors=True)
os.environ["TERMINAL_LOCAL_DIR"] = str(TMP_LOCAL)          # never touch the real local/ folder
for p in (ROOT / "terminal", ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_count = [0]


def make_tmp_dir(prefix: str = "tmp-") -> Path:
    """Create a scratch directory inside TMP_LOCAL so atexit cleans it up automatically."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=TMP_LOCAL))


def check(cond, what):
    assert cond, "FAILED: " + what
    _count[0] += 1


def done(name):
    print(f"{name}: {_count[0]} checks passed")
