"""
Tests für scripts/skip_counterfactual.py (Roadmap 3.2) — Skip-Gegenprobe,
Bucket-Aggregation und der decision_log-Read. Netzfrei (load_bars gemockt).
"""
import sqlite3

import numpy as np
import pytest

from scripts.skip_counterfactual import (
    _read_skip_decisions,
    _simulate_skips,
    _aggregate_by_bucket,
)


def _bars(closes, start="2026-01-01"):
    """Tages-Bars ab start, ein Tag pro Eintrag, High/Low = Close (kein Trigger)."""
    from datetime import datetime, timedelta
    d0 = datetime.strptime(start, "%Y-%m-%d")
    return [{"date": (d0 + timedelta(days=i)).strftime("%Y-%m-%d"),
            "open": c, "high": c, "low": c, "close": c} for i, c in enumerate(closes)]


def _make_decision_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE decisions (ticker TEXT, decided_at TEXT, reason TEXT, "
        "recommendation TEXT, direction TEXT, sentiment_score REAL, action TEXT)"
    )
    return conn


# ── _read_skip_decisions ────────────────────────────────────────────────────
def test_read_skip_decisions_only_skip_with_buy_or_sell_recommendation():
    conn = _make_decision_db()
    conn.execute("INSERT INTO decisions VALUES ('AAPL','T1','< Schwelle','BUY','LONG',0.6,'SKIP')")
    conn.execute("INSERT INTO decisions VALUES ('MSFT','T2','Kein Kaufsignal','HOLD',NULL,0.4,'SKIP')")
    conn.execute("INSERT INTO decisions VALUES ('NVDA','T3','ok','BUY','LONG',0.9,'BUY')")
    rows = _read_skip_decisions(conn)
    assert len(rows) == 1
    assert rows[0]["ticker"] == "AAPL"


def test_read_skip_decisions_respects_limit():
    conn = _make_decision_db()
    for i in range(5):
        conn.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
            (f"T{i}", f"2026-01-0{i+1}", "< Schwelle", "BUY", "LONG", 0.6, "SKIP"),
        )
    rows = _read_skip_decisions(conn, limit=2)
    assert len(rows) == 2


# ── _simulate_skips ──────────────────────────────────────────────────────────
def test_simulate_skips_labels_win_and_bucket():
    rows = [{"ticker": "AAPL", "decided_at": "2026-01-01", "reason": "Score 0.6 < Schwelle 0.7",
            "recommendation": "BUY", "direction": "LONG"}]

    def fake_load_bars(ticker, start):
        return _bars([100, 130] + [130] * 25, start="2026-01-01")  # springt sofort über TP

    sim = _simulate_skips(rows, load_bars=fake_load_bars, default_hold=20)
    assert len(sim) == 1
    assert sim[0]["bucket"] == "unter_schwelle"
    assert sim[0]["outcome"] == "WIN"


def test_simulate_skips_drops_rows_without_price_data():
    rows = [{"ticker": "XYZ", "decided_at": "2026-01-01", "reason": "Korrelation zu hoch",
            "recommendation": "BUY", "direction": "LONG"}]
    sim = _simulate_skips(rows, load_bars=lambda t, s: [], default_hold=20)
    assert sim == []


def test_simulate_skips_normalizes_direction_from_recommendation_when_missing():
    rows = [{"ticker": "AAPL", "decided_at": "2026-01-01", "reason": "Zu wenige Quellen",
            "recommendation": "SELL", "direction": None}]

    def fake_load_bars(ticker, start):
        return _bars([100, 70] + [70] * 25, start="2026-01-01")  # fällt stark -> SHORT gewinnt

    sim = _simulate_skips(rows, load_bars=fake_load_bars, default_hold=20)
    assert sim[0]["outcome"] == "WIN"
    assert sim[0]["bucket"] == "zu_wenige_quellen"


# ── _aggregate_by_bucket ─────────────────────────────────────────────────────
def test_aggregate_by_bucket_ok_status_with_enough_data():
    rng = np.random.default_rng(1)
    sim = ([{"bucket": "unter_schwelle", "pnl_pct": 3.0, "outcome": "WIN"}] * 12
          + [{"bucket": "unter_schwelle", "pnl_pct": -1.0, "outcome": "LOSS"}] * 3)
    results = _aggregate_by_bucket(sim, rng, min_n=10)
    r = next(x for x in results if x["bucket"] == "unter_schwelle")
    assert r["status"] == "ok"
    assert r["n"] == 15
    assert r["win_rate"] == pytest.approx(12 / 15)


def test_aggregate_by_bucket_insufficient_below_min_n():
    rng = np.random.default_rng(2)
    sim = [{"bucket": "korrelation", "pnl_pct": 1.0, "outcome": "WIN"} for _ in range(4)]
    results = _aggregate_by_bucket(sim, rng, min_n=10)
    r = next(x for x in results if x["bucket"] == "korrelation")
    assert r["status"] == "insufficient"
    assert r["n"] == 4


def test_aggregate_by_bucket_separates_multiple_buckets():
    rng = np.random.default_rng(3)
    sim = ([{"bucket": "a", "pnl_pct": 1.0, "outcome": "WIN"} for _ in range(10)]
          + [{"bucket": "b", "pnl_pct": -1.0, "outcome": "LOSS"} for _ in range(10)])
    results = _aggregate_by_bucket(sim, rng, min_n=10)
    by_bucket = {r["bucket"]: r for r in results}
    assert by_bucket["a"]["mean"] == pytest.approx(1.0)
    assert by_bucket["b"]["mean"] == pytest.approx(-1.0)
