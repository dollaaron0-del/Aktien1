"""
Conditional Entry Watcher – bedingte Einstiege bei Wunschkursniveau.

Wenn Claude eine Aktie mit SKIP bewertet aber ein bullisches Potential
sieht, speichert er einen Einstiegspreis (entry_trigger_price). Sobald
der Kurs diesen Level erreicht, wird der Trade sofort ohne neuen
Analyse-Zyklus ausgeführt.

Persistenz: data/conditional_entries.json
Ablauf: 3 Tage (danach muss die Aktie neu analysiert werden)
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "conditional_entries.json")
_EXPIRY_DAYS = 7


@dataclass
class ConditionalEntry:
    ticker:                 str
    trigger_price:          float     # Kauf wenn Kurs <= trigger_price
    price_at_creation:      float     # Kurs bei der Analyse (Referenz)
    sentiment_score:        float
    confidence:             str
    entry_rationale:        str
    bull_case:              str
    bear_case:              str
    suggested_hold_days:    int
    target_price:           Optional[float]
    target_price_rationale: str
    key_catalysts:          List[str] = field(default_factory=list)
    risk_factors:           List[str] = field(default_factory=list)
    created_at:             str = ""
    expires_at:             str = ""

    @property
    def pct_to_trigger(self) -> float:
        """Wie weit muss der Kurs noch fallen bis zum Trigger (%)."""
        if self.price_at_creation <= 0:
            return 0.0
        return (self.price_at_creation - self.trigger_price) / self.price_at_creation * 100


class ConditionalEntryWatcher:

    def add(self, entry: ConditionalEntry) -> None:
        entries = self._load()
        entries = [e for e in entries if e.ticker != entry.ticker]
        entries.append(entry)
        self._save(entries)

    def get_active(self) -> List[ConditionalEntry]:
        now = datetime.utcnow().isoformat()[:16]
        return [e for e in self._load() if e.expires_at >= now]

    def remove(self, ticker: str) -> None:
        self._save([e for e in self._load() if e.ticker != ticker])

    def cleanup_expired(self) -> int:
        all_e = self._load()
        now = datetime.utcnow().isoformat()[:16]
        active = [e for e in all_e if e.expires_at >= now]
        if len(active) < len(all_e):
            self._save(active)
        return len(all_e) - len(active)

    def check_triggered(self, prices: Dict[str, float]) -> List[ConditionalEntry]:
        """Gibt alle Einträge zurück deren Trigger-Preis erreicht oder unterschritten wurde."""
        return [
            e for e in self.get_active()
            if prices.get(e.ticker) and prices[e.ticker] <= e.trigger_price
        ]

    @staticmethod
    def build(ticker: str, trigger_price: float, current_price: float, analysis) -> "ConditionalEntry":
        now = datetime.utcnow()
        return ConditionalEntry(
            ticker=ticker,
            trigger_price=round(trigger_price, 4),
            price_at_creation=current_price,
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            entry_rationale=analysis.entry_rationale,
            bull_case=analysis.bull_case,
            bear_case=analysis.bear_case,
            suggested_hold_days=analysis.suggested_hold_days,
            target_price=analysis.target_price,
            target_price_rationale=getattr(analysis, "target_price_rationale", ""),
            key_catalysts=list(analysis.key_catalysts or []),
            risk_factors=list(analysis.risk_factors or []),
            created_at=now.isoformat()[:16],
            expires_at=(now + timedelta(days=_EXPIRY_DAYS)).isoformat()[:16],
        )

    def _load(self) -> List[ConditionalEntry]:
        try:
            with open(_FILE) as f:
                data = json.load(f)
            result = []
            for d in data:
                d.setdefault("key_catalysts", [])
                d.setdefault("risk_factors", [])
                d.setdefault("target_price_rationale", "")
                result.append(ConditionalEntry(**d))
            return result
        except Exception:
            return []

    def _save(self, entries: List[ConditionalEntry]) -> None:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
        ) as tmp:
            json.dump([asdict(e) for e in entries], tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, _FILE)
