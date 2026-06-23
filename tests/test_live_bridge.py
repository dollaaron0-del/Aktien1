"""
Tests für strategy_lab.live_bridge (Roadmap-Punkt d – Live-Naht, flaggengeschützt).

Kernzusicherung: ohne Flag passiert NICHTS (Bot unverändert); mit Flag liefert die
Bridge eine additive Konviktion; jeder Fehler ist fail-safe ({} / "").
"""
import pytest

from strategy_lab import allocator, live_bridge


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("STRATEGY_LAB_LIVE", raising=False)
    monkeypatch.delenv("STRATEGY_LAB_REGIME", raising=False)
    live_bridge.reset_cache()
    yield
    live_bridge.reset_cache()


def _enable(monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_LIVE", "true")


def test_disabled_by_default(monkeypatch):
    assert live_bridge.is_enabled() is False
    assert live_bridge.conviction_map(["AAPL"], loader=lambda t, y: None) == {}


def test_enable_flag(monkeypatch):
    _enable(monkeypatch)
    assert live_bridge.is_enabled() is True


def test_conviction_map_empty_universe(monkeypatch):
    _enable(monkeypatch)
    assert live_bridge.conviction_map([], loader=lambda t, y: None) == {}


def _patch_allocator(monkeypatch, plan, fired, conv, regime="BULL_CALM"):
    monkeypatch.setattr(allocator, "current_regime", lambda *a, **k: regime)
    monkeypatch.setattr(allocator, "weight_plan", lambda *a, **k: plan)
    monkeypatch.setattr(allocator, "current_signals", lambda *a, **k: fired)
    monkeypatch.setattr(allocator, "combine_signals", lambda *a, **k: conv)


def test_conviction_map_builds_when_enabled(monkeypatch):
    _enable(monkeypatch)
    _patch_allocator(
        monkeypatch,
        plan=[{"strategy": "baseline_swing", "weight": 1.0, "params": {}}],
        fired={"baseline_swing": ["AAPL"]},
        conv={"AAPL": 1.0},
    )
    m = live_bridge.conviction_map(["AAPL", "MSFT"], loader=lambda t, y: object())
    assert m["AAPL"]["conviction"] == 1.0
    assert m["AAPL"]["strategies"] == ["baseline_swing"]
    assert m["AAPL"]["regime"] == "BULL_CALM"
    assert "MSFT" not in m  # feuert heute nicht


def test_conviction_map_regime_off(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("STRATEGY_LAB_REGIME", "off")
    captured = {}
    def _wp(*a, **k):
        captured["regime"] = k.get("regime")
        return [{"strategy": "s", "weight": 1.0, "params": {}}]
    monkeypatch.setattr(allocator, "weight_plan", _wp)
    monkeypatch.setattr(allocator, "current_signals", lambda *a, **k: {"s": ["AAPL"]})
    monkeypatch.setattr(allocator, "combine_signals", lambda *a, **k: {"AAPL": 1.0})
    live_bridge.conviction_map(["AAPL"], loader=lambda t, y: object())
    assert captured["regime"] is None  # 'off' → kein Regime-Filter


def test_conviction_map_failsafe(monkeypatch):
    _enable(monkeypatch)
    def _boom(*a, **k):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(allocator, "weight_plan", _boom)
    monkeypatch.setattr(allocator, "current_regime", lambda *a, **k: "X")
    assert live_bridge.conviction_map(["AAPL"], loader=lambda t, y: object()) == {}


def test_conviction_map_caches(monkeypatch):
    _enable(monkeypatch)
    calls = {"n": 0}
    def _wp(*a, **k):
        calls["n"] += 1
        return [{"strategy": "s", "weight": 1.0, "params": {}}]
    monkeypatch.setattr(allocator, "current_regime", lambda *a, **k: "R")
    monkeypatch.setattr(allocator, "weight_plan", _wp)
    monkeypatch.setattr(allocator, "current_signals", lambda *a, **k: {"s": ["AAPL"]})
    monkeypatch.setattr(allocator, "combine_signals", lambda *a, **k: {"AAPL": 1.0})
    ld = lambda t, y: object()
    live_bridge.conviction_map(["AAPL"], loader=ld)
    live_bridge.conviction_map(["AAPL"], loader=ld)
    assert calls["n"] == 1  # zweiter Aufruf aus Cache


# ── brief_for ────────────────────────────────────────────────────────────────
def test_brief_for_empty():
    assert live_bridge.brief_for("AAPL", {}) == ""
    assert live_bridge.brief_for("AAPL", None) == ""


def test_brief_for_formats():
    m = {"AAPL": {"conviction": 1.0, "strategies": ["baseline_swing"], "regime": "BULL_CALM"}}
    s = live_bridge.brief_for("AAPL", m)
    assert "MECHANIK" in s and "baseline_swing" in s and "100%" in s and "BULL_CALM" in s
    assert "KEIN Auto-Trade" in s


def test_brief_for_zero_conviction():
    m = {"AAPL": {"conviction": 0.0, "strategies": [], "regime": None}}
    assert live_bridge.brief_for("AAPL", m) == ""


def test_brief_for_unknown_ticker():
    m = {"AAPL": {"conviction": 1.0, "strategies": ["s"], "regime": None}}
    assert live_bridge.brief_for("MSFT", m) == ""
