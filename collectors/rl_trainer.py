"""
RL Trainer – Hintergrund-Thread für periodisches Offline-Training des RLAgent

Läuft als Daemon-Thread und trainiert alle 24h (konfigurierbar) den RLAgent
auf der vollständigen Trade-History aus dem Trade-Journal.

Ablauf pro Trainings-Zyklus:
  1. Liest alle abgeschlossenen Trade-Paare (ENTRY + EXIT) aus der SQLite-DB
  2. Konvertiert jeden Trade in (RLState, reward) Tupel:
     - State: aus ENTRY-Feldern (sentiment, confidence, direction)
             + Schätzungen für nicht gespeicherte Features (VIX, momentum, velocity)
     - Reward: return_pct normalisiert + Fast-Win-Bonus + Stop-Loss-Malus
  3. Übergibt alle Tupel sequenziell an RLAgent.record_outcome()
  4. Loggt Fortschritt und Ergebnis-Statistik

Verwendung:
    from analyzers.rl_agent import RLAgent
    from collectors.rl_trainer import start_rl_trainer

    agent = RLAgent()
    start_rl_trainer(agent, "data/trade_journal.db", interval_hours=24)
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import List, Optional, Tuple

from logger import get_logger
from analyzers.rl_agent import RLAgent, RLState

log = get_logger(__name__)

# Standardpfad zur Journal-Datenbank (relativ zu Projektroot)
_DEFAULT_JOURNAL = os.path.join(os.path.dirname(__file__), "..", "data", "trade_journal.db")


# ── Trade-Lade- und Konvertierungs-Logik ──────────────────────────────────────

def _load_completed_trades(journal_path: str) -> List[Tuple[dict, dict]]:
    """
    Lädt alle abgeschlossenen Trade-Paare (ENTRY + EXIT) aus der SQLite-DB.

    Für jeden Ticker wird das jüngste ENTRY mit dem zugehörigen EXIT
    zusammengeführt. Offene Positionen (kein EXIT) werden übersprungen.

    Returns:
        Liste von (entry_dict, exit_dict) Tupeln – chronologisch sortiert.
    """
    pairs: List[Tuple[dict, dict]] = []
    try:
        conn = sqlite3.connect(journal_path)
        conn.row_factory = sqlite3.Row

        # Alle ENTRY-Events, nach Datum sortiert
        raw_entries = conn.execute(
            """SELECT * FROM trade_events
               WHERE event_type='ENTRY'
               ORDER BY event_date ASC"""
        ).fetchall()

        # Alle EXIT-Events, nach Datum sortiert
        raw_exits = conn.execute(
            """SELECT * FROM trade_events
               WHERE event_type='EXIT'
               ORDER BY event_date ASC"""
        ).fetchall()
        conn.close()

        # Entries in Dict: ticker → Liste von Entry-Dicts (kann mehrere Trades haben)
        entries_by_ticker: dict[str, List[dict]] = {}
        for row in raw_entries:
            d = dict(row)
            entries_by_ticker.setdefault(d["ticker"], []).append(d)

        # Exits mit passendem Entry matchen (FIFO per Ticker)
        used_indices: dict[str, int] = {}
        for exit_row in raw_exits:
            ex = dict(exit_row)
            ticker = ex["ticker"]
            entry_list = entries_by_ticker.get(ticker, [])
            idx = used_indices.get(ticker, 0)
            if idx < len(entry_list):
                pairs.append((entry_list[idx], ex))
                used_indices[ticker] = idx + 1

    except FileNotFoundError:
        log.warning("RLTrainer: Journal nicht gefunden: %s", journal_path)
    except sqlite3.Error as exc:
        log.warning("RLTrainer: DB-Fehler beim Laden: %s", exc)
    except Exception as exc:
        log.warning("RLTrainer: Unerwarteter Fehler beim Laden: %s", exc)

    return pairs


def _entry_to_state(entry: dict) -> RLState:
    """
    Konvertiert ein ENTRY-Dict in einen RLState.

    Direkt verfügbare Felder (aus dem Journal):
      - sentiment_score:     entry["sentiment"]
      - confidence_encoded:  entry["confidence"] → HIGH/MEDIUM/LOW → 1.0/0.5/0.0
      - regime_encoded:      aus entry["direction"] abgeleitet

    Schätzungen für nicht gespeicherte Felder:
      - vix_level:      0.3  (neutraler VIX-Level, ~15 normalisiert auf 50)
      - momentum_5d:    0.0  (kein Momentum im Journal gespeichert)
      - news_velocity:  0.5  (mittlere News-Aktivität als Annahme)

    Hinweis: Fehlende Features reduzieren die Lernqualität,
    sind aber besser als gar kein Training auf historischen Daten.
    """
    sentiment   = float(entry.get("sentiment") or 0.5)
    confidence  = str(entry.get("confidence") or "MEDIUM")
    direction   = str(entry.get("direction") or "NEUTRAL").upper()

    # Regime aus direction ableiten
    regime = "NEUTRAL"
    if "BULL" in direction or "UP" in direction or "LONG" in direction:
        regime = "BULL"
    elif "BEAR" in direction or "DOWN" in direction or "SHORT" in direction:
        regime = "BEAR"
    elif "CRISIS" in direction or "CRASH" in direction:
        regime = "CRISIS"

    return RLState(
        sentiment_score=    max(0.0, min(1.0, sentiment)),
        vix_level=          0.3,   # Schätzung: moderater VIX
        momentum_5d=        0.0,   # Schätzung: kein Momentum-Bias
        news_velocity=      0.5,   # Schätzung: mittlere News-Aktivität
        confidence_encoded= RLState.encode_confidence(confidence),
        regime_encoded=     RLState.encode_regime(regime),
    )


def _exit_to_reward(entry: dict, exit_ev: dict) -> float:
    """
    Berechnet den normalisierten Reward aus Exit-Daten.

    Verwendet RLAgent.compute_reward() mit:
      - return_pct:          exit["pnl_pct"] (direkter P&L in %)
      - hold_days:           exit["hold_days"]
      - planned_hold_days:   entry["hold_days"] (geplante Haltedauer)
      - stop_loss_triggered: True wenn "stop" in exit["reason"]

    Returns:
        Reward im Bereich [-1.0, +1.0]
    """
    pnl_pct        = float(exit_ev.get("pnl_pct") or 0.0)
    hold_days      = int(exit_ev.get("hold_days") or 0)
    planned_hold   = int(entry.get("hold_days") or 14)
    exit_reason    = str(exit_ev.get("reason") or "").lower()
    stop_triggered = "stop" in exit_reason

    return RLAgent.compute_reward(pnl_pct, hold_days, planned_hold, stop_triggered)


# ── Training-Zyklus ────────────────────────────────────────────────────────────

def run_training_cycle(rl_agent: RLAgent, journal_path: str) -> int:
    """
    Führt einen vollständigen Trainings-Zyklus durch.

    Lädt alle abgeschlossenen Trades, konvertiert sie in (state, reward) Tupel
    und trainiert den RLAgent. Loggt Fortschritt und Ergebnis-Statistik.

    Args:
        rl_agent:     RLAgent-Instanz die trainiert wird
        journal_path: Pfad zur trade_journal.db

    Returns:
        Anzahl der verarbeiteten Trades
    """
    log.info("RLTrainer: Starte Trainings-Zyklus auf %s", journal_path)

    pairs = _load_completed_trades(journal_path)
    if not pairs:
        log.info("RLTrainer: Keine abgeschlossenen Trades gefunden – Zyklus übersprungen")
        return 0

    log.info("RLTrainer: %d Trade-Paare geladen", len(pairs))

    trained       = 0
    total_reward  = 0.0
    wins          = 0
    losses        = 0

    for i, (entry, exit_ev) in enumerate(pairs, 1):
        try:
            state  = _entry_to_state(entry)
            reward = _exit_to_reward(entry, exit_ev)

            rl_agent.record_outcome(state, reward)
            total_reward += reward
            trained      += 1

            if reward > 0:
                wins += 1
            else:
                losses += 1

            # Fortschritts-Log alle 10 Trades
            if i % 10 == 0:
                log.debug("RLTrainer: %d/%d Trades verarbeitet ...", i, len(pairs))

        except Exception as exc:
            ticker = entry.get("ticker", "?")
            log.warning("RLTrainer: Fehler bei Trade %s (#%d): %s", ticker, i, exc)

    # Abschluss-Statistik
    avg_reward = total_reward / trained if trained > 0 else 0.0
    stats = rl_agent.get_stats()

    log.info(
        "RLTrainer: Zyklus abgeschlossen – %d Trades | avg_reward=%.4f | "
        "wins=%d losses=%d | agent_trades=%d | epsilon=%.2f",
        trained, avg_reward, wins, losses,
        stats.get("trades", 0), stats.get("epsilon", 0),
    )

    # Top-Features ausgeben
    top = stats.get("top_features", [])[:3]
    if top:
        log.info(
            "RLTrainer: Top-Features: %s",
            ", ".join(f"{name}={w:+.4f}" for name, w in top),
        )

    return trained


# ── Hintergrund-Thread ────────────────────────────────────────────────────────

def start_rl_trainer(
    rl_agent: RLAgent,
    journal_path: Optional[str] = None,
    interval_hours: int = 24,
) -> None:
    """
    Startet den RL-Training-Hintergrund-Thread.

    Der Thread läuft als Daemon (stirbt mit dem Hauptprozess) und führt
    alle `interval_hours` Stunden einen kompletten Trainings-Zyklus durch.
    Beim Start wird sofort ein erster Zyklus ausgeführt (kein initiales Warten).

    Args:
        rl_agent:       RLAgent-Instanz die trainiert werden soll
        journal_path:   Pfad zur trade_journal.db (None = Standardpfad)
        interval_hours: Trainings-Intervall in Stunden (Default: 24)

    Beispiel:
        agent = RLAgent()
        start_rl_trainer(agent, interval_hours=24)
        # Agent lernt jetzt täglich aus der Trade-History
    """
    resolved_path = journal_path or _DEFAULT_JOURNAL
    interval_secs = interval_hours * 3600

    def _loop() -> None:
        log.info(
            "RLTrainer: Hintergrund-Thread gestartet | Intervall=%dh | Journal=%s",
            interval_hours, resolved_path,
        )
        while True:
            try:
                run_training_cycle(rl_agent, resolved_path)
            except Exception as exc:
                log.error("RLTrainer: Unerwarteter Fehler im Trainings-Zyklus: %s", exc)

            log.debug("RLTrainer: Nächster Zyklus in %dh", interval_hours)
            time.sleep(interval_secs)

    thread = threading.Thread(target=_loop, name="rl-trainer", daemon=True)
    thread.start()
    log.info("RLTrainer: Daemon-Thread '%s' gestartet (id=%d)", thread.name, thread.ident or -1)
