"""
Crash Radar – Echtzeit-Crash-Wahrscheinlichkeit & Blasen-Detektor

Berechnet zwei Dinge:
  1. Gesamt-Crash-Wahrscheinlichkeit (0–100) aus 7 Markt-Indikatoren
  2. Blasen-Detektor: welche Sektoren/Assets sind überhitzt?

Datenquellen (alle kostenlos via yfinance, kein API-Key nötig):
  ^VIX, ^TNX, ^IRX, HYG, GLD, SPY, BTC-USD
  Sektor-ETFs: XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLRE, XLB, XLC

Historischer Vergleich mit 6 großen Crash-Phasen:
  1929, 1987, 2000 (Dot-com), 2008 (Finanzkrise), 2020 (COVID), 2022 (Zinsen)

Ergebnis-Cache: 1h in data/crash_radar_cache.json (kein Rate-Limit-Druck)
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "crash_radar_cache.json")
_CACHE_TTL  = 3600  # 1 Stunde

# ── Historische Crash-Fingerabdrücke (normalisiert 0.0–1.0) ───────────────────
# Jeder Wert repräsentiert wie weit der Indikator in Richtung "Gefahr" war.
# 0.0 = normal/harmlos, 1.0 = maximaler Stress

_HISTORICAL_CRASHES: Dict[str, Dict] = {
    "1929": {
        "label":       "1929 Schwarzer Donnerstag",
        "desc":        "Margin-Schulden, Spekulationsblase, Deflationsspirale",
        "vix":         0.80,   # Extremer implizierter Stress (kein echtes VIX, Proxy)
        "yield_curve": 0.20,   # Kurve war vor Fed-Ära kaum invertiert
        "credit":      0.95,   # Bankpleiten, Kredit kollabierte
        "momentum":    0.90,   # DOW war +400% in den 5 Jahren davor gestiegen
        "gold_ratio":  0.75,   # Goldflucht massiv
        "speculation": 0.85,   # Maximale Spekulation (Margin-Käufe)
        "breadth":     0.80,   # Viele Sektoren überwertet
    },
    "1987": {
        "label":       "1987 Black Monday",
        "desc":        "Programm-Handel, Portfolio-Versicherung, Zinsan­stieg",
        "vix":         0.95,
        "yield_curve": 0.60,
        "credit":      0.45,
        "momentum":    0.85,   # S&P +44% im Jahr davor
        "gold_ratio":  0.50,
        "speculation": 0.70,
        "breadth":     0.65,
    },
    "2000": {
        "label":       "2000 Dot-com Blase",
        "desc":        "Internet-Euphorie, CAPE 44, Nasdaq +400% in 5 Jahren",
        "vix":         0.65,
        "yield_curve": 0.70,
        "credit":      0.35,
        "momentum":    0.95,   # Nasdaq-Momentum extrem
        "gold_ratio":  0.25,   # Gold war damals schwach
        "speculation": 0.98,   # IPO-Wahnsinn, Unprofitable Techs bewertet wie Infra
        "breadth":     0.45,   # Nur Tech überwertet, andere Sektoren OK
    },
    "2008": {
        "label":       "2008 Globale Finanzkrise",
        "desc":        "Subprime, CDOs, Leverage, Lehman Brothers",
        "vix":         0.98,
        "yield_curve": 0.85,
        "credit":      0.99,   # Credit komplett eingefroren
        "momentum":    0.15,   # Markt war schon 18 Monate im Abschwung
        "gold_ratio":  0.90,
        "speculation": 0.75,
        "breadth":     0.90,
    },
    "2020": {
        "label":       "2020 COVID-Crash",
        "desc":        "Externer Schock, schnellster Crash der Geschichte (33% in 33 Tagen)",
        "vix":         0.99,
        "yield_curve": 0.40,
        "credit":      0.70,
        "momentum":    0.50,   # Markt war vorher gesund
        "gold_ratio":  0.65,
        "speculation": 0.30,
        "breadth":     0.75,
    },
    "2022": {
        "label":       "2022 Zinswende-Crash",
        "desc":        "Fed +425bp in 12 Monaten – schnellster Zinsanstieg seit 40 Jahren",
        "vix":         0.55,
        "yield_curve": 0.95,   # Stärkste Inversion seit 1981
        "credit":      0.65,
        "momentum":    0.25,
        "gold_ratio":  0.45,
        "speculation": 0.60,
        "breadth":     0.70,
    },
}

# Sektor-ETFs mit menschenlesbarem Namen und historischer Ø-Jahresrendite
_SECTORS: Dict[str, Dict] = {
    "XLK":  {"name": "Technologie",           "avg_annual_pct": 18.0},
    "XLC":  {"name": "Kommunikation",         "avg_annual_pct": 12.0},
    "XLY":  {"name": "Konsumgüter (diskr.)",  "avg_annual_pct": 12.0},
    "XLRE": {"name": "Immobilien (REITs)",     "avg_annual_pct":  9.0},
    "XLF":  {"name": "Finanzen",              "avg_annual_pct": 10.0},
    "XLV":  {"name": "Gesundheit",            "avg_annual_pct": 12.0},
    "XLI":  {"name": "Industrie",             "avg_annual_pct": 10.0},
    "XLE":  {"name": "Energie",               "avg_annual_pct":  6.0},
    "XLB":  {"name": "Rohstoffe",             "avg_annual_pct":  8.0},
    "XLU":  {"name": "Versorger",             "avg_annual_pct":  9.0},
    "XLP":  {"name": "Konsumgüter (Basis)",   "avg_annual_pct":  9.0},
}

# Bubble-Score-Schwellen
_BUBBLE_MILD   = 40   # Leicht erhöht
_BUBBLE_HIGH   = 65   # Blase wahrscheinlich
_BUBBLE_SEVERE = 80   # Ernste Überhitzung


@dataclass
class IndicatorReading:
    name:        str
    value_str:   str          # Anzeige-Wert (z.B. "23.4" oder "+18.2%")
    score:       float        # Normalisiert 0.0–1.0 (Gefahr-Score)
    status:      str          # "NORMAL" | "ERHÖHT" | "ALARM"
    description: str          # Was bedeutet dieser Wert?


@dataclass
class BubbleReading:
    ticker:      str
    name:        str
    ytd_pct:     float
    return_1y:   float
    bubble_score: int          # 0–100
    risk_label:  str           # "GERING" | "MITTEL" | "HOCH" | "EXTREM"
    note:        str


@dataclass
class HistoricalMatch:
    crash_key:   str
    label:       str
    desc:        str
    similarity:  float         # 0.0–1.0
    similarity_pct: int


@dataclass
class CrashRadarResult:
    timestamp:           str
    crash_probability:   int               # 0–100
    risk_level:          str               # GERING / MITTEL / HOCH / EXTREM
    indicators:          List[IndicatorReading]
    bubbles:             List[BubbleReading]
    historical_matches:  List[HistoricalMatch]
    summary_line:        str
    data_available:      bool = True


class CrashRadar:
    """
    Berechnet Crash-Wahrscheinlichkeit und erkennt Blasen in Echtzeit.

    Alle Daten kommen von yfinance (kostenlos, kein API-Key).
    Ergebnis wird 1h gecacht um Rate-Limits zu vermeiden.
    """

    def __init__(self) -> None:
        self._cache: Optional[Dict] = None
        self._cache_ts: float = 0.0

    # ── Öffentliches Interface ────────────────────────────────────────────────

    def analyze(self, force_refresh: bool = False) -> CrashRadarResult:
        """Vollanalyse: Indikatoren + Blasen + historischer Vergleich."""
        # Cache prüfen
        if not force_refresh and self._cache and time.monotonic() - self._cache_ts < _CACHE_TTL:
            return CrashRadarResult(**self._cache)

        # Aus Datei-Cache laden
        if not force_refresh:
            cached = self._load_file_cache()
            if cached:
                return cached

        try:
            raw = self._fetch_raw_data()
            indicators, scores = self._compute_indicators(raw)
            bubbles = self._detect_bubbles(raw)
            crash_prob = self._aggregate_score(scores)
            matches = self._historical_similarity(scores)
            risk_level = self._risk_label(crash_prob)
            summary = self._build_summary(crash_prob, risk_level, indicators, bubbles)

            result = CrashRadarResult(
                timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                crash_probability=crash_prob,
                risk_level=risk_level,
                indicators=indicators,
                bubbles=bubbles,
                historical_matches=matches,
                summary_line=summary,
                data_available=True,
            )
        except Exception as e:
            log.warning("CrashRadar: Analyse fehlgeschlagen – %s", e)
            result = CrashRadarResult(
                timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                crash_probability=0,
                risk_level="UNBEKANNT",
                indicators=[],
                bubbles=[],
                historical_matches=[],
                summary_line=f"Daten nicht verfügbar: {e}",
                data_available=False,
            )

        self._save_file_cache(result)
        self._cache    = asdict(result)
        self._cache_ts = time.monotonic()
        return result

    # ── Daten-Fetch ───────────────────────────────────────────────────────────

    def _fetch_raw_data(self) -> Dict:
        """Lädt alle benötigten Marktdaten in einem Batch."""
        tickers = ["^VIX", "^TNX", "^IRX", "HYG", "GLD", "SPY", "BTC-USD"] + list(_SECTORS.keys())
        data: Dict[str, object] = {}

        for t in tickers:
            try:
                hist = yf.Ticker(t).history(period="1y")
                if not hist.empty:
                    data[t] = hist
            except Exception as e:
                log.debug("CrashRadar: Fetch %s fehlgeschlagen – %s", t, e)

        return data

    # ── Indikator-Berechnung ──────────────────────────────────────────────────

    def _compute_indicators(
        self, raw: Dict
    ) -> Tuple[List[IndicatorReading], Dict[str, float]]:
        """Berechnet alle 7 Indikatoren. Gibt (Lesungen, Score-Dict) zurück."""
        readings: List[IndicatorReading] = []
        scores:   Dict[str, float]       = {}

        # 1. VIX – Angst-Index
        vix_val, vix_score = self._vix_score(raw)
        readings.append(IndicatorReading(
            name="VIX (Angst-Index)",
            value_str=f"{vix_val:.1f}" if vix_val else "N/A",
            score=vix_score,
            status=self._score_status(vix_score, 0.35, 0.60),
            description="<20=ruhig  20–30=erhöht  >35=Panik  >50=Krise",
        ))
        scores["vix"] = vix_score

        # 2. Zinskurve (3M vs 10Y)
        yc_val, yc_score = self._yield_curve_score(raw)
        yc_str = f"{yc_val:+.2f}%" if yc_val is not None else "N/A"
        readings.append(IndicatorReading(
            name="Zinskurve (10Y−3M)",
            value_str=yc_str,
            score=yc_score,
            status=self._score_status(yc_score, 0.40, 0.70),
            description="Positiv=normal  Negativ=Inversion=Rezessions­warnung",
        ))
        scores["yield_curve"] = yc_score

        # 3. Kredit-Stress (HYG Momentum)
        cr_val, cr_score = self._credit_score(raw)
        readings.append(IndicatorReading(
            name="Kredit-Stress (HYG)",
            value_str=f"{cr_val:+.1f}%" if cr_val is not None else "N/A",
            score=cr_score,
            status=self._score_status(cr_score, 0.35, 0.60),
            description="3M-Momentum HYG: negativ=Risikovermeidung bei Unternehmens­anleihen",
        ))
        scores["credit"] = cr_score

        # 4. Markt-Momentum (SPY 12M)
        mo_val, mo_score = self._momentum_score(raw)
        readings.append(IndicatorReading(
            name="Markt-Momentum (S&P 12M)",
            value_str=f"{mo_val:+.1f}%" if mo_val is not None else "N/A",
            score=mo_score,
            status=self._score_status(mo_score, 0.40, 0.70),
            description=">30% in 12M ohne Korrektur → überhitzt  <0% → Bärenmarkt",
        ))
        scores["momentum"] = mo_score

        # 5. Gold-Flucht (Gold vs. SPY relative Performance)
        gr_val, gr_score = self._gold_ratio_score(raw)
        readings.append(IndicatorReading(
            name="Gold vs. Aktien (3M)",
            value_str=f"{gr_val:+.1f}%" if gr_val is not None else "N/A",
            score=gr_score,
            status=self._score_status(gr_score, 0.35, 0.60),
            description="Gold outperformt Aktien → Flucht in Sicherheit",
        ))
        scores["gold_ratio"] = gr_score

        # 6. Spekulations-Index (Bitcoin 1J-Return als Proxy)
        sp_val, sp_score = self._speculation_score(raw)
        readings.append(IndicatorReading(
            name="Spekulation (Bitcoin 1J)",
            value_str=f"{sp_val:+.0f}%" if sp_val is not None else "N/A",
            score=sp_score,
            status=self._score_status(sp_score, 0.40, 0.70),
            description=">100% Jahresrendite Bitcoin → Spekulations-Hochpunkt­signal",
        ))
        scores["speculation"] = sp_score

        # 7. Markt-Breite (Sektoren im Aufwärtstrend)
        br_val, br_score = self._breadth_score(raw)
        readings.append(IndicatorReading(
            name="Markt-Breite (Sektoren Up)",
            value_str=f"{br_val:.0f}/11" if br_val is not None else "N/A",
            score=br_score,
            status=self._score_status(br_score, 0.35, 0.60),
            description="<4/11 Sektoren im Aufwärtstrend → schmale Markt­basis",
        ))
        scores["breadth"] = br_score

        return readings, scores

    def _vix_score(self, raw: Dict) -> Tuple[Optional[float], float]:
        df = raw.get("^VIX")
        if df is None or df.empty:
            return None, 0.3
        vix = float(df["Close"].iloc[-1])
        # 12=0.0, 20=0.3, 30=0.6, 40=0.85, 50+=1.0
        score = max(0.0, min(1.0, (vix - 12) / 38))
        return vix, score

    def _yield_curve_score(self, raw: Dict) -> Tuple[Optional[float], float]:
        tnx = raw.get("^TNX")
        irx = raw.get("^IRX")
        if tnx is None or irx is None or tnx.empty or irx.empty:
            return None, 0.3
        ten_yr  = float(tnx["Close"].iloc[-1])
        three_m = float(irx["Close"].iloc[-1])
        spread  = ten_yr - three_m           # Positiv = normal, negativ = invertiert
        # +2%=0.0  0%=0.4  -0.5%=0.7  -1%+=1.0
        score = max(0.0, min(1.0, (0.5 - spread) / 1.5))
        return spread, score

    def _credit_score(self, raw: Dict) -> Tuple[Optional[float], float]:
        df = raw.get("HYG")
        if df is None or len(df) < 63:
            return None, 0.3
        price_now  = float(df["Close"].iloc[-1])
        price_3m   = float(df["Close"].iloc[-63])
        momentum   = (price_now - price_3m) / price_3m * 100
        # +3%=0.0  0%=0.3  -3%=0.7  -6%+=1.0
        score = max(0.0, min(1.0, (0 - momentum) / 6 + 0.3))
        return momentum, score

    def _momentum_score(self, raw: Dict) -> Tuple[Optional[float], float]:
        df = raw.get("SPY")
        if df is None or len(df) < 250:
            return None, 0.3
        now   = float(df["Close"].iloc[-1])
        yr    = float(df["Close"].iloc[-252]) if len(df) >= 252 else float(df["Close"].iloc[0])
        ret   = (now - yr) / yr * 100
        # 0%=0.1  15%=0.3  30%=0.6  50%+=1.0   <-20%=0.8 (Bärenmarkt auch riskant)
        if ret >= 0:
            score = max(0.0, min(1.0, ret / 50))
        else:
            score = max(0.0, min(1.0, -ret / 25))   # Starker Abschwung auch Alarm
        return ret, score

    def _gold_ratio_score(self, raw: Dict) -> Tuple[Optional[float], float]:
        gld = raw.get("GLD")
        spy = raw.get("SPY")
        if gld is None or spy is None or len(gld) < 63 or len(spy) < 63:
            return None, 0.2
        gld_ret = (float(gld["Close"].iloc[-1]) - float(gld["Close"].iloc[-63])) / float(gld["Close"].iloc[-63]) * 100
        spy_ret = (float(spy["Close"].iloc[-1]) - float(spy["Close"].iloc[-63])) / float(spy["Close"].iloc[-63]) * 100
        outperform = gld_ret - spy_ret
        # Gold +10% vs Aktien = Flucht-Signal
        score = max(0.0, min(1.0, outperform / 20))
        return outperform, score

    def _speculation_score(self, raw: Dict) -> Tuple[Optional[float], float]:
        df = raw.get("BTC-USD")
        if df is None or len(df) < 250:
            return None, 0.2
        now = float(df["Close"].iloc[-1])
        yr  = float(df["Close"].iloc[-252]) if len(df) >= 252 else float(df["Close"].iloc[0])
        ret = (now - yr) / yr * 100
        # 0%=0.1  100%=0.5  200%+=1.0   <-50%=0.6 (Crash schon da)
        if ret >= 0:
            score = max(0.0, min(1.0, ret / 200))
        else:
            score = max(0.0, min(0.6, -ret / 80))
        return ret, score

    def _breadth_score(self, raw: Dict) -> Tuple[Optional[float], float]:
        up_count = 0
        available = 0
        for ticker in _SECTORS:
            df = raw.get(ticker)
            if df is None or len(df) < 50:
                continue
            available += 1
            ma50 = df["Close"].tail(50).mean()
            if float(df["Close"].iloc[-1]) > ma50:
                up_count += 1
        if available == 0:
            return None, 0.3
        up_ratio = up_count / available   # 1.0 = alle über MA50
        score = max(0.0, min(1.0, 1.0 - up_ratio))  # viele über MA50 = geringer Stress
        return float(up_count), score

    # ── Aggregation ───────────────────────────────────────────────────────────

    def _aggregate_score(self, scores: Dict[str, float]) -> int:
        """Gewichteter Durchschnitt → 0–100."""
        weights = {
            "vix":         0.20,
            "yield_curve": 0.20,
            "credit":      0.15,
            "momentum":    0.15,
            "gold_ratio":  0.10,
            "speculation": 0.10,
            "breadth":     0.10,
        }
        total = sum(scores.get(k, 0.3) * w for k, w in weights.items())
        return min(100, max(0, round(total * 100)))

    # ── Blasen-Detektor ───────────────────────────────────────────────────────

    def _detect_bubbles(self, raw: Dict) -> List[BubbleReading]:
        """Bewertet jeden Sektor-ETF auf Überhitzung."""
        results: List[BubbleReading] = []

        for ticker, meta in _SECTORS.items():
            df = raw.get(ticker)
            if df is None or len(df) < 20:
                continue

            now = float(df["Close"].iloc[-1])

            # YTD Return
            ytd_rows = df[df.index.year == datetime.now().year]
            ytd_pct = ((now - float(ytd_rows["Close"].iloc[0])) / float(ytd_rows["Close"].iloc[0]) * 100) \
                      if len(ytd_rows) > 1 else 0.0

            # 1J Return
            yr_pct = ((now - float(df["Close"].iloc[0])) / float(df["Close"].iloc[0]) * 100) \
                     if len(df) >= 200 else ytd_pct

            # Abstand vom 52-Wochen-Hoch
            high_52w   = float(df["High"].tail(252).max())
            dd_from_ath = (high_52w - now) / high_52w * 100

            # Bubble-Score (0–100)
            avg = meta["avg_annual_pct"]
            # Überschuss vs historischem Durchschnitt (1J)
            excess      = max(0.0, yr_pct - avg)
            excess_score = min(60, excess / avg * 40) if avg > 0 else 0
            # Wie hoch ist der Sektor relativ zu seinem 52W-Hoch?
            ath_score    = max(0, 40 - dd_from_ath * 4)
            bubble_score = min(100, int(excess_score + ath_score))

            if bubble_score >= _BUBBLE_SEVERE:
                risk_label = "EXTREM"
                note = f"{yr_pct:+.0f}% in 1J – {dd_from_ath:.1f}% vom Hoch"
            elif bubble_score >= _BUBBLE_HIGH:
                risk_label = "HOCH"
                note = f"{yr_pct:+.0f}% in 1J, Ø-Jahr: {avg:.0f}%"
            elif bubble_score >= _BUBBLE_MILD:
                risk_label = "MITTEL"
                note = f"Leicht über­bewertet"
            else:
                risk_label = "GERING"
                note = f"Normal bewertet"

            results.append(BubbleReading(
                ticker=ticker,
                name=meta["name"],
                ytd_pct=round(ytd_pct, 1),
                return_1y=round(yr_pct, 1),
                bubble_score=bubble_score,
                risk_label=risk_label,
                note=note,
            ))

        return sorted(results, key=lambda b: b.bubble_score, reverse=True)

    # ── Historischer Vergleich ────────────────────────────────────────────────

    def _historical_similarity(self, scores: Dict[str, float]) -> List[HistoricalMatch]:
        """
        Berechnet Ähnlichkeit (0–1) zwischen aktuellem Fingerabdruck
        und historischen Crash-Phasen via normalisierter euklidischer Distanz.
        """
        keys = ["vix", "yield_curve", "credit", "momentum", "gold_ratio", "speculation", "breadth"]
        current = [scores.get(k, 0.3) for k in keys]
        matches: List[HistoricalMatch] = []

        for crash_key, crash in _HISTORICAL_CRASHES.items():
            historical = [crash.get(k, 0.3) for k in keys]
            # Euklidische Distanz
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(current, historical)))
            max_dist = math.sqrt(len(keys))   # Maximale mögliche Distanz
            similarity = max(0.0, 1.0 - dist / max_dist)
            matches.append(HistoricalMatch(
                crash_key=crash_key,
                label=crash["label"],
                desc=crash["desc"],
                similarity=round(similarity, 3),
                similarity_pct=round(similarity * 100),
            ))

        return sorted(matches, key=lambda m: m.similarity, reverse=True)

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _score_status(score: float, warn_threshold: float, alarm_threshold: float) -> str:
        if score >= alarm_threshold:
            return "ALARM"
        if score >= warn_threshold:
            return "ERHÖHT"
        return "NORMAL"

    @staticmethod
    def _risk_label(prob: int) -> str:
        if prob >= 70:
            return "EXTREM"
        if prob >= 50:
            return "HOCH"
        if prob >= 30:
            return "MITTEL"
        return "GERING"

    def _build_summary(
        self,
        prob: int,
        risk_level: str,
        indicators: List[IndicatorReading],
        bubbles: List[BubbleReading],
    ) -> str:
        alarm_count  = sum(1 for i in indicators if i.status == "ALARM")
        bubble_count = sum(1 for b in bubbles if b.risk_label in ("HOCH", "EXTREM"))
        top_bubble   = bubbles[0].name if bubbles else "–"
        return (
            f"Crash-Wahrscheinlichkeit {prob}% ({risk_level}) | "
            f"{alarm_count} Alarm-Indikatoren | "
            f"{bubble_count} überhitzte Sektoren (stärkste: {top_bubble})"
        )

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _save_file_cache(self, result: CrashRadarResult) -> None:
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            payload = asdict(result)
            payload["_cached_at"] = time.time()
            with tempfile.NamedTemporaryFile(
                mode="w", dir=os.path.dirname(_CACHE_FILE),
                suffix=".tmp", delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(payload, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, _CACHE_FILE)
        except Exception as e:
            log.debug("CrashRadar: Cache-Speicher fehlgeschlagen – %s", e)

    def _load_file_cache(self) -> Optional[CrashRadarResult]:
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            age = time.time() - data.get("_cached_at", 0)
            if age > _CACHE_TTL:
                return None
            data.pop("_cached_at", None)
            # Nested dataclasses rekonstruieren
            data["indicators"] = [IndicatorReading(**i) for i in data.get("indicators", [])]
            data["bubbles"]    = [BubbleReading(**b) for b in data.get("bubbles", [])]
            data["historical_matches"] = [HistoricalMatch(**m) for m in data.get("historical_matches", [])]
            return CrashRadarResult(**data)
        except Exception:
            return None
