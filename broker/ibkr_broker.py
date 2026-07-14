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

import asyncio
import functools
import math
import os
import threading
import time
from typing import Dict, List, Optional

from logger import get_logger
from broker.order_result import OrderResult
from broker.order_log import log_order


def _synchronized(method):
    """Serialisiert ib_insync-Zugriffe über self._lock. ib_insync ist NICHT
    thread-safe (eine Event-Loop) – ohne diesen Lock kann ein paralleler
    Preis-Abruf (z.B. get_crypto_price aus dem Prefetch-Pool des Runners) die
    Verbindung eines gleichzeitigen Order-/Preis-Calls zerschießen. RLock, damit
    verschachtelte Aufrufe (buy → _ensure_connected) nicht selbst blockieren."""
    @functools.wraps(method)
    def _wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return _wrapper

log = get_logger(__name__)


def _whole_shares(ticker: str, shares: float, action: str) -> int:
    """IBKR-API kann keine Teilaktien handeln (Error 10243 –
    'Fractional-sized order cannot be placed via API'). Aktien-Mengen werden
    daher auf ganze Stück abgerundet. Ein etwaiger Bruchteil-Rest ('Dust',
    z.B. 0,91 St.) bleibt am Konto und kann manuell im Desktop geschlossen
    werden. Crypto läuft bewusst NICHT über diesen Pfad."""
    whole = int(shares)  # immer abrunden – nie mehr handeln als gedeckt/gehalten
    if whole != shares:
        log.info(
            "IBKR: %s %s auf ganze Stück gerundet %.4f → %d (Teilaktien nicht API-fähig)",
            action, ticker, shares, whole,
        )
    return whole


# ── Verbindungsparameter ──────────────────────────────────────────────────────
_HOST      = os.getenv("IBKR_HOST",      "127.0.0.1")
_PORT      = int(os.getenv("IBKR_PORT",  "7497"))
_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))
_ACCOUNT   = os.getenv("IBKR_ACCOUNT",   "")

_PRICE_TIMEOUT   = 8    # Sekunden Wartezeit auf Marktdaten
_ORDER_TIMEOUT   = 60   # Sekunden Wartezeit auf Fill (Paper braucht länger als Live)
# Broker-seitige Schutz-Stops (GTC-Stop-Orders bei IBKR): greifen auch, wenn der
# Bot ausgefallen ist. Bewusst NUR Stop-Loss (kein Take-Profit-Limit beim Broker,
# das würde mit Partial-TP/Trailing/Soft-Stop der Bot-Logik kollidieren).
_SERVER_STOPS = os.getenv("IBKR_SERVER_STOPS", "true").strip().lower() in ("1", "true", "yes")
# whatIf-Margin-Check (Roadmap 1.14): vor jeder echten Order eine billige
# Plausibilitätsprüfung bei IBKR (Margin/Kaufkraft) — verhindert Ablehnungs-
# Überraschungen, v.a. beim späteren Umstieg auf echtes Geld. Fail-open:
# liefert der Check kein klares NEIN, geht die Order normal raus.
_WHATIF_CHECK = os.getenv("IBKR_WHATIF_CHECK", "true").strip().lower() in ("1", "true", "yes")
_RECONNECT_DELAY = 5    # Sekunden vor Reconnect-Versuch
_PAPER_ONLY      = os.getenv("IBKR_PAPER_ONLY", "false").lower() == "true"
# Marktdaten-Typ: 1=Echtzeit (braucht Abo), 2=Frozen, 3=Delayed (~15min, abo-frei),
# 4=Delayed-Frozen. Paper-Konten haben i.d.R. KEIN Echtzeit-Abo → reqMktData liefert
# dann NaN (Error 354 "not subscribed") und jeder Trade scheitert mangels Kurs.
# Default 3 (Delayed) macht den Bot abo-frei lauffähig; per ENV überschreibbar.
_MKT_DATA_TYPE   = int(os.getenv("IBKR_MARKET_DATA_TYPE", "3"))
# Historische Kursreihen via reqHistoricalData (Roadmap 1.13) statt/neben
# yfinance – reduziert die yfinance-Abhängigkeit (NaN-Fälle, Rate-Limits,
# stille API-Brüche) im Live-Pfad. Gilt für dieselbe Session wie
# _MKT_DATA_TYPE, Delayed-Bars funktionieren auch ohne Echtzeit-Abo.
# Fail-open: bei jedem Fehler (Flag aus, kein Connect, leere Antwort) fällt
# get_history() auf yfinance zurück.
_HISTORICAL_DATA = os.getenv("IBKR_HISTORICAL_DATA", "true").strip().lower() in ("1", "true", "yes")

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


