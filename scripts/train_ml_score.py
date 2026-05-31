#!/usr/bin/env python3
"""
ML Score Calibration Trainer — CLI

Trains a GradientBoostingClassifier on historical OHLCV data to predict
P(trade reaches TP1).  Saves the model to data/ml_score_model.joblib.

Usage:
  python scripts/train_ml_score.py                      # config watchlist
  python scripts/train_ml_score.py AAPL MSFT NVDA TSLA # specific tickers
  python scripts/train_ml_score.py --years 5            # 5-year lookback
  python scripts/train_ml_score.py --show-importance    # feature importances
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from rich import box

import config as _cfg_module
from backtesting.engine import BacktestConfig
from ml.trainer import build_dataset, train_model, save_model
from ml.predictor import MLScorePredictor
from ml.feature_engineering import FEATURE_NAMES

console = Console()


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ruflo ML Score Calibration Trainer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("tickers", nargs="*", metavar="TICKER",
                   help="Tickersymbole (Standard: Watchlist aus config)")
    p.add_argument("--years",           type=int,  default=5,
                   help="Lookback in Jahren für Trainingsdaten (Standard: 5)")
    p.add_argument("--show-importance", action="store_true",
                   help="Feature-Importances nach dem Training anzeigen")
    p.add_argument("--sl",              type=float, default=None)
    p.add_argument("--tp1",             type=float, default=None)
    p.add_argument("--tp2",             type=float, default=None)
    return p.parse_args()


def _print_importances(predictor: MLScorePredictor) -> None:
    imp = predictor.feature_importances()
    if imp is None:
        console.print("[yellow]Feature importances nicht verfügbar (LogReg-Modell).[/yellow]")
        return

    table = Table(title="Feature Importances", box=box.SIMPLE)
    table.add_column("Feature",    style="cyan")
    table.add_column("Importance", justify="right")
    table.add_column("Bedeutung")

    descriptions = {
        "rsi":        "Momentum / Überkauft-Indikator",
        "ema_dev":    "Abstand vom EMA21 (niedriger = besserer Einstieg)",
        "vol_ratio":  "Volumen vs. 20d-Durchschnitt",
        "mom_5d":     "5-Tage-Rendite (Kurzfristtrend)",
        "mom_20d":    "20-Tage-Rendite (Mittelfrristtrend)",
        "atr_pct":    "Normierte Volatilität (ATR / Kurs)",
        "dist_52w_hi":"Abstand vom 52-Wochen-Hoch",
        "crossover":  "1 = frischer EMA21-Crossover, 0 = Pullback",
    }

    for feat, score in imp.items():
        bar = "█" * int(score * 40)
        table.add_row(feat, f"{score:.4f}", descriptions.get(feat, ""))

    console.print(table)


def main() -> None:
    args    = _parse()
    app_cfg = _cfg_module.Config()

    tickers = [t.upper() for t in args.tickers] if args.tickers else app_cfg.watchlist

    bt_cfg = BacktestConfig(
        sl_pct  = args.sl  if args.sl  is not None else app_cfg.stop_loss_pct,
        tp1_pct = args.tp1 if args.tp1 is not None else app_cfg.partial_tp_pct,
        tp2_pct = args.tp2 if args.tp2 is not None else app_cfg.partial_tp2_pct,
    )

    console.print(f"\n[bold]ML Score Calibration Trainer[/bold]")
    console.print(
        f"  Ticker: [cyan]{len(tickers)}[/cyan]  |  "
        f"Lookback: [cyan]{args.years}J[/cyan]  |  "
        f"SL=[yellow]{bt_cfg.sl_pct:.0%}[/yellow]  "
        f"TP1=[green]{bt_cfg.tp1_pct:.0%}[/green]\n"
    )

    # ── 1. Dataset bauen ──────────────────────────────────────────────────────
    console.print("[dim]Lade historische Daten und extrahiere Signale …[/dim]")
    try:
        X, y = build_dataset(tickers, years=args.years, bt_cfg=bt_cfg)
    except ValueError as e:
        console.print(f"[red]Dataset-Fehler: {e}[/red]")
        sys.exit(1)

    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    console.print(
        f"  Datensatz: [bold]{len(y)}[/bold] Trades  |  "
        f"[green]{n_pos} positiv[/green]  |  [red]{n_neg} negativ[/red]  |  "
        f"Basis-Win-Rate: [cyan]{n_pos / len(y) * 100:.1f}%[/cyan]\n"
    )

    # ── 2. Modell trainieren ───────────────────────────────────────────────────
    console.print("[dim]Trainiere Modell (5-fold CV) …[/dim]")
    pipe = train_model(X, y)

    # ── 3. Speichern ──────────────────────────────────────────────────────────
    path = save_model(pipe)
    console.print(f"\n[green]✓ Modell gespeichert:[/green] {path}\n")

    # ── 4. Feature-Importances (optional) ────────────────────────────────────
    if args.show_importance:
        predictor = MLScorePredictor()
        _print_importances(predictor)

    console.print(
        "[dim]Tipp: Starte den Bot neu oder rufe "
        "ml_score_filter.reload_model() auf, damit das neue Modell aktiv wird.[/dim]"
    )


if __name__ == "__main__":
    main()
