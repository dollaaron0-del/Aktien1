"""
TradingView Signal Executor

Hintergrund-Thread der jede Minute neue TradingView-Signale prüft
und sofort ausführt – ohne auf den nächsten Analyse-Zyklus zu warten.

BUY-Signale: werden aus der SignalQueue via process_signal_queue() ausgeführt.
SELL-Signale: offene Positionen werden sofort via _do_close() geschlossen.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from collectors.tradingview_webhook import get_pending_sells
from logger import get_logger

if TYPE_CHECKING:
    from strategy.swing_strategy import SwingStrategy

log = get_logger(__name__)

_executor_thread: threading.Thread | None = None


def start_tv_executor(strategy: "SwingStrategy", interval_seconds: int = 60) -> None:
    """Startet den Sofortausführungs-Thread für TradingView-Signale."""
    global _executor_thread
    if _executor_thread and _executor_thread.is_alive():
        return

    _executor_thread = threading.Thread(
        target=_run,
        args=(strategy, interval_seconds),
        daemon=True,
        name="tv-executor",
    )
    _executor_thread.start()
    log.info("TradingView Executor gestartet (Intervall: %ds)", interval_seconds)


def _run(strategy: "SwingStrategy", interval_seconds: int) -> None:
    while True:
        try:
            _execute_cycle(strategy)
        except Exception as e:
            log.warning("TV-Executor Fehler: %s", e)
        time.sleep(interval_seconds)


def _execute_cycle(strategy: "SwingStrategy") -> None:
    broker    = strategy.broker
    portfolio = strategy.portfolio

    # ── SELL-Signale sofort ausführen ─────────────────────────────────────────
    for sig in get_pending_sells():
        ticker = sig["ticker"]
        pos    = portfolio.get_position(ticker)
        if not pos:
            continue
        price = broker.get_price(ticker) or pos.entry_price
        strategy._do_close(
            ticker, pos, price,
            f"TradingView SELL ({sig.get('strategy', 'TV')})",
        )
        log.info("TV-Executor [%s]: Position sofort geschlossen @ $%.2f", ticker, price)

    # ── BUY-Signale aus Queue sofort ausführen ────────────────────────────────
    results = strategy.process_signal_queue()
    for msg in results:
        log.info("TV-Executor: %s", msg)
