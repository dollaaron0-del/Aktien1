"""
Tests für dashboard/achievements.py (Ausbau-Roadmap H7.2 — Plaketten-Wand).
"""
import json

from dashboard.achievements import (
    CATALOG,
    _check_first_live_trade,
    _check_first_proven_thesis,
    _check_hundred_labeled_trades,
    _check_no_breaker_trigger_30d,
    _check_one_year_operation,
    unlocked,
)


def _seed_experience(monkeypatch, tmp_path, stats):
    import analyzers.experience_store as es_mod
    db_path = str(tmp_path / "experience_test.db")

    class _FakeStore:
        def __init__(self):
            pass

        def stats(self):
            return stats

        def close(self):
            pass

    monkeypatch.setattr(es_mod, "ExperienceStore", _FakeStore)


# ── Einzelne Prüf-Funktionen ──────────────────────────────────────────────────

def test_check_first_live_trade_true_and_false(monkeypatch, tmp_path):
    _seed_experience(monkeypatch, tmp_path, {"live": 3})
    assert _check_first_live_trade() is True
    _seed_experience(monkeypatch, tmp_path, {"live": 0})
    assert _check_first_live_trade() is False


def test_check_hundred_labeled_trades_threshold(monkeypatch, tmp_path):
    _seed_experience(monkeypatch, tmp_path, {"labeled": 100})
    assert _check_hundred_labeled_trades() is True
    _seed_experience(monkeypatch, tmp_path, {"labeled": 99})
    assert _check_hundred_labeled_trades() is False


