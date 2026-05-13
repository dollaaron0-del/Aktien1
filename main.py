"""
Stock Sentiment Trading Bot
Sammelt Nachrichten & Reddit-Posts, analysiert per Claude API,
und handelt Aktien automatisch (Paper-Trading).

Starten:  python main.py
          python main.py --once      (einmalige Analyse, dann beenden)
          python main.py --status    (Portfolioübersicht)
          python main.py --report    (Lernbericht + Phaseninfo)
          python main.py --dashboard (Startet Streamlit-Dashboard)
"""

import argparse
import subprocess
import sys
import schedule
import time
from datetime import datetime
from typing import List, Dict, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import config
from collectors import (
    RedditCollector, YahooCollector, NewsAPICollector,
    InsiderCollector, USASpendingCollector,
    SECEdgarCollector, StockTwitsCollector, WireCollector,
)
from collectors.news_archive import NewsArchive
from analyzers import ClaudeAnalyzer, AnalysisResult
from broker.paper_broker import PaperBroker
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from strategy import SwingStrategy
from notifier.telegram_notifier import TelegramNotifier

console = Console()


def _make_phase_ctrl() -> PhaseController:
    return PhaseController(
        initial_capital=config.initial_capital,
        growth_target_multiple=config.growth_target_multiple,
        monthly_target_eur=config.monthly_distribution_eur,
        buffer_months=config.distribution_buffer_months,
    )


def collect_news(ticker: str, archive: NewsArchive) -> tuple[List[Dict], Dict[str, int]]:
    """
    Collects fresh news from all sources (incl. Congressional + SEC insider trades),
    stores them in the 30-day archive, and returns (deduplicated_items, sources_breakdown).
    """
    yahoo        = YahooCollector()
    reddit       = RedditCollector()
    newsapi      = NewsAPICollector()
    insider      = InsiderCollector(lookback_days=90)
    usaspending  = USASpendingCollector(lookback_days=180, min_award_usd=1_000_000)
    sec_edgar    = SECEdgarCollector(lookback_days=30)
    stocktwits   = StockTwitsCollector(lookback_hours=48)
    wire         = WireCollector(lookback_days=7)

    yahoo_items     = yahoo.collect(ticker)
    reddit_items    = reddit.collect(ticker)
    newsapi_items   = newsapi.collect(ticker)
    insider_items   = insider.collect(ticker)
    contract_items  = usaspending.collect(ticker)
    edgar_items     = sec_edgar.collect(ticker)
    twits_items     = stocktwits.collect(ticker)
    wire_items      = wire.collect(ticker)

    sources_breakdown = {
        "yahoo":       len(yahoo_items),
        "reddit":      len(reddit_items),
        "newsapi":     len(newsapi_items),
        "insider":     len(insider_items),
        "usaspending": len(contract_items),
        "sec_edgar":   len(edgar_items),
        "stocktwits":  len(twits_items),
        "wire":        len(wire_items),
    }

    all_items = (
        yahoo_items + reddit_items + newsapi_items + insider_items
        + contract_items + edgar_items + twits_items + wire_items
    )

    # Archive everything before deduplication (archive handles its own dedup)
    archive.store(ticker, all_items)

    # Deduplicate for the current analysis batch
    seen: set = set()
    unique: List[Dict] = []
    for item in all_items:
        key = (item.get("title") or "").lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    return unique, sources_breakdown


