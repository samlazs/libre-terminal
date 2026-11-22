"""Terminal UI server.

Standard library only. Serves the single-page terminal in ./static, the
plotly.js bundle that ships inside the installed `plotly` Python package, and a
tiny JSON store for saved dashboards (local/state/state.json; see local_paths.py).

The browser talks to the OpenBB backends directly (ports from TERMINAL_V5_PORT /
TERMINAL_V4_PORT, default 6900 / 6901); this server never proxies market data.

Usage: python terminal/server.py [--port 6950] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import mimetypes
import os
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import composite  # noqa: E402
import local_paths  # noqa: E402
import xwidgets  # noqa: E402
from redact import redact  # noqa: E402

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
STATE_DIR = local_paths.STATE_DIR
STATE_FILE = local_paths.STATE_FILE
DASHBOARDS_FILE = local_paths.DASHBOARDS_FILE
MAX_STATE_BYTES = 5 * 1024 * 1024

# The product name shown in the browser title and the sidebar. Renaming the product is this one line
# (static/app.js has the same name only as a fallback for the moment before /api/config answers).
PRODUCT_NAME = "Open Terminal"

V5_PORT = os.environ.get("TERMINAL_V5_PORT") or "6900"
V4_PORT = os.environ.get("TERMINAL_V4_PORT") or "6901"
CONFIG = {
    "product": PRODUCT_NAME,
    "backends": [
        {"id": "v5", "name": "Main data service", "url": f"http://127.0.0.1:{V5_PORT}"},
        {"id": "v4", "name": "Legacy data service", "url": f"http://127.0.0.1:{V4_PORT}"},
        {"id": "x", "name": "Terminal views", "url": ""},
    ]
}


def v4_active() -> bool:
    """True when the optional legacy V4 backend was requested or has run/stopped state in local/run."""
    env = os.environ.get("TERMINAL_WITH_V4", "").strip().lower()
    if env in ("1", "true", "yes"):
        return True
    if env in ("0", "false", "no"):
        return False
    return (local_paths.RUN_DIR / "v4.json").exists() or (local_paths.RUN_DIR / "v4.stopped.json").exists()


def config_for(host_header: str) -> dict:
    """CONFIG with backend URLs on the host name the page itself uses: a page opened as http://localhost:6950 calls
    http://localhost:6900, so its requests (and the backend iframes) stay same-site for the backends' request guard."""
    name = (host_header or "").strip().lower().rsplit(":", 1)[0] if not (host_header or "").strip().startswith("[") else ""
    name = name if name == "localhost" else "127.0.0.1"
    include_v4 = v4_active()
    out = dict(CONFIG, backends=[dict(b) for b in CONFIG["backends"] if include_v4 or b["id"] != "v4"])
    for b in out["backends"]:
        if b["url"]:
            b["url"] = b["url"].replace("127.0.0.1", name, 1)
    return out


def content_security_policy() -> str:
    """What the page may load: its own scripts and styles (Plotly from /vendor), data from the two backends, and
    frames from the backends (TradingView / pywry tools), https pages (provider widgets, SEC documents) and blob: PDFs."""
    bk = " ".join(f"http://{h}:{p}" for p in (V5_PORT, V4_PORT) for h in ("127.0.0.1", "localhost"))
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
            f"font-src 'self' data:; connect-src 'self' {bk}; frame-src 'self' blob: https: {bk}; worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


_state_lock = threading.Lock()


# ---------------------------------------------------------------- saved state on disk
class StateUnreadable(Exception):
    """state.json exists but cannot be used, and no daily backup could replace it (the file is left untouched)."""


