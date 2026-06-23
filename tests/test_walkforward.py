"""
Tests für strategy_lab.walkforward (Roadmap Phase 3).

Netzfrei: lange synthetische Historie wird per Loader injiziert. Eine winzige
Test-Strategie mit kleinem param_space hält den Grid-Search schnell.
"""
import numpy as np
import pandas as pd
import pytest

from strategy_lab.strategies import Strategy, register, REGISTRY
from strategy_lab.walkforward import run_walk_forward, _param_combos, _aggregate_report, WindowEval


def _long_df(n=3000, trend=0.0005, seed=0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.012, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range("2005-01-01", periods=n, freq="B")
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    openp = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def test_param_combos_caps():
    space = {"a": [1, 2, 3], "b": [10, 20, 30, 40]}  # 12 Kombis
    assert len(_param_combos(space, max_combos=100)) == 12
    assert len(_param_combos(space, max_combos=5)) == 5
    assert _param_combos({}, 10) == [{}]


def test_walk_forward_runs_and_aggregates():
    # Donchian auf langer Synthetik: Walk-Forward liefert mehrere Fenster + Verdikt.
    loader = lambda t, y: _long_df(seed=hash(t) % 100)
    rep = run_walk_forward("donchian_breakout", ["AAA", "BBB"],
                           total_years=12, train_years=4, test_years=2, step_years=2,
                           max_combos=8, loader=loader)
    assert rep.n_windows >= 1
    assert rep.verdict in ("ROBUST", "FRAGILE", "OVERFIT")
    # OOS-Felder gesetzt
    assert -1.0 <= rep.pct_positive_windows <= 1.0


def test_no_data_yields_overfit():
    rep = run_walk_forward("donchian_breakout", ["X"], loader=lambda t, y: None)
    assert rep.n_windows == 0
    assert rep.verdict == "OVERFIT"


def test_verdict_logic():
    # Konstruierte Fenster: Train top, Test negativ -> OVERFIT
    w = [WindowEval("", "", "", "", {"a": 1}, train_return=2.0, test_return=-0.5,
                    test_sharpe=0, test_trades=5, test_win_rate=0.4) for _ in range(4)]
    assert _aggregate_report("x", w).verdict == "OVERFIT"
    # Train gut, Test gut & stabil -> ROBUST
    w2 = [WindowEval("", "", "", "", {"a": 1}, train_return=1.0, test_return=0.8,
                     test_sharpe=1, test_trades=5, test_win_rate=0.6) for _ in range(4)]
    rep2 = _aggregate_report("x", w2)
    assert rep2.verdict == "ROBUST"
    assert rep2.param_stability == 1.0  # immer derselbe Parametersatz
