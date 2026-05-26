"""
Sector Sampler – erkundet pro Analyse-Zyklus einen neuen Sektor.

Rotiert durch alle Sektoren damit der Bot langfristig ein breites Netz aufbaut.
Gibt je Zyklus 2 Stichproben-Ticker zurück, die nicht in der aktuellen Watchlist sind.
"""
import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

_STATE_FILE = os.path.join("data", "sector_sampler_state.json")
_SAMPLE_PER_CYCLE = int(os.getenv("SECTOR_SAMPLE_COUNT", "4"))

# Sektoren mit repräsentativen Stichproben-Tickern
_SECTORS: List[Dict] = [
    {"name": "Halbleiter",         "tickers": ["NVDA", "AMD", "MU", "AMAT", "LRCX", "KLAC", "TSM", "QCOM"]},
    {"name": "Biotechnologie",     "tickers": ["MRNA", "BNTX", "REGN", "VRTX", "BIIB", "ILMN", "ALNY", "EXAS"]},
    {"name": "Rüstung & Defense",  "tickers": ["LMT", "RTX", "NOC", "GD", "HII", "RHM.DE", "AIR.PA"]},
    {"name": "Cloud & SaaS",       "tickers": ["CRM", "NOW", "SNOW", "MDB", "DDOG", "ZS", "NET", "HUBS"]},
    {"name": "Erneuerbare Energie","tickers": ["ENPH", "FSLR", "NEE", "BEP", "RUN", "SEDG", "PLUG", "ORSTED.CO"]},
    {"name": "Finanzen & Banken",  "tickers": ["GS", "MS", "BAC", "BRK-B", "AXP", "DB.DE", "BNP.PA"]},
    {"name": "E-Commerce",         "tickers": ["SHOP", "MELI", "SE", "PDD", "ETSY", "EBAY", "JD"]},
    {"name": "Automobil & EV",     "tickers": ["TSLA", "NIO", "RIVN", "BMW.DE", "MBG.DE", "VOW3.DE", "STLA"]},
    {"name": "Gesundheit & Pharma","tickers": ["LLY", "NVO", "ABBV", "MRK", "BMY", "AZN.L", "ROG.SW"]},
    {"name": "Rohstoffe & Mining", "tickers": ["BHP", "RIO", "FCX", "NEM", "VALE", "GOLD", "AA", "ALB"]},
    {"name": "KI & Data Centers",  "tickers": ["SMCI", "DELL", "AVGO", "CDNS", "ARM", "MRVL", "VRT"]},
    {"name": "Industrie",          "tickers": ["CAT", "DE", "HON", "SIE.DE", "ABB.SW", "ROK", "EMR"]},
    {"name": "Konsumgüter",        "tickers": ["LVMH", "MC.PA", "EL", "ULTA", "TJX", "NKE", "LULU"]},
    {"name": "Logistik & Transport","tickers": ["UPS", "FDX", "XPO", "GXO", "DSV.CO", "DPW.DE"]},
    {"name": "Cybersecurity",      "tickers": ["CRWD", "PANW", "FTNT", "S", "CYBR", "OKTA", "RPM"]},
]


class SectorSampler:
    """
    Gibt pro Zyklus (sector_name, [ticker1, ticker2]) zurück.
    Rotiert automatisch durch alle Sektoren.
    """

    def __init__(self, state_path: str = _STATE_FILE):
        self._path = state_path
        self._state = self._load()

    def get_sample(self, exclude: Optional[List[str]] = None) -> Tuple[str, List[str]]:
        """
        Gibt (Sektor-Name, Ticker-Liste) zurück.
        exclude: Ticker die bereits in der Watchlist sind → werden übersprungen.
        Rotiert danach zum nächsten Sektor.
        """
        exclude_set = {t.upper() for t in (exclude or [])}
        idx = self._state.get("sector_index", 0) % len(_SECTORS)
        sector = _SECTORS[idx]

        candidates = [t for t in sector["tickers"] if t.upper() not in exclude_set]
        ticker_idx = self._state.get("ticker_idx", 0) % max(1, len(candidates))

        sample: List[str] = []
        for i in range(len(candidates)):
            if len(sample) >= _SAMPLE_PER_CYCLE:
                break
            t = candidates[(ticker_idx + i) % len(candidates)]
            sample.append(t)

        # Advance state: nach allen Kandidaten eines Sektors → nächster Sektor
        next_ticker_idx = ticker_idx + _SAMPLE_PER_CYCLE
        if next_ticker_idx >= len(candidates):
            next_sector_idx = (idx + 1) % len(_SECTORS)
            next_ticker_idx = 0
        else:
            next_sector_idx = idx

        self._state = {
            "sector_index": next_sector_idx,
            "ticker_idx": next_ticker_idx,
            "last_sector": sector["name"],
            "last_run": datetime.utcnow().isoformat(),
        }
        self._save()
        return sector["name"], sample

    def current_sector_name(self) -> str:
        idx = self._state.get("sector_index", 0) % len(_SECTORS)
        return _SECTORS[idx]["name"]

    def _load(self) -> Dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self) -> None:
        dirpath = os.path.dirname(self._path) or "."
        os.makedirs(dirpath, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp, self._path)
        except Exception as e:
            log.warning("SectorSampler: Speichern fehlgeschlagen: %s", e)
