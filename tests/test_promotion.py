"""
Tests für strategy_lab.promotion (Roadmap Phase 4 – Survivorship-Registry).

Netzfrei: WalkForwardReports werden direkt konstruiert. Persistenz läuft über
eine ins tmp_path umgebogene Registry-Datei (kein Schreiben ins echte data/).
"""
import json

import pytest

from strategy_lab import promotion
from strategy_lab.walkforward import WalkForwardReport, WindowEval


def _report(name, verdict, median=0.5, pct_pos=0.7, wf_eff=0.8, params=None,
            n_windows=4):
    params = params or {"a": 1}
    windows = [
        WindowEval("", "", "", "", dict(params), train_return=1.0,
                   test_return=median, test_sharpe=1.0, test_trades=5,
                   test_win_rate=0.6)
        for _ in range(n_windows)
    ]
    return WalkForwardReport(
        strategy=name, n_windows=n_windows, median_test_return=median,
        worst_test_return=median - 0.2, pct_positive_windows=pct_pos,
        avg_train_return=1.0, avg_test_return=median, wf_efficiency=wf_eff,
        param_stability=1.0, verdict=verdict, windows=windows,
    )


def test_modal_params_picks_most_common():
    rep = _report("s", "ROBUST", params={"lookback": 20})
    # zwei Fenster mit abweichendem Parametersatz – der häufigste gewinnt.
    rep.windows[0] = WindowEval("", "", "", "", {"lookback": 55}, 1.0, 0.5, 1, 5, 0.6)
    assert promotion.modal_params(rep) == {"lookback": 20}


def test_modal_params_empty_report():
    rep = _report("s", "OVERFIT", n_windows=0)
    rep.windows = []
    assert promotion.modal_params(rep) == {}


def test_robustness_score_zero_unless_robust():
    assert promotion.robustness_score(_report("s", "FRAGILE")) == 0.0
    assert promotion.robustness_score(_report("s", "OVERFIT")) == 0.0
    assert promotion.robustness_score(_report("s", "ROBUST")) > 0.0


def test_build_registry_status_mapping_and_weights():
    reports = [
        _report("robust_hi", "ROBUST", median=1.0, pct_pos=0.8, wf_eff=1.0),
        _report("robust_lo", "ROBUST", median=0.2, pct_pos=0.6, wf_eff=0.6),
        _report("fragile", "FRAGILE"),
        _report("overfit", "OVERFIT"),
    ]
    reg = promotion.build_registry(reports)
    by_name = {e["strategy"]: e for e in reg["entries"]}

    assert by_name["robust_hi"]["status"] == "ACTIVE"
    assert by_name["robust_lo"]["status"] == "ACTIVE"
    assert by_name["fragile"]["status"] == "WATCH"
    assert by_name["overfit"]["status"] == "REJECTED"

    assert reg["n_active"] == 2
    # Gewichte der Aktiven normalisiert auf ~1, Nicht-Aktive = 0.
    active_weight = sum(e["weight"] for e in reg["entries"] if e["status"] == "ACTIVE")
    assert active_weight == pytest.approx(1.0, abs=1e-3)
    assert by_name["fragile"]["weight"] == 0.0
    assert by_name["overfit"]["weight"] == 0.0
    # Der robustere Kandidat trägt mehr Gewicht.
    assert by_name["robust_hi"]["weight"] > by_name["robust_lo"]["weight"]


def test_build_registry_sorts_active_first_by_weight():
    reports = [
        _report("overfit", "OVERFIT"),
        _report("robust_lo", "ROBUST", median=0.2, pct_pos=0.6, wf_eff=0.6),
        _report("robust_hi", "ROBUST", median=1.0, pct_pos=0.8, wf_eff=1.0),
    ]
    names = [e["strategy"] for e in promotion.build_registry(reports)["entries"]]
    assert names[0] == "robust_hi"  # höchstes Gewicht zuerst
    assert names[1] == "robust_lo"
    assert names[-1] == "overfit"   # REJECTED ans Ende


def test_no_active_yields_zero_weights():
    reg = promotion.build_registry([_report("fragile", "FRAGILE")])
    assert reg["n_active"] == 0
    assert reg["entries"][0]["weight"] == 0.0


def test_save_load_active_roundtrip(tmp_path, monkeypatch):
    target = tmp_path / "strategy_registry.json"
    monkeypatch.setattr(promotion, "_REGISTRY_FILE", str(target))

    assert promotion.load_registry() is None  # noch nichts da
    assert promotion.active_strategies() == []

    reg = promotion.build_registry([
        _report("robust_hi", "ROBUST", median=1.0),
        _report("overfit", "OVERFIT"),
    ])
    path = promotion.save_registry(reg)
    assert json.load(open(path))["n_active"] == 1

    loaded = promotion.load_registry()
    assert loaded["n_active"] == 1
    active = promotion.active_strategies()
    assert [e["strategy"] for e in active] == ["robust_hi"]


def test_load_registry_handles_corrupt_file(tmp_path, monkeypatch):
    target = tmp_path / "strategy_registry.json"
    target.write_text("{ not valid json")
    monkeypatch.setattr(promotion, "_REGISTRY_FILE", str(target))
    assert promotion.load_registry() is None
