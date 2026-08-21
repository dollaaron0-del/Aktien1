"""
analyzers/liquidity.py – Markt-Liquiditäts-Gate vor Entry.

Bisher prüfte die Strategie nur die *Cash*-Liquidität (genug Geld da), aber NICHT
ob die Aktie selbst handelbar ist. Bei illiquiden Small-Caps führt das zu
schlechten Fills, hoher Slippage und Positionen, die man kaum wieder los wird.

Dieses Gate prüft das durchschnittliche Tages-Dollar-Volumen (ADV = Ø-Volumen ×
Kurs) über die letzten N Tage. Liegt es unter der Schwelle, wird der Kauf
übersprungen. Krypto ist ausgenommen (andere Liquiditätsstruktur).

Konfiguration (.env):
  LIQUIDITY_GATE_ENABLED   (Standard: true)
  LIQUIDITY_MIN_ADV_USD    (Standard: 1_000_000 = 1 Mio USD/Tag)
  LIQUIDITY_LOOKBACK_DAYS  (Standard: 30)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

_ENABLED       = os.getenv("LIQUIDITY_GATE_ENABLED", "true").lower() in ("1", "true", "yes")
_MIN_ADV_USD   = float(os.getenv("LIQUIDITY_MIN_ADV_USD", str(1_000_000)))
_LOOKBACK_DAYS = int(os.getenv("LIQUIDITY_LOOKBACK_DAYS", "30"))
_CACHE_TTL_S   = 12 * 3600

# ticker → (LiquidityResult, ts)
_cache: Dict[str, Tuple["LiquidityResult", float]] = {}


@dataclass
class LiquidityResult:
    ok: bool
    adv_usd: float
    avg_volume: float
    reason: str


def _is_crypto(ticker: str) -> bool:
    u = ticker.upper()
    return u.endswith("/USD") or u.endswith("-USD")


def check_liquidity(ticker: str, current_price: Optional[float] = None) -> LiquidityResult:
    """Prüft, ob `ticker` ausreichend liquide für einen Entry ist.

    Fail-open: kann das Volumen nicht ermittelt werden (yfinance-Ausfall), wird
    NICHT blockiert (ok=True) – ein Datenausfall soll nicht den ganzen Handel
    lahmlegen; der Fall wird geloggt."""
    if not _ENABLED:
        return LiquidityResult(True, 0.0, 0.0, "Gate deaktiviert")
    if _is_crypto(ticker):
        return LiquidityResult(True, 0.0, 0.0, "Krypto – Gate übersprungen")

    cached = _cache.get(ticker)
    if cached and (time.time() - cached[1]) < _CACHE_TTL_S:
        return cached[0]

    result = _measure(ticker, current_price)
    _cache[ticker] = (result, time.time())
    return result


def _measure(ticker: str, current_price: Optional[float]) -> LiquidityResult:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=f"{_LOOKBACK_DAYS}d")
        if hist.empty or "Volume" not in hist:
            return LiquidityResult(True, 0.0, 0.0, "Liquidität unbekannt (keine Daten) – nicht geblockt")
        avg_volume = float(hist["Volume"].mean())
        price = current_price or float(hist["Close"].iloc[-1])
        adv_usd = avg_volume * price
    except Exception as e:
        log.debug("[%s] Liquiditäts-Messung fehlgeschlagen: %s", ticker, e)
        return LiquidityResult(True, 0.0, 0.0, "Liquidität unbekannt (Fehler) – nicht geblockt")

    if adv_usd < _MIN_ADV_USD:
        return LiquidityResult(
            False, adv_usd, avg_volume,
            f"Zu illiquide: Ø-Volumen ${adv_usd:,.0f}/Tag < ${_MIN_ADV_USD:,.0f}",
        )
    return LiquidityResult(True, adv_usd, avg_volume, f"Liquide (${adv_usd:,.0f}/Tag)")
