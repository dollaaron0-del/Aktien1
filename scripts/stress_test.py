#!/usr/bin/env python3
"""
Stress-Test-Vergleich CLI — Roadmap 2.3 Teil B.

Jagt den Allokator-Plan durch jedes benannte Krisenfenster
(strategy_lab.stress_test.CRISIS_WINDOWS) und vergleicht dabei drei
De-Risking-Varianten für NEUE Entries:

  aus     – kein Risiko-Eingriff im Backtest (heutiger Default).
  binär   – ein einziger Tier bei 15 % Drawdown (spiegelt den bestehenden
            Live-CircuitBreaker, portfolio/circuit_breaker.py::MAX_DRAWDOWN_PCT).
  gestuft – DeriskConfig-Default (5/10/15 % Stufen, Roadmap 2.3 Teil B).

Bewusst KEIN neuer Simulationscode: nutzt ausschließlich
strategy_lab.stress_test.run_stress_test() (Teil A) mit unterschiedlichem
derisk_cfg.

Usage:
  python -m scripts.stress_test
  python -m scripts.stress_test --tickers AAPL MSFT NVDA --years 20
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from strategy_lab import all_names  # noqa: E402
from strategy_lab.allocator import weight_plan  # noqa: E402
from strategy_lab.portfolio_backtest import equal_weight_plan, DeriskConfig  # noqa: E402
from strategy_lab.stress_test import CRISIS_WINDOWS, run_stress_test  # noqa: E402

console = Console()

VARIANTS = {
    "aus":     DeriskConfig(enabled=False),
    "binär":   DeriskConfig(enabled=True, tiers=((0.15, 1.0),)),
    "gestuft": DeriskConfig(enabled=True),  # Default-Tiers (5/10/15 %)
}


def _base_universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Stress-Test: aus vs. binär vs. gestuftes De-Risking")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--max-positions-per-theme", type=int, default=3)
    args = ap.parse_args()

    universe = _base_universe(args)

    plan_alloc = weight_plan()
    if plan_alloc:
        plan = plan_alloc
        plan_source = "weight_plan (Allokator)"
    else:
        plan = equal_weight_plan(all_names())
        plan_source = "Gleichgewichtung (Allokator hat 0 ACTIVE Strategien)"

    console.print(f"[bold]Stress-Test[/bold] über {len(universe)} Ticker | Plan: {plan_source} | "
                  f"Kapital ${args.capital:,.0f} | Max-Pos {args.max_positions} | "
                  f"Max/Thema {args.max_positions_per_theme}")
    console.print("[dim]Survivorship-Hinweis: yfinance liefert keine Kursdaten für die "
                  "Graveyard-Ticker (Lehman, WaMu, Bear Stearns, …) – jeder Lauf hier "
                  "sieht nur Überlebende, unterschätzt also den echten historischen "
                  "Drawdown, insbesondere für GFC_2008.[/dim]\n")

    common = dict(universe=universe, initial_capital=args.capital,
                  max_positions=args.max_positions,
                  max_positions_per_theme=args.max_positions_per_theme)

    for window in CRISIS_WINDOWS:
        start, end = CRISIS_WINDOWS[window]
        table = Table(title=f"{window}  ({start} … {end})", box=box.ROUNDED, border_style="dim")
        for col in ["Variante", "Return", "MaxDD", "Sharpe", "Genommen", "Geskippt",
                    "  Cash", "  MaxPos", "  Thema", "  Derisk", "Peak-Pos"]:
            table.add_column(col, justify="right" if col != "Variante" else "left")

        for label, derisk_cfg in VARIANTS.items():
            r = run_stress_test(window, plan, derisk_cfg=derisk_cfg, **common)
            table.add_row(
                label, f"{r.total_return:+.2%}", f"{r.max_drawdown:.2%}", f"{r.sharpe:.2f}",
                str(r.n_trades_taken), str(r.n_trades_skipped),
                str(r.skip_reasons.cash), str(r.skip_reasons.max_positions),
                str(r.skip_reasons.theme_cap), str(r.skip_reasons.derisk),
                str(r.peak_concurrent_positions),
            )
        console.print(table)


if __name__ == "__main__":
    main()
