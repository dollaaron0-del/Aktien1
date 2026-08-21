#!/usr/bin/env python3
"""
Permutation-Importance-CLI fürs Meta-Labeling-Modell — Roadmap 6.9(g).

Beantwortet: WELCHES der Kontext-Merkmale (Regime/Trailing-Vola/Trailing-
Rendite/Breadth/Strategie) trägt das Meta-Labeling-Signal (6.5b) tatsächlich?
Sieh strategy_lab/feature_importance.py-Docstring für die Validierungs-
Disziplin (identischer Block-Aufbau wie evaluate_meta_labeling, kein neuer
Split-Stil).

Usage:
  python -m scripts.feature_importance_study
  python -m scripts.feature_importance_study --tickers AAPL MSFT NVDA --n-repeats 20
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
from strategy_lab.feature_importance import evaluate_feature_importance  # noqa: E402
from strategy_lab.meta_label import build_training_rows  # noqa: E402

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
    ap = argparse.ArgumentParser(
        description="Permutation-Importance fürs Meta-Labeling-Modell (Roadmap 6.9g)")
    ap.add_argument("--strategy", nargs="*", default=None, help="Default: alle Familien")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--total-years", type=int, default=20)
    ap.add_argument("--n-blocks", type=int, default=6)
    ap.add_argument("--holdout", type=int, default=2, metavar="JAHRE")
    ap.add_argument("--n-repeats", type=int, default=10,
                     help="Permutations je Feature je Block (Default 10 — teuer, s. Docstring)")
    ap.add_argument("--scoring", default="roc_auc")
    args = ap.parse_args()

    universe = _base_universe(args)
    strategies = args.strategy or all_names()
    console.print(f"[bold]Permutation-Importance[/bold] über {len(universe)} Ticker | "
                  f"Familien: {', '.join(strategies)} | Historie {args.total_years}J")
    console.print("[dim]Teure Statistik (Roadmap 6.9g) — n_repeats × Features × Blöcke "
                  "Neu-Vorhersagen.[/dim]")

    console.print("[dim]Sammle Trainingszeilen (Backtest-Läufe je Familie×Ticker) …[/dim]")
    df = build_training_rows(strategies, universe, total_years=args.total_years)
    if df.empty:
        console.print("[red]Keine Trades gesammelt — Universum/Historie prüfen.[/red]")
        return
    console.print(f"[dim]{len(df)} Signal-Zeilen gesammelt.[/dim]")

    rep = evaluate_feature_importance(
        df, n_blocks=args.n_blocks, holdout_years=args.holdout,
        n_repeats=args.n_repeats, scoring=args.scoring,
    )
    if rep.n_blocks_evaluated == 0:
        console.print("[red]Zu wenig Historie für auch nur einen Block "
                      "(mehr Ticker/Jahre oder --n-blocks senken).[/red]")
        return

    console.print(f"\n[bold]{rep.n_blocks_evaluated}[/bold] Blöcke ausgewertet · "
                  f"Scoring={rep.scoring} · n_repeats={rep.n_repeats}")
    if args.holdout > 0:
        console.print(f"[yellow]Holdout: jüngste {args.holdout}J ausgespart — "
                      f"nie durchsucht, protokolliert.[/yellow]")

    table = Table(title="Feature-Wichtigkeit (Permutation, über Blöcke gemittelt)",
                  box=box.ROUNDED, border_style="dim")
    for col in ["Feature", "Ø-Importance", "Streuung", "Blöcke"]:
        table.add_column(col, justify="right" if col != "Feature" else "left")
    for f in rep.features:
        table.add_row(f.feature, f"{f.mean_importance:+.4f}", f"{f.std_importance:.4f}",
                      f"{f.n_blocks}/{rep.n_blocks_evaluated}")
    console.print(table)
    console.print(
        "\n[dim]Importance = Ø-Abfall im Scoring, wenn das Feature zufällig gemischt wird — "
        "höher heißt wichtiger. Nahe 0 oder negativ = das Feature trägt in diesem Modell "
        "praktisch nichts bei. Kein Wiring in den Live-Pfad, reine Diagnose.[/dim]"
    )


if __name__ == "__main__":
    main()
