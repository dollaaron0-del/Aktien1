"""
InstitutionalCollector – lädt 13F-Filings von SEC EDGAR.
13F = Quartalsbericht von Hedge-Fonds & institutionellen Investoren (>$100M AUM).
Zeigt welche Aktien Berkshire, Bridgewater, Renaissance, etc. kaufen/verkaufen.

Delay: 45 Tage nach Quartalsende (also nicht in Echtzeit).
Trotzdem wertvoller Kontext: "Smart Money" kauft oder verkauft diese Aktie.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict
import requests
from system.http import http_get

# Bekannte institutionelle Investoren (CIK → Name)
_KNOWN_INSTITUTIONS = {
    "0001067983": "Berkshire Hathaway (Buffett)",
    "0001350694": "Bridgewater Associates",
    "0001037389": "Renaissance Technologies",
    "0000102909": "Vanguard Group",
    "0000884217": "BlackRock",
    "0001336528": "Pershing Square (Ackman)",
    "0001603466": "Duquesne (Druckenmiller)",
}

_HEADERS = {"User-Agent": "StockSentimentBot research@example.com"}
_EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=13F-HR&dateRange=custom&startdt={start}&enddt={end}"


class InstitutionalCollector:
    """Sammelt 13F-Filings – was kaufen/verkaufen große Investoren?"""

    def collect(self, ticker: str, lookback_days: int = 120) -> List[Dict]:
        results = []
        start = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        end   = datetime.utcnow().strftime("%Y-%m-%d")

        try:
            url = _EDGAR_SEARCH.format(ticker=ticker, start=start, end=end)
            resp = http_get(url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                return []

            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])

            for hit in hits[:8]:
                src       = hit.get("_source", {})
                file_date = src.get("file_date", "")
                names     = src.get("display_names", [])
                entity    = names[0] if names else "Unbekannte Institution"

                # Check if it's a known top investor
                is_notable = any(
                    known in entity
                    for known in ["Berkshire", "Bridgewater", "Renaissance",
                                  "Vanguard", "BlackRock", "Pershing", "Druckenmiller",
                                  "Soros", "Tiger", "Baupost"]
                )
                priority = "HIGH" if is_notable else "MEDIUM"
                notable_tag = " ⭐ BEKANNTER INVESTOR" if is_notable else ""

                results.append({
                    "source":       "13F Institutional Filing",
                    "ticker":       ticker,
                    "title":        f"13F Filing: {entity} hält {ticker}-Position{notable_tag} ({file_date})",
                    "text":         (
                        f"Institutioneller Investor {entity} hat {ticker} in seinem "
                        f"13F-Quarterly-Report gemeldet (Filing: {file_date}). "
                        f"13F-Filings zeigen Positionen von Fonds mit >$100M AUM."
                        f"{notable_tag}"
                    ),
                    "url":          f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=13F-HR",
                    "published_at": file_date,
                    "priority":     priority,
                })
        except Exception:
            pass

        return results

    def get_notable_holders(self, ticker: str) -> List[Dict]:
        """Gibt nur bekannte Top-Investoren zurück."""
        all_results = self.collect(ticker)
        return [r for r in all_results if r.get("priority") == "HIGH"]
