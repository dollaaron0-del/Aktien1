"""
dashboard/thesis_board.py — Thesen-Board (Ausbau-Roadmap H4.1).

Liest die Thesen-Registry read-only (analyzers/thesis_verdict.py) und
bereitet sie für die Anzeige auf.

Wichtige Abweichung vom ursprünglichen Roadmap-Entwurf: die Registry kennt
NUR drei Status — PENDING/PROVEN/ABANDONED (kein "FALSIFIED", wie der
Roadmap-Text vermutete). Außerdem speichert die Registry KEINEN laufenden
Trade-Zähler — `n_trades` entsteht erst transient beim Aufruf von
`evaluate()` (braucht die echten ExperienceStore-Trade-Renditen + die
Bootstrap-Statistik aus scripts/track_record.py, beides außerhalb des für
diese Ausbau-Session erlaubten Pfads). Bis eine These ein Verdikt hat,
zeigt das Board darum den ZEIT-Fortschritt (started_at vs.
time_budget_months) — der ist allein aus der Registry berechenbar. Sobald
ein Verdikt gefallen ist (PROVEN/ABANDONED), steht die reale Trade-Zahl im
persistierten `verdict_reason`-Text, der mit angezeigt wird.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List


def default_criteria() -> Dict[str, int]:
    """Kriterien-Defaults direkt aus thesis_verdict.py ziehen (nicht
    hartkodieren) — für den Leerzustand-Hinweis. Fail-open: liefert die
    bekannten aktuellen Defaults, falls das Modul selbst nicht importierbar
    wäre (sollte praktisch nie vorkommen)."""
    try:
        from analyzers.thesis_verdict import (
            DEFAULT_N_MIN,
            DEFAULT_TIME_BUDGET_MONTHS,
        )
        return {"n_min": DEFAULT_N_MIN, "time_budget_months": DEFAULT_TIME_BUDGET_MONTHS}
    except Exception:
        return {"n_min": 150, "time_budget_months": 24}


def thesis_rows() -> List[Dict]:
    """Eine Zeile je registrierter These, read-only. Fail-open: eine
    fehlende/kaputte Registry (load_registry() selbst ist schon fail-open,
    dieser zweite try/except fängt zusätzlich einen kaputten Import ab)
    liefert eine leere Liste statt zu crashen."""
    try:
        from analyzers.thesis_verdict import load_registry
        registry = load_registry()
    except Exception:
        return []

    rows: List[Dict] = []
    for name, thesis in registry.items():
        try:
            months_elapsed = (
                (date.today() - date.fromisoformat(str(thesis.started_at)[:10])).days
                / 30.4375
            )
        except Exception:
            months_elapsed = 0.0
        budget = thesis.time_budget_months or 0
        time_progress = min(1.0, months_elapsed / budget) if budget > 0 else 0.0
        rows.append({
            "name": name,
            "description": thesis.description,
            "status": thesis.status,
            "n_min": thesis.n_min,
            "time_budget_months": thesis.time_budget_months,
            "months_elapsed": round(max(0.0, months_elapsed), 1),
            "time_progress": max(0.0, time_progress),
            "verdict_reason": thesis.verdict_reason,
        })
    rows.sort(key=lambda r: r["name"])
    return rows
