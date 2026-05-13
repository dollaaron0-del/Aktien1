"""
Twitter/X Collector – Twitter API v2 (Bearer Token)
Sucht nach Tweets zu einem Ticker-Symbol und gibt sie als strukturierte Dicts zurück.

Benötigte .env-Variable:
  TWITTER_BEARER_TOKEN=<dein Bearer Token>

Kostenlose Twitter API v2 erlaubt 500k Tweets/Monat (Essential access).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import requests

from config import config


_BASE_URL = "https://api.twitter.com/2"

# Accounts mit hoher Relevanz für Finanznachrichten (verifizierte Quellen)
_QUALITY_ACCOUNTS = {
    "Reuters", "Bloomberg", "WSJ", "FT", "CNBC", "TheEconomist",
    "MarketWatch", "Investopedia", "FinancialTimes", "business",
}


class TwitterCollector:
    """Sammelt Tweets zu einem Ticker über die Twitter API v2."""

    def __init__(self):
        self._token = config.twitter_bearer_token
        self._session = requests.Session()
        if self._token:
            self._session.headers["Authorization"] = f"Bearer {self._token}"

    @property
    def available(self) -> bool:
        return bool(self._token)

    def collect(
        self,
        ticker: str,
        max_results: int = 30,
        hours_back: int = 24,
    ) -> List[Dict]:
        if not self.available:
            return []

        # Build search query: cashtag OR keyword, no retweets, no replies
        query = f"(${ticker} OR #{ticker}) -is:retweet -is:reply lang:en"

        start_time = (
            datetime.now(timezone.utc) - timedelta(hours=hours_back)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "query": query,
            "max_results": min(max_results, 100),
            "start_time": start_time,
            "tweet.fields": "created_at,public_metrics,author_id,text",
            "expansions": "author_id",
            "user.fields": "username,public_metrics,verified",
            "sort_order": "relevancy",
        }

        try:
            resp = self._session.get(
                f"{_BASE_URL}/tweets/search/recent",
                params=params,
                timeout=15,
            )
        except requests.RequestException:
            return []

        if resp.status_code == 429:
            # Rate limited – return empty rather than crashing
            return []
        if resp.status_code != 200:
            return []

        data = resp.json()
        tweets = data.get("data", [])
        if not tweets:
            return []

        # Build author lookup from expansions
        users = {
            u["id"]: u
            for u in data.get("includes", {}).get("users", [])
        }

        results = []
        for tweet in tweets:
            author = users.get(tweet.get("author_id", ""), {})
            username = author.get("username", "unknown")
            followers = author.get("public_metrics", {}).get("followers_count", 0)
            verified = author.get("verified", False)

            metrics = tweet.get("public_metrics", {})
            engagement = (
                metrics.get("like_count", 0)
                + metrics.get("retweet_count", 0) * 3
                + metrics.get("reply_count", 0)
            )

            created_at = tweet.get("created_at", "")

            results.append({
                "source": f"Twitter/@{username}",
                "ticker": ticker,
                "title": tweet["text"][:120],
                "text": tweet["text"],
                "url": f"https://twitter.com/{username}/status/{tweet['id']}",
                "published_at": created_at,
                # Extra metadata for quality scoring
                "_followers": followers,
                "_engagement": engagement,
                "_verified": verified,
                "_quality_account": username in _QUALITY_ACCOUNTS,
            })

        # Sort by engagement + follower signal (high-quality first)
        results.sort(
            key=lambda x: x["_engagement"] + min(x["_followers"] // 1000, 500),
            reverse=True,
        )

        # Strip internal metadata keys before returning
        cleaned = []
        for r in results:
            cleaned.append({k: v for k, v in r.items() if not k.startswith("_")})

        return cleaned

    def get_sentiment_summary(self, ticker: str, hours_back: int = 6) -> Optional[Dict]:
        """
        Lightweight summary: tweet count and simple bullish/bearish keyword ratio.
        Used by the hourly social scan.
        """
        tweets = self.collect(ticker, max_results=50, hours_back=hours_back)
        if not tweets:
            return None

        bullish_words = {"bull", "buy", "long", "moon", "pump", "breakout", "bullish", "calls", "upside"}
        bearish_words = {"bear", "sell", "short", "dump", "crash", "puts", "bearish", "downside", "drop"}

        bull_count = 0
        bear_count = 0
        for t in tweets:
            text_lower = t["text"].lower()
            if any(w in text_lower for w in bullish_words):
                bull_count += 1
            if any(w in text_lower for w in bearish_words):
                bear_count += 1

        total = len(tweets)
        score = (bull_count - bear_count) / total if total else 0.0

        return {
            "ticker": ticker,
            "tweet_count": total,
            "bull_count": bull_count,
            "bear_count": bear_count,
            "sentiment_score": round(score, 3),
            "label": "BULLISH" if score > 0.1 else ("BEARISH" if score < -0.1 else "NEUTRAL"),
            "sampled_at": datetime.utcnow().isoformat(),
        }
