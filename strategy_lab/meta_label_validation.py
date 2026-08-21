"""
Meta-Labeling-Modell gegen ECHTE Live-Trades validieren — Roadmap 6.8(c).

6.5b trainiert/testet das Meta-Labeling-Modell ausschließlich auf zehntausenden
BACKTEST-Signalausgängen ("Label-Vervielfachung" gegen die kleine Stichprobe,
6.2-(3)) — bewusst NICHT auf den paar Dutzend echten Bot-Trades. Die sollen
laut Roadmap "Validierung bleiben, nicht Training". Dieses Modul ist genau
diese Validierung: ein auf ALLEN Backtest-Zeilen trainiertes Modell (kein
Block-Split nötig — die Live-Trades SIND bereits der Holdout, per Konstruktion
zeitlich später und aus einer anderen Datenquelle) wird auf die Kontext-
Merkmale echter Trades zum jeweiligen Entscheidungszeitpunkt angewendet.

KATEGORIE-LÜCKE bewusst gelöst statt ignoriert: die mechanischen Signal-
familien (Donchian/RSI/…) aus strategy_lab haben KEIN Live-Analogon —
echte Trades entstehen aus der KI-Analyse (Sentiment/News), nicht aus einem
der backtesteten Regelwerke. Ein Live-Trade trägt also keinen sinnvollen
"strategy"-Wert. Statt hier zu raten (z.B. eine erfundene Pseudo-Strategie
zuzuweisen), wird NUR mit dem strategie-freien Kontext-Feature-Set trainiert
(Regime/Trailing-Vola/Trailing-Rendite/Breadth — exakt das in 6.8(c) benannte
Set) — dieselbe entry_features()-Funktion wie beim Backtest-Training, also
strikt vergleichbare Merkmale.

Mindest-n-Gate (wie reanalysis_judge/skip_counterfactual): mit den heute noch
wenigen echten Trades ist ein belastbares Verdikt unwahrscheinlich — das ist
ein ehrliches Ergebnis, keine Bug-Vermutung.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

from backtesting import data_loader
from strategy_lab.meta_label import _FEATURE_NUMERIC, _encode, entry_features

CONTEXT_CATEGORICAL = ("regime",)
DEFAULT_THRESHOLDS = (0.50, 0.55, 0.60, 0.65)
MIN_N_FOR_VERDICT = 15


@dataclass
class LiveThresholdResult:
    threshold: float
    n: int                     # Live-Trades mit P(Win) >= Schwelle
    win_rate: float
    mean_pnl_pct: float        # Fraktion, z.B. 0.02 = +2%
    pnl_ci_lo: float
    pnl_ci_hi: float
    p_le0: float
    verdict: str                # "SIGNAL" | "NO_SIGNAL" | "ZU_WENIG_DATEN"


@dataclass
class LiveValidationReport:
    n_train_rows: int
    n_live_total: int          # alle gelabelten Live-Zeilen
    n_live_scored: int         # davon mit genug Preis-Historie für Kontext-Features
    baseline_win_rate: float
    baseline_mean_pnl_pct: float
    auc: Optional[float]
    brier: Optional[float]
    min_n: int
    thresholds: List[LiveThresholdResult] = field(default_factory=list)


def _empty_report(n_train_rows: int, n_live_total: int, min_n: int) -> LiveValidationReport:
    return LiveValidationReport(
        n_train_rows=n_train_rows, n_live_total=n_live_total, n_live_scored=0,
        baseline_win_rate=0.0, baseline_mean_pnl_pct=0.0, auc=None, brier=None,
        min_n=min_n,
    )


def collect_live_rows(
    store,
    loader: Callable[[str, int], object] = data_loader.load,
    total_years: int = 20,
) -> pd.DataFrame:
    """Ein Zeile je gelabeltem ECHTEN Trade (label_source='live') mit
    Kontext-Merkmalen zum Entscheidungszeitpunkt (entry_features — dieselbe
    Funktion wie beim Backtest-Training). Trades ohne genug Preis-Historie vor
    dem Entscheidungszeitpunkt oder ohne verwertbaren Outcome werden verworfen
    (kein Auffüllen mit Platzhaltern)."""
    full: Dict[str, object] = {}
    rows: List[Dict] = []
    for features, out in store.iter_labeled(label_source="live"):
        ticker = features.get("ticker")
        decided_at = features.get("decided_at")
        pnl_pct = out.get("pnl_pct")
        outcome = out.get("outcome")
        if not ticker or not decided_at or pnl_pct is None or outcome not in ("WIN", "LOSS"):
            continue
        if ticker not in full:
            full[ticker] = loader(ticker, total_years)
        df = full.get(ticker)
        if df is None:
            continue
        feats = entry_features(full, ticker, pd.Timestamp(decided_at))
        if feats is None:
            continue
        rows.append({
            "ticker": ticker, "decided_at": decided_at, **feats,
            "pnl_pct": float(pnl_pct) / 100.0,          # Store: Prozent -> Fraktion
            "win": int(outcome == "WIN"),
        })
    return pd.DataFrame(rows)


def validate_against_live(
    train_df: pd.DataFrame,
    live_df: pd.DataFrame,
    thresholds: tuple = DEFAULT_THRESHOLDS,
    min_n: int = MIN_N_FOR_VERDICT,
    seed: int = 20260809,
) -> LiveValidationReport:
    """Trainiert EIN Modell auf dem gesamten Backtest-Trainingspool (kein
    Block-Split — live_df ist bereits der Holdout) mit dem strategie-freien
    Kontext-Feature-Set, wendet es auf die echten Trades an."""
    n_train_rows = len(train_df)
    n_live_total = len(live_df)
    if train_df.empty or live_df.empty or len(set(train_df["win"])) < 2:
        return _empty_report(n_train_rows, n_live_total, min_n)

    x_train = _encode(train_df, CONTEXT_CATEGORICAL)
    y_train = train_df["win"].to_numpy()
    model = HistGradientBoostingClassifier(random_state=seed, max_depth=4)
    model.fit(x_train, y_train)

    x_live = _encode(live_df, CONTEXT_CATEGORICAL, columns=list(x_train.columns))
    proba = model.predict_proba(x_live)[:, 1]
    y_live = live_df["win"].to_numpy()
    pnl_live = live_df["pnl_pct"].to_numpy()

    auc = float(roc_auc_score(y_live, proba)) if len(set(y_live)) > 1 else None
    brier = float(brier_score_loss(y_live, proba))

    rng = np.random.default_rng(seed)
    results: List[LiveThresholdResult] = []
    for th in thresholds:
        mask = proba >= th
        n = int(mask.sum())
        if n < min_n:
            results.append(LiveThresholdResult(th, n, 0.0, 0.0, 0.0, 0.0, 1.0, "ZU_WENIG_DATEN"))
            continue
        pnls = pnl_live[mask]
        win_rate = float((pnls > 0).mean())
        idx = rng.integers(0, n, size=(2000, n))
        means = pnls[idx].mean(axis=1)
        lo, hi = np.percentile(means, [5, 95])
        p_le0 = float((means <= 0).mean())
        verdict = "SIGNAL" if lo > 0 else "NO_SIGNAL"
        results.append(LiveThresholdResult(
            threshold=th, n=n, win_rate=round(win_rate, 4),
            mean_pnl_pct=round(float(pnls.mean()), 4), pnl_ci_lo=round(float(lo), 4),
            pnl_ci_hi=round(float(hi), 4), p_le0=round(p_le0, 3), verdict=verdict,
        ))

    return LiveValidationReport(
        n_train_rows=n_train_rows, n_live_total=n_live_total, n_live_scored=len(live_df),
        baseline_win_rate=round(float(live_df["win"].mean()), 4),
        baseline_mean_pnl_pct=round(float(live_df["pnl_pct"].mean()), 4),
        auc=round(auc, 4) if auc is not None else None,
        brier=round(brier, 4), min_n=min_n, thresholds=results,
    )
