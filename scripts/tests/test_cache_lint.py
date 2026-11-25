"""Lint: a source failure (CompositeError) is never turned into empty data without saying so.

Every `except ... CompositeError` in terminal/ whose body swallows the failure (returns or assigns [] / {} / None, or is
only pass / continue) must mark the answer partial (C.partial_from / note_partial) or substituted (note_fallback), re-raise, or be listed below with
the reason it is not a cached empty answer. A new site fails this test until it is one of those three."""

import ast
import sys
from pathlib import Path

from _common import check, done

TERMINAL = Path(__file__).resolve().parents[2] / "terminal"

# file::function -> why swallowing is right there (each must still match a site: a stale entry fails the test)
ALLOWED = {
    "composite/core/figures.py::add_recession_shading": "the chart says so in a visible note ('Recession shading unavailable')",
    "composite/equity/earnings.py::_options_chain": "provider chain (Nasdaq -> Yahoo -> Cboe); a miss on every provider is held 1 minute",
    "composite/macro/sources.py::_fred_points": "falls back to FRED's public CSV on the next line; that failure raises",
    "composite/markets/prices.py::_fetch_history": "retried symbol by symbol below; history() reports symbols still missing",
    "composite/markets/prices.py::_ohlc.run": "retried once; an empty result raises below",
    "valuation.py::_figures": "SEC figures fall back to Yahoo; the basis used is named in the answer",
    "valuation.py::bands": "SEC figures fall back to Yahoo; the basis used is named in the answer",
    "valuation.py::roic_dupont": "SEC figures fall back to Yahoo; the basis used is named in the answer",
}
# the sites the 1.0 audit found (P2-01 problem_evidence): they must stay migrated, never allow-listed
MIGRATED = ("bonds.py::maturity_wall.run", "valuation.py::_splits.run", "composite/macro/sources.py::_fred_any.one",
            "cds.py::days", "composite/rates/curves.py::global_yields.run")


def _catches_composite(h):
    t = h.type
    if t is None:
        return False
    elts = t.elts if isinstance(t, ast.Tuple) else [t]
    return any(ast.unparse(x).endswith("CompositeError") for x in elts)


def _empty(v):
    return (v is None or (isinstance(v, ast.Constant) and v.value is None)
            or (isinstance(v, (ast.List, ast.Tuple)) and not v.elts) or (isinstance(v, ast.Dict) and not v.keys))


def _swallows(h):
    if all(isinstance(st, (ast.Pass, ast.Continue)) for st in h.body):
        return True
    return any((isinstance(st, ast.Return) and _empty(st.value)) or (isinstance(st, ast.Assign) and _empty(st.value)) for st in h.body)


def _says_so(h):
    for node in ast.walk(ast.Module(body=h.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and ast.unparse(node.func).split(".")[-1] in ("partial_from", "note_partial", "note_fallback"):
            return True
    return False


def sites():
    """[(file::qualified function, line, says_so)] for every swallowing CompositeError handler in terminal/."""
    out = []
    for path in sorted(TERMINAL.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parents[ch] = node
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _catches_composite(node) and _swallows(node):
                chain, p = [], parents.get(node)
                while p is not None:
                    if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        chain.append(p.name)
                    p = parents.get(p)
                rel = path.relative_to(TERMINAL).as_posix()
                out.append((f"{rel}::{'.'.join(reversed(chain)) or '<module>'}", node.lineno, _says_so(node)))
    return out


def _handler(src):
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ExceptHandler))


NEW_SITE = "def f():\n    try:\n        x()\n    except C.CompositeError:\n        rows = []\n"
FIXED = "def f():\n    try:\n        x()\n    except C.CompositeError as e:\n        C.partial_from(e, 'x')\n        rows = []\n"
check(_catches_composite(_handler(NEW_SITE)) and _swallows(_handler(NEW_SITE)) and not _says_so(_handler(NEW_SITE)), "a new silent site is caught")
check(_swallows(_handler(FIXED)) and _says_so(_handler(FIXED)), "partial_from makes it say so")
check(_swallows(_handler(NEW_SITE.replace("rows = []", "continue"))) and _says_so(_handler(NEW_SITE.replace("rows = []", "raise"))),
      "continue swallows; raise says so")

found = sites()
check(len(found) >= 30, f"the scan sees the handlers ({len(found)})")
bad = [f"terminal/{k.split('::')[0]}:{line} ({k.split('::')[1]})" for k, line, ok in found if not ok and k not in ALLOWED]
check(not bad, "every swallowed CompositeError marks the answer partial, re-raises, or is allow-listed with a reason: " + "; ".join(bad))
names = {k for k, _, _ in found}
stale = [k for k in ALLOWED if k not in {k2 for k2, _, ok in found if not ok}]
check(not stale, f"allow-list entries still match a silent site: {stale}")
check(not any(m in ALLOWED for m in MIGRATED), "the audited sites are never allow-listed")
for m in MIGRATED:
    hits = [ok for k, _, ok in found if k == m]
    check(all(hits), f"{m}: migrated ({len(hits)} handler(s), all say so)")

# _safe catches source failures only: a defect (TypeError, KeyError) reaches the 500-with-ref path
warm = ast.parse((TERMINAL / "composite" / "warm.py").read_text(encoding="utf-8"))
safe = next(n for n in ast.walk(warm) if isinstance(n, ast.FunctionDef) and n.name == "_safe")
handlers = [n for n in ast.walk(safe) if isinstance(n, ast.ExceptHandler)]
check(len(handlers) == 1 and handlers[0].type is not None and ast.unparse(handlers[0].type).endswith("CompositeError"),
      "_safe catches CompositeError only")

if "--list" in sys.argv:
    for k, line, ok in found:
        print(("ok    " if ok else "ALLOW " if k in ALLOWED else "BAD   ") + f"{k}:{line}")
done("test_cache_lint")
