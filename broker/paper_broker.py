"""
PaperBroker – Simuliert Order-Ausführung mit realistischer Slippage-Modellierung.

Slippage-Komponenten:
- Spread:          Basiert auf Liquiditätsstufe (bekannte ETFs vs. Small-Caps)
                   + VIX-Multiplikator: bei hoher Volatilität weiten sich Spreads
- Kommission:      0.1% pro Trade, mindestens $1.00
- Market-Impact:   Nur bei großen Orders (> $50k), zusätzlich 0.05%

VIX-Spread-Multiplikator:
  VIX < 20  → 1.0×  (normaler Markt)
  VIX 20–25 → 1.25× (erhöhte Volatilität)
  VIX 25–30 → 1.75× (stressiger Markt)
  VIX > 30  → 2.5×  (Krisenmodus, Spreads drastisch weiter)

Buy:  fill_price = price * (1 + spread + commission + market_impact)
Sell: fill_price = price * (1 - spread - commission - market_impact)

Konfigurierbar via Env-Variablen:
  SLIPPAGE_SPREAD_PCT       (Standard: 0.001  = 0.10%)
  SLIPPAGE_COMMISSION_PCT   (Standard: 0.001  = 0.10%)
  SLIPPAGE_MIN_COMMISSION   (Standard: 1.0 USD)
"""
import os
from typing import Dict, List, Optional, Tuple

from collectors.price_cache import get_price as _cached_price, get_prices as _cached_prices
from logger import get_logger

log = get_logger(__name__)

# ── Konfiguration via Env-Variablen ────────────────────────────────────────────
_SPREAD_PCT         = float(os.getenv("SLIPPAGE_SPREAD_PCT",     "0.001"))   # 0.10%
_COMMISSION_PCT     = float(os.getenv("SLIPPAGE_COMMISSION_PCT", "0.001"))
_MIN_COMMISSION_USD = float(os.getenv("SLIPPAGE_MIN_COMMISSION", "1.0"))
_MARKET_IMPACT_PCT  = 0.0005   # 0.05% bei Orders > $50k
_LARGE_ORDER_USD    = 50_000.0


def _vix_spread_multiplier() -> float:
    """Spread-Multiplikator basierend auf aktuellem VIX-Level."""
    try:
        from collectors.vix_monitor import get_vix
        vix = get_vix() or 20.0
        if vix > 30:
            return 2.5
        if vix > 25:
            return 1.75
        if vix > 20:
            return 1.25
        return 1.0
    except Exception:
        return 1.0

# Hochliquide ETFs und Large-Cap-Tickers mit engem Spread
_LIQUID_TICKERS = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "GLD", "SLV", "TLT", "HYG",
    "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "NVDA", "TSLA",
    "JPM", "BAC", "WFC", "GS", "MS", "C",
    "XLE", "XLF", "XLK", "XLV", "XLI", "XLU", "XLP", "XLB", "XLC",
}

# Spread-Stufen nach Liquidität
_SPREAD_LIQUID     = _SPREAD_PCT * 0.5   # enger Spread für liquide Werte   (0.05%)
_SPREAD_STANDARD   = _SPREAD_PCT         # Standard-Spread                  (0.10%)
_SPREAD_ILLIQUID   = _SPREAD_PCT * 5.0   # weiter Spread für illiquide Werte (0.50%)


def _get_spread(ticker: str) -> float:
    """Bestimmt den Spread basierend auf Liquiditätsstufe des Tickers."""
    base = ticker.split("/")[0].upper().replace("-USD", "")
    if base in _LIQUID_TICKERS:
        return _SPREAD_LIQUID
    # Heuristic: ETFs (3–4 Buchstaben, bekannte Endungen) = standard
    if len(base) <= 4 and base.isalpha():
        return _SPREAD_STANDARD
    # Unbekannte oder exotische Ticker = illiquide
    return _SPREAD_ILLIQUID


