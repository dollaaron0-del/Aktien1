"""
UserRequestQueue – vom Dashboard manuell angefragte Ticker + Headline-Signale.

Jeder Eintrag kann optionale Metadaten tragen (z.B. Signal-Typ, Score, Headline-Text)
damit runner.py nach der Analyse eine kontextuelle Follow-Up-Nachricht senden kann.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List, Dict, Optional, Tuple

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "user_requests.json")


def add_ticker(ticker: str, meta: Optional[Dict] = None) -> None:
    """Fügt einen Ticker zur Queue hinzu. meta = optionale Signal-Metadaten."""
    data = _load()
    t = ticker.strip().upper()
    if not t:
        return
    # Bestehenden Eintrag ersetzen falls vorhanden
    data = [e for e in data if e["ticker"] != t]
    data.append({"ticker": t, "meta": meta or {}})
    _save(data)


def consume_all() -> List[Tuple[str, Dict]]:
    """Gibt alle Einträge als (ticker, meta)-Tupel zurück und leert die Queue."""
    data = _load()
    if data:
        _save([])
    return [(e["ticker"], e.get("meta", {})) for e in data]


def peek() -> List[str]:
    return [e["ticker"] for e in _load()]


def _load() -> List[Dict]:
    try:
        with open(_FILE) as f:
            raw = json.load(f)
        # Rückwärtskompatibilität: alte Queue war eine Liste von Strings
        result = []
        for item in raw:
            if isinstance(item, str):
                result.append({"ticker": item, "meta": {}})
            else:
                result.append(item)
        return result
    except Exception:
        return []


def _save(data: List[Dict]) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
    ) as tmp:
        json.dump(data, tmp)
        tmp_path = tmp.name
    os.replace(tmp_path, _FILE)
