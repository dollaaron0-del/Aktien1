"""
EURegulationCollector – sammelt EU-Regulierungsmeldungen von EUR-Lex.
EU-Gesetze betreffen US-Tech-Firmen massiv (DSGVO, AI Act, DMA, DSA).
Apple/Meta/Google können Milliarden-Bußgelder erhalten.
Quelle: EUR-Lex RSS, EU-Kommission Pressemitteilungen.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import List, Dict
import requests
from system.http import http_get
from email.utils import parsedate_to_datetime

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

# EUR-Lex RSS Feeds für relevante Bereiche
_EURLEX_SOURCES = [
    {
        "name": "EU-Kommission Wettbewerb",
        "url":  "https://ec.europa.eu/competition/antitrust/cases/index/by_case_number.xml",
    },
    {
        "name": "EUR-Lex Neue Gesetze",
        "url":  "https://eur-lex.europa.eu/oj/direct-access.html?ojYear=latest&types=L&series=L&locale=de",
    },
    {
        "name": "EU Pressemitteilungen",
        "url":  "https://ec.europa.eu/commission/presscorner/api/documents?service=press&institutionid=COMM&typeids=IP&sortby=date&locale=en&rss=true",
    },
]

# Ticker → relevante Stichwörter für EU-Regulierung
_TICKER_EU_KEYWORDS = {
    "AAPL": ["Apple", "App Store", "iOS"],
    "GOOGL": ["Google", "Alphabet", "Android", "Search"],
    "META": ["Meta", "Facebook", "Instagram", "WhatsApp"],
    "AMZN": ["Amazon", "AWS", "marketplace"],
    "MSFT": ["Microsoft", "LinkedIn", "Teams", "Windows"],
    "TSLA": ["Tesla", "electric vehicle", "EV"],
    "NVDA": ["NVIDIA", "chip", "semiconductor"],
}

# Hochrisiko-Begriffe
_HIGH_IMPACT_TERMS = [
    "fine", "penalty", "ban", "block", "antitrust", "Bußgeld",
    "billion", "Milliarden", "illegal", "violation", "investigation",
    "probe", "DMA", "DSA", "GDPR", "DSGVO", "AI Act",
]


class EURegulationCollector:
    """Sammelt EU-Regulierungsnachrichten die US-Aktien betreffen."""

    def collect(self, ticker: str, lookback_days: int = 30) -> List[Dict]:
        results = []
        results += self._collect_eu_press(ticker, lookback_days)
        results += self._collect_eurlex_rss(ticker, lookback_days)
        return results

    def _collect_eu_press(self, ticker: str, lookback_days: int) -> List[Dict]:
        """EC Pressemitteilungen zu Wettbewerb und Regulierung."""
        results = []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
        keywords = _TICKER_EU_KEYWORDS.get(ticker.upper(), [ticker])

        try:
            # EU Competition RSS
            url = "https://ec.europa.eu/competition-policy/index/news_en"
            resp = http_get(url, headers=_HEADERS, timeout=12)
            # If not XML, skip
            if resp.status_code != 200:
                return []

            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                return []

            for item in root.iter("item"):
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link") or "").strip()
                summary = (item.findtext("description") or "").strip()
                pub     = item.findtext("pubDate") or ""

                combined = (title + " " + summary).lower()
                if not any(kw.lower() in combined for kw in keywords):
                    continue

                try:
                    pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pub_dt = datetime.now(timezone.utc).replace(tzinfo=None)

                is_high_impact = any(term.lower() in combined for term in _HIGH_IMPACT_TERMS)
                priority = "HIGH" if is_high_impact else "MEDIUM"

                results.append({
                    "source":       "EU-Kommission Regulierung",
                    "ticker":       ticker,
                    "title":        f"EU-Regulierung: {title[:80]}",
                    "text":         (
                        f"EU-Regulierungsmeldung zu {ticker}: {title}. "
                        f"{summary[:400] if summary else ''}"
                    ),
                    "url":          link,
                    "published_at": pub_dt.isoformat(),
                    "priority":     priority,
                })
        except Exception:
            pass
        return results

    def _collect_eurlex_rss(self, ticker: str, lookback_days: int) -> List[Dict]:
        """EUR-Lex Official Journal RSS."""
        results = []
        cutoff  = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
        keywords = _TICKER_EU_KEYWORDS.get(ticker.upper(), [ticker])

        # Tech/AI regulation feeds
        regulation_urls = [
            "https://eur-lex.europa.eu/search.html?type=quick&lang=en&text=artificial+intelligence&qid=latest&DTS_DOM=ALL&typeOfActStatus=COM&DTS_SUBDOM=ALL_ALL&locale=en&rss=true",
            "https://eur-lex.europa.eu/search.html?type=quick&lang=en&text=digital+markets&rss=true",
        ]

        for url in regulation_urls:
            try:
                resp = http_get(url, headers=_HEADERS, timeout=10)
                if resp.status_code != 200:
                    continue
                try:
                    root = ET.fromstring(resp.content)
                except ET.ParseError:
                    continue

                for item in root.iter("item"):
                    title   = (item.findtext("title") or "").strip()
                    link    = (item.findtext("link") or "").strip()
                    summary = (item.findtext("description") or "").strip()
                    pub     = item.findtext("pubDate") or ""

                    combined = (title + " " + summary).lower()
                    if not any(kw.lower() in combined for kw in keywords):
                        continue

                    try:
                        pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pub_dt = datetime.now(timezone.utc).replace(tzinfo=None)

                    results.append({
                        "source":       "EUR-Lex",
                        "ticker":       ticker,
                        "title":        f"EU-Gesetz: {title[:80]}",
                        "text":         f"Neues EU-Regelwerk relevant für {ticker}: {title}. {summary[:300]}",
                        "url":          link,
                        "published_at": pub_dt.isoformat(),
                        "priority":     "MEDIUM",
                    })
            except Exception:
                continue

        return results
