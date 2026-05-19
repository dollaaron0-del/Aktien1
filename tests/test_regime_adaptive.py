"""
Tests for analyzers/regime_adaptive.py

All tests operate on the in-memory RegimeAdaptiveConfig.REGIMES dict and the
get_adaptive_params() helper.  No real HTTP / yfinance calls are made.
"""
import pytest
from unittest.mock import patch

from analyzers.regime_adaptive import (
    RegimeAdaptiveConfig,
    RegimeParams,
    get_adaptive_params,
    BULL,
    NEUTRAL,
    BEAR,
    CRISIS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config():
    return RegimeAdaptiveConfig()


# ---------------------------------------------------------------------------
# Tests – regime parameter correctness
# ---------------------------------------------------------------------------

def test_regime_params_bull(config):
    """BULL regime has a lower buy threshold and largest position size vs BEAR."""
    bull = config.get(BULL)
    bear = config.get(BEAR)
    assert isinstance(bull, RegimeParams)
    # Lower or equal buy-threshold adjustment than BEAR
    assert bull.buy_threshold_adj < bear.buy_threshold_adj
    # Larger position size than BEAR
    assert bull.position_size_mult > bear.position_size_mult


def test_regime_params_bear(config):
    """BEAR regime has a higher threshold and smaller position size than BULL."""
    bull = config.get(BULL)
    bear = config.get(BEAR)
    assert bear.buy_threshold_adj > bull.buy_threshold_adj
    assert bear.position_size_mult < bull.position_size_mult


def test_regime_params_crisis(config):
    """CRISIS regime has the smallest position size and highest threshold of all."""
    crisis = config.get(CRISIS)
    for regime in (BULL, NEUTRAL, BEAR):
        other = config.get(regime)
        assert crisis.position_size_mult <= other.position_size_mult
        assert crisis.buy_threshold_adj >= other.buy_threshold_adj


def test_get_adaptive_params_returns_params():
    """
    get_adaptive_params() returns a RegimeParams instance.
    We patch get_current_regime() so no network call is made.
    """
    with patch("analyzers.regime_adaptive.get_current_regime", return_value=NEUTRAL):
        params = get_adaptive_params()
    assert isinstance(params, RegimeParams)


def test_get_adaptive_params_uses_passed_regime():
    """When a regime string is passed directly, no network call is needed."""
    params = get_adaptive_params(regime=BULL)
    assert isinstance(params, RegimeParams)
    assert params.label == "Bullenmarkt"


def test_get_adaptive_params_fallback_to_neutral():
    """Unknown regime string falls back to NEUTRAL parameters."""
    config = RegimeAdaptiveConfig()
    params = config.get("UNKNOWN_REGIME")
    neutral = config.get(NEUTRAL)
    assert params.sl_pct == neutral.sl_pct
    assert params.position_size_mult == neutral.position_size_mult
