"""
ML-Meta-Labeling der mechanischen Signale (Roadmap 6.5b).

WAS DAS IST — und was nicht: das Modell lernt NICHT, Kurse vorherzusagen
(78 gelabelte echte Trades + Random-Walk-Preise, das wäre Overfitting mit
Ansage — bewusst verworfen, siehe 6.5d). Es lernt stattdessen, WELCHE der
mechanischen Signale (Donchian/RSI/…-Entries aus dem Backtest) im Nachhinein
Gewinner waren, aus Kontext-Merkmalen, die zum Signalzeitpunkt bekannt waren:
Regime, Trailing-Volatilität, Trailing-Rendite, Markt-Breadth. Trainiert auf
zehntausenden Backtest-Signalausgängen (nicht auf den 78 echten Trades — die
bleiben Validierung, nicht Training, sobald der Bot wieder läuft, Roadmap
6.8c). Gradient Boosting zuerst (CPU reicht, interpretierbar) — NN/GPU nur,
falls das hier überhaupt eine Kante zeigt.

FEATURES BEWUSST OHNE Quellen-Kontext: die Roadmap nennt zusätzlich
"Quellen-Kontext" als Feature — das existiert nur live (analysis_log), nicht
im reinen Backtest. Hier also ehrlich nur, was aus Preis-Historie ableitbar
ist; Quellen-Kontext kommt erst dazu, wenn echte Live-Historie vorliegt.

VALIDIERUNG NACH 6.4-PROTOKOLL: expandierendes Fenster über Zeitblöcke
(gleiche make_blocks()-Mechanik wie cpcv.py) statt zufälligem Split — sonst
lernt/testet das Modell mit Zukunftsdaten. Mehrere P(Win)-Schwellen werden
verglichen (welcher Filter verbessert die Ø-Rendite ggü. ungefiltert?) →
das ist selbst wieder eine kleine Multiple-Testing-Situation und bekommt
dieselbe Šidák-Korrektur + Bootstrap-CI wie Walk-Forward/CPCV. Ein jüngster
Datenschwanz wird als Holdout ausgespart und über das bestehende
Zugriffsprotokoll (anti_overfit.log_holdout_access) mitgezählt.

Feature-Importance (Permutation) ist BEWUSST NICHT Teil dieses Moduls —
Roadmap 6.9(g) sieht sie als eigenen (rechenintensiveren) GPU-Routine-Punkt
vor; hier nur die Kern-Frage: hilft das Filtern überhaupt?

Reine numpy/pandas/sklearn-Analyse, kein LLM, kein Live-Eingriff — advisory
Naht folgt erst, wenn ein Signal belegt ist (Muster: strategy_lab/live_bridge.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from backtesting import data_loader
from strategy_lab.anti_overfit import (log_holdout_access,
                                       passes_multiple_testing, sidak_alpha)
from strategy_lab.cpcv import make_blocks
from strategy_lab.regime import classify_window
from strategy_lab.strategies import Strategy, get
from strategy_lab.walkforward import _bootstrap_ci

_FEATURE_NUMERIC = ["trailing_vol", "trailing_ret", "breadth"]
_FEATURE_CATEGORICAL = ["regime", "strategy"]
_MIN_HISTORY_BARS = 30
_DEFAULT_THRESHOLDS = (0.50, 0.55, 0.60, 0.65)


def _default_params(strategy: Strategy) -> Dict:
    """Erster Wert je Parameter — Meta-Labeling lernt über GEGEBENEN
    mechanischen Signalen, ist kein zweiter Grid-Search."""
    return {k: v[0] for k, v in strategy.param_space.items()}


def _breadth_at(full: Dict[str, pd.DataFrame], at_date, lookback_days: int = 20) -> float:
    """Anteil Ticker mit positiver Trailing-Rendite zum Stichtag (Markt-
    Breadth-Proxy). Nur Daten VOR at_date — kein Look-Ahead. 0.5 (neutral)
    ohne genug Daten."""
    up = total = 0
    for df in full.values():
        hist = df.loc[df.index < at_date]
        if len(hist) < lookback_days + 1:
            continue
        window = hist["Close"].tail(lookback_days + 1)
        total += 1
        if window.iloc[-1] / window.iloc[0] - 1 > 0:
            up += 1
    return up / total if total else 0.5


def entry_features(full: Dict[str, pd.DataFrame], ticker: str, at_date,
                   lookback_regime_days: int = 90, lookback_vol_days: int = 20,
                   lookback_breadth_days: int = 20) -> Optional[Dict]:
    """Kontext-Merkmale zum Signalzeitpunkt — ausschließlich aus Daten VOR
    at_date (df.loc[df.index < at_date]), damit kein Feature Zukunftswissen
    trägt. None, wenn zu wenig Historie vorliegt (Trade wird dann verworfen,
    nicht mit Platzhaltern aufgefüllt)."""
    df = full.get(ticker)
    if df is None:
        return None
    hist = df.loc[df.index < at_date]
    if len(hist) < _MIN_HISTORY_BARS:
        return None

    regime_window = hist.tail(lookback_regime_days)
    regime = classify_window({ticker: regime_window}) if len(regime_window) >= 20 else "UNKNOWN"

    vol_slice = hist["Close"].tail(lookback_vol_days + 1).pct_change().dropna()
    trailing_vol = float(vol_slice.std()) if len(vol_slice) >= 5 else 0.0

    ret_window = hist["Close"].tail(min(lookback_vol_days, len(hist) - 1) + 1)
    trailing_ret = float(ret_window.iloc[-1] / ret_window.iloc[0] - 1) if len(ret_window) >= 2 else 0.0

    breadth = _breadth_at(full, at_date, lookback_breadth_days)
    return {"regime": regime, "trailing_vol": trailing_vol,
            "trailing_ret": trailing_ret, "breadth": breadth}


def build_training_rows(
    strategies: List[Strategy | str],
    universe: List[str],
    total_years: int = 20,
    loader: Callable[[str, int], object] = data_loader.load,
    params_by_strategy: Optional[Dict[str, Dict]] = None,
) -> pd.DataFrame:
    """Ein Zeile je Backtest-Trade über alle (Strategie, Ticker)-Kombinationen:
    Kontext-Merkmale zum Entry + Ausgang (win/return_pct). Trainingsdaten für
    train_and_evaluate() — NICHT die 78 echten Trades (die bleiben Validierung,
    Roadmap 6.8c)."""
    resolved = [get(s) if isinstance(s, str) else s for s in strategies]
    full: Dict[str, pd.DataFrame] = {}
    for t in universe:
        df = loader(t, total_years)
        if df is not None and len(df) >= 252:
            full[t] = df

    rows: List[Dict] = []
    for strat in resolved:
        params = (params_by_strategy or {}).get(strat.name) or _default_params(strat)
        for ticker, df in full.items():
            trades = strat.runner(df, ticker, params)
            for tr in trades:
                feats = entry_features(full, ticker, tr.entry_date)
                if feats is None:
                    continue
                rows.append({
                    "ticker": ticker, "strategy": strat.name, "entry_date": tr.entry_date,
                    **feats, "return_pct": tr.return_pct, "win": int(tr.return_pct > 0),
                })
    return pd.DataFrame(rows)


def _encode(df: pd.DataFrame, categorical: tuple,
           columns: Optional[List[str]] = None) -> pd.DataFrame:
    """One-Hot der Kategorie-Spalten + numerische Spalten. `columns` fixiert
    die Zielspalten (Vorhersagezeit: exakt die Trainingsspalten — fehlende
    Dummy-Spalten werden 0, unbekannte Kategorien fallen weg, kein Leck
    künftiger Labels als neue Spalte)."""
    cat = pd.get_dummies(df[list(categorical)], prefix=list(categorical))
    x = pd.concat([df[_FEATURE_NUMERIC].reset_index(drop=True), cat.reset_index(drop=True)], axis=1)
    if columns is not None:
        x = x.reindex(columns=columns, fill_value=0)
    return x


def _design_matrix(train_df: pd.DataFrame, test_df: pd.DataFrame,
                   categorical: tuple = _FEATURE_CATEGORICAL):
    """One-Hot über die Train-Kategorien; Test-Kategorien, die im Train nicht
    vorkamen, fallen weg (kein Leck künftiger Regime-Labels als neue Spalte)."""
    x_train = _encode(train_df, categorical)
    x_test = _encode(test_df, categorical, columns=list(x_train.columns))
    return x_train, x_test


@dataclass
class ThresholdResult:
    threshold: float
    n_splits: int                  # Blöcke, in denen die Schwelle genug Treffer hatte
    mean_edge_gain: float          # Ø(gefilterter Test-Return − aller Test-Return) über Blöcke
    edge_gain_ci_lo: float
    edge_gain_ci_hi: float
    p_le0: float
    n_thresholds_tested: int
    alpha_adjusted: float
    verdict: str                   # "SIGNAL" | "NO_SIGNAL"


@dataclass
class MetaLabelReport:
    n_rows: int
    n_train_pool_rows: int         # nach Holdout-Abzug
    holdout_years: int
    n_blocks: int
    baseline_win_rate: float
    baseline_avg_return: float
    mean_auc: float
    mean_brier: float
    n_eval_splits: int
    thresholds: List[ThresholdResult] = field(default_factory=list)


def _empty_report(n_rows: int, holdout_years: int, n_blocks: int) -> MetaLabelReport:
    return MetaLabelReport(n_rows=n_rows, n_train_pool_rows=0, holdout_years=holdout_years,
                           n_blocks=n_blocks, baseline_win_rate=0.0, baseline_avg_return=0.0,
                           mean_auc=0.0, mean_brier=0.0, n_eval_splits=0)


def evaluate_meta_labeling(
    df: pd.DataFrame,
    n_blocks: int = 6,
    holdout_years: int = 2,
    thresholds: tuple = _DEFAULT_THRESHOLDS,
    min_train_rows: int = 200,
    min_test_rows: int = 30,
    min_filtered_rows: int = 5,
    seed: int = 20260712,
) -> MetaLabelReport:
    """Expandierendes Fenster über n_blocks Zeitblöcke (make_blocks, wie
    CPCV): Block i testet, alles davor trainiert. Je Schwelle wird über die
    Blöcke gemittelt, ob P(Win)≥Schwelle die Ø-Rendite ggü. ungefiltert
    verbessert — Bootstrap-CI + Šidák-Korrektur über die Zahl der Schwellen
    (dieselbe Statistik-Maschinerie wie walkforward.py/cpcv.py)."""
    if df.empty:
        return _empty_report(0, holdout_years, n_blocks)

    d = df.copy()
    d["entry_date"] = pd.to_datetime(d["entry_date"])
    d = d.sort_values("entry_date").reset_index(drop=True)

    end = d["entry_date"].max()
    if holdout_years > 0:
        cut = end - pd.DateOffset(years=holdout_years)
        held = d[d["entry_date"] >= cut]
        d = d[d["entry_date"] < cut]
        if len(held) > 0:
            log_holdout_access("meta_label", str(cut.date()), str(end.date()),
                               note=f"{len(held)} Zeilen reserviert")

    if d.empty or len(d) < min_train_rows:
        return _empty_report(len(df), holdout_years, n_blocks)

    start = d["entry_date"].min()
    blocks = make_blocks(start, d["entry_date"].max() + pd.Timedelta(days=1), n_blocks)

    threshold_gains: Dict[float, List[float]] = {th: [] for th in thresholds}
    aucs: List[float] = []
    briers: List[float] = []
    n_eval_splits = 0

    for i in range(1, n_blocks):
        test_start, test_end = blocks[i]
        train_df = d[d["entry_date"] < test_start]
        test_df = d[(d["entry_date"] >= test_start) & (d["entry_date"] < test_end)]
        if len(train_df) < min_train_rows or len(test_df) < min_test_rows:
            continue

        x_train, x_test = _design_matrix(train_df, test_df)
        y_train = train_df["win"].to_numpy()
        if len(set(y_train)) < 2:
            continue                                    # ein Modell auf nur einer Klasse ist sinnlos
        model = HistGradientBoostingClassifier(random_state=seed, max_depth=4)
        model.fit(x_train, y_train)
        proba = model.predict_proba(x_test)[:, 1]
        n_eval_splits += 1

        y_test = test_df["win"].to_numpy()
        if len(set(y_test)) > 1:
            aucs.append(float(roc_auc_score(y_test, proba)))
        briers.append(float(brier_score_loss(y_test, proba)))

        baseline_avg = float(test_df["return_pct"].mean())
        test_returns = test_df["return_pct"].to_numpy()
        for th in thresholds:
            mask = proba >= th
            if mask.sum() >= min_filtered_rows:
                filtered_avg = float(test_returns[mask].mean())
                threshold_gains[th].append(filtered_avg - baseline_avg)

    n_th = len(thresholds)
    results: List[ThresholdResult] = []
    for th in thresholds:
        gains = threshold_gains[th]
        if not gains:
            results.append(ThresholdResult(th, 0, 0.0, 0.0, 0.0, 1.0, n_th,
                                           round(sidak_alpha(n_th), 6), "NO_SIGNAL"))
            continue
        lo, hi, p_le0 = _bootstrap_ci(gains)
        verdict = "SIGNAL" if (lo > 0 and passes_multiple_testing(p_le0, n_th)) else "NO_SIGNAL"
        results.append(ThresholdResult(
            threshold=th, n_splits=len(gains), mean_edge_gain=round(float(np.mean(gains)), 4),
            edge_gain_ci_lo=round(lo, 4), edge_gain_ci_hi=round(hi, 4), p_le0=round(p_le0, 3),
            n_thresholds_tested=n_th, alpha_adjusted=round(sidak_alpha(n_th), 6), verdict=verdict,
        ))

    return MetaLabelReport(
        n_rows=len(df), n_train_pool_rows=len(d), holdout_years=holdout_years, n_blocks=n_blocks,
        baseline_win_rate=round(float(d["win"].mean()), 4),
        baseline_avg_return=round(float(d["return_pct"].mean()), 4),
        mean_auc=round(float(np.mean(aucs)), 4) if aucs else 0.0,
        mean_brier=round(float(np.mean(briers)), 4) if briers else 0.0,
        n_eval_splits=n_eval_splits, thresholds=results,
    )
