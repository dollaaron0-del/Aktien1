"""
Versions-Stempel für Entscheidungslogs (Roadmap 1.6).

decision_log/analysis_log speicherten bisher weder Git-Hash noch
Config-Schnappschuss — die Evidenz-Gates (scripts/track_record.py) messen
damit ein bewegliches Ziel: ein Track-Record über still wechselnde
Code-Stände beweist nichts. Dieses Modul liefert beides als billige,
pro Prozess einmal berechnete Strings, die die Log-Module beim Schreiben
automatisch anhängen.

Bewusst pro Prozess gecacht (nicht pro Aufruf): der laufende Prozess führt
den Code aus, der beim Start geladen wurde — ändert jemand während des
Laufs den Working Tree, wäre ein frisch gelesener Hash FALSCHER als der
gecachte.

Der Config-Schnappschuss ist eine kuratierte Whitelist entscheidungs-
relevanter Werte. NIEMALS die ganze Config dumpen — sie enthält API-Keys.

Fail-open by design: jeder Fehler liefert None statt einer Exception,
Logging darf nie den Handelszyklus reißen.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Dict, Optional, Tuple

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Entscheidungsrelevante Config-Attribute (Whitelist). Neue Verhaltens-Flags
# hier ergänzen — Keys/Tokens/URLs gehören NICHT hierher.
_CONFIG_FIELDS = (
    "broker_mode",
    "initial_capital",
    "buy_threshold",
    "sell_threshold",
    "min_sources",
    "max_position_pct",
    "stop_loss_pct",
    "take_profit_pct",
    "pre_market_threshold_boost",
    "learning_filter_enabled",
    "frugal_mode",
    "frugal_smart_mode",
    "turbo_mode",
    "correlation_signals_enabled",
)

# Verhaltensrelevante ENV-Flags, die nicht in der Config-Dataclass leben.
_ENV_FLAGS = ("IBKR_SERVER_STOPS", "PARALLEL_ANALYSIS", "ANALYSIS_WORKERS")

_cached: Optional[Tuple[Optional[str], Optional[str]]] = None


def _read_git_hash() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_PROJECT_DIR, capture_output=True, text=True, timeout=5,
        )
        h = out.stdout.strip()
        return h if out.returncode == 0 and h else None
    except Exception:
        return None


def _read_config_snapshot() -> Optional[str]:
    try:
        from config import config
        snap: Dict = {}
        for f in _CONFIG_FIELDS:
            if hasattr(config, f):
                snap[f] = getattr(config, f)
        for flag in _ENV_FLAGS:
            val = os.getenv(flag)
            if val is not None:
                snap[flag] = val
        # sort_keys → stabile Strings, gleiche Config ⇒ gleicher Stempel
        return json.dumps(snap, sort_keys=True, default=str)
    except Exception:
        return None


def stamp() -> Tuple[Optional[str], Optional[str]]:
    """(git_hash, config_json) — einmal pro Prozess berechnet, fail-open."""
    global _cached
    if _cached is None:
        _cached = (_read_git_hash(), _read_config_snapshot())
    return _cached
