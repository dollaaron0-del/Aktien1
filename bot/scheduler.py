"""
bot/scheduler.py – Main bot loop, schedule setup, and all _*_job functions.
"""

import os
import schedule
import time
from datetime import datetime, timezone
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
from portfolio.circuit_breaker import CircuitBreaker
from analyzers.reflection_engine import ReflectionEngine
from analyzers.regime_adaptive import invalidate_cache_if_crash, get_last_cached_regime
from collectors.news_archive import NewsArchive
from analyzers.market_schedule import MarketSchedule
from analyzers.weekend_prep import WeekendPrep
from analyzers.parameter_optimizer import ParameterOptimizer, _MIN_TRADES
from bot.pre_market_scanner import PreMarketScanner
from bot.runner import run_analysis_cycle, safe_run_analysis_cycle, _print_portfolio_summary
from bot import scheduler_maintenance
from bot import scheduler_risk
from bot import scheduler_macro
from bot import scheduler_scanners
from bot import scheduler_scanners2
from bot import scheduler_analysis
from cli.commands import run_weekend_prep

console = Console()
log = get_logger(__name__)


def _subtract_minutes(hhmm: str, minutes: int) -> str:
    """Zieht N Minuten von einem HH:MM String ab. Wrap-around über Mitternacht wird verhindert (auf 00:00 begrenzt)."""
    h, m = map(int, hhmm.split(":"))
    total = max(0, h * 60 + m - minutes)
    return f"{total // 60:02d}:{total % 60:02d}"


def _scanner_notify(notifier, msg: str) -> None:
    """Routine-Scanner-Meldung. Im Quiet-Mode (config.quiet_mode, Default an) wird
    sie NICHT per Telegram gesendet, sondern nur geloggt – das dämpft den
    Dauer-Lärm der Hintergrund-Scanner. Essentielle Meldungen (geplante Analysen,
    Abend-Digest, Trades/SL, Fehler) laufen weiter direkt über notifier.send()."""
    if config.quiet_mode:
        log.info("[quiet] Scanner-Meldung unterdrückt: %s",
                 " ".join(msg.split())[:160])
        return
    try:
        notifier.send(msg)
    except Exception:
        pass


def _regime_config_pct(regime: str) -> str:
    m = {"BULL": "100%", "NEUTRAL": "80%", "BEAR": "50%", "CRISIS": "25%"}
    return m.get(regime, "?")

def _regime_sl(regime: str) -> str:
    m = {"BULL": "6%", "NEUTRAL": "7%", "BEAR": "5%", "CRISIS": "4%"}
    return m.get(regime, "?")

def _regime_tp(regime: str) -> str:
    m = {"BULL": "22%", "NEUTRAL": "18%", "BEAR": "12%", "CRISIS": "8%"}
    return m.get(regime, "?")

def _regime_buy_adj(regime: str) -> str:
    m = {"BULL": "–3% (lockerer)", "NEUTRAL": "normal", "BEAR": "+5% (strenger)", "CRISIS": "+10% (sehr streng)"}
    return m.get(regime, "?")


