"""
Tests für analyzers/sentiment_forward_study.py (Roadmap 3.1: Sentiment-Forward-Study).
"""
import numpy as np
import pytest

from analyzers.experience_store import ExperienceStore
from analyzers.sentiment_forward_study import (
    _bucket_label,
    bucket_edges,
    information_coefficient,
    load_labeled_scores,
    run_study,
)


def _feat(ticker="AAPL", decided_at="2026-01-01T10:00:00", sentiment_score=0.7, **kw):
    base = {
        "decided_at": decided_at, "ticker": ticker, "recommendation": "BUY",
        "direction": "LONG", "sentiment_score": sentiment_score, "confidence": "HIGH",
    }
    base.update(kw)
    return base


@pytest.fixture
def store(tmp_path):
    s = ExperienceStore(db_path=str(tmp_path / "exp.db"))
    yield s
    s.close()


# ── _bucket_label ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,expected", [
    (0.0, "<0.30"), (0.29, "<0.30"),
    (0.3, "0.30–0.50"), (0.49, "0.30–0.50"),
    (0.5, "0.50–0.65"), (0.64, "0.50–0.65"),
    (0.65, "0.65–0.80"), (0.79, "0.65–0.80"),
    (0.8, "≥0.80"), (1.0, "≥0.80"),
])
def test_bucket_label_boundaries(score, expected):
    assert _bucket_label(score) == expected


# ── load_labeled_scores ───────────────────────────────────────────────────────

def test_load_labeled_scores_filters_missing_pnl_or_score(store):
    a = store.upsert_decision(_feat(ticker="A", sentiment_score=0.7))
    store.attach_outcome(a, {"outcome": "WIN", "pnl_pct": 5.0, "label_source": "backfill"})
    b = store.upsert_decision(_feat(ticker="B", sentiment_score=None))
    store.attach_outcome(b, {"outcome": "LOSS", "pnl_pct": -2.0, "label_source": "backfill"})
    c = store.upsert_decision(_feat(ticker="C", sentiment_score=0.9))
    # kein attach_outcome für C -> pnl_pct fehlt (nicht "gelabelt", taucht in
    # iter_labeled ohnehin nicht auf)

    rows = load_labeled_scores(store)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "A"
    assert rows[0]["sentiment_score"] == 0.7
    assert rows[0]["pnl_pct"] == 5.0


def test_load_labeled_scores_filters_by_source(store):
    a = store.upsert_decision(_feat(ticker="A", decided_at="2026-01-01T10:00:00"))
    store.attach_outcome(a, {"outcome": "WIN", "pnl_pct": 5.0, "label_source": "live"})
    b = store.upsert_decision(_feat(ticker="B", decided_at="2026-01-02T10:00:00"))
    store.attach_outcome(b, {"outcome": "WIN", "pnl_pct": 3.0, "label_source": "backfill_hypo"})

    live_only = load_labeled_scores(store, sources={"live"})
    assert len(live_only) == 1
    assert live_only[0]["ticker"] == "A"


# ── bucket_edges ──────────────────────────────────────────────────────────────

def test_bucket_edges_groups_by_bucket_and_computes_win_rate():
    rows = [
        {"sentiment_score": 0.2, "pnl_pct": -3.0},
        {"sentiment_score": 0.9, "pnl_pct": 4.0},
        {"sentiment_score": 0.85, "pnl_pct": 2.0},
    ]
    rng = np.random.default_rng(1)
    out = bucket_edges(rows, rng, iters=200)
    assert out["<0.30"]["n"] == 1
    assert out["≥0.80"]["n"] == 2
    assert out["≥0.80"]["win_rate"] == 1.0
    assert out["0.30–0.50"]["n"] == 0


def test_bucket_edges_empty_bucket_has_nan_ci():
    rows = [{"sentiment_score": 0.9, "pnl_pct": 1.0}]
    rng = np.random.default_rng(1)
    out = bucket_edges(rows, rng, iters=50)
    assert out["<0.30"]["n"] == 0
    assert np.isnan(out["<0.30"]["mean"])


# ── information_coefficient ───────────────────────────────────────────────────

def test_information_coefficient_too_few_points_returns_nan():
    rows = [{"sentiment_score": 0.5, "pnl_pct": 1.0},
            {"sentiment_score": 0.6, "pnl_pct": 2.0}]
    rng = np.random.default_rng(1)
    out = information_coefficient(rows, rng)
    assert out["n"] == 2
    assert np.isnan(out["ic"])


def test_information_coefficient_perfect_positive_relationship():
    """Monoton steigende Kunst-Daten: IC muss klar positiv sein, CI schließt 0 aus."""
    rows = [{"sentiment_score": s, "pnl_pct": s * 10} for s in
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]]
    rng = np.random.default_rng(42)
    out = information_coefficient(rows, rng, iters=2000)
    assert out["ic"] == pytest.approx(1.0)
    assert out["lo"] > 0.9
    assert out["p_le0"] == 0.0


def test_information_coefficient_no_relationship_ci_spans_zero():
    """Zufällige, unkorrelierte Kunst-Daten: CI sollte die Null nicht sicher
    ausschließen (kein erfundenes Signal aus Rauschen)."""
    rng_data = np.random.default_rng(7)
    scores = rng_data.uniform(0, 1, size=200)
    pnls = rng_data.normal(0, 5, size=200)  # unabhängig von scores
    rows = [{"sentiment_score": float(s), "pnl_pct": float(p)}
            for s, p in zip(scores, pnls)]
    rng = np.random.default_rng(42)
    out = information_coefficient(rows, rng, iters=2000)
    assert out["lo"] < 0 < out["hi"]


# ── run_study ─────────────────────────────────────────────────────────────────

def test_run_study_reports_source_breakdown_and_no_missing_bucket(store):
    a = store.upsert_decision(_feat(ticker="A", decided_at="2026-01-01T10:00:00",
                                    sentiment_score=0.2))
    store.attach_outcome(a, {"outcome": "LOSS", "pnl_pct": -4.0, "label_source": "backfill_hypo"})
    b = store.upsert_decision(_feat(ticker="B", decided_at="2026-01-02T10:00:00",
                                    sentiment_score=0.9))
    store.attach_outcome(b, {"outcome": "WIN", "pnl_pct": 6.0, "label_source": "live"})

    result = run_study(store=store, iters=100)
    assert result["n_total"] == 2
    assert result["by_source"] == {"backfill_hypo": 1, "live": 1}
    assert set(result["buckets"].keys()) == {"<0.30", "0.30–0.50", "0.50–0.65",
                                              "0.65–0.80", "≥0.80"}
    assert "ic" in result["information_coefficient"]


def test_run_study_empty_store_is_honest_about_zero_data(store):
    result = run_study(store=store, iters=50)
    assert result["n_total"] == 0
    assert result["by_source"] == {}
    assert result["information_coefficient"]["n"] == 0
