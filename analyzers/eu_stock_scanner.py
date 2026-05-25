"""
EU-Aktien Scanner – XETRA / LSE / CAC40 / SMI / AEX / BME / OMX

Scannt ein festes Universum europäischer Blue-Chip- und Mid-Cap-Aktien
und liefert die vielversprechendsten Kandidaten nach einem technischen
Score-Modell.

Aktivierung: manuell via  python main.py --eu-scan
Niemals automatisch im normalen Bot-Zyklus.

Score-Modell (0–100 Punkte):
  30 Pkt  5-Tage-Momentum     (positiv = Punkte, negativ = Abzug)
  25 Pkt  Kurs über MA20      (binäre Bedingung)
  25 Pkt  Volumen-Ratio       (aktuell / 20d-Durchschnitt)
  20 Pkt  1-Monats-Performance (positiv = Punkte)

Harte Filter:
  - above_ma20 == True  ODER  change_5d_pct > 2.0
  - volume_ratio >= min_volume_ratio  (Standard: 0.8)

Stop-Loss:  6 % (europäische Blue-Chips stabiler als US Small-Caps)
Take-Profit: 15 %
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

# ─── Konstanten ──────────────────────────────────────────────────────────────

_STOP_LOSS_PCT   = 0.06   # 6 %
_TAKE_PROFIT_PCT = 0.15   # 15 %

# Währungs-Mapping nach Börsen-Suffix
_CURRENCY_MAP: Dict[str, str] = {
    ".DE": "EUR",
    ".AS": "EUR",
    ".PA": "EUR",
    ".MC": "EUR",
    ".SW": "CHF",
    ".L":  "GBP",
    ".CO": "DKK",
}

# ─── Aktien-Universum ─────────────────────────────────────────────────────────

# Format: { "TICKER.SUFFIX": ("Name", "Land", "Sektor") }
EU_UNIVERSE: Dict[str, tuple] = {
    # Deutschland (XETRA .DE)
    "SAP.DE":    ("SAP SE",             "DE", "Technologie"),
    "SIE.DE":    ("Siemens AG",         "DE", "Industrie"),
    "BAS.DE":    ("BASF SE",            "DE", "Chemie"),
    "BMW.DE":    ("BMW AG",             "DE", "Automobil"),
    "MBG.DE":    ("Mercedes-Benz",      "DE", "Automobil"),
    "DTE.DE":    ("Deutsche Telekom",   "DE", "Telekommunikation"),
    "ALV.DE":    ("Allianz SE",         "DE", "Versicherung"),
    "MRK.DE":    ("Merck KGaA",         "DE", "Healthcare"),
    "RWE.DE":    ("RWE AG",             "DE", "Energie"),
    "IFX.DE":    ("Infineon",           "DE", "Halbleiter"),
    "VOW3.DE":   ("Volkswagen",         "DE", "Automobil"),
    "DBK.DE":    ("Deutsche Bank",      "DE", "Finanzen"),
    "BAYN.DE":   ("Bayer AG",           "DE", "Healthcare"),
    "ADS.DE":    ("Adidas AG",          "DE", "Konsum"),
    "HEN3.DE":   ("Henkel",             "DE", "Konsum"),
    # Niederlande (AEX .AS)
    "ASML.AS":   ("ASML Holding",       "NL", "Halbleiter"),
    "HEIA.AS":   ("Heineken",           "NL", "Konsum"),
    "PHIA.AS":   ("Philips",            "NL", "Healthcare"),
    "NN.AS":     ("NN Group",           "NL", "Versicherung"),
    # Frankreich (CAC40 .PA)
    "MC.PA":     ("LVMH",              "FR", "Luxus"),
    "OR.PA":     ("L'Oréal",           "FR", "Konsum"),
    "TTE.PA":    ("TotalEnergies",     "FR", "Energie"),
    "SAN.PA":    ("Sanofi",            "FR", "Healthcare"),
    "AIR.PA":    ("Airbus SE",         "FR", "Luftfahrt"),
    "BNP.PA":    ("BNP Paribas",       "FR", "Finanzen"),
    "KER.PA":    ("Kering",            "FR", "Luxus"),
    # Schweiz (SMI .SW)
    "NESN.SW":   ("Nestlé",            "CH", "Konsum"),
    "RO.SW":     ("Roche",             "CH", "Healthcare"),
    "NOVN.SW":   ("Novartis",          "CH", "Healthcare"),
    "ABBN.SW":   ("ABB Ltd",           "CH", "Industrie"),
    # UK (FTSE .L)
    "SHEL.L":    ("Shell plc",         "GB", "Energie"),
    "AZN.L":     ("AstraZeneca",       "GB", "Healthcare"),
    "HSBA.L":    ("HSBC Holdings",     "GB", "Finanzen"),
    "BP.L":      ("BP plc",            "GB", "Energie"),
    "ULVR.L":    ("Unilever",          "GB", "Konsum"),
    "GSK.L":     ("GSK plc",           "GB", "Healthcare"),
    # Dänemark (OMX .CO)
    "NOVO-B.CO": ("Novo Nordisk",      "DK", "Healthcare"),
    # Spanien (BME .MC)
    "ITX.MC":    ("Inditex (Zara)",    "ES", "Konsum"),
}


# ─── Datenklassen ─────────────────────────────────────────────────────────────

@dataclass
class EUCandidate:
    ticker:        str
    name:          str
    country:       str
    sector:        str
    price:         float       # in lokaler Währung
    currency:      str         # EUR, GBP, CHF, DKK
    change_1d_pct: float
    change_5d_pct: float
    change_1m_pct: float
    above_ma20:    bool
    volume_ratio:  float       # aktuell / 20-Tage-Durchschnitt
    score:         float       # 0–100
    stop_loss:     float
    take_profit:   float
    reason:        str


@dataclass
class EUScanResult:
    scan_date:      str
    candidates:     List[EUCandidate]
    by_country:     Dict[str, int]   # {"DE": 3, "FR": 2, …}
    by_sector:      Dict[str, int]   # {"Technologie": 2, …}
    skipped_count:  int
    scan_duration_s: float


# ─── Scanner ──────────────────────────────────────────────────────────────────

class EUStockScanner:
    """
    Scannt europäische Aktien (XETRA, LSE, CAC40, SMI, AEX, BME, OMX)
    nach einem technischen Score-Modell.

    Nur manuell aufrufbar — nicht im automatischen Bot-Zyklus.
    """

    def __init__(
        self,
        universe: Dict[str, tuple] = None,
        max_results: int = 12,
        min_volume_ratio: float = 0.8,
        country_filter: List[str] = None,
        sector_filter: List[str] = None,
    ):
        self.universe = universe if universe is not None else EU_UNIVERSE
        self.max_results = max_results
        self.min_volume_ratio = min_volume_ratio
        self.country_filter = [c.upper() for c in country_filter] if country_filter else None
        self.sector_filter = sector_filter

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def scan(self) -> EUScanResult:
        t0 = time.monotonic()
        log.info("EU-Aktien Scanner gestartet (%d Titel im Universum) …", len(self.universe))

        candidates: List[EUCandidate] = []
        skipped = 0

        for ticker, (name, country, sector) in self.universe.items():
            # Optionale Filter vor dem Daten-Download
            if self.country_filter and country not in self.country_filter:
                skipped += 1
                continue
            if self.sector_filter and sector not in self.sector_filter:
                skipped += 1
                continue

            result = self._evaluate(ticker, name, country, sector)
            if result is None:
                skipped += 1
            else:
                candidates.append(result)

        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[: self.max_results]

        by_country: Dict[str, int] = {}
        by_sector: Dict[str, int] = {}
        for c in candidates:
            by_country[c.country] = by_country.get(c.country, 0) + 1
            by_sector[c.sector] = by_sector.get(c.sector, 0) + 1

        duration = round(time.monotonic() - t0, 1)
        log.info(
            "EU-Scan abgeschlossen: %d Kandidaten, %d übersprungen, %.1fs",
            len(candidates), skipped, duration,
        )

        return EUScanResult(
            scan_date=datetime.utcnow().isoformat(),
            candidates=candidates,
            by_country=by_country,
            by_sector=by_sector,
            skipped_count=skipped,
            scan_duration_s=duration,
        )

    # ── Einzelaktie bewerten ──────────────────────────────────────────────────

    def _evaluate(
        self,
        ticker: str,
        name: str,
        country: str,
        sector: str,
    ) -> Optional[EUCandidate]:
        try:
            hist = yf.Ticker(ticker).history(period="30d")

            if hist.empty or len(hist) < 6:
                log.debug("EU %s: zu wenig History-Daten", ticker)
                return None

            close = hist["Close"]
            volume = hist["Volume"]

            price = float(close.iloc[-1])

            # Kursveränderungen
            change_1d = round((close.iloc[-1] / close.iloc[-2] - 1) * 100, 2)
            change_5d = round((close.iloc[-1] / close.iloc[-6] - 1) * 100, 2)

            # 1-Monats-Performance: erster vs. letzter Schlusskurs der 30d-Periode
            change_1m = round((close.iloc[-1] / close.iloc[0] - 1) * 100, 2)

            # MA20 (oder alle verfügbaren Tage wenn < 20)
            ma20 = float(close.tail(20).mean())
            above_ma20 = price > ma20

            # Volumen-Ratio: letzter Tag vs. 20d-Durchschnitt
            avg_vol_20 = float(volume.tail(20).mean())
            current_vol = float(volume.iloc[-1])
            volume_ratio = round(current_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0.0

            # ── Harte Filter ────────────────────────────────────────────────
            passes_trend = above_ma20 or (change_5d > 2.0)
            if not passes_trend:
                log.debug("EU %s: kein Trend (MA20=%s, 5d=%.2f%%)", ticker, above_ma20, change_5d)
                return None

            if volume_ratio < self.min_volume_ratio:
                log.debug("EU %s: Volumen zu niedrig (ratio=%.2f)", ticker, volume_ratio)
                return None

            # ── Score ────────────────────────────────────────────────────────
            score = self._score(
                change_5d=change_5d,
                above_ma20=above_ma20,
                volume_ratio=volume_ratio,
                change_1m=change_1m,
            )

            # ── Stop-Loss / Take-Profit ──────────────────────────────────────
            stop_loss   = round(price * (1 - _STOP_LOSS_PCT), 4)
            take_profit = round(price * (1 + _TAKE_PROFIT_PCT), 4)

            # ── Währung ──────────────────────────────────────────────────────
            suffix = "." + ticker.split(".")[-1] if "." in ticker else ""
            currency = _CURRENCY_MAP.get(suffix, "EUR")

            # ── Begründung ───────────────────────────────────────────────────
            reason_parts: List[str] = []
            if above_ma20:
                reason_parts.append("Kurs über MA20")
            if change_5d > 0:
                reason_parts.append(f"+{change_5d:.1f}% in 5 Tagen")
            elif change_5d > 2.0:
                reason_parts.append(f"Momentum {change_5d:.1f}%")
            if volume_ratio >= 1.5:
                reason_parts.append(f"Volumen {volume_ratio:.1f}× Durchschnitt")
            if change_1m > 0:
                reason_parts.append(f"+{change_1m:.1f}% im letzten Monat")

            return EUCandidate(
                ticker=ticker,
                name=name,
                country=country,
                sector=sector,
                price=round(price, 4),
                currency=currency,
                change_1d_pct=change_1d,
                change_5d_pct=change_5d,
                change_1m_pct=change_1m,
                above_ma20=above_ma20,
                volume_ratio=volume_ratio,
                score=round(score, 1),
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=" | ".join(reason_parts) if reason_parts else "Technische Kriterien erfüllt",
            )

        except Exception as exc:
            log.debug("EU %s: Fehler beim Abruf – %s", ticker, exc)
            return None

    # ── Score-Berechnung ──────────────────────────────────────────────────────

    @staticmethod
    def _score(
        change_5d: float,
        above_ma20: bool,
        volume_ratio: float,
        change_1m: float,
    ) -> float:
        """
        Score-Formel (0–100):
          30 Pkt  5-Tage-Momentum
          25 Pkt  Kurs über MA20
          25 Pkt  Volumen-Ratio
          20 Pkt  1-Monats-Performance
        """
        score = 0.0

        # 30 Punkte: 5-Tage-Momentum
        if change_5d >= 0:
            # Bis +10 % = volle Punktzahl
            score += min(change_5d / 10.0, 1.0) * 30
        else:
            # Bis -20 % = null Punkte; dazwischen linearer Abzug
            score += max(1.0 + change_5d / 20.0, 0.0) * 30

        # 25 Punkte: Kurs über MA20
        if above_ma20:
            score += 25

        # 25 Punkte: Volumen-Ratio (< 0.5 = 0 Pkt, > 1.5 = volle Punktzahl)
        vol_norm = (volume_ratio - 0.5) / (1.5 - 0.5)   # 0 bei 0.5×, 1 bei 1.5×
        score += max(min(vol_norm, 1.0), 0.0) * 25

        # 20 Punkte: 1-Monats-Performance
        if change_1m >= 0:
            # Bis +15 % = volle Punktzahl
            score += min(change_1m / 15.0, 1.0) * 20
        else:
            # Negativ: bis -30 % = null Punkte
            score += max(1.0 + change_1m / 30.0, 0.0) * 20

        return min(score, 100.0)
