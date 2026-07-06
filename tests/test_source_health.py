"""
Test für AnalysisLog.source_health – klassifiziert Quellen als tot/schwach/gesund.
"""
import json
from datetime import datetime, timezone

import analyzers.analysis_log as al_mod
from analyzers.analysis_log import AnalysisLog


def _make_log(tmp_path, monkeypatch):
    monkeypatch.setattr(al_mod, "DB_PATH", str(tmp_path / "analysis_log.db"))
    return AnalysisLog()


def _insert(log, breakdown):
    log._conn.execute(
        """INSERT INTO analyses
           (analyzed_at, ticker, recommendation, direction, sentiment_score,
            confidence, sources_used, sources_breakdown)
           VALUES (?,?,?,?,?,?,?,?)""",
        (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "X", "SKIP", "NEUTRAL", 0.5, "LOW",
         sum(breakdown.values()), json.dumps(breakdown)),
    )
    log._conn.commit()


def test_source_health_classifies(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    # 10 Analysen: newsapi immer aktiv, twitter immer 0 (tot), reddit selten (schwach)
    for i in range(10):
        _insert(log, {
            "newsapi": 3,
            "twitter": 0,
            "reddit": 2 if i == 0 else 0,   # 1/10 = 10% → schwach (< 10% Grenze? genau 10 → nicht <10 → grenzwertig)
        })
    h = log.source_health(days=7, min_analyses=5, weak_pct=15.0)
    assert h["n_analyses"] == 10
    assert h["reliable"] is True
    assert "twitter" in h["dead"]          # 0 Treffer → tot
    assert "newsapi" in h["healthy"]       # 100% → gesund
    assert "reddit" in h["weak"]           # 10% < 15% → schwach


def test_source_health_no_data(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    h = log.source_health(days=7)
    assert h["n_analyses"] == 0
    assert h["reliable"] is False
    assert h["dead"] == [] and h["healthy"] == []
