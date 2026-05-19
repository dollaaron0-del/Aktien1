"""
Earnings Surprise Predictor

Schätzt ob ein Quartal besser/schlechter als erwartet wird, basierend auf
kostenlosen Proxy-Signalen:

  Signal 1: Job-Listings-Trend (via JobListingsCollector)
  Signal 2: App-Rating-Trend   (iTunes Lookup API)
  Signal 3: Web-Traffic-Proxy  (Google Trends via pytrends)

EarningsPrediction.direction:
  score > 0.65 → BEAT
  score < 0.35 → MISS
  sonst        → INLINE

Cache: data/earnings_predictions.json, TTL 24 Stunden
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, Optional

import requests

from logger import get_logger
from collectors.job_listings_collector import JobListingsCollector

log = get_logger(__name__)

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "earnings_predictions.json")
_CACHE_TTL_HOURS = 24
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0)"}

# Known app bundle IDs per ticker (Proxy for consumer sentiment)
_APP_BUNDLE_IDS: Dict[str, str] = {
    "AAPL": "com.apple.mobilesafari",
    "MSFT": "com.microsoft.Office.Outlook",
    "AMZN": "com.amazon.Amazon",
    "META": "com.facebook.Facebook",
    "GOOGL": "com.google.Maps",
    "NFLX": "com.netflix.Netflix",
    "TSLA": "com.teslamotors.tesla",
}

_BEAT_THRESHOLD = 0.65
_MISS_THRESHOLD = 0.35


@dataclass
class EarningsPrediction:
    ticker: str
    timestamp: str
    score: float                  # 0.0 – 1.0
    direction: str                # BEAT | MISS | INLINE
    confidence: str               # LOW | MEDIUM | HIGH
    signals: Dict = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        lines = [
            f"=== Earnings Prediction: {self.ticker} ===",
            f"Timestamp  : {self.timestamp}",
            f"Score      : {self.score:.2f}",
            f"Direction  : {self.direction}",
            f"Confidence : {self.confidence}",
            "Signals:",
        ]
        for name, info in self.signals.items():
            lines.append(f"  {name}: {info}")
        return "\n".join(lines)


class EarningsPredictor:
    """Predicts earnings surprise direction from free proxy signals."""

    def __init__(self) -> None:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        self._job_collector = JobListingsCollector()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def predict(self, ticker: str) -> EarningsPrediction:
        cached = self._load_cache(ticker)
        if cached is not None:
            log.debug("earnings_predictor: using cached prediction for %s", ticker)
            return cached

        log.info("earnings_predictor: computing prediction for %s", ticker)
        prediction = self._build_prediction(ticker)
        self._save_cache(prediction)
        return prediction

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------

    def _build_prediction(self, ticker: str) -> EarningsPrediction:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        signal_scores: list[float] = []
        signals: Dict = {}

        # Signal 1: Job listings trend
        job_score, job_info = self._signal_job_listings(ticker)
        signal_scores.append(job_score)
        signals["job_listings"] = job_info

        # Signal 2: App rating trend
        app_score, app_info = self._signal_app_rating(ticker)
        signal_scores.append(app_score)
        signals["app_rating"] = app_info

        # Signal 3: Web traffic proxy (Google Trends)
        trend_score, trend_info = self._signal_google_trends(ticker)
        signal_scores.append(trend_score)
        signals["google_trends"] = trend_info

        score = round(sum(signal_scores) / len(signal_scores), 3)

        direction = (
            "BEAT" if score > _BEAT_THRESHOLD
            else "MISS" if score < _MISS_THRESHOLD
            else "INLINE"
        )

        valid_signals = sum(1 for s in signal_scores if s != 0.5)
        confidence = (
            "HIGH" if valid_signals >= 3
            else "MEDIUM" if valid_signals == 2
            else "LOW"
        )

        return EarningsPrediction(
            ticker=ticker,
            timestamp=now,
            score=score,
            direction=direction,
            confidence=confidence,
            signals=signals,
        )

    def _signal_job_listings(self, ticker: str) -> tuple[float, dict]:
        """More recent job postings → growing company → bullish proxy."""
        try:
            listings = self._job_collector.collect(ticker, lookback_days=90)
            count = len(listings)
            score = min(1.0, count / 10)
            return score, {"listing_count": count, "score": round(score, 3)}
        except Exception as exc:
            log.warning("earnings_predictor: job listings signal failed for %s: %s", ticker, exc)
            return 0.5, {"error": str(exc), "score": 0.5}

    def _signal_app_rating(self, ticker: str) -> tuple[float, dict]:
        """iTunes Lookup API — higher average rating → better user experience → bullish."""
        bundle_id = _APP_BUNDLE_IDS.get(ticker.upper())
        if not bundle_id:
            return 0.5, {"note": "no app bundle ID mapped", "score": 0.5}

        url = f"https://itunes.apple.com/lookup?bundleId={bundle_id}&country=us"
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return 0.5, {"note": "app not found", "score": 0.5}

            app = results[0]
            rating = float(app.get("averageUserRating", 3.0))
            rating_count = int(app.get("userRatingCount", 0))
            # Normalise: 5-star scale; 4.0+ = bullish, 3.0- = bearish
            score = min(1.0, max(0.0, (rating - 2.0) / 3.0))
            return score, {
                "bundle_id": bundle_id,
                "rating": rating,
                "rating_count": rating_count,
                "score": round(score, 3),
            }
        except Exception as exc:
            log.warning("earnings_predictor: app rating signal failed for %s: %s", ticker, exc)
            return 0.5, {"error": str(exc), "score": 0.5}

    def _signal_google_trends(self, ticker: str) -> tuple[float, dict]:
        """Google Trends via pytrends — rising brand search = growing interest."""
        try:
            from pytrends.request import TrendReq  # type: ignore

            pt = TrendReq(hl="en-US", tz=0, timeout=(5, 15))
            pt.build_payload([ticker], timeframe="today 3-m", geo="US")
            df = pt.interest_over_time()
            if df is None or df.empty or ticker not in df.columns:
                return 0.5, {"note": "no trend data", "score": 0.5}

            series = df[ticker].dropna()
            if len(series) < 4:
                return 0.5, {"note": "insufficient trend data", "score": 0.5}

            half = len(series) // 2
            first_half_avg = float(series.iloc[:half].mean())
            second_half_avg = float(series.iloc[half:].mean())

            if first_half_avg == 0:
                return 0.5, {"note": "zero baseline in trends", "score": 0.5}

            change_pct = (second_half_avg - first_half_avg) / first_half_avg * 100
            # Map: +50% = 1.0, -50% = 0.0
            score = min(1.0, max(0.0, 0.5 + change_pct / 100))
            return score, {
                "first_half_avg": round(first_half_avg, 1),
                "second_half_avg": round(second_half_avg, 1),
                "change_pct": round(change_pct, 1),
                "score": round(score, 3),
            }
        except ImportError:
            log.debug("earnings_predictor: pytrends not installed, using neutral fallback")
            return 0.5, {"note": "pytrends not available", "score": 0.5}
        except Exception as exc:
            log.warning("earnings_predictor: google trends signal failed for %s: %s", ticker, exc)
            return 0.5, {"error": str(exc), "score": 0.5}

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cache(self, ticker: str) -> Optional[EarningsPrediction]:
        try:
            if not os.path.exists(_CACHE_PATH):
                return None
            with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                store: dict = json.load(fh)
            entry = store.get(ticker)
            if not entry:
                return None
            ts = datetime.fromisoformat(entry["timestamp"].rstrip("Z"))
            if datetime.utcnow() - ts > timedelta(hours=_CACHE_TTL_HOURS):
                return None
            return EarningsPrediction(**entry)
        except Exception as exc:
            log.warning("earnings_predictor: cache load failed: %s", exc)
            return None

    def _save_cache(self, prediction: EarningsPrediction) -> None:
        try:
            store: dict = {}
            if os.path.exists(_CACHE_PATH):
                try:
                    with open(_CACHE_PATH, "r", encoding="utf-8") as fh:
                        store = json.load(fh)
                except Exception:
                    pass
            store[prediction.ticker] = asdict(prediction)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_CACHE_PATH), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(store, fh, indent=2)
            os.replace(tmp, _CACHE_PATH)
            log.debug("earnings_predictor: cache saved for %s", prediction.ticker)
        except Exception as exc:
            log.warning("earnings_predictor: cache save failed: %s", exc)
