"""
Tests for portfolio/circuit_breaker.py
"""
import json
import os
import pytest

import portfolio.circuit_breaker as cb_mod
from portfolio.circuit_breaker import CircuitBreaker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_cb(tmp_path, monkeypatch):
    """Return a CircuitBreaker backed by a temp cache file."""
    cache_file = str(tmp_path / "data" / "circuit_breaker.json")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(cb_mod, "_CACHE_FILE", cache_file)
    return CircuitBreaker()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_block_initially(tmp_path, monkeypatch):
    """A fresh CircuitBreaker allows buys when no loss has been recorded."""
    cb = make_cb(tmp_path, monkeypatch)
    allowed, reason = cb.check_buy_allowed(10_000.0)
    assert allowed is True
    assert reason == ""


def test_daily_loss_triggers(tmp_path, monkeypatch):
    """A daily loss of 6% (above the 5% limit) blocks new buys."""
    cb = make_cb(tmp_path, monkeypatch)
    open_value = 10_000.0
    cb.register_day_open(open_value)
    current_value = open_value * (1 - 0.06)  # -6%
    allowed, reason = cb.check_buy_allowed(current_value)
    assert allowed is False
    assert "CIRCUIT BREAKER" in reason


def test_daily_loss_below_limit(tmp_path, monkeypatch):
    """A daily loss of 4% (below the 5% limit) does NOT block buys."""
    cb = make_cb(tmp_path, monkeypatch)
    open_value = 10_000.0
    cb.register_day_open(open_value)
    current_value = open_value * (1 - 0.04)  # -4%
    allowed, reason = cb.check_buy_allowed(current_value)
    assert allowed is True
    assert reason == ""


def test_drawdown_triggers(tmp_path, monkeypatch):
    """
    A drawdown of 16% from the peak (above the 15% limit) blocks buys.

    We set today's open_value equal to current_value so the daily-loss check
    sees 0% loss, isolating the drawdown check.
    """
    cb = make_cb(tmp_path, monkeypatch)
    peak = 10_000.0
    cb._state["peak_value"] = peak

    current_value = peak * (1 - 0.16)  # 16% below peak, above 15% limit
    cb._state["day"] = "1970-01-01"
    cb.register_day_open(current_value)  # open = current → 0% daily loss

    allowed, reason = cb.check_buy_allowed(current_value)
    assert allowed is False
    assert "CIRCUIT BREAKER" in reason


def test_drawdown_below_limit(tmp_path, monkeypatch):
    """
    A drawdown of 14% from the peak (below the 15% limit) does NOT block buys.

    We set today's open_value equal to current_value so the daily-loss check
    sees 0% loss, isolating the drawdown check.
    """
    cb = make_cb(tmp_path, monkeypatch)
    peak = 10_000.0
    # Artificially inject a higher peak into state before registering today's open
    cb._state["peak_value"] = peak

    # Today's open = current_value → 0% daily loss, so only drawdown is relevant
    current_value = peak * (1 - 0.14)  # 14% below peak, below 15% limit
    cb._state["day"] = "1970-01-01"  # ensure register_day_open runs
    cb.register_day_open(current_value)

    allowed, reason = cb.check_buy_allowed(current_value)
    assert allowed is True, f"Expected allowed=True but got reason: {reason}"


def test_register_day_open_once_per_day(tmp_path, monkeypatch):
    """
    Only the first call to register_day_open per day sets open_value.
    A second call with a different value must not overwrite the first.
    """
    cb = make_cb(tmp_path, monkeypatch)
    first_open = 10_000.0
    second_open = 11_000.0

    cb.register_day_open(first_open)
    cb.register_day_open(second_open)  # same day – must be ignored

    # The stored open_value must still be the first one
    assert cb._state["open_value"] == first_open
