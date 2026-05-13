"""
StockTwits Collector – Echtzeitsentiment von aktiven Tradern

Warum StockTwits eine Frühquelle ist:
  StockTwits ist ein soziales Netzwerk exklusiv für Aktien-Trader.
  Nutzer taggen Beiträge mit $TICKER und posten Meinungen oft während
  Pre-Market, Earnings-Calls oder Breaking News – häufig BEVOR Mainstream-
  Medien reagieren. Die API gibt auch ein direktes Sentiment (Bullish/Bearish)
  zurück, das von den Nutzern selbst gesetzt wird.

API: https://api.stocktwits.com/api/2/streams/symbol/{TICKER}.json
  – kostenlos, kein API-Key erforderlich für Public Streams
  – max. 30 Nachrichten pro Anfrage, sortiert nach Aktualität
"""

import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional

_BASE_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_HEADERS = {"User-Agent": "StockSentimentBot/1.0 (educational use)"}
_TIMEOUT = 10

# Minimum likes/reshares to filter out noise
_MIN_LIKES = 2


class StockTwitsCollector:
    """
    Fetches recent StockTwits messages for a ticker.
    Only includes messages with explicit Bullish/Bearish sentiment tags
    or high engagement (likes ≥ MIN_LIKES).
    """

    def __init__(self, lookback_hours: int = 48, max_messages: int = 30):
        self.lookback_hours = lookback_hours
        self.max_messages = max_messages
        self._cutoff = datetime.utcnow() - timedelta(hours=lookback_hours)

    def collect(self, ticker: str) -> List[Dict]:
        raw_messages = self._fetch(ticker)
        return self._to_news_items(ticker, raw_messages)

    # ── API call ──────────────────────────────────────────────────────────────

    def _fetch(self, ticker: str) -> List[Dict]:
        try:
            resp = requests.get(
                _BASE_URL.format(ticker=ticker.upper()),
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 404:
                return []  # Unknown ticker on StockTwits
            resp.raise_for_status()
            data = resp.json()
            return data.get("messages", [])
        except Exception:
            return []

    # ── Parsing ───────────────────────────────────────────────────────────────

    def _to_news_items(self, ticker: str, messages: List[Dict]) -> List[Dict]:
        results = []
        bullish_count = 0
        bearish_count = 0

        for msg in messages[:self.max_messages]:
            created_raw = msg.get("created_at", "")
            created = self._parse_ts(created_raw)
            if created and created < self._cutoff:
                continue

            body = (msg.get("body") or "").strip()
            if not body or len(body) < 15:
                continue

            sentiment_obj = (msg.get("entities") or {}).get("sentiment") or {}
            sentiment_basic = (sentiment_obj.get("basic") or "").capitalize()

            likes    = msg.get("likes", {}).get("total", 0) or 0
            reshares = msg.get("reshares", {}).get("reshared_count", 0) or 0

            # Skip low-engagement, non-sentiment messages
            if sentiment_basic not in ("Bullish", "Bearish") and likes < _MIN_LIKES:
                continue

            user = (msg.get("user") or {}).get("username", "Anonymous")
            followers = (msg.get("user") or {}).get("followers", 0) or 0

            if sentiment_basic == "Bullish":
                bullish_count += 1
                signal = "BULLISH"
            elif sentiment_basic == "Bearish":
                bearish_count += 1
                signal = "BEARISH"
            else:
                signal = "NEUTRAL"

            date_str = created.strftime("%Y-%m-%d") if created else (created_raw or "")[:10]

            results.append({
                "source": "StockTwits",
                "ticker": ticker,
                "title": f"StockTwits [{signal}] @{user}: {body[:80]}",
                "text": (
                    f"Trader @{user} ({followers} Follower) über ${ticker}: {body}. "
                    f"Sentiment: {sentiment_basic or 'Nicht angegeben'}. "
                    f"Likes: {likes}, Reshares: {reshares}. "
                    f"StockTwits-Trader reagieren oft in Echtzeit auf Breaking News "
                    f"und Earnings – Frühindikator vor Mainstream-Berichterstattung."
                ),
                "published_at": date_str,
                "url": f"https://stocktwits.com/symbol/{ticker}",
                "sentiment": signal,
                "likes": likes,
            })

        # Append a sentiment-summary item if we have enough messages
        total = bullish_count + bearish_count
        if total >= 3:
            bull_pct = round(bullish_count / total * 100)
            agg_signal = "BULLISH" if bull_pct >= 60 else ("BEARISH" if bull_pct <= 40 else "NEUTRAL")
            results.append({
                "source": "StockTwits-Aggregate",
                "ticker": ticker,
                "title": (
                    f"StockTwits-Stimmung ${ticker}: "
                    f"{bull_pct}% Bullish / {100 - bull_pct}% Bearish "
                    f"({total} bewertete Nachrichten)"
                ),
                "text": (
                    f"Aktuelle StockTwits-Community-Stimmung für ${ticker}: "
                    f"{bullish_count} Bullish, {bearish_count} Bearish von {total} Beiträgen. "
                    f"Bullish-Anteil: {bull_pct}%. Signal: {agg_signal}. "
                    f"Hoher Konsens (>70% oder <30%) gilt als zuverlässiges Frühsignal."
                ),
                "published_at": datetime.utcnow().strftime("%Y-%m-%d"),
                "url": f"https://stocktwits.com/symbol/{ticker}",
                "sentiment": agg_signal,
            })

        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ts(raw: str) -> Optional[datetime]:
        if not raw:
            return None
        # Normalise: strip timezone suffix, keep first 19 chars (YYYY-MM-DDTHH:MM:SS)
        try:
            return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
