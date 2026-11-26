"""Lightweight GitHub release checker and in-place self-updater (stdlib only)."""

from __future__ import annotations

import ast
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "terminal") not in sys.path:
    sys.path.insert(0, str(ROOT / "terminal"))

import local_paths  # noqa: E402
from version import VERSION  # noqa: E402

REPO = "samlazs/libre-terminal"
LATEST_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
LATEST_WEB_URL = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "Libre-Terminal-Windows.zip"
TOP_DIR = "Libre-Terminal"

CHECK_TIMEOUT = 3.0
EXPLICIT_CHECK_TIMEOUT = 10.0
DOWNLOAD_TIMEOUT = 60.0

MAX_API_BYTES = 1_048_576
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 150 * 1024 * 1024
MAX_ZIP_FILES = 5000
MAX_REDIRECTS = 5

ALLOWED_EXACT_HOSTS = frozenset({"api.github.com", "github.com"})
ALLOWED_HOST_SUFFIX = ".githubusercontent.com"

PROTECTED_TOP = frozenset({"local", ".git", ".mcp.json", "agents.md", "." + "cla" + "ude"})
REQUIRED_FILES = frozenset({
    "terminal/version.py",
    "scripts/launcher.py",
    "Start.cmd",
    "terminal/server.py",
    "config/requirements-v5.lock.txt",
})
RESERVED_WIN_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

_VERSION_RX = re.compile(r"^[vV]?(\d+)\.(\d+)(?:\.(\d+))?$", re.ASCII)
_SHA256_RX = re.compile(r"^sha256:([0-9a-fA-F]{64})$", re.ASCII)
_TAG_PATH_RX = re.compile(r"/releases/tag/([^/?#]+)$")
_WIN_DRIVE_RX = re.compile(r"^[A-Za-z]:")


class UpdateError(RuntimeError):
    """Raised when an update check, verification, or install step fails."""


class IncompleteRollbackError(UpdateError):
    """Raised when an update fails and rollback could not restore every file."""

    def __init__(self, msg: str, backup_dir: Path):
        super().__init__(msg)
        self.backup_dir = backup_dir


