#!/usr/bin/env python3
"""
Quellen-Ablation CLI — Roadmap 2.4.

Beantwortet die Frage, die analysis_log.source_health() (Roadmap 1.4e) NICHT
beantwortet: source_health misst nur PRÄSENZ (feuert die Quelle?), nicht
WIRKUNG (führt ihr Beitrag zu besseren Entscheidungen?). Dieses Modul
verknüpft den Quellen-Breakdown jeder Analyse (analysis_log.sources_breakdown)
mit dem gelabelten Trade-Ausgang (data/experience.db über ExperienceStore) und
vergleicht je Quelle den mittleren Trade-Ertrag mit vs. ohne Treffer dieser
Quelle (Bootstrap-CI auf die Differenz, analog scripts/track_record.py).

Bewusst KEIN Modell/Gewichtungslernen — reine Zweigruppen-Ablation, kodiertes
Mindest-n je Gruppe (N_MIN_GROUP) als Ehrlichkeits-Gate: mit zu wenig Daten
gibt es "unzureichend Daten" statt einer erfundenen Aussage.

Usage:
  python -m scripts.source_ablation
  python -m scripts.source_ablation --min-n 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from analyzers.experience_store import DB_PATH as EXPERIENCE_DB_PATH  # noqa: E402
from analyzers.analysis_log import DB_PATH as ANALYSIS_LOG_DB_PATH  # noqa: E402

console = Console()

N_MIN_GROUP = 10        # Mindest-n je Gruppe (Treffer/kein Treffer), sonst "unzureichend"
CI_LEVEL = 0.95
BOOTSTRAP_ITERS = 20000
SEED = 20260712


# ── Statistik ─────────────────────────────────────────────────────────────────
def _bootstrap_diff_ci(hit: List[float], miss: List[float], rng: np.random.Generator,
                       iters: int = BOOTSTRAP_ITERS, ci: float = CI_LEVEL) -> Dict:
    """Zweigruppen-Bootstrap auf die Differenz der Mittelwerte (hit − miss).

    Unabhängiges Resampling je Gruppe (kein Paired-Test — verschiedene
    Trades). p_le0 = Anteil Bootstrap-Differenzen ≤ 0 = Evidenz GEGEN einen
    positiven Beitrag der Quelle."""
    h, m = np.asarray(hit, dtype=float), np.asarray(miss, dtype=float)
    mean_hit, mean_miss = float(h.mean()), float(m.mean())
    diff = mean_hit - mean_miss
    idx_h = rng.integers(0, h.size, size=(iters, h.size))
    idx_m = rng.integers(0, m.size, size=(iters, m.size))
    diffs = h[idx_h].mean(axis=1) - m[idx_m].mean(axis=1)
    lo, hi = np.percentile(diffs, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return {"n_hit": h.size, "n_miss": m.size, "mean_hit": mean_hit, "mean_miss": mean_miss,
            "diff": diff, "lo": float(lo), "hi": float(hi),
            "p_le0": float((diffs <= 0).mean())}


# ── Datenzugriff ──────────────────────────────────────────────────────────────
def _load_joined_trades(exp_conn: sqlite3.Connection, al_conn: sqlite3.Connection,
                        label_sources: Optional[set] = None) -> List[Dict]:
    """Verknüpft gelabelte Entscheidungen (experience.db) mit ihrem Quellen-
    Breakdown (analysis_log.db) über (ticker, decided_at==analyzed_at) — beide
    Tabellen werden von genau dieser Zeitstempel+Ticker-Kombination befüllt,
    da experience.db aus analysis_log rückwirkend gelabelt wird. Nur Zeilen mit
    gesetztem pnl_pct UND nicht-leerem sources_breakdown zählen."""
    rows = exp_conn.execute(
        "SELECT ticker, decided_at, pnl_pct, outcome, label_source "
        "FROM decisions WHERE pnl_pct IS NOT NULL"
    ).fetchall()
    out: List[Dict] = []
    for ticker, decided_at, pnl_pct, outcome, label_source in rows:
        if label_sources is not None and label_source not in label_sources:
            continue
        al_row = al_conn.execute(
            "SELECT sources_breakdown FROM analyses WHERE ticker=? AND analyzed_at=? "
            "AND sources_breakdown IS NOT NULL AND sources_breakdown != ''",
            (ticker, decided_at),
        ).fetchone()
        if al_row is None:
            continue
        try:
            breakdown = json.loads(al_row[0])
        except (json.JSONDecodeError, TypeError):
            continue
        if not breakdown:
            continue
        out.append({"ticker": ticker, "decided_at": decided_at, "pnl_pct": float(pnl_pct),
                    "outcome": outcome, "label_source": label_source, "breakdown": breakdown})
    return out


# ── Ablation ───────────────────────────────────────────────────────────────────
def _ablate_sources(trades: List[Dict], rng: np.random.Generator,
                    min_n: int = N_MIN_GROUP) -> List[Dict]:
    """Für jede in irgendeinem Breakdown vorkommende Quelle: Trades in Treffer
    (Breakdown[quelle] > 0) vs. kein Treffer aufteilen und den Ertrags-
    Unterschied bewerten. `status` ist einer von:
      "ok"           – beide Gruppen >= min_n, CI aussagekräftig.
      "insufficient" – mind. eine Gruppe < min_n.
      "no_variance"  – eine Gruppe ist leer (Quelle feuert immer/nie in der
                       Stichprobe) — Ablation strukturell nicht möglich."""
    names = sorted({src for t in trades for src in t["breakdown"]})
    results: List[Dict] = []
    for src in names:
        hit = [t["pnl_pct"] for t in trades if t["breakdown"].get(src, 0)]
        miss = [t["pnl_pct"] for t in trades if not t["breakdown"].get(src, 0)]
        if not hit or not miss:
            results.append({"source": src, "status": "no_variance",
                            "n_hit": len(hit), "n_miss": len(miss)})
            continue
        if min(len(hit), len(miss)) < min_n:
            results.append({"source": src, "status": "insufficient",
                            "n_hit": len(hit), "n_miss": len(miss)})
            continue
        stats = _bootstrap_diff_ci(hit, miss, rng)
        stats["source"] = src
        stats["status"] = "ok"
        results.append(stats)
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="Quellen-Ablation: welche Quelle verbessert Entscheidungen messbar?")
    ap.add_argument("--min-n", type=int, default=N_MIN_GROUP,
                    help=f"Mindest-n je Gruppe für ein Verdikt (Default {N_MIN_GROUP})")
    args = ap.parse_args()

    exp_conn = sqlite3.connect(EXPERIENCE_DB_PATH)
    al_conn = sqlite3.connect(ANALYSIS_LOG_DB_PATH)
    trades = _load_joined_trades(exp_conn, al_conn)
    exp_conn.close()
    al_conn.close()

    console.print(f"[bold]Quellen-Ablation[/bold] — {len(trades)} gelabelte Entscheidungen mit "
                  f"Quellen-Breakdown (Mindest-n je Gruppe: {args.min_n})")
    if not trades:
        console.print("[yellow]Keine gelabelten Entscheidungen mit sources_breakdown gefunden — "
                      "läuft erst sinnvoll, sobald analysis_log und experience.db gemeinsam "
                      "genug Historie haben (nach Bot-Reaktivierung).[/yellow]")
        return

    rng = np.random.default_rng(SEED)
    results = _ablate_sources(trades, rng, min_n=args.min_n)

    ok = sorted((r for r in results if r["status"] == "ok"), key=lambda r: -abs(r["diff"]))
    insufficient = [r for r in results if r["status"] == "insufficient"]
    no_variance = [r for r in results if r["status"] == "no_variance"]

    if ok:
        table = Table(title="Auswertbare Quellen (Bootstrap-Diff Treffer − kein Treffer)",
                      box=box.ROUNDED, border_style="dim")
        for col in ["Quelle", "n Treffer", "n Kein-Tr.", "Ø Treffer", "Ø Kein-Tr.",
                    "Diff", "95%-CI", "P(≤0)"]:
            table.add_column(col, justify="right" if col != "Quelle" else "left")
        for r in ok:
            table.add_row(r["source"], str(r["n_hit"]), str(r["n_miss"]),
                          f"{r['mean_hit']:+.2f}%", f"{r['mean_miss']:+.2f}%",
                          f"{r['diff']:+.2f}%", f"[{r['lo']:+.2f}, {r['hi']:+.2f}]",
                          f"{r['p_le0']*100:.0f}%")
        console.print(table)
    else:
        console.print("[yellow]Keine Quelle erreicht in beiden Gruppen das Mindest-n — "
                      "noch keine einzige belastbare Ablations-Aussage möglich.[/yellow]")

    if insufficient:
        names = ", ".join(f"{r['source']} ({r['n_hit']}/{r['n_miss']})" for r in insufficient)
        console.print(f"\n[dim]Unzureichend Daten (< {args.min_n} je Gruppe): {names}[/dim]")
    if no_variance:
        names = ", ".join(f"{r['source']} ({r['n_hit']}/{r['n_miss']})" for r in no_variance)
        console.print(f"[dim]Keine Varianz (feuert immer oder nie in der Stichprobe): {names}[/dim]")

    console.print("\n[dim]P(≤0) = Anteil Bootstrap-Differenzen ≤ 0, also Evidenz GEGEN einen "
                  "positiven Beitrag der Quelle. Kein Kausalitäts-Nachweis (keine "
                  "Randomisierung) — nur Korrelation zwischen Quellen-Treffer und "
                  "Ertrag; Sample stammt größtenteils aus backfill_hypo (kontrafaktisch, "
                  "s. scripts/track_record.py-Hinweis).[/dim]")


if __name__ == "__main__":
    main()
