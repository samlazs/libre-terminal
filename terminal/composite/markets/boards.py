"""Curated market boards, performance bars, style box, watchlist, and FX matrix."""

from __future__ import annotations

import bisect
import datetime as dt

import composite as C

# ---------------------------------------------------------------- board presets
# preset -> [(section, [(label, yahoo symbol), ...]), ...]
BOARDS = {
    "us_equity": ("U.S. equity markets", [
        ("Major indices", [("S&P 500", "^GSPC"), ("Dow Jones", "^DJI"), ("Nasdaq 100", "^NDX"), ("Nasdaq Composite", "^IXIC"), ("Russell 2000", "^RUT"), ("S&P MidCap 400", "^MID")]),
        ("Volatility", [("CBOE VIX", "^VIX"), ("VIX of VIX", "^VVIX"), ("Nasdaq 100 Vol", "^VXN")]),
    ]),
    "us_sectors": ("U.S. equity sectors", [
        ("S&P sector ETFs", [("Technology", "XLK"), ("Communications", "XLC"), ("Cons. Discretionary", "XLY"), ("Cons. Staples", "XLP"), ("Energy", "XLE"), ("Financials", "XLF"),
                             ("Health Care", "XLV"), ("Industrials", "XLI"), ("Materials", "XLB"), ("Real Estate", "XLRE"), ("Utilities", "XLU")]),
    ]),
    "us_factors": ("U.S. equity factors and styles", [
        ("Factors", [("Momentum", "MTUM"), ("Quality", "QUAL"), ("Value", "VLUE"), ("Min Volatility", "USMV"), ("Size", "SIZE"), ("High Dividend", "VYM"), ("Equal Weight", "RSP"), ("High Beta", "SPHB")]),
        ("Style", [("Large Value", "IWD"), ("Large Growth", "IWF"), ("Mid Value", "IWS"), ("Mid Growth", "IWP"), ("Small Value", "IWN"), ("Small Growth", "IWO")]),
    ]),
    "us_industries": ("U.S. industries and themes", [
        ("Industry ETFs", [("Semiconductors", "SMH"), ("Software", "IGV"), ("Biotech", "XBI"), ("Banks", "KBE"), ("Regional Banks", "KRE"), ("Insurance", "KIE"), ("Homebuilders", "XHB"),
                           ("Retail", "XRT"), ("Aerospace & Defense", "ITA"), ("Transports", "IYT"), ("Oil & Gas E&P", "XOP"), ("Oil Services", "OIH"), ("Gold Miners", "GDX"), ("Metals & Mining", "XME"),
                           ("Clean Energy", "ICLN"), ("Airlines", "JETS"), ("Cybersecurity", "HACK"), ("Innovation", "ARKK")]),
    ]),
    "megacaps": ("Mega-cap stocks", [
        ("Largest U.S. companies", [("Apple", "AAPL"), ("Microsoft", "MSFT"), ("Nvidia", "NVDA"), ("Alphabet", "GOOGL"), ("Amazon", "AMZN"), ("Meta", "META"), ("Broadcom", "AVGO"), ("Tesla", "TSLA"),
                                    ("Berkshire Hathaway", "BRK-B"), ("Eli Lilly", "LLY"), ("JPMorgan", "JPM"), ("Visa", "V"), ("Exxon Mobil", "XOM"), ("Walmart", "WMT"), ("UnitedHealth", "UNH")]),
    ]),
    "global_indices": ("Global equity indices", [
        ("Americas", [("S&P 500", "^GSPC"), ("S&P/TSX (Canada)", "^GSPTSE"), ("Bovespa (Brazil)", "^BVSP"), ("IPC (Mexico)", "^MXX")]),
        ("Europe", [("Euro Stoxx 50", "^STOXX50E"), ("FTSE 100 (UK)", "^FTSE"), ("DAX (Germany)", "^GDAXI"), ("CAC 40 (France)", "^FCHI"), ("IBEX 35 (Spain)", "^IBEX"), ("FTSE MIB (Italy)", "FTSEMIB.MI"),
                    ("SMI (Switzerland)", "^SSMI"), ("AEX (Netherlands)", "^AEX"), ("OMX Stockholm 30", "^OMX")]),
        ("Asia-Pacific", [("Nikkei 225 (Japan)", "^N225"), ("Hang Seng (HK)", "^HSI"), ("Shanghai Composite", "000001.SS"), ("KOSPI (Korea)", "^KS11"), ("TAIEX (Taiwan)", "^TWII"),
                          ("Sensex (India)", "^BSESN"), ("ASX 200 (Australia)", "^AXJO"), ("Straits Times (Singapore)", "^STI")]),
    ]),
    "global_markets": ("Global markets (country ETFs, in USD)", [
        ("Broad markets", [("World (ACWI)", "ACWI"), ("Developed ex-US", "EFA"), ("Emerging", "EEM"), ("Europe", "VGK"), ("Asia ex-Japan", "AAXJ"), ("Latin America", "ILF")]),
        ("Developed markets", [("United Kingdom", "EWU"), ("Japan", "EWJ"), ("Germany", "EWG"), ("France", "EWQ"), ("Canada", "EWC"), ("Australia", "EWA"), ("Switzerland", "EWL"),
                               ("Italy", "EWI"), ("Spain", "EWP"), ("Netherlands", "EWN"), ("Sweden", "EWD"), ("Hong Kong", "EWH"), ("Singapore", "EWS")]),
        ("Emerging markets", [("China", "FXI"), ("India", "INDA"), ("Brazil", "EWZ"), ("Mexico", "EWW"), ("South Korea", "EWY"), ("Taiwan", "EWT"), ("South Africa", "EZA"),
                              ("Turkey", "TUR"), ("Indonesia", "EIDO"), ("Thailand", "THD"), ("Poland", "EPOL"), ("Saudi Arabia", "KSA")]),
    ]),
    # Rows named after a currency show that currency against the dollar (+ = currency stronger), whatever way Yahoo
    # quotes the pair; the "Quote" column gives the market quote. Rows named as a pair (EUR/JPY) are that pair as quoted.
    "fx": ("Currencies", [
        ("Dollar index (+ = USD stronger)", [("U.S. Dollar Index (DXY)", "DX-Y.NYB")]),
        ("Majors vs USD (+ = currency stronger)", [("Euro", "EURUSD=X"), ("British Pound", "GBPUSD=X"),
                           ("Japanese Yen", "JPY=X"), ("Swiss Franc", "CHF=X"), ("Canadian Dollar", "CAD=X"), ("Australian Dollar", "AUDUSD=X"), ("New Zealand Dollar", "NZDUSD=X")]),
        ("Emerging vs USD (+ = currency stronger)", [("Chinese Yuan", "CNY=X"), ("Indian Rupee", "INR=X"), ("Brazilian Real", "BRL=X"), ("Mexican Peso", "MXN=X"),
                             ("South African Rand", "ZAR=X"), ("Turkish Lira", "TRY=X"), ("Korean Won", "KRW=X")]),
        ("Crosses (+ = first currency stronger)", [("EUR/JPY", "EURJPY=X"), ("EUR/GBP", "EURGBP=X"), ("EUR/CHF", "EURCHF=X"), ("GBP/JPY", "GBPJPY=X"), ("AUD/JPY", "AUDJPY=X")]),
    ]),
    # One board across asset classes: CME/CBOT front months, so the Sunday-evening reopen shows in 1D against Friday's close.
    # Currency futures are quoted in dollars per unit of the currency (+ = currency stronger), never inverted.
    # Treasury futures are prices (+ = yields lower). No VIX future (Yahoo has none; the VX curve is on Cboe).
    "futures": ("Futures (front month, all asset classes)", [
        ("Equity indices", [("E-mini S&P 500", "ES=F"), ("E-mini Nasdaq-100", "NQ=F"), ("E-mini Dow", "YM=F"), ("E-mini Russell 2000", "RTY=F"),
                            ("Nikkei 225 (USD)", "NKD=F")]),
        ("Treasuries (price: + = yields lower)", [("2-Year Note", "ZT=F"), ("5-Year Note", "ZF=F"), ("10-Year Note", "ZN=F"), ("30-Year Bond", "ZB=F"),
                                                 ("Ultra Bond", "UB=F")]),
        ("Currencies ($ per unit: + = currency stronger)", [("Euro", "6E=F"), ("Japanese Yen", "6J=F"), ("British Pound", "6B=F"), ("Canadian Dollar", "6C=F"),
                                                            ("Australian Dollar", "6A=F"), ("Swiss Franc", "6S=F")]),
        ("Commodities", [("WTI Crude", "CL=F"), ("Brent Crude", "BZ=F"), ("Natural Gas (U.S.)", "NG=F"), ("Gold", "GC=F"), ("Silver", "SI=F"), ("Copper", "HG=F"),
                         ("Corn", "ZC=F"), ("Soybeans", "ZS=F")]),
        ("Crypto (CME)", [("Bitcoin", "BTC=F"), ("Ether", "ETH=F")]),
    ]),
    # Markets Summary > Overview: the futures a desk checks first; currencies and commodities have their own boards there
    "futures_brief": ("Index and Treasury futures (front month)", [
        ("Equity indices", [("E-mini S&P 500", "ES=F"), ("E-mini Nasdaq-100", "NQ=F"), ("E-mini Dow", "YM=F"), ("E-mini Russell 2000", "RTY=F")]),
        ("Treasuries (price: + = yields lower)", [("10-Year Note", "ZN=F"), ("30-Year Bond", "ZB=F")]),
    ]),
    "commodities": ("Commodities (front-month futures)", [
        ("Energy", [("WTI Crude", "CL=F"), ("Brent Crude", "BZ=F"), ("Natural Gas (U.S.)", "NG=F"), ("Natural Gas (Europe TTF)", "TTF=F"), ("Gasoline", "RB=F"), ("Heating Oil", "HO=F"), ("Uranium (physical trust)", "U-UN.TO")]),
        ("Metals", [("Gold", "GC=F"), ("Silver", "SI=F"), ("Copper", "HG=F"), ("Platinum", "PL=F"), ("Palladium", "PA=F")]),
        ("Agriculture", [("Corn", "ZC=F"), ("Wheat", "ZW=F"), ("Soybeans", "ZS=F"), ("Coffee", "KC=F"), ("Sugar", "SB=F"), ("Cotton", "CT=F"), ("Cocoa", "CC=F")]),
        ("Livestock", [("Live Cattle", "LE=F"), ("Lean Hogs", "HE=F")]),
    ]),
    "agriculture": ("Agriculture (futures and equities)", [
        ("Grains and oilseeds", [("Corn", "ZC=F"), ("Wheat (Chicago)", "ZW=F"), ("Wheat (Kansas)", "KE=F"), ("Soybeans", "ZS=F"), ("Soybean meal", "ZM=F"), ("Soybean oil", "ZL=F"),
                                 ("Oats", "ZO=F"), ("Rough rice", "ZR=F")]),
        ("Softs", [("Coffee", "KC=F"), ("Cocoa", "CC=F"), ("Sugar", "SB=F"), ("Cotton", "CT=F"), ("Orange juice", "OJ=F")]),
        ("Livestock and dairy", [("Live cattle", "LE=F"), ("Feeder cattle", "GF=F"), ("Lean hogs", "HE=F"), ("Class III milk", "DC=F")]),
        ("Agribusiness", [("Agriculture ETF", "DBA"), ("Agribusiness equities", "MOO"), ("Deere", "DE"), ("Archer-Daniels-Midland", "ADM"), ("Bunge", "BG"), ("Corteva", "CTVA"),
                          ("Nutrien", "NTR"), ("Mosaic", "MOS"), ("CF Industries", "CF")]),
    ]),
    "energy": ("Energy markets", [
        ("Crude and products", [("WTI crude", "CL=F"), ("Brent crude", "BZ=F"), ("Gasoline (RBOB)", "RB=F"), ("Diesel / heating oil", "HO=F")]),
        ("Gas, power fuels", [("U.S. natural gas (Henry Hub)", "NG=F"), ("European gas (TTF, EUR/MWh)", "TTF=F"), ("Uranium (physical trust, C$)", "U-UN.TO"), ("Uranium miners", "URA")]),
        ("Energy equities", [("Energy sector", "XLE"), ("Exploration & production", "XOP"), ("Oil services", "OIH"), ("Exxon Mobil", "XOM"), ("Chevron", "CVX"),
                             ("Shell", "SHEL"), ("TotalEnergies", "TTE"), ("BP", "BP"), ("Equinor", "EQNR"), ("Cheniere (LNG)", "LNG")]),
        ("Volatility", [("Oil volatility (OVX)", "^OVX")]),
    ]),
    "defense": ("Defense and aerospace", [
        ("U.S. primes", [("Lockheed Martin", "LMT"), ("RTX", "RTX"), ("Northrop Grumman", "NOC"), ("General Dynamics", "GD"), ("L3Harris", "LHX")]),
        ("Europe", [("Rheinmetall", "RHM.DE"), ("BAE Systems", "BA.L"), ("Leonardo", "LDO.MI"), ("Thales", "HO.PA"), ("Saab", "SAAB-B.ST")]),
        ("ETFs", [("U.S. aerospace & defense", "ITA"), ("U.S. A&D equal weight", "XAR"), ("Europe defense", "EUAD")]),
    ]),
    "shipping": ("Shipping and freight", [
        ("Tankers", [("Frontline", "FRO"), ("Scorpio Tankers", "STNG"), ("DHT", "DHT"), ("Teekay Tankers", "TNK"), ("Crude tanker freight ETF", "BWET")]),
        ("Containers and dry bulk", [("ZIM", "ZIM"), ("Maersk", "MAERSK-B.CO"), ("Hapag-Lloyd", "HLAG.DE"), ("Dry bulk freight ETF", "BDRY")]),
    ]),
    "risk_monitor": ("Risk monitor", [
        ("Volatility", [("Equities (VIX)", "^VIX"), ("Rates (MOVE)", "^MOVE"), ("Oil (OVX)", "^OVX"), ("Gold (GVZ)", "^GVZ")]),
        ("Safe havens", [("Gold", "GC=F"), ("U.S. 10-year yield (changes in bp)", "^TNX"), ("Swiss Franc", "CHF=X"), ("Japanese Yen", "JPY=X"), ("Bitcoin", "BTC-USD")]),
        ("War and energy exposure", [("Brent crude", "BZ=F"), ("European gas (TTF)", "TTF=F"), ("Defense (ITA)", "ITA"), ("Europe defense", "EUAD"), ("Tanker freight", "BWET")]),
        ("Exposed markets", [("Israel (TA-125)", "^TA125.TA"), ("Saudi Arabia", "KSA"), ("Turkey", "TUR"), ("Russian Rouble", "RUB=X"), ("Israeli Shekel", "ILS=X")]),
    ]),
    "crypto": ("Crypto", [
        ("Largest coins (USD)", [("Bitcoin", "BTC-USD"), ("Ethereum", "ETH-USD"), ("Solana", "SOL-USD"), ("XRP", "XRP-USD"), ("BNB", "BNB-USD"), ("Cardano", "ADA-USD"),
                                 ("Dogecoin", "DOGE-USD"), ("Chainlink", "LINK-USD"), ("Avalanche", "AVAX-USD"), ("Litecoin", "LTC-USD")]),
    ]),
    "fixed_income": ("Fixed income (ETFs)", [
        ("Government", [("T-Bills 1-3M", "BIL"), ("Treasuries 1-3Y", "SHY"), ("Treasuries 7-10Y", "IEF"), ("Treasuries 20Y+", "TLT"), ("U.S. Treasuries", "GOVT"), ("TIPS", "TIP"),
                        ("Municipals", "MUB"), ("Intl Treasuries", "BWX")]),
        ("Credit", [("Aggregate", "AGG"), ("High Grade", "LQD"), ("High Yield", "HYG"), ("Senior Loans", "BKLN"), ("EM Sovereign USD", "EMB"), ("Convertibles", "CWB"), ("Mortgage-Backed", "MBB"),
                    ("Intl Aggregate", "BNDX")]),
    ]),
    "themes": ("Investment themes (one ETF each)", [
        ("AI and technology", [("Semiconductors", "SMH"), ("Software", "IGV"), ("AI & Robotics", "BOTZ"), ("Cloud Computing", "SKYY"), ("Cybersecurity", "CIBR"), ("Data Centers & Digital Infra", "DTCR")]),
        ("Power and energy transition", [("Grid & Electrification", "GRID"), ("Utilities", "XLU"), ("Nuclear & Uranium", "NLR"), ("Clean Energy", "ICLN"), ("Copper Miners", "COPX"), ("Lithium & Batteries", "LIT")]),
        ("Credit and private markets", [("Private Credit (BDCs)", "BIZD"), ("Listed Private Equity", "PSP"), ("Senior Loans", "BKLN"), ("AAA CLOs", "JAAA"), ("High Yield", "HYG"), ("Regional Banks", "KRE")]),
        ("Other", [("Aerospace & Defense", "ITA"), ("Homebuilders", "XHB"), ("Biotech", "XBI"), ("China Internet", "KWEB"), ("Gold Miners", "GDX"), ("Bitcoin", "IBIT")]),
    ]),
    "ai_complex": ("AI: chips, platforms, software, hardware", [
        ("Chips and equipment", [("Nvidia", "NVDA"), ("Broadcom", "AVGO"), ("AMD", "AMD"), ("TSMC", "TSM"), ("Micron", "MU"), ("ASML", "ASML"), ("Arm", "ARM"), ("Marvell", "MRVL")]),
        ("Hyperscalers and platforms", [("Microsoft", "MSFT"), ("Alphabet", "GOOGL"), ("Amazon", "AMZN"), ("Meta", "META"), ("Oracle", "ORCL"), ("CoreWeave", "CRWV")]),
        ("AI software", [("Palantir", "PLTR"), ("Salesforce", "CRM"), ("ServiceNow", "NOW"), ("Snowflake", "SNOW"), ("Adobe", "ADBE")]),
        ("Servers and networking", [("Dell", "DELL"), ("Super Micro", "SMCI"), ("Arista", "ANET"), ("Cisco", "CSCO"), ("Credo", "CRDO")]),
    ]),
    "datacenter_power": ("Data centers and the power they need", [
        ("Data-center operators", [("Equinix", "EQIX"), ("Digital Realty", "DLR"), ("Iron Mountain", "IRM")]),
        ("Power and cooling equipment", [("Vertiv", "VRT"), ("Eaton", "ETN"), ("GE Vernova", "GEV"), ("Quanta Services", "PWR"), ("Schneider (ADR)", "SBGSY")]),
        ("Power producers", [("Constellation", "CEG"), ("Vistra", "VST"), ("NRG", "NRG"), ("Talen", "TLN")]),
        ("Regulated utilities", [("NextEra", "NEE"), ("Dominion", "D"), ("Southern", "SO"), ("Duke", "DUK"), ("AEP", "AEP")]),
        ("Nuclear and uranium", [("Cameco", "CCJ"), ("BWX Technologies", "BWXT"), ("Oklo", "OKLO"), ("NuScale", "SMR")]),
        ("Natural gas supply", [("EQT", "EQT"), ("Williams", "WMB"), ("Kinder Morgan", "KMI")]),
    ]),
    "private_markets": ("Private credit and private equity (listed proxies)", [
        ("Alternative asset managers", [("Blackstone", "BX"), ("KKR", "KKR"), ("Apollo", "APO"), ("Ares", "ARES"), ("Blue Owl", "OWL"), ("Carlyle", "CG"), ("TPG", "TPG"), ("Brookfield AM", "BAM")]),
        ("Business development companies", [("Ares Capital", "ARCC"), ("Blue Owl Capital", "OBDC"), ("Blackstone Secured", "BXSL"), ("FS KKR", "FSK"), ("Main Street", "MAIN"), ("Golub", "GBDC"), ("BDC index ETF", "BIZD")]),
        ("Loans and CLOs", [("Senior Loans", "BKLN"), ("Senior Loans (active)", "SRLN"), ("AAA CLOs", "JAAA"), ("BBB-B CLOs", "JBBB"), ("High Yield bonds", "HYG")]),
        ("Listed private equity", [("Global listed PE", "PSP")]),
    ]),
    "banks": ("Banks and financials", [
        ("Money-center banks", [("JPMorgan", "JPM"), ("Bank of America", "BAC"), ("Citigroup", "C"), ("Wells Fargo", "WFC")]),
        ("Investment banks and brokers", [("Goldman Sachs", "GS"), ("Morgan Stanley", "MS"), ("Charles Schwab", "SCHW")]),
        ("Regional banks", [("Regional bank ETF", "KRE"), ("U.S. Bancorp", "USB"), ("PNC", "PNC"), ("Truist", "TFC")]),
        ("Insurance and exchanges", [("Insurance ETF", "KIE"), ("Berkshire Hathaway", "BRK-B"), ("CME Group", "CME"), ("Intercontinental Exchange", "ICE")]),
    ]),
    "us_rates": ("U.S. Treasury yields (CBOE indices, in %)", [
        ("Yields in %; changes in basis points", [("13-Week Bill", "^IRX"), ("5-Year Note", "^FVX"), ("10-Year Note", "^TNX"), ("30-Year Bond", "^TYX")]),
    ]),
    "nordics": ("Nordic markets", [
        ("Indices", [("OMX Stockholm 30", "^OMX"), ("OMX Copenhagen 25", "^OMXC25"), ("OMX Helsinki 25", "^OMXH25"), ("Oslo OBX", "OBX.OL")]),
        ("Large caps", [("Novo Nordisk", "NOVO-B.CO"), ("Atlas Copco", "ATCO-A.ST"), ("Investor", "INVE-B.ST"), ("Volvo", "VOLV-B.ST"), ("Ericsson", "ERIC-B.ST"),
                        ("Equinor", "EQNR.OL"), ("DNB Bank", "DNB.OL"), ("Nordea", "NDA-FI.HE"), ("Nokia", "NOKIA.HE"), ("DSV", "DSV.CO"), ("Vestas", "VWS.CO")]),
        ("Currencies", [("Swedish krona", "SEK=X"), ("Norwegian krone", "NOK=X"), ("Danish krone", "DKK=X")]),
    ]),
    "volatility": ("Volatility indices", [
        ("Implied volatility", [("Equity (VIX)", "^VIX"), ("VIX of VIX", "^VVIX"), ("Nasdaq 100 (VXN)", "^VXN"), ("Russell 2000 (RVX)", "^RVX"), ("Oil (OVX)", "^OVX"), ("Gold (GVZ)", "^GVZ"),
                                ("Rates (MOVE)", "^MOVE"), ("VIX 3-Month", "^VIX3M")]),
    ]),
}

