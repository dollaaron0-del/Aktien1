"""
EntryFilter – macht das Gelernte konsultierbar: ein Urteil pro Einstieg.

Bündelt die CalibrationModel-Befunde zu einer einzigen Entscheidung
PROCEED / CAUTION / AVOID / NEUTRAL – samt Begründung. Gedacht, um später im
Strategy-Scoring konsultiert zu werden (z.B. einen BUY im verlustreichen
Sentiment-Band 0.6–0.7 herabstufen). Greift NICHT selbst in den Live-Pfad ein.

Bewusst konservativ & ehrlich:
  * Urteilt nur, wenn der zugrunde liegende Kalibrierungs-Bucket belastbar ist
    (genug Beobachtungen). Sonst NEUTRAL – "wir wissen es nicht".
  * Basiert auf Papier-Backfill-Outcomes → liefert eine Tendenz, keine Garantie.

Entkoppelt vom Lernen: lädt ein bereits gefittetes CalibrationModel
(data/calibration.json). Re-Fit passiert via scripts/calibrate.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from analyzers.calibration import CalibrationModel

# Schwellen auf der erwarteten Kante (% Ø-P&L pro Trade, geshrinkt).
_EDGE_AVOID = -0.5    # darunter: Finger weg
_EDGE_GOOD = 0.5      # darüber: grünes Licht


@dataclass
class EntryVerdict:
    verdict: str                 # PROCEED | CAUTION | AVOID | NEUTRAL
    p_win: float
    expected_edge: float
    reliable: bool
    reasons: List[str] = field(default_factory=list)

    @property
    def block(self) -> bool:
        """True, wenn der Filter vom Einstieg abrät (für einfache Gate-Nutzung)."""
        return self.verdict == "AVOID"


class EntryFilter:
    def __init__(self, model: Optional[CalibrationModel] = None,
                 dimensions=("sentiment", "theme", "regime")):
        if model is None:
            model = CalibrationModel()
            model.load()  # no-op, wenn keine Datei → Globalquote 0.5
        self.model = model
        # Rückwärtskompatibel: einzelne Dimension als String erlaubt.
        if isinstance(dimensions, str):
            dimensions = (dimensions,)
        self.dimensions = tuple(dimensions)

    def _verdict_for(self, features: Dict, dimension: str) -> Optional[EntryVerdict]:
        cal = self.model.calibrate(features, dimension=dimension)
        reason = (f"{cal.basis}: P(Win)={cal.p_win:.0%}, "
                  f"Edge={cal.expected_edge:+.2f}%, n={cal.n_support}")
        if not cal.reliable:
            return EntryVerdict("NEUTRAL", cal.p_win, cal.expected_edge, False,
                                [reason + " → zu dünn (NEUTRAL)"])
        if cal.expected_edge <= _EDGE_AVOID:
            v = "AVOID"; reason += f" → ≤{_EDGE_AVOID:+.1f}% meiden"
        elif cal.expected_edge >= _EDGE_GOOD:
            v = "PROCEED"; reason += f" → ≥{_EDGE_GOOD:+.1f}% ok"
        else:
            v = "CAUTION"; reason += " → grenzwertig"
        return EntryVerdict(v, cal.p_win, cal.expected_edge, True, [reason])

    def evaluate(self, features: Dict) -> EntryVerdict:
        """Kombiniert alle konfigurierten Dimensionen zu einer Netto-Kante.

        Die belastbaren Dimensionen werden nach **√n_support** gewichtet gemittelt.
        √n statt n, weil grobe Dimensionen (z.B. 'regime' trifft JEDE Zeile,
        n≈Gesamtdatensatz) sonst die feinen, ticker-spezifischen Dimensionen
        (sentiment-Band, theme) systematisch erdrücken würden – mehr Beobachtungen
        heißt hier nicht proportional mehr Information, weil der Bucket breiter
        ist. √n dämpft das, ohne die Stichprobengröße zu ignorieren. Sind alle zu
        dünn → NEUTRAL. Auf die Netto-Kante werden dieselben Schwellen angewandt.
        """
        cals = {d: self.model.calibrate(features, dimension=d) for d in self.dimensions}
        reasons: List[str] = [
            f"[{d}] {c.basis}: P(Win)={c.p_win:.0%}, Edge={c.expected_edge:+.2f}%, "
            f"n={c.n_support}{'' if c.reliable else ' (dünn)'}"
            for d, c in cals.items()
        ]
        reliable = [c for c in cals.values() if c.reliable]
        if not reliable:
            base = next(iter(cals.values()))
            return EntryVerdict("NEUTRAL", base.p_win, base.expected_edge, False, reasons)

        wsum = sum(c.n_support ** 0.5 for c in reliable) or 1
        net_edge = sum(c.expected_edge * c.n_support ** 0.5 for c in reliable) / wsum
        net_pwin = sum(c.p_win * c.n_support ** 0.5 for c in reliable) / wsum

        if net_edge <= _EDGE_AVOID:
            verdict = "AVOID"
        elif net_edge >= _EDGE_GOOD:
            verdict = "PROCEED"
        else:
            verdict = "CAUTION"
        reasons.append(f"→ Netto-Kante {net_edge:+.2f}% (√n-gewichtet) ⇒ {verdict}")
        return EntryVerdict(verdict, round(net_pwin, 4), round(net_edge, 4), True, reasons)
