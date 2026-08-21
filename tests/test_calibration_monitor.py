"""
Tests für scripts/calibration_monitor.py — die Güte-Metriken (Brier, BSS, AUC,
Reliability/ECE), die Drift-Erkennung und die Sizing-Gates. Netzfrei; der
Walk-Forward-Smoke-Test nutzt synthetische (features, outcome)-Zeilen.
"""
import numpy as np

from scripts.calibration_monitor import (
    brier,
    brier_skill_score,
    auc,
    reliability_bins,
    ece_mce,
    drift_check,
    evaluate_gates,
    walk_forward,
)


# ── Brier / BSS ───────────────────────────────────────────────────────────────
def test_brier_perfect_and_worst():
    assert brier([1.0, 0.0], [1, 0]) == 0.0
    assert brier([0.0, 1.0], [1, 0]) == 1.0
    assert brier([0.5, 0.5], [1, 0]) == 0.25


def test_brier_skill_score_perfect_is_one():
    assert abs(brier_skill_score([1.0, 1.0, 0.0, 0.0], [1, 1, 0, 0]) - 1.0) < 1e-9


def test_brier_skill_score_climatology_is_zero():
    # Immer die Basisquote raten → BSS = 0 (kein Skill über Klimatologie).
    ys = [1, 1, 0, 0, 1, 0]
    base = np.mean(ys)
    assert abs(brier_skill_score([base] * len(ys), ys)) < 1e-9


# ── AUC ───────────────────────────────────────────────────────────────────────
def test_auc_perfect_ranking():
    assert auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0


def test_auc_inverted_ranking():
    assert auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0


def test_auc_ties_half():
    assert auc([0.5, 0.5], [1, 0]) == 0.5


def test_auc_single_class_is_nan():
    assert np.isnan(auc([0.5, 0.6], [1, 1]))


# ── Reliability / ECE ─────────────────────────────────────────────────────────
def test_reliability_bins_and_ece():
    # zwei Bänder, klar definierte Abweichung
    ps = [0.35, 0.35, 0.75, 0.75]
    ys = [0, 0, 1, 1]   # 0.3er-Band obs 0 (Δ 0.35), 0.7er-Band obs 1 (Δ 0.25)
    bins = reliability_bins(ps, ys)
    assert len(bins) == 2
    ece, mce = ece_mce(bins, len(ps))
    # ECE = 0.5*0.35 + 0.5*0.25 = 0.30 ; MCE = 0.35
    assert abs(ece - 0.30) < 1e-9
    assert abs(mce - 0.35) < 1e-9


def test_reliability_last_bin_includes_one():
    bins = reliability_bins([1.0, 1.0], [1, 1])
    assert len(bins) == 1 and bins[0]["n"] == 2


def test_ece_empty():
    e, m = ece_mce([], 0)
    assert np.isnan(e) and np.isnan(m)


# ── Drift ─────────────────────────────────────────────────────────────────────
def _preds(ps, ys):
    return [{"ts": f"t{i}", "p": p, "y": y, "pnl": 0.0}
            for i, (p, y) in enumerate(zip(ps, ys))]


def test_drift_alarm_when_recent_degrades():
    # frühe Hälfte perfekt kalibriert, jüngste stark überkonfident
    good = _preds([0.0] * 20, [0] * 20)               # ECE 0
    bad = _preds([0.9] * 20, [0] * 20)                # ECE 0.9
    d = drift_check(good + bad)
    assert d["available"] and d["alarm"] is True
    assert d["ece_delta"] > 0


def test_drift_insufficient_data():
    d = drift_check(_preds([0.5] * 10, [0, 1] * 5))
    assert d["available"] is False


# ── Gates ─────────────────────────────────────────────────────────────────────
def _good_drift():
    return {"available": True, "alarm": False, "ece_delta": -0.01,
            "early_ece": 0.05, "recent_ece": 0.04}


def test_gates_all_pass_when_well_calibrated():
    m = {"n": 150, "ece": 0.05, "bss": 0.1, "auc": 0.7}
    gates = evaluate_gates(m, _good_drift(), n_live=150)
    assert all(ok for _, ok, _ in gates)


def test_gates_fail_on_overconfidence_and_small_n():
    m = {"n": 40, "ece": 0.126, "bss": -0.02, "auc": 0.61}
    gates = evaluate_gates(m, _good_drift(), n_live=0)
    names_failed = {name for name, ok, _ in gates if not ok}
    assert any("Stichprobe" in n for n in names_failed)
    assert any("kalibriert" in n for n in names_failed)
    assert any("Klimatologie" in n for n in names_failed)


# ── Walk-Forward (Integration, netzfrei) ──────────────────────────────────────
def test_walk_forward_produces_valid_predictions():
    rows = []
    for i in range(30):
        feat = {"decided_at": f"2026-05-{i+1:02d}T00:00:00",
                "sentiment_score": 0.6, "ticker": "AAA", "regime": "BULL",
                "confidence": "MEDIUM"}
        outcome = {"outcome": "WIN" if i % 2 == 0 else "LOSS",
                   "pnl_pct": 1.0 if i % 2 == 0 else -1.0,
                   "label_source": "backfill"}
        rows.append((feat, outcome))
    preds = walk_forward(rows, dimensions=("sentiment", "regime"), warmup=5)
    assert len(preds) == 25
    assert all(0.0 <= d["p"] <= 1.0 for d in preds)
    assert all(d["y"] in (0, 1) for d in preds)
