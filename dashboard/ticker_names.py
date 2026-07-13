"""Ticker → Firmenname (Roadmap 4.4a, aus dashboard/app.py ausgelagert)."""
from analyzers.eu_stock_scanner import EU_UNIVERSE

US_NAMES = {
    # Mega-Cap Tech
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
    "AMZN": "Amazon", "GOOGL": "Alphabet", "GOOG": "Alphabet", "META": "Meta",
    "AVGO": "Broadcom", "ORCL": "Oracle", "ADBE": "Adobe", "CRM": "Salesforce",
    "AMD": "AMD", "INTC": "Intel", "QCOM": "Qualcomm", "CSCO": "Cisco",
    "TXN": "Texas Instruments", "INTU": "Intuit", "NOW": "ServiceNow",
    "PANW": "Palo Alto Networks", "SNOW": "Snowflake", "PLTR": "Palantir",
    "TSM": "TSMC", "ASML": "ASML", "AMAT": "Applied Materials",
    "LRCX": "Lam Research", "MU": "Micron", "MRVL": "Marvell", "ARM": "ARM Holdings",
    # Finanzen
    "JPM": "JPMorgan", "BAC": "Bank of America", "GS": "Goldman Sachs",
    "MS": "Morgan Stanley", "WFC": "Wells Fargo", "BLK": "BlackRock",
    "V": "Visa", "MA": "Mastercard", "AXP": "American Express", "PYPL": "PayPal",
    "COIN": "Coinbase",
    # Healthcare
    "LLY": "Eli Lilly", "NVO": "Novo Nordisk", "UNH": "UnitedHealth",
    "JNJ": "J&J", "MRK": "Merck", "PFE": "Pfizer", "ABBV": "AbbVie",
    "TMO": "Thermo Fisher", "ABT": "Abbott",
    # Energie / Industrie
    "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips",
    "CAT": "Caterpillar", "BA": "Boeing", "GE": "GE Aerospace",
    "RTX": "Raytheon", "HON": "Honeywell",
    # Konsum / Retail
    "WMT": "Walmart", "COST": "Costco", "HD": "Home Depot",
    "MCD": "McDonald's", "NKE": "Nike", "SBUX": "Starbucks",
    "DIS": "Disney", "NFLX": "Netflix",
    # Wachstum
    "SHOP": "Shopify", "UBER": "Uber", "ABNB": "Airbnb",
    "RIVN": "Rivian", "LCID": "Lucid", "SOFI": "SoFi", "HOOD": "Robinhood",
    # ETFs / Inverse
    "SPY": "S&P 500 ETF", "QQQ": "Nasdaq ETF", "VTI": "Total Market ETF",
    "SH": "S&P 500 Inv.", "PSQ": "Nasdaq Inv.", "SQQQ": "Nasdaq 3× Inv.",
    # Krypto
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "SOL-USD": "Solana",
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana",
}
EU_NAMES = {ticker: name for ticker, (name, *_) in EU_UNIVERSE.items()}
# Bekannte EU-Ticker die nicht im EU_UNIVERSE sind
EU_NAMES.update({
    "RHM.DE": "Rheinmetall", "DB1.DE": "Deutsche Börse", "MTX.DE": "MTU Aero",
    "SHL.DE": "Siemens Healthineers", "ZAL.DE": "Zalando", "ENR.DE": "Siemens Energy",
    "DHL.DE": "DHL Group", "HFG.DE": "HelloFresh", "WAF.DE": "Siltronic",
    "DHER.DE": "Delivery Hero", "O2D.DE": "Telefónica DE",
    "NDA-SE.ST": "Nordea", "ERIC-B.ST": "Ericsson",
    "NOVO-B.CO": "Novo Nordisk B", "ORSTED.CO": "Ørsted",
})
ALL_NAMES = {**US_NAMES, **EU_NAMES}


def ticker_label(ticker: str) -> str:
    name = ALL_NAMES.get(ticker.upper())
    return f"{ticker} ({name})" if name else ticker
