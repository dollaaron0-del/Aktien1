"""
Stock Relations Graph – speichert thematische Verbindungen zwischen Aktien.

Wenn Aktie A ein BUY-Signal hat (z.B. ASML wegen Chip-Nachfrage), merkt sich
der Bot welche anderen Aktien von derselben These profitieren (z.B. NVDA, MU).
Über Zeit entsteht ein Netz aus verbundenen Aktien.
"""
import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List

from logger import get_logger

log = get_logger(__name__)

_GRAPH_FILE = os.path.join("data", "stock_relations.json")
_MAX_ENTRIES_PER_TICKER = 5


class StockRelations:
    def __init__(self, path: str = _GRAPH_FILE):
        self._path = path
        self._graph: Dict[str, List[Dict]] = self._load()

    def add_relation(self, from_ticker: str, related: List[str], reason: str) -> None:
        """Speichert: from_ticker → related mit Begründung (aus BUY-These)."""
        if not related:
            return
        key = from_ticker.upper()
        related_clean = [t.upper() for t in related if t.strip()]
        if not related_clean:
            return

        entry = {
            "related": related_clean,
            "reason": reason[:120],
            "date": datetime.utcnow().date().isoformat(),
        }

        if key not in self._graph:
            self._graph[key] = []

        existing_sets = {tuple(sorted(e["related"])) for e in self._graph[key]}
        if tuple(sorted(related_clean)) not in existing_sets:
            self._graph[key].insert(0, entry)
            self._graph[key] = self._graph[key][:_MAX_ENTRIES_PER_TICKER]
            self._save()

    def get_related(self, ticker: str) -> List[str]:
        """Alle bekannten verwandten Ticker für einen Ticker (dedupliziert)."""
        seen: set = set()
        result: List[str] = []
        for entry in self._graph.get(ticker.upper(), []):
            for t in entry["related"]:
                if t not in seen:
                    seen.add(t)
                    result.append(t)
        return result

    def get_all_connections(self) -> List[Dict]:
        """Für Dashboard: alle Verbindungen als flache Liste."""
        rows = []
        for from_t, entries in self._graph.items():
            for e in entries[:2]:
                rows.append({
                    "Von": from_t,
                    "Verbunden mit": ", ".join(e["related"]),
                    "These": e["reason"],
                    "Datum": e["date"],
                })
        return rows

    def stats(self) -> Dict:
        total_related = sum(
            len(e["related"]) for entries in self._graph.values() for e in entries
        )
        return {
            "source_tickers": len(self._graph),
            "total_connections": total_related,
        }

    def _load(self) -> Dict:
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self) -> None:
        dirpath = os.path.dirname(self._path) or "."
        os.makedirs(dirpath, exist_ok=True)
        try:
            fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._graph, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self._path)
        except Exception as e:
            log.warning("StockRelations: Speichern fehlgeschlagen: %s", e)
