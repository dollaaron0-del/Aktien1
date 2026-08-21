"""
ReEntryTracker – verfolgt verkaufte Positionen 30 Tage lang und signalisiert
günstige Wiedereinstiegsgelegenheiten.

Ablauf:
  1. Nach jedem Verkauf: add_sold() aufrufen
  2. Periodisch: update_prices() mit aktuellen Kursen aufrufen
  3. get_candidates() gibt STRONG/MODERATE Signale zurück

Daten werden in data/reentry_watch.json gespeichert (atomare Schreibweise).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "reentry_watch.json")

# Sell reasons that are worth tracking for re-entry
_TRACKABLE_REASONS = {"take_profit", "hold_expired", "sentiment_sell", "thesis_broken"}


@dataclass
class ReEntryCandidate:
    ticker: str
    sell_price: float
    sell_date: str          # ISO8601
    sell_reason: str
    confidence: str         # HIGH / MEDIUM / LOW at time of sale
    low_since_sell: float   # lowest price seen since sale
    last_price: float
    days_watched: int
    re_entry_score: float   # 0.0–1.0
    signal: str             # "STRONG" | "MODERATE" | "WATCH" | "SKIP"


class ReEntryTracker:
    """Tracks sold positions and flags re-entry opportunities."""

    WATCH_DAYS = 30       # how long to watch a sold ticker
    MIN_DIP_PCT = 0.03    # price must have dipped at least 3% below sell price

    def __init__(self) -> None:
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def add_sold(
        self,
        ticker: str,
        sell_price: float,
        sell_reason: str,
        confidence: str = "MEDIUM",
    ) -> None:
        """
        Add a sold ticker to the watch list.

        Stop-loss exits are not tracked (they suggest fundamental problems).
        Tracked reasons: take_profit, hold_expired, sentiment_sell, thesis_broken.
        If the ticker already exists in the watch list it is updated in-place
        rather than duplicated.
        """
        reason_key = sell_reason.lower().strip()
        if reason_key not in _TRACKABLE_REASONS:
            log.debug(
                "ReEntryTracker: skipping %s – sell_reason '%s' not tracked",
                ticker,
                sell_reason,
            )
            return

        data = self._load()
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()

        # Update existing entry or create new one
        existing = next((d for d in data if d["ticker"] == ticker.upper()), None)
        if existing is not None:
            existing["sell_price"] = sell_price
            existing["sell_date"] = now_iso
            existing["sell_reason"] = sell_reason
            existing["confidence"] = confidence.upper()
            existing["low_since_sell"] = sell_price
            existing["last_price"] = sell_price
            existing["days_watched"] = 0
            existing["re_entry_score"] = 0.0
            existing["signal"] = "WATCH"
            log.info("ReEntryTracker: updated %s (sell=%.2f, reason=%s)", ticker, sell_price, sell_reason)
        else:
            data.append({
                "ticker": ticker.upper(),
                "sell_price": sell_price,
                "sell_date": now_iso,
                "sell_reason": sell_reason,
                "confidence": confidence.upper(),
                "low_since_sell": sell_price,
                "last_price": sell_price,
                "days_watched": 0,
                "re_entry_score": 0.0,
                "signal": "WATCH",
            })
            log.info("ReEntryTracker: added %s (sell=%.2f, reason=%s)", ticker, sell_price, sell_reason)

        self._save(data)

    def update_prices(self, prices: Dict[str, float]) -> None:
        """
        Update last_price and low_since_sell for all watched tickers.
        Removes entries older than WATCH_DAYS.
        Called periodically (e.g. once per trading session).
        """
        data = self._load()
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()
        surviving: List[Dict] = []

        for entry in data:
            try:
                sell_date = datetime.fromisoformat(entry["sell_date"]).date()
            except (KeyError, ValueError):
                sell_date = today

            days_watched = (today - sell_date).days
            entry["days_watched"] = days_watched

            if days_watched > self.WATCH_DAYS:
                log.debug("ReEntryTracker: dropping %s after %d days", entry["ticker"], days_watched)
                continue

            ticker = entry["ticker"]
            if ticker in prices:
                current = prices[ticker]
                entry["last_price"] = current
                if current < entry.get("low_since_sell", current):
                    entry["low_since_sell"] = current

            # Recompute score and signal
            score, signal = self._compute_score(entry)
            entry["re_entry_score"] = round(score, 4)
            entry["signal"] = signal

            surviving.append(entry)

        self._save(surviving)
        log.debug("ReEntryTracker: updated %d / %d entries", len(surviving), len(data))

    def get_candidates(self) -> List[ReEntryCandidate]:
        """
        Return tickers that qualify as attractive re-entry candidates.

        Qualification criteria:
          1. Dipped at least MIN_DIP_PCT below sell price after the sale.
          2. Currently recovering: last_price > low_since_sell * 1.03.
          3. Not chasing: last_price <= sell_price * 1.05.
          4. Signal is STRONG or MODERATE (score >= 0.40).
        """
        data = self._load()
        candidates: List[ReEntryCandidate] = []

        for entry in data:
            sell_price = entry.get("sell_price", 0.0)
            low = entry.get("low_since_sell", sell_price)
            last = entry.get("last_price", sell_price)

            dip_pct = (sell_price - low) / sell_price if sell_price > 0 else 0.0
            if dip_pct < self.MIN_DIP_PCT:
                continue
            if not (last > low * 1.03):
                continue
            if last > sell_price * 1.05:
                continue

            signal = entry.get("signal", "SKIP")
            if signal not in ("STRONG", "MODERATE"):
                continue

            candidates.append(self._to_candidate(entry))

        candidates.sort(key=lambda c: c.re_entry_score, reverse=True)
        return candidates

    def get_all_watched(self) -> List[ReEntryCandidate]:
        """Return all currently watched tickers (for display / dashboard)."""
        return [self._to_candidate(d) for d in self._load()]

    def to_text(self) -> str:
        """Human-readable summary table of watched tickers and re-entry candidates."""
        watched = self.get_all_watched()
        if not watched:
            return "ReEntry Watch: keine Positionen beobachtet."

        candidates = self.get_candidates()
        candidate_tickers = {c.ticker for c in candidates}

        lines = [
            "══ RE-ENTRY WATCH ══════════════════════════════════════════════════",
            f"{'Ticker':<8} {'Verkauft':>8} {'Tief':>8} {'Aktuell':>8} "
            f"{'Dip%':>6} {'Score':>6} {'Signal':<10} {'Tage':>4}",
            "─" * 68,
        ]

        for c in sorted(watched, key=lambda x: x.re_entry_score, reverse=True):
            sell_price = c.sell_price
            low = c.low_since_sell
            dip_pct = (sell_price - low) / sell_price * 100 if sell_price > 0 else 0.0
            marker = " <<" if c.ticker in candidate_tickers else ""
            lines.append(
                f"{c.ticker:<8} {sell_price:>8.2f} {low:>8.2f} {c.last_price:>8.2f} "
                f"{dip_pct:>5.1f}% {c.re_entry_score:>6.3f} {c.signal:<10} {c.days_watched:>4d}{marker}"
            )

        if candidates:
            lines.append("")
            lines.append(f"Aktive Kandidaten ({len(candidates)}): " + ", ".join(c.ticker for c in candidates))
        else:
            lines.append("")
            lines.append("Keine aktiven Kandidaten.")

        lines.append("════════════════════════════════════════════════════════════════════")
        return "\n".join(lines)

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _compute_score(entry: Dict) -> tuple[float, str]:
        """
        Compute re_entry_score and signal from raw entry dict.

        Score formula:
          dip_depth = (sell_price - low_since_sell) / sell_price
          recovery  = (last_price - low_since_sell) / max(sell_price - low_since_sell, 0.01)
          score     = min(1.0, dip_depth * 4 * recovery)

        Signals:
          >= 0.70 → STRONG
          >= 0.40 → MODERATE
          >= 0.20 → WATCH
          else    → SKIP
        """
        sell_price = entry.get("sell_price", 0.0)
        low = entry.get("low_since_sell", sell_price)
        last = entry.get("last_price", sell_price)

        if sell_price <= 0:
            return 0.0, "SKIP"

        dip_depth = (sell_price - low) / sell_price
        denominator = max(sell_price - low, 0.01)
        recovery = (last - low) / denominator
        score = min(1.0, dip_depth * 4.0 * recovery)
        score = max(0.0, score)

        if score >= 0.70:
            signal = "STRONG"
        elif score >= 0.40:
            signal = "MODERATE"
        elif score >= 0.20:
            signal = "WATCH"
        else:
            signal = "SKIP"

        return score, signal

    @staticmethod
    def _to_candidate(entry: Dict) -> ReEntryCandidate:
        return ReEntryCandidate(
            ticker=entry.get("ticker", ""),
            sell_price=entry.get("sell_price", 0.0),
            sell_date=entry.get("sell_date", ""),
            sell_reason=entry.get("sell_reason", ""),
            confidence=entry.get("confidence", "MEDIUM"),
            low_since_sell=entry.get("low_since_sell", entry.get("sell_price", 0.0)),
            last_price=entry.get("last_price", entry.get("sell_price", 0.0)),
            days_watched=entry.get("days_watched", 0),
            re_entry_score=entry.get("re_entry_score", 0.0),
            signal=entry.get("signal", "SKIP"),
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> List[Dict]:
        try:
            with open(_DATA_FILE) as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return []
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        except Exception as e:
            log.warning("ReEntryTracker: load error – %s", e)
            return []

    def _save(self, data: List[Dict]) -> None:
        try:
            os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=os.path.dirname(_DATA_FILE),
                suffix=".tmp",
                delete=False,
            ) as tmp:
                json.dump(data, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, _DATA_FILE)
        except Exception as e:
            log.warning("ReEntryTracker: save error – %s", e)