def atomic_write(path: Path, text: str):
    """Write through a temporary file and replace: a crash or a full disk never leaves half a file behind."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                 # Windows: a virus scanner or indexer holding the old file for a moment
            if attempt == 4:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.1 * (attempt + 1))


def _parse_state(path: Path):
    """(state, None) or (None, reason)."""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        return None, f"cannot be read ({e.strerror or e})"
    except (ValueError, UnicodeDecodeError) as e:
        return None, f"is not valid JSON ({str(e)[:120]})"
    problem = validate_state(obj)
    return (None, problem) if problem else (obj, None)


def _backups() -> list:
    """Daily copies, newest first (state.YYYYMMDD.bak.json)."""
    return sorted(STATE_DIR.glob("state.[0-9]*.bak.json"), key=lambda p: p.name, reverse=True)


KEEP_BACKUPS = 30


def prune_backups(keep: int = KEEP_BACKUPS) -> list:
    """Delete daily copies beyond the newest `keep` (one was added per day of use, never removed; audit C7).
    Only files named state.YYYYMMDD.bak.json are touched. Returns the names removed."""
    gone = []
    for p in _backups()[keep:]:
        try:
            p.unlink()
            gone.append(p.name)
        except OSError:
            pass
    return gone


def load_state():
    """(state, notice). Called with _state_lock held. A state.json that cannot be used is moved aside as
    state.corrupt-<time>.json and the newest valid daily copy takes its place (notice says so); with no valid copy,
    StateUnreadable is raised and nothing on disk changes, so a save can never overwrite the damaged file."""
    if not STATE_FILE.exists():
        return {}, None
    obj, problem = _parse_state(STATE_FILE)
    if obj is not None:
        return obj, None
    for bak in _backups():
        good, _ = _parse_state(bak)
        if good is None:
            continue
        aside = STATE_DIR / f"state.corrupt-{time.strftime('%Y%m%d-%H%M%S')}.json"
        os.replace(STATE_FILE, aside)
        atomic_write(STATE_FILE, json.dumps(good))
        log(f"state.json {problem}: kept aside as {aside.name}, restored from {bak.name}")
        return good, (f"Your saved layout file {problem}. It was kept as local/state/{aside.name} and the copy from "
                      f"{bak.name[6:14]} (local/state/{bak.name}) was restored; changes made after that copy are not in it.")
    raise StateUnreadable(f"Your saved layout (local/state/state.json) {problem}, and no daily copy (state.YYYYMMDD.bak.json) "
                          "could replace it. The file is left untouched and nothing is saved until it is repaired or removed.")


class FileCache:
    """Bytes of a file the page asks for often (dashboards.json is ~1 MB), re-read only when it changes on disk."""

    def __init__(self):
        self.key = None
        self.value = None
        self.lock = threading.Lock()

    def get(self, path: Path, build):
        st = path.stat()                                    # OSError when missing: the caller answers
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            if self.key != key:
                self.value = build(path.read_bytes())
                self.key = key
            return self.value


_dash_cache = FileCache()


def _dash_build(raw: bytes):
    json.loads(raw.decode("utf-8"))                         # refuse a damaged file instead of serving it
    return raw, '"' + hashlib.sha1(raw).hexdigest()[:20] + '"'


def service_status() -> dict:
    """Per backend: running (its supervisor's pid file is there), stopped (the supervisor gave up: why), or not started."""
    out = {}
    for name in ("v5", "v4"):
        stopped = local_paths.RUN_DIR / f"{name}.stopped.json"
        if (local_paths.RUN_DIR / f"{name}.json").exists():
            out[name] = {"state": "running"}
        elif stopped.exists():
            try:
                out[name] = {"state": "stopped", "why": str(json.loads(stopped.read_text(encoding="utf-8")).get("why", ""))[:300]}
            except (OSError, ValueError, AttributeError):
                out[name] = {"state": "stopped", "why": ""}
        else:
            out[name] = {"state": "not started"}
    return out


def _plotly_js() -> Path | None:
    spec = importlib.util.find_spec("plotly")
    if spec and spec.origin:
        p = Path(spec.origin).parent / "package_data" / "plotly.min.js"
        if p.exists():
            return p
    return None


PLOTLY_JS = _plotly_js()


ABORTS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)

