"""
Reddit collector using the public JSON API — no API key required.
Rate limit: ~60 requests/minute for unauthenticated access.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import requests

SUBREDDITS = ["stocks", "wallstreetbets", "investing", "StockMarket", "SecurityAnalysis", "wallstreetbetsGER"]

TICKER_TO_NAME: Dict[str, str] = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "TSLA": "Tesla",
    "AMZN": "Amazon", "GOOGL": "Google", "META": "Meta", "AVGO": "Broadcom",
    "AMD": "AMD", "INTC": "Intel", "TSM": "TSMC", "ASML": "ASML",
    "LLY": "Eli Lilly", "NVO": "Novo Nordisk", "JPM": "JPMorgan",
    "GS": "Goldman Sachs", "V": "Visa", "MA": "Mastercard",
    "COIN": "Coinbase", "MSTR": "MicroStrategy",
}

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 StockBot/1.0 (educational research)",
    "Accept": "application/json",
})
_LAST_REQUEST = 0.0
_MIN_INTERVAL = 1.1  # max ~55 req/min to stay safely under limit


def _get(url: str, params: Optional[dict] = None) -> Optional[dict]:
    global _LAST_REQUEST
    elapsed = time.time() - _LAST_REQUEST
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    try:
        resp = _SESSION.get(url, params=params, timeout=10)
        _LAST_REQUEST = time.time()
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            time.sleep(5)
    except Exception:
        pass
    return None


def _parse_posts(data: dict, ticker: str, subreddit: str, cutoff: datetime) -> List[Dict]:
    posts = []
    for child in data.get("data", {}).get("children", []):
        p = child.get("data", {})
        created = datetime.utcfromtimestamp(p.get("created_utc", 0))
        if created < cutoff:
            continue
        title = p.get("title", "")
        body = p.get("selftext", "")[:400]
        text = f"{title}. {body}".strip(". ") if body else title
        posts.append({
            "source":       f"Reddit r/{subreddit}",
            "ticker":       ticker,
            "title":        title,
            "text":         text,
            "score":        p.get("score", 0),
            "url":          p.get("url", ""),
            "published_at": created.isoformat(),
        })
    return posts


class RedditCollector:
    def collect(self, ticker: str, days_back: int = 3) -> List[Dict]:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_back)
        results = []
        company = TICKER_TO_NAME.get(ticker, "")
        queries = list(dict.fromkeys([ticker] + ([company] if company else [])))

        for subreddit in SUBREDDITS:
            for query in queries:
                data = _get(
                    f"https://www.reddit.com/r/{subreddit}/search.json",
                    params={"q": query, "sort": "new", "t": "week",
                            "limit": 15, "restrict_sr": "on"},
                )
                if data:
                    results.extend(_parse_posts(data, ticker, subreddit, cutoff))

        # Deduplicate by URL, sort by upvotes
        seen: set = set()
        unique = []
        for item in sorted(results, key=lambda x: x["score"], reverse=True):
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)

        return unique[:20]
