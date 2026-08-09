"""
Beobachtungs-Radar — Roadmap 6.11a.

Score-Zeitreihe je Aktie durch regelmäßige lokale (Ollama-)Analyse eines
Beobachtungs-Universums, unabhängig vom strengen Handels-Funnel. Ziel:
(1) Signal-Halbwertszeit + Kalibrierung je Titel/Sektor messbar machen
(speist 1.2), (2) jede Beobachtung + späterer Kurs-Ausgang wird über Zeit
ein Trainingsbeispiel (löst das kleine-Stichprobe-Problem aus 6.5b, ohne
auf echte Trades warten zu müssen).

KERN-TRENNUNG (Roadmap-Leitplanke, wörtlich): "Radar ≠ Trade-Kandidat."
Eine Beobachtung fließt NIE direkt in eine Kauf-Entscheidung — gehandelt
wird weiterhin nur, was den bestehenden, strengen Funnel übersteht. Wer
täglich hunderte statt zehn Aktien analysiert, findet garantiert zufällig
"starke" Scores (Multiple-Testing) — die Eskalations-Schwellen werden
dadurch NICHT gesenkt.

Praktische Grenze ist die Datenanbindung (Dutzende externe News-APIs mit
eigenen Rate-Limits/Kosten JE TICKER über bot.runner.collect_news), nicht
Compute — dieses Modul liefert deshalb nur die Speicher-/Scoring-Schicht;
wie viele Ticker wie oft tatsächlich abgefragt werden, ist eine separate,
spätere Betriebsentscheidung (s. scripts/observation_radar_scan.py).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "observation_radar.db")


class ObservationRadar:
    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at   TEXT NOT NULL,
                ticker        TEXT NOT NULL,
                score         REAL,
                direction     TEXT,
                confidence    TEXT,
                model         TEXT,
                n_headlines   INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_radar_ticker ON observations(ticker);
            CREATE INDEX IF NOT EXISTS idx_radar_date   ON observations(observed_at);
        """)
        self._conn.commit()

    def record(self, ticker: str, score: float, direction: str, confidence: str,
              model: str, n_headlines: int) -> Optional[int]:
        """Fail-open: ein Speicherfehler darf einen laufenden Scan nie abreißen."""
        try:
            cur = self._conn.execute(
                "INSERT INTO observations (observed_at, ticker, score, direction, "
                "confidence, model, n_headlines) VALUES (?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                 ticker, score, direction, confidence, model, n_headlines),
            )
            self._conn.commit()
            return cur.lastrowid
        except Exception:
            return None

    def history(self, ticker: str, limit: int = 100) -> List[Dict]:
        rows = self._conn.execute(
            "SELECT * FROM observations WHERE ticker=? ORDER BY observed_at DESC LIMIT ?",
            (ticker, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_per_ticker(self, limit: int = 500) -> List[Dict]:
        rows = self._conn.execute(
            """SELECT o.* FROM observations o
               INNER JOIN (SELECT ticker, MAX(id) AS max_id FROM observations
                          GROUP BY ticker) latest
               ON o.id = latest.max_id
               ORDER BY o.observed_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()


def observe_ticker(radar: ObservationRadar, prescreener, ticker: str,
                   news_items: List[Dict]) -> Optional[Dict]:
    """Ein prescreen()-Aufruf (lokales Ollama, Wiederverwendung — keine neue
    Prompt-Logik) + Speicherung. Fail-open: kein erfundener Radar-Eintrag,
    wenn Ollama offline war."""
    result = prescreener.prescreen(ticker, news_items)
    if not result.ollama_used:
        return None
    radar.record(
        ticker=ticker, score=result.score, direction=result.direction,
        confidence=result.confidence, model=getattr(prescreener, "model", ""),
        n_headlines=len(news_items),
    )
    return {"ticker": ticker, "score": result.score, "direction": result.direction,
            "confidence": result.confidence}
