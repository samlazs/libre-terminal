"""Data status view reporting missing keys, broken upstream routes, and backend fixes."""

from __future__ import annotations

import json
import os
import local_paths

import composite as C

# ---------------------------------------------------------------- data status: what is down upstream, which keys are missing
BACKEND_NAMES = {"v5": "Main data service (V5)", "v4": "Legacy data service (V4)"}


def _patch_status_md() -> list:
    """The runtime fixes each backend applied at its last start (scripts/run_backend.py writes local/run/patches-<name>.json)."""
    rows = []
    with_v4 = os.environ.get("TERMINAL_WITH_V4", "").strip().lower() in ("1", "true", "yes") or (local_paths.RUN_DIR / "v4.json").exists()
    for name in (("v5", "v4") if with_v4 else ("v5",)):
        try:
            st = json.loads((local_paths.RUN_DIR / f"patches-{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        def cell(v, n):
            return str(v).replace("|", "/").replace("\n", " ")[:n]
        for p in st.get("applied") or []:
            rows.append(f"| {BACKEND_NAMES[name]} | {cell(p, 160)} | applied |")
        for p in st.get("failed") or []:
            if isinstance(p, dict):
                rows.append(f"| {BACKEND_NAMES[name]} | {cell(p.get('patch', ''), 120)} | **not applied**: {cell(p.get('why', ''), 160)} |")
    if not rows:
        return []
    return ["", "#### Backend fixes", "Fixes the terminal applies to the data backends in memory when they start (no installed file is changed). "
            "One that is not applied usually means the backend package changed; the affected sources keep working, without that fix.", "",
            "| Backend | Fix | Status |", "|---|---|---|"] + rows


def data_status(**_):
    """What the dashboard builder recorded on the last start: functions broken upstream and API keys not set (names only)."""
    try:
        st = json.loads(local_paths.DASHBOARDS_FILE.read_text(encoding="utf-8")).get("status") or {}
    except (OSError, ValueError):
        st = {}
    md = ["### Data status", f"As of the last dashboard build ({st.get('generated') or 'unknown'}). The terminal rebuilds this on every start.", ""]
    keys = st.get("keys") or []
    missing = [k for k in keys if not k.get("set") and k.get("cards")]
    md += ["#### API keys", "Everything on Markets Summary, Rates & Curves, Company and the boards works without keys. Cards that need a key you have not set say so and sit at the end of their tab.", ""]
    if keys:
        md += ["| Source | Key | Cards that use it | Free sign-up |", "|---|---|---:|---|"]
        for k in keys:
            if k.get("cards") or k.get("set"):
                md.append(f"| {k['source']} | {'set' if k.get('set') else '**not set**'} | {k.get('cards', 0)} | {k.get('signup') or '–'} |")
        md += ["", ("To add keys: run Setup-Keys.cmd (Windows) or `sh setup-keys.sh`, paste the keys, save, then stop and start the terminal." if missing else "All keys that cards use are set.")]
    broken = st.get("broken") or []
    md += ["", f"#### Broken upstream ({len(broken)})",
           "These functions currently fail at the source (a site moved a page or file, or the backend lost a route). Their cards are kept below so you can retry them; "
           "each one returns to its normal place once its line is removed from `terminal/health.json`.", ""]
    if broken:
        md += ["| Function | Normally in | Reason |", "|---|---|---|"]
        md += [f"| {b['name']} | {b['home']} | {b['reason']} |" for b in broken]
    md += C._patch_status_md()
    md += ["", "#### Known limits", "No real-time feeds (Yahoo Finance prices are delayed), no single-name CDS, U.S. Treasury security prices are blocked at the source, "
           "euro-area member curves are monthly (3-month and 10-year only). OECD data is cached for 12 hours because OECD limits how often it can be queried."]
    return {"content": "\n".join(md)}
