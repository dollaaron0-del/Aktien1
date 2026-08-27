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
# Handelszeiten-Gate: Market-Orders nur einreichen, wenn die zuständige Börse
# offen ist. Ohne das Gate cancelt IBKR die Order sofort, der Bot deutet das als
# Fill-Timeout und meldet einen Fehlschlag – bei SELL zusätzlich nachdem der
# Schutz-Stop schon weggeräumt war. Gilt nur für Aktien; Krypto (PAXOS) läuft
# 24/7 über buy_crypto/sell_crypto und wird bewusst nicht gegated.
_MARKET_HOURS_GATE = os.getenv("IBKR_MARKET_HOURS_GATE", "true").strip().lower() in ("1", "true", "yes")


def _closed_market_reason(ticker: str) -> Optional[str]:
    """Grund, falls die Börse für `ticker` gerade zu ist. Fail-open: kann der
    Kalender nicht befragt werden, blockiert das Gate nichts."""
    if not _MARKET_HOURS_GATE:
        return None
    try:
        from analyzers.market_schedule import market_closed_reason
        return market_closed_reason(ticker)
    except Exception as e:
        log.debug("Handelszeiten-Gate übersprungen [%s]: %s", ticker, e)
        return None

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


def _market_price_extra(reference_price: Optional[float]) -> Optional[Dict]:
    """OrderResult.extra-Fragment für die Slippage-Kalibrierung (Roadmap 5.3).

    None (nicht leeres Dict) wenn kein Referenzpreis vorliegt, damit
    OrderResult.__init__ das `if extra:`-Update sauber überspringt statt ein
    leeres market_price=None zu speichern."""
    if reference_price is None:
        return None
    return {"market_price": reference_price}


def _valid_price(p) -> bool:
    """True nur für eine echte, positive Zahl. marketPrice() liefert bei
    fehlendem Abo/Tick NaN – und NaN ist truthy, NaN>0 jedoch False, weshalb
    ein naives `if p and p > 0` NaN durchrutschen lassen kann. Hier zentral
    abgesichert (vgl. yfinance-NaN-Score-Falle)."""
    try:
        return p is not None and not math.isnan(float(p)) and float(p) > 0
    except (TypeError, ValueError):
        return False


def _ticker_price(td, stale_close_fallback=None) -> Optional[float]:
    """Bester verfügbarer Preis aus einem ib_insync-Ticker: marketPrice
    (Midpoint), dann last. Beides sind echte Live-Ticks dieser Session -
    wenn vorhanden, verlässlich.

    Sind beide ungültig (kein Live-Tick diese Session – typisch bei
    geschlossenem Markt oder einer Subscription ohne ersten Tick), ist
    td.close KEINE verlässliche Quelle mehr: das Feld kann tagealt sein,
    ohne dass sich das an irgendeinem Zeitstempel erkennen ließe (td.time
    zeigt nur den Empfang des Snapshots, nicht das Alter des Preises
    selbst). Realer Vorfall 25.7.2026: td.close lieferte für SAP den
    Schluss von vor ZWEI Handelstagen (128,32 statt 141,16), während
    marketPrice/last beide -1 (ungültig) waren – der Bot hielt das für
    einen Stop-Loss-Bruch und versuchte wiederholt zu verkaufen, obwohl
    die Position komfortabel im Plus lag.

    `stale_close_fallback` ist ein Callable, das eine verlässliche
    Quelle für "letzter bekannter Schluss" liefert (siehe
    IBKRBroker._historical_close: reqHistoricalData statt Streaming-Cache).
    Erst wenn auch das fehlschlägt, wird td.close als letzter Ausweg
    genutzt (besser ein möglicherweise alter Preis als gar keiner)."""
    for getter in (lambda: td.marketPrice(), lambda: getattr(td, "last", None)):
        try:
            p = getter()
        except Exception:
            continue
        if _valid_price(p):
            return round(float(p), 4)

    if stale_close_fallback is not None:
        try:
            p = stale_close_fallback()
            if _valid_price(p):
                return round(float(p), 4)
        except Exception:
            pass

    try:
        p = getattr(td, "close", None)
        if _valid_price(p):
            return round(float(p), 4)
    except Exception:
        pass
    return None


