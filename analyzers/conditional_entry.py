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
    # Analyse-Kontext (wird beim Auslösen für den Trade & die Telegram-Nachricht gebraucht)
    created_at: str = ""
    confidence: str = "MEDIUM"
    entry_rationale: str = ""
    bull_case: str = ""
    bear_case: str = ""
    risk_factors: List[str] = field(default_factory=list)
    key_catalysts: List[str] = field(default_factory=list)
    suggested_hold_days: int = 0
    target_price: Optional[float] = None
    target_price_rationale: str = ""
    # Quellenzahl der Ursprungs-Analyse – wird beim Trigger replayed, damit die
    # min_sources-Schranke nicht mit 0 Quellen fälschlich blockt (der Entry wurde
    # bei Erstellung bereits voll analysiert).
    sources_count: int = 0

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

    @property
    def pct_to_trigger(self) -> float:
        """Alias für trigger_distance_pct – Distanz Trigger ↔ Erstellungskurs in %."""
        return self.trigger_distance_pct


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

    def get_active(self) -> List[ConditionalEntry]:
        """Alias für get_all() – alle aktiven (nicht abgelaufenen) Einträge."""
        return self.get_all()

    @staticmethod
    def build(
        ticker: str,
        trigger_price: float,
        current_price: float,
        analysis,
        expiry_days: int = 7,
    ) -> "ConditionalEntry":
        """Erzeugt einen ConditionalEntry aus einem AnalysisResult.
        Übernimmt den Analyse-Kontext (Bull/Bear-Case, Kursziel etc.), damit der
        Trade beim Auslösen ohne erneute Claude-Analyse ausgeführt werden kann."""
        from datetime import timedelta
        now = datetime.utcnow()
        # Quellenzahl der Analyse erfassen (sources_used ist je nach Pfad
        # int ODER Dict[str,int]), damit sie beim Trigger replayed werden kann.
        _su = getattr(analysis, "sources_used", 0)
        if isinstance(_su, dict):
            _src_count = sum(int(v or 0) for v in _su.values())
        else:
            try:
                _src_count = int(_su or 0)
            except (TypeError, ValueError):
                _src_count = 0
        return ConditionalEntry(
            ticker=ticker,
            trigger_price=float(trigger_price),
            price_at_creation=float(current_price),
            sentiment_score=float(getattr(analysis, "sentiment_score", 0.5) or 0.5),
            expires_at=(now + timedelta(days=expiry_days)).isoformat(),
            created_at=now.isoformat(),
            confidence=getattr(analysis, "confidence", "MEDIUM") or "MEDIUM",
            entry_rationale=getattr(analysis, "entry_rationale", "") or "",
            bull_case=getattr(analysis, "bull_case", "") or "",
            bear_case=getattr(analysis, "bear_case", "") or "",
            risk_factors=list(getattr(analysis, "risk_factors", []) or []),
            key_catalysts=list(getattr(analysis, "key_catalysts", []) or []),
            # Floor 3 Tage: SKIP-Analysen liefern oft suggested_hold_days=0, was
            # sonst zu Sofort-Exit nach dem Trigger führt (vgl. CRM/CAT-Bug).
            suggested_hold_days=max(3, int(getattr(analysis, "suggested_hold_days", 0) or 0)),
            target_price=getattr(analysis, "target_price", None),
            target_price_rationale=getattr(analysis, "target_price_rationale", "") or "",
            sources_count=_src_count,
        )

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
            # Unterstützt beide Formate: {ticker: {...}} und [{...}, ...]
            if isinstance(raw, dict):
                records = raw.values()
            elif isinstance(raw, list):
                records = raw
            else:
                records = []
            from dataclasses import fields as _dc_fields
            _valid = {f.name for f in _dc_fields(ConditionalEntry)}
            for d in records:
                try:
                    entry = ConditionalEntry(**{k: v for k, v in d.items() if k in _valid})
                    self._entries[entry.ticker] = entry
                except Exception as _le:
                    log.debug("Eintrag übersprungen: %s", _le)
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
