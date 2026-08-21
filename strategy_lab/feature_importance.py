"""
Permutation-Importance für das Meta-Labeling-Modell — Roadmap 6.9(g).

Bewusst aus strategy_lab/meta_label.py ausgelagert (dort: "Feature-
Importance (Permutation) ist BEWUSST NICHT Teil dieses Moduls — Roadmap
6.9(g) sieht sie als eigenen (rechenintensiveren) Routine-Punkt vor").
sklearn.inspection.permutation_importance ist per Definition teurer als ein
einzelner fit() (n_repeats × n_features Neu-Vorhersagen je Block) — genau
die Art "teure Statistik", die laut Roadmap künftig fester Nachtlauf-
Bestandteil werden soll statt Sonderlauf.

Wiederverwendet exakt denselben expandierenden Block-Aufbau wie
evaluate_meta_labeling() (make_blocks/_design_matrix, kein zufälliger
Split, kein Look-Ahead, gleiches Holdout-Protokoll) statt eines einzelnen
Train/Test-Splits — konsistent mit dem übrigen 6.4-Anti-Overfit-Protokoll,
kein neuer Validierungsstil.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance

from strategy_lab.anti_overfit import log_holdout_access
from strategy_lab.cpcv import make_blocks
from strategy_lab.meta_label import _FEATURE_CATEGORICAL, _FEATURE_NUMERIC, _design_matrix


@dataclass
class FeatureImportanceResult:
    feature:          str
    mean_importance:  float   # Ø permutation_importance.importances_mean über Blöcke
    std_importance:   float   # Streuung über Blöcke (Konsistenz-Indikator, keine CI)
    n_blocks:         int     # Blöcke, in denen dieses Feature (One-Hot-Spalte) vorkam


@dataclass
class FeatureImportanceReport:
    n_blocks_evaluated: int
    scoring:            str
    n_repeats:           int
    features: List[FeatureImportanceResult] = field(default_factory=list)


def _empty_report(scoring: str, n_repeats: int) -> FeatureImportanceReport:
    return FeatureImportanceReport(n_blocks_evaluated=0, scoring=scoring, n_repeats=n_repeats)


def evaluate_feature_importance(
    df: pd.DataFrame,
    n_blocks: int = 6,
    holdout_years: int = 2,
    n_repeats: int = 10,
    scoring: str = "roc_auc",
    min_train_rows: int = 200,
    min_test_rows: int = 30,
    seed: int = 20260809,
) -> FeatureImportanceReport:
    """Ein HistGradientBoostingClassifier je Block (identischer Aufbau wie
    evaluate_meta_labeling), Permutation-Importance auf dem jeweiligen
    Test-Split. Über Blöcke gemittelt statt eines einzelnen Splits — ein
    Feature, das nur in einem Block wichtig wirkt, ist Rauschen, keine
    robuste Erkenntnis."""
    if df.empty:
        return _empty_report(scoring, n_repeats)

    d = df.copy()
    d["entry_date"] = pd.to_datetime(d["entry_date"])
    d = d.sort_values("entry_date").reset_index(drop=True)

    end = d["entry_date"].max()
    if holdout_years > 0:
        cut = end - pd.DateOffset(years=holdout_years)
        held = d[d["entry_date"] >= cut]
        d = d[d["entry_date"] < cut]
        if len(held) > 0:
            log_holdout_access("feature_importance", str(cut.date()), str(end.date()),
                               note=f"{len(held)} Zeilen reserviert")

    if d.empty or len(d) < min_train_rows:
        return _empty_report(scoring, n_repeats)

    start = d["entry_date"].min()
    blocks = make_blocks(start, d["entry_date"].max() + pd.Timedelta(days=1), n_blocks)

    per_feature: Dict[str, List[float]] = {}
    n_blocks_evaluated = 0

    for i in range(1, n_blocks):
        test_start, test_end = blocks[i]
        train_df = d[d["entry_date"] < test_start]
        test_df = d[(d["entry_date"] >= test_start) & (d["entry_date"] < test_end)]
        if len(train_df) < min_train_rows or len(test_df) < min_test_rows:
            continue

        x_train, x_test = _design_matrix(train_df, test_df)
        y_train = train_df["win"].to_numpy()
        y_test = test_df["win"].to_numpy()
        if len(set(y_train)) < 2 or len(set(y_test)) < 2:
            continue  # AUC/Importance auf einer einzigen Klasse ist bedeutungslos

        model = HistGradientBoostingClassifier(random_state=seed, max_depth=4)
        model.fit(x_train, y_train)

        result = permutation_importance(
            model, x_test, y_test, n_repeats=n_repeats,
            random_state=seed, scoring=scoring,
        )
        n_blocks_evaluated += 1
        for col, imp in zip(x_test.columns, result.importances_mean):
            per_feature.setdefault(col, []).append(float(imp))

    features = [
        FeatureImportanceResult(
            feature=name,
            mean_importance=round(float(np.mean(vals)), 5),
            std_importance=round(float(np.std(vals)), 5),
            n_blocks=len(vals),
        )
        for name, vals in per_feature.items()
    ]
    features.sort(key=lambda f: -f.mean_importance)

    return FeatureImportanceReport(
        n_blocks_evaluated=n_blocks_evaluated, scoring=scoring,
        n_repeats=n_repeats, features=features,
    )
