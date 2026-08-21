#!/usr/bin/env python3
"""
Calibrate — fittet das CalibrationModel auf dem ExperienceStore und persistiert es.

Zeigt je Dimension (sentiment / confidence / debate_winner) die rohe vs. geshrinkte
Win-Rate, damit man sieht, wo Buckets zu dünn sind (raw weit weg, shrink nahe global)
und wo ein echtes Signal steckt.

Usage:
  python -m scripts.calibrate              # fitten, anzeigen, speichern
  python -m scripts.calibrate --no-save    # nur anzeigen
  python -m scripts.calibrate --source backfill   # nur Papier-Daten
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import ExperienceStore   # noqa: E402
from analyzers.calibration import CalibrationModel        # noqa: E402


def _print_dim(name: str, table) -> None:
    print(f"\n── {name} " + "─" * max(0, 46 - len(name)))
    print(f"{'Bucket':<12}{'N':>4}{'rawWin%':>9}{'shrWin%':>9}{'shrEdge%':>10}{'ok':>4}")
    for b in sorted(table):
        s = table[b]
        print(f"{b:<12}{s.n:>4}{s.raw_win_rate*100:>8.1f}%{s.win_rate*100:>8.1f}%"
              f"{s.avg_pnl:>10.2f}{'  ✓' if s.reliable else '  ·':>4}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-save", action="store_true", help="Modell nicht speichern")
    ap.add_argument("--source", default=None, help="filter: backfill | live")
    args = ap.parse_args()

    store = ExperienceStore()
    rows = list(store.iter_labeled(label_source=args.source))
    store.close()

    if not rows:
        print("Keine gelabelten Zeilen — erst `python -m scripts.backfill_outcomes`.")
        return

    model = CalibrationModel().fit_rows(rows)
    s = model.summary()
    print("══ CalibrationModel ══")
    print(f"gelabelte Zeilen : {s['n_total']}")
    print(f"Global Win-Rate  : {s['global_win_rate']*100:.1f}%")
    print(f"Global Avg P&L%  : {s['global_avg_pnl']:.2f}")
    print(f"(Shrinkage-Prior k={int(__import__('analyzers.calibration', fromlist=['_PRIOR_STRENGTH'])._PRIOR_STRENGTH)}, "
          f"min_support={int(__import__('analyzers.calibration', fromlist=['_MIN_SUPPORT'])._MIN_SUPPORT)})")

    for dim, table in model.tables.items():
        _print_dim(dim, table)

    if not args.no_save:
        model.save()
        print(f"\nModell gespeichert → {model.model_file}")
    else:
        print("\n(--no-save: nicht gespeichert)")


if __name__ == "__main__":
    main()