BOARD_OPTIONS = [{"label": v[0], "value": k} for k, v in BOARDS.items()]


def _resolve_board(preset, symbols):
    if symbols:
        syms = [s.strip() for s in str(symbols).replace(";", ",").split(",") if s.strip()]
        return [("", [(s.upper(), s.upper()) for s in syms])]
    if preset not in BOARDS:
        raise C.CompositeError(f"Unknown board '{preset}'. Choose one of: {', '.join(BOARDS)}.")
    return BOARDS[preset][1]


def _dated(out, dates, none):
    """out (a fresh answer) with as_of = the newest of dates, or as_of_na = none when no bar was dated."""
    newest = C.newest_date(dates)
    if newest:
        out["as_of"] = newest
    else:
        out["as_of_na"] = none
    return out


def _last_bars(hist, syms):
    """The date of the last bar of each symbol that has one."""
    return [ser[-1][0] for s in syms for ser in [hist.get(s)] if ser]


def board(preset="us_equity", symbols=None, **_):
    """Quote board with multi-horizon performance and a 3-month sparkline."""
    sections = C._resolve_board(preset, symbols)
    syms = [s for _, items in sections for _, s in items]
    hist = C.history(syms, dt.date.today() - dt.timedelta(days=410))
    basis, divs = C.returns_basis(syms), getattr(hist, "divs", {})
    rows = []
    for title, items in sections:
        if title:
            rows.append({"_section": title})
        for label, s in items:
            row = C._quote_row(label, s, hist.get(s) or [], divs.get(s) if basis == "total" else None)
            if preset == "fx" and not symbols and row.get("price") is not None:
                _fx_columns(row, s, hist[s])
            rows.append(row)
    out = C._with_convention({"results": C._mark_stale(rows), "sources": [C.yahoo_source(syms)]}, syms, basis)
    _dated(out, [r.get("as_of") for r in rows], "No price history for these symbols right now.")
    if preset == "fx" and not symbols:          # no distributions to speak of: say which closes the changes run between
        days = [r["as_of"][:10] for r in rows if r.get("as_of")]
        if days:
            out["convention"] = f"Closes at 17:00 New York, to {max(days)}"
    warn = C.null_warning(rows)
    if warn:
        out["warning"] = warn
    return out


