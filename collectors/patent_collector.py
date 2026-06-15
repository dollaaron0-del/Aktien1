"""
PatentCollector – sammelt Patent-Anmeldungen von USPTO.
Neue Patente geben frühe Hinweise auf Produktentwicklungen.
Apple patentiert AR-Headset → möglicher Wettbewerbsvorteil.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from system.http import http_get

_USPTO_RSS = "https://developer.uspto.gov/api-catalog"
_GOOGLE_PATENTS_RSS = "https://patents.google.com/rss?assignee={company}&after={date}"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

# Ticker → Unternehmensname für USPTO-Suche
_TICKER_TO_COMPANY = {
    "AAPL": "Apple", "MSFT": "Microsoft", "GOOGL": "Google",
    "AMZN": "Amazon", "META": "Meta", "TSLA": "Tesla",
    "NVDA": "NVIDIA", "INTC": "Intel", "AMD": "Advanced Micro",
}


class PatentCollector:
    """Sammelt Patent-Anmeldungen als Innovationssignal."""

    def collect(self, ticker: str, lookback_days: int = 30) -> List[Dict]:
        results = []
        results += self._collect_google_patents(ticker, lookback_days)
        results += self._collect_uspto_fulltext(ticker, lookback_days)
        return results

    def _collect_google_patents(self, ticker: str, lookback_days: int) -> List[Dict]:
        results = []
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        company = _TICKER_TO_COMPANY.get(ticker.upper(), ticker)
        date_str = cutoff.strftime("%Y%m%d")

        try:
            url = f"https://patents.google.com/rss?assignee={company}&after={date_str}&num=10"
            resp = http_get(url, headers=_HEADERS, timeout=12)
            if resp.status_code != 200:
                return []

            root = ET.fromstring(resp.content)
            for item in root.iter("item"):
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link") or "").strip()
                summary = (item.findtext("description") or "").strip()
                pub     = item.findtext("pubDate") or ""

                if not title:
                    continue

                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pub_dt = datetime.utcnow()

                results.append({
                    "source":       f"Patent Filing (Google Patents / {company})",
                    "ticker":       ticker,
                    "title":        f"Neues Patent: {title[:80]}",
                    "text":         (
                        f"{company} hat ein neues Patent angemeldet: {title}. "
                        f"{summary[:300] if summary else ''} "
                        f"Patente sind Frühindikatoren für F&E-Aktivitäten."
                    ),
                    "url":          link,
                    "published_at": pub_dt.isoformat(),
                    "priority":     "LOW",
                })
        except Exception:
            pass
        return results

    def _collect_uspto_fulltext(self, ticker: str, lookback_days: int) -> List[Dict]:
        results = []
        company = _TICKER_TO_COMPANY.get(ticker.upper(), ticker)
        cutoff  = datetime.utcnow() - timedelta(days=lookback_days)

        try:
            # USPTO Open Data API – Patent Full-Text Search
            url = (
                f"https://efts.uspto.gov/LATEST/search-index?q=%22{company}%22"
                f"&dateRangeField=datePublished&startdt={cutoff.strftime('%Y-%m-%d')}"
                f"&enddt={datetime.utcnow().strftime('%Y-%m-%d')}&hits.hits.total.value=true"
            )
            resp = http_get(url, headers=_HEADERS, timeout=12)
            if resp.status_code != 200:
                return []

            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])

            for hit in hits[:5]:
                src   = hit.get("_source", {})
                title = src.get("inventionTitle", "Patent ohne Titel")
                date  = src.get("datePublished", "")
                doc_id = src.get("patentNumber", "")

                results.append({
                    "source":       "USPTO Patent Filing",
                    "ticker":       ticker,
                    "title":        f"USPTO Patent: {title[:80]} ({doc_id})",
                    "text":         (
                        f"{company} hat ein Patent beim USPTO eingereicht: {title}. "
                        f"Patentnummer: {doc_id}. Datum: {date}."
                    ),
                    "url":          f"https://patents.google.com/patent/{doc_id}",
                    "published_at": date or datetime.utcnow().isoformat(),
                    "priority":     "LOW",
                })
        except Exception:
            pass
        return results
