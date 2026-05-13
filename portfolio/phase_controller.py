from typing import Dict


class PhaseController:
    """
    Controls the two lifecycle phases of the bot:
      GROWTH      – reinvest everything, compound gains
      DISTRIBUTION – monthly withdrawal once the capital target is hit
    """

    GROWTH = "GROWTH"
    DISTRIBUTION = "DISTRIBUTION"

    def __init__(
        self,
        initial_capital: float,
        growth_target_multiple: float = 3.0,
        monthly_target_eur: float = 500.0,
        buffer_months: int = 6,
    ):
        self.initial_capital = initial_capital
        self.growth_target = initial_capital * growth_target_multiple
        self.monthly_target = monthly_target_eur
        self.buffer_months = buffer_months

    def current_phase(self, portfolio_value: float) -> str:
        return self.DISTRIBUTION if portfolio_value >= self.growth_target else self.GROWTH

    def progress_pct(self, portfolio_value: float) -> float:
        if self.growth_target <= self.initial_capital:
            return 100.0
        pct = (
            (portfolio_value - self.initial_capital)
            / (self.growth_target - self.initial_capital)
            * 100
        )
        return max(0.0, min(pct, 100.0))

    def safe_monthly_distribution(self, portfolio_value: float, avg_monthly_return_pct: float = 2.0) -> float:
        """
        Conservative payout: at most the average monthly return,
        always keeping buffer_months × monthly_target untouched.
        """
        reserve = self.buffer_months * self.monthly_target
        if portfolio_value <= reserve:
            return 0.0
        max_from_returns = portfolio_value * (avg_monthly_return_pct / 100)
        return round(min(self.monthly_target, max_from_returns), 2)

    def get_entry_threshold_modifier(self, portfolio_value: float) -> float:
        """
        In DISTRIBUTION phase, require higher confidence → raise threshold by +0.05.
        """
        return 0.05 if self.current_phase(portfolio_value) == self.DISTRIBUTION else 0.0

    def get_info(self, portfolio_value: float, avg_monthly_return_pct: float = 2.0) -> Dict:
        phase = self.current_phase(portfolio_value)
        progress = self.progress_pct(portfolio_value)
        remaining = max(self.growth_target - portfolio_value, 0.0)

        info: Dict = {
            "phase": phase,
            "portfolio_value": round(portfolio_value, 2),
            "initial_capital": self.initial_capital,
            "growth_target": self.growth_target,
            "progress_pct": round(progress, 1),
            "remaining_to_goal": round(remaining, 2),
        }

        if phase == self.DISTRIBUTION:
            info["monthly_distribution"] = self.safe_monthly_distribution(
                portfolio_value, avg_monthly_return_pct
            )
            info["monthly_target"] = self.monthly_target
            info["buffer_reserve"] = round(self.buffer_months * self.monthly_target, 2)

        return info
