"""
bot/cycle_close.py – Zyklus-Abschluss: Portfolio-Übersicht + Housekeeping.

Ausgelagert aus bot/runner.py (Roadmap 4.4a, 500-Zeilen-Regel): der klar
abgegrenzte "Ende des Analyse-Zyklus"-Block – Konsolen-Summary, Snapshot,
Archiv-Cleanup, Telegram-Digest/Quellen-Health, Live-Status auf Idle. Reine
Housekeeping-Seiteneffekte, keine Trade-/Entscheidungslogik.

record_daily_actions bleibt bewusst in bot/runner.py (dort auch
_DAILY_ACTIONS_PATH, von scheduler.py und Tests direkt importiert/gepatcht) –
wird hier nur als Callback durchgereicht, um bestehende Monkeypatches
(runner_mod._DAILY_ACTIONS_PATH) nicht zu brechen.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from logger import get_logger
from notifier.telegram_notifier import TelegramNotifier
from portfolio import Portfolio
from portfolio.phase_controller import PhaseController

log = get_logger(__name__)
console = Console()


def progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def print_portfolio_summary(portfolio: Portfolio, broker, phase_ctrl: PhaseController) -> None:
    positions = portfolio.all_positions()
    prices = broker.get_prices(list(positions.keys())) if positions else {}
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)

    table = Table(title="Portfolio-Übersicht", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Stück", justify="right")
    table.add_column("Einstieg", justify="right")
    table.add_column("Aktuell", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("Tage", justify="right")

    for ticker, pos in positions.items():
        price = prices.get(ticker, pos.entry_price)
        pnl = (price - pos.entry_price) * pos.shares
        days = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
        pnl_str = f"[green]+${pnl:.2f}[/green]" if pnl >= 0 else f"[red]-${abs(pnl):.2f}[/red]"
        table.add_row(
            ticker, f"{pos.shares:.2f}", f"${pos.entry_price:.2f}",
            f"${price:.2f}", pnl_str, f"${pos.stop_loss:.2f}", f"${pos.take_profit:.2f}", str(days),
        )

    console.print()
    console.print(table)

    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"
    pbar = progress_bar(phase_info["progress_pct"])
    summary_lines = [
        f"Cash: [bold]${portfolio.cash:,.2f}[/bold]  |  Gesamtwert: [bold]${total:,.2f}[/bold]",
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}]  |  "
        f"Ziel: ${phase_info['growth_target']:,.0f}  |  "
        f"Fortschritt: {pbar} {phase_info['progress_pct']:.1f}%",
    ]
    if phase_info["phase"] == "DISTRIBUTION":
        summary_lines.append(
            f"[bold magenta]Monatliche Ausschüttung: ${phase_info.get('monthly_distribution', 0):,.2f} "
            f"(Ziel: ${phase_info['monthly_target']:,.2f})[/bold magenta]"
        )
    else:
        summary_lines.append(f"Noch ${phase_info['remaining_to_goal']:,.2f} bis zur Ausschüttungsphase")

    console.print(Panel("\n".join(summary_lines), title="Kapital & Phase", border_style=phase_color))

    # ── Portfolio Risk Panel ───────────────────────────────────────────────
    if positions:
        try:
            from portfolio.risk_monitor import PortfolioRiskMonitor
            _pos_values = {t: p.shares * (prices.get(t) or p.entry_price)
                           for t, p in positions.items()}
            risk = PortfolioRiskMonitor().compute(_pos_values, total)
            if risk:
                _risk_colors = {"LOW": "green", "MEDIUM": "yellow",
                                "HIGH": "red", "CRITICAL": "bold red"}
                rc = _risk_colors.get(risk.risk_label, "white")
                risk_lines = [
                    f"Risiko-Score: [{rc}]{risk.risk_score:.0f}/100 – {risk.risk_label}[/{rc}]"
                    f"  |  VaR(1T,95%): [yellow]${risk.var_1d_dollar:,.0f}"
                    f" ({risk.var_1d_pct*100:.1f}%)[/yellow]",
                    f"Beta: [cyan]{risk.portfolio_beta:.2f}[/cyan]"
                    f"  |  Konzentration (HHI): [cyan]{risk.concentration:.2f}[/cyan]"
                    f"  |  Ø Korrelation: [cyan]{risk.avg_correlation:.2f}[/cyan]",
                ]
                for pos_r in sorted(risk.positions, key=lambda x: -x.weight):
                    risk_lines.append(
                        f"  [dim]{pos_r.ticker:6}[/dim]"
                        f" Gewicht={pos_r.weight*100:.0f}%"
                        f"  Vola={pos_r.vol_daily*100:.1f}%/T"
                        f"  Beta={pos_r.beta:.2f}"
                        f"  VaR=${pos_r.var_1d:,.0f}"
                    )
                for w in risk.warnings:
                    risk_lines.append(f"  [bold yellow]⚠ {w}[/bold yellow]")
                border = _risk_colors.get(risk.risk_label, "white").replace("bold ", "")
                console.print(Panel("\n".join(risk_lines), title="Portfolio-Risiko", border_style=border))
        except Exception as _re:
            log.debug("Risk panel error: %s", _re)


def finalize_cycle(
    portfolio: Portfolio, broker, tracker, phase_ctrl: PhaseController, archive,
    cycle_actions: List[str], headline_results: List[str], wl_total: int, live,
    record_daily_actions: Callable[[List[str]], None],
) -> None:
    """Housekeeping am Zyklusende: Headline-Digest, Snapshot, Archiv-Cleanup,
    Konsolen-Summary, Telegram-Tagessummary/Quellen-Health, Tages-Aktionen
    (via übergebenem Callback – lebt weiter in bot/runner.py), Live-Idle.
    Reihenfolge 1:1 aus run_analysis_cycle übernommen (Roadmap 4.4a)."""
    # Headline-Analyse: alle Ergebnisse gebündelt in EINER Nachricht (statt einer
    # Einzelnachricht pro Aktie – das war die Telegram-Flut).
    if headline_results:
        try:
            TelegramNotifier().send(
                f"⚡ <b>Headline-Analyse</b> ({len(headline_results)} Titel)\n"
                f"━━━━━━━━━━━━━━\n"
                + "\n".join(headline_results)
            )
        except Exception as _dg_err:
            log.debug("Headline-Digest fehlgeschlagen: %s", _dg_err)

    # Record portfolio snapshot
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total_value = portfolio.total_value(prices)
    phase = phase_ctrl.current_phase(total_value)
    # record_snapshot erwartet (total_value, cash, n_positions); daily_pnl wird
    # intern aus dem letzten Snapshot berechnet. Früher wurden hier fälschlich
    # positions_value (statt Anzahl) und phase (statt pnl) übergeben.
    tracker.record_snapshot(total_value, portfolio.cash, len(portfolio.all_positions()))

    # Clean up news older than 32 days
    archive.cleanup_old(keep_days=32)

    print_portfolio_summary(portfolio, broker, phase_ctrl)

    # Send Telegram daily summary
    notifier = TelegramNotifier()

    # Live-Quellen-Health auswerten: plötzlich tote/fehlerhafte Quellen sofort
    # (gedrosselt) melden, statt erst im 7-Tage-Report.
    try:
        from analyzers.source_monitor import get_monitor
        get_monitor().finalize_cycle(notifier)
    except Exception as _sh_err:
        log.debug("Quellen-Health-Auswertung übersprungen: %s", _sh_err)

    # Tages-Zusammenfassung NICHT pro Zyklus senden – das war die Telegram-Flut
    # (1× je Markt-Slot/Trigger/Watchdog → mehrere "Tages-Zusammenfassungen" am
    # Tag). Aktionen werden gesammelt; die Abend-Summary (_daily_summary_job im
    # Scheduler) sendet sie einmal täglich gebündelt. Einzeltrades werden ohnehin
    # bereits sofort per notify_buy/notify_sell gemeldet.
    record_daily_actions(cycle_actions)

    # Live-Sichtbarkeit: Zyklus fertig → Status auf Idle, Abschluss-Event.
    n_trades = sum(1 for a in cycle_actions if "GEKAUFT" in a or "VERKAUFT" in a)
    live.feed_emit("cycle_end", detail=f"{wl_total} Titel analysiert · {n_trades} Trade(s)")
    live.set_idle(note="Zyklus beendet")
