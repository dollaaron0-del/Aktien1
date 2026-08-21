#!/usr/bin/env python3
"""
Regime-Übergangsmodell CLI — Roadmap 4.3.

Fährt eine rollierende, tagesweise Regime-Zeitreihe über echte Historie und
misst, wie stark eine Hysterese/Debounce-Regel das Schwellen-Flackern
gegenüber der Rohmessung senkt. Siehe strategy_lab/regime.py-Docstring:
bewusst NUR gebaut + gemessen, nicht live verdrahtet.

Usage:
  python -m scripts.regime_track
  python -m scripts.regime_track --tickers AAPL MSFT NVDA --min-confirm 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from strategy_lab.regime import (apply_hysteresis, count_transitions,
                                 track_regime)  # noqa: E402


def _base_universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Regime-Übergangsmodell: Hysterese-Effekt messen (Roadmap 4.3)")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--years", type=int, default=10, help="Historie-Fenster für die Zeitreihe")
    ap.add_argument("--lookback-years", type=int, default=2, help="Trailing-Fenster je Stichtag")
    ap.add_argument("--step-days", type=int, default=5)
    ap.add_argument("--min-confirm", nargs="*", type=int, default=[2, 3, 5],
                    help="mehrere Hysterese-Stärken vergleichen")
    args = ap.parse_args()

    from backtesting import data_loader
    universe = _base_universe(args)
    console = Console()
    console.print(f"[bold]Regime-Übergangsmodell[/bold] über {len(universe)} Ticker | "
                  f"{args.years}J Historie, {args.lookback_years}J Trailing-Fenster, "
                  f"Schritt {args.step_days}d")

    full = {t: data_loader.load(t, args.years) for t in universe}
    full = {t: d for t, d in full.items() if d is not None and len(d) > 0}
    if not full:
        console.print("[red]Keine Daten geladen.[/red]")
        return
    start = min(d.index.min() for d in full.values())

    raw = track_regime(universe, data_loader.load, start=start, history_years=args.years,
                       lookback_years=args.lookback_years, step_days=args.step_days)
    if not raw:
        console.print("[red]Keine Regime-Zeitreihe erzeugt.[/red]")
        return

    n_years = max((len(raw) * args.step_days) / 365.0, 0.1)
    raw_trans = count_transitions(raw)
    console.print(f"[dim]{len(raw)} Messpunkte über ~{n_years:.1f} Jahre.[/dim]\n")

    table = Table(title="Regime-Flackern: roh vs. Hysterese", box=box.ROUNDED, border_style="dim")
    for col in ["min_confirm", "Übergänge", "Übergänge/Jahr", "Δ ggü. roh"]:
        table.add_column(col, justify="right" if col != "min_confirm" else "left")
    table.add_row("roh (kein Debounce)", str(raw_trans), f"{raw_trans/n_years:.1f}", "—")
    for mc in args.min_confirm:
        smoothed = apply_hysteresis(raw, min_confirm=mc)
        trans = count_transitions(smoothed)
        delta = trans - raw_trans
        table.add_row(str(mc), str(trans), f"{trans/n_years:.1f}",
                      f"{delta:+d} ({delta/raw_trans*100:+.0f}%)" if raw_trans else "—")
    console.print(table)
    console.print("[dim]Weniger Übergänge = weniger Schwellen-Flackern, aber jeder echte Übergang "
                  "verzögert sich um bis zu min_confirm Messpunkte. Bewusst NICHT live verdrahtet "
                  "(weder RecessionDetector noch Exit-Multiplikatoren) — nur gemessen.[/dim]")


if __name__ == "__main__":
    main()
