#!/usr/bin/env python3
"""
Strategy-Lab CLI — Roadmap Phase 1.

Backtestet eine registrierte Strategie über das Universum × lange Historie und
speichert eine Performance-Karte (data/strategy_cards/<name>.json). Baseline ist
die bestehende Swing-Mechanik.

GRENZE: technischer Backtest, KEINE News-Sentiment-Alpha (siehe strategy_lab.lab).
Reine Analyse, kein Live-Eingriff, keine LLM-Kosten.

Usage:
  python -m scripts.strategy_lab                      # baseline_swing, 15 J., Watchlist
  python -m scripts.strategy_lab --years 20
  python -m scripts.strategy_lab --strategy baseline_swing --tickers AAPL MSFT NVDA
  python -m scripts.strategy_lab --list               # registrierte Strategien
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402

from strategy_lab import lab, all_names, get, build_universe  # noqa: E402
from backtesting import report  # noqa: E402

console = Console()


def _base_universe(args) -> list:
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Strategy-Lab: historischer Strategie-Backtest")
    ap.add_argument("--strategy", default="baseline_swing")
    ap.add_argument("--years", type=int, default=15)
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--include-delisted", action="store_true",
                    help="Graveyard delisteter/pleitegegangener Titel mitnehmen "
                         "(Survivorship-Bias mildern)")
    ap.add_argument("--list", action="store_true", help="registrierte Strategien zeigen")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name in all_names():
            console.print(f"  • [bold]{name}[/bold] — {get(name).description}")
        return

    from backtesting import data_loader
    base = _base_universe(args)
    info = build_universe(base, args.include_delisted,
                          loader=data_loader.load if args.include_delisted else None,
                          years=args.years)
    universe = info.tickers
    strat = get(args.strategy)
    console.print(f"[bold]Strategy-Lab[/bold]: '{strat.name}' über {len(universe)} Ticker, "
                  f"{args.years} Jahre Historie")
    console.print("[dim]Technischer Backtest – ohne News-Sentiment-Alpha.[/dim]")
    console.print(f"[dim]{info.survivorship_note}[/dim]")

    portfolio, per_ticker = lab.evaluate(strat, universe, years=args.years)
    n_with_trades = sum(1 for m in per_ticker if m.n_trades > 0)
    if not n_with_trades:
        console.print("[yellow]Keine Trades simuliert (zu wenig Historie?).[/yellow]")
        return

    report.print_table(per_ticker, title=f"Strategy-Lab: {strat.name} ({args.years}J)")

    if not args.no_save:
        meta = {"universe_size": len(universe), "tickers_with_trades": n_with_trades,
                "years_requested": args.years, "params": strat.default_params,
                "include_delisted": args.include_delisted,
                "survivors": info.survivors,
                "graveyard_with_data": info.graveyard_with_data,
                "graveyard_requested": info.graveyard_requested}
        path = lab.save_card(strat.name, portfolio, meta)
        console.print(f"[dim]Karte gespeichert → {path}[/dim]")


if __name__ == "__main__":
    main()
