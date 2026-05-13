"""
Signal Queue – speichert BUY-Signale wenn kein Kapital verfügbar ist
und führt sie automatisch aus sobald Kapital freigesetzt wird.

Ablauf:
  1. evaluate() erkennt BUY-Signal aber Kapital < $50 → enqueue()
  2. _do_close() gibt Kapital frei → process_queue() wird aufgerufen
  3. Stündliche Prüfung triggert ebenfalls process_queue()
  4. Signale älter als `max_age_hours` werden automatisch verworfen
"""
import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "signal_queue.db")


class SignalQueue:
    def __init__(self, max_age_hours: int = 48):
        self.max_age_hours = max_age_hours
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS pending_signals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                sentiment_score REAL NOT NULL,
                confidence      TEXT NOT NULL,
                target_price    REAL,
                direction       TEXT,
                entry_rationale TEXT,
                key_catalysts   TEXT,   -- JSON list
                risk_factors    TEXT,   -- JSON list
                sources_used    INTEGER,
                sources_breakdown TEXT, -- JSON dict
                suggested_hold_days INTEGER,
                created_at      TEXT NOT NULL,
                expires_at      TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending'
            );
        """)
        self._conn.commit()

    def enqueue(
        self,
        ticker: str,
        sentiment_score: float,
        confidence: str,
        target_price: Optional[float],
        direction: str,
        entry_rationale: Optional[str],
        key_catalysts: List[str],
        risk_factors: List[str],
        sources_used: int,
        sources_breakdown: Dict[str, int],
        suggested_hold_days: int,
    ) -> int:
        """Adds a BUY signal to the queue. Returns the new signal ID."""
        # Avoid duplicate pending entries for the same ticker
        self._conn.execute(
            "UPDATE pending_signals SET status='superseded' "
            "WHERE ticker=? AND status='pending'",
            (ticker,),
        )
        now = datetime.utcnow()
        cursor = self._conn.execute(
            """INSERT INTO pending_signals
               (ticker, sentiment_score, confidence, target_price, direction,
                entry_rationale, key_catalysts, risk_factors, sources_used,
                sources_breakdown, suggested_hold_days, created_at, expires_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')""",
            (
                ticker,
                sentiment_score,
                confidence,
                target_price,
                direction,
                entry_rationale,
                json.dumps(key_catalysts[:5]),
                json.dumps(risk_factors[:5]),
                sources_used,
                json.dumps(sources_breakdown),
                suggested_hold_days,
                now.isoformat(),
                (now + timedelta(hours=self.max_age_hours)).isoformat(),
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_pending(self) -> List[Dict]:
        """Returns all non-expired pending signals, sorted by score desc."""
        self._expire_old()
        cursor = self._conn.execute(
            """SELECT * FROM pending_signals
               WHERE status='pending'
               ORDER BY sentiment_score DESC""",
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["key_catalysts"] = json.loads(d["key_catalysts"] or "[]")
            d["risk_factors"] = json.loads(d["risk_factors"] or "[]")
            d["sources_breakdown"] = json.loads(d["sources_breakdown"] or "{}")
            result.append(d)
        return result

    def mark_executed(self, signal_id: int):
        self._conn.execute(
            "UPDATE pending_signals SET status='executed' WHERE id=?",
            (signal_id,),
        )
        self._conn.commit()

    def mark_expired(self, signal_id: int):
        self._conn.execute(
            "UPDATE pending_signals SET status='expired' WHERE id=?",
            (signal_id,),
        )
        self._conn.commit()

    def count_pending(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM pending_signals WHERE status='pending'"
        ).fetchone()[0]

    def get_history(self, limit: int = 20) -> List[Dict]:
        cursor = self._conn.execute(
            """SELECT ticker, sentiment_score, confidence, status,
                      created_at, expires_at, entry_rationale
               FROM pending_signals
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def _expire_old(self):
        now = datetime.utcnow().isoformat()
        self._conn.execute(
            "UPDATE pending_signals SET status='expired' "
            "WHERE status='pending' AND expires_at < ?",
            (now,),
        )
        self._conn.commit()
