"""
EarningsProtector – schließt offene Positionen vor anstehenden Earnings automatisch.

Läuft alle 12 Stunden und prüft ob eine offene Position in den nächsten
PROTECT_DAYS_BEFORE Tagen Quartalsergebnisse meldet.
Wenn ja: Position sofort schließen um Earnings-Gap-Risiko zu vermeiden.

Kann in .env deaktiviert werden: EARNINGS_PROTECTION_ENABLED=false
"""
from __future__ import annotations

import threading
import time
import os
from typing import TYPE_CHECKING

from logger import get_logger

if TYPE_CHECKING:
    from strategy.swing_strategy import SwingStrategy

log = get_logger(__name__)

PROTECT_DAYS_BEFORE: int = int(os.getenv("EARNINGS_PROTECT_DAYS", "2"))
_DEFAULT_INTERVAL_H: int = 12

_thread: threading.Thread | None = None


def start_earnings_protector(strategy: "SwingStrategy",
                              check_interval_hours: int = _DEFAULT_INTERVAL_H) -> None:
    """Startet den Earnings-Schutz-Hintergrund-Thread."""
    global _thread
    if _thread and _thread.is_alive():
        return
    enabled = os.getenv("EARNINGS_PROTECTION_ENABLED", "true").lower() in ("1", "true", "yes")
    if not enabled:
        log.info("Earnings-Protector deaktiviert (EARNINGS_PROTECTION_ENABLED=false)")
        return
    _thread = threading.Thread(
        target=_run,
        args=(strategy, check_interval_hours * 3600),
        daemon=True,
        name="earnings-protector",
    )
    _thread.start()
    log.info("Earnings-Protector gestartet (prüft alle %dh, schützt %dd vor Earnings)",
             check_interval_hours, PROTECT_DAYS_BEFORE)


def _run(strategy: "SwingStrategy", interval_seconds: int) -> None:
    # Erster Check nach kurzer Wartezeit damit der Bot vollständig gestartet ist
    time.sleep(60)
    while True:
        try:
            _check_and_protect(strategy)
        except Exception as e:
            log.warning("Earnings-Protector Fehler: %s", e)
        time.sleep(interval_seconds)


def _check_and_protect(strategy: "SwingStrategy") -> None:
    from analyzers.earnings_filter import EarningsFilter

    portfolio = strategy.portfolio
    broker    = strategy.broker
    ef        = EarningsFilter(block_days=PROTECT_DAYS_BEFORE, warn_days=PROTECT_DAYS_BEFORE + 1)

    positions = list(portfolio.all_positions().items())
    if not positions:
        return

    log.info("Earnings-Protector: prüfe %d offene Positionen...", len(positions))
    closed = 0

    from strategy.executor import TradeExecutor
    from strategy.swing_strategy import StrategyResult
    _executor = TradeExecutor(portfolio, broker, getattr(strategy, "journal", None), strategy=strategy)

    for ticker, pos in positions:
        try:
            result = ef.check(ticker)
            if not result["block"]:
                continue
            days_until = result.get("days_until", "?")
            earnings_date = result.get("date", "unbekannt")
            price = broker.get_price(ticker) or pos.entry_price
            _executor.execute(StrategyResult(
                "SELL", ticker,
                f"Earnings-Schutz: {ticker} meldet in {days_until}d ({earnings_date})",
                shares=pos.shares, price=price,
            ))
            log.info(
                "Earnings-Protector [%s]: Position geschlossen – Earnings in %s Tagen (%s)",
                ticker, days_until, earnings_date,
            )
            closed += 1
        except Exception as e:
            log.warning("Earnings-Protector [%s] Fehler: %s", ticker, e)

    if closed:
        log.info("Earnings-Protector: %d Position(en) vor Earnings geschlossen.", closed)
    else:
        log.info("Earnings-Protector: Keine kritischen Earnings in den nächsten %dd.", PROTECT_DAYS_BEFORE)
