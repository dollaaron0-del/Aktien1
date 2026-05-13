"""Alpaca Markets broker integration for real (or paper-API) execution."""
from typing import Dict, List, Optional
import requests

from config import config


class AlpacaBroker:
    """
    Thin REST wrapper for Alpaca Markets.
    Uses ALPACA_API_KEY/SECRET/BASE_URL from .env.
    Default base URL points to Alpaca's paper API (free).
    """

    def __init__(self):
        self.api_key = config.alpaca_api_key
        self.secret = config.alpaca_secret_key
        self.base_url = config.alpaca_base_url.rstrip("/")
        self.data_url = "https://data.alpaca.markets"

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.secret,
            "Content-Type": "application/json",
        }

    def _check_creds(self) -> bool:
        return bool(self.api_key and self.secret)

    def get_price(self, ticker: str) -> Optional[float]:
        if not self._check_creds():
            return None
        try:
            r = requests.get(
                f"{self.data_url}/v2/stocks/{ticker}/trades/latest",
                headers=self._headers(),
                timeout=10,
            )
            if r.status_code == 200:
                return round(float(r.json()["trade"]["p"]), 2)
        except Exception:
            pass
        # Fallback to yfinance if Alpaca data feed unavailable
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                return round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass
        return None

    def get_prices(self, tickers: List[str]) -> Dict[str, float]:
        prices = {}
        for t in tickers:
            p = self.get_price(t)
            if p:
                prices[t] = p
        return prices

    def _submit_order(self, ticker: str, shares: float, side: str) -> Dict:
        if not self._check_creds():
            return {"status": "error", "reason": "Alpaca credentials missing"}
        try:
            payload = {
                "symbol": ticker,
                "qty": str(shares),
                "side": side,
                "type": "market",
                "time_in_force": "day",
            }
            r = requests.post(
                f"{self.base_url}/v2/orders",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code in (200, 201):
                data = r.json()
                return {
                    "status": "submitted",
                    "ticker": ticker,
                    "shares": shares,
                    "order_id": data.get("id"),
                    "mode": "alpaca",
                }
            return {"status": "error", "reason": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"status": "error", "reason": str(e)}

    def buy(self, ticker: str, shares: float, price: float) -> Dict:
        return self._submit_order(ticker, shares, "buy")

    def sell(self, ticker: str, shares: float, price: float) -> Dict:
        return self._submit_order(ticker, shares, "sell")

    def get_account(self) -> Optional[Dict]:
        if not self._check_creds():
            return None
        try:
            r = requests.get(f"{self.base_url}/v2/account", headers=self._headers(), timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None
