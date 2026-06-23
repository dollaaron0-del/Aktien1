"""
Tests für analyzers/entry_filter.py – Urteilslogik auf einem synthetischen
CalibrationModel (kein I/O, kein Netz).
"""
import pytest

from analyzers.calibration import CalibrationModel
from analyzers.entry_filter import EntryFilter


def _model_from(rows):
    return CalibrationModel().fit_rows(rows)


def _row(sentiment, outcome, pnl):
    return ({"sentiment_score": sentiment, "confidence": "MEDIUM", "debate_winner": "BULL"},
            {"outcome": outcome, "pnl_pct": pnl})


def test_avoid_on_negative_edge_band():
    # 0.6-0.7 Band mit klar negativer Kante, genug Daten -> AVOID
    rows = [_row(0.65, "LOSS", -7.0) for _ in range(20)] + [_row(0.65, "WIN", 2.0) for _ in range(4)]
    f = EntryFilter(_model_from(rows))
    v = f.evaluate({"sentiment_score": 0.65})
    assert v.verdict == "AVOID"
    assert v.block is True
    assert v.reliable is True


def test_proceed_on_positive_edge():
    rows = [_row(0.75, "WIN", 5.0) for _ in range(20)]
    f = EntryFilter(_model_from(rows))
    v = f.evaluate({"sentiment_score": 0.75})
    assert v.verdict == "PROCEED"
    assert v.block is False


def test_neutral_when_bucket_too_thin():
    # Großer Trainings-Satz in einem Band, Abfrage in einem leeren/dünnen Band.
    rows = [_row(0.75, "WIN", 5.0) for _ in range(20)]
    f = EntryFilter(_model_from(rows))
    v = f.evaluate({"sentiment_score": 0.15})   # unbekannter Bucket -> global fallback
    assert v.verdict == "NEUTRAL"
    assert v.reliable is False


def test_caution_on_marginal_edge():
    # Kante zwischen AVOID- und GOOD-Schwelle -> CAUTION
    rows = ([_row(0.55, "WIN", 1.0) for _ in range(11)] +
            [_row(0.55, "LOSS", -1.0) for _ in range(11)])
    f = EntryFilter(_model_from(rows))
    v = f.evaluate({"sentiment_score": 0.55})
    assert v.verdict == "CAUTION"
    assert v.reliable is True


def test_reasons_present():
    rows = [_row(0.75, "WIN", 5.0) for _ in range(20)]
    v = EntryFilter(_model_from(rows)).evaluate({"sentiment_score": 0.75})
    assert any("Edge" in r for r in v.reasons)
