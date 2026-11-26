"""Offline tests for scripts/updater.py, Start.cmd self-replacement, and launcher update hooks."""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import types
import venv
import zipfile
from pathlib import Path

from _common import ROOT, check, done, make_tmp_dir

import launcher
import updater


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_release_zip(
    path: Path,
    version_str: str = "1.1.0",
    *,
    top_dir: str = "Libre-Terminal",
    extra_files: dict[str, bytes] | None = None,
    drop_required: set[str] | None = None,
    raw_entries: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
    corrupt_crc_for: str | None = None,
) -> tuple[bytes, str]:
    files: dict[str, bytes] = {
        "terminal/version.py": f'"""Version."""\n\nVERSION = "{version_str}"\n'.encode("utf-8"),
        "terminal/server.py": b"# server stub\nVERSION_MARKER = True\n",
        "scripts/launcher.py": b"# launcher stub\n",
        "scripts/updater.py": b"# updater stub\n",
        "Start.cmd": b"@echo off\r\necho started\r\n",
        "config/requirements-v5.lock.txt": b"openbb==5.0.0\n",
        "config/requirements-v4.lock.txt": b"openbb==4.0.0\n",
    }
    for k in (drop_required or set()):
        files.pop(k, None)
    if extra_files:
        files.update(extra_files)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            arc = f"{top_dir}/{rel}" if top_dir else rel
            zf.writestr(arc, content)
        for info_or_name, content in (raw_entries or []):
            zf.writestr(info_or_name, content)
    raw = buf.getvalue()
    if corrupt_crc_for:
        # Flip one byte inside the local/central CRC of the target entry or compressed payload
        idx = raw.find(corrupt_crc_for.encode("utf-8"))
        if idx > 16:
            ba = bytearray(raw)
            ba[idx - 10] ^= 0xFF
            raw = bytes(ba)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw, _sha256(raw)


# ---- 1. Version parsing and comparisons
check(updater.parse_version("1.0.0") == (1, 0, 0), "parse_version handles plain semver")
check(updater.parse_version("v1.0.1") == (1, 0, 1), "parse_version strips lowercase v")
check(updater.parse_version("V1.10.2") == (1, 10, 2), "parse_version strips uppercase V")
check(updater.parse_version("1.1") == (1, 1, 0), "parse_version pads 2-part version to 3 parts")
check(updater.is_newer("1.10.0", "1.2.0"), "numeric comparison: 1.10.0 > 1.2.0")
check(updater.is_newer("1.1.1", "1.1"), "1.1.1 > 1.1")
check(not updater.is_newer("1.1.0", "1.1"), "1.1.0 == 1.1 is not newer")
check(not updater.is_newer("1.0.0", "1.0.1"), "downgrade is not newer")
for bad_v in ("", "1.2.0-rc1", "1.2.0a1", "1.2.0+build1", "v1.2.3.4", "latest", "1.x.0"):
    bad_raised = False
    try:
        updater.parse_version(bad_v)
    except updater.UpdateError:
        bad_raised = True
    check(bad_raised, f"parse_version rejects invalid/prerelease tag {bad_v!r}")
check(not updater.is_newer("1.2.0-rc1", "1.0.0"), "is_newer returns False on prerelease tag")

# ---- 2. URL host and scheme validation
for good_u in (
    "https://api.github.com/repos/samlazs/libre-terminal/releases/latest",
    "https://github.com/samlazs/libre-terminal/releases/download/v1.0.0/Libre-Terminal-Windows.zip",
    "https://release-assets.githubusercontent.com/github-production-release-asset/123/abc",
    "https://objects.githubusercontent.com/github-production-release-asset-2e65be/abc",
):
    check(updater.validate_url(good_u) == good_u, f"validate_url accepts {good_u.split('/')[2]}")

for bad_u in (
    "http://github.com/samlazs/libre-terminal/releases/latest",
    "https://evil.example/Libre-Terminal-Windows.zip",
    "https://evil-githubusercontent.com/file.zip",
    "https://github.com.evil.example/file.zip",
    "https://user:pass@github.com/samlazs/libre-terminal/releases/latest",
    "file:///C:/Windows/System32/calc.exe",
    "https://127.0.0.1:8443/x.zip",
    "https://github.com/samlazs/libre-terminal\u202egnp.exe",
    "https://github.com/samlazs/libre-\u200bterminal",
    "https://github.com/samlazs/libre\x7fterminal",
    "https://github.com/samlazs/libre\x9bterminal",
):
    bad_url_raised = False
    try:
        updater.validate_url(bad_u)
    except updater.UpdateError:
        bad_url_raised = True
    check(bad_url_raised, f"validate_url rejects {bad_u}")

check(updater.validate_url("http://127.0.0.1:1234/api", allow_local_http=True).startswith("http://127.0.0.1:"),
      "validate_url allows loopback http only when allow_local_http=True")
bad_ext_under_local = False
try:
    updater.validate_url("http://evil.example/file.zip", allow_local_http=True)
except updater.UpdateError:
    bad_ext_under_local = True
check(bad_ext_under_local, "validate_url still rejects non-loopback http even when allow_local_http=True")

# ---- 3. Local HTTP test server for check, rate-limit fallback, timeout, redirects, sha256, max_bytes
srv_dir = make_tmp_dir("upd-http-")
zip_110_bytes, zip_110_sha = _make_release_zip(srv_dir / "v1.1.0.zip", "1.1.0")


class _ThreadSrv(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


_STATE = {
    "api_status": 200,
    "api_body": {},
    "web_location": "",
    "asset_redirect": "",
    "asset_bytes": zip_110_bytes,
    "delay": 0.0,
}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self._serve(head_only=True)

    def do_GET(self):
        self._serve(head_only=False)

    def _serve(self, head_only: bool):
        if _STATE["delay"] > 0:
            time.sleep(_STATE["delay"])
        if self.path == "/api/latest":
            st = _STATE["api_status"]
            body = json.dumps(_STATE["api_body"]).encode("utf-8") if isinstance(_STATE["api_body"], dict) else _STATE["api_body"]
            self.send_response(st)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return
        if self.path == "/web/latest":
            if _STATE["web_location"]:
                self.send_response(302)
                self.send_header("Location", _STATE["web_location"])
                self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
            return
        if self.path == "/download/v1.1.0.zip":
            if _STATE["asset_redirect"]:
                self.send_response(302)
                self.send_header("Location", _STATE["asset_redirect"])
                self.end_headers()
                return
            data = _STATE["asset_bytes"]
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
            return
        if self.path == "/cdn/Libre-Terminal-Windows.zip":
            data = _STATE["asset_bytes"]
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_a):
        pass


