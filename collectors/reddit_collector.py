import praw
from datetime import datetime, timedelta
from typing import List, Dict
from config import config


# Maps US ticker symbols to common company names for better Reddit search coverage
TICKER_TO_NAME = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "GOOGL": "Google Alphabet",
    "META": "Meta Facebook",
}

SUBREDDITS = ["stocks", "wallstreetbets", "investing", "StockMarket", "SecurityAnalysis"]


class RedditCollector:
    def __init__(self):
        self._reddit = None

    def _get_client(self):
        if self._reddit is None:
            self._reddit = praw.Reddit(
                client_id=config.reddit_client_id,
                client_secret=config.reddit_client_secret,
                user_agent=config.reddit_user_agent,
            )
        return self._reddit

    def collect(self, ticker: str, days_back: int = 3) -> List[Dict]:
        if not config.reddit_client_id or not config.reddit_client_secret:
            return []

        reddit = self._get_client()
        cutoff = datetime.utcnow() - timedelta(days=days_back)
        results = []

        company = TICKER_TO_NAME.get(ticker, ticker)
        queries = [ticker, company] if company != ticker else [ticker]

        for subreddit_name in SUBREDDITS:
            subreddit = reddit.subreddit(subreddit_name)
            for query in queries:
                try:
                    for submission in subreddit.search(query, sort="new", time_filter="week", limit=15):
                        created = datetime.utcfromtimestamp(submission.created_utc)
                        if created < cutoff:
                            continue
                        text = f"{submission.title}. {submission.selftext[:500]}" if submission.selftext else submission.title
                        results.append({
                            "source": f"Reddit r/{subreddit_name}",
                            "ticker": ticker,
                            "title": submission.title,
                            "text": text,
                            "score": submission.score,
                            "url": submission.url,
                            "published_at": created.isoformat(),
                        })
                except Exception:
                    pass

        # Deduplicate by URL and sort by upvotes
        seen = set()
        unique = []
        for item in sorted(results, key=lambda x: x["score"], reverse=True):
            if item["url"] not in seen:
                seen.add(item["url"])
                unique.append(item)

        return unique[:20]