def _fx_columns(row, s, ser):
    """Currencies board: the pair as quoted and the currency against the dollar, each in a column that says so."""
    row.update(pair_last=row["price"], pair_pct=ser[-1][1] / ser[-2][1] - 1, pair_ytd=C._ret(ser, "YTD"), usd_pct=None, usd_ytd=None)
    pair = C.fx_pair(s)
    dp = C.price_decimals(row["price"])
    if 0.1 <= abs(row["price"]) < 1:          # pairs are quoted to the pip (0.6623 AUD/USD): the pair beside it needs the room
        dp = 4
    row["_fmt"]["pair_last"] = {"decimalPlaces": dp, "suffix": " " + C.fx_pair_label(s) if pair else ""}
    if pair and "USD" in pair:          # a cross and the dollar index are not a currency against the dollar
        row.update(usd_pct=row["chg_pct"], usd_ytd=row["ytd"])


def null_warning(rows, share=0.5):
    """A board whose rows mostly came back without a price says so (review C4: Global equity indices showed 20 of 21 rows
    as dashes with nothing to tell a throttled source from a market holiday)."""
    data = [r for r in rows if not r.get("_section")]
    empty = sum(1 for r in data if r.get("price") is None)
    if data and empty / len(data) > share:
        return f"{empty} of {len(data)} rows have no price from Yahoo Finance right now (throttled or unlisted); refresh in a minute"
    return None


