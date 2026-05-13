"""
Focus Modes – Anlagestrategie auswählbar je nach Lebensphase

WEALTH_BUILDING (Vermögensaufbau):
  Aggressives Wachstum, Compounding. Höhere Volatilität akzeptiert,
  größere Take-Profits, längere Haltedauer für Vervielfacher.

INCOME (Regelmäßige Auszahlungen):
  Konservativ. Bevorzugt stabile Werte, schnellere Take-Profits,
  engere Stop-Losses. Monatliche Ausschüttung priorisiert.

TARGET_GOAL (Bestimmtes Ziel):
  Zielbetrag bis Zieldatum. Risikobereitschaft skaliert je nachdem,
  wie weit man vom Ziel entfernt ist – aggressiver wenn hinter Plan,
  defensiver wenn voraus.
"""

from dataclasses import dataclass
from datetime import datetime, date
from typing import Optional, Dict
import math


@dataclass
class FocusProfile:
    """Konkrete Parameter-Anpassungen je Modus."""
    name:                  str
    label:                 str
    buy_threshold_delta:   float  # added to base buy_threshold
    stop_loss_pct:         float  # absolute (overrides config)
    take_profit_pct:       float  # absolute (overrides config)
    max_position_pct:      float  # absolute (overrides config)
    preferred_hold_days:   int    # used as soft cap on Claude's suggestion
    min_sentiment_for_buy: float  # absolute minimum (hard floor)
    description:           str


class FocusMode:
    WEALTH_BUILDING = "WEALTH_BUILDING"
    INCOME          = "INCOME"
    TARGET_GOAL     = "TARGET_GOAL"

    PROFILES: Dict[str, FocusProfile] = {
        WEALTH_BUILDING: FocusProfile(
            name="WEALTH_BUILDING",
            label="🚀 Vermögensaufbau",
            buy_threshold_delta=0.00,
            stop_loss_pct=0.08,
            take_profit_pct=0.25,
            max_position_pct=0.25,
            preferred_hold_days=20,
            min_sentiment_for_buy=0.60,
            description=(
                "Maximales Wachstum durch Compounding. Akzeptiert höhere Volatilität "
                "für mehr Upside. Take-Profit erst bei +25%, Stop-Loss großzügig bei -8%."
            ),
        ),
        INCOME: FocusProfile(
            name="INCOME",
            label="💸 Regelmäßige Auszahlungen",
            buy_threshold_delta=0.10,   # require higher confidence
            stop_loss_pct=0.05,
            take_profit_pct=0.12,
            max_position_pct=0.15,
            preferred_hold_days=10,
            min_sentiment_for_buy=0.72,
            description=(
                "Kapitalerhalt zuerst. Nur sehr hohe Konfidenz akzeptiert. "
                "Schnelle Take-Profits bei +12%, enge Stop-Losses bei -5%."
            ),
        ),
        TARGET_GOAL: FocusProfile(
            name="TARGET_GOAL",
            label="🎯 Zielbetrag",
            buy_threshold_delta=0.00,
            stop_loss_pct=0.07,
            take_profit_pct=0.18,
            max_position_pct=0.20,
            preferred_hold_days=15,
            min_sentiment_for_buy=0.65,
            description=(
                "Risiko wird dynamisch skaliert je nach Abstand zum Ziel. "
                "Hinter Plan → aggressiver. Voraus → defensiver."
            ),
        ),
    }

    @classmethod
    def get(cls, mode: str) -> FocusProfile:
        return cls.PROFILES.get(mode, cls.PROFILES[cls.WEALTH_BUILDING])

    @classmethod
    def all_modes(cls) -> Dict[str, FocusProfile]:
        return cls.PROFILES


