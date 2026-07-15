"""
dashboard/factory/state.py — Datenmodell + Leser für die Fabrik-Szene
(Vision W1.1, docs/DESIGN_FABRIK.md).

Elf Maschinen, elf Leser (`_read_<id>`), jeder einzeln fail-open: eine
fehlende/kaputte Datenquelle darf NIE die ganze Szene zum Absturz bringen —
sie liefert dann nur Status "off" statt einer Exception. Kein Leser macht
Netzwerk-Calls, einzige Ausnahme ist der 0.4s-Gateway-Socket-Check (wie die
bestehende Gesundheits-Ampel, Roadmap 1.5d).

Wachstums-Regel (siehe auch machines.py): eine neue Bot-Funktion bekommt
künftig "ihre Maschine" — neuer Leser hier, Platz in scene.LAYOUT, Box in
machines.py, Tooltip, Test.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

MACHINE_IDS = (
    "docks", "analyzer_claude", "analyzer_ollama", "conveyor", "warehouse",
    "breaker", "gate", "weather", "lab", "backup_bot", "clock",
)

_BACKUPS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backups")
_REGIME_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "current_regime.json")


@dataclass
class MachineState:
    id: str
    label: str
    status: str  # ok | warn | err | off | active
    tooltip: List[str] = field(default_factory=list)
    payload: dict = field(default_factory=dict)


@dataclass
class FactoryState:
    machines: Dict[str, MachineState]
    paused: bool
    generated_at: str


def _off(machine_id: str, label: str, reason: str = "keine Daten") -> MachineState:
    return MachineState(id=machine_id, label=label, status="off", tooltip=[reason])


def _read_docks() -> MachineState:
    try:
        from analyzers.analysis_log import AnalysisLog
        h = AnalysisLog().source_health()
        if h.get("healthy"):
            status = "ok"
        elif h.get("weak"):
            status = "warn"
        else:
            status = "off"
        tooltip = [
            f"gesund: {len(h.get('healthy') or [])}",
            f"schwach: {len(h.get('weak') or [])}",
            f"tot: {len(h.get('dead') or [])}",
        ]
        return MachineState(id="docks", label="Laderampen", status=status,
                            tooltip=tooltip, payload=h)
    except Exception:
        return _off("docks", "Laderampen")


def _analyzer_share(prefix: str) -> MachineState:
    machine_id = f"analyzer_{prefix.rstrip('_')}"
    label = "Claude-Analysator" if prefix == "claude" else "Ollama-Werkbank"
    try:
        from analyzers.analysis_log import AnalysisLog
        rows = AnalysisLog().get_recent(limit=50)
        n = sum(
            1 for r in rows
            if str((r.get("provenance") or {}).get("model_route") or "").startswith(prefix)
        )
        status = "active" if n > 0 else "off"
        return MachineState(id=machine_id, label=label, status=status,
                            tooltip=[f"{n}/{len(rows)} der letzten Analysen"])
    except Exception:
        return _off(machine_id, label)


def _read_analyzer_claude() -> MachineState:
    return _analyzer_share("claude")


def _read_analyzer_ollama() -> MachineState:
    return _analyzer_share("ollama")


def _read_conveyor() -> MachineState:
    try:
        from analyzers.decision_log import DecisionLog
        today = datetime.now(timezone.utc).date().isoformat()
        funnel = DecisionLog().funnel(today)
        status = "active" if funnel.get("total") else "off"
        return MachineState(
            id="conveyor", label="Förderband", status=status,
            tooltip=[f"heute: {funnel.get('total', 0)} Entscheidungen"],
            payload=funnel,
        )
    except Exception:
        return _off("conveyor", "Förderband")


def _read_warehouse() -> MachineState:
    try:
        from portfolio.portfolio import Portfolio
        positions = Portfolio().all_positions()
        status = "ok" if positions else "off"
        tooltip = [f"{t}: {p.shares:g} Stk." for t, p in positions.items()] or ["keine Positionen"]
        return MachineState(
            id="warehouse", label="Hochregallager", status=status,
            tooltip=tooltip,
            payload={t: p.shares for t, p in positions.items()},
        )
    except Exception:
        return _off("warehouse", "Hochregallager")


def _read_breaker() -> MachineState:
    try:
        from portfolio.portfolio import Portfolio
        from portfolio.circuit_breaker import CircuitBreaker
        port = Portfolio()
        positions = port.all_positions()
        total = port.cash + sum(p.shares * p.entry_price for p in positions.values())
        cb = CircuitBreaker().status(total)
        triggered = bool(cb.get("triggered"))
        return MachineState(
            id="breaker", label="Not-Aus", status="err" if triggered else "ok",
            tooltip=[f"Tagesverlust: {cb.get('daily_pct', 0):+.1f}%",
                    f"Drawdown: {cb.get('drawdown_pct', 0):.1f}%"],
            payload=cb,
        )
    except Exception:
        return _off("breaker", "Not-Aus")


def _read_gate() -> MachineState:
    import socket
    try:
        from config import config
        with socket.create_connection((config.ibkr_host, config.ibkr_port), timeout=0.4):
            return MachineState(id="gate", label="Verladetor", status="ok",
                                tooltip=["IB-Gateway erreichbar"])
    except Exception:
        return MachineState(id="gate", label="Verladetor", status="err",
                            tooltip=["IB-Gateway nicht erreichbar"])


def _read_weather() -> MachineState:
    try:
        with open(_REGIME_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        regime = str(data.get("regime") or "").upper()
        status = {"BULL": "ok", "NEUTRAL": "warn"}.get(regime, "err" if regime else "off")
        return MachineState(id="weather", label="Wetterstation", status=status,
                            tooltip=[f"Regime: {regime or 'unbekannt'}"],
                            payload={"regime": regime})
    except Exception:
        return _off("weather", "Wetterstation")


def _read_lab() -> MachineState:
    try:
        from analyzers.experience_store import ExperienceStore
        store = ExperienceStore()
        s = store.stats()
        store.close()
        labeled = s.get("labeled") or 0
        return MachineState(
            id="lab", label="Qualitätslabor", status="ok" if labeled else "off",
            tooltip=[f"gelabelt: {labeled}", f"Gewinne: {s.get('wins') or 0}",
                    f"Verluste: {s.get('losses') or 0}"],
            payload=s,
        )
    except Exception:
        return _off("lab", "Qualitätslabor")


def _read_backup_bot() -> MachineState:
    try:
        files = [f for f in os.listdir(_BACKUPS_DIR) if f.endswith(".tar.gz")]
        if not files:
            return _off("backup_bot", "Nachtschicht-Roboter", "noch kein Backup")
        newest = max(
            (os.path.join(_BACKUPS_DIR, f) for f in files),
            key=os.path.getmtime,
        )
        age_h = (datetime.now().timestamp() - os.path.getmtime(newest)) / 3600
        status = "ok" if age_h < 36 else "warn" if age_h < 24 * 8 else "err"
        return MachineState(
            id="backup_bot", label="Nachtschicht-Roboter", status=status,
            tooltip=[f"letztes Backup: vor {age_h:.0f}h"],
            payload={"age_hours": round(age_h, 1)},
        )
    except Exception:
        return _off("backup_bot", "Nachtschicht-Roboter")


def _read_clock() -> MachineState:
    try:
        from system.live_status import read_status
        s = read_status() or {}
        state = s.get("state")
        if state == "cycle":
            status = "active"
        elif state == "idle" and s.get("next_run"):
            status = "ok"
        else:
            status = "off"
        tooltip = [f"Phase: {s.get('phase') or '–'}"]
        if s.get("next_run"):
            tooltip.append(f"nächster Lauf: {s['next_run'][:16].replace('T', ' ')}")
        return MachineState(id="clock", label="Werksuhr", status=status,
                            tooltip=tooltip, payload=s)
    except Exception:
        return _off("clock", "Werksuhr")


_READERS = {
    "docks": _read_docks,
    "analyzer_claude": _read_analyzer_claude,
    "analyzer_ollama": _read_analyzer_ollama,
    "conveyor": _read_conveyor,
    "warehouse": _read_warehouse,
    "breaker": _read_breaker,
    "gate": _read_gate,
    "weather": _read_weather,
    "lab": _read_lab,
    "backup_bot": _read_backup_bot,
    "clock": _read_clock,
}


def read_state() -> FactoryState:
    """Baut den kompletten Fabrik-Zustand. Jeder Leser ist bereits selbst
    fail-open; dieses zusätzliche try/except fängt auch noch den Fall ab,
    dass ein Leser selbst durch Monkeypatch/Bug zur Exception wird, statt
    nur ein MachineState mit status="off" zurückzugeben."""
    machines: Dict[str, MachineState] = {}
    for machine_id, reader in _READERS.items():
        try:
            machines[machine_id] = reader()
        except Exception:
            machines[machine_id] = _off(machine_id, machine_id)

    try:
        from system.bot_control import is_paused
        paused = is_paused()
    except Exception:
        paused = False

    return FactoryState(
        machines=machines,
        paused=paused,
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    )