class PaperBroker:
    """Simuliert Order-Ausführung mit realistischer Slippage-Modellierung."""

    def __init__(self) -> None:
        # Slippage-Tracking
        self._total_slippage_usd: float = 0.0
        self._total_commission_usd: float = 0.0
        self._trade_count: int = 0

    def get_price(self, ticker: str) -> Optional[float]:
        return _cached_price(ticker)

    def get_prices(self, tickers: List[str]) -> Dict[str, float]:
        return _cached_prices(tickers)

    # ── Slippage-Berechnung ────────────────────────────────────────────────────

    def _calc_slippage(
        self,
        ticker: str,
        price: float,
        shares: float,
        side: str,  # "BUY" | "SELL"
    ) -> Tuple[float, float, float]:
        """
        Berechnet realistische Ausführungskosten.

        Returns:
            (fill_price, slippage_usd, commission_usd)
        """
        spread      = _get_spread(ticker) * _vix_spread_multiplier()
        order_value = price * abs(shares)

        # Kommission: max(MIN_COMMISSION, order_value * COMMISSION_PCT)
        commission_usd  = max(_MIN_COMMISSION_USD, order_value * _COMMISSION_PCT)
        commission_pct  = commission_usd / order_value if order_value > 0 else _COMMISSION_PCT

        # Market Impact nur bei großen Orders
        market_impact = _MARKET_IMPACT_PCT if order_value > _LARGE_ORDER_USD else 0.0

        total_cost_pct = spread + commission_pct + market_impact

        if side == "BUY":
            fill_price = price * (1.0 + total_cost_pct)
        else:
            fill_price = price * (1.0 - total_cost_pct)

        slippage_usd = abs(fill_price - price) * abs(shares)

        log.debug(
            "Slippage [%s %s]: spread=%.4f%% commission=%.4f%% impact=%.4f%% "
            "order=$%.0f fill=%.4f (vs %.4f)",
            side, ticker,
            spread * 100, commission_pct * 100, market_impact * 100,
            order_value, fill_price, price,
        )

        return fill_price, slippage_usd, commission_usd

    def _track_costs(self, slippage_usd: float, commission_usd: float) -> None:
        """Akkumuliert Handelskosten für Statistik."""
        self._total_slippage_usd   += slippage_usd
        self._total_commission_usd += commission_usd
        self._trade_count          += 1

    # ── Aktien-Orders ──────────────────────────────────────────────────────────

    def buy(self, ticker: str, shares: float, price: float) -> Dict:
        fill_price, slippage, commission = self._calc_slippage(ticker, price, shares, "BUY")
        self._track_costs(slippage, commission)
        return {
            "status":       "filled",
            "ticker":       ticker,
            "shares":       shares,
            "fill_price":   fill_price,
            "market_price": price,
            "slippage_usd": round(slippage, 4),
            "commission":   round(commission, 4),
            "mode":         "paper",
        }

    def sell(self, ticker: str, shares: float, price: float) -> Dict:
        fill_price, slippage, commission = self._calc_slippage(ticker, price, shares, "SELL")
        self._track_costs(slippage, commission)
        return {
            "status":       "filled",
            "ticker":       ticker,
            "shares":       shares,
            "fill_price":   fill_price,
            "market_price": price,
            "slippage_usd": round(slippage, 4),
            "commission":   round(commission, 4),
            "mode":         "paper",
        }

    # ── Krypto-Orders ──────────────────────────────────────────────────────────

    def get_crypto_price(self, symbol: str) -> Optional[float]:
        """Paper-Fallback: nutzt generisches Price-Lookup (yfinance via price_cache)."""
        pair = symbol.split("/")[0].upper() + "-USD"
        price = _cached_price(pair)
        if price is None:
            price = _cached_price(symbol)
        return price

    def buy_crypto(self, symbol: str, usd_amount: float) -> Dict:
        price = self.get_crypto_price(symbol) or 1.0
        qty   = round(usd_amount / price, 6)
        # Krypto: höherer Spread (Krypto-Exchanges haben breiteren Spread)
        fill_price = price * (1.0 + _SPREAD_STANDARD * 2 + _COMMISSION_PCT)
        slippage   = abs(fill_price - price) * qty
        commission = max(_MIN_COMMISSION_USD, usd_amount * _COMMISSION_PCT)
        self._track_costs(slippage, commission)
        return {
            "status":       "filled",
            "ticker":       symbol,
            "qty":          qty,
            "usd_amount":   usd_amount,
            "fill_price":   fill_price,
            "market_price": price,
            "slippage_usd": round(slippage, 4),
            "commission":   round(commission, 4),
            "mode":         "paper",
        }

    def sell_crypto(self, symbol: str, qty: float) -> Dict:
        price = self.get_crypto_price(symbol) or 1.0
        fill_price = price * (1.0 - _SPREAD_STANDARD * 2 - _COMMISSION_PCT)
        slippage   = abs(fill_price - price) * qty
        commission = max(_MIN_COMMISSION_USD, price * qty * _COMMISSION_PCT)
        self._track_costs(slippage, commission)
        return {
            "status":       "filled",
            "ticker":       symbol,
            "qty":          qty,
            "fill_price":   fill_price,
            "market_price": price,
            "slippage_usd": round(slippage, 4),
            "commission":   round(commission, 4),
            "mode":         "paper",
        }

    # ── Statistik ─────────────────────────────────────────────────────────────

    def get_slippage_stats(self) -> Dict:
        """
        Gibt kumulierte Handelskosten zurück.

        Returns:
            dict mit: total_slippage_usd, total_commission_usd, total_costs_usd,
                      trade_count, avg_cost_per_trade_usd
        """
        total_costs = self._total_slippage_usd + self._total_commission_usd
        avg_cost    = total_costs / self._trade_count if self._trade_count > 0 else 0.0
        return {
            "total_slippage_usd":    round(self._total_slippage_usd, 2),
            "total_commission_usd":  round(self._total_commission_usd, 2),
            "total_costs_usd":       round(total_costs, 2),
            "trade_count":           self._trade_count,
            "avg_cost_per_trade_usd": round(avg_cost, 4),
        }
