"""
Live-Sichtbarkeit: "Was macht der Bot gerade?" (Roadmap 1.5a+b+e).

Drei Nähte, eine Datei:

- **Status-Zeile** (1.5a): der Runner meldet Phasenwechsel über set_phase()/
  set_idle() → data/bot_status.json (atomar via tmp+rename). Das Dashboard
  rendert daraus die Live-Zeile im Header ("Zyklus seit 09:02 · Analyse ·
  NVDA 12/45"). Der Scheduler schreibt zwischen Jobs den Idle-Zustand samt
  nächstem geplanten Lauf — dadurch heilt sich auch ein nach Crash
  hängengebliebener "cycle"-Status von selbst.

- **Aktivitätsfeed** (1.5b): strukturierte Events (cycle_start, analysis_done,
  trade, cycle_end, …) in data/activity_feed.db (SQLite, WAL) statt nur
  Fließtext im bot.log. Dashboard-Tab "Live" zeigt die letzten ~50.

- **Zyklus-Zeitleiste** (1.5e): phase_history in bot_status.json — ein
  Eintrag je Phasenname (Start/Exits prüfen/Vorladen/Analyse), mit
  started_at/ended_at. phase_durations() rechnet daraus Dauer je Phase in
  Sekunden; Dashboard-Tab "Live" zeigt sie als kleine Tabelle.

Fail-open by design: KEINE der öffentlichen Funktionen wirft — ein Fehler in
der Sichtbarkeits-Schicht darf nie den Handelszyklus reißen. Env-Overrides
(BOT_STATUS_PATH, ACTIVITY_FEED_PATH) primär für Tests.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATUS_PATH = os.getenv("BOT_STATUS_PATH") or os.path.join(
    _PROJECT_DIR, "data", "bot_status.json"
)
FEED_PATH = os.getenv("ACTIVITY_FEED_PATH") or os.path.join(
    _PROJECT_DIR, "data", "activity_feed.db"
)

# Feed nicht unbegrenzt wachsen lassen; ~2000 Events decken mehrere Tage.
_FEED_KEEP = int(os.getenv("ACTIVITY_FEED_KEEP", "2000"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


# ── Status-Zeile (1.5a) ───────────────────────────────────────────────────────

def _write_status(payload: Dict) -> None:
    payload["updated_at"] = _now_iso()
    payload["pid"] = os.getpid()
    tmp = STATUS_PATH + ".tmp"
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, STATUS_PATH)  # atomar — Dashboard liest nie halbe Dateien


def set_phase(phase: str, ticker: Optional[str] = None,
              idx: Optional[int] = None, total: Optional[int] = None,
              detail: Optional[str] = None) -> None:
    """Phasenwechsel im laufenden Zyklus melden. Behält cycle_started_at des
    laufenden Zyklus bei; beim ersten Aufruf (state≠cycle) beginnt ein neuer.

    Roadmap 1.5e (Zyklus-Zeitleiste): führt zusätzlich phase_history — ein
    Eintrag PRO PHASENNAME (nicht pro Aufruf), damit die vielen set_phase-
    Rufe innerhalb von "Analyse" (einer je Ticker, nur idx/total ändert sich)
    die Zeitleiste nicht mit hunderten Einträgen fluten."""
    try:
        prev = read_status() or {}
        same_cycle = prev.get("state") == "cycle"
        started = prev.get("cycle_started_at") if same_cycle else None
        history = list(prev.get("phase_history") or []) if same_cycle else []
        if not history or history[-1].get("phase") != phase:
            now = _now_iso()
            if history:
                history[-1]["ended_at"] = now
            history.append({"phase": phase, "started_at": now, "ended_at": None})
        _write_status({
            "state": "cycle",
            "phase": phase,
            "cycle_started_at": started or _now_iso(),
            "ticker": ticker,
            "idx": idx,
            "total": total,
            "detail": detail,
            "phase_history": history,
        })
    except Exception:
        pass


def set_idle(next_run: Optional[str] = None, note: Optional[str] = None) -> None:
    """Zyklus beendet / Bot wartet. next_run: ISO-Zeitpunkt des nächsten Jobs.

    phase_history bleibt erhalten (letzte Phase wird geschlossen) – so zeigt
    die Zeitleiste (Roadmap 1.5e) auch im Idle-Zustand noch den zuletzt
    abgeschlossenen Zyklus, bis der nächste set_phase()-Aufruf sie ersetzt."""
    try:
        prev = read_status() or {}
        history = list(prev.get("phase_history") or []) if prev.get("state") == "cycle" else []
        if history and history[-1].get("ended_at") is None:
            history[-1]["ended_at"] = _now_iso()
        _write_status({
            "state": "idle",
            "phase": None,
            "cycle_started_at": None,
            "ticker": None, "idx": None, "total": None,
            "next_run": next_run,
            "detail": note,
            "phase_history": history,
        })
    except Exception:
        pass


def phase_durations(status: Optional[Dict] = None) -> List[Dict]:
    """Zeitleiste des aktuellen/letzten Zyklus mit Dauer je Phase in Sekunden
    (Roadmap 1.5e). Die letzte Phase eines LAUFENDEN Zyklus hat kein ended_at
    → Dauer bis jetzt. Wirft nie, leere Liste im Fehlerfall/ohne Historie."""
    try:
        s = status if status is not None else read_status()
        history = (s or {}).get("phase_history") or []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        out = []
        for entry in history:
            started = datetime.fromisoformat(entry["started_at"])
            ended_raw = entry.get("ended_at")
            ended = datetime.fromisoformat(ended_raw) if ended_raw else now
            out.append({
                "phase": entry.get("phase"),
                "started_at": entry.get("started_at"),
                "ended_at": ended_raw,
                "duration_seconds": max(0.0, (ended - started).total_seconds()),
            })
        return out
    except Exception:
        return []


def read_status() -> Optional[Dict]:
    """Aktueller Status oder None. Wirft nie."""
    try:
        with open(STATUS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def status_age_seconds(status: Optional[Dict] = None) -> Optional[float]:
    """Alter des Status in Sekunden — Staleness-Check fürs Dashboard
    (ein seit Stunden nicht aktualisierter 'cycle' ist ein Crash-Rest)."""
    try:
        s = status if status is not None else read_status()
        if not s or not s.get("updated_at"):
            return None
        return (datetime.now(timezone.utc).replace(tzinfo=None)
                - datetime.fromisoformat(s["updated_at"])).total_seconds()
    except Exception:
        return None


# ── Aktivitätsfeed (1.5b) ─────────────────────────────────────────────────────

class ActivityFeed:
    def __init__(self, db_path: Optional[str] = None):
        # Kein Default-Parameter auf FEED_PATH: der würde zur Klassendefinition
        # gebunden und ignorierte spätere Overrides (Tests!). Zur Laufzeit lesen.
        self.db_path = db_path or FEED_PATH
        db_path = self.db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            pass
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id     INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts     TEXT NOT NULL,
                    event  TEXT NOT NULL,   -- cycle_start/analysis_done/trade/…
                    ticker TEXT,
                    detail TEXT             -- freier Text oder kompaktes JSON
                );
                CREATE INDEX IF NOT EXISTS idx_ev_ts ON events(ts);
                """
            )
            self._conn.commit()

    def emit(self, event: str, ticker: Optional[str] = None,
             detail: Optional[str] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, event, ticker, detail) VALUES (?,?,?,?)",
                (_now_iso(), event, ticker, detail),
            )
            # Gelegentliches Pruning (jede ~100. Zeile), hält die DB klein.
            row = self._conn.execute("SELECT MAX(id) AS m FROM events").fetchone()
            if row and row["m"] and row["m"] % 100 == 0:
                self._conn.execute(
                    "DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?",
                    (_FEED_KEEP,),
                )
            self._conn.commit()

    def recent(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()


_feed: Optional[ActivityFeed] = None
_feed_lock = threading.Lock()


def _get_feed() -> Optional[ActivityFeed]:
    global _feed
    with _feed_lock:
        if _feed is None:
            try:
                _feed = ActivityFeed()
            except Exception:
                return None
        return _feed


def feed_emit(event: str, ticker: Optional[str] = None,
              detail: Optional[str] = None) -> None:
    """Event in den Feed schreiben. Wirft nie."""
    try:
        f = _get_feed()
        if f is not None:
            f.emit(event, ticker=ticker, detail=detail)
    except Exception:
        pass


def feed_recent(limit: int = 50) -> List[Dict]:
    """Letzte Events (neueste zuerst). Wirft nie — leere Liste im Fehlerfall."""
    try:
        f = _get_feed()
        return f.recent(limit=limit) if f is not None else []
    except Exception:
        return []
