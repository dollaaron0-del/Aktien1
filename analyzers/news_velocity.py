"""
News Velocity / Acceleration Detector

Tracks how fast news articles appear for a given ticker and detects spikes
that may serve as early warnings before significant price moves.

Data stored in: data/news_velocity.json
Structure: {ticker: [{ts: ISO8601, count: int}]}  # hourly buckets, last 30 days
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List

from logger import get_logger

log = get_logger(__name__)

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "news_velocity.json")

# Acceleration labels
SPIKE  = "SPIKE"
HIGH   = "HIGH"
NORMAL = "NORMAL"
LOW    = "LOW"

# Retention window for stored buckets
_RETENTION_DAYS = 30


@dataclass
class VelocityResult:
    ticker: str
    articles_1h: int
    articles_6h: int
    articles_24h: int
    baseline_per_day: float   # rolling 7-day daily average (excluding today)
    velocity_score: float     # 0.0–1.0, where 1.0 = huge spike
    acceleration: str         # "SPIKE" | "HIGH" | "NORMAL" | "LOW"
    signal_boost: float       # sentiment multiplier


class NewsVelocityAnalyzer:
    """Records article counts in hourly buckets and detects velocity spikes."""

    # ── Public API ────────────────────────────────────────────────────────────

    def record_articles(self, ticker: str, articles: List[Dict]) -> None:
        """
        Bucket the provided articles into the current UTC hour and persist.

        Each element in `articles` must contain at least a 'published' key
        (ISO8601 string) or any key that can be used to identify the article.
        The count recorded is simply len(articles) – callers should pass only
        new, de-duplicated articles for the current fetch cycle.
        """
        if not articles:
            return

        data = self._load()
        ticker_buckets: List[Dict] = data.get(ticker, [])

        # Current hour bucket key (UTC, truncated to the hour)
        now_hour = datetime.now(timezone.utc).replace(
            minute=0, second=0, microsecond=0
        )
        bucket_ts = now_hour.isoformat()

        # Find existing bucket for this hour or create a new one
        for bucket in ticker_buckets:
            if bucket.get("ts") == bucket_ts:
                bucket["count"] = bucket.get("count", 0) + len(articles)
                break
        else:
            ticker_buckets.append({"ts": bucket_ts, "count": len(articles)})

        # Prune buckets older than _RETENTION_DAYS
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)
        ).isoformat()
        ticker_buckets = [b for b in ticker_buckets if b.get("ts", "") >= cutoff]

        data[ticker] = ticker_buckets
        self._save(data)
        log.debug(
            "NewsVelocity: recorded %d articles for %s (bucket %s)",
            len(articles), ticker, bucket_ts,
        )

    def analyze(self, ticker: str) -> VelocityResult:
        """Compute velocity metrics and return a VelocityResult for `ticker`."""
        data = self._load()
        buckets: List[Dict] = data.get(ticker, [])

        now = datetime.now(timezone.utc)

        articles_1h  = self._count_in_window(buckets, now, hours=1)
        articles_6h  = self._count_in_window(buckets, now, hours=6)
        articles_24h = self._count_in_window(buckets, now, hours=24)

        baseline_per_day = self._baseline(buckets, now)

        # velocity_score: 0.33 ≈ 1× baseline, 0.66 ≈ 2×, 1.0 ≈ 3×+
        velocity_score = min(
            1.0, articles_24h / max(baseline_per_day, 1.0) / 3.0
        )

        acceleration = self._classify(
            articles_24h, articles_6h, articles_1h, baseline_per_day
        )

        boost_map = {SPIKE: 1.35, HIGH: 1.15, NORMAL: 1.0, LOW: 0.90}
        signal_boost = boost_map[acceleration]

        return VelocityResult(
            ticker=ticker,
            articles_1h=articles_1h,
            articles_6h=articles_6h,
            articles_24h=articles_24h,
            baseline_per_day=round(baseline_per_day, 2),
            velocity_score=round(velocity_score, 3),
            acceleration=acceleration,
            signal_boost=signal_boost,
        )

    def to_text(self, ticker: str) -> str:
        """One-line summary suitable for logging."""
        r = self.analyze(ticker)
        return (
            f"{ticker} news velocity: {r.acceleration} "
            f"(1h={r.articles_1h}, 6h={r.articles_6h}, 24h={r.articles_24h} | "
            f"baseline={r.baseline_per_day:.1f}/day | "
            f"score={r.velocity_score:.2f}, boost={r.signal_boost:.2f})"
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _count_in_window(
        buckets: List[Dict], now: datetime, hours: int
    ) -> int:
        cutoff = (now - timedelta(hours=hours)).isoformat()
        return sum(
            b.get("count", 0)
            for b in buckets
            if b.get("ts", "") >= cutoff
        )

    @staticmethod
    def _baseline(buckets: List[Dict], now: datetime) -> float:
        """
        Rolling 7-day daily average, excluding today's data so the baseline
        is not polluted by the very spike we are trying to detect.
        """
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        seven_days_ago = (now - timedelta(days=7)).isoformat()

        daily_counts: Dict[str, int] = {}
        for b in buckets:
            ts = b.get("ts", "")
            if ts < seven_days_ago or ts >= today_start:
                continue
            day_key = ts[:10]  # YYYY-MM-DD
            daily_counts[day_key] = daily_counts.get(day_key, 0) + b.get("count", 0)

        if not daily_counts:
            return 0.0
        return sum(daily_counts.values()) / len(daily_counts)

    @staticmethod
    def _classify(
        articles_24h: int,
        articles_6h: int,
        articles_1h: int,
        baseline: float,
    ) -> str:
        effective_baseline = max(baseline, 1.0)

        if articles_24h < 0.5 * effective_baseline:
            return LOW
        if articles_24h >= 3 * effective_baseline and articles_6h >= articles_1h * 2:
            return SPIKE
        if articles_24h >= 2 * effective_baseline:
            return HIGH
        return NORMAL

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            with open(_FILE, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            log.warning("NewsVelocityAnalyzer: load error – %s", e)
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
            log.warning("NewsVelocityAnalyzer: save error – %s", e)
