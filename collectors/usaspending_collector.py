"""
USASpending Collector
Ruft Bundesaufträge (Federal Contracts) von usaspending.gov ab.

Hintergrund:
  - usaspending.gov ist die offizielle US-Regierungsseite für Ausgabentransparenz.
  - Wenn ein Unternehmen einen großen Bundesauftrag erhält, ist das ein starkes
    BULLISCHES Signal – gesicherter Umsatz, oft über mehrere Jahre.
  - Besonders relevant für: Defense (MSFT, AMZN, GOOGL für Cloud-Verträge),
    Pharma, IT-Services, Infrastruktur.

API: https://api.usaspending.gov – kostenlos, kein API-Key erforderlich.
"""

import requests
from system.http import http_post
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import yfinance as yf

_BASE_URL = "https://api.usaspending.gov/api/v2"
_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "StockSentimentBot/1.0 (educational use)",
}
_TIMEOUT = 20

# Award type codes: A=BPA Call, B=Purchase Order, C=Delivery Order, D=Definitive Contract
_CONTRACT_TYPES = ["A", "B", "C", "D"]


class USASpendingCollector:
    """
    Fetches recent US federal contract awards for a given stock ticker.
    Uses yfinance to resolve ticker → company name, then queries usaspending.gov.
    """

    def __init__(self, lookback_days: int = 180, min_award_usd: float = 1_000_000):
        self.lookback_days = lookback_days
        self.min_award_usd = min_award_usd
        self._cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        self._today = datetime.utcnow().strftime("%Y-%m-%d")

    def collect(self, ticker: str) -> List[Dict]:
        company_name = self._resolve_company_name(ticker)
        if not company_name:
            return []

        awards = self._fetch_awards(company_name)
        return [self._to_news_item(ticker, company_name, award) for award in awards]

    # ── Company name lookup ───────────────────────────────────────────────────

    def _resolve_company_name(self, ticker: str) -> Optional[str]:
        try:
            info = yf.Ticker(ticker).info
            # Prefer longName, fall back to shortName
            name = info.get("longName") or info.get("shortName") or ""
            # Strip common legal suffixes that confuse the search
            for suffix in [", Inc.", " Inc.", ", LLC", " LLC", " Corp.", ", Corp.",
                           " Corporation", " Limited", " Ltd.", " Holdings"]:
                name = name.replace(suffix, "")
            return name.strip() or None
        except Exception:
            return None

    # ── API call ──────────────────────────────────────────────────────────────

    def _fetch_awards(self, company_name: str) -> List[Dict]:
        payload = {
            "filters": {
                "recipient_search_text": [company_name],
                "award_type_codes": _CONTRACT_TYPES,
                "time_period": [
                    {"start_date": self._cutoff, "end_date": self._today}
                ],
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Description",
                "Start Date",
                "Award Type",
                "Awarding Agency",
                "Place of Performance State Code",
            ],
            "sort": "Award Amount",
            "order": "desc",
            "limit": 15,
            "page": 1,
        }
        try:
            resp = http_post(
                f"{_BASE_URL}/search/spending_by_award/",
                json=payload,
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            # Filter by minimum award size
            return [
                r for r in results
                if (r.get("Award Amount") or 0) >= self.min_award_usd
            ]
        except Exception:
            return []

    # ── Format as news item ───────────────────────────────────────────────────

    def _to_news_item(self, ticker: str, company_name: str, award: Dict) -> Dict:
        amount_raw = award.get("Award Amount") or 0
        amount_fmt = self._fmt_usd(amount_raw)
        agency = award.get("Awarding Agency") or "Unbekannte Behörde"
        description = (award.get("Description") or "Kein Beschreibung").strip().capitalize()
        start_date = (award.get("Start Date") or "")[:10]
        award_type = award.get("Award Type") or ""
        recipient = award.get("Recipient Name") or company_name

        signal = "STARK BULLISCH" if amount_raw >= 100_000_000 else "BULLISCH"

        return {
            "source": "USASpending-Contract",
            "ticker": ticker,
            "title": (
                f"{company_name} erhält Bundesauftrag von {agency} "
                f"– Volumen: {amount_fmt}"
            ),
            "text": (
                f"Empfänger: {recipient}. "
                f"Auftraggeber: {agency}. "
                f"Auftragsvolumen: {amount_fmt}. "
                f"Auftragsbeschreibung: {description}. "
                f"Vertragsbeginn: {start_date}. Vertragstyp: {award_type}. "
                f"Signal: {signal}. "
                f"Bundesaufträge sichern Umsatz über mehrere Jahre und reduzieren "
                f"Erlösrisiko – besonders relevant für Defense, Cloud und IT-Services."
            ),
            "published_at": start_date,
            "url": f"https://www.usaspending.gov/search/?query={company_name.replace(' ', '+')}",
            "award_amount_usd": amount_raw,
            "awarding_agency": agency,
            "contract_type": award_type,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_usd(amount: float) -> str:
        if amount >= 1_000_000_000:
            return f"${amount / 1_000_000_000:.1f}B"
        if amount >= 1_000_000:
            return f"${amount / 1_000_000:.1f}M"
        if amount >= 1_000:
            return f"${amount / 1_000:.0f}K"
        return f"${amount:.0f}"
