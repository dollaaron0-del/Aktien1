"""
Tests für strategy_lab.regime (Roadmap Phase 4-Rest – Regime-Tagging).

Netzfrei: synthetische Kursfenster mit gesteuertem Trend/Vola, plus
konstruierte WindowEvals für die Aufschlüsselung.
"""
import numpy as np
import pandas as pd

from strategy_lab.regime import (
    classify_window, regime_breakdown, robust_regimes,
)
from strategy_lab.walkforward import WindowEval


def _df(n=300, drift=0.0, vol=0.008, seed=0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": close}, index=idx)


def test_classify_bull_calm():
    # kräftige Drift (~+30%/Jahr trotz Vola-Drag), niedrige Vola → BULL_CALM
    dfs = {"A": _df(drift=0.0015, vol=0.006, seed=1),
           "B": _df(drift=0.0015, vol=0.006, seed=2)}
    assert classify_window(dfs) == "BULL_CALM"


def test_classify_bear_volatile():
    dfs = {"A": _df(drift=-0.0015, vol=0.03, seed=3)}
    label = classify_window(dfs)
    assert label == "BEAR_VOLATILE"


def test_classify_sideways():
    # Drift 0, langes Fenster + mehrere Ticker → realisierter Return nahe 0
    # (mittelt Seed-Glück weg) → weder BULL noch BEAR.
    dfs = {t: _df(n=750, drift=0.0, vol=0.007, seed=s)
           for t, s in zip("ABCD", range(10, 14))}
    assert classify_window(dfs).startswith("SIDE_")


def test_classify_unknown_on_thin_data():
    assert classify_window({}) == "UNKNOWN"
    assert classify_window({"A": _df(n=5)}) == "UNKNOWN"


def test_regime_breakdown_and_robust():
    def _w(regime, ret):
        return WindowEval("", "", "", "", {}, 1.0, ret, 0.0, 5, 0.5, regime=regime)
    windows = [
        _w("BULL_CALM", 0.3), _w("BULL_CALM", 0.1),     # 2× positiv
        _w("BEAR_VOLATILE", -0.2),                       # 1× negativ
        _w("SIDE_CALM", 0.05), _w("SIDE_CALM", -0.1),    # gemischt, Median < 0
    ]
    bd = regime_breakdown(windows)
    assert bd["BULL_CALM"]["n"] == 2
    assert bd["BULL_CALM"]["pct_positive"] == 1.0
    assert bd["BEAR_VOLATILE"]["median_test_return"] == -0.2

    rr = robust_regimes(bd, min_n=2)
    assert "BULL_CALM" in rr                  # 2 Fenster, Median > 0
    assert "BEAR_VOLATILE" not in rr          # nur 1 Fenster (< min_n)
    assert "SIDE_CALM" not in rr              # Median < 0


def test_breakdown_empty_regime_defaults_unknown():
    w = WindowEval("", "", "", "", {}, 1.0, 0.2, 0.0, 5, 0.5)  # regime=""
    bd = regime_breakdown([w])
    assert "UNKNOWN" in bd
