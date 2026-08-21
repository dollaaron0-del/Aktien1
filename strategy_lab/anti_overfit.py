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

3. Block-Bootstrap (Roadmap 6.8d, "Resampling statt Synthetik"): die
   bisherigen Bootstrap-CIs (walkforward._bootstrap_ci, track_record.
   _bootstrap_mean_ci, …) resamplen EINZELNE Werte unabhängig (i.i.d.) — bei
   seriell korrelierten Trade-Renditen (gemeinsame Regime-/Whipsaw-Phasen,
   Gewinn-/Verlust-Serien) unterschätzt das die wahre Unsicherheit, die CI
   wirkt enger als sie ist. block_bootstrap_ci() resampelt stattdessen
   ZUSAMMENHÄNGENDE Blöcke (Moving-Block-Bootstrap) und macht die Validierung
   dadurch strenger (breitere CI), OHNE künstliche Kurse zu erfinden — reines
   Resampling der tatsächlich beobachteten, chronologisch sortierten Werte.
   Bewusst NICHT automatisch in die bestehenden ROBUST/SIGNAL-Verdikte
   verdrahtet (würde bestehende Verdikte rückwirkend verschärfen können) —
   siehe scripts/block_bootstrap_check.py für den Vergleich auf echten Daten.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import numpy as np

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


def block_bootstrap_ci(values: List[float], block_size: int = 5, ci: float = 0.90,
                       iters: int = 2000, seed: int = 20260809) -> Tuple[float, float, float]:
    """Moving-Block-Bootstrap-CI auf den Mittelwert (Roadmap 6.8d) — härtere
    Alternative zum i.i.d.-Bootstrap bei seriell korrelierten Werten
    (chronologische Reihenfolge in `values` ist Voraussetzung, NICHT vorher
    shuffeln). Zieht wiederholt zusammenhängende Blöcke der Länge
    `block_size` (zirkulär, Wraparound am Ende) statt Einzelwerten, bis die
    Ziellänge n erreicht ist — erhält so lokale Autokorrelation (Gewinn-/
    Verlust-Serien), die eine i.i.d.-Ziehung wegmitteln würde.

    Randfälle wie _bootstrap_ci (walkforward.py): n=0 → (0,0,1); n=1 →
    degeneriert auf den Einzelwert. block_size wird auf höchstens n gekappt.
    """
    x = np.asarray([v for v in values if v is not None], dtype=float)
    n = len(x)
    if n == 0:
        return (0.0, 0.0, 1.0)
    if n == 1:
        return (float(x[0]), float(x[0]), 1.0 if x[0] <= 0 else 0.0)
    bs = max(1, min(block_size, n))
    n_blocks = -(-n // bs)                                  # ceil(n / bs)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(iters, n_blocks))
    means = np.empty(iters, dtype=float)
    for i in range(iters):
        pieces = [x[np.arange(s, s + bs) % n] for s in starts[i]]
        means[i] = np.concatenate(pieces)[:n].mean()
    lo = float(np.quantile(means, (1 - ci) / 2))
    hi = float(np.quantile(means, 1 - (1 - ci) / 2))
    return (lo, hi, float((means <= 0).mean()))
