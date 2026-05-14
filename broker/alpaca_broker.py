"""Alpaca Markets broker integration for real (or paper-API) execution."""
import time
from typing import Dict, List, Optional
import requests

from config import config
from logger import get_logger

log = get_logger(__name__)

_FILL_POLL_INTERVAL = 2    # seconds between status polls
_FILL_TIMEOUT       = 30   # max seconds to wait for fill confirmation


class AlpacaBroker:
    """
    Thin REST wrapper for Alpaca Markets.
    Uses ALPACA_API_KEY/SECRET/BASE_URL from .env.
    Default base URL points to Alpaca's paper API (free).
    """

    def __init__(self):
        self.api_key  = config.alpaca_api_key
        self.secret   = config.alpaca_secret_key
        self.base_url = config.alpaca_base_url.rstrip("/")
        self.data_url = "https://data.alpaca.markets"

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID":     self.api_key,
            "APCA-API-SECRET-KEY": self.secret,
            "Content-Type":        "application/json",
        }

    def _check_creds(self) -> bool:
        return bool(self.api_key and self.secret)

    # ── Price data ─────────────────────────────────────────────────────────────

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
            log.debug("Alpaca price %s: HTTP %d", ticker, r.status_code)
        except Exception as e:
            log.debug("Alpaca get_price %s: %s", ticker, e)

        # Fallback to yfinance if Alpaca data feed unavailable
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                return round(float(hist["Close"].iloc[-1]), 2)
        except Exception as e:
            log.debug("yfinance fallback %s: %s", ticker, e)
        return None

    def get_prices(self, tickers: List[str]) -> Dict[str, float]:
        prices = {}
        for t in tickers:
            p = self.get_price(t)
            if p is not None:
                prices[t] = p
        return prices

    # ── Order submission + fill tracking ──────────────────────────────────────

    def _submit_order(self, ticker: str, shares: float, side: str) -> Dict:
        if not self._check_creds():
            log.error("Alpaca: API-Credentials fehlen")
            return {"status": "error", "reason": "Alpaca credentials missing"}
        try:
            payload = {
                "symbol":        ticker,
                "qty":           str(shares),
                "side":          side,
                "type":          "market",
                "time_in_force": "day",
            }
            r = requests.post(
                f"{self.base_url}/v2/orders",
                json=payload,
                headers=self._headers(),
                timeout=15,
            )
            if r.status_code not in (200, 201):
                log.error("Alpaca order %s %s: HTTP %d – %s", side, ticker, r.status_code, r.text[:200])
                return {"status": "error", "reason": f"HTTP {r.status_code}: {r.text[:200]}"}

            data     = r.json()
            order_id = data.get("id")
            log.info("Alpaca order submitted: %s %s %.2f shares (id=%s)", side.upper(), ticker, shares, order_id)

            # Poll for fill confirmation
            fill_result = self._wait_for_fill(order_id)
            fill_result.update({
                "ticker": ticker,
                "shares": shares,
                "side":   side,
                "mode":   "alpaca",
            })
            return fill_result

        except Exception as e:
            log.exception("Alpaca _submit_order %s %s: %s", side, ticker, e)
            return {"status": "error", "reason": str(e)}

    def _wait_for_fill(self, order_id: str) -> Dict:
        """
        Pollt den Order-Status bis filled/canceled/expired oder Timeout.
        Gibt fill-Preis zurück wenn verfügbar.
        """
        if not order_id:
            return {"status": "submitted"}

        deadline = time.monotonic() + _FILL_TIMEOUT
        while time.monotonic() < deadline:
            try:
                r = requests.get(
                    f"{self.base_url}/v2/orders/{order_id}",
                    headers=self._headers(),
                    timeout=10,
                )
                if r.status_code != 200:
                    break
                data   = r.json()
                status = data.get("status", "")

                if status == "filled":
                    fill_price = data.get("filled_avg_price")
                    log.info(
                        "Alpaca order %s FILLED @ $%s",
                        order_id,
                        fill_price or "N/A",
                    )
                    return {
                        "status":     "filled",
                        "order_id":   order_id,
                        "fill_price": float(fill_price) if fill_price else None,
                    }
                if status in ("canceled", "expired", "rejected"):
                    log.warning("Alpaca order %s: %s", order_id, status)
                    return {"status": status, "order_id": order_id}

                # Still pending/partially filled – wait
                time.sleep(_FILL_POLL_INTERVAL)

            except Exception as e:
                log.debug("Alpaca fill poll %s: %s", order_id, e)
                time.sleep(_FILL_POLL_INTERVAL)

        # Timeout – order may fill later; log it
        log.warning(
            "Alpaca: Fill-Timeout für Order %s (>%ds) – Ausführung unbestätigt",
            order_id, _FILL_TIMEOUT,
        )
        return {"status": "pending", "order_id": order_id}

    # ── Public interface ───────────────────────────────────────────────────────

    def buy(self, ticker: str, shares: float, price: float) -> Dict:
        return self._submit_order(ticker, shares, "buy")

    def sell(self, ticker: str, shares: float, price: float) -> Dict:
        return self._submit_order(ticker, shares, "sell")

    def get_account(self) -> Optional[Dict]:
        if not self._check_creds():
            return None
        try:
            r = requests.get(
                f"{self.base_url}/v2/account",
                headers=self._headers(),
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            log.warning("Alpaca get_account: HTTP %d", r.status_code)
        except Exception as e:
            log.warning("Alpaca get_account: %s", e)
        return None
