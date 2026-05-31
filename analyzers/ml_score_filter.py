"""
MLScoreFilter — integrates ML calibration into the bot's signal flow.

Drop-in, non-breaking: if the model hasn't been trained yet (data/ml_score_model.joblib
doesn't exist), calibrate() silently returns the original score unchanged.

Typical usage in runner.py (after analysis is returned):
    from analyzers.ml_score_filter import ml_score_filter
    score = ml_score_filter.calibrate(analysis.sentiment_score, ticker)
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import yfinance as yf

from ml.predictor import MLScorePredictor
from logger import get_logger

log = get_logger(__name__)

_OHLCV_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}  # ticker → (ts, df)
_CACHE_TTL = 3600  # 1 hour


def _get_ohlcv(ticker: str) -> Optional[pd.DataFrame]:
    """Fetch 60 days of OHLCV for a ticker, cached for 1h."""
    now = time.time()
    if ticker in _OHLCV_CACHE:
        ts, df = _OHLCV_CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return df

    try:
        raw = yf.download(ticker, period="60d", progress=False, auto_adjust=True)
        if raw.empty:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) < 25:
            return None
        _OHLCV_CACHE[ticker] = (now, df)
        return df
    except Exception as e:
        log.debug("MLScoreFilter: OHLCV fetch error for %s: %s", ticker, e)
        return None


class MLScoreFilter:
    """Singleton-safe wrapper; instantiate once at module level."""

    def __init__(self) -> None:
        self._predictor = MLScorePredictor()

    @property
    def is_active(self) -> bool:
        return self._predictor.is_available()

    def calibrate(self, base_score: float, ticker: str) -> float:
        """
        Return ML-calibrated score for *ticker*.

        Falls back to *base_score* if:
          - model not trained yet
          - OHLCV data unavailable
          - any error occurs
        """
        if not self._predictor.is_available():
            return base_score

        df = _get_ohlcv(ticker)
        if df is None:
            return base_score

        return self._predictor.calibrate(base_score, df)

    def feature_importances(self) -> Optional[dict]:
        return self._predictor.feature_importances()

    def reload_model(self) -> bool:
        return self._predictor.reload()


# Module-level singleton — import this in runner.py
ml_score_filter = MLScoreFilter()
