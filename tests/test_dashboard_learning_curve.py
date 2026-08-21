"""
Tests für dashboard/learning_curve.py (Roadmap L6.1 — Lernkurven-Wand).

Netzfrei: Monitor-Datei als Temp-Kopie, Experience Store injiziert.
"""
import json

import pytest

from dashboard import learning_curve as lc


# ── Kalibrierungs-Historie ───────────────────────────────────────────────────

def _monitor(tmp_path, monkeypatch, history):
    f = tmp_path / "calibration_monitor.json"
    f.write_text(json.dumps({"history": history}), encoding="utf-8")
    monkeypatch.setattr(lc, "_MONITOR_FILE", str(f))
    return f


def test_calibration_history_reads_and_sorts(tmp_path, monkeypatch):
    _monitor(tmp_path, monkeypatch, [
        {"run_at": "2026-07-07T06:00:00", "n": 327, "brier": 0.228,
         "bss": -0.02, "ece": 0.126, "auc": 0.61},
        {"run_at": "2026-06-01T06:00:00", "n": 100, "brier": 0.25,
         "bss": -0.05, "ece": 0.15, "auc": 0.55},
    ])
    rows = lc.calibration_history()
    assert [r["run_at"][:10] for r in rows] == ["2026-06-01", "2026-07-07"]
    assert rows[1]["brier"] == 0.228


def test_calibration_history_skips_rows_without_date(tmp_path, monkeypatch):
    _monitor(tmp_path, monkeypatch, [
        {"run_at": "", "brier": 0.2},
        {"brier": 0.3},
        {"run_at": "2026-07-07T06:00:00", "brier": 0.228},
    ])
    rows = lc.calibration_history()
    assert len(rows) == 1


def test_calibration_history_non_numeric_becomes_none(tmp_path, monkeypatch):
    _monitor(tmp_path, monkeypatch, [
        {"run_at": "2026-07-07T06:00:00", "brier": "kaputt", "auc": None},
    ])
    row = lc.calibration_history()[0]
    assert row["brier"] is None
    assert row["auc"] is None


def test_calibration_history_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "_MONITOR_FILE", str(tmp_path / "weg.json"))
    assert lc.calibration_history() == []


def test_real_monitor_file_has_too_few_points_for_a_curve():
    """Ehrlichkeits-Anker gegen die ECHTE Datei: Stand 16.7. gibt es
    genau einen Messpunkt — der Tab MUSS deshalb die Tabelle zeigen,
    nicht eine Linie durch einen einzelnen Punkt."""
    rows = lc.calibration_history()
    assert len(rows) < lc.MIN_POINTS_FOR_CURVE


# ── Erfahrungs-Wachstum ──────────────────────────────────────────────────────

def _store_with(tmp_path, entries):
    import analyzers.experience_store as es_mod
    store = es_mod.ExperienceStore(db_path=str(tmp_path / "exp.db"))
    for i, (day, ticker) in enumerate(entries):
        did = store.upsert_decision({
            "decided_at": f"{day}T09:{i:02d}:00", "ticker": ticker,
            "recommendation": "BUY", "direction": "BULLISH",
            "sentiment_score": 0.7, "confidence": "HIGH",
        })
        store.attach_outcome(did, {"pnl_pct": 1.0, "outcome": "WIN",
                                   "label_source": "backfill"})
    return store


def test_experience_growth_cumulates_per_day(tmp_path):
    store = _store_with(tmp_path, [
        ("2026-07-02", "ZA"), ("2026-07-02", "ZB"), ("2026-07-05", "ZC"),
    ])
    rows = lc.experience_growth(store=store)
    assert rows == [
        {"date": "2026-07-02", "new": 2, "total": 2},
        {"date": "2026-07-05", "new": 1, "total": 3},
    ]


def test_experience_growth_ignores_unlabeled(tmp_path):
    """Nur GELABELTE Erfahrungen zählen — eine offene Entscheidung ist
    noch kein Wissen."""
    import analyzers.experience_store as es_mod
    store = es_mod.ExperienceStore(db_path=str(tmp_path / "exp2.db"))
    store.upsert_decision({
        "decided_at": "2026-07-02T09:00:00", "ticker": "ZOPEN",
        "recommendation": "BUY", "direction": "BULLISH",
        "sentiment_score": 0.7, "confidence": "HIGH",
    })
    assert lc.experience_growth(store=store) == []


def test_experience_growth_fail_open(monkeypatch):
    import analyzers.experience_store as es_mod

    class _Boom:
        pass

    monkeypatch.setattr(es_mod, "ExperienceStore", _Boom)
    assert lc.experience_growth() == []


def test_experience_growth_uses_decided_at_not_labeled_at(tmp_path):
    """Kernentscheidung des Punkts: die Zeitachse ist der Entscheidungs-
    tag. Alle echten Labels stammen aus EINEM Backfill-Lauf und trügen
    sonst dasselbe Datum — die Kurve wäre eine Stufe statt einer
    Lernkurve."""
    import analyzers.experience_store as es_mod
    store = es_mod.ExperienceStore(db_path=str(tmp_path / "exp3.db"))
    for day, ticker in (("2026-07-02", "ZA"), ("2026-07-09", "ZB")):
        did = store.upsert_decision({
            "decided_at": f"{day}T09:00:00", "ticker": ticker,
            "recommendation": "BUY", "direction": "BULLISH",
            "sentiment_score": 0.7, "confidence": "HIGH",
        })
        # beide am GLEICHEN Tag etikettiert (wie der echte Backfill)
        store.attach_outcome(did, {"pnl_pct": 1.0, "outcome": "WIN",
                                   "label_source": "backfill",
                                   "labeled_at": "2026-06-23T08:43:41"})
    rows = lc.experience_growth(store=store)
    assert [r["date"] for r in rows] == ["2026-07-02", "2026-07-09"]
