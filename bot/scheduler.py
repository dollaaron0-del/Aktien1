"""
bot/scheduler.py – Main bot loop, schedule setup, and all _*_job functions.
"""

import schedule
import time
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from config import config
from logger import get_logger
from notifier.telegram_notifier import TelegramNotifier
from notifier.daily_dashboard import DailyDashboard
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from portfolio.goal_risk_assessor import GoalRiskAssessor
from analyzers.reflection_engine import ReflectionEngine
from collectors.news_archive import NewsArchive
from analyzers.market_schedule import MarketSchedule
from analyzers.weekend_prep import WeekendPrep
from analyzers.parameter_optimizer import ParameterOptimizer, _MIN_TRADES
from bot.pre_market_scanner import PreMarketScanner
from bot.runner import run_analysis_cycle, _print_portfolio_summary
from cli.commands import run_social_scan, run_weekend_prep

console = Console()
log = get_logger(__name__)


def _subtract_minutes(hhmm: str, minutes: int) -> str:
    """Zieht N Minuten von einem HH:MM String ab. Ergebnis bleibt im selben Tag."""
    from datetime import timedelta as _td
    h, m = map(int, hhmm.split(":"))
    total = h * 60 + m - minutes
    total = max(0, total)
    return f"{total // 60:02d}:{total % 60:02d}"


def _auto_optimize_check(tracker, notifier: TelegramNotifier) -> None:
    """Prüft nach je 15 neuen Trades ob Optimierung sinnvoll ist. Sendet Telegram-Hinweis."""
    try:
        report_data = tracker.get_accuracy_report()
        total = report_data.get("total_closed", 0)
        if total < _MIN_TRADES:
            return
        if total % _MIN_TRADES != 0:
            return
        optimizer = ParameterOptimizer(tracker)
        report = optimizer.analyze()
        if report.has_suggestions:
            notifier.send(report.to_telegram())
            log.info("ParameterOptimizer: %d Vorschläge nach %d Trades gesendet.", len(report.suggestions), total)
    except Exception as e:
        log.warning("Auto-Optimize-Check fehlgeschlagen: %s", e)


def _margin_tier_watch(tracker, notifier: TelegramNotifier, _state: list) -> None:
    """Prüft ob sich der Margin-Tier geändert hat und sendet Telegram-Benachrichtigung."""
    if not config.use_margin:
        return
    try:
        from analyzers.margin_readiness import MarginTierTracker
        tracker_inst = MarginTierTracker(tracker)
        tracker_inst.invalidate_cache()
        result   = tracker_inst.get_active_tier(use_cache=False)
        prev_lvl = _state[0] if _state else -1

        if prev_lvl < 0:
            _state.append(result.active_tier.level)
            return

        if result.active_tier.level != prev_lvl:
            notifier.send(result.to_telegram(prev_level=prev_lvl))
            log.info(
                "Margin-Tier geändert: %d → %d (%s)",
                prev_lvl, result.active_tier.level, result.active_tier.label,
            )
            _state[0] = result.active_tier.level
    except Exception as e:
        log.warning("Margin-Tier-Watch fehlgeschlagen: %s", e)


def _check_goal_reached(
    goal_risk: GoalRiskAssessor,
    portfolio: Portfolio,
    broker,
    tracker,
    notifier: TelegramNotifier,
    _notified: list,
) -> None:
    """Sendet einmalig eine Telegram-Nachricht wenn das Ziel erreicht ist."""
    if not goal_risk.active or _notified:
        return
    try:
        prices = broker.get_prices(list(portfolio.all_positions().keys()))
        total = portfolio.total_value(prices)
        if total < goal_risk.target_value:
            return
        assessment = goal_risk.assess(total, tracker.get_stats())
        if assessment and assessment.goal_reached:
            _notified.append(True)
            notifier.send(
                f"🎉 *ZIEL ERREICHT!*\n\n"
                f"Portfolio: ${total:,.2f}\n"
                f"Ziel: ${goal_risk.target_value:,.2f}\n\n"
                f"Du kannst jetzt ${goal_risk.target_value:,.2f} entnehmen.\n"
                f"Danach FOCUS\\_MODE in der .env zurück auf WEALTH\\_BUILDING setzen."
            )
    except Exception:
        pass


