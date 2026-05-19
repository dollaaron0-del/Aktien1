"""
CorrelationEngine – leitet automatisch Folge-Signale aus ETF-Bewegungen ab.

Wenn ein führender ETF (GLD, XLE, XLF …) ein Signal sendet,
werden korrelierte Aktien/ETFs automatisch in die Signal-Queue eingereiht.

Beispiele:
  GLD BUY  → Gold-Miner (GDX, NEM, GOLD) bekommen BUY-Signale
  SPY SELL → Inverse ETF (SH) bekommt BUY-Signal
  QQQ SELL → SQQQ bekommt BUY-Signal
"""
from __future__ import annotations

from typing import Dict, List

from logger import get_logger

log = get_logger(__name__)

# Key: "TICKER" für BUY-Trigger, "TICKER_SELL" für SELL-Trigger
_CORRELATION_MAP: Dict[str, Dict] = {
    "GLD": {
        "correlated": ["GDX", "NEM", "GOLD", "AEM"],
        "action":     "BUY",
        "hold_days":  5,
        "rationale":  "GLD steigt – Gold-Miner folgen Gold mit Hebel",
        "strategy":   "GoldCorrelation",
        "score":      0.70,
        "confidence": "MEDIUM",
    },
    "SPY_SELL": {
        "correlated": ["SH"],
        "action":     "BUY",
        "hold_days":  3,
        "rationale":  "SPY fällt – S&P500 Absicherung via Inverse ETF",
        "strategy":   "SPYHedge",
        "score":      0.72,
        "confidence": "MEDIUM",
    },
    "QQQ_SELL": {
        "correlated": ["SQQQ", "PSQ"],
        "action":     "BUY",
        "hold_days":  3,
        "rationale":  "QQQ fällt – NASDAQ Absicherung via Inverse ETF",
        "strategy":   "QQQHedge",
        "score":      0.72,
        "confidence": "MEDIUM",
    },
    "XLE": {
        "correlated": ["XOM", "CVX", "COP", "SLB"],
        "action":     "BUY",
        "hold_days":  5,
        "rationale":  "Energie-Sektor (XLE) bullish – Öl-Aktien folgen",
        "strategy":   "EnergyCorrelation",
        "score":      0.68,
        "confidence": "MEDIUM",
    },
    "XLF": {
        "correlated": ["JPM", "BAC", "GS", "MS"],
        "action":     "BUY",
        "hold_days":  4,
        "rationale":  "Finanz-Sektor (XLF) bullish – Banken folgen",
        "strategy":   "FinancialCorrelation",
        "score":      0.68,
        "confidence": "MEDIUM",
    },
    "XLV": {
        "correlated": ["JNJ", "UNH", "PFE", "ABBV"],
        "action":     "BUY",
        "hold_days":  5,
        "rationale":  "Gesundheitssektor (XLV) bullish – defensive Werte folgen",
        "strategy":   "HealthcareCorrelation",
        "score":      0.67,
        "confidence": "MEDIUM",
    },
    "XLU_SELL": {
        "correlated": ["DUK", "SO", "NEE"],
        "action":     "SELL",
        "hold_days":  0,
        "rationale":  "Versorger-ETF (XLU) fällt – defensive Rotations-Signal",
        "strategy":   "UtilityRotation",
        "score":      0.65,
        "confidence": "LOW",
    },
}


def get_correlated_signals(ticker: str, action: str) -> List[dict]:
    """
    Gibt abgeleitete Signale zurück die durch diesen Trigger ausgelöst werden.
    Leere Liste wenn keine Korrelation bekannt.
    """
    key = ticker if action == "BUY" else f"{ticker}_SELL"
    mapping = _CORRELATION_MAP.get(key.upper())
    if not mapping:
        return []

    results = []
    for corr_ticker in mapping["correlated"]:
        results.append({
            "ticker":     corr_ticker,
            "action":     mapping["action"],
            "score":      mapping["score"],
            "confidence": mapping["confidence"],
            "rationale":  f"{mapping['rationale']} (Trigger: {ticker} {action})",
            "hold_days":  mapping["hold_days"],
            "strategy":   mapping["strategy"],
        })
    if results:
        log.info(
            "CorrelationEngine: %s %s → %d Folge-Signale (%s)",
            ticker, action, len(results),
            ", ".join(s["ticker"] for s in results),
        )
    return results


def list_triggers() -> List[str]:
    """Gibt alle konfigurierten Trigger-Ticker zurück (für Logging/Dashboard)."""
    return list(_CORRELATION_MAP.keys())
