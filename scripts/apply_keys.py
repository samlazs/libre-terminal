"""Merge keys from local/api_keys.env into ~/.openbb_platform/user_settings.json.

Only non-empty values are written. Existing keys not mentioned in the env file
are kept. To remove a key that was applied before (revoked, or no longer wanted),
set it to the word REMOVE (FMP_API_KEY=REMOVE) and run this again; settings that
only belong with that key (TRADIER_ACCOUNT_TYPE with TRADIER_API_KEY) go with it.
A timestamped backup of user_settings.json is made first; the three newest are kept.
The file is written through a temporary file, and a user_settings.json that is not
valid JSON is never overwritten (fix or delete it, then run this again).
Note: ~/.openbb_platform/user_settings.json is OpenBB's global settings file, shared
with any other OpenBB installation of this user.
Both the V5 and V4 environments read this same file.
`launcher.py start` runs this by itself when api_keys.env changed since the last run.
"""

import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
import local_paths  # noqa: E402

ENV_FILE = local_paths.KEYS_FILE
SETTINGS = Path.home() / ".openbb_platform" / "user_settings.json"
STAMP = local_paths.RUN_DIR / "keys-applied.json"      # which version of api_keys.env was applied last
# settings that only mean something next to a key: written with it, removed with it
COMPANIONS = {"tradier_api_key": ["tradier_account_type"]}


def read_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():      # -sig: tolerate a BOM added by some editors
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            out[k.strip().lower()] = None if v.upper() == "REMOVE" else v
    return out


def prune_backups(settings: Path, keep: int = 3) -> None:
    """Backups hold every key: keep only the newest few."""
    for old in sorted(settings.parent.glob(settings.name + ".bak-*"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass


def merge(settings: dict, keys: dict) -> tuple:
    """(settings, written names, removed names): REMOVE drops a key and its companions; a companion is only written
    together with its key."""
    creds = settings.setdefault("credentials", {})
    if not isinstance(creds, dict):
        raise ValueError("'credentials' in user_settings.json is not an object")
    for key, deps in COMPANIONS.items():
        if keys.get(key, "") is None:                      # the key is being removed
            for d in deps:
                keys[d] = None
        elif key not in keys and not creds.get(key):        # no key at all: its companions are not written either
            for d in deps:
                keys.pop(d, None)
    removed = sorted(k for k, v in keys.items() if v is None and k in creds)
    for k in [k for k, v in keys.items() if v is None]:
        creds.pop(k, None)
    written = {k: v for k, v in keys.items() if v is not None}
    creds.update(written)
    return settings, sorted(written), removed


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def apply(env_file: Path = None, settings_path: Path = None, quiet: bool = False) -> int:
    env_file = env_file or ENV_FILE
    settings_path = settings_path or SETTINGS
    keys = read_env(env_file)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings = {"credentials": {}, "preferences": {}, "defaults": {"commands": {}}}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8-sig") or "{}")
            if not isinstance(loaded, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as e:
            print(f"Keys NOT applied: {settings_path} cannot be read ({type(e).__name__}: {str(e)[:120]}).")
            print("  It is OpenBB's own settings file and is left as it is. Fix it or move it away, then run this again.")
            return 2
        orig_snapshot = json.dumps(loaded, sort_keys=True)
        settings = loaded
    else:
        orig_snapshot = None
    try:
        settings, names, removed = merge(settings, keys)
    except ValueError as e:
        print(f"Keys NOT applied: {e}.")
        return 2
    changed = orig_snapshot is None or json.dumps(settings, sort_keys=True) != orig_snapshot
    if changed:
        if settings_path.exists():
            shutil.copy2(settings_path, settings_path.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
        write_atomic(settings_path, json.dumps(settings, indent=4))
        if os.name != "nt":
            try:
                os.chmod(settings_path, 0o600)
            except OSError:
                pass
        prune_backups(settings_path)
    try:                                                    # remember which version of the env file this was
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(STAMP, json.dumps({"env_mtime": env_file.stat().st_mtime if env_file.exists() else 0, "time": time.time()}))
    except OSError:
        pass
    for n in removed:
        print(f"  removed {n}")
    if quiet:
        print(f"API keys applied from {env_file.name}: {len(names)} set" + (f", {len(removed)} removed" if removed else ""))
        return 0
    print(f"Wrote {len(names)} credential(s) to {settings_path}")
    for n in names:
        print(f"  - {n}")          # names only, never the values
    if not names:
        print(f"  (none filled in yet - edit {env_file} first; the terminal works without keys)")
    return 0


def main() -> int:
    local_paths.ensure()
    return apply(quiet="--quiet" in sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