def run_bot_loop(
    args,
    portfolio: Portfolio,
    broker,
    strategy,
    tracker: PerformanceTracker,
    phase_ctrl: PhaseController,
    focus_ctrl,
    archive: NewsArchive,
    reflection: ReflectionEngine,
    signal_queue,
    pulse_db,
    weekend_prep_inst: WeekendPrep,
    goal_risk: GoalRiskAssessor,
    hedge_strategy_inst,
    mkt_schedule: MarketSchedule,
) -> None:
    """Main bot event loop – sets up schedule and runs until Ctrl+C."""

    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)
    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"

    today_slots = mkt_schedule.get_schedule_strings()
    social_label = "[green]aktiv[/green]" if config.enable_social_scan else "[dim]deaktiviert[/dim]"
    queue_label = f"{signal_queue.count_pending()} Signal(e) ausstehend"

    if today_slots:
        schedule_display = ", ".join(
            f"[cyan]{s['hhmm']}[/cyan] ({s['exchange']})" for s in today_slots
        )
    else:
        schedule_display = "[dim]Heute kein Handelstag[/dim]"

    nxt = mkt_schedule.next_window()
    next_str = nxt["analysis_local"] if nxt else "–"

    console.print(Panel(
        f"[bold]Stock Sentiment Bot gestartet[/bold]\n"
        f"Broker: [cyan]{config.broker_mode.upper()}[/cyan] | "
        f"Watchlist: [cyan]{', '.join(config.watchlist)}[/cyan]\n"
        f"Fokus: [magenta]{focus_ctrl.profile.label}[/magenta] | "
        f"Nächste Analyse: [bold]{next_str}[/bold]\n"
        f"Heute: {schedule_display} ({config.market_lead_minutes} Min vor Börseneröffnung)\n"
        f"Social-Scan: {social_label} (stündlich) | "
        f"Signal-Queue: [yellow]{queue_label}[/yellow]\n"
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}] | "
        f"Kapital: ${total:,.2f} | Ziel: ${phase_info['growth_target']:,.0f}",
        border_style="green",
    ))

    console.print(f"\n[dim]Markt-Zeitplan für heute:[/dim]")
    console.print(f"[dim]{mkt_schedule.describe()}[/dim]\n")

    def _monthly_review_check():
        if datetime.utcnow().day == 1:
            console.print("[bold magenta]📋 Erstelle monatliche Selbsteinschätzung...[/bold magenta]")
            content = reflection.generate_monthly_review()
            if content:
                console.print(Panel(content[:800] + "...", title="Monatsreview erstellt", border_style="magenta"))

    def _social_scan_job():
        spikes = run_social_scan(pulse_db, strategy)
        if spikes:
            notifier = TelegramNotifier()
            spike_lines = [
                f"{s['ticker']}: {s['spike_ratio']}× Volumen, Score {s['avg_score']:+.2f}"
                for s in spikes[:5]
            ]
            notifier.notify_daily_summary(
                total_value=portfolio.total_value(broker.get_prices(list(portfolio.all_positions().keys()))),
                cash=portfolio.cash,
                open_positions=len(portfolio.all_positions()),
                phase=phase_ctrl.current_phase(portfolio.total_value(
                    broker.get_prices(list(portfolio.all_positions().keys()))
                )),
                progress_pct=phase_ctrl.progress_pct(portfolio.total_value(
                    broker.get_prices(list(portfolio.all_positions().keys()))
                )),
                actions_today=[f"📡 Social-Spike: {l}" for l in spike_lines],
            )

    def _reschedule_analysis():
        """Rebuilds analysis schedule for the new day (handles DST changes)."""
        for job in list(schedule.jobs):
            if getattr(job, "_is_analysis_job", False):
                schedule.cancel_job(job)
        _register_analysis_jobs()

    def _pre_market_job(exchange: str):
        """Pre-Market Briefing: schneller Daten-Scan ohne Claude."""
        console.rule(f"[bold yellow]Pre-Market Briefing – {exchange}[/bold yellow]")
        try:
            from bot.runner import _get_watchlist
            watchlist = _get_watchlist(portfolio)
            scanner = PreMarketScanner()
            briefing = scanner.run(exchange=exchange, watchlist=watchlist)
            if briefing:
                for line in briefing.to_console_lines():
                    console.print(line)
                TelegramNotifier().send(briefing.to_telegram())
        except Exception as e:
            log.warning("Pre-Market-Job %s fehlgeschlagen: %s", exchange, e)

    def _register_analysis_jobs():
        slots = mkt_schedule.get_schedule_strings()
        is_weekend = datetime.utcnow().weekday() >= 5
        if not slots or is_weekend:
            if is_weekend:
                console.print("[dim]Wochenende – keine Vollanalysen geplant (nur Wochenvorbereitung).[/dim]")
            else:
                console.print("[dim]Heute kein Handelstag.[/dim]")
            return
        for slot in slots:
            # Volle Analyse 30 Min vor Open (bisherig)
            job = schedule.every().day.at(slot["hhmm"]).do(
                run_analysis_cycle,
                portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                weekend_prep_inst, hedge_strategy_inst,
            )
            job._is_analysis_job = True
            review_job = schedule.every().day.at(slot["hhmm"]).do(_monthly_review_check)
            review_job._is_analysis_job = True

            # Pre-Market Briefing 90 Min vor Open (60 Min früher als Vollanalyse)
            pre_hhmm = _subtract_minutes(slot["hhmm"], 60)
            exch = slot["exchange"]
            pre_job = schedule.every().day.at(pre_hhmm).do(_pre_market_job, exch)
            pre_job._is_analysis_job = True

        times_str = ", ".join(f"{s['hhmm']} ({s['exchange']})" for s in slots)
        console.print(f"[dim]Analyse-Jobs registriert: {times_str}[/dim]")
        pre_times = ", ".join(
            f"{_subtract_minutes(s['hhmm'], 60)} pre-market ({s['exchange']})" for s in slots
        )
        console.print(f"[dim]Pre-Market-Jobs: {pre_times}[/dim]")

    def _weekend_prep_job():
        """Runs weekend preparation. Called Saturday 09:00 and Sunday 14:00."""
        console.print(f"\n[bold cyan]📅 Wochenvorbereitung startet...[/bold cyan]")
        run_weekend_prep(weekend_prep_inst)

    def _run_regime_check():
        regime, actions = hedge_strategy_inst.evaluate_regime()
        if actions:
            for a in actions:
                console.print(f"\n  [magenta]{a}[/magenta]")
            TelegramNotifier().notify_daily_summary(
                total_value=portfolio.total_value(broker.get_prices(list(portfolio.all_positions().keys()))),
                cash=portfolio.cash,
                open_positions=len(portfolio.all_positions()),
                phase=phase_ctrl.current_phase(portfolio.total_value(
                    broker.get_prices(list(portfolio.all_positions().keys()))
                )),
                progress_pct=phase_ctrl.progress_pct(portfolio.total_value(
                    broker.get_prices(list(portfolio.all_positions().keys()))
                )),
                actions_today=actions,
            )

    # Register today's analysis jobs (weekdays only)
    _register_analysis_jobs()

    # Reschedule every day at 00:01 (picks up DST changes and weekday/weekend transitions)
    schedule.every().day.at("00:01").do(_reschedule_analysis)

    # Weekend preparation: Saturday 09:00 and Sunday 14:00 (updated briefing after Sunday news)
    schedule.every().saturday.at("09:00").do(_weekend_prep_job)
    schedule.every().sunday.at("14:00").do(_weekend_prep_job)

    # If today is already weekend, run prep now if no briefing exists for next week
    if datetime.utcnow().weekday() >= 5 and not weekend_prep_inst.get_current_briefing():
        console.print("[bold cyan]📅 Wochenende erkannt – starte Wochenvorbereitung...[/bold cyan]")
        _weekend_prep_job()

    # Hourly tasks (7 days a week)
    schedule.every().hour.do(strategy.check_open_positions)
    if config.enable_social_scan:
        schedule.every().hour.do(_social_scan_job)

    # Auto-Optimierung: nach je 15 abgeschlossenen Trades per Telegram benachrichtigen
    schedule.every(6).hours.do(
        lambda: _auto_optimize_check(tracker, TelegramNotifier())
    )

    # Margin-Tier-Watch: bei Tier-Wechsel Telegram-Benachrichtigung
    _margin_tier_state: list = []
    if config.use_margin:
        _margin_tier_watch(tracker, TelegramNotifier(), _margin_tier_state)  # Init
        schedule.every(2).hours.do(
            lambda: _margin_tier_watch(tracker, TelegramNotifier(), _margin_tier_state)
        )

    # Tägliches Dashboard (21:00 UTC, nach NYSE-Schluss)
    from analyzers.bot_scorer import BotScorer as _BotScorer
    _dashboard = DailyDashboard()

    def _daily_dashboard_job():
        if not _dashboard.should_send():
            return
        try:
            msg = _dashboard.generate(
                portfolio=portfolio,
                tracker=tracker,
                scorer=_BotScorer(),
                broker=broker,
                initial_capital=config.initial_capital,
            )
            TelegramNotifier().send(msg)
            _dashboard.mark_sent()
            log.info("Tägliches Dashboard gesendet.")
        except Exception as e:
            log.warning("Daily Dashboard fehlgeschlagen: %s", e)

    schedule.every().hour.do(_daily_dashboard_job)

    # Goal-reached check (einmalige Telegram-Nachricht wenn Ziel erreicht)
    _goal_notified: list = []
    if goal_risk.active:
        schedule.every().hour.do(
            lambda: _check_goal_reached(goal_risk, portfolio, broker, tracker, TelegramNotifier(), _goal_notified)
        )

    # Periodic regime check + hedge exit monitoring
    if hedge_strategy_inst:
        schedule.every(config.regime_check_interval_hours).hours.do(
            lambda: _run_regime_check()
        )
        schedule.every().hour.do(
            lambda: [
                console.print(f"  [magenta]{a}[/magenta]")
                for a in hedge_strategy_inst.check_hedge_exits()
            ]
        )

    console.print(f"[dim]Stop-Loss-Check stündlich. Wochenvorbereitung Sa 09:00 + So 14:00. Ctrl+C zum Beenden.[/dim]")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot gestoppt.[/yellow]")
