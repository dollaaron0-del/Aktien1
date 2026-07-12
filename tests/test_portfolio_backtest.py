"""
Tests für Roadmap 2.2 (Portfolio-Level-Backtest) — strategy_lab/portfolio_backtest.py.

Cash-Constraint, Max-Positionen, Themen-Kappung (Korrelations-Proxy),
Vol-Targeting, Equity-Kurve/MaxDD/Sharpe, weight_plan-vs-equal_weight-Plumbing.

Netzfrei: Trades werden über eine Fake-Strategy hand-konstruiert (nicht über
die echte EMA/RSI-Signal-Logik), damit Tests exakte Zahlen prüfen können.
"""
import math

import numpy as np
import pandas as pd
import pytest

import strategy_lab.portfolio_backtest as pb
from backtesting.engine import Trade
from strategy_lab.portfolio_backtest import (
    PortfolioTrade, VolTargetConfig, SkipReasons,
    _vol_multiplier, _theme_cap_violated, _build_events, _simulate_events,
    equal_weight_plan, run_portfolio_backtest,
)
from strategy_lab.strategies import Strategy


def _ramp_df(n=60, daily=0.001, base=100.0, start="2022-01-03", high_low_pct=0.001):
    price = base * (1 + daily) ** np.arange(n)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({
        "Open": price, "High": price * (1 + high_low_pct), "Low": price * (1 - high_low_pct),
        "Close": price, "Volume": np.full(n, 1e6),
    }, index=idx)


def _trade(ticker, entry_date, exit_date, return_pct, entry_price=100.0):
    return Trade(ticker=ticker, entry_date=pd.Timestamp(entry_date), entry_price=entry_price,
                exit_date=pd.Timestamp(exit_date), exit_price=entry_price * (1 + return_pct),
                return_pct=return_pct, exit_reason="time_stop")


def _fake_strategy(name, trades_by_ticker):
    return Strategy(name=name, description="test double",
                     runner=lambda df, ticker, params: list(trades_by_ticker.get(ticker, [])))


class _StubRelations:
    def __init__(self, themes):
        self._themes = themes

    def get_themes(self, ticker):
        return self._themes.get(ticker, [])


# ── _vol_multiplier ──────────────────────────────────────────────────────────

def test_vol_multiplier_disabled_is_one():
    df = _ramp_df()
    cfg = VolTargetConfig(enabled=False)
    assert _vol_multiplier(df, 30, cfg) == 1.0


def test_vol_multiplier_low_vol_scales_up_high_vol_scales_down():
    low_vol_df = _ramp_df(daily=0.0005, high_low_pct=0.0005)
    high_vol_df = _ramp_df(daily=0.0005, high_low_pct=0.05)
    cfg = VolTargetConfig()
    m_low = _vol_multiplier(low_vol_df, 30, cfg)
    m_high = _vol_multiplier(high_vol_df, 30, cfg)
    assert m_low == pytest.approx(cfg.size_cap)
    assert m_high == pytest.approx(cfg.size_floor)


def test_vol_multiplier_fails_open_on_bad_data():
    df = _ramp_df(n=5)  # zu wenig Bars für ATR(14) -> NaN
    cfg = VolTargetConfig()
    assert _vol_multiplier(df, 2, cfg) == 1.0


# ── _theme_cap_violated ──────────────────────────────────────────────────────

def test_theme_cap_blocks_when_over_cap():
    relations = _StubRelations({"NVDA": ["AI_CHIPS"], "AMD": ["AI_CHIPS"], "KO": []})
    assert _theme_cap_violated({"NVDA"}, "AMD", relations, cap=1) is True
    assert _theme_cap_violated({"NVDA"}, "AMD", relations, cap=2) is False


def test_theme_cap_never_blocks_ticker_without_theme():
    relations = _StubRelations({"NVDA": ["AI_CHIPS"], "KO": []})
    assert _theme_cap_violated({"NVDA"}, "KO", relations, cap=0) is False


# ── _build_events ────────────────────────────────────────────────────────────

def test_build_events_same_day_close_before_open():
    t_a = PortfolioTrade(_trade("AAA", "2022-02-01", "2022-03-01", 0.10), "s", 1.0, "AAA", _ramp_df())
    t_b = PortfolioTrade(_trade("BBB", "2022-03-01", "2022-04-01", 0.05), "s", 1.0, "BBB", _ramp_df())
    events = _build_events([t_a, t_b])
    same_day = [e for e in events if e.date == pd.Timestamp("2022-03-01")]
    assert [e.kind for e in same_day] == ["CLOSE", "OPEN"]


# ── _simulate_events / run_portfolio_backtest: Cash-Constraint ─────────────

def test_cash_constraint_skips_trade_when_insufficient_capital():
    df = _ramp_df()
    trades = [
        PortfolioTrade(_trade("A", "2022-01-05", "2022-02-01", 0.0), "s", 0.5, "A", df),
        PortfolioTrade(_trade("B", "2022-01-05", "2022-02-01", 0.0), "s", 0.5, "B", df),
        PortfolioTrade(_trade("C", "2022-01-05", "2022-02-01", 0.0), "s", 0.5, "C", df),
    ]
    events = _build_events(trades)
    res = _simulate_events(events, trades, initial_capital=100.0, max_positions=10,
                           max_positions_per_theme=10, vol_target_cfg=VolTargetConfig(enabled=False),
                           relations=_StubRelations({}))
    assert res.n_trades_taken == 2
    assert res.skip_reasons.cash == 1
    assert res.n_trades_skipped == 1


