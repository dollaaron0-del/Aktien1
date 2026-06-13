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

        # Veraltetes Schema (alte Spalte 'entry_date' mit NOT NULL) erkennen.
        # record_prediction() schreibt diese Legacy-Spalte nicht → IntegrityError.
        # Bei LEERER Tabelle gefahrlos sauber neu aufbauen (kein Datenverlust).
        if "entry_date" in existing:
            n_rows = self._conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
            if n_rows == 0:
                log.warning("predictions: veraltetes Schema erkannt – leere Tabelle wird neu aufgebaut")
                self._conn.executescript("""
                    DROP TABLE predictions;
                    CREATE TABLE predictions (
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
                """)
                self._conn.commit()
                return  # frisches Schema – keine weiteren Spalten-Migrationen nötig
            else:
                log.error(
                    "predictions: veraltetes Schema mit %d Zeilen – kein automatischer "
                    "Neuaufbau (Datenverlust-Schutz). Manuelle Migration nötig.", n_rows
                )
        pred_migrations = [
            # Core-Spalten die in alten DBs fehlen können
            ("predicted_at",   "ALTER TABLE predictions ADD COLUMN predicted_at TEXT DEFAULT '2000-01-01T00:00:00'"),
            ("direction",      "ALTER TABLE predictions ADD COLUMN direction TEXT DEFAULT 'NEUTRAL'"),
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

        # Vorhandene Spalten merken: alte DBs haben zusätzliche NOT-NULL-Spalten
        # (snapshot_date, positions_value, phase) ohne Default. record_snapshot()
        # muss diese mitbefüllen, sonst → NOT NULL constraint failed.
        self._snap_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(portfolio_snapshots)")}

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
        now_iso = datetime.utcnow().isoformat()
        cols = {
            "recorded_at": now_iso,
            "total_value": total_value,
            "cash": cash,
            "n_positions": n_positions,
            "daily_pnl": daily_pnl,
        }
        # Legacy-NOT-NULL-Spalten alter DBs mitbefüllen (ohne Default → sonst Crash)
        legacy = getattr(self, "_snap_cols", set())
        if "snapshot_date" in legacy:
            cols["snapshot_date"] = now_iso[:10]
        if "positions_value" in legacy:
            cols["positions_value"] = total_value - cash
        if "phase" in legacy:
            cols["phase"] = ""
        col_names = ", ".join(cols)
        placeholders = ", ".join("?" for _ in cols)
        self._conn.execute(
            f"INSERT INTO portfolio_snapshots ({col_names}) VALUES ({placeholders})",
            tuple(cols.values()),
        )
        self._conn.commit()

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_accuracy_report(self, days: int = 30) -> Dict:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT outcome, confidence, pnl_pct, exit_category, debate_correct,
                   direction, hold_days
            FROM predictions
            WHERE predicted_at > ? AND outcome IS NOT NULL
            """,
            (cutoff,),
        ).fetchall()

        if not rows:
            return {
                "trades": 0, "total_closed": 0, "win_rate": 0.0, "win_rate_pct": 0.0,
                "avg_pnl": 0.0, "avg_return_pct": 0.0, "direction_accuracy_pct": 0.0,
                "target_hit_pct": 0.0, "avg_hold_days_actual": 0.0, "avg_hold_days_predicted": 0.0,
            }

        total = len(rows)
        wins  = sum(1 for r in rows if r["outcome"] == "WIN")
        avg_pnl = sum(r["pnl_pct"] or 0 for r in rows) / total

        # Richtungs-Genauigkeit: BULLISH/NEUTRAL→WIN bzw. BEARISH→LOSS gilt als korrekt
        dir_correct = sum(
            1 for r in rows
            if ((r["direction"] in ("BULLISH", "NEUTRAL") and r["outcome"] == "WIN")
                or (r["direction"] == "BEARISH" and r["outcome"] == "LOSS"))
        )
        # Zielkurs-Treffer ≈ Anteil der Trades die per Take-Profit geschlossen wurden
        target_hits = sum(1 for r in rows if (r["exit_category"] or "") == "take_profit")
        avg_hold = sum((r["hold_days"] or 0) for r in rows) / total

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
            "total_closed": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1),
            "win_rate_pct": round(wins / total * 100, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "avg_return_pct": round(avg_pnl, 2),
            "direction_accuracy_pct": round(dir_correct / total * 100, 1),
            "target_hit_pct": round(target_hits / total * 100, 1),
            "avg_hold_days_actual": round(avg_hold, 1),
            "avg_hold_days_predicted": 0.0,  # im aktuellen Schema nicht gespeichert
            "by_confidence": by_confidence,
            "by_exit_category": by_exit,
            "debate_accuracy": round(debate_acc * 100, 1),
            "period_days": days,
        }

    def get_value_history(self, days: int = 30) -> List[Dict]:
        """Portfolio-Wert-Verlauf aus portfolio_snapshots für die letzten N Tage."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT recorded_at, total_value, cash, n_positions, daily_pnl
            FROM portfolio_snapshots
            WHERE recorded_at > ?
            ORDER BY recorded_at ASC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

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

    def get_recent_trades(self, n: int = 20) -> List[Dict]:
        """Die letzten n abgeschlossenen Trades (neueste zuerst).
        Enthält Alias-Felder (actual_return_pct, entry_sentiment, actual_hold_days),
        die von margin_readiness, bot_scorer und reflection_engine erwartet werden."""
        rows = self._conn.execute(
            """
            SELECT ticker, predicted_at, direction, confidence, sentiment_score,
                   entry_price, exit_price, exit_reason, exit_category,
                   pnl_pct, hold_days, outcome, debate_winner, debate_correct
            FROM predictions
            WHERE outcome IS NOT NULL
            ORDER BY predicted_at DESC
            LIMIT ?
            """,
            (int(n),),
        ).fetchall()
        out: List[Dict] = []
        for r in rows:
            d = dict(r)
            d["actual_return_pct"] = d.get("pnl_pct")
            d["entry_sentiment"]   = d.get("sentiment_score")
            d["actual_hold_days"]  = d.get("hold_days")
            out.append(d)
        return out

    def get_exit_reason_stats(self, days: int = 365) -> List[Dict]:
        """Aggregierte Statistik je Exit-Kategorie: Anzahl, Win-Rate, Ø-Rendite."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT exit_category, outcome, pnl_pct
            FROM predictions
            WHERE predicted_at > ? AND outcome IS NOT NULL
            """,
            (cutoff,),
        ).fetchall()
        by_cat: Dict[str, List] = {}
        for r in rows:
            by_cat.setdefault(r["exit_category"] or "unknown", []).append(r)
        stats: List[Dict] = []
        for cat, items in by_cat.items():
            n = len(items)
            wins = sum(1 for i in items if i["outcome"] == "WIN")
            avg_ret = sum((i["pnl_pct"] or 0) for i in items) / n if n else 0.0
            stats.append({
                "category":      cat,
                "trades":        n,
                "wins":          wins,
                "win_rate_pct":  round(wins / n * 100, 1) if n else 0.0,
                "avg_return_pct": round(avg_ret, 2),
            })
        stats.sort(key=lambda s: -s["trades"])
        return stats

    # Sentiment-Score-Buckets – Labels müssen mit sentiment_calibrator übereinstimmen
    _SENTIMENT_BUCKETS = [
        ("0.65–0.70", 0.65, 0.70),
        ("0.70–0.75", 0.70, 0.75),
        ("0.75–0.80", 0.75, 0.80),
        ("0.80–0.85", 0.80, 0.85),
        ("0.85–1.00", 0.85, 1.01),
    ]

    def get_sentiment_score_buckets(self, days: int = 365) -> List[Dict]:
        """Win-Rate & Ø-Rendite je Sentiment-Score-Bucket (für Threshold-Kalibrierung)."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            """
            SELECT sentiment_score, outcome, pnl_pct
            FROM predictions
            WHERE predicted_at > ? AND outcome IS NOT NULL
            """,
            (cutoff,),
        ).fetchall()
        buckets: List[Dict] = []
        for label, lo, hi in self._SENTIMENT_BUCKETS:
            items = [
                r for r in rows
                if r["sentiment_score"] is not None and lo <= r["sentiment_score"] < hi
            ]
            n = len(items)
            wins = sum(1 for i in items if i["outcome"] == "WIN")
            avg_ret = sum((i["pnl_pct"] or 0) for i in items) / n if n else 0.0
            buckets.append({
                "score_range":   label,
                "trades":        n,
                "win_rate_pct":  round(wins / n * 100, 1) if n else 0.0,
                "avg_return_pct": round(avg_ret, 2),
            })
        return buckets

    def get_adaptive_threshold(self, base_threshold: float = 0.65) -> float:
        """Empfiehlt einen Buy-Threshold anhand der Win-Rate je Sentiment-Bucket.
        Rein informativ (Anzeige) – fällt bei dünner Datenlage auf base_threshold zurück."""
        buckets = [b for b in self.get_sentiment_score_buckets() if b["trades"] >= 5]
        if not buckets:
            return base_threshold
        best = max(buckets, key=lambda b: b["win_rate_pct"])
        if best["win_rate_pct"] < 55.0:
            return base_threshold
        try:
            lower = float(best["score_range"].split("–")[0])
        except (ValueError, IndexError):
            return base_threshold
        return round(max(min(lower, 0.85), 0.50), 2)

    def get_source_accuracy(self) -> List[Dict]:
        """Quellen-Trefferquote je Ticker. Das aktuelle predictions-Schema speichert
        keine Quellen-Aufschlüsselung mehr → leere Liste (Anzeige wird übersprungen)."""
        return []

    def open_prediction_id(self, ticker: str) -> Optional[int]:
        """Gibt die ID der neuesten offenen Vorhersage für einen Ticker zurück."""
        row = self._conn.execute(
            "SELECT id FROM predictions WHERE ticker=? AND outcome IS NULL ORDER BY id DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        return row[0] if row else None
