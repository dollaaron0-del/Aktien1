from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "performance.db")


def _categorize_exit(reason: str) -> str:
    r = (reason or "").lower()
    if "stop" in r or "sl" in r:
        return "stop_loss"
    if "take" in r or "tp" in r or "profit" in r:
        return "take_profit"
    if "thesis" in r or "these" in r:
        return "thesis_broken"
    if "hold" in r or "zeit" in r or "expir" in r:
        return "time_exit"
    if "partial" in r:
        return "partial_tp"
    return "manual"


class PerformanceTracker:
    """
    Verfolgt Vorhersage-Genauigkeit und Portfolio-Performance.
    Speichert in SQLite: predictions + portfolio_snapshots.
    Berechnet: Sharpe, Sortino, Calmar, Max Drawdown, Gewinnrate.
    """

    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                predicted_at    TEXT NOT NULL,
                direction       TEXT NOT NULL,
                confidence      TEXT NOT NULL,
                sentiment_score REAL NOT NULL,
                entry_price     REAL NOT NULL,
                exit_price      REAL,
                exit_reason     TEXT,
                exit_category   TEXT,
                pnl_pct         REAL,
                hold_days       INTEGER,
                outcome         TEXT,
                debate_winner   TEXT,
                debate_correct  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                total_value REAL NOT NULL,
                cash        REAL NOT NULL,
                n_positions INTEGER NOT NULL,
                daily_pnl   REAL DEFAULT 0.0
            );
        """)
        self._conn.commit()
        self._migrate()

    def _migrate(self):
        """Fügt fehlende Spalten zur bestehenden DB hinzu (Schema-Migration)."""
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(predictions)")}
        pred_migrations = [
            # Core-Spalten die in alten DBs fehlen können
            ("predicted_at",   "ALTER TABLE predictions ADD COLUMN predicted_at TEXT DEFAULT '2000-01-01T00:00:00'"),
            ("sentiment_score","ALTER TABLE predictions ADD COLUMN sentiment_score REAL DEFAULT 0.0"),
            ("entry_price",    "ALTER TABLE predictions ADD COLUMN entry_price REAL DEFAULT 0.0"),
            # Später hinzugefügte Spalten
            ("exit_price",     "ALTER TABLE predictions ADD COLUMN exit_price REAL"),
            ("exit_reason",    "ALTER TABLE predictions ADD COLUMN exit_reason TEXT"),
            ("exit_category",  "ALTER TABLE predictions ADD COLUMN exit_category TEXT"),
            ("pnl_pct",        "ALTER TABLE predictions ADD COLUMN pnl_pct REAL"),
            ("hold_days",      "ALTER TABLE predictions ADD COLUMN hold_days INTEGER"),
            ("outcome",        "ALTER TABLE predictions ADD COLUMN outcome TEXT"),
            ("debate_winner",  "ALTER TABLE predictions ADD COLUMN debate_winner TEXT"),
            ("debate_correct", "ALTER TABLE predictions ADD COLUMN debate_correct INTEGER DEFAULT 0"),
        ]
        for col, sql in pred_migrations:
            if col not in existing:
                self._conn.execute(sql)
                log.info("DB-Migration: Spalte '%s' zu predictions hinzugefügt", col)

        snap_existing = {row[1] for row in self._conn.execute("PRAGMA table_info(portfolio_snapshots)")}
        snap_migrations = [
            ("recorded_at", "ALTER TABLE portfolio_snapshots ADD COLUMN recorded_at TEXT DEFAULT '2000-01-01T00:00:00'"),
            ("cash",        "ALTER TABLE portfolio_snapshots ADD COLUMN cash REAL DEFAULT 0.0"),
            ("n_positions", "ALTER TABLE portfolio_snapshots ADD COLUMN n_positions INTEGER DEFAULT 0"),
            ("daily_pnl",   "ALTER TABLE portfolio_snapshots ADD COLUMN daily_pnl REAL DEFAULT 0.0"),
        ]
        for col, sql in snap_migrations:
            if col not in snap_existing:
                self._conn.execute(sql)
                log.info("DB-Migration: Spalte '%s' zu portfolio_snapshots hinzugefügt", col)

        self._conn.commit()

    # ── Prediction tracking ───────────────────────────────────────────────────

    def record_prediction(
        self,
        ticker: str,
        direction: str,
        confidence: str,
        sentiment_score: float,
        entry_price: float,
        debate_winner: str = "",
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO predictions
                (ticker, predicted_at, direction, confidence, sentiment_score,
                 entry_price, debate_winner)
            VALUES (?,?,?,?,?,?,?)
            """,
            (ticker, datetime.utcnow().isoformat(), direction, confidence,
             sentiment_score, entry_price, debate_winner),
        )
        self._conn.commit()
        return cur.lastrowid

    def record_outcome(
        self,
        prediction_id: int,
        exit_price: float,
        exit_reason: str = "",
        hold_days: int = 0,
    ) -> None:
        row = self._conn.execute(
            "SELECT entry_price, direction, debate_winner FROM predictions WHERE id=?",
            (prediction_id,),
        ).fetchone()
        if not row:
            return

        entry_price   = float(row["entry_price"])
        direction     = row["direction"]
        debate_winner = row["debate_winner"] or ""

        pnl_pct = (exit_price - entry_price) / entry_price * 100 if entry_price > 0 else 0.0
        outcome = "WIN" if pnl_pct > 0 else "LOSS"
        exit_cat = _categorize_exit(exit_reason)

        # Debate-Akkuratheit: BULL gewann + Position profitabel = korrekt
        debate_correct = 0
        if debate_winner == "BULL" and outcome == "WIN":
            debate_correct = 1
        elif debate_winner == "BEAR" and outcome == "LOSS":
            debate_correct = 1

        self._conn.execute(
            """
            UPDATE predictions SET
                exit_price=?, exit_reason=?, exit_category=?, pnl_pct=?,
                hold_days=?, outcome=?, debate_correct=?
            WHERE id=?
            """,
            (exit_price, exit_reason, exit_cat, round(pnl_pct, 3),
             hold_days, outcome, debate_correct, prediction_id),
        )
        self._conn.commit()

    def record_snapshot(
        self,
        total_value: float,
        cash: float,
        n_positions: int,
        daily_pnl: float = 0.0,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO portfolio_snapshots
                (recorded_at, total_value, cash, n_positions, daily_pnl)
            VALUES (?,?,?,?,?)
            """,
            (datetime.utcnow().isoformat(), total_value, cash, n_positions, daily_pnl),
        )
        self._conn.commit()

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_accuracy_report(self, days: int = 30) -> Dict:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT outcome, confidence, pnl_pct, exit_category, debate_correct
            FROM predictions
            WHERE predicted_at > ? AND outcome IS NOT NULL
            """,
            (cutoff,),
        ).fetchall()

        if not rows:
            return {"trades": 0, "win_rate": 0.0, "avg_pnl": 0.0}

        total = len(rows)
        wins  = sum(1 for r in rows if r["outcome"] == "WIN")
        avg_pnl = sum(r["pnl_pct"] or 0 for r in rows) / total

        by_confidence: Dict[str, Dict] = {}
        for r in rows:
            c = r["confidence"] or "UNKNOWN"
            if c not in by_confidence:
                by_confidence[c] = {"wins": 0, "total": 0}
            by_confidence[c]["total"] += 1
            if r["outcome"] == "WIN":
                by_confidence[c]["wins"] += 1

        by_exit: Dict[str, int] = {}
        for r in rows:
            cat = r["exit_category"] or "unknown"
            by_exit[cat] = by_exit.get(cat, 0) + 1

        debate_rows = [r for r in rows if r["debate_correct"] is not None]
        debate_acc = (
            sum(r["debate_correct"] for r in debate_rows) / len(debate_rows)
            if debate_rows else 0.0
        )

        return {
            "trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "by_confidence": by_confidence,
            "by_exit_category": by_exit,
            "debate_accuracy": round(debate_acc * 100, 1),
            "period_days": days,
        }

    def get_risk_metrics(self, days: int = 90) -> Dict:
        """Sharpe, Sortino, Calmar, Max Drawdown."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots WHERE recorded_at > ? ORDER BY recorded_at",
            (cutoff,),
        ).fetchall()

        if len(rows) < 5:
            return {}

        values = [float(r["total_value"]) for r in rows]
        returns = [(values[i] - values[i-1]) / values[i-1] for i in range(1, len(values))]

        if not returns:
            return {}

        import math
        avg_ret = sum(returns) / len(returns)
        std_ret = (sum((r - avg_ret)**2 for r in returns) / len(returns)) ** 0.5
        neg_returns = [r for r in returns if r < 0]
        downside_std = (sum(r**2 for r in neg_returns) / len(neg_returns)) ** 0.5 if neg_returns else 0.0001

        sharpe  = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0
        sortino = (avg_ret / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0

        # Max Drawdown
        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd

        calmar = (avg_ret * 252 / max_dd) if max_dd > 0 else 0.0

        return {
            "sharpe":       round(sharpe, 3),
            "sortino":      round(sortino, 3),
            "calmar":       round(calmar, 3),
            "max_drawdown": round(max_dd * 100, 2),
            "avg_daily_return": round(avg_ret * 100, 4),
            "period_days":  days,
        }

    def closed_trades(self, days: int = 365) -> List[Dict]:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT ticker, predicted_at, direction, confidence, sentiment_score,
                   entry_price, exit_price, exit_reason, exit_category,
                   pnl_pct, hold_days, outcome, debate_winner, debate_correct
            FROM predictions
            WHERE predicted_at > ? AND outcome IS NOT NULL
            ORDER BY predicted_at DESC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def open_prediction_id(self, ticker: str) -> Optional[int]:
        """Gibt die ID der neuesten offenen Vorhersage für einen Ticker zurück."""
        row = self._conn.execute(
            "SELECT id FROM predictions WHERE ticker=? AND outcome IS NULL ORDER BY id DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        return row[0] if row else None
