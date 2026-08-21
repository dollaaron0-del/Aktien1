#!/usr/bin/env python3
"""
Experience Report — erste Auswertung des Lern-Datensatzes.

Reiner Read-Report über data/experience.db: Datensatzgröße, Label-Balance und —
das Entscheidende — Win-Rate-Kalibrierung je confidence-Bucket / sentiment-Dezil /
debate_winner, plus Exit-Grund-Verteilung. Genau das ist der Input für die spätere
Paradigma-Entscheidung (lernt höhere Confidence wirklich höhere Trefferquote?).

Usage:
  python -m scripts.experience_report
  python -m scripts.experience_report --source backfill
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import ExperienceStore  # noqa: E402


def _bucket_sentiment(s) -> str:
    try:
        v = float(s)
    except (TypeError, ValueError):
        return "n/a"
    lo = int(v * 10) / 10
    return f"{lo:.1f}-{lo + 0.1:.1f}"


def _agg(rows: List[Dict], keyfn) -> List[Dict]:
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for f, o in rows:
        groups[keyfn(f, o)].append(o)
    out = []
    for k, items in groups.items():
        pnls = [i["pnl_pct"] for i in items if i.get("pnl_pct") is not None]
        wins = sum(1 for i in items if i.get("outcome") == "WIN")
        out.append({
            "key": k,
            "n": len(items),
            "win_rate": wins / len(items) if items else 0.0,
            "avg_pnl": statistics.mean(pnls) if pnls else 0.0,
            "med_pnl": statistics.median(pnls) if pnls else 0.0,
        })
    return sorted(out, key=lambda r: str(r["key"]))


def _print_table(title: str, rows: List[Dict]) -> None:
    print(f"\n── {title} " + "─" * max(0, 40 - len(title)))
    print(f"{'Bucket':<14}{'N':>5}{'Win%':>8}{'avgP&L%':>10}{'medP&L%':>10}")
    for r in rows:
        print(f"{str(r['key']):<14}{r['n']:>5}{r['win_rate']*100:>7.1f}%"
              f"{r['avg_pnl']:>10.2f}{r['med_pnl']:>10.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="filter: backfill | live")
    args = ap.parse_args()

    store = ExperienceStore()
    rows = list(store.iter_labeled(label_source=args.source))
    s = store.stats()

    print("══ Experience Store ══")
    print(f"Zeilen gesamt : {s.get('total')}")
    print(f"gelabelt      : {s.get('labeled')}  (backfill {s.get('backfill')} / live {s.get('live')})")
    print(f"WIN / LOSS    : {s.get('wins')} / {s.get('losses')}")
    wr = s.get("win_rate")
    print(f"Win-Rate ges. : {wr*100:.1f}%" if wr is not None else "Win-Rate ges. : n/a")
    avg = s.get("avg_pnl_pct")
    print(f"Avg P&L%      : {avg:.2f}" if avg is not None else "Avg P&L%      : n/a")

    if not rows:
        print("\n(keine gelabelten Zeilen — erst `python -m scripts.backfill_outcomes` laufen lassen)")
        store.close()
        return

    _print_table("Win-Rate je Confidence", _agg(rows, lambda f, o: f.get("confidence") or "n/a"))
    _print_table("Win-Rate je Sentiment-Bucket", _agg(rows, lambda f, o: _bucket_sentiment(f.get("sentiment_score"))))
    _print_table("Win-Rate je Debate-Winner", _agg(rows, lambda f, o: f.get("debate_winner") or "n/a"))
    _print_table("Exit-Grund-Verteilung", _agg(rows, lambda f, o: o.get("exit_reason") or "n/a"))
    store.close()


if __name__ == "__main__":
    main()
