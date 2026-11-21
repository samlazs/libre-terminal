"""News for the terminal: subject-aware headlines from official, wire and regional feeds (all keyless).

Design (HANDOFF "News / situation room"; feeds probed 2026-11-19, reasons and rejected candidates recorded there):
- Sources come in tiers: 1 = primary (central banks, statistics offices, regulators, EIA, the issuer's own SEC 8-K or
  TMX release), 2 = wires and financial press, 3 = aggregators (Google News search, Yahoo ticker feed). A paywalled
  outlet is a headline and a link only; no article body is fetched.
- A subject is more than a keyword: the entity (company names and short names, ticker, country, currency,
  commodity) and its drivers (home central bank, oil for an oil company...). A driver hit is labelled ("Fed", "oil")
  and counts only when the headline is material and comes from a tier 1 or 2 source.
- Matching is on word boundaries; an ambiguous name ("Apple", "euro", "gold") counts only next to its ticker or a
  context word. Listicles and buy/sell opinion pieces are dropped for a subject, demoted in the market view.
- Ranking: materiality (results, guidance, M&A, ratings, rate decisions, data releases, sanctions, outages,
  supply disruptions), source tier, recency, and the number of feeds carrying the same story (duplicates are
  clustered into one line with a source count).
- Empty beats filler: nothing relevant in the window is said as such, never padded with unrelated headlines.
Views: mode "top" (ranked; Markets Summary, subject cards) and "monitor" (newest first; the situation room).
"""

import concurrent.futures as _cf
import datetime as dt
import email.utils
import html
import math
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

import composite as C