# Top-level keys of the saved state and the JSON type each must have (anything else is refused, see validate_state).
STATE_SHAPE = {"v": int, "_rev": int, "dashboards": dict, "last": (dict, type(None)), "ui": dict, "groups": dict, "libsel": dict,
               "recent": list, "peers": dict, "watchlists": list, "wlsel": dict,
               "srcsel": dict, "viewsel": dict,      # per-card source picks and chart-strip choices (not layout edits)
               "colsel": dict,                       # per-card column picks on lists (not layout edits)
               "cardview": dict}                     # per-card view preferences: flip, chart/table, settings bar (not layout edits)


def validate_state(obj) -> str | None:
    """None when `obj` is a state the page can load, else the reason it is refused (a bad save must never brick the UI)."""
    if not isinstance(obj, dict):
        return "state must be an object"
    for k, v in obj.items():
        want = STATE_SHAPE.get(k)
        if want is None:
            if not isinstance(k, str) or len(k) > 40:
                return f"unexpected key {str(k)[:40]!r}"
            continue                                # a newer page may add a section; keep it
        if not isinstance(v, want) or (want is int and isinstance(v, bool)):
            return f"'{k}' has the wrong type"
    for did, d in (obj.get("dashboards") or {}).items():
        if not isinstance(d, dict) or not isinstance(d.get("tabs", []), list) or not isinstance(d.get("groups", []), list):
            return f"dashboard {str(did)[:60]!r} is malformed"
        if "mode" in d and d["mode"] != "overrides":
            return f"dashboard {str(did)[:60]!r} has an unknown mode"
        if "hiddenTabs" in d and (not isinstance(d["hiddenTabs"], list) or any(not isinstance(x, str) for x in d["hiddenTabs"])):
            return f"dashboard {str(did)[:60]!r} hiddenTabs is malformed"
        if "overrides" in d:
            ovs = d["overrides"]
            if not isinstance(ovs, dict):
                return f"dashboard {str(did)[:60]!r} overrides must be an object"
            for tid, tov in ovs.items():
                if not isinstance(tid, str) or not isinstance(tov, dict):
                    return f"override tab in dashboard {str(did)[:60]!r} is malformed"
                if "name" in tov and not isinstance(tov["name"], str):
                    return f"override tab name in dashboard {str(did)[:60]!r} must be a string"
                if "cards" in tov:
                    cards = tov["cards"]
                    if not isinstance(cards, dict) or any(not isinstance(k, str) or not isinstance(v, dict) for k, v in cards.items()):
                        return f"override cards in dashboard {str(did)[:60]!r} are malformed"
                if "hidden" in tov and (not isinstance(tov["hidden"], list) or any(not isinstance(x, str) for x in tov["hidden"])):
                    return f"override hidden list in dashboard {str(did)[:60]!r} is malformed"
                if "added" in tov and (not isinstance(tov["added"], list) or any(not isinstance(x, dict) for x in tov["added"])):
                    return f"override added cards in dashboard {str(did)[:60]!r} are malformed"
        for t in d.get("tabs") or []:
            if not isinstance(t, dict) or not isinstance(t.get("items", []), list) or not isinstance(t.get("id", ""), str):
                return f"a tab of dashboard {str(did)[:60]!r} is malformed"
            if any(not isinstance(it, dict) for it in t.get("items") or []):
                return f"a card of dashboard {str(did)[:60]!r} is malformed"
    for w in obj.get("watchlists") or []:
        if not isinstance(w, dict) or not isinstance(w.get("symbols", []), list):
            return "a watchlist is malformed"
    return None


def _safe_sources(name, params):
    """What a failing view would have been built from, so its card still names its source."""
    try:
        return composite.static_sources(name, params)
    except Exception:  # noqa: BLE001
        return []


def allowed_hosts(port: int) -> set:
    return {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]")}


