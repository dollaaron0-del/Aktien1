#!/usr/bin/env python3
"""
CPCV CLI — Roadmap 6.4c (zweite Anti-Overfit-Achse neben Walk-Forward).

Testet dieselbe Strategie über viele verschiedene Kombinationen, WELCHER
Zeitblock als Out-of-Sample-Test dient (statt nur der einen vorwärtslaufenden
Fenster-Abfolge von scripts/walk_forward.py) — mit Purging/Embargo gegen
Informationslecks an den Blockgrenzen. Siehe strategy_lab/cpcv.py-Docstring
für die bewusste Vereinfachung ggü. dem CPCV-Originalpapier.

Usage:
  python -m scripts.cpcv
  python -m scripts.cpcv --strategy donchian_breakout --n-blocks 8 --test-blocks 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from strategy_lab import all_names, build_universe  # noqa: E402
from strategy_lab.cpcv import run_cpcv  # noqa: E402

console = Console()
_VERDICT_COLOR = {"ROBUST": "green", "FRAGILE": "yellow", "OVERFIT": "red"}


def _base_universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="CPCV — kombinatorische Purged Cross-Validation")
    ap.add_argument("--strategy", default=None, help="nur diese Strategie (sonst alle)")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--include-delisted", action="store_true")
    ap.add_argument("--total", type=int, default=20, help="Jahre Gesamt-Historie")
    ap.add_argument("--n-blocks", type=int, default=6)
    ap.add_argument("--test-blocks", type=int, default=1,
                    help="wie viele Blöcke je Pfad als Test dienen (k von n)")
    ap.add_argument("--purge-days", type=int, default=10)
    ap.add_argument("--embargo-days", type=int, default=5)
    ap.add_argument("--max-combos", type=int, default=60)
    ap.add_argument("--max-paths", type=int, default=30,
                    help="Deckel gegen C(n_blocks,test_blocks)-Explosion")
    ap.add_argument("--workers", type=int, default=None,
                    help="Prozesse für die Grid-Search (0 = Kerne−1)")
    args = ap.parse_args()

    from backtesting import data_loader
    base = _base_universe(args)
    info = build_universe(base, args.include_delisted,
                          loader=data_loader.load if args.include_delisted else None,
                          years=args.total)
    universe = info.tickers
    strategies = [args.strategy] if args.strategy else all_names()
    console.print(f"[bold]CPCV[/bold] über {len(universe)} Ticker | "
                  f"{args.n_blocks} Blöcke, {args.test_blocks} als Test je Pfad | "
                  f"Purge {args.purge_days}d / Embargo {args.embargo_days}d | "
                  f"Historie {args.total}J")
    console.print("[dim]Zweite Validierungs-Achse neben Walk-Forward — "
                  "prüft Robustheit über VIELE Test-Block-Kombinationen statt "
                  "nur einer Zeitrichtung.[/dim]")
    console.print(f"[dim]{info.survivorship_note}[/dim]")

    table = Table(title="CPCV-Robustheit (OOS über Pfade)", box=box.ROUNDED, border_style="dim")
    for col in ["Strategie", "Pfade", "Ø Test", "Median", "Worst", "OOS-CI 90%", "%pos", "WF-Eff", "Stabil", "Verdikt"]:
        table.add_column(col, justify="right" if col not in ("Strategie",) else "left")

    for name in strategies:
        rep = run_cpcv(
            name, universe, total_years=args.total, n_blocks=args.n_blocks,
            test_blocks=args.test_blocks, purge_days=args.purge_days,
            embargo_days=args.embargo_days, max_combos=args.max_combos,
            max_paths=args.max_paths, workers=args.workers)
        vc = _VERDICT_COLOR.get(rep.verdict, "white")
        ci_color = "green" if rep.test_return_ci_lo > 0 else "yellow"
        table.add_row(
            name, str(rep.n_windows),
            f"{rep.avg_test_return:+.2f}", f"{rep.median_test_return:+.2f}",
            f"{rep.worst_test_return:+.2f}",
            f"[{ci_color}][{rep.test_return_ci_lo:+.2f},{rep.test_return_ci_hi:+.2f}][/{ci_color}]",
            f"{rep.pct_positive_windows*100:.0f}%",
            f"{rep.wf_efficiency:.2f}", f"{rep.param_stability*100:.0f}%",
            f"[{vc}]{rep.verdict}[/{vc}]",
        )
    console.print(table)
    console.print("[dim]ROBUST verlangt hier dieselben Šidák/Bootstrap-Gates wie Walk-Forward — "
                  "nur über Test-Block-Kombinationen statt Zeitfenstern. Zwei robuste Achsen "
                  "sind ein deutlich stärkeres Signal als eine.[/dim]")


if __name__ == "__main__":
    main()
