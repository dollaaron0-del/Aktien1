"""
dashboard/controls.py — Bedien-Logik des Leitstands (Ausbau-Roadmap H1).

Trennt die AKTIONEN (Pause umschalten, Not-Aus zurücksetzen) von ihrer
Darstellung: hier steht nur, was passiert — die Streamlit-Knöpfe/
Bestätigungen liegen im jeweiligen Tab. Dadurch sind die Aktionen ohne
Streamlit-Mechanik testbar.

Grundregeln aus der H1-Kopfzeile der Roadmap (gelten für JEDE Aktion):
Bestätigung im UI, Protokoll in den Activity-Feed, und klar erkennbar,
dass es eine MANUELLE Dashboard-Aktion war (nicht der Bot selbst) — jeder
Feed-Eintrag hier trägt darum den Präfix „[manuell]".

WICHTIG (Ehrlichkeit, H1.1): Der Pause-Flag (`system/bot_control.py`) und
der systemd-Dienst sind ZWEI UNABHÄNGIGE Ebenen. Der Flag steuert nur, ob
die laufende Hauptschleife Jobs ausführt. Ist der Dienst gestoppt/disabled
(aktueller Stand seit 21.6.2026), startet das Aufheben der Pause den Bot
NICHT — dafür braucht es zusätzlich `systemctl start/enable`, das bewusst
NUR der User selbst ausführt. `service_state()` macht diese zweite Ebene
sichtbar, damit der Schalter keine Wirkung vortäuscht.
"""
from __future__ import annotations

import subprocess
from typing import Dict, Optional

_SERVICE = "aktien_bot.service"
_SYSTEMCTL_TIMEOUT_S = 2


def _feed(event: str, detail: str) -> None:
    """Protokolliert eine manuelle Aktion im Activity-Feed. Fail-open —
    ein Log-Fehler darf die Aktion selbst nie verhindern."""
    try:
        from system.live_status import feed_emit
        feed_emit(event, detail=f"[manuell] {detail}")
    except Exception:
        pass


def service_state() -> str:
    """Zustand des systemd-Dienstes: "active" | "inactive" | "unknown".
    READ-ONLY (`systemctl is-active`) — dieses Modul startet/stoppt den
    Dienst NIEMALS. Fail-open: kein systemd/kein Recht/Timeout →
    "unknown" (dann macht die UI keine Aussage statt einer falschen)."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", _SERVICE],
            capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT_S,
        )
        state = (out.stdout or "").strip()
        return state if state in ("active", "inactive", "failed", "activating") else "unknown"
    except Exception:
        return "unknown"


def pause_status() -> Dict:
    """Aktueller Pause-Zustand (fail-open: unbekannt → nicht pausiert,
    gleiche Default-Annahme wie der bestehende Header-Banner)."""
    try:
        from system import bot_control
        return bot_control.get_status()
    except Exception:
        return {"paused": False, "since": None, "reason": None, "by": None}


def set_bot_paused(paused: bool, reason: str = "", by: str = "dashboard") -> Dict:
    """Schaltet den Pause-Flag um und protokolliert die manuelle Aktion.
    Nutzt `bot_control.set_paused()` — den dort bereits vorhandenen,
    ausdrücklich für UI-Schalter gedachten Helfer (keine eigene
    Flag-Mechanik). Gibt den neuen Status zurück."""
    from system import bot_control
    status = bot_control.set_paused(paused, reason=reason, by=by)
    _feed(
        "bot_paused" if paused else "bot_resumed",
        f"Bot {'pausiert' if paused else 'fortgesetzt'} über das Dashboard"
        + (f" — Grund: {reason}" if reason else ""),
    )
    return status


def reset_circuit_breaker(current_value: float, by: str = "dashboard") -> Optional[Dict]:
    """H1.3: setzt die ausgelösten Circuit-Breaker-Sperren zurück und
    protokolliert die manuelle Aktion. Gibt das Ergebnis von
    `CircuitBreaker.reset()` zurück (None bei Fehler)."""
    try:
        from portfolio.circuit_breaker import CircuitBreaker
        result = CircuitBreaker().reset(current_value, by=by)
    except Exception:
        return None
    _feed(
        "breaker_reset",
        "Circuit-Breaker über das Dashboard zurückgesetzt — Referenzwerte "
        f"auf ${current_value:,.2f} gesetzt "
        f"(vorher: Tagesöffnung ${result.get('prev_open_value') or 0:,.2f}, "
        f"Hoch ${result.get('prev_peak_value') or 0:,.2f})",
    )
    return result
