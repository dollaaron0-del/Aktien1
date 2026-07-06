"""
ExperienceStore – einheitlicher, paradigma-agnostischer Lern-Datensatz.

Eine Zeile pro Entscheidung = Features (zum Entscheidungszeitpunkt) + Outcome
(gelabeltes Ergebnis). Gefüllt aus zwei Quellen:

  * ``backfill`` – die historischen Analysen aus analysis_log.db, nachträglich
    mit echten Kursen gelabelt ("was wäre passiert, wären wir eingestiegen?").
    Papier-Ergebnisse ohne Slippage/Liquidität.
  * ``live``     – echte Bot-Trades (verdrahtet in bot/runner.py über
    record_live_entry / record_live_exit).

Bewusst KEIN Lern-Algorithmus hier – nur Speicherung + Lese-API. Welcher Lerner
(Bayes, RL, …) später darauf aufsetzt, ist offen; ``iter_labeled()`` ist die
neutrale Trainings-Schnittstelle.

Stil/Konventionen analog zu analyzers/analysis_log.py (sqlite3, idempotentes
_init_db/_migrate, JSON-Felder).
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "experience.db")

# Feature-Spalten (Entscheidungszeitpunkt) – Quelle: analysis_log.analyses
# regime/macro_bias sind nachgerüstete Kontext-Features (nullable; nur bei Live-
# Entries befüllt, im Papier-Backfill i.d.R. leer): Marktregime zum Entscheidungs-
# zeitpunkt (BULL/NEUTRAL/BEAR/CRISIS) und der Makro-Bias-Score (-1..+1).
_FEATURE_COLS = (
    "decided_at", "ticker", "recommendation", "direction", "sentiment_score",
    "confidence", "debate_winner", "target_price", "suggested_hold",
    "sources_used", "key_catalysts", "risk_factors",
    "regime", "macro_bias",
)
# Outcome-Spalten (gelabelt) – via attach_outcome()
_OUTCOME_COLS = (
    "entry_price", "exit_price", "exit_reason", "pnl_pct", "mfe_pct", "mae_pct",
    "hold_days", "outcome", "label_source", "labeled_at",
)
# JSON-serialisierte Felder
_JSON_COLS = ("key_catalysts", "risk_factors")


def _finite(x) -> Optional[float]:
    """float-Cast mit NaN/Inf-Guard (yfinance-NaN-Falle)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _fetch_daily_bars(ticker: str, start: str) -> List[Dict]:
    """Tages-OHLC ab `start` (YYYY-MM-DD) via yfinance — nur für die MFE/MAE-
    Bestimmung bei Live-Exits. Wirft bei Netz-/Datenfehlern; der Aufrufer
    (record_live_exit) fängt fail-open."""
    import yfinance as yf  # lazy: Store bleibt ohne Live-Exits netzfrei

    hist = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    return [
        {"date": idx.strftime("%Y-%m-%d"), "high": row.get("High"), "low": row.get("Low")}
        for idx, row in hist.iterrows()
    ]


def _live_excursions(window: List[Dict], entry: float, long: bool,
                     pnl_pct: float, hold_days: int) -> Tuple[Optional[float], Optional[float]]:
    """MFE/MAE (%) aus den Tages-Bars des Haltefensters.

    Entry-Tag ist bereits ausgefiltert — dieselbe Messbasis wie simulate_outcome
    im Backfill (dort bars[1:]), damit live- und backfill-Zeilen vergleichbar
    bleiben. Der reale Exit-P&L wird eingefaltet (MFE ≥ Gewinn-P&L, MAE ≤
    Verlust-P&L, denn zum Exit-Preis wurde tatsächlich gehandelt). Ohne
    verwertbare Bars: bei Same-Day-Exit (hold_days=0) genügt der P&L-Fold,
    bei längerem Halten ehrlich (None, None) statt fabrizierter Werte.
    """
    mfe = max(0.0, pnl_pct)
    mae = min(0.0, pnl_pct)
    usable = False
    for b in window:
        hi, lo = _finite(b.get("high")), _finite(b.get("low"))
        if hi is None or lo is None:
            continue
        usable = True
        if long:
            mfe = max(mfe, (hi - entry) / entry * 100)
            mae = min(mae, (lo - entry) / entry * 100)
        else:
            mfe = max(mfe, (entry - lo) / entry * 100)
            mae = min(mae, (entry - hi) / entry * 100)
    if not usable and hold_days > 0:
        return None, None
    return round(mfe, 4), round(mae, 4)


class ExperienceStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Ein Prozess-weites Lock serialisiert alle Zugriffe über die (geteilte)
        # Verbindung. Nötig, weil der Bot unter PARALLEL_ANALYSIS aus mehreren
        # Threads schreiben kann und sqlite3 mit check_same_thread=False sonst
        # "database is locked" / Interferenz riskiert. WAL erhöht die Nebenläufigkeit.
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            pass
        self._init_db()

    # ── Schema ────────────────────────────────────────────────────────────────
    def _init_db(self) -> None:
        with self._lock:
            self._init_db_locked()
            self._migrate_locked()

    def _init_db_locked(self) -> None:
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
                regime          TEXT,
                macro_bias      REAL,
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

    def _migrate_locked(self) -> None:
        """Idempotente Spalten-Migration für bestehende DBs (ADD COLUMN nur wenn
        fehlt). Neue nachgerüstete Features müssen hier stehen, sonst sehen alte
        Datenbanken sie nicht."""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(decisions)")}
        for col, decl in (("regime", "TEXT"), ("macro_bias", "REAL")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} {decl}")
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
        with self._lock:
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
        out.setdefault("labeled_at", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        cols = [c for c in _OUTCOME_COLS if c in out]
        if not cols:
            return
        set_clause = ",".join(f"{c}=?" for c in cols)
        with self._lock:
            self._conn.execute(
                f"UPDATE decisions SET {set_clause} WHERE id=?",
                [out[c] for c in cols] + [decision_id],
            )
            self._conn.commit()

    # ── Live-Forward-Logging (echte Trades) ─────────────────────────────────────
    def record_live_entry(self, features: Dict, entry_price: float) -> int:
        """Loggt einen echten Bot-Kauf als offene Entscheidung (label_source='live',
        outcome noch offen). Spiegelt das bestehende Prediction-Tracking."""
        with self._lock:
            did = self.upsert_decision(features)
            self._conn.execute(
                "UPDATE decisions SET entry_price=?, label_source='live' WHERE id=?",
                (float(entry_price), did),
            )
            self._conn.commit()
        return did

    def open_decision_id(self, ticker: str) -> Optional[int]:
        """Jüngste offene Live-Entscheidung (ohne outcome) für einen Ticker."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM decisions WHERE ticker=? AND label_source='live' "
                "AND outcome IS NULL ORDER BY decided_at DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        return int(row["id"]) if row else None

    def record_live_exit(self, ticker: str, exit_price: float,
                         exit_reason: str = "",
                         bars: Optional[List[Dict]] = None) -> Optional[int]:
        """Schließt die offene Live-Entscheidung eines Tickers und labelt das Ergebnis.

        bars: optionale, vorbeschaffte Tages-Bars ({date, high, low}) für die
        MFE/MAE-Bestimmung; None → fail-open-Fetch via yfinance. Schlägt beides
        fehl, bleibt MFE/MAE NULL, der Exit wird trotzdem gelabelt.
        """
        did = self.open_decision_id(ticker)
        if did is None:
            return None
        with self._lock:
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
            hold_days = max(0, (datetime.now(timezone.utc).replace(tzinfo=None) - d0).days)
        except Exception:
            pass
        # N2: MFE/MAE über das echte Haltefenster (Entry-Tag exklusive) —
        # dieselbe Tages-High/Low-Messbasis wie der Backfill. Komplett fail-open.
        entry_day = (row["decided_at"] or "")[:10]
        if bars is None:
            try:
                bars = _fetch_daily_bars(ticker, entry_day)
            except Exception:
                bars = None
        mfe_pct = mae_pct = None
        try:
            window = [b for b in (bars or []) if (b.get("date") or "") > entry_day]
            mfe_pct, mae_pct = _live_excursions(window, entry, long, pnl_pct, hold_days)
        except Exception:
            mfe_pct = mae_pct = None
        out = {
            "exit_price": round(float(exit_price), 6),
            "exit_reason": exit_reason or "",
            "pnl_pct": round(pnl_pct, 4),
            "hold_days": hold_days,
            "outcome": "WIN" if pnl_pct > 0 else "LOSS",
            "label_source": "live",
        }
        if mfe_pct is not None:
            out["mfe_pct"] = mfe_pct
            out["mae_pct"] = mae_pct
        self.attach_outcome(did, out)
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
        # Zeilen unter dem Lock materialisieren, NICHT über yield hinweg halten
        # (sonst deadlockt ein Konsument, der beim Iterieren schreibt).
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        for r in rows:
            d = dict(r)
            for c in _JSON_COLS:
                d[c] = json.loads(d.get(c) or "[]")
            features = {c: d[c] for c in _FEATURE_COLS if c in d}
            out = {c: d[c] for c in _OUTCOME_COLS if c in d}
            yield features, out

    def stats(self) -> Dict:
        with self._lock:
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
