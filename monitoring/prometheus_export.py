"""
Optionaler Prometheus-Metrics-Endpoint.
Wird nur gestartet wenn PROMETHEUS_PORT in .env gesetzt ist.
Erfordert: pip install prometheus_client

Beispiel: PROMETHEUS_PORT=8000
Danach: curl http://localhost:8000/metrics
"""
import os
from logger import get_logger

log = get_logger(__name__)

_PORT = int(os.getenv("PROMETHEUS_PORT", "0"))


def start_prometheus_server() -> bool:
    """
    Startet den Prometheus HTTP-Server falls PROMETHEUS_PORT > 0 gesetzt.
    Gibt True zurück wenn gestartet, False wenn deaktiviert/unavailable.
    """
    if _PORT <= 0:
        return False
    try:
        from prometheus_client import start_http_server, Gauge, Counter, Histogram

        # Metriken definieren
        portfolio_value_gauge = Gauge("bot_portfolio_value_usd", "Aktueller Portfolio-Gesamtwert in USD")
        open_positions_gauge  = Gauge("bot_open_positions_total", "Anzahl offener Positionen")
        trades_counter        = Counter("bot_trades_total", "Trades gesamt", ["action"])
        win_rate_gauge        = Gauge("bot_win_rate_pct", "Win-Rate in Prozent")
        api_latency_hist      = Histogram("bot_api_latency_seconds", "API-Latenz in Sekunden", ["api"])

        start_http_server(_PORT)
        log.info("Prometheus Metrics Server gestartet auf Port %d", _PORT)

        # Expose update function für main.py
        def update_gauges(portfolio_value: float, open_pos: int, win_rate: float):
            portfolio_value_gauge.set(portfolio_value)
            open_positions_gauge.set(open_pos)
            win_rate_gauge.set(win_rate)

        # Attach to module for external use
        import monitoring.prometheus_export as _self
        _self.update_gauges = update_gauges
        _self.api_latency_hist = api_latency_hist

        return True

    except ImportError:
        log.info("prometheus_client nicht installiert – Prometheus-Export deaktiviert")
        return False
    except Exception as e:
        log.warning("Prometheus-Server Start fehlgeschlagen: %s", e)
        return False


def update_gauges(portfolio_value: float = 0, open_pos: int = 0, win_rate: float = 0):
    """Stub – wird durch start_prometheus_server() ersetzt wenn aktiv."""
    pass
