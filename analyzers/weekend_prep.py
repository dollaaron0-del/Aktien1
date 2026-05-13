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

# Major indices
INDICES: Dict[str, str] = {
    "^GSPC":  "S&P 500",
    "^IXIC":  "NASDAQ",
    "^DJI":   "Dow Jones",
    "^GDAXI": "DAX",
    "^N225":  "Nikkei 225",
    "^HSI":   "Hang Seng",
    "^VIX":   "VIX (Angst-Index)",
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
          watchlist_earnings, broader_earnings (major movers)
        """
        next_monday, next_friday = self._next_week_range()
        watchlist_earnings = []
        broader_earnings = []

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

        watchlist_earnings.sort(key=lambda x: x["date"])
        broader_earnings.sort(key=lambda x: x["date"])
        return {
            "week": f"{next_monday} bis {next_friday}",
            "watchlist_earnings": watchlist_earnings,
            "broader_earnings": broader_earnings[:15],
        }

    def collect_market_sentiment(self) -> Dict:
        """VIX level + weekly sector + index performance."""
        results = {"indices": {}, "sectors": {}, "vix": None, "vix_signal": ""}

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

        # Sector ETFs – weekly performance
        for sym, name in SECTOR_ETFS.items():
            try:
                hist = yf.Ticker(sym).history(period="5d")
                if len(hist) >= 2:
                    chg = (hist["Close"].iloc[-1] - hist["Close"].iloc[0]) / hist["Close"].iloc[0] * 100
                    results["sectors"][name] = round(float(chg), 2)
            except Exception:
                pass

        # Sort sectors by performance
        results["sectors"] = dict(
            sorted(results["sectors"].items(), key=lambda x: x[1], reverse=True)
        )
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

        earnings = self.collect_earnings_calendar()
        sentiment = self.collect_market_sentiment()
        macro_news = self.collect_macro_news(newsapi_key)
        raw_data = {
            "earnings": earnings,
            "sentiment": sentiment,
            "macro_news": macro_news,
        }

        prompt = self._build_prompt(earnings, sentiment, macro_news)

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            response = client.messages.create(
                model="claude-opus-4-7",
                max_tokens=1500,
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

        # Format sectors (top 5 + bottom 5)
        sectors = sentiment.get("sectors", {})
        s_items = list(sectors.items())
        top5 = s_items[:5]
        bot5 = s_items[-5:]

        # Format earnings
        wl_earn = earnings.get("watchlist_earnings", [])
        brd_earn = earnings.get("broader_earnings", [])
        earn_lines = []
        if wl_earn:
            earn_lines.append("  WATCHLIST (direkt betroffen):")
            for e in wl_earn:
                earn_lines.append(f"    ⚠ {e['ticker']} am {e['weekday']} ({e['date']})")
        if brd_earn:
            earn_lines.append("  Markt-Schwergewichte:")
            for e in brd_earn[:8]:
                earn_lines.append(f"    {e['ticker']} am {e['weekday']}")

        # Format news
        news_lines = [f"  - {n['title']}" for n in macro_news[:8]]

        vix = sentiment.get("vix", "?")
        vix_signal = sentiment.get("vix_signal", "")

        return f"""Du bist ein erfahrener Swing-Trader und bereitest das Portfolio auf die Handelswoche {earnings['week']} vor.

## Marktdaten der vergangenen Woche

### Wichtige Indizes (Wochenperformance):
{chr(10).join(idx_lines)}

### VIX: {vix} → {vix_signal}

### Sektoren (beste):
{chr(10).join(f'  {n}: +{v:.1f}%' if v >= 0 else f'  {n}: {v:.1f}%' for n, v in top5)}

### Sektoren (schlechteste):
{chr(10).join(f'  {n}: +{v:.1f}%' if v >= 0 else f'  {n}: {v:.1f}%' for n, v in bot5)}

## Quartalszahlen nächste Woche
{chr(10).join(earn_lines) if earn_lines else '  Keine bekannten Earnings für Watchlist/Schwergewichte.'}

## Makro-Nachrichten (letzte 3 Tage)
{chr(10).join(news_lines)}

---

Erstelle ein strukturiertes **Wochenvorbereitungs-Briefing** mit genau diesen 5 Abschnitten:

**1. MARKTLAGE**
Wie ist die allgemeine Stimmung? Risk-on oder Risk-off? Was sagt der VIX?

**2. SEKTOREN DER WOCHE**
Welche Sektoren bieten die besten Chancen? Welche meiden?

**3. EARNINGS-RISIKEN**
Ticker mit Quartalszahlen = erhöhtes Risiko (gute & schlechte Überraschungen möglich).
Klare Empfehlung: kaufen/halten/meiden für Earnings-Woche.

**4. MAKRO-RISIKEN**
Was sind die 2–3 wichtigsten Makro-Themen die den Markt nächste Woche bewegen könnten?

**5. HANDLUNGSEMPFEHLUNG**
Konkrete Positionierungs-Empfehlung für die Woche:
- Aggressiver / defensiver als normal?
- Stop-Losses enger/weiter?
- Sektoren übergewichten/untergewichten?

Schreib kompakt (max. 400 Wörter), direkt, auf Deutsch.
Vermeide Floskeln. Fokus auf actionable Insights für einen Swing-Trader."""
