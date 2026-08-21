#!/usr/bin/env python3
"""scripts/backtest_tanker_flow.py – Hat Tanker-Verkehr Vorlauf vor Energie?

EXPLORATIV. Prüft, ob das in data/tanker_flow.json gesammelte Tanker-Signal
einen statistischen Zusammenhang mit *zukünftigen* Energie-Returns hat – BEVOR
es je eine Live-Entscheidung beeinflusst. Kein Trading, nur Korrelations-Check.

Vorgehen:
  1. tanker_flow.json (rollende Samples) → eine Tages-Reihe (Mittel je Tag).
  2. Tages-Ratio = Tanker-Wert / rollender Median (Trailing-Fenster) → relativer
     Verkehr, robust gegen Stichproben-Niveau.
  3. yfinance: Tages-Schlusskurse für ein Energie-Proxy (Default XLE) + USO.
  4. Forward-Returns über mehrere Horizonte (1/3/5/10 Handelstage) gegen die
     Tanker-Ratio des Vortags korrelieren (Pearson + Spearman).
  5. Optional: PNG-Plot nach logs/, wenn matplotlib vorhanden.

Wichtig: Das braucht GENUG Tanker-Samples (≥ ~20 Tage), sonst ist die Aussage
statistisch wertlos – das Skript sagt das dann auch klar.

Aufruf:
  ./venv/bin/python scripts/backtest_tanker_flow.py            # XLE, Default
  ./venv/bin/python scripts/backtest_tanker_flow.py --ticker USO --window 7
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_STATE_FILE = Path("data/tanker_flow.json")
_HORIZONS = [1, 3, 5, 10]            # Forward-Returns in Handelstagen
_DEFAULT_TICKER = "XLE"             # Energy Select Sector SPDR
_MIN_DAYS = 20                       # darunter: zu wenig für eine Aussage


def _load_daily_tankers() -> "list[tuple]":
    """tanker_flow.json → sortierte [(date, mean_tankers_total)] je Kalendertag."""
    try:
        state = json.loads(_STATE_FILE.read_text())
    except Exception as e:
        print(f"FEHLER: {_STATE_FILE} nicht lesbar ({e}). Erst Samples sammeln.")
        sys.exit(1)

    history = state.get("history") or []
    if not history:
        print("Keine Samples in der Historie. Erst den Collector laufen lassen.")
        sys.exit(1)

    per_day: dict = {}
    for s in history:
        ts = s.get("timestamp")
        val = s.get("tankers_total")
        if ts is None or val is None:
            continue
        try:
            day = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).date()
        except Exception:
            continue
        per_day.setdefault(day, []).append(val)

    daily = [(d, sum(v) / len(v)) for d, v in sorted(per_day.items())]
    return daily


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default=_DEFAULT_TICKER, help="Energie-Proxy (Default XLE)")
    ap.add_argument("--window", type=int, default=10, help="Trailing-Median-Fenster (Tage)")
    ap.add_argument("--no-plot", action="store_true", help="kein PNG erzeugen")
    args = ap.parse_args()

    import pandas as pd

    daily = _load_daily_tankers()
    if len(daily) < _MIN_DAYS:
        print(f"Nur {len(daily)} Tanker-Tage vorhanden – für eine belastbare Aussage "
              f"brauchst du ≥ {_MIN_DAYS}. (Korrelation wird trotzdem gezeigt, aber "
              f"statistisch NICHT verlässlich.)")

    idx = pd.to_datetime([d for d, _ in daily])
    tanker = pd.Series([v for _, v in daily], index=idx, name="tankers").sort_index()

    # Tages-Ratio = Wert / rollender Median (min_periods, damit der Anfang nutzbar bleibt)
    roll_med = tanker.rolling(args.window, min_periods=max(3, args.window // 2)).median()
    ratio = (tanker / roll_med).rename("ratio").dropna()
    if ratio.empty:
        print("Konnte keine Ratio bilden (zu wenig zusammenhängende Tage).")
        sys.exit(1)

    # Energie-Kurse laden (etwas Puffer für die Forward-Fenster)
    start = (ratio.index.min() - pd.Timedelta(days=10)).date().isoformat()
    end = (ratio.index.max() + pd.Timedelta(days=20)).date().isoformat()
    import yfinance as yf
    px = yf.download(args.ticker, start=start, end=end, progress=False, auto_adjust=True)
    if px is None or px.empty:
        print(f"Keine Kursdaten für {args.ticker}.")
        sys.exit(1)
    close = px["Close"]
    if hasattr(close, "columns"):       # MultiIndex bei Einzelticker abflachen
        close = close.iloc[:, 0]
    close = close.dropna()

    # Forward-Returns je Horizont
    fwd = pd.DataFrame(index=close.index)
    for h in _HORIZONS:
        fwd[f"fwd_{h}d"] = close.shift(-h) / close - 1.0

    # Tanker-Ratio auf Handelstage ausrichten (Vortags-Signal → heutiger Handelstag)
    ratio_daily = ratio.copy()
    ratio_daily.index = ratio_daily.index.normalize()
    aligned = fwd.join(ratio_daily.rename("ratio"), how="inner").dropna(subset=["ratio"])

    print("\n" + "=" * 60)
    print(f"  Tanker-Flow → {args.ticker}  (Vorlauf-Check)")
    print("=" * 60)
    print(f"Tanker-Tage gesammelt : {len(daily)}")
    print(f"Trailing-Median-Fenster: {args.window} Tage")
    print(f"Überlappende Punkte    : {len(aligned)}")
    print("-" * 60)
    print(f"{'Horizont':<10}{'Pearson r':>12}{'Spearman ρ':>14}{'n':>6}")
    print("-" * 60)

    rows = []
    for h in _HORIZONS:
        col = f"fwd_{h}d"
        sub = aligned[[col, "ratio"]].dropna()
        n = len(sub)
        if n < 3:
            print(f"{str(h)+'d':<10}{'—':>12}{'—':>14}{n:>6}")
            continue
        pear = sub["ratio"].corr(sub[col], method="pearson")
        spear = sub["ratio"].corr(sub[col], method="spearman")
        rows.append((h, pear, spear, n))
        print(f"{str(h)+'d':<10}{pear:>12.3f}{spear:>14.3f}{n:>6}")

    print("-" * 60)
    if len(aligned) < _MIN_DAYS:
        print("⚠  Zu wenig überlappende Punkte – als Trend-Indiz lesen, nicht als Beweis.")
    else:
        best = max(rows, key=lambda r: abs(r[1]), default=None) if rows else None
        if best:
            h, pear, _, _ = best
            strength = ("vernachlässigbar" if abs(pear) < 0.1 else
                        "schwach" if abs(pear) < 0.3 else
                        "moderat" if abs(pear) < 0.5 else "stark")
            print(f"Stärkster Zusammenhang: {h}d-Horizont, r={pear:+.3f} ({strength}).")
            print("Hinweis: |r|<0.1 ⇒ praktisch kein Vorlauf – Idee verwerfen statt verfeinern.")

    # Optionaler Plot
    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax1 = plt.subplots(figsize=(11, 5))
            ax1.plot(ratio.index, ratio.values, color="tab:blue", label="Tanker-Ratio")
            ax1.axhline(1.0, color="tab:blue", ls=":", lw=0.8)
            ax1.set_ylabel("Tanker-Ratio", color="tab:blue")
            ax2 = ax1.twinx()
            c = close.reindex(close.index.union(ratio.index)).interpolate()
            ax2.plot(c.index, c.values, color="tab:red", alpha=0.7, label=args.ticker)
            ax2.set_ylabel(f"{args.ticker} Close", color="tab:red")
            ax1.set_title(f"Tanker-Verkehr vs. {args.ticker}")
            out = Path("logs") / f"tanker_flow_{args.ticker}.png"
            out.parent.mkdir(parents=True, exist_ok=True)
            fig.tight_layout()
            fig.savefig(out, dpi=110)
            print(f"\nPlot gespeichert: {out}")
        except Exception as e:
            print(f"\n(Plot übersprungen: {e})")


if __name__ == "__main__":
    main()
