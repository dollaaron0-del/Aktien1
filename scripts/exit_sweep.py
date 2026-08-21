#!/usr/bin/env python3
"""
Exit-Sweep — testet verschiedene Stop-Loss / Take-Profit-Kombinationen auf dem
bereits gelabelten (deduplizierten) Einstiegs-Satz aus dem ExperienceStore.

Frage: Sind die aktuellen Regeln (SL 7% / TP 20%) zu eng? 141 von 347 Trades
gingen per Stop-Loss raus. Der Sweep simuliert jeden Einstieg über den gecachten
Kursen unter einem SL×TP-Raster neu und zeigt Win-Rate, Ø-Edge und SL-Quote.

Methodik/Caveat: Die Einstiegs-Liste (Ticker, Datum, Richtung, Hold) wird beim
aktuellen Dedup-Stand aus experience.db übernommen und über den Sweep konstant
gehalten. Dass andere Exit-Parameter das Dedup-Fenster minimal verschieben würden,
wird hier vernachlässigt (Effekt 2. Ordnung). Reine Papier-Analyse, kein Netz
(nutzt nur den Preis-Cache von backfill_outcomes), kein Live-Eingriff.

Usage:
  python -m scripts.exit_sweep                 # alle gelabelten Einstiege
  python -m scripts.exit_sweep --source backfill   # nur echte BUY/SELL
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from statistics import mean
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.backfill_outcomes import simulate_outcome, _load_bars, _DEFAULT_HOLD  # noqa: E402
from analyzers.experience_store import DB_PATH as _EXP_DB  # noqa: E402

_SL_GRID = [0.05, 0.07, 0.10, 0.12, 0.15]
_TP_GRID = [0.10, 0.15, 0.20, 0.30, 0.40]


def _load_entries(source: str | None) -> List[Dict]:
    conn = sqlite3.connect(_EXP_DB)
    conn.row_factory = sqlite3.Row
    sql = "SELECT ticker, decided_at, direction, suggested_hold FROM decisions WHERE outcome IS NOT NULL"
    params: List = []
    if source:
        sql += " AND label_source=?"
        params.append(source)
    rows = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    return rows


def _bars_for(entry: Dict) -> List[Dict]:
    start = entry["decided_at"][:10]
    bars = _load_bars(entry["ticker"], start)
    return [b for b in bars if b.get("date", "") >= start]


def run(source: str | None = None) -> None:
    entries = _load_entries(source)
    print(f"Einstiege im Sweep: {len(entries)}  (source={source or 'alle'})")
    # Bars einmal pro Einstieg cachen (netzfrei aus Datei-Cache).
    bars_cache = {i: _bars_for(e) for i, e in enumerate(entries)}

    win_grid: Dict = {}
    edge_grid: Dict = {}
    sl_grid: Dict = {}
    n_eval = 0
    for sl in _SL_GRID:
        for tp in _TP_GRID:
            pnls, wins, sl_hits, n = [], 0, 0, 0
            for i, e in enumerate(entries):
                hold = int(e.get("suggested_hold") or 0) or _DEFAULT_HOLD
                out = simulate_outcome(bars_cache[i], e.get("direction", "LONG"),
                                       sl_pct=sl, tp_pct=tp, max_hold=hold)
                if out is None:
                    continue
                n += 1
                pnls.append(out["pnl_pct"])
                wins += 1 if out["outcome"] == "WIN" else 0
                sl_hits += 1 if out["exit_reason"] == "SL" else 0
            if n:
                win_grid[(sl, tp)] = wins / n
                edge_grid[(sl, tp)] = mean(pnls)
                sl_grid[(sl, tp)] = sl_hits / n
                n_eval = n

    _print_grid("Ø Edge (% P&L pro Trade)", edge_grid, fmt="{:+7.2f}")
    _print_grid("Win-Rate (%)", win_grid, fmt="{:6.1%}")
    _print_grid("Stop-Loss-Quote (%)", sl_grid, fmt="{:6.1%}")

    best = max(edge_grid, key=edge_grid.get)
    cur = (0.07, 0.20)
    print(f"\nBester Edge: SL {best[0]:.0%} / TP {best[1]:.0%}  → {edge_grid[best]:+.2f}% "
          f"(Win {win_grid[best]:.0%}, SL-Quote {sl_grid[best]:.0%})")
    if cur in edge_grid:
        print(f"Aktuell:     SL {cur[0]:.0%} / TP {cur[1]:.0%}  → {edge_grid[cur]:+.2f}% "
              f"(Win {win_grid[cur]:.0%}, SL-Quote {sl_grid[cur]:.0%})")
    print(f"\nBasis: {n_eval} auswertbare Einstiege. Papier-Outcomes, kein Slippage.")


def _print_grid(title: str, grid: Dict, fmt: str) -> None:
    print(f"\n── {title} ─────────────  (Zeile=SL, Spalte=TP)")
    print("  SL\\TP " + "".join(f"{int(tp*100):>8}%" for tp in _TP_GRID))
    for sl in _SL_GRID:
        cells = "".join(("  " + fmt.format(grid.get((sl, tp), 0))) for tp in _TP_GRID)
        print(f"  {int(sl*100):>3}%  {cells}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="filter: backfill | backfill_hypo")
    args = ap.parse_args()
    run(source=args.source)


if __name__ == "__main__":
    main()
