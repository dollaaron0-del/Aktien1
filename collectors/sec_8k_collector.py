"""
SEC8KCollector – lädt aktuelle 8-K Meldungen von SEC EDGAR RSS in Echtzeit.
8-K = außerordentliche Ereignisse: CEO-Rücktritt, Übernahmen, Klagen,
      Restrukturierungen, Dividendenänderungen, etc.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict
import requests


_EDGAR_RSS = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&dateRange=custom&startdt={start}&enddt={end}&forms=8-K"
_EDGAR_FULL = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=8-K&hits.hits._source=period_of_report,display_names,file_date,entity_name,form_type,biz_location,inc_states"

# 8-K Items die besonders wichtig sind
_HIGH_IMPACT_ITEMS = {
    "1.01": "Material Definitive Agreement",
    "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy",
    "2.01": "Completion of Acquisition",
    "2.02": "Results of Operations (Earnings)",
    "2.05": "Costs Associated with Exit Activities",
    "2.06": "Material Impairments",
    "3.01": "Delisting Notice",
    "4.01": "Auditor Change",
    "4.02": "Non-Reliance on Financial Statements",
    "5.01": "Changes in Control",
    "5.02": "Director/Officer Changes",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
}


class SEC8KCollector:
    """Sammelt 8-K Meldungen von SEC EDGAR (kostenlos, keine API-Key nötig)."""

    HEADERS = {"User-Agent": "StockSentimentBot research@example.com"}

    def collect(self, ticker: str, lookback_days: int = 14) -> List[Dict]:
        results = []
        start = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end   = datetime.utcnow().strftime("%Y-%m-%d")

        try:
            url = f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=8-K&dateRange=custom&startdt={start}&enddt={end}"
            resp = requests.get(url, headers=self.HEADERS, timeout=15)
            if resp.status_code != 200:
                return []
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])

            for hit in hits[:10]:
                src = hit.get("_source", {})
                file_date  = src.get("file_date", "")
                entity     = src.get("entity_name", ticker)
                description = src.get("period_of_report", "")

                # Try to get filing detail
                filing_url = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={ticker}&type=8-K&dateb=&owner=include&count=5&search_text="

                results.append({
                    "source":       "SEC 8-K",
                    "ticker":       ticker,
                    "title":        f"8-K Filing: {entity} ({file_date})",
                    "text":         f"SEC Form 8-K filed by {entity} on {file_date}. {description}",
                    "url":          f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=8-K",
                    "published_at": file_date,
                    "priority":     "HIGH",
                })
        except Exception:
            pass

        # Fallback: EDGAR full-text RSS
        if not results:
            results = self._collect_via_rss(ticker, lookback_days)

        return results

    def _collect_via_rss(self, ticker: str, lookback_days: int) -> List[Dict]:
        results = []
        try:
            # CIK lookup
            cik_url = f"https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={ticker}&type=8-K&dateb=&owner=include&count=10&search_text=&action=getcompany&output=atom"
            resp = requests.get(cik_url, headers=self.HEADERS, timeout=15)
            if resp.status_code != 200:
                return []

            root = ET.fromstring(resp.content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            cutoff = datetime.utcnow() - timedelta(days=lookback_days)

            for entry in root.findall("atom:entry", ns)[:8]:
                title   = (entry.findtext("atom:title", "", ns) or "").strip()
                link_el = entry.find("atom:link", ns)
                url     = link_el.get("href", "") if link_el is not None else ""
                updated = entry.findtext("atom:updated", "", ns) or ""
                summary = entry.findtext("atom:summary", "", ns) or ""

                try:
                    pub = datetime.strptime(updated[:19], "%Y-%m-%dT%H:%M:%S")
                    if pub < cutoff:
                        continue
                except Exception:
                    pub = datetime.utcnow()

                results.append({
                    "source":       "SEC 8-K",
                    "ticker":       ticker,
                    "title":        title or f"SEC 8-K Filing ({ticker})",
                    "text":         summary or title,
                    "url":          url,
                    "published_at": pub.isoformat(),
                    "priority":     "HIGH",
                })
        except Exception:
            pass
        return results
