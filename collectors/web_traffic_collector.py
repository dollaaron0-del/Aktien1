"""
WebTrafficCollector – analysiert Web-Traffic als Umsatzindikator.
Steigender Traffic bei E-Commerce → steigende Umsätze (Frühindikator).
Quellen: SimilarWeb (öffentliche Daten), Semrush RSS/Blog.
Hinweis: Echte SimilarWeb-API kostet $300+/Monat, wir nutzen
         öffentlich zugängliche Daten und alternative Proxys.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
import yfinance as yf

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

# Ticker → Primäre Domain
_TICKER_DOMAINS = {
    "AMZN": "amazon.com", "GOOGL": "google.com", "META": "facebook.com",
    "AAPL": "apple.com",  "MSFT": "microsoft.com", "NFLX": "netflix.com",
    "SHOP": "shopify.com","EBAY": "ebay.com", "ETSY": "etsy.com",
    "SPOT": "spotify.com","SNAP": "snapchat.com", "PINS": "pinterest.com",
    "TWTR": "twitter.com","UBER": "uber.com", "LYFT": "lyft.com",
    "ABNB": "airbnb.com", "DASH": "doordash.com", "COIN": "coinbase.com",
    "HOOD": "robinhood.com",
}


class WebTrafficCollector:
    """Analysiert Web-Traffic als Frühindikator für Umsatzentwicklung."""

    def collect(self, ticker: str, lookback_days: int = 30) -> List[Dict]:
        results = []
        results += self._collect_from_rss_proxies(ticker, lookback_days)
        results += self._estimate_from_financials(ticker)
        return results

    def _collect_from_rss_proxies(self, ticker: str, lookback_days: int) -> List[Dict]:
        """Sammelt Traffic-relevante News von Tech-Blogs und Analystenseiten."""
        results = []
        domain   = _TICKER_DOMAINS.get(ticker.upper())
        cutoff   = datetime.utcnow() - timedelta(days=lookback_days)

        if not domain:
            return []

        # SimilarWeb-Erwähnungen in Tech-News
        search_terms = [ticker, domain.split(".")[0]]
        rss_urls = [
            f"https://news.google.com/rss/search?q={ticker}+web+traffic+users&hl=en-US&gl=US&ceid=US:en",
            f"https://news.google.com/rss/search?q={domain}+traffic+monthly+users&hl=en-US&gl=US&ceid=US:en",
        ]

        for url in rss_urls:
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=10)
                if resp.status_code != 200:
                    continue

                try:
                    root = ET.fromstring(resp.content)
                except ET.ParseError:
                    continue

                for item in root.iter("item"):
                    title   = (item.findtext("title") or "").strip()
                    link    = (item.findtext("link") or "").strip()
                    summary = (item.findtext("description") or "").strip()
                    pub     = item.findtext("pubDate") or ""

                    combined_lower = (title + summary).lower()
                    traffic_keywords = ["traffic", "visitors", "users", "downloads", "installs", "monthly active"]
                    if not any(kw in combined_lower for kw in traffic_keywords):
                        continue

                    try:
                        from email.utils import parsedate_to_datetime
                        pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pub_dt = datetime.utcnow()

                    results.append({
                        "source":       "Web Traffic Analyse",
                        "ticker":       ticker,
                        "title":        f"Traffic-Daten: {title[:80]}",
                        "text":         (
                            f"Web-Traffic-Bericht zu {ticker} ({domain}): {title}. "
                            f"{summary[:400] if summary else ''}"
                        ),
                        "url":          link,
                        "published_at": pub_dt.isoformat(),
                        "priority":     "MEDIUM",
                    })

                if results:
                    break  # Erste erfolgreiche Quelle reicht

            except Exception:
                continue

        return results[:3]  # Max 3 Traffic-Artikel

    def _estimate_from_financials(self, ticker: str) -> List[Dict]:
        """Berechnet Revenue-per-User als Proxy für Traffic-Qualität."""
        domain = _TICKER_DOMAINS.get(ticker.upper())
        if not domain:
            return []

        try:
            stock = yf.Ticker(ticker)
            info  = stock.info or {}

            revenue    = info.get("totalRevenue", 0) or 0
            users      = (info.get("impliedSharesOutstanding", 0) or 0)  # Proxy
            market_cap = info.get("marketCap", 0) or 0

            # Revenue-Wachstum als Traffic-Proxy
            rev_growth = info.get("revenueGrowth", None)
            if rev_growth is None:
                return []

            rev_growth_pct = rev_growth * 100

            if rev_growth_pct > 20:
                signal = "STARK WACHSEND"
                priority = "HIGH"
            elif rev_growth_pct > 5:
                signal = "MODERAT WACHSEND"
                priority = "MEDIUM"
            elif rev_growth_pct < -10:
                signal = "SCHRUMPFEND"
                priority = "HIGH"
            else:
                signal = "STAGNIEREND"
                priority = "LOW"

            return [{
                "source":       "Web/User Metrics (yfinance)",
                "ticker":       ticker,
                "title":        f"Nutzerwachstum {ticker}: {rev_growth_pct:+.1f}% ({signal})",
                "text":         (
                    f"{ticker} ({domain}): Umsatzwachstum {rev_growth_pct:+.1f}% YoY als "
                    f"Proxy für Nutzerwachstum. Signal: {signal}. "
                    f"Revenue: ${revenue/1e9:.1f}B, MarketCap: ${market_cap/1e9:.1f}B."
                ),
                "url":          f"https://finance.yahoo.com/quote/{ticker}/",
                "published_at": datetime.utcnow().isoformat(),
                "priority":     priority,
            }]
        except Exception:
            return []
