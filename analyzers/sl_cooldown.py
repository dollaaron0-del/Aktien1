"""
StopLossCooldown – verhindert Re-Entry nach SL-Auslösung für N Tage.

Wenn eine Position durch Stop-Loss geschlossen wird, blockiert dieser
Tracker den Kauf desselben Tickers für SL_COOLDOWN_DAYS Tage.

Verhindert: Doppel-Verlust durch sofortiges Wiederkaufen eines fallenden Titels.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

from logger import get_logger

log = get_logger(__name__)

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "sl_cooldown.json")


class StopLossCooldown:
    def __init__(self, cooldown_days: int = 5):
        self.cooldown_days = cooldown_days
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)

    def record(self, ticker: str, price: float) -> None:
        """Nach SL-Auslösung aufrufen – sperrt Ticker für cooldown_days."""
        data = self._load()
        data[ticker.upper()] = {
            "price": round(price, 4),
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
        self._save(data)
        log.info("[%s] SL-Sperre gesetzt für %d Tage (Kurs: $%.2f)", ticker, self.cooldown_days, price)

    def is_blocked(self, ticker: str) -> Tuple[bool, str]:
        """Gibt (True, Grund) zurück wenn Ticker noch gesperrt ist."""
        data = self._load()
        entry = data.get(ticker.upper())
        if not entry:
            return False, ""
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
        except Exception:
            return False, ""
        unblock_at = ts + timedelta(days=self.cooldown_days)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if now < unblock_at:
            days_since = (now - ts).days
            days_left = (unblock_at - now).days + 1
            return True, (
                f"SL-Sperre aktiv: vor {days_since}d gestoppt bei ${entry['price']:.2f} "
                f"– Re-Entry in {days_left}d erlaubt"
            )
        # Abgelaufen: Eintrag entfernen
        data.pop(ticker.upper(), None)
        self._save(data)
        return False, ""

    def clear(self, ticker: str) -> None:
        """Sperre manuell aufheben (z.B. bei manuellem Kauf-Override)."""
        data = self._load()
        data.pop(ticker.upper(), None)
        self._save(data)

    def all_blocked(self) -> Dict[str, Dict]:
        """Gibt alle aktiven Sperren zurück (für Dashboard-Anzeige)."""
        data = self._load()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        active = {}
        for ticker, entry in list(data.items()):
            try:
                ts = datetime.fromisoformat(entry["timestamp"])
                unblock_at = ts + timedelta(days=self.cooldown_days)
                if now < unblock_at:
                    entry["unblock_at"] = unblock_at.isoformat()
                    entry["days_left"] = (unblock_at - now).days + 1
                    active[ticker] = entry
                else:
                    data.pop(ticker, None)
            except Exception:
                pass
        if len(active) < len(data):
            self._save({k: v for k, v in data.items() if k in active})
        return active

    def _load(self) -> Dict:
        try:
            with open(_FILE) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self, data: Dict) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
        ) as tmp:
            json.dump(data, tmp)
            tmp_path = tmp.name
        os.replace(tmp_path, _FILE)