def _norm_symbol(sym: str) -> str:
    """Ticker auf IBKR-Basissymbol normalisieren (z.B. 'ASML.AS' → 'ASML')."""
    return (sym or "").split(".")[0].upper()


def _valid_price(p) -> bool:
    """True nur für eine echte, positive Zahl. marketPrice() liefert bei
    fehlendem Abo/Tick NaN – und NaN ist truthy, NaN>0 jedoch False, weshalb
    ein naives `if p and p > 0` NaN durchrutschen lassen kann. Hier zentral
    abgesichert (vgl. yfinance-NaN-Score-Falle)."""
    try:
        return p is not None and not math.isnan(float(p)) and float(p) > 0
    except (TypeError, ValueError):
        return False


def _ticker_price(td) -> Optional[float]:
    """Bester verfügbarer Preis aus einem ib_insync-Ticker: marketPrice
    (Midpoint), dann last, dann close. Wichtig bei Delayed-Daten, wo der
    Midpoint anfangs NaN sein kann, last/close aber schon stehen."""
    for getter in (
        lambda: td.marketPrice(),
        lambda: getattr(td, "last", None),
        lambda: getattr(td, "close", None),
    ):
        try:
            p = getter()
        except Exception:
            continue
        if _valid_price(p):
            return round(float(p), 4)
    return None


