"""
broker/ibkr_broker.py – Interactive Brokers Integration via ib_insync.

Voraussetzungen:
  pip install ib_insync

  TWS oder IB Gateway muss lokal laufen mit aktivierter API-Verbindung:
  TWS: Edit → Global Configuration → API → Settings → Enable ActiveX and Socket Clients
  Vertrauenswürdige IPs: 127.0.0.1 eintragen

Umgebungsvariablen (.env):
  IBKR_HOST       = 127.0.0.1     (Standard)
  IBKR_PORT       = 7496           (TWS Live) | 7497 (TWS Paper) | 4001 (GW Live) | 4002 (GW Paper)
  IBKR_CLIENT_ID  = 1
  IBKR_ACCOUNT    = ""             (leer = erstes verfügbares Konto, empfohlen: explizit setzen!)
  IBKR_PAPER_ONLY = true           (Sicherheitssperre: bricht ab wenn kein Paper-Account erkannt)

Unterstützte Ticker-Formate:
  US-Aktien:   AAPL, MSFT, NVDA, …
  EU-Aktien:   SAP.DE, ASML.AS, NESN.SW, SHEL.L, … (Suffix wird ausgewertet)
  Krypto:      BTC, ETH, SOL, … (via PAXOS-Exchange, nur wenn im Konto freigeschaltet)
"""

from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

# ── Verbindungsparameter ──────────────────────────────────────────────────────
_HOST      = os.getenv("IBKR_HOST",      "127.0.0.1")
_PORT      = int(os.getenv("IBKR_PORT",  "7497"))
_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))
_ACCOUNT   = os.getenv("IBKR_ACCOUNT",   "")

_PRICE_TIMEOUT   = 8    # Sekunden Wartezeit auf Marktdaten
_ORDER_TIMEOUT   = 60   # Sekunden Wartezeit auf Fill (Paper braucht länger als Live)
_RECONNECT_DELAY = 5    # Sekunden vor Reconnect-Versuch
_PAPER_ONLY      = os.getenv("IBKR_PAPER_ONLY", "false").lower() == "true"

# ── Ticker-Suffix → (Exchange, Currency) ─────────────────────────────────────
_SUFFIX_MAP: Dict[str, tuple] = {
    ".DE":  ("SMART", "EUR"),   # XETRA
    ".F":   ("SMART", "EUR"),
    ".MU":  ("SMART", "EUR"),
    ".PA":  ("SMART", "EUR"),   # Euronext Paris
    ".AS":  ("SMART", "EUR"),   # Euronext Amsterdam
    ".MI":  ("SMART", "EUR"),   # Borsa Italiana
    ".MC":  ("SMART", "EUR"),   # Bolsa Madrid
    ".BR":  ("SMART", "EUR"),   # Euronext Brüssel
    ".BE":  ("SMART", "EUR"),
    ".VI":  ("SMART", "EUR"),   # Wien
    ".L":   ("SMART", "GBP"),   # London
    ".SW":  ("SMART", "CHF"),   # Schweiz
    ".CO":  ("SMART", "DKK"),   # Kopenhagen
    ".ST":  ("SMART", "SEK"),   # Stockholm
    ".HE":  ("SMART", "EUR"),   # Helsinki
    ".OL":  ("SMART", "NOK"),   # Oslo
}


def _parse_ticker(ticker: str):
    """
    Gibt (symbol, exchange, currency) zurück.
    EU-Ticker: SAP.DE → ('SAP', 'SMART', 'EUR')
    US-Ticker: AAPL   → ('AAPL', 'SMART', 'USD')
    """
    upper = ticker.upper()
    for suffix, (exch, cur) in sorted(_SUFFIX_MAP.items(), key=lambda x: len(x[0]), reverse=True):
        if upper.endswith(suffix.upper()):
            symbol = ticker[:len(ticker) - len(suffix)]
            return symbol, exch, cur
    return ticker, "SMART", "USD"


