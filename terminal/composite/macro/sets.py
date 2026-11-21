"""Curated MACRO_SETS definitions, categories, and UI picker options."""

from __future__ import annotations

# key -> (title, y-axis suffix, default transform, [(label, FRED id, multiplier), ...])
# Series in one set always share a unit, so the chart needs a single axis.
MACRO_SETS = {
    # --- credit
    "credit_spreads": ("Credit spreads (option-adjusted)", " bp", None, [("High yield", "BAMLH0A0HYM2", 100), ("BBB", "BAMLC0A4CBBB", 100), ("Investment grade", "BAMLC0A0CM", 100), ("EM corporate", "BAMLEMCBPIOAS", 100)]),
    "distressed_spreads": ("High-yield and CCC spreads", " bp", None, [("CCC and lower", "BAMLH0A3HYC", 100), ("High yield", "BAMLH0A0HYM2", 100)]),
    "corporate_yields": ("Corporate bond yields", "%", None, [("High yield", "BAMLH0A0HYM2EY", 1), ("Moody's Baa", "DBAA", 1), ("Moody's Aaa", "DAAA", 1), ("Investment grade", "BAMLC0A0CMEY", 1)]),
    "corporate_debt": ("Non-financial corporate debt", " $tn", None, [("Debt securities and loans", "BCNSDODNS", 1e-6), ("of which bonds", "NCBDBIQ027S", 1e-6)]),
    "bank_lending": ("Bank lending by type", " $tn", None, [("Commercial real estate", "CREACBW027SBOG", 1e-3), ("Commercial & industrial", "BUSLOANS", 1e-3), ("Loans to non-bank lenders", "LNFACBW027SBOG", 1e-3), ("Consumer", "CLSACBW027SBOG", 1e-3)]),
    "delinquencies": ("Loan delinquency rates at banks", "%", None, [("Credit cards", "DRCCLACBS", 1), ("Single-family mortgages", "DRSFRMACBS", 1), ("Commercial real estate", "DRCRELEXFACBS", 1), ("Business loans", "DRBLACBS", 1)]),
    "lending_standards": ("Banks tightening lending standards (net share)", "%", None, [("C&I, large firms", "DRTSCILM", 1), ("C&I, small firms", "DRTSCIS", 1), ("Credit cards", "DRTSCLCC", 1)]),
    # Money & Banking (2026-11-19): Fed charge-off rates (quarterly, annualized, SA, %), FDIC Quarterly Banking Profile noncurrent rate (%),
    # H.8 weekly balance sheet ($bn -> $tn), Z.1 issuers of asset-backed securities ($mn -> $tn)
    "charge_offs": ("Net charge-off rates at banks by loan type (annualized)", "%", None, [("Credit cards", "CORCCACBS", 1), ("Consumer loans", "CORCACBS", 1),
                    ("Business (C&I)", "CORBLACBS", 1), ("Commercial real estate", "CORCREXFACBS", 1), ("Single-family mortgages", "CORSFRMACBS", 1), ("All loans", "CORALACBS", 1)]),
    "problem_loans": ("Problem loans: noncurrent (FDIC) and delinquent (Fed), share of loans", "%", None,
                      [("Noncurrent, all insured banks (FDIC)", "QBPLNTLNNCUR", 1), ("Delinquent, all banks (Fed)", "DRALACBS", 1)]),
    "bank_funding": ("Bank deposits and securities (H.8)", " $tn", None, [("Deposits", "DPSACBW027SBOG", 1e-3), ("Securities held", "SBCACBW027SBOG", 1e-3),
                     ("Large time deposits", "LTDACBW027SBOG", 1e-3)]),
    "securitization": ("Securitization: assets of asset-backed securities issuers (Z.1)", " $tn", None,
                       [("All financial assets", "BOGZ1FL674090005Q", 1e-6), ("Mortgages", "BOGZ1FL673065005Q", 1e-6)]),
    "financial_conditions": ("Financial conditions and stress (0 = average)", "", None, [("Chicago Fed NFCI", "NFCI", 1), ("St. Louis Fed stress", "STLFSI4", 1)]),
    # --- rates and liquidity
    "treasury_yields": ("U.S. Treasury yields", "%", None, [("3-month", "DGS3MO", 1), ("2-year", "DGS2", 1), ("10-year", "DGS10", 1), ("30-year", "DGS30", 1)]),
    "curve_spreads": ("Yield-curve slope", "%", None, [("10-year minus 2-year", "T10Y2Y", 1), ("10-year minus 3-month", "T10Y3M", 1)]),
    "real_yields": ("Real yields (TIPS)", "%", None, [("5-year", "DFII5", 1), ("10-year", "DFII10", 1), ("30-year", "DFII30", 1)]),
    "breakevens": ("Market inflation expectations", "%", None, [("5-year breakeven", "T5YIE", 1), ("10-year breakeven", "T10YIE", 1), ("5-year, 5 years forward", "T5YIFR", 1)]),
    "policy_corridor": ("Overnight rates", "%", None, [("SOFR", "SOFR", 1), ("Fed funds (effective)", "EFFR", 1), ("Interest on reserves", "IORB", 1)]),
    "fed_balance_sheet": ("Federal Reserve balance sheet", " $tn", None, [("Total assets", "WALCL", 1e-6), ("Treasuries held", "TREAST", 1e-6), ("Mortgage-backed securities", "WSHOMCB", 1e-6)]),
    "fed_liquidity": ("Dollar liquidity: reserves, Treasury cash, reverse repo", " $tn", None, [("Bank reserves", "WRESBAL", 1e-6), ("Treasury General Account", "WTREGEN", 1e-6), ("Overnight reverse repo", "RRPONTSYD", 1e-3)]),
    "money_supply": ("Money supply (M2)", " $tn", None, [("M2", "M2SL", 1e-3)]),
    # --- public finances and trade
    "federal_debt": ("Federal debt", "% of GDP", None, [("Total public debt", "GFDEGDQ188S", 1)]),
    "federal_deficit": ("Federal deficit and interest cost", "% of GDP", None, [("Surplus or deficit", "FYFSGDA188S", 1), ("Interest outlays", "FYOIGDA188S", 1)]),
    "federal_interest_tariffs": ("Federal interest payments and customs duties (annual rate)", " $bn", None, [("Interest payments", "A091RC1Q027SBEA", 1), ("Customs duties (tariffs)", "B235RC1Q027SBEA", 1)]),
    "trade_balance": ("U.S. trade balance, goods and services (monthly)", " $bn", None, [("Balance", "BOPGSTB", 1e-3)]),
    "dollar": ("Broad U.S. dollar index", "", None, [("Nominal broad dollar", "DTWEXBGS", 1)]),
    # --- growth, jobs, prices
    "gdpnow": ("Atlanta Fed GDPNow (current-quarter growth estimate)", "%", None, [("GDPNow", "GDPNOW", 1)]),
    # regional-Fed gauges whose own pages moved or changed layout: the same series as published on FRED
    "sticky_cpi": ("Sticky vs flexible CPI, Atlanta Fed (year over year)", "%", None, [("Sticky core CPI", "CORESTICKM159SFRBATL", 1), ("Flexible CPI", "FLEXCPIM159SFRBATL", 1)]),
    "wage_tracker": ("Atlanta Fed Wage Growth Tracker (median hourly wage growth, 3-month average, yoy)", "%", None, [("Overall", "FRBATLWGT3MMAUMHWGO", 1)]),
    "texas_manufacturing": ("Dallas Fed Texas manufacturing: general business activity (diffusion index)", "", None, [("Current activity", "BACTSAMFRBDAL", 1)]),
    "global_activity": ("Index of global real economic activity (Kilian, Dallas Fed)", "", None, [("Global real activity", "IGREA", 1)]),
    "trimmed_pce": ("Trimmed-mean PCE inflation, Dallas Fed", "%", None, [("12-month", "PCETRIM12M159SFRBDAL", 1), ("1-month, annualized", "PCETRIM1M158SFRBDAL", 1)]),
    "pce_report": ("Personal income and outlays (year over year)", "%", "yoy", [("PCE prices", "PCEPI", 1), ("Core PCE prices", "PCEPILFE", 1), ("Personal spending", "PCE", 1), ("Personal income", "PI", 1)]),
    "hqm_yields": ("High-quality corporate bond spot rates (HQM curve)", "%", None, [("2-year", "HQMCB2YR", 1), ("5-year", "HQMCB5YR", 1), ("10-year", "HQMCB10YR", 1), ("30-year", "HQMCB30YR", 1)]),
    "inflation": ("Inflation (year over year)", "%", "yoy", [("CPI", "CPIAUCSL", 1), ("Core CPI", "CPILFESL", 1), ("PCE", "PCEPI", 1), ("Core PCE", "PCEPILFE", 1)]),
    "jobless_claims": ("Weekly jobless claims", "k", None, [("Initial claims", "ICSA", 1e-3)]),
    "continuing_claims": ("Continuing jobless claims", "k", None, [("Continuing claims", "CCSA", 1e-3)]),
    "unemployment": ("Unemployment rate", "%", None, [("Headline (U-3)", "UNRATE", 1), ("Broad (U-6)", "U6RATE", 1)]),
    "job_openings": ("Job openings (JOLTS)", "k", None, [("Openings", "JTSJOL", 1)]),
    "industry": ("Industrial production (2017 = 100)", "", None, [("Semiconductors & components", "IPG3344S", 1), ("Electric power", "IPG2211S", 1), ("Total industry", "INDPRO", 1)]),
    "tech_investment": ("Business investment in technology (annual rate)", " $bn", None, [("Information processing equipment & software", "A679RC1Q027SBEA", 1), ("Software", "B985RC1Q027SBEA", 1)]),
    "construction": ("Construction spending by type (annual rate)", " $bn", None, [("Power", "TLPWRCONS", 1e-3), ("Manufacturing", "TLMFGCONS", 1e-3), ("Office, incl. data centers", "TLOFCONS", 1e-3), ("Commercial", "TLCOMCONS", 1e-3)]),
    "corporate_profits": ("Corporate profits after tax (annual rate)", " $bn", None, [("Profits", "CP", 1)]),
    # --- consumer and housing
    "consumer_health": ("Household finances", "%", None, [("Saving rate", "PSAVERT", 1), ("Debt service, share of income", "TDSP", 1), ("Credit-card delinquencies", "DRCCLACBS", 1)]),
    "consumer_sentiment": ("Consumer sentiment (University of Michigan)", "", None, [("Sentiment", "UMCSENT", 1)]),
    "retail_sales": ("Retail sales (year over year)", "%", "yoy", [("Retail and food services", "RSAFS", 1)]),
    "mortgage_rates": ("Mortgage rates", "%", None, [("30-year fixed", "MORTGAGE30US", 1), ("15-year fixed", "MORTGAGE15US", 1), ("10-year Treasury", "DGS10", 1)]),
    "housing_activity": ("Housing starts and permits (annual rate)", "k", None, [("Starts", "HOUST", 1), ("Permits", "PERMIT", 1)]),
    "home_prices": ("Home prices, Case-Shiller national (year over year)", "%", "yoy", [("Home prices", "CSUSHPISA", 1)]),
    "cre_prices": ("Commercial real estate prices (year over year)", "%", None, [("CRE prices", "COMREPUSQ159N", 1)]),
    # --- energy
    "oil_prices": ("Crude oil spot prices", " $/bbl", None, [("WTI", "DCOILWTICO", 1), ("Brent", "DCOILBRENTEU", 1)]),
    "natgas_price": ("Henry Hub natural gas spot price", " $/MMBtu", None, [("Henry Hub", "DHHNGSP", 1)]),
}

