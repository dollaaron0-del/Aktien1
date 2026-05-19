from typing import Dict, List, Optional

from collectors.price_cache import get_price as _cached_price, get_prices as _cached_prices


class PaperBroker:
    """Simulates order execution using real-time market prices from Yahoo Finance."""

    def get_price(self, ticker: str) -> Optional[float]:
        return _cached_price(ticker)

    def get_prices(self, tickers: List[str]) -> Dict[str, float]:
        return _cached_prices(tickers)

    def buy(self, ticker: str, shares: float, price: float) -> Dict:
        return {
            "status": "filled",
            "ticker": ticker,
            "shares": shares,
            "fill_price": price,
            "mode": "paper",
        }

    def sell(self, ticker: str, shares: float, price: float) -> Dict:
        return {
            "status": "filled",
            "ticker": ticker,
            "shares": shares,
            "fill_price": price,
            "mode": "paper",
        }
