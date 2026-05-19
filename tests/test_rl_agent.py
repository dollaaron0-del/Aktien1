"""
Tests for analyzers/rl_agent.py
"""
import json
import os
import pytest

import analyzers.rl_agent as rl_mod
from analyzers.rl_agent import RLAgent, RLState, _N_FEATURES, _MODIFIERS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def neutral_state():
    return RLState(
        sentiment_score=0.5,
        vix_level=0.3,
        momentum_5d=0.0,
        news_velocity=0.5,
        confidence_encoded=0.5,
        regime_encoded=0.5,
    )


def make_agent(tmp_path, monkeypatch):
    """Return a fresh RLAgent backed by a temp weights file."""
    weights_file = str(tmp_path / "data" / "rl_weights.json")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(rl_mod, "_WEIGHTS_FILE", weights_file)
    return RLAgent()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_initial_weights_length(tmp_path, monkeypatch):
    """The weights list always has exactly 6 elements (one per feature)."""
    agent = make_agent(tmp_path, monkeypatch)
    assert len(agent._weights) == _N_FEATURES


def test_should_buy_returns_tuple(tmp_path, monkeypatch):
    """should_buy() returns a (bool, float) tuple."""
    agent = make_agent(tmp_path, monkeypatch)
    result = agent.should_buy(neutral_state())
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert isinstance(result[0], bool)
    assert isinstance(result[1], float)


def test_compute_reward_positive_return():
    """A 10% return yields a reward of approximately 1.0."""
    reward = RLAgent.compute_reward(
        return_pct=10.0,
        hold_days=14,
        planned_hold_days=14,
        stop_loss_triggered=False,
    )
    assert reward == pytest.approx(1.0)


def test_compute_reward_negative_return():
    """A -10% return yields a reward of approximately -1.0."""
    reward = RLAgent.compute_reward(
        return_pct=-10.0,
        hold_days=14,
        planned_hold_days=14,
        stop_loss_triggered=False,
    )
    assert reward == pytest.approx(-1.0)


def test_compute_reward_clipped():
    """A 100% return is clipped to the maximum reward of 1.0."""
    reward = RLAgent.compute_reward(
        return_pct=100.0,
        hold_days=14,
        planned_hold_days=14,
        stop_loss_triggered=False,
    )
    assert reward == pytest.approx(1.0)


def test_compute_reward_stop_loss_malus():
    """A stop-loss exit produces a lower (more negative) reward than without it."""
    reward_no_sl = RLAgent.compute_reward(
        return_pct=-5.0,
        hold_days=14,
        planned_hold_days=14,
        stop_loss_triggered=False,
    )
    reward_with_sl = RLAgent.compute_reward(
        return_pct=-5.0,
        hold_days=14,
        planned_hold_days=14,
        stop_loss_triggered=True,
    )
    assert reward_with_sl < reward_no_sl


def test_compute_reward_fast_win_bonus():
    """Profit achieved in less than half the planned hold time earns a bonus."""
    reward_normal = RLAgent.compute_reward(
        return_pct=5.0,
        hold_days=14,       # = planned_hold_days → no bonus
        planned_hold_days=14,
        stop_loss_triggered=False,
    )
    reward_fast = RLAgent.compute_reward(
        return_pct=5.0,
        hold_days=5,        # < 14/2 = 7 → bonus applies
        planned_hold_days=14,
        stop_loss_triggered=False,
    )
    assert reward_fast > reward_normal


def test_record_outcome_increments_trade_count(tmp_path, monkeypatch):
    """trade_count increases by 1 after each call to record_outcome."""
    agent = make_agent(tmp_path, monkeypatch)
    initial_count = agent._trade_count
    agent.record_outcome(neutral_state(), reward=0.5)
    assert agent._trade_count == initial_count + 1


def test_modifier_range(tmp_path, monkeypatch):
    """The modifier returned by should_buy is always one of the allowed steps."""
    agent = make_agent(tmp_path, monkeypatch)
    state = neutral_state()
    for _ in range(20):
        _, modifier = agent.should_buy(state)
        assert modifier in _MODIFIERS


def test_get_stats_structure(tmp_path, monkeypatch):
    """get_stats() returns a dict with the required keys."""
    agent = make_agent(tmp_path, monkeypatch)
    stats = agent.get_stats()
    for key in ("trades", "avg_reward", "epsilon", "weights"):
        assert key in stats, f"Missing key in get_stats(): {key}"
