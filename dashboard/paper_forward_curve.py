"""
dashboard/paper_forward_curve.py — Paper-Forward-Fieberkurve (Ausbau-
Roadmap H4.3).

Baut eine kumulierte, gewichtete Renditekurve aus den ECHTEN,
abgeschlossenen Paper-Forward-Positionen (`strategy_lab/paper_forward.py`
Ledger, `data/paper_forward.json`) — chronologisch nach `exit_date`.

WICHTIGE EINSCHRÄNKUNG (dokumentiert, kein Bug): eine „vs. Benchmark"-
Linie (Buy&Hold) ist BEWUSST NICHT enthalten. Der Vergleichswert existiert
im Ledger nirgends vorberechnet — `strategy_lab.paper_forward.
benchmark_buy_hold()` braucht einen LIVE-Preis-Loader (Netzwerk-Abruf
historischer Kurse für jeden gehandelten Ticker), der für ein netzfreies,
read-only Dashboard-Modul außerhalb des Rahmens dieser Ausbau-Session
liegt. Selbst `strategy_lab/live_bridge.py`s eigene "netzfreie" Bilanz
(`_paper_forward_edge()`) lässt den Benchmark-Teil aus genau diesem Grund
bewusst weg. Die Kurve zeigt darum NUR die Strategie selbst — ehrlicher
als ein erfundener oder veralteter Vergleichswert.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional


def equity_curve(ledger_path: Optional[str] = None) -> List[Dict]:
    """Kumulierte, gewichtete Rendite-Kurve über abgeschlossene Paper-
    Forward-Positionen, chronologisch nach `exit_date`. Eine Zeile je
    Position: `{"date", "ticker", "strategy", "return_pct",
    "cum_return"}`. Fail-open: fehlendes/kaputtes Ledger → leere Liste."""
    try:
        from strategy_lab.paper_forward import CLOSED, _positions, load_ledger
        if ledger_path is not None:
            with open(ledger_path, encoding="utf-8") as fh:
                ledger = json.load(fh)
        else:
            ledger = load_ledger()
        positions = [
            p for p in _positions(ledger)
            if p.status == CLOSED and p.exit_date
        ]
    except Exception:
        return []

    positions.sort(key=lambda p: p.exit_date)
    rows: List[Dict] = []
    cum = 1.0
    for p in positions:
        weight = p.weight if p.weight else 1.0
        cum *= 1.0 + p.return_pct * weight
        rows.append({
            "date": p.exit_date,
            "ticker": p.ticker,
            "strategy": p.strategy,
            "return_pct": round(p.return_pct, 6),
            "cum_return": round(cum - 1.0, 6),
        })
    return rows
