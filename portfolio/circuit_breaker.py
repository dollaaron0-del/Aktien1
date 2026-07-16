"""
Daily Loss Circuit Breaker.

Speichert den Portfolio-Wert zu Beginn jedes Handelstages.
Wenn der aktuelle Verlust > MAX_DAILY_LOSS_PCT (Standard: 5 %),
werden neue Käufe für den Rest des Tages blockiert.

Zusätzlich: maximaler Drawdown vom Allzeithoch (15 %).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, date
from typing import Dict, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

_CACHE_FILE       = os.path.join(os.path.dirname(__file__), "..", "data", "circuit_breaker.json")
_MAX_DAILY_LOSS   = float(os.getenv("MAX_DAILY_LOSS_PCT",  "0.05"))   # 5 %
_MAX_DRAWDOWN     = float(os.getenv("MAX_DRAWDOWN_PCT",    "0.15"))   # 15 %


class CircuitBreaker:
    """
    Tagesverlust-Schutz und Drawdown-Schutz.
    Wird in SwingStrategy.evaluate() vor jedem Kauf konsultiert.
    """

    def __init__(self):
        self._state: Dict = self._load()

    # ── Persistenz ─────────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            with open(_CACHE_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            tmp = _CACHE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._state, f, indent=2)
            os.replace(tmp, _CACHE_FILE)
        except Exception as e:
            log.warning("CircuitBreaker: Speicherfehler – %s", e)

    # ── Tageswert registrieren ─────────────────────────────────────────────────

    def register_day_open(self, portfolio_value: float) -> None:
        """Muss einmal pro Handelstag zu Beginn aufgerufen werden."""
        today = date.today().isoformat()
        if self._state.get("day") != today:
            self._state["day"]        = today
            self._state["open_value"] = portfolio_value
            log.info(
                "CircuitBreaker: Tageseröffnungswert registriert – $%.2f",
                portfolio_value,
            )

        # Allzeithoch aktualisieren
        prev_peak = self._state.get("peak_value", 0.0)
        if portfolio_value > prev_peak:
            self._state["peak_value"] = portfolio_value

        self._save()

    # ── Kauf-Freigabe prüfen ──────────────────────────────────────────────────

    def check_buy_allowed(self, current_value: float) -> Tuple[bool, str]:
        """
        Gibt (True, "") zurück wenn Kauf erlaubt.
        Gibt (False, reason) zurück wenn Circuit Breaker ausgelöst.
        """
        open_value = self._state.get("open_value", current_value)
        peak_value = self._state.get("peak_value", current_value)

        # Tagesverlust
        if open_value > 0:
            daily_loss = (open_value - current_value) / open_value
            if daily_loss >= _MAX_DAILY_LOSS:
                reason = (
                    f"⛔ CIRCUIT BREAKER: Tagesverlust {daily_loss*100:.1f}% "
                    f"überschreitet Limit {_MAX_DAILY_LOSS*100:.0f}% – "
                    f"keine Käufe bis Handelsschluss."
                )
                log.warning(reason)
                return False, reason

        # Drawdown vom Allzeithoch
        if peak_value > 0:
            drawdown = (peak_value - current_value) / peak_value
            if drawdown >= _MAX_DRAWDOWN:
                reason = (
                    f"⛔ CIRCUIT BREAKER: Drawdown {drawdown*100:.1f}% "
                    f"vom Allzeithoch ${peak_value:,.2f} – "
                    f"keine Käufe bis Erholung."
                )
                log.warning(reason)
                return False, reason

        return True, ""

    # ── Status-Info ────────────────────────────────────────────────────────────

    def status(self, current_value: float) -> Dict:
        open_value = self._state.get("open_value", current_value)
        peak_value = self._state.get("peak_value", current_value)
        daily_pct  = (current_value - open_value) / open_value * 100 if open_value else 0.0
        drawdown   = (peak_value - current_value) / peak_value * 100 if peak_value else 0.0
        return {
            "day":          self._state.get("day", "—"),
            "open_value":   open_value,
            "peak_value":   peak_value,
            "daily_pct":    round(daily_pct, 2),
            "drawdown_pct": round(drawdown, 2),
            "triggered":    not self.check_buy_allowed(current_value)[0],
            "last_reset":   self._state.get("last_reset"),
        }

    # ── Manueller Reset (Dashboard-Bedienung, Ausbau-Roadmap H1.3) ────────────

    def reset(self, current_value: float, by: str = "dashboard") -> Dict:
        """Hebt ausgelöste Sperren auf, indem die Referenzwerte auf den
        AKTUELLEN Portfolio-Wert gesetzt werden.

        Das ist eine bewusste RISIKO-ÜBERSTEUERUNG, kein Reparatur-Knopf:
        Es gibt keinen "ausgelöst"-Flag zum Löschen — `check_buy_allowed()`
        rechnet die Sperre jedes Mal neu aus `open_value` (Tagesverlust) und
        `peak_value` (Drawdown vom Allzeithoch) gegen den aktuellen Wert.
        Beide zurückzusetzen heißt:
          - Tagesverlust: der heutige Verlust zählt ab jetzt als 0.
          - Drawdown: das bisherige Allzeithoch wird VERWORFEN — der
            Drawdown-Schutz misst danach ab dem (niedrigeren) aktuellen
            Wert und schlägt entsprechend später an.

        Die Vorzustände werden darum unter `last_reset` mitgeschrieben statt
        still überschrieben: Die Aktion bleibt im State nachvollziehbar, auch
        wenn der Aufrufer sie nicht separat protokolliert.
        """
        prev_open = self._state.get("open_value")
        prev_peak = self._state.get("peak_value")
        record = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "by": by,
            "prev_open_value": prev_open,
            "prev_peak_value": prev_peak,
            "reset_to": current_value,
        }
        self._state["day"] = date.today().isoformat()
        self._state["open_value"] = current_value
        self._state["peak_value"] = current_value
        self._state["last_reset"] = record
        self._save()
        log.warning(
            "CircuitBreaker MANUELL zurückgesetzt (%s): Referenzwerte auf $%.2f "
            "(vorher Tagesöffnung $%s, Allzeithoch $%s)",
            by, current_value, prev_open, prev_peak,
        )
        return record
