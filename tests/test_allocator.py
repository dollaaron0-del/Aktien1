"""
Tests für strategy_lab.allocator (Roadmap Phase 5 – Meta-Allokator).

Netzfrei: Registry wird über promotion.active_strategies gemockt, Marktdaten
über einen injizierten Loader. Kein Live-Eingriff.
"""
import numpy as np
import pandas as pd
import pytest

from strategy_lab import allocator, promotion


def _patch_active(monkeypatch, entries):
    monkeypatch.setattr(promotion, "active_strategies", lambda: entries)


def test_weight_plan_empty_registry(monkeypatch):
    _patch_active(monkeypatch, [])
    assert allocator.weight_plan() == []


def test_weight_plan_caps_and_renormalizes(monkeypatch):
    _patch_active(monkeypatch, [
        {"strategy": "a", "weight": 0.8, "params": {"x": 1}},
        {"strategy": "b", "weight": 0.15, "params": {}},
        {"strategy": "c", "weight": 0.05, "params": {}},
    ])
    plan = allocator.weight_plan(max_weight=0.5)
    w = {e["strategy"]: e["weight"] for e in plan}
    assert w["a"] <= 0.5 + 1e-9               # gekappt
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-3)  # renormiert
    assert [e["strategy"] for e in plan][0] == "a"          # höchstes zuerst
    assert plan[0]["params"] == {"x": 1}                    # Parameter durchgereicht


def test_weight_plan_risk_fraction(monkeypatch):
    _patch_active(monkeypatch, [
        {"strategy": "a", "weight": 0.5, "params": {}},
        {"strategy": "b", "weight": 0.5, "params": {}},
    ])
    plan = allocator.weight_plan(max_weight=1.0, risk_budget=0.4)
    for e in plan:
        assert e["risk_fraction"] == pytest.approx(e["weight"] * 0.4, abs=1e-4)


def test_weight_plan_equal_weight_when_registry_has_none(monkeypatch):
    _patch_active(monkeypatch, [
        {"strategy": "a", "weight": 0.0, "params": {}},
        {"strategy": "b", "weight": 0.0, "params": {}},
    ])
    w = {e["strategy"]: e["weight"] for e in allocator.weight_plan()}
    assert w["a"] == pytest.approx(0.5, abs=1e-3)
    assert w["b"] == pytest.approx(0.5, abs=1e-3)


def test_combine_signals_weighted_overlap(monkeypatch):
    plan = [
        {"strategy": "a", "weight": 0.6, "params": {}},
        {"strategy": "b", "weight": 0.4, "params": {}},
    ]
    fired = {"a": ["AAPL", "MSFT"], "b": ["AAPL"]}
    conv = allocator.combine_signals(fired, plan=plan)
    assert conv["AAPL"] == pytest.approx(1.0, abs=1e-3)   # beide feuern
    assert conv["MSFT"] == pytest.approx(0.6, abs=1e-3)   # nur a
    assert list(conv) == ["AAPL", "MSFT"]                 # sortiert nach Konviktion


def test_combine_signals_empty():
    assert allocator.combine_signals({}, plan=[]) == {}


# ── current_signals + fires_today gegen echte Familien (synthetische Daten) ─────
def _uptrend_df(n=400, slope=0.001, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(slope, 0.005, n))
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    high = close * 1.01
    low = close * 0.99
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": np.full(n, 2_000_000.0)}, index=idx)


def test_fires_today_returns_bool_for_family():
    df = _uptrend_df()
    res = allocator.fires_today("ts_momentum", df, {"lookback": 126, "trend_ma": 200})
    assert isinstance(res, bool)


def test_fires_today_none_for_unsupported():
    df = _uptrend_df()
    assert allocator.fires_today("baseline_swing", df) is None


def test_current_signals_with_injected_loader(monkeypatch):
    plan = [{"strategy": "ts_momentum", "weight": 1.0,
             "params": {"lookback": 126, "trend_ma": 200}}]
    loader = lambda t, y: _uptrend_df(seed=hash(t) % 50)
    fired = allocator.current_signals(["AAA", "BBB"], loader, plan=plan)
    assert "ts_momentum" in fired
    assert isinstance(fired["ts_momentum"], list)


def test_current_signals_skips_unsupported(monkeypatch):
    plan = [{"strategy": "baseline_swing", "weight": 1.0, "params": {}}]
    loader = lambda t, y: _uptrend_df()
    fired = allocator.current_signals(["AAA"], loader, plan=plan)
    assert fired == {}  # baseline_swing hat keine Heute-Naht
