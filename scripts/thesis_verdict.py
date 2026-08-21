#!/usr/bin/env python3
"""
Erfolgs-/Abbruchkriterien-CLI — Roadmap 6.10.

Registriert und wertet Strategie-Thesen gegen die am 12.7.2026 festgelegten
Kriterien aus: 150+ Live-Trades ODER 24 Monate Zeit-Budget (was zuerst
eintritt); Kante > 0 UND schlägt Buy&Hold (Bootstrap-CI-Untergrenze), sonst
VERWORFEN und nicht wiederbelebt. Details: analyzers/thesis_verdict.py.

Usage:
  python -m scripts.thesis_verdict --list
  python -m scripts.thesis_verdict --register mechanical_baseline \\
      --description "Mechanische Strategien ohne KI-Signal"
  python -m scripts.thesis_verdict --evaluate mechanical_baseline
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from analyzers import thesis_verdict  # noqa: E402

console = Console()
_STATUS_COLOR = {"PENDING": "yellow", "PROVEN": "green", "ABANDONED": "red"}


def _print_list() -> None:
    registry = thesis_verdict.load_registry()
    if not registry:
        console.print("[dim]Keine These registriert. --register NAME zum Anlegen.[/dim]")
        return
    table = Table(title="Thesen-Registry (Roadmap 6.10)", box=box.ROUNDED, border_style="dim")
    for col in ["These", "Status", "seit", "n_min", "Zeit-Budget", "Verdikt am", "Grund"]:
        table.add_column(col, justify="left" if col in ("These", "Grund") else "right")
    for t in registry.values():
        sc = _STATUS_COLOR.get(t.status, "white")
        table.add_row(t.name, f"[{sc}]{t.status}[/{sc}]", t.started_at, str(t.n_min),
                      f"{t.time_budget_months}Mon", t.verdict_at or "—", t.verdict_reason or "—")
    console.print(table)


def _do_evaluate(name: str, source: str | None, no_benchmark: bool) -> None:
    from analyzers.experience_store import ExperienceStore
    from scripts.track_record import _BenchmarkCache, _load_trades

    sources = {source} if source else {"backfill", "live"}
    store = ExperienceStore()
    trades = _load_trades(store, sources)
    store.close()

    if not trades:
        console.print("[red]Keine gelabelten Trades für diese Quelle — noch nichts zu bewerten.[/red]")
        return

    returns = [t["pnl_pct"] for t in trades]
    excess = []
    if not no_benchmark:
        cache = _BenchmarkCache()
        for t in trades:
            if t["entry_day"] is None:
                continue
            br = cache.window_return(t["ticker"], t["entry_day"], t["hold_days"])
            if br is not None:
                excess.append(t["pnl_pct"] - br)

    try:
        verdict = thesis_verdict.evaluate(name, returns, excess or None)
    except KeyError as e:
        console.print(f"[red]{e}[/red]")
        return

    sc = _STATUS_COLOR.get(verdict.status, "white")
    console.print(f"[bold]These '{name}'[/bold]: [{sc}]{verdict.status}[/{sc}]")
    console.print(f"  Trades: {verdict.n_trades}/{verdict.n_min}  ·  "
                  f"Zeit: {verdict.months_elapsed}/{verdict.time_budget_months} Monate")
    if verdict.edge_ci_lo is not None:
        console.print(f"  Kante-CI-Untergrenze: {verdict.edge_ci_lo:+.2f}%")
    if verdict.beats_bh_ci_lo is not None:
        console.print(f"  Excess-CI-Untergrenze (vs. B&H): {verdict.beats_bh_ci_lo:+.2f}%")
    console.print(f"  [dim]{verdict.reason}[/dim]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Erfolgs-/Abbruchkriterien je Strategie-These (Roadmap 6.10)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--register", metavar="NAME")
    ap.add_argument("--description", default="")
    ap.add_argument("--n-min", type=int, default=thesis_verdict.DEFAULT_N_MIN)
    ap.add_argument("--time-budget-months", type=int, default=thesis_verdict.DEFAULT_TIME_BUDGET_MONTHS)
    ap.add_argument("--evaluate", metavar="NAME")
    ap.add_argument("--source", default=None, help="backfill|live (Default: backfill+live)")
    ap.add_argument("--no-benchmark", action="store_true")
    args = ap.parse_args()

    if args.list:
        _print_list()
        return
    if args.register:
        t = thesis_verdict.register_thesis(
            args.register, n_min=args.n_min, time_budget_months=args.time_budget_months,
            description=args.description)
        console.print(f"These '{t.name}' registriert seit {t.started_at} "
                      f"(n_min={t.n_min}, Zeit-Budget={t.time_budget_months} Monate, "
                      f"Status={t.status}).")
        return
    if args.evaluate:
        _do_evaluate(args.evaluate, args.source, args.no_benchmark)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
