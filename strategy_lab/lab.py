"""
Lab – führt eine registrierte Strategie über ein Universum × lange Historie aus
und aggregiert die Performance zu einer Karte.

WICHTIGE GRENZE (ehrlich): Dieser Backtest nutzt die *technische* Mechanik der
Strategie (Preis/Volumen). Er kann die News-Sentiment-Alpha des Live-Bots NICHT
abbilden – dafür gibt es keine Dekaden-Historie von Claude-Urteilen. Ergebnisse
validieren also das RISIKO-/STRUKTUR-Gerüst, nicht das Stock-Picking.

Reine Analyse, kein Live-Eingriff. Nutzt backtesting.data_loader (Parquet-Cache)
und backtesting.metrics (Equity-Kurve, MaxDD, Sharpe, CAGR).
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from backtesting import data_loader
from backtesting.metrics import TickerMetrics, compute, aggregate
from strategy_lab.strategies import Strategy, get

_CARD_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "strategy_cards")


def evaluate(
    strategy: Strategy | str,
    universe: List[str],
    years: int = 15,
    params: Optional[Dict] = None,
    loader: Callable[[str, int], object] = data_loader.load,
) -> Tuple[TickerMetrics, List[TickerMetrics]]:
    """Bewertet eine Strategie über das Universum. loader ist injizierbar (Tests).

    Gibt (Portfolio-Aggregat, Liste je Ticker) zurück.
    """
    if isinstance(strategy, str):
        strategy = get(strategy)
    params = params if params is not None else dict(strategy.default_params)

    per_ticker: List[TickerMetrics] = []
    for ticker in universe:
        df = loader(ticker, years)
        if df is None or len(df) < 60:
            continue
        trades = strategy.runner(df, ticker, params)
        # Tatsächliche Jahres-Spanne aus den Daten (für CAGR), nicht das angefragte
        # `years` (alte Ticker haben oft weniger Historie).
        span_years = max(len(df) / 252.0, 0.1)
        per_ticker.append(compute(trades, ticker, years=span_years))

    portfolio = aggregate(per_ticker)
    return portfolio, per_ticker


def save_card(strategy_name: str, portfolio: TickerMetrics, meta: Dict) -> str:
    os.makedirs(_CARD_DIR, exist_ok=True)
    path = os.path.join(_CARD_DIR, f"{strategy_name}.json")
    payload = {
        "strategy": strategy_name,
        "saved_at": datetime.utcnow().isoformat(),
        "meta": meta,
        "portfolio": asdict(portfolio),
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


def load_card(strategy_name: str) -> Optional[Dict]:
    path = os.path.join(_CARD_DIR, f"{strategy_name}.json")
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path))
    except Exception:
        return None
