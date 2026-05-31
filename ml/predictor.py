"""
ML Score Predictor — runtime inference for the trained calibration model.

Usage:
    predictor = MLScorePredictor()          # auto-loads model if available
    p_success  = predictor.predict(df)      # P(trade reaches TP1), 0–1
    calibrated = predictor.calibrate(0.72, df)  # blended score
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from ml.feature_engineering import prepare, extract_features, features_to_array
from ml.trainer import load_model
from logger import get_logger

log = get_logger(__name__)

# Weight: how much the ML probability influences the final score.
# 0.0 = ignore ML entirely (fallback), 1.0 = use ML only.
_ML_WEIGHT = 0.35


class MLScorePredictor:
    """
    Thin inference wrapper around the trained GradientBoostingClassifier.

    The predictor is lazy-loaded: if no model file exists the class
    behaves like a no-op and calibrate() returns the base score unchanged.
    This means the bot continues working normally before the first training run.
    """

    def __init__(self, model_path: Optional[Path] = None):
        self._pipe     = None
        self._loaded   = False
        self._model_path = model_path
        self._try_load()

    def is_available(self) -> bool:
        return self._pipe is not None

    def _try_load(self) -> None:
        artifact = load_model(self._model_path)
        if artifact is None:
            return
        self._pipe   = artifact["pipeline"]
        self._loaded = True
        log.info("MLScorePredictor: model loaded (%d features)", len(artifact["features"]))

    def reload(self) -> bool:
        """Hot-reload model from disk (e.g. after retraining)."""
        self._pipe   = None
        self._loaded = False
        self._try_load()
        return self._loaded

    def predict(self, df: pd.DataFrame) -> Optional[float]:
        """
        Return P(TP1 reached) for the most recent bar of an OHLCV DataFrame.

        Returns None if the model is not loaded or features can't be computed.
        The DataFrame must have at least 25 rows of daily OHLCV
        (Open, High, Low, Close, Volume).
        """
        if not self._loaded:
            return None

        try:
            prepared = prepare(df)
            if len(prepared) < 2:
                return None
            feat = extract_features(prepared, len(prepared) - 1)
            if feat is None:
                return None
            arr = np.array([features_to_array(feat)], dtype=float)
            prob = float(self._pipe.predict_proba(arr)[0, 1])
            return prob
        except Exception as e:
            log.debug("MLScorePredictor.predict error: %s", e)
            return None

    def calibrate(self, base_score: float, df: pd.DataFrame) -> float:
        """
        Blend the ML probability with the existing heuristic score.

        If the model is not available, returns base_score unchanged.
        Formula: (1 - ml_weight) * base_score + ml_weight * ml_prob
        """
        ml_prob = self.predict(df)
        if ml_prob is None:
            return base_score

        blended = (1 - _ML_WEIGHT) * base_score + _ML_WEIGHT * ml_prob
        log.debug("ML calibration: base=%.3f ml=%.3f → blended=%.3f",
                  base_score, ml_prob, blended)
        return round(blended, 4)

    def feature_importances(self) -> Optional[dict]:
        """Return feature importance dict (only for GradientBoosting)."""
        if not self._loaded:
            return None
        try:
            from ml.feature_engineering import FEATURE_NAMES
            clf = self._pipe.named_steps["clf"]
            importances = clf.feature_importances_
            return dict(sorted(zip(FEATURE_NAMES, importances),
                               key=lambda x: -x[1]))
        except Exception:
            return None