httpd = _ThreadSrv(("127.0.0.1", 0), _Handler)
hport = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{hport}"

try:
    # 3a. Up to date -> None
    _STATE["api_status"] = 200
    _STATE["api_body"] = {
        "tag_name": "v1.0.0",
        "name": "Libre Terminal 1.0",
        "draft": False,
        "prerelease": False,
        "html_url": f"{base}/releases/tag/v1.0.0",
        "published_at": "2026-11-25T20:31:35Z",
        "assets": [{"name": updater.ASSET_NAME, "size": len(zip_110_bytes), "digest": f"sha256:{zip_110_sha}", "browser_download_url": f"{base}/download/v1.1.0.zip"}],
    }
    check(updater.check_for_update(current="1.0.0", api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", allow_local_http=True) is None,
          "check_for_update returns None when current == remote")

    # 3b. Newer release with digest -> returns dict
    _STATE["api_body"] = {
        "tag_name": "v1.1.0",
        "name": "Libre Terminal 1.1",
        "draft": False,
        "prerelease": False,
        "html_url": f"{base}/releases/tag/v1.1.0",
        "published_at": "2026-11-26T12:00:00Z",
        "assets": [{"name": updater.ASSET_NAME, "size": len(zip_110_bytes), "digest": f"sha256:{zip_110_sha}", "browser_download_url": f"{base}/download/v1.1.0.zip"}],
    }
    rel = updater.check_for_update(current="1.0.0", api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", allow_local_http=True)
    check(rel is not None and rel["version"] == "1.1.0" and rel["sha256"] == zip_110_sha and rel["download_url"] == f"{base}/download/v1.1.0.zip",
          "check_for_update returns structured release info when newer release exists")

    # 3c. Draft and prerelease ignored
    for flag in ("draft", "prerelease"):
        _STATE["api_body"][flag] = True
        check(updater.check_for_update(current="1.0.0", api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", quiet=True, allow_local_http=True) is None,
              f"{flag} release ignored by check_for_update")
        _STATE["api_body"][flag] = False

    # 3d. Missing asset or malformed digest rejected
    _STATE["api_body"]["assets"] = [{"name": "other.zip", "browser_download_url": f"{base}/download/other.zip"}]
    check(updater.check_for_update(current="1.0.0", api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", quiet=True, allow_local_http=True) is None,
          "release without Libre-Terminal-Windows.zip rejected")
    _STATE["api_body"]["assets"] = [{"name": updater.ASSET_NAME, "digest": "sha256:nothex", "browser_download_url": f"{base}/download/v1.1.0.zip"}]
    bad_dig = False
    try:
        updater.fetch_latest_release(api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", allow_local_http=True)
    except updater.UpdateError:
        bad_dig = True
    check(bad_dig, "malformed sha256 digest raises UpdateError")

    # 3e. Non-rate-limit 403 raises UpdateError; rate-limit 403 falls back to web_url redirect
    _STATE["api_status"] = 403
    _STATE["api_body"] = {"message": "Forbidden"}
    non_rl_403 = False
    try:
        updater.fetch_latest_release(api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", allow_local_http=True)
    except updater.UpdateError:
        non_rl_403 = True
    check(non_rl_403, "non-rate-limit HTTP 403 raises UpdateError instead of dropping digest")

    _STATE["api_body"] = {"message": "API rate limit exceeded"}
    _STATE["web_location"] = f"{base}/samlazs/libre-terminal/releases/tag/v1.1.0"
    rel_fb = updater.check_for_update(current="1.0.0", api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", quiet=False, allow_local_http=True)
    check(rel_fb is not None and rel_fb["version"] == "1.1.0" and rel_fb["sha256"] is None and "/releases/download/v1.1.0/Libre-Terminal-Windows.zip" in rel_fb["download_url"],
          "HTTP 403 rate limit falls back to web /releases/latest redirect")

    # 3f. Timeout & connection refused fail fast and return None in quiet mode
    _STATE["api_status"] = 200
    _STATE["delay"] = 0.6
    t0 = time.monotonic()
    res_to = updater.check_for_update(current="1.0.0", api_url=f"{base}/api/latest", web_url=f"{base}/web/latest", timeout=0.15, quiet=True, allow_local_http=True)
    dt_to = time.monotonic() - t0
    check(res_to is None and dt_to < 1.0, f"timeout returns None quickly ({dt_to:.2f} s)")
    _STATE["delay"] = 0.0
    res_ref = updater.check_for_update(current="1.0.0", api_url="http://127.0.0.1:1/api/latest", web_url="http://127.0.0.1:1/web/latest", timeout=0.5, quiet=True, allow_local_http=True)
    check(res_ref is None, "connection refused returns None in quiet mode")

    web_err_wrapped = False
    try:
        updater._fetch_latest_via_web("http://127.0.0.1:1/web/latest", timeout=0.5, allow_local_http=True)
    except updater.UpdateError:
        web_err_wrapped = True
    check(web_err_wrapped, "_fetch_latest_via_web wraps URLError in UpdateError")

    # 3g. Download with allowed redirect + matching SHA-256 succeeds
    _STATE["asset_redirect"] = f"{base}/cdn/Libre-Terminal-Windows.zip"
    dl_ok = srv_dir / "dl_ok.zip"
    got_sha = updater.download_release(f"{base}/download/v1.1.0.zip", dl_ok, expected_sha256=zip_110_sha, allow_local_http=True)
    check(got_sha == zip_110_sha and dl_ok.read_bytes() == zip_110_bytes, "download follows allowed redirect and verifies SHA-256")

    # 3h. Download with redirect to disallowed host rejected before download
    _STATE["asset_redirect"] = "https://evil.example/payload.zip"
    dl_bad_redir = srv_dir / "dl_bad_redir.zip"
    redir_rejected = False
    try:
        updater.download_release(f"{base}/download/v1.1.0.zip", dl_bad_redir, allow_local_http=True)
    except updater.UpdateError:
        redir_rejected = True
    check(redir_rejected and not dl_bad_redir.exists(), "disallowed redirect target rejected and leaves no file")
    _STATE["asset_redirect"] = ""

    # 3i. SHA-256 mismatch and max_bytes limit fail and delete temp file
    dl_bad_sha = srv_dir / "dl_bad_sha.zip"
    sha_rejected = False
    try:
        updater.download_release(f"{base}/download/v1.1.0.zip", dl_bad_sha, expected_sha256="0" * 64, allow_local_http=True)
    except updater.UpdateError:
        sha_rejected = True
    check(sha_rejected and not dl_bad_sha.exists(), "SHA-256 mismatch raises UpdateError and removes temp file")

    dl_big = srv_dir / "dl_big.zip"
    big_rejected = False
    try:
        updater.download_release(f"{base}/download/v1.1.0.zip", dl_big, max_bytes=128, allow_local_http=True)
    except updater.UpdateError:
        big_rejected = True
    check(big_rejected and not dl_big.exists(), "oversize download raises UpdateError and removes temp file")
finally:
    httpd.shutdown()
    httpd.server_close()

# ---- 4. Zip validation & staging
zip_dir = make_tmp_dir("upd-zip-")
sym_info = zipfile.ZipInfo("Libre-Terminal/scripts/link.py")
sym_info.external_attr = 0o120777 << 16

bad_zip_cases = [
    ("zip-slip", {"raw_entries": [("Libre-Terminal/../../evil.txt", b"pwn")]}),
    ("backslash-traversal", {"raw_entries": [("Libre-Terminal\\..\\evil.txt", b"pwn")]}),
    ("absolute-drive", {"raw_entries": [("Libre-Terminal/C:/evil.txt", b"pwn")]}),
    ("leading-slash", {"raw_entries": [("/Libre-Terminal/evil.txt", b"pwn")]}),
    ("symlink", {"raw_entries": [(sym_info, b"/etc/passwd")]}),
    ("win-reserved-con", {"extra_files": {"terminal/CON.txt": b"x"}}),
    ("win-reserved-nul", {"extra_files": {"NUL": b"x"}}),
    ("win-dot-bypass", {"extra_files": {"local./keys.env": b"x"}}),
    ("win-git-dot-bypass", {"extra_files": {".git./config": b"x"}}),
    ("win-83-tilde-git", {"extra_files": {"GIT~1/HEAD": b"x"}}),
    ("win-83-tilde-venv", {"extra_files": {"VENV-V~1/x.py": b"x"}}),
    ("win-trailing-space", {"extra_files": {"Start.cmd ": b"x"}}),
    ("win-trailing-dot", {"extra_files": {"terminal/foo.": b"x"}}),
    ("bad-utf8-version", {"extra_files": {"terminal/version.py": b"\xff\xfe"}}),
    ("protected-local", {"extra_files": {"local/state/state.json": b"{}"}}),
    ("protected-venv", {"extra_files": {".venv-v5/Scripts/python.exe": b"bad"}}),
    ("protected-venv-custom", {"extra_files": {".venv-custom/x.py": b"bad"}}),
    ("protected-git", {"extra_files": {".git/HEAD": b"ref: refs/heads/main"}}),
    ("protected-mcp", {"extra_files": {".mcp.json": b"{}"}}),
    ("protected-dir", {"extra_files": {"." + "cla" + "ude/x.md": b"x"}}),
    ("protected-agents", {"extra_files": {"AGENTS.md": b"x"}}),
    ("wrong-top-dir", {"top_dir": "WrongTop"}),
    ("missing-required", {"drop_required": {"Start.cmd"}}),
    ("bad-crc", {"extra_files": {"terminal/unique_crc_target.py": b"payload" * 50}, "corrupt_crc_for": "unique_crc_target.py"}),
    ("same-version", {"version_str": "1.0.0"}),
    ("downgrade-version", {"version_str": "0.9.0"}),
]

for label, kwargs in bad_zip_cases:
    zp = zip_dir / f"{label}.zip"
    _make_release_zip(zp, **kwargs)
    st_dir = zip_dir / f"stage-{label}"
    rejected = False
    try:
        updater.validate_and_stage_zip(zp, st_dir, expected_version=None, current_version="1.0.0")
    except updater.UpdateError:
        rejected = True
    check(rejected, f"validate_and_stage_zip rejects {label}")

# Mismatched expected_version vs tag rejected
zp_mis = zip_dir / "mismatch.zip"
_make_release_zip(zp_mis, "1.1.0")
mis_rejected = False
try:
    updater.validate_and_stage_zip(zp_mis, zip_dir / "stage-mis", expected_version="1.2.0", current_version="1.0.0")
except updater.UpdateError:
    mis_rejected = True
check(mis_rejected, "validate_and_stage_zip rejects version mismatch with release tag")

# ---- 5. In-place apply, manifest removal, lockfile sync rules, and rollback
app_dir = make_tmp_dir("upd-app-")
(app_dir / "terminal").mkdir()
(app_dir / "scripts").mkdir()
(app_dir / "config").mkdir()
(app_dir / "local" / "state").mkdir(parents=True)
(app_dir / ".venv-v5").mkdir()

(app_dir / "terminal" / "version.py").write_text('"""Version."""\n\nVERSION = "1.0.0"\n', encoding="utf-8")
(app_dir / "terminal" / "server.py").write_text("# old server\n", encoding="utf-8")
(app_dir / "terminal" / "old_mod.py").write_text("# to be removed\n", encoding="utf-8")
(app_dir / "scripts" / "launcher.py").write_text("# old launcher\n", encoding="utf-8")
(app_dir / "Start.cmd").write_bytes(b"@echo off\r\necho old\r\n")
(app_dir / "config" / "requirements-v5.lock.txt").write_bytes(b"openbb==5.0.0\n")
(app_dir / "config" / "requirements-v4.lock.txt").write_bytes(b"openbb==4.0.0\n")
(app_dir / "user_note.txt").write_text("user personal note\n", encoding="utf-8")
state_bytes = b'{"v": 4, "dashboards": {"user:1": {}}, "_rev": 9}'
keys_bytes = b"FRED_API_KEY=secret123\n"
venv_bytes = b"keep-venv-intact\n"
(app_dir / "local" / "state" / "state.json").write_bytes(state_bytes)
(app_dir / "local" / "api_keys.env").write_bytes(keys_bytes)
(app_dir / ".venv-v5" / "sentinel.txt").write_bytes(venv_bytes)

manifest_path = app_dir / "local" / "run" / "installed-files.json"
manifest_path.parent.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(
    json.dumps({"version": "1.0.0", "files": ["terminal/version.py", "terminal/server.py", "terminal/old_mod.py", "scripts/launcher.py", "Start.cmd", "config/requirements-v5.lock.txt", "config/requirements-v4.lock.txt"]}),
    encoding="utf-8",
)

# Serve 1.1.0 zip (same lockfiles, drops old_mod.py, adds terminal/new_mod.py) and 1.2.0 zip (changes v5 lockfile)
zip_110_app, sha_110_app = _make_release_zip(
    zip_dir / "rel-110.zip",
    "1.1.0",
    extra_files={"terminal/new_mod.py": b"# new module\n"},
)
zip_120_app, sha_120_app = _make_release_zip(
    zip_dir / "rel-120.zip",
    "1.2.0",
    extra_files={
        "terminal/new_mod.py": b"# v1.2\n",
        "config/requirements-v5.lock.txt": b"openbb==5.1.0\n",
    },
)
zip_130_app, sha_130_app = _make_release_zip(
    zip_dir / "rel-130.zip",
    "1.3.0",
    extra_files={
        "terminal/new_mod.py": b"# v1.3\n",
        "config/requirements-v5.lock.txt": b"openbb==5.1.0\n",
        "config/requirements-v4.lock.txt": b"openbb==4.1.0\n",
    },
)


class _RelHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/110.zip":
            data = zip_110_app
        elif self.path == "/120.zip":
            data = zip_120_app
        elif self.path == "/130.zip":
            data = zip_130_app
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_a):
        pass


httpd2 = _ThreadSrv(("127.0.0.1", 0), _RelHandler)
rport = httpd2.server_address[1]
threading.Thread(target=httpd2.serve_forever, daemon=True).start()
rbase = f"http://127.0.0.1:{rport}"

try:
    events: list[str] = []

    def _stop_cb():
        # Verify staging already happened before stop_fn runs
        events.append("stop")

    sync_calls: list[bool] = []

    def _sync_cb(with_v4: bool) -> int:
        sync_calls.append(with_v4)
        return 0

    work1 = make_tmp_dir("work-110-")
    orig_validate = updater.validate_and_stage_zip

    def _tracked_validate(*a, **kw):
        events.append("stage")
        return orig_validate(*a, **kw)

    updater.validate_and_stage_zip = _tracked_validate
    try:
        res110 = updater.perform_update(
            {"version": "1.1.0", "tag": "v1.1.0", "download_url": f"{rbase}/110.zip", "sha256": sha_110_app},
            root=app_dir,
            stop_fn=_stop_cb,
            sync_fn=_sync_cb,
            work_dir=work1,
            manifest_path=manifest_path,
            allow_local_http=True,
            say_fn=lambda _m="": None,
        )
    finally:
        updater.validate_and_stage_zip = orig_validate

    check(events == ["stage", "stop"], f"stop_fn ran AFTER staging ({events})")
    check(res110["updated"] and res110["version"] == "1.1.0" and not res110["sync_failed"], "perform_update 1.1.0 succeeded")
    check(updater.installed_version(app_dir) == "1.1.0", "installed_version updated to 1.1.0")
    check((app_dir / "terminal" / "new_mod.py").is_file() and not (app_dir / "terminal" / "old_mod.py").exists(),
          "added new_mod.py and removed dropped manifest file old_mod.py")
    check((app_dir / "user_note.txt").read_text(encoding="utf-8") == "user personal note\n", "untracked user file preserved")
    check((app_dir / "local" / "state" / "state.json").read_bytes() == state_bytes, "local/state/state.json preserved byte-for-byte")
    check((app_dir / "local" / "api_keys.env").read_bytes() == keys_bytes, "local/api_keys.env preserved byte-for-byte")
    check((app_dir / ".venv-v5" / "sentinel.txt").read_bytes() == venv_bytes, ".venv-v5 preserved byte-for-byte")
    check(sync_calls == [], "sync_fn NOT called when lockfiles are unchanged")
    check(not work1.exists(), "staging work_dir cleaned up after update")
    man110 = json.loads(manifest_path.read_text(encoding="utf-8"))
    check(man110["version"] == "1.1.0" and "terminal/new_mod.py" in man110["files"] and "terminal/old_mod.py" not in man110["files"],
          "manifest updated accurately")

    # 5b. Lockfile change triggers sync_fn(with_v4=False); v4 lockfile change with .venv-v4 triggers sync_fn(with_v4=True)
    res120 = updater.perform_update(
        {"version": "1.2.0", "tag": "v1.2.0", "download_url": f"{rbase}/120.zip", "sha256": sha_120_app},
        root=app_dir,
        stop_fn=lambda: None,
        sync_fn=_sync_cb,
        work_dir=make_tmp_dir("work-120-"),
        manifest_path=manifest_path,
        allow_local_http=True,
        say_fn=lambda _m="": None,
    )
    check(res120["updated"] and sync_calls == [False], "v5 lockfile change calls sync_fn(with_v4=False)")

    (app_dir / ".venv-v4").mkdir()
    res130 = updater.perform_update(
        {"version": "1.3.0", "tag": "v1.3.0", "download_url": f"{rbase}/130.zip", "sha256": sha_130_app},
        root=app_dir,
        stop_fn=lambda: None,
        sync_fn=_sync_cb,
        work_dir=make_tmp_dir("work-130-"),
        manifest_path=manifest_path,
        allow_local_http=True,
        say_fn=lambda _m="": None,
    )
    check(res130["updated"] and sync_calls == [False, True], "v4 lockfile change with .venv-v4 calls sync_fn(with_v4=True)")

    # 5c. Rollback on mid-apply PermissionError
    pre_snap = {
        rel: (app_dir / rel).read_bytes()
        for rel in ("terminal/version.py", "terminal/server.py", "scripts/launcher.py", "Start.cmd", "config/requirements-v5.lock.txt")
    }
    zip_140_app, sha_140_app = _make_release_zip(
        zip_dir / "rel-140.zip",
        "1.4.0",
        extra_files={"terminal/added_then_rolled_back.py": b"# rollback me\n"},
    )
    orig_install = updater._install_file
    call_n = [0]

    def _boom_install(src: Path, dst: Path):
        call_n[0] += 1
        if call_n[0] == 3:
            raise PermissionError(5, "locked by test", str(dst))
        return orig_install(src, dst)

    # Serve 1.4.0 via a tiny one-off handler or monkeypatch download_release
    orig_dl = updater.download_release
    updater._install_file = _boom_install
    updater.download_release = lambda _u, dst, **_kw: shutil.copy2(zip_dir / "rel-140.zip", dst) or sha_140_app
    rb_raised = False
    try:
        updater.perform_update(
            {"version": "1.4.0", "tag": "v1.4.0", "download_url": f"{rbase}/140.zip", "sha256": sha_140_app},
            root=app_dir,
            stop_fn=lambda: None,
            sync_fn=_sync_cb,
            work_dir=make_tmp_dir("work-140-"),
            manifest_path=manifest_path,
            allow_local_http=True,
            say_fn=lambda _m="": None,
        )
    except updater.UpdateError:
        rb_raised = True
    finally:
        updater._install_file = orig_install
        updater.download_release = orig_dl

    check(rb_raised, "mid-apply PermissionError raises UpdateError")
    for rel, expected_b in pre_snap.items():
        check((app_dir / rel).read_bytes() == expected_b, f"rollback restored {rel}")
    check(not (app_dir / "terminal" / "added_then_rolled_back.py").exists(), "rollback removed newly added file")
    check((app_dir / "local" / "state" / "state.json").read_bytes() == state_bytes, "rollback kept local/ intact")

    # 5d. KeyboardInterrupt during apply triggers clean rollback; incomplete rollback keeps backup_dir
    orig_install = updater._install_file
    orig_dl = updater.download_release
    call_n[0] = 0

    def _kb_install(src: Path, dst: Path):
        call_n[0] += 1
        res = orig_install(src, dst)
        if call_n[0] == 3:
            raise KeyboardInterrupt
        return res

    updater._install_file = _kb_install
    updater.download_release = lambda _u, dst, **_kw: shutil.copy2(zip_dir / "rel-140.zip", dst) or sha_140_app
    kb_work = make_tmp_dir("work-kb-")
    kb_caught = False
    try:
        updater.perform_update(
            {"version": "1.4.0", "tag": "v1.4.0", "download_url": f"{rbase}/140.zip", "sha256": sha_140_app},
            root=app_dir,
            stop_fn=lambda: None,
            sync_fn=_sync_cb,
            work_dir=kb_work,
            manifest_path=manifest_path,
            allow_local_http=True,
            say_fn=lambda _m="": None,
        )
    except updater.UpdateError:
        kb_caught = True
    finally:
        updater._install_file = orig_install
        updater.download_release = orig_dl
    check(kb_caught and not kb_work.exists(), "KeyboardInterrupt triggers clean rollback and cleans up work_dir")
    for rel, expected_b in pre_snap.items():
        check((app_dir / rel).read_bytes() == expected_b, f"KeyboardInterrupt rollback restored {rel}")

    call_n[0] = 0
    rb_fail_work = make_tmp_dir("work-rbfail-")

    def _rb_fail_install(src: Path, dst: Path):
        call_n[0] += 1
        if call_n[0] == 3 or str(src).startswith(str(rb_fail_work / "backup")):
            raise PermissionError(5, "locked during apply and rollback", str(dst))
        return orig_install(src, dst)

    updater._install_file = _rb_fail_install
    updater.download_release = lambda _u, dst, **_kw: shutil.copy2(zip_dir / "rel-140.zip", dst) or sha_140_app
    inc_err = None
    try:
        updater.perform_update(
            {"version": "1.4.0", "tag": "v1.4.0", "download_url": f"{rbase}/140.zip", "sha256": sha_140_app},
            root=app_dir,
            stop_fn=lambda: None,
            sync_fn=_sync_cb,
            work_dir=rb_fail_work,
            manifest_path=manifest_path,
            allow_local_http=True,
            say_fn=lambda _m="": None,
        )
    except updater.IncompleteRollbackError as e:
        inc_err = e
    finally:
        updater._install_file = orig_install
        updater.download_release = orig_dl
        # Restore pre_snap manually for remaining tests on app_dir
        for rel, expected_b in pre_snap.items():
            (app_dir / rel).write_bytes(expected_b)
        (app_dir / "terminal" / "added_then_rolled_back.py").unlink(missing_ok=True)
    check(inc_err is not None and (rb_fail_work / "backup").is_dir() and not (rb_fail_work / "stage").exists(),
          "IncompleteRollbackError preserves backup_dir while cleaning stage/zip")

    # 5e. Corrupt zip (non-UpdateError RuntimeError/BadZipFile) and manifest OSError handling
    bad_zip_file = zip_dir / "not-a-zip.zip"
    bad_zip_file.write_bytes(b"not a zip file")
    bad_zip_wrapped = False
    try:
        updater.validate_and_stage_zip(bad_zip_file, zip_dir / "stage-notzip")
    except updater.UpdateError:
        bad_zip_wrapped = True
    check(bad_zip_wrapped, "corrupt zip raises UpdateError")

    orig_write_man = updater._write_manifest
    updater._write_manifest = lambda *_a: (_ for _ in ()).throw(PermissionError("locked manifest"))
    try:
        res_man_warn = updater.apply_staged_update(
            app_dir,
            zip_dir / "stage-ok-man" if (zip_dir / "stage-ok-man").exists() else (lambda sd: updater.validate_and_stage_zip(zip_dir / "rel-130.zip", sd) and sd)(zip_dir / "stage-ok-man"),
            ["terminal/version.py", "terminal/server.py", "scripts/launcher.py", "Start.cmd", "config/requirements-v5.lock.txt", "config/requirements-v4.lock.txt", "terminal/new_mod.py"],
            backup_dir=make_tmp_dir("bak-man-"),
            manifest_path=manifest_path,
            new_version="1.3.0",
        )
    finally:
        updater._write_manifest = orig_write_man
    check(res_man_warn["version"] == "1.3.0" and "locked manifest" in res_man_warn["manifest_warning"],
          "manifest OSError after apply succeeds with manifest_warning instead of crashing")

    # 5f. Failed sync writes sync-pending.json; successful sync clears it
    (app_dir / "config" / "requirements-v5.lock.txt").write_bytes(b"openbb==5.0.0\n")
    (app_dir / "terminal" / "version.py").write_text('"""Version."""\n\nVERSION = "1.0.0"\n', encoding="utf-8")
    updater.download_release = lambda _u, dst, **_kw: shutil.copy2(zip_dir / "rel-120.zip", dst) or sha_120_app
    try:
        res_sync_fail = updater.perform_update(
            {"version": "1.2.0", "tag": "v1.2.0", "download_url": f"{rbase}/120.zip", "sha256": sha_120_app},
            root=app_dir,
            stop_fn=lambda: None,
            sync_fn=lambda with_v4=False: 1,
            work_dir=make_tmp_dir("work-syncfail-"),
            manifest_path=manifest_path,
            allow_local_http=True,
            say_fn=lambda _m="": None,
        )
        pending = updater.read_sync_pending(app_dir)
        check(res_sync_fail["sync_failed"] and pending is not None and pending["version"] == "1.2.0",
              "failed package sync records sync-pending.json")
        (app_dir / "terminal" / "version.py").write_text('"""Version."""\n\nVERSION = "1.0.0"\n', encoding="utf-8")
        (app_dir / "config" / "requirements-v5.lock.txt").write_bytes(b"openbb==5.0.0\n")
        updater.download_release = lambda _u, dst, **_kw: shutil.copy2(zip_dir / "rel-130.zip", dst) or sha_130_app
        res_sync_ok = updater.perform_update(
            {"version": "1.3.0", "tag": "v1.3.0", "download_url": f"{rbase}/130.zip", "sha256": sha_130_app},
            root=app_dir,
            stop_fn=lambda: None,
            sync_fn=lambda with_v4=False: 0,
            work_dir=make_tmp_dir("work-syncok-"),
            manifest_path=manifest_path,
            allow_local_http=True,
            say_fn=lambda _m="": None,
        )
        check(not res_sync_ok["sync_failed"] and updater.read_sync_pending(app_dir) is None,
              "successful package sync clears sync-pending.json")

        # KeyboardInterrupt mid-sync preserves sync-pending.json and cleans up work_dir
        (app_dir / "terminal" / "version.py").write_text('"""Version."""\n\nVERSION = "1.0.0"\n', encoding="utf-8")
        (app_dir / "config" / "requirements-v5.lock.txt").write_bytes(b"openbb==5.0.0\n")
        sync_kb_work = make_tmp_dir("work-synckb-")
        sync_kb_caught = False
        try:
            updater.perform_update(
                {"version": "1.3.0", "tag": "v1.3.0", "download_url": f"{rbase}/130.zip", "sha256": sha_130_app},
                root=app_dir,
                stop_fn=lambda: None,
                sync_fn=lambda with_v4=False: (_ for _ in ()).throw(KeyboardInterrupt()),
                work_dir=sync_kb_work,
                manifest_path=manifest_path,
                allow_local_http=True,
                say_fn=lambda _m="": None,
            )
        except KeyboardInterrupt:
            sync_kb_caught = True
        p_kb = updater.read_sync_pending(app_dir)
        check(sync_kb_caught and p_kb is not None and p_kb["version"] == "1.3.0" and not sync_kb_work.exists(),
              "KeyboardInterrupt during sync preserves sync-pending.json and cleans work_dir")
        updater.clear_sync_pending(app_dir)

        # 5g. Concurrent update lock rejects active PID and cleans stale lock
        lock_file = app_dir / "local" / "run" / "update.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            lock_file.write_text(json.dumps({"pid": other.pid}), encoding="utf-8")
            (app_dir / "terminal" / "version.py").write_text('"""Version."""\n\nVERSION = "1.0.0"\n', encoding="utf-8")
            lock_blocked = False
            try:
                updater.perform_update(
                    {"version": "1.3.0", "tag": "v1.3.0", "download_url": f"{rbase}/130.zip", "sha256": sha_130_app},
                    root=app_dir,
                    stop_fn=lambda: None,
                    sync_fn=lambda with_v4=False: 0,
                    work_dir=make_tmp_dir("work-lock-"),
                    manifest_path=manifest_path,
                    allow_local_http=True,
                    say_fn=lambda _m="": None,
                )
            except updater.UpdateError as e:
                lock_blocked = "already running" in str(e)
            check(lock_blocked, "active update.lock rejects second concurrent update")
        finally:
            other.kill()
            other.wait(timeout=5)
        # Dead PID lock is automatically replaced
        res_stale_lock = updater.perform_update(
            {"version": "1.3.0", "tag": "v1.3.0", "download_url": f"{rbase}/130.zip", "sha256": sha_130_app},
            root=app_dir,
            stop_fn=lambda: None,
            sync_fn=lambda with_v4=False: 0,
            work_dir=make_tmp_dir("work-stalelock-"),
            manifest_path=manifest_path,
            allow_local_http=True,
            say_fn=lambda _m="": None,
        )
        check(res_stale_lock["updated"] and not lock_file.exists(), "stale dead-PID update.lock is recovered and released")
    finally:
        updater.download_release = orig_dl
finally:
    httpd2.shutdown()
    httpd2.server_close()

# ---- 6. Git checkout guard, startup flags/env, and prompt behavior
git_dir_repo = make_tmp_dir("git-dir-")
(git_dir_repo / ".git").mkdir()
check(updater.is_git_checkout(git_dir_repo), "is_git_checkout detects .git directory")

git_wt_repo = make_tmp_dir("git-wt-")
(git_wt_repo / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n", encoding="utf-8")
check(updater.is_git_checkout(git_wt_repo), "is_git_checkout detects .git worktree file")

git_refused = False
try:
    updater.perform_update({"version": "1.1.0", "tag": "v1.1.0", "download_url": "https://github.com/x/y/releases/download/v1.1.0/Libre-Terminal-Windows.zip"}, root=git_dir_repo)
except updater.UpdateError:
    git_refused = True
check(git_refused, "perform_update refuses git checkout")

check(updater.prompt_user_update({"version": "1.1.0", "url": ""}, "1.0.0", input_fn=lambda _p: "n", say_fn=lambda _m="": None) == "no", "prompt 'n' returns 'no'")
check(updater.prompt_user_update({"version": "1.1.0", "url": ""}, "1.0.0", input_fn=lambda _p: "", say_fn=lambda _m="": None) == "yes", "prompt Enter returns 'yes'")
check(updater.prompt_user_update({"version": "1.1.0", "url": ""}, "1.0.0", input_fn=lambda _p: "Y", say_fn=lambda _m="": None) == "yes", "prompt 'Y' returns 'yes'")


def _eof(_p):
    raise EOFError


check(updater.prompt_user_update({"version": "1.1.0", "url": ""}, "1.0.0", input_fn=_eof, say_fn=lambda _m="": None) == "no", "prompt EOF returns 'no'")

# 6b. format_release_summary, release description display, and "don't ask again" preference
sample_body = (
    "A small update that fixes startup prompts.\n\n"
    "## Get it (Windows 10 / 11)\n\n"
    "1. Extract the zip.\n2. Double-click `Install.cmd`.\n\n"
    "## What changed\n\n"
    "- Added **auto-update** check via [OpenBB](https://example.invalid/openbb).\n"
    "- \x1b[31mRed text\x1b[0m and \x1b]0;evil-title\x07OSC stripped cleanly.\n"
    "- \x1bPignored-dcs\x1b\\Safe\u202egnp.exe\u200b line.\n"
    "- Keeps `local/` data intact.\n"
)
summary_lines = updater.format_release_summary(sample_body)
summary_text = "\n".join(summary_lines)
check("A small update that fixes startup prompts." in summary_lines[0], "format_release_summary keeps short intro")
check("Extract the zip." not in summary_text and "Get it" not in summary_text, "format_release_summary drops install boilerplate")
check("- Added auto-update check via OpenBB." in summary_text, "format_release_summary strips markdown links and bold")
check("\x1b" not in summary_text and "\x07" not in summary_text and "evil-title" not in summary_text and "ignored-dcs" not in summary_text and "Red text" in summary_text,
      "format_release_summary strips ANSI CSI, OSC, and DCS escape sequences")
check("\u202e" not in summary_text and "\u200b" not in summary_text and "Safegnp.exe line." in summary_text,
      "format_release_summary strips bidi overrides and zero-width chars")

trunc_lines = updater.format_release_summary("\n".join(f"- Item number {i} with extra text" for i in range(20)), max_lines=4, max_chars=80)
check(len(trunc_lines) <= 4 and sum(len(x) for x in trunc_lines) <= 80 and trunc_lines[-1].endswith("..."),
      f"format_release_summary caps max_lines and max_chars cleanly ({trunc_lines})")
single_long = updater.format_release_summary("x" * 50, max_chars=20)
check(single_long == ["x" * 17 + "..."] and sum(len(x) for x in single_long) <= 20,
      f"single oversize line truncates strictly within max_chars without duplicate ellipsis ({single_long})")

shown_prompt: list[str] = []
for d_ans in ("d", "D", "dont", "don't", "never"):
    shown_prompt.clear()
    ans_res = updater.prompt_user_update(
        {"version": "1.1.0", "url": "https://github.com/samlazs/libre-terminal/releases/tag/v1.1.0", "body": sample_body},
        "1.0.0",
        input_fn=lambda _p: d_ans,
        say_fn=shown_prompt.append,
    )
    check(ans_res == "disable", f"prompt {d_ans!r} returns 'disable'")
prompt_txt = "\n".join(shown_prompt)
check("What's new:" in prompt_txt and "Added auto-update check via OpenBB." in prompt_txt and "[D] No, don't ask me again" in prompt_txt,
      "prompt_user_update prints What's new description and [D] option")

pref_app = make_tmp_dir("upd-pref-")
(pref_app / "local" / "state").mkdir(parents=True)
pref_state = (pref_app / "local" / "state" / "state.json")
pref_state.write_bytes(b'{"v": 4}')
check(not updater.is_update_check_disabled(pref_app), "update check enabled by default")
updater.set_update_check_disabled(True, pref_app)
check(updater.is_update_check_disabled(pref_app) and pref_state.read_bytes() == b'{"v": 4}',
      "set_update_check_disabled(True) persists update-prefs.json without touching state.json")
updater.set_update_check_disabled(False, pref_app)
check(not updater.is_update_check_disabled(pref_app) and not updater.update_prefs_path(pref_app).exists(),
      "set_update_check_disabled(False) clears update-prefs.json")

# Verify launcher._maybe_update_on_start skips without any network call for --no-update, TERMINAL_NO_UPDATE=1, non-interactive, git checkout, or disabled pref
net_calls = [0]
orig_check_upd = launcher.updater.check_for_update
orig_is_git = launcher.updater.is_git_checkout
orig_is_int = launcher.updater.is_interactive
orig_is_dis = launcher.updater.is_update_check_disabled
orig_set_dis = launcher.updater.set_update_check_disabled
orig_prompt = launcher.updater.prompt_user_update
launcher.updater.check_for_update = lambda **_kw: net_calls.__setitem__(0, net_calls[0] + 1) or None
saved_no_upd = os.environ.get("TERMINAL_NO_UPDATE")
try:
    launcher.updater.is_git_checkout = lambda *_a: False
    launcher.updater.is_update_check_disabled = lambda *_a: False
    launcher.updater.is_interactive = lambda: True
    os.environ.pop("TERMINAL_NO_UPDATE", None)
    check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=True)) is None and net_calls[0] == 0,
          "--no-update skips update check with 0 network calls")
    os.environ["TERMINAL_NO_UPDATE"] = "1"
    check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=False)) is None and net_calls[0] == 0,
          "TERMINAL_NO_UPDATE=1 skips update check with 0 network calls")
    os.environ.pop("TERMINAL_NO_UPDATE", None)
    launcher.updater.is_interactive = lambda: False
    check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=False)) is None and net_calls[0] == 0,
          "non-interactive mode skips update check with 0 network calls")
    launcher.updater.is_interactive = lambda: True
    launcher.updater.is_update_check_disabled = lambda *_a: True
    check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=False)) is None and net_calls[0] == 0,
          "disabled preference skips update check with 0 network calls")
    launcher.updater.is_update_check_disabled = lambda *_a: False
    launcher.updater.is_git_checkout = lambda *_a: True
    check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=False)) is None and net_calls[0] == 0,
          "git checkout skips update check with 0 network calls")

    if os.name == "nt":
        launcher.updater.is_git_checkout = lambda *_a: False
        launcher.updater.check_for_update = lambda **_kw: {"version": "9.9.9", "url": "", "body": "- Fix"}
        launcher.updater.prompt_user_update = lambda *_a, **_kw: "disable"
        dis_calls: list[bool] = []
        launcher.updater.set_update_check_disabled = lambda val, *_a: dis_calls.append(val)
        check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=False)) is None and dis_calls == [True],
              "choosing 'disable' at startup prompt persists disabled preference and launches")

    h_upd = subprocess.run([sys.executable, str(ROOT / "scripts" / "launcher.py"), "update", "--help"], capture_output=True, text=True, timeout=15)
    check(h_upd.returncode == 0 and "--enable" in h_upd.stdout, "launcher.py update --help exposes --enable")

    # cmd_update handles OSError when saving/clearing preference cleanly
    launcher.updater.set_update_check_disabled = lambda *_a: (_ for _ in ()).throw(PermissionError("locked prefs"))
    check(launcher.cmd_update(types.SimpleNamespace(enable=True)) == 1, "cmd_update --enable returns 1 cleanly on OSError")

    # Verify sync-pending.json blocks startup when non-interactive or sync fails, and clears on interactive success
    launcher.updater.is_git_checkout = lambda *_a: False
    orig_read_sp = launcher.updater.read_sync_pending
    orig_sync_dep = launcher.updater.sync_dependencies
    orig_clr_sp = launcher.updater.clear_sync_pending
    cleared = [0]
    try:
        launcher.updater.read_sync_pending = lambda *_a: {"version": "1.2.0", "with_v4": False}
        launcher.updater.is_interactive = lambda: False
        check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=True)) == 1,
              "pending sync blocks startup even with --no-update when non-interactive")
        launcher.updater.is_interactive = lambda: True
        launcher.updater.sync_dependencies = lambda *_a, **_kw: 1
        check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=True)) == 1,
              "pending sync returns 1 when sync retry fails")
        launcher.updater.sync_dependencies = lambda *_a, **_kw: 0
        launcher.updater.clear_sync_pending = lambda *_a: cleared.__setitem__(0, cleared[0] + 1)
        check(launcher._maybe_update_on_start(types.SimpleNamespace(no_update=True)) is None and cleared[0] == 1,
              "pending sync clears marker when interactive sync retry succeeds")
    finally:
        launcher.updater.read_sync_pending = orig_read_sp
        launcher.updater.sync_dependencies = orig_sync_dep
        launcher.updater.clear_sync_pending = orig_clr_sp
