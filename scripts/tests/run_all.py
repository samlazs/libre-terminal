"""Run offline checks (the single entry point; also `launcher.py test`). Exit code 1 if any fails.

  .venv-v5\\Scripts\\python.exe scripts\\tests\\run_all.py [--fast] [-j N] [pattern ...]

Each test_*.py runs in its own isolated Python process with a temporary local/ folder; none needs the network or
the running backends. test_generator.py replays the last dashboard build from the backend catalogs recorded in
local/cache/ (skipped before the first start). test_frontend.py runs frontend_checks.js in headless Edge/Chrome.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def select_tests(all_tests: list[Path], patterns: list[str] | None = None, fast: bool = False) -> list[Path]:
    """Filter test_*.py paths by substring patterns (case-insensitive) and optional --fast (skipping test_frontend)."""
    selected = list(all_tests)
    if fast:
        selected = [p for p in selected if p.name != "test_frontend.py"]
    if patterns:
        needles = [str(x).lower() for x in patterns if str(x).strip()]
        if needles:
            selected = [p for p in selected if any(n in p.stem.lower() for n in needles)]
    return selected


def _run_one(test: Path) -> tuple[Path, int, int, str, list[str], float]:
    t = time.time()
    try:
        r = subprocess.run([sys.executable, str(test)], cwd=HERE.parents[1], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return test, 1, 0, "TIMEOUT", [], time.time() - t
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r": (\d+) checks passed", out)
    n = int(m.group(1)) if m else 0
    skipped = [ln for ln in out.splitlines() if "skipped" in ln]
    return test, r.returncode, n, out, skipped, time.time() - t


def main(argv=None):
    all_tests = sorted(HERE.glob("test_*.py"))
    ap = argparse.ArgumentParser(description="Run offline checks in isolated subprocesses.")
    ap.add_argument("patterns", nargs="*", help="optional substring filters on test filenames (e.g. numbers sources)")
    ap.add_argument("-j", "--jobs", type=int, default=min(6, os.cpu_count() or 4), help="parallel worker processes")
    ap.add_argument("--fast", action="store_true", help="skip headless browser checks (test_frontend.py)")
    ap.add_argument("--fail-fast", action="store_true", help="stop queuing new tests after the first failure")
    a = ap.parse_args(argv)

    tests = select_tests(all_tests, a.patterns, fast=a.fast)
    if not tests:
        avail = ", ".join(p.stem for p in all_tests)
        print(f"No test files matched {a.patterns!r}. Available: {avail}")
        return 2
    if a.fast and any(p.name == "test_frontend.py" for p in all_tests):
        print("  (--fast: skipping test_frontend.py headless browser checks)")

    t0 = time.time()
    results = {}
    failed = []
    jobs = max(1, min(a.jobs, len(tests)))
    if jobs == 1:
        for test in tests:
            res = _run_one(test)
            results[test] = res
            if res[1] and a.fail_fast:
                break
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(_run_one, test): test for test in tests}
            for fut in concurrent.futures.as_completed(futs):
                res = fut.result()
                results[res[0]] = res
                if res[1] and a.fail_fast:
                    for pending in futs:
                        pending.cancel()
                    break

    total = 0
    for test in tests:
        if test not in results:
            continue
        _, rc, n, out, skipped, dt_s = results[test]
        total += n
        if rc:
            failed.append(test.name)
            print(f"  {test.stem:<24} FAILED  {dt_s:4.1f} s")
            tail = "TIMEOUT" if out == "TIMEOUT" else "\n    ".join(out.strip().splitlines()[-6:])
            print("    " + tail)
        else:
            note = f"  ({skipped[0].split(': ', 1)[-1]})" if skipped else ""
            print(f"  {test.stem:<24} {n:>5} checks  {dt_s:4.1f} s{note}")
    print(("ALL PASSED" if not failed else "FAILED: " + ", ".join(failed)) + f"  ({total} checks, {time.time() - t0:.1f} s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
