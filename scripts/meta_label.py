#!/usr/bin/env python3
"""
ML-Meta-Labeling CLI — Roadmap 6.5b.

Lernt aus zehntausenden Backtest-Signalausgängen (nicht den 78 echten
Trades — die bleiben Validierung), WELCHE mechanischen Signale im
Nachhinein Gewinner waren, aus Kontext-Merkmalen zum Signalzeitpunkt
(Regime/Trailing-Vola/Trailing-Rendite/Breadth). Siehe
strategy_lab/meta_label.py-Docstring für die Validierungs-Disziplin
(expandierendes Fenster + Šidák/Bootstrap über mehrere P(Win)-Schwellen).

Usage:
  python -m scripts.meta_label
  python -m scripts.meta_label --tickers AAPL MSFT NVDA --total-years 16
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
from strategy_lab.meta_label import build_training_rows, evaluate_meta_labeling  # noqa: E402

console = Console()
_VERDICT_COLOR = {"SIGNAL": "green", "NO_SIGNAL": "yellow"}


def _base_universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="ML-Meta-Labeling der mechanischen Signale (Roadmap 6.5b)")
    ap.add_argument("--strategy", nargs="*", default=None, help="Default: alle Familien")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--total-years", type=int, default=20)
    ap.add_argument("--n-blocks", type=int, default=6)
    ap.add_argument("--holdout", type=int, default=2, metavar="JAHRE")
    ap.add_argument("--thresholds", nargs="*", type=float, default=[0.50, 0.55, 0.60, 0.65])
    args = ap.parse_args()

    universe = _base_universe(args)
    strategies = args.strategy or all_names()
    console.print(f"[bold]ML-Meta-Labeling[/bold] über {len(universe)} Ticker | "
                  f"Familien: {', '.join(strategies)} | Historie {args.total_years}J")
    console.print("[dim]Lernt aus Backtest-Signalausgängen, nicht den echten Trades — "
                  "die bleiben Validierung (Roadmap 6.8c).[/dim]")

    console.print("[dim]Sammle Trainingszeilen (Backtest-Läufe je Familie×Ticker) …[/dim]")
    df = build_training_rows(strategies, universe, total_years=args.total_years)
    if df.empty:
        console.print("[red]Keine Trades gesammelt — Universum/Historie prüfen.[/red]")
        return
    console.print(f"[dim]{len(df)} Signal-Zeilen gesammelt "
                  f"(Win-Rate gesamt {df['win'].mean()*100:.1f}%, "
                  f"Ø-Return {df['return_pct'].mean()*100:+.2f}%).[/dim]")

    rep = evaluate_meta_labeling(df, n_blocks=args.n_blocks, holdout_years=args.holdout,
                                 thresholds=tuple(args.thresholds))
    if rep.n_eval_splits == 0:
        console.print("[red]Zu wenig Historie für auch nur einen Train/Test-Split "
                      "(mehr Ticker/Jahre oder --n-blocks senken).[/red]")
        return

    console.print(f"\n[bold]Baseline[/bold]: Win-Rate {rep.baseline_win_rate*100:.1f}% · "
                  f"Ø-Return {rep.baseline_avg_return*100:+.2f}% · "
                  f"{rep.n_eval_splits} Auswertungs-Blöcke · "
                  f"Ø-AUC {rep.mean_auc:.3f} · Ø-Brier {rep.mean_brier:.3f}")
    if rep.holdout_years > 0:
        console.print(f"[yellow]Holdout: jüngste {rep.holdout_years}J ausgespart — "
                      f"nie durchsucht, protokolliert.[/yellow]")

    table = Table(title="P(Win)-Schwellen vs. ungefiltert (Ø-Renditegewinn)",
                  box=box.ROUNDED, border_style="dim")
    for col in ["Schwelle", "Blöcke", "Ø-Gewinn", "Bootstrap-CI 90%", "P(≤0)", "Verdikt"]:
        table.add_column(col, justify="right" if col != "Verdikt" else "left")
    for t in rep.thresholds:
        vc = _VERDICT_COLOR.get(t.verdict, "white")
        table.add_row(
            f"{t.threshold:.2f}", str(t.n_splits), f"{t.mean_edge_gain:+.2%}",
            f"[{t.edge_gain_ci_lo:+.2%},{t.edge_gain_ci_hi:+.2%}]", f"{t.p_le0:.0%}",
            f"[{vc}]{t.verdict}[/{vc}]",
        )
    console.print(table)
    console.print(f"[dim]SIGNAL verlangt CI-Untergrenze > 0 UND Šidák-korrigierte Signifikanz "
                  f"über {rep.thresholds[0].n_thresholds_tested if rep.thresholds else 0} getestete "
                  f"Schwellen (α ≤ {rep.thresholds[0].alpha_adjusted:.4f}).[/dim]")


if __name__ == "__main__":
    main()
