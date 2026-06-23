"""
ExperienceStore – einheitlicher, paradigma-agnostischer Lern-Datensatz.

Eine Zeile pro Entscheidung = Features (zum Entscheidungszeitpunkt) + Outcome
(gelabeltes Ergebnis). Gefüllt aus zwei Quellen:

  * ``backfill`` – die historischen Analysen aus analysis_log.db, nachträglich
    mit echten Kursen gelabelt ("was wäre passiert, wären wir eingestiegen?").
    Papier-Ergebnisse ohne Slippage/Liquidität.
  * ``live``     – echte Bot-Trades (Folgeschritt; Hooks noch nicht verdrahtet).

Bewusst KEIN Lern-Algorithmus hier – nur Speicherung + Lese-API. Welcher Lerner
(Bayes, RL, …) später darauf aufsetzt, ist offen; ``iter_labeled()`` ist die
neutrale Trainings-Schnittstelle.

Stil/Konventionen analog zu analyzers/analysis_log.py (sqlite3, idempotentes
_init_db/_migrate, JSON-Felder).
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, Iterator, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "experience.db")

# Feature-Spalten (Entscheidungszeitpunkt) – Quelle: analysis_log.analyses
_FEATURE_COLS = (
    "decided_at", "ticker", "recommendation", "direction", "sentiment_score",
    "confidence", "debate_winner", "target_price", "suggested_hold",
    "sources_used", "key_catalysts", "risk_factors",
)
# Outcome-Spalten (gelabelt) – via attach_outcome()
_OUTCOME_COLS = (
    "entry_price", "exit_price", "exit_reason", "pnl_pct", "mfe_pct", "mae_pct",
    "hold_days", "outcome", "label_source", "labeled_at",
)
# JSON-serialisierte Felder
_JSON_COLS = ("key_catalysts", "risk_factors")


class ExperienceStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    # ── Schema ────────────────────────────────────────────────────────────────
    def _init_db(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                -- Features
                decided_at      TEXT NOT NULL,
                ticker          TEXT NOT NULL,
                recommendation  TEXT,
                direction       TEXT,
                sentiment_score REAL,
                confidence      TEXT,
                debate_winner   TEXT,
                target_price    REAL,
                suggested_hold  INTEGER,
                sources_used    INTEGER,
                key_catalysts   TEXT,
                risk_factors    TEXT,
                -- Outcome (nullable bis gelabelt)
                entry_price     REAL,
                exit_price      REAL,
                exit_reason     TEXT,
                pnl_pct         REAL,
                mfe_pct         REAL,
                mae_pct         REAL,
                hold_days       INTEGER,
                outcome         TEXT,
                label_source    TEXT,
                labeled_at      TEXT,
                UNIQUE(ticker, decided_at)
            );
            CREATE INDEX IF NOT EXISTS idx_exp_ticker  ON decisions(ticker);
            CREATE INDEX IF NOT EXISTS idx_exp_outcome ON decisions(outcome);
            CREATE INDEX IF NOT EXISTS idx_exp_source  ON decisions(label_source);
            """
        )
        self._conn.commit()

    # ── Schreiben ───────────────────────────────────────────────────────────────
    def upsert_decision(self, feat: Dict) -> int:
        """Legt eine Entscheidung an oder aktualisiert ihre Features (idempotent).

        Eindeutig über (ticker, decided_at). Gibt die row-id zurück.
        """
        if not feat.get("ticker") or not feat.get("decided_at"):
            raise ValueError("upsert_decision braucht 'ticker' und 'decided_at'")

        cols, vals = [], []
        for c in _FEATURE_COLS:
            if c in feat:
                v = feat[c]
                if c in _JSON_COLS and not isinstance(v, str):
                    v = json.dumps(v or [])
                cols.append(c)
                vals.append(v)

        placeholders = ",".join("?" for _ in cols)
        # ON CONFLICT aktualisiert die Features (nicht den Outcome).
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("ticker", "decided_at"))
        sql = (
            f"INSERT INTO decisions ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(ticker, decided_at) DO UPDATE SET {updates}"
            if updates else
            f"INSERT INTO decisions ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(ticker, decided_at) DO NOTHING"
        )
        self._conn.execute(sql, vals)
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM decisions WHERE ticker=? AND decided_at=?",
            (feat["ticker"], feat["decided_at"]),
        ).fetchone()
        return int(row["id"])

    def attach_outcome(self, decision_id: int, outcome: Dict) -> None:
        """Hängt das gelabelte Ergebnis an eine Entscheidung."""
        out = dict(outcome)
        out.setdefault("labeled_at", datetime.utcnow().isoformat())
        cols = [c for c in _OUTCOME_COLS if c in out]
        if not cols:
            return
        set_clause = ",".join(f"{c}=?" for c in cols)
        self._conn.execute(
            f"UPDATE decisions SET {set_clause} WHERE id=?",
            [out[c] for c in cols] + [decision_id],
        )
        self._conn.commit()

    # ── Live-Forward-Logging (echte Trades) ─────────────────────────────────────
    def record_live_entry(self, features: Dict, entry_price: float) -> int:
        """Loggt einen echten Bot-Kauf als offene Entscheidung (label_source='live',
        outcome noch offen). Spiegelt das bestehende Prediction-Tracking."""
        did = self.upsert_decision(features)
        self._conn.execute(
            "UPDATE decisions SET entry_price=?, label_source='live' WHERE id=?",
            (float(entry_price), did),
        )
        self._conn.commit()
        return did

    def open_decision_id(self, ticker: str) -> Optional[int]:
        """Jüngste offene Live-Entscheidung (ohne outcome) für einen Ticker."""
        row = self._conn.execute(
            "SELECT id FROM decisions WHERE ticker=? AND label_source='live' "
            "AND outcome IS NULL ORDER BY decided_at DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        return int(row["id"]) if row else None

    def record_live_exit(self, ticker: str, exit_price: float,
                         exit_reason: str = "") -> Optional[int]:
        """Schließt die offene Live-Entscheidung eines Tickers und labelt das Ergebnis."""
        did = self.open_decision_id(ticker)
        if did is None:
            return None
        row = self._conn.execute(
            "SELECT entry_price, direction, decided_at FROM decisions WHERE id=?",
            (did,),
        ).fetchone()
        entry = float(row["entry_price"] or 0)
        if entry <= 0 or exit_price <= 0:
            return None
        long = (row["direction"] or "LONG").upper() != "SHORT"
        pnl_pct = ((exit_price - entry) / entry * 100) if long else ((entry - exit_price) / entry * 100)
        hold_days = 0
        try:
            d0 = datetime.fromisoformat(row["decided_at"])
            hold_days = max(0, (datetime.utcnow() - d0).days)
        except Exception:
            pass
        self.attach_outcome(did, {
            "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason or "",
            "pnl_pct": round(pnl_pct, 4),
            "hold_days": hold_days,
            "outcome": "WIN" if pnl_pct > 0 else "LOSS",
            "label_source": "live",
        })
        return did

    # ── Lesen ─────────────────────────────────────────────────────────────────
    def iter_labeled(
        self,
        label_source: Optional[str] = None,
        recommendation: Optional[str] = None,
    ) -> Iterator[Tuple[Dict, Dict]]:
        """Paradigma-neutrale Trainings-Schnittstelle.

        Liefert (features, outcome)-Tupel für alle Zeilen mit gesetztem outcome.
        Optional gefiltert nach label_source ('backfill'/'live') und/oder
        recommendation.
        """
        sql = "SELECT * FROM decisions WHERE outcome IS NOT NULL"
        params: List = []
        if label_source:
            sql += " AND label_source=?"
            params.append(label_source)
        if recommendation:
            sql += " AND recommendation=?"
            params.append(recommendation)
        sql += " ORDER BY decided_at"
        for r in self._conn.execute(sql, params):
            d = dict(r)
            for c in _JSON_COLS:
                d[c] = json.loads(d.get(c) or "[]")
            features = {c: d[c] for c in _FEATURE_COLS if c in d}
            out = {c: d[c] for c in _OUTCOME_COLS if c in d}
            yield features, out

    def stats(self) -> Dict:
        row = self._conn.execute(
            """
            SELECT
                COUNT(*)                                                AS total,
                SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END)    AS labeled,
                SUM(CASE WHEN outcome='WIN'  THEN 1 ELSE 0 END)         AS wins,
                SUM(CASE WHEN outcome='LOSS' THEN 1 ELSE 0 END)         AS losses,
                SUM(CASE WHEN label_source='backfill' THEN 1 ELSE 0 END) AS backfill,
                SUM(CASE WHEN label_source='live'     THEN 1 ELSE 0 END) AS live,
                AVG(pnl_pct)                                            AS avg_pnl_pct
            FROM decisions
            """
        ).fetchone()
        d = dict(row) if row else {}
        labeled = d.get("labeled") or 0
        wins = d.get("wins") or 0
        d["win_rate"] = round(wins / labeled, 4) if labeled else None
        return d

    def close(self) -> None:
        self._conn.close()
