"""
Per-Ticker Sentiment Reliability Tracker

After each closed trade, records the sentiment score at entry versus the
actual return achieved. Builds a per-ticker hit-rate that adjusts the buy
threshold: trustworthy tickers get a lower threshold; unreliable ones get a
higher bar.

Data stored in: data/sentiment_memory.json
Structure: {ticker: [{sentiment: float, return_pct: float, date: str}]}
           Maximum 50 records per ticker (oldest pruned first).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from typing import Dict, List

from logger import get_logger

log = get_logger(__name__)

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "sentiment_memory.json")

# Minimum records required before reliability is considered meaningful
_MIN_RECORDS = 5

# Maximum records retained per ticker
_MAX_RECORDS = 50

# Sentiment threshold above which a reading is considered a "buy signal"
_BUY_SIGNAL_THRESHOLD = 0.65


class SentimentMemory:
    """Tracks per-ticker sentiment vs. actual return to gauge signal reliability."""

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        ticker: str,
        sentiment_score: float,
        actual_return_pct: float,
    ) -> None:
        """
        Append a new sentiment/return observation for `ticker`.

        Keeps the most recent _MAX_RECORDS entries and saves atomically.
        """
        data = self._load()
        records: List[Dict] = data.get(ticker, [])

        records.append(
            {
                "sentiment": round(float(sentiment_score), 4),
                "return_pct": round(float(actual_return_pct), 4),
                "date": date.today().isoformat(),
            }
        )

        # Prune to the most recent _MAX_RECORDS entries
        if len(records) > _MAX_RECORDS:
            records = records[-_MAX_RECORDS:]

        data[ticker] = records
        self._save(data)
        log.debug(
            "SentimentMemory: recorded %s sentiment=%.3f return=%.2f%%",
            ticker, sentiment_score, actual_return_pct,
        )

    def get_reliability(self, ticker: str) -> float:
        """
        Return the fraction of buy-signal trades (sentiment > 0.65) that
        yielded a positive return.

        Returns 0.5 (neutral/unknown) when fewer than _MIN_RECORDS exist.
        """
        data = self._load()
        records: List[Dict] = data.get(ticker, [])

        buy_signal_trades = [
            r for r in records if r.get("sentiment", 0.0) > _BUY_SIGNAL_THRESHOLD
        ]

        if len(records) < _MIN_RECORDS or not buy_signal_trades:
            return 0.5

        winning = sum(
            1 for r in buy_signal_trades if r.get("return_pct", 0.0) > 0.0
        )
        return round(winning / len(buy_signal_trades), 4)

    def get_threshold_adjustment(self, ticker: str) -> float:
        """
        Return a float to add to the buy threshold based on reliability.

        Negative values lower the threshold (more aggressive buys for reliable
        tickers); positive values raise it (more conservative for shaky ones).
        Returns 0.0 when fewer than _MIN_RECORDS are available.
        """
        data = self._load()
        records: List[Dict] = data.get(ticker, [])

        if len(records) < _MIN_RECORDS:
            return 0.0

        reliability = self.get_reliability(ticker)

        if reliability >= 0.75:
            return -0.03
        if reliability >= 0.60:
            return -0.01
        if reliability >= 0.40:
            return 0.00
        if reliability >= 0.25:
            return +0.02
        return +0.04

    def get_all_stats(self) -> List[Dict]:
        """
        Return a list of per-ticker stats sorted by reliability descending.

        Each element contains: ticker, records, reliability, adjustment.
        """
        data = self._load()
        stats: List[Dict] = []

        for ticker, records in data.items():
            n = len(records)
            reliability = self.get_reliability(ticker)
            adjustment = self.get_threshold_adjustment(ticker)
            stats.append(
                {
                    "ticker": ticker,
                    "records": n,
                    "reliability": reliability,
                    "adjustment": adjustment,
                }
            )

        stats.sort(key=lambda x: x["reliability"], reverse=True)
        return stats

    def to_text(self) -> str:
        """
        Human-readable table of all tickers with their reliability scores,
        suitable for logging or display in the dashboard.
        """
        stats = self.get_all_stats()
        if not stats:
            return "SentimentMemory: no data recorded yet."

        header = f"{'Ticker':<8} {'Records':>7} {'Reliability':>11} {'Threshold Adj':>13}"
        separator = "-" * len(header)
        rows = [header, separator]

        for s in stats:
            reliability_str = (
                f"{s['reliability']:.1%}"
                if s["records"] >= _MIN_RECORDS
                else "  n/a   "
            )
            adj = s["adjustment"]
            adj_str = f"{adj:+.2f}" if s["records"] >= _MIN_RECORDS else "  n/a"
            rows.append(
                f"{s['ticker']:<8} {s['records']:>7} {reliability_str:>11} {adj_str:>13}"
            )

        return "\n".join(rows)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            with open(_FILE, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            log.warning("SentimentMemory: load error – %s", e)
            return {}

    @staticmethod
    def _save(data: Dict) -> None:
        try:
            data_dir = os.path.dirname(_FILE)
            os.makedirs(data_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=data_dir,
                suffix=".tmp",
                delete=False,
                encoding="utf-8",
            ) as tmp:
                json.dump(data, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, _FILE)
        except Exception as e:
            log.warning("SentimentMemory: save error – %s", e)