class IBKRBroker:
    """
    Thin synchronous wrapper um ib_insync für den Trading-Bot.
    Verbindung wird beim ersten Aufruf von connect() hergestellt.
    """

    def __init__(self):
        self._ib = None
        self._connected = False
        self._active_account: str = _ACCOUNT
        self._lock = threading.RLock()
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
        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError) as e:
            # Erwartbar, wenn das IB Gateway nicht läuft oder dessen Verbindung
            # zu den IB-Servern unterbrochen ist. Kein Traceback – der Aufrufer
            # fällt sauber auf den Paper-Broker zurück und alarmiert via Telegram.
            log.warning(
                "IBKR nicht erreichbar (%s:%d): %s – Paper-Broker übernimmt.",
                _HOST, _PORT, type(e).__name__,
            )
            self._connected = False
            return False
        except Exception as e:
            log.exception("IBKR connect() unerwartet fehlgeschlagen (%s:%d): %s", _HOST, _PORT, e)
            self._connected = False
            return False

        try:
            self._ib = ib
            self._connected = True

            # Marktdaten-Typ setzen, BEVOR Kurse abgefragt werden. Ohne Echtzeit-Abo
            # (typisch bei Paper-Konten) liefert reqMktData sonst NaN (Error 354) und
            # jeder Trade scheitert mangels gültigem Kurs. Delayed (Typ 3) ist abo-frei.
            try:
                ib.reqMarketDataType(_MKT_DATA_TYPE)
                log.info("IBKR: Marktdaten-Typ = %d (1=Echtzeit,3=Delayed)", _MKT_DATA_TYPE)
            except Exception as e:
                log.warning("IBKR: reqMarketDataType(%d) fehlgeschlagen: %s", _MKT_DATA_TYPE, e)

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

    @_synchronized
    def get_price(self, ticker: str) -> Optional[float]:
        if not self._ensure_connected():
            return self._yf_price(ticker)
        try:
            contract = self._stock_contract(ticker)
            self._ib.qualifyContracts(contract)
            ticker_data = self._ib.reqMktData(contract, "", False, False)
            self._ib.sleep(_PRICE_TIMEOUT)
            price = _ticker_price(ticker_data)
            self._ib.cancelMktData(contract)
            if price is not None:
                log.debug("IBKR price %s: %.4f", ticker, price)
                return price
        except Exception as e:
            log.debug("IBKR get_price %s: %s", ticker, e)
        return self._yf_price(ticker)

    @_synchronized
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
                p = _ticker_price(td)
                if p is not None:
                    result[t] = p

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

    @_synchronized
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
            if _valid_price(price):
                return round(float(price), 6)
        except Exception as e:
            log.debug("IBKR get_crypto_price %s: %s – Fallback yfinance", symbol, e)
        return self._yf_price(f"{base}-USD")

    @_synchronized
    def get_history(
        self,
        ticker: str,
        duration: str = "3 M",
        bar_size: str = "1 day",
        yf_period: str = "3mo",
    ):
        """Historische OHLCV-Bars (Roadmap 1.13). Spalten/Index wie
        yf.Ticker().history() (Open/High/Low/Close/Volume, DatetimeIndex),
        damit bestehende Aufrufer (z.B. TechnicalIndicators) unverändert
        bleiben. Fällt bei ausgeschaltetem Flag, fehlender Verbindung oder
        jedem Fehler auf yfinance zurück – nie ein harter Fehlschlag."""
        if _HISTORICAL_DATA and self._ensure_connected():
            try:
                contract = self._stock_contract(ticker)
                self._ib.qualifyContracts(contract)
                bars = self._ib.reqHistoricalData(
                    contract, endDateTime="", durationStr=duration,
                    barSizeSetting=bar_size, whatToShow="TRADES",
                    useRTH=True, formatDate=1,
                )
                if bars:
                    from ib_insync import util
                    df = util.df(bars)
                    if df is not None and not df.empty:
                        df = df.rename(columns={
                            "date": "Date", "open": "Open", "high": "High",
                            "low": "Low", "close": "Close", "volume": "Volume",
                        }).set_index("Date")
                        return df
            except Exception as e:
                log.debug("IBKR get_history %s: %s – Fallback yfinance", ticker, e)
        return self._yf_history(ticker, yf_period)

    # ── Orders ────────────────────────────────────────────────────────────────

    def _whatif_rejection(self, contract, order) -> Optional[str]:
        """Vorab-Margin-Check via ib_insync whatIfOrder (Roadmap 1.14).

        Liefert einen Ablehnungsgrund (str) NUR bei einem klaren Nein von
        IBKR; None heißt "einreichen" — auch bei Fehlern/leerer Antwort
        (fail-open: ein kaputter Check darf keinen Trade verhindern, der
        echte Gateway lehnt zur Not selbst ab).
        """
        if not _WHATIF_CHECK:
            return None
        try:
            state = self._ib.whatIfOrder(contract, order)
            if state is None:
                return None

            def _num(v) -> Optional[float]:
                try:
                    f = float(str(v).replace(",", ""))
                except (TypeError, ValueError):
                    return None
                # IBKR-Sentinel für "ungültig/abgelehnt" ist DBL_MAX
                # (1.7976931348623157E308) — als "kein Wert" behandeln.
                return f if math.isfinite(f) and abs(f) < 1e300 else None

            # Klares Nein #1: Margin-Felder tragen den DBL_MAX-Sentinel.
            _raw_init = str(getattr(state, "initMarginChange", "") or "")
            if "E308" in _raw_init.upper():
                return ("whatIf-Margin-Check: IBKR meldet Ablehnung "
                        "(Margin-Sentinel) – Order nicht eingereicht")

            # Klares Nein #2: Init-Margin NACH der Order überstiege das
            # Eigenkapital (equityWithLoan) → Order würde abgelehnt.
            init_after   = _num(getattr(state, "initMarginAfter", None))
            equity_after = _num(getattr(state, "equityWithLoanAfter", None))
            if (init_after is not None and equity_after is not None
                    and init_after > equity_after):
                return (f"whatIf-Margin-Check: Init-Margin nach Order "
                        f"({init_after:,.0f}) > Eigenkapital "
                        f"({equity_after:,.0f}) – Order nicht eingereicht")
            return None
        except Exception as e:
            log.debug("whatIf-Check übersprungen (%s %s): %s",
                      getattr(contract, "symbol", "?"),
                      getattr(order, "action", "?"), e)
            return None

    def _place_order(self, contract, action: str, shares: float) -> Dict:
        from ib_insync import MarketOrder
        # Hinweis: _place_order wird auch von buy_crypto/sell_crypto genutzt –
        # die brauchen Bruchteile. Aktien werden VORHER über _whole_shares()
        # auf ganze Stück gerundet (siehe buy()/sell()).
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

        # Billiger Margin-Vorab-Check (Roadmap 1.14): klares Nein von IBKR →
        # Order gar nicht erst einreichen, typisierter Fehler statt Ablehnung.
        _reject = self._whatif_rejection(contract, order)
        if _reject:
            log.warning("IBKR %s %s: %s", action, contract.symbol, _reject)
            return OrderResult.error(
                ticker=contract.symbol, reason=_reject, mode="ibkr")

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
                return OrderResult.filled(
                    contract.symbol, shares, fill_price,
                    order_id=trade.order.orderId, mode="ibkr",
                )
            if status in ("Cancelled", "Inactive"):
                log.warning("IBKR Order %s %s: %s", action, contract.symbol, status)
                return OrderResult(status.lower(), ticker=contract.symbol, mode="ibkr")

        # Timeout: Order ist nicht (voll) gefüllt. Um ein "umgekehrtes Phantom"
        # zu vermeiden (Broker füllt später, das Buch weiß nichts davon), die
        # Order aktiv canceln und den finalen Stand auswerten. So ist nach der
        # Rückgabe garantiert nichts mehr offen, das später unbemerkt füllt.
        try:
            self._ib.cancelOrder(trade.order)
            self._ib.sleep(2)
        except Exception as e:
            log.warning("IBKR cancelOrder nach Timeout %s %s: %s", action, contract.symbol, e)

        status     = trade.orderStatus.status
        filled_qty = float(getattr(trade.orderStatus, "filled", 0) or 0)
        fill_price = trade.orderStatus.avgFillPrice or 0.0

        # Race: zwischen Timeout und Cancel doch noch voll gefüllt.
        if status == "Filled" or filled_qty >= shares:
            log.info("IBKR %s %s FILLED (kurz vor Cancel) @ %.4f", action, contract.symbol, fill_price)
            return OrderResult.filled(
                contract.symbol, shares, fill_price,
                order_id=trade.order.orderId, mode="ibkr",
            )

        # Teilausführung: den gefüllten Teil buchen, Rest wurde gecancelt.
        if filled_qty > 0:
            log.warning(
                "IBKR %s %s TEILFILL %.4f/%.4f nach Timeout – Rest gecancelt",
                action, contract.symbol, filled_qty, shares,
            )
            return OrderResult.filled(
                contract.symbol, filled_qty, fill_price,
                order_id=trade.order.orderId, mode="ibkr", partial=True,
            )

        # Nichts gefüllt – Order gecancelt, kein offener Rest.
        log.warning(
            "IBKR Fill-Timeout %s %s (>%ds) – nicht gefüllt, Order gecancelt",
            action, contract.symbol, _ORDER_TIMEOUT,
        )
        return OrderResult.cancelled(
            ticker=contract.symbol, mode="ibkr",
            reason=f"Fill-Timeout nach {_ORDER_TIMEOUT}s – Order gecancelt",
        )

    @log_order("BUY")
    @_synchronized
    def buy(self, ticker: str, shares: float, price: float,
            limit: bool = False, stop_loss: Optional[float] = None,
            take_profit: Optional[float] = None) -> Dict:
        if not self._ensure_connected():
            log.error("IBKR: keine Verbindung – BUY %s nicht ausgeführt", ticker)
            return OrderResult.error(reason="IBKR nicht verbunden", mode="ibkr")
        try:
            contract = self._stock_contract(ticker)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                log.error("IBKR: Contract-Qualifizierung fehlgeschlagen für %s – BUY abgebrochen", ticker)
                return OrderResult.error(ticker=ticker, reason=f"Contract {ticker} nicht qualifizierbar", mode="ibkr")
            whole = _whole_shares(ticker, shares, "BUY")
            if whole <= 0:
                return OrderResult.error(
                    ticker=ticker, mode="ibkr",
                    reason=f"Positionsgröße {shares:.4f} < 1 Stück – IBKR-API kann keine Teilaktien handeln")
            result = self._place_order(contract, "BUY", whole)
            # Schutz-Stop (GTC) direkt nach dem Fill hinterlegen – überlebt
            # Bot-Ausfälle. stop_placed=False signalisiert dem Aufrufer, dass
            # die Position NUR bot-seitig überwacht ist (Executor alarmiert).
            if _SERVER_STOPS and stop_loss and result.get("status") == "filled":
                filled_qty = float(result.get("shares") or whole)
                result["stop_placed"] = self._place_stop(contract, filled_qty, stop_loss)
            return result
        except Exception as e:
            log.exception("IBKR buy %s: %s", ticker, e)
            return OrderResult.error(reason=str(e), mode="ibkr")

    @log_order("SELL")
    @_synchronized
    def sell(self, ticker: str, shares: float, price: float) -> Dict:
        if not self._ensure_connected():
            log.error("IBKR: keine Verbindung – SELL %s nicht ausgeführt", ticker)
            return OrderResult.error(reason="IBKR nicht verbunden", mode="ibkr")
        try:
            contract = self._stock_contract(ticker)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                log.error("IBKR: Contract-Qualifizierung fehlgeschlagen für %s – SELL abgebrochen", ticker)
                return OrderResult.error(ticker=ticker, reason=f"Contract {ticker} nicht qualifizierbar", mode="ibkr")
            whole = _whole_shares(ticker, shares, "SELL")
            if whole <= 0:
                return OrderResult.error(
                    ticker=ticker, mode="ibkr",
                    reason=f"Restbestand {shares:.4f} < 1 Stück – IBKR-API kann keine Teilaktien verkaufen "
                           f"(Dust ggf. manuell im Desktop schließen)")
            # Ruhenden Schutz-Stop VOR dem Verkauf räumen: bliebe er liegen,
            # würde er nach dem Exit auf leerer Position auslösen → Short.
            # (Schlägt der Verkauf danach fehl, ist die Position kurz ohne
            # Broker-Stop – der Bot lebt aber gerade und alarmiert/retryt.)
            if _SERVER_STOPS:
                self._cancel_stops(contract.symbol)
            return self._place_order(contract, "SELL", whole)
        except Exception as e:
            log.exception("IBKR sell %s: %s", ticker, e)
            return OrderResult.error(reason=str(e), mode="ibkr")

    # ── Broker-seitige Schutz-Stops (GTC) ────────────────────────────────────
    # Notfallnetz für Bot-Ausfälle: die Exit-Logik des Bots bleibt führend,
    # aber jede Position hat einen ruhenden GTC-Stop beim Broker. Lebenszyklus:
    # buy() legt ihn an, sell() räumt ihn weg, update_stop() ersetzt ihn nach
    # Partial-TP, sync_protective_stops() heilt Lücken beim Start.

    def _open_stop_orders(self, symbol: str) -> List:
        """Offene SELL-Stop-Orders für `symbol`. reqAllOpenOrders sieht auch
        Orders früherer Sessions (GTC überlebt Neustarts), openTrades nur die
        eigene Session – beide abfragen und per orderId deduplizieren."""
        trades: Dict[int, object] = {}
        for fn_name in ("reqAllOpenOrders", "openTrades"):
            fn = getattr(self._ib, fn_name, None)
            if not callable(fn):
                continue
            try:
                for tr in (fn() or []):
                    trades.setdefault(getattr(tr.order, "orderId", id(tr)), tr)
            except Exception as e:
                log.debug("IBKR %s: %s", fn_name, e)
        base = _norm_symbol(symbol)
        out = []
        for tr in trades.values():
            try:
                if (_norm_symbol(tr.contract.symbol) == base
                        and tr.order.action == "SELL"
                        and tr.order.orderType == "STP"
                        and tr.orderStatus.status not in ("Filled", "Cancelled", "Inactive")):
                    out.append(tr)
            except Exception:
                continue
        return out

    def _cancel_stops(self, symbol: str) -> int:
        """Cancelt alle ruhenden Schutz-Stops für `symbol`. Fehlschlag ist laut:
        ein liegengebliebener Stop würde auf leerer Position shorten."""
        n = 0
        for tr in self._open_stop_orders(symbol):
            try:
                self._ib.cancelOrder(tr.order)
                n += 1
            except Exception as e:
                log.error("IBKR: Schutz-Stop-Cancel %s fehlgeschlagen (%s) – "
                          "Order kann auf leerer Position auslösen (Short-Gefahr)!",
                          symbol, e)
        if n:
            log.info("IBKR: %d Schutz-Stop(s) für %s gecancelt", n, symbol)
        return n

    def _place_stop(self, contract, shares: float, stop_price: float) -> bool:
        """Platziert einen GTC-Stop (SELL). Kein Fill-Warten – die Order ruht."""
        try:
            from ib_insync import StopOrder
            whole = math.floor(float(shares))
            stop_price = round(float(stop_price), 2)
            if whole <= 0 or stop_price <= 0:
                return False
            order = StopOrder("SELL", whole, stop_price, tif="GTC")
            account = getattr(self, "_active_account", _ACCOUNT) or _ACCOUNT
            if account:
                order.account = account
            self._ib.placeOrder(contract, order)
            log.info("IBKR: GTC-Schutz-Stop platziert: SELL %d %s @ %.2f",
                     whole, contract.symbol, stop_price)
            return True
        except Exception as e:
            log.error("IBKR: Schutz-Stop für %s NICHT platziert: %s", contract.symbol, e)
            return False

    @_synchronized
    def update_stop(self, ticker: str, shares: float, stop_price: float) -> bool:
        """Ersetzt den Schutz-Stop (z.B. nach Partial-TP: neue Restmenge/SL).
        shares<=0 oder stop_price<=0 → nur aufräumen."""
        if not _SERVER_STOPS or not self._ensure_connected():
            return False
        try:
            contract = self._stock_contract(ticker)
            if not self._ib.qualifyContracts(contract):
                return False
            self._cancel_stops(contract.symbol)
            if shares <= 0 or not stop_price or stop_price <= 0:
                return True
            return self._place_stop(contract, shares, stop_price)
        except Exception as e:
            log.error("IBKR update_stop %s: %s", ticker, e)
            return False

    @_synchronized
    def sync_protective_stops(self, book: Dict[str, tuple]) -> Optional[Dict[str, bool]]:
        """Start-Heilung: stellt sicher, dass jede Buch-Position einen ruhenden
        GTC-Stop hat (Positionen aus der Zeit vor diesem Feature, verlorene
        Orders). `book` = {ticker: (shares, stop_loss)}. Bestehende Stops werden
        NICHT angefasst (Preis-Anpassung wäre Doppel-Management mit der
        Trailing-Logik des Bots). None = nicht prüfbar (offline/abgeschaltet)."""
        if not _SERVER_STOPS or not self._ensure_connected():
            return None
        result: Dict[str, bool] = {}
        for ticker, (shares, stop_price) in book.items():
            try:
                contract = self._stock_contract(ticker)
                if not self._ib.qualifyContracts(contract):
                    result[ticker] = False
                    continue
                if self._open_stop_orders(contract.symbol):
                    result[ticker] = True  # bereits geschützt
                    continue
                result[ticker] = self._place_stop(contract, shares, stop_price)
            except Exception as e:
                log.warning("IBKR Stop-Sync %s: %s", ticker, e)
                result[ticker] = False
        return result

    @log_order("BUY")
    @_synchronized
    def buy_crypto(self, symbol: str, usd_amount: float) -> Dict:
        if not self._ensure_connected():
            return OrderResult.error(reason="IBKR nicht verbunden", mode="ibkr")
        try:
            price = self.get_crypto_price(symbol)
            if not price:
                return OrderResult.error(ticker=symbol, reason=f"Kein Preis für {symbol}", mode="ibkr")
            qty = round(usd_amount / price, 6)
            contract = self._crypto_contract(symbol)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                return OrderResult.error(ticker=symbol, reason=f"Crypto-Contract {symbol} nicht qualifizierbar", mode="ibkr")
            result = self._place_order(contract, "BUY", qty)
            result["usd_amount"] = usd_amount
            return result
        except Exception as e:
            log.exception("IBKR buy_crypto %s: %s", symbol, e)
            return OrderResult.error(reason=str(e), mode="ibkr")

    @log_order("SELL")
    @_synchronized
    def sell_crypto(self, symbol: str, qty: float) -> Dict:
        if not self._ensure_connected():
            return OrderResult.error(reason="IBKR nicht verbunden", mode="ibkr")
        try:
            contract = self._crypto_contract(symbol)
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                return OrderResult.error(ticker=symbol, reason=f"Crypto-Contract {symbol} nicht qualifizierbar", mode="ibkr")
            return self._place_order(contract, "SELL", qty)
        except Exception as e:
            log.exception("IBKR sell_crypto %s: %s", symbol, e)
            return OrderResult.error(reason=str(e), mode="ibkr")

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

    @_synchronized
    def positions(self) -> Optional[Dict[str, float]]:
        """Tatsächlich bei IBKR gehaltene Positionen als {symbol: shares}.

        Gibt **None** zurück, wenn der Stand nicht ermittelbar ist (nicht
        verbunden / Fehler). Aufrufer dürfen None NICHT als "flach" deuten –
        sonst würde ein Verbindungsabriss fälschlich alle Buch-Positionen als
        Phantome markieren. Ein leeres Dict {} heißt dagegen sicher "flach".
        """
        if not self._ensure_connected():
            return None
        try:
            account = getattr(self, "_active_account", _ACCOUNT) or _ACCOUNT \
                or (self._ib.managedAccounts() or [""])[0]
            result: Dict[str, float] = {}
            for p in self._ib.positions(account):
                sym = p.contract.symbol
                result[sym] = result.get(sym, 0.0) + float(p.position)
            return result
        except Exception as e:
            log.warning("IBKR positions(): %s", e)
            return None

    def get_filled_limit_orders(self, order_ids: List[int]) -> List[Dict]:
        """Prüft, welche der übergebenen IBKR-Order-IDs (Conditional-Entry-Limit-
        Orders) inzwischen ausgeführt wurden. Gibt eine Liste
        {order_id, fill_price, shares} der GEFÜLLTEN zurück.

        Diese Methode fehlte – der Fill-Check-Job im Scheduler rief sie auf und
        scheiterte still (AttributeError), d.h. Limit-Order-Fills wurden nie ins
        Buch übernommen. None-sicher: bei fehlender Verbindung leere Liste."""
        if not order_ids or not self._ensure_connected():
            return []
        wanted = {int(o) for o in order_ids}
        result: List[Dict] = []
        try:
            # reqCompletedOrders holt auch Fills aus früheren Sessions (best effort).
            try:
                self._ib.reqCompletedOrders(apiOnly=True)
                self._ib.sleep(1)
            except Exception:
                pass
            seen: set = set()
            for trade in list(self._ib.trades()):
                try:
                    oid = int(trade.order.orderId)
                except Exception:
                    continue
                if oid not in wanted or oid in seen:
                    continue
                st = trade.orderStatus.status
                filled = float(getattr(trade.orderStatus, "filled", 0) or 0)
                if st == "Filled" or filled > 0:
                    seen.add(oid)
                    result.append({
                        "order_id":   oid,
                        "fill_price": float(trade.orderStatus.avgFillPrice or 0.0),
                        "shares":     filled or float(getattr(trade.order, "totalQuantity", 0) or 0),
                    })
        except Exception as e:
            log.warning("IBKR get_filled_limit_orders: %s", e)
        return result

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
                close = hist["Close"].iloc[-1]
                if _valid_price(close):
                    return round(float(close), 4)
        except Exception as e:
            log.debug("yfinance fallback %s: %s", ticker, e)
        return None

    @staticmethod
    def _yf_history(ticker: str, yf_period: str):
        try:
            import yfinance as yf
            return yf.Ticker(ticker).history(period=yf_period)
        except Exception as e:
            log.debug("yfinance history fallback %s: %s", ticker, e)
            return None
