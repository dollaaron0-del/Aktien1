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


# ── E2: Regime- und Makro-Dimension ─────────────────────────────────────────────
def test_regime_dimension_learns_per_regime():
    # BEAR-Regime verliert, BULL gewinnt – die Regime-Dimension muss das trennen.
    rows = []
    for _ in range(12):
        f, o = _row(0.6, "LOSS", -3.0); f["regime"] = "BEAR"; rows.append((f, o))
    for _ in range(12):
        f, o = _row(0.6, "WIN", 3.0); f["regime"] = "BULL"; rows.append((f, o))
    m = CalibrationModel().fit_rows(rows)
    assert m.calibrate({"regime": "BEAR"}, dimension="regime").expected_edge < 0
    assert m.calibrate({"regime": "BULL"}, dimension="regime").expected_edge > 0


def test_macro_bias_bucketing():
    rows = [(_row(0.6, "WIN", 1.0)[0] | {"macro_bias": -0.9}, {"outcome": "WIN", "pnl_pct": 1.0})
            for _ in range(9)]
    m = CalibrationModel().fit_rows(rows)
    assert "RISK_OFF" in m.tables["macro_bias"]
    # NEUTRAL-Bucket wurde nie beobachtet → Fallback auf Globalquote.
    res = m.calibrate({"macro_bias": 0.0}, dimension="macro_bias")
    assert res.basis == "macro_bias=global"
    assert res.n_support == 0
    # Der beobachtete RISK_OFF-Bucket ist dagegen auffindbar.
    ro = m.calibrate({"macro_bias": -0.9}, dimension="macro_bias")
    assert ro.basis == "macro_bias=RISK_OFF"


def test_missing_regime_is_ignored_not_crashing():
    # Zeilen ohne regime dürfen die Regime-Tabelle nicht mit None-Buckets füllen.
    rows = [_row(0.6, "WIN", 1.0) for _ in range(5)]
    m = CalibrationModel().fit_rows(rows)
    assert m.tables["regime"] == {}


# ── A3: Gewichteter Live/Backfill-Blend ─────────────────────────────────────────
def _tag(rows, source):
    """Setzt label_source im Outcome (fit() partitioniert selbst darüber)."""
    return [(f, {**o, "label_source": source}) for f, o in rows]


class _FakeStore:
    """Minimaler ExperienceStore-Ersatz: liefert vorgegebene Live/Backfill-Zeilen."""
    def __init__(self, live, backfill):
        self._live = _tag(live, "live")
        self._backfill = _tag(backfill, "backfill")

    def iter_labeled(self, label_source=None, recommendation=None):
        if label_source == "live":
            return iter(self._live)
        if label_source == "backfill":
            return iter(self._backfill)
        return iter(self._live + self._backfill)


def test_blend_backfill_weight_ramps_with_live():
    live = [_row(0.6, "WIN", 5.0) for _ in range(15)]          # klar positiv
    backfill = [_row(0.6, "LOSS", -5.0) for _ in range(100)]   # klar negativ
    # 0 Live → reiner Backfill (negativ), full Live (>=min_live) → nur Live (positiv).
    all_bf = CalibrationModel().fit(_FakeStore([], backfill), min_live=30)
    only_live = CalibrationModel().fit(_FakeStore(live * 3, backfill), min_live=30)  # 45 >= 30
    half = CalibrationModel().fit(_FakeStore(live, backfill), min_live=30)           # 15/30 → bf-Gewicht 0.5
    assert all_bf.global_avg_pnl < 0
    assert only_live.global_avg_pnl > 0
    # Der Halb-Blend liegt echt dazwischen (glatter Übergang, kein Sprung).
    assert all_bf.global_avg_pnl < half.global_avg_pnl < only_live.global_avg_pnl


def test_blend_endpoints_match_old_hard_cutoff():
    # >= min_live Live-Outcomes: Backfill-Gewicht 0 → identisch zu 'nur Live'.
    live = [_row(0.7, "WIN", 2.0) for _ in range(30)]
    backfill = [_row(0.7, "LOSS", -9.0) for _ in range(50)]
    blended = CalibrationModel().fit(_FakeStore(live, backfill), min_live=30)
    live_only = CalibrationModel().fit_rows(live)
    assert blended.global_avg_pnl == pytest.approx(live_only.global_avg_pnl)


def test_blend_includes_backfill_hypo_rows():
    """Regression: backfill_hypo (hypothetische HOLD/SKIP-Labels) müssen im
    Blend mitzählen — alles, was nicht 'live' ist, gehört zum Backfill-Topf."""
    class _HypoStore:
        def iter_labeled(self, label_source=None, recommendation=None):
            rows = (_tag([_row(0.6, "WIN", 3.0)] * 4, "backfill")
                    + _tag([_row(0.6, "LOSS", -3.0)] * 4, "backfill_hypo"))
            if label_source == "live":
                return iter([])
            if label_source == "backfill":
                return iter(rows[:4])
            return iter(rows)

    m = CalibrationModel().fit(_HypoStore(), min_live=30)
    assert m.n_total == 8                                     # 4 + 4, nicht nur 4
    assert m.global_avg_pnl == pytest.approx(0.0)             # +3/-3 heben sich auf
