"""
Tests für dashboard/memories.py (Roadmap L2.1 — „Heute vor …").

Netzfrei: alle vier Quellen gegen Temp-Dateien / Fake-Portfolio /
injizierten Store. Kernpunkt: nur ECHTE Treffer, exakte Wochen-Regel.
"""
import json
from datetime import date

import pytest

from dashboard import memories

_DAY = date(2026, 7, 16)  # Donnerstag


@pytest.fixture()
def quiet_sources(monkeypatch, tmp_path):
    """Alle Quellen stumm — für die Tests der TREFFER-REGEL, die ihr
    Ereignis selbst setzen. Bewusst NICHT autouse: die Quellen-Tests
    unten müssen die echten Funktionen aufrufen (eine autouse-Fixture
    hätte sie durch Stubs ersetzt, und ein `monkeypatch.undo()`
    dagegen würde auch die Portfolio-Isolation von `fresh_portfolio`
    aufheben — dann liefe der Test gegen die ECHTE data/portfolio.db)."""
    monkeypatch.setattr(memories, "_first_trade", lambda: [])
    monkeypatch.setattr(memories, "_pnl_extremes", lambda store=None: [])
    monkeypatch.setattr(memories, "_ACHIEVEMENTS_FILE", str(tmp_path / "no_ach.json"))
    monkeypatch.setattr(memories, "_THESIS_FILE", str(tmp_path / "no_th.json"))


# ── Treffer-Regel ────────────────────────────────────────────────────────────

def test_exact_week_multiple_hits(quiet_sources, monkeypatch):
    monkeypatch.setattr(memories, "_first_trade",
                        lambda: [{"date": date(2026, 7, 2), "text": "Ereignis"}])
    rows = memories.memories_for(_DAY)          # exakt 14 Tage = 2 Wochen
    assert len(rows) == 1
    assert rows[0]["when"] == "vor 2 Wochen"
    assert rows[0]["date"] == "2026-07-02"


def test_non_week_multiple_is_ignored(quiet_sources, monkeypatch):
    monkeypatch.setattr(memories, "_first_trade",
                        lambda: [{"date": date(2026, 7, 3), "text": "Ereignis"}])
    assert memories.memories_for(_DAY) == []    # 13 Tage — kein Treffer


def test_today_and_future_are_ignored(quiet_sources, monkeypatch):
    monkeypatch.setattr(memories, "_first_trade", lambda: [
        {"date": _DAY, "text": "heute"},
        {"date": date(2026, 7, 23), "text": "zukunft"},
    ])
    assert memories.memories_for(_DAY) == []


def test_singular_week_phrasing(quiet_sources, monkeypatch):
    monkeypatch.setattr(memories, "_first_trade",
                        lambda: [{"date": date(2026, 7, 9), "text": "X"}])
    assert memories.memories_for(_DAY)[0]["when"] == "vor einer Woche"


def test_year_phrasing_for_52_weeks(quiet_sources, monkeypatch):
    monkeypatch.setattr(memories, "_first_trade",
                        lambda: [{"date": date(2025, 7, 17), "text": "X"}])
    assert memories.memories_for(_DAY)[0]["when"] == "vor einem Jahr"


def test_capped_at_three_oldest_first(quiet_sources, monkeypatch):
    monkeypatch.setattr(memories, "_first_trade", lambda: [
        {"date": date(2026, 7, 9), "text": "neu"},
        {"date": date(2026, 6, 25), "text": "mittel"},
        {"date": date(2026, 6, 11), "text": "alt"},
        {"date": date(2026, 5, 28), "text": "sehr alt"},
    ])
    rows = memories.memories_for(_DAY)
    assert len(rows) == 3
    assert [r["text"] for r in rows] == ["sehr alt", "alt", "mittel"]


# ── Quellen ──────────────────────────────────────────────────────────────────

def test_first_trade_from_real_table(fresh_portfolio):
    fresh_portfolio._conn.execute(
        "INSERT INTO trades (ticker, action, shares, price, timestamp, pnl) "
        "VALUES (?,?,?,?,?,?)", ("ZFIRST", "BUY", 1.0, 100.0, "2026-07-02T09:00:00", 0.0),
    )
    fresh_portfolio._conn.execute(
        "INSERT INTO trades (ticker, action, shares, price, timestamp, pnl) "
        "VALUES (?,?,?,?,?,?)", ("ZLATER", "BUY", 1.0, 100.0, "2026-07-05T09:00:00", 0.0),
    )
    fresh_portfolio._conn.commit()
    rows = memories._first_trade()
    assert len(rows) == 1
    assert "ZFIRST" in rows[0]["text"]          # der ÄLTESTE, nicht der neueste
    assert rows[0]["date"] == date(2026, 7, 2)


