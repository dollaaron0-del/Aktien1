"""Sector / correlation risk check before opening a new position."""
from typing import Dict, List, Optional
import yfinance as yf


class CorrelationChecker:
    """Prevents over-concentration in a single sector."""

    def __init__(self, max_sector_pct: float = 0.40):
        self.max_sector_pct = max_sector_pct
        self._sector_cache: Dict[str, str] = {}

    def get_sector(self, ticker: str) -> str:
        if ticker in self._sector_cache:
            return self._sector_cache[ticker]
        sector = "Unknown"
        try:
            info = yf.Ticker(ticker).info or {}
            sector = info.get("sector") or info.get("industry") or "Unknown"
        except Exception:
            pass
        self._sector_cache[ticker] = sector
        return sector

    def sector_breakdown(self, positions_value: Dict[str, float]) -> Dict[str, float]:
        """positions_value: ticker → USD value. Returns sector → pct of total."""
        total = sum(positions_value.values()) or 1.0
        breakdown: Dict[str, float] = {}
        for ticker, val in positions_value.items():
            s = self.get_sector(ticker)
            breakdown[s] = breakdown.get(s, 0.0) + val / total
        return breakdown

    def can_open(
        self,
        new_ticker: str,
        new_invest: float,
        portfolio_value: float,
        existing_positions_value: Dict[str, float],
    ) -> Dict:
        """Returns {allowed: bool, sector: str, new_sector_pct: float, reason: str|None}."""
        if portfolio_value <= 0:
            return {"allowed": True, "sector": "Unknown", "new_sector_pct": 0.0, "reason": None}
        sector = self.get_sector(new_ticker)
        existing = sum(
            v for t, v in existing_positions_value.items()
            if self.get_sector(t) == sector
        )
        new_pct = (existing + new_invest) / portfolio_value
        if new_pct > self.max_sector_pct:
            return {
                "allowed": False,
                "sector": sector,
                "new_sector_pct": round(new_pct * 100, 1),
                "reason": (
                    f"Sektor-Limit erreicht: {sector} wäre {new_pct*100:.1f}% "
                    f"(max {self.max_sector_pct*100:.0f}%)"
                ),
            }
        return {
            "allowed": True,
            "sector": sector,
            "new_sector_pct": round(new_pct * 100, 1),
            "reason": None,
        }