MACRO_OPTIONS = [{"label": v[0], "value": k} for k, v in MACRO_SETS.items()]

# ---------------------------------------------------------------- curated sets: energy, geopolitics
MACRO_SETS.update({
    # energy: oil
    "crude_stocks": ("U.S. crude oil inventories", " mn bbl", None, [("Commercial", "EIA:PET.WCESTUS1.W", 1e-3), ("Strategic Petroleum Reserve", "EIA:PET.WCSSTUS1.W", 1e-3)]),
    "crude_stocks_seasonal": ("U.S. commercial crude stocks vs 5-year range", " mn bbl", "seasonal", [("Commercial crude", "EIA:PET.WCESTUS1.W", 1e-3)]),
    "cushing_seasonal": ("Cushing crude stocks vs 5-year range", " mn bbl", "seasonal", [("Cushing", "EIA:PET.W_EPC0_SAX_YCUOK_MBBL.W", 1e-3)]),
    "gasoline_stocks_seasonal": ("U.S. gasoline stocks vs 5-year range", " mn bbl", "seasonal", [("Gasoline", "EIA:PET.WGTSTUS1.W", 1e-3)]),
    "distillate_stocks_seasonal": ("U.S. distillate (diesel) stocks vs 5-year range", " mn bbl", "seasonal", [("Distillate", "EIA:PET.WDISTUS1.W", 1e-3)]),
    "refinery_utilization": ("U.S. refinery utilization vs 5-year range", "%", "seasonal", [("Utilization", "EIA:PET.WPULEUS3.W", 1)]),
    "us_oil_flows": ("U.S. crude production and trade", " mn b/d", None, [("Crude production", "EIA:PET.WCRFPUS2.W", 1e-3), ("Crude imports", "EIA:PET.WCEIMUS2.W@ma28", 1e-3),
                                                                          ("Crude exports", "EIA:PET.WCREXUS2.W@ma28", 1e-3), ("Product exports", "EIA:PET.WRPEXUS2.W@ma28", 1e-3)]),
    "oil_demand": ("U.S. oil demand (products supplied, 4-week average)", " mn b/d", None, [("Products supplied", "EIA:PET.WRPUPUS2.W@ma28", 1e-3)]),
    "pump_prices": ("U.S. retail fuel prices", " $/gal", None, [("Gasoline", "EIA:PET.EMM_EPMR_PTE_NUS_DPG.W", 1), ("Diesel", "EIA:PET.EMD_EPD2D_PTE_NUS_DPG.W", 1)]),
    "crack_spreads": ("Refining margins (crack spreads)", " $/bbl", None, [("3-2-1 crack (WTI)", "CALC:CRACK321", 1), ("Diesel crack (Brent)", "CALC:DIESEL_CRACK", 1), ("Gasoline crack (Brent)", "CALC:GASOLINE_CRACK", 1)]),
    "brent_wti": ("Brent minus WTI spread", " $/bbl", None, [("Brent - WTI", "CALC:BRENT_WTI", 1)]),
    "oil_producers": ("Crude oil production by country (EIA)", " mn b/d", None, [("United States", "EIA:INTL.57-1-USA-TBPD.M", 1e-3), ("Russia", "EIA:INTL.57-1-RUS-TBPD.M", 1e-3),
                                                                                    ("Saudi Arabia", "EIA:INTL.57-1-SAU-TBPD.M", 1e-3), ("Iraq", "EIA:INTL.57-1-IRQ-TBPD.M", 1e-3),
                                                                                    ("Iran", "EIA:INTL.57-1-IRN-TBPD.M", 1e-3), ("UAE", "EIA:INTL.57-1-ARE-TBPD.M", 1e-3),
                                                                                    ("Kuwait", "EIA:INTL.57-1-KWT-TBPD.M", 1e-3), ("Venezuela", "EIA:INTL.57-1-VEN-TBPD.M", 1e-3)]),
    "world_oil_balance": ("World oil supply and demand (EIA, incl. forecast)", " mn b/d", None, [("Supply", "EIA:STEO.PAPR_WORLD.M", 1), ("Demand", "EIA:STEO.PATC_WORLD.M", 1)]),
    "oecd_oil_stocks": ("OECD commercial oil inventories (EIA, incl. forecast)", " mn bbl", None, [("OECD stocks", "EIA:STEO.PASC_OECD_T3.M", 1)]),
    "oil_price_forecast": ("Oil price forecast (EIA STEO)", " $/bbl", None, [("Brent", "EIA:STEO.BREPUUS.M", 1), ("WTI", "EIA:STEO.WTIPUUS.M", 1)]),
    # energy: gas and power
    "gas_europe_us": ("Natural gas: Europe vs U.S.", " $/MMBtu", None, [("Europe (TTF)", "CALC:TTF_USD", 1), ("U.S. (Henry Hub futures)", "YF:NG=F", 1)]),
    "natgas_storage_seasonal": ("U.S. natural gas storage vs 5-year range", " Bcf", "seasonal", [("Working gas", "EIA:NG.NW2_EPG0_SWO_R48_BCF.W", 1)]),
    "gas_supply": ("U.S. natural gas production (monthly)", " Bcf", None, [("Dry gas production", "EIA:NG.N9070US2.M", 1e-3)]),
    "lng_exports": ("U.S. LNG exports (monthly)", " Bcf", None, [("LNG exports", "EIA:NG.N9133US2.M", 1e-3)]),
    "gas_price_forecast": ("Henry Hub price forecast (EIA STEO)", " $/MMBtu", None, [("Henry Hub", "EIA:STEO.NGHHUUS.M", 1)]),
    "electricity_prices": ("Average U.S. electricity price", " $/kWh", None, [("Electricity per kWh", "APU000072610", 1)]),
    "power_output": ("Electric power generation (industrial production, 2017 = 100)", "", None, [("Electric power", "IPG2211S", 1)]),
    "uranium": ("Uranium: physical trust and miners (indexed)", "", "index", [("Sprott Physical Uranium Trust", "YF:U-UN.TO", 1), ("Uranium miners (URA)", "YF:URA", 1)]),
    "oil_volatility": ("Oil and equity volatility", "", None, [("Oil (OVX)", "YF:^OVX", 1), ("Equities (VIX)", "YF:^VIX", 1)]),
    # geopolitics and policy risk
    "gpr_daily": ("Geopolitical Risk Index (daily, 30-day average)", "", None, [("Overall", "GPR:D_GPRD_MA30", 1), ("Threats", "GPR:D_GPRD_THREAT_MA30", 1), ("Acts", "GPR:D_GPRD_ACT_MA30", 1)]),
    "gpr_countries": ("Geopolitical risk by country (monthly, share of news)", "", None, [("Russia", "GPR:M_GPRC_RUS", 1), ("Ukraine", "GPR:M_GPRC_UKR", 1), ("Israel", "GPR:M_GPRC_ISR", 1),
                                                                                          ("China", "GPR:M_GPRC_CHN", 1), ("Taiwan", "GPR:M_GPRC_TWN", 1), ("Saudi Arabia", "GPR:M_GPRC_SAU", 1)]),
    "policy_uncertainty": ("Economic policy uncertainty", "", None, [("U.S. (30-day average)", "USEPUINDXD@ma30", 1), ("Global (monthly)", "GEPUCURRENT", 1)]),
    "trade_uncertainty": ("U.S. trade policy uncertainty", "", None, [("Trade policy uncertainty", "EPUTRADE", 1)]),
    "chokepoint_tankers": ("Oil tanker transits through key chokepoints (7-day average)", " ships/day", None,
                           [("Strait of Hormuz", "PW:Strait of Hormuz:vessels_tanker", 1), ("Bab el-Mandeb (Red Sea)", "PW:Bab el-Mandeb Strait:vessels_tanker", 1),
                            ("Suez Canal", "PW:Suez Canal:vessels_tanker", 1), ("Cape of Good Hope", "PW:Cape of Good Hope:vessels_tanker", 1), ("Bosporus (Black Sea)", "PW:Bosporus Strait:vessels_tanker", 1)]),
    "red_sea_diversion": ("Red Sea diversion: all ships, Suez vs the Cape (7-day average)", " ships/day", None,
                          [("Suez Canal", "PW:Suez Canal:vessels_total", 1), ("Bab el-Mandeb", "PW:Bab el-Mandeb Strait:vessels_total", 1),
                           ("Cape of Good Hope", "PW:Cape of Good Hope:vessels_total", 1), ("Panama Canal", "PW:Panama Canal:vessels_total", 1)]),
    "asia_straits": ("Asian shipping lanes (7-day average)", " ships/day", None, [("Taiwan Strait", "PW:Taiwan Strait:vessels_total", 1), ("Malacca Strait", "PW:Malacca Strait:vessels_total", 1),
                                                                                  ("Korea Strait", "PW:Korea Strait:vessels_total", 1)]),
    "defense_spending": ("U.S. national defense spending (annual rate)", " $bn", None, [("Defense consumption and investment", "FDEFX", 1)]),
    # credit by rating and region (ICE BofA option-adjusted spreads; FRED keeps about three years of these)
    "ig_by_rating": ("U.S. investment-grade spreads by rating (OAS)", " bp", None, [("AAA", "BAMLC0A1CAAA", 100), ("AA", "BAMLC0A2CAA", 100), ("A", "BAMLC0A3CA", 100), ("BBB", "BAMLC0A4CBBB", 100)]),
    "hy_by_rating": ("U.S. high-yield spreads by rating (OAS)", " bp", None, [("BB", "BAMLH0A1HYBB", 100), ("B", "BAMLH0A2HYB", 100), ("CCC and lower", "BAMLH0A3HYC", 100)]),
    "credit_regions": ("Corporate spreads by region (OAS)", " bp", None, [("U.S. investment grade", "BAMLC0A0CM", 100), ("U.S. high yield", "BAMLH0A0HYM2", 100), ("Euro high yield", "BAMLHE00EHYIOAS", 100),
                                                                     ("EM corporate", "BAMLEMCBPIOAS", 100), ("EM Asia", "BAMLEMRACRPIASIAOAS", 100), ("EM Latin America", "BAMLEMRLCRPILAOAS", 100),
                                                                     ("EM Europe, Middle East, Africa", "BAMLEMRECRPIEMEAOAS", 100)]),
    "euro_spreads": ("Euro-area sovereign spreads over Germany, 10-year (monthly)", " bp", None, [("Italy (BTP-Bund)", "DIFF:IRLTLT01ITM156N-IRLTLT01DEM156N", 100),
                                                                                                  ("France (OAT-Bund)", "DIFF:IRLTLT01FRM156N-IRLTLT01DEM156N", 100),
                                                                                                  ("Spain (Bonos-Bund)", "DIFF:IRLTLT01ESM156N-IRLTLT01DEM156N", 100)]),
    "euro_10y": ("European 10-year government yields (monthly)", "%", None, [("Germany", "IRLTLT01DEM156N", 1), ("France", "IRLTLT01FRM156N", 1), ("Italy", "IRLTLT01ITM156N", 1),
                                                                          ("Spain", "IRLTLT01ESM156N", 1), ("United Kingdom", "IRLTLT01GBM156N", 1)]),
    # central banks and currencies
    "cb_balance_sheets": ("Central-bank balance sheets: Fed, ECB, BoJ (indexed)", "", "index", [("Federal Reserve", "WALCL", 1), ("European Central Bank", "ECBASSETSW", 1), ("Bank of Japan", "JPNASSETS", 1)]),
    "reer": ("Real effective exchange rates (BIS, broad, 2020 = 100)", "", None, [("U.S. dollar", "RBUSBIS", 1), ("Euro", "RBXMBIS", 1), ("Yen", "RBJPBIS", 1), ("Sterling", "RBGBBIS", 1),
                                                                                ("Renminbi", "RBCNBIS", 1), ("Swiss franc", "RBCHBIS", 1)]),
    "fx_reserves": ("Foreign-exchange reserves excluding gold", " $bn", None, [("China", "TRESEGCNM052N", 1e-3), ("Japan", "TRESEGJPM052N", 1e-3), ("India", "TRESEGINM052N", 1e-3)]),
})