def test_first_trade_fail_open(monkeypatch):
    import portfolio.portfolio as port_mod
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", "/nicht/da.db")
    assert memories._first_trade() == []


def test_pnl_extremes_best_and_worst(tmp_path):
    import analyzers.experience_store as es_mod
    store = es_mod.ExperienceStore(db_path=str(tmp_path / "exp.db"))
    for ticker, day, pnl in (("ZWIN", "2026-07-02", 12.5),
                             ("ZLOSS", "2026-07-09", -8.0),
                             ("ZMID", "2026-07-05", 1.0)):
        did = store.upsert_decision({
            "decided_at": f"{day}T09:00:00", "ticker": ticker,
            "recommendation": "BUY", "direction": "BULLISH",
            "sentiment_score": 0.7, "confidence": "HIGH",
        })
        store.attach_outcome(did, {"pnl_pct": pnl, "outcome": "WIN" if pnl > 0 else "LOSS",
                                   "label_source": "backfill"})
    rows = memories._pnl_extremes(store=store)
    texts = " ".join(r["text"] for r in rows)
    assert "ZWIN" in texts and "+12.5" in texts
    assert "ZLOSS" in texts and "-8.0" in texts
    assert "ZMID" not in texts


def test_pnl_extremes_single_row_not_duplicated(tmp_path):
    """Bei nur EINER gelabelten Zeile ist bester == schlechtester Trade —
    das darf nicht doppelt erscheinen."""
    import analyzers.experience_store as es_mod
    store = es_mod.ExperienceStore(db_path=str(tmp_path / "exp1.db"))
    did = store.upsert_decision({
        "decided_at": "2026-07-02T09:00:00", "ticker": "ZONLY",
        "recommendation": "BUY", "direction": "BULLISH",
        "sentiment_score": 0.7, "confidence": "HIGH",
    })
    store.attach_outcome(did, {"pnl_pct": 5.0, "outcome": "WIN",
                               "label_source": "backfill"})
    assert len(memories._pnl_extremes(store=store)) == 1


def test_pnl_extremes_fail_open(monkeypatch):
    import analyzers.experience_store as es_mod

    class _Boom:
        pass

    monkeypatch.setattr(es_mod, "ExperienceStore", _Boom)
    assert memories._pnl_extremes() == []


def test_achievements_source(tmp_path, monkeypatch):
    f = tmp_path / "ach.json"
    f.write_text(json.dumps({"first_live_trade": {"unlocked_at": "2026-07-02"}}))
    monkeypatch.setattr(memories, "_ACHIEVEMENTS_FILE", str(f))
    rows = memories._achievements()
    assert rows[0]["date"] == date(2026, 7, 2)
    assert "Plakette" in rows[0]["text"]


def test_achievements_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(memories, "_ACHIEVEMENTS_FILE", str(tmp_path / "weg.json"))
    assert memories._achievements() == []


def test_theses_source(tmp_path, monkeypatch):
    f = tmp_path / "th.json"
    f.write_text(json.dumps({"mechanical_baseline": {"started_at": "2026-07-02"}}))
    monkeypatch.setattr(memories, "_THESIS_FILE", str(f))
    rows = memories._theses()
    assert rows[0]["date"] == date(2026, 7, 2)
    assert "mechanical_baseline" in rows[0]["text"]


def test_theses_broken_date_skipped(tmp_path, monkeypatch):
    f = tmp_path / "th2.json"
    f.write_text(json.dumps({"kaputt": {"started_at": "gestern"}}))
    monkeypatch.setattr(memories, "_THESIS_FILE", str(f))
    assert memories._theses() == []


def test_memories_never_raises_on_real_data():
    """Gegen die ECHTEN Produktionsdaten: darf nie werfen, egal was
    dort steht (Ergebnis selbst ist datumsabhängig, also nicht
    festgenagelt)."""
    assert isinstance(memories.memories_for(), list)
