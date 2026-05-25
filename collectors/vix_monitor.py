"""
VixMonitor – passt Positionsgrößen basierend auf dem Fear-Index (VIX) an.

VIX < 20 : normaler Markt     → Faktor 1.0 (keine Änderung)
VIX 20–30: erhöhte Volatilität → Faktor 0.75 (25 % kleinere Positionen)
VIX 30–40: Panik-Zone          → Faktor 0.5  (halbe Positionen)
VIX > 40 : Markt-Crash         → Faktor 0.0  (keine neuen Käufe)

Daten kommen von yfinance (^VIX), gecacht für 5 Minuten.
"""
from __future__ import annotations

import time
from typing import Optional

from logger import get_logger

log = get_logger(__name__)

_CACHE_TTL = 300  # 5 Minuten

_vix_cache: dict = {"value": None, "ts": 0.0}


def get_vix() -> Optional[float]:
    """Holt den aktuellen VIX-Wert (mit 5-Min-Cache). Gibt None zurück bei Fehler."""
    now = time.time()
    if _vix_cache["value"] is not None and now - _vix_cache["ts"] < _CACHE_TTL:
        return _vix_cache["value"]
    try:
        import yfinance as yf
        data = yf.download("^VIX", period="1d", interval="5m", progress=False, auto_adjust=True)
        if data.empty:
            return None
        raw = data["Close"].iloc[-1]
        # yfinance may return a Series with MultiIndex columns — extract scalar
        if hasattr(raw, "__len__"):
            raw = raw.iloc[0]
        val = float(raw)
        _vix_cache["value"] = val
        _vix_cache["ts"] = now
        log.info("VIX aktuell: %.2f", val)
        return val
    except Exception as e:
        log.warning("VIX-Abruf fehlgeschlagen: %s", e)
        return None


def get_position_size_modifier() -> float:
    """
    Gibt einen Multiplikator für Positionsgrößen zurück.
    0.0 bedeutet: keine neuen Käufe (Crash-Modus).
    """
    vix = get_vix()
    if vix is None:
        return 1.0  # Kein Datenpunkt → kein Eingriff
    if vix > 40:
        return 0.0
    if vix > 30:
        return 0.5
    if vix > 20:
        return 0.75
    return 1.0


def should_block_buy() -> tuple[bool, str]:
    """Gibt (True, Grund) zurück wenn VIX zu hoch für neue Käufe ist."""
    vix = get_vix()
    if vix is None:
        return False, ""
    if vix > 40:
        return True, f"VIX={vix:.1f} – Markt-Crash-Modus, keine neuen Käufe"
    return False, ""


def vix_summary() -> str:
    """Kurze Statuszeile für Logs."""
    vix = get_vix()
    if vix is None:
        return "VIX: n/a"
    modifier = get_position_size_modifier()
    level = (
        "CRASH" if vix > 40 else
        "PANIC" if vix > 30 else
        "CAUTION" if vix > 20 else
        "NORMAL"
    )
    return f"VIX={vix:.1f} [{level}] → Positionsgröße ×{modifier:.2f}"
