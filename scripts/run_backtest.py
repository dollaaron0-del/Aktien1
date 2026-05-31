#!/usr/bin/env python3
"""
Ruflo Backtest Runner — simulates the swing strategy on historical OHLCV data.

Usage examples:
  python scripts/run_backtest.py                          # config watchlist, 3y
  python scripts/run_backtest.py AAPL MSFT NVDA TSLA     # specific tickers
  python scripts/run_backtest.py --years 5 --csv          # 5-year lookback + CSV
  python scripts/run_backtest.py --sl 0.08 --tp1 0.18    # custom parameters
  python scripts/run_backtest.py --help
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

import config as _cfg_module
from backtesting.data_loader import load
from backtesting.engine import BacktestConfig, run
from backtesting.metrics import compute
from backtesting import report

console = Console()


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ruflo Backtest Engine – technische EMA21-Swing-Strategie",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("tickers", nargs="*", metavar="TICKER",
                   help="Tickersymbole (Standard: Watchlist aus config/env)")
    p.add_argument("--years",    type=int,   default=3,
                   help="Lookback in Jahren (Standard: 3)")
    p.add_argument("--sl",       type=float, default=None,
                   help="Stop-Loss in Dezimal (z.B. 0.07 = 7%%)")
    p.add_argument("--tp1",      type=float, default=None,
                   help="TP1 in Dezimal (z.B. 0.15 = 15%%)")
    p.add_argument("--tp2",      type=float, default=None,
                   help="TP2 in Dezimal (z.B. 0.30 = 30%%)")
    p.add_argument("--ema-tol",  type=float, default=None,
                   help="EMA21-Toleranz für Pullback-Entry (z.B. 0.06)")
    p.add_argument("--max-hold", type=int,   default=None,
                   help="Max. Haltedauer in Handelstagen (Standard: 45)")
    p.add_argument("--csv",      action="store_true",
                   help="Ergebnisse als CSV speichern")
    p.add_argument("--csv-path", default="backtest_results.csv",
                   help="Pfad für CSV-Export (Standard: backtest_results.csv)")
    return p.parse_args()


def main() -> None:
    args   = _parse()
    app_cfg = _cfg_module.Config()

    tickers = [t.upper() for t in args.tickers] if args.tickers else app_cfg.watchlist

    bt_cfg = BacktestConfig(
        sl_pct        = args.sl      if args.sl      is not None else app_cfg.stop_loss_pct,
        tp1_pct       = args.tp1     if args.tp1     is not None else app_cfg.partial_tp_pct,
        tp2_pct       = args.tp2     if args.tp2     is not None else app_cfg.partial_tp2_pct,
        ema_tolerance = args.ema_tol if args.ema_tol is not None else app_cfg.entry_ema_max_deviation,
        max_hold_days = args.max_hold if args.max_hold is not None else 45,
        cooldown_days = app_cfg.sl_cooldown_days,
    )

    console.print(f"\n[bold white]Ruflo Backtest Engine[/bold white]")
    console.print(
        f"  Ticker: [cyan]{len(tickers)}[/cyan]  |  "
        f"Lookback: [cyan]{args.years}J[/cyan]  |  "
        f"SL=[yellow]{bt_cfg.sl_pct:.0%}[/yellow]  "
        f"TP1=[green]{bt_cfg.tp1_pct:.0%}[/green]  "
        f"TP2=[green]{bt_cfg.tp2_pct:.0%}[/green]  "
        f"EMA-Tol=[cyan]{bt_cfg.ema_tolerance:.0%}[/cyan]\n"
    )

    results = []

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Lade Daten & simuliere …", total=len(tickers))

        for ticker in tickers:
            progress.update(task, description=f"[cyan]{ticker}[/cyan] …")
            df = load(ticker, years=args.years)
            if df is None:
                console.print(f"  [dim]{ticker}: keine Daten — übersprungen[/dim]")
                progress.advance(task)
                continue

            trades = run(df, ticker, bt_cfg)
            m = compute(trades, ticker, years=float(args.years))
            results.append(m)
            progress.advance(task)

    if not results:
        console.print("[red]Keine Ergebnisse — alle Ticker ohne Daten.[/red]")
        sys.exit(1)

    console.print()
    report.print_table(results)

    if args.csv:
        report.save_csv(results, path=args.csv_path)


if __name__ == "__main__":
    main()
