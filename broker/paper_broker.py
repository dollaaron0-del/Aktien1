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

    def get_crypto_price(self, symbol: str) -> Optional[float]:
        """Paper fallback: re-uses generic price lookup (yfinance via price_cache)."""
        pair = symbol.split("/")[0].upper() + "-USD"
        price = _cached_price(pair)
        if price is None:
            price = _cached_price(symbol)
        return price

    def buy_crypto(self, symbol: str, usd_amount: float) -> Dict:
        price = self.get_crypto_price(symbol) or 1.0
        qty = round(usd_amount / price, 6)
        return {
            "status":     "filled",
            "ticker":     symbol,
            "qty":        qty,
            "usd_amount": usd_amount,
            "fill_price": price,
            "mode":       "paper",
        }

    def sell_crypto(self, symbol: str, qty: float) -> Dict:
        price = self.get_crypto_price(symbol) or 1.0
        return {
            "status":     "filled",
            "ticker":     symbol,
            "qty":        qty,
            "fill_price": price,
            "mode":       "paper",
        }
