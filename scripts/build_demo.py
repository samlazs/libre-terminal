"""Build the self-contained static interactive preview under docs/ from a running local terminal.

Usage:
    python scripts/build_demo.py [--ui-url http://127.0.0.1:6950] [--v5-url http://127.0.0.1:6900]
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import getpass
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
sys.path.insert(0, str(ROOT / "scripts"))
import project_check as pc  # noqa: E402
import redact  # noqa: E402

DOCS_DIR = ROOT / "docs"
DEMO_DIR = DOCS_DIR / "demo"
STATIC_SRC = ROOT / "terminal" / "static"
STATIC_DST = DEMO_DIR / "static"
DATA_DST = DEMO_DIR / "data"
FRAMES_DST = DEMO_DIR / "frames"

VIRTUAL_V5 = "https://demo.libre-terminal.local"
STATIC_FILES = (
    "app.css",
    "request.js",
    "md.js",
    "table.js",
    "statements.js",
    "controls.js",
    "securities.js",
    "grid.js",
    "widgets.js",
    "app.js",
)

CASE_UPPER = {"symbol", "symbols", "company", "cusip"}
CASE_LOWER = {"country", "provider", "period", "duration", "metric", "option_type"}
VOLATILE_DATES = {"start_date", "end_date", "date"}

STARTER_WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "ASML", "7203.T", "SHEL.L", "RHM.DE", "^GSPC", "EURUSD=X", "GC=F", "BTC-USD",
]

COUNTRY_ALIASES = {
    "south_korea": ["korea"],
    "korea": ["south_korea"],
    "turkey": ["turkiye"],
    "turkiye": ["turkey"],
    "czech_republic": ["czechia"],
    "czechia": ["czech_republic"],
    "slovakia": ["slovak_republic"],
    "euro_area": ["euro_area_20_countries", "euro_area_19_countries", "euro_area20", "ea20"],
    "united_states": ["united_states_of_america"],
}

# Showcase targets: (pack_name, dash_id, tab_id, param_overrides_list)
SHOWCASE_SPECS = [
    ("markets", "d:markets", "overview", [{}]),
    ("company-aapl", "d:eq-company", "overview", [{"symbol": "AAPL"}]),
    ("company-aapl", "d:eq-company", "estimates", [{"symbol": "AAPL"}]),
    ("company-aapl", "d:eq-company", "price-peers", [{"symbol": "AAPL"}]),
    ("company-aapl", "d:eq-company", "growth-ratios", [{"symbol": "AAPL"}]),
    ("company-jpm", "d:eq-company", "overview", [{"symbol": "JPM"}]),
    ("company-jpm", "d:eq-company", "estimates", [{"symbol": "JPM"}]),
    ("company-jpm", "d:eq-company", "price-peers", [{"symbol": "JPM"}]),
    ("company-jpm", "d:eq-company", "growth-ratios", [{"symbol": "JPM"}]),
    ("credit", "d:credit", "bond-lookup-trace", [{"company": "AAPL"}]),
    ("credit", "d:credit", "spreads", [{}]),
    ("rates", "d:rates", "policy-expectations", [{}]),
    (
        "rates",
        "d:rates",
        "by-country",
        [{"country": "united_states"}, {"country": "germany"}, {"country": "japan"}],
    ),
    ("options", "d:options", "volatility", [{"symbol": "AAPL"}, {"symbol": "SPY"}]),
    ("energy", "d:energy", "oil", [{}]),
    ("trade", "d:trade", "chokepoints", [{}]),
]

SHOWCASE_TABS = {(dash_id, tab_id) for _, dash_id, tab_id, _ in SHOWCASE_SPECS}
MAX_TABLE_ROWS = 260
SEARCH_SEEDS = ["AAPL", "JPM", "SPY", "MSFT", "NVDA", "QQQ", "ASML", "7203.T", "SHEL.L", "RHM.DE", "GLD", "BTC-USD"]


def _http_json(url: str, origin: str, method: str = "GET", payload: dict | None = None, timeout: int = 75):
    headers = {"Origin": origin, "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        keep_hdrs = {
            k: resp.headers.get(k)
            for k in ("X-Terminal-Source", "X-Terminal-Fallback", "X-Terminal-Delayed", "X-Terminal-Stale")
            if resp.headers.get(k)
        }
        return resp.status, json.loads(raw.decode("utf-8")) if raw.strip() else None, keep_hdrs


def _http_text(url: str, origin: str, timeout: int = 45) -> str:
    req = urllib.request.Request(url, headers={"Origin": origin})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _ny_today() -> dt.date:
    try:
        from zoneinfo import ZoneInfo

        return dt.datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # noqa: BLE001
        return dt.date.today()


def resolve_param(val, today: dt.date | None = None):
    if not isinstance(val, str) or not val.startswith("@"):
        return val
    m = re.match(r"^@(days|bday):(-?\d+)$", val)
    if not m:
        return val
    d = today or _ny_today()
    n = int(m.group(2))
    if m.group(1) == "days":
        d = d + dt.timedelta(days=n)
    else:
        step = -1 if n < 0 else 1
        left = abs(n)
        while left > 0:
            d = d + dt.timedelta(days=step)
            if d.weekday() < 5:
                left -= 1
    return d.isoformat()


def canonical_key(method: str, path: str, params: dict | None = None, body: dict | None = None, drop_dates: bool = False) -> str:
    pairs: list[tuple[str, str]] = []
    for k, v in (params or {}).items():
        if v is None or v == "" or v == [] or k in ("_", "t"):
            continue
        if drop_dates and k in VOLATILE_DATES:
            continue
        val = ",".join(str(x) for x in v) if isinstance(v, list) else str(v)
        if k in CASE_UPPER:
            val = val.upper()
        elif k in CASE_LOWER:
            val = val.lower()
        elif val in ("True", "False"):
            val = val.lower()
        elif isinstance(v, bool):
            val = "true" if v else "false"
        pairs.append((str(k), val))
    if method.upper() == "POST" and isinstance(body, dict):
        for k in sorted(body):
            v = body[k]
            val = ",".join(sorted(str(x) for x in v)) if isinstance(v, list) else str(v)
            pairs.append((f"__body_{k}", val))
    pairs.sort(key=lambda kv: (kv[0], kv[1]))
    qs = urllib.parse.urlencode(pairs)
    return f"{method.upper()} {path}" + (f"?{qs}" if qs else "")


def _country_for(pdef: dict, value):
    opts = [str(o.get("value")) for o in (pdef.get("options") or []) if isinstance(o, dict) and o.get("value") is not None]
    if not opts:
        return value

    def one(c: str) -> str:
        c = str(c)
        if c in opts:
            return c
        for alt in COUNTRY_ALIASES.get(c, ()):
            if alt in opts:
                return alt
        return c

    if isinstance(value, list):
        return [one(x) for x in value]
    return ",".join(one(x) for x in str(value).split(","))


def _effective_params(item: dict, wdef: dict, dash: dict, group_overrides: dict) -> dict:
    out: dict = {}
    grp_by_name = {g["name"]: g for g in dash.get("groups", [])}
    item_groups = [grp_by_name[n] for n in (item.get("groups") or []) if n in grp_by_name]

    def grp_val(pname: str):
        for g in item_groups:
            if g.get("paramName") == pname:
                if pname in group_overrides and group_overrides[pname] not in (None, ""):
                    return group_overrides[pname]
                if g.get("value") not in (None, ""):
                    return g["value"]
        return None

    for p in wdef.get("params") or []:
        pn = p.get("paramName")
        if not pn:
            continue
        gv = grp_val(pn)
        if gv not in (None, ""):
            val = _country_for(p, gv) if pn == "country" else gv
        elif pn in (item.get("params") or {}):
            val = item["params"][pn]
        else:
            val = p.get("value")
        out[pn] = resolve_param(val)

    for k, v in (item.get("params") or {}).items():
        if k not in out and v not in (None, "", []):
            out[k] = resolve_param(v)

    for g in item_groups:
        pn = g.get("paramName")
        if pn and pn not in out:
            gv = group_overrides.get(pn, g.get("value"))
            if gv not in (None, ""):
                out[pn] = gv

    if wdef.get("symbolList") == "watchlist":
        out["list"] = "main"
        out["symbols"] = ",".join(STARTER_WATCHLIST)
    elif wdef.get("symbolList") == "peers":
        out.pop("peers", None)

    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _strip_script_and_events(html: str) -> str:
    cleaned = re.sub(r"(?is)<script\b[^>]*>.*?</script>", "", html)
    cleaned = re.sub(r"(?is)<noscript\b[^>]*>.*?</noscript>", "", cleaned)
    cleaned = re.sub(r"(?i)\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", cleaned)
    cleaned = re.sub(
        r"(?is)<img\b[^>]*\bsrc\s*=\s*(?:\"(?!(?:https?:|data:))[^\"]*\"|'(?!(?:https?:|data:))[^']*')[^>]*/?>",
        "",
        cleaned,
    )
    base_tag = '<base target="_blank">'
    if not re.search(r"(?is)<base\b", cleaned):
        if re.search(r"(?is)<head\b[^>]*>", cleaned):
            cleaned = re.sub(r"(?is)(<head\b[^>]*>)", r"\1" + base_tag, cleaned, count=1)
        else:
            cleaned = base_tag + cleaned
    return cleaned


def _trim_rows(rows: list) -> list:
    cap = 60 if (rows and isinstance(rows[0], dict) and len(rows[0]) > 40) else MAX_TABLE_ROWS
    if len(rows) <= cap or not rows or not isinstance(rows[0], dict):
        return rows[:cap] if len(rows) > cap else rows
    date_keys = [k for k in ("date", "period_ending", "last_trade_date", "report_date") if k in rows[0]]
    if date_keys and len(rows) >= 2:
        dk = date_keys[0]
        first_d, last_d = str(rows[0].get(dk) or ""), str(rows[-1].get(dk) or "")
        return rows[:cap] if first_d >= last_d else rows[-cap:]
    return rows[:cap]


def _sanitize_body(path: str, body):
    """Trim huge tables to keep demo lean and drop news items that mention restricted terms."""
    if isinstance(body, list):
        rows = copy.deepcopy(body)
        if "/news" in path:
            rows = [
                row for row in rows
                if not pc._AI_ATTR_RX.search(json.dumps(row, ensure_ascii=False))
            ]
        return _trim_rows(rows)
    if not isinstance(body, dict):
        return body
    out = copy.deepcopy(body)
    if "/news" in path and isinstance(out.get("results"), list):
        out["results"] = [
            row for row in out["results"]
            if not pc._AI_ATTR_RX.search(json.dumps(row, ensure_ascii=False))
        ]
    if isinstance(out.get("results"), list):
        out["results"] = _trim_rows(out["results"])
    return out


def _slim_widgets_v5(v5_widgets: dict, dashboards: dict) -> dict:
    used: set[str] = set()
    showcase_v5: set[str] = set()
    for d in dashboards.get("dashboards") or []:
        did = d.get("id")
        for t in d.get("tabs") or []:
            in_showcase = (did, t.get("id")) in SHOWCASE_TABS
            for it in t.get("items") or []:
                if it.get("backend") == "v5" and it.get("widgetId"):
                    used.add(it["widgetId"])
                    if in_showcase:
                        showcase_v5.add(it["widgetId"])
                for ch in it.get("choices") or []:
                    if ch.get("b") == "v5" and ch.get("w"):
                        used.add(ch["w"])
                        if in_showcase:
                            showcase_v5.add(ch["w"])
    for spec in (dashboards.get("concepts") or {}).values():
        for m in spec.get("members") or []:
            if m.get("b") == "v5" and m.get("w"):
                used.add(m["w"])

    slim: dict = {}
    for wid, wdef in v5_widgets.items():
        if wid not in used:
            continue
        w = copy.deepcopy(wdef)
        w.pop("refetchInterval", None)
        w.pop("staleTime", None)
        w.pop("gridData", None)
        w.pop("mcp_tool", None)
        if wid not in showcase_v5:
            w.pop("data", None)
            w.pop("runButton", None)
            w.pop("subCategory", None)
            for p in w.get("params") or []:
                if isinstance(p, dict):
                    p.pop("options", None)
                    p.pop("description", None)
        else:
            for p in w.get("params") or []:
                opts = p.get("options")
                if isinstance(opts, list) and len(opts) > 40:
                    cur_val = p.get("value")
                    kept = opts[:25]
                    if cur_val is not None and not any(isinstance(o, dict) and o.get("value") == cur_val for o in kept):
                        match = [o for o in opts if isinstance(o, dict) and o.get("value") == cur_val]
                        if match:
                            kept = match + kept[:24]
                    p["options"] = kept
        slim[wid] = w
    return slim


def _demo_dashboards(raw_dashboards: dict) -> dict:
    d_out = copy.deepcopy(raw_dashboards)
    for d in d_out.get("dashboards") or []:
        did = d.get("id")
        for t in d.get("tabs") or []:
            tid = t.get("id")
            in_showcase = (did, tid) in SHOWCASE_TABS
            for it in t.get("items") or []:
                if in_showcase and it.get("widgetId") == "yfinance_tv_widget_obb":
                    it["widgetId"] = "x_chart"
                    it["backend"] = "x"
                    it["title"] = "Price chart (Yahoo Finance)"
                    it["src"] = ["Yahoo Finance"]
                    it["params"] = {"period": "1Y", "view": "price", "studies": "ma50,ma200"}
                it.pop("needsKey", None)
    return d_out


def _write_lf_json(path: Path, obj) -> int:
    text = json.dumps(obj, allow_nan=False, separators=(",", ":"), ensure_ascii=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text + "\n").encode("utf-8"))
    return path.stat().st_size


def _copy_static() -> None:
    if STATIC_DST.exists():
        shutil.rmtree(STATIC_DST)
    STATIC_DST.mkdir(parents=True, exist_ok=True)
    for name in STATIC_FILES:
        raw = (STATIC_SRC / name).read_bytes().replace(b"\r\n", b"\n")
        (STATIC_DST / name).write_bytes(raw)


def _check_privacy_and_hygiene() -> None:
    user = getpass.getuser()
    user_rx = re.compile(rf"(?i)(?:\\|/){re.escape(user)}(?:\\|/|$)") if user and len(user) >= 3 else None
    secrets = redact._key_values()
    bad: list[str] = []

    targets = [DOCS_DIR / "index.html", DEMO_DIR / "demo.js", DEMO_DIR / "demo.css"]
    for folder in (STATIC_DST, DATA_DST, FRAMES_DST):
        if folder.is_dir():
            targets.extend(sorted(p for p in folder.rglob("*") if p.is_file()))

    for p in targets:
        rel = p.relative_to(ROOT).as_posix()
        raw = p.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            bad.append(f"{rel}: UTF-8 BOM")
        if b"\r\n" in raw:
            bad.append(f"{rel}: contains CRLF")
        txt = raw.decode("utf-8", errors="strict")
        if p.suffix == ".json":
            json.loads(txt)
        if pc._PATH_RX.search(txt) or (user_rx and user_rx.search(txt)):
            bad.append(f"{rel}: personal/machine path")
        if pc._AI_ATTR_RX.search(txt):
            bad.append(f"{rel}: restricted attribution/tool wording")
        for sec in secrets:
            if sec in txt:
                bad.append(f"{rel}: contains configured secret value")
                break
    if bad:
        raise RuntimeError("Demo privacy/hygiene check failed:\n  " + "\n  ".join(bad))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the frozen static demo snapshot under docs/ from a running terminal.")
    ap.add_argument("--ui-url", default="http://127.0.0.1:6950", help="Running UI server base URL")
    ap.add_argument("--v5-url", default="http://127.0.0.1:6900", help="Running V5 backend base URL")
    args = ap.parse_args(argv)

    ui_url = args.ui_url.rstrip("/")
    v5_url = args.v5_url.rstrip("/")

    if DATA_DST.exists():
        shutil.rmtree(DATA_DST)
    if FRAMES_DST.exists():
        shutil.rmtree(FRAMES_DST)
    DATA_DST.mkdir(parents=True, exist_ok=True)
    FRAMES_DST.mkdir(parents=True, exist_ok=True)

    _copy_static()

    _, cfg, _ = _http_json(f"{ui_url}/api/config", ui_url)
    _, raw_dash, _ = _http_json(f"{ui_url}/api/dashboards", ui_url)
    _, wx, _ = _http_json(f"{ui_url}/widgets.json", ui_url)
    _, wv5, _ = _http_json(f"{v5_url}/widgets.json", ui_url)

    demo_cfg = {
        "product": cfg.get("product", "Libre Terminal"),
        "version": cfg.get("version", ""),
        "budgets": cfg.get("budgets", {}),
        "backends": [
            {"id": "v5", "name": "Main data service", "url": VIRTUAL_V5},
            {"id": "x", "name": "Terminal views", "url": ""},
        ],
    }
    demo_dash = _demo_dashboards(raw_dash)
    slim_v5 = _slim_widgets_v5(wv5, demo_dash)

    dash_map = {d["id"]: d for d in demo_dash.get("dashboards") or []}
    packs: dict[str, dict] = {}
    routes_map: dict[str, str] = {}
    fallback_map: dict[str, str] = {}
    frames_map: dict[str, str] = {}
    search_items: dict[str, dict] = {}
    default_groups: dict[str, dict] = {}
    failed: list[str] = []

    def record_search_rows(rows, featured: bool = False):
        if not isinstance(rows, list):
            return
        for r in rows:
            if not isinstance(r, dict):
                continue
            sym = str(r.get("symbol") or "").strip()
            nm = str(r.get("name") or "").strip()
            if not sym or not nm or len(sym) > 16:
                continue
            key = sym.upper()
            if key not in search_items:
                search_items[key] = {
                    "symbol": sym,
                    "name": nm,
                    "type": r.get("type") or "",
                    "type_label": r.get("type_label") or "",
                    "exchange": r.get("exchange") or "",
                    "featured": featured,
                }
            elif featured:
                search_items[key]["featured"] = True

    def _has_dynamic_date(item: dict, wdef: dict) -> bool:
        for src in (item.get("params") or {}, {p.get("paramName"): p.get("value") for p in (wdef.get("params") or []) if isinstance(p, dict)}):
            for k, v in src.items():
                if k in VOLATILE_DATES and isinstance(v, str) and re.match(r"^@(days|bday):(-?\d+)$", v):
                    return True
        return False

    def store(pack_name: str, method: str, path: str, params: dict | None, body_req: dict | None,
              status: int, body_resp, headers: dict, allow_nodate: bool = False):
        clean_body = _sanitize_body(path, body_resp)
        ck = canonical_key(method, path, params, body_req, drop_dates=False)
        pack_file = f"{pack_name}.json"
        packs.setdefault(pack_name, {"entries": {}})["entries"][ck] = {
            "status": status,
            "headers": headers,
            "body": clean_body,
        }
        routes_map[ck] = pack_file
        if allow_nodate:
            nk = canonical_key(method, path, params, body_req, drop_dates=True)
            if nk != ck and nk not in fallback_map:
                fallback_map[nk] = ck
        if path in ("/x/board", "/x/watchlist", "/x/comps") and isinstance(clean_body, dict):
            record_search_rows(clean_body.get("results"), featured=(path == "/x/watchlist"))
        return ck, clean_body

    def fetch_and_store(pack_name: str, backend_id: str, path: str, params: dict | None = None,
                        method: str = "GET", body_req: dict | None = None, allow_nodate: bool = False):
        ck = canonical_key(method, path, params, body_req, drop_dates=False)
        if ck in routes_map:
            existing_pack = routes_map[ck][:-5]
            return ck, packs[existing_pack]["entries"][ck]["body"]
        base = ui_url if backend_id == "x" else v5_url
        qs = urllib.parse.urlencode(
            {k: (",".join(str(x) for x in v) if isinstance(v, list) else ("true" if v is True else "false" if v is False else str(v)))
             for k, v in (params or {}).items() if v not in (None, "", [])}
        )
        full_url = f"{base}{path}" + (f"?{qs}" if qs else "")
        st, body, hdrs = _http_json(full_url, ui_url, method=method, payload=body_req)
        return store(pack_name, method, path, params, body_req, st, body, hdrs, allow_nodate=allow_nodate)

    # 1. Capture every card on the curated showcase tabs
    for pack_name, dash_id, tab_id, overrides_list in SHOWCASE_SPECS:
        dash = dash_map[dash_id]
        tab = next(t for t in dash["tabs"] if t["id"] == tab_id)
        for ov in overrides_list:
            for it in tab.get("items") or []:
                bid = it["backend"]
                wid = it["widgetId"]
                wdef = (wx if bid == "x" else slim_v5).get(wid)
                if not wdef:
                    continue
                if it.get("manual"):
                    continue
                ep = wdef.get("endpoint") or ""
                if not ep:
                    continue
                wtype = wdef.get("type")
                params = _effective_params(it, wdef, dash, ov)

                # Embedded frames
                if wtype in ("iframe", "html"):
                    if bid == "x" and ep == "/x/earnings_release":
                        try:
                            _, rel_body, hdrs = _http_json(
                                f"{ui_url}{ep}?{urllib.parse.urlencode(params)}", ui_url
                            )
                            sym = str(params.get("symbol") or "AAPL").lower()
                            doc_url = (rel_body.get("sources") or [{}])[0].get("url")
                            if doc_url:
                                _, htm_obj, _ = _http_json(
                                    f"{v5_url}/api/v1/sec/htm_file?url={urllib.parse.quote(doc_url)}", ui_url
                                )
                                htm = ((htm_obj or {}).get("results") or {}).get("content") or ""
                                if htm:
                                    fname = f"sec-8k-{sym}.html"
                                    (FRAMES_DST / fname).write_bytes(
                                        (_strip_script_and_events(htm).rstrip() + "\n").encode("utf-8")
                                    )
                                    rel_body["frame"] = f"./demo/frames/{fname}"
                                    rel_body["dark_doc"] = True
                            store(pack_name, "GET", ep, params, None, 200, rel_body, hdrs)
                        except Exception as e:  # noqa: BLE001
                            failed.append(f"{pack_name} {ep} {params}: {e}")
                    continue

                # Multi-file viewer (FOMC documents)
                if wtype in ("multi_file_viewer", "pdf"):
                    try:
                        _, choices, hdrs = _http_json(
                            f"{v5_url}/api/v1/federal_reserve/fomc_documents_choices", ui_url
                        )
                        picked = None
                        for ch in choices or []:
                            if "Monetary Policy" in (ch.get("label") or ""):
                                _, dl_body, dl_hdrs = _http_json(
                                    f"{v5_url}/api/v1/federal_reserve/fomc_documents_download",
                                    ui_url,
                                    method="POST",
                                    payload={"url": [ch["value"]]},
                                )
                                picked = ch
                                slim_choices = [picked]
                                store(pack_name, "GET", "/api/v1/federal_reserve/fomc_documents_choices", {}, None, 200, slim_choices, hdrs)
                                store(pack_name, "POST", "/api/v1/federal_reserve/fomc_documents_download", {}, {"url": [ch["value"]]}, 200, dl_body, dl_hdrs)
                                break
                    except Exception as e:  # noqa: BLE001
                        failed.append(f"{pack_name} FOMC: {e}")
                    continue

                # Wait for required group inputs (e.g. Bond CUSIP on Bond Lookup) handled below
                if any(g.get("prompt") and g.get("paramName") not in params for g in dash.get("groups", []) if g["name"] in (it.get("groups") or [])):
                    continue

                try:
                    fetch_and_store(pack_name, bid, ep, params, allow_nodate=_has_dynamic_date(it, wdef))
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{pack_name} {ep} {params}: {e}")

    # 2. Extra interactive support per showcase area
    # 2a. Company helpers (links, earnings reports)
    for pack_name, sym in (("company-aapl", "AAPL"), ("company-jpm", "JPM")):
        for ep in ("/x/company_links", "/x/earnings_reports"):
            try:
                fetch_and_store(pack_name, "x", ep, {"symbol": sym})
            except Exception as e:  # noqa: BLE001
                failed.append(f"{pack_name} {ep} {sym}: {e}")

    # 2b. Bond Lookup: capture pricer & TRACE history for top 3 active AAPL CUSIPs
    bond_entry = next(
        (v for k, v in (packs.get("credit", {}).get("entries") or {}).items() if k.startswith("GET /x/bond_lookup?")),
        None,
    )
    bond_rows = ((bond_entry or {}).get("body") or {}).get("results") or []
    top_cusips = [r["cusip"] for r in bond_rows if isinstance(r, dict) and r.get("cusip")][:3]
    if top_cusips:
        default_groups["d:credit"] = {"Issuer": "AAPL", "Bond": top_cusips[0]}
        credit_dash = dash_map["d:credit"]
        trace_tab = next(t for t in credit_dash["tabs"] if t["id"] == "bond-lookup-trace")
        for cusip in top_cusips:
            for it in trace_tab.get("items") or []:
                bid, wid = it["backend"], it["widgetId"]
                if wid not in ("x_bond_pricer", "finra_fixedincome_bond_historical_finra_obb"):
                    continue
                wdef = (wx if bid == "x" else slim_v5).get(wid)
                if not wdef or not wdef.get("endpoint"):
                    continue
                p = _effective_params(it, wdef, credit_dash, {"company": "AAPL", "cusip": cusip})
                try:
                    fetch_and_store("credit", bid, wdef["endpoint"], p)
                except Exception as e:  # noqa: BLE001
                    failed.append(f"credit {wdef['endpoint']} {cusip}: {e}")

    # 2c. Options Volatility: capture 60d GEX + smile expiry choices & 3 monthly smiles
    for sym in ("AAPL", "SPY"):
        try:
            fetch_and_store("options", "x", "/x/gex_strike", {"symbol": sym, "expiry": "60d"})
        except Exception as e:  # noqa: BLE001
            failed.append(f"options gex 60d {sym}: {e}")
        try:
            _, exps_body = fetch_and_store(
                "options", "v5", "/api/v1/cboe/options/get_tickers", {"symbol": sym, "expiry_list": "true"}
            )
            exp_vals = [
                x.get("value") if isinstance(x, dict) else x
                for x in (exps_body if isinstance(exps_body, list) else [])
                if (x.get("value") if isinstance(x, dict) else x)
            ][:3]
            for exp_v in exp_vals:
                fetch_and_store(
                    "options", "v5", "/api/v1/cboe/options/smile",
                    {"symbol": sym, "otm": "true", "skew": "false", "expirations": exp_v},
                )
        except Exception as e:  # noqa: BLE001
            failed.append(f"options smile extras {sym}: {e}")

    # 2d. Search index seeds for Go-To & symbol picker
    for q in SEARCH_SEEDS:
        try:
            _, sr, _ = _http_json(f"{ui_url}/x/search?query={urllib.parse.quote(q)}", ui_url)
            record_search_rows((sr or {}).get("results"), featured=(q in ("AAPL", "JPM", "SPY", "NVDA")))
        except Exception:  # noqa: BLE001
            pass

    if failed:
        print("Warnings during capture:")
        for f in failed:
            print(" ", f)

    # 3. Write config, dashboards, widget definitions, packs, and manifest
    sizes: dict[str, int] = {}
    sizes["config.json"] = _write_lf_json(DATA_DST / "config.json", demo_cfg)
    sizes["dashboards.json"] = _write_lf_json(DATA_DST / "dashboards.json", demo_dash)
    sizes["widgets-x.json"] = _write_lf_json(DATA_DST / "widgets-x.json", wx)
    sizes["widgets-v5.json"] = _write_lf_json(DATA_DST / "widgets-v5.json", slim_v5)

    for pack_name, pack_obj in sorted(packs.items()):
        fname = f"{pack_name}.json"
        sizes[fname] = _write_lf_json(DATA_DST / fname, pack_obj)

    wl_key = canonical_key("GET", "/x/watchlist", {"list": "main", "symbols": ",".join(STARTER_WATCHLIST)})
    manifest = {
        "snapshot_date": _ny_today().isoformat(),
        "routes": routes_map,
        "fallback_routes": fallback_map,
        "frames": frames_map,
        "default_groups": default_groups,
        "watchlist_default_key": wl_key,
        "search_index": sorted(search_items.values(), key=lambda x: (not x.get("featured"), x["symbol"])),
    }
    sizes["manifest.json"] = _write_lf_json(DATA_DST / "manifest.json", manifest)

    _check_privacy_and_hygiene()

    total_bytes = sum(sizes.values()) + sum(p.stat().st_size for p in FRAMES_DST.glob("*.html")) + sum(p.stat().st_size for p in STATIC_DST.glob("*"))
    print(f"Static demo built ({manifest['snapshot_date']}): {len(routes_map)} routes across {len(packs)} packs, total={total_bytes / 1e6:.2f} MB")
    for k, sz in sorted(sizes.items()):
        print(f"  {k:<22} {sz / 1024:7.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
