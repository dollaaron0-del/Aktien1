"""
Headline Signal Detector

Scannt allgemeine Marktnachrichten nach handlungsrelevanten Signalen.
Erkennt 12 Signaltypen mit Konfidenzwerten.
Löst bei starken Signalen Telegram-Alert aus.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "headline_scanner_state.json")

# Signal patterns: (regex, base_score, signal_type)
_SIGNAL_PATTERNS: List[Tuple[str, float, str]] = [
    (r"acqui[rs]|merger|takeover|buyout",          0.90, "ACQUISITION"),
    (r"fda.*approv|approved.*fda|breakthrough.*therapy", 0.88, "FDA_APPROVAL"),
    (r"beat.*estimate|earnings.*beat|surpass.*expectation", 0.80, "EARNINGS_BEAT"),
    (r"contract.*award|won.*contract|awarded.*billion", 0.75, "CONTRACT_WIN"),
    (r"breakout|new.*52.week.*high|all.time.*high",  0.72, "BREAKOUT"),
    (r"spin.?off|split.*company|carve.?out",         0.70, "SPIN_OFF"),
    (r"upgrad|raised.*target|buy.*rating",           0.65, "UPGRADE"),
    (r"short.*squeeze|short.*interest.*high",        0.68, "SHORT_SQUEEZE"),
    (r"partner|joint.*venture|collaboration|deal.*sign", 0.65, "PARTNERSHIP"),
    (r"downgrad|cut.*target|sell.*rating",           0.28, "DOWNGRADE"),
    (r"miss.*estimate|earnings.*miss|below.*expectation", 0.20, "EARNINGS_MISS"),
    (r"recall|safety.*concern|investigation.*product", 0.15, "RECALL"),
]

# Ticker extraction patterns
_TICKER_DOLLAR  = re.compile(r'\$([A-Z]{1,5})')
_TICKER_PARENS  = re.compile(r'\(([A-Z]{1,5})\)')
_TICKER_UPPER   = re.compile(r'\b([A-Z]{2,5})\b')

# Common false-positives to ignore
_IGNORE_WORDS = {
    "CEO", "CFO", "COO", "CTO", "IPO", "ETF", "USD", "EUR", "GDP", "CPI",
    "FDA", "SEC", "NYSE", "NASDAQ", "UK", "US", "EU", "THE", "AND", "FOR",
    "NEW", "INC", "LLC", "LTD", "PLC", "NOT", "BUT", "ALL", "BIG",
}


from dataclasses import dataclass


@dataclass
class HeadlineSignal:
    ticker: str
    signal_type: str
    score: float
    headline: str
    source: str
    detected_at: str


class HeadlineSignalDetector:
    """
    Scannt RSS-Feeds und NewsAPI nach handlungsrelevanten Schlagzeilen.
    Extrahiert Ticker-Symbole und ordnet Signaltypen zu.
    """

    # Strong signal threshold → add to BenchList
    STRONG_THRESHOLD = 0.60
    # Very strong threshold → immediate Telegram alert
    VERY_STRONG_THRESHOLD = 0.85

    def __init__(self, newsapi_key: str = "", telegram_notifier=None):
        self.newsapi_key = newsapi_key
        self.notifier   = telegram_notifier
        self._state: Dict = self._load_state()

    def scan(self, bench_list: Optional[List[str]] = None) -> List[HeadlineSignal]:
        """
        Hauptmethode: Scannt Quellen, filtert Duplikate, gibt Signale zurück.
        Starke Signale werden dem bench_list hinzugefügt.
        """
        articles = self._fetch_articles()
        signals  = []
        added_to_bench = []

        for article in articles:
            title = article.get("title", "")
            if not title or self._is_duplicate(title):
                continue

            signal = self._classify(article)
            if signal is None:
                continue

            signals.append(signal)
            self._mark_seen(title)

            # Add strong signals to BenchList
            if bench_list is not None and signal.score >= self.STRONG_THRESHOLD:
                if signal.ticker and signal.ticker not in bench_list:
                    bench_list.append(signal.ticker)
                    added_to_bench.append(signal.ticker)

            # Immediate Telegram for very strong signals
            if self.notifier and signal.score >= self.VERY_STRONG_THRESHOLD:
                self._send_alert(signal)

        if added_to_bench:
            log.info("HeadlineScanner: %d Ticker zu BenchList hinzugefügt: %s",
                     len(added_to_bench), added_to_bench)

        self._save_state()
        return signals

    def _fetch_articles(self) -> List[Dict]:
        articles = []

        # RSS Feeds
        rss_urls = [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://feeds.finance.yahoo.com/rss/2.0/headline",
            "https://www.marketwatch.com/rss/topstories",
        ]
        try:
            import feedparser
            for url in rss_urls:
                feed = feedparser.parse(url)
                for entry in feed.entries[:30]:
                    articles.append({
                        "title":   entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                        "source":  url.split("/")[2],
                        "link":    entry.get("link", ""),
                    })
        except Exception:
            pass

        # NewsAPI
        if self.newsapi_key:
            try:
                import requests
                resp = requests.get(
                    "https://newsapi.org/v2/top-headlines",
                    params={"category": "business", "language": "en",
                            "pageSize": 50, "apiKey": self.newsapi_key},
                    timeout=10,
                )
                if resp.status_code == 200:
                    for a in resp.json().get("articles", []):
                        articles.append({
                            "title":   a.get("title", ""),
                            "summary": a.get("description", ""),
                            "source":  a.get("source", {}).get("name", ""),
                        })
            except Exception:
                pass

        return articles

    def _classify(self, article: Dict) -> Optional[HeadlineSignal]:
        """Versucht einen Signaltyp und Ticker zu extrahieren."""
        title = (article.get("title") or "").lower()
        full  = title + " " + (article.get("summary") or "").lower()

        best_type  = None
        best_score = 0.0
        for pattern, score, sig_type in _SIGNAL_PATTERNS:
            if re.search(pattern, full, re.IGNORECASE):
                if score > best_score:
                    best_score = score
                    best_type  = sig_type

        if best_type is None:
            return None

        # Extract ticker
        ticker = self._extract_ticker(article.get("title", ""))
        if not ticker:
            return None

        return HeadlineSignal(
            ticker=ticker,
            signal_type=best_type,
            score=best_score,
            headline=article.get("title", "")[:200],
            source=article.get("source", ""),
            detected_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        )

    def _extract_ticker(self, text: str) -> Optional[str]:
        """Extrahiert Ticker aus Überschrift."""
        # $TICKER notation (highest priority)
        m = _TICKER_DOLLAR.search(text)
        if m:
            return m.group(1)

        # (TICKER) in parentheses
        m = _TICKER_PARENS.search(text)
        if m and m.group(1) not in _IGNORE_WORDS:
            return m.group(1)

        # Uppercase word (lowest priority, many false positives)
        matches = _TICKER_UPPER.findall(text)
        for match in matches:
            if match not in _IGNORE_WORDS and len(match) >= 2:
                return match

        return None

    def _is_duplicate(self, title: str) -> bool:
        seen = self._state.get("seen_titles", {})
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)).isoformat()
        # Clean old entries
        self._state["seen_titles"] = {
            t: ts for t, ts in seen.items() if ts > cutoff
        }
        return title in self._state["seen_titles"]

    def _mark_seen(self, title: str) -> None:
        if "seen_titles" not in self._state:
            self._state["seen_titles"] = {}
        self._state["seen_titles"][title] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    def _send_alert(self, signal: HeadlineSignal) -> None:
        msg = (
            f"🚨 <b>Starkes Signal: {signal.ticker}</b>\n"
            f"Typ: {signal.signal_type} (Score: {signal.score:.2f})\n"
            f"📰 {signal.headline[:200]}"
        )
        try:
            self.notifier.send(msg)
        except Exception:
            pass

    def _load_state(self) -> Dict:
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        try:
            with open(_STATE_FILE, "w") as f:
                json.dump(self._state, f)
        except Exception:
            pass
