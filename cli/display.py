"""
cli/display.py – All show_* and _print_* display functions.
"""

from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import config
from logger import get_logger
from analyzers import AnalysisResult
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from portfolio.focus_mode import FocusController, FocusMode
from portfolio.trade_journal import TradeJournal
from portfolio.signal_queue import SignalQueue
from portfolio.goal_risk_assessor import GoalRiskAssessor
from collectors.social_scan import SocialPulseDB
from analyzers.reflection_engine import ReflectionEngine
from analyzers.recession_detector import RecessionDetector
from analyzers.news_velocity import NewsVelocityAnalyzer
from analyzers.sentiment_memory import SentimentMemory
from analyzers.reentry_tracker import ReEntryTracker
from analyzers.weekend_prep import WeekendPrep

console = Console()
log = get_logger(__name__)


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def _print_analysis(a: AnalysisResult):
    color = {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}.get(a.direction, "white")
    conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(a.confidence, "white")
    rec_color = {"BUY": "bold green", "SELL": "bold red", "HOLD": "yellow", "SKIP": "dim"}.get(
        a.recommendation, "white"
    )

    console.print(
        f"  Sentiment: [{color}]{a.direction}[/{color}] "
        f"(Score: {a.sentiment_score:.2f}) | "
        f"Konfidenz: [{conf_color}]{a.confidence}[/{conf_color}] | "
        f"Empfehlung: [{rec_color}]{a.recommendation}[/{rec_color}]"
    )
    if a.bull_case or a.bear_case:
        winner_color = {"BULL": "green", "BEAR": "red", "DRAW": "yellow"}.get(a.debate_winner, "white")
        winner_icon  = {"BULL": "🟢", "BEAR": "🔴", "DRAW": "⚖️"}.get(a.debate_winner, "")
        console.print(f"  {winner_icon} Debatte: [{winner_color}]{a.debate_winner}[/{winner_color}]")
        if a.bull_case:
            console.print(f"  [green]▲ Bull:[/green] [italic]{a.bull_case}[/italic]")
        if a.bear_case:
            console.print(f"  [red]▼ Bear:[/red] [italic]{a.bear_case}[/italic]")
    if a.entry_rationale:
        console.print(f"  Begründung: [italic]{a.entry_rationale}[/italic]")
    if a.target_price:
        console.print(f"  [bold]Zielkurs: ${a.target_price:.2f}[/bold] – {a.target_price_rationale}")
    if a.thesis_valid is False:
        console.print(f"  [bold red]⚠ THESE GEBROCHEN: {a.thesis_break_reason}[/bold red]")
    elif a.thesis_valid is True:
        console.print(f"  [green]✓ Kaufthese weiterhin gültig[/green]")
    if a.key_catalysts:
        console.print(f"  Katalysatoren: {', '.join(a.key_catalysts[:3])}")
    if a.risk_factors:
        console.print(f"  Risiken: {', '.join(a.risk_factors[:3])}")


def _print_portfolio_summary(portfolio: Portfolio, broker, phase_ctrl: PhaseController):
    from datetime import datetime
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
        days = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
        pnl_str = f"[green]+${pnl:.2f}[/green]" if pnl >= 0 else f"[red]-${abs(pnl):.2f}[/red]"
        table.add_row(
            ticker, f"{pos.shares:.2f}", f"${pos.entry_price:.2f}",
            f"${price:.2f}", pnl_str, f"${pos.stop_loss:.2f}", f"${pos.take_profit:.2f}", str(days),
        )

    console.print()
    console.print(table)

    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"
    progress_bar = _progress_bar(phase_info["progress_pct"])
    summary_lines = [
        f"Cash: [bold]${portfolio.cash:,.2f}[/bold]  |  Gesamtwert: [bold]${total:,.2f}[/bold]",
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}]  |  "
        f"Ziel: ${phase_info['growth_target']:,.0f}  |  "
        f"Fortschritt: {progress_bar} {phase_info['progress_pct']:.1f}%",
    ]
    if phase_info["phase"] == "DISTRIBUTION":
        summary_lines.append(
            f"[bold magenta]Monatliche Ausschüttung: ${phase_info.get('monthly_distribution', 0):,.2f} "
            f"(Ziel: ${phase_info['monthly_target']:,.2f})[/bold magenta]"
        )
    else:
        summary_lines.append(f"Noch ${phase_info['remaining_to_goal']:,.2f} bis zur Ausschüttungsphase")

    console.print(Panel("\n".join(summary_lines), title="Kapital & Phase", border_style=phase_color))