class IBKRBroker:
    """
    Thin synchronous wrapper um ib_insync für den Trading-Bot.
    Verbindung wird beim ersten Aufruf von connect() hergestellt.
    """

    def __init__(self):
        self._ib = None
        self._connected = False
        self._active_account: str = _ACCOUNT
        self._connect()

    # ── Verbindungsmanagement ─────────────────────────────────────────────────

    def _connect(self) -> bool:
        try:
            from ib_insync import IB
        except ImportError:
            log.error(
                "ib_insync nicht installiert. Bitte: pip install ib_insync\n"
                "Danach TWS/IB Gateway starten und BROKER_MODE=ibkr setzen."
            )
            return False
        try:
            ib = IB()
            log.info("IBKR: Verbindungsversuch %s:%d (clientId=%d) …", _HOST, _PORT, _CLIENT_ID)
            ib.connect(_HOST, _PORT, clientId=_CLIENT_ID, readonly=False, timeout=10)
            log.info("IBKR: TCP-Verbindung hergestellt – frage Konten ab …")
        except Exception as e:
            log.exception("IBKR connect() fehlgeschlagen (%s:%d): %s", _HOST, _PORT, e)
            self._connected = False
            return False

        try:
            self._ib = ib
            self._connected = True

            accounts = ib.managedAccounts()
            log.info("IBKR: managedAccounts = %s", accounts)
            self._active_account = _ACCOUNT or (accounts[0] if accounts else "")

            # Paper-Account-Erkennung: IBKR Paper-Accounts beginnen mit "DU"
            is_paper = self._active_account.upper().startswith("DU")
            account_type = "PAPER" if is_paper else "LIVE ⚠️"
            log.info(
                "IBKR verbunden: %s:%d | Account: %s (%s) | Alle: %s",
                _HOST, _PORT, self._active_account, account_type, accounts,
            )

            if _PAPER_ONLY and not is_paper:
                log.error(
                    "IBKR_PAPER_ONLY=true aber Live-Account erkannt (%s) – Verbindung getrennt!",
                    self._active_account,
                )
                ib.disconnect()
                self._connected = False
                return False

            if not is_paper:
                log.warning(
                    "⚠️  IBKR Live-Account aktiv (%s)! "
                    "Für Paper-Trading IBKR_PORT=7497 (TWS) oder IBKR_PORT=4002 (GW) setzen.",
                    self._active_account,
                )
            return True
        except Exception as e:
            log.exception("IBKR post-connect fehlgeschlagen: %s", e)
            self._connected = False
            return False

    def _ensure_connected(self) -> bool:
        if self._connected and self._ib and self._ib.isConnected():
            return True
        log.info("IBKR: Reconnect-Versuch …")
        time.sleep(_RECONNECT_DELAY)
        return self._connect()

    def is_connected(self) -> bool:
        return self._connected and self._ib is not None and self._ib.isConnected()

    # ── Contract-Erzeugung ────────────────────────────────────────────────────

    def _stock_contract(self, ticker: str):
        from ib_insync import Stock
        symbol, exch, cur = _parse_ticker(ticker)
        return Stock(symbol, exch, cur)

    def _crypto_contract(self, symbol: str):
        """PAXOS-Exchange für Krypto. Nur verfügbar wenn im IBKR-Konto freigeschaltet."""
        from ib_insync import Crypto
        base = symbol.split("/")[0].upper().removesuffix("-USD")
        return Crypto(base, "PAXOS", "USD")

    # ── Preisabfragen ─────────────────────────────────────────────────────────

    def get_price(self, ticker: str) -> Optional[float]:
        if not self._ensure_connected():
            return self._yf_price(ticker)
        try:
            contract = self._stock_contract(ticker)
            self._ib.qualifyContracts(contract)
            ticker_data = self._ib.reqMktData(contract, "", False, False)
            self._ib.sleep(_PRICE_TIMEOUT)
            price = ticker_data.marketPrice()
            self._ib.cancelMktData(contract)
            if price and price > 0:
                log.debug("IBKR price %s: %.4f", ticker, price)
                return round(float(price), 4)
        except Exception as e:
            log.debug("IBKR get_price %s: %s", ticker, e)
        return self._yf_price(ticker)

    def get_prices(self, tickers: List[str]) -> Dict[str, float]:
        if not tickers:
            return {}
        result: Dict[str, float] = {}
        if not self._ensure_connected():
            from collectors.price_cache import get_prices as _cached
            return _cached(tickers)
        try:
            from ib_insync import Stock
            contracts = []
            for t in tickers:
                c = self._stock_contract(t)
                contracts.append((t, c))

            self._ib.qualifyContracts(*[c for _, c in contracts])
            ticker_map = {}
            for t, c in contracts:
                td = self._ib.reqMktData(c, "", False, False)
                ticker_map[t] = td

            self._ib.sleep(_PRICE_TIMEOUT)

            for t, td in ticker_map.items():
                p = td.marketPrice()
                if p and p > 0:
                    result[t] = round(float(p), 4)

            # Cancel all subscriptions
            for _, c in contracts:
                try:
                    self._ib.cancelMktData(c)
                except Exception:
                    pass
        except Exception as e:
            log.warning("IBKR get_prices: %s", e)

        # Fallback yfinance für fehlende Ticker
        missing = [t for t in tickers if t not in result]
        if missing:
            from collectors.price_cache import get_prices as _cached
            result.update(_cached(missing))
        return result

    def get_crypto_price(self, symbol: str) -> Optional[float]:
        base = symbol.split("/")[0].upper().removesuffix("-USD")
        if not self._ensure_connected():
            return self._yf_price(f"{base}-USD")
        try:
            contract = self._crypto_contract(symbol)
            self._ib.qualifyContracts(contract)
            td = self._ib.reqMktData(contract, "", False, False)
            self._ib.sleep(_PRICE_TIMEOUT)
            price = td.marketPrice()
            self._ib.cancelMktData(contract)
            if price and price > 0:
                return round(float(price), 6)
        except Exception as e:
            log.debug("IBKR get_crypto_price %s: %s – Fallback yfinance", symbol, e)
        return self._yf_price(f"{base}-USD")

    # ── Orders ────────────────────────────────────────────────────────────────

    def _place_order(self, contract, action: str, shares: float) -> Dict:
        from ib_insync import MarketOrder
        order = MarketOrder(action, shares)
        # Account immer explizit setzen – bei mehreren Konten (paper + live) sonst falsches Konto
        account = getattr(self, "_active_account", _ACCOUNT) or _ACCOUNT
        if account:
            order.account = account
        else:
            log.warning(
                "IBKR: Kein Account gesetzt – Order geht an Default-Account. "
                "IBKR_ACCOUNT in .env setzen um sicherzustellen dass Paper-Account genutzt wird."
            )

        trade = self._ib.placeOrder(contract, order)
        log.info(
            "IBKR %s %s %.4f shares – Order eingereicht (account=%s)",
            action, contract.symbol, shares, account or "default",
        )

        deadline = time.monotonic() + _ORDER_TIMEOUT
        while time.monotonic() < deadline:
            self._ib.sleep(2)
            status = trade.orderStatus.status
            if status == "Filled":
                fill_price = trade.orderStatus.avgFillPrice
                log.info(
                    "IBKR %s %s FILLED @ %.4f",
                    action, contract.symbol, fill_price
                )
                return {
                    "status":     "filled",
                    "ticker":     contract.symbol,
                    "shares":     shares,
                    "fill_price": fill_price,
                    "order_id":   trade.order.orderId,
                    "mode":       "ibkr",
                }
            if status in ("Cancelled", "Inactive"):
                log.warning("IBKR Order %s %s: %s", action, contract.symbol, status)
                return {"status": status.lower(), "ticker": contract.symbol, "mode": "ibkr"}

        log.warning(
            "IBKR Fill-Timeout %s %s (>%ds) – Order läuft weiter",
            action, contract.symbol, _ORDER_TIMEOUT,
        )
        return {
            "status":   "pending",
            "ticker":   contract.symbol,
            "shares":   shares,
            "order_id": trade.order.orderId,
            "mode":     "ibkr",
        }

    def buy(self, ticker: str, shares: float, price: float,
            limit: bool = False, stop_loss: Optional[float] = None,
            take_profit: Optional[float] = None) -> Dict:
        if not self._ensure_connected():
            log.error("IBKR: keine Verbindung – BUY %s nicht ausgeführt", ticker)
            return {"status": "error", "reason": "IBKR nicht verbunden"}
        try:
            contract = self._stock_contract(ticker)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                log.error("IBKR: Contract-Qualifizierung fehlgeschlagen für %s – BUY abgebrochen", ticker)
                return {"status": "error", "reason": f"Contract {ticker} nicht qualifizierbar"}
            return self._place_order(contract, "BUY", shares)
        except Exception as e:
            log.exception("IBKR buy %s: %s", ticker, e)
            return {"status": "error", "reason": str(e)}

    def sell(self, ticker: str, shares: float, price: float) -> Dict:
        if not self._ensure_connected():
            log.error("IBKR: keine Verbindung – SELL %s nicht ausgeführt", ticker)
            return {"status": "error", "reason": "IBKR nicht verbunden"}
        try:
            contract = self._stock_contract(ticker)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                log.error("IBKR: Contract-Qualifizierung fehlgeschlagen für %s – SELL abgebrochen", ticker)
                return {"status": "error", "reason": f"Contract {ticker} nicht qualifizierbar"}
            return self._place_order(contract, "SELL", shares)
        except Exception as e:
            log.exception("IBKR sell %s: %s", ticker, e)
            return {"status": "error", "reason": str(e)}

    def buy_crypto(self, symbol: str, usd_amount: float) -> Dict:
        if not self._ensure_connected():
            return {"status": "error", "reason": "IBKR nicht verbunden"}
        try:
            price = self.get_crypto_price(symbol)
            if not price:
                return {"status": "error", "reason": f"Kein Preis für {symbol}"}
            qty = round(usd_amount / price, 6)
            contract = self._crypto_contract(symbol)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                return {"status": "error", "reason": f"Crypto-Contract {symbol} nicht qualifizierbar"}
            result = self._place_order(contract, "BUY", qty)
            result["usd_amount"] = usd_amount
            return result
        except Exception as e:
            log.exception("IBKR buy_crypto %s: %s", symbol, e)
            return {"status": "error", "reason": str(e)}

    def sell_crypto(self, symbol: str, qty: float) -> Dict:
        if not self._ensure_connected():
            return {"status": "error", "reason": "IBKR nicht verbunden"}
        try:
            contract = self._crypto_contract(symbol)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                return {"status": "error", "reason": f"Crypto-Contract {symbol} nicht qualifizierbar"}
            return self._place_order(contract, "SELL", qty)
        except Exception as e:
            log.exception("IBKR sell_crypto %s: %s", symbol, e)
            return {"status": "error", "reason": str(e)}

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account(self) -> Optional[Dict]:
        if not self._ensure_connected():
            return None
        try:
            account = _ACCOUNT or (self._ib.managedAccounts() or [""])[0]
            summary = self._ib.accountSummary(account)
            return {item.tag: item.value for item in summary}
        except Exception as e:
            log.warning("IBKR get_account: %s", e)
            return None

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            self._connected = False
            log.info("IBKR Verbindung getrennt")

    # ── yfinance Fallback ─────────────────────────────────────────────────────

    @staticmethod
    def _yf_price(ticker: str) -> Optional[float]:
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                return round(float(hist["Close"].iloc[-1]), 4)
        except Exception as e:
            log.debug("yfinance fallback %s: %s", ticker, e)
        return None
