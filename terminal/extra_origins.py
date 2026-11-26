"""Extra request origins the owner trusts (TERMINAL_EXTRA_ORIGINS), shared by the backends and the UI server.

By default only the terminal page itself may call the local servers from a browser. A desktop client that embeds a
web view (a Tauri app sends `Origin: tauri://localhost` on macOS/Linux, `http://tauri.localhost` on Windows) is refused
like any other site. Listing its origin here lets it in:

    TERMINAL_EXTRA_ORIGINS="tauri://localhost http://tauri.localhost"

Entries are separated by commas or spaces and compared exactly (case does not matter). There are no wildcards: `*` or
anything that is not `scheme://host[:port]` is ignored and logged. Empty (the default) changes nothing.
"""

from __future__ import annotations

import os
import re

ENV = "TERMINAL_EXTRA_ORIGINS"
_ORIGIN = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*)://(?P<host>[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:.]+\])(?::(?P<port>[0-9]{1,5}))?")
_DEFAULT_PORT = {"http": "80", "https": "443"}


def normalize(entry: str) -> str | None:
    """`scheme://host[:port]` in the form a browser sends it (lower case, no default port), or None if it is not one."""
    m = _ORIGIN.fullmatch(entry.strip().lower())
    if not m:
        return None
    scheme, host, port = m.group("scheme"), m.group("host"), m.group("port")
    if port is not None:
        if not 1 <= int(port) <= 65535:
            return None
        if _DEFAULT_PORT.get(scheme) == port.lstrip("0"):
            port = None
    return f"{scheme}://{host}" + (f":{int(port)}" if port is not None else "")


def parse(raw: str | None) -> tuple[frozenset, list]:
    """(accepted origins, rejected entries) from a comma- or space-separated list."""
    good, bad = set(), []
    for entry in re.split(r"[\s,]+", raw or ""):
        if not entry:
            continue
        origin = normalize(entry)
        if origin is None:
            bad.append(entry)
        else:
            good.add(origin)
    return frozenset(good), bad


def from_env(log=None) -> frozenset:
    """The accepted origins from TERMINAL_EXTRA_ORIGINS; each ignored entry and the accepted list are logged once."""
    good, bad = parse(os.environ.get(ENV))
    if log is not None:
        for entry in bad:
            log(f"{ENV}: ignored {entry!r} (not an origin of the form scheme://host[:port]; wildcards are not supported)")
        if good:
            log(f"{ENV}: also answering {', '.join(sorted(good))}")
    return good


def allowed(origin: str | None, extra: frozenset) -> bool:
    """True when the request's Origin header is one of the extra origins (exact, case-insensitive)."""
    return origin is not None and bool(extra) and origin.strip().lower() in extra
