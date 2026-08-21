"""
Tests für strategy_lab.walkforward (Roadmap Phase 3).

Netzfrei: lange synthetische Historie wird per Loader injiziert. Eine winzige
Test-Strategie mit kleinem param_space hält den Grid-Search schnell.
"""
import numpy as np
import pandas as pd
import pytest

from strategy_lab.strategies import Strategy, register, REGISTRY
from strategy_lab.walkforward import (
    run_walk_forward, _param_combos, _aggregate_report, WindowEval, _bootstrap_ci,
)


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


def test_walk_forward_runs_with_enlarged_exit_param_space():
    """Roadmap 2.1: der vergrößerte, kategoriale param_space (sl_mode/
    time_stop_mode als Strings) darf _param_combos/_aggregate_report nicht
    zum Absturz bringen (Counter(tuple(sorted(...))) braucht hashbare Werte
    – Strings sind das, aber das war vor Exit-Lab ungetestet)."""
    loader = lambda t, y: _long_df(seed=hash(t) % 100)
    rep = run_walk_forward("baseline_swing", ["AAA", "BBB"],
                           total_years=12, train_years=4, test_years=2, step_years=2,
                           max_combos=12, loader=loader)
    assert rep.n_windows >= 1
    assert rep.verdict in ("ROBUST", "FRAGILE", "OVERFIT")
    assert rep.avg_test_max_drawdown <= 0
    assert rep.worst_test_max_drawdown <= rep.avg_test_max_drawdown
    for w in rep.windows:
        assert w.test_max_drawdown <= 0


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


# ── Bootstrap-Konfidenz (Roadmap 4.2) ────────────────────────────────────────

def _win(test_return, train_return=1.0, dd=-0.1):
    return WindowEval("", "", "", "", {"a": 1}, train_return=train_return,
                      test_return=test_return, test_sharpe=1.0, test_trades=5,
                      test_win_rate=0.6, test_max_drawdown=dd)


def test_bootstrap_ci_behaviour():
    lo, hi, p = _bootstrap_ci([2.0] * 40)          # eng positiv
    assert lo > 0 and p < 0.01
    lo, hi, p = _bootstrap_ci([-1.0, -2.0, -0.5, -1.5, -2.5, -1.0])
    assert hi < 0 and p > 0.95                       # klar negativ
    lo, hi, _ = _bootstrap_ci([1.0])                 # n=1 → degeneriert
    assert lo == hi == 1.0
    lo, hi, _ = _bootstrap_ci([])                    # leer
    assert (lo, hi) == (0.0, 0.0)


def test_verdict_demotes_positive_but_noisy_to_fragile():
    """Der Kern von 4.2: im Mittel positiv, hohe %pos und WF-Eff — aber ein
    Ausreißer macht die Kante insignifikant (CI-Untergrenze ≤ 0). Punkt-Verdikt
    hätte ROBUST gesagt; Konfidenz-Verdikt sagt korrekt FRAGILE."""
    windows = [_win(2.0), _win(2.0), _win(2.0), _win(-4.0)]
    rep = _aggregate_report("noisy", windows)
    assert rep.avg_test_return > 0 and rep.pct_positive_windows >= 0.6
    assert rep.wf_efficiency >= 0.5          # alte Bedingungen erfüllt …
    assert rep.test_return_ci_lo <= 0        # … aber CI berührt/unterschreitet 0
    assert rep.verdict == "FRAGILE"


def test_verdict_stays_robust_when_ci_clears_zero():
    """Eng gestreute, durchgängig positive OOS-Fenster: CI klar > 0 → ROBUST."""
    windows = [_win(0.8), _win(0.9), _win(0.7), _win(0.85)]
    rep = _aggregate_report("tight", windows)
    assert rep.test_return_ci_lo > 0
    assert rep.verdict == "ROBUST"


def test_ci_and_drawdown_fields_populated():
    windows = [_win(0.5, dd=-0.1), _win(0.3, dd=-0.2), _win(0.6, dd=-0.15)]
    rep = _aggregate_report("s", windows)
    assert rep.test_return_ci_lo <= rep.avg_test_return <= rep.test_return_ci_hi
    assert rep.max_drawdown_ci_lo <= rep.max_drawdown_ci_hi <= 0
    assert 0.0 <= rep.test_return_p_le0 <= 1.0