STALE_ROW_DAYS = 4          # calendar days behind the newest row of the same board: a long weekend still passes


def _mark_stale(rows):
    """A row whose last bar is more than STALE_ROW_DAYS older than the newest row of its board (halted, delisted or a
    market on holiday) shows its last trade date in "stale" and no 1D change, which would be weeks old."""
    dates = [r["as_of"][:10] for r in rows if r.get("as_of")]
    if not dates:
        return rows
    newest = dt.date.fromisoformat(max(dates))
    for r in rows:
        if r.get("as_of") and (newest - dt.date.fromisoformat(r["as_of"][:10])).days > STALE_ROW_DAYS:
            r.update(stale=r["as_of"][:10], chg=None, chg_pct=None)
            r.update({k: None for k in ("pair_pct", "usd_pct") if k in r})
    return rows


def _with_convention(out, syms, basis, short=False):
    """Say which return convention the view's horizon columns use (nothing for a board of yields: those are bp)."""
    if not all(C.is_yield(s) for s in syms):
        out["convention"] = (C.CONVENTION_SHORT if short else C.CONVENTION)[basis]
    return out


BP_FMT = {"formatterFn": "int", "suffix": " bp", "decimalPlaces": None, "heatScale": 25}

HORIZONS = (("w1", "1W"), ("m1", "1M"), ("m3", "3M"), ("ytd", "YTD"), ("y1", "1Y"))


