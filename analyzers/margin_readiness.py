"""
MarginReadinessChecker – bewertet ob der Bot bereit ist Margin (Hebel) zu nutzen.

Kriterien:
  1. Mindest-Trades:     >= 20 abgeschlossene Trades
  2. Win-Rate:           >= 60% (letzte 20 Trades)
  3. Ø-Rendite/Trade:    >= 2.5% (nach Verlusten)
  4. Max. Verlustserie:  <= 3 aufeinanderfolgende Verluste zuletzt
  5. Stop-Loss-Rate:     <= 35% (SL-Treffer / alle Trades)

Ausgabe: BEREIT | VORSICHT | NICHT BEREIT
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from logger import get_logger

log = get_logger(__name__)

# Schwellwerte
_MIN_TRADES        = 20
_MIN_WIN_RATE      = 0.60   # 60%
_MIN_AVG_RETURN    = 2.5    # % pro Trade
_MAX_CONSEC_LOSSES = 3
_MAX_SL_RATE       = 0.35   # 35%


@dataclass
class CriterionResult:
    name:    str
    passed:  bool
    value:   str     # aktueller Wert
    target:  str     # Zielwert
    weight:  str     # "KRITISCH" | "WICHTIG" | "BONUS"
    note:    str = ""


@dataclass
class MarginReadiness:
    status:     str                              # "BEREIT" | "VORSICHT" | "NICHT BEREIT"
    score:      int                              # 0–100
    criteria:   List[CriterionResult] = field(default_factory=list)
    suggestion: str = ""
    recommended_factor: float = 1.0             # 1.0 = kein Hebel, 1.5 = 50% Hebel
    analyzed_at: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "BEREIT"

    def to_text(self) -> str:
        icon = {"BEREIT": "✅", "VORSICHT": "⚠️", "NICHT BEREIT": "❌"}.get(self.status, "?")
        lines = [
            f"=== MARGIN-BEREITSCHAFT ===",
            f"Status:   {icon} {self.status}",
            f"Score:    {self.score}/100",
            f"Empfohlener Hebel: {self.recommended_factor:.1f}×",
            f"",
            "Kriterien:",
        ]
        for c in self.criteria:
            tick = "✓" if c.passed else "✗"
            lines.append(
                f"  [{tick}] {c.name} ({c.weight})\n"
                f"       Ist: {c.value}  |  Ziel: {c.target}"
                + (f"\n       {c.note}" if c.note else "")
            )
        lines += ["", f"Empfehlung: {self.suggestion}", "=" * 30]
        return "\n".join(lines)

    def to_telegram(self) -> str:
        icon = {"BEREIT": "✅", "VORSICHT": "⚠️", "NICHT BEREIT": "❌"}.get(self.status, "?")
        lines = [
            f"{icon} *Margin-Bereitschaft: {self.status}*",
            f"Score: {self.score}/100 | Hebel: {self.recommended_factor:.1f}×",
            "",
        ]
        for c in self.criteria:
            tick = "✓" if c.passed else "✗"
            lines.append(f"  [{tick}] {c.name}: {c.value} (Ziel: {c.target})")
        lines += ["", f"_{self.suggestion}_"]
        return "\n".join(lines)


class MarginReadinessChecker:

    def __init__(self, tracker):
        self._tracker = tracker

    def check(self) -> MarginReadiness:
        report    = self._tracker.get_accuracy_report()
        total     = report.get("total_closed", 0)
        recent    = self._tracker.get_recent_trades(n=20)
        exit_stats = {r["category"]: r for r in self._tracker.get_exit_reason_stats()}

        criteria: List[CriterionResult] = []
        score = 0

        # ── 1. Mindest-Trades ────────────────────────────────────────────────
        c1 = CriterionResult(
            name="Mindest-Trades",
            passed=total >= _MIN_TRADES,
            value=str(total),
            target=f">= {_MIN_TRADES}",
            weight="KRITISCH",
            note="" if total >= _MIN_TRADES else f"Noch {_MIN_TRADES - total} Trades fehlen.",
        )
        criteria.append(c1)
        if c1.passed:
            score += 25

        # ── 2. Win-Rate (letzte 20 Trades) ───────────────────────────────────
        recent_wins = sum(1 for t in recent if (t.get("actual_return_pct") or 0) > 0)
        recent_wr   = recent_wins / max(len(recent), 1)
        c2 = CriterionResult(
            name="Win-Rate (letzte 20)",
            passed=recent_wr >= _MIN_WIN_RATE,
            value=f"{recent_wr*100:.0f}%",
            target=f">= {_MIN_WIN_RATE*100:.0f}%",
            weight="KRITISCH",
        )
        criteria.append(c2)
        if c2.passed:
            score += 30
        elif recent_wr >= 0.50:
            score += 10

        # ── 3. Ø-Rendite pro Trade ───────────────────────────────────────────
        avg_ret = report.get("avg_return_pct", 0.0)
        c3 = CriterionResult(
            name="Ø-Rendite/Trade",
            passed=avg_ret >= _MIN_AVG_RETURN,
            value=f"{avg_ret:+.2f}%",
            target=f">= {_MIN_AVG_RETURN:.1f}%",
            weight="WICHTIG",
        )
        criteria.append(c3)
        if c3.passed:
            score += 20
        elif avg_ret > 0:
            score += 8

        # ── 4. Max. aufeinanderfolgende Verluste ──────────────────────────────
        consec = _max_consecutive_losses(recent)
        c4 = CriterionResult(
            name="Max. Verlustserie (zuletzt)",
            passed=consec <= _MAX_CONSEC_LOSSES,
            value=str(consec),
            target=f"<= {_MAX_CONSEC_LOSSES}",
            weight="WICHTIG",
            note="Hohe Verlustserien erhöhen Margin-Call-Risiko." if consec > _MAX_CONSEC_LOSSES else "",
        )
        criteria.append(c4)
        if c4.passed:
            score += 15
        elif consec <= 5:
            score += 5

        # ── 5. Stop-Loss-Rate ─────────────────────────────────────────────────
        sl_trades = exit_stats.get("stop_loss", {}).get("trades", 0)
        sl_rate   = sl_trades / max(total, 1)
        c5 = CriterionResult(
            name="Stop-Loss-Rate",
            passed=sl_rate <= _MAX_SL_RATE,
            value=f"{sl_rate*100:.0f}%",
            target=f"<= {_MAX_SL_RATE*100:.0f}%",
            weight="WICHTIG",
            note="Viele SL-Treffer mit Hebel werden zu großen Verlusten." if sl_rate > _MAX_SL_RATE else "",
        )
        criteria.append(c5)
        if c5.passed:
            score += 10
        elif sl_rate <= 0.45:
            score += 4

        # ── Status + Empfehlung ───────────────────────────────────────────────
        critical_passed = all(c.passed for c in criteria if c.weight == "KRITISCH")
        important_passed = sum(1 for c in criteria if c.weight == "WICHTIG" and c.passed)

        if critical_passed and important_passed >= 2 and score >= 70:
            status = "BEREIT"
            factor = min(1.0 + (score - 70) / 100, 1.5)  # 1.0x–1.5x je nach Score
            factor = round(factor * 4) / 4  # auf 0.25 runden
            suggestion = (
                f"Der Bot hat bewiesen dass er zuverlässig handelt. "
                f"Empfohlen: {factor:.2f}× Hebel nur bei HIGH-Confidence-Signalen. "
                f"Aktivieren mit: USE_MARGIN=true in .env"
            )
        elif critical_passed and score >= 45:
            status = "VORSICHT"
            factor = 1.0
            missing = [c.name for c in criteria if not c.passed]
            suggestion = (
                f"Grundvoraussetzungen erfüllt, aber noch nicht optimal. "
                f"Verbesserungsbedarf: {', '.join(missing)}. "
                f"Noch nicht empfohlen Margin zu aktivieren."
            )
        else:
            status = "NICHT BEREIT"
            factor = 1.0
            missing_crit = [c.name for c in criteria if c.weight == "KRITISCH" and not c.passed]
            suggestion = (
                f"Kritische Kriterien nicht erfüllt: {', '.join(missing_crit)}. "
                f"Margin würde das Verlustrisiko erheblich erhöhen. "
                f"Weiter im Normalmodus handeln."
            )

        return MarginReadiness(
            status=status,
            score=min(score, 100),
            criteria=criteria,
            suggestion=suggestion,
            recommended_factor=factor,
            analyzed_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


def _max_consecutive_losses(trades: List[Dict]) -> int:
    max_streak = 0
    streak = 0
    for t in trades:
        if (t.get("actual_return_pct") or 0) < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak
