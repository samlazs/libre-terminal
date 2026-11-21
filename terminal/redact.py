"""Remove API keys, tokens and passwords from text before it is shown or logged.

Used by the UI server (terminal/server.py), the backend wrapper (scripts/run_backend.py,
for error responses and log output of both OpenBB backends) and the offline tests.
Standard library only.

Two layers:
  * a pattern for "name=value" / "name": "value" pairs whose name looks like a secret
    (api_key, apikey, key, token, access_token, secret, password, ...);
  * the literal values of the keys this computer actually has (local/api_keys.env and
    ~/.openbb_platform/user_settings.json), wherever they appear.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

MASK = "REDACTED"

# name=value in URLs and messages, "name": "value" in JSON. The name must not be glued to a longer word
# ("monkey=1" stays), the value runs to the next separator.
_PAIR = re.compile(
    r"""(?ix)
    (?<![a-z0-9])
    (                                                   # 1: the name and its separator
      ["']?
      (?:[a-z0-9]+[_-])*
      (?:api[_-]?key|apikey|api[_-]?token|access[_-]?token|auth[_-]?token|app[_-]?token|app[_-]?key|registration[_-]?key|subscription[_-]?key|token|key|secret|client[_-]?secret|password|passwd|pwd)
      ["']?
      \s*(?:=|:)\s*
      ["']?
    )
    (?!REDACTED)
    ([^\s&"',;}\]<>)]{3,})                               # 2: the value
    """
)
# Authorization headers that end up in a message
_BEARER = re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9._\-]{12,}")

_known: list = []
_known_at = [0.0]
_lock = threading.Lock()


def _key_values() -> list:
    """Literal key values configured on this computer (refreshed every 5 minutes; never returned to callers)."""
    now = time.time()
    if now - _known_at[0] < 300:
        return _known
    vals = set()
    try:
        import local_paths  # noqa: PLC0415  (terminal/ is on sys.path for every caller)
        env_file = local_paths.KEYS_FILE
    except Exception:  # noqa: BLE001
        env_file = None
    try:
        if env_file and Path(env_file).exists():
            for line in Path(env_file).read_text(encoding="utf-8-sig", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if len(v) >= 8:
                        vals.add(v)
    except OSError:
        pass
    try:
        p = Path.home() / ".openbb_platform" / "user_settings.json"
        creds = json.loads(p.read_text(encoding="utf-8")).get("credentials", {}) or {}
        for v in creds.values():
            if isinstance(v, str) and len(v) >= 8:
                vals.add(v)
    except (OSError, ValueError, AttributeError):
        pass
    for k, v in os.environ.items():
        if v and len(v) >= 12 and re.search(r"(?i)(api_?key|token|secret|password)", k):
            vals.add(v)
    with _lock:
        # the URL-encoded forms too (a key with + / = reaches a logged URL as %2B %2F %3D; audit C8)
        from urllib.parse import quote, quote_plus  # noqa: PLC0415
        vals |= {quote(v, safe="") for v in vals} | {quote_plus(v) for v in vals}
        _known[:] = sorted(vals, key=len, reverse=True)
        _known_at[0] = now
    return _known


def redact(text) -> str:
    """The text with every secret replaced by REDACTED. Accepts any object (str() is applied)."""
    if text is None:
        return text
    s = text if isinstance(text, str) else str(text)
    if not s:
        return s
    s = _PAIR.sub(lambda m: m.group(1) + MASK, s)
    s = _BEARER.sub(lambda m: m.group(1) + " " + MASK, s)
    for v in _key_values():
        if v in s:
            s = s.replace(v, MASK)
    return s


def redact_obj(obj):
    """Redact every string inside a JSON-like structure (dicts, lists, tuples)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


def contains_secret(text) -> bool:
    return text is not None and redact(text) != str(text)
