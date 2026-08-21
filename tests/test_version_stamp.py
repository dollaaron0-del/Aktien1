"""Tests für den Versions-Stempel (Roadmap 1.6): analyzers/version_stamp.py
plus die automatische Befüllung in decision_log/analysis_log.

Ohne Git-Hash + Config-Schnappschuss messen die Evidenz-Gates
(scripts/track_record.py) ein bewegliches Ziel — diese Tests sichern,
dass jeder neue Log-Eintrag den Code-/Config-Stand trägt und dass die
Migration bestehende DBs idempotent nachrüstet.
"""
import json
import sqlite3

import pytest

import analyzers.version_stamp as vs_mod
from analyzers.version_stamp import stamp
from analyzers.decision_log import DecisionLog


@pytest.fixture(autouse=True)
def fresh_stamp_cache():
    """Prozess-Cache pro Test zurücksetzen (Singleton-Semantik testbar halten)."""
    vs_mod._cached = None
    yield
    vs_mod._cached = None


# ── Stempel selbst ────────────────────────────────────────────────────────────

def test_stamp_returns_hash_and_config():
    git_hash, config_json = stamp()
    # Repo liegt unter Git → Hash muss da sein (7+ Hex-Zeichen, short hash)
    assert git_hash and len(git_hash) >= 7
    int(git_hash, 16)  # gültiger Hex-String
    snap = json.loads(config_json)
    assert "buy_threshold" in snap
    assert "broker_mode" in snap


def test_stamp_is_cached_per_process():
    first = stamp()
    assert stamp() is first  # identisches Tupel-Objekt → kein Neu-Rechnen


def test_config_snapshot_contains_no_secrets():
    _, config_json = stamp()
    lowered = config_json.lower()
    # Whitelist-Ansatz: nichts Key-/Token-artiges darf hineinrutschen
    for verboten in ("api_key", "token", "secret", "password", "sk-ant"):
        assert verboten not in lowered


def test_stamp_fail_open(monkeypatch):
    """Kaputte Umgebung (kein git, kaputte Config) → (None, None), keine Exception."""
    monkeypatch.setattr(vs_mod, "_read_git_hash", lambda: None)
    monkeypatch.setattr(vs_mod, "_read_config_snapshot", lambda: None)
    assert stamp() == (None, None)


# ── DecisionLog ───────────────────────────────────────────────────────────────

@pytest.fixture
def dlog(tmp_path):
    d = DecisionLog(db_path=str(tmp_path / "decision_log.db"))
    yield d
    d.close()


def test_decision_log_auto_stamps(dlog):
    dlog.log({"ticker": "NVDA", "action": "SKIP", "reason": "test"})
    row = dlog.get_recent(limit=1)[0]
    assert row["git_hash"] and len(row["git_hash"]) >= 7
    assert json.loads(row["config_json"])["buy_threshold"] is not None


def test_decision_log_explicit_stamp_wins(dlog):
    dlog.log({"ticker": "X", "action": "SKIP", "reason": "t",
              "git_hash": "abc1234", "config_json": "{}"})
    row = dlog.get_recent(limit=1)[0]
    assert row["git_hash"] == "abc1234"
    assert row["config_json"] == "{}"


def test_decision_log_migration_adds_columns(tmp_path):
    """Alte DB ohne Stempel-Spalten → Öffnen rüstet idempotent nach."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "decided_at TEXT NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL, "
        "reason TEXT, executed TEXT, source TEXT, recommendation TEXT, "
        "direction TEXT, sentiment_score REAL, confidence TEXT, "
        "sources_used INTEGER, regime TEXT, macro_bias REAL)"
    )
    conn.execute(
        "INSERT INTO decisions (decided_at, ticker, action) "
        "VALUES ('2026-07-01T10:00:00', 'AAPL', 'SKIP')"
    )
    conn.commit()
    conn.close()

    d = DecisionLog(db_path=db)
    try:
        cols = {r["name"] for r in d._conn.execute("PRAGMA table_info(decisions)")}
        assert {"git_hash", "config_json", "cost_eur"} <= cols
        # Alt-Zeile bleibt lesbar (Stempel dort NULL — rückwirkend nie)
        old = d.get_recent(limit=5)[0]
        assert old["ticker"] == "AAPL" and old["git_hash"] is None
        # Neue Zeile wird gestempelt
        d.log({"ticker": "MSFT", "action": "SKIP", "reason": "t"})
        assert d.get_recent(limit=1)[0]["git_hash"]
    finally:
        d.close()


# ── AnalysisLog ───────────────────────────────────────────────────────────────

def test_analysis_log_auto_stamps(tmp_path, monkeypatch):
    import analyzers.analysis_log as al_mod
    from analyzers.analysis_log import AnalysisLog
    from analyzers.claude_analyzer import AnalysisResult

    monkeypatch.setattr(al_mod, "DB_PATH", str(tmp_path / "analysis_log.db"))
    log = AnalysisLog()
    log.store(AnalysisResult(
        ticker="NVDA", recommendation="BUY", direction="BULLISH",
        sentiment_score=0.8, confidence="HIGH",
    ))
    row = log._conn.execute(
        "SELECT git_hash, config_json FROM analyses ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["git_hash"] and len(row["git_hash"]) >= 7
    assert "buy_threshold" in json.loads(row["config_json"])


def test_analysis_log_migration_adds_columns(tmp_path, monkeypatch):
    """Alte analyses-DB (nur Ur-Schema) → Öffnen rüstet alle drei Spalten nach."""
    import analyzers.analysis_log as al_mod
    from analyzers.analysis_log import AnalysisLog

    db = str(tmp_path / "old_analysis.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE analyses (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "analyzed_at TEXT NOT NULL, ticker TEXT NOT NULL, "
        "recommendation TEXT NOT NULL, direction TEXT NOT NULL, "
        "sentiment_score REAL NOT NULL, confidence TEXT NOT NULL, "
        "entry_rationale TEXT, bull_case TEXT, bear_case TEXT, "
        "debate_winner TEXT, key_catalysts TEXT, risk_factors TEXT, "
        "target_price REAL, suggested_hold INTEGER, sources_used INTEGER, "
        "exchange TEXT DEFAULT '')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(al_mod, "DB_PATH", db)
    log = AnalysisLog()
    cols = {r["name"] for r in log._conn.execute("PRAGMA table_info(analyses)")}
    assert {"sources_breakdown", "git_hash", "config_json"} <= cols
