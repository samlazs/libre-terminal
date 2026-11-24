"""Generate local/dashboards.json: the Terminal's asset-class dashboards.

Reads the live widget lists of the OpenBB Platform backend (and optional legacy
V4 backend with --with-v4) plus the Terminal's own composite views, then lays
every data function out under a logical dashboard
and tab (asset class / topic) instead of by data provider. A function offered
by several providers is placed once, using the best provider that needs no key;
the UI lets you switch source on the card.

Usage (backends must be running):  python scripts/build_dashboards.py [--report]
The output is generated on every start, so it lives with this computer's data in
local/ (see terminal/local_paths.py), not in the project files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "terminal"))
import columns  # noqa: E402
import composite  # noqa: E402
import local_paths  # noqa: E402
import xwidgets  # noqa: E402

OUT = local_paths.DASHBOARDS_FILE
# Data functions known to fail upstream right now ({endpoint: reason}); they are kept in Data Explorers > Data status.
HEALTH = ROOT / "terminal" / "health.json"
BACKENDS = {"v5": f"http://127.0.0.1:{os.environ.get('TERMINAL_V5_PORT') or 6900}", "v4": f"http://127.0.0.1:{os.environ.get('TERMINAL_V4_PORT') or 6901}"}


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboards.layouts import (  # noqa: E402
    W,
    CURVE,
    ECON_CAL,
    M,
    C,
    LIB,
    METRICS,
    ANALYST_ACTIONS,
    X,
    NEWS,
    OVB,
    WATCH,
    HYPERSCALERS,
    T,
    D,
    SYMBOL,
    REPORT,
    COUNTRY,
    MACRO_COUNTRY,
    SECTIONS,
    PERF,
    DASHBOARDS,
)
from dashboards.overrides import (  # noqa: E402
    PREF,
    PARAM_FIXES,
    CHOICE_PLOTS,
    REQUIRED_PARAMS,
    UNREGISTERED,
    RENAMED_TABS,
    SORTS,
    TITLES,
    PROVIDER_FIXES,
    OPT,
    OPT_EP,
    OPT_EXTRA,
    OPT_LABELS,
    NO_OPTIONS,
    CONCEPTS,
    SKIP,
    DEAD,
    KEYS,
    KEY_LABELS,
    KEY_SIGNUP,
    FRED_KEY_ONLY,
    FRED_KEYLESS_SUB,
    STATUS_DASH,
    MACRO_COUNTRIES,
    SIZES,
    FALLBACK,
)

__all__ = [
    'PREF',
    'PARAM_FIXES',
    'CHOICE_PLOTS',
    'REQUIRED_PARAMS',
    'UNREGISTERED',
    'RENAMED_TABS',
    'SORTS',
    'TITLES',
    'PROVIDER_FIXES',
    'OPT',
    'OPT_EP',
    'OPT_EXTRA',
    'OPT_LABELS',
    'NO_OPTIONS',
    'CONCEPTS',
    'SKIP',
    'DEAD',
    'KEYS',
    'KEY_LABELS',
    'KEY_SIGNUP',
    'FRED_KEY_ONLY',
    'FRED_KEYLESS_SUB',
    'STATUS_DASH',
    'MACRO_COUNTRIES',
    'SIZES',
    'FALLBACK',
    'W',
    'CURVE',
    'ECON_CAL',
    'M',
    'C',
    'LIB',
    'METRICS',
    'ANALYST_ACTIONS',
    'X',
    'NEWS',
    'OVB',
    'WATCH',
    'HYPERSCALERS',
    'T',
    'D',
    'SYMBOL',
    'REPORT',
    'COUNTRY',
    'MACRO_COUNTRY',
    'SECTIONS',
    'PERF',
    'DASHBOARDS',
    'ROOT',
    'OUT',
    'HEALTH',
    'BACKENDS',
    'CATALOG',
    'LAST_REPORT',
    'needs_key',
    'credentials',
    'load',
    'provider_of',
    'is_chart_variant',
    'Index',
    'macro_key_provider',
    'slug',
    'build',
]

def needs_key(prov, ep):
    """The credential a card needs, or None (FRED series work without a key; see FRED_KEY_ONLY)."""
    if prov == "fred" and ep not in FRED_KEY_ONLY:
        return None
    return KEYS.get(prov)

def credentials():
    try:
        return {k for k, v in json.loads((Path.home() / ".openbb_platform" / "user_settings.json").read_text(encoding="utf-8")).get("credentials", {}).items() if v}
    except (OSError, ValueError):
        return set()

# ------------------------------------------------------------------ index
CATALOG = local_paths.CACHE_DIR / "catalog-{}.json"     # last widgets.json of each backend: lets scripts/tests replay a build offline


def load():
    defs = {"x": xwidgets.widgets()}
    with_v4 = os.environ.get("TERMINAL_WITH_V4", "").strip().lower()
    for bid, url in BACKENDS.items():
        if bid == "v4" and with_v4 in ("0", "false", "no"):
            defs[bid] = {}
            continue
        try:
            with urllib.request.urlopen(url + "/widgets.json", timeout=5 if bid == "v4" else 120) as r:
                defs[bid] = json.load(r)
            try:
                local_paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = Path(str(CATALOG).format(bid) + ".tmp")
                tmp.write_text(json.dumps(defs[bid], separators=(",", ":")), encoding="utf-8")
                os.replace(tmp, str(CATALOG).format(bid))
            except OSError:
                pass                            # only a test aid
        except OSError:
            if bid != "v4":
                raise
            # The legacy backend is optional (opt-in via --with-v4): build without its sources.
            print(f"Optional legacy V4 backend not running at {url}: building V5-only dashboards")
            defs[bid] = {}
    return defs


def provider_of(d):
    for p in d.get("params", []):
        if p.get("paramName") == "provider":
            return p.get("value")
    return None


def is_chart_variant(d):
    return d.get("type") == "chart" and any(p.get("paramName") == "chart" for p in d.get("params", []))


class Index:
    def __init__(self, defs):
        self.defs = defs
        self.by_ep = {}            # (endpoint, chart) -> [(rank, bid, wid)]
        for bid, ws in defs.items():
            for wid, d in ws.items():
                ep = d.get("endpoint", "")
                ep = "x:" + ep[3:] if bid == "x" else ep.replace("/api/v1/", "")
                prov = provider_of(d)
                if prov in DEAD or ep in UNREGISTERED:
                    continue
                rank = (PREF.index(prov) if prov in PREF else len(PREF) - 3 if prov else 0, 0 if bid == "v5" else 1)
                self.by_ep.setdefault((ep, is_chart_variant(d)), []).append((rank, bid, wid, prov))
        for k in self.by_ep:
            self.by_ep[k].sort()
        self.functions = sorted({ep for ep, chart in self.by_ep if not chart} | {ep for ep, chart in self.by_ep if chart and (ep, False) not in self.by_ep})

    def pick(self, ep, chart=False, provider=None, wid=None):
        cands = self.by_ep.get((ep, chart)) or (self.by_ep.get((ep, False)) if chart else None) or self.by_ep.get((ep, True))
        if not cands:
            return None
        if wid:
            for c in cands:
                if c[2] == wid:
                    return c[1], c[2]
            return None
        if provider:
            for c in cands:
                if c[3] == provider:
                    return c[1], c[2]
        return cands[0][1], cands[0][2]

    def match(self, pattern):
        """'ns/sub/*' glob or regex (prefix 're:'), optional '@Sub Category' filter."""
        sub = None
        if "@" in pattern:
            pattern, sub = pattern.split("@", 1)
        rx = re.compile(pattern[3:] if pattern.startswith("re:") else re.escape(pattern).replace(r"\*", ".*"))
        out = []
        for ep in self.functions:
            if not rx.fullmatch(ep):
                continue
            if sub is not None:
                b, w = self.pick(ep)
                if (self.defs[b][w].get("subCategory") or "") != sub:
                    continue
            out.append(ep)
        return out

def macro_key_provider(preset):
    """'eia' when every series of a curated chart comes from EIA (no keyless fallback), else None (FRED series fall
    back to FRED's public download, calculated series need nothing)."""
    entry = composite.MACRO_SETS.get(preset or "")
    ids = [s[1] for s in (entry[3] if entry and len(entry) > 3 else [])]
    return "eia" if ids and all(str(i).startswith("EIA:") for i in ids) else None

# ------------------------------------------------------------------ build
def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


LAST_REPORT = {}                                # coverage accounting of the last build (read by scripts/tests)


def build(report=False):
    defs = load()
    ix = Index(defs)
    have_keys = credentials()
    try:
        broken = {k: v for k, v in json.loads(HEALTH.read_text(encoding="utf-8-sig")).items() if any((k, c) in ix.by_ep for c in (False, True))}
    except (OSError, ValueError):
        broken = {}
    covered = {}                               # endpoint -> first "Dashboard > Tab"
    n_down = [0]
    out, problems = [], []

    ctx = {"placed": set(), "home": ""}        # what the dashboard being built has placed so far, and the tab being built
    down_home = {}                             # broken upstream: endpoint -> "Dashboard > Tab" where it would normally be
    wid_ep = {}                                # (backend, widgetId) -> endpoint, for the column specs

    def note_down(ep):
        down_home.setdefault(ep, ctx["home"])
        ctx["placed"].add(ep)

    def make_item(spec, tab_groups, dash_groups, n, tid):
        if spec.get("library"):
            eps, down_eps, seen = [], [], set()
            for sel in spec["library"]:
                if isinstance(sel, dict):
                    e, swid = sel["ep"], sel.get("wid")
                    if e in SKIP or (e, swid) in seen:
                        continue
                    if e in broken and not sel.get("params", {}).get("_keep"):
                        down_eps.append(e)
                        continue
                    if not ix.pick(e, sel.get("chart", False), sel.get("provider"), wid=swid):
                        problems.append(f"library {spec['title']}: unknown widget {swid or e}")
                        continue
                    seen.add((e, swid))
                    eps.append((e, swid))
                    continue
                rest = sel.startswith("rest:")
                sel = sel[5:] if rest else sel
                found = ix.match(sel) if ("*" in sel or "@" in sel or sel.startswith("re:")) else ([sel] if ix.pick(sel) else [])
                if rest:
                    found = [e for e in found if e not in ctx["placed"]]
                for e in found:
                    if e in broken and e not in down_eps:
                        down_eps.append(e)
                    elif e not in SKIP and (e, None) not in seen:
                        seen.add((e, None))
                        eps.append((e, None))
            for e in down_eps:
                note_down(e)
            if not eps:
                problems.append(f"empty library {spec['title']}")
                return None
            if spec.get("first"):
                idx = next((i for i, (e, swid) in enumerate(eps) if spec["first"] in (e, swid)), None)
                if idx is not None:
                    eps.insert(0, eps.pop(idx))
                else:
                    problems.append(f"library {spec['title']}: first choice {spec['first']} not in it")
            ctx["placed"].update([e for e, _ in eps] + down_eps)
            choices = []
            for e, swid in eps:
                b, w = ix.pick(e, wid=swid)
                d = defs[b][w]
                lbl = TITLES.get(e) or d.get("name") or w
                sub = d.get("subCategory") or d.get("category") or ""
                ch = {"b": b, "w": w, "l": lbl, "g": "" if sub == lbl else sub}
                wid_ep.setdefault((b, w), e)
                if PARAM_FIXES.get(e):
                    ch["p"] = PARAM_FIXES[e]                   # the working defaults travel with the choice
                if REQUIRED_PARAMS.get(e):
                    ch["r"] = REQUIRED_PARAMS[e]
                if CHOICE_PLOTS.get(e):
                    ch["c"] = CHOICE_PLOTS[e]                  # this dataset opens plotted

                if needs_key(provider_of(d), e) and needs_key(provider_of(d), e) not in have_keys:
                    ch["k"] = needs_key(provider_of(d), e)
                choices.append(ch)
            item = make_item(dict(spec, ep=eps[0][0], wid=eps[0][1], library=None, title=None), tab_groups, dash_groups, n, tid)
            item.update({"title": spec["title"], "choices": choices, "_eps": [e for e, _ in eps]})
            item.pop("compact", None)
            if choices[0].get("c"):
                item.update({"chart": dict(choices[0]["c"]), "view": "chart"})
            return item
        if spec["ep"] in FRED_KEYLESS_SUB and KEYS["fred"] not in have_keys:
            sub = make_item(M(FRED_KEYLESS_SUB[spec["ep"]], spec["w"] or 20, spec["h"] or 16), tab_groups, dash_groups, n, tid)
            if sub:
                sub["_eps"] = ["x:macro_chart"]          # the endpoint counts as placed here; its series are shown keyless
            return sub
        got = ix.pick(spec["ep"], spec["chart"], spec["provider"] or PROVIDER_FIXES.get(spec["ep"]), wid=spec.get("wid"))
        if not got:
            problems.append(f"no widget for {spec['ep']} (provider={spec['provider']}, wid={spec.get('wid')})" if spec.get("wid")
                            else f"no widget for {spec['ep']} (provider={spec['provider']})")
            return None
        bid, wid = got
        d = defs[bid][wid]
        wid_ep.setdefault((bid, wid), spec["ep"])
        dw, dh = SIZES.get(d.get("type"), (20, 14))
        item = {"uid": f"{tid}-{n}", "widgetId": wid, "backend": bid, "w": spec["w"] or dw, "h": spec["h"] or dh, "params": dict(spec["params"]), "groups": [],
                "name": TITLES.get(spec["ep"]) or d.get("name") or wid}   # shown if the backend is down when the card opens (never the raw id)
        if REQUIRED_PARAMS.get(spec["ep"]):
            item["require"] = REQUIRED_PARAMS[spec["ep"]]
        if needs_key(provider_of(d), spec["ep"]):
            item["_kprov"] = provider_of(d)
            if needs_key(provider_of(d), spec["ep"]) not in have_keys:
                item["_keyed"] = True
                item["needsKey"] = needs_key(provider_of(d), spec["ep"])   # the card says so at once instead of sending a request that must fail
        if wid == "x_macro":                    # a curated chart drawn only from EIA series needs the EIA key
            prov = macro_key_provider(spec["params"].get("preset"))
            if prov:
                item["_kprov"] = prov
                if KEYS[prov] not in have_keys:
                    item["_keyed"] = True
                    item["needsKey"] = KEYS[prov]
        if spec["title"]:
            item["title"] = spec["title"]
        if bid == "x":                          # the card header shows these names before the first answer arrives
            try:
                item["src"] = list(dict.fromkeys(s["name"] for s in composite.static_sources(spec["ep"][2:], {**{p["paramName"]: p.get("value") for p in d.get("params", []) if p.get("paramName")}, **spec["params"]})))
            except Exception:  # noqa: BLE001
                pass
        if spec.get("view"):
            item["view"] = spec["view"]          # "chart": open a table already plotted
        if spec.get("manual"):
            item["manual"] = spec["manual"]      # too heavy to fetch automatically: the card asks first
        if spec.get("sort") or SORTS.get(spec["ep"]):
            item["sort"] = spec.get("sort") or SORTS[spec["ep"]]
        if spec.get("since"):
            item["since"] = spec["since"]
        if spec.get("tparam"):
            item["titleParam"] = spec["tparam"]
        if spec.get("cols"):
            item["cols"] = spec["cols"]
        if spec.get("plot"):
            item["chart"] = dict(spec["plot"])
        if spec.get("only"):
            item["srcOnly"] = list(spec["only"])
        # boards and rankings read better without their settings strip (the gear on the card brings it back)
        if spec["compact"] or (spec["compact"] is None and wid in ("x_board", "x_perf_bars", "x_style_box", "x_global_yields", "x_policy_rates", "x_rates_by_country", "x_macro", "x_compare", "x_chokepoints", "x_gpr_table")):
            item["compact"] = True
        # A widget follows a dashboard group when it has that parameter and the spec did not pin a value.
        allowed = spec["groups"] if spec["groups"] is not None else tab_groups
        names = {p.get("paramName") for p in d.get("params", [])}
        for g in dash_groups:
            if allowed is not None:
                if g["name"] not in allowed:
                    continue
            elif g is not next(o for o in dash_groups if o["paramName"] == g["paramName"]):
                continue                        # a second group on the same parameter only applies where a tab asks for it
            forced = spec["groups"] is not None and g["name"] in spec["groups"]      # the spec asked for it even though the widget definition omits the parameter
            if (g["paramName"] in names or forced) and g["paramName"] not in item["params"] and not any(x["paramName"] == g["paramName"] and x["name"] in item["groups"] for x in dash_groups):
                item["groups"].append(g["name"])
        # working defaults, except where a dashboard group (ticker, country) drives the parameter
        driven = {g["paramName"] for g in dash_groups if g["name"] in item["groups"]}
        for k, v in PARAM_FIXES.get(spec["ep"], {}).items():
            if k not in item["params"] and k not in driven:
                item["params"][k] = v
        return item

    for dash in DASHBOARDS:
        placed_here = ctx["placed"] = set()
        tabs = []

        def down(spec):
            if spec["ep"] in broken and not spec["params"].get("_keep"):
                note_down(spec["ep"])                  # broken upstream: goes to Data Explorers > Data status
                return True
            return False

        def add_tab(name, items):
            if not items:
                return
            tabs.append({"id": slug(name) or f"t{len(tabs)}", "name": name, "items": items})
            for it in items:
                ep = it.pop("_ep")
                for e in [ep] + it.pop("_eps", []):
                    placed_here.add(e)
                    covered.setdefault(e, f"{dash['name']} > {name}")

        def auto_items(selectors, tab, tid, start_y, start_n):
            eps = []
            for sel in selectors:
                if isinstance(sel, dict):
                    if sel["ep"] not in SKIP:
                        eps.append(sel)
                    continue
                found = ix.match(sel) if ("*" in sel or sel.startswith("re:") or "@" in sel) else ([sel] if ix.pick(sel) else [])
                if not found and not ("*" in sel or sel.startswith("re:") or "@" in sel):
                    problems.append(f"{dash['name']} > {tab['name']}: unknown endpoint {sel}")
                is_glob = "*" in sel or sel.startswith("re:") or "@" in sel
                for ep in found:
                    if ep in SKIP or ep in [e if isinstance(e, str) else e["ep"] for e in eps]:
                        continue
                    if is_glob and ep in placed_here:
                        continue
                    eps.append(ep)
            items, x, y, row_h, n = [], 0, start_y, 0, start_n
            built = []
            for e in eps:
                spec = e if isinstance(e, dict) else W(e)
                if down(spec):
                    continue
                it = make_item(spec, tab["groups"], dash["groups"], n, tid)
                if not it:
                    continue
                n += 1
                built.append((spec, it))
            built.sort(key=lambda p: 1 if p[1].get("_keyed") else 0)       # sources that still need a key go last
            for spec, it in built:
                d = defs[it["backend"]][it["widgetId"]]
                full = tab["cols"] == 1 or d.get("type") in ("iframe", "html")
                it["w"] = 40 if full else (spec["w"] or 20)
                if tab["h"] and not spec["h"]:
                    it["h"] = tab["h"]
                if x + it["w"] > 40:
                    x, y, row_h = 0, y + row_h, 0
                it["x"], it["y"], it["_ep"] = x, y, spec["ep"]
                x += it["w"]
                row_h = max(row_h, it["h"])
                items.append(it)
                placed_here.add(spec["ep"])
            return items

        for tab in dash["tabs"]:
            tid = slug(tab["name"])
            ctx["home"] = f"{dash['name']} > {tab['name']}"
            items, y, n = [], 0, 0
            def build_specs(specs):
                nonlocal n
                out_ = []
                for spec in specs:
                    if down(spec):
                        continue
                    it = make_item(spec, tab["groups"], dash["groups"], n, tid)
                    if not it:
                        continue
                    n += 1
                    out_.append((spec, it))
                # cards that need a key this computer does not have move behind the keyless ones (ids stay the same)
                out_.sort(key=lambda p: 1 if p[1].get("_keyed") else 0)
                return out_

            if tab["columns"]:
                x = 0
                for width, col in tab["columns"]:
                    cy = 0
                    for spec, it in build_specs(col):
                        it.update({"x": x, "y": cy, "w": width, "_ep": spec["ep"]})
                        cy += it["h"]
                        items.append(it)
                        placed_here.add(spec["ep"])
                    x += width
                    y = max(y, cy)
            built_rows = []
            for row in tab["rows"]:
                r = build_specs(row)
                for spec, _ in r:
                    placed_here.add(spec["ep"])
                built_rows.append(r)
            auto_built = [(spec, it) for it in auto_items(tab["auto"], tab, tid, y, n) for spec in [{"ep": it.pop("_ep")}]]
            free_rows = [r for r in built_rows if r and not any(it.get("_keyed") for _, it in r)]
            mixed_rows = [r for r in built_rows if r and any(it.get("_keyed") for _, it in r) and not all(it.get("_keyed") for _, it in r)]
            keyed_rows = [r for r in built_rows if r and all(it.get("_keyed") for _, it in r)]
            auto_free = [(s, it) for s, it in auto_built if not it.get("_keyed")]
            auto_keyed = [(s, it) for s, it in auto_built if it.get("_keyed")]
            for row in free_rows + mixed_rows:
                x, row_h = 0, 0
                for spec, it in row:
                    it.update({"x": x, "y": y, "_ep": spec["ep"]})
                    x += it["w"]
                    row_h = max(row_h, it["h"])
                    items.append(it)
                y += row_h
            for group in (auto_free, *(r for r in keyed_rows), auto_keyed):
                if not group:
                    continue
                x, row_h = 0, 0
                for spec, it in group:
                    if x + it["w"] > 40:
                        x, y, row_h = 0, y + row_h, 0
                    it.update({"x": x, "y": y, "_ep": spec["ep"]})
                    x += it["w"]
                    row_h = max(row_h, it["h"])
                    items.append(it)
                y += row_h
            # keep tabs readable: split very long ones
            limit = 16
            if len(items) > limit and not tab["rows"] and not tab["columns"]:
                parts = [items[i:i + limit] for i in range(0, len(items), limit)]
                for i, part in enumerate(parts):
                    base = part[0]["y"]
                    for it in part:
                        it["y"] -= base
                        it["uid"] = f"{tid}{i + 1}-{it['uid'].split('-')[-1]}"
                    add_tab(f"{tab['name']} ({i + 1}/{len(parts)})", part)
            else:
                add_tab(tab["name"], items)

        # the landing tab must show data without keys: a first tab that is mostly key-gated (for this computer's keys)
        # gives its place to the first tab that is mostly keyless
        def keyed_share(t):
            its = t["items"]
            return sum(1 for i in its if i.get("needsKey")) / len(its) if its else 1.0
        if tabs and keyed_share(tabs[0]) >= 0.75:
            j = next((i for i, t in enumerate(tabs) if keyed_share(t) < 0.5), None)
            if j:
                tabs.insert(0, tabs.pop(j))

        groups = []
        for g in dash["groups"]:
            g2 = {"name": g["name"], "paramName": g["paramName"], "value": g["value"]}
            g2.update({k: g[k] for k in ("optionsEndpoint", "optionsParams", "resetWith", "syncFrom", "syncUrl", "prompt") if k in g})
            if g.get("options") == "countries":
                import composite
                g2["options"] = composite.COUNTRY_OPTIONS
                g2["multi"] = True
            elif g.get("options") == "macro":
                g2["options"] = [{"label": c.replace("_", " ").title(), "value": c} for c in MACRO_COUNTRIES]
                g2["multi"] = True
            groups.append(g2)
        out.append({"id": "d:" + dash["id"], "name": dash["name"], "section": dash["section"], "description": dash["desc"], "source": "app", "groups": groups, "tabs": tabs, "_spec": dash,
                    "renamed": RENAMED_TABS.get(dash["id"], {})})

    by_id = {d["id"]: d for d in out}

    # ---- one place for everything broken upstream (instead of an "Unavailable" tab on each dashboard):
    # the status card, then the broken functions themselves so they can be retried
    status = by_id["d:" + STATUS_DASH]
    items = [dict(make_item(X("data_status", 40, 24, groups=[]), [], [], 0, "data-status"), x=0, y=0)]
    covered["x:data_status"] = f"{status['name']} > Data status"
    x, y, row_h = 0, 24, 0
    broken_rows = []
    for n, ep in enumerate([e for e in down_home if e not in covered], 1):
        # broken cards wait for a click: loading 30 failing sources at once would only stall the backends
        it = make_item(W(ep, manual=f"Broken upstream: {broken.get(ep) or 'source failing'}. Normally in {down_home[ep]}."), [], [], n, "data-status")
        if not it:
            continue
        it["w"] = 20
        if x + it["w"] > 40:
            x, y, row_h = 0, y + row_h, 0
        it["x"], it["y"] = x, y
        x += it["w"]
        row_h = max(row_h, it["h"])
        items.append(it)
        covered[ep] = f"{status['name']} > Data status"
        d = defs[it["backend"]][it["widgetId"]]
        broken_rows.append({"name": d.get("name") or ep, "home": down_home[ep].replace(" > ", " › "), "reason": broken.get(ep, "")})
    n_down[0] = len(broken_rows)
    status["tabs"].append({"id": "data-status", "name": "Data status", "items": items})

    # ---- concept siblings: resolve members, and count a member reached through a placed card's source picker as placed
    concepts = {}
    for cid, c in CONCEPTS.items():
        members = []
        for ep, prov, label, opt in c["members"]:
            opt = dict(opt)
            wid = opt.pop("wid", None)
            got = ix.pick(ep, False, prov, wid=wid)
            if not got or (prov and provider_of(defs[got[0]][got[1]]) not in (prov, None)):
                continue
            b, w = got
            wid_ep.setdefault((b, w), ep)
            m = {"b": b, "w": w, "l": label, "_ep": ep, **opt}
            pk = provider_of(defs[b][w])
            if KEYS.get(pk) and KEYS[pk] not in have_keys:
                m["k"] = KEYS[pk]
            members.append(m)
        if len(members) < 1 or (len(members) < 2 and not any(m.get("mk") for m in members)):
            continue
        home = next((covered[m["_ep"]] for m in members if m["_ep"] in covered), None)
        for m in members:
            if home and m["_ep"] not in covered:
                covered[m["_ep"]] = home + " (source picker)"
            m.pop("_ep")
        concepts[cid] = {k: v for k, v in c.items() if k != "members"}
        concepts[cid]["members"] = members

    # ---- guarantee coverage: every function must live somewhere
    leftovers = [ep for ep in ix.functions if ep not in covered]
    extra = {}
    for ep in leftovers:
        # first choice: a dashboard that claims this namespace in its "more" list, else the prefix table
        target = next((d["_spec"]["id"] for d in out for sel in d["_spec"]["more"] if ep in ix.match(sel)), None)
        target = target or next(did for prefix, did in FALLBACK if ep.startswith(prefix))
        extra.setdefault(target, []).append(ep)
    for did, eps in extra.items():
        dash = by_id["d:" + did]
        spec = dash["_spec"]
        for i in range(0, len(eps), 14):
            items, x, y, row_h = [], 0, 0, 0
            for n, ep in enumerate(eps[i:i + 14]):
                it = make_item(W(ep), [], spec["groups"], n, f"other{i}")
                if not it:
                    continue
                d = defs[it["backend"]][it["widgetId"]]
                it["w"] = 40 if d.get("type") in ("iframe", "html") else 20
                if x + it["w"] > 40:
                    x, y, row_h = 0, y + row_h, 0
                it["x"], it["y"] = x, y
                x += it["w"]
                row_h = max(row_h, it["h"])
                items.append(it)
                covered[ep] = f"{dash['name']} > More"
            name = "More" if len(eps) <= 14 else f"More ({i // 14 + 1}/{(len(eps) + 13) // 14})"
            dash["tabs"].append({"id": slug(name), "name": name, "items": items})

    key_cards = {}
    for d in out:
        d.pop("_spec")
        for t in d["tabs"]:
            for it in t["items"]:
                it.pop("_keyed", None)
                prov = it.pop("_kprov", None)
                if prov:
                    key_cards[prov] = key_cards.get(prov, 0) + 1
        d["rev"] = hashlib.sha1(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10]

    # What the Data status card shows: broken functions, and which keys are set (never their values)
    keys = [{"source": KEY_LABELS.get(p, p), "set": KEYS[p] in have_keys, "cards": key_cards.get(p, 0), "signup": KEY_SIGNUP.get(p, "")}
            for p in KEYS if key_cards.get(p) or KEYS[p] in have_keys]
    keys.sort(key=lambda k: (k["set"], -k["cards"]))
    # How each placed table presents its columns (headers, formats, default column set): terminal/columns.py
    colspecs = {}
    for (b, w), ep in wid_ep.items():
        if b == "x":
            continue                            # the Terminal's own views define their columns by hand
        s = columns.spec(defs[b][w], ep)
        if s:
            colspecs[w] = s
    titles = {}
    for (b, w), ep in wid_ep.items():
        if b != "x" and TITLES.get(ep):
            titles[w] = TITLES[ep]
    stamp = composite.mtl_time(time.time())                 # shown on Data status: Montreal time like every card
    result = {"generated": stamp, "sections": [{"id": s, "name": n} for s, n in SECTIONS], "dashboards": out, "concepts": concepts, "columns": colspecs, "titles": titles,
              "status": {"generated": stamp, "broken": broken_rows, "keys": keys}}
    local_paths.ensure()
    tmp = OUT.with_name(f"{OUT.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, OUT)                    # the UI server may be reading the previous file right now

    n_items = sum(len(t["items"]) for d in out for t in d["tabs"])
    missing = [ep for ep in ix.functions if ep not in covered]
    print(f"{len(out)} dashboards, {sum(len(d['tabs']) for d in out)} tabs, {n_items} widget placements")
    print(f"functions covered: {len(ix.functions) - len(missing)}/{len(ix.functions)}; placed by fallback: {len(leftovers)}; parked as unavailable upstream: {n_down[0]}")
    print(f"concept families: {len(concepts)}; reached only through a source picker: {sum(1 for v in covered.values() if v.endswith('(source picker)'))}")
    for p in sorted(set(problems)):
        print("  WARN", p)
    LAST_REPORT.clear()
    LAST_REPORT.update(functions=len(ix.functions), missing=missing, fallback=list(leftovers), parked=n_down[0], warnings=sorted(set(problems)))
    if report:
        for d in out:
            print(f"\n[{d['section']}] {d['name']}")
            for t in d["tabs"]:
                print(f"   {t['name']:<34} {len(t['items']):>3}")
        if leftovers:
            print("\nfallback placements:")
            for ep in leftovers:
                print("   ", ep, "->", covered[ep])
    return result


if __name__ == "__main__":
    build(report="--report" in sys.argv)
