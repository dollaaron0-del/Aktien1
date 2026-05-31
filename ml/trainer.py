"""
ML Score Calibration Trainer.

Builds a labeled dataset from historical OHLCV data using the backtesting
engine, then trains a GradientBoostingClassifier to predict P(trade reaches TP1).

Dataset construction:
  For each ticker → load N years of OHLCV → find all EMA21 entry signals →
  extract 8 technical features → label by whether trade reached TP1.

Training:
  Stratified 5-fold cross-validation → reports CV AUC.
  Final model trained on full dataset → saved via joblib.

Usage (from scripts/train_ml_score.py):
  from ml.trainer import build_dataset, train_model, save_model
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np
import joblib
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backtesting.data_loader import load as load_ohlcv
from backtesting.engine import BacktestConfig, run as run_backtest, _prepare, _is_signal
from ml.feature_engineering import prepare, extract_features, features_to_array, FEATURE_NAMES
from logger import get_logger

log = get_logger(__name__)

_MODEL_PATH = Path(__file__).parent.parent / "data" / "ml_score_model.joblib"


def build_dataset(
    tickers: List[str],
    years: int = 5,
    bt_cfg: Optional[BacktestConfig] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Build (X, y) from historical data.

    X shape: (n_signals, 8 features)
    y: 1 if trade reached TP1, 0 otherwise
    """
    if bt_cfg is None:
        bt_cfg = BacktestConfig()

    X_rows, y_rows = [], []

    for ticker in tickers:
        df_raw = load_ohlcv(ticker, years=years)
        if df_raw is None or len(df_raw) < 60:
            log.debug("ML trainer: no data for %s", ticker)
            continue

        # Run backtest to get trade outcomes (engine uses its own internal _prepare)
        trades = run_backtest(df_raw, ticker, bt_cfg)
        if not trades:
            continue

        # Prepared df for the engine (same as bt_run uses internally)
        df_bt = _prepare(df_raw, bt_cfg)

        # Prepared df for ML features (different indicator set)
        df_feat = prepare(df_raw)

        # For each trade: go back one bar to the signal day, extract features
        for trade in trades:
            try:
                entry_pos = df_bt.index.get_loc(trade.entry_date)
            except KeyError:
                continue

            signal_pos = entry_pos - 1
            if signal_pos < 1:
                continue

            signal_date = df_bt.index[signal_pos]
            try:
                feat_pos = df_feat.index.get_loc(signal_date)
            except KeyError:
                continue

            feat = extract_features(df_feat, feat_pos)
            if feat is None:
                continue

            X_rows.append(features_to_array(feat))
            y_rows.append(1 if trade.tp1_hit else 0)

        log.debug("ML trainer: %s → %d trades collected", ticker, len(X_rows))

    if not X_rows:
        raise ValueError("No training samples collected. Check that tickers have data.")

    return np.array(X_rows, dtype=float), np.array(y_rows, dtype=int)


def train_model(X: np.ndarray, y: np.ndarray) -> Pipeline:
    """
    Train a GradientBoostingClassifier with cross-validation report.
    Falls back to LogisticRegression if sample count is very small.
    """
    n_samples = len(y)
    n_pos = int(y.sum())
    log.info("ML trainer: %d samples, %d positive (%.0f%%)",
             n_samples, n_pos, 100 * n_pos / n_samples)

    if n_samples < 30:
        log.warning("Very few samples (%d) – results may be unreliable.", n_samples)

    if n_samples >= 50:
        clf = GradientBoostingClassifier(
            n_estimators=200,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )
    else:
        clf = LogisticRegression(C=1.0, max_iter=500, random_state=42)

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf",    clf),
    ])

    # 5-fold stratified cross-validation
    cv = StratifiedKFold(n_splits=min(5, n_pos), shuffle=True, random_state=42)
    try:
        auc_scores = cross_val_score(pipe, X, y, cv=cv, scoring="roc_auc")
        log.info("ML trainer: CV ROC-AUC = %.3f ± %.3f", auc_scores.mean(), auc_scores.std())
    except Exception as e:
        log.warning("CV failed: %s", e)

    pipe.fit(X, y)
    return pipe


def save_model(pipe: Pipeline, path: Optional[Path] = None) -> Path:
    target = path or _MODEL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"pipeline": pipe, "features": FEATURE_NAMES}, target)
    log.info("ML model saved: %s", target)
    return target


def load_model(path: Optional[Path] = None) -> Optional[dict]:
    target = path or _MODEL_PATH
    if not target.exists():
        return None
    try:
        return joblib.load(target)
    except Exception as e:
        log.warning("ML model load failed: %s", e)
        return None
