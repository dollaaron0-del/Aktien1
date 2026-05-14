"""
JobListingsCollector – analysiert Stellenausschreibungen als Unternehmenssignal.
Massenentlassungen → Restrukturierung/Kostensenkung (kurzfristig bullish?).
Masseneinstellungen in KI → Wachstumsinvestition (bullish langfristig).
Quellen: LinkedIn RSS (inoffiziell), Indeed RSS, USAJobs für Behörden.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from email.utils import parsedate_to_datetime

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

# Stellentypen die auf strategische Investitionen hinweisen
_STRATEGIC_KEYWORDS = [
    "AI", "artificial intelligence", "machine learning", "data center",
    "semiconductor", "quantum", "autonomous", "self-driving",
    "chief", "VP", "vice president", "director",
]
_LAYOFF_KEYWORDS = ["layoff", "reduction", "restructur", "severance", "position eliminated"]


class JobListingsCollector:
    """Analysiert Jobausschreibungen und Entlassungsmeldungen."""

    def collect(self, ticker: str, lookback_days: int = 14) -> List[Dict]:
        results = []
        results += self._collect_layoff_rss(ticker, lookback_days)
        results += self._collect_indeed_rss(ticker, lookback_days)
        return results

    def _collect_layoff_rss(self, ticker: str, lookback_days: int) -> List[Dict]:
        """Layoffs.fyi RSS – Entlassungsmeldungen aus Tech."""
        results = []
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)
        try:
            resp = requests.get(
                "https://layoffs.fyi/feed/",
                headers=_HEADERS, timeout=10
            )
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
                    pub_dt = datetime.utcnow()

                # Extrahiere Anzahl falls vorhanden
                count_match = re.search(r"(\d[\d,]+)\s*(?:employees?|workers?|jobs?|people)", summary, re.I)
                count_str = f" ({count_match.group(0)})" if count_match else ""

                results.append({
                    "source":       "Layoffs.fyi",
                    "ticker":       ticker,
                    "title":        f"Entlassungen: {title[:80]}{count_str}",
                    "text":         (
                        f"Entlassungsmeldung für {ticker}: {title}. "
                        f"{summary[:400] if summary else ''}"
                    ),
                    "url":          link,
                    "published_at": pub_dt.isoformat(),
                    "priority":     "HIGH",
                })
        except Exception:
            pass
        return results

    def _collect_indeed_rss(self, ticker: str, lookback_days: int) -> List[Dict]:
        """Indeed RSS für strategische Stellenausschreibungen."""
        results = []
        cutoff = datetime.utcnow() - timedelta(days=lookback_days)

        # Suche nach Unternehmensname (Ticker als Proxy)
        try:
            url = f"https://www.indeed.com/rss?q={ticker}&sort=date&fromage={lookback_days}"
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            if resp.status_code != 200:
                return []

            root = ET.fromstring(resp.content)
            strategic_jobs = []

            for item in root.iter("item"):
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link") or "").strip()
                summary = (item.findtext("description") or "").strip()

                # Nur strategisch relevante Jobs
                combined = (title + " " + summary).lower()
                is_strategic = any(kw.lower() in combined for kw in _STRATEGIC_KEYWORDS)
                if not is_strategic:
                    continue

                strategic_jobs.append({"title": title, "link": link, "summary": summary})
                if len(strategic_jobs) >= 3:
                    break

            if strategic_jobs:
                titles_list = "; ".join(j["title"] for j in strategic_jobs[:3])
                results.append({
                    "source":       "Indeed Job Listings",
                    "ticker":       ticker,
                    "title":        f"Strategische Neueinstellungen {ticker}: {len(strategic_jobs)} relevante Jobs",
                    "text":         (
                        f"{ticker} sucht aktiv nach strategischen Positionen: {titles_list}. "
                        f"Massenhiring in Wachstumsbereichen ist ein positives Unternehmensignal."
                    ),
                    "url":          f"https://www.indeed.com/jobs?q={ticker}&sort=date",
                    "published_at": datetime.utcnow().isoformat(),
                    "priority":     "LOW",
                })
        except Exception:
            pass
        return results
