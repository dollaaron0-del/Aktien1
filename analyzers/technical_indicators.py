"""
Technische Indikatoren – RSI, MACD, Bollinger Bands, EMA, ATR
Werden aus yfinance-Kursdaten berechnet und als strukturierter String
an den Claude-Analyzer übergeben.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any
import yfinance as yf
import pandas as pd


@dataclass
class TechnicalSnapshot:
    ticker: str
    rsi_14: Optional[float]
    macd: Optional[float]
    macd_signal: Optional[float]
    macd_hist: Optional[float]
    bb_upper: Optional[float]
    bb_middle: Optional[float]
    bb_lower: Optional[float]
    bb_pct: Optional[float]          # (price - lower) / (upper - lower), 0–1
    ema_9: Optional[float]
    ema_21: Optional[float]
    ema_50: Optional[float]
    atr_14: Optional[float]
    volume_ratio: Optional[float]    # current volume / 20-day avg volume
    trend: str                       # "UPTREND" | "DOWNTREND" | "SIDEWAYS"
    price: Optional[float]

    def to_prompt_block(self) -> str:
        """Formatiert die Indikatoren als lesbarer Text-Block für Claude."""
        lines = [f"=== TECHNISCHE INDIKATOREN ({self.ticker}) ==="]

        if self.rsi_14 is not None:
            rsi_label = "überkauft" if self.rsi_14 > 70 else ("überverkauft" if self.rsi_14 < 30 else "neutral")
            lines.append(f"RSI(14): {self.rsi_14:.1f}  [{rsi_label}]")

        if self.macd is not None:
            hist_dir = "positiv" if (self.macd_hist or 0) > 0 else "negativ"
            lines.append(
                f"MACD: {self.macd:.3f}  Signal: {self.macd_signal:.3f}  "
                f"Hist: {self.macd_hist:.3f}  [{hist_dir}]"
            )

        if self.bb_upper is not None and self.bb_pct is not None:
            bb_label = "oben (überdehnt)" if self.bb_pct > 0.8 else ("unten (überverkauft)" if self.bb_pct < 0.2 else "mittig")
            lines.append(
                f"Bollinger Bands: U={self.bb_upper:.2f}  M={self.bb_middle:.2f}  "
                f"L={self.bb_lower:.2f}  %B={self.bb_pct:.2f}  [{bb_label}]"
            )

        ema_parts = []
        if self.ema_9:
            ema_parts.append(f"EMA9={self.ema_9:.2f}")
        if self.ema_21:
            ema_parts.append(f"EMA21={self.ema_21:.2f}")
        if self.ema_50:
            ema_parts.append(f"EMA50={self.ema_50:.2f}")
        if ema_parts:
            lines.append("EMAs: " + "  ".join(ema_parts))

        if self.atr_14 is not None and self.price:
            atr_pct = self.atr_14 / self.price * 100
            lines.append(f"ATR(14): {self.atr_14:.2f}  ({atr_pct:.1f}% des Kurses)")

        if self.volume_ratio is not None:
            vol_label = "hoch" if self.volume_ratio > 1.5 else ("niedrig" if self.volume_ratio < 0.5 else "normal")
            lines.append(f"Volumen-Ratio (vs 20T-Ø): {self.volume_ratio:.2f}x  [{vol_label}]")

        lines.append(f"Trend (EMA-Stapel): {self.trend}")
        lines.append("=" * 50)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rsi_14": self.rsi_14,
            "macd": self.macd,
            "macd_signal": self.macd_signal,
            "macd_hist": self.macd_hist,
            "bb_upper": self.bb_upper,
            "bb_middle": self.bb_middle,
            "bb_lower": self.bb_lower,
            "bb_pct": self.bb_pct,
            "ema_9": self.ema_9,
            "ema_21": self.ema_21,
            "ema_50": self.ema_50,
            "atr_14": self.atr_14,
            "volume_ratio": self.volume_ratio,
            "trend": self.trend,
        }


class TechnicalIndicators:

    def calculate(self, ticker: str, period: str = "3mo") -> Optional[TechnicalSnapshot]:
        try:
            df = yf.Ticker(ticker).history(period=period)
        except Exception:
            return None

        if df is None or len(df) < 26:
            return None

        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        volume = df["Volume"]

        price = round(float(close.iloc[-1]), 2)

        rsi = self._rsi(close, 14)
        macd_line, signal_line, hist = self._macd(close)
        bb_upper, bb_mid, bb_lower = self._bollinger(close, 20)
        ema9 = self._ema(close, 9)
        ema21 = self._ema(close, 21)
        ema50 = self._ema(close, 50) if len(close) >= 50 else None
        atr = self._atr(high, low, close, 14)
        vol_ratio = self._volume_ratio(volume, 20)

        # Trend determination from EMA stack
        trend = "SIDEWAYS"
        if ema9 and ema21:
            if ema9 > ema21:
                trend = "UPTREND" if (ema50 is None or ema21 > ema50) else "UPTREND (partial)"
            else:
                trend = "DOWNTREND" if (ema50 is None or ema21 < ema50) else "DOWNTREND (partial)"

        bb_pct = None
        if bb_upper is not None and bb_lower is not None and (bb_upper - bb_lower) > 0:
            bb_pct = round((price - bb_lower) / (bb_upper - bb_lower), 3)

        return TechnicalSnapshot(
            ticker=ticker,
            price=price,
            rsi_14=round(rsi, 1) if rsi is not None else None,
            macd=round(float(macd_line), 4) if macd_line is not None else None,
            macd_signal=round(float(signal_line), 4) if signal_line is not None else None,
            macd_hist=round(float(hist), 4) if hist is not None else None,
            bb_upper=round(float(bb_upper), 2) if bb_upper is not None else None,
            bb_middle=round(float(bb_mid), 2) if bb_mid is not None else None,
            bb_lower=round(float(bb_lower), 2) if bb_lower is not None else None,
            bb_pct=bb_pct,
            ema_9=round(float(ema9), 2) if ema9 is not None else None,
            ema_21=round(float(ema21), 2) if ema21 is not None else None,
            ema_50=round(float(ema50), 2) if ema50 is not None else None,
            atr_14=round(float(atr), 2) if atr is not None else None,
            volume_ratio=round(float(vol_ratio), 2) if vol_ratio is not None else None,
            trend=trend,
        )

    # ── Indicator helpers ────────────────────────────────────────────────────

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> Optional[float]:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, float("inf"))
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return float(val) if pd.notna(val) else None

    @staticmethod
    def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        sig = macd.ewm(span=signal, adjust=False).mean()
        hist = macd - sig
        if pd.isna(macd.iloc[-1]):
            return None, None, None
        return macd.iloc[-1], sig.iloc[-1], hist.iloc[-1]

    @staticmethod
    def _bollinger(close: pd.Series, period: int = 20):
        mid = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        if pd.isna(mid.iloc[-1]):
            return None, None, None
        return upper.iloc[-1], mid.iloc[-1], lower.iloc[-1]

    @staticmethod
    def _ema(close: pd.Series, period: int) -> Optional[float]:
        if len(close) < period:
            return None
        val = close.ewm(span=period, adjust=False).mean().iloc[-1]
        return float(val) if pd.notna(val) else None

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> Optional[float]:
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(com=period - 1, min_periods=period).mean()
        val = atr.iloc[-1]
        return float(val) if pd.notna(val) else None

    @staticmethod
    def _volume_ratio(volume: pd.Series, period: int = 20) -> Optional[float]:
        if len(volume) < period:
            return None
        avg = volume.iloc[-period:].mean()
        if avg == 0:
            return None
        return float(volume.iloc[-1] / avg)
