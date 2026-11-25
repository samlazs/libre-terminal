"""Windows installer guard (P1-07): install.ps1 stops before any download when the folder path leaves too little room
for the deepest file the install writes (MAX_PATH), and its recorded depth keeps up with the installed packages."""

import os
import re
import shutil
import subprocess
import sys

from _common import ROOT, check, done, make_tmp_dir

PS1 = ROOT / "scripts" / "install.ps1"
text = PS1.read_text(encoding="ascii")
deep_m, max_m = re.search(r"^\$DeepestVenvPath = (\d+)\r?$", text, re.M), re.search(r"^\$MaxPath = (\d+)\r?$", text, re.M)
check(deep_m is not None and max_m is not None, "install.ps1 records the deepest install path and the path limit")
DEEPEST, MAX_PATH = int(deep_m.group(1)), int(max_m.group(1))
check(MAX_PATH == 255, f"the limit leaves room under Windows' 260 characters ({MAX_PATH})")
check(text.index("$DeepestVenvPath = ") < text.index("function Find-Uv") < text.index("& $uv run"),
      "the guard runs before uv is looked for, installed or run")

# the recorded depth is the real one: deepest file below the folder, .venv-v5\... included (bytecode too: the install
# compiles it)
venv = ROOT / ".venv-v5"
if venv.is_dir():
    deepest, where = 0, ""
    for d, _dirs, files in os.walk(venv):
        rel = os.path.relpath(d, ROOT)
        for f in files:
            n = len(rel) + 1 + len(f)
            if n > deepest:
                deepest, where = n, os.path.join(rel, f)
    check(deepest <= DEEPEST, f"no installed file is deeper than install.ps1 assumes ({deepest} <= {DEEPEST}: {where})")
    check(deepest >= DEEPEST - 40, f"the recorded depth is not far above the real one, so short folders are not refused ({deepest} vs {DEEPEST})")

if sys.platform == "win32":
    long_paths = subprocess.run(["powershell", "-NoProfile", "-Command",
                                 "(Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem').LongPathsEnabled"],
                                capture_output=True, text=True, timeout=60).stdout.strip() == "1"
    if long_paths:
        print("  long paths are enabled on this machine: the refusal run is skipped (the guard lets the install go on)")
    else:
        # a folder one character too long (with a non-ASCII name on the way) is refused before anything happens
        base = make_tmp_dir("guard-") / "Zo\u00eb"
        fit = MAX_PATH - 1 - DEEPEST
        root = base / ("x" * max(1, fit + 1 - len(str(base)) - 1))
        check(len(str(root)) == fit + 1, f"test folder is exactly one character too long ({len(str(root))} vs {fit + 1})")
        (root / "scripts").mkdir(parents=True)
        shutil.copy2(PS1, root / "scripts" / "install.ps1")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts" / "install.ps1")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL)
        out = r.stdout + r.stderr
        check(r.returncode == 1, f"install.ps1 exits 1 from a {len(str(root))}-character folder ({r.returncode}: {out.strip()[-200:]})")
        check("too long" in out and "C:\\Libre-Terminal" in out and "Nothing was downloaded" in out,
              "the message says why, where to move the folder, and that nothing changed")
        check(sorted(p.name for p in root.iterdir()) == ["scripts"] and not (root / ".venv-v5").exists(),
              "nothing was created next to the installer")
        check(not re.search(r"uv is not installed|Installing it for your Windows user", out), "uv was not looked for or installed")

done("test_install_guard")
