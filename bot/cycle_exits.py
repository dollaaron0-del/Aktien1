"""
bot/cycle_exits.py – Exit-Prüfung: SL/TP-Check + TradingView-SELL-Signale.

Ausgelagert aus bot/runner.py (Roadmap 4.4a, 500-Zeilen-Regel): der Zyklus-
Schritt direkt nach den Pre-Analyse-Checks (bot/cycle_checks.py) und vor der
eigentlichen (Claude-)Analyse-Schleife – braucht keinen Claude-Aufruf, prüft
nur offene Positionen gegen ihre Stop-Loss/Take-Profit-Level und gegen
TradingView-SELL-Webhooks (Short/Bearish Engulfing). Mutiert cycle_actions
in place (dieselbe Tages-Liste wie die übrigen Zyklus-Schritte).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from rich.console import Console

from collectors.tradingview_webhook import get_pending_sells
from config import config
from logger import get_logger
from strategy.swing_strategy import StrategyResult

log = get_logger(__name__)
console = Console()


def run_exit_checks(portfolio, broker, strategy, executor, regime: str,
                    cycle_actions: List[str], live) -> None:
    """SL/TP-Check (strategy.check_exits) → TradingView-SELL-Signale.
    Mutiert cycle_actions in place."""
    live.set_phase("Exits prüfen")
    try:
        open_tickers = list(portfolio.all_positions().keys())
        exit_prices = broker.get_prices(open_tickers) if open_tickers else {}
        for res in strategy.check_exits(exit_prices, regime):
            pos = portfolio.get_position(res.ticker)
            days_held = 0
            if pos:
                try:
                    days_held = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
                except Exception:
                    days_held = 0
            action = executor.execute(res, days_held=days_held)
            if action:
                console.print(f"  [yellow]{action}[/yellow]")
                cycle_actions.append(action)
    except Exception as sl_err:
        log.error("SL/TP-Check fehlgeschlagen: %s", sl_err, exc_info=True)
        console.print(f"  [dim red]⚠ SL/TP-Check fehlgeschlagen: {sl_err}[/dim red]")

    # TradingView SELL-Signale verarbeiten (Short/Bearish Engulfing)
    if config.tradingview_webhook_enabled:
        tv_sells = get_pending_sells()
        for sig in tv_sells:
            tv_ticker = sig["ticker"]
            pos = portfolio.get_position(tv_ticker)
            if pos:
                price = broker.get_price(tv_ticker) or pos.entry_price
                days_held = 0
                try:
                    days_held = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
                except Exception:
                    days_held = 0
                action = executor.execute(
                    StrategyResult(
                        "SELL", tv_ticker,
                        f"TradingView SELL-Signal ({sig.get('strategy', 'TV')})",
                        shares=pos.shares, price=price,
                    ),
                    days_held=days_held,
                )
                console.print(f"  [bold red]{action}[/bold red]")
                if action:
                    cycle_actions.append(action)
            else:
                console.print(
                    f"  [dim]TradingView SELL [{tv_ticker}]: keine offene Position[/dim]"
                )