def run_analysis_cycle(
    portfolio: Portfolio,
    broker: PaperBroker,
    strategy: SwingStrategy,
    tracker: PerformanceTracker,
    phase_ctrl: PhaseController,
    archive: NewsArchive,
):
    console.rule(f"[bold blue]Analyse-Zyklus – {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    analyzer = ClaudeAnalyzer()
    yahoo = YahooCollector()

    # Track all material trade actions for the daily summary
    cycle_actions: List[str] = []

    # Check stop-loss / take-profit first (no Claude needed)
    exit_actions = strategy.check_open_positions()
    for action in exit_actions:
        console.print(f"  [yellow]{action}[/yellow]")
    cycle_actions.extend(exit_actions)

    for ticker in config.watchlist:
        console.print(f"\n[cyan]Sammle Daten für {ticker}...[/cyan]")

        news, sources_breakdown = collect_news(ticker, archive)
        price_data = yahoo.get_price_data(ticker)

        # Load 30-day history, excluding articles already in current batch
        current_titles = {item.get("title") or "" for item in news}
        historical = archive.get_history(ticker, days=30, exclude_titles=current_titles)

        src = sources_breakdown
        console.print(
            f"  [bold]{len(news)}[/bold] Artikel total | {len(historical)} historisch | "
            f"Yahoo:{src['yahoo']} Reddit:{src['reddit']} NewsAPI:{src['newsapi']} "
            f"SEC:{src['sec_edgar']} Wire:{src['wire']} "
            f"Twits:{src['stocktwits']} Insider:{src['insider']} "
            f"Contracts:{src['usaspending']} | "
            f"Kurs: ${price_data.get('current_price', 'N/A')}"
        )

        if not news:
            console.print("  [dim]Keine Nachrichten – übersprungen[/dim]")
            continue

        # Build open-position context for thesis check
        open_position_ctx = strategy.build_open_position_context(ticker)
        if open_position_ctx:
            console.print(f"  [yellow]Offene Position – prüfe Kaufthese...[/yellow]")

        console.print(f"  [cyan]Analysiere mit Claude ({config.claude_model})...[/cyan]")
        analysis = analyzer.analyze(
            ticker=ticker,
            news_items=news,
            price_data=price_data,
            historical_news=historical if historical else None,
            open_position=open_position_ctx,
        )

        _print_analysis(analysis)

        action = strategy.evaluate(analysis, sources_breakdown)
        if action:
            color = "bold red" if "VERKAUFT" in action else "bold green"
            console.print(f"  [{color}]{action}[/{color}]")
            # Only material trades go into the Telegram summary, not skip/hold notices
            if "GEKAUFT" in action or "VERKAUFT" in action:
                cycle_actions.append(action)

    # Record portfolio snapshot
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total_value = portfolio.total_value(prices)
    positions_value = total_value - portfolio.cash
    phase = phase_ctrl.current_phase(total_value)
    tracker.record_snapshot(total_value, portfolio.cash, positions_value, phase)

    # Clean up news older than 32 days
    archive.cleanup_old(keep_days=32)

    _print_portfolio_summary(portfolio, broker, phase_ctrl)

    # Send Telegram daily summary
    notifier = TelegramNotifier()
    notifier.notify_daily_summary(
        total_value=total_value,
        cash=portfolio.cash,
        open_positions=len(portfolio.all_positions()),
        phase=phase,
        progress_pct=phase_ctrl.progress_pct(total_value),
        actions_today=cycle_actions,
    )


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


def _print_portfolio_summary(portfolio: Portfolio, broker: PaperBroker, phase_ctrl: PhaseController):
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


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def show_status(portfolio: Portfolio, broker: PaperBroker, phase_ctrl: PhaseController):
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
    broker: PaperBroker,
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


def main():
    parser = argparse.ArgumentParser(description="Stock Sentiment Trading Bot")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--dashboard", action="store_true")
    args = parser.parse_args()

    if args.dashboard:
        dashboard_path = __file__.replace("main.py", "dashboard/app.py")
        console.print("[cyan]Starte Streamlit-Dashboard...[/cyan]")
        subprocess.run(["streamlit", "run", dashboard_path])
        return

    broker = PaperBroker()
    portfolio = Portfolio(config.initial_capital)
    tracker = PerformanceTracker()
    phase_ctrl = _make_phase_ctrl()
    archive = NewsArchive()

    if args.status:
        show_status(portfolio, broker, phase_ctrl)
        return

    if args.report:
        show_report(tracker, phase_ctrl, portfolio, broker)
        return

    if not config.anthropic_api_key:
        console.print("[bold red]Fehler: ANTHROPIC_API_KEY nicht gesetzt.[/bold red]")
        sys.exit(1)

    strategy = SwingStrategy(portfolio, broker, tracker, phase_ctrl)

    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)
    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"

    console.print(Panel(
        f"[bold]Stock Sentiment Bot gestartet[/bold]\n"
        f"Broker: [cyan]{config.broker_mode.upper()}[/cyan] | "
        f"Watchlist: [cyan]{', '.join(config.watchlist)}[/cyan]\n"
        f"Analyse täglich um [cyan]{config.analysis_hour:02d}:{config.analysis_minute:02d} Uhr[/cyan] | "
        f"Nachrichtenarchiv: [cyan]30 Tage[/cyan]\n"
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}] | "
        f"Kapital: ${total:,.2f} | Ziel: ${phase_info['growth_target']:,.0f}",
        border_style="green",
    ))

    if args.once:
        run_analysis_cycle(portfolio, broker, strategy, tracker, phase_ctrl, archive)
        return

    schedule_time = f"{config.analysis_hour:02d}:{config.analysis_minute:02d}"
    schedule.every().day.at(schedule_time).do(
        run_analysis_cycle, portfolio, broker, strategy, tracker, phase_ctrl, archive
    )
    schedule.every().hour.do(strategy.check_open_positions)

    console.print(f"[dim]Nächste Analyse um {schedule_time} Uhr. Ctrl+C zum Beenden.[/dim]")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot gestoppt.[/yellow]")


if __name__ == "__main__":
    main()
