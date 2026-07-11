import yfinance as yf
import requests
from system.http import http_get
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict


RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
]


class YahooCollector:

    def collect(self, ticker: str, days_back: int = 7) -> List[Dict]:
        results = []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_back)

        # yfinance news
        try:
            stock = yf.Ticker(ticker)
            news = stock.news or []
            for item in news[:20]:
                published = datetime.utcfromtimestamp(item.get("providerPublishTime", 0))
                if published < cutoff:
                    continue
                results.append({
                    "source": f"Yahoo Finance / {item.get('publisher', 'unknown')}",
                    "ticker": ticker,
                    "title": item.get("title", ""),
                    "text": item.get("title", ""),
                    "url": item.get("link", ""),
                    "published_at": published.isoformat(),
                })
        except Exception:
            pass

        # Yahoo RSS feed (parsed with stdlib xml to avoid feedparser/sgmllib dep)
        try:
            feed_url = RSS_FEEDS[0].format(ticker=ticker)
            resp = http_get(feed_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(resp.content)
            ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link") or "").strip()
                desc  = (item.findtext("description") or title).strip()
                pub   = item.findtext("pubDate") or ""
                try:
                    published = parsedate_to_datetime(pub).replace(tzinfo=None)
                except Exception:
                    published = datetime.now(timezone.utc).replace(tzinfo=None)
                if published < cutoff:
                    continue
                results.append({
                    "source": "Yahoo Finance RSS",
                    "ticker": ticker,
                    "title": title,
                    "text": desc,
                    "url": link,
                    "published_at": published.isoformat(),
                })
        except Exception:
            pass

        seen = set()
        unique = []
        for item in results:
            if item["title"] not in seen:
                seen.add(item["title"])
                unique.append(item)

        return unique

    def get_price_data(self, ticker: str, period: str = "1mo") -> Dict:
        stock = yf.Ticker(ticker)

        # Price history is a separate endpoint – fetch it first so a failed
        # fundamentals call (HTTP 404 on quoteSummary) never blocks the price.
        try:
            hist = stock.history(period=period)
        except Exception:
            hist = None

        current_price = None
        last_bar_date = None
        if hist is not None and not hist.empty:
            try:
                current_price = round(float(hist["Close"].iloc[-1]), 2)
            except Exception:
                pass
            # Datum der letzten Kerze → Staleness-Check im Daten-Gate
            # (analyzers/data_quality.py): veraltete Kurse vor der Analyse
            # erkennen statt mit ihnen zu rechnen.
            try:
                last_bar_date = hist.index[-1].date().isoformat()
            except Exception:
                pass

        try:
            info = stock.info or {}
        except Exception:
            info = {}

        return {
            "ticker": ticker,
            "current_price": current_price,
            "last_bar_date": last_bar_date,
            "price_change_1w": self._pct_change(hist, 5) if hist is not None else None,
            "price_change_1m": self._pct_change(hist, 21) if hist is not None else None,
            "volume_avg": int(hist["Volume"].mean()) if hist is not None and not hist.empty else None,
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector", "Unknown"),
            "pe_ratio": info.get("trailingPE"),
            "52w_high": info.get("fiftyTwoWeekHigh"),
            "52w_low": info.get("fiftyTwoWeekLow"),
        }

    def _pct_change(self, hist, days: int) -> float | None:
        if hist.empty or len(hist) < days:
            return None
        start = hist["Close"].iloc[-days]
        end = hist["Close"].iloc[-1]
        if start == 0:
            return None
        return round((end - start) / start * 100, 2)
