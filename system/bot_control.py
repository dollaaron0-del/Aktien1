"""
system/bot_control.py – Zentraler Pause-Schalter für den Bot.

Single Source of Truth: sowohl die Bot-Hauptschleife (bot/scheduler.py) als auch
das Dashboard (dashboard/app.py) lesen/schreiben denselben Flag über dieses Modul.

Pause = KOMPLETTER Stopp: solange der Flag gesetzt ist, führt die Hauptschleife
KEINE geplanten Jobs aus (weder Analyse/Käufe noch SL/TP-Überwachung, Scanner
oder Wartung). Der systemd-Service läuft weiter, der Bot nimmt die Arbeit nach dem
Aufheben der Pause automatisch wieder auf.

Der Flag ist dateibasiert und überlebt damit auch einen Bot-Neustart.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

_PAUSE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "bot_paused.json")


def _read() -> dict:
    try:
        with open(_PAUSE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def is_paused() -> bool:
    """True, wenn der Bot aktuell pausiert ist (frisch von Platte gelesen)."""
    return bool(_read().get("paused"))


def get_status() -> dict:
    """Liefert {paused, since, reason, by} – leere/Default-Werte wenn nie gesetzt."""
    data = _read()
    return {
        "paused": bool(data.get("paused")),
        "since": data.get("since"),
        "reason": data.get("reason"),
        "by": data.get("by"),
    }


def pause(reason: str = "", by: str = "dashboard") -> dict:
    """Setzt den Bot in den Pause-Zustand. Idempotent (überschreibt 'since' nicht
    bei erneutem Aufruf, solange schon pausiert)."""
    current = _read()
    if current.get("paused"):
        return get_status()
    payload = {
        "paused": True,
        "since": datetime.now().isoformat(timespec="seconds"),
        "reason": reason or None,
        "by": by,
    }
    _write(payload)
    return payload


def resume(by: str = "dashboard") -> dict:
    """Hebt die Pause auf. Bewahrt minimale Historie (last_resumed) auf."""
    payload = {
        "paused": False,
        "since": None,
        "reason": None,
        "by": by,
        "last_resumed": datetime.now().isoformat(timespec="seconds"),
    }
    _write(payload)
    return payload


def set_paused(value: bool, reason: str = "", by: str = "dashboard") -> dict:
    """Bequemer Toggle-Helfer für UI-Schalter."""
    return pause(reason=reason, by=by) if value else resume(by=by)


def _write(payload: dict) -> None:
    os.makedirs(os.path.dirname(_PAUSE_FILE), exist_ok=True)
    tmp = f"{_PAUSE_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _PAUSE_FILE)
