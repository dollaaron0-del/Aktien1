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
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker                TEXT NOT NULL,
                entry_date            TEXT NOT NULL,
                entry_price           REAL NOT NULL,
                predicted_target_price REAL,
                predicted_hold_days   INTEGER,
                predicted_direction   TEXT,
                sentiment_score       REAL,
                confidence            TEXT,
                sources_used          INTEGER,
                sources_breakdown     TEXT,     -- JSON: {"reddit":3,"yahoo":2,"newsapi":1}
                sell_date             TEXT,
                sell_price            REAL,
                sell_reason           TEXT,
                sell_reason_category  TEXT,     -- "stop_loss"|"take_profit"|"thesis_broken"|"hold_expired"|"sentiment_sell"
                actual_hold_days      INTEGER,
                actual_return_pct     REAL,
                direction_correct     INTEGER,
                target_hit            INTEGER,
                mode                  TEXT DEFAULT 'normal',  -- 'normal'|'exploration'|'turbo'
                shares                REAL DEFAULT 0.0
            );
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date   TEXT NOT NULL,
                total_value     REAL NOT NULL,
                cash            REAL NOT NULL,
                positions_value REAL NOT NULL,
                phase           TEXT NOT NULL
            );
        """)
        # Migrate existing DBs: add columns if missing
        for _col, _def in [
            ("mode",   "TEXT DEFAULT 'normal'"),
            ("shares", "REAL DEFAULT 0.0"),
        ]:
            try:
                self._conn.execute(f"ALTER TABLE predictions ADD COLUMN {_col} {_def}")
                self._conn.commit()
            except Exception:
                pass  # column already exists

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
        sources_breakdown: Optional[Dict[str, int]] = None,
        mode: str = "normal",
        shares: float = 0.0,
    ) -> int:
        import json as _json
        cursor = self._conn.execute(
            """INSERT INTO predictions
               (ticker, entry_date, entry_price, predicted_target_price,
                predicted_hold_days, predicted_direction, sentiment_score,
                confidence, sources_used, sources_breakdown, mode, shares)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                _json.dumps(sources_breakdown or {}),
                mode,
                shares,
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
        category = _categorize_exit(sell_reason)

        self._conn.execute(
            """UPDATE predictions SET
               sell_date=?, sell_price=?, sell_reason=?, sell_reason_category=?,
               actual_hold_days=?, actual_return_pct=?,
               direction_correct=?, target_hit=?
               WHERE id=?""",
            (
                sell_dt.isoformat(),
                sell_price,
                sell_reason,
                category,
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

    # ── Accuracy reports ───────────────────────────────────────────────────────

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

    def get_source_accuracy(self) -> List[Dict]:
        """
        Win rate per news source per ticker, derived from sources_breakdown JSON.
        Returns rows: {source, ticker, trades, wins, win_rate_pct, avg_return_pct}
        """
        import json as _json
        cursor = self._conn.execute(
            """SELECT ticker, sources_breakdown, actual_return_pct
               FROM predictions
               WHERE sell_date IS NOT NULL AND sources_breakdown IS NOT NULL"""
        )
        rows = cursor.fetchall()

        # Aggregate: source → ticker → [returns]
        data: Dict[str, Dict[str, List[float]]] = {}
        for row in rows:
            try:
                breakdown = _json.loads(row["sources_breakdown"] or "{}")
            except Exception:
                continue
            ret = row["actual_return_pct"] or 0
            for source, count in breakdown.items():
                if count and count > 0:
                    data.setdefault(source, {}).setdefault(row["ticker"], []).append(ret)

        results = []
        for source, tickers in data.items():
            for ticker, returns in tickers.items():
                wins = sum(1 for r in returns if r > 0)
                results.append({
                    "source": source,
                    "ticker": ticker,
                    "trades": len(returns),
                    "wins": wins,
                    "win_rate_pct": round(wins / len(returns) * 100, 1),
                    "avg_return_pct": round(sum(returns) / len(returns), 2),
                })
        results.sort(key=lambda x: x["win_rate_pct"], reverse=True)
        return results

    def get_sentiment_score_buckets(self) -> List[Dict]:
        """
        Groups closed trades by sentiment score bucket and shows win rate per bucket.
        Helps identify which score ranges are actually predictive.
        """
        cursor = self._conn.execute(
            """SELECT sentiment_score, actual_return_pct
               FROM predictions WHERE sell_date IS NOT NULL"""
        )
        rows = cursor.fetchall()

        buckets: Dict[str, List[float]] = {
            "0.65–0.70": [], "0.70–0.75": [], "0.75–0.80": [],
            "0.80–0.85": [], "0.85–1.00": [],
        }

        def _bucket(score: float) -> Optional[str]:
            if score < 0.65:
                return None
            if score < 0.70:
                return "0.65–0.70"
            if score < 0.75:
                return "0.70–0.75"
            if score < 0.80:
                return "0.75–0.80"
            if score < 0.85:
                return "0.80–0.85"
            return "0.85–1.00"

        for row in rows:
            b = _bucket(row["sentiment_score"] or 0)
            if b:
                buckets[b].append(row["actual_return_pct"] or 0)

        results = []
        for label, returns in buckets.items():
            if not returns:
                continue
            wins = sum(1 for r in returns if r > 0)
            results.append({
                "score_range": label,
                "trades": len(returns),
                "win_rate_pct": round(wins / len(returns) * 100, 1),
                "avg_return_pct": round(sum(returns) / len(returns), 2),
            })
        return results

    def get_exit_reason_stats(self) -> List[Dict]:
        """
        Average P&L and count per exit reason category.
        Reveals which exit mechanisms are most reliable.
        """
        cursor = self._conn.execute(
            """SELECT sell_reason_category,
                      COUNT(*) as trades,
                      AVG(actual_return_pct) as avg_return,
                      SUM(CASE WHEN actual_return_pct > 0 THEN 1 ELSE 0 END) as wins
               FROM predictions
               WHERE sell_date IS NOT NULL AND sell_reason_category IS NOT NULL
               GROUP BY sell_reason_category
               ORDER BY avg_return DESC"""
        )
        results = []
        for row in cursor.fetchall():
            trades = row["trades"]
            results.append({
                "category": row["sell_reason_category"],
                "trades": trades,
                "avg_return_pct": round(row["avg_return"] or 0, 2),
                "win_rate_pct": round((row["wins"] or 0) / trades * 100, 1),
            })
        return results

    def get_adaptive_threshold(self, default: float = 0.65) -> float:
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
                      predicted_target_price, direction_correct, target_hit,
                      sell_reason, sell_reason_category, shares
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

    def get_risk_metrics(self, risk_free_rate: float = 0.045) -> Dict:
        """
        Sharpe, Sortino, Calmar Ratio + Max Drawdown aus portfolio_snapshots.
        risk_free_rate: annualisierter risikofreier Zins (Standard 4,5 %).
        """
        import math
        history = list(reversed(self.get_value_history(days=365)))
        if len(history) < 10:
            return {"message": "Zu wenig Portfolio-Snapshots für Risikometriken (min. 10 nötig)."}

        values = [h["total_value"] for h in history]
        returns = [
            (values[i] / values[i - 1]) - 1
            for i in range(1, len(values))
            if values[i - 1] > 0
        ]
        if not returns:
            return {"message": "Keine Renditen berechenbar."}

        n = len(returns)
        rf_daily = risk_free_rate / 252
        mean_r = sum(returns) / n
        excess = [r - rf_daily for r in returns]
        mean_excess = sum(excess) / n

        variance = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
        std = math.sqrt(variance)

        downside = [r - rf_daily for r in returns if r < rf_daily]
        downside_std = math.sqrt(sum(d ** 2 for d in downside) / max(len(downside) - 1, 1)) if len(downside) > 1 else std

        sharpe = round(mean_excess / std * math.sqrt(252), 3) if std > 0 else 0.0
        sortino = round(mean_excess / downside_std * math.sqrt(252), 3) if downside_std > 0 else 0.0

        # Max Drawdown
        peak = values[0]
        max_dd = 0.0
        dd_start_idx = 0
        max_dd_days = 0
        for i, v in enumerate(values):
            if v >= peak:
                peak = v
                dd_start_idx = i
            else:
                dd = (v - peak) / peak
                if dd < max_dd:
                    max_dd = dd
                    max_dd_days = i - dd_start_idx

        # Calmar: annualisierte Rendite / |Max Drawdown|
        total_ret = (values[-1] / values[0] - 1) if values[0] > 0 else 0
        years = n / 252
        ann_ret = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
        calmar = round(ann_ret / abs(max_dd), 3) if max_dd < 0 else 0.0

        return {
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "max_drawdown_duration_days": max_dd_days,
            "annualized_return_pct": round(ann_ret * 100, 2),
            "volatility_annual_pct": round(std * math.sqrt(252) * 100, 2),
            "total_snapshots": n + 1,
        }


def _categorize_exit(reason: str) -> str:
    r = reason.lower()
    if "stop-loss" in r or "stop_loss" in r:
        return "stop_loss"
    if "take-profit" in r or "take_profit" in r:
        return "take_profit"
    if "these" in r or "thesis" in r or "gebrochen" in r:
        return "thesis_broken"
    if "haltedauer" in r or "max" in r:
        return "hold_expired"
    if "sentiment" in r or "sell" in r:
        return "sentiment_sell"
    return "other"
