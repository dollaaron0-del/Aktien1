"""
Weekend preparation module.

Every Saturday and Sunday the bot:
  1. Fetches next week's earnings calendar (watchlist + broader market)
  2. Collects VIX, sector ETF performance, major index returns
  3. Pulls macro/economic news (Fed, inflation, GDP, geopolitics)
  4. Asks Claude to synthesize a structured weekly briefing
  5. Stores the briefing so Monday's analysis has full context

The briefing is injected into the system prompt of every analysis
that runs during the following week, similar to the lessons memo.
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Tuple

import yfinance as yf
import requests

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "weekly_briefing.db")

# Sector ETFs → readable name
SECTOR_ETFS: Dict[str, str] = {
    "XLK": "Technologie",
    "XLF": "Finanzen",
    "XLE": "Energie",
    "XLV": "Gesundheit",
    "XLI": "Industrie",
    "XLY": "Konsum zyklisch",
    "XLP": "Konsum defensiv",
    "XLU": "Versorger",
    "XLRE": "Immobilien",
    "XLB": "Rohstoffe",
    "XLC": "Kommunikation",
}

# Major indices (US + EU + Asia)
INDICES: Dict[str, str] = {
    "^GSPC":     "S&P 500",
    "^IXIC":     "NASDAQ",
    "^DJI":      "Dow Jones",
    "^GDAXI":    "DAX",
    "^STOXX50E": "Euro Stoxx 50",
    "^FCHI":     "CAC 40",
    "^FTSE":     "FTSE 100",
    "^N225":     "Nikkei 225",
    "^HSI":      "Hang Seng",
    "^VIX":      "VIX (Angst-Index)",
}

# EU Sector ETFs (STOXX-basiert, ergänzend zu US-Sektoren)
EU_SECTOR_ETFS: Dict[str, str] = {
    "EXV3.DE": "EU Technologie",
    "EXH1.DE": "EU Gesundheit",
    "EXV1.DE": "EU Banken",
    "EXH4.DE": "EU Industrie",
    "EXV6.DE": "EU Energie",
    "EXH7.DE": "EU Konsum",
}

# Crypto weekend tickers
CRYPTO_WEEKEND: Dict[str, str] = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
}

# High-profile tickers that commonly move markets even outside user watchlist
MARKET_MOVERS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "JPM", "GS", "BAC", "WFC", "V", "MA",
    "LLY", "UNH", "JNJ", "PFE",
    "XOM", "CVX",
]


class WeekendPrep:
    def __init__(self, anthropic_api_key: str, watchlist: List[str]):
        self.api_key = anthropic_api_key
        self.watchlist = watchlist
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS weekly_briefings (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start   TEXT NOT NULL,   -- ISO date of the following Monday
                generated_at TEXT NOT NULL,
                briefing     TEXT NOT NULL,   -- Full Claude-generated briefing
                raw_data     TEXT             -- JSON dump of collected data
            );
            CREATE INDEX IF NOT EXISTS idx_brief_week ON weekly_briefings(week_start);
        """)
        self._conn.commit()

    # ── Data collection ───────────────────────────────────────────────────────

    def collect_earnings_calendar(self) -> Dict:
        """
        Returns earnings for next week split into:
          watchlist_earnings, broader_earnings (major movers), eu_earnings
        """
        next_monday, next_friday = self._next_week_range()
        watchlist_earnings = []
        broader_earnings = []
        eu_earnings = []

        all_tickers = list(dict.fromkeys(self.watchlist + MARKET_MOVERS))
        for ticker in all_tickers:
            info = self._get_earnings_date(ticker)
            if not info:
                continue
            ed = info["date"]
            if next_monday <= ed <= next_friday:
                entry = {
                    "ticker": ticker,
                    "date": ed.isoformat(),
                    "weekday": ed.strftime("%A"),
                    "in_watchlist": ticker in self.watchlist,
                    "estimate": info.get("estimate"),
                }
                if ticker in self.watchlist:
                    watchlist_earnings.append(entry)
                else:
                    broader_earnings.append(entry)

        # EU Earnings aus EU_UNIVERSE
        try:
            from analyzers.eu_stock_scanner import EU_UNIVERSE
            eu_tickers_in_watchlist = {t for t in self.watchlist if "." in t}
            eu_check = list(EU_UNIVERSE.keys())
            for ticker in eu_check:
                info = self._get_earnings_date(ticker)
                if not info:
                    continue
                ed = info["date"]
                if next_monday <= ed <= next_friday:
                    name, country, sector = EU_UNIVERSE[ticker]
                    eu_earnings.append({
                        "ticker": ticker,
                        "name": name,
                        "country": country,
                        "date": ed.isoformat(),
                        "weekday": ed.strftime("%A"),
                        "in_watchlist": ticker in eu_tickers_in_watchlist,
                    })
            eu_earnings.sort(key=lambda x: x["date"])
        except Exception:
            pass

        watchlist_earnings.sort(key=lambda x: x["date"])
        broader_earnings.sort(key=lambda x: x["date"])
        return {
            "week": f"{next_monday} bis {next_friday}",
            "watchlist_earnings": watchlist_earnings,
            "broader_earnings": broader_earnings[:15],
            "eu_earnings": eu_earnings[:15],
        }

    def collect_market_sentiment(self) -> Dict:
        """VIX level + weekly sector + index performance (US + EU + Crypto)."""
        results = {
            "indices": {}, "sectors": {}, "eu_sectors": {},
            "crypto": {}, "vix": None, "vix_signal": "",
            "ecb_next_week": None, "boe_next_week": None,
        }

        # Indices – last 5 days return
        for sym, name in INDICES.items():
            try:
                hist = yf.Ticker(sym).history(period="5d")
                if len(hist) >= 2:
                    chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0] * 100
                    val = float(hist["Close"].iloc[-1])
                    results["indices"][name] = {
                        "value": round(val, 2),
                        "week_change_pct": round(float(chg), 2),
                    }
                    if sym == "^VIX":
                        results["vix"] = round(val, 2)
                        results["vix_signal"] = self._vix_signal(val)
            except Exception:
                pass

        # US Sector ETFs – weekly performance
        for sym, name in SECTOR_ETFS.items():
            try:
                hist = yf.Ticker(sym).history(period="5d")
                if len(hist) >= 2:
                    chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0] * 100
                    results["sectors"][name] = round(float(chg), 2)
            except Exception:
                pass
        results["sectors"] = dict(
            sorted(results["sectors"].items(), key=lambda x: x[1], reverse=True)
        )

        # EU Sector ETFs
        for sym, name in EU_SECTOR_ETFS.items():
            try:
                hist = yf.Ticker(sym).history(period="5d")
                if len(hist) >= 2:
                    chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0] * 100
                    results["eu_sectors"][name] = round(float(chg), 2)
            except Exception:
                pass
        results["eu_sectors"] = dict(
            sorted(results["eu_sectors"].items(), key=lambda x: x[1], reverse=True)
        )

        # Crypto Wochenend-Performance (7d)
        for sym, name in CRYPTO_WEEKEND.items():
            try:
                hist = yf.Ticker(sym).history(period="7d")
                if len(hist) >= 2:
                    chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0] * 100
                    val = float(hist["Close"].iloc[-1])
                    results["crypto"][name] = {
                        "price": round(val, 2),
                        "week_change_pct": round(float(chg), 2),
                    }
            except Exception:
                pass

        # EZB / BoE nächste Woche?
        try:
            from analyzers.eu_market_context import _ECB_DATES, _BOE_DATES
            next_monday, next_friday = self._next_week_range()
            ecb_next = [d for d in _ECB_DATES if next_monday <= d <= next_friday]
            boe_next = [d for d in _BOE_DATES if next_monday <= d <= next_friday]
            results["ecb_next_week"] = ecb_next[0].isoformat() if ecb_next else None
            results["boe_next_week"] = boe_next[0].isoformat() if boe_next else None
        except Exception:
            pass

        return results

    def collect_macro_news(self, newsapi_key: str = "") -> List[Dict]:
        """Fetches macro/economic news via NewsAPI or Google News RSS fallback."""
        items = []

        queries = ["Federal Reserve interest rates", "inflation GDP economy", "market outlook next week"]
        if newsapi_key:
            for q in queries:
                try:
                    r = requests.get(
                        "https://newsapi.org/v2/everything",
                        params={
                            "q": q,
                            "language": "en",
                            "sortBy": "relevancy",
                            "pageSize": 5,
                            "from": (datetime.utcnow() - timedelta(days=3)).date().isoformat(),
                        },
                        headers={"X-Api-Key": newsapi_key},
                        timeout=10,
                    )
                    if r.status_code == 200:
                        for art in r.json().get("articles", []):
                            items.append({
                                "title": art.get("title", ""),
                                "summary": (art.get("description") or "")[:200],
                                "source": art.get("source", {}).get("name", ""),
                                "url": art.get("url", ""),
                            })
                except Exception:
                    pass

        # RSS fallback
        if not items:
            items = self._fetch_macro_rss()

        # Deduplicate
        seen, unique = set(), []
        for item in items:
            k = item["title"][:60]
            if k and k not in seen:
                seen.add(k)
                unique.append(item)
        return unique[:15]

    def _fetch_macro_rss(self) -> List[Dict]:
        import xml.etree.ElementTree as ET
        import urllib.parse as up
        items = []
        queries = ["market outlook week", "Fed interest rates", "earnings season"]
        for q in queries:
            url = f"https://news.google.com/rss/search?q={up.quote(q)}&hl=en&gl=US&ceid=US:en"
            try:
                r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
                root = ET.fromstring(r.text)
                channel = root.find("channel")
                if channel is None:
                    continue
                for item in channel.findall("item")[:4]:
                    title = (item.findtext("title") or "").strip()
                    desc = (item.findtext("description") or "").strip()[:200]
                    items.append({"title": title, "summary": desc, "source": "Google News", "url": ""})
            except Exception:
                pass
        return items

    # ── Claude briefing ───────────────────────────────────────────────────────

    def generate_briefing(self, newsapi_key: str = "") -> Optional[str]:
        """Collects all data and generates a Claude weekly briefing."""
        if not self.api_key:
            return None

        earnings  = self.collect_earnings_calendar()
        sentiment = self.collect_market_sentiment()
        macro_news = self.collect_macro_news(newsapi_key)
        raw_data = {
            "earnings":   earnings,
            "sentiment":  sentiment,
            "macro_news": macro_news,
        }

        prompt = self._build_prompt(earnings, sentiment, macro_news)

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=3500,
                messages=[{"role": "user", "content": prompt}],
            )
            briefing = response.content[0].text.strip()
        except Exception as e:
            return None

        # Persist
        next_monday = self._next_week_range()[0].isoformat()
        self._conn.execute(
            """INSERT INTO weekly_briefings (week_start, generated_at, briefing, raw_data)
               VALUES (?, ?, ?, ?)""",
            (next_monday, datetime.utcnow().isoformat(), briefing, json.dumps(raw_data)),
        )
        self._conn.commit()
        return briefing

    def get_current_briefing(self) -> Optional[str]:
        """Returns this week's briefing if one exists (used to inject into daily analysis)."""
        today = date.today()
        # Monday of the current week
        monday = (today - timedelta(days=today.weekday())).isoformat()
        row = self._conn.execute(
            "SELECT briefing FROM weekly_briefings WHERE week_start=? ORDER BY generated_at DESC LIMIT 1",
            (monday,),
        ).fetchone()
        return row["briefing"] if row else None

    def get_latest_briefing(self, limit: int = 3) -> List[Dict]:
        cursor = self._conn.execute(
            "SELECT week_start, generated_at, briefing FROM weekly_briefings ORDER BY generated_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in cursor.fetchall()]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _next_week_range(self) -> Tuple[date, date]:
        today = date.today()
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        monday = today + timedelta(days=days_until_monday)
        friday = monday + timedelta(days=4)
        return monday, friday

    def _get_earnings_date(self, ticker: str) -> Optional[Dict]:
        try:
            stock = yf.Ticker(ticker)
            cal = stock.calendar
            if cal is None:
                return None
            ed = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if isinstance(ed, list) and ed:
                    ed = ed[0]
            else:
                try:
                    ed = cal.loc["Earnings Date"].iloc[0]
                except Exception:
                    return None
            if ed is None:
                return None
            if hasattr(ed, "to_pydatetime"):
                ed = ed.to_pydatetime().date()
            elif hasattr(ed, "date"):
                ed = ed.date()
            elif isinstance(ed, str):
                ed = date.fromisoformat(ed[:10])
            if not isinstance(ed, date):
                return None
            info = stock.info or {}
            return {"date": ed, "estimate": info.get("forwardEps")}
        except Exception:
            return None

    def _vix_signal(self, vix: float) -> str:
        if vix < 15:
            return "Sehr niedrig – Markt sorglos, Selbstzufriedenheit-Risiko"
        if vix < 20:
            return "Niedrig – Ruhige Marktlage"
        if vix < 25:
            return "Moderat – Leichte Unsicherheit"
        if vix < 30:
            return "Erhöht – Deutliche Nervosität, Vorsicht geboten"
        if vix < 40:
            return "Hoch – Angst im Markt, Crash-Risiko"
        return "Extrem – Panik, Crash-Modus"

    def _build_prompt(self, earnings: Dict, sentiment: Dict, macro_news: List[Dict]) -> str:
        # Format indices
        idx_lines = []
        for name, data in sentiment.get("indices", {}).items():
            if name == "VIX (Angst-Index)":
                continue
            chg = data["week_change_pct"]
            sign = "+" if chg >= 0 else ""
            idx_lines.append(f"  {name}: {sign}{chg:.2f}% (letzte Woche)")

        # US Sectors (top 5 + bottom 3)
        sectors = sentiment.get("sectors", {})
        s_items = list(sectors.items())
        top5 = s_items[:5]
        bot3 = s_items[-3:]

        # EU Sectors
        eu_sec_lines = []
        for name, chg in list(sentiment.get("eu_sectors", {}).items())[:6]:
            eu_sec_lines.append(f"  {name}: {chg:+.1f}%")

        # Crypto weekend
        crypto_lines = []
        for name, data in sentiment.get("crypto", {}).items():
            chg = data["week_change_pct"]
            crypto_lines.append(f"  {name}: ${data['price']:,.0f} ({chg:+.1f}% 7d)")

        # EZB / BoE next week
        central_bank_lines = []
        if sentiment.get("ecb_next_week"):
            central_bank_lines.append(f"  ⚠️  EZB Zinsentscheid: {sentiment['ecb_next_week']} – erhöhte Volatilität EU-Aktien")
        if sentiment.get("boe_next_week"):
            central_bank_lines.append(f"  ⚠️  BoE Zinsentscheid: {sentiment['boe_next_week']} – erhöhte Volatilität GBP-Werte")

        # US Earnings
        wl_earn  = earnings.get("watchlist_earnings", [])
        brd_earn = earnings.get("broader_earnings", [])
        eu_earn  = earnings.get("eu_earnings", [])
        earn_lines = []
        if wl_earn:
            earn_lines.append("  US WATCHLIST (direkt betroffen):")
            for e in wl_earn:
                earn_lines.append(f"    ⚠ {e['ticker']} am {e['weekday']} ({e['date']})")
        if brd_earn:
            earn_lines.append("  US Markt-Schwergewichte:")
            for e in brd_earn[:8]:
                earn_lines.append(f"    {e['ticker']} am {e['weekday']}")
        if eu_earn:
            earn_lines.append("  EU Unternehmen:")
            for e in eu_earn[:6]:
                wl_flag = " ⚠ (Watchlist)" if e.get("in_watchlist") else ""
                earn_lines.append(f"    {e['ticker']} ({e.get('name','')}) am {e['weekday']}{wl_flag}")

        # Macro news
        news_lines = [f"  - {n['title']}" for n in macro_news[:8]]

        vix = sentiment.get("vix", "?")
        vix_signal = sentiment.get("vix_signal", "")

        central_bank_block = "\n".join(central_bank_lines) if central_bank_lines else "  Keine Zinsentscheide nächste Woche"
        eu_sector_block = "\n".join(eu_sec_lines) if eu_sec_lines else "  Keine EU-Sektordaten verfügbar"
        crypto_block = "\n".join(crypto_lines) if crypto_lines else "  Keine Crypto-Daten"

        return f"""Du bist ein erfahrener Swing-Trader und bereitest das Portfolio auf die Handelswoche {earnings['week']} vor.

## Marktdaten der vergangenen Woche

### Wichtige Indizes (US + EU + Asien, Wochenperformance):
{chr(10).join(idx_lines)}

### VIX: {vix} → {vix_signal}

### US Sektoren (beste → schlechteste):
{chr(10).join(f'  {n}: {v:+.1f}%' for n, v in top5)}
  ...
{chr(10).join(f'  {n}: {v:+.1f}%' for n, v in bot3)}

### EU Sektoren:
{eu_sector_block}

### Krypto (7-Tage-Performance):
{crypto_block}

## Zentralbank-Ereignisse nächste Woche
{central_bank_block}

## Quartalszahlen nächste Woche
{chr(10).join(earn_lines) if earn_lines else '  Keine bekannten Earnings für Watchlist/Schwergewichte.'}

## Makro-Nachrichten (letzte 3 Tage)
{chr(10).join(news_lines)}

---

Erstelle ein ausführliches **Wochenvorbereitungs-Briefing** mit genau diesen 6 Abschnitten.
Erkläre bei jedem Punkt WARUM und WAS ES BEDEUTET – nicht nur was passiert ist.
Wiederhole keine Information die bereits in einem anderen Abschnitt steht.
Schreib auf Deutsch, direkt und klar. Mindestens 120 Wörter pro Abschnitt.

**1. MARKTLAGE (US + EU + Krypto)**
Beschreibe die globale Stimmung: Risk-on oder Risk-off? Erkläre warum der VIX auf diesem Niveau ist und was das konkret bedeutet.
Analysiere die Divergenzen zwischen US, EU und Asien – warum performen sie unterschiedlich?
Was sagt die Krypto-Performance über die Risikobereitschaft der Märkte aus?

**2. SEKTOREN DER WOCHE**
Erkläre für jeden der Top-3 Sektoren WARUM er outperformt – ist es strukturell oder kurzfristig?
Welche US- und EU-Sektoren konkret kaufen, welche meiden und warum?
Beschreibe erkennbare Rotationen und was diese über die Markterwartungen aussagen.

**3. ZENTRALBANK-RISIKEN**
Erkläre den aktuellen Zinspolitik-Status der Fed, EZB und BoE und warum das relevant ist.
Falls EZB oder BoE nächste Woche tagen: konkrete Auswirkungen auf welche Positionen/Sektoren und warum?
Was erwartet der Markt aktuell von der Zinspolitik und wo liegen die Überraschungsrisiken?

**4. QUARTALSZAHLEN DER WOCHE**
Für JEDEN Ticker mit Earnings nächste Woche: Was erwartet der Markt (Konsensschätzung), welche Überraschungen sind möglich und warum?
Erkläre welche Sektoren durch diese Earnings bewegt werden könnten (auch indirekte Effekte).
Konkrete Empfehlung: vor Earnings kaufen / halten / meiden – mit Begründung für jeden Ticker.
Wenn keine Earnings: warum ist das gut/schlecht für diese Woche?

**5. MAKRO-RISIKEN**
Die 3 wichtigsten Makro-Themen die den Markt nächste Woche bewegen könnten.
Erkläre für jedes Thema: Was ist das Szenario, warum ist es relevant, und wie reagiert der Markt wenn es eintritt?
Unterscheide zwischen wahrscheinlichen und Tail-Risk-Szenarien.

**6. HANDLUNGSEMPFEHLUNG**
Konkrete Positionierungs-Empfehlung für die Woche mit Begründung:
- Aggressiver oder defensiver als normal – und warum?
- Stop-Losses enger oder weiter stellen – basierend auf welcher Begründung?
- Welche Sektoren konkret übergewichten/untergewichten und warum gerade jetzt?
- Krypto: bullisch oder bärisch für nächste Woche – Begründung?
- Ein konkreter Trade-Setup den du für diese Woche besonders interessant findest.

Schreib klar und direkt, auf Deutsch. Keine Floskeln. Jeder Satz muss einen Informationswert haben."""
