"""
SectorRotation – erkennt wohin Geld im Markt fließt.
Vergleicht Performance der 11 S&P500-Sektoren und identifiziert:
  - Starke Sektoren (Kapitalzufluss)
  - Schwache Sektoren (Kapitalabfluss)
  - Risikobereitschaft (Risk-On vs Risk-Off)

ETFs:
  XLK=Tech, XLF=Finanz, XLE=Energie, XLV=Gesundheit, XLI=Industrie,
  XLY=Konsum (zyklisch), XLP=Konsum (defensiv), XLU=Versorger,
  XLB=Rohstoffe, XLRE=Immobilien, XLC=Kommunikation
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple

import yfinance as yf

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "sector_rotation.json")
_CACHE_TTL_HOURS = 4

_SECTOR_ETFS = {
    "XLK":  "Technologie",
    "XLF":  "Finanzen",
    "XLE":  "Energie",
    "XLV":  "Gesundheit",
    "XLI":  "Industrie",
    "XLY":  "Konsum (zyklisch)",
    "XLP":  "Konsum (defensiv)",
    "XLU":  "Versorger",
    "XLB":  "Rohstoffe",
    "XLRE": "Immobilien",
    "XLC":  "Kommunikation",
}

# Risk-On-Sektoren: steigen wenn Anleger risikofreudig sind
_RISK_ON  = {"XLK", "XLY", "XLF", "XLC"}
# Risk-Off-Sektoren: steigen wenn Anleger defensiv werden
_RISK_OFF = {"XLP", "XLU", "XLV", "XLRE"}

# Ticker → Sektor-Zugehörigkeit (häufige Watchlist-Aktien)
_TICKER_SECTOR = {
    "AAPL":"XLK", "MSFT":"XLK", "NVDA":"XLK", "AMD":"XLK", "INTC":"XLK",
    "GOOGL":"XLC", "META":"XLC", "NFLX":"XLC", "DIS":"XLC",
    "AMZN":"XLY", "TSLA":"XLY", "NKE":"XLY", "MCD":"XLY",
    "JPM":"XLF", "BAC":"XLF", "GS":"XLF", "MS":"XLF", "V":"XLF", "MA":"XLF",
    "XOM":"XLE", "CVX":"XLE", "COP":"XLE",
    "JNJ":"XLV", "PFE":"XLV", "MRK":"XLV", "ABBV":"XLV", "LLY":"XLV",
    "CAT":"XLI", "BA":"XLI", "GE":"XLI", "RTX":"XLI",
    "WMT":"XLP", "COST":"XLP",
}


class SectorSnapshot:
    def __init__(self, etf: str, name: str, perf_1w: float, perf_1m: float, perf_3m: float):
        self.etf = etf
        self.name = name
        self.perf_1w = perf_1w
        self.perf_1m = perf_1m
        self.perf_3m = perf_3m

    @property
    def momentum_score(self) -> float:
        """Gewichtetes Momentum: 1W×0.5 + 1M×0.3 + 3M×0.2"""
        return self.perf_1w * 0.5 + self.perf_1m * 0.3 + self.perf_3m * 0.2

    def to_dict(self) -> Dict:
        return {
            "etf": self.etf, "name": self.name,
            "perf_1w": round(self.perf_1w, 2),
            "perf_1m": round(self.perf_1m, 2),
            "perf_3m": round(self.perf_3m, 2),
            "momentum": round(self.momentum_score, 2),
        }


class SectorRotation:
    """Erkennt Sektor-Rotation und gibt Handlungsempfehlungen."""

    def __init__(self):
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)

    def get_snapshot(self) -> List[SectorSnapshot]:
        """Gibt aktuellen Sektor-Überblick zurück (gecacht)."""
        cached = self._load_cache()
        if cached is not None:
            return cached
        snaps = self._fetch()
        self._save_cache(snaps)
        return snaps

    def get_strong_sectors(self, top_n: int = 3) -> List[SectorSnapshot]:
        """Top-N Sektoren nach Momentum."""
        snaps = self.get_snapshot()
        return sorted(snaps, key=lambda x: x.momentum_score, reverse=True)[:top_n]

    def get_weak_sectors(self, bottom_n: int = 3) -> List[SectorSnapshot]:
        """Bottom-N Sektoren nach Momentum."""
        snaps = self.get_snapshot()
        return sorted(snaps, key=lambda x: x.momentum_score)[:bottom_n]

    def get_risk_mode(self) -> Tuple[str, float]:
        """
        Gibt (Modus, Score) zurück.
        Modus: "RISK_ON" | "RISK_OFF" | "NEUTRAL"
        Score: positiv = Risk-On, negativ = Risk-Off
        """
        snaps = {s.etf: s for s in self.get_snapshot()}
        risk_on_score  = sum(snaps[e].momentum_score for e in _RISK_ON  if e in snaps) / len(_RISK_ON)
        risk_off_score = sum(snaps[e].momentum_score for e in _RISK_OFF if e in snaps) / len(_RISK_OFF)
        diff = risk_on_score - risk_off_score
        if diff > 0.5:
            return "RISK_ON", diff
        elif diff < -0.5:
            return "RISK_OFF", diff
        return "NEUTRAL", diff

    def is_ticker_in_strong_sector(self, ticker: str) -> Tuple[bool, str]:
        """Prüft ob ein Ticker in einem der Top-3 Sektoren liegt."""
        sector_etf = _TICKER_SECTOR.get(ticker.upper())
        if not sector_etf:
            return True, ""  # unbekannt → nicht blockieren
        strong = {s.etf for s in self.get_strong_sectors(4)}
        weak   = {s.etf for s in self.get_weak_sectors(3)}
        if sector_etf in weak:
            sector_name = _SECTOR_ETFS.get(sector_etf, sector_etf)
            return False, f"Sektor {sector_name} ({sector_etf}) im Abfluss"
        return True, ""

    def get_position_size_modifier(self, ticker: str) -> float:
        """
        Gibt Multiplikator für Positionsgröße basierend auf Sektor-Stärke zurück.
        Starker Sektor = 1.1×, schwacher Sektor = 0.7×.
        """
        snaps = {s.etf: s for s in self.get_snapshot()}
        sector_etf = _TICKER_SECTOR.get(ticker.upper())
        if not sector_etf or sector_etf not in snaps:
            return 1.0
        score = snaps[sector_etf].momentum_score
        if score > 2.0:
            return 1.15
        elif score > 1.0:
            return 1.05
        elif score < -2.0:
            return 0.65
        elif score < -1.0:
            return 0.80
        return 1.0

    def summary(self) -> str:
        mode, score = self.get_risk_mode()
        strong = self.get_strong_sectors(3)
        weak   = self.get_weak_sectors(3)
        lines = [
            f"Marktmodus: {'🟢 RISK-ON' if mode == 'RISK_ON' else ('🔴 RISK-OFF' if mode == 'RISK_OFF' else '⚪ NEUTRAL')} (Score: {score:+.1f})",
            f"Starke Sektoren: {', '.join(s.name for s in strong)}",
            f"Schwache Sektoren: {', '.join(s.name for s in weak)}",
        ]
        return "\n".join(lines)

    # ── Intern ───────────────────────────────────────────────────────────────

    def _fetch(self) -> List[SectorSnapshot]:
        snaps = []
        tickers = list(_SECTOR_ETFS.keys())
        try:
            data = yf.download(tickers, period="3mo", progress=False, auto_adjust=True)["Close"]
            for etf in tickers:
                try:
                    prices = data[etf].dropna()
                    if len(prices) < 20:
                        continue
                    p_now  = float(prices.iloc[-1])
                    p_1w   = float(prices.iloc[-5])
                    p_1m   = float(prices.iloc[-21]) if len(prices) >= 21 else float(prices.iloc[0])
                    p_3m   = float(prices.iloc[0])
                    snaps.append(SectorSnapshot(
                        etf=etf,
                        name=_SECTOR_ETFS[etf],
                        perf_1w=(p_now - p_1w) / p_1w * 100,
                        perf_1m=(p_now - p_1m) / p_1m * 100,
                        perf_3m=(p_now - p_3m) / p_3m * 100,
                    ))
                except Exception:
                    continue
        except Exception:
            pass
        return snaps

    def _load_cache(self) -> Optional[List[SectorSnapshot]]:
        try:
            with open(_CACHE_FILE) as f:
                data = json.load(f)
            updated = datetime.fromisoformat(data["updated_at"])
            if (datetime.now(timezone.utc).replace(tzinfo=None) - updated).total_seconds() > _CACHE_TTL_HOURS * 3600:
                return None
            return [
                SectorSnapshot(d["etf"], d["name"], d["perf_1w"], d["perf_1m"], d["perf_3m"])
                for d in data["sectors"]
            ]
        except Exception:
            return None

    def _save_cache(self, snaps: List[SectorSnapshot]):
        import tempfile, os as _os
        data = {
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "sectors": [s.to_dict() for s in snaps],
        }
        _os.makedirs(_os.path.dirname(_CACHE_FILE), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=_os.path.dirname(_CACHE_FILE), suffix=".tmp", delete=False
        ) as tmp:
            json.dump(data, tmp, indent=2)
            tmp_path = tmp.name
        _os.replace(tmp_path, _CACHE_FILE)
