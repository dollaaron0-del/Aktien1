"""
Tests für dashboard/compare.py (Ausbau-Roadmap H2.4 — Zeitraum-Vergleich).

week_stats() gegen präparierte DecisionLog-Einträge (bare DecisionLog() —
conftest.py bindet DECISION_LOG_PATH auf eine Test-DB) + eine per
DB_PATH-Monkeypatch isolierte AnalysisLog-DB (Muster: Portfolio/PORTFOLIO_DB
— DB_PATH wird zur Laufzeit gelesen, nicht als Default-Parameter gebunden).
"""
from dashboard.compare import _daterange, week_stats


def _log_decision(ticker, action, decided_at):
    from analyzers.decision_log import DecisionLog
    DecisionLog().log({
        "ticker": ticker, "action": action, "reason": "Test",
        "recommendation": action if action != "SKIP" else "SKIP",
        "sentiment_score": 0.5, "decided_at": decided_at,
    })


def _seed_analysis(monkeypatch, tmp_path, rows):
    import analyzers.analysis_log as alog_mod
    monkeypatch.setattr(alog_mod, "DB_PATH", str(tmp_path / "analysis_log_test.db"))
    alog = alog_mod.AnalysisLog()
    for analyzed_at, ticker, score in rows:
        alog._conn.execute(
            "INSERT INTO analyses (analyzed_at, ticker, recommendation, "
            "direction, sentiment_score, confidence) VALUES (?,?,?,?,?,?)",
            (analyzed_at, ticker, "BUY", "BULLISH", score, "HIGH"),
        )
    alog._conn.commit()
    return alog


# ── _daterange() ──────────────────────────────────────────────────────────────

def test_daterange_inclusive_both_ends():
    assert _daterange("2026-07-10", "2026-07-12") == [
        "2026-07-10", "2026-07-11", "2026-07-12",
    ]


def test_daterange_single_day():
    assert _daterange("2026-07-10", "2026-07-10") == ["2026-07-10"]


def test_daterange_swaps_reversed_order():
    assert _daterange("2026-07-12", "2026-07-10") == [
        "2026-07-10", "2026-07-11", "2026-07-12",
    ]


# ── week_stats() ──────────────────────────────────────────────────────────────

def test_week_stats_sums_decision_log_actions_across_days():
    _log_decision("AAPL", "BUY", "2026-07-10T09:00:00")
    _log_decision("NVDA", "SKIP", "2026-07-10T09:05:00")
    _log_decision("MSFT", "BUY", "2026-07-11T09:00:00")
    _log_decision("TSLA", "SKIP", "2026-07-20T09:00:00")  # außerhalb des Zeitraums

    stats = week_stats("2026-07-10", "2026-07-11")
    assert stats["total"] == 3
    assert stats["buy"] == 2
    assert stats["skip"] == 1
    assert stats["days"] == ["2026-07-10", "2026-07-11"]


def test_week_stats_missing_days_count_as_zero():
    stats = week_stats("2030-01-01", "2030-01-02")  # garantiert leer
    assert stats["total"] == 0
    assert stats["buy"] == 0
    assert stats["n_analyses"] == 0
    assert stats["avg_sentiment"] == 0.0


def test_week_stats_averages_analysis_log_sentiment_in_range(monkeypatch, tmp_path):
    _seed_analysis(monkeypatch, tmp_path, [
        ("2026-07-10T09:00:00", "AAPL", 0.8),
        ("2026-07-11T09:00:00", "NVDA", 0.4),
        ("2026-07-20T09:00:00", "TSLA", 0.0),  # außerhalb
    ])
    stats = week_stats("2026-07-10", "2026-07-11")
    assert stats["n_analyses"] == 2
    assert stats["avg_sentiment"] == 0.6


def test_week_stats_fail_open_on_decision_log_error(monkeypatch):
    import analyzers.decision_log as dlog_mod

    class _Boom:
        def funnel(self, day):
            raise RuntimeError("kaputt")

    monkeypatch.setattr(dlog_mod, "DecisionLog", _Boom)
    stats = week_stats("2026-07-10", "2026-07-11")
    assert stats["total"] == 0  # kein Crash, nur 0


def test_week_stats_fail_open_on_analysis_log_error(monkeypatch):
    import analyzers.analysis_log as alog_mod

    class _Boom:
        def get_recent(self, limit=5000):
            raise RuntimeError("kaputt")

    monkeypatch.setattr(alog_mod, "AnalysisLog", _Boom)
    stats = week_stats("2026-07-10", "2026-07-11")
    assert stats["n_analyses"] == 0
    assert stats["avg_sentiment"] == 0.0