def log(msg: str):
    try:
        print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + redact(msg), flush=True)
    except Exception:  # noqa: BLE001
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "TerminalUI/1.0"
    protocol_version = "HTTP/1.1"
    timeout = 120                   # a half-sent request must not hold a thread forever

    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("TERMINAL_DEBUG"):
            super().log_message(fmt, *args)

    def log_error(self, fmt, *args):    # e.g. request timeouts; never a traceback for a client that went away
        if os.environ.get("TERMINAL_DEBUG"):
            super().log_error(fmt, *args)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except ABORTS:
            self.close_connection = True

    # ---- helpers -------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-cache", headers: dict | None = None):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD" and code != 304:
                self.wfile.write(body)
        except ABORTS:                  # the browser closed the connection (tab switch, reload): nothing to report
            self.close_connection = True

    def _json(self, code: int, obj, headers: dict | None = None):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8", headers=headers)

    def _frame_answer(self, result: dict):
        """A frame card's answer from a view (earnings release), as JSON: {"frame": the main backend's SEC filing viewer URL,
        the only frame target ever answered} or {"message": text}. The card loads that URL into its frame itself. Not a
        302: Firefox labels a frame navigation that was redirected from this port to the backend's port
        Sec-Fetch-Site: cross-site (a direct load of the same URL is same-site), so the backend's request guard refused
        it (403, owner 2026-11-18)."""
        out = {"sources": result.get("sources") or []}
        path = str(result.get("_frame") or "")
        if path.startswith("/sec/filing_viewer_app?"):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            host = host if host in ("127.0.0.1", "localhost") else "127.0.0.1"      # the page's own host: same-site for the guard
            out["frame"] = f"http://{host}:{V5_PORT}/api/v1{path}"
        else:
            out["message"] = str(result.get("_message") or "Nothing to show for this choice.")
        return self._json(200, out)

    def _cached_json(self, raw: bytes, etag: str, headers: dict | None = None):
        """200 with an ETag, or 304 when the page already has this version (no-cache: the browser always asks)."""
        h = dict(headers or {}, ETag=etag)
        if etag in [t.strip() for t in (self.headers.get("If-None-Match") or "").split(",")]:
            return self._send(304, b"", "application/json; charset=utf-8", headers=h)
        return self._send(200, raw, "application/json; charset=utf-8", headers=h)

    def _file(self, path: Path, cache: str = "no-cache", headers: dict | None = None):
        try:
            data = path.read_bytes()
        except OSError:
            return self._json(404, {"detail": "not found"})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype, cache, headers)

    def _host_ok(self) -> bool:
        """Only requests addressed to this computer by name are answered (blocks DNS-rebinding pages)."""
        return self.headers.get("Host", "").strip().lower() in allowed_hosts(self.server.server_address[1])

    def _same_origin(self) -> bool:
        """Only this page may read or write state and run views (blocks cross-site requests from other tabs)."""
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return False
        if origin is None:
            return True
        return origin.strip().lower() == f"http://{host.strip().lower()}"

    def _guard(self) -> bool:
        if not self._host_ok():
            self._json(403, {"detail": "Refused: the terminal only answers http://127.0.0.1 or http://localhost."})
            return False
        return True

    # ---- routes --------------------------------------------------------
    def do_OPTIONS(self):
        self._json(405, {"detail": "not allowed"})

    def do_GET(self):
        if not self._guard():
            return None
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", headers={"Content-Security-Policy": content_security_policy()})
        if path == "/api/config":
            return self._json(200, config_for(self.headers.get("Host", "")))
        if path == "/api/services":
            return self._json(200, service_status())
        if path == "/widgets.json":
            return self._json(200, xwidgets.widgets())
        if path == "/api/dashboards":
            try:
                raw, etag = _dash_cache.get(DASHBOARDS_FILE, _dash_build)
            except FileNotFoundError:
                return self._json(503, {"detail": "The dashboards are not built yet (local/dashboards.json is missing). Start the terminal "
                                        "with Start.cmd (or start.sh), which builds them once the data backends answer.", "sections": [], "dashboards": []})
            except (OSError, ValueError, UnicodeDecodeError) as e:
                log(f"local/dashboards.json unreadable: {type(e).__name__}: {e}")
                return self._json(503, {"detail": "The built-in dashboards (local/dashboards.json) could not be read. Restart the terminal "
                                        "to rebuild them (Start.cmd or start.sh).", "sections": [], "dashboards": []})
            return self._cached_json(raw, etag)
        if path.startswith("/x/"):
            if not self._same_origin():
                return self._json(403, {"detail": "cross-origin request refused"})
            query = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "", keep_blank_values=False)
            params = {k: v[-1] for k, v in query.items()}
            name = path[3:].strip("/")
            try:
                result = composite.dispatch(name, params)
            except composite.CompositeError as e:
                # written for the user (or carrying a source's own error, which the page classifies)
                return self._json(502, {"detail": redact(str(e)), "user_message": True, "sources": _safe_sources(name, params)})
            except Exception as e:  # noqa: BLE001
                # "bad parameters" only when the call itself failed (a parameter the view does not take): the traceback
                # then ends in dispatch. A TypeError/ValueError raised inside a view is a defect: logged with a ref.
                if isinstance(e, TypeError) and traceback.extract_tb(e.__traceback__)[-1].name == "dispatch":
                    return self._json(400, {"detail": "Bad parameters for this view: " + redact(str(e))[:200]})
                ref = hashlib.sha1(f"{name}{time.time()}".encode()).hexdigest()[:6]
                log(f"view {name} failed (ref {ref}): {type(e).__name__}: {e}\n" + redact(traceback.format_exc()))
                return self._json(500, {"detail": f"Internal error in the terminal (ref {ref}; details in local/logs/ui.log).", "ref": ref})
            if isinstance(result, dict) and ("_frame" in result or "_message" in result):
                return self._frame_answer(result)
            return self._json(200, result)
        if path == "/api/state":
            if not self._same_origin():
                return self._json(403, {"detail": "cross-origin request refused"})
            with _state_lock:
                try:
                    obj, notice = load_state()
                except StateUnreadable as e:
                    return self._json(503, {"detail": str(e), "state_unreadable": True})
                except OSError as e:
                    log(f"state.json could not be restored: {e}")
                    return self._json(503, {"detail": f"Your saved layout could not be read or restored ({e.strerror or e}). Nothing is saved "
                                            "until this is fixed.", "state_unreadable": True})
            raw = json.dumps(obj).encode("utf-8")
            if notice:                                      # said once, with the restored copy
                return self._send(200, raw, "application/json; charset=utf-8", headers={"X-State-Notice": urllib.parse.quote(notice)})
            return self._cached_json(raw, '"' + hashlib.sha1(raw).hexdigest()[:20] + '"')
        if path == "/vendor/plotly.min.js":
            if PLOTLY_JS is None:
                return self._json(404, {"detail": "plotly not installed in this environment"})
            return self._file(PLOTLY_JS, cache="public, max-age=86400")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            target = (STATIC / rel).resolve()
            if STATIC.resolve() in target.parents and target.is_file():
                return self._file(target)
        return self._json(404, {"detail": "not found"})

    do_HEAD = do_GET

    def _body(self, limit: int):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > limit:
            return None
        return self.rfile.read(length)

    def do_POST(self):
        """/api/clientlog: the page reports a card error it could not classify (written to local/logs/ui.log, redacted)."""
        if not self._guard():
            return None
        if self.path.split("?", 1)[0] != "/api/clientlog":
            return self._json(404, {"detail": "not found"})
        if not self._same_origin():
            return self._json(403, {"detail": "cross-origin request refused"})
        raw = self._body(16 * 1024)
        if raw is None:
            return self._json(413, {"detail": "too large"})
        try:
            j = json.loads(raw.decode("utf-8"))
            log(f"card error ref {str(j.get('ref'))[:12]} on {str(j.get('where'))[:200]}: {str(j.get('detail'))[:2000]}")
        except (ValueError, UnicodeDecodeError, AttributeError):
            pass
        return self._json(200, {"ok": True})

    def do_PUT(self):
        if not self._guard():
            return None
        path = self.path.split("?", 1)[0]
        if path != "/api/state":
            return self._json(404, {"detail": "not found"})
        if not self._same_origin():
            return self._json(403, {"detail": "cross-origin write refused"})
        if "json" not in (self.headers.get("Content-Type") or ""):
            return self._json(415, {"detail": "state must be sent as JSON"})
        raw = self._body(MAX_STATE_BYTES)
        if raw is None:
            return self._json(413, {"detail": "state too large"})
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return self._json(400, {"detail": f"invalid JSON: {e}"})
        problem = validate_state(obj)
        if problem:
            log(f"refused a state save: {problem}")
            return self._json(400, {"detail": f"Layout not saved: {problem}."})
        with _state_lock:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            old = {}
            if STATE_FILE.exists():
                old, problem = _parse_state(STATE_FILE)
                if old is None:
                    # never overwrite a file we cannot read: the page that sent this was built without it
                    log(f"refused a state save: state.json {problem}")
                    return self._json(503, {"detail": f"Not saved: the saved layout file {problem}. Reload the page.", "state_unreadable": True})
                # keep one rolling backup per day (only of a file that is valid, so a backup can always restore)
                bak = STATE_DIR / f"state.{time.strftime('%Y%m%d')}.bak.json"
                if not bak.exists():
                    shutil.copy2(STATE_FILE, bak)
                    prune_backups()
            # Two open pages (two browser tabs, or a tab left open since yesterday) must not overwrite each other:
            # each save carries the revision it was based on, and a stale one gets the current state back instead.
            rev = old.get("_rev", 0) if isinstance(old.get("_rev"), int) else 0
            if old and obj.get("_rev", 0) != rev:
                return self._json(409, {"detail": "The saved layout changed in another window.", "state": old})
            # A page from before a section existed (watchlists, peers, recent tickers...) keeps the saved one.
            obj = dict({k: v for k, v in old.items() if k not in obj}, **obj)
            obj["_rev"] = rev + 1
            atomic_write(STATE_FILE, json.dumps(obj))
        return self._json(200, {"ok": True, "rev": obj["_rev"]})


