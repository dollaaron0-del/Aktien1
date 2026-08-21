#!/usr/bin/env python3
"""
Skip-Kontrafaktik CLI — Roadmap 3.2.

Beantwortet: wenn der Bot eine Aktie NICHT gekauft hat, obwohl die KI-Analyse
BUY/SELL empfahl (decision_log.action='SKIP' bei recommendation BUY/SELL) —
was wäre passiert, hätten wir trotzdem eingestiegen? Simuliert per
scripts.backfill_outcomes.simulate_outcome (dieselbe Exit-Logik/Kurs-Ladung
wie beim echten Backfill, kein neuer Simulationscode) und bricht das Ergebnis
nach Skip-GRUND auf (analyzers.decision_log.bucket_reason).

Unterschied zu ``backfill_outcomes --include-holds``: das nutzt die KI-EIGENE
Empfehlung aus analysis_log (die KI selbst sagte HOLD/SKIP). Hier geht es um
die entgegengesetzte Lücke — die KI wollte BUY/SELL, aber eine operative
Schranke im Bot hat das VETOED (Schwelle/Korrelation/Liquidität/Lernfilter-
AVOID/Max-Positionen/Earnings-Sperre/Tagesverlust). Insbesondere die Buckets
"unter_schwelle" (config.buy_threshold) und "lernfilter_avoid"
(analyzers/entry_filter.py-Verdikt) sind die in der Roadmap benannten
"EntryFilter-Schwellen" — wenn ihre kontrafaktische Kante klar NICHT negativ
ist, filtern sie zu aggressiv; ist sie klar negativ, validiert das die Schranke.

Bewusst KEIN Kausalitäts-Nachweis (keine Randomisierung, Papier-Simulation
ohne den echten Constraint-Kontext zum Skip-Zeitpunkt — z.B. konnten Slots/
Cash da bereits anderweitig belegt gewesen sein) und kodiertes Mindest-n je
Bucket (N_MIN_GROUP) als Ehrlichkeits-Gate, analog scripts/source_ablation.py.

Usage:
  python -m scripts.skip_counterfactual
  python -m scripts.skip_counterfactual --min-n 15 --hold 20
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402
from rich import box  # noqa: E402

from analyzers.decision_log import DB_PATH as DECISION_LOG_DB_PATH, bucket_reason  # noqa: E402
from scripts.backfill_outcomes import (  # noqa: E402
    simulate_outcome, normalize_direction, _load_bars, _DEFAULT_HOLD,
)

console = Console()

N_MIN_GROUP = 10        # Mindest-n je Bucket, sonst "unzureichend"
CI_LEVEL = 0.95
BOOTSTRAP_ITERS = 20000
SEED = 20260717


# ── Datenzugriff ──────────────────────────────────────────────────────────────
def _read_skip_decisions(conn: sqlite3.Connection, limit: Optional[int] = None) -> List[Dict]:
    """SKIPs, bei denen die KI tatsächlich BUY/SELL empfahl — der Bot hat aus
    operativen Gründen dagegen entschieden. SKIPs mit recommendation HOLD/SKIP
    (die KI selbst sah kein Signal) sind hier keine Gegenprobe wert."""
    sql = ("SELECT ticker, decided_at, reason, recommendation, direction, sentiment_score "
          "FROM decisions WHERE action='SKIP' AND recommendation IN ('BUY','SELL') "
          "ORDER BY decided_at")
    rows = conn.execute(sql).fetchall()
    out = [dict(zip(("ticker", "decided_at", "reason", "recommendation",
                     "direction", "sentiment_score"), r)) for r in rows]
    return out[:limit] if limit else out


# ── Simulation (Gegenprobe) ─────────────────────────────────────────────────────
def _simulate_skips(rows: List[Dict], load_bars: Callable[[str, str], List[Dict]] = _load_bars,
                    default_hold: int = _DEFAULT_HOLD) -> List[Dict]:
    """Für jeden Skip: kontrafaktischer Einstieg am decided_at-Tag, echte
    Exit-Regeln (simulate_outcome). Liefert eine Zeile pro simulierbarem Skip
    mit bucket + pnl_pct; Skips ohne brauchbare Kursdaten fallen weg."""
    out: List[Dict] = []
    for row in rows:
        ticker = row["ticker"]
        start_date = (row["decided_at"] or "")[:10]
        if not start_date:
            continue
        direction = normalize_direction(row.get("direction") or "", row.get("recommendation") or "")
        bars = load_bars(ticker, start_date)
        bars = [b for b in bars if b.get("date", "") >= start_date]
        outcome = simulate_outcome(bars, direction=direction, max_hold=default_hold)
        if outcome is None:
            continue
        out.append({
            "ticker": ticker, "decided_at": row["decided_at"],
            "bucket": bucket_reason(row.get("reason") or ""),
            "pnl_pct": outcome["pnl_pct"], "outcome": outcome["outcome"],
        })
    return out


# ── Aggregation je Skip-Grund ───────────────────────────────────────────────────
def _aggregate_by_bucket(sim_rows: List[Dict], rng: np.random.Generator,
                         min_n: int = N_MIN_GROUP) -> List[Dict]:
    """Bootstrap-CI auf die Ø-Rendite je Bucket (Bootstrap-Helfer wiederverwendet,
    keine Kopie — analog scripts/sentiment_forward_study.py)."""
    from scripts.track_record import _bootstrap_mean_ci

    buckets = sorted({r["bucket"] for r in sim_rows})
    results: List[Dict] = []
    for b in buckets:
        pnls = [r["pnl_pct"] for r in sim_rows if r["bucket"] == b]
        n_win = sum(1 for r in sim_rows if r["bucket"] == b and r["outcome"] == "WIN")
        if len(pnls) < min_n:
            results.append({"bucket": b, "status": "insufficient", "n": len(pnls)})
            continue
        ci = _bootstrap_mean_ci(pnls, rng, iters=BOOTSTRAP_ITERS, ci=CI_LEVEL)
        ci.update({"bucket": b, "status": "ok", "win_rate": n_win / len(pnls)})
        results.append(ci)
    return results


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Skip-Kontrafaktik: was wäre passiert, hätten wir übersprungene BUY/SELL-Signale genommen?")
    ap.add_argument("--min-n", type=int, default=N_MIN_GROUP,
                    help=f"Mindest-n je Skip-Grund für ein Verdikt (Default {N_MIN_GROUP})")
    ap.add_argument("--hold", type=int, default=_DEFAULT_HOLD,
                    help=f"Haltedauer in Bars für die Gegenprobe (Default {_DEFAULT_HOLD})")
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten N Skips")
    args = ap.parse_args()

    conn = sqlite3.connect(DECISION_LOG_DB_PATH)
    rows = _read_skip_decisions(conn, limit=args.limit)
    conn.close()

    console.print(f"[bold]Skip-Kontrafaktik[/bold] — {len(rows)} SKIP-Entscheidungen mit "
                  f"KI-Empfehlung BUY/SELL (Mindest-n je Grund: {args.min_n})")
    if not rows:
        console.print("[yellow]Keine passenden SKIPs im decision_log gefunden — läuft erst "
                      "sinnvoll, sobald der laufende Bot genug Entscheidungen gesammelt hat.[/yellow]")
        return

    sim_rows = _simulate_skips(rows, default_hold=args.hold)
    console.print(f"davon simulierbar (Kursdaten vorhanden): {len(sim_rows)}")
    if not sim_rows:
        console.print("[yellow]Für keinen Skip ließen sich Kursdaten laden.[/yellow]")
        return

    rng = np.random.default_rng(SEED)
    results = _aggregate_by_bucket(sim_rows, rng, min_n=args.min_n)
    ok = sorted((r for r in results if r["status"] == "ok"), key=lambda r: -r["n"])
    insufficient = [r for r in results if r["status"] == "insufficient"]

    if ok:
        table = Table(title="Kontrafaktische Kante je Skip-Grund (wäre eingestiegen worden)",
                      box=box.ROUNDED, border_style="dim")
        for col in ["Skip-Grund", "n", "Win-Rate", "Ø Kante", "95%-CI", "P(≤0)"]:
            table.add_column(col, justify="right" if col != "Skip-Grund" else "left")
        for r in ok:
            table.add_row(r["bucket"], str(r["n"]), f"{r['win_rate']:.0%}",
                          f"{r['mean']:+.2f}%", f"[{r['lo']:+.2f}, {r['hi']:+.2f}]",
                          f"{r['p_le0']*100:.0f}%")
        console.print(table)
    else:
        console.print("[yellow]Kein Skip-Grund erreicht das Mindest-n — noch keine belastbare "
                      "Aussage möglich.[/yellow]")

    if insufficient:
        names = ", ".join(f"{r['bucket']} (n={r['n']})" for r in insufficient)
        console.print(f"\n[dim]Unzureichend Daten (< {args.min_n}): {names}[/dim]")

    console.print(
        "\n[dim]P(≤0) = Anteil Bootstrap-Mittelwerte ≤ 0, also Evidenz GEGEN eine positive "
        "kontrafaktische Kante. 'unter_schwelle' und 'lernfilter_avoid' sind die in Roadmap 3.2 "
        "gemeinten EntryFilter-Schwellen — klar negative Kante dort validiert die Schranke, "
        "eine klar nicht-negative Kante spricht für zu aggressives Filtern. Papier-Simulation "
        "ohne echten Constraint-Kontext zum Skip-Zeitpunkt (z.B. Slots/Cash), kein "
        "Kausalitätsnachweis.[/dim]"
    )


if __name__ == "__main__":
    main()
