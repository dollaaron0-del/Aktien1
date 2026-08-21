"""
bot/cycle_checks.py – Pre-Analyse-Marktkontext: Regime/Hedge-Eval, Makro-Events,
Earnings-Pre-Exit.

Ausgelagert aus bot/runner.py (Roadmap 4.4a, 500-Zeilen-Regel): die drei
Zyklus-EINGANGS-Checks, die VOR dem eigentlichen Analyse-Loop laufen und das
Markt-Regime für den Rest des Zyklus festlegen. Mutiert cycle_actions in place
(Hedge-/Earnings-Pre-Exit-Aktionen fließen in dieselbe Tages-Liste wie
Trade-Aktionen aus der Hauptschleife).
"""
from __future__ import annotations

from typing import List

from rich.console import Console

from collectors import NewsAPICollector
from collectors.tradingview_webhook import get_pending_macro_events
from config import config
from logger import get_logger

log = get_logger(__name__)
console = Console()


def run_pre_analysis_checks(hedge_strategy, earnings_strategy, cycle_actions: List[str]) -> str:
    """Regime-Check+Hedge-Eval → Makro-Events-Webhook-Anzeige → Earnings-Pre-
    Exit. Mutiert cycle_actions in place. Gibt das Markt-Regime zurück
    (Default 'NEUTRAL', falls kein Hedge aktiv ist oder der Check scheitert –
    check_exits()/strategy.evaluate() in run_analysis_cycle brauchen regime)."""
    regime = "NEUTRAL"

    # ── Regime check + hedge evaluation ──────────────────────────────────────
    if hedge_strategy:
        macro_news_for_regime = []
        try:
            macro_news_for_regime = NewsAPICollector().collect_general("market recession economy", max_results=10)
        except Exception as e:
            log.warning("Hedge-Regime: Macro-News konnten nicht geladen werden – %s", e)
        try:
            regime, hedge_actions = hedge_strategy.evaluate_regime(macro_news_for_regime or None)
            regime_color = {"BULL": "green", "NEUTRAL": "yellow", "BEAR": "red", "CRISIS": "bold red"}.get(regime, "white")
            latest = hedge_strategy.regime_summary()
            score_str = f" (Score: {latest['recession_score']:.2f})" if latest else ""
            console.print(f"  Marktregime: [{regime_color}]{regime}[/{regime_color}]{score_str}")
            for action in hedge_actions:
                console.print(f"  [magenta]{action}[/magenta]")
                cycle_actions.append(action)
        except Exception as _reg_err:
            log.error("Regime-Check fehlgeschlagen – fahre ohne Hedge fort: %s", _reg_err, exc_info=True)
            console.print(f"  [dim red]⚠ Regime-Check fehlgeschlagen: {_reg_err}[/dim red]")

    # ── Makro-Events aus Webhook anzeigen ────────────────────────────────────
    if config.tradingview_webhook_enabled:
        try:
            macro_events = get_pending_macro_events(since_hours=24)
            for me in macro_events:
                surprise_color = "red" if me.get("surprise") == "ABOVE" and me.get("event") in ("CPI", "PPI") \
                                 else "green" if me.get("surprise") == "BELOW" else "yellow"
                console.print(
                    f"  [{surprise_color}]📣 Makro: {me['event']} "
                    f"(Surprise={me.get('surprise','?')}, Impact={me.get('impact','?')})[/{surprise_color}]"
                )
        except Exception:
            pass

    # Force-exit pre-earnings positions before the report
    if earnings_strategy:
        try:
            for _ea in earnings_strategy.check_pre_earnings_exits():
                console.print(f"  [bold yellow]{_ea}[/bold yellow]")
                cycle_actions.append(_ea)
        except Exception as _ee:
            log.debug("Earnings pre-exit check failed: %s", _ee)

    return regime
