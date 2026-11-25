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
os.environ.setdefault("TERMINAL_STRICT_CONTRACT", "1")       # a view answering outside the envelope fails the test
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


def check_as_of(route: str, out: dict, expected: str, label: str = ""):
    """P3-03: a view's answer (through dispatch) is dated by the newest data point of its fixture input. expected is the
    hand-picked newest date of that input, typed as a literal; test_asof.py counts these calls per route."""
    got = out.get("as_of") if isinstance(out, dict) else None
    na = out.get("as_of_na") if isinstance(out, dict) else None
    check(got == expected and not na, f"{route}{' (' + label + ')' if label else ''}: as_of {expected} (got {got!r}, as_of_na {na!r})")


NON_US_CCY = ("eur", "gbp", "jpy")


def covered(route: str, market: str, out):
    """P3-05: a 'global' route answered a fixture input of this market ('us', or the currency of a listing elsewhere:
    'eur', 'gbp', 'jpy') inside the envelope; test_contract counts these calls per route."""
    check(market in ("us",) + NON_US_CCY, f"{route}: market {market!r} is us, eur, gbp or jpy")
    check(isinstance(out, dict) and out.get("sources") and not out.get("error"), f"{route} ({market}): answered inside the envelope")


def refused(route: str, params: dict, kind: str):
    """P3-05: a 'us_only' route asked about a listing elsewhere answers not_covered (a 'set' route asked for something
    outside its list answers input), with a plain message and before any source is called."""
    import composite as C
    from _fixtures import serve
    from composite.core import contract as K
    C._cache.clear()
    with serve({}) as rec:                                 # any outbound request would be recorded (and answer 404)
        try:
            C.dispatch(route, dict(params))
        except C.CompositeError as e:
            msg = str(e)
            check(K.kind_of(e) == kind, f"{route} {params}: error kind {kind} (got {K.kind_of(e)}: {msg[:80]})")
            check(msg and len(msg) < 300 and "Traceback" not in msg and "Error" not in msg.split(":")[0], f"{route}: a plain message ({msg[:80]})")
            check(rec.calls == 0, f"{route} {params}: refused before any source was called ({rec.calls} calls)")
            return msg
    check(False, f"{route} {params}: answered instead of {kind}")
