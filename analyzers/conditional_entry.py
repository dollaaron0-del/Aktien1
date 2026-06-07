"""
Conditional Entry Watcher

Speichert bedingte Kaufaufträge ("Wenn Preis X erreicht wird, kaufe").
Persistiert in data/conditional_entries.json.
Prüft bei jedem Preis-Update ob ein Trigger ausgelöst wird.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_STORE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "conditional_entries.json")


@dataclass
class ConditionalEntry:
    ticker: str
    trigger_price: float          # Preis bei dem gekauft werden soll
    price_at_creation: float      # Preis als der Eintrag erstellt wurde
    sentiment_score: float        # Sentiment-Score bei Erstellung
    expires_at: str               # ISO-Timestamp: Ablaufdatum
    ibkr_order_id: Optional[str] = None   # Optionale IBKR-Order-ID
    shares_reserved: float = 0.0          # Reservierte Anteile (0 = noch nicht berechnet)

    @property
    def is_expired(self) -> bool:
        try:
            return datetime.utcnow() > datetime.fromisoformat(self.expires_at)
        except Exception:
            return False

    @property
    def trigger_distance_pct(self) -> float:
        """Wie weit der aktuelle Preis vom Trigger entfernt ist."""
        if self.price_at_creation <= 0:
            return 0.0
        return (self.trigger_price - self.price_at_creation) / self.price_at_creation * 100


class ConditionalEntryWatcher:
    """Verwaltet und prüft bedingte Kaufaufträge."""

    def __init__(self, store_file: str = _STORE_FILE, default_expiry_days: int = 7):
        self._store_file = store_file
        self._default_expiry_days = default_expiry_days
        self._entries: Dict[str, ConditionalEntry] = {}
        self._load()

    def add(self, entry: ConditionalEntry) -> None:
        """Fügt neuen Eintrag hinzu (überschreibt alten für gleichen Ticker)."""
        self._entries[entry.ticker] = entry
        self._save()
        log.info(
            "Conditional Entry gesetzt: %s @ $%.2f (Trigger-Distanz: %+.1f%%)",
            entry.ticker, entry.trigger_price, entry.trigger_distance_pct,
        )

    def remove(self, ticker: str) -> None:
        if ticker in self._entries:
            del self._entries[ticker]
            self._save()

    def get_all(self) -> List[ConditionalEntry]:
        """Gibt alle nicht abgelaufenen Einträge zurück."""
        return [e for e in self._entries.values() if not e.is_expired]

    def check_triggered(self, prices: Dict[str, float]) -> List[ConditionalEntry]:
        """
        Prüft ob Trigger-Preise erreicht wurden.
        Gibt Liste ausgelöster Einträge zurück und entfernt sie.
        """
        triggered = []
        to_remove = []

        for ticker, entry in list(self._entries.items()):
            if entry.is_expired:
                to_remove.append(ticker)
                continue
            price = prices.get(ticker)
            if price is None:
                continue
            # Trigger: Preis erreicht oder überschritten (aufwärts)
            if price >= entry.trigger_price:
                triggered.append(entry)
                to_remove.append(ticker)
                log.info(
                    "Conditional Entry ausgelöst: %s @ $%.2f (Trigger war $%.2f)",
                    ticker, price, entry.trigger_price,
                )

        for t in to_remove:
            self._entries.pop(t, None)
        if to_remove:
            self._save()

        return triggered

    def _load(self) -> None:
        try:
            with open(self._store_file) as f:
                raw = json.load(f)
            for ticker, d in raw.items():
                try:
                    self._entries[ticker] = ConditionalEntry(**d)
                except Exception:
                    pass
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("ConditionalEntryWatcher: Laden fehlgeschlagen: %s", e)

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._store_file), exist_ok=True)
        try:
            with open(self._store_file, "w") as f:
                json.dump({k: asdict(v) for k, v in self._entries.items()}, f, indent=2)
        except Exception as e:
            log.warning("ConditionalEntryWatcher: Speichern fehlgeschlagen: %s", e)
