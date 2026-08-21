"""
RedditHypeScanner — discovers trending tickers organically from Reddit posts.

Unlike RedditCollector (which searches for known tickers), this scanner reads
hot/new posts freely and extracts all ticker mentions, then ranks them by
mention velocity (current vs. previous scan window).

Subreddits: wallstreetbets, stocks, investing, StockMarket, wallstreetbetsGER,
            SecurityAnalysis, options, Daytrading, pennystocks

Output: list of HypeTicker(ticker, mentions, upvotes, velocity, sample_titles)
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Optional

import requests

from logger import get_logger

log = get_logger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
_SUBREDDITS = [
    "wallstreetbets", "stocks", "investing", "StockMarket",
    "wallstreetbetsGER", "SecurityAnalysis", "options", "Daytrading",
]
_POSTS_PER_SUB   = 50       # hot + new combined per subreddit
_LOOKBACK_HOURS  = 48       # how far back to count mentions
_MIN_MENTIONS    = 2        # ignore tickers mentioned only once
_TOP_N           = 15       # max tickers returned
_CACHE_FILE      = Path("data/reddit_hype_cache.json")
_CACHE_TTL_SEC   = 2.5 * 3600  # don't re-scan within 2.5h
_REQUEST_DELAY   = 1.2      # seconds between requests

# False-positive filter: common uppercase words that look like tickers
_STOPWORDS = {
    "I", "A", "THE", "FOR", "AND", "BUT", "ARE", "NOT", "YOU", "ALL",
    "CAN", "HAS", "MORE", "NEW", "NOW", "BUY", "PUT", "GET", "USE",
    "ANY", "SEE", "OUT", "ITS", "HIS", "HER", "OUR", "USA", "USD",
    "UK", "EU", "US", "EV", "AI", "ML", "ETF", "WSB", "DD", "IPO",
    "ER", "PE", "ATH", "ATL", "YOY", "QOQ", "CEO", "CFO", "SEC",
    "FED", "IMO", "IMHO", "TBH", "YOLO", "FOMO", "DCA", "SP",
    "EDIT", "UPDATE", "TLDR", "TL", "DR", "OP", "PM", "AM",
    "COVID", "EBIT", "EBITDA", "ROI", "EPS", "GAAP", "R", "OH",
    "UP", "DOWN", "ON", "OF", "OR", "AT", "BE", "BY", "IF",
    "HODL", "LOL", "WTF", "OMG", "GG", "GTE", "LTE",
}

_TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')


_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 StockBot/1.0 (educational research)",
    "Accept": "application/json",
})
_LAST_REQ = 0.0


def _get(url: str, params: Optional[dict] = None) -> Optional[dict]:
    global _LAST_REQ
    wait = _REQUEST_DELAY - (time.time() - _LAST_REQ)
    if wait > 0:
        time.sleep(wait)
    try:
        r = _SESSION.get(url, params=params, timeout=12)
        _LAST_REQ = time.time()
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            log.debug("Reddit rate-limited, sleeping 10s")
            time.sleep(10)
    except Exception as e:
        log.debug("Reddit request error: %s", e)
    return None


def _extract_tickers(text: str) -> List[str]:
    return [m for m in _TICKER_RE.findall(text) if m not in _STOPWORDS and len(m) >= 2]


def _fetch_posts(subreddit: str, sort: str = "hot", limit: int = 25) -> List[dict]:
    data = _get(
        f"https://www.reddit.com/r/{subreddit}/{sort}.json",
        params={"limit": limit, "t": "day"},
    )
    if not data:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)
    posts = []
    for child in data.get("data", {}).get("children", []):
        p = child.get("data", {})
        created = datetime.fromtimestamp(p.get("created_utc", 0), tz=timezone.utc)
        if created < cutoff:
            continue
        posts.append({
            "title":    p.get("title", ""),
            "body":     p.get("selftext", "")[:300],
            "score":    p.get("score", 0),
            "comments": p.get("num_comments", 0),
            "sub":      subreddit,
        })
    return posts


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(data: dict) -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(data))


class HypeTicker:
    __slots__ = ("ticker", "mentions", "upvotes", "velocity", "subreddits", "sample_titles")

    def __init__(
        self,
        ticker: str,
        mentions: int,
        upvotes: int,
        velocity: float,
        subreddits: List[str],
        sample_titles: List[str],
    ):
        self.ticker        = ticker
        self.mentions      = mentions
        self.upvotes       = upvotes
        self.velocity      = velocity       # current / previous (>1 = growing)
        self.subreddits    = subreddits
        self.sample_titles = sample_titles


class RedditHypeScanner:
    """
    Scans Reddit for organically trending tickers.

    Usage::
        hits = RedditHypeScanner().scan(top_n=10, min_mentions=3)
        for h in hits:
            print(h.ticker, h.mentions, h.velocity)
    """

    def scan(self, top_n: int = _TOP_N, min_mentions: int = _MIN_MENTIONS) -> List[HypeTicker]:
        cache = _load_cache()
        now_ts = time.time()

        if cache.get("ts", 0) and now_ts - cache["ts"] < _CACHE_TTL_SEC:
            log.debug("RedditHypeScanner: using cached results (%.0fmin old)",
                      (now_ts - cache["ts"]) / 60)
            return self._from_cache(cache.get("results", []), top_n)

        prev_counts: Dict[str, int] = cache.get("counts", {})
        log.info("RedditHypeScanner: scanning %d subreddits …", len(_SUBREDDITS))

        ticker_data: Dict[str, dict] = {}

        for sub in _SUBREDDITS:
            posts = _fetch_posts(sub, sort="hot",  limit=_POSTS_PER_SUB // 2)
            posts += _fetch_posts(sub, sort="new",  limit=_POSTS_PER_SUB // 2)

            for post in posts:
                combined = post["title"] + " " + post["body"]
                tickers  = _extract_tickers(combined)
                for t in set(tickers):
                    if t not in ticker_data:
                        ticker_data[t] = {
                            "mentions":  0,
                            "upvotes":   0,
                            "subs":      set(),
                            "titles":    [],
                        }
                    entry = ticker_data[t]
                    entry["mentions"]  += combined.count(t)
                    entry["upvotes"]   += post["score"]
                    entry["subs"].add(sub)
                    if post["title"] not in entry["titles"]:
                        entry["titles"].append(post["title"])

        current_counts = {t: d["mentions"] for t, d in ticker_data.items()}

        results: List[HypeTicker] = []
        for ticker, d in ticker_data.items():
            if d["mentions"] < min_mentions:
                continue
            prev = prev_counts.get(ticker, 0)
            velocity = (d["mentions"] / max(prev, 1)) if prev else 2.0
            results.append(HypeTicker(
                ticker=ticker,
                mentions=d["mentions"],
                upvotes=d["upvotes"],
                velocity=round(velocity, 2),
                subreddits=list(d["subs"]),
                sample_titles=d["titles"][:3],
            ))

        results.sort(key=lambda h: (h.mentions * min(h.velocity, 5.0) + h.upvotes * 0.01), reverse=True)
        top = results[:top_n]

        _save_cache({
            "ts":      now_ts,
            "counts":  current_counts,
            "results": [self._to_dict(h) for h in top],
        })

        log.info("RedditHypeScanner: %d tickers found, top %d returned", len(results), len(top))
        return top

    @staticmethod
    def _to_dict(h: HypeTicker) -> dict:
        return {
            "ticker":        h.ticker,
            "mentions":      h.mentions,
            "upvotes":       h.upvotes,
            "velocity":      h.velocity,
            "subreddits":    h.subreddits,
            "sample_titles": h.sample_titles,
        }

    @staticmethod
    def _from_cache(raw: list, top_n: int) -> List[HypeTicker]:
        out = []
        for d in raw[:top_n]:
            out.append(HypeTicker(
                ticker=d["ticker"],
                mentions=d["mentions"],
                upvotes=d["upvotes"],
                velocity=d["velocity"],
                subreddits=d.get("subreddits", []),
                sample_titles=d.get("sample_titles", []),
            ))
        return out
