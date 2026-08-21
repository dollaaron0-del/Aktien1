"""
dashboard/position_notes.py — Positions-Notizen (Ausbau-Roadmap H1.4).

Freies Notizfeld je offener Position, NUR fürs Auge des Users — der Bot
liest diese Notizen nicht, sie fließen in keine Entscheidung ein. Reine
Gedächtnisstütze (z.B. "warte auf Earnings am 24.7." oder "bewusst über
Zielhaltedauer hinaus, weil Momentum stark").
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

# Modul-Konstante statt Default-Parameter-Wert (bekannte Falle: ein
# Default-Parameter wird bei der Funktionsdefinition gebunden und
# ignoriert spätere monkeypatch-Overrides in Tests) — der Pfad wird im
# Konstruktor-Body zur Laufzeit aufgelöst.
_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "position_notes.db")


class PositionNotes:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS notes ("
            "ticker TEXT PRIMARY KEY, text TEXT, updated TEXT)"
        )
        self._conn.commit()

    def get(self, ticker: str) -> str:
        """Liefert die gespeicherte Notiz, oder "" falls keine existiert."""
        row = self._conn.execute(
            "SELECT text FROM notes WHERE ticker=?", (ticker,)
        ).fetchone()
        return row[0] if row and row[0] is not None else ""

    def set(self, ticker: str, text: str) -> None:
        """Speichert/überschreibt die Notiz eines Tickers."""
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._conn.execute(
            "INSERT INTO notes (ticker, text, updated) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET text=excluded.text, updated=excluded.updated",
            (ticker, text, now),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
