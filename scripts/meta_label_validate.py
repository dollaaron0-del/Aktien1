#!/usr/bin/env python3
"""
Meta-Labeling-Modell gegen echte Live-Trades validieren — Roadmap 6.8(c).

Trainiert ein Modell auf zehntausenden Backtest-Signalausgängen (wie 6.5b),
wendet es auf die Kontext-Merkmale der ECHTEN Bot-Trades an (ExperienceStore,
label_source='live') und prüft, ob P(Win)-Schwellen dort ebenfalls eine
positive Kante zeigen. Siehe strategy_lab/meta_label_validation.py-Docstring
für die Kategorie-Lücke (kein "strategy"-Feature für Live-Trades) und das
Mindest-n-Gate.

Usage:
  python -m scripts.meta_label_validate
  python -m scripts.meta_label_validate --tickers AAPL MSFT NVDA --total-years 16
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from analyzers.experience_store import ExperienceStore  # noqa: E402
from strategy_lab import all_names  # noqa: E402
from strategy_lab.meta_label import build_training_rows  # noqa: E402
from strategy_lab.meta_label_validation import (  # noqa: E402
    DEFAULT_THRESHOLDS, collect_live_rows, validate_against_live)

console = Console()
_VERDICT_COLOR = {"SIGNAL": "green", "NO_SIGNAL": "yellow", "ZU_WENIG_DATEN": "dim"}


def _base_universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Meta-Labeling gegen echte Live-Trades validieren (Roadmap 6.8c)")
    ap.add_argument("--strategy", nargs="*", default=None, help="Default: alle Familien")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--total-years", type=int, default=20)
    ap.add_argument("--thresholds", nargs="*", type=float, default=list(DEFAULT_THRESHOLDS))
    ap.add_argument("--min-n", type=int, default=15)
    args = ap.parse_args()

    universe = _base_universe(args)
    strategies = args.strategy or all_names()
    console.print(f"[bold]Meta-Labeling-Live-Validierung[/bold] über {len(universe)} Ticker | "
                  f"Familien: {', '.join(strategies)} | Historie {args.total_years}J")
    console.print("[dim]Training auf Backtest-Signalen (6.5b), Validierung auf echten "
                  "Trades (Roadmap 6.8c) — strategie-freies Kontext-Feature-Set.[/dim]")

    console.print("[dim]Sammle Trainingszeilen (Backtest-Läufe je Familie×Ticker) …[/dim]")
    train_df = build_training_rows(strategies, universe, total_years=args.total_years)
    if train_df.empty:
        console.print("[red]Keine Backtest-Trainingszeilen — Universum/Historie prüfen.[/red]")
        return
    console.print(f"[dim]{len(train_df)} Backtest-Signal-Zeilen gesammelt.[/dim]")

    console.print("[dim]Lade echte Live-Trades (ExperienceStore) …[/dim]")
    store = ExperienceStore()
    try:
        live_df = collect_live_rows(store, total_years=args.total_years)
    finally:
        store.close()
    if live_df.empty:
        console.print("[yellow]Keine gelabelten echten Live-Trades mit verwertbarer "
                      "Preis-Historie — noch keine Validierung möglich.[/yellow]")
        return
    console.print(f"[dim]{len(live_df)} echte Trades verwertbar (Win-Rate "
                  f"{live_df['win'].mean()*100:.1f}%, Ø-P&L {live_df['pnl_pct'].mean()*100:+.2f}%).[/dim]")

    rep = validate_against_live(train_df, live_df, thresholds=tuple(args.thresholds), min_n=args.min_n)
    console.print(f"\n[bold]Baseline (alle {rep.n_live_scored} Live-Trades)[/bold]: "
                  f"Win-Rate {rep.baseline_win_rate*100:.1f}% · "
                  f"Ø-P&L {rep.baseline_mean_pnl_pct*100:+.2f}%"
                  + (f" · AUC {rep.auc:.3f} · Brier {rep.brier:.3f}" if rep.auc is not None else ""))

    table = Table(title="P(Win)-Schwellen vs. echte Trades (Ø-P&L, Bootstrap-CI 90%)",
                  box=box.ROUNDED, border_style="dim")
    for col in ["Schwelle", "n", "Win-Rate", "Ø-P&L", "CI 90%", "P(≤0)", "Verdikt"]:
        table.add_column(col, justify="right" if col != "Verdikt" else "left")
    for t in rep.thresholds:
        vc = _VERDICT_COLOR.get(t.verdict, "white")
        if t.verdict == "ZU_WENIG_DATEN":
            table.add_row(f"{t.threshold:.2f}", str(t.n), "—", "—", "—", "—",
                         f"[{vc}]{t.verdict} (< {rep.min_n})[/{vc}]")
            continue
        table.add_row(
            f"{t.threshold:.2f}", str(t.n), f"{t.win_rate*100:.1f}%", f"{t.mean_pnl_pct*100:+.2f}%",
            f"[{t.pnl_ci_lo*100:+.2f}%,{t.pnl_ci_hi*100:+.2f}%]", f"{t.p_le0:.0%}",
            f"[{vc}]{t.verdict}[/{vc}]",
        )
    console.print(table)
    console.print("[dim]SIGNAL verlangt CI-Untergrenze > 0 bei mindestens "
                  f"{rep.min_n} Trades über der Schwelle — mit heute noch wenigen echten "
                  "Trades ist ZU_WENIG_DATEN das ehrlich erwartete Ergebnis, kein Bug.[/dim]")


if __name__ == "__main__":
    main()
