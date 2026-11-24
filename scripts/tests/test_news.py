"""terminal/news.py: feed parsing, subject matching, materiality, clustering, ranking, empty answers (offline)."""

import datetime as dt
import math

from _common import check, done

import composite
import news
import xwidgets

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 19, 12, 0, tzinfo=UTC)

# ---- parsing: RSS 2.0 with a Google News <source>, Atom, and a feed with a bare '&' (Bank of Japan, 2026-11-19)
rss = b"""<?xml version="1.0"?><rss><channel><title>x</title>
<item><title>Fed raises rates - Reuters</title><link>https://news.google.com/a</link><pubDate>Wed, 18 Nov 2026 19:00:00 GMT</pubDate>
<source url="https://www.reuters.com">Reuters</source><description>&lt;b&gt;Body&lt;/b&gt; text</description></item>
<item><title>No date here</title><link>https://x/b</link></item></channel></rss>"""
items = news.parse_feed(rss)
check(len(items) == 2 and items[0]["publisher"] == "Reuters" and items[0]["publisher_url"] == "https://www.reuters.com", "RSS item with <source>")
check(items[0]["date"] == dt.datetime(2026, 11, 18, 19, 0, tzinfo=UTC) and items[1]["date"] is None, "RFC 822 date to UTC; missing date is None")
check(items[0]["summary"] == "Body text", "HTML stripped from the description")
atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Suncor results</title><link rel="alternate" href="https://x/c"/>
<updated>2026-11-03T17:30:00-05:00</updated></entry></feed>"""
a = news.parse_feed(atom)[0]
check(a["url"] == "https://x/c" and a["date"] == dt.datetime(2026, 11, 3, 22, 30, tzinfo=UTC), "Atom entry: link href, offset date to UTC")
bad = b"""<rss><channel><item><title>Statement on R&D and Q&amp;A &nbsp;today</title><pubDate>Thu, 19 Nov 2026 01:13:00 +0900</pubDate></item></channel></rss>"""
b = news.parse_feed(bad)
check(len(b) == 1 and b[0]["title"].startswith("Statement on R&D and Q&A") and b[0]["date"] == dt.datetime(2026, 11, 18, 16, 13, tzinfo=UTC),
      "bare '&' and HTML entities recovered; +0900 to UTC")

# ---- names to aliases
check(news.aliases_from_name("Exxon Mobil Corporation") == ["Exxon Mobil", "Exxon"], "Exxon Mobil Corporation")
check(news.aliases_from_name("Suncor Energy Inc.") == ["Suncor Energy", "Suncor"], "Suncor Energy Inc.")
check(news.aliases_from_name("Apple Inc.") == ["Apple"] and news.aliases_from_name("SAP SE") == ["SAP"], "Apple Inc., SAP SE")
check(news.aliases_from_name("General Motors Company") == ["General Motors"], "a common first word is not an alias on its own")

# ---- subjects
composite._profiles = lambda syms: {s: {"name": {"AAPL": "Apple Inc.", "SAP.DE": "SAP SE", "XOM": "Exxon Mobil Corporation"}.get(s, ""),
                                        "sector": "Energy" if s == "XOM" else "Technology", "industry": ""} for s in syms}
apple = news.resolve(symbol="AAPL")
check(apple.name == "Apple (AAPL)" and apple.home == "us", "AAPL subject from the Yahoo profile")
check(apple.match("Apple pie recipes for the holidays")[0] == 0, "'Apple' without context is not Apple Inc.")
check(apple.match("Apple unveils a new iPhone")[0] == 3, "'Apple' with a product word: ambiguous-name hit")
check(apple.match("AAPL slides in late trading")[0] == 2 and apple.match("Pineapple prices jump")[0] == 0, "ticker hit; no match inside a word")
check(apple.match("Federal Reserve raises rates by 25 basis points") == (1, "Fed"), "home central bank is a labelled driver")
check(apple.match("Indian rupee hits roadblock after rally amid fears of back-to-back Federal Reserve hikes")[0] == 0,
      "a driver mentioned late in a headline about something else does not count")
xom = news.resolve(symbol="XOM")
check(xom.match("ExxonMobil starts Guyana output")[0] == 2 and xom.match("Brent crude jumps 3% on OPEC cut")[1] == "oil", "XOM: alias hit; oil is its sector driver")
sap = news.resolve(symbol="SAP.DE")
check(sap.queries[0][1:] == ("de", "DE") and sap.match("SAP-Aktie: Kurs gibt nach")[0] == 2, "SAP.DE: German Google News; 'SAP-Aktie' matches")
check(news.resolve(symbol="SU.TO").queries[0][1:] == ("en-CA", "CA"), "SU.TO: Canadian edition")
gold = news.resolve("gold")
check(gold.match("Goldman Sachs raises its S&P target")[0] == 0 and gold.match("Team wins gold medal in Milan")[0] == 0, "Goldman and gold medal are not gold")
check(gold.match("Gold slips as the dollar firms")[0] == 3 and gold.match("Spot gold hits a record")[0] == 2, "gold with context; spot gold")
crude = news.resolve("crude")
check(crude.match("Palm oil futures rise on Malaysian demand")[0] == 0 and crude.match("Oil prices fall as OPEC+ adds supply")[0] == 2, "palm oil is not crude")
check(news.resolve(symbol="GC=F").key == "gold" and news.resolve(symbol="^GSPC").market and news.resolve().market, "futures symbol -> commodity; index/none -> market")
check(news.resolve("country", country="euro_area").name == "Euro area" and news.resolve("c:japan").name == "Japan", "country subjects")
check(news.gnews_url("SAP when:3d", "de", "DE") == "https://news.google.com/rss/search?q=SAP+when%3A3d&hl=de&gl=DE&ceid=DE%3Ade", "Google News URL")

# ---- materiality
m, kinds = news.materiality("Fed raises interest rates by 25 basis points")
check(m == 2.5 and kinds == ["policy"], "rate decision weight 2.5")
check(news.materiality("Apple pie recipes")[0] == 0, "nothing material")


# ---- ranking: hand-built items, fixed clock
def it(title, pub, tier, hours_ago, feed="bloomberg"):
    return {"title": title, "url": "https://x/" + title[:8], "date": NOW - dt.timedelta(hours=hours_ago), "publisher": pub, "tier": tier, "feed": feed, "summary": ""}


mkt = news.market_subject()
rows = news.rank(mkt, [it("Fed raises interest rates by 25 basis points", "Bloomberg", 2, 0),
                       it("Fed raises interest rates by 25 basis points", "Reuters", 2, 0, "gnews"),
                       it("Stocks edge higher in quiet trading", "CNBC", 2, 1),
                       it("Fed raises rates again", "Bloomberg", 2, 60),                 # outside 48 h
                       {"title": "Stocks rally", "url": "", "date": None, "publisher": "X", "tier": 2, "feed": "x"},    # undated
                       it("3 stocks to buy now before the Fed meets", "Motley Fool", 4, 0, "gnews"),
                       it("My husband wants to retire at 64", "MarketWatch", 2, 0),       # not market news
                       ], hours=48, now=NOW)
check([r["title"] for r in rows] == ["Fed raises interest rates by 25 basis points", "Stocks edge higher in quiet trading"],
      "market top: duplicates folded, old/undated/opinion/off-topic dropped, material first")
r0 = rows[0]
check(r0["source"] == "Bloomberg +1" and r0["sources_n"] == 2 and r0["excerpt"] == "Also: Reuters", "two feeds, one line with a source count")
check(abs(r0["score"] - (2.0 + 1.2 * 2.5 + 1.5 * math.log2(2) + 3.0)) < 0.01 and r0["score"] == 9.5, "score = tier 2 + 1.2 x 2.5 + 1.5 x log2(2) + 3 x e^0 = 9.5")
check(r0["date"] == "2026-11-19T12:00:00Z" and r0["tag"] == "", "first report time, no label")

mon = news.rank(mkt, [it("Stocks edge higher", "CNBC", 2, 3), it("Treasury yields climb", "WSJ", 2, 1), it("ECB holds rates", "ECB", 1, 2, "ecb")],
                mode="monitor", hours=24, now=NOW)
check([r["title"] for r in mon] == ["Treasury yields climb", "ECB holds rates", "Stocks edge higher"] and mon[1]["tag"] == "Official", "monitor: newest first; official labelled")

drv = [it(t, "Bloomberg", 2, i) for i, t in enumerate(["Federal Reserve raises rates by a quarter point", "Federal Reserve minutes show hawkish tilt",
                                                    "Federal Reserve holds rates steady in surprise", "Federal Reserve cuts rate outlook for 2027",
                                                    "Federal Reserve policy statement flags inflation risk"])] + \
      [it("Apple unveils a new iPhone", "Bloomberg", 2, 1), it("AAPL results beat estimates", "CNBC", 2, 2),
       it("Apple pie sales soar", "Food Weekly", 3, 0), it("Apple unveils iPad", "Unknown Blog", 3, 0)]
ar = news.rank(apple, drv, hours=48, now=NOW)
check(sum(1 for r in ar if r["tag"] == "Fed") == 3 and len(ar) == 6 and ar[0]["title"] == "AAPL results beat estimates",
      "subject: at most three driver lines; results first; Apple pie dropped")
eur = news.resolve("eurusd")
er = news.rank(eur, [it("The dollar and the euro fell slightly in Armenia", "NEWS.am", 3, 1, "gnews"), it("EUR/USD slides to 1.09", "FXStreet", 2, 1, "fxstreet"),
                     it("Euro weakens against the dollar after ECB comments", "Bloomberg", 2, 2)], hours=48, now=NOW)
check(sorted(r["title"] for r in er) == ["EUR/USD slides to 1.09", "Euro weakens against the dollar after ECB comments"],
      "EUR/USD: an ambiguous 'euro' hit counts only from a wire or press source")

# ---- the view: empty beats filler; sources named
news.collect = lambda subj, feed_ids, queries=True: ([], set())
news._window = lambda h: h
out = news.news(subject="gold")
check(out["results"] == [] and out["empty"] == "No material news on Gold in the last 48 h." and out["order"] == "rank", "empty answer says so")
check(out["sources"] and out["sources"][0]["name"] == "Terminal news", "an empty answer still carries sources")
check(news.news(mode="monitor")["order"] == "time", "monitor order flag")
seen = []
news.resolve = lambda subject="", symbol="", country="": seen.append(symbol) or news.market_subject()
news.news(subject="symbol", symbol="BBD.B", listing="TO"); news.news(subject="symbol", symbol="RY", listing="TO"); news.news(subject="symbol", symbol="SU.TO", listing="TO")
check(seen == ["BBD-B.TO", "RY.TO", "SU.TO"], "TMX tickers get the Yahoo listing suffix once")
check(news._sources({"fed", "gnews", "sec_8k"}, apple)[0]["name"] == "Federal Reserve", "official feed listed first")
check(news._sources({"fed", "bloomberg"}, apple, [{"feed": "bloomberg"}])[0]["name"] == "Bloomberg", "the feed that supplied the lines shown comes first")
q = news.rank(mkt, [it("Stocks slide as yields jump", "CNBC", 2, 0), it("Is the stock market rally over?", "CNBC", 2, 0)], hours=48, now=NOW)
check([r["title"] for r in q][0] == "Stocks slide as yields jump" and q[0]["score"] - q[1]["score"] > 1.4, "a question headline ranks below a news headline")

# ---- _sec_8k / _tmx_releases do not poison C._cache on transient failure, but do cache permanent errors
_saved_api = composite.api
_sec_calls = [0]
_tmx_calls = [0]
try:
    def _flaky_sec(*a, **kw):
        _sec_calls[0] += 1
        if _sec_calls[0] == 1:
            raise composite.CompositeError("SEC down", transient=True)
        return [{"items": "2.02", "filing_date": "2026-11-18", "filing_detail_url": "https://sec/1"}]
    composite.api = _flaky_sec
    composite._cache.pop(("news_8k", "TESTSEC"), None)
    check(news._sec_8k("TESTSEC") == [], "_sec_8k returns [] on transient failure")
    check(len(news._sec_8k("TESTSEC")) == 1, "_sec_8k retries on next call instead of caching [] after transient error")

    def _perm_sec(*a, **kw):
        _sec_calls[0] += 1
        raise composite.CompositeError("HTTP 404 from SEC", transient=False)
    composite.api = _perm_sec
    composite._cache.pop(("news_8k", "NONUSPERM"), None)
    check(news._sec_8k("NONUSPERM") == [] and news._sec_8k("NONUSPERM") == [] and _sec_calls[0] == 3,
          "_sec_8k caches [] for 900s on permanent non-transient error")

    def _flaky_tmx(*a, **kw):
        _tmx_calls[0] += 1
        if _tmx_calls[0] == 1:
            raise composite.CompositeError("TMX down", transient=True)
        return [{"title": "TMX News", "url": "https://tmx/1", "date": "2026-11-18T12:00:00Z", "source": "CNW"}]
    composite.api = _flaky_tmx
    composite._cache.pop(("news_tmx", "TESTTMX"), None)
    check(news._tmx_releases("TESTTMX") == [], "_tmx_releases returns [] on transient failure")
    check(len(news._tmx_releases("TESTTMX")) == 1, "_tmx_releases retries on next call after transient error")

    def _perm_tmx(*a, **kw):
        _tmx_calls[0] += 1
        raise composite.CompositeError("HTTP 404 from TMX", transient=False)
    composite.api = _perm_tmx
    composite._cache.pop(("news_tmx", "TMXPERM"), None)
    check(news._tmx_releases("TMXPERM") == [] and news._tmx_releases("TMXPERM") == [] and _tmx_calls[0] == 3,
          "_tmx_releases caches [] for 900s on permanent non-transient error")
finally:
    composite.api = _saved_api
    composite._cache.pop(("news_8k", "TESTSEC"), None)
    composite._cache.pop(("news_8k", "NONUSPERM"), None)
    composite._cache.pop(("news_tmx", "TESTTMX"), None)
    composite._cache.pop(("news_tmx", "TMXPERM"), None)

# ---- wiring
check("news" in composite.ROUTES and composite.static_sources("news"), "route and static sources")
_ws = xwidgets.widgets()
w = [x for x in (_ws.values() if isinstance(_ws, dict) else _ws) if x["widgetId"] == "x_news"]
check(len(w) == 1 and w[0]["type"] == "newsfeed" and w[0]["data"]["dataKey"] == "results", "x_news widget: newsfeed reading results")
done("test_news")