def show_status(portfolio: Portfolio, broker, phase_ctrl: PhaseController):
    _print_portfolio_summary(portfolio, broker, phase_ctrl)

    trades = portfolio.trade_history()
    if trades:
        trade_table = Table(title="Trade-History (letzte 20)", box=box.ROUNDED)
        trade_table.add_column("Datum", style="dim")
        trade_table.add_column("Ticker", style="cyan")
        trade_table.add_column("Aktion")
        trade_table.add_column("Stück", justify="right")
        trade_table.add_column("Kurs", justify="right")
        trade_table.add_column("P&L", justify="right")
        trade_table.add_column("Grund")

        for t in trades[-20:]:
            pnl = t.pnl
            pnl_str = (
                f"[green]+${pnl:.2f}[/green]" if pnl > 0
                else (f"[red]-${abs(pnl):.2f}[/red]" if pnl < 0 else "")
            )
            action_color = "bold green" if t.action == "BUY" else "bold red"
            trade_table.add_row(
                t.timestamp[:10], t.ticker,
                f"[{action_color}]{t.action}[/{action_color}]",
                f"{t.shares:.2f}", f"${t.price:.2f}", pnl_str,
                (t.reason or "")[:45],
            )
        console.print(trade_table)


def show_report(
    tracker: PerformanceTracker,
    phase_ctrl: PhaseController,
    portfolio: Portfolio,
    broker,
):
    console.rule("[bold blue]Lern- und Phasenbericht")

    # ── 1. Accuracy overview ───────────────────────────────────────────────
    acc = tracker.get_accuracy_report()
    if acc.get("total_closed", 0) == 0:
        console.print(Panel(
            "[dim]Noch keine abgeschlossenen Trades.[/dim]",
            title="Vorhersage-Genauigkeit",
        ))
    else:
        adaptive_threshold = tracker.get_adaptive_threshold(config.buy_threshold)
        acc_lines = [
            f"Abgeschlossene Trades:     [bold]{acc['total_closed']}[/bold]",
            f"Win-Rate:                  [bold]{acc['win_rate_pct']}%[/bold]",
            f"Richtungs-Genauigkeit:     [bold]{acc['direction_accuracy_pct']}%[/bold]",
            f"Zielkurs-Trefferquote:     [bold]{acc['target_hit_pct']}%[/bold]",
            f"Ø Rendite pro Trade:       [bold]{acc['avg_return_pct']:+.2f}%[/bold]",
            f"Ø Haltedauer (Ist/Plan):   [bold]{acc['avg_hold_days_actual']}d[/bold] / {acc['avg_hold_days_predicted']}d",
            f"Adaptiver Kauf-Threshold:  [bold]{adaptive_threshold:.2f}[/bold] (Basis: {config.buy_threshold:.2f})",
        ]
        console.print(Panel("\n".join(acc_lines), title="Vorhersage-Genauigkeit", border_style="cyan"))

    # ── 2. Exit-Grund-Statistik ────────────────────────────────────────────
    exit_stats = tracker.get_exit_reason_stats()
    if exit_stats:
        et = Table(title="Exit-Grund vs. P&L", box=box.SIMPLE)
        et.add_column("Ausstiegsgrund")
        et.add_column("Trades", justify="right")
        et.add_column("Ø Rendite", justify="right")
        et.add_column("Win-Rate", justify="right")
        labels = {
            "stop_loss": "Stop-Loss",
            "take_profit": "Take-Profit",
            "thesis_broken": "These gebrochen ⚠",
            "hold_expired": "Haltedauer abgelaufen",
            "sentiment_sell": "Sentiment-SELL",
            "other": "Sonstiges",
        }
        for row in exit_stats:
            ret = row["avg_return_pct"]
            ret_str = f"[green]{ret:+.2f}%[/green]" if ret >= 0 else f"[red]{ret:+.2f}%[/red]"
            et.add_row(
                labels.get(row["category"], row["category"]),
                str(row["trades"]),
                ret_str,
                f"{row['win_rate_pct']}%",
            )
        console.print(et)

    # ── 3. Sentiment-Score-Buckets ─────────────────────────────────────────
    buckets = tracker.get_sentiment_score_buckets()
    if buckets:
        bt = Table(title="Sentiment-Score-Bereich vs. Performance", box=box.SIMPLE)
        bt.add_column("Score-Bereich")
        bt.add_column("Trades", justify="right")
        bt.add_column("Win-Rate", justify="right")
        bt.add_column("Ø Rendite", justify="right")
        for b in buckets:
            ret = b["avg_return_pct"]
            ret_str = f"[green]{ret:+.2f}%[/green]" if ret >= 0 else f"[red]{ret:+.2f}%[/red]"
            bt.add_row(b["score_range"], str(b["trades"]), f"{b['win_rate_pct']}%", ret_str)
        console.print(bt)

    # ── 4. Quellen-Trefferquote ────────────────────────────────────────────
    source_acc = tracker.get_source_accuracy()
    if source_acc:
        st = Table(title="Quellen-Trefferquote (top 10)", box=box.SIMPLE)
        st.add_column("Quelle")
        st.add_column("Ticker")
        st.add_column("Trades", justify="right")
        st.add_column("Win-Rate", justify="right")
        st.add_column("Ø Rendite", justify="right")
        for row in source_acc[:10]:
            ret = row["avg_return_pct"]
            ret_str = f"[green]{ret:+.2f}%[/green]" if ret >= 0 else f"[red]{ret:+.2f}%[/red]"
            st.add_row(row["source"], row["ticker"], str(row["trades"]), f"{row['win_rate_pct']}%", ret_str)
        console.print(st)

    # ── 5. Letzte abgeschlossene Trades ───────────────────────────────────
    recent = tracker.get_recent_trades(5)
    if recent:
        rt = Table(title="Letzte 5 geschlossene Trades", box=box.SIMPLE)
        rt.add_column("Ticker")
        rt.add_column("Rendite", justify="right")
        rt.add_column("Haltedauer", justify="right")
        rt.add_column("These ✓")
        rt.add_column("Zielkurs ✓")
        rt.add_column("Ausstiegsgrund")
        for t in recent:
            ret = t.get("actual_return_pct") or 0
            ret_str = f"[green]{ret:+.1f}%[/green]" if ret >= 0 else f"[red]{ret:+.1f}%[/red]"
            rt.add_row(
                t["ticker"], ret_str,
                f"{t.get('actual_hold_days', '?')}d",
                "✓" if t.get("direction_correct") else "✗",
                "✓" if t.get("target_hit") else "✗",
                (t.get("sell_reason") or "")[:40],
            )
        console.print(rt)

    # ── 6. Phase-Info ──────────────────────────────────────────────────────
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)
    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"
    phase_lines = [
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}]",
        f"Startkapital:  ${phase_info['initial_capital']:,.2f}",
        f"Aktuell:       ${phase_info['portfolio_value']:,.2f}",
        f"Wachstumsziel: ${phase_info['growth_target']:,.2f}  ({config.growth_target_multiple:.1f}×)",
        f"Fortschritt:   {_progress_bar(phase_info['progress_pct'])} {phase_info['progress_pct']:.1f}%",
    ]
    if phase_info["phase"] == "GROWTH":
        phase_lines.append(f"Noch ${phase_info['remaining_to_goal']:,.2f} bis zur Ausschüttungsphase")
    else:
        phase_lines += [
            "",
            f"[bold magenta]Monatliche Ausschüttung: ${phase_info['monthly_distribution']:,.2f}[/bold magenta]",
            f"Ziel-Ausschüttung:       ${phase_info['monthly_target']:,.2f}",
            f"Sicherheitspuffer:       ${phase_info['buffer_reserve']:,.2f} ({config.distribution_buffer_months} Monate)",
        ]
    console.print(Panel("\n".join(phase_lines), title="Portfolio-Phase", border_style=phase_color))


