"""
Lightweight Metrics Collector – kein externer Service nötig.

Sammelt:
  - Trade-Metriken (Anzahl, Win-Rate, P&L)
  - Collector-Latenz (ms pro Collector)
  - API-Aufruf-Zähler (Claude, yfinance)
  - Portfolio-Snapshots

Speichert in data/metrics.json (atomic write).
Dashboard-kompatibel: metrics.summary() gibt serialisierbares Dict zurück.

Optional: Prometheus-Export wenn prometheus_client installiert.
"""
import json
import os
import tempfile
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_METRICS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "metrics.json")
_MAX_LATENCY_HISTORY = 100   # Letzte N Latenz-Messungen pro Collector


class MetricsCollector:
    """
    Thread-sicherer Metrics-Sammler mit In-Memory-Aggregation und JSON-Persistenz.
    """
    _instance: Optional["MetricsCollector"] = None
    _class_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> "MetricsCollector":
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._data = self._load()
        self._latencies: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_MAX_LATENCY_HISTORY))

    # ── Trade Metriken ────────────────────────────────────────────────────────

    def record_trade(self, ticker: str, action: str, pnl: float = 0.0, return_pct: float = 0.0) -> None:
        """Jeden Trade aufzeichnen."""
        with self._lock:
            trades = self._data.setdefault("trades", {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0})
            trades["total"] += 1
            if pnl > 0:
                trades["wins"] += 1
            elif pnl < 0:
                trades["losses"] += 1
            trades["total_pnl"] = round(trades["total_pnl"] + pnl, 2)

            # Recent trades (letzte 50)
            recent = self._data.setdefault("recent_trades", [])
            recent.append({
                "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "ticker": ticker,
                "action": action,
                "pnl": round(pnl, 2),
                "return_pct": round(return_pct, 2),
            })
            self._data["recent_trades"] = recent[-50:]
        self._save_async()

    def record_collector_latency(self, collector: str, latency_ms: float) -> None:
        """Collector-Latenz aufzeichnen (für Performance-Monitoring)."""
        with self._lock:
            self._latencies[collector].append(latency_ms)
            coll_data = self._data.setdefault("collectors", {})
            hist = list(self._latencies[collector])
            coll_data[collector] = {
                "avg_ms":  round(sum(hist) / len(hist)),
                "max_ms":  round(max(hist)),
                "calls":   coll_data.get(collector, {}).get("calls", 0) + 1,
            }

    def record_api_call(self, api: str, success: bool = True, latency_ms: float = 0.0) -> None:
        """API-Aufruf-Statistik (claude, yfinance, alpaca, newsapi, ...)."""
        with self._lock:
            apis = self._data.setdefault("apis", {})
            entry = apis.setdefault(api, {"calls": 0, "errors": 0, "total_latency_ms": 0})
            entry["calls"] += 1
            if not success:
                entry["errors"] += 1
            entry["total_latency_ms"] = round(entry["total_latency_ms"] + latency_ms)
            if entry["calls"] > 0:
                entry["avg_latency_ms"] = round(entry["total_latency_ms"] / entry["calls"])

    def record_portfolio_snapshot(self, total_value: float, cash: float, open_positions: int) -> None:
        """Portfolio-Wert-Snapshot für Trend-Anzeige."""
        with self._lock:
            snaps = self._data.setdefault("portfolio_snapshots", [])
            snaps.append({
                "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "total": round(total_value, 2),
                "cash": round(cash, 2),
                "positions": open_positions,
            })
            self._data["portfolio_snapshots"] = snaps[-90:]  # 90 Snapshots behalten
        self._save_async()

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> Dict:
        with self._lock:
            trades = self._data.get("trades", {})
            total  = trades.get("total", 0)
            wins   = trades.get("wins", 0)
            return {
                "trades": {
                    "total":    total,
                    "wins":     wins,
                    "losses":   trades.get("losses", 0),
                    "win_rate": round(wins / total * 100, 1) if total else 0.0,
                    "total_pnl": trades.get("total_pnl", 0.0),
                },
                "collectors":          self._data.get("collectors", {}),
                "apis":                self._data.get("apis", {}),
                "recent_trades":       self._data.get("recent_trades", [])[-10:],
                "portfolio_snapshots": self._data.get("portfolio_snapshots", [])[-30:],
            }

    # ── Persistenz ────────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            with open(_METRICS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_async(self) -> None:
        """Speichert in Background-Thread um den Haupt-Thread nicht zu blockieren."""
        threading.Thread(target=self._save, daemon=True).start()

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(_METRICS_FILE), exist_ok=True)
            with self._lock:
                payload = dict(self._data)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=os.path.dirname(_METRICS_FILE),
                suffix=".tmp", delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(payload, tmp, indent=2)
                tmp_path = tmp.name
            os.replace(tmp_path, _METRICS_FILE)
        except Exception as e:
            log.warning("Metrics: Speicherfehler – %s", e)


# Modul-Level Singleton
metrics = MetricsCollector.get_instance()
