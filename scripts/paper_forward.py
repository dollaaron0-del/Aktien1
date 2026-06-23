#!/usr/bin/env python3
"""
Paper-Forward-CLI — Roadmap-Punkt (c).

Schreibt die vom Meta-Allokator ausgewählten Signale als PAPIER-Positionen mit und
löst sie gegen reale Kursbalken auf – ohne echte Orders. Über die Zeit wiederholt
ausgeführt (z. B. täglich) entsteht ein ehrlicher Vorwärts-Track der robusten
Strategien, bevor irgendetwas live geht. NUR Anzeige/Protokoll, kein Live-Eingriff
(Bot bleibt pausiert).

Usage:
  python -m scripts.paper_forward record        # Heute-Signale aufnehmen (+ auflösen)
  python -m scripts.paper_forward update        # offene Positionen neu auflösen
  python -m scripts.paper_forward status        # Track + Kennzahlen anzeigen
  python -m scripts.paper_forward replay --months 12   # aus jüngster Historie bootstrappen
  python -m scripts.paper_forward record --tickers AAPL MSFT NVDA
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from backtesting import data_loader  # noqa: E402
from strategy_lab import allocator, paper_forward as pf  # noqa: E402

console = Console()


def _universe(args):
    if args.tickers:
        return [t.upper() for t in args.tickers]
    try:
        from config import config
        return list(config.watchlist)
    except Exception:
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]


def _plan(universe, args):
    regime = None
    if args.regime.lower() != "off":
        regime = (allocator.current_regime(universe, data_loader.load, lookback_years=args.years)
                  if args.regime.upper() == "AUTO" else args.regime.upper())
    plan = allocator.weight_plan(max_weight=args.max_weight, regime=regime)
    return plan, regime


def _print_summary(ledger):
    s = pf.summary(ledger)
    mode = ledger.get("mode", "live")
    win = ledger.get("replay_window")
    header = f"Paper-Forward-Track ({mode}"
    header += f", {win[0]}→{win[1]})" if win else ")"
    console.print(f"[bold]{header}[/bold]")
    console.print(
        f"Positionen {s['n_positions']} · geschlossen {s['n_closed']} · "
        f"offen {s['n_open']} · pending {s['n_pending']}")
    if s["n_closed"]:
        console.print(
            f"[bold]Geschlossen:[/bold] Trefferquote {s['win_rate']*100:.1f}% · "
            f"Ø-Return {s['avg_return_closed']*100:+.2f}% · "
            f"gewichtet {s['weighted_return_closed']*100:+.2f}%")
    if s["n_open"]:
        console.print(f"[dim]Offen (MtM, gewichtet): {s['open_mtm_weighted']*100:+.2f}%[/dim]")

    if s["by_strategy"]:
        t = Table(title="Je Strategie", box=box.ROUNDED, border_style="dim")
        for c in ["Strategie", "Pos.", "zu", "Trefferquote", "Ø-Return"]:
            t.add_column(c, justify="left" if c == "Strategie" else "right")
        for name, d in sorted(s["by_strategy"].items()):
            t.add_row(name, str(d["n"]), str(d["closed"]),
                      f"{d['win_rate']*100:.0f}%" if d["closed"] else "—",
                      f"{d['avg_return']*100:+.2f}%" if d["closed"] else "—")
        console.print(t)


def _print_positions(ledger, only_open=False):
    positions = ledger.get("positions", [])
    if only_open:
        positions = [p for p in positions if p["status"] != pf.CLOSED]
    if not positions:
        return
    positions = sorted(positions, key=lambda p: (p["status"], p["strategy"], p["ticker"]))
    t = Table(title="Positionen", box=box.ROUNDED, border_style="dim")
    for c in ["Status", "Strategie", "Ticker", "Signal", "Entry", "Return", "Grund"]:
        t.add_column(c, justify="left" if c in ("Status", "Strategie", "Ticker", "Grund") else "right")
    colors = {pf.OPEN: "yellow", pf.CLOSED: "green", pf.PENDING: "dim"}
    for p in positions[:60]:
        ret = p.get("return_pct") or 0.0
        rc = "green" if ret > 0 else ("red" if ret < 0 else "white")
        t.add_row(
            f"[{colors.get(p['status'],'white')}]{p['status']}[/]",
            p["strategy"], p["ticker"], p["signal_date"],
            f"{p['entry_price']:.2f}" if p.get("entry_price") else "—",
            f"[{rc}]{ret*100:+.2f}%[/]",
            p.get("exit_reason") or "—")
    console.print(t)


def cmd_record(args):
    universe = _universe(args)
    plan, regime = _plan(universe, args)
    if not plan:
        console.print("[yellow]Kein Allokations-Plan (Registry leer oder Regime risk-off). "
                      "Erst `python -m scripts.walk_forward` laufen lassen.[/yellow]")
        return
    if regime:
        console.print(f"[dim]Aktuelles Regime: {regime}[/dim]")
    ledger = pf.load_ledger()
    as_of = args.as_of or pd.Timestamp(datetime.now()).strftime("%Y-%m-%d")
    n_new = pf.record_signals(ledger, as_of, universe, data_loader.load, plan=plan, years=args.years)
    stats = pf.update_positions(ledger, data_loader.load, years=args.history_years)
    pf.save_ledger(ledger)
    console.print(f"[green]{n_new} neue Papier-Position(en) aufgenommen[/green] "
                  f"(as_of {as_of}); aufgelöst: {stats}")
    _print_summary(ledger)
    _print_positions(ledger, only_open=True)


def cmd_update(args):
    ledger = pf.load_ledger()
    if not ledger.get("positions"):
        console.print("[yellow]Leerer Ledger – erst `record` oder `replay`.[/yellow]")
        return
    stats = pf.update_positions(ledger, data_loader.load, years=args.history_years)
    pf.save_ledger(ledger)
    console.print(f"[green]aufgelöst:[/green] {stats}")
    _print_summary(ledger)


def cmd_status(args):
    ledger = pf.load_ledger()
    if not ledger.get("positions"):
        console.print("[yellow]Leerer Ledger – erst `record` oder `replay`.[/yellow]")
        return
    _print_summary(ledger)
    _print_positions(ledger, only_open=args.open_only)


def cmd_replay(args):
    universe = _universe(args)
    plan, regime = _plan(universe, args)
    if not plan:
        console.print("[yellow]Kein Allokations-Plan – erst `python -m scripts.walk_forward`.[/yellow]")
        return
    end = pd.Timestamp(datetime.now())
    start = end - pd.DateOffset(months=args.months)
    console.print(f"[dim]Replay {start.date()} → {end.date()} "
                  f"(regime: {regime or 'off'}); bootstrappt den Ledger neu.[/dim]")
    ledger = pf.replay(universe, data_loader.load, plan, start, end,
                       step_days=args.step_days, years=args.years)
    pf.save_ledger(ledger)
    _print_summary(ledger)
    _print_positions(ledger, only_open=args.open_only)
    console.print("[dim]Replay ≠ echter Vorwärts-Lauf: füllt den Track aus jüngsten Daten. "
                  "Für genuinen Beweis `record` wiederholt über die Zeit laufen lassen.[/dim]")


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper-Forward-Validierung des Meta-Allokators")
    ap.add_argument("--tickers", nargs="*", default=None)
    ap.add_argument("--max-weight", type=float, default=0.6)
    ap.add_argument("--years", type=int, default=2, help="Lookback für Signal-Erkennung")
    ap.add_argument("--history-years", type=int, default=3, help="Lookback fürs Auflösen")
    ap.add_argument("--regime", default="AUTO", help="AUTO | off | festes Label (z. B. BULL_CALM)")
    ap.add_argument("--open-only", action="store_true", help="nur offene/pending Positionen zeigen")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="Heute-Signale aufnehmen und auflösen")
    p_rec.add_argument("--as-of", default=None, help="Datum YYYY-MM-DD (Default heute)")
    p_rec.set_defaults(func=cmd_record)

    sub.add_parser("update", help="offene Positionen neu auflösen").set_defaults(func=cmd_update)
    sub.add_parser("status", help="Track + Kennzahlen anzeigen").set_defaults(func=cmd_status)

    p_rep = sub.add_parser("replay", help="aus jüngster Historie bootstrappen")
    p_rep.add_argument("--months", type=int, default=12)
    p_rep.add_argument("--step-days", type=int, default=1)
    p_rep.set_defaults(func=cmd_replay)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
