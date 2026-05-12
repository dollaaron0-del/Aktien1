import sqlite3
import os
from datetime import datetime
from typing import Optional, Dict, List

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "performance.db")


class PerformanceTracker:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                predicted_target_price REAL,
                predicted_hold_days INTEGER,
                predicted_direction TEXT,
                sentiment_score REAL,
                confidence TEXT,
                sources_used INTEGER,
                sell_date TEXT,
                sell_price REAL,
                sell_reason TEXT,
                actual_hold_days INTEGER,
                actual_return_pct REAL,
                direction_correct INTEGER,
                target_hit INTEGER
            );
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                total_value REAL NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                phase TEXT NOT NULL
            );
        """)
        self._conn.commit()

    def record_prediction(
        self,
        ticker: str,
        entry_price: float,
        predicted_target_price: Optional[float],
        predicted_hold_days: int,
        predicted_direction: str,
        sentiment_score: float,
        confidence: str,
        sources_used: int,
    ) -> int:
        cursor = self._conn.execute(
            """INSERT INTO predictions
               (ticker, entry_date, entry_price, predicted_target_price,
                predicted_hold_days, predicted_direction, sentiment_score,
                confidence, sources_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker,
                datetime.utcnow().isoformat(),
                entry_price,
                predicted_target_price,
                predicted_hold_days,
                predicted_direction,
                sentiment_score,
                confidence,
                sources_used,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def record_outcome(
        self,
        ticker: str,
        entry_price: float,
        entry_date: str,
        sell_price: float,
        sell_reason: str,
    ):
        actual_return_pct = (sell_price - entry_price) / entry_price * 100
        entry_dt = datetime.fromisoformat(entry_date)
        sell_dt = datetime.utcnow()
        actual_hold_days = (sell_dt - entry_dt).days

        cursor = self._conn.execute(
            """SELECT id, predicted_target_price, predicted_direction
               FROM predictions
               WHERE ticker=? AND sell_date IS NULL
               ORDER BY entry_date DESC LIMIT 1""",
            (ticker,),
        )
        row = cursor.fetchone()
        if not row:
            return

        pred_id = row["id"]
        pred_target = row["predicted_target_price"]
        pred_direction = row["predicted_direction"]

        direction_correct = (
            1
            if (
                (pred_direction == "BULLISH" and actual_return_pct > 0)
                or (pred_direction == "BEARISH" and actual_return_pct < 0)
            )
            else 0
        )
        target_hit = 1 if (pred_target and sell_price >= pred_target) else 0

        self._conn.execute(
            """UPDATE predictions SET
               sell_date=?, sell_price=?, sell_reason=?,
               actual_hold_days=?, actual_return_pct=?,
               direction_correct=?, target_hit=?
               WHERE id=?""",
            (
                sell_dt.isoformat(),
                sell_price,
                sell_reason,
                actual_hold_days,
                actual_return_pct,
                direction_correct,
                target_hit,
                pred_id,
            ),
        )
        self._conn.commit()

    def record_snapshot(self, total_value: float, cash: float, positions_value: float, phase: str):
        self._conn.execute(
            """INSERT INTO portfolio_snapshots (snapshot_date, total_value, cash, positions_value, phase)
               VALUES (?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), total_value, cash, positions_value, phase),
        )
        self._conn.commit()

    def get_accuracy_report(self) -> Dict:
        cursor = self._conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN direction_correct=1 THEN 1 ELSE 0 END) as correct_direction,
                SUM(CASE WHEN target_hit=1 THEN 1 ELSE 0 END) as target_hit,
                AVG(actual_return_pct) as avg_return,
                AVG(actual_hold_days) as avg_hold_days,
                AVG(predicted_hold_days) as avg_predicted_hold,
                SUM(CASE WHEN actual_return_pct > 0 THEN 1 ELSE 0 END) as profitable
               FROM predictions WHERE sell_date IS NOT NULL"""
        )
        row = cursor.fetchone()
        if not row or row["total"] == 0:
            return {"total_closed": 0, "message": "Noch keine abgeschlossenen Trades für Analyse."}

        total = row["total"]
        return {
            "total_closed": total,
            "win_rate_pct": round((row["profitable"] or 0) / total * 100, 1),
            "direction_accuracy_pct": round((row["correct_direction"] or 0) / total * 100, 1),
            "target_hit_pct": round((row["target_hit"] or 0) / total * 100, 1),
            "avg_return_pct": round(row["avg_return"] or 0, 2),
            "avg_hold_days_actual": round(row["avg_hold_days"] or 0, 1),
            "avg_hold_days_predicted": round(row["avg_predicted_hold"] or 0, 1),
        }

    def get_adaptive_threshold(self, default: float = 0.65) -> float:
        """Raises buy threshold when predictions are poor, lowers it when accurate."""
        report = self.get_accuracy_report()
        if report.get("total_closed", 0) < 5:
            return default
        win_rate = report["win_rate_pct"] / 100
        if win_rate < 0.40:
            return min(default + 0.10, 0.85)
        if win_rate < 0.50:
            return min(default + 0.05, 0.80)
        if win_rate > 0.70:
            return max(default - 0.05, 0.55)
        return default

    def get_recent_trades(self, n: int = 10) -> List[Dict]:
        cursor = self._conn.execute(
            """SELECT ticker, entry_date, entry_price, sell_price,
                      actual_return_pct, actual_hold_days, predicted_hold_days,
                      predicted_target_price, direction_correct, target_hit, sell_reason
               FROM predictions
               WHERE sell_date IS NOT NULL
               ORDER BY sell_date DESC LIMIT ?""",
            (n,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_value_history(self, days: int = 90) -> List[Dict]:
        cursor = self._conn.execute(
            """SELECT snapshot_date, total_value, cash, positions_value, phase
               FROM portfolio_snapshots
               ORDER BY snapshot_date DESC LIMIT ?""",
            (days,),
        )
        return [dict(row) for row in cursor.fetchall()]
