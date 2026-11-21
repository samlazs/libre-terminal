"""Check that every card names a real data source (live or --offline against recorded catalogs; read-only).

    .venv-v5\\Scripts\\python.exe scripts\\check_sources.py [--offline] [--skip-v4] [old-dashboards.json]

Walks local/dashboards.json (built-in layouts) and the saved layouts/overrides in local/state/state.json, resolves each
card's header source the way the page does (provider of the widget definition; for the terminal's own views the sources
the view reports for those parameters), and fails on any card without one. Also counts cards with a source picker and,
given an older dashboards.json, the cards before and after.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
import composite  # noqa: E402
import local_paths  # noqa: E402
import xwidgets  # noqa: E402

# same table as static/widgets.js (PROV, RAW_NAMES): a provider's plain name
PROV = {"yfinance": "Yahoo Finance", "cboe": "Cboe", "tmx": "TMX", "nasdaq": "Nasdaq", "fred": "FRED", "bls": "BLS", "eia": "U.S. EIA", "sec": "SEC",
        "federal_reserve": "Federal Reserve", "ecb": "ECB", "oecd": "OECD", "imf": "IMF", "us_treasury": "U.S. Treasury", "government_us": "U.S. Government",
        "cftc": "CFTC", "finra": "FINRA", "deribit": "Deribit", "usda": "USDA", "jodi": "JODI", "famafrench": "Fama-French", "congress_gov": "Congress.gov",
        "econdb": "EconDB", "finviz": "Finviz", "multpl": "multpl", "wsj": "WSJ", "fmp": "FMP", "polygon": "Polygon", "tiingo": "Tiingo", "intrinio": "Intrinio",
        "alpha_vantage": "Alpha Vantage", "tradier": "Tradier", "benzinga": "Benzinga", "biztoc": "BizToc", "tradingeconomics": "Trading Economics",
        "ustreasury": "U.S. Treasury", "uscongress": "Congress.gov", "news": "News feeds"}
BAD = re.compile(r"^(|custom|default|terminal views|source shown.*)$", re.I)


def default_ports() -> dict:
    saved = local_paths.saved_ports()
    return {
        "v5": os.environ.get("TERMINAL_V5_PORT") or str(saved.get("v5", 6900)),
        "v4": os.environ.get("TERMINAL_V4_PORT") or str(saved.get("v4", 6901)),
    }


def provider_name(d):
    prov = next((p.get("value") for p in d.get("params", []) if p.get("paramName") == "provider"), None)
    if prov in PROV:
        return PROV[prov]
    src = d.get("source")
    src = src if isinstance(src, list) else [src] if src else []
    names = [re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(s)).split(";")[-1].strip() for s in src]
    names = [n for n in names if n and not BAD.match(n)]
    if names:
        return " / ".join(names)
    ns = str(d.get("endpoint", "")).replace("/api/v1/", "").split("/")[0]
    return PROV.get(ns, ns.title())


def extract_saved_cards(state: dict | None) -> list[tuple[str, dict]]:
    """Extract card entries from v4 overrides, legacy v2/v3 edited tabs, and user/app dashboards."""
    out: list[tuple[str, dict]] = []
    if not isinstance(state, dict):
        return out
    for did, d in (state.get("dashboards") or {}).items():
        if not isinstance(d, dict):
            continue
        if str(did).startswith("d:"):
            if d.get("mode") == "overrides":
                for tid, ov in (d.get("overrides") or {}).items():
                    if not isinstance(ov, dict):
                        continue
                    for it in ov.get("added") or []:
                        if isinstance(it, dict) and it.get("backend") and it.get("widgetId"):
                            out.append((f"(saved) {did} > {tid}", it))
                    for cid, cov in (ov.get("cards") or {}).items():
                        if isinstance(cov, dict) and cov.get("backend") and cov.get("widgetId"):
                            out.append((f"(saved) {did} > {tid} ({cid})", cov))
            else:
                for t in d.get("tabs") or []:
                    if isinstance(t, dict) and t.get("edited"):
                        for it in t.get("items") or []:
                            if isinstance(it, dict) and it.get("backend") and it.get("widgetId"):
                                out.append((f"(saved) {did} > {t.get('name')}", it))
        else:
            for t in d.get("tabs") or []:
                if isinstance(t, dict):
                    for it in t.get("items") or []:
                        if isinstance(it, dict) and it.get("backend") and it.get("widgetId"):
                            out.append((f"(saved) {did} > {t.get('name')}", it))
    return out


def load_catalogs(offline: bool = False, skip_v4: bool = False, ports: dict | None = None, quiet: bool = False) -> dict:
    defs = {"x": xwidgets.widgets()}
    ports = ports or default_ports()
    if offline:
        v5_cat = local_paths.CACHE_DIR / "catalog-v5.json"
        if not v5_cat.exists():
            raise FileNotFoundError(f"{v5_cat} not found (start the terminal once to record backend catalogs)")
        defs["v5"] = json.loads(v5_cat.read_text(encoding="utf-8"))
        v4_cat = local_paths.CACHE_DIR / "catalog-v4.json"
        if not skip_v4 and v4_cat.exists():
            defs["v4"] = json.loads(v4_cat.read_text(encoding="utf-8"))
        else:
            defs["v4"] = {}
        return defs

    with urllib.request.urlopen(f"http://127.0.0.1:{ports['v5']}/widgets.json", timeout=120) as r:
        defs["v5"] = json.load(r)
    if skip_v4:
        defs["v4"] = {}
        return defs
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{ports['v4']}/widgets.json", timeout=30) as r:
            defs["v4"] = json.load(r)
    except (OSError, urllib.error.URLError, ValueError):
        v4_cat = local_paths.CACHE_DIR / "catalog-v4.json"
        if v4_cat.exists():
            defs["v4"] = json.loads(v4_cat.read_text(encoding="utf-8"))
            if not quiet:
                print(f"note: V4 backend offline; using cached {v4_cat.name}")
        else:
            defs["v4"] = {}
            if not quiet:
                print("note: V4 backend offline; checking V5 and Terminal views only")
    return defs


def check_all(defs: dict, built: dict, state: dict | None = None, root: Path = ROOT) -> dict:
    concepts = built.get("concepts") or {}
    in_concept = {f"{m['b']}:{m['w']}" for c in concepts.values() for m in c["members"] if len(c["members"]) > 1 or m.get("mk")}
    by_ep = {}
    for b in ("v5", "v4"):
        for w, d in (defs.get(b) or {}).items():
            by_ep.setdefault(d.get("endpoint", ""), []).append((b, w))
    cards = [(f"{d['name']} > {t['name']}", it) for d in built.get("dashboards", []) for t in d.get("tabs", []) for it in t.get("items", [])]
    cards += extract_saved_cards(state)
    bad, picker, seen = [], 0, 0
    names = {}
    for where, it in cards:
        variants = [(it["backend"], it["widgetId"], it.get("params") or {})] + [(c["b"], c["w"], c.get("p") or {}) for c in it.get("choices") or []]
        for b, w, params in variants:
            d = (defs.get(b) or {}).get(w)
            if d is None:
                continue                          # a widget the running backends no longer offer: the card says so itself
            seen += 1
            if b == "x":
                p = {pp["paramName"]: pp.get("value") for pp in d.get("params", []) if pp.get("paramName")}
                p.update(params)
                got = [s["name"] for s in composite.static_sources(d["endpoint"][3:], p)]
                name = " · ".join(dict.fromkeys(got))
            else:
                name = provider_name(d)
            names[name] = names.get(name, 0) + 1
            if BAD.match(name.strip()):
                bad.append(f"{where}: {w} -> {name!r}")
        d = (defs.get(it["backend"]) or {}).get(it["widgetId"])
        if d and (f"{it['backend']}:{it['widgetId']}" in in_concept or d.get("sourceParam") or len(by_ep.get(d.get("endpoint"), [])) > 1):
            picker += 1
    code_hits = [p for p in list((root / "terminal").rglob("*.py")) + list((root / "terminal" / "static").glob("*.js")) + [root / "scripts" / "build_dashboards.py"]
                 if p.exists() and "Source shown" in p.read_text(encoding="utf-8", errors="replace")]
    n_cards = sum(len(t["items"]) for d in built.get("dashboards", []) for t in d.get("tabs", []))
    n_tabs = sum(len(d["tabs"]) for d in built.get("dashboards", []))
    return {"seen": seen, "bad": bad, "picker": picker, "names": names, "n_cards": n_cards,
            "n_tabs": n_tabs, "n_concepts": len(concepts), "code_hits": code_hits}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check that every card names a real data source.")
    ap.add_argument("old_dashboards", nargs="?", help="optional older dashboards.json to compare counts against")
    ap.add_argument("--offline", action="store_true", help="use cached backend catalogs in local/cache/catalog-*.json")
    ap.add_argument("--skip-v4", action="store_true", help="ignore the optional V4 backend")
    ap.add_argument("--quiet", action="store_true", help="print only errors if all passes")
    a = ap.parse_args(argv)

    defs = load_catalogs(offline=a.offline, skip_v4=a.skip_v4, quiet=a.quiet)
    built = json.loads(local_paths.DASHBOARDS_FILE.read_text(encoding="utf-8"))
    state = json.loads(local_paths.STATE_FILE.read_text(encoding="utf-8")) if local_paths.STATE_FILE.exists() else {}
    res = check_all(defs, built, state)
    if not a.quiet:
        print(f"cards checked (incl. library choices and saved tabs): {res['seen']}; without a real source: {len(res['bad'])}")
        print(f"built-in cards: {res['n_cards']}; tabs: {res['n_tabs']}; cards with a source picker: {res['picker']}; concept families: {res['n_concepts']}")
        if a.old_dashboards:
            old = json.loads(Path(a.old_dashboards).read_text(encoding="utf-8"))
            print(f"before: {sum(len(t['items']) for d in old['dashboards'] for t in d['tabs'])} cards, {sum(len(d['tabs']) for d in old['dashboards'])} tabs")
        print("'Source shown' in code: " + (", ".join(str(p) for p in res["code_hits"]) if res["code_hits"] else "nowhere"))
        print("most common header names:", sorted(res["names"].items(), key=lambda x: -x[1])[:12])
    for b in res["bad"][:30]:
        print("  NO SOURCE", b)
    sys.exit(1 if res["bad"] or res["code_hits"] else 0)


if __name__ == "__main__":
    main()
