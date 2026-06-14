"""
AnalysisLog – persistente SQLite-Datenbank aller Bot-Analysen.

Speichert jede Analyse (BUY, HOLD, SKIP, SELL) mit vollständiger
Begründung, damit sie im Dashboard eingesehen werden kann.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "analysis_log.db")


class AnalysisLog:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS analyses (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                analyzed_at      TEXT NOT NULL,
                ticker           TEXT NOT NULL,
                recommendation   TEXT NOT NULL,
                direction        TEXT NOT NULL,
                sentiment_score  REAL NOT NULL,
                confidence       TEXT NOT NULL,
                entry_rationale  TEXT,
                bull_case        TEXT,
                bear_case        TEXT,
                debate_winner    TEXT,
                key_catalysts    TEXT,
                risk_factors     TEXT,
                target_price     REAL,
                suggested_hold   INTEGER,
                sources_used     INTEGER,
                exchange         TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_al_ticker ON analyses(ticker);
            CREATE INDEX IF NOT EXISTS idx_al_date   ON analyses(analyzed_at);
        """)
        self._conn.commit()

    def store(self, analysis, exchange: str = "") -> None:
        from analyzers.claude_analyzer import AnalysisResult
        if not isinstance(analysis, AnalysisResult):
            return
        # sources_used ist je nach Analyzer-Pfad mal Dict[str,int] (Quelle→Anzahl),
        # mal int, mal {} (Default). Die Spalte ist INTEGER → hier zu einer Zahl
        # normalisieren, sonst crasht SQLite ("type 'dict' is not supported") und
        # reißt den gesamten Analyse-Zyklus ab.
        _sources = analysis.sources_used
        if isinstance(_sources, dict):
            _sources = sum(_sources.values()) if _sources else 0
        elif not isinstance(_sources, int):
            try:
                _sources = int(_sources)
            except (TypeError, ValueError):
                _sources = 0
        self._conn.execute(
            """INSERT INTO analyses
               (analyzed_at, ticker, recommendation, direction, sentiment_score,
                confidence, entry_rationale, bull_case, bear_case, debate_winner,
                key_catalysts, risk_factors, target_price, suggested_hold,
                sources_used, exchange)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.utcnow().isoformat(),
                analysis.ticker,
                analysis.recommendation,
                analysis.direction,
                analysis.sentiment_score,
                analysis.confidence,
                analysis.entry_rationale or "",
                analysis.bull_case or "",
                analysis.bear_case or "",
                analysis.debate_winner or "",
                json.dumps(analysis.key_catalysts or []),
                json.dumps(analysis.risk_factors or []),
                analysis.target_price,
                analysis.suggested_hold_days,
                _sources,
                exchange,
            ),
        )
        self._conn.commit()

    def get_recent(self, limit: int = 100, ticker: Optional[str] = None) -> List[Dict]:
        if ticker:
            rows = self._conn.execute(
                "SELECT * FROM analyses WHERE ticker=? ORDER BY analyzed_at DESC LIMIT ?",
                (ticker.upper(), limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM analyses ORDER BY analyzed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["key_catalysts"] = json.loads(d.get("key_catalysts") or "[]")
            d["risk_factors"]  = json.loads(d.get("risk_factors")  or "[]")
            result.append(d)
        return result

    def get_stats(self) -> Dict:
        row = self._conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN recommendation='BUY'  THEN 1 ELSE 0 END) as buys,
                SUM(CASE WHEN recommendation='SKIP' THEN 1 ELSE 0 END) as skips,
                SUM(CASE WHEN recommendation='HOLD' THEN 1 ELSE 0 END) as holds,
                SUM(CASE WHEN recommendation='SELL' THEN 1 ELSE 0 END) as sells,
                AVG(sentiment_score) as avg_score
            FROM analyses
        """).fetchone()
        return dict(row) if row else {}

    def get_current_stats(self) -> Dict:
        """
        Aktueller Stand: neueste Analyse pro Ticker (dedupliziert).
        Zeigt wie viele Ticker JETZT auf BUY/SKIP/HOLD stehen.
        """
        row = self._conn.execute("""
            WITH latest AS (
                SELECT ticker, recommendation, sentiment_score,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY analyzed_at DESC) as rn
                FROM analyses
            )
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN recommendation='BUY'  THEN 1 ELSE 0 END) as buys,
                SUM(CASE WHEN recommendation='SKIP' THEN 1 ELSE 0 END) as skips,
                SUM(CASE WHEN recommendation='HOLD' THEN 1 ELSE 0 END) as holds,
                SUM(CASE WHEN recommendation='SELL' THEN 1 ELSE 0 END) as sells,
                AVG(sentiment_score) as avg_score
            FROM latest WHERE rn = 1
        """).fetchone()
        return dict(row) if row else {}

    def get_latest_per_ticker(self, limit: int = 200) -> List[Dict]:
        """Neueste Analyse pro Ticker – keine Duplikate."""
        rows = self._conn.execute("""
            SELECT * FROM analyses
            WHERE id IN (
                SELECT MAX(id) FROM analyses GROUP BY ticker
            )
            ORDER BY analyzed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["key_catalysts"] = json.loads(d.get("key_catalysts") or "[]")
            d["risk_factors"]  = json.loads(d.get("risk_factors")  or "[]")
            result.append(d)
        return result

    def get_prev_recommendation(self, ticker: str) -> Optional[str]:
        """Gibt die vorletzte Empfehlung für einen Ticker zurück (für Trend-Anzeige)."""
        rows = self._conn.execute(
            "SELECT recommendation FROM analyses WHERE ticker=? ORDER BY analyzed_at DESC LIMIT 2",
            (ticker.upper(),),
        ).fetchall()
        return rows[1]["recommendation"] if len(rows) >= 2 else None
        """Statistiken nur für heute (UTC-Datum)."""
        today = __import__("datetime").date.today().isoformat()
        row = self._conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN recommendation='BUY'  THEN 1 ELSE 0 END) as buys,
                SUM(CASE WHEN recommendation='SKIP' THEN 1 ELSE 0 END) as skips,
                SUM(CASE WHEN recommendation='HOLD' THEN 1 ELSE 0 END) as holds,
                SUM(CASE WHEN recommendation='SELL' THEN 1 ELSE 0 END) as sells,
                AVG(sentiment_score) as avg_score
            FROM analyses
            WHERE analyzed_at >= ?
        """, (today,)).fetchone()
        return dict(row) if row else {}

    def get_last_cycle_tickers(self) -> List[str]:
        """Gibt die Ticker des letzten Analyse-Zyklus zurück (letzter Batch innerhalb 2h)."""
        last_row = self._conn.execute(
            "SELECT analyzed_at FROM analyses ORDER BY analyzed_at DESC LIMIT 1"
        ).fetchone()
        if not last_row:
            return []
        last_ts = last_row["analyzed_at"]
        rows = self._conn.execute("""
            SELECT DISTINCT ticker FROM analyses
            WHERE analyzed_at >= datetime(?, '-120 minutes')
            ORDER BY analyzed_at DESC
        """, (last_ts,)).fetchall()
        return [r["ticker"] for r in rows]
