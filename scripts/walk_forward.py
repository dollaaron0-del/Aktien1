#!/usr/bin/env python3
"""
Walk-Forward CLI — Roadmap Phase 3 (Anti-Overfit-Herz).

Optimiert je Familie die Parameter NUR auf Trainingsfenstern und bewertet sie auf
ungesehenen Testfenstern. Rankt nach Robustheit (Median/Worst-OOS, %-positiv,
Walk-Forward-Effizienz), nicht nach Bestwert.

Usage:
  python -m scripts.walk_forward                       # alle Familien
  python -m scripts.walk_forward --strategy donchian_breakout
  python -m scripts.walk_forward --train 4 --test 2 --step 2 --total 20
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
from strategy_lab.walkforward import run_walk_forward  # noqa: E402
from strategy_lab.promotion import build_registry, save_registry  # noqa: E402

console = Console()
_VERDICT_COLOR = {"ROBUST": "green", "FRAGILE": "yellow", "OVERFIT": "red"}
_STATUS_COLOR = {"ACTIVE": "green", "WATCH": "yellow", "REJECTED": "red"}


def _base_universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-Forward-Selektion über Strategie-Familien")
    ap.add_argument("--strategy", default=None, help="nur diese Strategie (sonst alle)")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--include-delisted", action="store_true",
                    help="Graveyard delisteter/pleitegegangener Titel mitnehmen "
                         "(Survivorship-Bias mildern)")
    ap.add_argument("--total", type=int, default=20)
    ap.add_argument("--train", type=int, default=4)
    ap.add_argument("--test", type=int, default=2)
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--max-combos", type=int, default=60)
    ap.add_argument("--no-save", action="store_true",
                    help="Promotion-Registry NICHT auf data/strategy_registry.json schreiben")
    args = ap.parse_args()

    from backtesting import data_loader
    base = _base_universe(args)
    info = build_universe(base, args.include_delisted,
                          loader=data_loader.load if args.include_delisted else None,
                          years=args.total)
    universe = info.tickers
    strategies = [args.strategy] if args.strategy else all_names()
    console.print(f"[bold]Walk-Forward[/bold] über {len(universe)} Ticker | "
                  f"Train {args.train}J / Test {args.test}J / Schritt {args.step}J / "
                  f"Historie {args.total}J")
    console.print("[dim]Out-of-Sample-Validierung – technisch, kein News-Sentiment.[/dim]")
    console.print(f"[dim]{info.survivorship_note}[/dim]")

    table = Table(title="Walk-Forward-Robustheit (OOS)", box=box.ROUNDED, border_style="dim")
    for col in ["Strategie", "Fenster", "Ø Test", "Median", "Worst", "OOS-CI 90%", "%pos", "WF-Eff", "Stabil", "robuste Regime", "Verdikt"]:
        table.add_column(col, justify="right" if col not in ("Strategie", "robuste Regime") else "left")

    reports = []
    for name in strategies:
        rep = run_walk_forward(
            name, universe, total_years=args.total, train_years=args.train,
            test_years=args.test, step_years=args.step, max_combos=args.max_combos)
        reports.append(rep)
        vc = _VERDICT_COLOR.get(rep.verdict, "white")
        ci_color = "green" if rep.test_return_ci_lo > 0 else "yellow"
        table.add_row(
            name, str(rep.n_windows),
            f"{rep.avg_test_return:+.2f}", f"{rep.median_test_return:+.2f}",
            f"{rep.worst_test_return:+.2f}",
            f"[{ci_color}][{rep.test_return_ci_lo:+.2f},{rep.test_return_ci_hi:+.2f}][/{ci_color}]",
            f"{rep.pct_positive_windows*100:.0f}%",
            f"{rep.wf_efficiency:.2f}", f"{rep.param_stability*100:.0f}%",
            ", ".join(rep.robust_regimes) or "—",
            f"[{vc}]{rep.verdict}[/{vc}]",
        )
    console.print(table)
    console.print("[dim]ROBUST = OOS-Kante signifikant > 0 (Bootstrap-CI-Untergrenze) & stabil · "
                  "OVERFIT = Train gut, Test schlecht.[/dim]")

    # ── Phase 4: Promotion-Registry (Survivorship) ───────────────────────────
    registry = build_registry(reports)
    promo = Table(title="Promotion-Registry (Survivorship)", box=box.ROUNDED, border_style="dim")
    for col in ["Strategie", "Status", "Gewicht", "modale Parameter"]:
        promo.add_column(col, justify="left" if col in ("Strategie", "modale Parameter") else "right")
    for e in registry["entries"]:
        sc = _STATUS_COLOR.get(e["status"], "white")
        params = ", ".join(f"{k}={v}" for k, v in e["params"].items()) or "—"
        promo.add_row(e["strategy"], f"[{sc}]{e['status']}[/{sc}]",
                      f"{e['weight']*100:.0f}%" if e["weight"] else "—", params)
    console.print(promo)
    console.print(f"[dim]{registry['n_active']} aktiv · modale (stabilste) Parameter, "
                  f"Gewicht nach Robustheit · Phase 5 Meta-Allokator liest diese Registry.[/dim]")

    # Registry nur beim VOLLEN Familien-Lauf speichern – ein --strategy-Filter würde
    # sonst die Registry mit Teildaten überschreiben.
    if args.no_save:
        console.print("[dim]--no-save: Registry nicht geschrieben.[/dim]")
    elif args.strategy:
        console.print("[yellow]Teil-Lauf (--strategy): Registry NICHT überschrieben "
                      "(nur vollständige Läufe persistieren).[/yellow]")
    else:
        path = save_registry(registry)
        console.print(f"[dim]→ gespeichert: {path}[/dim]")


if __name__ == "__main__":
    main()
