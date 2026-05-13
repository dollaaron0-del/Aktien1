"""Kelly Criterion position sizing based on historical performance."""
from typing import Optional
from portfolio.performance_tracker import PerformanceTracker


class KellySizer:
    """
    Computes mathematically optimal position size using Kelly Criterion.
    Uses fractional Kelly (default 25%) for safety against overestimation.
    """

    def __init__(
        self,
        tracker: PerformanceTracker,
        fraction: float = 0.25,
        min_trades: int = 10,
        cap: float = 0.25,
        floor: float = 0.02,
    ):
        self.tracker = tracker
        self.fraction = fraction
        self.min_trades = min_trades
        self.cap = cap
        self.floor = floor

    def compute(self, fallback_pct: float) -> float:
        """Returns position size as fraction of portfolio. Falls back if insufficient data."""
        cursor = self.tracker._conn.execute(
            """SELECT actual_return_pct FROM predictions
               WHERE sell_date IS NOT NULL"""
        )
        returns = [row["actual_return_pct"] or 0 for row in cursor.fetchall()]
        if len(returns) < self.min_trades:
            return fallback_pct

        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        if not wins or not losses:
            return fallback_pct

        win_rate = len(wins) / len(returns)
        avg_win = sum(wins) / len(wins) / 100  # convert % to fraction
        avg_loss = abs(sum(losses) / len(losses)) / 100
        if avg_loss == 0:
            return fallback_pct

        b = avg_win / avg_loss
        kelly = (win_rate * b - (1 - win_rate)) / b
        sized = max(self.floor, min(self.cap, kelly * self.fraction))
        return sized

    def info(self) -> dict:
        cursor = self.tracker._conn.execute(
            """SELECT actual_return_pct FROM predictions
               WHERE sell_date IS NOT NULL"""
        )
        returns = [row["actual_return_pct"] or 0 for row in cursor.fetchall()]
        if len(returns) < self.min_trades:
            return {"available": False, "trades": len(returns), "need": self.min_trades}
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        if not wins or not losses:
            return {"available": False, "trades": len(returns), "reason": "missing wins or losses"}
        win_rate = len(wins) / len(returns)
        avg_win = sum(wins) / len(wins) / 100
        avg_loss = abs(sum(losses) / len(losses)) / 100
        b = avg_win / avg_loss if avg_loss else 0
        kelly = (win_rate * b - (1 - win_rate)) / b if b else 0
        return {
            "available": True,
            "trades": len(returns),
            "win_rate": round(win_rate * 100, 1),
            "avg_win_pct": round(avg_win * 100, 2),
            "avg_loss_pct": round(avg_loss * 100, 2),
            "kelly_full": round(kelly * 100, 2),
            "kelly_fractional": round(max(self.floor, min(self.cap, kelly * self.fraction)) * 100, 2),
            "fraction_used": self.fraction,
        }
