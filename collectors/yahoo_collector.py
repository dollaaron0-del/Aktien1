import yfinance as yf
import feedparser
from datetime import datetime, timedelta
from typing import List, Dict


RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
    "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
]


class YahooCollector:

    def collect(self, ticker: str, days_back: int = 7) -> List[Dict]:
        results = []
        cutoff = datetime.utcnow() - timedelta(days=days_back)

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

        # Yahoo RSS feed
        try:
            feed_url = RSS_FEEDS[0].format(ticker=ticker)
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:15]:
                published_struct = entry.get("published_parsed")
                if published_struct:
                    published = datetime(*published_struct[:6])
                    if published < cutoff:
                        continue
                else:
                    published = datetime.utcnow()
                results.append({
                    "source": "Yahoo Finance RSS",
                    "ticker": ticker,
                    "title": entry.get("title", ""),
                    "text": entry.get("summary", entry.get("title", "")),
                    "url": entry.get("link", ""),
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
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)
            info = stock.info
            return {
                "ticker": ticker,
                "current_price": round(hist["Close"].iloc[-1], 2) if not hist.empty else None,
                "price_change_1w": self._pct_change(hist, 5),
                "price_change_1m": self._pct_change(hist, 21),
                "volume_avg": int(hist["Volume"].mean()) if not hist.empty else None,
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector", "Unknown"),
                "pe_ratio": info.get("trailingPE"),
                "52w_high": info.get("fiftyTwoWeekHigh"),
                "52w_low": info.get("fiftyTwoWeekLow"),
            }
        except Exception:
            return {"ticker": ticker, "current_price": None}

    def _pct_change(self, hist, days: int) -> float | None:
        if hist.empty or len(hist) < days:
            return None
        start = hist["Close"].iloc[-days]
        end = hist["Close"].iloc[-1]
        if start == 0:
            return None
        return round((end - start) / start * 100, 2)
