"""
dashboard/calibration_curve.py — Kalibrier-Kurve live (Ausbau-Roadmap H3.3).

Trefferquote je Konfidenz-Stufe (HIGH/MEDIUM/LOW), direkt aus den echten
gelabelten Trades im Experience Store (`ExperienceStore.iter_labeled()`,
dieselbe Trainings-Schnittstelle wie `scripts/track_record.py` und
`analyzers/calibration.py` nutzen — keine zweite Aggregations-Logik).
Read-only, fail-open.
"""
from __future__ import annotations

from typing import Dict, List, Optional

_CONFIDENCE_ORDER = ("HIGH", "MEDIUM", "LOW")


def confidence_win_rates(store=None) -> List[Dict]:
    """Eine Zeile je Konfidenz-Stufe (feste Reihenfolge HIGH/MEDIUM/LOW):
    `{"confidence", "n", "wins", "win_rate"}` — `win_rate` ist `None` bei
    n=0 statt einer Division durch Null. Fail-open: eine kaputte/fehlende
    Datenquelle liefert eine leere Liste statt zu crashen."""
    counts: Dict[str, List[int]] = {}
    try:
        from analyzers.experience_store import ExperienceStore
        owns_store = store is None
        s = store or ExperienceStore()
        try:
            for feat, out in s.iter_labeled():
                conf = str(feat.get("confidence") or "").upper()
                if conf not in _CONFIDENCE_ORDER:
                    continue
                outcome = out.get("outcome")
                if outcome not in ("WIN", "LOSS"):
                    continue
                n, wins = counts.get(conf, [0, 0])
                counts[conf] = [n + 1, wins + (1 if outcome == "WIN" else 0)]
        finally:
            if owns_store:
                s.close()
    except Exception:
        return []

    rows = []
    for conf in _CONFIDENCE_ORDER:
        n, wins = counts.get(conf, [0, 0])
        rows.append({
            "confidence": conf, "n": n, "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
        })
    return rows
