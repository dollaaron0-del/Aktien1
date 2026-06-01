"""
PEADTracker – Post-Earnings Drift Tracking.

Aktien die Earnings stark übertreffen (EPS-Surprise ≥5%) steigen statistisch
noch 5-20 Tage weiter (Post-Earnings Drift / PEAD-Effekt).

Strategie: 24-48h nach starkem Beat analysieren und kaufen falls Trend bestätigt.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, date
from typing import Dict, List, Optional

from analyzers.earnings_surprise import EarningsSurprise

_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "pead_queue.json")

_PEAD_MIN_DAYS = 1       # Wait at least 1 day after earnings
_PEAD_MAX_DAYS = 10      # PEAD effect expires after 10 days
_CLEANUP_DAYS  = 12      # Remove entries older than this
_MIN_SURPRISE_PCT = 0.05 # Only track BEAT (≥5%) and STRONG_BEAT (≥15%)
_BEAT_LABELS = {"BEAT", "STRONG_BEAT"}


class PEADTracker:
    """Tracks Post-Earnings Drift candidates after strong earnings beats."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_watchlist(self, watchlist: List[str]) -> List[Dict]:
        """
        Check each ticker for recent earnings beats and add new entries to
        the PEAD queue.

        Returns newly found candidates (not already in the queue).
        """
        try:
            queue   = self._load()
            checker = EarningsSurprise()
            new_entries: List[Dict] = []

            for ticker in watchlist:
                try:
                    result = checker.check(ticker)
                except Exception:
                    continue

                label     = result.get("label", "UNKNOWN")
                days_ago  = result.get("days_ago")
                surprise  = result.get("surprise_pct", 0.0)
                earn_date = result.get("earnings_date")

                if label not in _BEAT_LABELS:
                    continue
                if days_ago is None or not (_PEAD_MIN_DAYS <= days_ago <= _PEAD_MAX_DAYS):
                    continue
                if surprise < _MIN_SURPRISE_PCT:
                    continue

                # Avoid duplicates: same ticker + same earnings_date
                already_queued = any(
                    e.get("ticker") == ticker and e.get("earnings_date") == earn_date
                    for e in queue
                )
                if already_queued:
                    continue

                entry = {
                    "ticker":        ticker,
                    "surprise_pct":  surprise,
                    "label":         label,
                    "earnings_date": earn_date,
                    "queued_at":     datetime.utcnow().isoformat(),
                    "notified":      False,
                }
                queue.append(entry)
                new_entries.append(entry)

            if new_entries:
                self._save(queue)

            return new_entries
        except Exception:
            return []

    def get_ready_for_analysis(self) -> List[Dict]:
        """
        Return entries that are ready for PEAD analysis:
          - At least _PEAD_MIN_DAYS after earnings (≥ 24 h)
          - Not older than _PEAD_MAX_DAYS (drift window still open)
          - Not already re-queued today (queued_at date != today)
        """
        try:
            queue  = self._load()
            today  = date.today()
            result = []

            for entry in queue:
                earn_date = self._parse_date(entry.get("earnings_date"))
                if earn_date is None:
                    continue

                days_ago = (today - earn_date).days
                if not (_PEAD_MIN_DAYS <= days_ago <= _PEAD_MAX_DAYS):
                    continue

                queued_at = self._parse_date(entry.get("queued_at", "")[:10])
                if queued_at == today:
                    continue

                result.append(entry)

            return result
        except Exception:
            return []

    def mark_queued(self, ticker: str) -> None:
        """Update queued_at to now for the given ticker."""
        try:
            queue = self._load()
            now   = datetime.utcnow().isoformat()
            for entry in queue:
                if entry.get("ticker") == ticker:
                    entry["queued_at"] = now
            self._save(queue)
        except Exception:
            pass

    def cleanup_expired(self) -> int:
        """Remove entries older than _CLEANUP_DAYS. Returns count removed."""
        try:
            queue   = self._load()
            today   = date.today()
            kept    = []
            removed = 0

            for entry in queue:
                earn_date = self._parse_date(entry.get("earnings_date"))
                if earn_date is not None and (today - earn_date).days > _CLEANUP_DAYS:
                    removed += 1
                else:
                    kept.append(entry)

            if removed:
                self._save(kept)

            return removed
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load(self) -> List[Dict]:
        try:
            with open(_DATA_FILE) as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, queue: List[Dict]) -> None:
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
        with open(_DATA_FILE, "w") as f:
            json.dump(queue, f, indent=2)

    @staticmethod
    def _parse_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
