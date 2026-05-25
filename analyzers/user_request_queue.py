"""
UserRequestQueue – vom Dashboard manuell angefragte Ticker.

Der Bot liest die Queue beim nächsten Analysezyklus aus, analysiert die
Ticker einmalig und leert die Queue danach automatisch.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import List

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "user_requests.json")


def add_ticker(ticker: str) -> None:
    data = _load()
    t = ticker.strip().upper()
    if t and t not in data:
        data.append(t)
        _save(data)


def consume_all() -> List[str]:
    """Gibt alle angeforderten Ticker zurück und leert die Queue."""
    data = _load()
    if data:
        _save([])
    return data


def peek() -> List[str]:
    return _load()


def _load() -> List[str]:
    try:
        with open(_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save(data: List[str]) -> None:
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
    ) as tmp:
        json.dump(data, tmp)
        tmp_path = tmp.name
    os.replace(tmp_path, _FILE)
