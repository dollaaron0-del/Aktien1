"""
AnalysisLog – persistente SQLite-Datenbank aller Bot-Analysen.

Speichert jede Analyse (BUY, HOLD, SKIP, SELL) mit vollständiger
Begründung, damit sie im Dashboard eingesehen werden kann.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "analysis_log.db")


class AnalysisLog:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()
        self._migrate()

    def _migrate(self):
        """Fügt neue Spalten zu bestehenden DBs hinzu (idempotent)."""
        try:
            self._conn.execute("ALTER TABLE analyses ADD COLUMN sources_breakdown TEXT")
            self._conn.commit()
        except Exception:
            pass  # Spalte existiert bereits

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

    def store(self, analysis, exchange: str = "",
              sources_breakdown: Optional[dict] = None) -> None:
        from analyzers.claude_analyzer import AnalysisResult
        if not isinstance(analysis, AnalysisResult):
            return
        # sources_used ist je nach Analyzer-Pfad mal Dict[str,int] (Quelle→Anzahl),
        # mal int, mal {} (Default). Die Spalte ist INTEGER → hier zu einer Zahl
        # normalisieren, sonst crasht SQLite ("type 'dict' is not supported") und
        # reißt den gesamten Analyse-Zyklus ab.
        # sources_used kann Dict[str,int] (Quelle→Anzahl) oder int sein. Den int-Sum
        # in sources_used speichern (Spalte ist INTEGER), den vollen Breakdown als
        # JSON in sources_breakdown – das ermöglicht später eine fundierte Auswertung,
        # welche Collectors real beitragen (Basis für Abschalt-Entscheidungen).
        _sources = analysis.sources_used
        _breakdown = None
        if isinstance(_sources, dict):
            _breakdown = json.dumps(_sources) if _sources else None
            _sources = sum(_sources.values()) if _sources else 0
        elif not isinstance(_sources, int):
            try:
                _sources = int(_sources)
            except (TypeError, ValueError):
                _sources = 0
        # Mehrere Analyzer-Pfade (z.B. multi_agent) setzen sources_used als int und
        # verlieren so den Quellen-Breakdown. Der Runner kennt das echte Dict und
        # reicht es hier explizit durch → Vorrang vor dem aus sources_used abgeleiteten.
        if sources_breakdown:
            _breakdown = json.dumps(sources_breakdown)
            if not _sources:
                _sources = sum(sources_breakdown.values())
        self._conn.execute(
            """INSERT INTO analyses
               (analyzed_at, ticker, recommendation, direction, sentiment_score,
                confidence, entry_rationale, bull_case, bear_case, debate_winner,
                key_catalysts, risk_factors, target_price, suggested_hold,
                sources_used, sources_breakdown, exchange)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
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
                _breakdown,
                exchange,
            ),
        )
        self._conn.commit()

    def get_source_contributions(self, days: int = 30) -> List[Dict]:
        """Aggregiert den Beitrag jedes Collectors über die letzten `days` Tage.

        Liefert pro Quelle: Summe der Treffer, Anzahl Analysen mit >0 Treffern und
        deren Anteil. Quellen mit 0 Treffern über viele Analysen sind Abschalt-
        Kandidaten (sofern nicht nur ein fehlender API-Key die Ursache ist).
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        rows = self._conn.execute(
            "SELECT sources_breakdown FROM analyses "
            "WHERE analyzed_at > ? AND sources_breakdown IS NOT NULL",
            (cutoff,),
        ).fetchall()
        totals: Dict[str, int] = {}
        nonzero: Dict[str, int] = {}
        n = 0
        for (raw,) in rows:
            try:
                d = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            n += 1
            for src, cnt in d.items():
                totals[src] = totals.get(src, 0) + int(cnt or 0)
                if cnt:
                    nonzero[src] = nonzero.get(src, 0) + 1
        out = [
            {
                "source": src,
                "total_hits": totals[src],
                "analyses_with_hits": nonzero.get(src, 0),
                "pct_analyses": round(100 * nonzero.get(src, 0) / n, 1) if n else 0.0,
            }
            for src in sorted(totals, key=lambda s: totals[s], reverse=True)
        ]
        return out

    def source_health(self, days: int = 7, min_analyses: int = 20, weak_pct: float = 10.0) -> Dict:
        """Klassifiziert jede Quelle als tot/schwach/gesund über die letzten `days`.

        - tot:    0 Treffer über alle Analysen (Kandidat: fehlender API-Key oder defekt)
        - schwach: trägt in < `weak_pct` % der Analysen bei
        - gesund: darüber
        `reliable` ist False, wenn weniger als `min_analyses` Analysen vorliegen
        (dann ist die Aussage statistisch zu dünn – z.B. direkt nach einem Crash).
        """
        contribs = self.get_source_contributions(days=days)
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()
        n = self._conn.execute(
            "SELECT COUNT(*) FROM analyses WHERE analyzed_at > ? AND sources_breakdown IS NOT NULL",
            (cutoff,),
        ).fetchone()[0]
        dead, weak, healthy = [], [], []
        for c in contribs:
            if c["total_hits"] == 0:
                dead.append(c["source"])
            elif c["pct_analyses"] < weak_pct:
                weak.append(c["source"])
            else:
                healthy.append(c["source"])
        return {
            "n_analyses": n,
            "reliable": n >= min_analyses,
            "dead": dead,
            "weak": weak,
            "healthy": healthy,
            "days": days,
        }

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
