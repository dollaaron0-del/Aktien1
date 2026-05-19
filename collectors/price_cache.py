"""
Price Cache – batched yfinance requests with TTL caching.

Verhindert redundante HTTP-Requests wenn mehrere Komponenten
zur gleichen Zeit denselben Ticker-Kurs abrufen.
"""
import threading
import time
from typing import Dict, List, Optional
import yfinance as yf
from logger import get_logger

log = get_logger(__name__)

_CACHE_TTL_SECONDS = 30      # Kurs 30s gültig (Intraday reicht EOD+30s)
_BATCH_SIZE        = 20      # Max Ticker pro yfinance Download-Call


class PriceCache:
    """
    Singleton-fähiger TTL-Cache für Aktienkurse.

    Batcht mehrere Ticker in einem Download-Call (viel effizienter als
    einzelne yf.Ticker(t).history() Aufrufe).
    """
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "PriceCache":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._cache: Dict[str, tuple] = {}  # ticker -> (price, timestamp)
        self._lock = threading.RLock()

    def get(self, ticker: str) -> Optional[float]:
        """Single ticker lookup, cached."""
        with self._lock:
            entry = self._cache.get(ticker)
            if entry and time.monotonic() - entry[1] < _CACHE_TTL_SECONDS:
                return entry[0]

        prices = self.get_many([ticker])
        return prices.get(ticker)

    def get_many(self, tickers: List[str]) -> Dict[str, float]:
        """Batch lookup – fetches stale/missing tickers in one yfinance call."""
        result: Dict[str, float] = {}
        to_fetch: List[str] = []
        now = time.monotonic()

        with self._lock:
            for t in tickers:
                entry = self._cache.get(t)
                if entry and now - entry[1] < _CACHE_TTL_SECONDS:
                    result[t] = entry[0]
                else:
                    to_fetch.append(t)

        if not to_fetch:
            return result

        # Batch-Download in Chunks
        for i in range(0, len(to_fetch), _BATCH_SIZE):
            chunk = to_fetch[i:i + _BATCH_SIZE]
            self._fetch_chunk(chunk, result)

        return result

    def _fetch_chunk(self, tickers: List[str], result: Dict[str, float]) -> None:
        try:
            space_sep = " ".join(tickers)
            df = yf.download(space_sep, period="1d", auto_adjust=True, progress=False)

            now = time.monotonic()
            with self._lock:
                if len(tickers) == 1:
                    if not df.empty:
                        price = round(float(df["Close"].iloc[-1]), 2)
                        self._cache[tickers[0]] = (price, now)
                        result[tickers[0]] = price
                else:
                    close = df.get("Close", df)
                    for t in tickers:
                        try:
                            price = round(float(close[t].dropna().iloc[-1]), 2)
                            self._cache[t] = (price, now)
                            result[t] = price
                        except (KeyError, IndexError):
                            pass
        except Exception as e:
            log.warning("PriceCache: Batch-Download fehlgeschlagen (%s): %s", tickers[:3], e)

    def invalidate(self, ticker: str) -> None:
        """Cache-Eintrag invalidieren (z.B. nach Trade)."""
        with self._lock:
            self._cache.pop(ticker, None)

    def stats(self) -> Dict:
        with self._lock:
            now = time.monotonic()
            valid = sum(1 for _, ts in self._cache.values() if now - ts < _CACHE_TTL_SECONDS)
            return {"cached": len(self._cache), "valid": valid, "ttl_seconds": _CACHE_TTL_SECONDS}


# Modul-Level Singleton für einfachen Import
_cache = PriceCache.get_instance()


def get_price(ticker: str) -> Optional[float]:
    return _cache.get(ticker)


def get_prices(tickers: List[str]) -> Dict[str, float]:
    return _cache.get_many(tickers)


def invalidate(ticker: str) -> None:
    _cache.invalidate(ticker)