def currency_view(label, symbol) -> bool:
    """A row named after a currency (not a pair) whose Yahoo quote is USD/XXX is shown as XXX against the dollar."""
    p = C.fx_pair(symbol)
    return bool(p and p[0] == "USD" and "/" not in str(label))


# Quoting unit of the Yahoo front-month futures the boards show (the exchange's contract specification: CME/NYMEX/COMEX/
# CBOT, ICE US softs, ICE Endex TTF). Cents are written "¢".
FUTURES_UNITS = {"CL=F": "$/bbl", "BZ=F": "$/bbl", "NG=F": "$/MMBtu", "TTF=F": "EUR/MWh", "RB=F": "$/gal", "HO=F": "$/gal",
                 "GC=F": "$/oz", "SI=F": "$/oz", "PL=F": "$/oz", "PA=F": "$/oz", "HG=F": "$/lb",
                 "ZC=F": "¢/bu", "ZW=F": "¢/bu", "KE=F": "¢/bu", "ZS=F": "¢/bu", "ZM=F": "$/short ton", "ZL=F": "¢/lb",
                 "KC=F": "¢/lb", "SB=F": "¢/lb", "CT=F": "¢/lb", "CC=F": "$/t", "LE=F": "¢/lb", "HE=F": "¢/lb", "U-UN.TO": "C$",
                 # financial futures (CME, CBOT): index points; Treasuries in points of par (decimal, not 32nds); dollars per currency unit
                 "ES=F": "pts", "NQ=F": "pts", "YM=F": "pts", "RTY=F": "pts", "NKD=F": "pts",
                 "ZT=F": "% of par", "ZF=F": "% of par", "ZN=F": "% of par", "ZB=F": "% of par", "UB=F": "% of par",
                 "6E=F": "$/EUR", "6J=F": "$/JPY", "6B=F": "$/GBP", "6C=F": "$/CAD", "6A=F": "$/AUD", "6S=F": "$/CHF",
                 "BTC=F": "$/BTC", "ETH=F": "$/ETH"}
