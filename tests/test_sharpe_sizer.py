"""
Tests for analyzers/sharpe_sizer.py

SharpeSizer._ensure_loaded() calls _compute_all_stats() which imports
TradeJournal.  We inject pre-computed stats directly so no DB / network
access is required.
"""
import json
import os
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

import analyzers.sharpe_sizer as ss_mod
from analyzers.sharpe_sizer import SharpeSizer, _SHARPE_DEFAULT, _SHARPE_NEG_MULT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_sizer_with_stats(stats: dict) -> SharpeSizer:
    """
    Build a SharpeSizer whose internal _stats are pre-populated so that
    _ensure_loaded() won't try to hit the TradeJournal or file system.
    """
    sizer = SharpeSizer.__new__(SharpeSizer)
    sizer._stats = stats
    sizer._loaded_at = datetime.utcnow()  # mark as freshly loaded
    return sizer


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_default_modifier_no_trades():
    """
    A ticker with fewer than 5 trades (or unknown) returns the default
    modifier of 1.0.
    """
    sizer = make_sizer_with_stats({})
    modifier = sizer.get_size_modifier("TSLA")
    assert modifier == _SHARPE_DEFAULT


def test_modifier_for_high_sharpe():
    """Sharpe >= 1.5 must return modifier 1.2."""
    stats = {
        "AAPL": {
            "n_trades": 10,
            "sharpe_ratio": 2.0,
            "win_rate": 0.7,
            "avg_return": 5.0,
            "std_return": 2.5,
        }
    }
    sizer = make_sizer_with_stats(stats)
    modifier = sizer.get_size_modifier("AAPL")
    assert modifier == pytest.approx(1.2)


def test_modifier_for_negative_sharpe():
    """Negative Sharpe ratio must return the minimum modifier of 0.5."""
    stats = {
        "NVDA": {
            "n_trades": 8,
            "sharpe_ratio": -0.8,
            "win_rate": 0.3,
            "avg_return": -2.0,
            "std_return": 2.5,
        }
    }
    sizer = make_sizer_with_stats(stats)
    modifier = sizer.get_size_modifier("NVDA")
    assert modifier == pytest.approx(_SHARPE_NEG_MULT)


def test_modifier_medium_sharpe():
    """Sharpe in [0.5, 1.5) must return modifier 1.0."""
    stats = {
        "MSFT": {
            "n_trades": 6,
            "sharpe_ratio": 0.9,
            "win_rate": 0.6,
            "avg_return": 3.0,
            "std_return": 3.3,
        }
    }
    sizer = make_sizer_with_stats(stats)
    modifier = sizer.get_size_modifier("MSFT")
    assert modifier == pytest.approx(1.0)


def test_modifier_low_positive_sharpe():
    """Sharpe in [0.0, 0.5) must return modifier 0.8."""
    stats = {
        "AMD": {
            "n_trades": 7,
            "sharpe_ratio": 0.3,
            "win_rate": 0.5,
            "avg_return": 1.0,
            "std_return": 3.3,
        }
    }
    sizer = make_sizer_with_stats(stats)
    modifier = sizer.get_size_modifier("AMD")
    assert modifier == pytest.approx(0.8)


def test_ticker_lookup_is_case_insensitive():
    """get_size_modifier normalises the ticker to uppercase."""
    stats = {
        "GOOG": {
            "n_trades": 5,
            "sharpe_ratio": 1.8,
            "win_rate": 0.8,
            "avg_return": 4.0,
            "std_return": 2.2,
        }
    }
    sizer = make_sizer_with_stats(stats)
    # Lower-case input
    assert sizer.get_size_modifier("goog") == pytest.approx(1.2)
