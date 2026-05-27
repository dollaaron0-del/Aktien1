"""
BenchList – Warteliste für Aktien die der Bot selbst entdeckt hat.

Der Bot fügt Aktien hinzu wenn:
  • SignalDrivenExpander einen neuen Ticker aus News/Reddit aufschnappt
  • Opportunity-Scan einen Kandidaten findet der nicht in die Watchlist passt
  • Social-Spike einen unbekannten Ticker mehrfach erwähnt

Bei freien Positions-Slots wird zuerst aus der BenchList geschöpft
(sortiert nach Signal-Score), bevor ein teurer Vollscan läuft.

Max. 30 Einträge. Einträge die 14 Tage kein neues Signal hatten werden entfernt.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from typing import List, Dict, Optional

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "bench_list.json")
_MAX_ENTRIES   = 30
_TTL_DAYS      = 14


class BenchEntry:
    def __init__(self, ticker: str, reason: str, score: float = 0.5,
                 geo_context: Optional[Dict] = None):
        self.ticker     = ticker.upper()
        self.reason     = reason
        self.score      = max(0.0, min(1.0, score))
        self.added_at   = datetime.utcnow().isoformat()
        self.last_seen  = self.added_at
        self.signal_count = 1
        # Geopolitischer Kontext (optional) – wird an Claude weitergegeben
        self.geo_context: Optional[Dict] = geo_context

    def to_dict(self) -> Dict:
        d = {
            "ticker":       self.ticker,
            "reason":       self.reason,
            "score":        self.score,
            "added_at":     self.added_at,
            "last_seen":    self.last_seen,
            "signal_count": self.signal_count,
        }
        if self.geo_context:
            d["geo_context"] = self.geo_context
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "BenchEntry":
        e = cls.__new__(cls)
        e.ticker       = d["ticker"]
        e.reason       = d.get("reason", "")
        e.score        = float(d.get("score", 0.5))
        e.added_at     = d.get("added_at", datetime.utcnow().isoformat())
        e.last_seen    = d.get("last_seen", e.added_at)
        e.signal_count = int(d.get("signal_count", 1))
        e.geo_context  = d.get("geo_context")
        return e


class BenchList:
    """Warteliste für vom Bot entdeckte Kandidaten."""

    def add(self, ticker: str, reason: str, score: float = 0.5,
            geo_context: Optional[Dict] = None) -> bool:
        """
        Fügt Ticker zur Warteliste hinzu.
        Wenn er bereits vorhanden: score und signal_count aktualisieren.
        Gibt True zurück wenn neu hinzugefügt.
        geo_context: optionaler geopolitischer Kontext der an Claude weitergegeben wird.
        """
        ticker = ticker.strip().upper()
        if not ticker:
            return False
        entries = self._load()

        existing = next((e for e in entries if e.ticker == ticker), None)
        if existing:
            existing.score       = max(existing.score, score)
            existing.last_seen   = datetime.utcnow().isoformat()
            existing.signal_count += 1
            if reason and reason not in existing.reason:
                existing.reason = f"{existing.reason}; {reason}"[:200]
            # Geo-Kontext aktualisieren wenn ein neuer mitgegeben wird
            if geo_context:
                existing.geo_context = geo_context
            self._save(entries)
            return False

        entry = BenchEntry(ticker, reason, score, geo_context=geo_context)
        entries.append(entry)

        # Sortieren und auf max. _MAX_ENTRIES begrenzen
        entries.sort(key=lambda e: (-e.score, -e.signal_count))
        entries = entries[:_MAX_ENTRIES]
        self._save(entries)
        return True

    def get_context(self, ticker: str) -> Optional[Dict]:
        """Gibt den gespeicherten Kontext (inkl. geo_context) für einen Ticker zurück."""
        entries = self._load()
        entry = next((e for e in entries if e.ticker == ticker.upper()), None)
        if not entry:
            return None
        return {
            "reason":      entry.reason,
            "score":       entry.score,
            "geo_context": entry.geo_context,
        }

    def pop_candidates(self, n: int, exclude: List[str]) -> List[str]:
        """
        Gibt die n besten Kandidaten zurück die nicht in `exclude` sind.
        Die zurückgegebenen Ticker werden NICHT entfernt (bleiben für spätere Zyklen).
        """
        entries = self._load_valid()
        excl = {t.upper() for t in exclude}
        result = []
        for e in sorted(entries, key=lambda e: (-e.score, -e.signal_count)):
            if e.ticker not in excl and e.ticker not in result:
                result.append(e.ticker)
                if len(result) >= n:
                    break
        return result

    def remove(self, ticker: str) -> None:
        """Entfernt einen Ticker (z.B. nachdem er analysiert wurde)."""
        entries = self._load()
        entries = [e for e in entries if e.ticker != ticker.upper()]
        self._save(entries)

    def cleanup(self) -> int:
        """Entfernt Einträge die älter als _TTL_DAYS sind. Gibt Anzahl zurück."""
        entries = self._load()
        cutoff = datetime.utcnow() - timedelta(days=_TTL_DAYS)
        before = len(entries)
        entries = [
            e for e in entries
            if datetime.fromisoformat(e.last_seen) > cutoff
        ]
        self._save(entries)
        return before - len(entries)

    def get_all(self) -> List[Dict]:
        """Alle gültigen Einträge für Dashboard-Anzeige."""
        return [e.to_dict() for e in self._load_valid()]

    def count(self) -> int:
        return len(self._load_valid())

    # ── Intern ────────────────────────────────────────────────────────────────

    def _load_valid(self) -> List[BenchEntry]:
        entries = self._load()
        cutoff = datetime.utcnow() - timedelta(days=_TTL_DAYS)
        return [e for e in entries if datetime.fromisoformat(e.last_seen) > cutoff]

    def _load(self) -> List[BenchEntry]:
        try:
            with open(_FILE) as f:
                data = json.load(f)
            return [BenchEntry.from_dict(d) for d in data]
        except Exception:
            return []

    def _save(self, entries: List[BenchEntry]) -> None:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
        ) as tmp:
            json.dump([e.to_dict() for e in entries], tmp, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, _FILE)
