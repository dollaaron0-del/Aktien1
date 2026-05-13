"""Earnings calendar filter – blocks new entries if earnings are imminent."""
from datetime import datetime, timedelta
from typing import Optional, Dict
import yfinance as yf


class EarningsFilter:
    """Skips or flags tickers that have earnings within `block_days`."""

    def __init__(self, block_days: int = 5, warn_days: int = 10):
        self.block_days = block_days
        self.warn_days = warn_days
        self._cache: Dict[str, Optional[datetime]] = {}

    def next_earnings(self, ticker: str) -> Optional[datetime]:
        if ticker in self._cache:
            return self._cache[ticker]
        result: Optional[datetime] = None
        try:
            stock = yf.Ticker(ticker)
            cal = stock.calendar
            if cal is not None:
                ed = None
                if isinstance(cal, dict):
                    ed = cal.get("Earnings Date")
                    if isinstance(ed, list) and ed:
                        ed = ed[0]
                else:
                    try:
                        ed = cal.loc["Earnings Date"].iloc[0]
                    except Exception:
                        ed = None
                if ed:
                    if hasattr(ed, "to_pydatetime"):
                        ed = ed.to_pydatetime()
                    elif isinstance(ed, str):
                        ed = datetime.fromisoformat(ed[:19])
                    if isinstance(ed, datetime):
                        result = ed
        except Exception:
            pass
        self._cache[ticker] = result
        return result

    def check(self, ticker: str) -> Dict:
        """Returns {block: bool, warn: bool, days_until: int|None, date: str|None}."""
        ed = self.next_earnings(ticker)
        if not ed:
            return {"block": False, "warn": False, "days_until": None, "date": None}
        days_until = (ed - datetime.utcnow()).days
        if days_until < 0:
            return {"block": False, "warn": False, "days_until": days_until, "date": ed.date().isoformat()}
        return {
            "block": days_until <= self.block_days,
            "warn": days_until <= self.warn_days,
            "days_until": days_until,
            "date": ed.date().isoformat(),
        }
