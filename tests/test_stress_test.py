"""
Tests für Roadmap 2.3 Teil A (Stress-Test-Harness) —
strategy_lab/stress_test.py. Netzfrei: injizierter Loader liefert
synthetische, lange Kursreihen; keine echten Netzwerkaufrufe.
"""
import numpy as np
import pandas as pd
import pytest

import strategy_lab.portfolio_backtest as pb
import strategy_lab.stress_test as st
from backtesting.engine import Trade
from strategy_lab.portfolio_backtest import PortfolioBacktestResult, VolTargetConfig
from strategy_lab.strategies import Strategy


def _long_df(start="2000-01-03", n=6000, seed=0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0004, 0.012, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": np.full(n, 1e6),
    }, index=idx)


def _fake_strategy(name, trades_by_ticker):
    return Strategy(name=name, description="test double",
                     runner=lambda df, ticker, params: list(trades_by_ticker.get(ticker, [])))


# ── _crisis_loader ───────────────────────────────────────────────────────────

def test_crisis_loader_slices_to_window():
    df = _long_df()
    loader = st._crisis_loader(lambda t, y: df, "2007-10-01", "2009-06-01")
    sliced = loader("AAA", 20)
    assert sliced is not None
    assert sliced.index.min() >= pd.Timestamp("2007-10-01")
    assert sliced.index.max() <= pd.Timestamp("2009-06-01")


def test_crisis_loader_returns_none_when_ticker_not_yet_listed():
    # "IPO" 2015 -> keine Daten vor 2015, GFC-2008-Fenster liefert leer -> None
    df = _long_df(start="2015-01-02", n=2000)
    loader = st._crisis_loader(lambda t, y: df, "2007-10-01", "2009-06-01")
    assert loader("NEWCO", 20) is None


def test_crisis_loader_passes_through_none_from_base_loader():
    loader = st._crisis_loader(lambda t, y: None, "2007-10-01", "2009-06-01")
    assert loader("MISSING", 20) is None


# ── run_stress_test ──────────────────────────────────────────────────────────

def test_run_stress_test_runs_for_each_window(monkeypatch):
    df_a = _long_df(seed=1)
    loader = lambda ticker, years: df_a
    plan = [{"strategy": "fake", "params": {}, "weight": 1.0}]

    for window in st.CRISIS_WINDOWS:
        start, end = st.CRISIS_WINDOWS[window]
        window_df = df_a.loc[start:end]
        entry_date, exit_date = window_df.index[5], window_df.index[40]
        trades = {"AAA": [
            Trade(ticker="AAA", entry_date=entry_date, entry_price=100.0,
                  exit_date=exit_date, exit_price=90.0, return_pct=-0.10,
                  exit_reason="SL"),
        ]}
        monkeypatch.setattr(pb, "get", lambda name, trades=trades: _fake_strategy("fake", trades))
        res = st.run_stress_test(window, plan, ["AAA"], loader=loader,
                                 vol_target_cfg=VolTargetConfig(enabled=False))
        assert isinstance(res, PortfolioBacktestResult)


def test_run_stress_test_unknown_window_raises():
    with pytest.raises(KeyError):
        st.run_stress_test("NOT_A_WINDOW", [], ["AAA"], loader=lambda t, y: None)


def test_run_stress_test_empty_plan_is_flat():
    res = st.run_stress_test("COVID_2020", [], ["AAA"], loader=lambda t, y: None)
    assert res.n_trades_taken == 0
    assert res.final_equity == res.initial_capital
