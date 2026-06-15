"""
AAIISentimentCollector – weekly AAII Investor Sentiment Survey.

The AAII (American Association of Individual Investors) publishes a weekly
survey of retail investor sentiment (Bullish / Neutral / Bearish %).

Contrarian signal: extremes historically precede reversals.
  Bearish > 50%  → strong contrarian BUY signal (panic bottom)
  Bullish > 55%  → contrarian caution / potential top

Source: https://www.aaii.com/sentimentsurvey/sent_results (public, no key)
Cache: 7 days (survey is published once per week on Thursdays)
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import List, Dict, Optional

import requests
from system.http import http_get

from logger import get_logger

log = get_logger(__name__)

_URL = "https://www.aaii.com/sentimentsurvey/sent_results"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0; educational)"}
_CACHE: Optional[List[Dict]] = None
_CACHE_TS: float = 0.0
_CACHE_TTL = 7 * 86_400  # 7 days


def _parse_pct(text: str, label: str) -> Optional[float]:
    """Extract percentage value after a label like 'Bullish: 38.5%'."""
    pattern = rf"{label}[:\s]+(\d+(?:\.\d+)?)\s*%"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _interpret(bullish: float, bearish: float) -> tuple[str, str, int]:
    """Returns (signal, explanation, priority_score)."""
    spread = bullish - bearish  # bull-bear spread

    if bearish >= 50:
        return (
            "STRONG_BUY_SIGNAL",
            f"Extrem bearish: {bearish:.1f}% der Privatanleger bearish. "
            "Historisch starkes Konträr-Kaufsignal – Panik-Tiefs entstehen oft hier.",
            92,
        )
    if bearish >= 40:
        return (
            "CONTRARIAN_BULLISH",
            f"Erhöhter Pessimismus: {bearish:.1f}% bearish, {bullish:.1f}% bullish. "
            "Bull-Bear-Spread: {spread:+.1f}%. Leichtes Konträr-Signal.",
            75,
        )
    if bullish >= 55:
        return (
            "CONTRARIAN_CAUTION",
            f"Überhitzter Optimismus: {bullish:.1f}% bullish. "
            "Historisch ein Warnsignal für kurzfristige Überbewertung.",
            70,
        )
    if bullish >= 45:
        return (
            "NEUTRAL_BULLISH",
            f"Normale Stimmung: {bullish:.1f}% bullish, {bearish:.1f}% bearish. "
            "Kein extremes Signal.",
            40,
        )
    return (
        "NEUTRAL",
        f"Gemischte Stimmung: {bullish:.1f}% bullish, {bearish:.1f}% bearish.",
        30,
    )


def _fetch() -> Optional[List[Dict]]:
    try:
        resp = http_get(_URL, headers=_HEADERS, timeout=15)
        if resp.status_code != 200:
            log.debug("AAII: HTTP %d", resp.status_code)
            return None
        text = resp.text

        bullish = _parse_pct(text, "Bullish")
        bearish = _parse_pct(text, "Bearish")
        neutral = _parse_pct(text, "Neutral")

        if bullish is None or bearish is None:
            log.debug("AAII: could not parse sentiment percentages")
            return None

        signal, explanation, score = _interpret(bullish, bearish or 0)
        neutral_str = f", {neutral:.1f}% neutral" if neutral else ""
        now = datetime.utcnow().isoformat()

        return [{
            "source":       "AAII Sentiment Survey",
            "ticker":       "__MACRO__",
            "title":        f"📊 AAII: {bullish:.1f}% bullish / {bearish:.1f}% bearish{neutral_str} [{signal}]",
            "text":         explanation,
            "score":        score,
            "url":          _URL,
            "published_at": now,
            "bullish_pct":  bullish,
            "bearish_pct":  bearish,
            "neutral_pct":  neutral,
            "signal":       signal,
        }]
    except Exception as e:
        log.debug("AAII fetch error: %s", e)
        return None


class AAIISentimentCollector:
    """Collects AAII weekly investor sentiment as a macro contrarian signal."""

    def _get_data(self) -> List[Dict]:
        global _CACHE, _CACHE_TS
        if _CACHE is not None and (time.time() - _CACHE_TS) < _CACHE_TTL:
            return _CACHE
        result = _fetch()
        if result:
            _CACHE = result
            _CACHE_TS = time.time()
            return result
        return _CACHE or []

    def collect(self, ticker: str) -> List[Dict]:
        items = self._get_data()
        return [{**item, "ticker": ticker} for item in items]
