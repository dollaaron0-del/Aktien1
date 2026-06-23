"""
Promotion-Registry (Roadmap Phase 4) – "behalte, was funktioniert".

Konsumiert die Walk-Forward-Reports (Phase 3) und entscheidet pro Strategie:
  * ROBUST  → AKTIV: kommt mit Gewicht in die Live-Kandidaten (für Phase 5 Meta-Allokator)
  * FRAGILE → WATCH: bleibt beobachtet, nicht aktiv
  * OVERFIT → REJECTED: aussortiert

Das ist die Survivorship-Schicht: nur out-of-sample robuste Taktiken überleben,
und das Ranking erfolgt nach Robustheit, NICHT nach dem In-Sample-Bestwert. Die
gewählten Parameter sind die über die Fenster *stabilsten* (modal), nicht die eines
einzelnen Glücksfensters.

Persistiert nach data/strategy_registry.json. Greift NICHT in den Live-Pfad ein –
der Meta-Allokator (Phase 5) liest diese Registry später.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from typing import Dict, List, Optional

from strategy_lab.walkforward import WalkForwardReport

_REGISTRY_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "strategy_registry.json")

_STATUS = {"ROBUST": "ACTIVE", "FRAGILE": "WATCH", "OVERFIT": "REJECTED"}


def modal_params(report: WalkForwardReport) -> Dict:
    """Der über die Fenster am häufigsten gewählte Parametersatz (Stabilität)."""
    if not report.windows:
        return {}
    keyed = Counter(tuple(sorted(w.best_params.items())) for w in report.windows)
    return dict(keyed.most_common(1)[0][0])


def robustness_score(report: WalkForwardReport) -> float:
    """Gewicht für aktive Strategien: belohnt positiven OOS-Median, Trefferquote der
    Fenster und Walk-Forward-Effizienz. 0, wenn nicht aktiv-würdig."""
    if report.verdict != "ROBUST":
        return 0.0
    base = max(report.median_test_return, 0.0)
    return round(base * report.pct_positive_windows * min(report.wf_efficiency, 1.0), 4)


def build_registry(reports: List[WalkForwardReport]) -> Dict:
    """Baut die Promotion-Registry aus Walk-Forward-Reports."""
    entries = []
    for r in reports:
        entries.append({
            "strategy": r.strategy,
            "status": _STATUS.get(r.verdict, "REJECTED"),
            "verdict": r.verdict,
            "params": modal_params(r),
            "weight_raw": robustness_score(r),
            "n_windows": r.n_windows,
            "median_test_return": r.median_test_return,
            "pct_positive_windows": r.pct_positive_windows,
            "wf_efficiency": r.wf_efficiency,
            "param_stability": r.param_stability,
            "robust_regimes": r.robust_regimes,
            "regime_breakdown": r.regime_breakdown,
        })
    # Gewichte der AKTIVEN normalisieren (Summe = 1), Rest 0.
    active = [e for e in entries if e["status"] == "ACTIVE"]
    total = sum(e["weight_raw"] for e in active)
    for e in entries:
        e["weight"] = round(e["weight_raw"] / total, 4) if (total > 0 and e["status"] == "ACTIVE") else 0.0
    entries.sort(key=lambda e: (e["status"] != "ACTIVE", -e["weight"]))
    return {
        "built_at": datetime.utcnow().isoformat(),
        "n_active": len(active),
        "entries": entries,
    }


def save_registry(registry: Dict) -> str:
    os.makedirs(os.path.dirname(_REGISTRY_FILE), exist_ok=True)
    with open(_REGISTRY_FILE, "w") as fh:
        json.dump(registry, fh, indent=2)
    return _REGISTRY_FILE


def load_registry() -> Optional[Dict]:
    if not os.path.exists(_REGISTRY_FILE):
        return None
    try:
        return json.load(open(_REGISTRY_FILE))
    except Exception:
        return None


def active_strategies() -> List[Dict]:
    """Für den späteren Meta-Allokator (Phase 5): nur aktive Strategien mit Gewicht."""
    reg = load_registry()
    if not reg:
        return []
    return [e for e in reg.get("entries", []) if e.get("status") == "ACTIVE"]
