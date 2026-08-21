#!/usr/bin/env python3
"""
Meta-Backtest des Allokators CLI — Roadmap 4.1.

Stellt an jedem rollierenden Stichtag die komplette Selektions-Pipeline
(Walk-Forward → Registry → weight_plan) NUR mit Vergangenheitsdaten nach und
fährt drei Arme out-of-sample durch den Portfolio-Backtest (2.2):

  allokator         – weight_plan aus der Stichtags-Registry (ohne Regime)
  allokator_regime  – dito + Regime-Faktor am Stichtag
  gleichgewichtung  – alle Familien gleichgewichtet (Baseline)

Leerer Plan (0 ACTIVE / kein Regime-Fit) heißt ehrlich: flat in diesem
Fenster — genau das hätte der Allokator live entschieden.

Usage:
  python -m scripts.meta_backtest
  python -m scripts.meta_backtest --tickers AAPL MSFT NVDA --total-years 16
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
from strategy_lab.meta_backtest import ARMS, run_meta_backtest  # noqa: E402

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
    ap = argparse.ArgumentParser(description="Meta-Backtest: Allokator vs. Gleichgewichtung (Roadmap 4.1)")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--total-years", type=int, default=20)
    ap.add_argument("--selection-years", type=int, default=8,
                    help="Mindest-Historie vor dem ersten Stichtag")
    ap.add_argument("--oos-years", type=int, default=2)
    ap.add_argument("--step-years", type=int, default=2)
    ap.add_argument("--max-combos", type=int, default=60)
    ap.add_argument("--workers", type=int, default=None,
                    help="Prozesse für die innere Grid-Search (0 = Kerne−1; "
                         "Default ENV STRATEGY_LAB_WORKERS bzw. 1 = seriell)")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--max-positions-per-theme", type=int, default=3)
    args = ap.parse_args()

    universe = _base_universe(args)
    strategies = all_names()

    console.print(f"[bold]Meta-Backtest des Allokators[/bold] über {len(universe)} Ticker | "
                  f"Familien: {', '.join(strategies)} | Kapital ${args.capital:,.0f}")
    console.print("[dim]Survivorship-Hinweis: yfinance liefert nur Überlebende — Renditen "
                  "aller Arme sind nach oben verzerrt; der PAARWEISE Vergleich der Arme "
                  "(gleiche Daten, gleiche Verzerrung) bleibt davon weitgehend unberührt.[/dim]\n")

    rep = run_meta_backtest(
        strategies, universe,
        total_years=args.total_years, selection_years=args.selection_years,
        oos_years=args.oos_years, step_years=args.step_years,
        max_combos=args.max_combos, workers=args.workers,
        initial_capital=args.capital, max_positions=args.max_positions,
        max_positions_per_theme=args.max_positions_per_theme,
    )
    if rep.n_windows == 0:
        console.print("[red]Keine Meta-Fenster (zu wenig Historie?).[/red]")
        return

    # Fenster-Tabelle
    table = Table(title="OOS-Ergebnis je Meta-Fenster", box=box.ROUNDED, border_style="dim")
    for col in ["Stichtag", "OOS bis", "Regime", "ACTIVE"] + [
            f"{arm} Ret/DD" for arm in ARMS]:
        table.add_column(col, justify="right" if "Ret" in col or col == "ACTIVE" else "left")
    for w in rep.windows:
        cells = [w.as_of, w.oos_end, w.regime, str(w.n_active)]
        for arm in ARMS:
            a = w.arms[arm]
            cells.append("flat" if a.plan_size == 0
                         else f"{a.total_return:+.1%} / {a.max_drawdown:.1%}")
        table.add_row(*cells)
    console.print(table)

    # Aggregat
    agg = Table(title="Aggregat über alle Meta-Fenster", box=box.ROUNDED, border_style="dim")
    for col in ["Arm", "Ø Return", "Median", "Ø MaxDD", "Worst MaxDD", "flat", "Trades"]:
        agg.add_column(col, justify="right" if col != "Arm" else "left")
    for arm in ARMS:
        s = rep.summary[arm]
        agg.add_row(arm, f"{s['mean_return']:+.2%}", f"{s['median_return']:+.2%}",
                    f"{s['mean_max_drawdown']:.2%}", f"{s['worst_max_drawdown']:.2%}",
                    f"{s['n_flat']}/{rep.n_windows}", str(s["n_trades"]))
    console.print(agg)

    # Paired-Differenzen
    console.print("\n[bold]Paired-Differenzen (je Meta-Fenster, Bootstrap-90%-CI):[/bold]")
    labels = {
        "allokator_vs_gleich": "Allokator − Gleichgewichtung (hilft die Selektion?)",
        "regime_vs_allokator": "Regime-Faktor − Allokator (bringt Regime zusätzlich was?)",
    }
    for key, label in labels.items():
        d = rep.diffs[key]
        console.print(f"  {label}: Ø {d['mean_diff']:+.2%}  "
                      f"CI [{d['ci_lo']:+.2%}, {d['ci_hi']:+.2%}]  P(≤0)={d['p_le0']:.0%}")
    console.print("\n[dim]Lesart: CI-Untergrenze > 0 → Arm ist signifikant besser. "
                  "CI um 0 → keine belegte Kante, Verdikt offen.[/dim]")


if __name__ == "__main__":
    main()
