#!/usr/bin/env python3
"""
Portfolio-Backtest CLI — Roadmap 2.2.

Simuliert EIN Portfolio (gemeinsamer Cash-Pool, Max-Positionen, Themen-
Kappung, Vol-Targeting) über den Allokator-Plan (weight_plan(), Phase 5)
UND über eine naive Gleichgewichtung derselben Strategien — validiert damit
zugleich, ob der Allokator gegenüber Gleichgewichtung tatsächlich hilft.

Usage:
  python -m scripts.portfolio_backtest
  python -m scripts.portfolio_backtest --years 10 --capital 100000 \
      --max-positions 10 --max-positions-per-theme 3
  python -m scripts.portfolio_backtest --no-vol-target
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from strategy_lab import build_universe, all_names  # noqa: E402
from strategy_lab.allocator import weight_plan  # noqa: E402
from strategy_lab.portfolio_backtest import (  # noqa: E402
    run_portfolio_backtest, equal_weight_plan, VolTargetConfig,
)

console = Console()


def _base_universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Portfolio-Backtest: weight_plan vs. Gleichgewichtung")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--years", type=int, default=15)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--max-positions-per-theme", type=int, default=3)
    ap.add_argument("--no-vol-target", action="store_true")
    args = ap.parse_args()

    from backtesting import data_loader

    base = _base_universe(args)
    info = build_universe(base, False, years=args.years)
    universe = info.tickers

    plan_alloc = weight_plan()
    if not plan_alloc:
        console.print("[yellow]weight_plan() ist leer — keine ACTIVE Strategie in der "
                      "Registry (data/strategy_registry.json). Allokator-Zeile zeigt "
                      "daher Flat/kein Trade; das ist der ehrliche Ist-Zustand, kein "
                      "Fehler.[/yellow]")
    plan_equal = equal_weight_plan(plan_alloc and [e["strategy"] for e in plan_alloc] or all_names())

    vol_cfg = VolTargetConfig(enabled=not args.no_vol_target)
    common = dict(universe=universe, years=args.years, loader=data_loader.load,
                  initial_capital=args.capital, max_positions=args.max_positions,
                  max_positions_per_theme=args.max_positions_per_theme, vol_target_cfg=vol_cfg)

    console.print(f"[bold]Portfolio-Backtest[/bold] über {len(universe)} Ticker | "
                  f"{args.years}J | Kapital ${args.capital:,.0f} | "
                  f"Max-Pos {args.max_positions} | Max/Thema {args.max_positions_per_theme} | "
                  f"Vol-Targeting {'aus' if args.no_vol_target else 'an'}")
    console.print(f"[dim]{info.survivorship_note}[/dim]")

    result_alloc = run_portfolio_backtest(plan_alloc, **common)
    result_equal = run_portfolio_backtest(plan_equal, **common)

    table = Table(title="weight_plan (Allokator) vs. Gleichgewichtung", box=box.ROUNDED, border_style="dim")
    for col in ["Variante", "Return", "MaxDD", "Sharpe", "CAGR", "Genommen", "Geskippt",
                "  Cash", "  MaxPos", "  Thema", "Peak-Pos", "Auslastung"]:
        table.add_column(col, justify="right" if col != "Variante" else "left")

    for label, r in [("weight_plan (Allokator)", result_alloc), ("Gleichgewichtung (naiv)", result_equal)]:
        table.add_row(
            label, f"{r.total_return:+.2%}", f"{r.max_drawdown:.2%}", f"{r.sharpe:.2f}",
            f"{r.cagr:+.2%}", str(r.n_trades_taken), str(r.n_trades_skipped),
            str(r.skip_reasons.cash), str(r.skip_reasons.max_positions),
            str(r.skip_reasons.theme_cap), str(r.peak_concurrent_positions),
            f"{r.capital_utilization:.1%}",
        )
    console.print(table)
    console.print("[dim]Sharpe hier datumsbasiert (Tages-Resampling der Equity-Kurve) — "
                  "NICHT dieselbe Formel wie backtesting.metrics.TickerMetrics.sharpe "
                  "(trade-count-basiert).[/dim]")


if __name__ == "__main__":
    main()