MACRO_CATEGORY = {}

for _cat, _keys in (
        ("Credit", "credit_spreads ig_by_rating hy_by_rating credit_regions distressed_spreads corporate_yields corporate_debt bank_lending delinquencies lending_standards financial_conditions"),
        ("Rates & liquidity", "treasury_yields curve_spreads real_yields breakevens policy_corridor fed_balance_sheet cb_balance_sheets fed_liquidity money_supply euro_10y euro_spreads"),
        ("Public finances, trade & FX", "federal_debt federal_deficit federal_interest_tariffs trade_balance dollar reer fx_reserves defense_spending trade_uncertainty"),
        ("Growth, jobs & prices", "gdpnow inflation jobless_claims continuing_claims unemployment job_openings industry tech_investment construction corporate_profits"),
        ("Consumers & housing", "consumer_health consumer_sentiment retail_sales mortgage_rates housing_activity home_prices cre_prices"),
        ("Energy: oil", "oil_prices brent_wti crack_spreads crude_stocks crude_stocks_seasonal cushing_seasonal gasoline_stocks_seasonal distillate_stocks_seasonal refinery_utilization us_oil_flows oil_demand pump_prices oil_producers world_oil_balance oecd_oil_stocks oil_price_forecast oil_volatility"),
        ("Energy: gas & power", "natgas_price gas_europe_us natgas_storage_seasonal gas_supply lng_exports gas_price_forecast electricity_prices power_output uranium"),
        ("Geopolitics & shipping", "gpr_daily gpr_countries policy_uncertainty chokepoint_tankers red_sea_diversion asia_straits")):
    for _k in _keys.split():
        MACRO_CATEGORY[_k] = _cat

