"""
EarningsTranscriptCollector – sammelt Earnings-Call-Transkripte.
Quellen: Motley Fool RSS, Seeking Alpha (öffentliche Seiten), Yahoo Finance.
CEOs verraten oft mehr durch Wortwahl und Ton als durch Zahlen.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import List, Dict
import requests
from system.http import http_get
from email.utils import parsedate_to_datetime

_MOTLEY_RSS = "https://www.fool.com/earnings-call-transcripts/rss.xml"
_SEEKING_RSS = "https://seekingalpha.com/tag/earnings/rss.xml"

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}


class EarningsTranscriptCollector:
    """Sammelt Earnings-Call-Transkripte und -Zusammenfassungen."""

    def collect(self, ticker: str, lookback_days: int = 30) -> List[Dict]:
        results = []
        results += self._collect_motley_fool(ticker, lookback_days)
        results += self._collect_seeking_alpha(ticker, lookback_days)
        return results

    def _collect_motley_fool(self, ticker: str, lookback_days: int) -> List[Dict]:
        results = []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
        try:
            resp = http_get(_MOTLEY_RSS, headers=_HEADERS, timeout=12)
            if resp.status_code != 200:
                return []
            root = ET.fromstring(resp.content)
            for item in root.iter("item"):
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link") or "").strip()
                summary = (item.findtext("description") or "").strip()
                pub     = item.findtext("pubDate") or ""

                # Filter für Ticker-Relevanz
                search_text = (title + " " + summary).upper()
                if ticker.upper() not in search_text and "TRANSCRIPT" not in title.upper():
                    continue

                try:
                    pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pub_dt = datetime.now(timezone.utc).replace(tzinfo=None)

                results.append({
                    "source":       "Earnings Call Transcript (Motley Fool)",
                    "ticker":       ticker,
                    "title":        title,
                    "text":         summary[:800] if summary else title,
                    "url":          link,
                    "published_at": pub_dt.isoformat(),
                    "priority":     "HIGH",
                })
        except Exception:
            pass
        return results

    def _collect_seeking_alpha(self, ticker: str, lookback_days: int) -> List[Dict]:
        results = []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
        try:
            resp = http_get(_SEEKING_RSS, headers=_HEADERS, timeout=12)
            if resp.status_code != 200:
                return []
            root = ET.fromstring(resp.content)
            for item in root.iter("item"):
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link") or "").strip()
                summary = (item.findtext("description") or "").strip()
                pub     = item.findtext("pubDate") or ""

                if ticker.upper() not in (title + summary).upper():
                    continue

                try:
                    pub_dt = parsedate_to_datetime(pub).replace(tzinfo=None)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pub_dt = datetime.now(timezone.utc).replace(tzinfo=None)

                results.append({
                    "source":       "Earnings Transcript (Seeking Alpha)",
                    "ticker":       ticker,
                    "title":        title,
                    "text":         summary[:800],
                    "url":          link,
                    "published_at": pub_dt.isoformat(),
                    "priority":     "HIGH",
                })
        except Exception:
            pass
        return results
