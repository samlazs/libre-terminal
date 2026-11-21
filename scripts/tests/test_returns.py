"""Board and chart returns (audit A5): total return (distributions reinvested) for boards made only of stocks and
funds, price change where any row is an index, future, currency or coin; never both in one column, and every view
says which. Expected values are worked out by hand."""

import datetime as dt

from _common import check, done

import composite as c

# ---- the audit's case: (d1, 100, 0), (d2, 99, 1.0), (d3, 99, 0)
ser = [("2026-10-15", 100.0), ("2026-11-02", 99.0), ("2026-11-16", 99.0)]
tr = c.total_return(ser, {"2026-11-02": 1.0})
check([d for d, _ in tr] == [d for d, _ in ser], "same dates")
check(abs(tr[1][1] - 99.99) < 1e-9 and abs(tr[2][1] - 99.99) < 1e-9, "99 x (1 + 1/100) = 99.99 from the ex-date on")
check(abs((ser[-1][1] / ser[0][1] - 1) - (-0.01)) < 1e-12 and abs((tr[-1][1] / tr[0][1] - 1) - (-0.0001)) < 1e-12, "price -1.00%, total return -0.01%")
check(c.total_return(ser, {}) == ser and c.total_return(ser, None) == ser, "no distributions: the price series itself")
two = c.total_return([("a", 50.0), ("b", 50.0), ("c", 40.0), ("d", 44.0)], {"b": 1.0, "d": 2.0})
check(abs(two[-1][1] - 44.0 * 1.02 * 1.05) < 1e-9, "two distributions compound: 44 x (1 + 1/50) x (1 + 2/40)")
check(c.total_return([("a", 10.0)], {"a": 1.0}) == [("a", 10.0)], "a distribution with no previous close is ignored")

# ---- which instruments have a total return
for s in ("HYG", "AAPL", "SAP.DE", "BRK-B", "NOVO-B.CO", "U-UN.TO", "7203.T"):
    check(c.is_security(s), f"{s}: a stock or fund")
for s in ("^GSPC", "^TNX", "GC=F", "JPY=X", "EURUSD=X", "BTC-USD", "DX-Y.NYB", "000001.SS", "FTSEMIB.MI", "OBX.OL"):
    check(not c.is_security(s), f"{s}: price only (index, future, currency, coin)")
tr_boards = {k for k, (_, secs) in c.BOARDS.items() if c.returns_basis([s for _, its in secs for _, s in its]) == "total"}
check(tr_boards == {"us_sectors", "us_factors", "us_industries", "megacaps", "global_markets", "defense", "shipping", "fixed_income", "themes",
                    "ai_complex", "datacenter_power", "private_markets", "banks"}, "boards of stocks and funds show total return: " + ", ".join(sorted(tr_boards)))

# ---- the views, on a history with one distribution
today = dt.date.today()


def days_ago(n):
    return (today - dt.timedelta(days=n)).isoformat()


def fake_history(divs):
    def history(syms, start, interval="1d"):
        h = c.PriceHistory({s: [(days_ago(400), 100.0), (days_ago(40), 100.0), (days_ago(15), 99.0), (days_ago(1), 99.0)] for s in syms})
        h.divs = {s: {days_ago(15): 1.0} for s in syms if s in divs}
        return h
    return history


saved = c.history
try:
    c._cache.clear()
    c.history = fake_history({"HYG", "BIL", "IWD"})
    out = c.board(preset="fixed_income")
    row = next(r for r in out["results"] if r.get("symbol") == "HYG")
    check(abs(row["m1"] - (-0.0001)) < 1e-12 and abs(row["m3"] - (-0.0001)) < 1e-12, "fixed-income board: 1M and 3M are total returns (-0.01%)")
    check(row["chg_pct"] == 0.0 and row["price"] == 99.0, "1D % and Last stay on prices")
    check(out["convention"].startswith("Total return"), "the board says its returns are total returns")
    agg = next(r for r in out["results"] if r.get("symbol") == "AGG")
    check(abs(agg["m1"] - (-0.01)) < 1e-12, "a fund with no distribution in the window: total return = price change")
    eq = c.board(preset="us_equity")
    check(eq["convention"].startswith("Price return") and abs(next(r for r in eq["results"] if r.get("symbol") == "^GSPC")["m1"] - (-0.01)) < 1e-12,
          "an index board: price change, and says so")
    bars = c.perf_bars(preset="fixed_income", period="1M")
    got = dict(zip(bars["data"][0]["y"], bars["data"][0]["x"]))
    check(got["High Yield"] == -0.01 and got["Aggregate"] == -1.0, "ranking: HYG -0.01% (total), AGG -1.00%")
    check("Total return" in " ".join(a["text"] for a in bars["layout"]["annotations"]), "ranking says total return")
    pb = c.perf_bars(preset="global_indices", period="1M")
    check("Price return" in " ".join(a["text"] for a in pb["layout"]["annotations"]), "index ranking says price change")
    perf = c.performance(symbols="HYG,AGG", period="1M")
    ys = {t["name"].split()[0]: t["y"][-1] for t in perf["data"]}
    check(ys == {"HYG": -0.01, "AGG": -1.0}, "performance chart of funds: total return in percent")
    check("Total return" in " ".join(a["text"] for a in perf["layout"]["annotations"]), "performance chart says total return")
    mixed = c.performance(symbols="HYG,^GSPC", period="1M")
    check({t["name"].split()[0]: t["y"][-1] for t in mixed["data"]} == {"HYG": -1.0, "^GSPC": -1.0}, "fund next to an index: both price change")
    check("Price return" in " ".join(a["text"] for a in mixed["layout"]["annotations"]), "mixed chart says price change")
    box = c.style_box(period="1M")
    check(abs(box["results"][0]["value"] - (-0.0001)) < 1e-12 and abs(box["results"][0]["core"] - (-0.01)) < 1e-12, "style box: IWD total return, IWB price = total (no distribution)")
    check(box["convention"].startswith("Total return"), "style box says total return")
finally:
    c.history = saved
    c._cache.clear()

done("test_returns")
