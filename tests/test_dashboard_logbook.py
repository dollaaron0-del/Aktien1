"""
Tests für dashboard/logbook.py (Ausbau-Roadmap H7.3 — Schichtbuch).
"""
import json

from dashboard.logbook import (
    _rule_based_text,
    all_entries,
    read_entry,
    write_entry,
)


def _seed_feed(db_path, rows):
    import system.live_status as ls_mod
    feed = ls_mod.ActivityFeed(db_path=db_path)
    for ts, event, ticker, detail in rows:
        feed._conn.execute(
            "INSERT INTO events (ts, event, ticker, detail) VALUES (?,?,?,?)",
            (ts, event, ticker, detail),
        )
    feed._conn.commit()


# ── _rule_based_text() ────────────────────────────────────────────────────────

def test_rule_based_text_empty_day():
    assert _rule_based_text("2026-07-15", []) == "2026-07-15: Keine Aktivität aufgezeichnet."


def test_rule_based_text_counts_analyses_and_trades():
    events = [
        {"event": "analysis_done"}, {"event": "analysis_done"},
        {"event": "trade", "ticker": "AAPL"},
    ]
    text = _rule_based_text("2026-07-15", events)
    assert "2 Analysen" in text
    assert "1 Trade(s)" in text
    assert "Bewegt: AAPL" in text


def test_rule_based_text_quiet_day_without_trades():
    text = _rule_based_text("2026-07-15", [{"event": "analysis_done"}])
    assert "Ruhiger Tag ohne Trades" in text


def test_rule_based_text_mentions_gate_blocks():
    events = [{"event": "gate_blocked"}, {"event": "gate_blocked"}]
    text = _rule_based_text("2026-07-15", events)
    assert "2× durch ein Gate blockiert" in text


# ── write_entry() / read_entry() / all_entries() ─────────────────────────────

def test_write_entry_persists_and_read_entry_roundtrip(tmp_path, monkeypatch):
    feed_db = str(tmp_path / "feed.db")
    monkeypatch.setattr("system.live_status.FEED_PATH", feed_db)
    _seed_feed(feed_db, [("2026-07-15T09:00:00", "trade", "AAPL", "GEKAUFT 3 @ $100")])

    logbook_path = str(tmp_path / "logbook.jsonl")
    text = write_entry("2026-07-15", path=logbook_path, use_ollama=False)
    assert "1 Trade(s)" in text

    entry = read_entry("2026-07-15", path=logbook_path)
    assert entry["text"] == text
    assert entry["rule_text"] == text  # ohne Ollama identisch


def test_write_entry_is_idempotent_per_day_not_duplicated(tmp_path, monkeypatch):
    feed_db = str(tmp_path / "feed.db")
    monkeypatch.setattr("system.live_status.FEED_PATH", feed_db)
    logbook_path = str(tmp_path / "logbook.jsonl")

    write_entry("2026-07-15", path=logbook_path, use_ollama=False)
    write_entry("2026-07-15", path=logbook_path, use_ollama=False)

    with open(logbook_path, encoding="utf-8") as fh:
        lines = [l for l in fh if l.strip()]
    assert len(lines) == 1


def test_all_entries_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr("system.live_status.FEED_PATH", str(tmp_path / "feed.db"))
    logbook_path = str(tmp_path / "logbook.jsonl")
    write_entry("2026-07-10", path=logbook_path, use_ollama=False)
    write_entry("2026-07-15", path=logbook_path, use_ollama=False)
    write_entry("2026-07-12", path=logbook_path, use_ollama=False)

    days = [e["day"] for e in all_entries(path=logbook_path)]
    assert days == ["2026-07-15", "2026-07-12", "2026-07-10"]


def test_read_entry_none_for_missing_file(tmp_path):
    assert read_entry("2026-07-15", path=str(tmp_path / "nope.jsonl")) is None


def test_write_entry_fail_open_on_corrupt_feed_db(tmp_path):
    bad_db = tmp_path / "corrupt.db"
    bad_db.write_bytes(b"not a real sqlite file")
    logbook_path = str(tmp_path / "logbook.jsonl")
    text = write_entry("2026-07-15", path=logbook_path, feed_db_path=str(bad_db),
                       use_ollama=False)
    assert text == "2026-07-15: Keine Aktivität aufgezeichnet."


# ── Ollama-Zweig (gemockt) ────────────────────────────────────────────────────

def test_write_entry_uses_ollama_prose_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr("system.live_status.FEED_PATH", str(tmp_path / "feed.db"))
    monkeypatch.setattr(
        "dashboard.logbook._ollama_prose",
        lambda rule_text, model=None: "Ruhige Schicht, alles im grünen Bereich.",
    )
    logbook_path = str(tmp_path / "logbook.jsonl")
    text = write_entry("2026-07-15", path=logbook_path, use_ollama=True)
    assert text == "Ruhige Schicht, alles im grünen Bereich."

    entry = read_entry("2026-07-15", path=logbook_path)
    assert entry["text"] == "Ruhige Schicht, alles im grünen Bereich."
    assert entry["rule_text"] == "2026-07-15: Keine Aktivität aufgezeichnet."


def test_write_entry_keeps_rule_text_when_ollama_down(tmp_path, monkeypatch):
    monkeypatch.setattr("system.live_status.FEED_PATH", str(tmp_path / "feed.db"))
    monkeypatch.setattr(
        "dashboard.logbook._ollama_prose", lambda rule_text, model=None: None,
    )
    logbook_path = str(tmp_path / "logbook.jsonl")
    text = write_entry("2026-07-15", path=logbook_path, use_ollama=True)
    assert text == "2026-07-15: Keine Aktivität aufgezeichnet."


def test_ollama_prose_fail_open_on_request_exception(monkeypatch):
    from dashboard.logbook import _ollama_prose

    class _BoomRequests:
        @staticmethod
        def post(*a, **k):
            raise RuntimeError("kaputt")

    monkeypatch.setattr("dashboard.logbook.requests", _BoomRequests, raising=False)
    import sys
    monkeypatch.setitem(sys.modules, "requests", _BoomRequests)
    assert _ollama_prose("Testtext", model="llama3.1:8b") is None


def test_ollama_prose_none_on_non_200_status(monkeypatch):
    from dashboard.logbook import _ollama_prose

    class _Resp:
        status_code = 500

    class _FakeRequests:
        @staticmethod
        def post(*a, **k):
            return _Resp()

    import sys
    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    assert _ollama_prose("Testtext", model="llama3.1:8b") is None
