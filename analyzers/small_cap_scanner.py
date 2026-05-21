"""
Small-Cap Sektor-Follower Scanner

Sucht Small-Cap Aktien (Marktkapitalisierung 200 Mio – 2 Mrd USD) die von
einem aktuell laufenden Sektor-Trend profitieren könnten.

Aktivierung: manuell via  python main.py --small-cap-scan
Niemals automatisch im normalen Bot-Zyklus.

Kriterien:
  - Marktkapitalisierung: 200 Mio – 2 Mrd USD
  - Tagesvolumen: >= 300.000 Stück (Liquidität)
  - Kurs: 5 – 100 USD (keine Pennystocks, kein Large-Cap-Preis)
  - Momentum: Kurs > 20-Tage-Durchschnitt (Aufwärtstrend)
  - Sektor-Bezug: gleicher Sektor wie ein aktuell starker Index-ETF
  - Stop-Loss Empfehlung: 7% (strenger als normale Strategie)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional

import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

# Mindest- und Höchstgrenzen
_MIN_MARKET_CAP   = 200_000_000    # 200 Mio USD
_MAX_MARKET_CAP   = 2_000_000_000  # 2 Mrd USD
_MIN_VOLUME       = 300_000        # Stück/Tag
_MIN_PRICE        = 5.0            # USD
_MAX_PRICE        = 100.0          # USD
_STOP_LOSS_PCT    = 0.07           # 7% Stop-Loss Empfehlung
_TAKE_PROFIT_PCT  = 0.20           # 20% Take-Profit Empfehlung

# Sektor-ETFs als Referenz für Trend-Stärke
# Format: { ETF-Ticker: (Sektor-Name, [Small-Cap Kandidaten]) }
_SECTOR_MAP: Dict[str, tuple] = {
    "SMH":  ("Halbleiter",     ["ONTO", "AMBA", "FORM", "CAMT", "ACLS", "ICHR", "COHU", "MKSI"]),
    "XLK":  ("Technologie",    ["RAMP", "TASK", "SPSC", "FOUR", "JAMF", "PYCR", "DOCN", "RELY"]),
    "XLE":  ("Energie",        ["CIVI", "REX", "TALO", "MARPS", "SilverBow", "MTDR", "CTRA", "BATL"]),
    "XLF":  ("Finanzen",       ["COOP", "LPRO", "UWMC", "PFSI", "GHLD", "NMIH", "ESNT", "ACT"]),
    "XLV":  ("Healthcare",     ["ACAD", "TGTX", "ITCI", "NUVL", "PRAX", "KYMR", "VRTX", "RXST"]),
    "XLI":  ("Industrie",      ["KTOS", "BWXT", "TDW", "HAYW", "HLIO", "MYRG", "AAON", "PRIM"]),
    "ARKK": ("Disruptive Tech",["RXRX", "PACB", "TWLO", "BEAM", "TDOC", "EXAS", "IONS", "NTLA"]),
    "XBI":  ("Biotech",        ["FGEN", "FOLD", "ARWR", "ALNY", "KRYS", "RARE", "IMVT", "ZYME"]),
    "ICLN": ("Clean Energy",   ["PLUG", "FCEL", "ARRY", "NOVA", "MAXN", "HASI", "AZEK", "ENPH"]),
    "GDX":  ("Gold/Mining",    ["EQX", "GATO", "MAG", "WPM", "AEM", "SSRM", "PAAS", "CDE"]),
}


@dataclass
class SmallCapCandidate:
    ticker:          str
    name:            str
    sector_etf:      str
    sector_name:     str
    price:           float
    market_cap_m:    float       # in Millionen USD
    volume:          int
    change_1d_pct:   float
    change_5d_pct:   float
    above_ma20:      bool
    stop_loss:       float
    take_profit:     float
    score:           float       # 0–100
    reason:          str


@dataclass
class SmallCapScanResult:
    scan_date:       str
    trending_sectors: List[str]
    candidates:      List[SmallCapCandidate]
    skipped_count:   int
    scan_duration_s: float


class SmallCapScanner:
    """
    Scannt Small-Cap Aktien in aktuell trending Sektoren.
    Nur manuell via CLI aufrufbar — nicht im automatischen Bot-Zyklus.
    """

    def __init__(self, max_results: int = 10, min_sector_etf_gain: float = 1.0):
        self.max_results = max_results
        self.min_sector_etf_gain = min_sector_etf_gain  # ETF muss >= X% in 5 Tagen gestiegen sein

    def scan(self) -> SmallCapScanResult:
        import time
        t0 = time.monotonic()
        log.info("Small-Cap Scanner gestartet …")

        trending = self._find_trending_sectors()
        log.info("Trending Sektoren: %s", [s for s, _ in trending])

        candidates: List[SmallCapCandidate] = []
        skipped = 0

        for sector_etf, (sector_name, tickers) in _SECTOR_MAP.items():
            if not any(etf == sector_etf for etf, _ in trending):
                continue
            etf_gain = next(g for etf, g in trending if etf == sector_etf)
            for ticker in tickers:
                result = self._evaluate(ticker, sector_etf, sector_name, etf_gain)
                if result is None:
                    skipped += 1
                else:
                    candidates.append(result)

        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[:self.max_results]

        return SmallCapScanResult(
            scan_date=datetime.utcnow().isoformat(),
            trending_sectors=[s for etf, (s, _) in _SECTOR_MAP.items()
                              if any(e == etf for e, _ in trending)],
            candidates=candidates,
            skipped_count=skipped,
            scan_duration_s=round(time.monotonic() - t0, 1),
        )

    # ── Sektor-Trend Erkennung ────────────────────────────────────────────────

    def _find_trending_sectors(self) -> List[tuple]:
        """Gibt Liste von (ETF-Ticker, 5d-Gain%) für alle trending Sektor-ETFs zurück."""
        trending = []
        for etf in _SECTOR_MAP:
            try:
                hist = yf.Ticker(etf).history(period="10d")
                if len(hist) < 5:
                    continue
                gain_5d = (hist["Close"].iloc[-1] / hist["Close"].iloc[-5] - 1) * 100
                if gain_5d >= self.min_sector_etf_gain:
                    trending.append((etf, round(gain_5d, 2)))
                    log.debug("Trending ETF: %s +%.1f%%", etf, gain_5d)
            except Exception as e:
                log.debug("ETF %s: %s", etf, e)
        trending.sort(key=lambda x: x[1], reverse=True)
        return trending

    # ── Einzelaktie prüfen ────────────────────────────────────────────────────

    def _evaluate(
        self,
        ticker: str,
        sector_etf: str,
        sector_name: str,
        etf_gain_5d: float,
    ) -> Optional[SmallCapCandidate]:
        try:
            t = yf.Ticker(ticker)
            info = t.info
            hist = t.history(period="30d")

            if hist.empty or len(hist) < 6:
                return None

            price      = float(hist["Close"].iloc[-1])
            volume     = int(hist["Volume"].iloc[-1])
            market_cap = info.get("marketCap") or 0
            name       = info.get("shortName") or ticker

            # Harte Filter
            if not (_MIN_PRICE <= price <= _MAX_PRICE):
                return None
            if not (_MIN_MARKET_CAP <= market_cap <= _MAX_MARKET_CAP):
                return None
            if volume < _MIN_VOLUME:
                return None

            market_cap_m = round(market_cap / 1_000_000, 1)

            # Technische Indikatoren
            change_1d = round((hist["Close"].iloc[-1] / hist["Close"].iloc[-2] - 1) * 100, 2)
            change_5d = round((hist["Close"].iloc[-1] / hist["Close"].iloc[-6] - 1) * 100, 2)
            ma20      = float(hist["Close"].tail(20).mean())
            above_ma20 = price > ma20

            # Score berechnen (0–100)
            score = self._score(
                market_cap=market_cap,
                volume=volume,
                change_5d=change_5d,
                above_ma20=above_ma20,
                etf_gain_5d=etf_gain_5d,
            )

            stop_loss   = round(price * (1 - _STOP_LOSS_PCT), 2)
            take_profit = round(price * (1 + _TAKE_PROFIT_PCT), 2)

            reason_parts = []
            if above_ma20:
                reason_parts.append("Kurs über MA20")
            if change_5d > 0:
                reason_parts.append(f"+{change_5d:.1f}% in 5 Tagen")
            reason_parts.append(f"Sektor {sector_name} trending (+{etf_gain_5d:.1f}%)")

            return SmallCapCandidate(
                ticker=ticker,
                name=name,
                sector_etf=sector_etf,
                sector_name=sector_name,
                price=price,
                market_cap_m=market_cap_m,
                volume=volume,
                change_1d_pct=change_1d,
                change_5d_pct=change_5d,
                above_ma20=above_ma20,
                stop_loss=stop_loss,
                take_profit=take_profit,
                score=round(score, 1),
                reason=" | ".join(reason_parts),
            )

        except Exception as e:
            log.debug("SmallCap %s: %s", ticker, e)
            return None

    def _score(
        self,
        market_cap: int,
        volume: int,
        change_5d: float,
        above_ma20: bool,
        etf_gain_5d: float,
    ) -> float:
        score = 0.0

        # Volumen-Score (0–30 Punkte): höheres Volumen = besser für Liquidität
        vol_ratio = min(volume / _MIN_VOLUME, 5.0)  # Cap bei 5× Minimum
        score += (vol_ratio / 5.0) * 30

        # Momentum-Score (0–25 Punkte)
        if change_5d > 0:
            score += min(change_5d / 10.0, 1.0) * 25
        else:
            score += max(1 + change_5d / 20.0, 0) * 10  # Abzug bei Minus

        # MA20-Score (0–20 Punkte)
        if above_ma20:
            score += 20

        # Sektor-Stärke (0–15 Punkte)
        score += min(etf_gain_5d / 5.0, 1.0) * 15

        # Marktkapitalisierung (0–10 Punkte): mittlere MK bevorzugt
        mid = (_MIN_MARKET_CAP + _MAX_MARKET_CAP) / 2
        proximity = 1 - abs(market_cap - mid) / mid
        score += max(proximity, 0) * 10

        return min(score, 100.0)