def _run_post_cb_reflection(
    circuit_breaker: CircuitBreaker,
    portfolio: Portfolio,
    broker,
    tracker: PerformanceTracker,
    reflection: ReflectionEngine,
    notifier: TelegramNotifier,
) -> None:
    """
    Wird einmalig ausgelöst wenn der Circuit Breaker den Handelstag sperrt.
    Analysiert konkret warum die Strategie heute versagt hat und sendet es per Telegram.
    """
    try:
        import datetime as _dt
        prices = broker.get_prices(list(portfolio.all_positions().keys()))
        current_value = portfolio.total_value(prices)
        cb_status = circuit_breaker.status(current_value)
        cb_status["current_value"] = current_value

        daily_loss = cb_status.get("daily_pct", 0.0)
        drawdown   = cb_status.get("drawdown_pct", 0.0)

        # Alle heutigen Trades aus dem Tracker holen
        all_recent = tracker.get_recent_trades(n=20)
        today_str  = _dt.date.today().isoformat()

        # Verlust-Trades von heute (nach sell_date filtern, Fallback: alle letzten)
        today_losers = [
            t for t in all_recent
            if (t.get("actual_return_pct") or 0) < 0
            and (t.get("sell_date") or "").startswith(today_str)
        ]
        # Wenn keine Trades explizit von heute, nimm die letzten Verlusttrades
        if not today_losers:
            today_losers = [t for t in all_recent if (t.get("actual_return_pct") or 0) < 0][:8]

        # Entry-Rationale aus dem Journal nachladen
        for t in today_losers:
            try:
                stories = reflection.journal.get_trade_story(t.get("ticker", ""), limit_trades=1)
                if stories:
                    t["entry_rationale"] = stories[0].get("entry_rationale", "")
                    t["catalysts"]       = stories[0].get("catalysts", [])
            except Exception:
                pass

        # Exit-Kategorie-Zusammenfassung für heute
        cat_counts: dict = {}
        for t in today_losers:
            cat = t.get("sell_reason_category") or "unbekannt"
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        # Blöcke für die Telegram-Nachricht
        trade_lines = []
        for t in today_losers:
            ret = t.get("actual_return_pct") or 0.0
            cat = t.get("sell_reason_category") or "?"
            reason = (t.get("sell_reason") or "?")[:55]
            rationale = (t.get("entry_rationale") or "–")[:90]
            trade_lines.append(
                f"  <b>{t.get('ticker','?')}</b>: {ret:+.1f}% [{cat}]\n"
                f"  Ausstieg: {reason}\n"
                f"  Kauf-Begründung: {rationale}"
            )
        trades_block = "\n\n".join(trade_lines) if trade_lines else "  (keine Trades heute)"

        cat_summary = " | ".join(f"{k}: {v}×" for k, v in cat_counts.items()) or "–"

        # Gesamt-Fehler-Statistik
        exit_stats = tracker.get_exit_reason_stats()
        worst_cat  = min(exit_stats, key=lambda e: e["avg_return_pct"], default=None)
        worst_line = (
            f"{worst_cat['category']} (Ø {worst_cat['avg_return_pct']:+.2f}%, "
            f"Win-Rate {worst_cat['win_rate_pct']}%)"
            if worst_cat else "–"
        )

        # Claude-Fehleranalyse
        ai_analysis = reflection.generate_post_cb_analysis(today_losers, cb_status)
        ai_section  = f"\n\n🤖 <b>KI-Fehleranalyse:</b>\n{ai_analysis}" if ai_analysis else ""

        trigger  = "Tagesverlust" if abs(daily_loss) >= 5.0 else "Drawdown"
        loss_val = daily_loss if trigger == "Tagesverlust" else drawdown

        msg = (
            f"⛔ <b>CIRCUIT BREAKER – Handel für heute gesperrt</b>\n\n"
            f"Auslöser: {trigger} <b>{loss_val:+.1f}%</b>\n"
            f"Portfolio: ${current_value:,.2f} | Drawdown ATH: {drawdown:.1f}%\n\n"
            f"<b>Exit-Kategorien heute:</b> {cat_summary}\n"
            f"<b>Historisch schlechteste Kategorie:</b> {worst_line}\n\n"
            f"<b>Heutige Verlust-Trades:</b>\n\n{trades_block}"
            f"{ai_section}\n\n"
            f"<i>Morgen: Limit zurückgesetzt. Regime wird neu berechnet.</i>"
        )
        notifier.send(msg)
        log.warning("Circuit-Breaker-Reflection gesendet. Tagesverlust: %.1f%%", daily_loss)
    except Exception as e:
        log.warning("Post-CB-Reflection fehlgeschlagen: %s", e)


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
        assessment = goal_risk.assess(total, tracker.get_accuracy_report())
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
    weekend_prep_inst: WeekendPrep,
    goal_risk: GoalRiskAssessor,
    hedge_strategy_inst,
    mkt_schedule: MarketSchedule,
    earnings_strategy=None,
) -> None:
    """Main bot event loop – sets up schedule and runs until Ctrl+C."""

    # Führt StrategyResult-Entscheidungen der reinen SwingStrategy aus
    # (SL/TP-Job, Conditional Entries). Siehe strategy/executor.py.
    from strategy.executor import TradeExecutor
    from strategy.swing_strategy import StrategyResult
    _executor = TradeExecutor(portfolio, broker, getattr(strategy, "journal", None), strategy=strategy)

    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)
    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"

    today_slots = mkt_schedule.get_schedule_strings()
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
        f"Signal-Queue: [yellow]{queue_label}[/yellow]\n"
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}] | "
        f"Kapital: ${total:,.2f} | Ziel: ${phase_info['growth_target']:,.0f}",
        border_style="green",
    ))

    console.print(f"\n[dim]Markt-Zeitplan für heute:[/dim]")
    console.print(f"[dim]{mkt_schedule.describe()}[/dim]\n")

    # Startup-Benachrichtigung
    try:
        _wday = datetime.now(timezone.utc).replace(tzinfo=None).weekday()
        _is_weekend = _wday >= 5
        _next_info = f"Nächste Analyse: {next_str}" if next_str != "–" else "Wochenende – keine Analyse heute"
        TelegramNotifier().send(
            f"🟢 <b>Bot gestartet</b>\n\n"
            f"💼 ${total:,.2f} · {len(portfolio.all_positions())} Positionen\n"
            f"📋 Watchlist: {', '.join(config.watchlist[:6])}{'…' if len(config.watchlist) > 6 else ''}\n"
            f"⏰ {_next_info}"
        )
    except Exception as _sn_err:
        log.debug("Startup-Notification fehlgeschlagen: %s", _sn_err)

    def _monthly_review_check():
        """Monats-Review (ausgelagert nach bot/scheduler_analysis.py,
        Roadmap 4.4a)."""
        scheduler_analysis.monthly_review_check(reflection)

    def _pre_market_job(exchange: str):
        """Pre-Market Briefing (ausgelagert nach bot/scheduler_analysis.py,
        Roadmap 4.4a)."""
        scheduler_analysis.pre_market_job(exchange, portfolio, TelegramNotifier, PreMarketScanner)

    def _register_analysis_jobs():
        """Analyse-Job-Registrierung (ausgelagert nach
        bot/scheduler_analysis.py, Roadmap 4.4a)."""
        scheduler_analysis.register_analysis_jobs(
            mkt_schedule, portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy, safe_run_analysis_cycle,
            _pre_market_job, _monthly_review_check)

    def _reschedule_analysis():
        """Täglicher Reschedule für DST (ausgelagert nach
        bot/scheduler_analysis.py, Roadmap 4.4a)."""
        scheduler_analysis.reschedule_analysis(_register_analysis_jobs)

    def _weekend_prep_job():
        """Wochenvorbereitung (ausgelagert nach bot/scheduler_macro.py,
        Roadmap 4.4a)."""
        scheduler_macro.weekend_prep_job(weekend_prep_inst, TelegramNotifier, run_weekend_prep)

    _last_regime: list = [get_last_cached_regime()]   # [0] = vorheriges Regime

    def _run_regime_check():
        prev_regime = _last_regime[0]

        # 1. Flash-Crash: Cache invalidieren bevor evaluate_regime() läuft
        crashed, spy_change = invalidate_cache_if_crash(threshold_pct=3.0)
        if crashed:
            console.print(
                f"\n  [bold red]⚡ FLASH-CRASH: SPY {spy_change:.1f}% – Regime-Neuberechnung erzwungen.[/bold red]"
            )

        # 2. Regime evaluieren (lädt frisch wenn Cache leer)
        regime, actions = hedge_strategy_inst.evaluate_regime()
        _last_regime[0] = regime

        notifier = TelegramNotifier()

        # 3. Regime-Wechsel-Benachrichtigung
        if prev_regime is not None and regime != prev_regime:
            latest = hedge_strategy_inst.regime_summary() or {}
            score  = latest.get("recession_score", "?")
            comps  = latest.get("components", {})
            vix    = latest.get("vix", "?")

            _REGIME_EMOJI = {"BULL": "🟢", "NEUTRAL": "🟡", "BEAR": "🟠", "CRISIS": "🔴"}
            emoji = _REGIME_EMOJI.get(regime, "⚪")

            comp_lines = []
            for name, data in comps.items():
                label = data.get("label") or name
                val   = data.get("value")
                sc    = data.get("score")
                if val is not None and sc is not None:
                    comp_lines.append(f"  • {label}: {val} (Score {sc:.2f})")
                elif sc is not None:
                    comp_lines.append(f"  • {label}: Score {sc:.2f}")
            comp_text = "\n".join(comp_lines) if comp_lines else "  (keine Daten)"

            is_ranging   = latest.get("market_is_ranging", False)
            sideways_info = latest.get("sideways_info", {})
            sideways_note = ""
            if is_ranging:
                net_m = sideways_info.get("net_move_pct", "?")
                rng   = sideways_info.get("total_range_pct", "?")
                ratio = sideways_info.get("range_ratio", "?")
                sideways_note = (
                    f"\n\n⚠️ <b>SEITWÄRTSMARKT erkannt</b>\n"
                    f"  20-Tage-Bewegung: {net_m}% bei Spanne {rng}% (Ratio {ratio})\n"
                    f"  → Positionsgröße zusätzlich –20%, Kaufhürde +5%"
                )

            crash_note = f"\n⚡ Auslöser: SPY {spy_change:.1f}% intraday" if crashed else ""
            msg = (
                f"{emoji} <b>REGIME GEWECHSELT: {prev_regime} → {regime}</b>{crash_note}\n\n"
                f"Rezessions-Score: <b>{score}</b> (0=sicher, 1=Krise)\n"
                f"VIX: {vix}\n\n"
                f"<b>Komponenten:</b>\n{comp_text}"
                f"{sideways_note}\n\n"
                f"<b>Neue Parameter:</b>\n"
                f"  • Positionsgröße: {_regime_config_pct(regime)}"
                + (" (–20% Seitwärts-Malus)" if is_ranging else "") +
                f"\n  • Stop-Loss: {_regime_sl(regime)}\n"
                f"  • Take-Profit: {_regime_tp(regime)}"
                + (" (–15% Seitwärts-Malus)" if is_ranging else "") +
                f"\n  • Kaufhürde: {_regime_buy_adj(regime)}"
                + (" (+5% Seitwärts-Malus)" if is_ranging else "")
            )
            notifier.send(msg)
            console.print(f"\n  [bold magenta]{emoji} Regime-Wechsel: {prev_regime} → {regime}[/bold magenta]")
            log.info("Regime-Wechsel: %s → %s (Score: %s)", prev_regime, regime, score)

        # 4. Hedge-Aktionen senden – als eigene, knappe Nachricht (früher als
        #    volle "Tages-Zusammenfassung" verschickt → trug zur Summary-Flut bei).
        #    Aktionen fließen zusätzlich in die abendliche Tages-Summary ein.
        if actions:
            for a in actions:
                console.print(f"\n  [magenta]{a}[/magenta]")
            try:
                from bot.runner import record_daily_actions
                record_daily_actions(list(actions))
            except Exception as _rda_err:
                log.debug("record_daily_actions (Hedge) fehlgeschlagen: %s", _rda_err)
            notifier.send(
                "🛡️ <b>Hedge-Aktionen</b>\n"
                + "\n".join(f"  • {a}" for a in actions)
            )

    def _daily_maintenance_job():
        """Läuft täglich um 02:00 UTC (ausgelagert nach
        bot/scheduler_maintenance.py, Roadmap 4.4a) — Name bleibt hier, weil
        schedule den Funktionsnamen für die Job-Introspektion braucht."""
        scheduler_maintenance.daily_maintenance_job(
            archive, reflection, signal_queue, TelegramNotifier)

    # Register today's analysis jobs (weekdays only)
    _register_analysis_jobs()

    # ── Catch-up: verpasstes Analyse-Fenster nachholen ─────────────────────
    # Wenn der Bot nach dem geplanten Zeitfenster startet (z.B. nach Neustart),
    # wird die Analyse sofort nachgeholt – bis zu 180 Minuten nach dem Fenster
    # (_CATCHUP_MAX_MINUTES, jetzt in bot/scheduler_analysis.py).
    def _catchup_missed_window():
        """Catch-up bei verpasstem Analyse-Fenster (ausgelagert nach
        bot/scheduler_analysis.py, Roadmap 4.4a)."""
        scheduler_analysis.catchup_missed_window(
            mkt_schedule, portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy, TelegramNotifier,
            _pre_market_job, safe_run_analysis_cycle)

    _catchup_missed_window()

    # Reschedule every day at 00:01 (picks up DST changes and weekday/weekend transitions)
    schedule.every().day.at("00:01").do(_reschedule_analysis)

    # Weekend preparation: Saturday 09:00 and Sunday 14:00 (updated briefing after Sunday news)
    schedule.every().saturday.at("09:00").do(_weekend_prep_job)
    schedule.every().sunday.at("14:00").do(_weekend_prep_job)

    # If today is already weekend, run prep now if no briefing exists for next week
    if datetime.now(timezone.utc).replace(tzinfo=None).weekday() >= 5 and not weekend_prep_inst.get_current_briefing():
        console.print("[bold cyan]📅 Wochenende erkannt – starte Wochenvorbereitung...[/bold cyan]")
        import threading as _thr_wp
        _thr_wp.Thread(target=_weekend_prep_job, daemon=True, name="weekend-prep-startup").start()

    # Tägliche Datenbankwartung: 02:00 UTC (außerhalb aller Handelszeiten)
    schedule.every().day.at("02:00").do(_daily_maintenance_job)

    # IPO-Tracker: täglich um 06:00 UTC (vor dem Analysezyklus)
    def _ipo_check_job():
        """IPO-Check (ausgelagert nach bot/scheduler_macro.py, Roadmap 4.4a)."""
        scheduler_macro.ipo_check_job(TelegramNotifier)

    schedule.every().day.at("06:00").do(_ipo_check_job)

    # IPO-Check sofort beim Start ausführen wenn Daten älter als 20 Stunden
    try:
        import sqlite3 as _sq, os as _os
        _ipo_db = _os.path.join(_os.path.dirname(__file__), "..", "data", "ipo_tracker.db")
        if _os.path.exists(_ipo_db):
            _conn = _sq.connect(_ipo_db)
            _row = _conn.execute(
                "SELECT MAX(checked_at) FROM ipo_sentiment"
            ).fetchone()
            _conn.close()
            _last = _row[0] if _row and _row[0] else None
            _stale = True
            if _last:
                from datetime import timezone as _tz
                _age = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(_last)).total_seconds()
                _stale = _age > 20 * 3600
        else:
            _stale = True
        if _stale:
            import threading as _thr
            _thr.Thread(target=_ipo_check_job, daemon=True, name="ipo-startup").start()
    except Exception as _ipo_st_err:
        log.debug("IPO-Startup-Check fehlgeschlagen: %s", _ipo_st_err)

    # ── Nutzeranfragen-Job: alle 15 Minuten prüfen ──────────────────────────
    def _user_request_job():
        """Nutzeranfrage-Sofort-Analyse (ausgelagert nach bot/scheduler_macro.py,
        Roadmap 4.4a)."""
        scheduler_macro.user_request_job(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy, safe_run_analysis_cycle,
        )

    schedule.every(15).minutes.do(_user_request_job)

    # ── Tages-Watchdog: stellt sicher dass täglich mindestens eine Analyse läuft ──
    _watchdog_last_triggered: dict = {}  # date_str → datetime

    def _daily_analysis_watchdog():
        """Tages-Watchdog (ausgelagert nach bot/scheduler_analysis.py,
        Roadmap 4.4a)."""
        scheduler_analysis.daily_analysis_watchdog(
            mkt_schedule, portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy, _watchdog_last_triggered,
            TelegramNotifier, safe_run_analysis_cycle)

    schedule.every().hour.do(_daily_analysis_watchdog)
    _daily_analysis_watchdog()  # sofort beim Start prüfen

    # ── Tages-Zusammenfassung: EINMAL täglich am Abend ──────────────────────
    # Früher hat jeder Analyse-Zyklus eine "Tages-Zusammenfassung" gesendet
    # (mehrere pro Tag). Jetzt gebündelt einmal nach US-Börsenschluss.
    _DAILY_SUMMARY_AT = os.getenv("DAILY_SUMMARY_AT", "22:15")

    def _daily_summary_job():
        """Tages-Zusammenfassung (ausgelagert nach
        bot/scheduler_maintenance.py, Roadmap 4.4a)."""
        scheduler_maintenance.daily_summary_job(
            broker, portfolio, phase_ctrl, TelegramNotifier)

    schedule.every().day.at(_DAILY_SUMMARY_AT).do(_daily_summary_job)
    console.print(f"[dim]Tages-Zusammenfassung geplant: {_DAILY_SUMMARY_AT} (Werktags)[/dim]")

    # ── Conditional Entry Preis-Check: alle 15 Minuten ──────────────────────
    def _conditional_entry_job():
        """Conditional-Entry-Ausführung (ausgelagert nach bot/scheduler_risk.py,
        Roadmap 4.4a)."""
        scheduler_risk.conditional_entry_job(broker, strategy, _executor, TelegramNotifier)

    schedule.every(15).minutes.do(_conditional_entry_job)

    # ── IBKR Fill-Check: alle 5 Minuten (nur bei BROKER_MODE=ibkr) ──────────
    if config.broker_mode == "ibkr":
        def _ibkr_fill_check_job():
            """IBKR-Limit-Fill-Buchung (ausgelagert nach bot/scheduler_risk.py,
            Roadmap 4.4a)."""
            scheduler_risk.ibkr_fill_check_job(broker, portfolio, TelegramNotifier)

        schedule.every(5).minutes.do(_ibkr_fill_check_job)

    def _signal_queue_job():
        """Signal-Queue-Drain (ausgelagert nach bot/scheduler_risk.py,
        Roadmap 4.4a)."""
        scheduler_risk.signal_queue_job(signal_queue, strategy, _executor, broker)
    schedule.every(60).minutes.do(_signal_queue_job)

    def _sl_tp_check_job():
        """SL/TP-Check (ausgelagert nach bot/scheduler_risk.py, Roadmap 4.4a)."""
        scheduler_risk.sl_tp_check_job(portfolio, broker, strategy, _executor, _signal_queue_job)
    schedule.every(30).minutes.do(_sl_tp_check_job)

    # ── Positions-Aging-Check alle 4 Stunden ────────────────────────────────
    def _position_aging_job():
        """Position-Aging-Warnung (ausgelagert nach bot/scheduler_risk.py,
        Roadmap 4.4a)."""
        scheduler_risk.position_aging_job(portfolio, broker, TelegramNotifier)

    schedule.every(4).hours.do(_position_aging_job)

    # ── Einzel-Aktien-Eskalation ────────────────────────────────────────────
    # Statt bei jedem Signal den GANZEN Watchlist-Zyklus zu fahren (teuer + laut),
    # analysiert ein getriggertes Signal nur die betroffene(n) Aktie(n) als
    # Fokus-Lauf. Das Frugal-Routing im Zyklus übernimmt die „Ollama prüft das
    # Potenzial vor, Claude entscheidet final"-Logik automatisch: bei lebender
    # lokaler Engine bewertet Ollama vor und nur echte Katalysatoren erreichen
    # Claude; auf langsamer Hardware (Circuit Breaker offen) fällt es direkt auf
    # das günstige Hauptmodell (Haiku). Nur handelbare Ergebnisse melden sich über
    # den normalen Trade-/Digest-Pfad – kein „Analyse läuft"-Spam.
    def _escalate_ticker(tickers, reason: str = "Signal"):
        """Eskalations-Helfer (ausgelagert nach bot/scheduler_scanners.py,
        Roadmap 4.4a)."""
        scheduler_scanners.escalate_ticker(
            tickers, portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy, safe_run_analysis_cycle, reason=reason)

    # ── Headline-Signal-Scanner: stündlich ──────────────────────────────────
    _headline_last_queued: dict = scheduler_scanners.load_headline_cooldown()

    def _headline_scan_job():
        """Headline-Signal-Scanner (ausgelagert nach bot/scheduler_scanners.py,
        Roadmap 4.4a)."""
        scheduler_scanners.headline_scan_job(
            _headline_last_queued, TelegramNotifier, _scanner_notify, _escalate_ticker)

    schedule.every(20).minutes.do(_headline_scan_job)
    import threading as _thr_stagger
    _thr_stagger.Timer(30, _headline_scan_job).start()   # 30s nach Start

    # ── Momentum/Hype-Scanner: alle 45 Minuten während Handelszeiten ─────────
    _momentum_last_queued: dict = scheduler_scanners.load_momentum_cooldown()

    def _momentum_scan_job():
        """Momentum/Hype-Scanner (ausgelagert nach bot/scheduler_scanners.py,
        Roadmap 4.4a)."""
        scheduler_scanners.momentum_scan_job(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
            _momentum_last_queued, TelegramNotifier, _scanner_notify, safe_run_analysis_cycle)

    schedule.every(45).minutes.do(_momentum_scan_job)
    _thr_stagger.Timer(60, _momentum_scan_job).start()   # 60s nach Start

    # ── Breakout-Watch-Scanner: alle 30 Minuten ──────────────────────────────
    _breakout_last_queued: dict = {}   # ticker → datetime

    def _breakout_watch_job():
        """Breakout-Watch-Scanner (ausgelagert nach bot/scheduler_scanners.py,
        Roadmap 4.4a)."""
        scheduler_scanners.breakout_watch_job(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
            _breakout_last_queued, TelegramNotifier, _scanner_notify, safe_run_analysis_cycle)

    schedule.every(30).minutes.do(_breakout_watch_job)

    # ── Reddit-Hype-Scanner: alle 3 Stunden ─────────────────────────────────
    def _reddit_hype_job():
        """Reddit-Hype-Scanner (ausgelagert nach bot/scheduler_scanners2.py,
        Roadmap 4.4a)."""
        scheduler_scanners2.reddit_hype_job(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy, config,
            TelegramNotifier, _scanner_notify, safe_run_analysis_cycle)

    schedule.every(3).hours.do(_reddit_hype_job)
    _thr_stagger.Timer(90, _reddit_hype_job).start()   # 90s nach Start

    # ── Kursbewegungs-Alarm: alle 5 Minuten während Handelszeiten ───────────
    _price_move_last: dict = {}   # ticker → letzter bekannter Kurs

    def _price_move_job():
        """Kursbewegungs-Alarm (ausgelagert nach bot/scheduler_scanners2.py,
        Roadmap 4.4a)."""
        scheduler_scanners2.price_move_job(
            broker, config, _price_move_last, TelegramNotifier, _scanner_notify)

    schedule.every(5).minutes.do(_price_move_job)

    # ── Options-Flow-Scan: stündlich für Watchlist ───────────────────────────
    def _options_flow_job():
        """Options-Flow-Scan (ausgelagert nach bot/scheduler_scanners2.py,
        Roadmap 4.4a)."""
        scheduler_scanners2.options_flow_job(config, TelegramNotifier, _scanner_notify)

    schedule.every().hour.do(_options_flow_job)

    # ── Geopolitischer Radar: alle 2 Stunden ────────────────────────────────
    def _geopolitical_radar_job():
        """Geopolitik-Radar (ausgelagert nach bot/scheduler_macro.py,
        Roadmap 4.4a)."""
        scheduler_macro.geopolitical_radar_job(config, TelegramNotifier, _scanner_notify)

    schedule.every(2).hours.do(_geopolitical_radar_job)
    _thr_stagger.Timer(120, _geopolitical_radar_job).start()   # 120s nach Start

    # ── Marktbreite-Check: Vorsichts-Modus bei breitem Sektor-Einbruch ───────
    def _market_breadth_job():
        """Marktbreite-Check (ausgelagert nach bot/scheduler_macro.py,
        Roadmap 4.4a)."""
        scheduler_macro.market_breadth_job(TelegramNotifier)

    schedule.every(2).hours.do(_market_breadth_job)

    # ── Markt-Lagebericht: täglich 08:30 UTC + Cache-Refresh alle 4h ─────────
    def _market_overview_refresh_job():
        """Market-Overview-Cache-Refresh (ausgelagert nach bot/scheduler_macro.py,
        Roadmap 4.4a)."""
        scheduler_macro.market_overview_refresh_job()

    _lagebericht_sent_date: list = [""]   # [0] = ISO-Datum des letzten Sendens

    def _morning_lagebericht_job():
        """Morgen-Lagebericht (ausgelagert nach bot/scheduler_macro.py,
        Roadmap 4.4a)."""
        scheduler_macro.morning_lagebericht_job(_lagebericht_sent_date, TelegramNotifier)

    schedule.every(4).hours.do(_market_overview_refresh_job)
    schedule.every().day.at("08:30").do(_morning_lagebericht_job)
    _thr_stagger.Timer(5, _market_overview_refresh_job).start()   # 5s nach Start (Cache für andere Jobs)

    # ── PEAD-Scanner: täglich morgens + stündlich während Handelszeiten ──────
    def _pead_scan_job():
        """PEAD-Scanner (ausgelagert nach bot/scheduler_scanners2.py,
        Roadmap 4.4a)."""
        scheduler_scanners2.pead_scan_job(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
            TelegramNotifier, _scanner_notify, safe_run_analysis_cycle)

    schedule.every().hour.do(_pead_scan_job)

    # ── Short-Squeeze-Scanner: alle 4 Stunden während Handelszeiten ──────────
    def _short_squeeze_scan_job():
        """Short-Squeeze-Scanner (ausgelagert nach bot/scheduler_scanners2.py,
        Roadmap 4.4a)."""
        scheduler_scanners2.short_squeeze_scan_job(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy, config,
            TelegramNotifier, _scanner_notify, safe_run_analysis_cycle)

    schedule.every(4).hours.do(_short_squeeze_scan_job)

    # ── Insider-Proaktiv-Scanner: täglich + nach Marktöffnung ───────────────
    _insider_last_queued: dict = {}   # ticker → datetime

    def _insider_proactive_job():
        """Insider-Proaktiv-Scanner (ausgelagert nach
        bot/scheduler_scanners2.py, Roadmap 4.4a)."""
        scheduler_scanners2.insider_proactive_job(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
            _insider_last_queued, TelegramNotifier, _scanner_notify, safe_run_analysis_cycle)

    schedule.every().day.at("08:30").do(_insider_proactive_job)
    schedule.every().day.at("13:00").do(_insider_proactive_job)

    # ── Sektor-Kaskaden-Scanner: alle 60 Minuten ─────────────────────────────
    _cascade_last_queued: dict = {}   # ticker → datetime

    def _sector_cascade_job():
        """Sektor-Kaskaden-Scanner (ausgelagert nach
        bot/scheduler_scanners2.py, Roadmap 4.4a)."""
        scheduler_scanners2.sector_cascade_job(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
            _cascade_last_queued, TelegramNotifier, _scanner_notify, safe_run_analysis_cycle)

    schedule.every(60).minutes.do(_sector_cascade_job)

    # ── Intraday-Scan: optionales drittes Analysefenster ────────────────────
    if config.intraday_scan_enabled:
        def _intraday_scan_job():
            """Intraday-Scan (ausgelagert nach bot/scheduler_scanners2.py,
            Roadmap 4.4a)."""
            scheduler_scanners2.intraday_scan_job(
                portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                safe_run_analysis_cycle)

        # Bereinigung: nur HH:MM behalten, Kommentare/Leerzeichen abschneiden
        intraday_time = (config.intraday_scan_time or "17:30").split()[0].strip()
        try:
            schedule.every().day.at(intraday_time).do(_intraday_scan_job)
            console.print(
                f"[dim]Intraday-Scan aktiviert: täglich {intraday_time} UTC "
                f"(= {intraday_time} Serverzeit)[/dim]"
            )
        except Exception as _idt_err:
            log.warning(
                "Intraday-Scan: ungültige INTRADAY_SCAN_TIME='%s' – "
                "verwende 17:30 als Fallback. Fehler: %s",
                intraday_time, _idt_err,
            )
            schedule.every().day.at("17:30").do(_intraday_scan_job)
            console.print("[dim]Intraday-Scan aktiviert: täglich 17:30 UTC (Fallback)[/dim]")
    else:
        console.print(
            "[dim]Intraday-Scan deaktiviert. Aktivieren: INTRADAY_SCAN_ENABLED=true "
            "in .env (empfohlen: INTRADAY_SCAN_TIME=17:30)[/dim]"
        )

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

    # Tägliches Dashboard: 20:30 UTC = 22:30 CEST (30 Min nach NYSE-Schluss)
    from analyzers.bot_scorer import BotScorer as _BotScorer
    _dashboard = DailyDashboard()

    def _daily_dashboard_job():
        """Tägliches Dashboard-Digest (ausgelagert nach
        bot/scheduler_maintenance.py, Roadmap 4.4a)."""
        scheduler_maintenance.daily_dashboard_job(
            _dashboard, portfolio, tracker, broker, _BotScorer, TelegramNotifier)

    schedule.every().day.at("20:30").do(_daily_dashboard_job)

    # Goal-reached check (einmalige Telegram-Nachricht wenn Ziel erreicht)
    _goal_notified: list = []
    if goal_risk.active:
        schedule.every().hour.do(
            lambda: _check_goal_reached(goal_risk, portfolio, broker, tracker, TelegramNotifier(), _goal_notified)
        )

    # Circuit-Breaker-Monitor: einmalige Reflexion wenn CB heute ausgelöst wird
    _cb_triggered_today: list = []
    _cb_trigger_date: list = []   # eigenes Datum-Tracking statt _state-Zugriff
    _circuit_breaker = CircuitBreaker()

    def _cb_monitor_job():
        try:
            prices = broker.get_prices(list(portfolio.all_positions().keys()))
            current_value = portfolio.total_value(prices)
            _circuit_breaker.register_day_open(current_value)
            allowed, reason = _circuit_breaker.check_buy_allowed(current_value)
            today = __import__("datetime").date.today().isoformat()
            # Tages-Reset zuerst: neuer Tag → CB-Status zurücksetzen
            if _cb_triggered_today and (not _cb_trigger_date or _cb_trigger_date[0] != today):
                _cb_triggered_today.clear()
                _cb_trigger_date.clear()
            if not allowed and not _cb_triggered_today:
                _cb_triggered_today.append(True)
                _cb_trigger_date.append(today)
                console.print(f"\n  [bold red]⛔ CIRCUIT BREAKER: {reason}[/bold red]")
                _run_post_cb_reflection(
                    _circuit_breaker, portfolio, broker, tracker, reflection, TelegramNotifier()
                )
        except Exception as _cb_err:
            log.warning("Circuit-Breaker-Monitor fehlgeschlagen: %s", _cb_err)

    schedule.every(15).minutes.do(_cb_monitor_job)

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

    console.print(f"[dim]SL/TP-Check alle 30 Min · Aging-Check alle 4h · Wochenvorbereitung Sa 09:00 + So 14:00 · Ctrl+C zum Beenden.[/dim]")

    # ── Adaptive Resource Manager: alle 2 Minuten ────────────────────────────
    try:
        from system.resource_manager import get_resource_manager, ResourceTier, TIER_MODELS
        _rm = get_resource_manager()
        _rm_prev_tier: list = [None]   # mutable container for closure

        def _resource_check_job():
            try:
                state = _rm.update(force=True)
                prev  = _rm_prev_tier[0]
                if prev == state.tier:
                    return
                _rm_prev_tier[0] = state.tier

                tier_labels = {
                    ResourceTier.PERFORMANCE: ("🚀", "PERFORMANCE", "green"),
                    ResourceTier.BALANCED:    ("⚖️",  "BALANCED",    "yellow"),
                    ResourceTier.MINIMAL:     ("🔋", "MINIMAL",     "dim"),
                }
                icon, label, color = tier_labels[state.tier]
                console.print(
                    f"\n  [{color}]{icon} Ressourcentier: {label}[/{color}]  "
                    f"[dim]RAM frei: {state.ram_free_gb:.1f}GB / {state.ram_total_gb:.1f}GB "
                    f"({state.ram_free_pct*100:.0f}%) | "
                    f"Idle: {state.idle_seconds:.0f}s | "
                    f"Ollama: {state.ollama_model}[/dim]"
                )

                # Apply model change to Ollama prescreener if loaded
                try:
                    from analyzers.ollama_prescreener import OllamaPrescreener as _OP
                    import analyzers.ollama_prescreener as _op_mod
                    if hasattr(_op_mod, "_prescreener_instance"):
                        _rm.apply_to_ollama(_op_mod._prescreener_instance)
                except Exception:
                    pass

                _rm.apply_to_caches()

                # Telegram only on significant tier changes
                if prev is not None:
                    try:
                        TelegramNotifier().send(
                            f"{icon} <b>Ressourcentier: {label}</b>\n\n"
                            f"RAM frei: {state.ram_free_gb:.1f}GB / {state.ram_total_gb:.1f}GB "
                            f"({state.ram_free_pct*100:.0f}%)\n"
                            f"Mac idle: {state.idle_seconds:.0f}s\n"
                            f"Ollama-Modell: <code>{state.ollama_model}</code>"
                        )
                    except Exception:
                        pass
            except Exception as _re:
                log.debug("Resource-Check fehlgeschlagen: %s", _re)

        schedule.every(2).minutes.do(_resource_check_job)
        _resource_check_job()   # Einmal beim Start
    except ImportError:
        log.debug("system.resource_manager nicht verfügbar – adaptives RAM-Management deaktiviert")

    _CRASH_LOG = os.path.join(os.path.dirname(__file__), "..", "data", "crash_log.txt")

    # Pause-Schalter (Dashboard): solange gesetzt, werden KEINE Jobs ausgeführt –
    # kompletter Stopp inkl. SL/TP-Überwachung. _paused_state merkt sich den letzten
    # Zustand, damit Telegram/Konsole nur beim Übergang (nicht jede Minute) meldet.
    from system import bot_control
    _paused_state = False

    try:
        while True:
            try:
                _now_paused = bot_control.is_paused()
                if _now_paused:
                    if not _paused_state:
                        _paused_state = True
                        log.warning("Bot pausiert (Dashboard) – alle Jobs angehalten.")
                        console.print("[bold yellow]⏸ Bot pausiert – alle Jobs angehalten (Dashboard).[/bold yellow]")
                        try:
                            TelegramNotifier().send(
                                "⏸ <b>Bot pausiert</b>\n\n"
                                "Alle Jobs sind angehalten (inkl. SL/TP-Überwachung).\n"
                                "Im Dashboard wieder aktivieren, um fortzufahren."
                            )
                        except Exception:
                            pass
                    time.sleep(60)
                    continue
                if _paused_state:
                    _paused_state = False
                    log.info("Bot fortgesetzt (Dashboard) – Jobs laufen wieder.")
                    console.print("[bold green]▶️ Bot fortgesetzt – Jobs laufen wieder.[/bold green]")
                    try:
                        TelegramNotifier().send(
                            "▶️ <b>Bot fortgesetzt</b>\n\nAlle Jobs sind wieder aktiv."
                        )
                    except Exception:
                        pass
                schedule.run_pending()
                # Live-Status (Roadmap 1.5a): zwischen Jobs ist der Bot idle.
                # Schreibt auch den nächsten geplanten Lauf und heilt einen
                # nach Crash hängengebliebenen "cycle"-Status von selbst.
                try:
                    from system import live_status as _ls
                    _nr = schedule.next_run()
                    _ls.set_idle(next_run=_nr.isoformat(timespec="seconds")
                                 if _nr else None)
                except Exception:
                    pass
                # Externer Dead-Man-Switch (Roadmap 1.7): Lebenszeichen an einen
                # Dienst außerhalb dieses Servers – deckt Server-/Netzausfall ab,
                # den watchdog.sh (läuft auf demselben Host) nicht mehr melden
                # könnte. No-Op ohne konfigurierte URL, intern gedrosselt.
                try:
                    from system import dead_man_switch as _dms
                    _dms.ping()
                except Exception:
                    pass
                # Telegram /status-Befehl (Roadmap 1.5g): kein Webhook (kein
                # öffentlicher Endpunkt) – Short-Polling an derselben Stelle
                # wie der Dead-Man-Switch-Ping, die Schleife tickt ohnehin
                # ~1×/Minute. No-Op ohne konfigurierten Token.
                try:
                    from system import telegram_commands as _tgc
                    _tgc.poll()
                except Exception:
                    pass
            except KeyboardInterrupt:
                raise
            except Exception as _job_err:
                import traceback as _tb_loop
                _tb_text = _tb_loop.format_exc()
                log.exception("Unbehandelter Fehler in schedule.run_pending – Bot läuft weiter: %s", _job_err)
                # Crash in Datei schreiben (Telegram könnte Rate-Limited sein)
                try:
                    with open(_CRASH_LOG, "a", encoding="utf-8") as _cf:
                        _cf.write(f"\n=== {datetime.now().isoformat()} ===\n{_tb_text}\n")
                except Exception:
                    pass
                # Telegram-Crash-Meldung (best-effort)
                try:
                    TelegramNotifier().send(
                        f"⚠️ <b>Job-Fehler</b> (Bot läuft weiter)\n\n"
                        f"<code>{_tb_text[-800:]}</code>",
                        level="critical",
                    )
                except Exception:
                    pass
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot gestoppt.[/yellow]")