def show_monthly_review(reflection: ReflectionEngine, year_month: Optional[str] = None):
    console.rule("[bold blue]Monatliche Selbsteinschätzung")
    content = reflection.generate_monthly_review(year_month)
    if not content:
        console.print("[dim]Nicht genug abgeschlossene Trades im Zeitraum oder API-Fehler.[/dim]")
        return
    console.print(Panel(content, title=f"Self-Assessment {year_month or 'letzter Monat'}", border_style="magenta"))


def show_trade_journal(journal: TradeJournal, ticker: Optional[str] = None):
    console.rule(f"[bold blue]Trade-Tagebuch{' – ' + ticker if ticker else ''}")
    if ticker:
        stories = journal.get_trade_story(ticker, limit_trades=5)
    else:
        stories = journal.get_all_trade_summaries(limit=20)
    if not stories:
        console.print("[dim]Noch keine Trade-Events gespeichert.[/dim]")
        return
    for s in stories[:10]:
        status = "🟢 OFFEN" if s.get("is_open") else (
            "🟩 GEWINN" if (s.get("pnl") or 0) >= 0 else "🔴 VERLUST"
        )
        lines = [
            f"[bold]{s['ticker']}[/bold]  {status}",
            f"  Eingestiegen: {s.get('entry_date', '?')[:10]} @ ${s.get('entry_price', 0):.2f}",
            f"  Sentiment: {s.get('entry_sentiment', 0):.2f}  |  These: [italic]{(s.get('entry_rationale') or '')[:120]}[/italic]",
        ]
        if s.get("catalysts"):
            lines.append(f"  Katalysatoren: {', '.join(s['catalysts'][:3])}")
        if s.get("risks"):
            lines.append(f"  Risiken: {', '.join(s['risks'][:3])}")
        lines.append(f"  Tagesprüfungen: {s.get('n_daily_checks', 0)}  |  Warnungen: {s.get('n_warnings', 0)}")
        if not s.get("is_open"):
            pnl = s.get("pnl", 0)
            color = "green" if pnl >= 0 else "red"
            lines += [
                f"  Verkauft: {s.get('exit_date', '?')[:10]} @ ${s.get('exit_price', 0):.2f}",
                f"  P&L: [{color}]{pnl:+.2f} USD ({s.get('pnl_pct', 0):+.1f}%)[/{color}]  |  "
                f"Haltedauer: {s.get('actual_hold_days', '?')}d",
                f"  Grund: {(s.get('exit_reason') or '')[:120]}",
            ]
        console.print(Panel("\n".join(lines), border_style="cyan" if s.get("is_open") else "dim"))


