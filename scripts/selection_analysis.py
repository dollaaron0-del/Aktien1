#!/usr/bin/env python3
"""
Selektions-Analyse — was trennt Gewinner von Verlierern beim Einstieg?

Das Fazit aus Kalibrierung + Exit-Sweep war: das Problem ist die Entry-Selektion,
nicht die Exits. Dieses Skript sucht im ExperienceStore nach den Features, die WIN
von LOSS trennen — als Vorstufe zu einem echten Entry-Filter.

Untersucht:
  * numerische Features (sentiment, Quellenzahl, Target-Upside, suggested_hold)
    im Vergleich Gewinner vs. Verlierer
  * Katalysator-/Risiko-Tokens: Win-Rate je Token (mit Mindest-Support + Lift)
  * Win-Rate je Ticker (welche Namen meiden / favorisieren)

Reine Read-Analyse, kein Netz, kein Live-Eingriff. Papier-Outcomes (Caveat).

Usage:
  python -m scripts.selection_analysis                 # alle gelabelten
  python -m scripts.selection_analysis --source backfill   # nur echte Trades
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import DB_PATH as _EXP_DB  # noqa: E402


def _load(source: Optional[str]) -> List[Dict]:
    conn = sqlite3.connect(_EXP_DB)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM decisions WHERE outcome IS NOT NULL"
    params: List = []
    if source:
        sql += " AND label_source=?"
        params.append(source)
    rows = [dict(r) for r in conn.execute(sql, params)]
    conn.close()
    for r in rows:
        for c in ("key_catalysts", "risk_factors"):
            try:
                r[c] = json.loads(r.get(c) or "[]")
            except (json.JSONDecodeError, TypeError):
                r[c] = []
    return rows


def _target_upside(r: Dict) -> Optional[float]:
    tp, ep = r.get("target_price"), r.get("entry_price")
    if tp and ep and ep > 0:
        return (tp - ep) / ep * 100
    return None


def _num_compare(rows: List[Dict]) -> None:
    wins = [r for r in rows if r["outcome"] == "WIN"]
    loss = [r for r in rows if r["outcome"] == "LOSS"]
    print(f"\n── Numerische Features: Gewinner ({len(wins)}) vs. Verlierer ({len(loss)}) "
          "── (Median)")
    feats = {
        "sentiment_score": lambda r: r.get("sentiment_score"),
        "sources_used":    lambda r: r.get("sources_used"),
        "target_upside_%": _target_upside,
        "suggested_hold":  lambda r: r.get("suggested_hold"),
        "mae_pct":         lambda r: r.get("mae_pct"),
    }
    print(f"{'Feature':<18}{'WIN':>10}{'LOSS':>10}{'Δ':>10}")
    for name, fn in feats.items():
        w = [v for v in (fn(r) for r in wins) if v is not None]
        l = [v for v in (fn(r) for r in loss) if v is not None]
        if not w or not l:
            continue
        mw, ml = median(w), median(l)
        print(f"{name:<18}{mw:>10.2f}{ml:>10.2f}{mw - ml:>+10.2f}")


def _token_lift(rows: List[Dict], field: str, min_support: int = 5, top: int = 12) -> None:
    base = sum(1 for r in rows if r["outcome"] == "WIN") / len(rows) if rows else 0.0
    agg: Dict[str, Dict] = defaultdict(lambda: {"n": 0, "wins": 0})
    for r in rows:
        for tok in set(str(t).strip().lower() for t in r.get(field, []) if str(t).strip()):
            agg[tok]["n"] += 1
            agg[tok]["wins"] += 1 if r["outcome"] == "WIN" else 0
    items = [(t, a["n"], a["wins"] / a["n"]) for t, a in agg.items() if a["n"] >= min_support]
    if not items:
        print(f"\n── {field}: (keine Tokens mit ≥{min_support} Vorkommen)")
        return
    items.sort(key=lambda x: x[2])
    print(f"\n── {field}: Win-Rate je Token (Basis-Win {base:.0%}, ≥{min_support} Vorkommen)")
    print(f"{'Token':<34}{'N':>4}{'Win%':>8}{'Lift':>8}")
    # Worst + best ohne Überlappung (bei wenig Tokens einfach alle zeigen).
    if len(items) <= top:
        show = items
    else:
        show = items[:top // 2] + [("…", 0, -1.0)] + items[-(top // 2):]
    for t, n, wr in show:
        if wr < 0:
            print("  …")
            continue
        lift = (wr / base) if base else 0
        print(f"{t[:33]:<34}{n:>4}{wr*100:>7.0f}%{lift:>7.2f}x")


def _by_ticker(rows: List[Dict], min_trades: int = 2) -> None:
    agg: Dict[str, Dict] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for r in rows:
        a = agg[r["ticker"]]
        a["n"] += 1
        a["wins"] += 1 if r["outcome"] == "WIN" else 0
        a["pnl"] += r.get("pnl_pct") or 0.0
    items = [(t, a["n"], a["wins"] / a["n"], a["pnl"] / a["n"])
             for t, a in agg.items() if a["n"] >= min_trades]
    items.sort(key=lambda x: x[3])
    print(f"\n── Win-Rate je Ticker (≥{min_trades} Trades) — schlechteste & beste nach Ø-P&L")
    print(f"{'Ticker':<12}{'N':>4}{'Win%':>8}{'avgP&L%':>10}")
    show = items[:8] + [("…", 0, 0, 0)] + items[-8:] if len(items) > 16 else items
    for t, n, wr, pnl in show:
        if t == "…":
            print("  …")
            continue
        print(f"{t:<12}{n:>4}{wr*100:>7.0f}%{pnl:>10.2f}")


def run(source: Optional[str]) -> None:
    rows = _load(source)
    if not rows:
        print("Keine gelabelten Zeilen.")
        return
    wins = sum(1 for r in rows if r["outcome"] == "WIN")
    print(f"Basis: {len(rows)} gelabelte Einstiege (source={source or 'alle'}), "
          f"Win-Rate {wins/len(rows):.0%}")
    _num_compare(rows)
    _token_lift(rows, "key_catalysts")
    _token_lift(rows, "risk_factors")
    _by_ticker(rows)
    print("\n(Papier-Outcomes, kein Slippage. Tokens roh aus Claude-Analysen.)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=None, help="filter: backfill | backfill_hypo")
    args = ap.parse_args()
    run(args.source)


if __name__ == "__main__":
    main()
