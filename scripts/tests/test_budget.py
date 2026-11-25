"""End-to-end time budgets: one route table (core/contract.py ROUTE_BUDGETS) for the server and the browser, a request
deadline that cuts every outbound call to the time left, and no unbounded wait in the backend's slow-source policy."""

import ast
import re
import time
from pathlib import Path

from _common import check, done
from _fixtures import no_retry_delays, serve

import composite as C
from composite.core import contract
from composite.equity import lookup
import server

ROOT = Path(__file__).resolve().parents[2]

# ---- the table: real views, compilable patterns, the browser always waits longer than the server
views = contract.ROUTE_BUDGETS["views"]
check(not [v for v in views if v not in C.ROUTES], f"every budgeted view exists: {[v for v in views if v not in C.ROUTES]}")
client = contract.client_budgets()
check(all(views[v] + 2 <= client["views"][v] / 1000 for v in views), "every view: server budget + 2 s <= browser timeout")
check(contract.ROUTE_BUDGETS["default"] + 2 <= client["default_ms"] / 1000 and contract.ROUTE_BUDGETS["files"] + 2 <= client["files_ms"] / 1000,
      "default and files: server budget + 2 s <= browser timeout")
check(all(s + 2 <= ms / 1000 for (_, s), (_, ms) in zip(contract.ROUTE_BUDGETS["backend"], client["patterns"])),
      "every backend route: budget + 2 s <= browser timeout")
for pat, _ in contract.ROUTE_BUDGETS["backend"]:
    re.compile(pat)
check(server.config_for("127.0.0.1:6950")["budgets"] == client, "/api/config serves the table to the page")


def browser_ms(url):
    """What T.requestTimeout answers for a URL, computed from the served table the same way widgets.js does."""
    m = re.search(r"/x/([A-Za-z0-9_]+)", url)
    if m and m.group(1) in client["views"]:
        return client["views"][m.group(1)]
    for pat, ms in client["patterns"]:
        if re.search(pat, url):
            return ms
    return client["default_ms"]


expect = {"/x/board?preset=us_equity": 30000, "/api/v1/yfinance/estimates/recommendations?symbol=AAPL": 30000, "/x/chart?symbol=AAPL": 60000,
          "/api/v1/cboe/options/surface?symbol=SPX": 70000, "/api/v1/derivatives/options/chains?provider=cboe&symbol=SPX": 70000,
          "/x/policy_path?currency=USD": 70000, "/x/global_yields?countries=g10": 70000, "/api/v1/oecd/gdp?country=US": 70000,
          "/api/v1/economy/cpi?provider=imf&country=japan": 70000, "/x/snapshot?symbol=7203.T": 30000, "/api/v1/custom/endpoint": 45000}
got = {u: browser_ms(u) for u in expect}
check(got == expect, f"route timeouts as before the move to one table: {[(u, got[u]) for u in expect if got[u] != expect[u]]}")
js = (ROOT / "terminal" / "static" / "widgets.js").read_text(encoding="utf-8")
check("global_yields|policy_path" not in js and "return 70000" not in js and "T.setBudgets" in js,
      "widgets.js has no timeout table of its own: it reads /api/config")
check("T.setBudgets(cfg.budgets)" in (ROOT / "terminal" / "static" / "app.js").read_text(encoding="utf-8"), "the page loads the budgets at boot")

# ---- the backend's slow-source policy: every 'answer within' is bounded by the route budget that reaches the provider
src = (ROOT / "scripts" / "run_backend.py").read_text(encoding="utf-8")
node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "CACHE_POLICY")
policy = eval(compile(ast.Expression(node.value), "CACHE_POLICY", "eval"), {"__builtins__": {}})   # constants and 6 * 3600 only
token = {"openbb_us_eia": "eia", "openbb_famafrench": "famafrench", "openbb_government_us": "us_treasury", "openbb_cftc": "cftc",
         "openbb_bls": "bls", "openbb_usda": "usda", "openbb_imf": "imf", "openbb_oecd": "oecd", "openbb_ecb": "ecb",
         "openbb_federal_reserve": "federal_reserve", "openbb_fred": "fred", "openbb_jodi": "jodi"}
