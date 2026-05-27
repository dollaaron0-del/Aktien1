"""
SentimentCalibrator – Kalibriert den Buy-Threshold basierend auf historischen Win-Rates.

Liest Sentiment-Score-Buckets aus dem PerformanceTracker und findet den optimalen
Threshold, der historisch die beste Win-Rate bei ausreichend vielen Trades liefert.

Verwendung:
    from analyzers.sentiment_calibrator import SentimentCalibrator
    from portfolio.performance_tracker import PerformanceTracker

    tracker    = PerformanceTracker()
    calibrator = SentimentCalibrator(tracker)

    threshold = calibrator.get_calibrated_threshold(default=0.65)
    report    = calibrator.get_calibration_report()
"""
from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

from logger import get_logger

if TYPE_CHECKING:
    from portfolio.performance_tracker import PerformanceTracker

log = get_logger(__name__)

# Mindest-Trades pro Bucket damit der Bucket als aussagekräftig gilt
_MIN_TRADES_PER_BUCKET = 3

# Bucket-Untergrenzen (linke Seite der Ranges aus get_sentiment_score_buckets)
_BUCKET_LOWER_BOUNDS: Dict[str, float] = {
    "0.65–0.70": 0.65,
    "0.70–0.75": 0.70,
    "0.75–0.80": 0.75,
    "0.80–0.85": 0.80,
    "0.85–1.00": 0.85,
}


class SentimentCalibrator:
    """
    Analysiert historische Trade-Performance pro Sentiment-Score-Bucket
    und empfiehlt einen optimierten Buy-Threshold.
    """

    def __init__(self, performance_tracker: "PerformanceTracker") -> None:
        self._tracker = performance_tracker

    def get_calibrated_threshold(self, default: float = 0.65) -> float:
        """
        Berechnet den optimalen Buy-Threshold basierend auf historischen Win-Rates.

        Logik:
          1. Lade alle Sentiment-Buckets mit ihrer Win-Rate
          2. Filtere Buckets mit mind. MIN_TRADES_PER_BUCKET Trades
          3. Wähle den Bucket mit der höchsten Win-Rate
          4. Gib die untere Grenze dieses Buckets als Threshold zurück

        Args:
            default: Fallback-Threshold wenn keine ausreichenden Daten vorhanden

        Returns:
            Empfohlener Sentiment-Score-Threshold (z.B. 0.75)
        """
        buckets = self._get_valid_buckets(min_trades=_MIN_TRADES_PER_BUCKET)

        if not buckets:
            log.info(
                "SentimentCalibrator: Nicht genug Daten – verwende Standard-Threshold %.2f",
                default,
            )
            return default

        # Bestes Bucket nach Win-Rate suchen
        best = max(buckets, key=lambda b: b["win_rate_pct"])
        threshold = _BUCKET_LOWER_BOUNDS.get(best["score_range"], default)

        log.info(
            "SentimentCalibrator: Optimaler Threshold=%.2f "
            "(Bucket=%s, Win-Rate=%.1f%%, Trades=%d)",
            threshold,
            best["score_range"],
            best["win_rate_pct"],
            best["trades"],
        )
        return threshold

    def get_calibration_report(self) -> Dict:
        """
        Gibt einen vollständigen Kalibrierungsbericht zurück.

        Returns:
            Dict mit:
              - recommended_threshold: float
              - calibrated: bool (True wenn genug Daten vorhanden)
              - buckets: List[Dict] (alle Buckets mit Win-Rate, avg_return, trades)
              - best_bucket: str (Score-Range des besten Buckets)
              - total_trades: int
              - note: str (erläuternde Nachricht)
        """
        all_buckets = self._tracker.get_sentiment_score_buckets()
        valid_buckets = [b for b in all_buckets if b["trades"] >= _MIN_TRADES_PER_BUCKET]

        total_trades = sum(b["trades"] for b in all_buckets)
        calibrated   = bool(valid_buckets)

        if calibrated:
            best        = max(valid_buckets, key=lambda b: b["win_rate_pct"])
            best_bucket = best["score_range"]
            threshold   = _BUCKET_LOWER_BOUNDS.get(best_bucket, 0.65)
            note = (
                f"Empfehlung basiert auf {len(valid_buckets)} aussagekräftigen Buckets. "
                f"Bester Bucket: {best_bucket} mit {best['win_rate_pct']:.1f}% Win-Rate."
            )
        else:
            best_bucket = "–"
            threshold   = 0.65
            note = (
                f"Nicht genug Daten (Gesamt: {total_trades} Trades, "
                f"mind. {_MIN_TRADES_PER_BUCKET} pro Bucket nötig). "
                "Verwende Standard-Threshold."
            )

        return {
            "recommended_threshold": threshold,
            "calibrated":            calibrated,
            "buckets":               all_buckets,
            "best_bucket":           best_bucket,
            "total_trades":          total_trades,
            "note":                  note,
        }

    def needs_calibration(self, min_trades: int = 10) -> bool:
        """
        Prüft ob genug Trades vorhanden sind um eine sinnvolle Kalibrierung durchzuführen.

        Args:
            min_trades: Minimum an abgeschlossenen Trades für Kalibrierung

        Returns:
            True wenn Kalibrierung möglich und empfohlen
        """
        all_buckets  = self._tracker.get_sentiment_score_buckets()
        total_trades = sum(b["trades"] for b in all_buckets)

        result = total_trades >= min_trades
        log.debug(
            "SentimentCalibrator.needs_calibration: %d/%d Trades → %s",
            total_trades, min_trades, result,
        )
        return result

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _get_valid_buckets(self, min_trades: int) -> List[Dict]:
        """Gibt nur Buckets zurück die mind. min_trades Trades haben."""
        all_buckets = self._tracker.get_sentiment_score_buckets()
        return [b for b in all_buckets if b["trades"] >= min_trades]