class FocusController:
    """
    Wendet Focus-Modus auf konkrete Trade-Entscheidungen an.
    Für TARGET_GOAL berechnet auch dynamische Risiko-Anpassung
    abhängig von Zielfortschritt.
    """

    def __init__(
        self,
        mode: str,
        target_amount: Optional[float] = None,
        target_date: Optional[str] = None,
        initial_capital: float = 10000.0,
    ):
        self.mode = mode if mode in FocusMode.PROFILES else FocusMode.WEALTH_BUILDING
        self.profile = FocusMode.get(self.mode)
        self.target_amount = target_amount
        self.target_date = self._parse_date(target_date) if target_date else None
        self.initial_capital = initial_capital

    @staticmethod
    def _parse_date(raw: str) -> Optional[date]:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    # ── Trade parameters ──────────────────────────────────────────────────────

    def get_effective_threshold(self, base: float, portfolio_value: float) -> float:
        """Mindest-Sentiment-Score für Käufe in diesem Modus."""
        adjusted = base + self.profile.buy_threshold_delta

        # TARGET_GOAL: scale by goal progress
        if self.mode == FocusMode.TARGET_GOAL and self.target_amount:
            urgency = self._goal_urgency(portfolio_value)
            # urgency > 1: behind plan → lower threshold (more aggressive)
            # urgency < 1: ahead of plan → raise threshold (more selective)
            adjusted -= (urgency - 1.0) * 0.05

        return max(self.profile.min_sentiment_for_buy, min(adjusted, 0.95))

    def get_stop_loss_pct(self) -> float:
        return self.profile.stop_loss_pct

    def get_take_profit_pct(self) -> float:
        return self.profile.take_profit_pct

    def get_max_position_pct(self, portfolio_value: float) -> float:
        if self.mode == FocusMode.TARGET_GOAL and self.target_amount:
            urgency = self._goal_urgency(portfolio_value)
            # Behind plan → allow larger positions (but max 30%)
            scaled = self.profile.max_position_pct * urgency
            return max(0.10, min(scaled, 0.30))
        return self.profile.max_position_pct

    def cap_hold_days(self, claude_suggestion: int) -> int:
        """Cap Claude's suggestion at the mode's preferred holding period."""
        preferred = self.profile.preferred_hold_days
        if self.mode == FocusMode.INCOME:
            return min(claude_suggestion, preferred)
        return claude_suggestion  # other modes trust Claude

    # ── TARGET_GOAL helpers ───────────────────────────────────────────────────

    def _goal_urgency(self, portfolio_value: float) -> float:
        """
        Returns 1.0 = on track, >1.0 = behind plan, <1.0 = ahead.
        Based on linear progress from initial_capital → target_amount.
        """
        if not self.target_amount or not self.target_date:
            return 1.0

        today = datetime.utcnow().date()
        if today >= self.target_date:
            return 1.5  # at or past deadline → most aggressive

        total_days = (self.target_date - today).days
        # Linear expected value today (rough heuristic; assumes 1.5x init at start)
        expected_today = self.initial_capital + (
            (self.target_amount - self.initial_capital)
            * max(0, (365 - total_days)) / 365
        )
        if portfolio_value >= self.target_amount:
            return 0.5  # already at goal
        if expected_today <= 0:
            return 1.0
        ratio = expected_today / max(portfolio_value, 1)
        return max(0.5, min(ratio, 1.5))

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_info(self, portfolio_value: float) -> Dict:
        info = {
            "mode": self.mode,
            "label": self.profile.label,
            "description": self.profile.description,
            "stop_loss_pct": self.profile.stop_loss_pct,
            "take_profit_pct": self.profile.take_profit_pct,
            "max_position_pct": self.profile.max_position_pct,
            "min_sentiment": self.profile.min_sentiment_for_buy,
            "preferred_hold_days": self.profile.preferred_hold_days,
        }
        if self.mode == FocusMode.TARGET_GOAL and self.target_amount and self.target_date:
            urgency = self._goal_urgency(portfolio_value)
            days_left = (self.target_date - datetime.utcnow().date()).days
            progress = (portfolio_value - self.initial_capital) / max(
                self.target_amount - self.initial_capital, 1
            ) * 100
            info.update({
                "target_amount": self.target_amount,
                "target_date": self.target_date.isoformat(),
                "days_remaining": max(days_left, 0),
                "progress_pct": round(max(0, min(progress, 100)), 1),
                "urgency": round(urgency, 2),
                "on_track": 0.9 <= urgency <= 1.1,
                "behind_plan": urgency > 1.1,
            })
        return info