for pkg, (_ttl, bound, _att, label) in policy.items():
    tok = token.get(pkg, pkg.replace("openbb_", ""))
    route = next((s for p, s in contract.ROUTE_BUDGETS["backend"] if re.search(p, f"/api/v1/x?provider={tok}") or re.search(p, f"/api/v1/{tok}/x")),
                 contract.ROUTE_BUDGETS["default"])
    check(isinstance(bound, (int, float)) and 0 < bound <= route, f"{label}: answers within {bound} s <= its route budget {route} s")

# ---- the deadline: time_left, nesting, and fetch refusing to start with no time left
check(contract.time_left() is None and contract.time_left(7) == 7, "no deadline: callers keep their own timeouts")
tok = contract.set_deadline(10)
try:
    left = contract.time_left()
    check(8.5 < left <= 9.0, f"10 s budget leaves ~9 s for calls (1 s kept to answer): {left:.2f}")
    check(contract.time_left(3) == 3, "a shorter cap wins")
    inner = contract.set_deadline(60)
    check(contract.time_left() <= 9.0, "a nested deadline never extends the running one")
    contract._DEADLINE.reset(inner)
finally:
    contract._DEADLINE.reset(tok)
tok = contract.set_deadline(1.2)
try:
    with serve({r"example\.test": {"ok": 1}}) as rec:
        try:
            C.http_json("https://example.test/a", timeout=30)
            e = None
        except C.CompositeError as err:
            e = err
    check(e is not None and e.kind == "timeout" and rec.calls == 0, f"no time left: not asked, typed timeout ({e})")
finally:
    contract._DEADLINE.reset(tok)

# ---- a slow source makes the view partial within its budget (snapshot: 2 of 5 parts hang)
PROFILE = [{"symbol": "TSTB", "name": "Test Bank Holdings", "currency": "USD", "sector": "Technology", "industry": "Software",
            "long_description": "Makes test software.", "hq_country": "Canada"}]
QUOTE = [{"symbol": "TSTB", "name": "Test Bank Holdings", "last_price": 10.0, "prev_close": 9.5, "currency": "USD"}]
saved_views, saved_prefetch = dict(views), lookup.PREFETCH_ESTIMATES
try:
    lookup.PREFETCH_ESTIMATES = False
    views["snapshot"] = 4
    C.reset_runtime_caches()
    with no_retry_delays(), serve({r"/equity/profile": PROFILE, r"/equity/price/quote": QUOTE, r"/yfinance/company_calendar": [{}],
                                   r"/equity/fundamental/metrics": "hang", r"/equity/estimates/consensus": "hang"}):
        t0 = time.monotonic()
        out = C.dispatch("snapshot", {"symbol": "TSTB"})
        wall = time.monotonic() - t0
    check(wall < 4.0, f"snapshot answered within its 4 s budget ({wall:.2f} s)")
    check(out["status"] == "partial" and any("metrics" in n and "consensus" in n for n in out["notes"]), f"partial, naming both: {out.get('notes')}")
    check("Test Bank Holdings" in out["content"], "the parts that answered are shown")

    # comps: company profiles hang; quotes and metrics answer
    views["comps"] = 5
    C.reset_runtime_caches()
    C._METRICS.clear()
    MET = [{"symbol": s, "pe_ratio": pe, "market_cap": 1e9, "enterprise_value": 1.1e9, "currency": "USD"} for s, pe in (("AAA", 20.0), ("BBB", 25.0), ("CCC", 30.0))]
    QUOTES = [{"symbol": s, "name": f"{s} Corp", "currency": "USD", "last_price": 10.0} for s in ("AAA", "BBB", "CCC")]
    with no_retry_delays(), serve({r"/equity/profile": "hang", r"/equity/price/quote": QUOTES, r"/equity/fundamental/metrics": MET}):
        t0 = time.monotonic()
        out = C.dispatch("comps", {"symbol": "AAA", "peers": "BBB,CCC"})
        wall = time.monotonic() - t0
    check(wall < 5.0, f"comps answered within its 5 s budget ({wall:.2f} s)")
    check(out["status"] == "partial" and any("profiles" in n for n in out["notes"]), f"comps partial, profiles named: {out.get('notes')}")
    pes = {r["symbol"]: r.get("pe") for r in out["results"] if r.get("symbol")}
    check(pes == {"AAA": 20.0, "BBB": 25.0, "CCC": 30.0}, f"metrics still shown: {pes}")
finally:
    views.clear()
    views.update(saved_views)
    lookup.PREFETCH_ESTIMATES = saved_prefetch
    C._METRICS.clear()
    C.reset_runtime_caches()

done("test_budget")
