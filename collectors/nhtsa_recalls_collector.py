"""
collectors/nhtsa_recalls_collector.py – Auto-Rückrufe (NHTSA).

Fahrzeug-Rückrufe bewegen Automobilwerte (Kosten, Reputation, Absatzrisiko).
Quelle: NHTSA Recalls API (frei, kein Key). Da die API Rückrufe je Make+Modelljahr
liefert (nicht je Börsenticker), mappen wir die großen gelisteten Hersteller auf
ihre Marken. Unbekannte Ticker → [] (kein blinder Call).

Fail-safe → []. Cache 24h/Ticker.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, List

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

# Börsenticker → NHTSA-Marken (nur die großen, klar zuordenbaren Hersteller)
_TICKER_MAKES: Dict[str, List[str]] = {
    "TSLA": ["TESLA"],
    "F":    ["FORD", "LINCOLN"],
    "GM":   ["CHEVROLET", "GMC", "BUICK", "CADILLAC"],
    "STLA": ["JEEP", "RAM", "DODGE", "CHRYSLER", "FIAT"],
    "TM":   ["TOYOTA", "LEXUS"],
    "HMC":  ["HONDA", "ACURA"],
    "RIVN": ["RIVIAN"],
    "LCID": ["LUCID"],
    "NSANY": ["NISSAN", "INFINITI"],
    "VWAGY": ["VOLKSWAGEN", "AUDI", "PORSCHE"],
}
_MODELS_API = "https://api.nhtsa.gov/products/vehicle/models"   # Modelle MIT Rückruf
_RECALLS_API = "https://api.nhtsa.gov/recalls/recallsByVehicle"  # braucht make+model+year
_MAX_MODELS = 14            # Call-Deckel je Ticker
_CACHE_TTL_S = 24 * 3600


class NHTSARecallsCollector:
    def __init__(self, lookback_days: int = 30):
        self.lookback_days = lookback_days
        self._cache: Dict[str, tuple] = {}

    def collect(self, ticker: str) -> List[Dict]:
        makes = _TICKER_MAKES.get(ticker.upper())
        if not makes:
            return []
        cached = self._cache.get(ticker)
        if cached and (time.time() - cached[1]) < _CACHE_TTL_S:
            return cached[0]
        try:
            items = self._query(ticker, makes)
        except Exception as e:
            log.debug("[%s] NHTSA: %s", ticker, e)
            items = []
        self._cache[ticker] = (items, time.time())
        return items

    def _query(self, ticker: str, makes: List[str]) -> List[Dict]:
        cutoff = datetime.utcnow().date() - timedelta(days=self.lookback_days)
        years = [datetime.utcnow().year, datetime.utcnow().year + 1]
        seen: set = set()
        out: List[Dict] = []
        budget = _MAX_MODELS
        for make in makes:
            for year in years:
                if budget <= 0:
                    break
                # Schritt 1: nur Modelle, die überhaupt einen Rückruf haben
                mresp = http_get(_MODELS_API,
                                 params={"modelYear": year, "make": make, "issueType": "r"},
                                 timeout=15)
                if mresp.status_code != 200:
                    continue
                models = {m.get("model") for m in (mresp.json() or {}).get("results", []) or []}
                for model in filter(None, models):
                    if budget <= 0:
                        break
                    budget -= 1
                    # Schritt 2: die konkreten Rückruf-Kampagnen
                    resp = http_get(_RECALLS_API,
                                    params={"make": make, "model": model, "modelYear": year},
                                    timeout=15)
                    if resp.status_code != 200:
                        continue
                    for r in (resp.json() or {}).get("results", []) or []:
                        camp = r.get("NHTSACampaignNumber") or ""
                        if not camp or camp in seen:
                            continue
                        rd = self._parse_date(r.get("ReportReceivedDate", ""))
                        if rd is None or rd < cutoff:
                            continue
                        seen.add(camp)
                        comp = (r.get("Component", "") or "")[:80]
                        summ = (r.get("Summary", "") or "")[:200]
                        out.append({
                        "source": "NHTSA",
                        "ticker": ticker,
                        "title": f"NHTSA-Rückruf {make} ({comp}) – {rd}",
                        "text": (f"Fahrzeug-Rückruf {make}, Kampagne {camp}. "
                                 f"Komponente: {comp}. {summ}. Rückrufe sind Kosten-/"
                                 f"Reputationsrisiko für den Hersteller."),
                        "url": "https://www.nhtsa.gov/recalls",
                        "published_at": str(rd),
                        "priority": "NORMAL",
                    })
        return out

    @staticmethod
    def _parse_date(s: str):
        # NHTSA liefert teils '/Date(...)/' oder 'DD/MM/YYYY' – beides abfangen.
        try:
            if s.startswith("/Date("):
                ms = int(s[6:].split(")")[0].split("+")[0].split("-")[0])
                return datetime.utcfromtimestamp(ms / 1000).date()
            for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s[:10], fmt).date()
                except ValueError:
                    continue
        except Exception:
            return None
        return None
