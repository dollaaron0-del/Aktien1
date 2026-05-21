"""
cli/tax_commands.py – CLI für Steuer-Report und Dividenden-Übersicht.

  python main.py --tax [--tax-year 2025] [--tax-csv]
  python main.py --dividends [--dividend-ticker AAPL]
  python main.py --risk-metrics
  python main.py --fx-pnl
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel

console = Console()


def run_risk_metrics() -> None:
    """Zeigt Sharpe, Sortino, Calmar, Max Drawdown aus Portfolio-History."""
    from portfolio.performance_tracker import PerformanceTracker

    tracker = PerformanceTracker()
    m = tracker.get_risk_metrics()

    if "message" in m:
        console.print(f"[yellow]{m['message']}[/yellow]")
        return

    def _color(val: float, good_positive: bool = True) -> str:
        if good_positive:
            return "green" if val > 0 else "red"
        return "red" if val > 0 else "green"

    sharpe_c = "green" if m["sharpe_ratio"] > 1.0 else "yellow" if m["sharpe_ratio"] > 0 else "red"
    sortino_c = "green" if m["sortino_ratio"] > 1.5 else "yellow" if m["sortino_ratio"] > 0 else "red"
    calmar_c = "green" if m["calmar_ratio"] > 1.0 else "yellow" if m["calmar_ratio"] > 0 else "red"
    dd_c = "red" if m["max_drawdown_pct"] < -15 else "yellow" if m["max_drawdown_pct"] < -5 else "green"
    ret_c = "green" if m["annualized_return_pct"] > 0 else "red"

    table = Table(title="📊 Risk-Metriken (Portfolio)", box=box.ROUNDED, show_header=True)
    table.add_column("Kennzahl", style="bold")
    table.add_column("Wert", justify="right")
    table.add_column("Bewertung", justify="center")

    table.add_row("Sharpe Ratio", f"[{sharpe_c}]{m['sharpe_ratio']:.3f}[/{sharpe_c}]",
                  "[green]✓ Gut[/green]" if m["sharpe_ratio"] > 1.0 else "[yellow]OK[/yellow]" if m["sharpe_ratio"] > 0 else "[red]⚠ Schlecht[/red]")
    table.add_row("Sortino Ratio", f"[{sortino_c}]{m['sortino_ratio']:.3f}[/{sortino_c}]",
                  "[green]✓ Gut[/green]" if m["sortino_ratio"] > 1.5 else "[yellow]OK[/yellow]" if m["sortino_ratio"] > 0 else "[red]⚠ Schlecht[/red]")
    table.add_row("Calmar Ratio", f"[{calmar_c}]{m['calmar_ratio']:.3f}[/{calmar_c}]",
                  "[green]✓ Gut[/green]" if m["calmar_ratio"] > 1.0 else "[yellow]OK[/yellow]" if m["calmar_ratio"] > 0 else "[red]⚠ Schlecht[/red]")
    table.add_row("Max. Drawdown", f"[{dd_c}]{m['max_drawdown_pct']:.2f}%[/{dd_c}]",
                  f"Dauer: {m['max_drawdown_duration_days']} Tage")
    table.add_row("Annualisierte Rendite", f"[{ret_c}]{m['annualized_return_pct']:+.2f}%[/{ret_c}]", "")
    table.add_row("Annualisierte Volatilität", f"{m['volatility_annual_pct']:.2f}%", "")
    table.add_row("Portfolio-Snapshots", str(m["total_snapshots"]), "Basis: letzte 365 Tage")

    console.print(table)
    console.print("[dim]Sharpe > 1.0 = gut | Sortino > 1.5 = gut | Calmar > 1.0 = gut[/dim]")


def run_tax_report(year: Optional[int] = None, export_csv: bool = False) -> None:
    """Zeigt Abgeltungssteuer-Übersicht für das angegebene Jahr."""
    from portfolio.tax_calculator import TaxCalculator
    from portfolio.dividend_tracker import DividendTracker

    y = year or datetime.utcnow().year
    tc = TaxCalculator()
    dt = DividendTracker()
    div_income = dt.get_total_income(year=y)

    s = tc.get_yearly_summary(y, dividend_income_eur=div_income)
    frei = tc.get_freistellung_status(y)

    console.print(Panel(
        f"[bold]Steuer-Report {y}[/bold]  |  "
        f"Abgeltungssteuer 25% + Soli = [bold]26,375%[/bold]  |  "
        f"Freistellungsauftrag [bold]1.000 €[/bold]",
        border_style="cyan",
    ))

    # Freistellungsauftrag
    frei_bar_len = int(frei["pct_used"] / 10)
    frei_bar = "█" * frei_bar_len + "░" * (10 - frei_bar_len)
    frei_color = "green" if frei["pct_used"] < 70 else "yellow" if frei["pct_used"] < 100 else "red"
    console.print(
        f"  Freistellungsauftrag: [{frei_color}]{frei_bar}[/{frei_color}] "
        f"[bold]{frei['used_eur']:.2f} €[/bold] / {frei['freistellungsauftrag_eur']:.0f} €  "
        f"(noch {frei['remaining_eur']:.2f} € frei)"
    )
    console.print()

    # Übersichtstabelle
    summary_table = Table(box=box.SIMPLE, show_header=False)
    summary_table.add_column("", style="dim")
    summary_table.add_column("", justify="right", style="bold")

    green = lambda v: f"[green]+{v:.2f} €[/green]" if v >= 0 else f"[red]{v:.2f} €[/red]"
    summary_table.add_row("Realisierte Gewinne", f"[green]{s.gross_gains_eur:.2f} €[/green]")
    summary_table.add_row("Realisierte Verluste", f"[red]{s.gross_losses_eur:.2f} €[/red]")
    summary_table.add_row("Dividenden-Einnahmen", f"{s.dividend_income_eur:.2f} €")
    summary_table.add_row("─" * 30, "─" * 14)
    summary_table.add_row("Netto-Kapitalertrag", green(s.net_gain_eur))
    summary_table.add_row("Freistellungsauftrag genutzt", f"[green]−{s.freistellungsauftrag_used_eur:.2f} €[/green]")
    summary_table.add_row("─" * 30, "─" * 14)
    summary_table.add_row("Zu versteuern", f"[bold]{s.taxable_gain_eur:.2f} €[/bold]")
    summary_table.add_row(
        f"Abgeltungssteuer (26,375%)",
        f"[bold red]{s.tax_due_eur:.2f} €[/bold red]",
    )
    if s.net_gain_eur > 0:
        summary_table.add_row("Effektiver Steuersatz", f"{s.effective_rate_pct:.1f}%")

    console.print(summary_table)

    # Einzeltransaktionen
    if s.gains:
        detail = Table(title=f"Einzeltransaktionen {y}", box=box.ROUNDED)
        detail.add_column("Ticker")
        detail.add_column("Kauf", justify="center")
        detail.add_column("Verkauf", justify="center")
        detail.add_column("Anteile", justify="right")
        detail.add_column("Einstand (€)", justify="right")
        detail.add_column("Erlöse (€)", justify="right")
        detail.add_column("G/V (€)", justify="right")

        for g in s.gains:
            gv_color = "green" if g.gross_gain_eur >= 0 else "red"
            gv = f"[{gv_color}]{g.gross_gain_eur:+.2f}[/{gv_color}]"
            detail.add_row(
                g.ticker, g.buy_date[:10], g.sell_date[:10],
                f"{g.shares:.2f}", f"{g.cost_basis_eur:.2f}",
                f"{g.proceeds_eur:.2f}", gv,
            )
        console.print(detail)
    else:
        console.print(f"[dim]Keine realisierten Transaktionen in {y}.[/dim]")

    if export_csv:
        csv_str = tc.export_csv(y, div_income)
        out_path = os.path.join("data", f"steuerbericht_{y}.csv")
        os.makedirs("data", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(csv_str)
        console.print(f"\n[green]CSV exportiert:[/green] {out_path}")


def run_dividend_overview(ticker: Optional[str] = None) -> None:
    """Zeigt Dividenden-History und bevorstehende Ex-Div-Termine."""
    from portfolio.dividend_tracker import DividendTracker
    from portfolio import Portfolio
    from config import config

    dt = DividendTracker()
    portfolio = Portfolio(config.initial_capital)
    positions = portfolio.all_positions()
    position_shares = {t: pos.shares for t, pos in positions.items()}

    # Jahresübersicht aktuelles Jahr
    current_year = datetime.utcnow().year
    yearly = dt.get_yearly_summary(current_year)

    console.print(Panel(
        f"[bold]Dividenden {current_year}[/bold]  |  "
        f"Gesamteinnahmen: [bold green]{yearly['total_income']:.2f}[/bold green]",
        border_style="green",
    ))

    # Einzel-History
    history = dt.get_dividend_history(ticker=ticker)
    if history:
        hist_table = Table(title="Erhaltene Dividenden", box=box.ROUNDED)
        hist_table.add_column("Ticker")
        hist_table.add_column("Ex-Datum")
        hist_table.add_column("Zahlung")
        hist_table.add_column("pro Aktie", justify="right")
        hist_table.add_column("Anteile", justify="right")
        hist_table.add_column("Total", justify="right")
        hist_table.add_column("Währung", justify="center")

        for h in history[:20]:
            hist_table.add_row(
                h["ticker"], h["ex_date"], h["pay_date"] or "–",
                f"{h['amount_per_share']:.4f}", f"{h['shares_held']:.2f}",
                f"[green]{h['total_amount']:.2f}[/green]", h["currency"],
            )
        console.print(hist_table)
    else:
        console.print("[dim]Noch keine Dividenden erfasst.[/dim]")
        console.print("[dim]Dividenden erfassen: portfolio.dividend_tracker.DividendTracker().record_dividend(...)[/dim]")

    # Bevorstehende Ex-Div-Termine (aus Watchlist + offenen Positionen)
    tickers_to_check = list(positions.keys())
    if tickers_to_check:
        console.print("\n[bold]Bevorstehende Ex-Dividenden-Termine:[/bold]")
        upcoming = dt.fetch_upcoming_dividends(tickers_to_check, position_shares)
        if upcoming:
            up_table = Table(box=box.ROUNDED)
            up_table.add_column("Ticker")
            up_table.add_column("Ex-Datum")
            up_table.add_column("Zahldatum")
            up_table.add_column("pro Aktie", justify="right")
            up_table.add_column("Erwartete Zahlung", justify="right")
            up_table.add_column("Währung", justify="center")

            for d in upcoming:
                up_table.add_row(
                    d.ticker, d.ex_date, d.pay_date or "–",
                    f"{d.amount_per_share:.4f}",
                    f"[green]{d.total_amount:.2f}[/green]", d.currency,
                )
            console.print(up_table)
        else:
            console.print("[dim]Keine bevorstehenden Ex-Dividenden-Termine gefunden.[/dim]")


def run_fx_pnl() -> None:
    """Zeigt unrealisierten P&L aufgeschlüsselt nach Handelswährung."""
    from portfolio import Portfolio
    from config import config

    portfolio = Portfolio(config.initial_capital)
    positions = portfolio.all_positions()

    if not positions:
        console.print("[yellow]Keine offenen Positionen.[/yellow]")
        return

    # Aktuelle Preise holen
    prices: dict = {}
    try:
        import yfinance as yf
        for ticker in positions:
            try:
                prices[ticker] = float(yf.Ticker(ticker).info.get("regularMarketPrice") or 0)
            except Exception:
                pass
    except ImportError:
        pass

    summary = portfolio.get_multicurrency_summary(prices)

    console.print(Panel("[bold]Multi-Währungs P&L[/bold]", border_style="cyan"))

    if not summary["by_currency"]:
        console.print("[dim]Keine Positionen mit Währungsinformationen.[/dim]")
        return

    table = Table(box=box.ROUNDED)
    table.add_column("Währung")
    table.add_column("FX-Rate (→USD)", justify="right")
    table.add_column("P&L (Lokalwährung)", justify="right")
    table.add_column("P&L (USD)", justify="right")
    table.add_column("Positionen")

    for ccy, data in summary["by_currency"].items():
        pnl_c = "green" if data["pnl_local"] >= 0 else "red"
        table.add_row(
            f"[bold]{ccy}[/bold]",
            f"{data['fx_rate']:.4f}",
            f"[{pnl_c}]{data['pnl_local']:+.2f}[/{pnl_c}]",
            f"[{pnl_c}]{data['pnl_usd']:+.2f}[/{pnl_c}]",
            ", ".join(data["tickers"]),
        )

    total_c = "green" if summary["total_pnl_usd"] >= 0 else "red"
    console.print(table)
    console.print(
        f"\n  Gesamt unrealisierter P&L: [{total_c}][bold]{summary['total_pnl_usd']:+.2f} USD[/bold][/{total_c}]"
    )
    console.print(f"  [dim]FX-Rates: {summary['fx_rates']}[/dim]")
