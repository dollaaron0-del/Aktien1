"""
AdhocCollector – Deutsche Pflichtmitteilungen & Directors' Dealings.

Quellen (alle keyless):
  1. Google News RSS gefiltert auf Ad-hoc-Meldungen / Insidertransaktionen
     für den jeweiligen Ticker auf Deutsch
  2. Presseportal.de RSS – Branchen-Feed "Wirtschaft & Finanzen" (allgemein)

Warum wichtig:
  Ad-hoc-Meldungen sind preisrelevante Informationen, zu deren sofortiger
  Veröffentlichung börsennotierte Unternehmen verpflichtet sind (§ 26 WpHG).
  Directors' Dealings (Managergeschäfte) zeigen, wie Insider ihre eigene Aktie
  einschätzen – historisch eines der stärksten Einzelsignale.

Signalgewichtung:
  Ad-hoc Meldung            → Score 92  (Kauf-/Verkaufspflicht)
  Directors' Dealings Kauf  → Score 88  (Insider bullish)
  Directors' Dealings Kauf  → Score 60  (Insider bearish / Verkauf)
  Insidertransaktion        → Score 80

Nur für EU-Aktien aktiv (Ticker-Suffix .DE .AS .PA .SW .L .CO .MC .MI .ST).
Für US/Crypto: leere Liste.

Cache: 4 Stunden (Ad-hoc-Meldungen erscheinen intraday)
"""
from __future__ import annotations

import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import requests
from system.http import http_get

from logger import get_logger

log = get_logger(__name__)

_TIMEOUT = 12
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockSentimentBot/1.0; educational)"}
_CACHE: Dict[str, tuple] = {}   # ticker → (ts, items)
_CACHE_TTL = 4 * 3600           # 4 Stunden

_EU_SUFFIXES = {".DE", ".AS", ".PA", ".SW", ".L", ".CO", ".MC", ".MI", ".ST", ".VI", ".OL"}

# Google News RSS – deutscher Ad-hoc-Filter pro Ticker
_GNEWS = (
    "https://news.google.com/rss/search"
    "?q={query}"
    "&hl=de&gl=DE&ceid=DE:de"
)

# Presseportal.de – Finanz-/Wirtschaftsmeldungen (allgemein, ticker-unabhängig)
_PRESSEPORTAL_RSS = "https://www.presseportal.de/rss/branchen/80.rss"

# Keyword-Scores (title check, case-insensitive)
_KEYWORD_SCORES = [
    # (pattern, score, label)
    (r"ad.?hoc.{0,15}(meldung|bekanntmachung|mitteilung)", 92, "AD_HOC"),
    (r"(directors.{0,5}dealing|manager.{0,5}gesch[äa]ft)", 85,  "DIRECTORS_DEALINGS"),
    (r"insidertransaktion|insider.{0,5}(kauf|erwerb|handel)", 82,  "INSIDER_BUY"),
    (r"insidertransaktion|insider.{0,5}(verkauf|ver[äa]u[ßs]erung)", 62,  "INSIDER_SELL"),
    (r"pflichtmitteilung|stimmrechtsmitteilung",              80,  "REGULATORY"),
    (r"(gewinnwarnung|umsatzwarnung|profit.?warning)",        88,  "PROFIT_WARNING"),
    (r"(positiv|erhöh).{0,20}(prognose|guidance|ergebnis)",  84,  "GUIDANCE_UP"),
    (r"(negativ|senk).{0,20}(prognose|guidance|ergebnis)",   86,  "GUIDANCE_DOWN"),
    (r"übernahme|akquisition|fusio|m&a|takeover",            80,  "M_AND_A"),
    (r"aktienrückkauf|rückkaufprogramm|share.?buyback",      78,  "BUYBACK"),
]

_MAX_AGE_HOURS = 72


def _is_eu_ticker(ticker: str) -> bool:
    upper = ticker.upper()
    return any(upper.endswith(sfx) for sfx in _EU_SUFFIXES)


def _parse_dt(text: str) -> Optional[datetime]:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(text).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _score_item(title: str, text: str) -> tuple[int, str]:
    """Return (score, signal_label) based on keyword matches."""
    combined = (title + " " + text).lower()
    for pattern, score, label in _KEYWORD_SCORES:
        if re.search(pattern, combined, re.I):
            return score, label
    return 55, "EU_NEWS"


def _parse_rss(xml_text: str, ticker: str, source: str) -> List[Dict]:
    items: List[Dict] = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_MAX_AGE_HOURS)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link")  or "").strip()
        desc  = re.sub(r"<[^>]+>", "", item.findtext("description") or "")
        pub   = item.findtext("pubDate") or ""

        if not title:
            continue

        dt = _parse_dt(pub)
        if dt and dt < cutoff:
            continue

        score, label = _score_item(title, desc)
        pub_iso = dt.isoformat() if dt else datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        items.append({
            "source":       source,
            "ticker":       ticker,
            "title":        title,
            "text":         desc[:500] if desc else title,
            "score":        score,
            "url":          link,
            "published_at": pub_iso,
            "signal_label": label,
        })
    return items


def _fetch_gnews(ticker: str) -> List[Dict]:
    """Fetch Google News RSS for this ticker filtered on German regulatory terms."""
    base = ticker.split(".")[0]   # SAP from SAP.DE
    query = urllib.parse.quote_plus(
        f'"{base}" (Ad-hoc OR "Directors Dealings" OR Insidertransaktion '
        f'OR Gewinnwarnung OR Aktienrückkauf OR Pflichtmitteilung)'
    )
    url = _GNEWS.format(query=query)
    try:
        resp = http_get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        return _parse_rss(resp.text, ticker, "DGAP/Google-News-DE")
    except Exception as e:
        log.debug("AdhocCollector gnews error [%s]: %s", ticker, e)
        return []


def _fetch_presseportal(ticker: str) -> List[Dict]:
    """General German financial press releases — filter those mentioning the ticker base."""
    base = ticker.split(".")[0].upper()
    try:
        resp = http_get(_PRESSEPORTAL_RSS, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        raw_items = _parse_rss(resp.text, ticker, "Presseportal.de")
        # Keep only items that mention the company ticker in title/text
        return [i for i in raw_items if base in (i["title"] + i["text"]).upper()]
    except Exception as e:
        log.debug("AdhocCollector presseportal error: %s", e)
        return []


class AdhocCollector:
    """
    Collects German regulatory signals (Ad-hoc, Directors' Dealings, Gewinnwarnungen).
    Returns empty list for non-EU tickers.
    """

    def collect(self, ticker: str) -> List[Dict]:
        if not _is_eu_ticker(ticker):
            return []

        now = time.time()
        if ticker in _CACHE:
            ts, items = _CACHE[ticker]
            if now - ts < _CACHE_TTL:
                return items

        items = _fetch_gnews(ticker) + _fetch_presseportal(ticker)

        # Deduplicate by title prefix
        seen: set = set()
        unique: List[Dict] = []
        for item in items:
            key = item["title"][:60].lower()
            if key not in seen:
                seen.add(key)
                unique.append(item)

        unique.sort(key=lambda x: -x["score"])
        _CACHE[ticker] = (now, unique)
        return unique