# Decimals where size-based precision hides the exchange tick: Treasury futures move in 32nds (ZT 1/256 = 0.0039),
# currency futures in 0.00005 (6J in 0.0000005).
FUTURES_DECIMALS = {"ZT=F": 4, "ZF=F": 4, "ZN=F": 4, "ZB=F": 4, "UB=F": 4,
                    "6E=F": 5, "6B=F": 5, "6C=F": 5, "6A=F": 5, "6S=F": 5, "6J=F": 7}


def price_decimals(v) -> int:
    """Quote precision by size: 2 at 50 and above (indices, futures, USD/JPY, BTC), 4 from 1 (EUR/USD), 5 below 1, 6 below 0.01."""
    a = abs(v or 0)
    return 2 if a >= 50 else 4 if a >= 1 else 5 if a >= 0.01 else 6


def _quote_row(label, s, ser, divs=None):
    """One board line: last, change, returns over five horizons, 3-month sparkline, 52-week range (from closes).

    With `divs` (cash distributions by ex-date; the caller passes them only when the whole board is stocks and funds)
    the five horizons are total returns; Last, 1D %, the sparkline and the 52-week range stay on prices.
    Yields (^TNX...) report changes in basis points (row-level "_fmt" tells the table). A currency row whose Yahoo
    quote is USD/XXX reports XXX's own move (+ = currency stronger); "price" stays the market quote, shown with
    "quote" (e.g. USD/JPY)."""
    if len(ser) < 2:
        return {"name": label, "symbol": s, "price": None, "note": "no data"}
    last, prev = ser[-1][1], ser[-2][1]
    pair = C.fx_pair_label(s)
    if C.is_yield(s):
        year = [c for _, c in ser[-253:]]
        lo, hi = min(year), max(year)
        row = {"name": label, "symbol": s, "price": last, "chg": round((last - prev) * 100, 1), "chg_pct": None,
               **{f: C._diff_bp(ser, k) for f, k in HORIZONS}, "spark": [round(c, 6) for _, c in ser[-63:]],
               "range52": round((last - lo) / (hi - lo) * 100, 1) if hi > lo else None, "low52": lo, "high52": hi, "as_of": ser[-1][0],
               "_fmt": {"chg": BP_FMT, **{f: BP_FMT for f, _ in HORIZONS}, "spark": {"bp": True}}}
        return row
    inverted = C.currency_view(label, s)
    view = C._invert(ser) if inverted else ser
    vl, vp = view[-1][1], view[-2][1]
    year = [c for _, c in ser[-253:]]
    lo, hi = min(year), max(year)
    vyear = [c for _, c in view[-253:]]          # the 52-week position is of what the row measures (the currency's own value)
    vlo, vhi = min(vyear), max(vyear)
    horizon = C.total_return(view, divs) if divs and not inverted else view
    row = {
        "name": label, "symbol": s, "price": last,
        "chg": None if inverted else last - prev,
        "chg_pct": vl / vp - 1 if vp else None,
        **{f: C._ret(horizon, k) for f, k in HORIZONS},
        "spark": [round(c, 8) for _, c in view[-63:]],
        "range52": round((vl - vlo) / (vhi - vlo) * 100, 1) if vhi > vlo else None,
        "low52": lo, "high52": hi, "as_of": ser[-1][0],
    }
    if pair:
        row["quote"] = pair
    # one precision per instrument: 2 decimals for indices, futures and USD/JPY, 4 for EUR/USD, more for sub-dollar coins
    dp = FUTURES_DECIMALS.get(s) or C.price_decimals(last)
    row["_fmt"] = {"price": {"decimalPlaces": dp}, "chg": {"decimalPlaces": dp}}
    if s in FUTURES_UNITS:                      # TTF 85.48 EUR/MWh beside Henry Hub 3.10 $/MMBtu (review A6)
        row["_fmt"]["price"]["suffix"] = " " + FUTURES_UNITS[s]
    if inverted:
        # A currency named in the row (Japanese yen) whose market quote is USD/XXX: every change column, the sparkline
        # and the 52-week position measure the currency against the dollar (+ = yen stronger). "Last" stays the market
        # quote, labelled with the pair so 159.60 cannot be read as the yen's price; the quote-unit change is left out
        # because its sign would contradict the quote shown next to it.
        row["_fmt"] = {"price": {"suffix": " " + pair, "decimalPlaces": dp}, "spark": {"pct": True}}
        row["_rangeTip"] = (f"{pair} 52-week range {lo:,.2f} – {hi:,.2f}. The {label} is at {row['range52']}% of its own 52-week range "
                            "against the dollar (0 = weakest, 100 = strongest).") if row["range52"] is not None else None
        row["note"] = f"Changes, trend and range: {label} against the dollar (+ = {label} stronger); Last = {pair} quote"
    return row


def _labels(preset, symbols):
    return {s: label for _, items in C._resolve_board(preset, symbols) for label, s in items}