MACRO_OPTIONS[:] = sorted(({"label": f"{MACRO_CATEGORY.get(k, 'Other')} · {v[0]}", "value": k} for k, v in MACRO_SETS.items()), key=lambda o: o["label"])

SOURCE_NAMES = (("EIA:", "U.S. EIA"), ("YF:", "Yahoo Finance"), ("CALC:", "Yahoo Finance futures"), ("GPR:", "Caldara & Iacoviello GPR"), ("PW:", "IMF PortWatch"))

CALC_TEXT = {"CRACK321": "3-2-1 crack: (2 × RBOB gasoline + 1 × heating oil) × 42 / 3 − WTI, front-month futures",
             "DIESEL_CRACK": "heating oil × 42 − Brent, front-month futures", "GASOLINE_CRACK": "RBOB gasoline × 42 − Brent, front-month futures",
             # measured 2026-05..11: on average $3.01 below FRED's spot spread (DCOILBRENTEU − DCOILWTICO): a different measure
             "BRENT_WTI": "Brent − WTI, front-month futures (their delivery months can differ, so this is not the spot spread: FRED DCOILBRENTEU − DCOILWTICO)",
             "TTF_USD": "Dutch TTF (EUR/MWh) × EUR/USD ÷ 3.412, in $/MMBtu"}