def show_focus_info(focus_ctrl: FocusController, portfolio: Portfolio, broker):
    console.rule("[bold blue]Fokus-Modus")
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    info = focus_ctrl.get_info(total)
    lines = [
        f"Modus:               [bold]{info['label']}[/bold]",
        f"Beschreibung:        [italic]{info['description']}[/italic]",
        "",
        f"Stop-Loss:           {info['stop_loss_pct']*100:.0f}%",
        f"Take-Profit:         {info['take_profit_pct']*100:.0f}%",
        f"Max. Positionsgröße: {info['max_position_pct']*100:.0f}%",
        f"Min. Sentiment:      {info['min_sentiment']:.2f}",
        f"Bevorzugte Haltedauer: {info['preferred_hold_days']}d",
    ]
    if info["mode"] == FocusMode.TARGET_GOAL and info.get("target_amount"):
        status = "✓ Im Plan" if info["on_track"] else ("⚠ Hinter Plan" if info["behind_plan"] else "✓ Voraus")
        lines += [
            "",
            f"Zielbetrag:    ${info['target_amount']:,.2f}",
            f"Zieldatum:     {info['target_date']}",
            f"Tage übrig:    {info['days_remaining']}",
            f"Fortschritt:   {info['progress_pct']:.1f}%",
            f"Status:        [bold]{status}[/bold]  (Urgency {info['urgency']:.2f})",
        ]
    console.print(Panel("\n".join(lines), title="Aktiver Fokus", border_style="cyan"))


