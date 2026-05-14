"""
CEOInterviewCollector – sammelt CEO/CFO Interviews und Statements.
CEOs sprechen Klartext in Interviews – oft wichtiger als Quartalsberichte.
Quellen: CNBC RSS, Bloomberg RSS (öffentlich), Yahoo Finance RSS.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from email.utils import parsedate_to_datetime

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

_SOURCES = [
    {
        "name": "CNBC",
        "url":  "https://www.cnbc.com/id/100003114/device/rss/rss.html",  # Markets RSS
    },
    {
        "name": "CNBC CEO",
        "url":  "https://www.cnbc.com/id/10000664/device/rss/rss.html",  # CEO Council
    },
    {
        "name": "Yahoo Finance",
        "url":  "https://finance.yahoo.com/news/rssindex",
    },
    {
        "name": "MarketWatch",
        "url":  "https://feeds.marketwatch.com/marketwatch/topstories/",
    },
]

_CEO_KEYWORDS = [
    "CEO", "CFO", "COO", "CTO", "chief executive", "chief financial",
    "interview", "speaks", "says", "told", "conference", "investor day",
    "presentation", "remarks", "statement", "outlook",
]


class CEOInterviewCollector:
    """Sammelt CEO/CFO-Interviews und wichtige Managementstatements."""

    def collect(self, ticker: str, lookback_days: int = 14) -> List[Dict]:
        results = []
        cutoff  = datetime.utcnow() - timedelta(days=lookback_days)

        for source in _SOURCES:
            try:
                resp = requests.get(source["url"], headers=_HEADERS, timeout=10)
                if resp.status_code != 200:
                    continue

                root = ET.fromstring(resp.content)
                for item in root.iter("item"):
                    title   = (item.findtext("title") or "").strip()
                    link    = (item.findtext("link") or "").strip()
                    summary = (item.findtext("description") or "").strip()
                    pub     = item.findtext("pubDate") or ""

                    combined = (title + " " + summary).upper()
                    if ticker.upper() not in combined:
                        continue

                    # Nur CEO/Management-relevante Artikel
                    combined_lower = combined.lower()
                    is_ceo_related = any(kw.lower() in combined_lower for kw in _CEO_KEYWORDS)
                    if not is_ceo_related:
                        continue

                    try:
                        pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pub_dt = datetime.utcnow()

                    results.append({
                        "source":       f"CEO Interview ({source['name']})",
                        "ticker":       ticker,
                        "title":        title,
                        "text":         (
                            f"Management-Statement zu {ticker}: {title}. "
                            f"{summary[:500] if summary else ''}"
                        ),
                        "url":          link,
                        "published_at": pub_dt.isoformat(),
                        "priority":     "HIGH",
                    })
            except Exception:
                continue

        return results
