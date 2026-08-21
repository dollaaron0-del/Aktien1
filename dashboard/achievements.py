"""
dashboard/achievements.py — Plaketten-Wand (Ausbau-Roadmap H7.2).

Fünf feste Plaketten (`CATALOG`), jede an eine echte, bestehende
Datenquelle gebunden — read-only, fail-open. `unlocked()` prüft alle und
MERKT neu erreichte dauerhaft in `data/achievements.json`: einmal
erreicht, bleibt eine Plakette erreicht, auch wenn die zugrunde liegende
Bedingung später wieder kippt (z.B. eine spätere ABANDONED-These nach
einer früher schon PROVENen — Rückwärts-Entzug widerspricht dem Sinn
einer Plakette).

Dokumentierte Einschränkung bei „30 Tage ohne Not-Aus": `CircuitBreaker`
(portfolio/circuit_breaker.py) persistiert KEINE Trigger-Historie, nur
den aktuellen Tag (`data/circuit_breaker.json`: day/open_value/
peak_value). Diese Prüfung nutzt darum die Fabrik-Zustands-
Schnappschüsse (H2.1, `data/factory_history.jsonl`) als Ersatz-Historie
— die Plakette kann erst unlocken, sobald mindestens 30 Tage ECHTE
Aufzeichnung vorliegen (bewusst kein Kurzschluss "keine Daten = kein
Fehler = Plakette").
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
ACHIEVEMENTS_FILE = os.path.join(_DATA_DIR, "achievements.json")


def _check_first_live_trade() -> bool:
    try:
        from analyzers.experience_store import ExperienceStore
        store = ExperienceStore()
        stats = store.stats()
        store.close()
        return (stats.get("live") or 0) > 0
    except Exception:
        return False


def _check_hundred_labeled_trades() -> bool:
    try:
        from analyzers.experience_store import ExperienceStore
        store = ExperienceStore()
        stats = store.stats()
        store.close()
        return (stats.get("labeled") or 0) >= 100
    except Exception:
        return False


def _check_first_proven_thesis() -> bool:
    try:
        from analyzers.thesis_verdict import PROVEN, load_registry
        registry = load_registry()
        return any(t.status == PROVEN for t in registry.values())
    except Exception:
        return False


def _check_no_breaker_trigger_30d(history_path: Optional[str] = None) -> bool:
    """Heuristik (dokumentiert): CircuitBreaker hat keine eigene
    Trigger-Historie — nutzt stattdessen die Fabrik-Schnappschüsse."""
    try:
        if history_path is None:
            from dashboard.factory.state import HISTORY_FILE
            history_path = HISTORY_FILE
        if not os.path.exists(history_path):
            return False
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        earliest_ts: Optional[str] = None
        with open(history_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("ts") or ""
                if not ts:
                    continue
                if earliest_ts is None or ts < earliest_ts:
                    earliest_ts = ts
                if ts[:10] >= cutoff:
                    breaker = (row.get("machines") or {}).get("breaker") or {}
                    if breaker.get("status") == "err":
                        return False
        if earliest_ts is None or earliest_ts[:10] > cutoff:
            return False  # noch keine 30 Tage echte Aufzeichnung
        return True
    except Exception:
        return False


def _check_one_year_operation(analysis_db_path: Optional[str] = None) -> bool:
    try:
        import sqlite3
        if analysis_db_path is None:
            from analyzers.analysis_log import DB_PATH
            analysis_db_path = DB_PATH
        if not os.path.exists(analysis_db_path):
            return False
        conn = sqlite3.connect(analysis_db_path)
        try:
            row = conn.execute("SELECT MIN(analyzed_at) AS m FROM analyses").fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return False
        earliest = date.fromisoformat(str(row[0])[:10])
        return (date.today() - earliest).days >= 365
    except Exception:
        return False


CATALOG: List[Dict] = [
    {
        "id": "first_live_trade",
        "title": "Erster Live-Trade",
        "condition_text": "Der erste echte (nicht Backfill-)Trade wurde ausgeführt.",
        "check": _check_first_live_trade,
    },
    {
        "id": "hundred_labeled_trades",
        "title": "100 gelabelte Trades",
        "condition_text": "100 Trades im Selbstlern-Fundament ausgewertet.",
        "check": _check_hundred_labeled_trades,
    },
    {
        "id": "first_proven_thesis",
        "title": "Erste bewiesene These",
        "condition_text": "Eine Strategie-These hat die Kante statistisch belegt (PROVEN).",
        "check": _check_first_proven_thesis,
    },
    {
        "id": "thirty_days_no_breaker",
        "title": "30 Tage ohne Not-Aus",
        "condition_text": "30 aufgezeichnete Tage ohne ausgelösten Circuit-Breaker.",
        "check": _check_no_breaker_trigger_30d,
    },
    {
        "id": "one_year_operation",
        "title": "1 Jahr Betrieb",
        "condition_text": "Seit der ersten Analyse ist ein Jahr vergangen.",
        "check": _check_one_year_operation,
    },
]


def _load_unlocked(path: Optional[str] = None) -> Dict[str, Dict]:
    target = path or ACHIEVEMENTS_FILE
    if not os.path.exists(target):
        return {}
    try:
        with open(target, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _save_unlocked(data: Dict[str, Dict], path: Optional[str] = None) -> None:
    target = path or ACHIEVEMENTS_FILE
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def unlocked(path: Optional[str] = None) -> List[Dict]:
    """Prüft alle Plaketten aus `CATALOG` (in Katalog-Reihenfolge),
    merkt neu erreichte dauerhaft. Gibt EINE Zeile je Plakette zurück:
    `{id, title, condition_text, unlocked, unlocked_at}`. Fail-open:
    eine kaputte Prüf-Funktion zählt als "nicht erreicht", niemals als
    Crash."""
    stored = _load_unlocked(path)
    changed = False
    rows: List[Dict] = []
    for item in CATALOG:
        entry = stored.get(item["id"])
        if entry is not None:
            rows.append({
                "id": item["id"], "title": item["title"],
                "condition_text": item["condition_text"],
                "unlocked": True, "unlocked_at": entry.get("unlocked_at"),
            })
            continue
        try:
            achieved = bool(item["check"]())
        except Exception:
            achieved = False
        if achieved:
            now = date.today().isoformat()
            stored[item["id"]] = {"unlocked_at": now}
            changed = True
            rows.append({
                "id": item["id"], "title": item["title"],
                "condition_text": item["condition_text"],
                "unlocked": True, "unlocked_at": now,
            })
        else:
            rows.append({
                "id": item["id"], "title": item["title"],
                "condition_text": item["condition_text"],
                "unlocked": False, "unlocked_at": None,
            })
    if changed:
        try:
            _save_unlocked(stored, path)
        except Exception:
            pass
    return rows
