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
from analyzers.regime_adaptive import (
    invalidate_cache_if_crash, get_last_cached_regime,
    set_market_caution, clear_market_caution, get_market_caution,
)
from collectors.news_archive import NewsArchive
from analyzers.market_schedule import MarketSchedule
from analyzers.weekend_prep import WeekendPrep
from analyzers.parameter_optimizer import ParameterOptimizer, _MIN_TRADES
from bot.pre_market_scanner import PreMarketScanner
from bot.runner import run_analysis_cycle, safe_run_analysis_cycle, _print_portfolio_summary
from bot import scheduler_maintenance
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
        if datetime.now(timezone.utc).replace(tzinfo=None).day == 1:
            console.print("[bold magenta]📋 Erstelle monatliche Selbsteinschätzung...[/bold magenta]")
            content = reflection.generate_monthly_review()
            if content:
                console.print(Panel(content[:800] + "...", title="Monatsreview erstellt", border_style="magenta"))

    def _reschedule_analysis():
        """Rebuilds analysis schedule for the new day (handles DST changes)."""
        # Snapshot existing jobs BEFORE cancelling — restore on error
        cancelled = [
            job for job in list(schedule.jobs)
            if getattr(job, "_is_analysis_job", False)
        ]
        for job in cancelled:
            schedule.cancel_job(job)
        try:
            _register_analysis_jobs()
        except Exception as _rsa_err:
            log.error("_register_analysis_jobs fehlgeschlagen – stelle Jobs wieder her: %s", _rsa_err)
            # Entferne halb-erstellte Jobs bevor alte wiederhergestellt werden
            for _partial in [j for j in list(schedule.jobs) if getattr(j, "_is_analysis_job", False)]:
                schedule.cancel_job(_partial)
            for job in cancelled:
                schedule.jobs.append(job)

    def _pre_market_job(exchange: str):
        """Pre-Market Briefing: schneller Daten-Scan ohne Claude."""
        console.rule(f"[bold yellow]Pre-Market Briefing – {exchange}[/bold yellow]")
        try:
            from bot.runner import _get_watchlist
            watchlist, _ = _get_watchlist(portfolio)
            scanner = PreMarketScanner()
            briefing = scanner.run(exchange=exchange, watchlist=watchlist)
            if briefing:
                for line in briefing.to_console_lines():
                    console.print(line)
                TelegramNotifier().send(briefing.to_telegram())
        except Exception as e:
            log.warning("Pre-Market-Job %s fehlgeschlagen: %s", exchange, e)

    def _register_analysis_jobs():
        # Use LOCAL date (not UTC) so midnight reschedule doesn't get "yesterday"
        local_date = datetime.now().date()
        slots = mkt_schedule.get_schedule_strings(date=local_date)
        is_weekend = local_date.weekday() >= 5
        if not slots or is_weekend:
            if is_weekend:
                console.print("[dim]Wochenende – keine Vollanalysen geplant (nur Wochenvorbereitung).[/dim]")
            else:
                console.print("[dim]Heute kein Handelstag.[/dim]")
            return
        for slot in slots:
            # Pre-Market Briefing 30 Min vor Open (gleiche Zeit wie Vollanalyse).
            # MUSS ZUERST registriert werden: schedule arbeitet gleichzeitig fällige
            # Jobs in Registrierungsreihenfolge sequenziell im selben Thread ab.
            # Wird die Vollanalyse vorher registriert, blockiert sie (Claude, ~15-40 min)
            # das schnelle Briefing-Telegram bis nach Analyse-Ende.
            pre_hhmm = slot["hhmm"]
            exch = slot["exchange"]
            pre_job = schedule.every().day.at(pre_hhmm).do(_pre_market_job, exch)
            pre_job._is_analysis_job = True

            # Volle Analyse 30 Min vor Open – läuft direkt nach dem Briefing
            job = schedule.every().day.at(slot["hhmm"]).do(
                safe_run_analysis_cycle,
                portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                announce_start=True,
            )
            job._is_analysis_job = True
            review_job = schedule.every().day.at(slot["hhmm"]).do(_monthly_review_check)
            review_job._is_analysis_job = True

        times_str = ", ".join(f"{s['hhmm']} ({s['exchange']})" for s in slots)
        console.print(f"[dim]Analyse-Jobs registriert: {times_str}[/dim]")
        pre_times = ", ".join(
            f"{s['hhmm']} pre-market ({s['exchange']})" for s in slots
        )
        console.print(f"[dim]Pre-Market-Jobs: {pre_times}[/dim]")

    def _weekend_prep_job():
        """Runs weekend preparation. Called Saturday 09:00 and Sunday 14:00."""
        console.print(f"\n[bold cyan]📅 Wochenvorbereitung startet...[/bold cyan]")
        try:
            run_weekend_prep(weekend_prep_inst)
        except Exception as _wp_err:
            log.warning("Wochenvorbereitung fehlgeschlagen: %s", _wp_err)
            try:
                TelegramNotifier().send(
                    f"⚠️ <b>Wochenvorbereitung fehlgeschlagen</b>\n\n"
                    f"Fehler: {str(_wp_err)[:200]}\n\n"
                    f"Bot läuft weiter – Briefing wird beim nächsten Versuch (So 14:00) nachgeholt."
                )
            except Exception:
                pass

        # Weekly buy-blocked diagnostics report
        try:
            import json as _j
            _bb_path = os.path.join(os.path.dirname(__file__), "..", "data", "buy_blocked.json")
            with open(_bb_path, encoding="utf-8") as _fh:
                _bb = _j.load(_fh)
            # Get last 2 weeks of data
            _weeks = sorted(_bb.keys())[-2:]
            if _weeks:
                _lines = []
                for _wk in _weeks:
                    _counts = _bb[_wk]
                    _sorted = sorted(_counts.items(), key=lambda x: x[1], reverse=True)
                    _wk_lines = "\n".join(
                        f"  • {k}: {v}×" for k, v in _sorted[:8]
                    )
                    _lines.append(f"<b>{_wk}</b>\n{_wk_lines}")
                TelegramNotifier().send(
                    f"🔒 <b>Wöchentlicher Kauf-Blockier-Bericht</b>\n\n"
                    + "\n\n".join(_lines)
                    + "\n\n<i>Tipps: 'sentiment_schwelle' → Schwelle senken; "
                    f"'positionslimit' → mehr Slots; 'sektor_schwach' → normal.</i>"
                )
        except FileNotFoundError:
            pass  # No data yet – skip silently
        except Exception as _bb_err:
            log.debug("Buy-Blocked-Report fehlgeschlagen: %s", _bb_err)

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
    # wird die Analyse sofort nachgeholt – bis zu 180 Minuten nach dem Fenster.
    _CATCHUP_MAX_MINUTES = 180

    def _catchup_missed_window():
        now_local = datetime.now()
        local_date = now_local.date()
        if local_date.weekday() >= 5:
            return
        slots = mkt_schedule.get_schedule_strings(date=local_date)

        # Analyse-Log einmal laden um doppelte Nachholungen zu verhindern
        _today_str = local_date.isoformat()
        _already_ran = False
        try:
            from analyzers.analysis_log import AnalysisLog as _AL
            _recent = _AL().get_recent(limit=1)
            if _recent and (_recent[0].get("analyzed_at") or "").startswith(_today_str):
                _already_ran = True
        except Exception:
            pass

        if _already_ran:
            return  # Analyse hat heute schon stattgefunden – kein Nachholen nötig

        for slot in slots:
            try:
                h, m = map(int, slot["hhmm"].split(":"))
                slot_dt = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = (now_local - slot_dt).total_seconds() / 60
                if 0 < diff <= _CATCHUP_MAX_MINUTES:
                    console.print(
                        f"\n[bold yellow]⏰ Analyse-Fenster {slot['hhmm']} ({slot['exchange']}) "
                        f"um {diff:.0f} Min verpasst – hole jetzt nach...[/bold yellow]"
                    )
                    TelegramNotifier().send(
                        f"⏰ <b>Nachhol-Analyse</b>\n\n"
                        f"Bot wurde nach dem geplanten Fenster gestartet "
                        f"({slot['hhmm']} {slot['exchange']} vor {diff:.0f} Min).\n"
                        f"Starte Pre-Market Briefing und Analyse jetzt..."
                    )
                    _pre_market_job(slot["exchange"])
                    safe_run_analysis_cycle(
                        portfolio, broker, strategy, tracker, phase_ctrl,
                        archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                        earnings_strategy,
                    )
                    break
            except Exception as e:
                log.warning("Catch-up-Check fehlgeschlagen: %s", e)

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
        try:
            from analyzers.ipo_tracker import IPOTracker
            tracker_ipo = IPOTracker()
            new_ipos = tracker_ipo.run_daily_check()
            for event in new_ipos:
                cand = event["candidate"]
                ticker = event["live_ticker"]
                notifier_ipo = TelegramNotifier()
                eligible_txt = (
                    f"✅ Ticker <b>{ticker}</b> wurde zur Analyse-Queue hinzugefügt."
                    if cand.auto_watchlist_eligible
                    else f"⚠️ Bewertung unter $25 Mrd. → Ticker NICHT automatisch aufgenommen."
                )
                notifier_ipo.send(
                    f"🚀 <b>IPO ERKANNT: {cand.name}</b>\n\n"
                    f"Ticker: <b>{ticker}</b>\n"
                    f"Sektor: {cand.sector}\n"
                    f"Bewertung: ~${cand.expected_valuation_b:.0f} Mrd.\n"
                    f"{cand.notes}\n\n"
                    f"{eligible_txt}\n\n"
                    f"<i>Erster Handelstag – noch wenig Daten vorhanden.</i>"
                )
                tracker_ipo.mark_notified(event["slug"])
                console.print(
                    f"  [bold magenta]🚀 IPO erkannt: {cand.name} ({ticker})[/bold magenta]"
                )
        except Exception as e:
            log.warning("IPO-Check fehlgeschlagen: %s", e)

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
        """Sofort-Analyse wenn Nutzer Ticker über das Dashboard angefordert hat.
        Außerhalb der Handelszeiten (06–23 Uhr, Wochentags) verbleiben Ticker in
        der Queue und werden bei der nächsten vorbörslichen Analyse verarbeitet.
        """
        try:
            _now = datetime.now()
            if _now.weekday() >= 5 or not (6 <= _now.hour < 23):
                return  # Queue wartet – nächste Vorbörsliche Analyse nimmt sie mit
            from analyzers import user_request_queue as _urq
            pending = _urq.peek()
            if not pending:
                return
            log.info(
                "Nutzeranfrage-Job: %d Ticker sofort analysieren: %s",
                len(pending), pending,
            )
            console.print(
                f"\n[bold cyan]📬 Nutzeranfrage – sofortige Analyse: {', '.join(pending)}[/bold cyan]"
            )
            safe_run_analysis_cycle(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
        except Exception as _urq_err:
            log.error("Nutzeranfrage-Job fehlgeschlagen: %s", _urq_err)

    schedule.every(15).minutes.do(_user_request_job)

    # ── Tages-Watchdog: stellt sicher dass täglich mindestens eine Analyse läuft ──
    _watchdog_last_triggered: dict = {}  # date_str → datetime

    def _daily_analysis_watchdog():
        """
        Prüft stündlich ob heute bereits eine Analyse gelaufen ist.
        Fehlt sie (nach dem geplanten Zeitfenster), wird eine Nachhol-Analyse gestartet.
        Verhindert stille Ausfälle durch Reschedule-Fehler oder Bot-Neustart.
        Skippt nur wenn die letzte Watchdog-Analyse weniger als 3 Stunden her ist,
        damit nach einem Neustart (z.B. nach Deploy) eine Folgeanalyse möglich ist.
        """
        now = datetime.now()
        today = now.date()
        if today.weekday() >= 5:
            return
        today_str = today.isoformat()

        # Nur skippen wenn dieser Watchdog heute schon eine Analyse ausgelöst hat
        # UND das weniger als 3 Stunden her ist
        _last = _watchdog_last_triggered.get(today_str)
        if _last is not None:
            _age_h = (now - _last).total_seconds() / 3600
            if _age_h < 3.0:
                return

        slots = mkt_schedule.get_schedule_strings(date=today)
        if not slots:
            return

        # Prüfe ob wir den ersten Slot um mehr als 30 Min überschritten haben
        h, m = map(int, slots[0]["hhmm"].split(":"))
        slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now < slot_dt + __import__("datetime").timedelta(minutes=30):
            return  # noch zu früh

        # Obergrenze: nach dem letzten Slot + 90 Min ist der Handelstag vorbei.
        # Keine Nachhol-Analyse am Abend/in der Nacht (verhindert Telegram-Spam).
        _last_h, _last_m = 0, 0
        for _s in slots:
            _hh, _mm = map(int, _s["hhmm"].split(":"))
            if (_hh, _mm) > (_last_h, _last_m):
                _last_h, _last_m = _hh, _mm
        last_slot_dt = now.replace(hour=_last_h, minute=_last_m, second=0, microsecond=0)
        if now > last_slot_dt + __import__("datetime").timedelta(minutes=90):
            return  # Handelstag vorbei – nachts nicht nachholen

        # Prüfe ob IRGENDEIN heutiger Slot in den letzten 45 Min war → Analyse läuft noch
        _td = __import__("datetime").timedelta
        for _slot in slots:
            _sh, _sm = map(int, _slot["hhmm"].split(":"))
            _slot_dt = now.replace(hour=_sh, minute=_sm, second=0, microsecond=0)
            if _td(0) <= now - _slot_dt <= _td(minutes=45):
                return  # Analyse läuft wahrscheinlich noch

        # Jüngsten "fälligen" Slot bestimmen: dessen Zeit liegt >45 Min zurück.
        # Eine feste 3h-Freshness war falsch – zwischen zwei Börsen-Slots (z.B.
        # XETRA 07:30 → NYSE 15:00) liegen legitime ~7,5h, in denen der Watchdog
        # sonst grundlos feuert (+ überflüssige Nachhol-Analyse + Tages-Summary).
        _due_slot_dt = None
        for _slot in slots:
            _sh, _sm = map(int, _slot["hhmm"].split(":"))
            _sd = now.replace(hour=_sh, minute=_sm, second=0, microsecond=0)
            if now - _sd > _td(minutes=45) and (_due_slot_dt is None or _sd > _due_slot_dt):
                _due_slot_dt = _sd
        if _due_slot_dt is None:
            return  # heute noch kein Slot fällig

        # Lief seit (fälligem Slot − 30 Min) eine Analyse? Dann wurde der Slot
        # bedient → kein Ausfall, nicht feuern. Nur ein echter Slot-Miss alarmiert.
        try:
            from analyzers.analysis_log import AnalysisLog as _AL
            recent = _AL().get_recent(limit=1)
            if recent:
                _last_ts = recent[0].get("analyzed_at") or ""
                try:
                    from datetime import datetime as _dt
                    # analysis_log speichert analyzed_at als naive UTC (utcnow()).
                    # Der Watchdog rechnet in lokaler Zeit (now/_due_slot_dt). Ohne
                    # Umrechnung erscheint eine 07:30-Analyse als 05:30 (UTC+2) und
                    # der Watchdog hält den planmäßig bedienten Slot fälschlich für
                    # verpasst → unnötige Nachhol-Analyse + Telegram-Spam.
                    _utc_offset = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
                    _last_dt = _dt.fromisoformat(_last_ts) + _utc_offset
                    if _last_dt >= _due_slot_dt - _td(minutes=30):
                        _watchdog_last_triggered[today_str] = _last_dt
                        return
                except Exception:
                    pass
        except Exception:
            pass

        log.warning(
            "Tages-Watchdog: Geplanter Slot %s ohne Analyse – starte Nachhol-Analyse.",
            _due_slot_dt.strftime("%H:%M"),
        )
        console.print(
            f"\n[bold yellow]🔔 Tages-Watchdog: Slot {_due_slot_dt.strftime('%H:%M')} verpasst – hole nach...[/bold yellow]"
        )
        TelegramNotifier().send(
            "⏰ <b>Tages-Watchdog</b>\n\n"
            f"Geplante Analyse um {_due_slot_dt.strftime('%H:%M')} fehlt – starte Nachhol-Analyse jetzt."
        )
        safe_run_analysis_cycle(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
        # Zeitstempel merken – nächster Watchdog-Lauf in 1h prüft ob Analyse frisch genug
        _watchdog_last_triggered[today_str] = datetime.now()
        try:
            from analyzers.analysis_log import AnalysisLog as _AL
            after = _AL().get_recent(limit=1)
            if not (after and (after[0].get("analyzed_at") or "").startswith(today_str)):
                log.warning("Tages-Watchdog: Analyse lief, aber kein Log-Eintrag – erneuter Versuch in 1h")
                del _watchdog_last_triggered[today_str]  # Retry erlauben
        except Exception:
            pass  # Im Zweifel nächste Stunde wieder prüfen

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
        """Führt ausstehende bedingte Einstiege aus sobald der Trigger-Kurs erreicht ist.
        Entries mit IBKR-Limit-Order (ibkr_order_id gesetzt) werden übersprungen –
        der _ibkr_fill_check_job übernimmt deren Abwicklung."""
        try:
            from analyzers.conditional_entry import ConditionalEntryWatcher
            from analyzers import AnalysisResult
            watcher = ConditionalEntryWatcher()
            active = watcher.get_active()
            if not active:
                return
            # Nur Entries ohne IBKR-Limit-Order per Polling prüfen
            manual_entries = [e for e in active if not e.ibkr_order_id]
            if not manual_entries:
                return
            prices = broker.get_prices([e.ticker for e in manual_entries])
            triggered = watcher.check_triggered(prices)
            triggered = [e for e in triggered if not e.ibkr_order_id]
            if not triggered:
                return
            notifier = TelegramNotifier()
            for entry in triggered:
                price = prices.get(entry.ticker, entry.trigger_price)
                console.print(
                    f"\n[bold green]📌 Conditional Entry ausgelöst: "
                    f"{entry.ticker} @ ${price:.2f} "
                    f"(Trigger war: ${entry.trigger_price:.2f})[/bold green]"
                )
                analysis = AnalysisResult(
                    ticker=entry.ticker,
                    sentiment_score=entry.sentiment_score,
                    direction="BULLISH",
                    confidence=entry.confidence,
                    recommendation="BUY",
                    entry_rationale=(
                        f"[Conditional Entry @ ${price:.2f}] {entry.entry_rationale}"
                    ),
                    risk_factors=entry.risk_factors,
                    key_catalysts=entry.key_catalysts,
                    suggested_hold_days=entry.suggested_hold_days,
                    target_price=entry.target_price,
                    target_price_rationale=entry.target_price_rationale,
                    thesis_valid=None,
                    thesis_break_reason="",
                    # Quellenzahl der Ursprungs-Analyse replayen (Floor 1), sonst
                    # blockt die min_sources-Schranke den bereits vollständig
                    # analysierten Entry fälschlich mit 0 Quellen.
                    sources_used={"conditional_entry": max(1, entry.sources_count)},
                    bull_case=entry.bull_case,
                    bear_case=entry.bear_case,
                    debate_winner="BULL",
                )
                result = strategy.evaluate(entry.ticker, analysis, price, "NEUTRAL")
                action = _executor.execute(result, analysis=analysis)
                if action and "GEKAUFT" in action:
                    watcher.remove(entry.ticker)
                    # Executor hat bereits die Standard-Kauf-Benachrichtigung
                    # gesendet (inkl. "[Conditional Entry @ …]"-Kontext).
                    log.info("Conditional Entry ausgeführt: %s @ $%.2f", entry.ticker, price)
                else:
                    log.info(
                        "Conditional Entry ausgelöst aber kein Trade: %s (Strategy-Filter: %s)",
                        entry.ticker, action,
                    )
        except Exception as _e:
            log.warning("Conditional-Entry-Job fehlgeschlagen: %s", _e)

    schedule.every(15).minutes.do(_conditional_entry_job)

    # ── IBKR Fill-Check: alle 5 Minuten (nur bei BROKER_MODE=ibkr) ──────────
    if config.broker_mode == "ibkr":
        def _ibkr_fill_check_job():
            """Prüft ob hinterlegte IBKR Limit-Orders ausgeführt wurden und trägt Positionen ein."""
            try:
                import math as _math
                from analyzers.conditional_entry import ConditionalEntryWatcher
                from portfolio.portfolio import Position
                from datetime import datetime as _dt
                watcher = ConditionalEntryWatcher()
                active = watcher.get_active()
                ibkr_entries = [e for e in active if e.ibkr_order_id]
                if not ibkr_entries:
                    return
                order_ids = [e.ibkr_order_id for e in ibkr_entries]
                fills = broker.get_filled_limit_orders(order_ids)
                if not fills:
                    return
                notifier = TelegramNotifier()
                filled_ids = {f["order_id"] for f in fills}
                fill_map   = {f["order_id"]: f for f in fills}
                for entry in ibkr_entries:
                    if entry.ibkr_order_id not in filled_ids:
                        continue
                    fill = fill_map[entry.ibkr_order_id]
                    fill_price = fill["fill_price"]
                    shares     = fill["shares"]
                    sl = fill_price * (1 - 0.07)
                    tp = entry.target_price or fill_price * 1.20
                    pos = Position(
                        ticker=entry.ticker,
                        shares=shares,
                        entry_price=fill_price,
                        entry_date=_dt.utcnow().isoformat()[:10],
                        stop_loss=round(sl, 4),
                        take_profit=round(tp, 4),
                        target_hold_days=entry.suggested_hold_days,
                        rationale=f"[IBKR Limit-Fill @ ${fill_price:.2f}] {entry.entry_rationale}",
                        entry_catalysts=entry.key_catalysts,
                    )
                    try:
                        # force=True: der Fill ist beim Broker bereits Tatsache
                        # (Geld ausgegeben) – ein Buch-Cash-Mangel darf die
                        # Buchung nicht auf ewig verhindern (sonst hält IBKR die
                        # Position, das Buch weiß nie davon, Job spammt alle 5min).
                        portfolio.open_position(pos, force=True)
                        watcher.remove(entry.ticker)
                        # Broker-seitigen GTC-Schutz-Stop hinterlegen (greift
                        # auch bei Bot-Ausfall); update_stop platziert neu.
                        _upd = getattr(broker, "update_stop", None)
                        if callable(_upd):
                            try:
                                _upd(entry.ticker, shares, round(sl, 4))
                            except Exception as _ste:
                                log.warning("Schutz-Stop für Limit-Fill %s nicht platziert: %s",
                                            entry.ticker, _ste)
                        log.info(
                            "IBKR Limit-Order gefüllt: %s %.4f @ $%.4f (Order #%d)",
                            entry.ticker, shares, fill_price, entry.ibkr_order_id,
                        )
                        notifier.send(
                            f"📌 <b>IBKR Limit-Order ausgeführt: {entry.ticker}</b>\n\n"
                            f"Fill: {shares} Stück @ <b>${fill_price:.2f}</b>\n"
                            f"SL: ${sl:.2f} | TP: ${tp:.2f}\n"
                            f"<b>Bull-Case:</b> {entry.bull_case}\n"
                            f"<b>Halteziel:</b> {entry.suggested_hold_days}d"
                            + (f"\n<b>Kursziel:</b> ${entry.target_price:.2f}" if entry.target_price else "")
                        )
                    except ValueError as _ve:
                        log.warning("IBKR Fill – Portfolio-Eintrag fehlgeschlagen (%s): %s", entry.ticker, _ve)
                        notifier.send(
                            f"⚠️ IBKR Limit-Fill {entry.ticker} konnte nicht ins Portfolio eingetragen werden: {_ve}"
                        )
            except Exception as _e:
                log.warning("IBKR-Fill-Check fehlgeschlagen: %s", _e)

        schedule.every(5).minutes.do(_ibkr_fill_check_job)

    # ── SL/TP-Check alle 30 Minuten ─────────────────────────────────────────
    # ── Signal-Queue-Drain ──────────────────────────────────────────────────
    # N3-Befund: process_signal_queue() war nie verdrahtet — vorgemerkte
    # Signale ("Max Positionen erreicht") liefen deshalb immer nur ab
    # (historisch 0 von 144 ausgeführt). Jeder Eintrag wird beim Drain über
    # strategy.evaluate() komplett neu geprüft (Slots, Schwelle, Korrelation,
    # Liquidität, Sizing) — es kauft nur, was aktuell noch gültig ist.
    def _signal_queue_job():
        try:
            if signal_queue.count_pending() == 0:
                return
            from strategy.executor import process_signal_queue
            for _msg in process_signal_queue(strategy, _executor, broker, regime="NEUTRAL"):
                console.print(f"  [bold green]{_msg}[/bold green]")
        except Exception as _e:
            log.warning("Signal-Queue-Drain fehlgeschlagen: %s", _e)
    schedule.every(60).minutes.do(_signal_queue_job)

    def _sl_tp_check_job():
        try:
            positions = portfolio.all_positions()
            if not positions:
                return
            prices = broker.get_prices(list(positions.keys()))
            _closed = False
            for _res in strategy.check_exits(prices):
                _p = portfolio.get_position(_res.ticker)
                _dh = 0
                if _p:
                    try:
                        _dh = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(_p.entry_date)).days
                    except Exception:
                        _dh = 0
                _act = _executor.execute(_res, days_held=_dh)
                if _act:
                    console.print(f"  [yellow]{_act}[/yellow]")
                    if "VERKAUFT" in _act:
                        _closed = True
            if _closed:
                # Slot/Kapital frei geworden → vorgemerkte Signale sofort prüfen
                _signal_queue_job()
        except Exception as _e:
            log.warning("SL/TP-Check fehlgeschlagen: %s", _e)
    schedule.every(30).minutes.do(_sl_tp_check_job)

    # ── Positions-Aging-Check alle 4 Stunden ────────────────────────────────
    def _position_aging_job():
        """Warnt per Telegram wenn eine Position zu lange ohne Gewinn gehalten wird."""
        try:
            positions = portfolio.all_positions()
            if not positions:
                return
            prices = broker.get_prices(list(positions.keys()))
            warnings = []
            runners = []
            for ticker, pos in positions.items():
                price = prices.get(ticker, pos.entry_price)
                days_held = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                ratio = days_held / max(pos.target_hold_days, 1)
                if ratio >= 0.8 and pnl_pct < 0:
                    warnings.append(
                        f"  ⚠️ <b>{ticker}</b>: {days_held}/{pos.target_hold_days}d · "
                        f"P&L {pnl_pct:+.1f}% · Kurs ${price:.2f}"
                    )
                elif ratio >= 1.0 and pnl_pct > 0:
                    runners.append(
                        f"  🏃 <b>{ticker}</b>: {days_held}d (Ziel {pos.target_hold_days}d überschritten) · "
                        f"+{pnl_pct:.1f}% · Kurs ${price:.2f}"
                    )
            if warnings or runners:
                parts = []
                if warnings:
                    parts.append("⚠️ <b>Aging-Warnung – Positionen ohne Gewinn nahe Halteziel:</b>\n" + "\n".join(warnings))
                if runners:
                    parts.append("🏃 <b>Läufer – Haltedauer überschritten, noch im Gewinn:</b>\n" + "\n".join(runners))
                TelegramNotifier().send("\n\n".join(parts))
                for line in warnings + runners:
                    console.print(f"  {line}")
        except Exception as e:
            log.warning("Position-Aging-Job fehlgeschlagen: %s", e)

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
        _tk = list(dict.fromkeys(t for t in (tickers or []) if t))
        if not _tk:
            return
        log.info("Eskalation (%s): Fokus-Analyse %s", reason, ", ".join(_tk))
        console.print(
            f"  [bold yellow]⚡ {reason}: Fokus-Analyse {', '.join(_tk)}[/bold yellow]"
        )
        safe_run_analysis_cycle(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy, only_tickers=_tk,
        )

    # ── Headline-Signal-Scanner: stündlich ──────────────────────────────────
    _SIGNAL_TRIGGER_SCORE = 0.90   # Ab hier sofortige Analyse auslösen
    _HEADLINE_COOLDOWN_HOURS = int(os.getenv("HEADLINE_COOLDOWN_HOURS", "4"))
    _HEADLINE_COOLDOWN_FILE = os.path.join(
        os.path.dirname(__file__), "..", "data", "headline_cooldown.json"
    )

    def _load_headline_cooldown() -> dict:
        import json as _j
        try:
            with open(_HEADLINE_COOLDOWN_FILE) as _f:
                raw = _j.load(_f)
            return {k: datetime.fromisoformat(v) for k, v in raw.items()}
        except Exception:
            return {}

    def _save_headline_cooldown(cd: dict) -> None:
        import json as _j
        try:
            with open(_HEADLINE_COOLDOWN_FILE, "w") as _f:
                _j.dump({k: v.isoformat() for k, v in cd.items()}, _f)
        except Exception:
            pass

    _headline_last_queued: dict = _load_headline_cooldown()

    def _headline_scan_job():
        """
        Scannt allgemeine Börsennachrichten auf starke Signale (M&A, FDA,
        Earnings-Beats, etc.) und speist entdeckte Ticker in die BenchList.
        Sehr starke Signale (Score ≥ 0.85) → Telegram + sofortige Analyse (alle Ticker).
        Follow-Up nach der Analyse: Telegram mit Kauf-/Skip-Ergebnis.
        """
        try:
            from analyzers.headline_signal_detector import HeadlineSignalDetector
            detector = HeadlineSignalDetector()
            signals  = detector.scan()
            if signals:
                notifier = TelegramNotifier()
                # Urgent-Signale vorab bestimmen damit ihre Meldung
                # in einer einzigen kombinierten Nachricht landet
                import datetime as _dt_hl
                _hl_cutoff = datetime.now() - _dt_hl.timedelta(hours=_HEADLINE_COOLDOWN_HOURS)
                urgent = [
                    sig for sig in signals
                    if sig.score >= _SIGNAL_TRIGGER_SCORE
                    and (
                        _headline_last_queued.get(sig.ticker) is None
                        or _headline_last_queued[sig.ticker] < _hl_cutoff
                    )
                ]
                _urgent_tickers = {sig.ticker for sig in urgent}
                # Headline-Scanner-Meldung: urgent-Ticker ausschließen
                added = detector.process_signals(
                    signals,
                    notify_fn=lambda _m: _scanner_notify(notifier, _m),
                    exclude_tickers=_urgent_tickers,
                )
                if added:
                    console.print(
                        f"  [magenta]📰 Headline-Scanner: "
                        f"{len(added)} neue Kandidaten → BenchList: "
                        f"{', '.join(added[:6])}[/magenta]"
                    )
                if urgent:
                    for sig in urgent:
                        _headline_last_queued[sig.ticker] = datetime.now()
                    _save_headline_cooldown(_headline_last_queued)
                    tickers_str = ", ".join(sig.ticker for sig in urgent)
                    console.print(
                        f"  [bold yellow]⚡ Signal-Trigger ({_SIGNAL_TRIGGER_SCORE:.0%}): "
                        f"Fokus-Analyse: {tickers_str}[/bold yellow]"
                    )
                    _in_trading_hours = (
                        datetime.now().weekday() < 5
                        and 6 <= datetime.now().hour < 23
                    )
                    if _in_trading_hours:
                        # Nur die getriggerten Aktien analysieren (Fokus-Lauf),
                        # NICHT mehr den ganzen Watchlist-Zyklus. Frugal-Routing
                        # entscheidet Ollama-vs-Claude; nur handelbare Ergebnisse
                        # melden sich über den Trade-/Digest-Pfad.
                        _escalate_ticker(
                            [sig.ticker for sig in urgent], reason="Headline-Trigger"
                        )
                    else:
                        # Außerhalb der Handelszeiten: für das nächste geplante
                        # Fenster vormerken statt nachts zu analysieren.
                        from analyzers.user_request_queue import add_ticker as _req_ticker_inline
                        for sig in urgent:
                            _req_ticker_inline(sig.ticker, meta={
                                "signal_type":  sig.signal_type,
                                "score":        sig.score,
                                "headline":     getattr(sig, "headline", ""),
                                "from_headline": True,
                            })
                        _scanner_notify(
                            notifier,
                            f"⚡ <b>Signal-Trigger</b> (außerhalb Handelszeiten)\n\n"
                            + "\n".join(
                                f"  • <b>{sig.ticker}</b> – {sig.signal_type} "
                                f"(Score {sig.score:.2f})"
                                for sig in urgent
                            )
                            + "\n\n📋 In Queue gespeichert – Analyse startet mit dem nächsten Vorbörslichen Fenster.",
                        )
                        log.info(
                            "Signal-Trigger außerhalb Handelszeiten – %d Ticker in Queue für Vorbörsliche Analyse.",
                            len(urgent),
                        )
        except Exception as e:
            log.warning("Headline-Scan-Job fehlgeschlagen: %s", e)

    schedule.every(20).minutes.do(_headline_scan_job)
    import threading as _thr_stagger
    _thr_stagger.Timer(30, _headline_scan_job).start()   # 30s nach Start

    # ── Momentum/Hype-Scanner: alle 45 Minuten während Handelszeiten ─────────
    _MOMENTUM_COOLDOWN_HOURS = int(os.getenv("MOMENTUM_COOLDOWN_HOURS", "8"))
    _MOMENTUM_COOLDOWN_FILE  = os.path.join(
        os.path.dirname(__file__), "..", "data", "momentum_cooldown.json"
    )

    def _load_momentum_cooldown() -> dict:
        import json as _j
        try:
            with open(_MOMENTUM_COOLDOWN_FILE) as _f:
                raw = _j.load(_f)
            return {k: datetime.fromisoformat(v) for k, v in raw.items()}
        except Exception:
            return {}

    def _save_momentum_cooldown(cd: dict) -> None:
        import json as _j
        try:
            with open(_MOMENTUM_COOLDOWN_FILE, "w") as _f:
                _j.dump({k: v.isoformat() for k, v in cd.items()}, _f)
        except Exception:
            pass

    _momentum_last_queued: dict = _load_momentum_cooldown()

    def _momentum_scan_job():
        """
        Scannt das Universum auf Aktien mit ungewöhnlichem Kaufdruck
        (Volumen ≥ 2× Schnitt UND Kurs ≥ +2%). Wer gerade gehyped wird,
        kommt sofort in die Analyse-Queue und löst eine Sofort-Analyse aus.
        Läuft nur an Handelstagen 08:00–22:00 Lokalzeit.
        Cooldown: Dieselbe Aktie wird frühestens nach MOMENTUM_COOLDOWN_HOURS (8h)
        erneut analysiert — effektiv einmal pro Handelstag.
        """
        import datetime as _dt
        try:
            local_now = datetime.now()
            if local_now.weekday() >= 5 or not (8 <= local_now.hour < 22):
                return
            from analyzers.watchlist_scanner import WatchlistScanner
            from analyzers.user_request_queue import add_ticker as _req_ticker
            scanner = WatchlistScanner(
                min_volume_ratio=2.0,
                min_price_change_pct=2.0,
                max_picks=5,
            )
            exclude = list(portfolio.all_positions().keys())
            hits = scanner.scan(exclude=exclude)
            if not hits:
                return

            # Cooldown-Filter: Ticker die in den letzten N Stunden bereits analysiert wurden
            cutoff = local_now - _dt.timedelta(hours=_MOMENTUM_COOLDOWN_HOURS)
            today_str = local_now.date().isoformat()

            # Zusätzlich: Ticker aus dem Analysis-Cache prüfen (heute bereits analysiert?)
            _analyzed_today: set = set()
            try:
                import json as _json
                _cache_path = os.path.join(os.path.dirname(__file__), "..", "data", "analysis_cache.json")
                with open(_cache_path) as _cf:
                    _cache_data = _json.load(_cf)
                for _t, _d in _cache_data.items():
                    if isinstance(_d, dict) and (_d.get("updated_at") or "").startswith(today_str):
                        _analyzed_today.add(_t.upper())
            except Exception:
                pass

            new_hits = [
                h for h in hits
                if h["ticker"].upper() not in _analyzed_today
                and (
                    _momentum_last_queued.get(h["ticker"]) is None
                    or _momentum_last_queued[h["ticker"]] < cutoff
                )
            ]
            if not new_hits:
                skipped = [h["ticker"] for h in hits]
                log.debug(
                    "Momentum-Scanner: alle Kandidaten im Cooldown oder heute analysiert: %s",
                    skipped,
                )
                return

            notifier = TelegramNotifier()
            for h in new_hits:
                _req_ticker(h["ticker"], meta={
                    "signal_type":   "MOMENTUM",
                    "score":         min(0.95, 0.70 + h["volume_ratio"] * 0.05),
                    "headline":      f"Vol ×{h['volume_ratio']:.1f}, +{h['change_pct']:.1f}%",
                    "from_headline": False,
                    "momentum":      True,
                })
                _momentum_last_queued[h["ticker"]] = local_now
            _save_momentum_cooldown(_momentum_last_queued)

            msg = "\n".join(
                f"  • <b>{h['ticker']}</b> +{h['change_pct']:.1f}% | "
                f"Vol ×{h['volume_ratio']:.1f} | {h['streak_days']}d↑"
                for h in new_hits
            )
            _scanner_notify(
                notifier,
                f"📈 <b>Momentum-Scanner</b>\n\n{msg}\n\n"
                f"🔍 Sofort-Analyse gestartet."
            )
            console.print(
                f"  [bold green]📈 Momentum-Scanner: "
                f"{len(new_hits)} Hype-Kandidaten → "
                f"{', '.join(h['ticker'] for h in new_hits)}[/bold green]"
            )
            safe_run_analysis_cycle(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
        except Exception as e:
            log.warning("Momentum-Scan-Job fehlgeschlagen: %s", e)

    schedule.every(45).minutes.do(_momentum_scan_job)
    _thr_stagger.Timer(60, _momentum_scan_job).start()   # 60s nach Start

    # ── Breakout-Watch-Scanner: alle 30 Minuten ──────────────────────────────
    _BREAKOUT_COOLDOWN_HOURS = int(os.getenv("BREAKOUT_COOLDOWN_HOURS", "12"))
    _breakout_last_queued: dict = {}   # ticker → datetime

    def _breakout_watch_job():
        """
        Prädiktiver Scanner: erkennt Breakout-Setups BEVOR der Kurs steigt.
        Signale: volume_buildup, bb_squeeze, resistance_obv.
        Läuft an Handelstagen 07:30–21:00 (breiter als Momentum, erfasst Pre-Market).
        Cooldown: 12h pro Ticker (einmal pro Tag reicht).
        """
        import datetime as _dt
        try:
            local_now = datetime.now()
            if local_now.weekday() >= 5 or not (7 <= local_now.hour < 21):
                return
            from analyzers.watchlist_scanner import WatchlistScanner
            from analyzers.user_request_queue import add_ticker as _req_ticker

            scanner = WatchlistScanner(max_picks=6)
            exclude = list(portfolio.all_positions().keys())
            hits = scanner.scan(exclude=exclude)
            if not hits:
                return

            cutoff = local_now - _dt.timedelta(hours=_BREAKOUT_COOLDOWN_HOURS)
            new_hits = [
                h for h in hits
                if (
                    _breakout_last_queued.get(h["ticker"]) is None
                    or _breakout_last_queued[h["ticker"]] < cutoff
                )
            ]
            if not new_hits:
                return

            _SIGNAL_LABELS = {
                "volume_buildup": "Vol↑ Akkumulation",
                "bb_squeeze":     "BB-Squeeze",
                "resistance_obv": "Widerstand+OBV",
            }

            notifier = TelegramNotifier()
            for h in new_hits:
                sig_label = " + ".join(_SIGNAL_LABELS.get(s, s) for s in h["signals"])
                _req_ticker(h["ticker"], meta={
                    "signal_type":   "BREAKOUT_WATCH",
                    "score":         0.60 + h["setup_score"] * 0.08,
                    "headline":      sig_label,
                    "from_headline": False,
                    "momentum":      False,
                    "breakout_watch": True,
                })
                _breakout_last_queued[h["ticker"]] = local_now

            msg_lines = []
            for h in new_hits:
                sig_label = " + ".join(_SIGNAL_LABELS.get(s, s) for s in h["signals"])
                dist = f" | {h['dist_52w_pct']:.1f}% u. 52W-Hoch" if h.get("dist_52w_pct") is not None else ""
                msg_lines.append(f"  • <b>{h['ticker']}</b> ${h['price']:.2f} | {sig_label}{dist}")

            _scanner_notify(
                notifier,
                f"🎯 <b>Breakout-Watch</b> – Setup erkannt (kein Kursanstieg nötig)\n\n"
                + "\n".join(msg_lines)
                + "\n\n🔍 Analyse vorgemerkt."
            )
            log.info(
                "Breakout-Watch: %d Kandidaten → %s",
                len(new_hits), [h["ticker"] for h in new_hits],
            )
            safe_run_analysis_cycle(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
        except Exception as e:
            log.warning("Breakout-Watch-Job fehlgeschlagen: %s", e)

    schedule.every(30).minutes.do(_breakout_watch_job)

    # ── Reddit-Hype-Scanner: alle 3 Stunden ─────────────────────────────────
    def _reddit_hype_job():
        """
        Scannt Reddit (wallstreetbets, stocks, investing …) auf organisch
        trending Ticker — unabhängig von der Watchlist.
        Tickers mit steigender Erwähnungsfrequenz (velocity > 1.5) kommen
        direkt in die Analyse-Queue.
        """
        try:
            from collectors.reddit_hype_scanner import RedditHypeScanner
            from analyzers.user_request_queue import add_ticker as _req_ticker

            log.info("Reddit-Hype-Scanner gestartet …")
            hits = RedditHypeScanner().scan(top_n=10, min_mentions=3)
            if not hits:
                return

            exclude   = set(portfolio.all_positions().keys())
            watchlist = set(getattr(config, "watchlist", []))
            notifier  = TelegramNotifier()

            queued = []
            for h in hits:
                # Skip existing positions and tickers already on watchlist
                # (watchlist is handled by existing scanners)
                if h.ticker in exclude:
                    continue
                if h.velocity < 1.5 and h.mentions < 5:
                    continue
                _req_ticker(h.ticker, meta={
                    "signal_type":   "REDDIT_HYPE",
                    "score":         min(0.90, 0.55 + min(h.velocity, 5.0) * 0.07),
                    "headline":      (
                        f"Reddit: {h.mentions}× erwähnt | "
                        f"Velocity ×{h.velocity:.1f} | "
                        f"r/{', r/'.join(h.subreddits[:2])}"
                    ),
                    "from_headline": False,
                    "reddit_hype":   True,
                })
                queued.append(h)

            if not queued:
                return

            lines = "\n".join(
                f"  • <b>{h.ticker}</b> – {h.mentions}× | "
                f"Velocity ×{h.velocity:.1f} | "
                f"{h.sample_titles[0][:60] if h.sample_titles else ''}"
                for h in queued
            )
            _scanner_notify(
                notifier,
                f"🔥 <b>Reddit-Hype-Scanner</b>\n\n{lines}\n\n"
                f"🔍 Analyse wird gestartet …"
            )
            console.print(
                f"  [bold magenta]🔥 Reddit-Hype: "
                f"{', '.join(h.ticker for h in queued)} "
                f"→ Analyse-Queue[/bold magenta]"
            )
            safe_run_analysis_cycle(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
        except Exception as e:
            log.warning("Reddit-Hype-Job fehlgeschlagen: %s", e)

    schedule.every(3).hours.do(_reddit_hype_job)
    _thr_stagger.Timer(90, _reddit_hype_job).start()   # 90s nach Start

    # ── Kursbewegungs-Alarm: alle 5 Minuten während Handelszeiten ───────────
    _price_move_last: dict = {}   # ticker → letzter bekannter Kurs

    def _price_move_job():
        """
        Prüft alle 5 Min ob eine Watchlist-Aktie um ≥ PRICE_MOVE_THRESHOLD
        gestiegen/gefallen ist. Bei Ausschlag: sofortige Claude-Analyse +
        Telegram-Alert. Läuft nur an Handelstagen während der Kernzeiten.
        """
        _MOVE_THRESHOLD = float(os.getenv("PRICE_MOVE_THRESHOLD", "0.02"))  # 2%
        try:
            local_now = datetime.now()
            # Nur wochentags zwischen 08:00 und 22:00 Lokalzeit prüfen
            if local_now.weekday() >= 5 or not (8 <= local_now.hour < 22):
                return
            watchlist = list(config.watchlist)
            if not watchlist:
                return
            prices = broker.get_prices(watchlist)
            triggered = []
            for ticker, price in prices.items():
                if not price or price <= 0:
                    continue
                last = _price_move_last.get(ticker)
                if last and last > 0:
                    move = (price - last) / last
                    if abs(move) >= _MOVE_THRESHOLD:
                        direction = "📈" if move > 0 else "📉"
                        triggered.append((ticker, price, last, move, direction))
                _price_move_last[ticker] = price
            if not triggered:
                return
            notifier = TelegramNotifier()
            from analyzers.user_request_queue import add_ticker as _add_req
            # Alle ausgelösten Ticker in EINER Sammelnachricht bündeln (statt einer
            # Einzelnachricht pro Aktie – das war die Telegram-Flut). Queuing bleibt pro Ticker.
            _alert_lines = []
            for ticker, price, last_p, move, icon in triggered:
                console.print(
                    f"  [bold {'green' if move > 0 else 'red'}]"
                    f"{icon} Kursalarm {ticker}: {move:+.1%} "
                    f"(${last_p:.2f} → ${price:.2f})[/bold {'green' if move > 0 else 'red'}]"
                )
                # Options-Flow als Bestätigung prüfen
                options_note = ""
                try:
                    from collectors.options_flow_collector import OptionsFlowCollector
                    flow = OptionsFlowCollector().collect(ticker)
                    bullish = [f for f in flow if f.get("signal") == "BULLISCH"]
                    bearish = [f for f in flow if f.get("signal") == "BÄRISCH"]
                    if bullish:
                        options_note = "  📊 Flow bestätigt"
                    elif bearish:
                        options_note = "  📊 Flow warnt"
                except Exception:
                    pass
                _alert_lines.append(
                    f"{icon} <b>{ticker}</b>  <b>{move:+.1%}</b>  "
                    f"${last_p:.2f} → ${price:.2f}{options_note}"
                )
                _add_req(ticker, meta={
                    "signal_type":   "PRICE_MOVE",
                    "score":         min(0.95, 0.70 + abs(move) * 5),
                    "headline":      f"{icon} {ticker} {move:+.1%} in 5 Min",
                    "from_headline": True,
                    "move_pct":      round(move * 100, 2),
                })
                log.info("Kursalarm %s: %+.1f%% → Sofort-Analyse ausgelöst", ticker, move * 100)
            _scanner_notify(
                notifier,
                f"⚡ <b>Kursalarm</b> ({len(triggered)} Titel · 5 Min)\n"
                f"━━━━━━━━━━━━━━\n"
                + "\n".join(_alert_lines)
                + "\n\n🔍 Vollanalyse läuft – Ergebnis folgt in wenigen Minuten."
            )
        except Exception as e:
            log.debug("Kursbewegungs-Job fehlgeschlagen: %s", e)

    schedule.every(5).minutes.do(_price_move_job)

    # ── Options-Flow-Scan: stündlich für Watchlist ───────────────────────────
    def _options_flow_job():
        """
        Scannt Options-Flow der gesamten Watchlist auf ungewöhnliche
        Call/Put-Aktivität. C/P-Ratio ≥ 3 oder P/C-Ratio ≥ 3 → Sofort-Analyse.
        """
        _OPT_RATIO = float(os.getenv("OPTIONS_FLOW_RATIO", "3.0"))
        try:
            local_now = datetime.now()
            if local_now.weekday() >= 5 or not (14 <= local_now.hour < 21):
                return   # Nur während NYSE-Handelszeiten sinnvoll
            from collectors.options_flow_collector import OptionsFlowCollector
            from analyzers.user_request_queue import add_ticker as _add_req
            collector = OptionsFlowCollector(min_volume_ratio=_OPT_RATIO)
            notifier = TelegramNotifier()
            # Treffer sammeln und in EINER Nachricht bündeln (kein Spam pro Ticker).
            _flow_hits = []
            for ticker in config.watchlist:
                try:
                    signals = collector.collect(ticker)
                    bullish = [s for s in signals if s.get("signal") == "BULLISCH"]
                    if not bullish:
                        continue
                    headline = bullish[0]["title"]
                    console.print(f"  [cyan]📊 Options-Flow: {headline}[/cyan]")
                    _add_req(ticker, meta={
                        "signal_type":   "OPTIONS_FLOW",
                        "score":         0.80,
                        "headline":      headline,
                        "from_headline": True,
                    })
                    _flow_hits.append((ticker, headline))
                except Exception:
                    continue
            if _flow_hits:
                _scanner_notify(
                    notifier,
                    f"📊 <b>Options-Flow Signale</b> ({len(_flow_hits)} Titel)\n"
                    f"━━━━━━━━━━━━━━\n"
                    + "\n".join(f"• <b>{t}</b> – {h}" for t, h in _flow_hits)
                    + "\n\n🔍 Analyse läuft – Ergebnis folgt in wenigen Minuten."
                )
        except Exception as e:
            log.debug("Options-Flow-Job fehlgeschlagen: %s", e)

    schedule.every().hour.do(_options_flow_job)

    # ── Geopolitischer Radar: alle 2 Stunden ────────────────────────────────
    def _geopolitical_radar_job():
        """
        Scannt Weltpolitik-Feeds auf geopolitische Frühsignale und
        leitet Marktauswirkungen ab (Rüstung, Öl, Safe-Haven, etc.).
        Severity 2+ → sofortiger Telegram-Alert.
        Severity 3  → kritischer Alert + Sofort-Analyse für Watchlist-Ticker.
        """
        try:
            from analyzers.geopolitical_radar import GeopoliticalRadar
            radar  = GeopoliticalRadar()
            events = radar.scan()
            if events:
                notifier = TelegramNotifier()
                added = radar.process_events(
                    events,
                    notify_fn=lambda _m: _scanner_notify(notifier, _m),
                )
                if added:
                    console.print(
                        f"  [bold red]🌍 Geo-Radar: {len(events)} Event(s) – "
                        f"Ticker → BenchList: {', '.join(added[:8])}[/bold red]"
                    )
                # Severity-3 Ereignisse: Watchlist-Ticker sofort analysieren
                watchlist_set = {t.upper() for t in config.watchlist}
                geo_urgent: list = []
                for ev in events:
                    if ev.severity == 3:
                        for impact in ev.impacts:
                            for t in impact.tickers:
                                if t.upper() in watchlist_set and t not in geo_urgent:
                                    geo_urgent.append(t)
                if geo_urgent:
                    from analyzers.user_request_queue import add_ticker as _req_ticker_inline
                    for t in geo_urgent:
                        _req_ticker_inline(t)
                    console.print(
                        f"  [bold red]🌍 Geo-Severity-3: Sofort-Analyse: "
                        f"{', '.join(geo_urgent)}[/bold red]"
                    )
        except Exception as e:
            log.warning("Geopolitical-Radar-Job fehlgeschlagen: %s", e)

    schedule.every(2).hours.do(_geopolitical_radar_job)
    _thr_stagger.Timer(120, _geopolitical_radar_job).start()   # 120s nach Start

    # ── Marktbreite-Check: Vorsichts-Modus bei breitem Sektor-Einbruch ───────
    # Gemessen über die 11 Sektor-ETFs (XLK/XLF/XLE/…) statt der 8-Titel-
    # Watchlist: die Watchlist ist fast nur Mega-Cap-Tech und hochkorreliert –
    # „62% gefallen" hieß dort oft nur „Tech-Rottag", keine echte Marktbreite.
    # Sektor-ETFs sind das saubere Breadth-Maß (ein Index je Sektor). Damit
    # können einzelne event-getriebene Aktien (FDA/Earnings) den Breadth-Wert
    # nicht mehr verzerren – das ursprüngliche „nicht wegen FDA-Dip sperren"
    # ist hier strukturell gelöst. SPY bestätigt zusätzlich die Magnitude.
    from analyzers.recession_detector import SECTOR_ETFS as _SECTOR_ETFS
    _BREADTH_CRASH_PCT  = 0.60   # ≥60% der Sektoren rot = ≥7 von 11 → Crash-Verdacht
    _BREADTH_MIN_SAMPLE = 6      # mind. so viele ETFs müssen Daten liefern
    _SPY_CONFIRM_PCT    = -1.0   # SPY muss ≥1% fallen für „echten" Breitmarkt-Einbruch

    def _market_breadth_job():
        """Aktiviert Vorsichts-Modus bei breitem Sektor-Einbruch. Fallen ≥60% der
        Sektoren UND bestätigt SPY (≤ -1%) → 24h-Vorsicht (echter Einbruch).
        Sektoren breit rot, aber SPY flach → 12h-Beobachtung, kein harter Stopp."""
        try:
            if datetime.now(timezone.utc).replace(tzinfo=None).weekday() >= 5:
                return
            sectors = list(_SECTOR_ETFS)
            import yfinance as _yf
            dl_tickers = sectors + ["SPY"]
            hist = _yf.download(dl_tickers, period="2d", auto_adjust=True, progress=False, threads=False)
            closes = hist.get("Close") if hasattr(hist, "get") else hist["Close"]
            if closes is None or closes.shape[0] < 2:
                return
            prev_row  = closes.iloc[-2]
            curr_row  = closes.iloc[-1]

            def _chg(t):
                try:
                    prev = float(prev_row[t]) if t in prev_row else None
                    curr = float(curr_row[t]) if t in curr_row else None
                    if prev and curr:
                        return (curr - prev) / prev * 100.0
                except Exception:
                    pass
                return None

            spy_change = _chg("SPY")
            checked, down_sectors = 0, []
            for t in sectors:
                c = _chg(t)
                if c is None:
                    continue
                checked += 1
                if c < 0:
                    down_sectors.append(t)
            if checked < _BREADTH_MIN_SAMPLE:
                return

            pct_down = len(down_sectors) / checked

            if pct_down < _BREADTH_CRASH_PCT:
                # Markt hat sich erholt → aktiven Vorsichts-Modus aufheben
                if get_market_caution() and clear_market_caution():
                    log.info("Marktbreite erholt (%d%% Sektoren rot) – Vorsichts-Modus aufgehoben.", int(pct_down*100))
                return

            spy_str = f"SPY {spy_change:+.1f}%" if spy_change is not None else "SPY n/v"
            spy_confirms = (spy_change is None) or (spy_change <= _SPY_CONFIRM_PCT)

            if spy_confirms:
                reason = (
                    f"Breiter Markteinbruch: {int(pct_down*100)}% der Sektoren gefallen "
                    f"({len(down_sectors)}/{checked}, {spy_str})"
                )
                newly = set_market_caution(reason, pct_down, hours=24)
                log.warning("Marktbreite-Vorsicht (Crash) aktiviert: %s", reason)
                if newly:
                    TelegramNotifier().send(
                        f"⚠️ <b>Vorsichts-Modus aktiviert (24h)</b>\n\n"
                        f"{reason}\n\n"
                        f"Positionsgröße reduziert, Kaufhürde angehoben – "
                        f"nur noch hohe Konviktion. Kein harter Stopp."
                    )
            else:
                # Sektoren breit rot, aber SPY flach → kein echter Einbruch, beobachten
                reason = (
                    f"{int(pct_down*100)}% der Sektoren gefallen ({len(down_sectors)}/{checked}), "
                    f"aber {spy_str} – kein Breitmarkt-Einbruch, nur Beobachtung."
                )
                newly = set_market_caution(reason, pct_down, hours=12)
                log.info("Marktbreite: Sektoren rot, SPY flach – beobachten statt Stopp: %s", reason)
                if newly:
                    TelegramNotifier().send(
                        f"👀 <b>Beobachtungs-Modus (12h)</b>\n\n"
                        f"{reason}\n\n"
                        f"Leicht vorsichtiger (kleinere Positionen), aber kein Handelsstopp."
                    )
        except Exception as _e:
            log.warning("Marktbreite-Job fehlgeschlagen: %s", _e)

    schedule.every(2).hours.do(_market_breadth_job)

    # ── Markt-Lagebericht: täglich 08:30 UTC + Cache-Refresh alle 4h ─────────
    def _market_overview_refresh_job():
        """Aktualisiert den Market-Overview-Cache (SPY-Trend, Put/Call, VIX, etc.)."""
        try:
            from analyzers.market_overview import MarketOverview
            MarketOverview().full_assessment()
            log.debug("Market-Overview-Cache aktualisiert.")
        except Exception as _e:
            log.warning("Market-Overview-Refresh fehlgeschlagen: %s", _e)

    _lagebericht_sent_date: list = [""]   # [0] = ISO-Datum des letzten Sendens

    def _morning_lagebericht_job():
        """Sendet täglich um 08:30 UTC einen strukturierten Markt-Lagebericht via Telegram."""
        _today = datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()
        if _lagebericht_sent_date[0] == _today:
            log.debug("Morgen-Lagebericht heute bereits gesendet – übersprungen.")
            return
        try:
            from analyzers.market_overview import MarketOverview
            overview = MarketOverview()
            overview.full_assessment()  # Cache auffrischen
            msg = overview.format_telegram()
            TelegramNotifier().send(msg)
            _lagebericht_sent_date[0] = _today
            log.info("Morgen-Lagebericht gesendet.")
        except Exception as _e:
            log.warning("Morgen-Lagebericht fehlgeschlagen: %s", _e)

    schedule.every(4).hours.do(_market_overview_refresh_job)
    schedule.every().day.at("08:30").do(_morning_lagebericht_job)
    _thr_stagger.Timer(5, _market_overview_refresh_job).start()   # 5s nach Start (Cache für andere Jobs)

    # ── PEAD-Scanner: täglich morgens + stündlich während Handelszeiten ──────
    def _pead_scan_job():
        """
        Post-Earnings Drift: Aktien mit starkem Earnings-Beat (≥5%) werden
        24-48h nach dem Report analysiert — statistischer Drift-Vorteil.
        Läuft nur an Wochentagen.
        """
        try:
            local_now = datetime.now()
            if local_now.weekday() >= 5:
                return
            from analyzers.pead_tracker import PEADTracker
            from analyzers.user_request_queue import add_ticker as _req_ticker
            from bot.runner import _get_watchlist
            watchlist, _ = _get_watchlist(portfolio)
            tracker_pead = PEADTracker()
            tracker_pead.scan_watchlist(watchlist)  # Neue Beats registrieren
            ready = tracker_pead.get_ready_for_analysis()
            if not ready:
                return
            notifier = TelegramNotifier()
            queued_tickers = []
            for entry in ready:
                t = entry["ticker"]
                _req_ticker(t, meta={
                    "signal_type":   "PEAD",
                    "score":         min(0.90, 0.70 + entry.get("surprise_pct", 0.05) * 2),
                    "headline":      f"PEAD: {entry.get('label','BEAT')} {entry.get('surprise_pct',0)*100:+.1f}% EPS-Surprise",
                    "from_headline": False,
                    "pead":          True,
                })
                tracker_pead.mark_queued(t)
                queued_tickers.append(t)
            tracker_pead.cleanup_expired()
            if queued_tickers:
                msg = "\n".join(f"  • <b>{t}</b>" for t in queued_tickers)
                _scanner_notify(
                    notifier,
                    f"📈 <b>PEAD-Scanner</b>\n\n{msg}\n\n"
                    f"Earnings-Beat erkannt → Post-Drift-Analyse gestartet."
                )
                console.print(
                    f"  [bold green]📈 PEAD: {', '.join(queued_tickers)} → Queue[/bold green]"
                )
                safe_run_analysis_cycle(
                    portfolio, broker, strategy, tracker, phase_ctrl,
                    archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                    earnings_strategy,
                )
        except Exception as e:
            log.warning("PEAD-Scan-Job fehlgeschlagen: %s", e)

    schedule.every().hour.do(_pead_scan_job)

    # ── Short-Squeeze-Scanner: alle 4 Stunden während Handelszeiten ──────────
    def _short_squeeze_scan_job():
        """
        Scannt Watchlist auf Short-Squeeze-Setups: Short-Interest >15% + positiver Trend.
        Ticker mit Squeeze-Potenzial kommen in die Analyse-Queue.
        Läuft nur an Handelstagen.
        """
        try:
            local_now = datetime.now()
            if local_now.weekday() >= 5 or not (8 <= local_now.hour < 22):
                return
            from analyzers.short_squeeze_detector import ShortSqueezeDetector
            from analyzers.user_request_queue import add_ticker as _req_ticker
            detector = ShortSqueezeDetector()
            hits = detector.scan_watchlist(list(config.watchlist))
            if not hits:
                return
            notifier = TelegramNotifier()
            for h in hits:
                t = h["ticker"]
                signal_item = detector.build_signal_item(t, h)
                _req_ticker(t, meta={
                    "signal_type":   "SHORT_SQUEEZE",
                    "score":         min(0.88, 0.65 + h.get("squeeze_score", 0.2)),
                    "headline":      signal_item["title"],
                    "from_headline": False,
                    "squeeze_setup": True,
                })
            tickers_str = ", ".join(h["ticker"] for h in hits)
            _scanner_notify(
                notifier,
                f"🎯 <b>Short-Squeeze-Scanner</b>\n\n"
                + "\n".join(
                    f"  • <b>{h['ticker']}</b> – SI {h.get('si_pct',0):.1f}% | "
                    f"DtC {h.get('days_to_cover',0):.1f}"
                    for h in hits
                )
                + f"\n\n🔍 Analyse gestartet."
            )
            console.print(
                f"  [bold yellow]🎯 Short-Squeeze: {tickers_str} → Queue[/bold yellow]"
            )
            safe_run_analysis_cycle(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
        except Exception as e:
            log.warning("Short-Squeeze-Scan-Job fehlgeschlagen: %s", e)

    schedule.every(4).hours.do(_short_squeeze_scan_job)

    # ── Insider-Proaktiv-Scanner: täglich + nach Marktöffnung ───────────────
    _INSIDER_COOLDOWN_HOURS = int(os.getenv("INSIDER_COOLDOWN_HOURS", "24"))
    _insider_last_queued: dict = {}   # ticker → datetime

    def _insider_proactive_job():
        """
        Scannt alle Watchlist-Ticker auf frische Form-4-Insider-Käufe
        (letzte 3 Tage). STRONG_BUY → sofortige Analyse.
        Läuft einmal täglich morgens + einmal mittags.
        """
        import datetime as _dt
        try:
            local_now = datetime.now()
            if local_now.weekday() >= 5:
                return
            from analyzers.insider_signal import get_insider_score
            from analyzers.user_request_queue import add_ticker as _req_ticker
            from bot.runner import _get_watchlist

            watchlist, _ = _get_watchlist(portfolio)
            cutoff = local_now - _dt.timedelta(hours=_INSIDER_COOLDOWN_HOURS)
            hits = []
            for ticker in watchlist:
                if (
                    _insider_last_queued.get(ticker) is not None
                    and _insider_last_queued[ticker] >= cutoff
                ):
                    continue
                try:
                    score = get_insider_score(ticker, lookback_days=3)
                    if score.signal == "STRONG_BUY":
                        hits.append((ticker, score))
                        _insider_last_queued[ticker] = local_now
                except Exception:
                    continue

            if not hits:
                return

            notifier = TelegramNotifier()
            for ticker, score in hits:
                _req_ticker(ticker, meta={
                    "signal_type":   "INSIDER_BUY",
                    "score":         0.82,
                    "headline":      score.message if hasattr(score, "message") else f"Insider STRONG_BUY ({score.bullish_count} Käufe)",
                    "from_headline": False,
                    "insider_buy":   True,
                })

            msg = "\n".join(
                f"  • <b>{t}</b> – {s.bullish_count} Insider-Käufe (Score {s.score:.1f})"
                for t, s in hits
            )
            _scanner_notify(
                notifier,
                f"🏦 <b>Insider-Scanner</b> – Frische Form-4-Käufe\n\n{msg}\n\n"
                f"🔍 Sofort-Analyse gestartet."
            )
            log.info("Insider-Proaktiv: %d Kandidaten → %s", len(hits), [t for t, _ in hits])
            safe_run_analysis_cycle(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
        except Exception as e:
            log.warning("Insider-Proaktiv-Job fehlgeschlagen: %s", e)

    schedule.every().day.at("08:30").do(_insider_proactive_job)
    schedule.every().day.at("13:00").do(_insider_proactive_job)

    # ── Sektor-Kaskaden-Scanner: alle 60 Minuten ─────────────────────────────
    _CASCADE_COOLDOWN_HOURS = int(os.getenv("CASCADE_COOLDOWN_HOURS", "6"))
    _cascade_last_queued: dict = {}   # ticker → datetime

    def _sector_cascade_job():
        """
        Wenn ein Sektor-ETF (XLK, XLF, etc.) stark bewegt (+/- ≥ 1.5%),
        werden die Top-Aktien desselben Sektors aus der Watchlist
        sofort zur Analyse vorgemerkt (Kaskaden-Effekt).
        Läuft nur an Handelstagen 09:00–20:00.
        """
        import datetime as _dt
        try:
            local_now = datetime.now()
            if local_now.weekday() >= 5 or not (9 <= local_now.hour < 20):
                return

            import yfinance as yf
            from analyzers.user_request_queue import add_ticker as _req_ticker
            from bot.runner import _get_watchlist

            _SECTOR_ETFS = {
                "XLK": "Technologie", "XLF": "Finanzen", "XLE": "Energie",
                "XLV": "Gesundheit",  "XLI": "Industrie", "XLY": "Konsum",
                "XLC": "Kommunikation",
            }
            _TICKER_SECTOR = {
                "AAPL":"XLK","MSFT":"XLK","NVDA":"XLK","AMD":"XLK","INTC":"XLK","QCOM":"XLK",
                "GOOGL":"XLC","META":"XLC","NFLX":"XLC","DIS":"XLC",
                "AMZN":"XLY","TSLA":"XLY","NKE":"XLY","MCD":"XLY","SBUX":"XLY",
                "JPM":"XLF","BAC":"XLF","GS":"XLF","MS":"XLF","V":"XLF","MA":"XLF","AXP":"XLF",
                "XOM":"XLE","CVX":"XLE","COP":"XLE",
                "JNJ":"XLV","PFE":"XLV","MRK":"XLV","ABBV":"XLV","LLY":"XLV","UNH":"XLV",
                "CAT":"XLI","BA":"XLI","GE":"XLI","RTX":"XLI","HON":"XLI",
            }

            _MIN_SECTOR_MOVE = float(os.getenv("CASCADE_MIN_SECTOR_MOVE", "1.5"))

            # Sektor-ETFs auf Tagesbewegung prüfen
            moving_sectors: list = []
            for etf, name in _SECTOR_ETFS.items():
                try:
                    hist = yf.Ticker(etf).history(period="2d")
                    if len(hist) < 2:
                        continue
                    move = (float(hist["Close"].iloc[-1]) / float(hist["Close"].iloc[-2]) - 1) * 100
                    if abs(move) >= _MIN_SECTOR_MOVE:
                        moving_sectors.append((etf, name, move))
                except Exception:
                    continue

            if not moving_sectors:
                return

            watchlist = set(_get_watchlist(portfolio)[0])
            cutoff = local_now - _dt.timedelta(hours=_CASCADE_COOLDOWN_HOURS)
            notifier = TelegramNotifier()
            all_queued = []

            for etf, sector_name, move in moving_sectors:
                # Finde Watchlist-Ticker die zu diesem Sektor gehören
                siblings = [
                    t for t, s in _TICKER_SECTOR.items()
                    if s == etf and t in watchlist
                    and (
                        _cascade_last_queued.get(t) is None
                        or _cascade_last_queued[t] < cutoff
                    )
                ][:5]  # max 5 pro Sektor

                if not siblings:
                    continue

                direction = "steigt" if move > 0 else "fällt"
                for t in siblings:
                    _req_ticker(t, meta={
                        "signal_type":    "SECTOR_CASCADE",
                        "score":          0.65,
                        "headline":       f"{sector_name} {direction} {move:+.1f}% → Sektor-Kaskade",
                        "from_headline":  False,
                        "cascade_sector": etf,
                    })
                    _cascade_last_queued[t] = local_now
                    all_queued.append(t)

                icon = "📈" if move > 0 else "📉"
                _scanner_notify(
                    notifier,
                    f"{icon} <b>Sektor-Kaskade: {sector_name} {move:+.1f}%</b>\n\n"
                    f"Verwandte Aktien analysiert: {', '.join(siblings)}"
                )

            if all_queued:
                log.info("Sektor-Kaskade: %d Ticker → Queue: %s", len(all_queued), all_queued)
                safe_run_analysis_cycle(
                    portfolio, broker, strategy, tracker, phase_ctrl,
                    archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                    earnings_strategy,
                )
        except Exception as e:
            log.warning("Sektor-Kaskaden-Job fehlgeschlagen: %s", e)

    schedule.every(60).minutes.do(_sector_cascade_job)

    # ── Intraday-Scan: optionales drittes Analysefenster ────────────────────
    if config.intraday_scan_enabled:
        def _intraday_scan_job():
            """
            Drittes Analysefenster (US-Session) – scannt Watchlist + BenchList
            auf Intraday-Setups. Läuft nur an Handelstagen.
            """
            local_date = datetime.now().date()
            if local_date.weekday() >= 5:
                return
            console.rule(
                f"[bold cyan]Intraday-Scan – {datetime.now().strftime('%H:%M')}[/bold cyan]"
            )
            try:
                safe_run_analysis_cycle(
                    portfolio, broker, strategy, tracker, phase_ctrl,
                    archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                    earnings_strategy,
                )
            except Exception as e:
                log.warning("Intraday-Scan-Job fehlgeschlagen: %s", e)

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
