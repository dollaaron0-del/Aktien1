"""
Tests für strategy_lab/feature_importance.py (Roadmap 6.9g: Permutation-
Importance fürs Meta-Labeling-Modell). Netzfrei, synthetische Historie
(gleiches Muster wie tests/test_meta_label.py::_synthetic_rows).
"""
import numpy as np
import pandas as pd

from strategy_lab.anti_overfit import holdout_access_count
from strategy_lab.feature_importance import evaluate_feature_importance


def _synthetic_rows(n=1200, seed=0, signal_strength=0.0):
    """trailing_vol trägt bei signal_strength>0 ein echtes, lernbares Signal
    (Positivkontrolle: muss als wichtigstes Feature auftauchen)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2005-01-01", periods=n, freq="3D")
    trailing_vol = rng.uniform(0.0, 1.0, n)
    logit = signal_strength * (trailing_vol - 0.5) + rng.normal(0, 1.0, n)
    p_win = 1 / (1 + np.exp(-logit))
    win = (rng.uniform(0, 1, n) < p_win).astype(int)
    base_ret = rng.normal(0.0, 0.02, n)
    return_pct = np.where(win == 1, np.abs(base_ret) + 0.001, -np.abs(base_ret) - 0.001)
    regimes = rng.choice(["BULL_CALM", "BEAR_CALM", "SIDE_CALM"], n)
    return pd.DataFrame({
        "ticker": "AAA", "strategy": "synthetic", "entry_date": dates,
        "regime": regimes, "trailing_vol": trailing_vol,
        "trailing_ret": rng.normal(0, 0.02, n), "breadth": rng.uniform(0, 1, n),
        "return_pct": return_pct, "win": win,
    })


def test_empty_df_is_empty_report():
    rep = evaluate_feature_importance(pd.DataFrame())
    assert rep.n_blocks_evaluated == 0
    assert rep.features == []


def test_too_few_rows_is_empty_report():
    df = _synthetic_rows(n=20)
    rep = evaluate_feature_importance(df, min_train_rows=200)
    assert rep.n_blocks_evaluated == 0


def test_deterministic():
    df = _synthetic_rows(n=800, seed=3, signal_strength=1.5)
    kw = dict(n_blocks=5, holdout_years=1, n_repeats=5)
    r1 = evaluate_feature_importance(df, **kw)
    r2 = evaluate_feature_importance(df, **kw)
    assert [f.mean_importance for f in r1.features] == [f.mean_importance for f in r2.features]


def test_holdout_logged(monkeypatch, tmp_path):
    monkeypatch.setenv("HOLDOUT_LOG_PATH", str(tmp_path / "holdout.json"))
    before = holdout_access_count("feature_importance")
    df = _synthetic_rows(n=800, seed=4, signal_strength=1.5)
    evaluate_feature_importance(df, n_blocks=5, holdout_years=1, n_repeats=5)
    assert holdout_access_count("feature_importance") == before + 1


def test_no_holdout_no_log(monkeypatch, tmp_path):
    monkeypatch.setenv("HOLDOUT_LOG_PATH", str(tmp_path / "holdout2.json"))
    df = _synthetic_rows(n=800, seed=5, signal_strength=1.5)
    evaluate_feature_importance(df, n_blocks=5, holdout_years=0, n_repeats=5)
    assert holdout_access_count("feature_importance") == 0


def test_detects_real_signal_positive_control():
    """trailing_vol trägt das injizierte Signal — muss als wichtigstes
    Feature auftauchen, nicht nur irgendein Feature mit Importance>0."""
    df = _synthetic_rows(n=2400, seed=7, signal_strength=3.0)
    rep = evaluate_feature_importance(df, n_blocks=8, holdout_years=1, n_repeats=8)
    assert rep.n_blocks_evaluated >= 3
    assert rep.features, "keine Features ausgewertet"
    top = rep.features[0]
    assert top.feature == "trailing_vol"
    assert top.mean_importance > 0


def test_pure_noise_runs_without_error_and_returns_features():
    df = _synthetic_rows(n=1600, seed=9, signal_strength=0.0)
    rep = evaluate_feature_importance(df, n_blocks=6, holdout_years=1, n_repeats=5)
    assert rep.n_blocks_evaluated >= 1
    assert len(rep.features) > 0


def test_features_sorted_descending_by_importance():
    df = _synthetic_rows(n=2400, seed=7, signal_strength=3.0)
    rep = evaluate_feature_importance(df, n_blocks=8, holdout_years=1, n_repeats=8)
    values = [f.mean_importance for f in rep.features]
    assert values == sorted(values, reverse=True)


def test_n_blocks_per_feature_does_not_exceed_evaluated_blocks():
    df = _synthetic_rows(n=2400, seed=7, signal_strength=3.0)
    rep = evaluate_feature_importance(df, n_blocks=8, holdout_years=1, n_repeats=8)
    for f in rep.features:
        assert f.n_blocks <= rep.n_blocks_evaluated
