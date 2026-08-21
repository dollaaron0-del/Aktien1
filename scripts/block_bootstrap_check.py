#!/usr/bin/env python3
"""
Block-Bootstrap vs. i.i.d.-Bootstrap auf echten Backtest-Trade-Renditen —
Roadmap 6.8(d) "Resampling statt Synthetik".

Sammelt Backtest-Trade-Zeilen (wie 6.5b/scripts/meta_label.py), sortiert
chronologisch nach Entry-Datum und vergleicht die i.i.d.-Bootstrap-CI
(walkforward._bootstrap_ci) mit der seriell-korrelationsbewussten
Block-Bootstrap-CI (anti_overfit.block_bootstrap_ci, Roadmap 6.8d) auf
denselben Werten — macht sichtbar, ob/wie sehr die härtere Validierung
das Bild ändert. Siehe strategy_lab/anti_overfit.py-Docstring: bewusst NICHT
automatisch in die bestehenden ROBUST/SIGNAL-Verdikte verdrahtet.

Usage:
  python -m scripts.block_bootstrap_check --strategy donchian_breakout
  python -m scripts.block_bootstrap_check --tickers AAPL MSFT --block-size 15
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402

from strategy_lab import all_names  # noqa: E402
from strategy_lab.anti_overfit import block_bootstrap_ci  # noqa: E402
from strategy_lab.meta_label import build_training_rows  # noqa: E402
from strategy_lab.walkforward import _bootstrap_ci  # noqa: E402

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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", nargs="*", default=None, help="Default: alle Familien")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--total-years", type=int, default=20)
    ap.add_argument("--block-size", type=int, default=10)
    args = ap.parse_args()

    universe = _base_universe(args)
    strategies = args.strategy or all_names()
    console.print(f"[bold]Block-Bootstrap-Check[/bold] über {len(universe)} Ticker | "
                  f"Familien: {', '.join(strategies)} | block_size={args.block_size}")

    df = build_training_rows(strategies, universe, total_years=args.total_years)
    if df.empty:
        console.print("[red]Keine Trades gesammelt — Universum/Historie prüfen.[/red]")
        return
    df = df.sort_values("entry_date")
    values = df["return_pct"].tolist()
    console.print(f"[dim]{len(values)} chronologisch sortierte Trade-Renditen "
                  f"(Win-Rate {df['win'].mean()*100:.1f}%).[/dim]\n")

    iid_lo, iid_hi, iid_p = _bootstrap_ci(values)
    blk_lo, blk_hi, blk_p = block_bootstrap_ci(values, block_size=args.block_size)

    console.print(f"i.i.d.-Bootstrap   (90%-CI): [{iid_lo:+.4f}, {iid_hi:+.4f}]  P(≤0)={iid_p:.0%}")
    console.print(f"Block-Bootstrap    (90%-CI): [{blk_lo:+.4f}, {blk_hi:+.4f}]  P(≤0)={blk_p:.0%}")
    width_iid, width_blk = iid_hi - iid_lo, blk_hi - blk_lo
    console.print(f"\nCI-Breite: i.i.d. {width_iid:.4f} vs. Block {width_blk:.4f} "
                  f"({'+' if width_blk >= width_iid else ''}{(width_blk - width_iid):.4f})")

    iid_verdict = "SIGNIFIKANT" if iid_lo > 0 else "NICHT SIGNIFIKANT"
    blk_verdict = "SIGNIFIKANT" if blk_lo > 0 else "NICHT SIGNIFIKANT"
    if iid_verdict != blk_verdict:
        console.print(f"\n[yellow]VERDIKT-WECHSEL: i.i.d. sagt {iid_verdict}, "
                      f"Block-Bootstrap sagt {blk_verdict}.[/yellow]")
    else:
        console.print(f"\n[dim]Beide Verdikte stimmen überein: {iid_verdict}.[/dim]")


if __name__ == "__main__":
    main()
