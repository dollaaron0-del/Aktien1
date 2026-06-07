"""
Regime-Adaptive Config – passt Trading-Parameter automatisch ans Marktregime an.

Liest das aktuelle Regime aus dem RecessionDetector (BULL/NEUTRAL/BEAR/CRISIS)
und liefert passende SL/TP/Hold/Positionsgröße-Parameter zurück.

Zwischenstand wird in data/current_regime.json gecacht (TTL: 6 Stunden),
damit nicht bei jedem Start yfinance-Daten geladen werden.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "current_regime.json")
_CACHE_TTL_HOURS = 6

# Regime-Konstanten (gleich wie in recession_detector.py)
BULL    = "BULL"
NEUTRAL = "NEUTRAL"
BEAR    = "BEAR"
CRISIS  = "CRISIS"


@dataclass
class RegimeParams:
    sl_pct: float               # Stop-Loss Prozent
    tp_pct: float               # Take-Profit Prozent
    hold_days_mult: float       # Multiplikator auf configured hold_days
    position_size_mult: float   # Multiplikator auf Positionsgröße
    buy_threshold_adj: float    # Anpassung der Kaufschwelle (+/- delta)
    label: str


class RegimeAdaptiveConfig:
    """Liefert regime-spezifische Trading-Parameter."""

    REGIMES: Dict[str, RegimeParams] = {
        BULL:    RegimeParams(
            sl_pct=0.06, tp_pct=0.22, hold_days_mult=1.2,
            position_size_mult=1.0,  buy_threshold_adj=-0.03, label="Bullenmarkt",
        ),
        NEUTRAL: RegimeParams(
            sl_pct=0.07, tp_pct=0.18, hold_days_mult=1.0,
            position_size_mult=0.80, buy_threshold_adj=0.0,   label="Neutraler Markt",
        ),
        BEAR:    RegimeParams(
            sl_pct=0.05, tp_pct=0.12, hold_days_mult=0.7,
            position_size_mult=0.50, buy_threshold_adj=+0.05, label="Bärenmarkt",
        ),
        CRISIS:  RegimeParams(
            sl_pct=0.04, tp_pct=0.08, hold_days_mult=0.5,
            position_size_mult=0.25, buy_threshold_adj=+0.10, label="Krisenmarkt",
        ),
    }

    def get(self, regime: str) -> RegimeParams:
        """Gibt RegimeParams für das angegebene Regime zurück (Fallback: NEUTRAL)."""
        return self.REGIMES.get(regime, self.REGIMES[NEUTRAL])

    def summary(self, regime: str) -> str:
        """Kompakter Text für Logs / Telegram."""
        p = self.get(regime)
        return (
            f"Regime {p.label}: SL={p.sl_pct*100:.0f}%, TP={p.tp_pct*100:.0f}%, "
            f"Hold×{p.hold_days_mult}, Pos×{p.position_size_mult}"
        )


# Singleton
_regime_config = RegimeAdaptiveConfig()


def get_regime_config() -> RegimeAdaptiveConfig:
    return _regime_config


# ── Cache-Helfer ──────────────────────────────────────────────────────────────

def _load_cached_regime() -> Optional[str]:
    """Gibt gecachtes Regime zurück, falls jünger als TTL. Sonst None."""
    try:
        with open(_CACHE_FILE) as f:
            data = json.load(f)
        ts = datetime.fromisoformat(data["timestamp"])
        if datetime.utcnow() - ts < timedelta(hours=_CACHE_TTL_HOURS):
            return data["regime"]
    except Exception:
        pass
    return None


def _save_cached_regime(regime: str) -> None:
    """Atomic write des Regime-Caches."""
    os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
    payload = {"regime": regime, "timestamp": datetime.utcnow().isoformat()}
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(_CACHE_FILE), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp_path, _CACHE_FILE)
        log.debug("Regime-Cache gespeichert: %s", regime)
    except Exception as exc:
        log.warning("Regime-Cache konnte nicht gespeichert werden: %s", exc)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ── Haupt-Hilfsfunktion ───────────────────────────────────────────────────────

def get_current_regime(force_refresh: bool = False) -> str:
    """
    Gibt das aktuelle Marktregime zurück (BULL/NEUTRAL/BEAR/CRISIS).

    Verwendet gecachten Wert wenn vorhanden und nicht abgelaufen.
    Bei force_refresh oder abgelaufenem Cache: RecessionDetector neu berechnen.
    """
    if not force_refresh:
        cached = _load_cached_regime()
        if cached is not None:
            log.debug("Regime aus Cache: %s", cached)
            return cached

    try:
        # Lazy import um zirkuläre Abhängigkeiten zu vermeiden
        from analyzers.recession_detector import RecessionDetector  # type: ignore
        detector = RecessionDetector()
        result = detector.analyze()
        regime = result.get("regime", NEUTRAL)
        score  = result.get("recession_score", 0.0)
        log.info("Regime neu berechnet: %s (score=%.3f)", regime, score)
    except Exception as exc:
        log.warning("RecessionDetector fehlgeschlagen, Fallback NEUTRAL: %s", exc)
        regime = NEUTRAL

    _save_cached_regime(regime)
    return regime


def get_adaptive_params(
    regime: Optional[str] = None,
    market_is_ranging: bool = False,
) -> RegimeParams:
    """
    Gibt die aktuellen RegimeParams zurück.
    Wenn market_is_ranging=True und Regime BEAR/CRISIS: zusätzliche Seitwärts-Anpassung:
      - Positionsgröße nochmals –20% (auf max 40% bei BEAR, 20% bei CRISIS)
      - Kaufhürde +5% (noch selektiver)
      - Take-Profit leicht enger (Seitwärts-Markt vergibt weniger Luft nach oben)
    """
    if regime is None:
        regime = get_current_regime()
    params = _regime_config.get(regime)

    if market_is_ranging and regime in (BEAR, CRISIS):
        from dataclasses import replace as _replace
        params = _replace(
            params,
            position_size_mult = round(params.position_size_mult * 0.80, 2),
            buy_threshold_adj  = round(params.buy_threshold_adj + 0.05, 2),
            tp_pct             = round(params.tp_pct * 0.85, 3),
            label              = params.label + " + Seitwärts",
        )
        log.info(
            "Seitwärtsmarkt erkannt: Positionsgröße ×0.8, Kaufhürde +5%%, TP –15%% → %s",
            _regime_config.summary(regime),
        )

    log.info("Regime-Parameter: %s", params.label)
    return params


def get_last_cached_regime() -> Optional[str]:
    """Gibt das zuletzt gecachte Regime zurück, ohne TTL-Prüfung (für Vergleich)."""
    try:
        with open(_CACHE_FILE) as f:
            return json.load(f).get("regime")
    except Exception:
        return None


def invalidate_cache_if_crash(threshold_pct: float = 3.0) -> Tuple[bool, float]:
    """
    Prüft ob SPY intraday um mehr als threshold_pct% gefallen ist.
    Falls ja: Regime-Cache löschen → nächster get_current_regime()-Aufruf neu berechnen.
    Gibt (invalidated, intraday_change_pct) zurück.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker("SPY").history(period="1d", interval="5m")
        if hist.empty or len(hist) < 2:
            return False, 0.0
        day_open = float(hist["Open"].iloc[0])
        current  = float(hist["Close"].iloc[-1])
        if day_open <= 0:
            return False, 0.0
        change_pct = (current - day_open) / day_open * 100
        if change_pct <= -threshold_pct:
            if os.path.exists(_CACHE_FILE):
                os.remove(_CACHE_FILE)
                log.warning(
                    "Flash-Crash erkannt: SPY %.1f%% intraday – Regime-Cache invalidiert.",
                    change_pct,
                )
            return True, change_pct
        return False, change_pct
    except Exception as exc:
        log.warning("invalidate_cache_if_crash fehlgeschlagen – Prüfung übersprungen: %s", exc)
        return False, None  # None = Prüfung fehlgeschlagen (nicht: kein Crash)
