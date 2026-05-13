"""
GoalRiskAssessor – bewertet nach jedem abgeschlossenen Trade, ob das Portfolio-Ziel
noch erreichbar ist, ohne unverhältnismäßig hohes Risiko einzugehen.

Logik:
  1. Berechne die benötigte annualisierte Rendite, um das Zieldatum zu erreichen.
  2. Vergleiche mit der realistisch erreichbaren Rendite auf Basis der letzten Trades.
  3. Stufe das Risiko ein: OK / CAUTION / DANGER / UNREACHABLE.
  4. Schlage Maßnahmen vor (Strategie anpassen, Ziel/Datum korrigieren).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List, Dict

# Risk levels
OK = "OK"
CAUTION = "CAUTION"
DANGER = "DANGER"
UNREACHABLE = "UNREACHABLE"

# Thresholds: required annual return vs realistic limit
_CAUTION_THRESHOLD = 0.30    # >30 % p.a. benötigt → Vorsicht
_DANGER_THRESHOLD = 0.60     # >60 % p.a. benötigt → Gefahr
_UNREACHABLE_THRESHOLD = 1.5 # >150 % p.a. benötigt → praktisch unerreichbar

# Conservative cap on what swing trading can realistically achieve p.a.
_REALISTIC_MAX_ANNUAL_RETURN = 0.50  # 50 % p.a. obere Grenze für konservative Schätzung


@dataclass
class GoalAssessment:
    portfolio_value: float
    target_value: float
    days_remaining: int
    required_annual_return: float     # dezimal, z.B. 0.25 = 25 % p.a.
    realistic_annual_return: float    # basiert auf bisherigen Trades
    shortfall_pct: float              # wie weit fehlt man noch (%)
    risk_level: str                   # OK | CAUTION | DANGER | UNREACHABLE
    actions: List[str] = field(default_factory=list)
    note: str = ""

    @property
    def on_track(self) -> bool:
        return self.risk_level == OK

    def to_text(self) -> str:
        lines = [
            f"=== ZIEL-RISIKOANALYSE ===",
            f"Portfolio:       ${self.portfolio_value:,.2f}",
            f"Zielwert:        ${self.target_value:,.2f}",
            f"Fehlend:         {self.shortfall_pct:.1f}%",
            f"Tage bis Ziel:   {self.days_remaining}",
            f"Benötigte Rendite p.a.: {self.required_annual_return*100:.1f}%",
            f"Realistische Rendite p.a.: {self.realistic_annual_return*100:.1f}%",
            f"Risiko-Level:    {self.risk_level}",
        ]
        if self.note:
            lines.append(f"Hinweis:         {self.note}")
        if self.actions:
            lines.append("Empfehlungen:")
            for a in self.actions:
                lines.append(f"  → {a}")
        lines.append("=" * 30)
        return "\n".join(lines)


class GoalRiskAssessor:
    """
    Nach jedem abgeschlossenen Trade aufrufen, um die Zielerreichbarkeit zu prüfen.
    """

    def __init__(
        self,
        target_value: float,
        target_date_str: str,       # "YYYY-MM-DD", leer = kein Datum gesetzt
        initial_capital: float,
    ):
        self.target_value = target_value
        self.initial_capital = initial_capital
        self._target_date: Optional[date] = None
        if target_date_str:
            try:
                self._target_date = datetime.strptime(target_date_str[:10], "%Y-%m-%d").date()
            except ValueError:
                pass

    @property
    def active(self) -> bool:
        """True wenn ein konkretes Ziel mit Datum gesetzt ist."""
        return self.target_value > 0 and self._target_date is not None

    def assess(
        self,
        portfolio_value: float,
        tracker_stats: Optional[Dict] = None,
    ) -> Optional[GoalAssessment]:
        """
        Führt die Risikoanalyse durch.
        tracker_stats: Dict aus PerformanceTracker.get_stats() (kann None sein)
        """
        if not self.active:
            return None

        today = date.today()
        days_remaining = (self._target_date - today).days

        if days_remaining <= 0:
            # Zieldatum bereits überschritten
            return GoalAssessment(
                portfolio_value=portfolio_value,
                target_value=self.target_value,
                days_remaining=0,
                required_annual_return=float("inf"),
                realistic_annual_return=0.0,
                shortfall_pct=max(0.0, (self.target_value - portfolio_value) / self.target_value * 100),
                risk_level=UNREACHABLE if portfolio_value < self.target_value else OK,
                note="Zieldatum überschritten.",
                actions=["Zieldatum neu setzen (TARGET_GOAL_DATE in .env)"] if portfolio_value < self.target_value else [],
            )

        shortfall_pct = max(0.0, (self.target_value - portfolio_value) / self.target_value * 100)

        # Already reached?
        if portfolio_value >= self.target_value:
            return GoalAssessment(
                portfolio_value=portfolio_value,
                target_value=self.target_value,
                days_remaining=days_remaining,
                required_annual_return=0.0,
                realistic_annual_return=self._realistic_return(tracker_stats),
                shortfall_pct=0.0,
                risk_level=OK,
                note="Ziel bereits erreicht!",
            )

        # Required annualized return to reach target
        years_left = days_remaining / 365.25
        required_annual = self._required_annual_return(portfolio_value, self.target_value, years_left)
        realistic_annual = self._realistic_return(tracker_stats)

        risk_level, actions, note = self._classify(
            required_annual, realistic_annual, shortfall_pct, years_left, portfolio_value
        )

        return GoalAssessment(
            portfolio_value=portfolio_value,
            target_value=self.target_value,
            days_remaining=days_remaining,
            required_annual_return=required_annual,
            realistic_annual_return=realistic_annual,
            shortfall_pct=shortfall_pct,
            risk_level=risk_level,
            actions=actions,
            note=note,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _required_annual_return(current: float, target: float, years: float) -> float:
        if years <= 0 or current <= 0:
            return float("inf")
        # compound: target = current * (1 + r)^years  → r = (target/current)^(1/years) - 1
        ratio = target / current
        return ratio ** (1.0 / years) - 1.0

    @staticmethod
    def _realistic_return(stats: Optional[Dict]) -> float:
        """
        Schätzt die realistisch erreichbare Jahresrendite aus vergangenen Trades.
        Nutzt avg_return_pct (pro Trade) × estimated_trades_per_year.
        """
        if not stats or stats.get("total_closed", 0) < 3:
            # Zu wenig Datenpunkte – konservative Annahme 15% p.a.
            return 0.15

        avg_return_pct = stats.get("avg_return", 0.0) or 0.0
        win_rate = stats.get("win_rate", 0.5) or 0.5

        # Expected return per trade (accounting for losers)
        # Conservative: assume ~20 trades / year, full-capital reuse
        trades_per_year = 20
        # Risk-adjusted: reduce by (1 - win_rate) to account for losses
        adjusted_return_per_trade = avg_return_pct / 100 * win_rate
        annual = adjusted_return_per_trade * trades_per_year

        # Cap at realistic maximum
        return min(max(annual, 0.0), _REALISTIC_MAX_ANNUAL_RETURN)

    @staticmethod
    def _classify(
        required: float,
        realistic: float,
        shortfall_pct: float,
        years_left: float,
        portfolio_value: float,
    ) -> tuple[str, List[str], str]:
        actions: List[str] = []
        note = ""

        if required == float("inf") or required > _UNREACHABLE_THRESHOLD:
            level = UNREACHABLE
            note = f"Benötigte Rendite ({required*100:.0f}% p.a.) weit über realistischem Maximum."
            actions += [
                "Zieldatum verlängern oder Zielwert reduzieren",
                "Regelmäßige Einzahlungen erwägen um die Lücke zu schließen",
                "Strategie beibehalten – kein erhöhtes Risiko eingehen",
            ]
        elif required > _DANGER_THRESHOLD:
            level = DANGER
            note = f"Benötigte Rendite ({required*100:.0f}% p.a.) deutlich über realistischem Niveau ({realistic*100:.0f}%)."
            actions += [
                "Zieldatum um mindestens 12–24 Monate verlängern",
                "Zielwert auf ein realistisches Niveau anpassen",
                "Keine erhöhte Positionsgrößen zur Kompensation – erhöht nur Verlustrisiko",
            ]
            if years_left < 1:
                actions.append("Zeitrahmen ist kritisch kurz – sofort Ziel überarbeiten")
        elif required > _CAUTION_THRESHOLD:
            level = CAUTION
            note = f"Benötigte Rendite ({required*100:.0f}% p.a.) erhöht, aber potenziell erreichbar."
            actions += [
                "Aktuelle Strategie beibehalten, keine unnötigen Risiken eingehen",
                "Verbesserungspotenzial: Signalqualität erhöhen, schwache Trades früher schließen",
            ]
            if required > realistic * 1.5:
                actions.append(
                    f"Achtung: Benötigte Rendite {required*100:.0f}% p.a. ist {required/realistic:.1f}× "
                    f"höher als bisher erzielt ({realistic*100:.0f}%) – Ziel überarbeiten empfohlen"
                )
        else:
            level = OK
            note = f"Ziel auf Kurs. Benötigte Rendite {required*100:.0f}% p.a. im realistischen Bereich."

        return level, actions, note
