#!/usr/bin/env python3
"""
Beobachtungs-Radar-CLI — Roadmap 6.11a.

Analysiert ein Beobachtungs-Universum lokal (Ollama), speichert eine
Score-Zeitreihe je Ticker (analyzers.observation_radar). Siehe dortigen
Docstring für die Kern-Trennung: Radar ≠ Trade-Kandidat, fließt NICHT in
den Handels-Funnel.

Zwei Nachrichtenquellen:
  --headlines-file  netzfrei/kostenfrei, "TICKER: Schlagzeile" pro Zeile —
                     zum Prüfen des Mechanismus selbst, keine echten
                     Live-Daten.
  --tickers         echter Live-Collector-Stack (bot.runner.collect_news,
                     Dutzende externe APIs mit eigenen Rate-Limits/Kosten
                     JE TICKER). Bewusst kleiner Default (5) — mehr Ticker
                     heißt mehr echte API-Aufrufe gegen Dutzende Dienste,
                     das ist eine Betriebsentscheidung (Rate-Limits/Budgets
                     gegenchecken), kein Automatismus.

Usage:
  python -m scripts.observation_radar_scan --headlines-file h.txt
  python -m scripts.observation_radar_scan --tickers AAPL MSFT NVDA
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.observation_radar import ObservationRadar, observe_ticker  # noqa: E402
from analyzers.ollama_prescreener import OllamaPrescreener  # noqa: E402

_MAX_TICKERS_WITHOUT_FORCE = 10


def _load_headlines_file(path: Path) -> dict:
    """'TICKER: Schlagzeile' pro Zeile -> {ticker: [news_items]}."""
    by_ticker = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        ticker, headline = line.split(":", 1)
        ticker, headline = ticker.strip().upper(), headline.strip()
        if ticker and headline:
            by_ticker[ticker].append({"title": headline, "source": "cli", "published_at": ""})
    return dict(by_ticker)


def _fetch_via_live_collectors(tickers: list) -> dict:
    from bot.runner import _make_collectors, collect_news
    from collectors.news_archive import NewsArchive

    archive = NewsArchive()
    collectors = _make_collectors()
    by_ticker = {}
    for ticker in tickers:
        items, _breakdown = collect_news(ticker, archive, collectors)
        by_ticker[ticker] = items
    return by_ticker


def _p(s: str = "") -> None:
    print(s)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--headlines-file", type=Path, default=None,
                     help="netzfreie Quelle statt Live-Collectors")
    ap.add_argument("--tickers", nargs="*", default=None,
                     help="Live-Collector-Stack für diese Ticker (echte API-Aufrufe!)")
    ap.add_argument("--force", action="store_true",
                     help=f"mehr als {_MAX_TICKERS_WITHOUT_FORCE} Ticker gegen Live-Collectors erlauben")
    ap.add_argument("--model", default=None,
                     help="Ollama-Modell (Default: aus resource_manager.TIER_MODELS)")
    args = ap.parse_args()

    if not args.headlines_file and not args.tickers:
        _p("--headlines-file oder --tickers angeben.")
        sys.exit(1)

    if args.headlines_file:
        by_ticker = _load_headlines_file(args.headlines_file)
    else:
        if len(args.tickers) > _MAX_TICKERS_WITHOUT_FORCE and not args.force:
            _p(f"{len(args.tickers)} Ticker gegen den echten Live-Collector-Stack "
               f"(Dutzende externe APIs) überschreitet den Sicherheits-Default "
               f"({_MAX_TICKERS_WITHOUT_FORCE}) — Rate-Limits/Kosten vorher prüfen, "
               f"dann --force setzen.")
            sys.exit(1)
        _p(f"Live-Collector-Stack für {len(args.tickers)} Ticker …")
        by_ticker = _fetch_via_live_collectors([t.upper() for t in args.tickers])

    if not by_ticker:
        _p("Keine Ticker/Schlagzeilen gefunden.")
        return

    model = args.model
    if model is None:
        from system.resource_manager import TIER_MODELS, ResourceTier
        model = TIER_MODELS[ResourceTier.PERFORMANCE]
    prescreener = OllamaPrescreener(model=model)
    if not prescreener.is_available():
        _p("Ollama nicht erreichbar — Abbruch.")
        sys.exit(1)

    radar = ObservationRadar()
    _p(f"── Beobachtungs-Radar (Roadmap 6.11a) {'─' * 15}")
    _p(f"Modell={model}  Ticker={len(by_ticker)}")
    n_recorded = 0
    for ticker, news_items in by_ticker.items():
        result = observe_ticker(radar, prescreener, ticker, news_items)
        if result is None:
            _p(f"  {ticker:<8} übersprungen (Ollama-Fehler)")
            continue
        n_recorded += 1
        _p(f"  {ticker:<8} score={result['score']:.2f} {result['direction']:<8} "
           f"{result['confidence']}")
    radar.close()
    _p(f"\n{n_recorded}/{len(by_ticker)} in data/observation_radar.db gespeichert.")
    _p("[Radar-Beobachtung – kein Trade-Kandidat, fließt nicht in den Handels-Funnel]")


if __name__ == "__main__":
    main()
