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
from portfolio.circuit_breaker import CircuitBreaker
from analyzers.reflection_engine import ReflectionEngine
from analyzers.regime_adaptive import invalidate_cache_if_crash, get_last_cached_regime
from collectors.news_archive import NewsArchive
from analyzers.market_schedule import MarketSchedule
from analyzers.weekend_prep import WeekendPrep
from analyzers.parameter_optimizer import ParameterOptimizer, _MIN_TRADES
from analyzers.turbo_learner import TurboLearner
from bot.pre_market_scanner import PreMarketScanner
from bot.runner import run_analysis_cycle, _print_portfolio_summary
from cli.commands import run_weekend_prep

console = Console()
log = get_logger(__name__)


def _subtract_minutes(hhmm: str, minutes: int) -> str:
    """Zieht N Minuten von einem HH:MM String ab. Wrap-around über Mitternacht wird verhindert (auf 00:00 begrenzt)."""
    h, m = map(int, hhmm.split(":"))
    total = max(0, h * 60 + m - minutes)
    return f"{total // 60:02d}:{total % 60:02d}"


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
) -> None:
    """Main bot event loop – sets up schedule and runs until Ctrl+C."""

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

    def _monthly_review_check():
        if datetime.utcnow().day == 1:
            console.print("[bold magenta]📋 Erstelle monatliche Selbsteinschätzung...[/bold magenta]")
            content = reflection.generate_monthly_review()
            if content:
                console.print(Panel(content[:800] + "...", title="Monatsreview erstellt", border_style="magenta"))

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
            # Volle Analyse 30 Min vor Open (bisherig)
            job = schedule.every().day.at(slot["hhmm"]).do(
                run_analysis_cycle,
                portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                weekend_prep_inst, hedge_strategy_inst,
            )
            job._is_analysis_job = True
            review_job = schedule.every().day.at(slot["hhmm"]).do(_monthly_review_check)
            review_job._is_analysis_job = True

            # Pre-Market Briefing 30 Min vor Open (gleiche Zeit wie Vollanalyse)
            # Läuft zuerst durch (kein Claude → schnell), danach startet Vollanalyse
            pre_hhmm = slot["hhmm"]
            exch = slot["exchange"]
            pre_job = schedule.every().day.at(pre_hhmm).do(_pre_market_job, exch)
            pre_job._is_analysis_job = True

        times_str = ", ".join(f"{s['hhmm']} ({s['exchange']})" for s in slots)
        console.print(f"[dim]Analyse-Jobs registriert: {times_str}[/dim]")
        pre_times = ", ".join(
            f"{s['hhmm']} pre-market ({s['exchange']})" for s in slots
        )
        console.print(f"[dim]Pre-Market-Jobs: {pre_times}[/dim]")

    def _weekend_prep_job():
        """Runs weekend preparation. Called Saturday 09:00 and Sunday 14:00."""
        console.print(f"\n[bold cyan]📅 Wochenvorbereitung startet...[/bold cyan]")
        run_weekend_prep(weekend_prep_inst)

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

        # 4. Hedge-Aktionen senden
        if actions:
            for a in actions:
                console.print(f"\n  [magenta]{a}[/magenta]")
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
                actions_today=actions,
            )

    def _daily_maintenance_job():
        """
        Läuft täglich um 02:00 UTC. Bereinigt alle Datenbanken und verhindert
        unkontrolliertes Wachstum über Monate und Jahre hinweg.
        """
        import sqlite3 as _sqlite3
        report_lines = []

        # 1. News-Archiv: älter als 32 Tage löschen
        try:
            archive.cleanup_old(keep_days=32)
            report_lines.append("✅ News-Archiv: alte Artikel bereinigt (>32 Tage)")
        except Exception as e:
            report_lines.append(f"⚠️ News-Archiv Cleanup: {e}")

        # 2. Regime-Snapshots: älter als 90 Tage löschen
        try:
            from analyzers.recession_detector import RecessionDetector
            n = RecessionDetector().cleanup_old_snapshots(keep_days=90)
            if n:
                report_lines.append(f"✅ Regime-Snapshots: {n} alte Einträge gelöscht")
        except Exception as e:
            report_lines.append(f"⚠️ Regime-Snapshot Cleanup: {e}")

        # 4. Reflection-Engine: älteste Memos/Reviews löschen
        try:
            n = reflection.cleanup_old(keep_memos=30, keep_monthly=24)
            if n:
                report_lines.append(f"✅ Reflections: {n} alte Einträge gelöscht")
        except Exception as e:
            report_lines.append(f"⚠️ Reflection Cleanup: {e}")

        # 5. Signal-Queue: abgelaufene Signale bereinigen (nutzt bestehende Logik)
        try:
            expired = signal_queue.cleanup_expired()
            if expired:
                report_lines.append(f"✅ Signal-Queue: {expired} abgelaufene Signale entfernt")
        except Exception as e:
            report_lines.append(f"⚠️ Signal-Queue Cleanup: {e}")

        # 6. VACUUM auf allen SQLite-Datenbanken (gibt gelöschte Seiten frei)
        db_paths = [
            "data/news_archive.db",
            "data/trade_journal.db",
            "data/performance.db",
            "data/reflections.db",
            "data/signal_queue.db",
            "data/portfolio.db",
        ]
        vacuumed = 0
        for db_path in db_paths:
            try:
                conn = _sqlite3.connect(db_path)
                conn.execute("VACUUM")
                conn.close()
                vacuumed += 1
            except Exception:
                pass
        if vacuumed:
            report_lines.append(f"✅ VACUUM: {vacuumed} Datenbanken komprimiert")

        summary = "\n".join(report_lines)
        log.info("Tägliche Wartung abgeschlossen:\n%s", summary)

        if any("⚠️" in l for l in report_lines):
            TelegramNotifier().send(
                f"🔧 <b>Tägliche DB-Wartung</b>\n\n{summary}"
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

    # Turbo-Lernauswertung: täglich um 02:30 UTC (nur wenn Turbo-Modus aktiv)
    if config.turbo_mode and config.broker_mode == "paper":
        def _turbo_learn_job():
            try:
                learner = TurboLearner()
                result  = learner.analyze_and_save()
                if result:
                    notifier = TelegramNotifier()
                    notifier.send(learner.summary_text(result))
                    changes = learner.apply_to_config(config)
                    if changes:
                        notifier.send(
                            "⚙️ <b>Turbo-Lernwerte angewendet:</b>\n"
                            + "\n".join(f"• {c}" for c in changes)
                        )
                    log.info("Turbo-Lernauswertung abgeschlossen: %d Trades", result["turbo_trades_total"])
            except Exception as exc:
                log.exception("Turbo-Lernauswertung fehlgeschlagen: %s", exc)

        schedule.every().day.at("02:30").do(_turbo_learn_job)

    # ── Nutzeranfragen-Job: alle 15 Minuten prüfen ──────────────────────────
    def _user_request_job():
        """Sofort-Analyse wenn Nutzer Ticker über das Dashboard angefordert hat."""
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
        run_analysis_cycle(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
        )

    schedule.every(15).minutes.do(_user_request_job)

    # ── SL/TP-Check alle 30 Minuten ─────────────────────────────────────────
    schedule.every(30).minutes.do(strategy.check_open_positions)

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
                days_held = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
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

    # ── Headline-Signal-Scanner: stündlich ──────────────────────────────────
    def _headline_scan_job():
        """
        Scannt allgemeine Börsennachrichten auf starke Signale (M&A, FDA,
        Earnings-Beats, etc.) und speist entdeckte Ticker in die BenchList.
        Sehr starke Signale (Score ≥ 0.85) werden sofort per Telegram gemeldet.
        """
        try:
            from analyzers.headline_signal_detector import HeadlineSignalDetector
            detector = HeadlineSignalDetector()
            signals  = detector.scan()
            if signals:
                notifier = TelegramNotifier()
                added = detector.process_signals(
                    signals,
                    notify_fn=notifier.send,
                )
                if added:
                    console.print(
                        f"  [magenta]📰 Headline-Scanner: "
                        f"{len(added)} neue Kandidaten → BenchList: "
                        f"{', '.join(added[:6])}[/magenta]"
                    )
        except Exception as e:
            log.warning("Headline-Scan-Job fehlgeschlagen: %s", e)

    schedule.every().hour.do(_headline_scan_job)
    _headline_scan_job()   # Einmal sofort beim Start ausführen

    # ── Geopolitischer Radar: alle 2 Stunden ────────────────────────────────
    def _geopolitical_radar_job():
        """
        Scannt Weltpolitik-Feeds auf geopolitische Frühsignale und
        leitet Marktauswirkungen ab (Rüstung, Öl, Safe-Haven, etc.).
        Severity 2+ → sofortiger Telegram-Alert.
        Severity 3  → kritischer Alert (Krieg, Nuklear, Blockade).
        """
        try:
            from analyzers.geopolitical_radar import GeopoliticalRadar
            radar  = GeopoliticalRadar()
            events = radar.scan()
            if events:
                notifier = TelegramNotifier()
                added = radar.process_events(
                    events,
                    notify_fn=notifier.send,
                )
                if added:
                    console.print(
                        f"  [bold red]🌍 Geo-Radar: {len(events)} Event(s) – "
                        f"Ticker → BenchList: {', '.join(added[:8])}[/bold red]"
                    )
        except Exception as e:
            log.warning("Geopolitical-Radar-Job fehlgeschlagen: %s", e)

    schedule.every(2).hours.do(_geopolitical_radar_job)
    _geopolitical_radar_job()   # Sofort beim Start

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

    schedule.every().day.at("20:30").do(_daily_dashboard_job)

    # Goal-reached check (einmalige Telegram-Nachricht wenn Ziel erreicht)
    _goal_notified: list = []
    if goal_risk.active:
        schedule.every().hour.do(
            lambda: _check_goal_reached(goal_risk, portfolio, broker, tracker, TelegramNotifier(), _goal_notified)
        )

    # Circuit-Breaker-Monitor: einmalige Reflexion wenn CB heute ausgelöst wird
    _cb_triggered_today: list = []
    _circuit_breaker = CircuitBreaker()

    def _cb_monitor_job():
        prices = broker.get_prices(list(portfolio.all_positions().keys()))
        current_value = portfolio.total_value(prices)
        _circuit_breaker.register_day_open(current_value)
        allowed, reason = _circuit_breaker.check_buy_allowed(current_value)
        if not allowed and not _cb_triggered_today:
            _cb_triggered_today.append(True)
            console.print(f"\n  [bold red]⛔ CIRCUIT BREAKER: {reason}[/bold red]")
            _run_post_cb_reflection(
                _circuit_breaker, portfolio, broker, tracker, reflection, TelegramNotifier()
            )
        # Tages-Reset: neuer Tag → CB-Status zurücksetzen
        today = __import__("datetime").date.today().isoformat()
        if _cb_triggered_today and _circuit_breaker._state.get("day") != today:
            _cb_triggered_today.clear()

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

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot gestoppt.[/yellow]")
