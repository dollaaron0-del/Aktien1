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
    "breaker", "gate", "weather", "lab", "backup_bot", "clock", "control_room",
    # Stufe 3 (24.7.2026): granulare Entscheidungs-Ketten-Maschinen, je an eine
    # echte Datenquelle gebunden (Decision-Funnel / Portfolio / User-Queue).
    "risk_check", "position_limit", "ausschuss", "queue",
    # Stufe 4 (24.7.2026): restliche Ketten-Stationen der freigegebenen
    # Baumstruktur (Daten-Kontrolle → Katalysator-Weiche → Bestands-/Signal-Prüfung).
    "data_gate", "catalyst_check", "position_check", "signal_check",
)

# H2.2: History-Zeilen (snapshot()) speichern bewusst KEIN Label (klein
# halten) — für die Zeitreise-Rekonstruktion (reconstruct_from_snapshot)
# ist das hier die einzige Quelle. MUSS mit den Labels in den _read_*()-
# Funktionen unten übereinstimmen.
MACHINE_LABELS: Dict[str, str] = {
    "docks": "Laderampen",
    "analyzer_claude": "Claude-Analysator",
    "analyzer_ollama": "Ollama-Werkbank",
    "conveyor": "Förderband",
    "warehouse": "Hochregallager",
    "breaker": "Not-Aus",
    "gate": "Verladetor",
    "weather": "Wetterstation",
    "lab": "Qualitätslabor",
    "backup_bot": "Nachtschicht-Roboter",
    "clock": "Werksuhr",
    "control_room": "Kontrollraum",
    "risk_check": "Risiko-Kontrolle",
    "position_limit": "Positions-Limit",
    "ausschuss": "Ausschuss-Sammelstelle",
    "queue": "Signal-Warteschlange",
    "data_gate": "Daten-Kontrolle",
    "catalyst_check": "Katalysator-Weiche",
    "position_check": "Bestands-Prüfung",
    "signal_check": "Signal-Prüfung",
}

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
_BACKUPS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "backups")
_REGIME_FILE = os.path.join(_DATA_DIR, "current_regime.json")
_EONET_FILE = os.path.join(_DATA_DIR, "eonet_hazards.json")
_THESIS_REGISTRY_FILE = os.path.join(_DATA_DIR, "thesis_registry.json")
_WEATHER_MACRO_FILE = os.path.join(_DATA_DIR, "weather_macro.json")

# H2.1: Grundlage für Zeitreise/Replay (H2.2/H2.3) — regelmäßige, schlanke
# Schnappschüsse des Fabrik-Zustands (ohne payload, um die Datei klein zu
# halten). Modul-Konstante statt in einer Funktion gebunden, damit Tests
# sie per monkeypatch umbiegen können (Muster _REGIME_FILE).
HISTORY_FILE = os.path.join(_DATA_DIR, "factory_history.jsonl")
_HISTORY_MAX_BYTES = 5 * 1024 * 1024

# Vision W4.4: "Backup heute Nacht gelaufen" gilt bis zu dieser Alters-
# schwelle (Stunden) als frisch — großzügig genug, um den 03:00-Timer
# tagsüber noch als "letzte Nacht" zu zählen.
_BACKUP_FRESH_HOURS = 15


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
    # Vision W4: Requisiten für die Entdeckungs-Ebene, an ECHTE Zustände
    # gebunden statt Zufalls-Deko. events = boolesche Flags (scene_events()
    # entscheidet daraus, was gezeigt wird); weather_demand_label ist
    # kategorial (ELEVATED/SUBDUED/NORMAL, W4.3).
    events: Dict[str, bool] = field(default_factory=dict)
    weather_demand_label: str = ""


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
        routes = [str((r.get("provenance") or {}).get("model_route") or "") for r in rows]
        n = sum(1 for r in routes if r.startswith(prefix))
        status = "active" if n > 0 else "off"
        # L1.5-Folge (Fabrik-Detail-Panel): der EXAKTE model_route
        # (z.B. ollama_frugal_full vs. claude_dedup_cache) statt nur des
        # Präfix-Anteils — beantwortet "WARUM lief das auf diesem Pfad?"
        # nicht nur "WIE VIEL". Nur die zu diesem Analysator gehörenden
        # Routen (startswith prefix), sonst leer statt irreführender {}.
        breakdown: Dict[str, int] = {}
        for r in routes:
            if r.startswith(prefix):
                breakdown[r] = breakdown.get(r, 0) + 1
        # payload für D7.2: Rauch-Intensität in der Szene = echter
        # Routing-Anteil (machines.py skaliert die Anzahl Rauchwolken).
        return MachineState(id=machine_id, label=label, status=status,
                            tooltip=[f"{n}/{len(rows)} der letzten Analysen"],
                            payload={"n": n, "total": len(rows), "route_breakdown": breakdown})
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


