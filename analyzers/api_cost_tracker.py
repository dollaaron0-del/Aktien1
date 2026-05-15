"""
API-Kosten-Tracker – verfolgt Claude-API-Aufrufe und Einsparungen durch Ollama.

Speichert in data/api_savings.json.
Wird im Dashboard angezeigt.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, date
from typing import Dict

from logger import get_logger

log = get_logger(__name__)

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "api_savings.json")

# Geschätzte Kosten pro Claude-API-Aufruf (claude-opus-4-7, ~1200 Tokens Output)
# Input: ~3000 Tokens × $0.015/1k = $0.045
# Output: ~1200 Tokens × $0.075/1k = $0.090
# Gesamt: ~$0.135 pro Aufruf (konservative Schätzung)
_COST_PER_CLAUDE_CALL = float(os.getenv("CLAUDE_COST_PER_CALL", "0.135"))


class APICostTracker:

    def __init__(self):
        self._data = self._load()

    def _load(self) -> Dict:
        try:
            with open(_FILE) as f:
                return json.load(f)
        except Exception:
            return {
                "total_analyses":      0,
                "claude_calls":        0,
                "ollama_skips":        0,
                "ollama_fallbacks":    0,
                "total_cost_usd":      0.0,
                "total_saved_usd":     0.0,
                "daily": {},
            }

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(_FILE), exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=os.path.dirname(_FILE), suffix=".tmp", delete=False
            ) as tmp:
                json.dump(self._data, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, _FILE)
        except Exception as e:
            log.warning("APICostTracker: Speicherfehler – %s", e)

    def record(self, claude_called: bool, ollama_used: bool) -> None:
        """Einen Analyse-Vorgang erfassen."""
        today = date.today().isoformat()

        self._data["total_analyses"] += 1

        if today not in self._data["daily"]:
            self._data["daily"][today] = {
                "analyses": 0, "claude": 0, "ollama_skips": 0, "cost_usd": 0.0, "saved_usd": 0.0
            }

        day = self._data["daily"][today]
        day["analyses"] += 1

        if claude_called:
            self._data["claude_calls"]  += 1
            self._data["total_cost_usd"] = round(
                self._data["total_cost_usd"] + _COST_PER_CLAUDE_CALL, 4
            )
            day["claude"]   += 1
            day["cost_usd"]  = round(day["cost_usd"] + _COST_PER_CLAUDE_CALL, 4)
        else:
            saved = _COST_PER_CLAUDE_CALL
            self._data["ollama_skips"]    += 1
            self._data["total_saved_usd"]  = round(
                self._data["total_saved_usd"] + saved, 4
            )
            day["ollama_skips"] += 1
            day["saved_usd"]     = round(day["saved_usd"] + saved, 4)

        if ollama_used and not claude_called:
            pass  # Bereits als skip erfasst
        elif ollama_used and claude_called:
            pass  # Ollama hat vorgeprüft aber Claude bestätigt

        self._save()

    def record_fallback(self) -> None:
        """Ollama war offline → Claude-Fallback."""
        self._data["ollama_fallbacks"] = self._data.get("ollama_fallbacks", 0) + 1
        self._save()

    def summary(self) -> Dict:
        total    = self._data["total_analyses"]
        claude   = self._data["claude_calls"]
        skips    = self._data["ollama_skips"]
        saved    = self._data["total_saved_usd"]
        cost     = self._data["total_cost_usd"]
        skip_pct = round(skips / total * 100, 1) if total > 0 else 0.0

        # Heutiger Tag
        today     = date.today().isoformat()
        today_day = self._data["daily"].get(today, {})

        return {
            "total_analyses":   total,
            "claude_calls":     claude,
            "ollama_skips":     skips,
            "skip_rate_pct":    skip_pct,
            "total_cost_usd":   round(cost, 2),
            "total_saved_usd":  round(saved, 2),
            "today_cost_usd":   round(today_day.get("cost_usd", 0.0), 2),
            "today_saved_usd":  round(today_day.get("saved_usd", 0.0), 2),
            "today_claude":     today_day.get("claude", 0),
            "today_skips":      today_day.get("ollama_skips", 0),
            "cost_per_call":    _COST_PER_CLAUDE_CALL,
            "fallbacks":        self._data.get("ollama_fallbacks", 0),
        }
