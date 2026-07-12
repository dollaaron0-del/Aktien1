"""
Tests für die Erfolgs-/Abbruchkriterien je Strategie-These (Roadmap 6.10).

Kern-Zusagen: (1) register_thesis() ist idempotent — ein bestehender Eintrag
(egal welcher Status) bleibt unangetastet, außer force=True. (2) evaluate()
liefert PENDING solange weder Stichprobe noch Zeit-Budget erschöpft sind.
(3) PROVEN nur bei erreichter Stichprobe UND beiden CI-Untergrenzen > 0.
(4) ABANDONED bei erreichter Stichprobe ohne erfülltes Kriterium ODER bei
abgelaufenem Zeit-Budget vor erreichter Stichprobe. (5) "Nicht wiederbeleben":
ein einmal gefälltes Verdikt wird NIE neu berechnet, selbst wenn neue Daten
anders aussähen. (6) evaluate() ohne Registrierung wirft KeyError. Netzfrei
(_bootstrap_mean_ci arbeitet rein auf übergebenen Zahlen).
"""
from datetime import date, timedelta

import numpy as np
import pytest

from analyzers import thesis_verdict as tv


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry.json"))
    yield


def _good_returns(n, seed=0):
    """Klar positive, aber verrauschte Renditen — Bootstrap-CI-Untergrenze > 0."""
    rng = np.random.default_rng(seed)
    return list(rng.normal(2.0, 1.5, n))


def _noise_returns(n, seed=0):
    """Rauschen um 0 — Bootstrap-CI spannt die Null, Kriterium NICHT erfüllt."""
    rng = np.random.default_rng(seed)
    return list(rng.normal(0.0, 3.0, n))


def test_register_is_idempotent():
    t1 = tv.register_thesis("x", n_min=50, time_budget_months=12)
    t2 = tv.register_thesis("x", n_min=999, time_budget_months=999)   # andere Werte
    assert t2.n_min == 50 and t2.time_budget_months == 12             # unverändert


def test_register_force_overwrites():
    tv.register_thesis("x", n_min=50)
    t2 = tv.register_thesis("x", n_min=200, force=True)
    assert t2.n_min == 200


def test_evaluate_unregistered_raises():
    with pytest.raises(KeyError):
        tv.evaluate("nope", [1.0, 2.0])


def test_evaluate_pending_when_below_both_thresholds():
    tv.register_thesis("x", n_min=150, time_budget_months=24,
                       started_at=date.today().isoformat())
    v = tv.evaluate("x", _good_returns(10))
    assert v.status == tv.PENDING
    assert v.n_trades == 10


def test_evaluate_proven_when_sample_reached_and_edge_holds():
    tv.register_thesis("x", n_min=50, time_budget_months=24,
                       started_at=date.today().isoformat())
    returns = _good_returns(60, seed=1)
    excess = _good_returns(60, seed=2)     # ebenfalls klar positiv -> schlägt B&H
    v = tv.evaluate("x", returns, excess)
    assert v.status == tv.PROVEN
    assert v.edge_ci_lo is not None and v.edge_ci_lo > 0
    assert v.beats_bh_ci_lo is not None and v.beats_bh_ci_lo > 0


def test_evaluate_abandoned_when_sample_reached_but_no_edge():
    tv.register_thesis("x", n_min=50, time_budget_months=24,
                       started_at=date.today().isoformat())
    v = tv.evaluate("x", _noise_returns(60, seed=3), _noise_returns(60, seed=4))
    assert v.status == tv.ABANDONED
    assert "Kriterium NICHT erfüllt" in v.reason


def test_evaluate_abandoned_when_time_budget_expired_before_sample():
    old_start = (date.today() - timedelta(days=800)).isoformat()   # >24 Monate her
    tv.register_thesis("x", n_min=150, time_budget_months=24, started_at=old_start)
    v = tv.evaluate("x", _good_returns(10))          # weit unter n_min
    assert v.status == tv.ABANDONED
    assert "Zeit-Budget" in v.reason


def test_verdict_is_frozen_never_revived():
    old_start = (date.today() - timedelta(days=800)).isoformat()
    tv.register_thesis("x", n_min=150, time_budget_months=24, started_at=old_start)
    first = tv.evaluate("x", _good_returns(10))
    assert first.status == tv.ABANDONED

    # Jetzt mit massenhaft überzeugenden Daten erneut auswerten — darf NICHT
    # wiederbelebt werden, das eingefrorene Verdikt bleibt bestehen.
    second = tv.evaluate("x", _good_returns(500, seed=99), _good_returns(500, seed=100))
    assert second.status == tv.ABANDONED
    assert second.reason == first.reason


def test_is_abandoned_helper():
    tv.register_thesis("x", n_min=5, time_budget_months=24,
                       started_at=date.today().isoformat())
    assert tv.is_abandoned("x") is False
    tv.evaluate("x", _noise_returns(10, seed=5), _noise_returns(10, seed=6))
    assert tv.is_abandoned("x") is True


def test_evaluate_without_excess_returns_cannot_prove():
    # Ohne Excess-Renditen ist "schlägt B&H" nie belegbar -> nie PROVEN,
    # aber bei erreichter Stichprobe trotzdem ABANDONED (nicht ewig PENDING).
    tv.register_thesis("x", n_min=20, time_budget_months=24,
                       started_at=date.today().isoformat())
    v = tv.evaluate("x", _good_returns(30), excess_returns=None)
    assert v.status == tv.ABANDONED
    assert v.beats_bh_ci_lo is None


def test_registry_roundtrip_persists_across_loads():
    tv.register_thesis("x", n_min=77, description="Test-These")
    reloaded = tv.load_registry()
    assert "x" in reloaded
    assert reloaded["x"].n_min == 77
    assert reloaded["x"].description == "Test-These"


def test_default_constants_match_user_decision_12_7():
    # Dokumentiert die konkrete User-Entscheidung 12.7.2026 als Regression.
    assert tv.DEFAULT_N_MIN == 150
    assert tv.DEFAULT_TIME_BUDGET_MONTHS == 24