def _warehouse_movements(limit: int = 5) -> Dict:
    """L3.6: Bestandsveränderungen aus der echten `trades`-Tabelle
    (portfolio.db). Eigene READ-ONLY sqlite3-Verbindung (Muster
    dashboard/genealogy.py) — die Portfolio-Klasse hat keine
    Trade-Historie-Schnittstelle, und ein zweiter Schreibpfad hierher
    wäre ohnehin falsch.

    Liefert `{"in_today", "out_today", "recent": [{ts, ticker, action,
    shares}]}`. Netzfrei, fail-open (leere Bewegung statt Exception).
    Der Tages-Schnitt läuft über lokale Kalendertage (die timestamps
    der Tabelle sind naive lokale ISO-Strings)."""
    out = {"in_today": 0, "out_today": 0, "recent": []}
    try:
        import sqlite3

        from portfolio.portfolio import PORTFOLIO_DB
        today = datetime.now().date().isoformat()
        conn = sqlite3.connect(PORTFOLIO_DB)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute(
                "SELECT action, COUNT(*) n FROM trades "
                "WHERE substr(timestamp,1,10)=? GROUP BY action", (today,)
            ):
                if r["action"] == "BUY":
                    out["in_today"] = int(r["n"])
                elif r["action"] == "SELL":
                    out["out_today"] = int(r["n"])
            out["recent"] = [
                {"ts": r["timestamp"], "ticker": r["ticker"],
                 "action": r["action"], "shares": r["shares"]}
                for r in conn.execute(
                    "SELECT timestamp, ticker, action, shares FROM trades "
                    "ORDER BY timestamp DESC LIMIT ?", (limit,)
                )
            ]
        finally:
            conn.close()
    except Exception:
        return {"in_today": 0, "out_today": 0, "recent": []}
    return out


