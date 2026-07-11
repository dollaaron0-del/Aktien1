"""Tests für system/live_status.py (Roadmap 1.5a+b): Status-Zeile
(bot_status.json) und Aktivitätsfeed (activity_feed.db). Kernzusagen:
fail-open (wirft nie), atomare Status-Writes, Zyklus-Startzeit bleibt
über Phasenwechsel erhalten, Feed-Pruning."""
import json
import os

import pytest

import system.live_status as ls


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Nie in die echte data/ schreiben; Feed-Singleton pro Test frisch."""
    monkeypatch.setattr(ls, "STATUS_PATH", str(tmp_path / "bot_status.json"))
    monkeypatch.setattr(ls, "FEED_PATH", str(tmp_path / "activity_feed.db"))
    monkeypatch.setattr(ls, "_feed", None)
    yield
    if ls._feed is not None:
        ls._feed.close()
        ls._feed = None


# ── Status-Zeile ─────────────────────────────────────────────────────────────

def test_set_phase_and_read_roundtrip():
    ls.set_phase("Analyse", ticker="NVDA", idx=12, total=45)
    s = ls.read_status()
    assert s["state"] == "cycle"
    assert s["phase"] == "Analyse"
    assert s["ticker"] == "NVDA" and s["idx"] == 12 and s["total"] == 45
    assert s["cycle_started_at"] and s["updated_at"] and s["pid"] == os.getpid()


def test_cycle_started_at_survives_phase_changes():
    ls.set_phase("Start")
    started = ls.read_status()["cycle_started_at"]
    ls.set_phase("Analyse", ticker="MSFT", idx=1, total=10)
    assert ls.read_status()["cycle_started_at"] == started


def test_set_idle_resets_cycle():
    ls.set_phase("Analyse", ticker="NVDA")
    ls.set_idle(next_run="2026-07-12T07:30:00", note="Zyklus beendet")
    s = ls.read_status()
    assert s["state"] == "idle"
    assert s["cycle_started_at"] is None and s["ticker"] is None
    assert s["next_run"] == "2026-07-12T07:30:00"
    # Neuer Zyklus nach Idle beginnt mit frischer Startzeit
    ls.set_phase("Start")
    assert ls.read_status()["cycle_started_at"] is not None


def test_read_status_missing_file_returns_none():
    assert ls.read_status() is None
    assert ls.status_age_seconds() is None


def test_status_age_seconds():
    ls.set_phase("Start")
    age = ls.status_age_seconds()
    assert age is not None and 0 <= age < 10


def test_status_write_never_raises(monkeypatch):
    # Unschreibbarer Pfad → Funktionen schlucken den Fehler (fail-open)
    monkeypatch.setattr(ls, "STATUS_PATH", "/proc/unmöglich/bot_status.json")
    ls.set_phase("Start")     # darf nicht werfen
    ls.set_idle()             # darf nicht werfen


# ── Aktivitätsfeed ───────────────────────────────────────────────────────────

def test_feed_emit_and_recent():
    ls.feed_emit("cycle_start", detail="2026-07-11 07:30")
    ls.feed_emit("analysis_done", ticker="NVDA", detail="BUY · Score 0.81 · HIGH")
    ls.feed_emit("trade", ticker="NVDA", detail="GEKAUFT 3 NVDA @ $120")
    rows = ls.feed_recent(limit=10)
    assert [r["event"] for r in rows] == ["trade", "analysis_done", "cycle_start"]
    assert rows[0]["ticker"] == "NVDA"
    assert rows[2]["detail"] == "2026-07-11 07:30"


def test_feed_recent_empty_is_list():
    assert ls.feed_recent() == []


def test_feed_pruning(monkeypatch):
    monkeypatch.setattr(ls, "_FEED_KEEP", 50)
    for i in range(200):
        ls.feed_emit("analysis_done", ticker=f"T{i}")
    rows = ls.feed_recent(limit=500)
    # Pruning greift alle 100 Inserts → deutlich unter 200, mindestens KEEP
    assert 50 <= len(rows) <= 150
    assert rows[0]["ticker"] == "T199"  # neueste bleiben erhalten


def test_feed_never_raises(monkeypatch):
    monkeypatch.setattr(ls, "FEED_PATH", "/proc/unmöglich/feed.db")
    monkeypatch.setattr(ls, "_feed", None)
    ls.feed_emit("cycle_start")          # darf nicht werfen
    assert ls.feed_recent() == []
