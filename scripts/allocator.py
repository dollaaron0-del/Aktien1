#!/usr/bin/env python3
"""
Meta-Allokator CLI — Roadmap Phase 5.

Zeigt aus der Promotion-Registry (data/strategy_registry.json) den gekappten,
renormierten Gewichts-Plan der aktiven Strategien. Mit --tickers zusätzlich:
welche aktive Strategie feuert HEUTE für welche Ticker und die kombinierte,
gewichtete Konviktion je Ticker.

NUR Anzeige – kein Live-Eingriff (Bot bleibt pausiert).

Usage:
  python -m scripts.allocator
  python -m scripts.allocator --max-weight 0.5
  python -m scripts.allocator --tickers AAPL MSFT NVDA --years 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from backtesting import data_loader  # noqa: E402
from strategy_lab import allocator  # noqa: E402

console = Console()


def _universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Meta-Allokator: Gewichts-Plan + Heute-Signale")
    ap.add_argument("--max-weight", type=float, default=0.6,
                    help="Diversifikations-Cap je Strategie (Default 0.6)")
    ap.add_argument("--risk-budget", type=float, default=1.0)
    ap.add_argument("--tickers", nargs="*", default=None,
                    help="bei Angabe: Heute-Signale + kombinierte Konviktion zeigen")
    ap.add_argument("--years", type=int, default=2)
    args = ap.parse_args()

    plan = allocator.weight_plan(max_weight=args.max_weight, risk_budget=args.risk_budget)
    if not plan:
        console.print("[yellow]Keine aktiven Strategien in der Registry. "
                      "Erst `python -m scripts.walk_forward` laufen lassen.[/yellow]")
        return

    table = Table(title="Allokations-Plan (aktive Strategien)", box=box.ROUNDED, border_style="dim")
    for col in ["Strategie", "Gewicht", "Risiko-Anteil", "Parameter"]:
        table.add_column(col, justify="left" if col in ("Strategie", "Parameter") else "right")
    for e in plan:
        params = ", ".join(f"{k}={v}" for k, v in e["params"].items()) or "—"
        table.add_row(e["strategy"], f"{e['weight']*100:.0f}%",
                      f"{e['risk_fraction']*100:.0f}%", params)
    console.print(table)
    console.print(f"[dim]Cap {args.max_weight*100:.0f}%/Strategie · Risiko-Budget "
                  f"{args.risk_budget:.2f} · Phase-5-Naht, kein Live-Eingriff.[/dim]")

    if not args.tickers:
        return

    universe = _universe(args)
    fired = allocator.current_signals(universe, data_loader.load, plan=plan, years=args.years)
    conviction = allocator.combine_signals(fired, plan=plan)

    if not conviction:
        console.print("[dim]Heute kein aktives Signal über das Universum.[/dim]")
        return
    conv = Table(title="Heute-Konviktion (gewichtet kombiniert)", box=box.ROUNDED, border_style="dim")
    for col in ["Ticker", "Konviktion", "feuernde Strategien"]:
        conv.add_column(col, justify="left" if col != "Konviktion" else "right")
    fired_inv = {t: [s for s, ts in fired.items() if t in ts] for t in conviction}
    for t, c in conviction.items():
        conv.add_row(t, f"{c*100:.0f}%", ", ".join(fired_inv[t]))
    console.print(conv)


if __name__ == "__main__":
    main()