def show_pulse(pulse_db: SocialPulseDB):
    """Displays aggregated social pulse for the last 6 hours."""
    console.rule("[bold blue]Social-Marktpuls (letzte 6h)")
    summary = pulse_db.get_pulse_summary(hours=6)
    if not summary:
        console.print("[dim]Noch keine Social-Scan-Daten.[/dim]")
        return
    table = Table(title="Marktpuls per Ticker", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Erwähnungen", justify="right")
    table.add_column("Bullisch", justify="right")
    table.add_column("Bärisch", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Trend")
    for row in summary:
        score = row["avg_score"]
        score_str = (
            f"[green]{score:+.2f}[/green]" if score > 0.1
            else (f"[red]{score:+.2f}[/red]" if score < -0.1 else f"{score:+.2f}")
        )
        trend = "🟢 Bullisch" if score > 0.1 else ("🔴 Bärisch" if score < -0.1 else "⚪ Neutral")
        table.add_row(
            row["ticker"],
            str(row["total_mentions"]),
            str(row["bull"]),
            str(row["bear"]),
            score_str,
            trend,
        )
    console.print(table)
    spikes = pulse_db.get_spikes(hours=2)
    if spikes:
        console.print("\n[bold yellow]Aktuelle Spikes (2h-Fenster):[/bold yellow]")
        for s in spikes:
            console.print(f"  {s['ticker']}: {s['spike_ratio']}× normales Volumen")


def show_signal_queue(signal_queue: SignalQueue):
    """Displays all pending signals in the queue."""
    console.rule("[bold blue]Signal-Warteschlange")
    pending = signal_queue.get_pending()
    history = signal_queue.get_history(limit=10)
    if not pending:
        console.print("[dim]Keine ausstehenden Signale.[/dim]")
    else:
        table = Table(title=f"{len(pending)} ausstehende Signale", box=box.ROUNDED)
        table.add_column("ID", justify="right")
        table.add_column("Ticker", style="cyan")
        table.add_column("Score", justify="right")
        table.add_column("Konfidenz")
        table.add_column("Erstellt")
        table.add_column("Läuft ab")
        table.add_column("Begründung")
        for s in pending:
            table.add_row(
                str(s["id"]), s["ticker"],
                f"{s['sentiment_score']:.2f}", s["confidence"],
                s["created_at"][:16], s["expires_at"][:16],
                (s.get("entry_rationale") or "")[:60],
            )
        console.print(table)
    if history:
        ht = Table(title="Historie (letzte 10)", box=box.SIMPLE)
        ht.add_column("Ticker", style="cyan")
        ht.add_column("Score", justify="right")
        ht.add_column("Status")
        ht.add_column("Erstellt")
        status_color = {"pending": "yellow", "executed": "green", "expired": "dim", "superseded": "dim"}
        for s in history:
            color = status_color.get(s["status"], "white")
            ht.add_row(
                s["ticker"], f"{s['sentiment_score']:.2f}",
                f"[{color}]{s['status']}[/{color}]",
                s["created_at"][:16],
            )
        console.print(ht)


def show_briefing(wp: WeekendPrep):
    console.rule("[bold blue]Wochenbriefings")
    entries = wp.get_latest_briefing(limit=3)
    if not entries:
        console.print("[dim]Noch keine Briefings gespeichert. Starte mit: python main.py --weekend[/dim]")
        return
    for entry in entries:
        console.print(Panel(
            entry["briefing"],
            title=f"Woche ab {entry['week_start']} (generiert {entry['generated_at'][:16]})",
            border_style="cyan",
        ))


def show_regime(detector: RecessionDetector):
    console.rule("[bold blue]Marktregime-Analyse")
    console.print("[cyan]Analysiere VIX, Zinskurve, Sektorbreite, Credit-Spreads...[/cyan]")
    result = detector.analyze()

    regime = result["regime"]
    score  = result["recession_score"]
    regime_color = {"BULL": "green", "NEUTRAL": "yellow", "BEAR": "red", "CRISIS": "bold red"}.get(regime, "white")
    score_bar = _progress_bar(score * 100)

    lines = [
        f"Regime: [{regime_color}]{regime}[/{regime_color}]  |  Score: {score:.3f}",
        f"Rezessions-Risiko: {score_bar} {score*100:.1f}%",
        "",
    ]
    comp = result.get("components", {})
    if "vix" in comp:
        v = comp["vix"]
        lines.append(f"VIX:            {v['value']}  →  {v['label']}")
    if "yield_curve" in comp:
        yc = comp["yield_curve"]
        spread = f"{yc['spread_pct']:+.2f}%" if yc['spread_pct'] is not None else "N/A"
        lines.append(f"Zinskurve:      Spread {spread}  →  {yc['label']}")
    if "sp500_ma200" in comp:
        sp = comp["sp500_ma200"]
        gap = f"{sp['gap_pct']:+.1f}%" if sp['gap_pct'] is not None else "N/A"
        lines.append(f"S&P vs 200-MA:  {gap}  →  {sp['label']}")
    if "sector_breadth" in comp:
        sb = comp["sector_breadth"]
        lines.append(f"Sektoren bear:  {sb['bear_sectors_pct']}%  →  {sb['label']}")
    if result.get("macro_summary"):
        lines += ["", f"Makro-Signal: [italic]{result['macro_summary'][:200]}[/italic]"]

    console.print(Panel("\n".join(lines), title="Aktuelles Marktregime", border_style=regime_color))

    hedges = result.get("recommended_hedges", [])
    if hedges:
        ht = Table(title=f"Empfohlene Hedges ({result['hedge_intensity']})", box=box.ROUNDED)
        ht.add_column("ETF", style="cyan")
        ht.add_column("Thema")
        ht.add_column("Allokation", justify="right")
        ht.add_column("Grund")
        for h in hedges:
            ht.add_row(
                h["ticker"], h["description"],
                f"{h['allocation_pct']*100:.0f}%", h["reason"][:60],
            )
        console.print(ht)
    else:
        console.print("[dim]Keine Hedges empfohlen – Regime zu gut.[/dim]")


def show_goal(goal_risk: GoalRiskAssessor, portfolio: Portfolio, broker, tracker):
    """Zeigt aktuellen Zielstatus mit Wahrscheinlichkeit und Empfehlungen."""
    console.rule("[bold blue]Ziel-Analyse (TARGET_GOAL)")

    if not goal_risk.active:
        console.print(
            "[yellow]Kein aktives Ziel gesetzt.[/yellow]\n"
            "In .env eintragen:\n"
            "  FOCUS_MODE=TARGET_GOAL\n"
            "  TARGET_GOAL_AMOUNT=2000\n"
            "  TARGET_GOAL_DATE=2025-12-31"
        )
        return

    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    stats = tracker.get_stats()
    assessment = goal_risk.assess(total, stats)
    if not assessment:
        console.print("[dim]Keine Bewertung möglich.[/dim]")
        return

    risk_colors = {"OK": "green", "CAUTION": "yellow", "DANGER": "red", "UNREACHABLE": "bold red"}
    risk_icons  = {"OK": "✓", "CAUTION": "⚠", "DANGER": "🚨", "UNREACHABLE": "✗"}
    color = risk_colors.get(assessment.risk_level, "white")
    icon  = risk_icons.get(assessment.risk_level, "?")

    prob_bar = _progress_bar(assessment.probability_pct)
    progress_pct = min(100.0, total / assessment.target_value * 100)
    progress_bar = _progress_bar(progress_pct)

    lines = [
        f"Ziel:              ${assessment.target_value:,.2f}",
        f"Aktuell:           ${assessment.portfolio_value:,.2f}",
        f"Fortschritt:       {progress_bar} {progress_pct:.1f}%",
        f"Tage bis Zieldatum:{assessment.days_remaining}",
        f"",
        f"Wahrscheinlichkeit: {prob_bar} [bold]{assessment.probability_pct:.0f}%[/bold]",
        f"Benötigte Rendite:  {assessment.required_annual_return*100:.1f}% p.a.",
        f"Realistische Rendite:{assessment.realistic_annual_return*100:.1f}% p.a.",
        f"",
        f"Status: [{color}]{icon} {assessment.risk_level}[/{color}]",
        f"Hinweis: {assessment.note}",
    ]
    if assessment.actions:
        lines.append("")
        lines.append("Empfehlungen:")
        for a in assessment.actions:
            lines.append(f"  → {a}")

    border = {"OK": "green", "CAUTION": "yellow", "DANGER": "red", "UNREACHABLE": "red"}.get(
        assessment.risk_level, "cyan"
    )
    console.print(Panel("\n".join(lines), title="Zielerreichungs-Analyse", border_style=border))

    if assessment.goal_reached:
        console.print(
            "\n[bold green]🎉 Ziel erreicht![/bold green] "
            f"Du kannst jetzt ${assessment.target_value:,.2f} entnehmen.\n"
            "[dim]Tipp: FOCUS_MODE auf WEALTH_BUILDING zurückstellen nach der Entnahme.[/dim]"
        )


def _run_score_display() -> None:
    """Zeigt den aktuellen Bot-Score mit History und Meilensteinen."""
    from analyzers.bot_scorer import BotScorer, MILESTONES
    scorer = BotScorer()
    state  = scorer.get()

    console.rule("[bold blue]Bot-Score")
    console.print(state.to_text())

    # Nächster Meilenstein
    next_ms = next((t for t in sorted(MILESTONES) if t > state.current), None)
    if next_ms:
        label, desc, reward = MILESTONES[next_ms]
        console.print(
            f"\nNächster Meilenstein: [cyan]{label}[/cyan] bei Score [bold]{next_ms}[/bold]\n"
            f"  {desc}"
            + (f"\n  💡 {reward}" if reward else "")
        )


def _run_margin_check(tracker) -> None:
    """Zeigt Margin-Tier-Status und aktuelle Einstellung."""
    from analyzers.margin_readiness import MarginTierTracker
    result = MarginTierTracker(tracker).get_active_tier(use_cache=False)

    console.rule("[bold blue]Progressiver Hebel-Tracker")
    console.print(result.to_text())

    margin_active = config.use_margin
    status_color  = "green" if margin_active else "yellow"
    status_label  = f"AKTIV – Tier {result.active_tier.level} ({result.factor:.2f}×)" if margin_active else "DEAKTIVIERT"
    console.print(f"\nSystem-Einstellung: [{status_color}]{status_label}[/{status_color}]")

    if not margin_active:
        console.print(
            "\nZum Aktivieren des Tier-Systems in .env eintragen:\n"
            "  [cyan]USE_MARGIN=true[/cyan]\n"
            "  [cyan]MARGIN_MIN_CONFIDENCE=HIGH[/cyan]\n"
            "[dim]Der Bot bestimmt den Hebel dann selbst anhand seiner Performance.[/dim]\n"
            "[dim]⚠ Erfordert Alpaca Margin-Account (nicht Cash-Account)![/dim]"
        )
    elif result.active_tier.level == 0 and result.downgrade_reason:
        console.print(f"\n[bold red]⚠ Hebel pausiert:[/bold red] {result.downgrade_reason}")
    elif result.at_max:
        console.print("\n[bold green]🏆 Maximaler Tier erreicht – 2.00× Hebel aktiv.[/bold green]")


def _run_velocity_display(ticker: str) -> None:
    """News-Geschwindigkeit für einen Ticker anzeigen."""
    console.rule(f"[bold blue]News-Velocity – {ticker}")
    vel = NewsVelocityAnalyzer().analyze(ticker)
    acc_color = {"SPIKE": "bold red", "HIGH": "yellow", "NORMAL": "green", "LOW": "dim"}.get(
        vel.acceleration, "white"
    )
    lines = [
        f"Ticker:         {vel.ticker}",
        f"Artikel (1h):   {vel.articles_1h}",
        f"Artikel (6h):   {vel.articles_6h}",
        f"Artikel (24h):  {vel.articles_24h}",
        f"Tages-Basis:    {vel.baseline_per_day:.1f} Artikel/Tag (7-Tage-Ø)",
        f"Velocity-Score: {vel.velocity_score:.2f}",
        f"Signal-Boost:   ×{vel.signal_boost:.2f}",
    ]
    console.print(Panel("\n".join(lines), title=f"Beschleunigung: [{acc_color}]{vel.acceleration}[/{acc_color}]"))


def _run_sentiment_memory_display() -> None:
    """Sentiment-Verlässlichkeit pro Ticker anzeigen."""
    console.rule("[bold blue]Sentiment-Verlässlichkeit pro Ticker")
    sm = SentimentMemory()
    stats = sm.get_all_stats()
    if not stats:
        console.print("[dim]Noch keine Daten – Verlässlichkeit wird nach abgeschlossenen Trades berechnet.[/dim]")
        return
    table = Table(title="Sentiment-Trefferquote", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Trades", justify="right")
    table.add_column("Trefferquote", justify="right")
    table.add_column("Schwellen-Adj.", justify="right")
    for row in stats:
        rel = row["reliability"]
        rel_color = "green" if rel >= 0.65 else ("red" if rel < 0.4 else "yellow")
        adj = row["adjustment"]
        adj_str = f"[green]{adj:+.2f}[/green]" if adj < 0 else (f"[red]{adj:+.2f}[/red]" if adj > 0 else "±0.00")
        table.add_row(
            row["ticker"], str(row["records"]),
            f"[{rel_color}]{rel*100:.0f}%[/{rel_color}]",
            adj_str,
        )
    console.print(table)
    console.print(sm.to_text())


def _run_reentry_display(broker) -> None:
    """Re-Entry-Kandidaten anzeigen."""
    console.rule("[bold blue]Re-Entry-Kandidaten")
    rt = ReEntryTracker()
    watched = rt.get_all_watched()
    if not watched:
        console.print("[dim]Keine Positionen werden beobachtet.[/dim]")
        return

    # Preise aktualisieren
    prices = broker.get_prices([c.ticker for c in watched])
    rt.update_prices(prices)

    candidates = rt.get_candidates()
    all_watched = rt.get_all_watched()

    if candidates:
        table = Table(title="Re-Entry-Kandidaten", box=box.ROUNDED)
        table.add_column("Ticker", style="cyan")
        table.add_column("Verkauft @ ", justify="right")
        table.add_column("Tief", justify="right")
        table.add_column("Jetzt", justify="right")
        table.add_column("Erholung", justify="right")
        table.add_column("Score", justify="right")
        table.add_column("Signal")
        for c in candidates:
            recovery = (c.last_price - c.low_since_sell) / max(c.low_since_sell, 0.01) * 100
            signal_color = "bold green" if c.signal == "STRONG" else "green"
            table.add_row(
                c.ticker, f"${c.sell_price:.2f}", f"${c.low_since_sell:.2f}",
                f"${c.last_price:.2f}", f"+{recovery:.1f}%",
                f"{c.re_entry_score:.2f}", f"[{signal_color}]{c.signal}[/{signal_color}]",
            )
        console.print(table)
    else:
        console.print("[dim]Aktuell keine attraktiven Re-Entry-Möglichkeiten.[/dim]")

    console.print(f"\n[dim]Beobachtet werden {len(all_watched)} Ticker.[/dim]")
    console.print(rt.to_text())


def show_crash_radar(force: bool = False) -> None:
    """Crash-Wahrscheinlichkeit, Blasen-Detektor und historischer Vergleich."""
    from analyzers.crash_radar import CrashRadar, _BUBBLE_MILD, _BUBBLE_HIGH, _BUBBLE_SEVERE

    console.rule("[bold red]CRASH RADAR – Markt-Risiko & Blasen-Detektor")
    console.print("[dim]Lade Marktdaten (yfinance)... Das kann 10–20 Sekunden dauern.[/dim]")

    radar  = CrashRadar()
    result = radar.analyze(force_refresh=force)

    if not result.data_available:
        console.print(f"[red]Daten nicht verfügbar: {result.summary_line}[/red]")
        return

    # ── Gesamt-Score ──────────────────────────────────────────────────────────
    prob  = result.crash_probability
    level = result.risk_level
    color = {"GERING": "green", "MITTEL": "yellow", "HOCH": "bold red", "EXTREM": "bold red on white"}.get(level, "white")
    bar_filled = round(prob / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)

    console.print(Panel(
        f"[{color}]GESAMT-CRASH-WAHRSCHEINLICHKEIT: {prob}%[/{color}]\n"
        f"[{color}][{bar}] {level}[/{color}]\n\n"
        f"[dim]Stand: {result.timestamp}  |  Daten: yfinance  |  "
        f"Cache: 1h  |  [/dim][dim]python main.py --crash-radar --refresh[/dim]",
        border_style=color if color not in ("bold red", "bold red on white") else "red",
    ))

    # ── Indikatoren-Tabelle ───────────────────────────────────────────────────
    ind_table = Table(title="Markt-Indikatoren", box=box.ROUNDED, show_lines=False)
    ind_table.add_column("Indikator",   style="cyan", min_width=28)
    ind_table.add_column("Wert",        justify="right", min_width=10)
    ind_table.add_column("Status",      justify="center", min_width=10)
    ind_table.add_column("Gefahr",      justify="center", min_width=8)
    ind_table.add_column("Bedeutung",   style="dim", min_width=40)

    status_colors = {"NORMAL": "green", "ERHÖHT": "yellow", "ALARM": "bold red"}
    for ind in result.indicators:
        sc = ind.score
        bar_len = round(sc * 10)
        score_bar = f"[{'█' * bar_len}{'░' * (10 - bar_len)}]"
        s_color = status_colors.get(ind.status, "white")
        ind_table.add_row(
            ind.name,
            ind.value_str,
            f"[{s_color}]{ind.status}[/{s_color}]",
            score_bar,
            ind.description,
        )
    console.print(ind_table)

    # ── Blasen-Detektor ───────────────────────────────────────────────────────
    bubble_table = Table(title="Blasen-Detektor – Sektor-Überhitzung", box=box.ROUNDED)
    bubble_table.add_column("Sektor",      style="cyan", min_width=22)
    bubble_table.add_column("Ticker",      style="dim", justify="center")
    bubble_table.add_column("YTD",         justify="right")
    bubble_table.add_column("1 Jahr",      justify="right")
    bubble_table.add_column("Blase",       justify="center", min_width=10)
    bubble_table.add_column("Risiko",      justify="center")
    bubble_table.add_column("Hinweis",     style="dim")

    risk_colors = {
        "EXTREM": "bold red",
        "HOCH":   "red",
        "MITTEL": "yellow",
        "GERING": "green",
    }
    for b in result.bubbles:
        r_color = risk_colors.get(b.risk_label, "white")
        score_bar = "█" * round(b.bubble_score / 10) + "░" * (10 - round(b.bubble_score / 10))
        ytd_color = "green" if b.ytd_pct >= 0 else "red"
        y1_color  = "green" if b.return_1y >= 0 else "red"
        bubble_table.add_row(
            b.name,
            b.ticker,
            f"[{ytd_color}]{b.ytd_pct:+.1f}%[/{ytd_color}]",
            f"[{y1_color}]{b.return_1y:+.1f}%[/{y1_color}]",
            f"[{r_color}]{score_bar}[/{r_color}]",
            f"[{r_color}]{b.risk_label}[/{r_color}]",
            b.note,
        )
    console.print(bubble_table)

    # ── Historischer Vergleich ────────────────────────────────────────────────
    hist_table = Table(title="Historischer Vergleich – Ähnlichkeit mit früheren Crashes", box=box.ROUNDED)
    hist_table.add_column("Crash-Phase",       style="cyan", min_width=28)
    hist_table.add_column("Ähnlichkeit",       justify="center", min_width=14)
    hist_table.add_column("Beschreibung",      style="dim")

    for i, m in enumerate(result.historical_matches[:6]):
        pct = m.similarity_pct
        bar_len  = round(pct / 5)
        sim_bar  = "█" * bar_len + "░" * (20 - bar_len)
        if i == 0:
            label = f"[bold yellow]{m.label}[/bold yellow]"
        else:
            label = m.label
        hist_table.add_row(
            label,
            f"{sim_bar} {pct}%",
            m.desc,
        )
    console.print(hist_table)

    # ── Empfehlung ────────────────────────────────────────────────────────────
    best_match = result.historical_matches[0] if result.historical_matches else None
    alarm_inds = [i for i in result.indicators if i.status == "ALARM"]
    high_bubbles = [b for b in result.bubbles if b.risk_label in ("HOCH", "EXTREM")]

    rec_lines = []
    if prob >= 70:
        rec_lines.append("[bold red]HANDLUNGS­EMPFEHLUNG: Portfolio-Absicherung prüfen (Stop-Losses straffen, Hedge-ETFs).[/bold red]")
    elif prob >= 50:
        rec_lines.append("[yellow]HANDLUNGS­EMPFEHLUNG: Erhöhte Vorsicht – keine neuen großen Positionen, SL überprüfen.[/yellow]")
    elif prob >= 30:
        rec_lines.append("[cyan]HANDLUNGS­EMPFEHLUNG: Normaler Betrieb, aber Indikatoren im Auge behalten.[/cyan]")
    else:
        rec_lines.append("[green]HANDLUNGS­EMPFEHLUNG: Markt-Umfeld ruhig. Normale Strategie.[/green]")
    if alarm_inds:
        rec_lines.append(f"Alarm-Indikatoren: {', '.join(i.name for i in alarm_inds)}")
    if high_bubbles:
        rec_lines.append(f"Überhitzte Sektoren meiden: {', '.join(b.name for b in high_bubbles[:3])}")
    if best_match and best_match.similarity_pct >= 50:
        rec_lines.append(f"Ähnelt historisch am meisten: {best_match.label} ({best_match.similarity_pct}%)")

    console.print(Panel("\n".join(rec_lines), title="Einschätzung", border_style="cyan"))
