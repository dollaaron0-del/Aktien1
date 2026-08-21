"""Rich console table and optional CSV export for backtest results."""
from __future__ import annotations

import csv
from typing import List

from rich import box
from rich.console import Console
from rich.table import Table

from backtesting.metrics import TickerMetrics, aggregate

console = Console()


def _pct(v: float, decimals: int = 1) -> str:
    return f"{v * 100:+.{decimals}f}%"


def _color_pct(v: float, decimals: int = 1) -> str:
    color = "green" if v > 0 else "red" if v < 0 else "white"
    return f"[{color}]{_pct(v, decimals)}[/{color}]"


def print_table(results: List[TickerMetrics], title: str = "Backtest Ergebnisse") -> None:
    """Print a rich table with per-ticker metrics plus a portfolio summary row."""
    valid = [m for m in results if m.n_trades > 0]
    if not valid:
        console.print("[yellow]Keine Trades simuliert.[/yellow]")
        return

    summary = aggregate(valid)

    table = Table(title=title, box=box.ROUNDED, show_footer=False, border_style="dim")
    table.add_column("Ticker",    style="bold cyan",  min_width=6)
    table.add_column("Trades",    justify="right")
    table.add_column("Win%",      justify="right")
    table.add_column("Ø Win",     justify="right")
    table.add_column("Ø Loss",    justify="right")
    table.add_column("P-Faktor",  justify="right")
    table.add_column("Total",     justify="right")
    table.add_column("Max DD",    justify="right")
    table.add_column("Sharpe",    justify="right")
    table.add_column("CAGR",      justify="right")
    table.add_column("TP1%",      justify="right")
    table.add_column("TP2%",      justify="right")

    def _row(m: TickerMetrics, style: str = "") -> None:
        pf_color = "green" if m.profit_factor >= 1.5 else "yellow" if m.profit_factor >= 1.0 else "red"
        table.add_row(
            m.ticker,
            str(m.n_trades),
            f"{m.win_rate * 100:.0f}%",
            _color_pct(m.avg_win),
            _color_pct(m.avg_loss),
            f"[{pf_color}]{m.profit_factor:.2f}[/{pf_color}]",
            _color_pct(m.total_return),
            _color_pct(m.max_drawdown),
            f"{m.sharpe:.2f}",
            _color_pct(m.cagr),
            f"{m.tp1_rate * 100:.0f}%",
            f"{m.tp2_rate * 100:.0f}%",
            style=style,
        )

    for m in valid:
        _row(m)

    table.add_section()
    _row(summary, style="bold")

    console.print(table)


def save_csv(results: List[TickerMetrics], path: str = "backtest_results.csv") -> None:
    """Export all metrics to CSV."""
    fields = [
        "ticker", "n_trades", "win_rate", "avg_win", "avg_loss",
        "profit_factor", "total_return", "max_drawdown", "sharpe",
        "cagr", "tp1_rate", "tp2_rate",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for m in results:
            writer.writerow({k: round(getattr(m, k), 6) if isinstance(getattr(m, k), float) else getattr(m, k)
                             for k in fields})
    console.print(f"[dim]CSV gespeichert: {path}[/dim]")