def performance(symbols="^GSPC,^NDX,^DJI", period="YTD", preset=None, **_):
    """Normalised performance (percent change from the start of the period)."""
    period = str(period or "YTD").upper()
    labels = C._labels(preset, None) if preset and not symbols else {}
    syms = list(labels) if labels else [s.strip() for s in str(symbols).replace(";", ",").split(",") if s.strip()]
    if len(syms) > 8:
        syms = syms[:8]
    today = dt.date.today()
    start = C.horizon_date(today, period) - dt.timedelta(days=7)
    interval = "1W" if period in ("5Y", "10Y") else "1d"
    hist = C.history(syms, start, interval)
    data, notes = [], []
    all_yields = all(C.is_yield(s) for s in syms)
    basis, divs = C.returns_basis([s for s in syms if not C.is_yield(s)]), getattr(hist, "divs", {})   # yields plot in bp
    for i, s in enumerate(syms):
        ser = hist.get(s) or []
        if len(ser) < 2:
            notes.append(s)
            continue
        name = labels.get(s) or C.fx_pair_label(s) or s          # an FX ticker is named as the pair it is (JPY=X = USD/JPY)
        if labels.get(s) and C.currency_view(name, s):
            ser = C._invert(ser)                                 # a currency named on a board: its own move against the dollar
        elif basis == "total":
            ser = C.total_return(ser, divs.get(s))               # every line a stock or fund: distributions reinvested
        # the base comes from the series' own last bar, like the boards (_base): 1D = the previous bar, else the last close at
        # or before the horizon counted from the last bar. Drawn from that close, so the line starts at 0. (Counting from
        # today showed +0.0% for 1D on a weekend.)
        if period == "1D":
            k = len(ser) - 2
        else:
            ref = C.horizon_date(dt.date.fromisoformat(ser[-1][0][:10]), period).isoformat()
            k = bisect.bisect_right([p[0] for p in ser], ref) - 1
        pts = ser[max(k, 0):]
        base = pts[0][1]
        if not base:
            notes.append(s)
            continue
        if C.is_yield(s):                                        # yields: change in basis points, never percent of the yield
            ys = [round((c - base) * 100, 1) for _, c in pts]
            tr = {"name": f"{name}  {ys[-1]:+.0f} bp", "hovertemplate": "%{y:+.0f} bp<extra>" + name + "</extra>"}
            if not all_yields:
                tr.update(yaxis="y2", line={"width": 2, "dash": "dot", "color": C.PALETTE[i % len(C.PALETTE)]})
        else:
            ys = [round((c / base - 1) * 100, 3) for _, c in pts]
            tr = {"name": f"{name}  {ys[-1]:+.1f}%", "hovertemplate": "%{y:+.2f}%<extra>" + name + "</extra>"}
        data.append(dict({"type": "scatter", "mode": "lines", "x": [d for d, _ in pts], "y": ys, "line": {"width": 2, "color": C.PALETTE[i % len(C.PALETTE)]}}, **tr))
    if not data:
        raise C.CompositeError("No price history returned for: " + ", ".join(syms))
    lay = C._layout(len(data) > 1)
    lay["yaxis"]["ticksuffix"] = " bp" if all_yields else "%"
    lay["yaxis"]["zeroline"] = True
    if any(t.get("yaxis") == "y2" for t in data):
        lay["yaxis2"] = {"overlaying": "y", "side": "right", "ticksuffix": " bp", "showgrid": False, "zeroline": False, "automargin": True}
        lay["margin"]["r"] = 48
    text = "; ".join(([] if all_yields else [C.CONVENTION_SHORT[basis]]) + (["No data: " + ", ".join(notes)] if notes else []))
    if text:
        lay["annotations"] = [C._note(text)]
    return _dated({"data": data, "layout": lay}, [t["x"][-1] for t in data], "No price history for this choice.")


def perf_bars(preset="us_sectors", period="1D", symbols=None, **_):
    """Ranked performance bars for one board."""
    period = str(period or "1D").upper()
    sections = C._resolve_board(preset, symbols)
    items = [(label, s) for _, its in sections for label, s in its]
    vs_usd = preset == "fx" and not symbols          # one measure only: DXY and the crosses are not a currency against the dollar
    if vs_usd:
        items = [(label, s) for label, s in items if "USD" in (C.fx_pair(s) or ())]
    hist = C.history([s for _, s in items], dt.date.today() - dt.timedelta(days=410))
    yields = [(label, s) for label, s in items if C.is_yield(s)]
    bp = bool(yields) and len(yields) == len(items)          # a board of yields ranks changes in basis points
    basis, divs = C.returns_basis([s for _, s in items if not C.is_yield(s)]), getattr(hist, "divs", {})
    vals, left_out, last_bars = [], [], []
    for label, s in items:
        ser = hist.get(s) or []
        if C.is_yield(s) and not bp:
            left_out.append(label)                          # a yield change is not a return: keep it out of a return ranking
            continue
        if bp:
            r = C._diff_bp(ser, period)
        else:   # a USD/XXX row ranks the foreign currency's own move (inverted quote); every return is in percent
            r = C._ret(C._invert(ser) if C.currency_view(label, s) else C.total_return(ser, divs.get(s)) if basis == "total" else ser, period)
            r = None if r is None else r * 100
        if r is not None:
            vals.append((label, r))
            last_bars.append(ser[-1][0])
    if not vals:
        raise C.CompositeError("No price history returned.")
    vals.sort(key=lambda x: x[1])
    unit = " bp" if bp else "%"
    lay = C._layout(False, hovermode="closest")
    lay["margin"] = {"l": 8, "r": 46, "t": 6, "b": 26}
    lay["xaxis"].update({"ticksuffix": unit, "showgrid": True, "gridcolor": C.INK["grid"], "zeroline": True, "zerolinecolor": C.INK["axis"]})
    lay["yaxis"].update({"showgrid": False, "tickfont": {"color": C.INK["text2"]}})
    lay["bargap"] = 0.3
    if vs_usd:          # under the axis, where it cannot cover the lowest bar, and room for the values past both ends
        xs = [0.0] + [v for _, v in vals]
        pad = (max(xs) - min(xs) or 1) * 0.30          # a value label is up to a third of a narrow card's plot
        lay["margin"]["b"] = 40
        lay["xaxis"].update(range=[min(xs) - (pad if min(xs) < 0 else 0), max(xs) + (pad if max(xs) > 0 else 0)],
                            title={"text": "Against USD · right = stronger", "font": {"size": 10}})
    text = "; ".join(([] if bp or vs_usd else [C.CONVENTION_SHORT[basis]]) + (["Not ranked (yields; see the board for their change in bp): " + ", ".join(left_out)] if left_out else []))
    if text:
        lay["annotations"] = [{"text": text, "xref": "paper", "yref": "paper", "x": 1, "y": 0,
                               "xanchor": "right", "showarrow": False, "font": {"color": C.INK["muted"], "size": 10}}]
    fmt = (lambda v: f"{v:+.0f} bp") if bp else (lambda v: f"{v:+.2f}%")
    longest = max(abs(v) for _, v in vals)          # currencies: the longest bars carry their value inside, clear of the names
    where = ["inside" if longest and abs(v) >= 0.75 * longest else "outside" for _, v in vals] if vs_usd else "outside"
    return {"data": [{
        "type": "bar", "orientation": "h", "y": [v[0] for v in vals], "x": [round(v[1], 2) for v in vals],
        "marker": {"color": [C.UP if v[1] >= 0 else C.DOWN for v in vals]},
        "text": [fmt(v[1]) for v in vals], "textposition": where, "textfont": {"color": C.INK["text2"], "size": 11}, "cliponaxis": False,
        "hovertemplate": "%{y}: %{x:+.2f}" + unit + "<extra></extra>",
    }], "layout": lay, "as_of": C.newest_date(last_bars)}


