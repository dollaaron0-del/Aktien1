"""
Trade Journal – Vollständige Nachvollziehbarkeit jedes Trades

Speichert ALLE Events eines Trade-Lifecycles:
  - ENTRY:        Kaufanalyse, Sentiment, Katalysatoren, These, Quellen
  - DAILY_CHECK:  Tägliche Thesen-Überprüfung (Claude-Antwort)
  - WARNING:      These angeschlagen (mittlere Konfidenz)
  - EXIT:         Verkauf mit Grund, P&L, Haltedauer

Ermöglicht:
  - Dashboard-Ansicht "Warum wurde X gekauft?"
  - Lernanalyse: welche Trades verliefen typisch wie?
  - Audit: bei jedem Trade vollständige Begründungskette
"""

import sqlite3
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional


JOURNAL_DB = os.path.join(os.path.dirname(__file__), "..", "data", "trade_journal.db")

EVENT_ENTRY        = "ENTRY"
EVENT_DAILY_CHECK  = "DAILY_CHECK"
EVENT_WARNING      = "WARNING"
EVENT_EXIT         = "EXIT"


class TradeJournal:
    def __init__(self):
        os.makedirs(os.path.dirname(JOURNAL_DB), exist_ok=True)
        self._conn = sqlite3.connect(JOURNAL_DB, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS trade_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT NOT NULL,
                event_type    TEXT NOT NULL,
                event_date    TEXT NOT NULL,
                price         REAL,
                sentiment     REAL,
                confidence    TEXT,
                direction     TEXT,
                recommendation TEXT,
                thesis_valid  INTEGER,
                rationale     TEXT,
                catalysts     TEXT,     -- JSON list
                risks         TEXT,     -- JSON list
                sources       TEXT,     -- JSON dict
                target_price  REAL,
                hold_days     INTEGER,
                pnl           REAL,
                pnl_pct       REAL,
                reason        TEXT,
                position_open INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_ticker_date
                ON trade_events(ticker, event_date);
        """)
        self._conn.commit()

    # ── Logging methods ───────────────────────────────────────────────────────

    def log_entry(
        self,
        ticker: str,
        price: float,
        sentiment: float,
        confidence: str,
        direction: str,
        rationale: str,
        catalysts: List[str],
        risks: List[str],
        sources: Dict[str, int],
        target_price: Optional[float],
        hold_days: int,
    ):
        self._insert(
            ticker=ticker,
            event_type=EVENT_ENTRY,
            price=price,
            sentiment=sentiment,
            confidence=confidence,
            direction=direction,
            recommendation="BUY",
            rationale=rationale,
            catalysts=catalysts,
            risks=risks,
            sources=sources,
            target_price=target_price,
            hold_days=hold_days,
            position_open=1,
        )

    def log_daily_check(
        self,
        ticker: str,
        price: float,
        sentiment: float,
        confidence: str,
        thesis_valid: Optional[bool],
        rationale: str,
        recommendation: str,
    ):
        self._insert(
            ticker=ticker,
            event_type=EVENT_DAILY_CHECK,
            price=price,
            sentiment=sentiment,
            confidence=confidence,
            recommendation=recommendation,
            thesis_valid=1 if thesis_valid is True else (0 if thesis_valid is False else None),
            rationale=rationale,
            position_open=1,
        )

    def log_warning(self, ticker: str, price: float, reason: str, confidence: str):
        self._insert(
            ticker=ticker,
            event_type=EVENT_WARNING,
            price=price,
            confidence=confidence,
            rationale=reason,
            position_open=1,
        )

    def log_exit(
        self,
        ticker: str,
        price: float,
        entry_price: float,
        pnl: float,
        reason: str,
        days_held: int,
    ):
        pnl_pct = (price - entry_price) / entry_price * 100 if entry_price else 0
        self._insert(
            ticker=ticker,
            event_type=EVENT_EXIT,
            price=price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            reason=reason,
            hold_days=days_held,
            position_open=0,
        )
        # Mark all prior open events of this position as closed
        self._conn.execute(
            """UPDATE trade_events SET position_open=0
               WHERE ticker=? AND position_open=1""",
            (ticker,),
        )
        self._conn.commit()

    def _insert(self, **kwargs):
        kwargs.setdefault("event_date", datetime.utcnow().isoformat())
        # JSON-encode list/dict fields
        for k in ("catalysts", "risks", "sources"):
            if k in kwargs and isinstance(kwargs[k], (list, dict)):
                kwargs[k] = json.dumps(kwargs[k])
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" * len(kwargs))
        self._conn.execute(
            f"INSERT INTO trade_events ({cols}) VALUES ({placeholders})",
            tuple(kwargs.values()),
        )
        self._conn.commit()

    # ── Reading methods ───────────────────────────────────────────────────────

    def get_trade_story(self, ticker: str, limit_trades: int = 1) -> List[Dict]:
        """
        Returns the full event chain for the most recent closed trade(s) of a ticker,
        plus any currently open position. Each result is one trade with all its events.
        """
        cursor = self._conn.execute(
            """SELECT * FROM trade_events
               WHERE ticker=? ORDER BY event_date ASC""",
            (ticker,),
        )
        all_events = [self._row_to_dict(r) for r in cursor.fetchall()]

        trades: List[List[Dict]] = []
        current: List[Dict] = []
        for ev in all_events:
            current.append(ev)
            if ev["event_type"] == EVENT_EXIT:
                trades.append(current)
                current = []
        if current:  # open position
            trades.append(current)

        # Return newest trades first, summarized
        results = []
        for events in reversed(trades[-limit_trades:]):
            results.append(self._summarize_trade(events))
        return results

    def get_all_trade_summaries(self, limit: int = 50) -> List[Dict]:
        """Returns one summary row per trade for dashboard listing."""
        cursor = self._conn.execute(
            """SELECT DISTINCT ticker FROM trade_events
               ORDER BY (SELECT MAX(event_date) FROM trade_events t2 WHERE t2.ticker=trade_events.ticker) DESC
               LIMIT ?""",
            (limit,),
        )
        results = []
        for row in cursor.fetchall():
            stories = self.get_trade_story(row["ticker"], limit_trades=10)
            results.extend(stories)
        # Sort by entry date desc, capped
        results.sort(key=lambda s: s.get("entry_date") or "", reverse=True)
        return results[:limit]

    def get_recent_lessons(self, days: int = 30) -> List[Dict]:
        """All closed trades from the last N days, for reflection."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor = self._conn.execute(
            """SELECT ticker, MIN(event_date) as start, MAX(event_date) as end
               FROM trade_events
               WHERE event_date >= ? AND position_open=0
               GROUP BY ticker
               HAVING COUNT(*) > 1""",
            (cutoff,),
        )
        results = []
        for row in cursor.fetchall():
            stories = self.get_trade_story(row["ticker"], limit_trades=5)
            for s in stories:
                if s.get("exit_date"):
                    results.append(s)
        return results

    def _row_to_dict(self, row: sqlite3.Row) -> Dict:
        d = dict(row)
        for k in ("catalysts", "risks", "sources"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    d[k] = []
        return d

    def _summarize_trade(self, events: List[Dict]) -> Dict:
        if not events:
            return {}
        entry = next((e for e in events if e["event_type"] == EVENT_ENTRY), None)
        exit_ev = next((e for e in events if e["event_type"] == EVENT_EXIT), None)
        checks = [e for e in events if e["event_type"] == EVENT_DAILY_CHECK]
        warnings = [e for e in events if e["event_type"] == EVENT_WARNING]

        summary = {
            "ticker": events[0]["ticker"],
            "is_open": exit_ev is None,
            "events": events,
            "n_daily_checks": len(checks),
            "n_warnings": len(warnings),
        }
        if entry:
            summary.update({
                "entry_date": entry["event_date"],
                "entry_price": entry["price"],
                "entry_sentiment": entry["sentiment"],
                "entry_rationale": entry["rationale"],
                "catalysts": entry.get("catalysts") or [],
                "risks": entry.get("risks") or [],
                "sources": entry.get("sources") or {},
                "target_price": entry["target_price"],
                "planned_hold_days": entry["hold_days"],
            })
        if exit_ev:
            summary.update({
                "exit_date": exit_ev["event_date"],
                "exit_price": exit_ev["price"],
                "pnl": exit_ev["pnl"],
                "pnl_pct": exit_ev["pnl_pct"],
                "exit_reason": exit_ev["reason"],
                "actual_hold_days": exit_ev["hold_days"],
            })
        return summary