def test_check_first_proven_thesis(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry.json"))
    from analyzers.thesis_verdict import ABANDONED, PROVEN, load_registry, register_thesis, save_registry
    assert _check_first_proven_thesis() is False

    register_thesis("t1", started_at="2026-01-01")
    registry = load_registry()
    registry["t1"].status = PROVEN
    save_registry(registry)
    assert _check_first_proven_thesis() is True


def test_check_first_proven_thesis_false_when_only_abandoned(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry.json"))
    from analyzers.thesis_verdict import ABANDONED, load_registry, register_thesis, save_registry
    register_thesis("t1", started_at="2026-01-01")
    registry = load_registry()
    registry["t1"].status = ABANDONED
    save_registry(registry)
    assert _check_first_proven_thesis() is False


def test_check_one_year_operation(tmp_path):
    import sqlite3
    db_path = str(tmp_path / "analysis_log_test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE analyses (analyzed_at TEXT, ticker TEXT, recommendation TEXT, "
        "direction TEXT, sentiment_score REAL, confidence TEXT)"
    )
    conn.execute(
        "INSERT INTO analyses VALUES (?,?,?,?,?,?)",
        ("2025-01-01T09:00:00", "AAPL", "BUY", "BULLISH", 0.5, "HIGH"),
    )
    conn.commit()
    conn.close()
    assert _check_one_year_operation(analysis_db_path=db_path) is True


def test_check_one_year_operation_false_when_too_recent(tmp_path):
    import sqlite3
    db_path = str(tmp_path / "analysis_log_test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE analyses (analyzed_at TEXT, ticker TEXT, recommendation TEXT, "
        "direction TEXT, sentiment_score REAL, confidence TEXT)"
    )
    from datetime import date, timedelta
    recent = (date.today() - timedelta(days=10)).isoformat() + "T09:00:00"
    conn.execute(
        "INSERT INTO analyses VALUES (?,?,?,?,?,?)",
        (recent, "AAPL", "BUY", "BULLISH", 0.5, "HIGH"),
    )
    conn.commit()
    conn.close()
    assert _check_one_year_operation(analysis_db_path=db_path) is False


def test_check_one_year_operation_false_without_db(tmp_path):
    assert _check_one_year_operation(analysis_db_path=str(tmp_path / "nope.db")) is False


def test_check_no_breaker_trigger_30d_needs_full_coverage(tmp_path):
    from datetime import date, timedelta
    history_path = tmp_path / "history.jsonl"
    # nur 5 Tage Historie -> nicht genug fuer "30 Tage"
    lines = []
    for i in range(5):
        day = (date.today() - timedelta(days=i)).isoformat()
        lines.append(json.dumps({
            "ts": f"{day}T10:00:00", "paused": False,
            "machines": {"breaker": {"status": "ok", "tooltip": []}},
        }))
    history_path.write_text("\n".join(lines) + "\n")
    assert _check_no_breaker_trigger_30d(str(history_path)) is False


def test_check_no_breaker_trigger_30d_true_with_full_clean_history(tmp_path):
    from datetime import date, timedelta
    history_path = tmp_path / "history.jsonl"
    lines = []
    for i in range(31):
        day = (date.today() - timedelta(days=i)).isoformat()
        lines.append(json.dumps({
            "ts": f"{day}T10:00:00", "paused": False,
            "machines": {"breaker": {"status": "ok", "tooltip": []}},
        }))
    history_path.write_text("\n".join(lines) + "\n")
    assert _check_no_breaker_trigger_30d(str(history_path)) is True


def test_check_no_breaker_trigger_30d_false_if_triggered_recently(tmp_path):
    from datetime import date, timedelta
    history_path = tmp_path / "history.jsonl"
    lines = []
    for i in range(31):
        day = (date.today() - timedelta(days=i)).isoformat()
        status = "err" if i == 3 else "ok"
        lines.append(json.dumps({
            "ts": f"{day}T10:00:00", "paused": False,
            "machines": {"breaker": {"status": status, "tooltip": []}},
        }))
    history_path.write_text("\n".join(lines) + "\n")
    assert _check_no_breaker_trigger_30d(str(history_path)) is False


def test_check_no_breaker_trigger_30d_false_without_history_file(tmp_path):
    assert _check_no_breaker_trigger_30d(str(tmp_path / "nope.jsonl")) is False


# ── unlocked() Merk-Logik ─────────────────────────────────────────────────────

def test_unlocked_returns_all_catalog_entries_locked_when_no_data(tmp_path, monkeypatch):
    """Alle fünf Prüf-Funktionen einzeln auf garantiert leere/fehlende
    Quellen isoliert — sonst würde dieser Test gegen die ECHTEN
    Produktionsdaten laufen (real bereits >100 gelabelte Trades, ein
    echter Live-Trade) und fälschlich "erreicht" melden."""
    _seed_experience(monkeypatch, tmp_path, {"live": 0, "labeled": 0})
    monkeypatch.setenv("THESIS_REGISTRY_PATH", str(tmp_path / "thesis_registry.json"))
    monkeypatch.setattr(
        "dashboard.achievements._check_no_breaker_trigger_30d",
        lambda history_path=None: False,
    )
    monkeypatch.setattr(
        "dashboard.achievements._check_one_year_operation",
        lambda analysis_db_path=None: False,
    )
    # CATALOG-Einträge referenzieren die Original-Funktionsobjekte, nicht
    # die Modul-Attribute -> für die beiden monkeypatch-übergangenen
    # Checks direkt im Katalog überschreiben:
    for item in CATALOG:
        if item["id"] == "thirty_days_no_breaker":
            monkeypatch.setitem(item, "check", lambda: False)
        if item["id"] == "one_year_operation":
            monkeypatch.setitem(item, "check", lambda: False)

    path = str(tmp_path / "achievements.json")
    rows = unlocked(path=path)
    assert len(rows) == len(CATALOG)
    assert all(not r["unlocked"] for r in rows)
    assert all(r["unlocked_at"] is None for r in rows)


def test_unlocked_persists_newly_achieved_milestone(tmp_path, monkeypatch):
    _seed_experience(monkeypatch, tmp_path, {"live": 1, "labeled": 0})
    path = str(tmp_path / "achievements.json")
    rows = unlocked(path=path)
    live_row = next(r for r in rows if r["id"] == "first_live_trade")
    assert live_row["unlocked"] is True
    assert live_row["unlocked_at"] is not None

    with open(path, encoding="utf-8") as fh:
        stored = json.load(fh)
    assert "first_live_trade" in stored


def test_unlocked_keeps_milestone_once_condition_flips_back(tmp_path, monkeypatch):
    """Der Sinn einer Plakette: einmal erreicht, bleibt erreicht — auch
    wenn die Bedingung später wieder nicht mehr zutrifft."""
    _seed_experience(monkeypatch, tmp_path, {"live": 1})
    path = str(tmp_path / "achievements.json")
    rows_first = unlocked(path=path)
    assert next(r for r in rows_first if r["id"] == "first_live_trade")["unlocked"] is True

    _seed_experience(monkeypatch, tmp_path, {"live": 0})  # Bedingung kippt zurück
    rows_second = unlocked(path=path)
    live_row = next(r for r in rows_second if r["id"] == "first_live_trade")
    assert live_row["unlocked"] is True  # bleibt trotzdem erreicht


def test_unlocked_fail_open_on_corrupt_storage_file(tmp_path):
    path = tmp_path / "achievements.json"
    path.write_text("{not valid json")
    rows = unlocked(path=str(path))
    assert len(rows) == len(CATALOG)  # kein Crash, alle als "nicht erreicht" behandelt