class IBKRBroker:
    """
    Thin synchronous wrapper um ib_insync für den Trading-Bot.
    Verbindung wird beim ersten Aufruf von connect() hergestellt.
    """

    def __init__(self, client_id: Optional[int] = None, readonly: bool = False):
        """`client_id`/`readonly` erlauben eine ZWEITE, rein lesende Verbindung
        zum selben Gateway (Dashboard/Telegram über broker.factory), ohne die
        Handels-Session des Live-Bots (eigene Client-ID) zu stören. Ohne
        Angabe identisch zum bisherigen Verhalten (main.py: Client-ID aus
        IBKR_CLIENT_ID, volle Handelsrechte)."""
        self._ib = None
        self._connected = False
        self._active_account: str = _ACCOUNT
        self._lock = threading.RLock()
        self._client_id = client_id if client_id is not None else _CLIENT_ID
        self._readonly = readonly
        # conId/Symbol → [(lowEdge, increment), …] aus reqMarketRule; die
        # Tick-Staffel einer Aktie ändert sich nicht innerhalb einer Session.
        self._market_rule_cache: Dict = {}
        # conId/Symbol → (Preis, monotonic-Zeit) für _historical_close.
        self._hist_close_cache: Dict = {}
        # permIds übergroßer Schutz-Stops fremder Client-IDs, die wir per API
        # NICHT stornieren können (IBKR erlaubt cancelOrder nur für Orders der
        # eigenen Client-ID). Einmal pro Session warnen statt jeden Zyklus.
        self._uncancelable_stops: set = set()
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
            log.info("IBKR: Verbindungsversuch %s:%d (clientId=%d%s) …",
                     _HOST, _PORT, self._client_id, ", readonly" if self._readonly else "")
            ib.connect(_HOST, _PORT, clientId=self._client_id, readonly=self._readonly, timeout=10)
            # ib_insync.IB.RequestTimeout ist per Default 0 (= KEIN Timeout) für
            # alle blockierenden Aufrufe, die intern über IB._run()/util.run()
            # laufen (whatIfOrder, qualifyContracts, …) – bleibt IB Gateway eine
            # Antwort schuldig (beobachtet bei whatIfOrder für einen ausländischen
            # Titel), haengt der Aufruf für IMMER und blockiert wegen @_synchronized
            # den gesamten Broker (auch Preis-Abrufe/Sells anderer Ticker). Fester
            # Timeout macht solche Aufrufe zu einem klaren TimeoutError statt
            # einem Totalausfall.
            ib.RequestTimeout = 20
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

    # Cache für _historical_close: mehrere Preisabfragen kurz hintereinander
    # (z.B. get_prices für ein ganzes Portfolio) sollen nicht pro Ticker eine
    # eigene reqHistoricalData-Runde auslösen. Kurze TTL genügt – der Fallback
    # greift ohnehin nur, wenn diese Session noch keinen Live-Tick bekam.
    _HIST_CLOSE_TTL = 60

    def _historical_close(self, contract) -> Optional[float]:
        """Letzter bekannter Tagesschluss via reqHistoricalData – verlässliche
        Quelle, wenn weder marketPrice() noch last einen Live-Tick liefern.

        Anders als der Streaming-Snapshot (td.close, kann tagealt im Cache
        hängen bleiben, siehe _ticker_price-Docstring) fragt reqHistoricalData
        aktiv bei IBKR nach und lieferte im SAP-Vorfall den korrekten
        Freitagsschluss (141,16), während der Snapshot bei 128,32 (Mittwoch)
        hängengeblieben war."""
        key = getattr(contract, "conId", None) or contract.symbol
        now = time.monotonic()
        cached = self._hist_close_cache.get(key)
        if cached and now - cached[1] < self._HIST_CLOSE_TTL:
            return cached[0]
        try:
            bars = self._ib.reqHistoricalData(
                contract, endDateTime="", durationStr="2 D",
                barSizeSetting="1 day", whatToShow="TRADES",
                useRTH=True, formatDate=1,
            )
            price = float(bars[-1].close) if bars else None
        except Exception as e:
            log.debug("IBKR _historical_close %s: %s", getattr(contract, "symbol", "?"), e)
            price = None
        if price is not None:
            self._hist_close_cache[key] = (price, now)
        return price

    @_synchronized
    def get_price(self, ticker: str) -> Optional[float]:
        if not self._ensure_connected():
            return self._yf_price(ticker)
        try:
            contract = self._stock_contract(ticker)
            self._ib.qualifyContracts(contract)
            ticker_data = self._ib.reqMktData(contract, "", False, False)
            self._ib.sleep(_PRICE_TIMEOUT)
            price = _ticker_price(ticker_data, lambda: self._historical_close(contract))
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
            contract_map = {t: c for t, c in contracts}
            for t, c in contracts:
                td = self._ib.reqMktData(c, "", False, False)
                ticker_map[t] = td

            self._ib.sleep(_PRICE_TIMEOUT)

            for t, td in ticker_map.items():
                c = contract_map[t]
                p = _ticker_price(td, lambda c=c: self._historical_close(c))
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

    def _place_order(self, contract, action: str, shares: float,
                     cash_qty: Optional[float] = None,
                     reference_price: Optional[float] = None) -> Dict:
        from ib_insync import MarketOrder
        # Hinweis: _place_order wird auch von buy_crypto/sell_crypto genutzt –
        # die brauchen Bruchteile. Aktien werden VORHER über _whole_shares()
        # auf ganze Stück gerundet (siehe buy()/sell()).
        # reference_price (Roadmap 5.3): der Preis, mit dem der Aufrufer die
        # Order-Entscheidung getroffen hat (Kurs zum Analysezeitpunkt, keine
        # Live-Quote unmittelbar vor Order-Einreichung) – landet unverändert
        # in OrderResult.extra["market_price"], rein zu Diagnose-/Kalibrierungs-
        # zwecken (order_log.py persistiert es). Beeinflusst NICHT die Order
        # selbst (weiterhin MarketOrder ohne Limit).
        order = MarketOrder(action, shares)
        # IBKR-Krypto-Orders (PAXOS) lehnen sonst mit Error 10289 ab
        # ("You must set Cash Quantity for this order") – cash_qty wird nur
        # von buy_crypto() durchgereicht, Aktien-Orders bleiben unberührt.
        # PAXOS akzeptiert bei Market-Orders ausserdem nur tif=IOC (Error
        # 10052 "Invalid time in force" bei ib_insync-Default "DAY") UND
        # verlangt totalQuantity=0, wenn cashQty gesetzt ist (Error 10293
        # "Cryptocurrency Cash Quantity order cannot specify size" – beides
        # gleichzeitig ist nicht erlaubt).
        if cash_qty is not None:
            order.cashQty = round(float(cash_qty), 2)
            order.totalQuantity = 0
            order.tif = "IOC"
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
                    extra=_market_price_extra(reference_price),
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
                extra=_market_price_extra(reference_price),
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
                extra=_market_price_extra(reference_price),
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
        _closed = _closed_market_reason(ticker)
        if _closed:
            log.info("IBKR: BUY %s nicht eingereicht – %s", ticker, _closed)
            return OrderResult.error(ticker=ticker, reason=_closed, mode="ibkr")
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
            result = self._place_order(contract, "BUY", whole, reference_price=price)
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
        # VOR dem Stop-Cancel prüfen: bei geschlossener Börse würde die Order
        # ohnehin sofort gecancelt – der Schutz-Stop bliebe dann grundlos weg.
        _closed = _closed_market_reason(ticker)
        if _closed:
            log.info("IBKR: SELL %s nicht eingereicht – %s (Schutz-Stop bleibt liegen)",
                     ticker, _closed)
            return OrderResult.error(ticker=ticker, reason=_closed, mode="ibkr")
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
            # Vorher die Parameter sichern: geht der Verkauf NICHT (voll) durch,
            # wird der Stop unten auf die verbliebene Menge wiederhergestellt.
            # (Ohne das stand SAP.DE am 25.7.2026 nach drei gescheiterten SELL-
            # Versuchen dauerhaft ohne Broker-Stop da.)
            stop_backup: List[tuple] = []
            if _SERVER_STOPS:
                stop_backup = self._snapshot_stops(contract.symbol)
                self._cancel_stops(contract.symbol)
            result = self._place_order(contract, "SELL", whole, reference_price=price)
            if _SERVER_STOPS and stop_backup:
                self._restore_stops(contract, stop_backup, result)
            return result
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

    def _snapshot_stops(self, symbol: str) -> List[tuple]:
        """Parameter der ruhenden Schutz-Stops als [(shares, stop_price), …].
        Grundlage für die Wiederherstellung, falls ein Verkauf scheitert."""
        out: List[tuple] = []
        for tr in self._open_stop_orders(symbol):
            try:
                qty  = float(getattr(tr.order, "totalQuantity", 0) or 0)
                aux  = float(getattr(tr.order, "auxPrice", 0) or 0)
                if qty > 0 and aux > 0:
                    out.append((qty, aux))
            except (TypeError, ValueError):
                continue
        return out

    def _restore_stops(self, contract, backup: List[tuple], result: Dict) -> None:
        """Stellt nach einem nicht (voll) ausgeführten SELL den Schutz-Stop für
        die verbliebene Menge wieder her. Bei vollem Fill bleibt die Position
        leer – dann ist der weggeräumte Stop korrekt und es passiert nichts."""
        try:
            sold = 0.0
            if result.get("status") == "filled":
                sold = float(result.get("shares") or 0)
            for qty, stop_price in backup:
                remaining = qty - sold
                if remaining < 1:
                    continue
                if self._place_stop(contract, remaining, stop_price):
                    log.info("IBKR: Schutz-Stop für %s nach nicht ausgeführtem SELL "
                             "wiederhergestellt (%d Stück @ %.2f)",
                             contract.symbol, int(remaining), stop_price)
                else:
                    log.error("IBKR: Schutz-Stop für %s nach gescheitertem SELL NICHT "
                              "wiederhergestellt – Position ist broker-seitig ungeschützt!",
                              contract.symbol)
        except Exception as e:
            log.error("IBKR: Schutz-Stop-Wiederherstellung %s fehlgeschlagen: %s",
                      getattr(contract, "symbol", "?"), e)

    def _tick_size(self, contract, price: float) -> float:
        """Gültige Kursschrittweite für `contract` bei `price`.

        Börsen staffeln die Tick-Größe nach Kurshöhe (MiFID-Regime): eine
        XETRA-Aktie tickt bei 66 € in 0,05er-Schritten, bei 956 € in 0,10er.
        `contractDetails.minTick` gibt dafür nur den kleinsten Wert des
        gesamten Bandes zurück (0,0001) und ist damit nutzlos – die echten
        Stufen liefert reqMarketRule. Ergebnis wird je Kontrakt gecacht.
        Fail-open: ohne Regel-Antwort 0.01 (bisheriges Verhalten)."""
        default = 0.01
        try:
            key = (getattr(contract, "conId", None) or contract.symbol)
            rule = self._market_rule_cache.get(key)
            if rule is None:
                details = self._ib.reqContractDetails(contract)
                if not details:
                    return default
                rule_id = str(details[0].marketRuleIds).split(",")[0].strip()
                rule = sorted(
                    ((float(i.lowEdge), float(i.increment))
                     for i in self._ib.reqMarketRule(int(rule_id))),
                    key=lambda x: x[0],
                )
                self._market_rule_cache[key] = rule
            tick = default
            for low_edge, increment in rule:
                if price >= low_edge:
                    tick = increment
            return tick or default
        except Exception as e:
            log.debug("IBKR: Tick-Size für %s nicht ermittelbar (%s) – nutze %.2f",
                      getattr(contract, "symbol", "?"), e, default)
            return default

    def _conform_price(self, contract, price: float) -> float:
        """Stop-Preis auf eine gültige Kursschrittweite bringen.

        Nicht konforme Preise lehnt IBKR mit Error 110 ab ("does not conform to
        the minimum price variation") – am 24.7.2026 blieben dadurch die Stops
        für DWS (66,13 bei Tick 0,05) und RHM (955,98 bei Tick 0,10) still auf
        der Strecke. Es wird ABGERUNDET: ein Schutz-Stop darf lieber minimal
        tiefer liegen als zu früh auslösen."""
        tick = self._tick_size(contract, price)
        steps = price / tick
        if abs(steps - round(steps)) < 1e-6:
            return round(price, 6)          # schon konform – nicht verschieben
        return round(math.floor(steps) * tick, 6)

    # Stop-Orders ruhen (kein Fill-Warten), aber die ANNAHME muss bestätigt
    # sein. Sekunden, die auf einen belastbaren Order-Status gewartet wird.
    _STOP_ACK_TIMEOUT = 5

    def _stop_qty_cap(self, symbol: str, whole: int) -> Optional[int]:
        """Deckelt die Stop-Menge auf die WIRKLICH bei IBKR gehaltene Stückzahl.

        Rückgabe: erlaubte Menge, oder None = gar keinen Stop legen.

        Hintergrund (22.8.2026): Ein Schutz-Stop wurde bisher blind über die
        BUCH-Menge gelegt. Lief das Buch gegenüber IBKR auseinander, war die
        ruhende GTC-Order größer als der reale Bestand — beim Auslösen drehte
        sie das Konto short, exakt um die Differenz. Nachweisbar an acht
        Positionen (z.B. S: IBKR 1 Stk, Buch 695 → −694; SSNC: 13 − 23 → −10).
        Der Anti-Short-Schutz im Executor griff nur für Markt-Verkäufe; die
        ruhende Order umging ihn und richtete den Schaden zeitversetzt an.

        `positions()` liefert None, wenn der Stand nicht ermittelbar ist – dann
        bleibt es fail-open beim alten Verhalten (lieber ein Stop mit
        Buch-Menge als eine ungeschützte Position), aber mit Warnung.
        """
        held_map = self.positions()
        if held_map is None:
            log.warning(
                "IBKR: Bestand für %s nicht ermittelbar – Schutz-Stop wird "
                "ungeprüft über die Buch-Menge (%d) gelegt.", symbol, whole,
            )
            return whole
        held = math.floor(held_map.get(symbol, 0.0))
        if held <= 0:
            log.error(
                "IBKR: KEIN Schutz-Stop für %s – IBKR hält %g, Buch will %d "
                "absichern. Eine Stop-Order würde das Konto shorten statt "
                "schützen (Buch/IBKR-Desync).", symbol, held_map.get(symbol, 0.0), whole,
            )
            return None
        if held < whole:
            log.warning(
                "IBKR: Schutz-Stop für %s auf %d Stück gedeckelt (Buch wollte "
                "%d, IBKR hält %d) – die Differenz wäre ein Short gewesen.",
                symbol, held, whole, held,
            )
            return held
        return whole

    def _place_stop(self, contract, shares: float, stop_price: float) -> bool:
        """Platziert einen GTC-Stop (SELL) und verifiziert die Annahme.

        Früher wurde direkt nach placeOrder True gemeldet. Lehnte IBKR den Stop
        danach ab, blieb das unsichtbar: der Bot loggte "Schutz-Stop platziert"
        und meldete beim Start "N Positionen abgesichert", während real keine
        Order lag (25.7.2026: 3 von 23 Positionen ungeschützt, ohne jede
        Warnung). Ein nicht bestätigter Stop gilt jetzt als Fehlschlag.

        Die Menge wird zusätzlich gegen den echten IBKR-Bestand gedeckelt
        (siehe _stop_qty_cap) – ein zu großer Stop schützt nicht, er shortet."""
        try:
            from ib_insync import StopOrder
            whole = math.floor(float(shares))
            if whole <= 0 or not stop_price or float(stop_price) <= 0:
                return False

            capped = self._stop_qty_cap(contract.symbol, whole)
            if capped is None:
                return False
            whole = capped
            stop_price = self._conform_price(contract, float(stop_price))
            if stop_price <= 0:
                return False
            order = StopOrder("SELL", whole, stop_price, tif="GTC")
            account = getattr(self, "_active_account", _ACCOUNT) or _ACCOUNT
            if account:
                order.account = account
            trade = self._ib.placeOrder(contract, order)

            deadline = time.monotonic() + self._STOP_ACK_TIMEOUT
            status = getattr(trade.orderStatus, "status", "")
            while time.monotonic() < deadline:
                status = getattr(trade.orderStatus, "status", "")
                if status in ("PreSubmitted", "Submitted", "Filled",
                              "Cancelled", "Inactive", "ApiCancelled"):
                    break
                self._ib.sleep(0.5)

            if status in ("PreSubmitted", "Submitted", "Filled"):
                log.info("IBKR: GTC-Schutz-Stop platziert: SELL %d %s @ %s",
                         whole, contract.symbol, stop_price)
                return True

            why = "; ".join(
                e.message for e in getattr(trade, "log", []) if getattr(e, "message", "")
            ) or f"Status {status or 'unbekannt'}"
            log.error("IBKR: Schutz-Stop für %s NICHT angenommen (SELL %d @ %s): %s",
                      contract.symbol, whole, stop_price, why)
            return False
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
        self._cancel_oversized_stops()
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

    def _cancel_oversized_stops(self) -> int:
        """Räumt ruhende Schutz-Stops ab, deren Menge den REALEN Bestand
        übersteigt – die würden beim Auslösen shorten statt zu schützen.

        Bewusst rein broker-seitig entschieden (Bestand vs. Order-Menge), nicht
        gegen das Buch: ein Stop auf einer Position, die das Buch nicht kennt,
        ist solange harmlos, wie er gedeckt ist – abgeräumt wird nur das, was
        tatsächlich Schaden anrichten kann. Damit bleiben gedeckte Stops (auch
        für unbekannte Positionen) erhalten.

        Fand am 22.8.2026 vier Altlasten, die nie storniert wurden, nachdem
        ihre Buch-Position als Phantom ausgebucht worden war (3× SAP über
        105/276/406 Stück bei 1 real gehaltenen, 1× RHM über 35 bei 16).
        """
        held_map = self.positions()
        if held_map is None:
            return 0  # Bestand unbekannt – nichts anfassen
        seen: Dict[int, object] = {}
        for fn_name in ("reqAllOpenOrders", "openTrades"):
            fn = getattr(self._ib, fn_name, None)
            if not callable(fn):
                continue
            try:
                for tr in (fn() or []):
                    seen.setdefault(getattr(tr.order, "orderId", id(tr)), tr)
            except Exception as e:
                log.debug("IBKR %s: %s", fn_name, e)

        n = 0
        for tr in seen.values():
            try:
                o = tr.order
                if (o.action != "SELL" or o.orderType != "STP"
                        or tr.orderStatus.status in ("Filled", "Cancelled", "Inactive")):
                    continue
                sym = tr.contract.symbol
                held = math.floor(held_map.get(sym, 0.0))
                qty = float(o.totalQuantity or 0)
                if qty <= max(held, 0):
                    continue

                # cancelOrder greift nur bei Orders der EIGENEN Client-ID
                # (bzw. clientId 0). Ein übergroßer Stop einer früheren Session
                # (andere Client-ID) lässt sich per API nicht wegräumen – dann
                # bringt ein Storno-Versuch jeden Zyklus nur einen Error-10147-
                # Log. Einmal pro Session klar warnen, dann in Ruhe lassen.
                order_cid = getattr(o, "clientId", None)
                if order_cid not in (0, self._client_id):
                    perm = getattr(o, "permId", None) or id(o)
                    if perm not in self._uncancelable_stops:
                        self._uncancelable_stops.add(perm)
                        log.warning(
                            "IBKR: übergroßer Schutz-Stop für %s (%g Stück, nur %d "
                            "real) stammt aus fremder Session (clientId=%s) und ist "
                            "per API nicht stornierbar – bitte im Gateway/TWS manuell "
                            "canceln (permId=%s).",
                            sym, qty, held, order_cid, perm,
                        )
                    continue

                log.error(
                    "IBKR: Schutz-Stop für %s über %g Stück bei nur %d real "
                    "gehaltenen – würde %g Stück shorten, wird storniert.",
                    sym, qty, held, qty - max(held, 0),
                )
                self._ib.cancelOrder(o)
                n += 1
            except Exception as e:
                log.error("IBKR: Storno eines übergroßen Schutz-Stops "
                          "fehlgeschlagen: %s", e)
        if n:
            log.warning("IBKR: %d übergroße(r) Schutz-Stop(s) storniert.", n)
        return n

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
            result = self._place_order(contract, "BUY", qty, cash_qty=usd_amount)
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
            # reqPositions() blockiert bis "positionEnd" – erzwingt einen
            # VOLLSTÄNDIGEN Stand. Ohne das liefert self._ib.positions() direkt
            # nach dem Connect (oder Reconnect) den noch halb gefüllten Cache;
            # reconcile_against_broker deutete die fehlenden Positionen dann als
            # Phantome und buchte echte Bestände mit fiktivem Verlust aus
            # (Buch-Korruption Juli–August 2026: Buch $1,0 Mio → $0,5 Mio,
            # während das IBKR-Konto unverändert bei ~$1,04 Mio stand).
            try:
                self._ib.reqPositions()
                self._ib.sleep(0.2)
            except Exception as e:
                log.debug("IBKR reqPositions(): %s", e)
            raw = self._ib.positions(account)
            if not raw:
                # Echt flach ist möglich – aber direkt nach Connect ist ein
                # leeres Ergebnis verdächtig. Einmal nachfassen.
                self._ib.sleep(0.5)
                raw = self._ib.positions(account)
            result: Dict[str, float] = {}
            for p in raw:
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
