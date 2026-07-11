"""Tests für die Provenienz-Verkettung decision_log ↔ analysis_log
(Roadmap 1.4b): AnalysisLog.store() liefert die Zeilen-ID, get_by_id()
findet die Analyse wieder, DecisionLog trägt analysis_id (inkl.
idempotenter Migration bestehender DBs)."""
import json
import sqlite3

import pytest

import analyzers.analysis_log as al_mod
from analyzers.analysis_log import AnalysisLog
from analyzers.claude_analyzer import AnalysisResult
from analyzers.decision_log import DecisionLog


def make_alog(tmp_path, monkeypatch):
    monkeypatch.setattr(al_mod, "DB_PATH", str(tmp_path / "analysis_log.db"))
    return AnalysisLog()


def _result(ticker="NVDA", **kw):
    return AnalysisResult(
        ticker=ticker, recommendation="BUY", direction="BULLISH",
        sentiment_score=0.8, confidence="HIGH", **kw,
    )


# ── AnalysisLog: ID-Rückgabe + Wiederfinden ──────────────────────────────────

def test_store_returns_row_id(tmp_path, monkeypatch):
    log = make_alog(tmp_path, monkeypatch)
    first = log.store(_result())
    second = log.store(_result(ticker="MSFT"))
    assert isinstance(first, int) and isinstance(second, int)
    assert second == first + 1


def test_store_non_result_returns_none(tmp_path, monkeypatch):
    log = make_alog(tmp_path, monkeypatch)
    assert log.store({"kein": "AnalysisResult"}) is None


def test_get_by_id_roundtrip(tmp_path, monkeypatch):
    log = make_alog(tmp_path, monkeypatch)
    rid = log.store(
        _result(entry_rationale="Starke Earnings",
                key_catalysts=["Earnings-Beat"], risk_factors=["Bewertung"]),
        sources_breakdown={"yahoo": 5, "reddit": 2},
    )
    row = log.get_by_id(rid)
    assert row["ticker"] == "NVDA"
    assert row["entry_rationale"] == "Starke Earnings"
    assert row["key_catalysts"] == ["Earnings-Beat"]   # JSON geparst
    assert json.loads(row["sources_breakdown"]) == {"yahoo": 5, "reddit": 2}


def test_get_by_id_missing(tmp_path, monkeypatch):
    log = make_alog(tmp_path, monkeypatch)
    assert log.get_by_id(99999) is None


# ── DecisionLog: analysis_id ─────────────────────────────────────────────────

@pytest.fixture
def dlog(tmp_path):
    d = DecisionLog(db_path=str(tmp_path / "decision_log.db"))
    yield d
    d.close()


def test_decision_carries_analysis_id(dlog):
    dlog.log({"ticker": "NVDA", "action": "BUY", "reason": "t",
              "analysis_id": 42})
    assert dlog.get_recent(limit=1)[0]["analysis_id"] == 42


def test_decision_without_analysis_id_is_null(dlog):
    # z.B. Queue-Drains oder "Kein Kurs"-Skips haben keine frische Analyse
    dlog.log({"ticker": "X", "action": "SKIP", "reason": "Kein Kurs"})
    assert dlog.get_recent(limit=1)[0]["analysis_id"] is None


def test_decision_migration_adds_analysis_id(tmp_path):
    """Bestehende DB ohne analysis_id → Öffnen rüstet nach (idempotent)."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "decided_at TEXT NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL, "
        "reason TEXT, executed TEXT, source TEXT, recommendation TEXT, "
        "direction TEXT, sentiment_score REAL, confidence TEXT, "
        "sources_used INTEGER, regime TEXT, macro_bias REAL, cost_eur REAL)"
    )
    conn.commit()
    conn.close()

    d = DecisionLog(db_path=db)
    try:
        cols = {r["name"] for r in d._conn.execute("PRAGMA table_info(decisions)")}
        assert "analysis_id" in cols
        d.log({"ticker": "MSFT", "action": "BUY", "reason": "t", "analysis_id": 7})
        assert d.get_recent(limit=1)[0]["analysis_id"] == 7
    finally:
        d.close()


# ── Ende-zu-Ende: Analyse speichern → Entscheidung verketten → auflösen ──────

def test_link_end_to_end(tmp_path, monkeypatch):
    alog = make_alog(tmp_path, monkeypatch)
    dlog = DecisionLog(db_path=str(tmp_path / "decision_log.db"))
    try:
        rid = alog.store(_result(), sources_breakdown={"yahoo": 3})
        dlog.log({"ticker": "NVDA", "action": "BUY", "reason": "Alle Kriterien",
                  "analysis_id": rid})
        decision = dlog.get_recent(limit=1)[0]
        linked = alog.get_by_id(decision["analysis_id"])
        assert linked["ticker"] == decision["ticker"] == "NVDA"
        assert json.loads(linked["sources_breakdown"]) == {"yahoo": 3}
    finally:
        dlog.close()