STYLE_BOX = [("Large", ("IWD", "IWB", "IWF")), ("Mid", ("IWS", "IWR", "IWP")), ("Small", ("IWN", "IWM", "IWO"))]


def style_box(period="1D", **_):
    """U.S. size x style grid (Russell ETFs)."""
    period = str(period or "1D").upper()
    syms = [s for _, row in STYLE_BOX for s in row]
    hist = C.history(syms, dt.date.today() - dt.timedelta(days=410))
    basis, divs = C.returns_basis(syms), getattr(hist, "divs", {})

    def ret(s):
        ser = hist.get(s) or []
        return C._ret(C.total_return(ser, divs.get(s)) if basis == "total" else ser, period)
    rows = [{"size": size, "value": ret(v), "core": ret(c), "growth": ret(g)} for size, (v, c, g) in STYLE_BOX]
    return _dated(C._with_convention({"results": rows}, syms, basis, short=True), _last_bars(hist, syms), "No price history for the Russell funds right now.")


BOARD_LABELS = {s: label for _, secs in BOARDS.values() for _, items in secs for label, s in items}

# Shown until the user saves a list of their own (the page sends the same starter list; see static/securities.js).
STARTER_WATCHLIST = "AAPL,MSFT,NVDA,ASML,7203.T,SHEL.L,RHM.DE,^GSPC,EURUSD=X,GC=F,BTC-USD"


def watchlist(symbols=None, list=None, **_):  # noqa: A002
    """A user's own list of symbols as a quote board (same columns as the market boards, plus the trading currency)."""
    syms = C._syms(STARTER_WATCHLIST if symbols is None and not list else symbols, 80)
    if not syms:
        raise C.CompositeError("This watchlist is empty. Press Edit on the card to add symbols.")
    hist = C.history(syms, dt.date.today() - dt.timedelta(days=410))
    meta = C._meta(syms)
    basis, divs = C.returns_basis(syms), getattr(hist, "divs", {})
    rows = []
    for s in syms:
        m = meta.get(s) or {}
        # an FX symbol is named as its pair (Yahoo's own name "USD/JPY", or derived): the row then shows the pair as quoted
        row = C._quote_row(C.fx_pair_label(s) or m.get("name") or BOARD_LABELS.get(s) or s, s, hist.get(s) or [], divs.get(s) if basis == "total" else None)
        row["currency"] = m.get("currency")
        rows.append(row)
    return _dated(C._with_convention({"results": C._mark_stale(rows)}, syms, basis), [r.get("as_of") for r in rows], "No price history for these symbols right now.")


# ---------------------------------------------------------------- currency cross matrix (Bloomberg FXC)
FX_MATRIX = ["USD", "EUR", "JPY", "GBP", "CHF", "CAD", "AUD", "NZD", "CNY"]

# the board's own symbols, so a change reads the same on both cards (Yahoo rounds JPYUSD=X to four figures)
FX_MATRIX_PAIRS = {c: c + ("USD=X" if c in ("EUR", "GBP", "AUD", "NZD") else "=X") for c in FX_MATRIX if c != "USD"}


def fx_matrix(**_):
    """Cross rates of the major currencies: each row is one unit of that currency priced in the column currency."""
    today = C._today()
    # YTD needs the close of the year before the last bar's year: early in January the last bar can still be 31 Dec
    hist = C.history(list(FX_MATRIX_PAIRS.values()), dt.date(today.year - (2 if today.timetuple().tm_yday <= 10 else 1), 12, 20))
    usd = {"USD": [("", 1.0)]}
    for c, p in FX_MATRIX_PAIRS.items():
        ser = hist.get(p) or []
        if C.fx_pair(p)[0] == "USD":
            ser = C._invert(ser)
        if len(ser) >= 2:
            usd[c] = ser
    if len(usd) < 3:
        raise C.CompositeError("Yahoo Finance returned no currency prices.")
    rows = []
    for r in FX_MATRIX:
        if r not in usd:
            continue
        row = {"ccy": r, "_fmt": {}}
        for c in FX_MATRIX:
            if c in usd:
                row[c] = None if c == r else usd[r][-1][1] / usd[c][-1][1]
                if row[c] is not None:
                    # about four significant figures per cell: 1 JPY = 0.006401 USD, not "0.01" (review A6)
                    a = abs(row[c])
                    row["_fmt"][c] = {"decimalPlaces": 2 if a >= 100 else 4 if a >= 0.1 else 6}
        if r != "USD":
            ser = usd[r]
            row["chg_1d"] = ser[-1][1] / ser[-2][1] - 1
            row["ytd"] = C._ret(ser, "YTD")
            row["as_of"] = ser[-1][0]
        rows.append(row)
    return {"results": rows}
