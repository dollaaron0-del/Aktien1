"""
dashboard/learning_curve.py — Lernkurven-Wand (Roadmap L6.1,
docs/FABRIK_LEBENDIG.md).

Das gesammelte Wissen WIRKT längst (Lern-Filter, Kalibrierung), war aber
nur als Ist-Stand sichtbar. Hier kommt die ENTWICKLUNG dazu:

(a) `calibration_history()` — Güte der Selbsteinschätzung über die Zeit
    aus `data/calibration_monitor.json` (brier/bss/ece/auc je Lauf des
    Kalibrierungs-Monitors).
(b) `experience_growth()` — wie viel Erfahrung das Werk über die Zeit
    gesammelt hat (kumulierte gelabelte Entscheidungen).

ZEITACHSE (16.7.2026 an echten Daten geprüft, bewusste Abweichung von
der Roadmap-Skizze): für (b) wird `decided_at` verwendet, NICHT
`labeled_at`. Grund: alle 347 gelabelten Zeilen tragen exakt denselben
`labeled_at` (23.6.2026, innerhalb von 7 Sekunden) — sie stammen aus
EINEM Backfill-Lauf. Eine Kurve darüber wäre eine Stufe von 0 auf 347 an
einem einzigen Tag, also ein Artefakt des Nachetikettierens statt einer
Lernkurve. `decided_at` ist zudem die inhaltlich richtige Achse: wann
wurde die Erfahrung GEMACHT, nicht wann nachträglich etikettiert.

Read-only, netzfrei, fail-open.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from typing import Dict, List, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_MONITOR_FILE = os.path.join(_DATA_DIR, "calibration_monitor.json")

# Ab so vielen Messpunkten lohnt eine Linie; darunter zeigt der Tab die
# Zahlen als Tabelle (zwei Punkte sind kein Trend, eine Linie dazwischen
# suggeriert eine Entwicklung, die nicht belegt ist).
MIN_POINTS_FOR_CURVE = 3


def calibration_history() -> List[Dict]:
    """Messpunkte des Kalibrierungs-Monitors, chronologisch aufsteigend:
    `[{"run_at", "n", "brier", "bss", "ece", "auc"}]`. Zeilen ohne
    verwertbares Datum werden übersprungen. Fail-open → []."""
    try:
        with open(_MONITOR_FILE, encoding="utf-8") as fh:
            history = json.load(fh).get("history") or []
    except Exception:
        return []
    rows = []
    for entry in history:
        run_at = str((entry or {}).get("run_at") or "")
        if len(run_at) < 10:
            continue
        row = {"run_at": run_at}
        for key in ("n", "brier", "bss", "ece", "auc"):
            value = (entry or {}).get(key)
            row[key] = value if isinstance(value, (int, float)) else None
        rows.append(row)
    rows.sort(key=lambda r: r["run_at"])
    return rows


def experience_growth(store=None) -> List[Dict]:
    """Kumulierte gelabelte Erfahrungen je Tag: `[{"date", "new",
    "total"}]`, chronologisch. `store` injizierbar (Muster
    `calibration_curve.confidence_win_rates`). Fail-open → []."""
    try:
        from analyzers.experience_store import ExperienceStore
        owns = store is None
        s = store or ExperienceStore()
        per_day: Counter = Counter()
        try:
            for feat, _out in s.iter_labeled():
                day = str(feat.get("decided_at") or "")[:10]
                if len(day) == 10:
                    per_day[day] += 1
        finally:
            if owns:
                s.close()
    except Exception:
        return []
    rows, total = [], 0
    for day in sorted(per_day):
        total += per_day[day]
        rows.append({"date": day, "new": per_day[day], "total": total})
    return rows