# ------------------------------------------------------------------ feeds
# id: (label, url, tier, tags, home page)
FEEDS = {
    # tier 1: official
    "fed": ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", 1, {"us", "cb", "official"}, "https://www.federalreserve.gov/newsevents.htm"),
    "fed_speech": ("Federal Reserve (speeches)", "https://www.federalreserve.gov/feeds/speeches.xml", 1, {"us", "cb", "official"}, "https://www.federalreserve.gov/newsevents/speeches.htm"),
    "ecb": ("ECB", "https://www.ecb.europa.eu/rss/press.html", 1, {"ea", "cb", "official"}, "https://www.ecb.europa.eu/press/html/index.en.html"),
    "ecb_stats": ("ECB (statistics)", "https://www.ecb.europa.eu/rss/statpress.html", 1, {"ea", "data", "official"}, "https://www.ecb.europa.eu/press/stats/html/index.en.html"),
    "boe": ("Bank of England", "https://www.bankofengland.co.uk/rss/news", 1, {"uk", "cb", "official"}, "https://www.bankofengland.co.uk/news"),
    "boj": ("Bank of Japan", "https://www.boj.or.jp/en/rss/whatsnew.xml", 1, {"jp", "cb", "official"}, "https://www.boj.or.jp/en/"),
    "boc": ("Bank of Canada", "https://www.bankofcanada.ca/content_type/press-releases/feed/", 1, {"ca", "cb", "official"}, "https://www.bankofcanada.ca/press/"),
    "snb": ("Swiss National Bank", "https://www.snb.ch/public/en/rss/news", 1, {"ch", "cb", "official"}, "https://www.snb.ch/en/"),
    "riksbank": ("Riksbank", "https://www.riksbank.se/en-gb/rss/press-releases/", 1, {"se", "cb", "official"}, "https://www.riksbank.se/en-gb/press-and-published/"),
    "bundesbank": ("Bundesbank", "https://www.bundesbank.de/service/rss/en/633306/feed.rss", 1, {"de", "ea", "official"}, "https://www.bundesbank.de/en"),
    "bea": ("BEA", "https://apps.bea.gov/rss/rss.xml", 1, {"us", "data", "official"}, "https://www.bea.gov/news/current-releases"),
    "census": ("Census Bureau", "https://www.census.gov/economic-indicators/indicator.xml", 1, {"us", "data", "official"}, "https://www.census.gov/economic-indicators/"),
    "ons": ("ONS", "https://www.ons.gov.uk/releasecalendar?rss", 1, {"uk", "data", "official"}, "https://www.ons.gov.uk/releasecalendar"),
    "destatis": ("Destatis", "https://www.destatis.de/SiteGlobals/Functions/RSSFeed/DE/RSSNewsfeed/Aktuell.xml", 1, {"de", "ea", "data", "official"}, "https://www.destatis.de/"),
    "eia": ("EIA", "https://www.eia.gov/rss/todayinenergy.xml", 1, {"us", "energy", "official"}, "https://www.eia.gov/todayinenergy/"),
    "eia_press": ("EIA (press)", "https://www.eia.gov/rss/press_rss.xml", 1, {"us", "energy", "official"}, "https://www.eia.gov/pressroom/releases.php"),
    "sec": ("SEC", "https://www.sec.gov/news/pressreleases.rss", 1, {"us", "reg", "official"}, "https://www.sec.gov/newsroom/press-releases"),
    "cftc": ("CFTC", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", 1, {"us", "reg", "official"}, "https://www.cftc.gov/PressRoom/PressReleases"),
    # bank supervisors and regulators (probed 2026-11-19; FDIC and CFPB feeds refuse or are gone: see HANDOFF)
    "fed_bankreg": ("Federal Reserve (bank regulation)", "https://www.federalreserve.gov/feeds/press_bcreg.xml", 1, {"us", "bankreg", "official"}, "https://www.federalreserve.gov/supervisionreg.htm"),
    "fed_enforce": ("Federal Reserve (enforcement)", "https://www.federalreserve.gov/feeds/press_enforcement.xml", 1, {"us", "bankreg", "official"}, "https://www.federalreserve.gov/supervisionreg/enforcementactions.htm"),
    "occ": ("OCC", "https://www.occ.gov/rss/occ_news.xml", 1, {"us", "bankreg", "official"}, "https://www.occ.gov/news-events/newsroom/"),
    "fsb": ("Financial Stability Board", "https://www.fsb.org/feed/", 1, {"bankreg", "official"}, "https://www.fsb.org/press/"),
    "ecb_ssm": ("ECB Banking Supervision", "https://www.bankingsupervision.europa.eu/rss/press.html", 1, {"ea", "bankreg", "official"}, "https://www.bankingsupervision.europa.eu/press/html/index.en.html"),
    "eba": ("European Banking Authority", "https://www.eba.europa.eu/news-press/news/rss.xml", 1, {"ea", "bankreg", "official"}, "https://www.eba.europa.eu/publications-and-media/press-releases"),
    # tier 2: wires and financial press (headline + link)
    "bloomberg": ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", 2, {"wire", "markets"}, "https://www.bloomberg.com/markets"),
    "bloomberg_econ": ("Bloomberg", "https://feeds.bloomberg.com/economics/news.rss", 2, {"wire", "macro"}, "https://www.bloomberg.com/economics"),
    "ft_markets": ("Financial Times", "https://www.ft.com/markets?format=rss", 2, {"wire", "markets"}, "https://www.ft.com/markets"),
    "ft": ("Financial Times", "https://www.ft.com/?format=rss", 2, {"wire", "macro"}, "https://www.ft.com/"),
    "wsj_markets": ("Wall Street Journal", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", 2, {"wire", "markets"}, "https://www.wsj.com/finance"),
    "mw": ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins", 2, {"wire", "markets"}, "https://www.marketwatch.com/"),
    "cnbc": ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", 2, {"wire", "markets"}, "https://www.cnbc.com/"),
    "cnbc_fin": ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html", 2, {"wire", "macro"}, "https://www.cnbc.com/finance/"),
    "nyt": ("New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", 2, {"wire", "macro"}, "https://www.nytimes.com/section/business"),
    "investinglive": ("investingLive", "https://investinglive.com/feed", 2, {"wire", "macro", "fx", "markets"}, "https://investinglive.com/"),
    "bbc": ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml", 2, {"wire", "uk"}, "https://www.bbc.co.uk/news/business"),
    "euronews": ("Euronews", "https://www.euronews.com/rss?level=vertical&name=business", 2, {"wire", "ea"}, "https://www.euronews.com/business"),
    "economist": ("The Economist", "https://www.economist.com/finance-and-economics/rss.xml", 2, {"wire", "macro"}, "https://www.economist.com/finance-and-economics"),
    # tier 2: regional press
    "globe": ("Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/business/", 2, {"ca"}, "https://www.theglobeandmail.com/business/"),
    "cbc": ("CBC", "https://www.cbc.ca/webfeed/rss/rss-business", 2, {"ca"}, "https://www.cbc.ca/news/business"),
    "handelsblatt": ("Handelsblatt", "https://www.handelsblatt.com/contentexport/feed/finanzen", 2, {"de", "ea"}, "https://www.handelsblatt.com/finanzen/"),
    "tagesschau": ("tagesschau", "https://www.tagesschau.de/wirtschaft/index~rss2.xml", 2, {"de", "ea"}, "https://www.tagesschau.de/wirtschaft"),
    "scmp": ("South China Morning Post", "https://www.scmp.com/rss/92/feed", 2, {"asia", "cn"}, "https://www.scmp.com/business"),
    "mint": ("Mint", "https://www.livemint.com/rss/markets", 2, {"asia", "in"}, "https://www.livemint.com/market"),
    # tier 2: sector press
    "oilprice": ("OilPrice.com", "https://oilprice.com/rss/main", 2, {"energy"}, "https://oilprice.com/"),
    "rigzone": ("Rigzone", "https://www.rigzone.com/news/rss/rigzone_latest.aspx", 2, {"energy"}, "https://www.rigzone.com/news/"),
    "mining": ("MINING.COM", "https://www.mining.com/feed/", 2, {"metals"}, "https://www.mining.com/"),
    "fxstreet": ("FXStreet", "https://www.fxstreet.com/rss/news", 2, {"fx", "metals"}, "https://www.fxstreet.com/news"),
    "coindesk": ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", 2, {"crypto"}, "https://www.coindesk.com/"),
    "theblock": ("The Block", "https://www.theblock.co/rss.xml", 2, {"crypto"}, "https://www.theblock.co/"),
    "decrypt": ("Decrypt", "https://decrypt.co/feed", 2, {"crypto"}, "https://decrypt.co/"),
    # tier 3: aggregator
    "gnews_business": ("Google News", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en", 3, {"aggregator", "markets"}, "https://news.google.com/"),
}
WIRES = ["bloomberg", "bloomberg_econ", "ft_markets", "ft", "wsj_markets", "mw", "cnbc", "cnbc_fin", "nyt", "investinglive", "economist"]
OFFICIAL = [k for k, v in FEEDS.items() if v[2] == 1 and "bankreg" not in v[3]]      # bank supervision has its own monitor
BANK_REGULATORS = [k for k, v in FEEDS.items() if "bankreg" in v[3]]
REGIONS = {   # monitor "feeds" choice -> (label, feed ids)
    "ca": ("Canada", ["boc", "globe", "cbc"]),
    "ea": ("Euro area & Germany", ["ecb", "ecb_stats", "bundesbank", "destatis", "handelsblatt", "tagesschau", "euronews"]),
    "uk": ("United Kingdom", ["boe", "ons", "bbc"]),
    "jp": ("Japan", ["boj"]),
    "asia": ("Asia", ["boj", "scmp", "mint"]),
    "banks": ("Bank regulators and supervisors", BANK_REGULATORS),
}
TIER_W = {1: 3.0, 2: 2.0, 3: 1.0}

# publishers seen through Google News or Yahoo: wires and press count as tier 2, official domains as tier 1;
# the rest stays tier 3, and outlets that mostly publish buy/sell opinion are demoted.
PRESS = ("reuters", "bloomberg", "financial times", "wall street journal", "wsj", "cnbc", "associated press", "ap news", "nikkei", "economist",
         "marketwatch", "barron", "handelsblatt", "les echos", "globe and mail", "financial post", "bnn bloomberg", "kyodo", "japan times", "nhk",
         "south china morning post", "caixin", "frankfurter allgemeine", "faz", "süddeutsche", "börsen-zeitung", "boersen-zeitung", "il sole",
         "expansión", "axios", "politico", "coindesk", "the block", "fox business", "new york times", "washington post", "bbc", "guardian",
         "le monde", "euronews", "rigzone", "argus", "s&p global", "kitco", "investinglive", "der spiegel", "spiegel", "welt", "tagesschau",
         "manager magazin", "wirtschaftswoche", "capital.de", "the canadian press", "cbc", "ctv news", "toronto star", "dpa", "afp", "agence france")
LOW = ("motley fool", "fool.com", "zacks", "simply wall", "24/7 wall", "insider monkey", "trefis", "gurufocus", "kiplinger", "marketbeat", "ainvest",
       "benzinga private", "stocktitan", "stock titan", "seeking alpha", "tipranks", "investorplace", "the street", "thestreet", "finbold",
       "coinpedia", "u.today", "stock traders daily", "traders union", "beincrypto", "moneycheck", "aktiencheck", "finanztrends", "der aktionär", "deraktionaer", "boerse-express")
OFFICIAL_DOMAINS = (".gov", "ecb.europa.eu", "europa.eu", "bankofengland.co.uk", "boj.or.jp", "bankofcanada.ca", "snb.ch", "riksbank.se",
                    "bundesbank.de", "destatis.de", "ons.gov.uk", "opec.org", "iea.org", "imf.org", "bis.org", "statcan.gc.ca", "mof.go.jp")


# ------------------------------------------------------------------ fetching
_FEED_CACHE: dict = {}             # url -> (time fetched, items); kept on failure so a feed that stops answering ages out
_FEED_FAIL: dict = {}              # url -> time of the last failure (no retry for 10 minutes)
_FEED_ERR: dict = {}               # url -> last error text
_lock = threading.Lock()
_pool = _cf.ThreadPoolExecutor(max_workers=12, thread_name_prefix="news")
_GNEWS_GAP = 2.5                   # Google News answers 429 to a burst (probed: the 4th query within a few seconds)
_gnews_lock = threading.Lock()
_gnews_last = [0.0]


def _ptime(s):
    """RSS (RFC 822), Atom (ISO 8601) or dc:date -> aware UTC datetime, or None."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc)


_TAG = re.compile(r"<[^>]+>")


def _text(s):
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", html.unescape(s or "")))).strip()


def parse_feed(body):
    """[{title, url, date (aware UTC datetime or None), publisher, publisher_url, summary}] from RSS 2.0, RSS 1.0 or Atom."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:                       # a bare '&' in a title (Bank of Japan, 2026-11-19): escape it and read again
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        text = re.sub(r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)", "&amp;", text)
        text = re.sub(r"&([A-Za-z][A-Za-z0-9]*);", lambda m: m.group(0) if m.group(1) in ("amp", "lt", "gt", "quot", "apos") else
                      (f"&#{ord(html.unescape(m.group(0)))};" if len(html.unescape(m.group(0))) == 1 else "&amp;" + m.group(1) + ";"), text)
        root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", text.lstrip("﻿ \r\n")))
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] not in ("item", "entry"):
            continue
        r = {"title": "", "url": "", "date": None, "publisher": "", "publisher_url": "", "summary": ""}
        for c in el:
            t = c.tag.split("}")[-1]
            if t == "title":
                r["title"] = _text(c.text)
            elif t == "link":
                href = c.get("href")
                if href and (c.get("rel") in (None, "alternate")):
                    r["url"] = r["url"] or href.strip()
                elif c.text and c.text.strip():
                    r["url"] = r["url"] or c.text.strip()
            elif t in ("pubDate", "published", "date", "updated", "issued") and r["date"] is None:
                r["date"] = _ptime(c.text)
            elif t == "source":
                r["publisher"] = _text(c.text)
                r["publisher_url"] = c.get("url") or ""
            elif t in ("description", "summary") and not r["summary"]:
                r["summary"] = _text(c.text)[:300]
        if r["title"]:
            out.append(r)
    return out


def _get(url, ua=C.BROWSER_UA):
    return C._download(url, timeout=15, ua=ua)


def fetch(url, ttl=300, gnews=False):
    """A feed's items, at most `ttl` seconds old. A feed that fails keeps its last items (they age out of the time
    window by themselves) and is not asked again for 10 minutes."""
    now = time.time()
    with _lock:
        hit = _FEED_CACHE.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
        if now - _FEED_FAIL.get(url, 0) < 600:
            return hit[1] if hit else []
    try:
        if gnews:
            with _gnews_lock:                       # one Google News query at a time, spaced
                wait = _gnews_last[0] + _GNEWS_GAP - time.time()
                if wait > 0:
                    time.sleep(wait)
                try:
                    body = _get(url)
                finally:
                    _gnews_last[0] = time.time()
        else:
            body = _get(url)
        items = parse_feed(body)
    except Exception as e:  # noqa: BLE001 - a feed down or malformed must not take the card with it
        with _lock:
            _FEED_FAIL[url] = time.time()
            _FEED_ERR[url] = str(e)[:200]
        return hit[1] if hit else []
    with _lock:
        _FEED_CACHE[url] = (time.time(), items)
        _FEED_FAIL.pop(url, None)
        _FEED_ERR.pop(url, None)
        if len(_FEED_CACHE) > 300:
            for k in sorted(_FEED_CACHE, key=lambda k: _FEED_CACHE[k][0])[:60]:
                _FEED_CACHE.pop(k, None)
    return items


def gnews_url(q, lang="en-US", country="US"):
    hl = lang
    ceid = f"{country}:{lang.split('-')[0]}"
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": hl, "gl": country, "ceid": ceid})


# ------------------------------------------------------------------ classification
def _publisher_tier(name, url=""):
    n, u = (name or "").lower(), (url or "").lower()
    if any(d in u for d in OFFICIAL_DOMAINS):
        return 1
    if any(p in n for p in LOW):
        return 4                                    # below the aggregator itself: opinion mills
    if any(p in n for p in PRESS):
        return 2
    return 3


MATERIAL = [   # (pattern, weight, kind)
    (r"\b(raises?|cuts?|hikes?|holds?|keeps?|leaves?|lowers?)\b.{0,40}\b(rates?|interest rates?|policy rate|key rate|benchmark rate)\b|\brate (hike|cut|decision|increase|rise)s?\b|"
     r"\bminutes\b|\bmonetary policy\b|\bpolicy (decision|statement|meeting)\b|\bFOMC\b|\bLeitzins|\bZinsentscheid|\bZinserhöhung|\bZinssenkung", 2.5, "policy"),
    (r"\b(CPI|PCE|PPI|inflation|payrolls|jobs report|jobless|unemployment|GDP|PMI|retail sales|industrial production|housing starts|trade balance|"
     r"trade deficit|consumer confidence|Inflation|Arbeitslos|Konjunktur|BIP|Ifo|ZEW|Tankan|core prices)\b", 2.0, "data"),
    (r"\b(results|earnings|quarterly|profit|revenue|sales|guidance|outlook|forecast|beats?|misses|tops estimates|"
     r"profit warning|warns|Quartal|Gewinn|Umsatz|Prognose|Gewinnwarnung|Ergebnis)\b", 1.8, "results"),
    (r"\b(acquire[sd]?|acquisition|merger|merge|takeover|buyout|bid for|to buy|deal to|stake in|divest|spin[- ]?off|Übernahme|Fusion)\b", 2.0, "deal"),
    (r"\b(downgrades?|upgrades?|downgraded|upgraded|credit rating|outlook (to )?negative|outlook (to )?positive|Moody's|Fitch|price target)\b", 1.4, "rating"),
    (r"\b(sanctions?|tariffs?|embargo|export (ban|controls?|curbs?)|price cap|Zölle?)\b", 1.6, "policy-trade"),
    (r"\b(outage|strike|halt(s|ed)?|shut(s|down)?|explosion|fire at|attack(s|ed)?|disruption|blockade|recall|hack(ed)?|breach|default(s|ed)?|"
     r"bankruptcy|insolvency|leak|spill|Insolvenz|Streik)\b", 1.6, "disruption"),
    (r"\b(lawsuit|probe|investigation|charged|fine[sd]?|settle(s|ment)?|antitrust|SEC charges|Klage|Ermittlung)\b", 1.2, "legal"),
    (r"\b(OPEC\+?|output (cut|increase)|production (cut|quota)|inventor(y|ies)|stockpiles?|crude stocks?)\b", 1.6, "supply"),
    (r"\b(plunges?|plummets?|surges?|soars?|tumbles?|slumps?|sinks?|jumps?|crash(es)?|sell-?off|rout|record high|record low|worst (day|week)|best (day|week)|"
     r"all-time high|bricht ein|Kurssturz|Rekordhoch)\b", 1.0, "move"),
    (r"\b(dividend|buyback|share repurchase|IPO|listing|delisting|Dividende|Aktienrückkauf|Börsengang)\b", 1.0, "capital"),
    (r"\b(CEO|chief executive|CFO|resigns?|steps? down|appoints?|names new|Vorstandschef|Rücktritt)\b", 1.0, "people"),
]
MATERIAL = [(re.compile(p, re.I if k not in ("data",) else 0), w, k) for p, w, k in MATERIAL]
OPINION = re.compile(r"\b(should you|better buy|is it time to|here'?s why|here is why|buy now|stocks? to (buy|watch|own)|millionaire|retire(ment)?\b|"
                     r"could (soar|double|triple)|to buy (now|today|in)|is .{1,40} a buy|(top|best) \d+\b|\d+ (top-ranked |high-yield |dividend )?(stocks|picks|reasons|things|etfs)\b|"
                     r"featured highlights|save \$\d+|black friday|deal alert|best deals?\b|discount|zacks rank|bull of the day|bear of the day|unusual options|undervalued|overvalued|price prediction|"
                     r"what to know before|stock (price|quote).{0,20}(news|history)|my (husband|wife)|ask an advisor)", re.I)
QUOTE_PAGE = re.compile(r"\b(stock price|share price)\b.{0,30}\b(quote|news|history|chart)\b|seasonal returns chart|price dynamics|stock forecast", re.I)


def materiality(title):
    w, kinds = 0.0, []
    for rx, wt, k in MATERIAL:
        if rx.search(title):
            w += wt
            kinds.append(k)
    return min(w, 5.0), kinds


def _alt(terms):
    return "|".join(sorted({re.escape(t).replace(r"\ ", r"[\s-]") for t in terms}, key=len, reverse=True))


def word_rx(terms, case=False):
    """Word-boundary regex for names: 'S&P 500', 'Bank of Japan', 'EUR/USD' work; 'Goldman' does not match 'gold'."""
    terms = [t for t in terms if t]
    if not terms:
        return None
    return re.compile(r"(?<![\w$])(?:" + _alt(terms) + r")(?![\w])", 0 if case else re.I)


class Subject:
    """What a card is about: entity terms (direct hits), ambiguous names (need context), drivers (labelled)."""

    def __init__(self, key, name, terms=(), case_terms=(), ambiguous=(), context=(), drivers=(), exclude=(), feeds=(), queries=(),
                 primary=None, home=None, market=False, amb_min_tier=3):
        self.key, self.name = key, name
        self.rx = word_rx(terms)
        self.rx_case = word_rx(case_terms, case=True)
        self.amb = word_rx(ambiguous)
        self.ctx = word_rx(context)
        self.exclude = re.compile("|".join(exclude), re.I) if exclude else None
        self.drivers = [(label, word_rx(ts) if not case else word_rx(ts, case=True)) for label, ts, case in drivers]
        self.feeds = list(dict.fromkeys(feeds))
        self.queries = list(queries)                # [(q, lang, country)] Google News searches
        self.primary = primary                      # callable -> extra items (issuer filings and releases)
        self.home = home
        self.market = market
        self.amb_min_tier = amb_min_tier            # an ambiguous-name hit from a lower tier is dropped (local "dollar and euro" fixings)

    def match(self, text):
        """(hit, label): hit 2 = the subject itself, 1 = a driver (label says which), 0 = off topic."""
        if self.exclude and self.exclude.search(text):
            return 0, ""
        if (self.rx and self.rx.search(text)) or (self.rx_case and self.rx_case.search(text)):
            return 2, ""
        if self.amb and self.amb.search(text) and self.ctx and self.ctx.search(text):
            return 3, ""
        for label, rx in self.drivers:
            m = rx.search(text) if rx else None
            if m and m.start() <= 40:               # the driver leads the headline ("Fed minutes show..."), not a side mention
                return 1, label
        return 0, ""


# ------------------------------------------------------------------ subjects
MARKET_TERMS = ["stocks", "stock market", "shares", "equities", "markets", "Wall Street", "S&P 500", "S&P", "Dow", "Dow Jones", "Nasdaq", "Russell 2000",
                "Nikkei", "Topix", "FTSE", "DAX", "Stoxx", "CAC 40", "Hang Seng", "CSI 300", "Kospi", "TSX", "Sensex", "Treasury", "Treasuries", "yields",
                "bond", "bonds", "gilts", "Bunds", "JGB", "Federal Reserve", "central bank", "rate cut", "rate hike", "rate cuts", "rate hikes", "interest rates",
                "inflation", "payrolls", "jobs report", "unemployment", "recession", "GDP", "PMI", "consumer prices", "tariff", "tariffs", "sanctions",
                "oil", "crude", "Brent", "OPEC", "gold", "dollar", "yen", "euro", "yuan", "sterling", "bitcoin", "IPO", "earnings", "profit warning",
                "merger", "acquisition", "takeover", "default", "downgrade", "credit rating", "Moody's", "Fitch", "debt ceiling", "government shutdown",
                "volatility", "VIX", "futures", "selloff", "sell-off", "rally", "Nvidia", "Apple", "Microsoft", "Alphabet", "Amazon", "Meta", "Tesla",
                "Broadcom", "Berkshire", "JPMorgan", "TSMC", "Samsung", "Aramco", "Anthropic", "OpenAI",
                "Börse", "Aktie", "Aktien", "Anleger", "Zinsen", "Inflation", "Konjunktur", "Ölpreis", "Leitzins", "Dax", "Bundesanleihen"]
MARKET_CASE = ["Fed", "FOMC", "ECB", "BOJ", "BoJ", "BoE", "PBOC", "CPI", "PCE", "PPI", "ETF"]

# country key -> (name, entity terms, case-sensitive terms, central bank label + terms, feeds, google query, lang, gl)
COUNTRY_DEF = {
    "united_states": ("United States", ["U.S. economy", "US economy", "American economy", "Wall Street", "Treasury", "Treasuries", "Federal Reserve",
                                        "payrolls", "jobs report", "White House", "Congress", "Washington", "U.S.", "United States", "American"],
                      ["FOMC", "Fed", "CPI", "PCE"], ("Fed", ["Federal Reserve", "Fed"]), ["fed", "fed_speech", "bea", "census", "sec", "cftc"],
                      "US economy OR Federal Reserve OR Treasury yields", "en-US", "US"),
    "euro_area": ("Euro area", ["euro zone", "eurozone", "euro area", "euro-area", "European Central Bank", "Lagarde", "Eurostat", "Bund", "Bunds",
                                "euro", "EU economy", "European economy", "European stocks", "Stoxx", "Eurozone"],
                  ["ECB", "EZB"], ("ECB", ["European Central Bank", "ECB"]), ["ecb", "ecb_stats", "bundesbank", "destatis", "euronews", "handelsblatt"],
                  "eurozone economy OR ECB OR euro area", "en-US", "US"),
    "germany": ("Germany", ["Germany", "German", "Deutschland", "Bundesbank", "Destatis", "DAX", "Bund", "Bunds", "Berlin", "Merz", "Ifo", "ZEW"],
                [], ("ECB", ["European Central Bank", "ECB", "EZB"]), ["bundesbank", "destatis", "handelsblatt", "tagesschau", "ecb"],
                "Wirtschaft Deutschland OR DAX OR Bundesbank", "de", "DE"),
    "france": ("France", ["France", "French", "CAC 40", "Paris", "OAT", "Banque de France", "INSEE"], [], ("ECB", ["European Central Bank", "ECB"]),
               ["ecb", "euronews"], "France economy OR CAC 40", "en-US", "US"),
    "italy": ("Italy", ["Italy", "Italian", "BTP", "BTPs", "FTSE MIB", "Rome", "Bank of Italy", "Istat"], [], ("ECB", ["European Central Bank", "ECB"]),
              ["ecb", "euronews"], "Italy economy OR BTP", "en-US", "US"),
    "united_kingdom": ("United Kingdom", ["Britain", "British", "UK", "U.K.", "United Kingdom", "Bank of England", "gilts", "gilt", "sterling", "pound",
                                          "FTSE", "ONS", "Reeves", "Bailey", "London"], ["BoE", "BOE"], ("BoE", ["Bank of England", "BoE"]),
                       ["boe", "ons", "bbc"], "UK economy OR Bank of England OR gilts", "en-GB", "GB"),
    "japan": ("Japan", ["Japan", "Japanese", "Tokyo", "Bank of Japan", "Ueda", "yen", "JGB", "JGBs", "Nikkei", "Topix", "Takaichi"], ["BOJ", "BoJ"],
              ("BOJ", ["Bank of Japan", "BOJ", "BoJ"]), ["boj", "scmp"], "Japan economy OR BOJ OR yen OR Nikkei", "en-US", "US"),
    "canada": ("Canada", ["Canada", "Canadian", "Bank of Canada", "loonie", "TSX", "Ottawa", "Macklem", "Carney", "Statistics Canada", "StatCan"],
               ["BoC", "BOC"], ("BoC", ["Bank of Canada", "BoC"]), ["boc", "globe", "cbc"], "Canada economy OR Bank of Canada OR TSX", "en-CA", "CA"),
    "switzerland": ("Switzerland", ["Switzerland", "Swiss", "Swiss franc", "franc", "Swiss National Bank", "SMI"], ["SNB"], ("SNB", ["Swiss National Bank", "SNB"]),
                    ["snb"], "Swiss economy OR SNB OR Swiss franc", "en-US", "US"),
    "sweden": ("Sweden", ["Sweden", "Swedish", "krona", "Riksbank", "Stockholm"], [], ("Riksbank", ["Riksbank"]), ["riksbank"],
               "Sweden economy OR Riksbank", "en-US", "US"),
    "china": ("China", ["China", "Chinese", "Beijing", "yuan", "renminbi", "People's Bank of China", "Hang Seng", "CSI 300", "Shanghai", "Xi Jinping"],
              ["PBOC", "PBoC"], ("PBOC", ["People's Bank of China", "PBOC"]), ["scmp"], "China economy OR PBOC OR yuan", "en-US", "US"),
    "india": ("India", ["India", "Indian", "rupee", "Reserve Bank of India", "Sensex", "Nifty", "Mumbai"], ["RBI"], ("RBI", ["Reserve Bank of India", "RBI"]),
              ["mint"], "India economy OR RBI OR Sensex", "en-IN", "IN"),
    "australia": ("Australia", ["Australia", "Australian", "Aussie", "Reserve Bank of Australia", "ASX"], ["RBA"], ("RBA", ["Reserve Bank of Australia", "RBA"]),
                  [], "Australia economy OR RBA", "en-AU", "AU"),
    "brazil": ("Brazil", ["Brazil", "Brazilian", "real", "Banco Central do Brasil", "Bovespa", "Ibovespa", "Selic", "Lula"], [], ("BCB", ["Banco Central", "Selic"]),
               [], "Brazil economy OR Selic OR Ibovespa", "en-US", "US"),
}
CB_EXCLUDE = []

COMMODITY_DEF = {   # key -> (name, terms, case terms, ambiguous, context, drivers, exclude, feeds, google query)
    "gold": ("Gold", ["bullion", "gold price", "gold prices", "spot gold", "gold futures", "precious metals", "XAU/USD", "XAUUSD", "gold ETF", "gold ETFs"],
             ["XAU"], ["gold"], ["price", "prices", "ounce", "bullion", "futures", "metal", "metals", "haven", "rally", "record", "Fed", "yields", "dollar",
                                 "central bank", "ETF", "silver", "rises", "falls", "gains", "slips", "steadies", "jumps", "drops", "hits"],
             [("Fed", ["Federal Reserve", "Fed", "FOMC"], True), ("dollar", ["dollar index", "DXY", "greenback"], False), ("real yields", ["real yields", "TIPS"], False)],
             [r"gold medal", r"gold standard", r"Gold Coast", r"Gold Cup", r"golden"],
             ["fxstreet", "mining", "investinglive", "bloomberg", "ft_markets", "wsj_markets", "cnbc", "mw"], "gold price OR bullion"),
    "silver": ("Silver", ["silver price", "silver prices", "spot silver", "silver futures", "XAG/USD"], ["XAG"], ["silver"], ["price", "ounce", "metal", "futures", "gold"],
               [("Fed", ["Federal Reserve", "Fed"], True)], [r"silver medal", r"silver lining"], ["fxstreet", "mining", "investinglive", "bloomberg", "mw"], "silver price"),
    "crude": ("Crude oil", ["crude", "crude oil", "oil price", "oil prices", "Brent", "WTI", "OPEC", "OPEC+", "barrel", "barrels", "oil market", "oil futures",
                            "oil supply", "oil demand", "refinery", "refineries", "refiners", "petroleum", "gasoline", "diesel", "oil tanker", "tankers",
                            "Strait of Hormuz", "Hormuz", "oil output", "oil production", "oil exports", "shale"], ["EIA", "IEA", "SPR"], ["oil"],
              ["price", "prices", "market", "supply", "demand", "output", "Saudi", "Russia", "Iran", "Gulf", "futures", "producers", "exports", "falls", "rises", "jumps"],
              [("sanctions", ["sanctions on Russia", "Russian oil", "Iranian oil", "Venezuelan oil"], False)],
              [r"palm oil", r"olive oil", r"cooking oil", r"oil paint", r"essential oil", r"cod liver"],
              ["eia", "eia_press", "oilprice", "rigzone", "investinglive", "bloomberg", "ft_markets", "wsj_markets", "cnbc", "mw"], "oil prices OR crude OR OPEC"),
    "natgas": ("Natural gas", ["natural gas", "LNG", "Henry Hub", "TTF", "gas prices", "gas storage", "gas inventories", "liquefied natural gas"], [], ["gas"],
               ["prices", "supply", "storage", "pipeline", "Europe", "winter"], [("weather", ["cold snap", "heat wave", "heating demand"], False)], [r"gas station", r"greenhouse gas"],
               ["eia", "eia_press", "oilprice", "rigzone", "bloomberg", "investinglive"], "natural gas prices OR LNG"),
    "copper": ("Copper", ["copper", "copper price", "copper prices", "LME"], [], [], [], [("China demand", ["Chinese demand", "China demand"], False)], [],
               ["mining", "bloomberg", "ft_markets", "investinglive", "fxstreet"], "copper price"),
}
FX_DEF = {   # key -> (name, terms, case terms, ambiguous, drivers, google query)
    "eurusd": ("EUR/USD", ["EUR/USD", "EURUSD", "euro-dollar", "euro dollar"], [], ["euro"],
               [("ECB", ["European Central Bank", "ECB", "Lagarde"], False), ("Fed", ["Federal Reserve", "Fed", "FOMC"], True), ("dollar", ["dollar index", "DXY", "greenback"], False)],
               "EUR/USD"),
    "usdjpy": ("USD/JPY", ["USD/JPY", "USDJPY", "yen"], [], [], [("BOJ", ["Bank of Japan", "BOJ", "BoJ"], True), ("Fed", ["Federal Reserve", "Fed", "FOMC"], True),
                                                              ("MoF", ["intervention", "Katayama", "Ministry of Finance"], False)], "USD/JPY OR yen"),
    "gbpusd": ("GBP/USD", ["GBP/USD", "GBPUSD", "sterling", "cable"], [], ["pound"], [("BoE", ["Bank of England", "BoE"], True), ("Fed", ["Federal Reserve", "Fed"], True)],
               "GBP/USD OR sterling"),
    "usdcad": ("USD/CAD", ["USD/CAD", "USDCAD", "loonie", "Canadian dollar"], [], [], [("BoC", ["Bank of Canada", "BoC"], True), ("oil", ["crude", "oil prices"], False)],
               "Canadian dollar OR USD/CAD"),
    "usdcny": ("USD/CNY", ["USD/CNY", "USDCNY", "yuan", "renminbi", "USD/CNH"], [], [], [("PBOC", ["People's Bank of China", "PBOC", "fixing"], False)], "yuan OR renminbi"),
}
FX_CONTEXT = ["dollar", "currency", "currencies", "forex", "FX", "exchange rate", "greenback", "parity", "EUR", "single currency", "weaker", "stronger"]
CRYPTO_DEF = {
    "btc": ("Bitcoin", ["Bitcoin", "bitcoin's", "spot bitcoin ETF", "spot bitcoin ETFs"], ["BTC"], [],
            [("crypto", ["crypto", "cryptocurrency", "cryptocurrencies", "stablecoin", "stablecoins", "digital assets", "Coinbase", "Binance", "MicroStrategy", "Strategy Inc"], False)],
            "bitcoin"),
    "eth": ("Ether", ["Ethereum", "Ether"], ["ETH"], [], [("crypto", ["crypto", "cryptocurrency", "stablecoin", "digital assets"], False)], "ethereum"),
}
SUBJECT_OPTIONS = ([{"label": "The market (most important)", "value": "market"}, {"label": "Dashboard symbol", "value": "symbol"},
                    {"label": "Dashboard country", "value": "country"}]
                   + [{"label": v[0], "value": k} for k, v in COMMODITY_DEF.items()]
                   + [{"label": v[0], "value": k} for k, v in FX_DEF.items()]
                   + [{"label": v[0], "value": k} for k, v in CRYPTO_DEF.items()]
                   + [{"label": v[0], "value": "c:" + k} for k, v in COUNTRY_DEF.items()])
FEED_OPTIONS = ([{"label": "All curated feeds", "value": "all"}, {"label": "Official only (central banks, statistics, regulators)", "value": "official"},
                 {"label": "Wires & financial press", "value": "wires"}]
                + [{"label": v[0], "value": k} for k, v in REGIONS.items()])

# suffix -> (home key, Google News lang, gl, regional feeds, central bank driver)
HOME = {
    "": ("us", "en-US", "US", [], ("Fed", ["Federal Reserve", "FOMC"])),
    "DE": ("de", "de", "DE", ["handelsblatt", "tagesschau", "euronews"], ("ECB", ["European Central Bank", "EZB"])),
    "F": ("de", "de", "DE", ["handelsblatt", "tagesschau"], ("ECB", ["European Central Bank", "EZB"])),
    "TO": ("ca", "en-CA", "CA", ["globe", "cbc"], ("BoC", ["Bank of Canada"])),
    "V": ("ca", "en-CA", "CA", ["globe", "cbc"], ("BoC", ["Bank of Canada"])),
    "L": ("uk", "en-GB", "GB", ["bbc"], ("BoE", ["Bank of England"])),
    "PA": ("fr", "fr", "FR", ["euronews"], ("ECB", ["European Central Bank", "BCE"])),
    "AS": ("nl", "nl", "NL", ["euronews"], ("ECB", ["European Central Bank"])),
    "MI": ("it", "it", "IT", ["euronews"], ("ECB", ["European Central Bank", "BCE"])),
    "SW": ("ch", "de", "CH", [], ("SNB", ["Swiss National Bank"])),
    "ST": ("se", "sv", "SE", [], ("Riksbank", ["Riksbank"])),
    "T": ("jp", "en-US", "US", [], ("BOJ", ["Bank of Japan"])),
    "HK": ("hk", "en-US", "US", ["scmp"], ("PBOC", ["People's Bank of China"])),
    "NS": ("in", "en-IN", "IN", ["mint"], ("RBI", ["Reserve Bank of India"])),
    "BO": ("in", "en-IN", "IN", ["mint"], ("RBI", ["Reserve Bank of India"])),
}
NAME_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|ag|se|sa|nv|n\.v|s\.a|spa|s\.p\.a|ab|asa|oyj|a/s|"
                         r"holdings?|group|kgaa|& co|class [a-z]|the)\.?$", re.I)
COMMON_WORDS = {"apple", "shell", "target", "visa", "gap", "block", "snap", "match", "oracle", "alphabet", "meta", "square", "coach", "progressive",
                "southern", "general", "american", "united", "first", "national", "energy", "international", "global", "sun", "amazon", "next", "via",
                "best", "home", "dollar", "public", "carnival", "corning", "ball", "booking", "chevron", "intuitive", "insight", "unity", "zoom", "uber"}
# extra aliases and context words a name alone does not give (kept small: the mega-caps with a common-word name)
KNOWN = {
    "AAPL": (["Apple Inc", "Apple's", "Tim Cook"], ["iPhone", "iPhones", "iPad", "Mac", "MacBook", "App Store", "Cupertino", "Vision Pro", "Siri", "iOS", "AirPods",
                                                     "Apple Intelligence", "Apple Watch", "Apple TV", "Apple Pay", "Apple Music"]),
    "XOM": (["ExxonMobil", "Exxon Mobil", "Exxon"], []),
    "SHEL": (["Shell plc"], ["oil", "gas", "LNG", "refinery", "Sawan", "BP"]),
    "SAP": (["SAP SE"], []),
    "GOOGL": (["Google", "Alphabet"], []), "GOOG": (["Google", "Alphabet"], []),
    "META": (["Meta Platforms", "Facebook", "Instagram", "WhatsApp", "Zuckerberg"], []),
    "TGT": (["Target Corp"], ["retailer", "stores"]),
    "V": (["Visa Inc"], ["payments", "card"]),
}
GENERIC_CONTEXT = ["shares", "stock", "stocks", "earnings", "revenue", "profit", "CEO", "investors", "analyst", "analysts", "quarter", "results", "guidance",
                   "market cap", "valuation", "deal", "acquisition", "lawsuit", "antitrust", "Aktie", "Aktien", "Konzern", "company"]
SECTOR_DRIVERS = {   # Yahoo sector or industry word -> (label, terms)
    "Energy": ("oil", ["crude", "oil prices", "Brent", "WTI", "OPEC", "OPEC+"]),
    "Gold": ("gold", ["gold price", "gold prices", "bullion", "spot gold"]),
    "Copper": ("copper", ["copper price", "copper prices"]),
    "Airlines": ("jet fuel", ["jet fuel", "oil prices"]),
}


def _split_symbol(sym):
    s = (sym or "").strip().upper()
    base, _, suf = s.partition(".")
    if suf not in HOME:
        base, suf = s, ""
    return base, suf


def aliases_from_name(name):
    """'Exxon Mobil Corporation' -> ['Exxon Mobil', 'Exxon']; 'Suncor Energy Inc.' -> ['Suncor Energy', 'Suncor']."""
    n = re.sub(r"\s+", " ", (name or "").replace(" ", " ")).strip()
    prev = None
    while n and n != prev:
        prev = n
        n = NAME_SUFFIX.sub("", n).strip(" ,.")
    out = [n] if n else []
    first = n.split(" ")[0] if n else ""
    if first and first != n and len(first) >= 4 and first.lower() not in COMMON_WORDS:
        out.append(first)
    return out


def equity_subject(sym):
    base, suf = _split_symbol(sym)
    home = HOME[suf]
    full = base + (("." + suf) if suf else "")
    prof = {}
    try:
        prof = C._profiles([full]).get(full) or {}
    except Exception:  # noqa: BLE001 - the subject still works from the ticker
        prof = {}
    names = aliases_from_name(prof.get("name") or "")
    extra, ctx = KNOWN.get(base, ([], []))
    terms, amb = [], []
    for a in names + extra:
        (amb if a.lower() in COMMON_WORDS else terms).append(a)
    case_terms = [f"${base}", f"({base})", f"NASDAQ:{base}", f"NYSE:{base}", f"TSX:{base}", f"{base}.{suf}" if suf else ""]
    if len(base) >= 3:
        case_terms.append(base)                     # 'SU' or 'V' alone is a word; 'AAPL', 'XOM', 'SAP' are not
    cb_label, cb_terms = home[4]
    drivers = [(cb_label, cb_terms, False)]
    for key, (label, ts) in SECTOR_DRIVERS.items():
        if key == prof.get("sector") or key.lower() in str(prof.get("industry") or "").lower():
            drivers.insert(0, (label, ts, False))
    feeds = WIRES + home[3] + (["fed"] if home[0] == "us" else [])
    q_names = [f'"{a}"' for a in (names[:1] or [base])]
    q = " OR ".join(q_names + ([base] if len(base) >= 3 and base.lower() not in (n.lower() for n in names) else [])) + " when:3d"
    queries = [(q, home[1], home[2])]
    display = (names[0] if names else base) + f" ({full})"

    def primary():
        out = []
        if home[0] == "us" and not suf:
            out += _sec_8k(base)
            out += _yahoo_ticker(base)
        if home[0] == "ca":
            out += _tmx_releases(base)
        return out
    return Subject("eq:" + full, display, terms=terms, case_terms=[t for t in case_terms if t], ambiguous=amb, context=ctx + GENERIC_CONTEXT + [base],
                   drivers=drivers, feeds=feeds, queries=queries, primary=primary, home=home[0])


def country_subject(key):
    d = COUNTRY_DEF.get(key)
    if d is None:
        name = C.COUNTRIES.get(key, (key.replace("_", " ").title(),))[0]
        return Subject("c:" + key, name, terms=[name], feeds=WIRES, queries=[(f"{name} economy when:2d", "en-US", "US")])
    name, terms, case_terms, (cb_label, cb_terms), feeds, q, lang, gl = d
    # the central bank is the subject here (not a driver): its decisions are country news
    return Subject("c:" + key, name, terms=terms + cb_terms, case_terms=case_terms, feeds=feeds + WIRES, queries=[(q + " when:2d", lang, gl)],
                   home=key)


def commodity_subject(key):
    name, terms, case_terms, amb, ctx, drivers, excl, feeds, q = COMMODITY_DEF[key]
    return Subject(key, name, terms=terms, case_terms=case_terms, ambiguous=amb, context=ctx, drivers=drivers, exclude=excl, feeds=feeds,
                   queries=[(q + " when:2d", "en-US", "US")])


def fx_subject(key):
    name, terms, case_terms, amb, drivers, q = FX_DEF[key]
    return Subject(key, name, terms=terms, case_terms=case_terms, ambiguous=amb, context=FX_CONTEXT, drivers=drivers,
                   feeds=["fxstreet", "investinglive", "ecb", "fed", "boj", "boe", "boc", "bloomberg", "ft_markets", "wsj_markets", "mw"],
                   queries=[(q + " when:2d", "en-US", "US")], amb_min_tier=2)


def crypto_subject(key):
    name, terms, case_terms, amb, drivers, q = CRYPTO_DEF[key]
    return Subject(key, name, terms=terms, case_terms=case_terms, ambiguous=amb, drivers=drivers,
                   feeds=["coindesk", "theblock", "decrypt", "bloomberg", "ft_markets", "wsj_markets", "cnbc", "investinglive", "sec", "cftc"],
                   queries=[(q + " when:2d", "en-US", "US")])


def market_subject():
    return Subject("market", "The market", terms=MARKET_TERMS, case_terms=MARKET_CASE,
                   feeds=WIRES + ["fed", "fed_speech", "ecb", "boe", "boj", "boc", "snb", "bea", "census", "ons", "eia", "sec", "cftc", "gnews_business", "bbc"],
                   market=True)


def resolve(subject="", symbol="", country=""):
    s = (subject or "").strip().lower()
    if s in ("", "symbol"):
        if (symbol or "").strip():
            sym = symbol.strip().upper()
            alias = {"GC=F": "gold", "SI=F": "silver", "CL=F": "crude", "BZ=F": "crude", "NG=F": "natgas", "HG=F": "copper", "EURUSD=X": "eurusd",
                     "JPY=X": "usdjpy", "USDJPY=X": "usdjpy", "GBPUSD=X": "gbpusd", "CAD=X": "usdcad", "BTC-USD": "btc", "ETH-USD": "eth"}.get(sym)
            if alias:
                return resolve(alias)
            if sym.startswith("^") or sym.startswith("FRED:"):
                return market_subject()
            return equity_subject(sym)
        s = "market" if s == "" or not country else "country"
    if s == "country":
        return country_subject(country or "united_states")
    if s.startswith("c:"):
        return country_subject(s[2:])
    if s in COMMODITY_DEF:
        return commodity_subject(s)
    if s in FX_DEF:
        return fx_subject(s)
    if s in CRYPTO_DEF:
        return crypto_subject(s)
    if s in C.COUNTRIES:
        return country_subject(s)
    return market_subject()


# ------------------------------------------------------------------ primary sources for companies
SEC_ITEMS = {"1.01": "material agreement", "1.02": "agreement ended", "1.03": "bankruptcy", "2.01": "acquisition or disposal completed",
             "2.02": "results of operations", "2.03": "new debt", "2.05": "restructuring costs", "2.06": "impairment", "3.01": "delisting notice",
             "4.01": "auditor change", "4.02": "restatement", "5.01": "change in control", "5.02": "officers or directors changed",
             "5.07": "shareholder vote", "7.01": "Regulation FD disclosure", "8.01": "other events"}


def _sec_8k(sym):
    def run():
        try:
            rows = C.api(C.V5, "equity/fundamental/filings", provider="sec", symbol=sym, form_type="8-K", limit=12) or []
        except Exception:  # noqa: BLE001 - not an SEC filer, or SEC down
            return []
        out = []
        for r in rows:
            items = [i.strip() for i in str(r.get("items") or "").split(",") if i.strip() and i.strip() != "9.01"]
            what = "; ".join(SEC_ITEMS.get(i, "item " + i) for i in items) or "current report"
            when = _ptime(str(r.get("accepted_date") or "").replace(" ", "T")) or _ptime(str(r.get("filing_date") or "")[:10] + "T12:00:00")
            title = f"{sym} files Form 8-K: {what}" + (" (earnings release)" if "2.02" in items else "")
            out.append({"title": title, "url": r.get("filing_detail_url") or r.get("report_url") or "", "date": when, "publisher": "SEC EDGAR (8-K)",
                        "tier": 1, "feed": "sec_8k", "boost": 1.5 if set(items) & {"1.01", "1.03", "2.01", "2.02", "4.02", "5.01", "5.02"} else 0.3})
        return out
    return C.cached(("news_8k", sym), 900, run)


def _yahoo_ticker(sym):
    items = fetch("https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode({"s": sym, "region": "US", "lang": "en-US"}), ttl=600)
    return [dict(i, publisher="Yahoo Finance", tier=3, feed="yahoo") for i in items]


def _tmx_releases(sym):
    def run():
        try:
            rows = C.api(C.V5, "tmx/news/feed", symbol=sym, limit=15, fetch_body="false") or []
        except Exception:  # noqa: BLE001
            return []
        return [{"title": _text(r.get("title")), "url": r.get("url") or "", "date": _ptime(str(r.get("date") or "")), "tier": 1, "feed": "tmx",
                 "publisher": "Issuer release via TMX (" + str(r.get("source") or "newswire").split(" via ")[0] + ")"} for r in rows if r.get("title")]
    return C.cached(("news_tmx", sym), 900, run)


# ------------------------------------------------------------------ ranking and clustering
STOP = set("a an the of to in on for and or at by with from as is are was be it its this that after over amid into new says say said vs us u.s. -".split())


def _tokens(title):
    return {w for w in re.findall(r"[a-z0-9äöüß]+", title.lower()) if w not in STOP and len(w) > 1}


def _clean_title(t, publisher):
    if publisher and t.endswith(" - " + publisher):
        return t[: -len(publisher) - 3].strip()
    return re.sub(r"\s+[-|–]\s+[^-|–]{2,40}$", "", t) if publisher else t


def collect(subj, feed_ids, queries=True):
    """Every item from the subject's feeds, primary sources and Google News queries, tagged with its source and tier."""
    jobs = {}
    for fid in feed_ids:
        f = FEEDS.get(fid)
        if f:
            jobs[_pool.submit(fetch, f[1], 900 if f[2] == 1 else 300)] = fid
    if queries:
        for q, lang, gl in subj.queries:
            jobs[_pool.submit(fetch, gnews_url(q, lang, gl), 900, True)] = "gnews"
    prim = _pool.submit(subj.primary) if subj.primary else None
    out, used = [], set()
    try:
        for fut in _cf.as_completed(jobs, timeout=40):
            fid = jobs[fut]
            try:
                items = fut.result()
            except Exception:  # noqa: BLE001
                continue
            for it in items:
                if fid == "gnews":
                    tier = _publisher_tier(it["publisher"], it["publisher_url"])
                    pub = it["publisher"] or "Google News"
                else:
                    label, _, tier, _, _ = FEEDS[fid]
                    pub = label
                    if fid == "gnews_business" and it.get("publisher"):
                        tier, pub = _publisher_tier(it["publisher"], it["publisher_url"]), it["publisher"]
                out.append(dict(it, title=_clean_title(it["title"], it.get("publisher")), publisher=pub, tier=tier, feed=fid))
                used.add(fid)
    except _cf.TimeoutError:
        pass
    if prim is not None:
        try:
            for it in prim.result(timeout=40):
                out.append(it)
                used.add(it.get("feed"))
        except Exception:  # noqa: BLE001
            pass
    return out, used


def rank(subj, items, mode="top", hours=48, now=None, limit=20, require_market=True):
    """Filter to the subject, cluster duplicate stories, score and order. Returns rows for the news card."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=hours)
    kept = []
    for it in items:
        d = it.get("date")
        if d is None or d < cutoff or d > now + dt.timedelta(hours=2):
            continue                                 # undated or out of the window: never shown (a time on every line)
        t = it["title"]
        if not t or QUOTE_PAGE.search(t):
            continue
        tier = it.get("tier", 3)
        opinion = bool(OPINION.search(t)) or tier == 4
        mat, kinds = materiality(t)
        text = t + " " + (it.get("summary") or "")[:200] if tier == 1 else t
        if subj.market:
            hit, label = subj.match(t)
            hit = min(hit, 2)
            if not hit and not (tier == 1 and it.get("feed") in OFFICIAL and mat > 0):
                if require_market:
                    continue
            label = ""
        else:
            hit, label = subj.match(text)
            if hit == 0 or (hit == 3 and tier > subj.amb_min_tier):
                continue
            hit = min(hit, 2)
            if opinion:
                continue                             # listicles and buy/sell opinion are not news about the subject
            if hit == 1 and (mat == 0 or tier > 2):
                continue                             # a driver counts only when material and from a primary or press source
        kept.append({"it": it, "tier": min(tier, 3), "mat": mat + it.get("boost", 0), "kinds": kinds, "label": label, "hit": hit, "opinion": opinion,
                     "tok": _tokens(t)})
    # cluster: same story in several feeds -> one line, best source shown, count kept
    kept.sort(key=lambda k: (k["tier"], k["it"]["date"]))
    clusters = []
    index: dict = {}
    for k in kept:
        cand = set()
        for w in k["tok"]:
            cand.update(index.get(w, ()))
        best = None
        for ci in cand:
            c = clusters[ci]
            inter = len(k["tok"] & c["tok"])
            if inter >= 3 and inter / max(1, min(len(k["tok"]), len(c["tok"]))) >= 0.6:
                best = ci
                break
        if best is None:
            best = len(clusters)
            clusters.append({"lead": k, "tok": set(k["tok"]), "pubs": [], "first": k["it"]["date"], "last": k["it"]["date"], "mat": k["mat"]})
            for w in k["tok"]:
                index.setdefault(w, []).append(best)
        c = clusters[best]
        if k["it"]["publisher"] not in c["pubs"]:
            c["pubs"].append(k["it"]["publisher"])
        c["first"] = min(c["first"], k["it"]["date"])
        c["last"] = max(c["last"], k["it"]["date"])
        c["mat"] = max(c["mat"], k["mat"])
        if k["hit"] > c["lead"]["hit"]:
            c["lead"] = dict(k, tier=min(k["tier"], c["lead"]["tier"]))
    rows = []
    for c in clusters:
        k = c["lead"]
        age_h = max(0.0, (now - c["last"]).total_seconds() / 3600)
        score = TIER_W[k["tier"]] + 1.2 * c["mat"] + 1.5 * math.log2(len(c["pubs"])) + 3.0 * math.exp(-age_h / 12)
        if k["opinion"]:
            score -= 4
        if k["it"]["title"].rstrip().endswith("?"):
            score -= 1.5                             # questions are features and opinion more often than news
        if k["hit"] == 1:
            score *= 0.6                             # a driver explains the subject; it does not outrank news about it
        if subj.market and c["mat"] == 0 and k["tier"] > 1:
            score -= 2
        it = k["it"]
        n = len(c["pubs"])
        rows.append({"title": it["title"], "url": it.get("url") or "", "date": c["first"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "source": it["publisher"] + (f" +{n - 1}" if n > 1 else ""), "tag": k["label"] or ("Official" if k["tier"] == 1 else ""),
                     "excerpt": ("Also: " + ", ".join(c["pubs"][1:6])) if n > 1 else "", "tier": k["tier"], "score": round(score, 2), "sources_n": n, "feed": it.get("feed") or "",
                     "_opinion": k["opinion"]})
    if mode == "monitor":
        rows.sort(key=lambda r: r["date"], reverse=True)
    else:
        rows = [r for r in rows if not (subj.market and r["_opinion"])]
        rows.sort(key=lambda r: (-r["score"], r["date"]))
        # drivers explain, they do not crowd out the subject: at most three driver lines in the top ten
        out, drv = [], 0
        for r in rows:
            if r["tag"] and r["tag"] != "Official":
                if drv >= 3 and len(out) < 10:
                    continue
                drv += 1
            out.append(r)
        rows = out
    for r in rows:
        r.pop("_opinion", None)
    return rows[:limit]


def _window(hours):
    """Weekends add their hours: Monday morning still shows Friday's news."""
    now = dt.datetime.now(dt.timezone.utc)
    extra = sum(24 for i in range(1, 4) if (now - dt.timedelta(days=i)).weekday() >= 5 and i * 24 <= hours + 48)
    return hours + extra


def _sources(used, subj, rows=()):
    """The feeds read, those that supplied the lines shown first (the card header names the first few)."""
    shown: dict = {}
    for r in rows:
        shown[r.get("feed")] = shown.get(r.get("feed"), 0) + 1
    out = []
    for fid in sorted(used, key=lambda f: (-shown.get(f, 0), FEEDS[f][2] if f in FEEDS else 1, f)):
        if fid in FEEDS:
            label, url, tier, _, home = FEEDS[fid]
            out.append(C.src(label, {1: "official release feed", 2: "headlines (RSS)", 3: "aggregated headlines"}[tier], home))
        elif fid == "gnews":
            out.append(C.src("Google News", "search: " + "; ".join(q for q, _, _ in subj.queries), "https://news.google.com/"))
        elif fid == "sec_8k":
            out.append(C.src("SEC", "Form 8-K current reports (issuer filings)", "https://www.sec.gov/edgar/search/"))
        elif fid == "tmx":
            out.append(C.src("TMX Money", "issuer news releases", "https://money.tmx.com/"))
        elif fid == "yahoo":
            out.append(C.src("Yahoo Finance", "ticker headline feed", "https://finance.yahoo.com/"))
    seen, uniq = set(), []
    for s in out:
        key = (s["name"], s["detail"])
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    return uniq or [C.src("Terminal news", "no feed answered", "")]


def news(subject="", symbol="", country="", mode="top", feeds="all", hours="", limit="", listing="", **_):
    """The /x/news view. mode "top": ranked for the subject; "monitor": newest first, deduplicated (situation room).
    listing: the Yahoo suffix of a dashboard whose tickers carry none (Canada (TMX): RY -> RY.TO)."""
    symbol = (symbol or "").strip()
    suf = str(listing or "").strip(". ").upper()
    if suf and symbol and not symbol.startswith("^") and "=" not in symbol and not symbol.upper().endswith("." + suf):
        symbol = symbol.replace(" ", "-").replace(".", "-") + "." + suf          # TMX "BBD.B" is Yahoo "BBD-B.TO"
    mode = "monitor" if str(mode).lower() == "monitor" else "top"
    subj = resolve(subject, symbol, country) if mode == "top" or subject not in ("", "market") else market_subject()
    try:
        hours = float(hours) if hours not in ("", None) else (24 if mode == "monitor" else 48)
    except ValueError:
        hours = 48
    hours = max(1.0, min(hours, 24 * 14))
    try:
        limit = int(limit) if limit not in ("", None) else (80 if mode == "monitor" else 20)
    except ValueError:
        limit = 20
    feed_ids = subj.feeds
    queries = True
    if mode == "monitor":
        f = (feeds or "all").lower()
        queries = not subj.market
        if f == "official":
            feed_ids, hours = OFFICIAL, max(hours, 24 * 7)
        elif f == "wires":
            feed_ids = WIRES
        elif f in REGIONS:
            feed_ids, hours = REGIONS[f][1], max(hours, 24 * 14 if f == "banks" else 48)       # supervisors publish weekly, not hourly
        elif subj.market:
            feed_ids = WIRES + OFFICIAL + ["bbc", "euronews", "gnews_business", "globe", "handelsblatt", "scmp"]
    items, used = collect(subj, feed_ids, queries=queries and bool(subj.queries))
    win = _window(hours)
    rows = rank(subj, items, mode=mode, hours=win, limit=limit, require_market=not (mode == "monitor" and str(feeds).lower() in ("official", "banks")))
    out = {"results": rows, "order": "time" if mode == "monitor" else "rank", "subject": subj.name, "sources": _sources(used, subj, rows)}
    if not rows:
        out["empty"] = f"No material news on {subj.name} in the last {win:.0f} h." if not subj.market else f"No market news in the last {win:.0f} h."
    return out
