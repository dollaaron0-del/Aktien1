"""
Feature extraction for the ML score calibration model.

All features are computed from an OHLCV DataFrame at a specific row index
(the signal day). They are dimensionless ratios or bounded values so that
the model is scale-invariant and generalises across price levels.

Features (8 total):
  rsi          – RSI(14): momentum / overbought indicator
  ema_dev      – (close - EMA21) / EMA21: how far above EMA21 (lower = better entry)
  vol_ratio    – today's volume / 20-day avg volume: confirmation strength
  mom_5d       – 5-day return: short-term trend
  mom_20d      – 20-day return: medium-term trend
  atr_pct      – ATR(14) / close: normalised volatility (higher = wider swings)
  dist_52w_hi  – (52w_high - close) / 52w_high: room to run upward
  crossover    – 1 if fresh EMA21 crossover, 0 if pullback re-entry
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

FEATURE_NAMES = [
    "rsi", "ema_dev", "vol_ratio", "mom_5d", "mom_20d",
    "atr_pct", "dist_52w_hi", "crossover",
]


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, prev_close = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Add all indicator columns to a copy of df. Call once per ticker."""
    df = df.copy()
    df["ema21"]     = _ema(df["Close"], 21)
    df["rsi"]       = _rsi(df["Close"], 14)
    df["vol_avg20"] = df["Volume"].rolling(20).mean()
    df["atr"]       = _atr(df, 14)
    df["above_ema"] = df["Close"] >= df["ema21"]
    df["52w_high"]  = df["High"].rolling(252, min_periods=60).max()
    return df.dropna()


def extract_features(df: pd.DataFrame, i: int) -> Optional[Dict[str, float]]:
    """
    Extract ML features for the signal at row i of a *prepared* DataFrame.
    Returns None if not enough history exists.
    """
    if i < 21 or i < 1:
        return None

    row  = df.iloc[i]
    prev = df.iloc[i - 1]

    close    = float(row["Close"])
    ema21    = float(row["ema21"])
    vol_avg  = float(row["vol_avg20"])
    w52_high = float(row["52w_high"])

    if ema21 == 0 or vol_avg == 0 or close == 0:
        return None

    mom_5d = (close / float(df.iloc[max(i - 5, 0)]["Close"]) - 1) if i >= 5 else 0.0
    mom_20d = (close / float(df.iloc[max(i - 20, 0)]["Close"]) - 1) if i >= 20 else 0.0

    crossover_flag = 1.0 if (not prev["above_ema"] and row["above_ema"]) else 0.0

    return {
        "rsi":         float(row["rsi"]),
        "ema_dev":     (close - ema21) / ema21,
        "vol_ratio":   float(row["Volume"]) / vol_avg,
        "mom_5d":      mom_5d,
        "mom_20d":     mom_20d,
        "atr_pct":     float(row["atr"]) / close,
        "dist_52w_hi": (w52_high - close) / w52_high if w52_high > 0 else 0.0,
        "crossover":   crossover_flag,
    }


def features_to_array(feat: Dict[str, float]) -> list:
    """Return feature dict as an ordered list matching FEATURE_NAMES."""
    return [feat[k] for k in FEATURE_NAMES]
