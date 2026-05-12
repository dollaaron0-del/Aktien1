"""
Stock Sentiment Trading Bot
Sammelt Nachrichten & Reddit-Posts, analysiert per Claude API,
und handelt Aktien automatisch (Paper-Trading).

Starten: python main.py
         python main.py --once   (einmalige Analyse, dann beenden)
         python main.py --status (Portfolioübersicht)
"""

import argparse
import sys
import schedule
import time
from datetime import datetime
from typing import List, Dict

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import config
from collectors import RedditCollector, YahooCollector, NewsAPICollector
from analyzers import ClaudeAnalyzer, AnalysisResult
from broker.paper_broker import PaperBroker
from portfolio import Portfolio
from strategy import SwingStrategy

console = Console()


def collect_news(ticker: str) -> List[Dict]:
    yahoo = YahooCollector()
    reddit = RedditCollector()
    newsapi = NewsAPICollector()

    items = []
    items += yahoo.collect(ticker)
    items += reddit.collect(ticker)
    items += newsapi.collect(ticker)

    # Deduplicate by title
    seen = set()
    unique = []
    for item in items:
        key = item.get("title", "").lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def run_analysis_cycle(portfolio: Portfolio, broker: PaperBroker, strategy: SwingStrategy):
    console.rule(f"[bold blue]Analyse-Zyklus – {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    analyzer = ClaudeAnalyzer()
    yahoo = YahooCollector()
    results: List[AnalysisResult] = []

    # First, check stop-loss / take-profit on open positions
    exit_actions = strategy.check_open_positions()
    for action in exit_actions:
        console.print(f"  [yellow]{action}[/yellow]")

    for ticker in config.watchlist:
        console.print(f"\n[cyan]Sammle Daten für {ticker}...[/cyan]")
        news = collect_news(ticker)
        price_data = yahoo.get_price_data(ticker)
        console.print(f"  {len(news)} Artikel gefunden | Kurs: ${price_data.get('current_price', 'N/A')}")

        if not news:
            console.print(f"  [dim]Keine Nachrichten – übersprungen[/dim]")
            continue

        console.print(f"  [cyan]Analysiere mit Claude ({config.claude_model})...[/cyan]")
        analysis = analyzer.analyze(ticker, news, price_data)
        results.append(analysis)

        _print_analysis(analysis)

        action = strategy.evaluate(analysis)
        if action:
            console.print(f"  [bold green]{action}[/bold green]")

    _print_portfolio_summary(portfolio, broker)


def _print_analysis(a: AnalysisResult):
    color = {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}.get(a.direction, "white")
    conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(a.confidence, "white")
    rec_color = {"BUY": "bold green", "SELL": "bold red", "HOLD": "yellow", "SKIP": "dim"}.get(a.recommendation, "white")

    console.print(
        f"  Sentiment: [{color}]{a.direction}[/{color}] "
        f"(Score: {a.sentiment_score:.2f}) | "
        f"Konfidenz: [{conf_color}]{a.confidence}[/{conf_color}] | "
        f"Empfehlung: [{rec_color}]{a.recommendation}[/{rec_color}]"
    )
    if a.entry_rationale:
        console.print(f"  Begründung: [italic]{a.entry_rationale}[/italic]")
    if a.key_catalysts:
        console.print(f"  Katalysatoren: {', '.join(a.key_catalysts[:3])}")
    if a.risk_factors:
        console.print(f"  Risiken: {', '.join(a.risk_factors[:3])}")


def _print_portfolio_summary(portfolio: Portfolio, broker: PaperBroker):
    positions = portfolio.all_positions()
    prices = broker.get_prices(list(positions.keys())) if positions else {}
    total = portfolio.total_value(prices)

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
            ticker,
            f"{pos.shares:.2f}",
            f"${pos.entry_price:.2f}",
            f"${price:.2f}",
            pnl_str,
            f"${pos.stop_loss:.2f}",
            f"${pos.take_profit:.2f}",
            str(days),
        )

    console.print()
    console.print(table)
    console.print(
        Panel(
            f"Cash: [bold]${portfolio.cash:,.2f}[/bold]  |  "
            f"Gesamtwert: [bold]${total:,.2f}[/bold]",
            title="Kapital",
            border_style="blue",
        )
    )


def show_status():
    broker = PaperBroker()
    portfolio = Portfolio(config.initial_capital)
    _print_portfolio_summary(portfolio, broker)

    trades = portfolio.trade_history()
    if trades:
        trade_table = Table(title="Trade-History", box=box.ROUNDED)
        trade_table.add_column("Datum", style="dim")
        trade_table.add_column("Ticker", style="cyan")
        trade_table.add_column("Aktion")
        trade_table.add_column("Stück", justify="right")
        trade_table.add_column("Kurs", justify="right")
        trade_table.add_column("P&L", justify="right")
        trade_table.add_column("Grund")

        for t in trades[-20:]:
            pnl = t.pnl
            pnl_str = f"[green]+${pnl:.2f}[/green]" if pnl > 0 else (f"[red]-${abs(pnl):.2f}[/red]" if pnl < 0 else "")
            action_color = "bold green" if t.action == "BUY" else "bold red"
            trade_table.add_row(
                t.timestamp[:10],
                t.ticker,
                f"[{action_color}]{t.action}[/{action_color}]",
                f"{t.shares:.2f}",
                f"${t.price:.2f}",
                pnl_str,
                (t.reason or "")[:40],
            )
        console.print(trade_table)


def main():
    parser = argparse.ArgumentParser(description="Stock Sentiment Trading Bot")
    parser.add_argument("--once", action="store_true", help="Einmalige Analyse ausführen")
    parser.add_argument("--status", action="store_true", help="Portfolioübersicht anzeigen")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if not config.anthropic_api_key:
        console.print("[bold red]Fehler: ANTHROPIC_API_KEY nicht gesetzt. Bitte .env Datei konfigurieren.[/bold red]")
        sys.exit(1)

    broker = PaperBroker()
    portfolio = Portfolio(config.initial_capital)
    strategy = SwingStrategy(portfolio, broker)

    console.print(Panel(
        f"[bold]Stock Sentiment Bot gestartet[/bold]\n"
        f"Broker: [cyan]{config.broker_mode.upper()}[/cyan] | "
        f"Watchlist: [cyan]{', '.join(config.watchlist)}[/cyan]\n"
        f"Analyse täglich um [cyan]{config.analysis_hour:02d}:{config.analysis_minute:02d} Uhr[/cyan]",
        border_style="green",
    ))

    if args.once:
        run_analysis_cycle(portfolio, broker, strategy)
        return

    # Schedule daily analysis
    schedule_time = f"{config.analysis_hour:02d}:{config.analysis_minute:02d}"
    schedule.every().day.at(schedule_time).do(run_analysis_cycle, portfolio, broker, strategy)

    # Also check stop-loss every hour during trading hours
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
