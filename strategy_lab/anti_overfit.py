"""
Anti-Overfit-Protokoll für große Suchräume (Roadmap 6.4).

Warum: Das Bootstrap-Verdikt (4.2) prüft EINE Strategie gegen die Null — aber
eine Grid-Search testet viele Kombos, und wer von n Versuchen den besten
vorzeigt, findet auch in reinem Rauschen "Signifikanz" (Multiple Testing /
Data Snooping, vgl. White's Reality Check). Die heutigen Gates reichen für
24 Kombos; auf dem GPU-Server mit 10.000er-Suchräumen produzieren sie
GARANTIERT Scheinkanten. Zwei Werkzeuge dagegen:

1. Šidák-korrigiertes Signifikanz-Gate: die Zahl der getesteten Kombos fließt
   ins Verdikt ein — je größer die Suche, desto strenger die Schwelle, die
   das Bootstrap-p des OOS-Mittels unterschreiten muss. Bewusst Šidák statt
   echtem Deflated Sharpe Ratio (Bailey/López de Prado): DSR braucht
   Skew/Kurtosis-stabile Schätzungen, die auf 3–8 OOS-Fenstern nicht tragen —
   das wird hier nicht vorgetäuscht. n_trials zählt konservativ ALLE Kombos.

2. Holdout-Zugriffsprotokoll: run_walk_forward(holdout_years=…) spart den
   jüngsten Daten-Schwanz komplett von der Suche aus; run_holdout() bewertet
   feste (z.B. modale Registry-) Parameter darauf und protokolliert JEDEN
   Zugriff — das Fenster nutzt sich durch Anfassen ab (Ziel: ≤1×/Quartal).

Reine numpy/stdlib-Statistik, kein LLM, kein Live-Eingriff.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Einseitiges Basis-Signifikanzniveau. Entspricht dem 4.2-Gate "90%-CI-
# Untergrenze > 0" (zweiseitiges 90%-CI ⇔ einseitig 5%).
BASE_ALPHA = 0.05

_HOLDOUT_LOG = Path(__file__).resolve().parent.parent / "data" / "holdout_access.json"


def sidak_alpha(n_trials: int, alpha: float = BASE_ALPHA) -> float:
    """Šidák-korrigierte Signifikanzschwelle bei n_trials Versuchen.

    n=1 → alpha (unverändert); wächst n, sinkt die Schwelle so, dass die
    Familien-Fehlerrate (mind. ein falsch-positives Verdikt) bei alpha bleibt.
    """
    n = max(int(n_trials), 1)
    if n == 1:
        return alpha  # exakt, ohne Float-Rundung durch die Potenz
    return 1.0 - (1.0 - alpha) ** (1.0 / n)


def passes_multiple_testing(p_le0: float, n_trials: int,
                            alpha: float = BASE_ALPHA) -> bool:
    """True, wenn das Bootstrap-p (P(Kante ≤ 0)) auch nach Šidák-Korrektur
    für n_trials getestete Kombos signifikant ist."""
    return p_le0 <= sidak_alpha(n_trials, alpha)


def log_holdout_access(strategy: str, start: str, end: str,
                       note: str = "") -> None:
    """Protokolliert einen Holdout-Zugriff nach data/holdout_access.json
    (Override: ENV HOLDOUT_LOG_PATH). Disziplin-Werkzeug, kein Schloss:
    das Protokoll macht sichtbar, wie oft das Fenster angefasst wurde.
    Fail-open — ein Log-Fehler verhindert nie die Bewertung."""
    try:
        path = Path(os.getenv("HOLDOUT_LOG_PATH", str(_HOLDOUT_LOG)))
        entries = []
        if path.exists():
            try:
                entries = json.loads(path.read_text() or "[]")
            except Exception:
                entries = []
        entries.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "strategy": strategy, "holdout_start": start, "holdout_end": end,
            "note": note,
        })
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, indent=1))
    except Exception as e:  # pragma: no cover - defensiv
        log.warning("Holdout-Zugriffsprotokoll fehlgeschlagen (%s)", e)


def holdout_access_count(strategy: str | None = None) -> int:
    """Wie oft wurde das Holdout schon angefasst (optional je Strategie)?
    Fail-open: 0 bei fehlendem/kaputtem Log."""
    try:
        path = Path(os.getenv("HOLDOUT_LOG_PATH", str(_HOLDOUT_LOG)))
        if not path.exists():
            return 0
        entries = json.loads(path.read_text() or "[]")
        if strategy is None:
            return len(entries)
        return sum(1 for e in entries if e.get("strategy") == strategy)
    except Exception:
        return 0
