"""
Tests für analyzers/calibration.py – Beta-Binomial-Shrinkage und Persistenz.
"""
import pytest

from analyzers.calibration import (
    CalibrationModel, _PRIOR_STRENGTH, _MIN_SUPPORT,
)


def _row(sentiment, outcome, pnl, confidence="MEDIUM", debate="BULL"):
    feat = {"sentiment_score": sentiment, "confidence": confidence, "debate_winner": debate}
    out = {"outcome": outcome, "pnl_pct": pnl}
    return feat, out


def test_small_bucket_shrinks_to_prior():
    # Globalquote ~50% aus vielen 0.5er-Trades; ein winziger 0.9er-Bucket mit 1 WIN.
    rows = []
    for i in range(50):
        rows.append(_row(0.50, "WIN" if i % 2 == 0 else "LOSS", 1.0 if i % 2 == 0 else -1.0))
    rows.append(_row(0.95, "WIN", 5.0))  # n=1 Bucket 0.9-1.0
    m = CalibrationModel().fit_rows(rows)
    tbl = m.tables["sentiment"]
    small = tbl["0.9-1.0"]
    assert small.raw_win_rate == 1.0           # roh: 100%
    # geshrinkt deutlich Richtung Globalquote (~0.5), nicht 100%
    assert small.win_rate < 0.6
    assert small.reliable is False             # n=1 < min_support


def test_large_bucket_stays_near_empirical():
    # Großer 0.7er-Bucket mit 80% Win; ein gegenläufiger Bucket drückt die Globalquote
    # nach unten. Der große Bucket bleibt trotz Shrinkage nahe seiner Empirie.
    rows = []
    for i in range(100):
        win = i % 10 < 8  # 80%
        rows.append(_row(0.75, "WIN" if win else "LOSS", 2.0 if win else -2.0))
    for _ in range(100):  # zieht global Richtung ~40%
        rows.append(_row(0.35, "LOSS", -2.0))
    m = CalibrationModel().fit_rows(rows)
    assert m.global_win_rate < 0.5
    s = m.tables["sentiment"]["0.7-0.8"]
    assert s.reliable is True
    # geshrinkt liegt zwischen Globalquote und 80%, aber klar näher an 80%
    assert 0.72 < s.win_rate < 0.80


def test_global_fallback_for_unknown_bucket():
    rows = [_row(0.50, "WIN", 1.0), _row(0.50, "LOSS", -1.0)]
    m = CalibrationModel().fit_rows(rows)
    res = m.calibrate({"sentiment_score": 0.05}, dimension="sentiment")
    assert res.n_support == 0
    assert res.reliable is False
    assert res.p_win == pytest.approx(m.global_win_rate, abs=1e-6)


def test_calibrate_returns_bucket_estimate():
    rows = [_row(0.75, "WIN", 3.0) for _ in range(20)]
    m = CalibrationModel().fit_rows(rows)
    res = m.calibrate({"sentiment_score": 0.73}, dimension="sentiment")
    assert res.basis == "sentiment=0.7-0.8"
    assert res.n_support == 20
    assert res.reliable is True
    assert res.p_win > 0.9                     # fast nur WINs


def test_save_load_roundtrip(tmp_path):
    rows = [_row(0.75, "WIN", 3.0) for _ in range(10)] + [_row(0.6, "LOSS", -2.0) for _ in range(10)]
    path = str(tmp_path / "calib.json")
    m = CalibrationModel(model_file=path).fit_rows(rows)
    m.save()
    m2 = CalibrationModel(model_file=path)
    assert m2.load() is True
    assert m2.global_win_rate == pytest.approx(m.global_win_rate)
    a = m.calibrate({"sentiment_score": 0.75})
    b = m2.calibrate({"sentiment_score": 0.75})
    assert a.p_win == pytest.approx(b.p_win)


def test_confidence_and_debate_dimensions_present():
    rows = [_row(0.7, "WIN", 1.0, confidence="HIGH", debate="BULL") for _ in range(5)]
    m = CalibrationModel().fit_rows(rows)
    assert "HIGH" in m.tables["confidence"]
    assert "BULL" in m.tables["debate_winner"]
