"""
collectors/openfda_collector.py – FDA-Rückrufe (Drug/Device/Food).

Ergänzt bewusst den FDA-*Kalender* (clinicaltrials, künftige Readouts): hier geht
es um bereits ERFOLGTE Enforcement-Aktionen = Produktrückrufe. Ein Class-I-Recall
kann Pharma/MedTech/Consumer kurzfristig stark bewegen.

Quelle: openFDA Enforcement-Endpunkte (frei, kein Key, gedrosselt). Nur für
Healthcare/Consumer-Ticker (Sektor-Gate spart Calls). Sucht im recalling_firm
nach dem Firmennamen, meldet Rückrufe innerhalb des Lookbacks. Fail-safe → [].
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from logger import get_logger
from system.http import http_get
from collectors._company import company_name, matches_sector

log = get_logger(__name__)

_ENDPOINTS = {
    "Arzneimittel": "https://api.fda.gov/drug/enforcement.json",
    "Medizinprodukt": "https://api.fda.gov/device/enforcement.json",
    "Lebensmittel": "https://api.fda.gov/food/enforcement.json",
}
_SECTOR_HINTS = ("healthcare", "pharmaceutical", "biotech", "drug", "medical",
                 "device", "consumer", "food", "beverage", "life sciences", "health")
_CLASS_PRIORITY = {"Class I": "HIGH", "Class II": "NORMAL", "Class III": "LOW"}
_CACHE_TTL_S = 24 * 3600


class OpenFDACollector:
    def __init__(self, lookback_days: int = 30):
        self.lookback_days = lookback_days
        self._cache: Dict[str, tuple] = {}

    def collect(self, ticker: str) -> List[Dict]:
        cached = self._cache.get(ticker)
        if cached and (time.time() - cached[1]) < _CACHE_TTL_S:
            return cached[0]
        try:
            items = [] if not matches_sector(ticker, _SECTOR_HINTS) else self._query(ticker)
        except Exception as e:
            log.debug("[%s] openFDA: %s", ticker, e)
            items = []
        self._cache[ticker] = (items, time.time())
        return items

    def _query(self, ticker: str) -> List[Dict]:
        name = company_name(ticker)
        if not name:
            return []
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None).date() - timedelta(days=self.lookback_days)
        firm = name.split()[0]  # erstes Wort als robuster Firm-Treffer (z.B. "Pfizer")
        out: List[Dict] = []
        for label, url in _ENDPOINTS.items():
            params = {
                "search": f'recalling_firm:"{firm}"',
                "sort": "report_date:desc",
                "limit": 5,
            }
            resp = http_get(url, params=params, timeout=15)
            if resp.status_code != 200:
                continue
            for r in (resp.json() or {}).get("results", []) or []:
                rd = self._parse_date(r.get("report_date", ""))
                if rd is None or rd < cutoff:
                    continue
                if firm.lower() not in (r.get("recalling_firm", "") or "").lower():
                    continue
                cls = r.get("classification", "") or "?"
                reason = (r.get("reason_for_recall", "") or "")[:200]
                out.append({
                    "source": f"openFDA/{label}",
                    "ticker": ticker,
                    "title": f"FDA-Rückruf ({cls}) {label}: {name} – {rd}",
                    "text": (f"openFDA Enforcement: {r.get('recalling_firm','')} ruft "
                             f"zurück. Klassifikation {cls}. Grund: {reason}. "
                             f"Class-I-Rückrufe sind potenziell starke Negativ-Impulse."),
                    "url": "https://www.fda.gov/safety/recalls-market-withdrawals-safety-alerts",
                    "published_at": str(rd),
                    "priority": _CLASS_PRIORITY.get(cls, "NORMAL"),
                })
        return out

    @staticmethod
    def _parse_date(s: str):
        try:
            return datetime.strptime(s[:8], "%Y%m%d").date()
        except (ValueError, TypeError):
            return None