def test_max_positions_cap_skips_excess_trade():
    df = _ramp_df()
    trades = [
        PortfolioTrade(_trade(t, "2022-01-05", "2022-02-01", 0.0), "s", 0.1, t, df)
        for t in ("A", "B", "C")
    ]
    events = _build_events(trades)
    res = _simulate_events(events, trades, initial_capital=100_000.0, max_positions=2,
                           max_positions_per_theme=10, vol_target_cfg=VolTargetConfig(enabled=False),
                           relations=_StubRelations({}))
    assert res.n_trades_taken == 2
    assert res.skip_reasons.max_positions == 1


def test_theme_cap_skips_correlated_same_day_trade():
    df = _ramp_df()
    trades = [
        PortfolioTrade(_trade("NVDA", "2022-01-05", "2022-02-01", 0.0), "s", 0.1, "NVDA", df),
        PortfolioTrade(_trade("AMD", "2022-01-05", "2022-02-01", 0.0), "s", 0.1, "AMD", df),
    ]
    events = _build_events(trades)
    relations = _StubRelations({"NVDA": ["AI_CHIPS"], "AMD": ["AI_CHIPS"]})
    res = _simulate_events(events, trades, initial_capital=100_000.0, max_positions=10,
                           max_positions_per_theme=1, vol_target_cfg=VolTargetConfig(enabled=False),
                           relations=relations)
    assert res.n_trades_taken == 1
    assert res.skip_reasons.theme_cap == 1


# ── Equity-Kurve / MaxDD / Sharpe (golden) ──────────────────────────────────

def test_equity_curve_and_max_drawdown_golden():
    df = _ramp_df()
    trades = [
        PortfolioTrade(_trade("A", "2022-01-03", "2022-02-01", 0.20), "s", 1.0, "A", df),   # +20%
        PortfolioTrade(_trade("A", "2022-02-02", "2022-03-01", -0.30), "s", 1.0, "A", df),  # -30%
        PortfolioTrade(_trade("A", "2022-03-02", "2022-04-01", 0.10), "s", 1.0, "A", df),   # +10%
    ]
    events = _build_events(trades)
    res = _simulate_events(events, trades, initial_capital=100.0, max_positions=1,
                           max_positions_per_theme=10, vol_target_cfg=VolTargetConfig(enabled=False),
                           relations=_StubRelations({}))
    # Sequenziell, volles Kapital je Trade: 100 -> 120 -> 84 -> 92.4
    assert res.equity_curve[-1][1] == pytest.approx(92.4, abs=1e-6)
    assert res.final_equity == pytest.approx(92.4, abs=1e-6)
    assert res.total_return == pytest.approx(-0.076, abs=1e-6)
    # Peak 120, Trough 84 -> DD = (84-120)/120 = -0.30
    assert res.max_drawdown == pytest.approx(-0.30, abs=1e-6)


def test_empty_plan_yields_flat_result():
    res = run_portfolio_backtest([], ["AAPL"], loader=lambda t, y: None)
    assert res.final_equity == res.initial_capital == 100_000.0
    assert res.total_return == 0.0
    assert res.n_trades_taken == 0


# ── weight_plan-vs-equal_weight Plumbing ────────────────────────────────────

def test_skewed_vs_equal_weight_plans_produce_different_results(monkeypatch):
    df_a = _ramp_df(base=100.0)
    df_b = _ramp_df(base=100.0)
    trades_a = {"AAA": [_trade("AAA", "2022-01-05", "2022-02-05", 0.50)]}
    trades_b = {"BBB": [_trade("BBB", "2022-01-05", "2022-02-05", -0.10)]}

    registry = {
        "fake_a": _fake_strategy("fake_a", trades_a),
        "fake_b": _fake_strategy("fake_b", trades_b),
    }
    monkeypatch.setattr(pb, "get", lambda name: registry[name])
    loader = lambda ticker, years: {"AAA": df_a, "BBB": df_b}.get(ticker)

    skewed_plan = [
        {"strategy": "fake_a", "params": {}, "weight": 0.9},
        {"strategy": "fake_b", "params": {}, "weight": 0.1},
    ]
    equal_plan = equal_weight_plan(["fake_a", "fake_b"])
    assert equal_plan == [
        {"strategy": "fake_a", "params": {}, "weight": 0.5, "risk_fraction": 0.5, "regime_fit": None},
        {"strategy": "fake_b", "params": {}, "weight": 0.5, "risk_fraction": 0.5, "regime_fit": None},
    ]

    common = dict(universe=["AAA", "BBB"], loader=loader, initial_capital=100_000.0,
                  vol_target_cfg=VolTargetConfig(enabled=False), stock_relations=_StubRelations({}))
    res_skewed = run_portfolio_backtest(skewed_plan, **common)
    res_equal = run_portfolio_backtest(equal_plan, **common)

    assert res_skewed.n_trades_taken == 2
    assert res_equal.n_trades_taken == 2
    assert res_skewed.final_equity != res_equal.final_equity
    # Skewed (90% auf den Gewinner-Trade) muss besser abschneiden als 50/50.
    assert res_skewed.final_equity > res_equal.final_equity


def test_equal_weight_plan_empty_input():
    assert equal_weight_plan([]) == []
