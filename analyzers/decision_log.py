"""
DecisionLog – persistiert JEDE Strategie-Entscheidung mit Begründung.

Das Analyse-Log (analysis_log.py) beantwortet "was hält die KI von der Aktie?";
dieses Log beantwortet die zweite Hälfte: "und was hat der Bot daraus GEMACHT —
und warum?" (SKIP wegen Schwelle/Quellen/Korrelation/Slots, BUY, Queue, …).
Bisher existierten die Gründe nur als Konsolen-Ausgabe; das Dashboard
(Tab "Entscheidungen") liest hier.

Fail-open by design: ein Logging-Fehler darf nie den Handelszyklus reißen.
Stil analog analyzers/analysis_log.py (sqlite3, WAL, idempotentes Schema).
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

# Env-Override primär für Tests (Singleton, prozessweit — Tests dürfen nie in
# die echte data/ schreiben).
DB_PATH = os.getenv("DECISION_LOG_PATH") or os.path.join(
    os.path.dirname(__file__), "..", "data", "decision_log.db"
)

# Grobe Grund-Kategorien für die Funnel-Aggregation im Dashboard. Reihenfolge
# zählt: der erste Regex-Treffer gewinnt. Muster folgen den reason-Texten der
# SwingStrategy (strategy/swing_strategy.py).
_REASON_BUCKETS = (
    # Vor "kein_kurs": Gate-Reasons enthalten oft "kein gültiger Kurs".
    ("daten_gate",        re.compile(r"Daten-Gate", re.I)),
    ("kein_kaufsignal",   re.compile(r"Kein Kaufsignal", re.I)),
    ("unter_schwelle",    re.compile(r"< Schwelle", re.I)),
    ("zu_wenige_quellen", re.compile(r"wenige Quellen", re.I)),
    ("max_positionen",    re.compile(r"Max Positionen", re.I)),
    ("earnings_sperre",   re.compile(r"Earnings-Sperre", re.I)),
    ("korrelation",       re.compile(r"Korrelation", re.I)),
    ("liquiditaet",       re.compile(r"Liquidit|Dollar-Volumen", re.I)),
    ("lernfilter_avoid",  re.compile(r"AVOID|Selbstlern|Kalibrier", re.I)),
    ("positionsgroesse",  re.compile(r"Positionsgröße", re.I)),
    ("tagesverlust",      re.compile(r"Tagesverlust|daily.?loss", re.I)),
    ("kein_kurs",         re.compile(r"[Kk]ein Kurs", re.I)),
)


def bucket_reason(reason: str) -> str:
    """Mappt einen freien reason-Text auf eine Funnel-Kategorie."""
    for name, rx in _REASON_BUCKETS:
        if rx.search(reason or ""):
            return name
    return "sonstiges"


class DecisionLog:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            pass
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    decided_at      TEXT NOT NULL,
                    ticker          TEXT NOT NULL,
                    action          TEXT NOT NULL,   -- BUY/SELL/SKIP/HOLD
                    reason          TEXT,            -- Strategie-Begründung
                    executed        TEXT,            -- Executor-Ergebnis (GEKAUFT …)
                    source          TEXT,            -- cycle/queue/conditional/sl_tp
                    recommendation  TEXT,
                    direction       TEXT,
                    sentiment_score REAL,
                    confidence      TEXT,
                    sources_used    INTEGER,
                    regime          TEXT,
                    macro_bias      REAL,
                    cost_eur        REAL,            -- attribuierte API-Kosten (Ziel 5)
                    git_hash        TEXT,            -- Code-Stand (Roadmap 1.6)
                    config_json     TEXT,            -- Config-Schnappschuss (Roadmap 1.6)
                    analysis_id     INTEGER          -- Verkettung → analysis_log (Roadmap 1.4b)
                );
                CREATE INDEX IF NOT EXISTS idx_dec_day    ON decisions(decided_at);
                CREATE INDEX IF NOT EXISTS idx_dec_ticker ON decisions(ticker);
                """
            )
            self._migrate_locked()
            self._conn.commit()

    def _migrate_locked(self) -> None:
        """Idempotente Spalten-Migration für bestehende DBs (ADD COLUMN nur wenn
        fehlt) – analog experience_store. Nachgerüstete Spalten hier eintragen."""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(decisions)")}
        for col, decl in (("cost_eur", "REAL"),
                          ("git_hash", "TEXT"),
                          ("config_json", "TEXT"),
                          ("analysis_id", "INTEGER")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE decisions ADD COLUMN {col} {decl}")

    # ── Schreiben ─────────────────────────────────────────────────────────────
    def log(self, entry: Dict) -> None:
        """Schreibt eine Entscheidung. Fehlt decided_at, wird jetzt genommen.

        Optional ``cost_eur``: die dieser Entscheidung zurechenbaren API-Kosten
        (z.B. der Claude-Analyse-Aufruf, der zu ihr führte) – Naht für die
        Edge−Kosten-Rechnung (Ziel 5, scripts/cost_attribution.py).
        """
        cols = ("decided_at", "ticker", "action", "reason", "executed", "source",
                "recommendation", "direction", "sentiment_score", "confidence",
                "sources_used", "regime", "macro_bias", "cost_eur",
                "git_hash", "config_json", "analysis_id")
        e = dict(entry)
        e.setdefault("decided_at",
                     datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        # Versions-Stempel (Roadmap 1.6): automatisch anhängen, damit jeder
        # Aufrufer ihn gratis bekommt. Fail-open (None), nie eine Exception.
        try:
            from analyzers.version_stamp import stamp
            git_hash, config_json = stamp()
            e.setdefault("git_hash", git_hash)
            e.setdefault("config_json", config_json)
        except Exception:
            pass
        with self._lock:
            self._conn.execute(
                f"INSERT INTO decisions ({','.join(cols)}) "
                f"VALUES ({','.join('?' for _ in cols)})",
                [e.get(c) for c in cols],
            )
            self._conn.commit()

    def add_cost(self, ticker: str, cost_eur: float) -> bool:
        """Rechnet API-Kosten der JÜNGSTEN Entscheidung eines Tickers zu (kumulativ).

        Für den realistischen Live-Fall, in dem die Analyse-Kosten erst NACH dem
        Loggen der Entscheidung bekannt sind (oder über mehrere Aufrufe anfallen).
        Fail-open: nie eine Exception in den Zyklus. Gibt True bei Treffer zurück.
        """
        if not ticker or not cost_eur:
            return False
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT id FROM decisions WHERE ticker=? ORDER BY decided_at DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
                if row is None:
                    return False
                self._conn.execute(
                    "UPDATE decisions SET cost_eur = COALESCE(cost_eur, 0) + ? WHERE id=?",
                    (round(float(cost_eur), 6), int(row["id"])),
                )
                self._conn.commit()
            return True
        except Exception:
            return False

    def cost_stats(self) -> Dict:
        """Aggregat der attribuierten Kosten (für den Edge−Kosten-Report)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n, "
                "SUM(CASE WHEN cost_eur IS NOT NULL THEN 1 ELSE 0 END) AS n_cost, "
                "SUM(cost_eur) AS total_cost "
                "FROM decisions"
            ).fetchone()
        d = dict(row) if row else {}
        n_cost = d.get("n_cost") or 0
        total = d.get("total_cost") or 0.0
        return {
            "n_decisions": d.get("n") or 0,
            "n_with_cost": n_cost,
            "total_cost_eur": round(total, 6),
            "avg_cost_eur": round(total / n_cost, 6) if n_cost else None,
        }

    # ── Lesen (Dashboard) ─────────────────────────────────────────────────────
    def days(self, limit: int = 30) -> List[str]:
        """Tage (YYYY-MM-DD, absteigend), an denen Entscheidungen fielen."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT substr(decided_at, 1, 10) AS d FROM decisions "
                "ORDER BY d DESC LIMIT ?", (limit,),
            ).fetchall()
        return [r["d"] for r in rows]

    def get_day(self, day: str) -> List[Dict]:
        """Alle Entscheidungen eines Tages (neueste zuerst)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decisions WHERE decided_at LIKE ? "
                "ORDER BY decided_at DESC", (f"{day}%",),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent(self, limit: int = 200,
                   ticker: Optional[str] = None) -> List[Dict]:
        sql, params = "SELECT * FROM decisions", []
        if ticker:
            sql += " WHERE ticker=?"
            params.append(ticker)
        sql += " ORDER BY decided_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def funnel(self, day: str) -> Dict:
        """Funnel eines Tages: analysiert → Aktionen → SKIP-Grund-Kategorien."""
        entries = self.get_day(day)
        actions: Dict[str, int] = {}
        skip_reasons: Dict[str, int] = {}
        for e in entries:
            a = (e.get("action") or "?").upper()
            actions[a] = actions.get(a, 0) + 1
            if a == "SKIP":
                b = bucket_reason(e.get("reason") or "")
                skip_reasons[b] = skip_reasons.get(b, 0) + 1
        return {
            "total": len(entries),
            "actions": actions,
            "skip_reasons": dict(sorted(skip_reasons.items(),
                                        key=lambda kv: -kv[1])),
        }

    def close(self) -> None:
        self._conn.close()


_singleton: Optional[DecisionLog] = None
_singleton_lock = threading.Lock()


def get_decision_log() -> Optional[DecisionLog]:
    """Prozessweites Singleton, fail-open (None statt Exception)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            try:
                _singleton = DecisionLog()
            except Exception:
                return None
        return _singleton