PRUNE_INTERVAL_S = 24 * 3600


def start_prune_timer(
    interval_s: float = PRUNE_INTERVAL_S,
    stop_event: threading.Event | None = None,
    prune_fn=None,
) -> tuple[threading.Thread, threading.Event]:
    """Run local_paths.prune_local() daily in a daemon thread so long sessions keep local/cache/ bounded (audit B9)."""
    stop = stop_event or threading.Event()
    fn = prune_fn or local_paths.prune_local

    def _loop():
        while not stop.wait(interval_s):
            try:
                gone = fn()
                if gone:
                    log(f"pruned {gone} stale cache file(s)")
            except Exception:  # noqa: BLE001
                log("cache prune failed: " + redact(traceback.format_exc()))

    t = threading.Thread(target=_loop, daemon=True, name="local-cache-prune")
    t.start()
    return t, stop


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=6950)
    a = ap.parse_args()
    local_paths.ensure()
    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            exc = sys.exc_info()[1]
            if isinstance(exc, ABORTS + (TimeoutError,)):
                return                                  # a browser that went away is not an error
            log("request failed: " + redact(traceback.format_exc()))

    httpd = Server((a.host, a.port), Handler)
    bk_desc = f"V5 :{V5_PORT}, V4 :{V4_PORT}" if v4_active() else f"V5 :{V5_PORT}"
    print(f"{PRODUCT_NAME} UI -> http://{a.host}:{a.port}  (backends: {bk_desc})")
    print(f"  dashboards saved in {STATE_FILE}")
    if PLOTLY_JS is None:
        print("  WARNING: plotly.min.js not found; charts will not render", file=sys.stderr)
    composite.warm()
    _, prune_stop = start_prune_timer()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        prune_stop.set()


if __name__ == "__main__":
    main()
