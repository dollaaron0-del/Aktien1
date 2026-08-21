"""
Erfolgs-/Abbruchkriterien je Strategie-These (Roadmap 6.10).

Bisher konnte das Lab unbegrenzt weiterlaufen, ohne dass je eine Entscheidung
fällt — als bewusstes Hobby okay, aber ohne kodiertes Kriterium bleibt es
Zufall statt Entscheidung, WANN eine These als bewiesen oder gescheitert
gilt. Dieses Modul kodiert genau das: pro benannter These (z.B.
"mechanical_baseline") ein Verdikt-Kriterium, das automatisch eines von drei
Ergebnissen liefert:

  PENDING   – weiter sammeln, weder Stichprobe noch Zeit-Budget erschöpft.
  PROVEN    – Stichprobe erreicht UND Kante belegt (Bootstrap-CI-Untergrenze
              der Ø-Rendite > 0 UND schlägt Buy&Hold).
  ABANDONED – entweder Stichprobe erreicht, aber Kriterium NICHT erfüllt,
              ODER Zeit-Budget abgelaufen, bevor die Stichprobe erreicht war.
              „VERWERFEN und nicht wiederbeleben" (Roadmap-Wortlaut): ein
              einmal gefälltes Verdikt wird von evaluate() NIE mehr neu
              berechnet, auch wenn spätere Daten anders aussähen.

User-Entscheidung 12.7.2026: n_min=150 Live-Trades ODER 24 Monate
Zeit-Budget (was zuerst eintritt) — bewusst am oberen Ende der besprochenen
18–24-Monats-Spanne, um auch seltenere Marktzyklen abzudecken.

Nutzt dieselbe Bootstrap-Statistik wie scripts/track_record.py (importiert
_bootstrap_mean_ci von dort, keine zweite Implementierung). Reine
Dataclass-/JSON-Mechanik hier, keine DB-Zugriffe — der Aufrufer lädt die
Trade-Renditen (z.B. via track_record._load_trades) und übergibt sie.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

PENDING = "PENDING"
PROVEN = "PROVEN"
ABANDONED = "ABANDONED"

_REGISTRY_FILE = Path(__file__).resolve().parent.parent / "data" / "thesis_registry.json"

DEFAULT_N_MIN = 150
DEFAULT_TIME_BUDGET_MONTHS = 24


@dataclass
class Thesis:
    name: str
    started_at: str                        # ISO-Datum, wann die Uhr zu laufen begann
    n_min: int = DEFAULT_N_MIN
    time_budget_months: int = DEFAULT_TIME_BUDGET_MONTHS
    description: str = ""
    status: str = PENDING                  # eingefroren, sobald PROVEN/ABANDONED
    verdict_at: Optional[str] = None
    verdict_reason: str = ""


@dataclass
class ThesisVerdict:
    name: str
    status: str
    n_trades: int
    n_min: int
    months_elapsed: float
    time_budget_months: int
    edge_ci_lo: Optional[float]
    beats_bh_ci_lo: Optional[float]
    reason: str


# ── Registry-IO ───────────────────────────────────────────────────────────────

def _registry_path() -> Path:
    return Path(os.getenv("THESIS_REGISTRY_PATH", str(_REGISTRY_FILE)))


def load_registry() -> Dict[str, Thesis]:
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text() or "{}")
    except Exception:
        return {}
    return {name: Thesis(**data) for name, data in raw.items()}


def save_registry(registry: Dict[str, Thesis]) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({name: asdict(t) for name, t in registry.items()}, indent=2))
    tmp.replace(path)


def register_thesis(
    name: str,
    n_min: int = DEFAULT_N_MIN,
    time_budget_months: int = DEFAULT_TIME_BUDGET_MONTHS,
    description: str = "",
    started_at: Optional[str] = None,
    force: bool = False,
) -> Thesis:
    """Legt eine neue These an. Idempotent: eine bereits registrierte These
    (egal welcher Status) bleibt unverändert, außer `force=True` — das ist
    der Punkt der Übung, ein gefälltes Verdikt "nicht wiederzubeleben"."""
    registry = load_registry()
    existing = registry.get(name)
    if existing is not None and not force:
        return existing
    thesis = Thesis(name=name, started_at=started_at or date.today().isoformat(),
                    n_min=n_min, time_budget_months=time_budget_months,
                    description=description)
    registry[name] = thesis
    save_registry(registry)
    return thesis


def is_abandoned(name: str) -> bool:
    t = load_registry().get(name)
    return bool(t and t.status == ABANDONED)


# ── Verdikt ─────────────────────────────────────────────────────────────────

def _months_between(started_at: str, as_of: Optional[date]) -> float:
    start = date.fromisoformat(started_at[:10])
    end = as_of or date.today()
    return (end - start).days / 30.4375


def evaluate(
    name: str,
    returns: List[float],
    excess_returns: Optional[List[float]] = None,
    as_of: Optional[date] = None,
    seed: int = 20260712,
) -> ThesisVerdict:
    """Wertet eine REGISTRIERTE These gegen aktuelle Trade-Renditen aus.
    `returns` = Ø-Rendite je Trade (%), `excess_returns` = Rendite abzüglich
    Buy&Hold über dasselbe Haltefenster (paired, optional — ohne sie gilt
    das Schlägt-B&H-Kriterium als nicht erfüllbar, PROVEN bleibt dann aus).

    Einmal PROVEN/ABANDONED, liefert jeder weitere Aufruf dasselbe
    eingefrorene Verdikt zurück — KEINE Neuberechnung, das ist "nicht
    wiederbeleben" im Code. Wirft KeyError, wenn die These nicht existiert
    (bewusst kein Auto-Register — Registrierung ist ein deliberater Schritt
    mit eigenen Parametern, kein Nebeneffekt)."""
    registry = load_registry()
    thesis = registry.get(name)
    if thesis is None:
        raise KeyError(f"These '{name}' ist nicht registriert — erst register_thesis() aufrufen.")

    months = round(_months_between(thesis.started_at, as_of), 1)

    if thesis.status != PENDING:
        return ThesisVerdict(name=name, status=thesis.status, n_trades=len(returns),
                             n_min=thesis.n_min, months_elapsed=months,
                             time_budget_months=thesis.time_budget_months,
                             edge_ci_lo=None, beats_bh_ci_lo=None, reason=thesis.verdict_reason)

    from scripts.track_record import _bootstrap_mean_ci
    rng = np.random.default_rng(seed)
    n = len(returns)

    edge_lo: Optional[float] = None
    if returns:
        ci = _bootstrap_mean_ci(returns, rng)
        if np.isfinite(ci.get("lo", float("nan"))):
            edge_lo = float(ci["lo"])

    excess_lo: Optional[float] = None
    if excess_returns:
        ci = _bootstrap_mean_ci(excess_returns, rng)
        if np.isfinite(ci.get("lo", float("nan"))):
            excess_lo = float(ci["lo"])

    success = edge_lo is not None and edge_lo > 0 and excess_lo is not None and excess_lo > 0

    if n >= thesis.n_min:
        if success:
            status = PROVEN
            reason = (f"{n}≥{thesis.n_min} Trades, Kante-CI-Untergrenze {edge_lo:+.2f}%>0, "
                      f"Excess-CI-Untergrenze {excess_lo:+.2f}%>0")
        else:
            status = ABANDONED
            reason = (f"{n}≥{thesis.n_min} Trades erreicht, Kriterium NICHT erfüllt "
                      f"(Kante-CI-lo={edge_lo}, Excess-CI-lo={excess_lo})")
    elif months >= thesis.time_budget_months:
        status = ABANDONED
        reason = (f"Zeit-Budget ({thesis.time_budget_months} Monate) abgelaufen, "
                  f"bevor die Stichprobe erreicht war ({n}/{thesis.n_min} Trades)")
    else:
        status = PENDING
        reason = f"{n}/{thesis.n_min} Trades, {months:.1f}/{thesis.time_budget_months} Monate — weiter sammeln"

    if status != PENDING:
        thesis.status = status
        thesis.verdict_at = (as_of or date.today()).isoformat()
        thesis.verdict_reason = reason
        registry[name] = thesis
        save_registry(registry)

    return ThesisVerdict(name=name, status=status, n_trades=n, n_min=thesis.n_min,
                         months_elapsed=months, time_budget_months=thesis.time_budget_months,
                         edge_ci_lo=edge_lo, beats_bh_ci_lo=excess_lo, reason=reason)
