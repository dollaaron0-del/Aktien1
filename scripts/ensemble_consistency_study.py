#!/usr/bin/env python3
"""
Selbst-Konsistenz-Ensemble-Studie — Roadmap 6.9e.

Fragt dieselbe Prescreen-Analyse n-mal bei erhöhter Temperature ab
(analyzers.ollama_prescreener.OllamaPrescreener.sample_consistency) und
berichtet die Streuung — ein ehrliches Unsicherheitsmaß, das ein einzelner
deterministischer Aufruf (temperature=0.1, wie im Live-Betrieb) nicht zeigt.

Bewusst NUR Messung, kein Wiring: ob hohe Uneinigkeit unter den Samples
tatsächlich mit schlechterer Kalibrierung (1.2) korreliert, ist eine offene
Frage — Verdrahtung in die Kalibrierung oder den send_to_claude-Entscheid
erst nach belegtem Effekt (Muster wie 2.1/2.5/4.3: erst bauen + messen).
Lokal ~kostenlos (Ollama) — deshalb bewusst nicht für Claude gebaut
(n-facher API-Preis, vgl. Roadmap-Leitplanke zu 6.9).

Netzfrei bis auf den Ollama-Aufruf selbst (localhost). Schlagzeilen kommen
bewusst aus einer einfachen Textdatei (eine pro Zeile) statt aus dem vollen
Live-Collector-Stack (bot/cycle_analysis.py::collect_news) — der braucht
Netz + API-Keys + die volle Runner-Initialisierung, hier soll das Ensemble-
Verfahren selbst isoliert geprüft werden können.

Usage:
  python -m scripts.ensemble_consistency_study --ticker AAPL --headlines-file h.txt
  python -m scripts.ensemble_consistency_study --ticker AAPL --headlines-file h.txt --n 8 --temperature 0.9
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.ollama_prescreener import OllamaPrescreener  # noqa: E402


def _load_headlines(path: Path) -> list:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    return [{"title": ln, "source": "cli", "published_at": ""} for ln in lines if ln]


def _p(s: str = "") -> None:
    print(s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--headlines-file", required=True, type=Path,
                     help="Textdatei, eine Schlagzeile pro Zeile")
    ap.add_argument("--n", type=int, default=5, help="Samples (Default 5)")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--model", default=None,
                     help="Ollama-Modell (Default: aus resource_manager.TIER_MODELS)")
    args = ap.parse_args()

    if not args.headlines_file.exists():
        _p(f"Datei nicht gefunden: {args.headlines_file}")
        sys.exit(1)
    news_items = _load_headlines(args.headlines_file)
    if not news_items:
        _p("Keine Schlagzeilen in der Datei — Abbruch.")
        sys.exit(1)

    model = args.model
    if model is None:
        from system.resource_manager import TIER_MODELS, ResourceTier
        model = TIER_MODELS[ResourceTier.PERFORMANCE]

    prescreener = OllamaPrescreener(model=model)
    _p(f"── Selbst-Konsistenz-Ensemble (Roadmap 6.9e) {'─' * 20}")
    _p(f"Ticker={args.ticker}  Modell={model}  n={args.n}  temperature={args.temperature}")
    _p(f"Schlagzeilen: {len(news_items)}")

    result = prescreener.sample_consistency(
        args.ticker, news_items, n=args.n, temperature=args.temperature
    )

    if result.n_valid == 0:
        _p("\nKeine verwertbaren Samples (Ollama offline oder alle Antworten "
           "unparsbar) — kein Verdikt.")
        return

    _p(f"\nn_valid={result.n_valid}/{result.n_requested}")
    _p(f"Score: Ø={result.score_mean:.3f}  Std={result.score_std:.3f}")
    _p(f"Mehrheits-Richtung: {result.majority_direction} "
       f"(Einigkeit {result.direction_agreement*100:.0f}%)")
    _p(f"Konfidenz-Verteilung: {result.confidence_mix}")
    _p("\nEinzel-Samples:")
    for i, s in enumerate(result.samples, 1):
        _p(f"  {i}. score={s['score']:.2f} {s['direction']:<8} {s['confidence']:<6} {s['reason']}")

    if result.score_std > 0.15 or result.direction_agreement < 0.7:
        _p("\nHinweis: hohe Streuung/Uneinigkeit — Grenzfall-Einschätzung, "
           "ein Einzel-Sample (wie im Live-Betrieb) hätte das nicht gezeigt.")


if __name__ == "__main__":
    main()
