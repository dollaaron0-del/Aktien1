"""
collectors/crypto_news_collector.py – Krypto-spezifische Signale.

Quellen:
  1. Fear & Greed Index  – api.alternative.me/fng/  (kein API-Key)
  2. CoinDesk RSS Feed   – coindesk.com/arc/outboundfeeds/rss/
  3. Reddit              – r/CryptoCurrency, r/Bitcoin, r/ethereum (öffentliches JSON)
"""

from __future__ import annotations

import urllib.request
import urllib.parse
import json
import xml.etree.ElementTree as ET
from typing import Dict, List

from logger import get_logger

log = get_logger(__name__)

_REDDIT_SUBREDDITS = ["CryptoCurrency", "Bitcoin", "ethereum"]
_COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"
_FNG_URL = "https://api.alternative.me/fng/?limit=1"

_REQUEST_TIMEOUT = 10  # seconds


def _fetch(url: str, headers: Dict[str, str] | None = None) -> bytes | None:
    """Simple HTTP GET; returns body bytes or None on error."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return resp.read()
    except Exception as e:
        log.debug("CryptoNewsCollector fetch %s: %s", url, e)
        return None


class CryptoNewsCollector:
    """
    Sammelt Krypto-spezifische Signale:
    1. Fear & Greed Index (api.alternative.me/fng/ – kostenlos, kein Key)
    2. CoinDesk RSS Feed (coindesk.com/arc/outboundfeeds/rss/)
    3. Reddit r/CryptoCurrency + r/Bitcoin + r/ethereum (öffentliche JSON API)
    """

    # ── Public interface ──────────────────────────────────────────────────────

    def collect(self, ticker: str) -> List[Dict]:
        """
        ticker: "BTC", "ETH", "SOL" etc.
        Gibt Liste von News-Items zurück im Standard-Format:
        {"title": str, "text": str, "published": str, "source": str, "sentiment_score": None}
        """
        items: List[Dict] = []
        items.extend(self._fetch_fear_greed())
        items.extend(self._fetch_coindesk(ticker))
        items.extend(self._fetch_reddit(ticker))
        return items

    # ── Fear & Greed ──────────────────────────────────────────────────────────

    def _fetch_fear_greed(self) -> List[Dict]:
        try:
            body = _fetch(_FNG_URL)
            if not body:
                return []
            data = json.loads(body)
            entry = data.get("data", [{}])[0]
            value = entry.get("value", "?")
            label = entry.get("value_classification", "Unknown")
            timestamp = entry.get("timestamp", "")
            title = f"Krypto Fear & Greed Index: {value} ({label})"
            return [_make_item(
                title=title,
                text=title,
                published=timestamp,
                source="fear_greed_index",
            )]
        except Exception as e:
            log.debug("CryptoNewsCollector fear_greed: %s", e)
            return []

    # ── CoinDesk RSS ──────────────────────────────────────────────────────────

    def _fetch_coindesk(self, ticker: str) -> List[Dict]:
        try:
            body = _fetch(_COINDESK_RSS)
            if not body:
                return []
            root = ET.fromstring(body)
            channel = root.find("channel")
            if channel is None:
                return []

            search_terms = {ticker.lower(), _TICKER_ALIASES.get(ticker.upper(), ticker.lower())}
            results: List[Dict] = []

            for item in channel.findall("item"):
                title = (item.findtext("title") or "").strip()
                desc = (item.findtext("description") or "").strip()
                combined = (title + " " + desc).lower()

                if not any(term in combined for term in search_terms):
                    continue

                pub_date = (item.findtext("pubDate") or "").strip()
                link = (item.findtext("link") or "").strip()

                results.append(_make_item(
                    title=title,
                    text=desc or title,
                    published=pub_date,
                    source="coindesk_rss",
                ))
                if len(results) >= 5:
                    break

            return results
        except Exception as e:
            log.debug("CryptoNewsCollector coindesk %s: %s", ticker, e)
            return []

    # ── Reddit ────────────────────────────────────────────────────────────────

    def _fetch_reddit(self, ticker: str) -> List[Dict]:
        items: List[Dict] = []
        search_terms = {ticker.lower(), _TICKER_ALIASES.get(ticker.upper(), ticker.lower())}
        headers = {"User-Agent": "CryptoNewsCollector/1.0 (trading bot research)"}

        for subreddit in _REDDIT_SUBREDDITS:
            try:
                query = urllib.parse.quote(ticker)
                url = (
                    f"https://www.reddit.com/r/{subreddit}/search.json"
                    f"?q={query}&sort=new&limit=10&t=day"
                )
                body = _fetch(url, headers=headers)
                if not body:
                    continue

                data = json.loads(body)
                posts = data.get("data", {}).get("children", [])
                for post in posts:
                    pd = post.get("data", {})
                    title = (pd.get("title") or "").strip()
                    if not any(term in title.lower() for term in search_terms):
                        continue
                    text = (pd.get("selftext") or pd.get("url") or "").strip()
                    created = str(pd.get("created_utc", ""))
                    items.append(_make_item(
                        title=title,
                        text=text or title,
                        published=created,
                        source=f"reddit_r_{subreddit.lower()}",
                    ))
            except Exception as e:
                log.debug("CryptoNewsCollector reddit/%s %s: %s", subreddit, ticker, e)
                continue

        return items


# ── Helpers ───────────────────────────────────────────────────────────────────

# Map ticker symbols to common name variants used in article text
_TICKER_ALIASES: Dict[str, str] = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "BNB":  "binance",
    "XRP":  "ripple",
    "ADA":  "cardano",
    "AVAX": "avalanche",
    "DOT":  "polkadot",
    "MATIC": "polygon",
    "LINK": "chainlink",
    "UNI":  "uniswap",
    "ATOM": "cosmos",
}


def _make_item(title: str, text: str, published: str, source: str) -> Dict:
    return {
        "title":           title,
        "text":            text,
        "published":       published,
        "source":          source,
        "sentiment_score": None,
    }
