#!/usr/bin/env python3
"""
Parquet-Cache vorab befüllen — Roadmap 6.2a ("GRATIS/SOFORT" gegen zu wenige
unabhängige Stichproben: bisher nur config.watchlist, ~10-42 Ticker).

Lädt den aktuellen S&P-500-Bestand aus der PIT-Mitgliederliste (6.2b,
strategy_lab.universe) und befüllt backtesting.data_loader's Parquet-Cache für
jeden Ticker — damit Walk-Forward/CPCV-Läufe über ein breites Universum sofort
aus dem Cache bedienen statt live bei yfinance nachzuladen (langsam, Rate-Limit-
Risiko mitten im Lab-Lauf).

Reines Cache-Warmup, kein Bot-Wiring, keine Bewertung. Macht das Universum
GRÖSSER, behebt aber NICHT den Survivorship-Bias (6.2c bleibt offen, braucht
eine Bezahlquelle mit Delisting-Historie) — die hier geladenen Ticker sind alle
heutige Überlebende.

Usage:
  python -m scripts.prefill_universe_cache
  python -m scripts.prefill_universe_cache --years 20 --delay 0.4
  python -m scripts.prefill_universe_cache --tickers AAPL MSFT NVDA
"""
from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402

from strategy_lab.universe import constituents_at, load_membership  # noqa: E402
from backtesting import data_loader  # noqa: E402

console = Console()


def _universe(args) -> list[str]:
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        membership = load_membership()
        tickers = constituents_at(datetime.date.today(), membership)
        if not tickers:
            console.print(
                "[red]Keine Mitgliederliste gefunden — erst "
                "scripts.sp500_membership_download laufen lassen.[/red]"
            )
            sys.exit(1)
    # fja05680/sp500 schreibt Aktienklassen mit Punkt (BRK.B, BF.B); yfinance
    # kennt nur den Bindestrich (BRK-B) — sonst liefert der Loader 0 Zeilen.
    return [t.replace(".", "-") for t in tickers]


def main() -> None:
    ap = argparse.ArgumentParser(description="Parquet-Cache für ein breites Universum vorbefüllen")
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--delay", type=float, default=0.3, help="Sekunden zwischen Yahoo-Requests")
    ap.add_argument("--tickers", nargs="*", default=None)
    args = ap.parse_args()

    universe = _universe(args)
    console.print(f"[bold]Cache-Prefill[/bold] über {len(universe)} Ticker, years={args.years}")

    ok, failed = [], []
    for i, ticker in enumerate(universe, 1):
        df = data_loader.load(ticker, years=args.years)
        if df is not None:
            ok.append(ticker)
        else:
            failed.append(ticker)
        if i % 25 == 0 or i == len(universe):
            console.print(f"  {i}/{len(universe)} — {len(ok)} ok, {len(failed)} ohne Daten")
        time.sleep(args.delay)

    console.print(f"\n[green]{len(ok)} Ticker gecacht[/green], "
                  f"[yellow]{len(failed)} ohne verwertbare Daten[/yellow]")
    if failed:
        console.print(f"Ohne Daten: {', '.join(failed)}")


if __name__ == "__main__":
    main()
