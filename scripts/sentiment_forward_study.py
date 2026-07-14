#!/usr/bin/env python3
"""
Sentiment-Forward-Study-CLI — Roadmap 3.1.

Prüft, ob der KI-sentiment_score forward-prädiktiv ist: Edge je Score-Bucket
(Bootstrap-CI) + Information Coefficient (Spearman, Bootstrap-CI). Datenbasis:
data/experience.db (ExperienceStore) — siehe analyzers/sentiment_forward_study.py
für die Methodik und das Ehrlichkeits-Protokoll (feste Buckets, label_source
getrennt ausgewiesen).

Usage:
  python -m scripts.sentiment_forward_study                # alle Quellen
  python -m scripts.sentiment_forward_study --source live  # nur echte Trades
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from analyzers.sentiment_forward_study import run_study  # noqa: E402

console = Console()


def _fmt_ci(d: dict) -> str:
    import math
    if not math.isfinite(d.get("lo", float("nan"))):
        return f"{d['mean']:+.2f}%  (CI n/a, n={d['n']})"
    return (f"{d['mean']:+.2f}%  90%-CI [{d['lo']:+.2f}, {d['hi']:+.2f}]  "
            f"P(≤0)={d['p_le0']*100:.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description="Sentiment-Forward-Study (Roadmap 3.1)")
    ap.add_argument("--source", default=None,
                    help="live|backfill|backfill_hypo (Default: alle)")
    ap.add_argument("--iters", type=int, default=20000)
    args = ap.parse_args()

    sources = {args.source} if args.source else None
    result = run_study(sources=sources, iters=args.iters)

    console.print(f"[bold]Sentiment-Forward-Study[/bold] — n={result['n_total']} "
                  f"gelabelte Entscheidungen mit sentiment_score")
    console.print(f"  je label_source: {result['by_source']}")
    if not result["by_source"].get("live"):
        console.print("[yellow]Hinweis: keine echten Live-Trades in der Stichprobe — "
                      "backfill/backfill_hypo sind Mechanik-/Kontrafaktik-Sicht, "
                      "keine reale Evidenz (vgl. track_record.py).[/yellow]")

    table = Table(title="Edge je Score-Bucket", box=box.ROUNDED, border_style="dim")
    for col in ["Bucket", "N", "Win%", "Ø-Rendite (Bootstrap-CI)"]:
        table.add_column(col, justify="right" if col != "Bucket" else "left")
    for label, d in result["buckets"].items():
        win_txt = f"{d['win_rate']*100:.0f}%" if d["n"] else "–"
        table.add_row(label, str(d["n"]), win_txt, _fmt_ci(d))
    console.print(table)

    ic = result["information_coefficient"]
    import math
    if math.isfinite(ic.get("lo", float("nan"))):
        console.print(
            f"\n[bold]Information Coefficient[/bold] (Spearman, sentiment_score↔pnl_pct): "
            f"{ic['ic']:+.3f}  90%-CI [{ic['lo']:+.3f}, {ic['hi']:+.3f}]  "
            f"P(IC≤0)={ic['p_le0']*100:.0f}%  (n={ic['n']})"
        )
    else:
        console.print(f"\n[dim]Information Coefficient: n={ic['n']} — zu wenig Daten für eine CI.[/dim]")


if __name__ == "__main__":
    main()
