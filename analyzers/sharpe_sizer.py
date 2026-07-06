"""
Sharpe-basierter Positions-Sizer.

Berechnet pro Ticker den Sharpe Ratio aus abgeschlossenen Trades im
TradeJournal und gibt einen Größen-Multiplikator zurück:

  Sharpe > 1.5   → 1.2×  (sehr gute Risk-Adj. Performance)
  0.5 – 1.5      → 1.0×  (normal)
  0.0 – 0.5      → 0.8×  (schwache Performance)
  < 0             → 0.5×  (Verlust-Ticker)
  < 5 Trades      → 1.0×  (zu wenig Daten)

Statistiken werden in data/sharpe_stats.json gecacht (TTL: 24 Stunden).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from logger import get_logger

log = get_logger(__name__)

_STATS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "sharpe_stats.json")
_CACHE_TTL_HOURS = 24
_MIN_TRADES = 5

# Sharpe-Schwellen → Multiplikatoren
_SHARPE_BRACKETS = [
    (1.5,  1.2),   # Sharpe >= 1.5 → 1.2×
    (0.5,  1.0),   # Sharpe >= 0.5 → 1.0×
    (0.0,  0.8),   # Sharpe >= 0.0 → 0.8×
]
_SHARPE_NEG_MULT = 0.5   # Sharpe < 0 → 0.5×
_SHARPE_DEFAULT  = 1.0   # Fallback (zu wenig Trades)


class SharpeSizer:
    """Liefert Positionsgröße-Multiplikator basierend auf historischer Performance."""

    def __init__(self) -> None:
        self._stats: Dict[str, Dict] = {}
        self._loaded_at: Optional[datetime] = None
        self._ensure_loaded()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_size_modifier(self, ticker: str) -> float:
        """
        Gibt den Positionsgröße-Multiplikator für den Ticker zurück.
        Gibt 1.0 zurück wenn weniger als MIN_TRADES vorhanden.
        """
        self._ensure_loaded()
        entry = self._stats.get(ticker.upper())

        if entry is None or entry.get("n_trades", 0) < _MIN_TRADES:
            log.debug(
                "[%s] Sharpe-Sizer: zu wenig Trades (%d), Multiplikator=1.0",
                ticker, entry.get("n_trades", 0) if entry else 0,
            )
            return _SHARPE_DEFAULT

        sharpe = entry.get("sharpe_ratio", 0.0)
        mult = self._sharpe_to_mult(sharpe)
        log.info(
            "[%s] Sharpe=%.3f (win_rate=%.0f%%, n=%d) → Multiplikator=%.2f",
            ticker, sharpe, entry.get("win_rate", 0) * 100, entry["n_trades"], mult,
        )
        return mult

    def get_stats(self, ticker: str) -> Optional[Dict]:
        """Gibt die vollständigen Statistiken für einen Ticker zurück."""
        self._ensure_loaded()
        return self._stats.get(ticker.upper())

    def refresh(self) -> None:
        """Erzwingt Neuberechnung aller Statistiken aus dem TradeJournal."""
        self._stats = self._compute_all_stats()
        self._loaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._save_cache(self._stats)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Lädt Cache falls nötig; berechnet neu wenn abgelaufen."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if self._loaded_at is not None:
            if now - self._loaded_at < timedelta(hours=_CACHE_TTL_HOURS):
                return

        cached = self._load_cache()
        if cached is not None:
            self._stats = cached
            self._loaded_at = now
            return

        # Cache fehlt oder abgelaufen → neu berechnen
        log.info("Sharpe-Stats werden neu berechnet...")
        self._stats = self._compute_all_stats()
        self._loaded_at = now
        self._save_cache(self._stats)

    def _compute_all_stats(self) -> Dict[str, Dict]:
        """Liest TradeJournal und berechnet Statistiken pro Ticker."""
        try:
            from portfolio.trade_journal import TradeJournal  # lazy import
            journal = TradeJournal()
            summaries = journal.get_all_trade_summaries(limit=500)
        except Exception as exc:
            log.warning("TradeJournal nicht lesbar: %s", exc)
            return {}

        # Sammle pnl_pct pro Ticker (nur abgeschlossene Trades)
        ticker_returns: Dict[str, list] = {}
        for s in summaries:
            if s.get("is_open"):
                continue
            ticker = s.get("ticker", "").upper()
            pnl_pct = s.get("pnl_pct")
            if ticker and pnl_pct is not None:
                ticker_returns.setdefault(ticker, []).append(float(pnl_pct))

        stats: Dict[str, Dict] = {}
        for ticker, returns in ticker_returns.items():
            stats[ticker] = self._calc_ticker_stats(ticker, returns)

        log.info("Sharpe-Stats berechnet für %d Ticker.", len(stats))
        return stats

    def _calc_ticker_stats(self, ticker: str, returns: list) -> Dict:
        n = len(returns)
        if n == 0:
            return {"n_trades": 0, "avg_return": 0.0, "std_return": 0.0,
                    "sharpe_ratio": 0.0, "win_rate": 0.0}

        avg = sum(returns) / n
        wins = sum(1 for r in returns if r > 0)
        win_rate = wins / n

        if n >= 2:
            variance = sum((r - avg) ** 2 for r in returns) / (n - 1)
            std = math.sqrt(variance) if variance > 0 else 0.0
        else:
            std = 0.0

        # Annualisierter Sharpe (Swing-Trades: ~250 Handelstage / 14 Tage Ø)
        # Vereinfacht: Sharpe = avg / std (nicht-annualisiert, da Haltedauer variabel)
        sharpe = avg / std if std > 0 else (avg * 10 if avg > 0 else avg * 10)

        return {
            "ticker": ticker,
            "n_trades": n,
            "avg_return": round(avg, 4),
            "std_return": round(std, 4),
            "sharpe_ratio": round(sharpe, 4),
            "win_rate": round(win_rate, 4),
        }

    @staticmethod
    def _sharpe_to_mult(sharpe: float) -> float:
        if sharpe < 0:
            return _SHARPE_NEG_MULT
        for threshold, mult in _SHARPE_BRACKETS:
            if sharpe >= threshold:
                return mult
        return _SHARPE_NEG_MULT  # sollte nicht erreicht werden

    def _load_cache(self) -> Optional[Dict[str, Dict]]:
        """Gibt gecachte Stats zurück, falls TTL noch nicht abgelaufen."""
        try:
            with open(_STATS_FILE) as f:
                data = json.load(f)
            ts = datetime.fromisoformat(data["_meta"]["timestamp"])
            if datetime.now(timezone.utc).replace(tzinfo=None) - ts < timedelta(hours=_CACHE_TTL_HOURS):
                stats = {k: v for k, v in data.items() if k != "_meta"}
                log.debug("Sharpe-Stats aus Cache geladen (%d Ticker).", len(stats))
                return stats
        except Exception:
            pass
        return None

    def _save_cache(self, stats: Dict[str, Dict]) -> None:
        """Atomic write der Sharpe-Statistiken."""
        os.makedirs(os.path.dirname(_STATS_FILE), exist_ok=True)
        payload = dict(stats)
        payload["_meta"] = {"timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "n_tickers": len(stats)}
        tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(_STATS_FILE), suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, _STATS_FILE)
            log.debug("Sharpe-Stats gecacht: %s", _STATS_FILE)
        except Exception as exc:
            log.warning("Sharpe-Stats konnten nicht gespeichert werden: %s", exc)
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