finally:
    launcher.updater.check_for_update = orig_check_upd
    launcher.updater.is_git_checkout = orig_is_git
    launcher.updater.is_interactive = orig_is_int
    launcher.updater.is_update_check_disabled = orig_is_dis
    launcher.updater.set_update_check_disabled = orig_set_dis
    launcher.updater.prompt_user_update = orig_prompt
    if saved_no_upd is None:
        os.environ.pop("TERMINAL_NO_UPDATE", None)
    else:
        os.environ["TERMINAL_NO_UPDATE"] = saved_no_upd

# ---- 7. Windows Start.cmd self-replacement in a folder with spaces and parentheses
if os.name == "nt":
    cmd_base = make_tmp_dir("cmd-self-")
    app_cmd = cmd_base / "upd (test) folder"
    (app_cmd / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "Start.cmd", app_cmd / "Start.cmd")
    venv.create(app_cmd / ".venv-v5", with_pip=False, symlinks=False)

    # Stub launcher rewrites Start.cmd to a much longer version with lots of padding lines, then exits 0
    long_body = (
        "@echo off\r\n"
        + "REM " + ("padding line for length expansion " * 4) + "\r\n" * 15
        + (app_cmd / "Start.cmd").read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    )
    stub = app_cmd / "scripts" / "launcher.py"
    stub.write_text(
        "import pathlib, sys\n"
        "p = pathlib.Path(__file__).resolve().parent.parent / 'Start.cmd'\n"
        f"mode = (pathlib.Path(__file__).resolve().parent / 'mode.txt').read_text(encoding='utf-8').strip()\n"
        "if mode == 'grow':\n"
        f"    p.write_bytes({long_body.encode('utf-8')!r})\n"
        "    print('REWRITTEN_LONG')\n"
        "    sys.exit(0)\n"
        "elif mode == 'shrink':\n"
        "    p.write_bytes(b'@echo off\\r\\necho tiny\\r\\nexit /b 0\\r\\n')\n"
        "    print('REWRITTEN_SHORT')\n"
        "    sys.exit(0)\n"
        "else:\n"
        "    print('SIMULATED_FAIL')\n"
        "    sys.exit(1)\n",
        encoding="utf-8",
    )

    (app_cmd / "scripts" / "mode.txt").write_text("grow", encoding="utf-8")
    r_grow = subprocess.run(
        ["cmd", "/c", str(app_cmd / "Start.cmd"), "--no-browser"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=30,
    )
    out_grow = r_grow.stdout + r_grow.stderr
    check(r_grow.returncode == 0 and "REWRITTEN_LONG" in out_grow and "You can close this window" in out_grow and "not recognized" not in out_grow and "unexpected" not in out_grow.lower(),
          f"Start.cmd survives growing self-replacement in folder with spaces & parens (rc={r_grow.returncode}, out={out_grow!r})")

    (app_cmd / "scripts" / "mode.txt").write_text("shrink", encoding="utf-8")
    r_shrink = subprocess.run(
        ["cmd", "/c", str(app_cmd / "Start.cmd"), "--no-browser"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=30,
    )
    out_shrink = r_shrink.stdout + r_shrink.stderr
    check(r_shrink.returncode == 0 and "REWRITTEN_SHORT" in out_shrink and "You can close this window" in out_shrink,
          f"Start.cmd survives shrinking self-replacement cleanly (rc={r_shrink.returncode})")

    shutil.copy2(ROOT / "Start.cmd", app_cmd / "Start.cmd")
    (app_cmd / "scripts" / "mode.txt").write_text("fail", encoding="utf-8")
    r_fail = subprocess.run(
        ["cmd", "/c", str(app_cmd / "Start.cmd"), "--no-browser"],
        input="\r\n", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    out_fail = r_fail.stdout + r_fail.stderr
    check(r_fail.returncode == 1 and "Something did not start" in out_fail and "upd (test) folder\\local\\logs" in out_fail,
          f"Start.cmd failure path displays expanded LOGDIR with spaces & parens (rc={r_fail.returncode}, out={out_fail!r})")

done("test_updater")
