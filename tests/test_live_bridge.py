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
    # Paper-Forward-Bilanz + Registry-Regime entkoppeln: Tests lesen keine echten Dateien.
    monkeypatch.setattr(live_bridge, "_paper_forward_edge", lambda: {})
    monkeypatch.setattr(live_bridge, "_registry_regime_lookup", lambda: {})
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
    # Ohne Evidenz: ehrlich kennzeichnen, NIE Robustheit behaupten.
    assert "robuste" not in s.lower()
    assert "Kaufempfehlung" in s and "Paper-Forward" in s


def test_brief_for_evidence_no_edge():
    # Paper-Forward zeigt KEINE Kante → Brief warnt ausdrücklich.
    ev = {"verdict": "none", "n_closed": 12, "avg_return": -0.021, "win_rate": 0.39}
    m = {"AAPL": {"conviction": 1.0, "strategies": ["baseline_swing"],
                  "regime": None, "evidence": ev}}
    s = live_bridge.brief_for("AAPL", m)
    assert "KEINE nachgewiesene Kante" in s
    assert "NICHT als Bestätigung" in s
    assert "39%" in s


def test_brief_for_evidence_thin():
    ev = {"verdict": "thin", "n_closed": 2, "avg_return": 0.05, "win_rate": 0.5}
    m = {"AAPL": {"conviction": 1.0, "strategies": ["s"], "regime": None, "evidence": ev}}
    s = live_bridge.brief_for("AAPL", m)
    assert "zu dünn" in s


def test_brief_for_evidence_positive():
    ev = {"verdict": "positive", "n_closed": 10, "avg_return": 0.03, "win_rate": 0.6}
    m = {"AAPL": {"conviction": 1.0, "strategies": ["s"], "regime": None, "evidence": ev}}
    s = live_bridge.brief_for("AAPL", m)
    assert "leicht positiv" in s and "kein Beweis" in s


def test_aggregate_edge_weights_by_closed():
    pf = {"a": {"n_closed": 8, "avg_return": -0.05, "win_rate": 0.4},
          "b": {"n_closed": 2, "avg_return": 0.10, "win_rate": 0.5}}
    ev = live_bridge._aggregate_edge(["a", "b"], pf)
    assert ev["n_closed"] == 10
    # gewichtet: (8*-0.05 + 2*0.10)/10 = -0.02 < 0 und n>=5 → keine Kante
    assert ev["verdict"] == "none"


def test_aggregate_edge_thin_when_few():
    pf = {"a": {"n_closed": 3, "avg_return": 0.5, "win_rate": 1.0}}
    assert live_bridge._aggregate_edge(["a"], pf)["verdict"] == "thin"


def test_aggregate_edge_empty():
    assert live_bridge._aggregate_edge(["x"], {})["verdict"] == "thin"


# ── Regime-Stance (#2: warnt aktiv im ungünstigen Regime) ─────────────────────
def test_regime_stance_adverse_when_oos_negative():
    lookup = {"s": {"breakdown": {"BULL_CALM": {"median_test_return": -0.08}},
                    "robust": set()}}
    rs = live_bridge._regime_stance(["s"], "BULL_CALM", lookup)
    assert rs["stance"] == "adverse"


def test_regime_stance_favorable_when_robust():
    lookup = {"s": {"breakdown": {"BEAR_VOLATILE": {"median_test_return": 0.12}},
                    "robust": {"BEAR_VOLATILE"}}}
    rs = live_bridge._regime_stance(["s"], "BEAR_VOLATILE", lookup)
    assert rs["stance"] == "favorable"


def test_regime_stance_untested_when_regime_absent():
    lookup = {"s": {"breakdown": {"BULL_CALM": {"median_test_return": 0.1}}, "robust": set()}}
    assert live_bridge._regime_stance(["s"], "SIDE_CALM", lookup)["stance"] == "untested"


def test_regime_stance_conservative_adverse_wins():
    lookup = {"good": {"breakdown": {"R": {"median_test_return": 0.1}}, "robust": {"R"}},
              "bad": {"breakdown": {"R": {"median_test_return": -0.05}}, "robust": set()}}
    assert live_bridge._regime_stance(["good", "bad"], "R", lookup)["stance"] == "adverse"


def test_brief_for_warns_in_adverse_regime():
    m = {"AAPL": {"conviction": 1.0, "strategies": ["baseline_swing"], "regime": "BULL_CALM",
                  "evidence": {"verdict": "none", "n_closed": 10, "avg_return": -0.02,
                               "win_rate": 0.39},
                  "regime_stance": {"stance": "adverse", "regime": "BULL_CALM",
                                    "median": -0.08}}}
    s = live_bridge.brief_for("AAPL", m)
    assert "ACHTUNG" in s and "Buy&Hold" in s and "BULL_CALM" in s
    assert "KEINE nachgewiesene Kante" in s   # beide Signale stehen nebeneinander


def test_brief_for_supportive_in_favorable_regime():
    m = {"AAPL": {"conviction": 1.0, "strategies": ["s"], "regime": "BEAR_VOLATILE",
                  "evidence": {"verdict": "positive", "n_closed": 8, "avg_return": 0.03,
                               "win_rate": 0.6},
                  "regime_stance": {"stance": "favorable", "regime": "BEAR_VOLATILE",
                                    "median": 0.12}}}
    s = live_bridge.brief_for("AAPL", m)
    assert "getragen" in s and "ACHTUNG" not in s


def test_brief_for_zero_conviction():
    m = {"AAPL": {"conviction": 0.0, "strategies": [], "regime": None}}
    assert live_bridge.brief_for("AAPL", m) == ""


def test_brief_for_unknown_ticker():
    m = {"AAPL": {"conviction": 1.0, "strategies": ["s"], "regime": None}}
    assert live_bridge.brief_for("MSFT", m) == ""