def parse_version(raw: str) -> tuple[int, int, int]:
    """Parse a release tag or VERSION string (e.g. 'v1.0.1', '1.1') into (major, minor, patch)."""
    if not isinstance(raw, str):
        raise UpdateError(f"Unsupported version value: {raw!r}")
    s = raw.strip()
    m = _VERSION_RX.match(s)
    if not m:
        raise UpdateError(f"Unsupported version string: {raw!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def is_newer(remote: str, current: str) -> bool:
    """Return True when remote and current are valid versions and remote > current."""
    try:
        return parse_version(remote) > parse_version(current)
    except UpdateError:
        return False


def _extract_version_from_text(text: str) -> str:
    """Read VERSION = '...' safely via AST without executing module code."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise UpdateError(f"Could not parse terminal/version.py: {e}") from e
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "VERSION":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        val = node.value.value.strip()
                        parse_version(val)
                        return val
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "VERSION":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    val = node.value.value.strip()
                    parse_version(val)
                    return val
    raise UpdateError("terminal/version.py does not define a valid string VERSION.")


def installed_version(root: Path = ROOT) -> str:
    """Return the installed version from root/terminal/version.py."""
    vpath = root / "terminal" / "version.py"
    try:
        text = vpath.read_text(encoding="utf-8")
    except OSError as e:
        raise UpdateError(f"Could not read {vpath}: {e}") from e
    return _extract_version_from_text(text)


def is_git_checkout(root: Path = ROOT) -> bool:
    """Return True if root appears to be a git repository or worktree."""
    return (root / ".git").exists()


def is_interactive() -> bool:
    """Return True only when both stdin and stdout are attached to an interactive terminal."""
    try:
        return bool(sys.stdin and sys.stdout and sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


def _is_unsafe_char(ch: str, *, allow_newline: bool = False) -> bool:
    code = ord(ch)
    if code < 32:
        return not (allow_newline and ch == "\n")
    if 0x7F <= code <= 0x9F:
        return True
    return unicodedata.category(ch) == "Cf"


def validate_url(url: str, *, allow_local_http: bool = False) -> str:
    """Ensure URL uses HTTPS and points only to GitHub release/API/CDN hosts (or loopback HTTP in tests)."""
    if not isinstance(url, str) or not url.strip():
        raise UpdateError("Update URL is empty.")
    if any(ch.isspace() or _is_unsafe_char(ch) for ch in url):
        raise UpdateError("Update URL contains whitespace or unsafe control characters.")
    parsed = urllib.parse.urlsplit(url)
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise UpdateError("Update URL may not include user credentials.")
    try:
        _ = parsed.port
    except ValueError as e:
        raise UpdateError(f"Invalid port in update URL: {url}") from e
    host = (parsed.hostname or "").lower()
    if not host:
        raise UpdateError(f"Update URL has no host: {url}")
    scheme = parsed.scheme.lower()
    if allow_local_http and scheme == "http" and host in ("127.0.0.1", "localhost"):
        return url
    if scheme != "https":
        raise UpdateError(f"Refusing non-HTTPS update URL: {url}")
    if host in ALLOWED_EXACT_HOSTS:
        return url
    if host.endswith(ALLOWED_HOST_SUFFIX) and len(host) > len(ALLOWED_HOST_SUFFIX):
        return url
    raise UpdateError(f"Refusing update URL on untrusted host {host!r}.")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that validates every hop and enforces a hop limit."""

    def __init__(self, *, allow_local_http: bool = False):
        super().__init__()
        self.allow_local_http = allow_local_http
        self._hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._hops += 1
        if self._hops > MAX_REDIRECTS:
            raise UpdateError("Too many redirects while fetching update.")
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        validate_url(resolved, allow_local_http=self.allow_local_http)
        new_req = super().redirect_request(req, fp, code, msg, headers, resolved)
        if new_req is not None:
            new_req.headers.pop("Authorization", None)
            new_req.headers.pop("Cookie", None)
            new_req.unredirected_hdrs.pop("Authorization", None)
            new_req.unredirected_hdrs.pop("Cookie", None)
        return new_req


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Redirect handler that refuses to follow redirects so Location can be inspected."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _build_opener(*, allow_local_http: bool = False, follow_redirects: bool = True) -> urllib.request.OpenerDirector:
    handler = _SafeRedirectHandler(allow_local_http=allow_local_http) if follow_redirects else _NoRedirectHandler()
    return urllib.request.build_opener(handler)


def _norm_version_str(tag: str) -> str:
    parse_version(tag)
    return tag.strip().lstrip("vV")


def _fetch_latest_via_web(web_url: str, *, timeout: float, allow_local_http: bool) -> dict:
    validate_url(web_url, allow_local_http=allow_local_http)
    req = urllib.request.Request(web_url, headers={"User-Agent": f"Libre-Terminal/{VERSION}"})
    opener = _build_opener(allow_local_http=allow_local_http, follow_redirects=False)
    loc = ""
    try:
        with opener.open(req, timeout=timeout) as resp:
            loc = resp.geturl()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location") or ""
        else:
            raise UpdateError(f"Release page check failed (HTTP {e.code}).") from e
    except (OSError, urllib.error.URLError, http.client.HTTPException) as e:
        raise UpdateError(f"Could not reach GitHub release page: {e}") from e
    if not loc:
        raise UpdateError("Release page did not return a redirect location.")
    resolved = urllib.parse.urljoin(web_url, loc)
    validate_url(resolved, allow_local_http=allow_local_http)
    parsed = urllib.parse.urlsplit(resolved)
    m = _TAG_PATH_RX.search(parsed.path)
    if not m:
        raise UpdateError(f"Could not determine latest tag from {resolved}.")
    tag = urllib.parse.unquote(m.group(1)).strip()
    ver_str = _norm_version_str(tag)
    if allow_local_http and parsed.scheme.lower() == "http":
        dl_url = f"{parsed.scheme}://{parsed.netloc}/{REPO}/releases/download/{tag}/{ASSET_NAME}"
    else:
        dl_url = f"https://github.com/{REPO}/releases/download/{tag}/{ASSET_NAME}"
    validate_url(dl_url, allow_local_http=allow_local_http)
    return {
        "version": ver_str,
        "tag": tag,
        "name": f"Libre Terminal {ver_str}",
        "url": resolved,
        "download_url": dl_url,
        "asset_name": ASSET_NAME,
        "size": None,
        "sha256": None,
        "published_at": "",
        "body": "",
    }


def fetch_latest_release(
    *,
    api_url: str = LATEST_API_URL,
    web_url: str = LATEST_WEB_URL,
    timeout: float = CHECK_TIMEOUT,
    allow_local_http: bool = False,
) -> dict:
    """Query GitHub for the latest non-draft, non-prerelease release containing Libre-Terminal-Windows.zip."""
    validate_url(api_url, allow_local_http=allow_local_http)
    req = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Libre-Terminal/{VERSION}",
        },
    )
    opener = _build_opener(allow_local_http=allow_local_http, follow_redirects=True)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(MAX_API_BYTES + 1)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return _fetch_latest_via_web(web_url, timeout=timeout, allow_local_http=allow_local_http)
        if e.code == 403:
            rem = (e.headers.get("X-RateLimit-Remaining") or "").strip() if e.headers else ""
            try:
                err_body = e.read(2048)
            except OSError:
                err_body = b""
            if rem == "0" or b"rate limit" in err_body.lower():
                return _fetch_latest_via_web(web_url, timeout=timeout, allow_local_http=allow_local_http)
        raise UpdateError(f"GitHub release check failed (HTTP {e.code}).") from e
    except (OSError, urllib.error.URLError, http.client.HTTPException) as e:
        raise UpdateError(f"Could not reach GitHub: {e}") from e

    if len(raw) > MAX_API_BYTES:
        raise UpdateError("Release metadata response exceeded size limit.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise UpdateError(f"Invalid release metadata JSON: {e}") from e
    if not isinstance(data, dict):
        raise UpdateError("Release metadata is not a JSON object.")
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("Latest release is marked draft or prerelease.")

    tag = str(data.get("tag_name") or "").strip()
    ver_str = _norm_version_str(tag)

    assets = data.get("assets")
    if not isinstance(assets, list):
        raise UpdateError(f"Release {tag} contains no asset list.")
    asset = next((a for a in assets if isinstance(a, dict) and a.get("name") == ASSET_NAME), None)
    if asset is None:
        raise UpdateError(f"Release {tag} does not include {ASSET_NAME}.")

    dl_url = validate_url(str(asset.get("browser_download_url") or ""), allow_local_http=allow_local_http)
    size = asset.get("size")
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or size > MAX_ZIP_BYTES:
            raise UpdateError(f"Invalid release asset size: {size!r}")

    sha256: str | None = None
    digest = asset.get("digest")
    if digest is not None and str(digest).strip():
        m = _SHA256_RX.match(str(digest).strip())
        if not m:
            raise UpdateError(f"Unsupported or invalid asset digest format: {digest!r}")
        sha256 = m.group(1).lower()

    html_url = str(data.get("html_url") or "").strip()
    if html_url:
        validate_url(html_url, allow_local_http=allow_local_http)

    return {
        "version": ver_str,
        "tag": tag,
        "name": str(data.get("name") or tag).strip(),
        "url": html_url,
        "download_url": dl_url,
        "asset_name": ASSET_NAME,
        "size": size,
        "sha256": sha256,
        "published_at": str(data.get("published_at") or "").strip(),
        "body": str(data.get("body") or "")[:32768],
    }


def check_for_update(
    *,
    root: Path = ROOT,
    current: str | None = None,
    api_url: str = LATEST_API_URL,
    web_url: str = LATEST_WEB_URL,
    timeout: float = CHECK_TIMEOUT,
    quiet: bool = True,
    allow_local_http: bool = False,
) -> dict | None:
    """Return release dict if a newer release is available, or None if current (or failed when quiet=True)."""
    cur = current if current is not None else installed_version(root)
    try:
        rel = fetch_latest_release(
            api_url=api_url,
            web_url=web_url,
            timeout=timeout,
            allow_local_http=allow_local_http,
        )
    except (UpdateError, OSError, urllib.error.URLError, ValueError, TimeoutError, http.client.HTTPException):
        if quiet:
            return None
        raise
    return rel if is_newer(rel["version"], cur) else None


def download_release(
    url: str,
    dst: Path,
    *,
    expected_sha256: str | None = None,
    timeout: float = DOWNLOAD_TIMEOUT,
    max_bytes: int = MAX_ZIP_BYTES,
    allow_local_http: bool = False,
) -> str:
    """Stream release zip to dst, enforcing host allow-list, size cap, and optional SHA-256 digest."""
    validate_url(url, allow_local_http=allow_local_http)
    dst.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": f"Libre-Terminal/{VERSION}"})
    opener = _build_opener(allow_local_http=allow_local_http, follow_redirects=True)
    hasher = hashlib.sha256()
    total = 0
    try:
        with opener.open(req, timeout=timeout) as resp:
            cl_hdr = resp.headers.get("Content-Length")
            if cl_hdr:
                try:
                    cl = int(cl_hdr.strip())
                except ValueError:
                    cl = -1
                if cl > max_bytes:
                    raise UpdateError(f"Update download exceeds maximum allowed size ({cl} > {max_bytes}).")
            with open(dst, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateError(f"Update download exceeded maximum allowed size ({max_bytes} bytes).")
                    hasher.update(chunk)
                    out.write(chunk)
        if total == 0:
            raise UpdateError("Downloaded update archive is empty.")
        digest = hasher.hexdigest().lower()
        if expected_sha256 and digest != expected_sha256.lower():
            raise UpdateError("Update download checksum did not match the release metadata.")
        return digest
    except Exception as e:
        dst.unlink(missing_ok=True)
        if isinstance(e, UpdateError):
            raise
        raise UpdateError(f"Could not download update: {e}") from e


_WIN_BAD_CHARS = set('<>:"|?*')


def _validate_rel_path(rel: str) -> list[str]:
    """Validate an archive-relative path (after stripping TOP_DIR/) and return its parts."""
    if not rel or "\\" in rel or "\x00" in rel or rel.startswith("/") or _WIN_DRIVE_RX.match(rel):
        raise UpdateError(f"Unsafe path in update archive: {rel!r}")
    parts = rel.split("/")
    for part in parts:
        if (
            not part
            or part in (".", "..")
            or part.startswith(" ")
            or part.endswith((".", " "))
            or "~" in part
            or any(ord(c) < 32 or c in _WIN_BAD_CHARS for c in part)
        ):
            raise UpdateError(f"Unsafe path component in update archive: {rel!r}")
        stem = part.split(".")[0].upper()
        if stem in RESERVED_WIN_NAMES:
            raise UpdateError(f"Reserved Windows filename in update archive: {rel!r}")
    top = parts[0].rstrip(". ").lower()
    if top in PROTECTED_TOP or top.startswith(".venv"):
        raise UpdateError(f"Update archive may not overwrite protected path {rel!r}.")
    return parts


def _is_safe_rel_path(rel: str) -> bool:
    try:
        _validate_rel_path(rel)
        return True
    except UpdateError:
        return False


def validate_and_stage_zip(
    zip_path: Path,
    stage_dir: Path,
    *,
    expected_version: str | None = None,
    current_version: str | None = None,
) -> dict:
    """Verify archive integrity, paths, required files, and version, then extract into stage_dir."""
    try:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        stage_dir.mkdir(parents=True, exist_ok=True)
        stage_root = stage_dir.resolve()

        with zipfile.ZipFile(zip_path, "r") as zf:
            bad_member = zf.testzip()
            if bad_member is not None:
                raise UpdateError(f"Corrupted file in update archive: {bad_member}")

            entries: list[tuple[zipfile.ZipInfo, str]] = []
            seen_lower: set[str] = set()
            file_count = 0
            declared_total = 0

            for info in zf.infolist():
                mode_type = (info.external_attr >> 16) & 0o170000
                if mode_type == 0o120000 or (mode_type not in (0, stat.S_IFREG, stat.S_IFDIR)):
                    raise UpdateError(f"Unsupported special file in update archive: {info.filename!r}")
                name = info.filename
                if "\\" in name or "\x00" in name or name.startswith("/") or _WIN_DRIVE_RX.match(name):
                    raise UpdateError(f"Unsafe path in update archive: {name!r}")
                if not name.startswith(TOP_DIR + "/"):
                    raise UpdateError(f"Unexpected top-level entry in update archive: {name!r}")
                rel = name[len(TOP_DIR) + 1:]
                if info.is_dir():
                    if rel:
                        _validate_rel_path(rel.rstrip("/"))
                    continue
                _validate_rel_path(rel)
                low = rel.lower()
                if low in seen_lower:
                    raise UpdateError(f"Duplicate entry in update archive: {rel!r}")
                seen_lower.add(low)
                file_count += 1
                if file_count > MAX_ZIP_FILES:
                    raise UpdateError("Update archive contains too many files.")
                if info.file_size < 0:
                    raise UpdateError(f"Invalid file size in update archive: {rel!r}")
                declared_total += info.file_size
                if declared_total > MAX_UNCOMPRESSED_BYTES:
                    raise UpdateError("Update archive uncompressed size exceeds limit.")
                entries.append((info, rel))

            rel_map = {rel: info for info, rel in entries}
            missing = sorted(REQUIRED_FILES - set(rel_map))
            if missing:
                raise UpdateError(f"Update archive is missing required files: {', '.join(missing)}")

            ver_text = zf.read(rel_map["terminal/version.py"]).decode("utf-8")
            zip_ver = _extract_version_from_text(ver_text)
            if expected_version is not None and parse_version(zip_ver) != parse_version(expected_version):
                raise UpdateError(f"Archive version {zip_ver} does not match release version {expected_version}.")
            if current_version is not None and not is_newer(zip_ver, current_version):
                raise UpdateError(f"Archive version {zip_ver} is not newer than installed {current_version}.")

            extracted_total = 0
            for info, rel in entries:
                dst = stage_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                resolved = dst.resolve()
                if not resolved.is_relative_to(stage_root):
                    raise UpdateError(f"Staged path escaped staging root: {rel!r}")
                with zf.open(info, "r") as src, open(dst, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        extracted_total += len(chunk)
                        if extracted_total > MAX_UNCOMPRESSED_BYTES:
                            raise UpdateError("Update archive expanded beyond size limit.")
                        out.write(chunk)

        return {"version": zip_ver, "files": sorted(rel_map)}
    except UpdateError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, UnicodeDecodeError, ValueError) as e:
        raise UpdateError(f"Update archive is unreadable or invalid: {e}") from e


def _ensure_safe_target(root: Path, rel: str) -> Path:
    parts = _validate_rel_path(rel)
    root_res = root.resolve()
    cur = root
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise UpdateError(f"Refusing to follow symlink during update: {rel!r}")
    if not cur.parent.resolve().is_relative_to(root_res):
        raise UpdateError(f"Target path escapes installation directory: {rel!r}")
    return cur


def _install_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        local_paths.replace_file(src, dst)
        return
    except OSError:
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(src, tmp)
            local_paths.replace_file(tmp, dst)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(manifest_path: Path) -> set[str]:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = raw.get("files") if isinstance(raw, dict) else []
        if not isinstance(files, list):
            return set()
        return {f for f in files if isinstance(f, str) and _is_safe_rel_path(f)}
    except (OSError, ValueError):
        return set()


def _write_manifest(manifest_path: Path, version: str, files: list[str]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_name(f"{manifest_path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"version": version, "files": sorted(files)}, indent=2) + "\n")
        f.flush()
    try:
        local_paths.replace_file(tmp, manifest_path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _prune_empty_parents(path: Path, root: Path) -> None:
    root_res = root.resolve()
    cur = path.parent
    while cur.resolve() != root_res and cur.resolve().is_relative_to(root_res):
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def apply_staged_update(
    root: Path,
    stage_dir: Path,
    new_files: list[str],
    *,
    backup_dir: Path,
    manifest_path: Path,
    new_version: str,
) -> dict:
    """Replace files atomically from stage_dir with rollback on error, preserving local/ and .venv-*."""
    v5_lock = root / "config" / "requirements-v5.lock.txt"
    v4_lock = root / "config" / "requirements-v4.lock.txt"
    pre_v5 = _file_sha256(v5_lock)
    pre_v4 = _file_sha256(v4_lock)

    prev_files = _load_manifest(manifest_path)
    new_set = set(new_files)
    to_remove = [r for r in sorted(prev_files - new_set) if _is_safe_rel_path(r)]

    backup_dir.mkdir(parents=True, exist_ok=True)
    replaced: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    try:
        for rel in sorted(new_files):
            dst = _ensure_safe_target(root, rel)
            src = stage_dir / rel
            if dst.exists():
                bak = backup_dir / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bak)
                replaced.append(rel)
                _install_file(src, dst)
            else:
                added.append(rel)
                _install_file(src, dst)

        for rel in to_remove:
            dst = _ensure_safe_target(root, rel)
            if dst.is_file():
                bak = backup_dir / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bak)
                removed.append(rel)
                dst.unlink()
    except BaseException as e:
        rollback_failures: list[str] = []
        try:
            for rel in reversed(added):
                dst = root / rel
                try:
                    dst.unlink(missing_ok=True)
                    _prune_empty_parents(dst, root)
                except OSError as re_err:
                    rollback_failures.append(f"{rel}: {re_err}")
            for rel in [*reversed(replaced), *reversed(removed)]:
                bak = backup_dir / rel
                if bak.is_file():
                    try:
                        _install_file(bak, root / rel)
                    except OSError as re_err:
                        rollback_failures.append(f"{rel}: {re_err}")
        except BaseException as rb_exc:
            raise IncompleteRollbackError(
                f"Update failed ({e}) and rollback was interrupted ({rb_exc}). Backup kept at: {backup_dir}",
                backup_dir=backup_dir,
            ) from e
        if rollback_failures:
            raise IncompleteRollbackError(
                f"Update failed ({e}) and rollback could not restore {len(rollback_failures)} file(s). Backup kept at: {backup_dir}",
                backup_dir=backup_dir,
            ) from e
        if isinstance(e, KeyboardInterrupt):
            raise UpdateError("Update was interrupted (restored previous files).") from e
        raise UpdateError(f"Could not apply update (restored previous files): {e}") from e

    manifest_warning = ""
    try:
        _write_manifest(manifest_path, new_version, new_files)
    except OSError as e:
        manifest_warning = str(e)

    for rel in removed:
        _prune_empty_parents(root / rel, root)
    for code_dir in ("terminal", "scripts"):
        base = root / code_dir
        if base.is_dir():
            for cache_dir in base.rglob("__pycache__"):
                if cache_dir.is_dir():
                    shutil.rmtree(cache_dir, ignore_errors=True)

    return {
        "version": new_version,
        "v5_lock_changed": _file_sha256(v5_lock) != pre_v5,
        "v4_lock_changed": _file_sha256(v4_lock) != pre_v4,
        "replaced": len(replaced),
        "added": len(added),
        "removed": len(removed),
        "manifest_warning": manifest_warning,
    }


def sync_pending_path(root: Path = ROOT) -> Path:
    return (local_paths.RUN_DIR / "sync-pending.json") if root == ROOT else (root / "local" / "run" / "sync-pending.json")


def read_sync_pending(root: Path = ROOT) -> dict | None:
    p = sync_pending_path(root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {"version": str(raw.get("version") or ""), "with_v4": bool(raw.get("with_v4", False))}


def write_sync_pending(root: Path, version: str, with_v4: bool) -> None:
    p = sync_pending_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"version": version, "with_v4": bool(with_v4)}, indent=2) + "\n")
        f.flush()
    try:
        local_paths.replace_file(tmp, p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def clear_sync_pending(root: Path = ROOT) -> None:
    try:
        sync_pending_path(root).unlink(missing_ok=True)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                try:
                    code = ctypes.c_ulong()
                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                        return code.value == 259
                finally:
                    kernel32.CloseHandle(handle)
            elif kernel32.GetLastError() == 87:
                return False
        except Exception:  # noqa: BLE001
            pass
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True)
        return f'"{pid}"' in (r.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _acquire_update_lock(lock_path: Path, *, stale_seconds: float = 1800.0) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps({"pid": os.getpid()}).encode("utf-8"))
            finally:
                os.close(fd)
            return
        except FileExistsError:
            if attempt == 1:
                raise UpdateError("Another Libre Terminal update is already running.")
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = stale_seconds + 1
            if age < 2.0:
                time.sleep(0.1)
            pid = 0
            try:
                raw = json.loads(lock_path.read_text(encoding="utf-8"))
                pid = int(raw.get("pid") or 0) if isinstance(raw, dict) else 0
            except (OSError, ValueError):
                pid = 0
            if pid and pid != os.getpid() and _pid_alive(pid) and age < stale_seconds:
                raise UpdateError("Another Libre Terminal update is already running.")
            lock_path.unlink(missing_ok=True)


def _release_update_lock(lock_path: Path) -> None:
    try:
        raw = json.loads(lock_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("pid") == os.getpid():
            lock_path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _find_uv() -> str | None:
    found = shutil.which("uv")
    if found:
        return found
    exe = "uv.exe" if os.name == "nt" else "uv"
    cands = [Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"]
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            cands.append(Path(local_app) / "Programs" / "uv")
    for d in cands:
        if (d / exe).exists():
            return str(d / exe)
    return None


def sync_dependencies(root: Path = ROOT, *, with_v4: bool = False) -> int:
    """Run the platform installer to sync Python environments after lockfile changes."""
    if os.name == "nt":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts" / "install.ps1")]
        if with_v4:
            cmd.append("--with-v4")
        return subprocess.run(cmd, cwd=root).returncode
    uv = _find_uv()
    if not uv:
        return 1
    cmd = [uv, "run", "--no-project", "--python", "3.12", str(root / "scripts" / "launcher.py"), "install"]
    if with_v4:
        cmd.append("--with-v4")
    return subprocess.run(cmd, cwd=root).returncode


def perform_update(
    release: dict,
    *,
    root: Path = ROOT,
    stop_fn=None,
    sync_fn=None,
    work_dir: Path | None = None,
    manifest_path: Path | None = None,
    allow_local_http: bool = False,
    say_fn=print,
) -> dict:
    """Download, verify, stage, stop running services, apply in place, and sync lockfiles if changed."""
    if is_git_checkout(root):
        raise UpdateError("Automatic zip updates are disabled in git checkouts; use `git pull` instead.")
    cur_ver = installed_version(root)
    target_ver = str(release.get("version") or "").strip()
    if not target_ver or not is_newer(target_ver, cur_ver):
        raise UpdateError(f"Release {target_ver or '?'} is not newer than installed {cur_ver}.")

    default_local = local_paths.LOCAL if root == ROOT else (root / "local")
    wdir = work_dir or (default_local / "upd" / str(os.getpid()))
    mpath = manifest_path or ((local_paths.RUN_DIR / "installed-files.json") if root == ROOT else (default_local / "run" / "installed-files.json"))
    lock_path = (local_paths.RUN_DIR / "update.lock") if root == ROOT else (default_local / "run" / "update.lock")

    _acquire_update_lock(lock_path)
    zip_path = wdir / ASSET_NAME
    stage_dir = wdir / "stage"
    backup_dir = wdir / "backup"
    keep_backup = False
    apply_done = False

    try:
        try:
            wdir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise UpdateError(f"Could not prepare temporary update directory: {e}") from e

        say_fn(f"Downloading Libre Terminal {target_ver}...")
        download_release(
            release["download_url"],
            zip_path,
            expected_sha256=release.get("sha256"),
            allow_local_http=allow_local_http,
        )
        say_fn("Verifying update archive...")
        staged = validate_and_stage_zip(
            zip_path,
            stage_dir,
            expected_version=target_ver,
            current_version=cur_ver,
        )
        if stop_fn is not None:
            say_fn("Stopping running terminal services...")
            stop_fn()

        say_fn("Installing new program files...")
        applied = apply_staged_update(
            root,
            stage_dir,
            staged["files"],
            backup_dir=backup_dir,
            manifest_path=mpath,
            new_version=staged["version"],
        )
        apply_done = True

        need_v5 = bool(applied["v5_lock_changed"])
        has_v4 = (root / ".venv-v4").is_dir()
        need_v4 = bool(applied["v4_lock_changed"] and has_v4)
        sync_failed = False
        if need_v5 or need_v4:
            say_fn("Package list changed; updating Python packages (this can take 1 to 4 minutes)...")
            try:
                write_sync_pending(root, staged["version"], need_v4)
            except OSError:
                pass
            runner = sync_fn if sync_fn is not None else (lambda with_v4=False: sync_dependencies(root, with_v4=with_v4))
            try:
                rc = runner(with_v4=need_v4)
            except OSError:
                rc = 1
            if rc != 0:
                sync_failed = True
            else:
                clear_sync_pending(root)

        return {
            "updated": True,
            "version": staged["version"],
            "previous_version": cur_ver,
            "sync_failed": sync_failed,
            **applied,
        }
    except IncompleteRollbackError:
        keep_backup = True
        raise
    except BaseException as exc:
        if not isinstance(exc, UpdateError) and not apply_done and backup_dir.exists():
            keep_backup = True
        raise
    finally:
        if keep_backup:
            zip_path.unlink(missing_ok=True)
            shutil.rmtree(stage_dir, ignore_errors=True)
        else:
            shutil.rmtree(wdir, ignore_errors=True)
            try:
                if wdir.parent.name == "upd" and wdir.parent.is_dir() and not any(wdir.parent.iterdir()):
                    wdir.parent.rmdir()
            except OSError:
                pass
        _release_update_lock(lock_path)


_ANSI_OSC_RX = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_DCS_RX = re.compile(r"\x1b[PX^_][^\x1b]*(?:\x1b\\)?", re.S)
_ANSI_CSI_RX = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ANSI_ESC_RX = re.compile(r"\x1b.?")
_BOILERPLATE_HEAD_RX = re.compile(r"^(?:get it|download|install|upgrading|how to (?:install|update))\b", re.I)
_WHATS_NEW_HEAD_RX = re.compile(r"^(?:what'?s new|what changed|changes|highlights|release notes)\b", re.I)


def _strip_terminal_escapes(text: str) -> str:
    text = _ANSI_OSC_RX.sub("", text)
    text = _ANSI_DCS_RX.sub("", text)
    text = _ANSI_CSI_RX.sub("", text)
    text = _ANSI_ESC_RX.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(ch for ch in text if not _is_unsafe_char(ch, allow_newline=True))


def _clean_md_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    if s.startswith("#"):
        s = re.sub(r"^#{1,6}\s*", "", s).strip()
        return (s.rstrip(":") + ":") if s else ""
    bullet = ""
    m_bul = re.match(r"^[-*]\s+(.*)$", s)
    if m_bul:
        bullet = "- "
        s = m_bul.group(1)
    s = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"(?<!\S)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\S)", r"\1", s)
    return bullet + s.strip()


def _clean_section_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        cleaned = _clean_md_line(raw)
        if not cleaned:
            if out and out[-1] != "":
                out.append("")
        else:
            out.append(cleaned)
    while out and not out[-1]:
        out.pop()
    return out


def format_release_summary(body: str, *, max_lines: int = 12, max_chars: int = 900) -> list[str]:
    """Turn remote GitHub release markdown into safe, plain-text summary lines for the console."""
    if not isinstance(body, str) or not body.strip() or max_lines <= 0 or max_chars <= 0:
        return []
    safe = _strip_terminal_escapes(body)

    sections: list[tuple[str, list[str]]] = []
    cur_head = ""
    cur_lines: list[str] = []
    for line in safe.splitlines():
        if line.startswith("## "):
            sections.append((cur_head, cur_lines))
            cur_head = _clean_md_line(line[3:]).rstrip(":")
            cur_lines = []
        else:
            cur_lines.append(line)
    sections.append((cur_head, cur_lines))

    kept = [(h, _clean_section_lines(lines)) for h, lines in sections if not (h and _BOILERPLATE_HEAD_RX.match(h))]
    wn_idx = next((i for i, (h, _lines) in enumerate(kept) if h and _WHATS_NEW_HEAD_RX.match(h)), None)

    chosen: list[str] = []
    if wn_idx is not None:
        if wn_idx > 0 and kept[0][0] == "" and kept[0][1]:
            intro = " ".join(x for x in kept[0][1] if x)
            if 0 < len(intro) <= 240:
                chosen.append(intro)
        for j in range(wn_idx, len(kept)):
            h, lines = kept[j]
            if not lines:
                continue
            if chosen and chosen[-1] != "":
                chosen.append("")
            if j > wn_idx and h:
                chosen.append(f"{h}:")
            chosen.extend(lines)
    else:
        for h, lines in kept:
            if not lines:
                continue
            if chosen and chosen[-1] != "":
                chosen.append("")
            if h:
                chosen.append(f"{h}:")
            chosen.extend(lines)

    while chosen and not chosen[0]:
        chosen.pop(0)
    while chosen and not chosen[-1]:
        chosen.pop()
    if not chosen:
        return []

    out: list[str] = []
    used = 0
    truncated = False
    already_marked = False
    for line in chosen:
        if len(out) >= max_lines:
            truncated = True
            break
        if used + len(line) > max_chars:
            if not out and max_chars >= 1:
                cut = (line[: max_chars - 3].rstrip() + "...") if max_chars >= 4 else line[:max_chars]
                out.append(cut)
                used = len(cut)
                already_marked = True
            truncated = True
            break
        out.append(line)
        used += len(line)
    if truncated and out and not already_marked:
        if len(out) < max_lines and used + 3 <= max_chars:
            out.append("...")
        elif len(out[-1]) + 3 <= max_chars - (used - len(out[-1])):
            out[-1] = out[-1].rstrip() + "..."
        elif len(out[-1]) >= 4:
            out[-1] = out[-1][:-3].rstrip() + "..."
    return out


def update_prefs_path(root: Path = ROOT) -> Path:
    return (local_paths.STATE_DIR / "update-prefs.json") if root == ROOT else (root / "local" / "state" / "update-prefs.json")


def is_update_check_disabled(root: Path = ROOT) -> bool:
    """Return True when the user previously chose 'No, don't ask me again'."""
    p = update_prefs_path(root)
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return bool(isinstance(raw, dict) and raw.get("disabled", False))
    except (OSError, ValueError):
        return False


def set_update_check_disabled(disabled: bool, root: Path = ROOT) -> None:
    """Persist or clear the 'don't ask again' startup update check preference."""
    p = update_prefs_path(root)
    if not disabled:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"disabled": True}, indent=2) + "\n")
        f.flush()
    try:
        local_paths.replace_file(tmp, p)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def prompt_user_update(
    release: dict,
    current_version: str,
    *,
    input_fn=input,
    say_fn=print,
) -> str:
    """Show what is new and prompt the user: 'yes', 'no' (once), 'disable' (don't ask again), or 'cancel'."""
    say_fn("")
    say_fn(f"A newer version of Libre Terminal is available: {release['version']} (you have {current_version}).")
    summary = format_release_summary(release.get("body") or "")
    if summary:
        say_fn("What's new:")
        for line in summary:
            say_fn(f"  {line}" if line else "")
    if release.get("url"):
        say_fn(f"Full release notes: {release['url']}")
    say_fn("Your saved dashboards, watchlists, and API keys in the local folder will be kept.")
    say_fn("  [Y] Update now")
    say_fn("  [N] Not now (ask again next time)")
    say_fn("  [D] No, don't ask me again")
    while True:
        try:
            ans = input_fn("Choice [Y/n/d]: ")
        except EOFError:
            return "no"
        except KeyboardInterrupt:
            return "cancel"
        norm = (ans or "").strip().lower()
        if norm in ("", "y", "yes"):
            return "yes"
        if norm in ("n", "no"):
            return "no"
        if norm in ("d", "dont", "don't", "never"):
            return "disable"
        say_fn("Please enter Y (update now), N (not now), or D (don't ask again).")
