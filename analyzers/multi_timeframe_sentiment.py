"""
MultiTimeframeSentiment – vergleicht Sentiment über 1d, 7d und 30d Zeitfenster
und bestimmt Trendrichtung sowie Momentum-Bonus für Kauf-/Verkaufsschwellen.

Verwendung:
  mts = MultiTimeframeSentiment()
  result = mts.analyze("AAPL", articles_by_date)
  print(mts.to_text(result))

articles_by_date Format:
  {"2025-05-16": [{"sentiment_score": 0.72, ...}, ...], ...}
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

# Threshold for calling a trend "strong"
_STRONG_THRESHOLD = 0.6

# Maximum spread used for normalising trend_strength to 0–1
_MAX_SPREAD = 0.30

# Minimum difference between consecutive timeframe scores to confirm a trend
_MIN_STEP = 0.03


@dataclass
class MultiTimeframeResult:
    ticker: str
    score_1d: Optional[float]   # today's average sentiment (None if no data)
    score_7d: Optional[float]   # 7-day weighted rolling average
    score_30d: Optional[float]  # 30-day uniform rolling average
    trend: str                  # "UPTREND" | "DOWNTREND" | "NEUTRAL" | "UNKNOWN"
    trend_strength: float       # 0.0–1.0
    momentum_bonus: float       # threshold adjustment –0.04 … +0.04
    summary: str                # human-readable one-liner


class MultiTimeframeSentiment:
    """Analyses sentiment across multiple timeframes to detect trend direction."""

    def analyze(
        self,
        ticker: str,
        articles_by_date: Dict[str, List[Dict]],
    ) -> MultiTimeframeResult:
        """
        Compute multi-timeframe sentiment for a single ticker.

        Parameters
        ----------
        ticker:
            Stock symbol (used only for labelling the result).
        articles_by_date:
            Mapping of ISO date strings to lists of article dicts.
            Each article dict must contain a ``sentiment_score`` key (float 0–1).

        Returns
        -------
        MultiTimeframeResult with trend classification and momentum_bonus.
        """
        today = datetime.now(timezone.utc).replace(tzinfo=None).date()

        score_1d = self._score_1d(articles_by_date, today)
        score_7d = self._score_7d(articles_by_date, today)
        score_30d = self._score_30d(articles_by_date, today)

        available = [s for s in (score_1d, score_7d, score_30d) if s is not None]

        if len(available) < 2:
            return MultiTimeframeResult(
                ticker=ticker,
                score_1d=score_1d,
                score_7d=score_7d,
                score_30d=score_30d,
                trend="UNKNOWN",
                trend_strength=0.0,
                momentum_bonus=0.0,
                summary=f"{ticker}: insufficient data for trend detection",
            )

        trend = self._detect_trend(score_1d, score_7d, score_30d)
        trend_strength = self._compute_strength(score_1d, score_30d)
        momentum_bonus = self._compute_bonus(trend, trend_strength)
        summary = self._build_summary(ticker, score_1d, score_7d, score_30d, trend, trend_strength)

        log.debug(
            "MultiTimeframeSentiment %s: 1d=%s 7d=%s 30d=%s → %s (strength=%.2f bonus=%+.2f)",
            ticker,
            f"{score_1d:.3f}" if score_1d is not None else "N/A",
            f"{score_7d:.3f}" if score_7d is not None else "N/A",
            f"{score_30d:.3f}" if score_30d is not None else "N/A",
            trend,
            trend_strength,
            momentum_bonus,
        )

        return MultiTimeframeResult(
            ticker=ticker,
            score_1d=score_1d,
            score_7d=score_7d,
            score_30d=score_30d,
            trend=trend,
            trend_strength=trend_strength,
            momentum_bonus=momentum_bonus,
            summary=summary,
        )

    def to_text(self, result: MultiTimeframeResult) -> str:
        """
        One-line summary suitable for logs or terminal output.

        Example:
          AAPL: 1d=0.72 7d=0.65 30d=0.58 → UPTREND (stark)
        """
        fmt_score = lambda s: f"{s:.2f}" if s is not None else " N/A"
        parts = [
            f"{result.ticker}:",
            f"1d={fmt_score(result.score_1d)}",
            f"7d={fmt_score(result.score_7d)}",
            f"30d={fmt_score(result.score_30d)}",
            f"→ {result.trend}",
        ]
        if result.trend != "UNKNOWN":
            strength_label = "stark" if result.trend_strength >= _STRONG_THRESHOLD else "moderat"
            parts.append(f"({strength_label})")
        if result.momentum_bonus != 0.0:
            parts.append(f"bonus={result.momentum_bonus:+.2f}")
        return " ".join(parts)

    # ── Timeframe scorers ─────────────────────────────────────────────────────

    @staticmethod
    def _score_1d(
        articles_by_date: Dict[str, List[Dict]],
        today,
    ) -> Optional[float]:
        """Plain mean of today's articles."""
        key = today.isoformat()
        articles = articles_by_date.get(key, [])
        scores = [
            a["sentiment_score"]
            for a in articles
            if isinstance(a.get("sentiment_score"), (int, float))
        ]
        if not scores:
            return None
        return sum(scores) / len(scores)

    @staticmethod
    def _score_7d(
        articles_by_date: Dict[str, List[Dict]],
        today,
    ) -> Optional[float]:
        """
        Weighted mean over the last 7 days.
        Day 0 (oldest in window) gets weight 1, day 6 (today) gets weight 7.
        """
        total_weight = 0.0
        weighted_sum = 0.0

        for offset in range(7):
            day = today - timedelta(days=6 - offset)  # offset 0 = 6 days ago, offset 6 = today
            key = day.isoformat()
            weight = offset + 1  # 1 … 7
            articles = articles_by_date.get(key, [])
            scores = [
                a["sentiment_score"]
                for a in articles
                if isinstance(a.get("sentiment_score"), (int, float))
            ]
            if scores:
                day_mean = sum(scores) / len(scores)
                weighted_sum += day_mean * weight
                total_weight += weight

        if total_weight == 0.0:
            return None
        return weighted_sum / total_weight

    @staticmethod
    def _score_30d(
        articles_by_date: Dict[str, List[Dict]],
        today,
    ) -> Optional[float]:
        """Uniform mean over the last 30 days."""
        all_scores: List[float] = []

        for offset in range(30):
            day = today - timedelta(days=29 - offset)
            key = day.isoformat()
            articles = articles_by_date.get(key, [])
            scores = [
                a["sentiment_score"]
                for a in articles
                if isinstance(a.get("sentiment_score"), (int, float))
            ]
            all_scores.extend(scores)

        if not all_scores:
            return None
        return sum(all_scores) / len(all_scores)

    # ── Trend detection ───────────────────────────────────────────────────────

    @staticmethod
    def _detect_trend(
        score_1d: Optional[float],
        score_7d: Optional[float],
        score_30d: Optional[float],
    ) -> str:
        """
        Classify trend from three timeframe scores.

        UPTREND:   score_1d > score_7d > score_30d, each step >= MIN_STEP
        DOWNTREND: score_1d < score_7d < score_30d, each step >= MIN_STEP
        NEUTRAL:   otherwise (when enough data is available)
        UNKNOWN:   fewer than two non-None scores
        """
        available = [s for s in (score_1d, score_7d, score_30d) if s is not None]
        if len(available) < 2:
            return "UNKNOWN"

        # Full three-window comparison
        if all(s is not None for s in (score_1d, score_7d, score_30d)):
            if (
                score_1d - score_7d >= _MIN_STEP
                and score_7d - score_30d >= _MIN_STEP
            ):
                return "UPTREND"
            if (
                score_7d - score_1d >= _MIN_STEP
                and score_30d - score_7d >= _MIN_STEP
            ):
                return "DOWNTREND"
            return "NEUTRAL"

        # Two-window fallback
        pairs = [
            (score_1d, score_7d),
            (score_1d, score_30d),
            (score_7d, score_30d),
        ]
        for newer, older in pairs:
            if newer is None or older is None:
                continue
            if newer - older >= _MIN_STEP:
                return "UPTREND"
            if older - newer >= _MIN_STEP:
                return "DOWNTREND"
            return "NEUTRAL"

        return "NEUTRAL"

    @staticmethod
    def _compute_strength(
        score_1d: Optional[float],
        score_30d: Optional[float],
    ) -> float:
        """
        trend_strength = |score_1d - score_30d| capped at MAX_SPREAD, normalised to 0–1.
        Falls back to |score_1d| or 0.0 if 30d not available.
        """
        if score_1d is None:
            return 0.0
        if score_30d is None:
            # Use absolute deviation from 0.5 (neutral centre) as proxy
            return min(abs(score_1d - 0.5) * 2, 1.0)
        spread = abs(score_1d - score_30d)
        return min(spread / _MAX_SPREAD, 1.0)

    @staticmethod
    def _compute_bonus(trend: str, strength: float) -> float:
        """
        Return threshold adjustment based on trend and strength.

          UPTREND strong (>= 0.6):   -0.04  (lower buy threshold → easier to buy)
          UPTREND moderate:          -0.02
          DOWNTREND strong:          +0.04  (raise buy threshold → harder to buy)
          DOWNTREND moderate:        +0.02
          NEUTRAL / UNKNOWN:          0.00
        """
        if trend == "UPTREND":
            return -0.04 if strength >= _STRONG_THRESHOLD else -0.02
        if trend == "DOWNTREND":
            return +0.04 if strength >= _STRONG_THRESHOLD else +0.02
        return 0.0

    @staticmethod
    def _build_summary(
        ticker: str,
        score_1d: Optional[float],
        score_7d: Optional[float],
        score_30d: Optional[float],
        trend: str,
        trend_strength: float,
    ) -> str:
        fmt = lambda s: f"{s:.2f}" if s is not None else "N/A"
        strength_label = "stark" if trend_strength >= _STRONG_THRESHOLD else "moderat"
        return (
            f"{ticker}: 1d={fmt(score_1d)} 7d={fmt(score_7d)} 30d={fmt(score_30d)}"
            f" → {trend} ({strength_label})"
        )