def _read_warehouse() -> MachineState:
    try:
        from portfolio.portfolio import Portfolio
        positions = Portfolio().all_positions()
        status = "ok" if positions else "off"
        tooltip = [f"{t}: {p.shares:g} Stk." for t, p in positions.items()] or ["keine Positionen"]
        # D7.2: eine Kiste je Position in der Szene, Farbe nach
        # Haltedauer-Ratio (gleiche Logik wie der Portfolio-Tab: grün <0.8,
        # amber <1.0, rot ab Zielüberschreitung). Kein Live-Kurs-Abruf hier —
        # read_state() muss schnell und netzwerkfrei bleiben, daher Alter
        # statt P&L als Kisten-Signal.
        details = {}
        for t, p in positions.items():
            age_ratio = None
            try:
                days = (datetime.now(timezone.utc).replace(tzinfo=None)
                        - datetime.fromisoformat(p.entry_date)).days
                age_ratio = round(days / max(p.target_hold_days, 1), 2)
            except Exception:
                pass
            details[t] = {"shares": p.shares, "age_ratio": age_ratio}
        # L3.6: Zu-/Abgänge des Tages — der Bestand allein zeigt nicht,
        # ob sich heute etwas BEWEGT hat.
        moves = _warehouse_movements()
        tooltip.append(
            f"heute: +{moves['in_today']} rein / -{moves['out_today']} raus"
        )
        return MachineState(
            id="warehouse", label="Hochregallager", status=status,
            tooltip=tooltip,
            payload={"positions": details, "movements": moves},
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
        host, port = config.ibkr_host, config.ibkr_port
    except Exception:
        host, port = None, None
    payload = {"host": host, "port": port}
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return MachineState(id="gate", label="Verladetor", status="ok",
                                tooltip=["IB-Gateway erreichbar"], payload=payload)
    except Exception:
        return MachineState(id="gate", label="Verladetor", status="err",
                            tooltip=["IB-Gateway nicht erreichbar"], payload=payload)


def _read_weather() -> MachineState:
    try:
        with open(_REGIME_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        regime = str(data.get("regime") or "").upper()
        status = {"BULL": "ok", "NEUTRAL": "warn"}.get(regime, "err" if regime else "off")
        # Detail-Panel-Ausbau: Nachfrage-Label (dieselbe Quelle wie das
        # W4.3-Regen/Sonne-Overlay, hier zusätzlich als Tabellenzeile statt
        # nur als Szenen-Effekt) + Zeitstempel des Regime-Standes.
        return MachineState(id="weather", label="Wetterstation", status=status,
                            tooltip=[f"Regime: {regime or 'unbekannt'}"],
                            payload={"regime": regime,
                                    "demand_label": _read_weather_demand_label(),
                                    "timestamp": data.get("timestamp")})
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


def _read_backup_bot(recent_limit: int = 5) -> MachineState:
    try:
        paths = [os.path.join(_BACKUPS_DIR, f) for f in os.listdir(_BACKUPS_DIR)
                if f.endswith(".tar.gz")]
        if not paths:
            return _off("backup_bot", "Nachtschicht-Roboter", "noch kein Backup")
        now = datetime.now().timestamp()
        paths.sort(key=os.path.getmtime, reverse=True)
        newest = paths[0]
        age_h = (now - os.path.getmtime(newest)) / 3600
        status = "ok" if age_h < 36 else "warn" if age_h < 24 * 8 else "err"
        # Detail-Panel-Ausbau: die letzten Backups als kleine Liste
        # (Name/Alter/Größe) statt nur der einen Altersangabe des jüngsten.
        recent = [
            {"name": os.path.basename(p),
            "age_hours": round((now - os.path.getmtime(p)) / 3600, 1),
            "size_mb": round(os.path.getsize(p) / (1024 * 1024), 1)}
            for p in paths[:recent_limit]
        ]
        return MachineState(
            id="backup_bot", label="Nachtschicht-Roboter", status=status,
            tooltip=[f"letztes Backup: vor {age_h:.0f}h"],
            payload={"age_hours": round(age_h, 1), "recent": recent},
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


def _read_control_room() -> MachineState:
    """18.7.2026 (Karten-Umbau): Verwaltung/Einstellungen bekommt eine
    eigene Maschine — das frühere Einstellungen-Tab lebt jetzt als
    Detail-Panel hier (dashboard/settings_panel.py). Status spiegelt
    echte Absicherungs-Lücken (kein erfundener Wert): fehlendes
    Dashboard-Passwort ist eine bewusste, dokumentierte Härtungslücke
    (dashboard/auth.py) — "warn" macht sie in der Szene sichtbar statt
    sie nur im Code-Kommentar stehen zu lassen."""
    try:
        password_set = bool(os.getenv("DASHBOARD_PASSWORD", "").strip())
        from config import config as _cfg
        api_key_set = bool(getattr(_cfg, "anthropic_api_key", ""))
        status = "ok" if password_set else "warn"
        tooltip = [
            f"Dashboard-Passwort: {'gesetzt' if password_set else 'NICHT gesetzt'}",
            f"Anthropic-Key: {'gesetzt' if api_key_set else 'fehlt'}",
            f"Broker-Modus: {getattr(_cfg, 'broker_mode', '–')}",
        ]
        return MachineState(
            id="control_room", label="Kontrollraum", status=status,
            tooltip=tooltip,
            payload={"password_set": password_set, "api_key_set": api_key_set,
                     "broker_mode": getattr(_cfg, "broker_mode", "")},
        )
    except Exception:
        return _off("control_room", "Kontrollraum")


# ── Stufe 3 (24.7.2026): granulare Entscheidungs-Ketten-Maschinen ───────────
# Alle netzfrei + fail-open, gebunden an den echten Decision-Funnel
# (analyzers.decision_log), das Portfolio und die User-Analyse-Queue.
_RISK_BUCKETS = ("korrelation", "positionsgroesse", "liquiditaet", "tagesverlust")


def _funnel_today() -> dict:
    from analyzers.decision_log import DecisionLog
    today = datetime.now(timezone.utc).date().isoformat()
    return DecisionLog().funnel(today)


def _read_risk_check() -> MachineState:
    """Risiko-Kontrolle — heutige risiko-bedingte Ablehnungen aus dem
    Decision-Funnel (Korrelation/Positionsgröße/Liquidität/Tagesverlust)."""
    try:
        f = _funnel_today()
        sr = f.get("skip_reasons") or {}
        rejected = sum(int(sr.get(b, 0)) for b in _RISK_BUCKETS)
        status = "ok" if f.get("total") else "off"
        return MachineState(
            id="risk_check", label="Risiko-Kontrolle", status=status,
            tooltip=[f"heute {rejected} Risiko-Ablehnungen"],
            payload={"rejected": rejected, "total": f.get("total", 0),
                     "reasons": {b: int(sr.get(b, 0)) for b in _RISK_BUCKETS if sr.get(b)}},
        )
    except Exception:
        return _off("risk_check", "Risiko-Kontrolle")


def _read_position_limit() -> MachineState:
    """Positions-Limit — offene Positionen + heutige "Max Positionen"-
    Abweisungen aus dem Funnel; voll = es gab heute solche Abweisungen."""
    try:
        from portfolio.portfolio import Portfolio
        open_n = len(Portfolio().all_positions())
        f = _funnel_today()
        full_hits = int((f.get("skip_reasons") or {}).get("max_positionen", 0))
        status = "warn" if full_hits else ("ok" if f.get("total") else "off")
        return MachineState(
            id="position_limit", label="Positions-Limit", status=status,
            tooltip=[f"offen: {open_n}", f"heute {full_hits}× voll abgewiesen"],
            payload={"open": open_n, "full_hits": full_hits},
        )
    except Exception:
        return _off("position_limit", "Positions-Limit")


def _read_ausschuss() -> MachineState:
    """Ausschuss-Sammelstelle — heute abgelehnte (SKIP) Kisten + Grund-
    Aufschlüsselung aus dem Decision-Funnel."""
    try:
        f = _funnel_today()
        rejected = int((f.get("actions") or {}).get("SKIP", 0))
        status = "ok" if f.get("total") else "off"
        return MachineState(
            id="ausschuss", label="Ausschuss-Sammelstelle", status=status,
            tooltip=[f"heute {rejected} abgelehnt"],
            payload={"rejected": rejected, "reasons": f.get("skip_reasons") or {}},
        )
    except Exception:
        return _off("ausschuss", "Ausschuss-Sammelstelle")


def _read_queue() -> MachineState:
    """Signal-Warteschlange — vorgemerkte Ticker in der User-Analyse-Queue
    (analyzers.user_request_queue), die auf den nächsten Zyklus warten."""
    try:
        from analyzers.user_request_queue import peek
        waiting = [str(t) for t in (peek() or [])]
        status = "warn" if waiting else "off"
        return MachineState(
            id="queue", label="Signal-Warteschlange", status=status,
            tooltip=[f"wartend: {len(waiting)}"] + waiting[:6],
            payload={"waiting": waiting, "count": len(waiting)},
        )
    except Exception:
        return _off("queue", "Signal-Warteschlange")


# ── Stufe 4 (24.7.2026): fehlende Ketten-Stationen der freigegebenen
# Baumstruktur (Daten-Kontrolle → Katalysator-Weiche → … → Signal-Prüfung).
# Ebenfalls netzfrei + fail-open, an denselben Decision-Funnel / Routing /
# Portfolio gebunden wie die übrigen. ───────────────────────────────────────
_DATA_BUCKETS = ("daten_gate", "kein_kurs", "zu_wenige_quellen")
_SIGNAL_BUCKETS = ("kein_kaufsignal", "unter_schwelle")


def _read_data_gate() -> MachineState:
    """Daten-Kontrolle — heute wegen schlechter/fehlender Daten abgewiesen
    (Daten-Gate / kein Kurs / zu wenige Quellen aus dem Decision-Funnel)."""
    try:
        f = _funnel_today()
        sr = f.get("skip_reasons") or {}
        rejected = sum(int(sr.get(b, 0)) for b in _DATA_BUCKETS)
        status = "ok" if f.get("total") else "off"
        return MachineState(
            id="data_gate", label="Daten-Kontrolle", status=status,
            tooltip=[f"heute {rejected} Datenfehler abgewiesen"],
            payload={"rejected": rejected, "total": f.get("total", 0),
                     "reasons": {b: int(sr.get(b, 0)) for b in _DATA_BUCKETS if sr.get(b)}},
        )
    except Exception:
        return _off("data_gate", "Daten-Kontrolle")


def _read_catalyst_check() -> MachineState:
    """Katalysator-Weiche — Routing-Split der letzten Analysen: Ollama
    (Standard) vs. Claude (Katalysator/force_claude). Zahlen wie die
    Analysator-Leser, hier als Weiche/Verteiler zusammengefasst."""
    try:
        from analyzers.analysis_log import AnalysisLog
        rows = AnalysisLog().get_recent(limit=50)
        routes = [str((r.get("provenance") or {}).get("model_route") or "") for r in rows]
        claude_n = sum(1 for r in routes if r.startswith("claude"))
        ollama_n = sum(1 for r in routes if r.startswith("ollama"))
        status = "active" if (claude_n or ollama_n) else "off"
        return MachineState(
            id="catalyst_check", label="Katalysator-Weiche", status=status,
            tooltip=[f"Claude {claude_n} / Ollama {ollama_n} (letzte {len(rows)})"],
            payload={"claude_n": claude_n, "ollama_n": ollama_n, "total": len(rows)},
        )
    except Exception:
        return _off("catalyst_check", "Katalysator-Weiche")


def _read_position_check() -> MachineState:
    """Bestands-Prüfung — liest das Lager (offene Positionen); markiert, was
    schon im Depot ist (kein Nachkauf). Gehaltene Ticker = held-Set."""
    try:
        from portfolio.portfolio import Portfolio
        held = sorted(Portfolio().all_positions().keys())
        status = "ok" if held else "off"
        return MachineState(
            id="position_check", label="Bestands-Prüfung", status=status,
            tooltip=[f"im Depot: {len(held)}"] + held[:6],
            payload={"held": held, "n": len(held)},
        )
    except Exception:
        return _off("position_check", "Bestands-Prüfung")


def _read_signal_check() -> MachineState:
    """Signal-Prüfung — heute wegen zu schwachem Score abgewiesen (unter
    Kaufschwelle / kein Kaufsignal aus dem Decision-Funnel)."""
    try:
        f = _funnel_today()
        sr = f.get("skip_reasons") or {}
        rejected = sum(int(sr.get(b, 0)) for b in _SIGNAL_BUCKETS)
        status = "ok" if f.get("total") else "off"
        return MachineState(
            id="signal_check", label="Signal-Prüfung", status=status,
            tooltip=[f"heute {rejected}× unter Schwelle"],
            payload={"rejected": rejected, "total": f.get("total", 0),
                     "reasons": {b: int(sr.get(b, 0)) for b in _SIGNAL_BUCKETS if sr.get(b)}},
        )
    except Exception:
        return _off("signal_check", "Signal-Prüfung")


# ── Entdeckungs-Ebene (Vision W4) — jede Requisite an einen echten Zustand
# gebunden, kein Zufalls-Deko-Generator. Jeder Leser einzeln fail-open. ────

def _hazard_active() -> bool:
    try:
        with open(_EONET_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return (data.get("data") or {}).get("hazard_label") == "ELEVATED"
    except Exception:
        return False


def _sl_cooldown_active() -> bool:
    """Nutzt StopLossCooldown.all_blocked() (bewusst NICHT die Rohdatei
    direkt lesen) — die Methode ignoriert bereits abgelaufene Sperren. Ein
    naives "Datei nicht leer" hätte hier einen echten Bug erzeugt: ein
    Cooldown-Eintrag von vor über einem Monat (in der echten
    data/sl_cooldown.json vorgefunden) ist längst abgelaufen, stand aber
    noch als Zeile in der Datei. `all_blocked()` liefert trotzdem IMMER das
    korrekte Ergebnis (geprüft) — ein Nebenbefund beim End-to-End-Check
    gegen echte Daten: die dortige Selbstbereinigung der Datei greift wegen
    eines Zähl-Bugs in sl_cooldown.py selbst nie wirklich (data.pop()
    passiert vor dem len(active)<len(data)-Vergleich, der danach immer
    False ist). Betrifft nur Datei-Hygiene, nicht die Korrektheit hier —
    bewusst NICHT gefixt (außerhalb der dashboard/-Grenze dieser
    Design-Session, siehe docs/DESIGN_ROADMAP.md Arbeitsprotokoll)."""
    try:
        from analyzers.sl_cooldown import StopLossCooldown
        return bool(StopLossCooldown().all_blocked())
    except Exception:
        return False


def _thesis_proven() -> bool:
    try:
        with open(_THESIS_REGISTRY_FILE, encoding="utf-8") as fh:
            registry = json.load(fh)
        return any(t.get("status") == "PROVEN" for t in registry.values())
    except Exception:
        return False


def _first_live_trade_exists() -> bool:
    try:
        from analyzers.experience_store import ExperienceStore
        store = ExperienceStore()
        s = store.stats()
        store.close()
        return bool(s.get("live"))
    except Exception:
        return False


def _backup_ran_recently() -> bool:
    try:
        files = [f for f in os.listdir(_BACKUPS_DIR) if f.endswith(".tar.gz")]
        if not files:
            return False
        newest = max((os.path.join(_BACKUPS_DIR, f) for f in files), key=os.path.getmtime)
        age_h = (datetime.now().timestamp() - os.path.getmtime(newest)) / 3600
        return age_h < _BACKUP_FRESH_HOURS
    except Exception:
        return False


def _read_events() -> Dict[str, bool]:
    """Sammelt alle W4-Ereignis-Flags. Fail-open pro Flag (siehe die
    einzelnen `_..._active()`/`_..._exists()`-Helfer oben) plus dieses
    äußere try/except als zweite Sicherheitsnetz-Schicht, analog
    read_state()."""
    try:
        return {
            "hazard_active": _hazard_active(),
            "sl_cooldown_active": _sl_cooldown_active(),
            "thesis_proven": _thesis_proven(),
            "first_live_trade": _first_live_trade_exists(),
            "backup_ran_recently": _backup_ran_recently(),
        }
    except Exception:
        return {}


def _read_weather_demand_label() -> str:
    try:
        with open(_WEATHER_MACRO_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return str((data.get("data") or {}).get("demand_label") or "")
    except Exception:
        return ""


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
    "control_room": _read_control_room,
    "risk_check": _read_risk_check,
    "position_limit": _read_position_limit,
    "ausschuss": _read_ausschuss,
    "queue": _read_queue,
    "data_gate": _read_data_gate,
    "catalyst_check": _read_catalyst_check,
    "position_check": _read_position_check,
    "signal_check": _read_signal_check,
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
        events=_read_events(),
        weather_demand_label=_read_weather_demand_label(),
    )


def _cap_history_file(path: str) -> None:
    """Wirft die ältere Hälfte der Zeilen weg, sobald die Datei
    `_HISTORY_MAX_BYTES` überschreitet — hält die Historie unbegrenzt
    lange nutzbar statt unbegrenzt zu wachsen. Fail-open."""
    try:
        if os.path.getsize(path) <= _HISTORY_MAX_BYTES:
            return
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        keep = lines[len(lines) // 2:]
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
    except Exception:
        pass


def snapshot(state: FactoryState, path: str | None = None) -> None:
    """H2.1: hängt eine schlanke JSON-Zeile des Fabrik-Zustands an
    `HISTORY_FILE` an — Grundlage für Zeitreise/Replay (H2.2/H2.3).
    Bewusst OHNE payload (nur status+tooltip je Maschine), damit die
    Datei klein bleibt. Fail-open: ein Schreibfehler darf die Fabrik-
    Anzeige nie stören."""
    target = path or HISTORY_FILE
    try:
        row = {
            "ts": state.generated_at,
            "paused": state.paused,
            "machines": {
                mid: {"status": m.status, "tooltip": list(m.tooltip)}
                for mid, m in state.machines.items()
            },
        }
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        _cap_history_file(target)
    except Exception:
        pass


def reconstruct_from_snapshot(row: dict) -> FactoryState:
    """H2.2: baut aus einer history-Zeile (snapshot()) einen renderbaren
    FactoryState. Payload-lose Maschinen sind by design in Ordnung —
    machines.py/scene.py rendern Extras/Events fail-open, eine Archiv-
    Ansicht ohne Zähler/Kisten ist also korrekt, kein Bug. `events`/
    `weather_demand_label` bleiben Default (leer) — die history speichert
    bewusst nur Status+Tooltip je Maschine."""
    machines: Dict[str, MachineState] = {}
    for mid, info in (row.get("machines") or {}).items():
        info = info or {}
        machines[mid] = MachineState(
            id=mid,
            label=MACHINE_LABELS.get(mid, mid),
            status=str(info.get("status") or "off"),
            tooltip=list(info.get("tooltip") or []),
        )
    return FactoryState(
        machines=machines,
        paused=bool(row.get("paused")),
        generated_at=str(row.get("ts") or ""),
    )


def read_feed_events_until(day: str, ts: str, db_path: str | None = None) -> List[dict]:
    """H2.3 (Tages-Replay): Feed-Ereignisse eines Tages bis zu einem
    Zeitpunkt, read-only direkt aus der Activity-Feed-DB
    (system/live_status.py) gelesen — `feed_recent()` liefert nur die
    letzten 50 Einträge, für einen ganzen Handelstag reicht das nicht.
    Älteste zuerst. Fail-open: fehlende/kaputte DB → leere Liste."""
    import sqlite3

    from system.live_status import FEED_PATH
    path = db_path or FEED_PATH
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT ts, event, ticker, detail FROM events "
                "WHERE ts LIKE ? AND ts <= ? ORDER BY ts ASC",
                (f"{day}%", ts),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def read_history(day: str, path: str | None = None) -> List[dict]:
    """H2.1: alle Schnappschüsse eines Tages (`day` = "YYYY-MM-DD"),
    älteste zuerst. Kaputte/unlesbare Zeilen werden übersprungen statt
    die ganze Historie zu verwerfen. Fehlt die Datei: leere Liste."""
    target = path or HISTORY_FILE
    rows: List[dict] = []
    try:
        with open(target, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("ts") or "").startswith(day):
                    rows.append(row)
    except Exception:
        return []
    return rows
