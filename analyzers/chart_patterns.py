"""
Chart-Pattern-Erkennung auf OHLCV-Daten

Erkennt klassische technische Muster:
  Trendumkehr: Double Bottom, Double Top, Head & Shoulders
  Trend-Fortsetzung: Bull Flag, Bear Flag, Ascending/Descending Triangle
  Momentum: Golden Cross, Death Cross, RSI-Divergenz
  Levels: Support / Resistance

Gibt PatternResult zurück mit:
  - Liste erkannter Muster mit Richtung (BULLISH/BEARISH) und Stärke (0–1)
  - Technischen Gesamt-Score (0–100)
  - Klartext-Block für Claude-Prompt

Verwendung:
  result = ChartPatternAnalyzer().analyze("BTC")
  # result.score, result.patterns, result.to_prompt_block()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import yfinance as yf
import numpy as np

from logger import get_logger

log = get_logger(__name__)

_MIN_BARS = 30   # Mindestanzahl Kerzen für Muster-Erkennung


@dataclass
class Pattern:
    name:      str         # z.B. "Double Bottom", "Bull Flag"
    direction: str         # "BULLISH" | "BEARISH" | "NEUTRAL"
    strength:  float       # 0.0–1.0
    note:      str         # kurze Erklärung


@dataclass
class PatternResult:
    ticker:         str
    patterns:       List[Pattern]
    score:          float          # 0–100 (technischer Gesamtscore)
    ma50:           Optional[float]
    ma200:          Optional[float]
    support:        Optional[float]
    resistance:     Optional[float]
    rsi:            Optional[float]
    volume_trend:   str            # "RISING" | "FALLING" | "FLAT"
    primary_signal: str            # "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL"

    def to_prompt_block(self) -> str:
        lines = [f"=== CHART-MUSTER & TECHNISCHE SIGNALE ({self.ticker}) ==="]

        if self.patterns:
            lines.append("Erkannte Muster:")
            for p in self.patterns:
                icon = "▲" if p.direction == "BULLISH" else ("▼" if p.direction == "BEARISH" else "◆")
                lines.append(f"  {icon} {p.name} [{p.direction}] (Stärke: {p.strength:.0%}) – {p.note}")
        else:
            lines.append("Erkannte Muster: Keine klaren Muster im aktuellen Zeitfenster")

        lines.append(f"Technischer Score: {self.score:.0f}/100  →  {self.primary_signal}")

        if self.ma50 and self.ma200:
            cross = "Golden Cross (bullisch)" if self.ma50 > self.ma200 else "Death Cross (bärisch)"
            lines.append(f"MA50={self.ma50:.2f}  MA200={self.ma200:.2f}  [{cross}]")
        if self.support and self.resistance:
            lines.append(f"Support: {self.support:.2f}  |  Resistance: {self.resistance:.2f}")
        if self.rsi:
            rsi_note = "überkauft" if self.rsi > 70 else ("überverkauft" if self.rsi < 30 else "neutral")
            lines.append(f"RSI(14): {self.rsi:.1f}  [{rsi_note}]")
        lines.append(f"Volumen-Trend: {self.volume_trend}")
        lines.append("=" * 50)
        return "\n".join(lines)


class ChartPatternAnalyzer:

    def __init__(self, period: str = "6mo"):
        self.period = period

    def analyze(self, ticker: str) -> Optional[PatternResult]:
        try:
            yf_ticker = ticker.split("/")[0] + "-USD" if "/" in ticker or len(ticker) <= 5 and "." not in ticker and ticker.isalpha() else ticker
            # For known crypto symbols, always use -USD suffix
            from analyzers.crypto_universe import CRYPTO_UNIVERSE
            if ticker.upper() in CRYPTO_UNIVERSE:
                yf_ticker = ticker.upper() + "-USD"

            hist = yf.Ticker(yf_ticker).history(period=self.period)
            if hist.empty or len(hist) < _MIN_BARS:
                hist = yf.Ticker(ticker).history(period=self.period)
            if hist.empty or len(hist) < _MIN_BARS:
                return None

            close  = hist["Close"].values.astype(float)
            high   = hist["High"].values.astype(float)
            low    = hist["Low"].values.astype(float)
            volume = hist["Volume"].values.astype(float)

            patterns: List[Pattern] = []

            # ── Klassische Muster ─────────────────────────────────────────────
            p = self._double_bottom(close, low)
            if p: patterns.append(p)

            p = self._double_top(close, high)
            if p: patterns.append(p)

            p = self._bull_flag(close, volume)
            if p: patterns.append(p)

            p = self._bear_flag(close, volume)
            if p: patterns.append(p)

            p = self._ascending_triangle(close, high, low)
            if p: patterns.append(p)

            p = self._descending_triangle(close, high, low)
            if p: patterns.append(p)

            p = self._head_and_shoulders(close, high)
            if p: patterns.append(p)

            # ── Moving Average Crosses ────────────────────────────────────────
            ma50, ma200, cross = self._ma_cross(close)
            if cross:
                patterns.append(cross)

            # ── RSI-Divergenz ─────────────────────────────────────────────────
            rsi_val = self._rsi(close)
            div = self._rsi_divergence(close, rsi_val)
            if div:
                patterns.append(div)

            # ── Support / Resistance ──────────────────────────────────────────
            support, resistance = self._support_resistance(close, low, high)

            # ── Volumen-Trend ─────────────────────────────────────────────────
            volume_trend = self._volume_trend(volume)

            # ── Score berechnen ───────────────────────────────────────────────
            score = self._score(patterns, close, ma50, ma200, rsi_val, volume_trend)
            primary = self._primary_signal(score)

            return PatternResult(
                ticker=ticker,
                patterns=patterns,
                score=round(score, 1),
                ma50=round(ma50, 2) if ma50 else None,
                ma200=round(ma200, 2) if ma200 else None,
                support=round(support, 2) if support else None,
                resistance=round(resistance, 2) if resistance else None,
                rsi=round(rsi_val, 1) if rsi_val else None,
                volume_trend=volume_trend,
                primary_signal=primary,
            )
        except Exception as e:
            log.debug("ChartPatterns %s: %s", ticker, e)
            return None

    # ── Muster-Erkennung ──────────────────────────────────────────────────────

    def _double_bottom(self, close: np.ndarray, low: np.ndarray) -> Optional[Pattern]:
        """W-Muster: zwei ähnliche Tiefs, dazwischen Erholung."""
        n = len(low)
        if n < 20:
            return None
        window = low[-30:]
        idx_sorted = np.argsort(window)
        t1, t2 = sorted(idx_sorted[:2])
        if abs(t2 - t1) < 5:
            return None
        trough1, trough2 = window[t1], window[t2]
        if abs(trough1 - trough2) / max(trough1, trough2) > 0.04:  # max 4% Abstand
            return None
        neck = window[t1:t2].max()
        if close[-1] > neck * 0.99:
            strength = min((close[-1] - min(trough1, trough2)) / (neck - min(trough1, trough2)), 1.0)
            return Pattern("Double Bottom", "BULLISH", round(strength, 2),
                           f"Zwei Tiefs bei ~{min(trough1,trough2):.2f}, Necklevel {neck:.2f}")
        return None

    def _double_top(self, close: np.ndarray, high: np.ndarray) -> Optional[Pattern]:
        """M-Muster: zwei ähnliche Hochs, dazwischen Rückgang."""
        n = len(high)
        if n < 20:
            return None
        window = high[-30:]
        idx_sorted = np.argsort(window)[::-1]
        t1, t2 = sorted(idx_sorted[:2])
        if abs(t2 - t1) < 5:
            return None
        peak1, peak2 = window[t1], window[t2]
        if abs(peak1 - peak2) / max(peak1, peak2) > 0.04:
            return None
        neck = window[t1:t2].min()
        if close[-1] < neck * 1.01:
            strength = min((max(peak1, peak2) - close[-1]) / (max(peak1, peak2) - neck), 1.0)
            return Pattern("Double Top", "BEARISH", round(strength, 2),
                           f"Zwei Hochs bei ~{max(peak1,peak2):.2f}, Necklevel {neck:.2f}")
        return None

    def _bull_flag(self, close: np.ndarray, volume: np.ndarray) -> Optional[Pattern]:
        """Flagge bullisch: starker Anstieg, dann kurze Konsolidierung mit Keil nach unten."""
        if len(close) < 15:
            return None
        pole = close[-20:-10]
        flag = close[-10:]
        pole_gain = (pole[-1] - pole[0]) / pole[0]
        flag_drift = (flag[-1] - flag[0]) / flag[0]
        vol_avg_pole = volume[-20:-10].mean()
        vol_avg_flag = volume[-10:].mean()
        if pole_gain > 0.05 and -0.04 < flag_drift < 0.01 and vol_avg_flag < vol_avg_pole * 0.8:
            strength = min(pole_gain / 0.15, 1.0)
            return Pattern("Bull Flag", "BULLISH", round(strength, 2),
                           f"Fahnenstange +{pole_gain*100:.1f}%, Konsolidierung mit abnehmendem Volumen")
        return None

    def _bear_flag(self, close: np.ndarray, volume: np.ndarray) -> Optional[Pattern]:
        """Flagge bärisch: starker Abstieg, kurze Erholung, Volumen nimmt ab."""
        if len(close) < 15:
            return None
        pole = close[-20:-10]
        flag = close[-10:]
        pole_drop = (pole[0] - pole[-1]) / pole[0]
        flag_drift = (flag[-1] - flag[0]) / flag[0]
        vol_avg_pole = volume[-20:-10].mean()
        vol_avg_flag = volume[-10:].mean()
        if pole_drop > 0.05 and -0.01 < flag_drift < 0.04 and vol_avg_flag < vol_avg_pole * 0.8:
            strength = min(pole_drop / 0.15, 1.0)
            return Pattern("Bear Flag", "BEARISH", round(strength, 2),
                           f"Fahnenstange −{pole_drop*100:.1f}%, schwache Erholung mit niedrigem Volumen")
        return None

    def _ascending_triangle(self, close: np.ndarray, high: np.ndarray, low: np.ndarray) -> Optional[Pattern]:
        """Aufsteigendes Dreieck: flache Resistance, steigende Tiefs."""
        if len(close) < 20:
            return None
        w = 20
        highs = high[-w:]
        lows  = low[-w:]
        res_flat = (highs.max() - highs.min()) / highs.max() < 0.03
        x = np.arange(w)
        low_slope = np.polyfit(x, lows, 1)[0]
        if res_flat and low_slope > 0 and close[-1] > highs.mean() * 0.97:
            return Pattern("Ascending Triangle", "BULLISH", 0.7,
                           f"Widerstand bei ~{highs.max():.2f}, steigende Tiefs – Ausbruch nach oben wahrscheinlich")
        return None

    def _descending_triangle(self, close: np.ndarray, high: np.ndarray, low: np.ndarray) -> Optional[Pattern]:
        """Absteigendes Dreieck: flache Unterstützung, sinkende Hochs."""
        if len(close) < 20:
            return None
        w = 20
        highs = high[-w:]
        lows  = low[-w:]
        sup_flat = (lows.max() - lows.min()) / lows.max() < 0.03
        x = np.arange(w)
        high_slope = np.polyfit(x, highs, 1)[0]
        if sup_flat and high_slope < 0 and close[-1] < lows.mean() * 1.03:
            return Pattern("Descending Triangle", "BEARISH", 0.7,
                           f"Unterstützung bei ~{lows.min():.2f}, sinkende Hochs – Ausbruch nach unten wahrscheinlich")
        return None

    def _head_and_shoulders(self, close: np.ndarray, high: np.ndarray) -> Optional[Pattern]:
        """Schulter-Kopf-Schulter: mittleres Hoch höher als zwei seitliche."""
        if len(high) < 30:
            return None
        w = high[-30:]
        # Finde 3 lokale Maxima
        from scipy.signal import argrelextrema
        try:
            peaks = argrelextrema(w, np.greater, order=3)[0]
            if len(peaks) < 3:
                return None
            # Nimm die drei markantesten
            peak_vals = [(w[p], p) for p in peaks]
            peak_vals.sort(key=lambda x: -x[0])
            top3 = sorted(peak_vals[:3], key=lambda x: x[1])
            v = [x[0] for x in top3]
            # Kopf muss höher als beide Schultern
            if v[1] > v[0] * 1.02 and v[1] > v[2] * 1.02 and abs(v[0] - v[2]) / v[1] < 0.06:
                if close[-1] < v[2] * 0.98:
                    return Pattern("Head & Shoulders", "BEARISH", 0.8,
                                   f"Kopf ~{v[1]:.2f}, Schultern ~{v[0]:.2f}/{v[2]:.2f} – Trendumkehr Signal")
        except Exception:
            pass
        return None

    # ── Moving Average Cross ──────────────────────────────────────────────────

    def _ma_cross(self, close: np.ndarray) -> Tuple[Optional[float], Optional[float], Optional[Pattern]]:
        if len(close) < 200:
            return None, None, None
        ma50  = float(close[-50:].mean())
        ma200 = float(close[-200:].mean())
        prev_ma50  = float(close[-51:-1].mean())
        prev_ma200 = float(close[-201:-1].mean()) if len(close) >= 201 else ma200

        pattern = None
        if prev_ma50 <= prev_ma200 and ma50 > ma200:
            pattern = Pattern("Golden Cross", "BULLISH", 0.9,
                              f"MA50 ({ma50:.2f}) kreuzt MA200 ({ma200:.2f}) nach oben – starkes Langzeitsignal")
        elif prev_ma50 >= prev_ma200 and ma50 < ma200:
            pattern = Pattern("Death Cross", "BEARISH", 0.9,
                              f"MA50 ({ma50:.2f}) kreuzt MA200 ({ma200:.2f}) nach unten – starkes Warnsignal")
        return ma50, ma200, pattern

    # ── RSI-Divergenz ─────────────────────────────────────────────────────────

    def _rsi(self, close: np.ndarray, period: int = 14) -> Optional[float]:
        if len(close) < period + 1:
            return None
        deltas = np.diff(close)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = gains[-period:].mean()
        avg_loss = losses[-period:].mean()
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _rsi_divergence(self, close: np.ndarray, rsi: Optional[float]) -> Optional[Pattern]:
        """Bullische Divergenz: neues Kurs-Tief, RSI aber höher als beim letzten Tief."""
        if rsi is None or len(close) < 20:
            return None
        window = 20
        c = close[-window:]
        # Suche zwei Tiefs
        min_idx = int(np.argmin(c[:-5]))
        current_low = c[-1]
        prev_low = c[min_idx]
        if current_low < prev_low * 0.99:  # neues Kurs-Tief
            if rsi > 35:  # aber RSI nicht auf neuem Tief (bullische Divergenz)
                return Pattern("Bullische RSI-Divergenz", "BULLISH", 0.75,
                               f"Kurs auf neuem Tief, RSI={rsi:.1f} noch nicht – Momentum dreht")
        if current_low > prev_low * 1.01:  # Kurs erholt sich
            if rsi < 45:  # aber RSI schwach (bärische Divergenz)
                return Pattern("Bärische RSI-Divergenz", "BEARISH", 0.65,
                               f"Kurs erholt sich, RSI={rsi:.1f} aber schwach – Momentum fehlt")
        return None

    # ── Support / Resistance ──────────────────────────────────────────────────

    def _support_resistance(self, close: np.ndarray, low: np.ndarray, high: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
        if len(close) < 20:
            return None, None
        support = float(low[-30:].min()) if len(low) >= 30 else float(low.min())
        resistance = float(high[-30:].max()) if len(high) >= 30 else float(high.max())
        return support, resistance

    # ── Volumen-Trend ─────────────────────────────────────────────────────────

    def _volume_trend(self, volume: np.ndarray) -> str:
        if len(volume) < 10:
            return "FLAT"
        recent = volume[-5:].mean()
        older  = volume[-15:-5].mean() if len(volume) >= 15 else volume[:-5].mean()
        if older == 0:
            return "FLAT"
        ratio = recent / older
        if ratio > 1.3:
            return "RISING"
        if ratio < 0.7:
            return "FALLING"
        return "FLAT"

    # ── Gesamt-Score ──────────────────────────────────────────────────────────

    def _score(self, patterns: List[Pattern], close: np.ndarray,
               ma50: Optional[float], ma200: Optional[float],
               rsi: Optional[float], volume_trend: str) -> float:
        score = 50.0  # Neutral-Startpunkt

        for p in patterns:
            weight = p.strength * 15
            if p.direction == "BULLISH":
                score += weight
            elif p.direction == "BEARISH":
                score -= weight

        # MA-Stapel
        if ma50 and ma200:
            if close[-1] > ma50 > ma200:
                score += 10
            elif close[-1] < ma50 < ma200:
                score -= 10

        # RSI
        if rsi:
            if rsi < 30:
                score += 8   # überverkauft → Erholung wahrscheinlich
            elif rsi > 70:
                score -= 8   # überkauft → Korrektur wahrscheinlich
            elif 40 <= rsi <= 60:
                score += 3   # neutraler RSI = stabiles Momentum

        # Volumen bestätigt Trend
        if volume_trend == "RISING" and score > 50:
            score += 5
        elif volume_trend == "RISING" and score < 50:
            score -= 5  # Volumen steigt bei Abwärtsbewegung = Druck

        return max(0.0, min(100.0, score))

    def _primary_signal(self, score: float) -> str:
        if score >= 75: return "STRONG_BUY"
        if score >= 60: return "BUY"
        if score >= 40: return "NEUTRAL"
        if score >= 25: return "SELL"
        return "STRONG_SELL"
