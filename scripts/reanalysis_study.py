#!/usr/bin/env python3
"""
Re-Analyse-Studie CLI — Roadmap 6.9c.

Liest gelabelte Entscheidungen aus data/experience.db (ExperienceStore.
iter_labeled(), bereits mit echtem Outcome versehen) und lässt ein lokales
LLM jede im Nachhinein beurteilen (analyzers.reanalysis_judge). Aggregiert
die Urteile zu einer Kategorie-Verteilung — ein Hinweis auf systematische
Analysefehler (z.B. viele RISIKO_UEBERSEHEN bei Verlust-Trades), keine
automatische Kalibrierungs-Anpassung.

Bewusst begrenzte --limit-Vorgabe (Default 30): jedes Urteil ist ein
Ollama-Aufruf, ein voller Lauf über alle gelabelten Entscheidungen kann
je nach Modell/Hardware Minuten bis Stunden dauern. --limit 0 = alle.

Usage:
  python -m scripts.reanalysis_study
  python -m scripts.reanalysis_study --limit 100 --label-source live
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.experience_store import ExperienceStore  # noqa: E402
from analyzers.ollama_prescreener import OllamaPrescreener  # noqa: E402
from analyzers.reanalysis_judge import judge_decision  # noqa: E402

MIN_N_FOR_PATTERN = 15


def _p(s: str = "") -> None:
    print(s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=30,
                     help="max. beurteilte Entscheidungen, 0 = alle (Default 30)")
    ap.add_argument("--label-source", default=None,
                     help="nur 'backfill' oder 'live' (Default: beide)")
    ap.add_argument("--model", default=None,
                     help="Ollama-Modell (Default: aus resource_manager.TIER_MODELS)")
    args = ap.parse_args()

    model = args.model
    if model is None:
        from system.resource_manager import TIER_MODELS, ResourceTier
        model = TIER_MODELS[ResourceTier.PERFORMANCE]
    prescreener = OllamaPrescreener(model=model)

    if not prescreener.is_available():
        _p("Ollama nicht erreichbar — Abbruch (keine Fake-Urteile).")
        sys.exit(1)

    store = ExperienceStore()
    judged: list = []
    n_seen = 0
    for features, outcome in store.iter_labeled(label_source=args.label_source):
        if outcome.get("pnl_pct") is None or not outcome.get("outcome"):
            continue
        n_seen += 1
        if args.limit and n_seen > args.limit:
            break
        result = judge_decision(
            prescreener,
            ticker=features.get("ticker", ""),
            recommendation=features.get("recommendation", ""),
            direction=features.get("direction", ""),
            confidence=features.get("confidence", ""),
            key_catalysts=features.get("key_catalysts") or [],
            risk_factors=features.get("risk_factors") or [],
            outcome=outcome.get("outcome", ""),
            pnl_pct=outcome.get("pnl_pct") or 0.0,
        )
        if result is not None:
            judged.append({**result, "ticker": features.get("ticker", ""),
                           "pnl_pct": outcome.get("pnl_pct")})
    store.close()

    _p(f"── Re-Analyse-Studie (Roadmap 6.9c) {'─' * 20}")
    _p(f"Modell={model}  betrachtet={n_seen}  beurteilt={len(judged)}")

    if len(judged) < MIN_N_FOR_PATTERN:
        _p(f"\nUnter Mindest-n ({MIN_N_FOR_PATTERN}) für ein Muster-Verdikt — "
           "nur Rohzahl, --limit erhöhen für eine belastbarere Stichprobe.")
        for j in judged:
            _p(f"  {j['ticker']:<8} {j['category']:<20} ({j['pnl_pct']:+.2f}%) {j['reason']}")
        return

    counts = Counter(j["category"] for j in judged)
    _p("\nKategorie-Verteilung:")
    for cat, n in counts.most_common():
        _p(f"  {cat:<20} {n:>4}  ({n/len(judged)*100:.0f}%)")

    n_risk_missed = counts.get("RISIKO_UEBERSEHEN", 0)
    if n_risk_missed / len(judged) > 0.25:
        _p(f"\nHinweis: {n_risk_missed}/{len(judged)} Urteile sehen einen übersehenen "
           "Risikofaktor als Erklärung für den Ausgang — könnte auf eine "
           "systematisch lückenhafte Risiko-Erhebung hindeuten.")


if __name__ == "__main__":
    main()
