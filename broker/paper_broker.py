import yfinance as yf
from typing import Dict


class PaperBroker:
    """Simulates order execution using real-time market prices from Yahoo Finance."""

    def get_price(self, ticker: str) -> float | None:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                return round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass
        return None

    def get_prices(self, tickers: list[str]) -> Dict[str, float]:
        prices = {}
        for ticker in tickers:
            price = self.get_price(ticker)
            if price:
                prices[ticker] = price
        return prices

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
