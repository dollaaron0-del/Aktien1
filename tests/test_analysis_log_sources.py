"""
Tests für die Collector-Beitrags-Auswertung in analyzers/analysis_log.py.
"""
import json
from datetime import datetime, timezone

import analyzers.analysis_log as al_mod
from analyzers.analysis_log import AnalysisLog
from analyzers.claude_analyzer import AnalysisResult


def make_log(tmp_path, monkeypatch):
    db = str(tmp_path / "analysis_log.db")
    monkeypatch.setattr(al_mod, "DB_PATH", db)
    return AnalysisLog()


def _insert(log, breakdown, when=None):
    log._conn.execute(
        "INSERT INTO analyses (analyzed_at, ticker, recommendation, direction, "
        "sentiment_score, confidence, sources_used, sources_breakdown) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            (when or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
            "AAPL", "BUY", "BULLISH", 0.7, "MEDIUM",
            sum(breakdown.values()), json.dumps(breakdown),
        ),
    )
    log._conn.commit()


def test_migration_adds_column(tmp_path, monkeypatch):
    """Die sources_breakdown-Spalte existiert nach Init."""
    log = make_log(tmp_path, monkeypatch)
    cols = [r[1] for r in log._conn.execute("PRAGMA table_info(analyses)").fetchall()]
    assert "sources_breakdown" in cols


def test_aggregates_contributions(tmp_path, monkeypatch):
    """Treffer werden korrekt pro Quelle summiert; Null-Quellen erscheinen mit 0."""
    log = make_log(tmp_path, monkeypatch)
    _insert(log, {"german_media": 10, "twitter": 0, "newsapi": 3})
    _insert(log, {"german_media": 5, "twitter": 0, "newsapi": 0})

    contrib = {c["source"]: c for c in log.get_source_contributions(days=30)}
    assert contrib["german_media"]["total_hits"] == 15
    assert contrib["german_media"]["analyses_with_hits"] == 2
    assert contrib["german_media"]["pct_analyses"] == 100.0
    # twitter trägt nie bei → Abschalt-Kandidat
    assert contrib["twitter"]["total_hits"] == 0
    assert contrib["twitter"]["analyses_with_hits"] == 0
    assert contrib["newsapi"]["total_hits"] == 3
    assert contrib["newsapi"]["analyses_with_hits"] == 1


def test_store_persists_explicit_breakdown(tmp_path, monkeypatch):
    """Setzt ein Analyzer sources_used als int (z.B. multi_agent), muss der vom
    Runner durchgereichte Breakdown trotzdem in der Spalte landen – sonst bleibt
    sources_breakdown NULL und die Collector-Auswertung läuft leer."""
    log = make_log(tmp_path, monkeypatch)
    a = AnalysisResult(
        ticker="AAPL", sentiment_score=0.6, direction="BULLISH",
        confidence="HIGH", recommendation="BUY", sources_used=42,
    )
    log.store(a, sources_breakdown={"yahoo": 4, "newsapi": 8, "reddit": 0})
    row = log._conn.execute(
        "SELECT sources_used, sources_breakdown FROM analyses"
    ).fetchone()
    assert row[0] == 42
    assert json.loads(row[1]) == {"yahoo": 4, "newsapi": 8, "reddit": 0}
    contrib = {c["source"]: c for c in log.get_source_contributions(days=1)}
    assert contrib["newsapi"]["total_hits"] == 8
    assert contrib["reddit"]["total_hits"] == 0


def test_ignores_rows_without_breakdown(tmp_path, monkeypatch):
    """Alt-Zeilen ohne Breakdown (NULL) verfälschen die Auswertung nicht."""
    log = make_log(tmp_path, monkeypatch)
    log._conn.execute(
        "INSERT INTO analyses (analyzed_at, ticker, recommendation, direction, "
        "sentiment_score, confidence, sources_used) VALUES (?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "MSFT", "SKIP", "NEUTRAL", 0.3, "LOW", 5),
    )
    log._conn.commit()
    _insert(log, {"newsapi": 2})
    contrib = log.get_source_contributions(days=30)
    # Nur die eine Zeile mit Breakdown zählt
    assert {c["source"] for c in contrib} == {"newsapi"}
