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
# Prompt caching: cached tokens cost ~10% of normal input price
_CACHE_DISCOUNT = 0.90  # 90% saved on cached tokens (~3000 tokens × $0.015/1k × 0.9 ≈ $0.04/call)


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

    def record(self, claude_called: bool, ollama_used: bool, cache_hit_tokens: int = 0) -> None:
        """Einen Analyse-Vorgang erfassen."""
        today = date.today().isoformat()

        self._data["total_analyses"] += 1

        if today not in self._data["daily"]:
            self._data["daily"][today] = {
                "analyses": 0, "claude": 0, "ollama_skips": 0,
                "cost_usd": 0.0, "saved_usd": 0.0, "cache_saved_usd": 0.0,
            }

        day = self._data["daily"][today]
        day["analyses"] += 1

        if claude_called:
            # Estimate cache savings: cached_tokens × price × discount_rate
            cache_saved = round(cache_hit_tokens / 1000 * 0.015 * _CACHE_DISCOUNT, 5) if cache_hit_tokens else 0.0
            actual_cost = round(_COST_PER_CLAUDE_CALL - cache_saved, 4)

            self._data["claude_calls"]  += 1
            self._data["total_cost_usd"] = round(self._data["total_cost_usd"] + actual_cost, 4)
            self._data["total_saved_usd"] = round(
                self._data["total_saved_usd"] + cache_saved, 4
            )
            self._data["cache_hits"] = self._data.get("cache_hits", 0) + (1 if cache_hit_tokens else 0)

            day["claude"]          += 1
            day["cost_usd"]         = round(day["cost_usd"] + actual_cost, 4)
            day["cache_saved_usd"]  = round(day.get("cache_saved_usd", 0.0) + cache_saved, 4)
        else:
            saved = _COST_PER_CLAUDE_CALL
            self._data["ollama_skips"]    += 1
            self._data["total_saved_usd"]  = round(self._data["total_saved_usd"] + saved, 4)
            day["ollama_skips"] += 1
            day["saved_usd"]     = round(day["saved_usd"] + saved, 4)

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

        today     = date.today().isoformat()
        today_day = self._data["daily"].get(today, {})

        return {
            "total_analyses":       total,
            "claude_calls":         claude,
            "ollama_skips":         skips,
            "skip_rate_pct":        skip_pct,
            "total_cost_usd":       round(cost, 2),
            "total_saved_usd":      round(saved, 2),
            "cache_hits":           self._data.get("cache_hits", 0),
            "today_cost_usd":       round(today_day.get("cost_usd", 0.0), 2),
            "today_saved_usd":      round(today_day.get("saved_usd", 0.0), 2),
            "today_cache_saved":    round(today_day.get("cache_saved_usd", 0.0), 4),
            "today_claude":         today_day.get("claude", 0),
            "today_skips":          today_day.get("ollama_skips", 0),
            "cost_per_call":        _COST_PER_CLAUDE_CALL,
            "fallbacks":            self._data.get("ollama_fallbacks", 0),
        }
