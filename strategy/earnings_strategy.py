"""
EarningsStrategy — two distinct trades around quarterly reports.

Mode 1 – PRE_EARNINGS (5–10 days before report):
  Empirical edge: stocks historically drift +3–7% in the week before earnings
  when the market expects a beat.  We enter early, capture the drift, and exit
  the day before the announcement to avoid the binary event risk.

  Entry conditions:
    • Earnings in 5–10 calendar days (not blocked by standard filter, not too early)
    • EarningsPredictor → direction=BEAT, confidence≥MEDIUM
    • Ticker not already in portfolio
    • Sufficient free cash

  Parameters: position_pct=40% of normal, SL=−5%, TP=+8%
  Forced exit: 1 calendar day before earnings date

Mode 2 – POST_EARNINGS (0–2 days after BEAT):
  Post-earnings drift: stocks that gap up on a strong beat continue drifting
  higher for 5–15 trading days as institutional money flows in.

  Entry conditions:
    • EarningsSurprise label = BEAT or STRONG_BEAT, happened 0–2 days ago
    • Gap since report < 15% (avoid chasing extreme gaps)
    • Ticker not already in portfolio
    • Sufficient free cash

  Parameters: position_pct=80% of normal, SL=−4%, TP=+15%
  Hold: until TP/SL or 15 trading days
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional, TYPE_CHECKING

from portfolio.portfolio import Portfolio, Position
from analyzers.earnings_filter import EarningsFilter
from analyzers.earnings_predictor import EarningsPredictor
from analyzers.earnings_surprise import EarningsSurprise
from config import config
from logger import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# ── Parameters ─────────────────────────────────────────────────────────────
_PRE_WIN_MIN = 5    # calendar days before earnings: earliest entry
_PRE_WIN_MAX = 10   # calendar days before earnings: latest entry
_PRE_POS_PCT  = 0.40  # fraction of normal max_position_pct
_PRE_SL_PCT   = 0.05
_PRE_TP_PCT   = 0.08
_PRE_HOLD     = 12

_POST_WIN_MAX  = 2    # days after earnings to still enter
_POST_POS_PCT  = 0.80
_POST_SL_PCT   = 0.04
_POST_TP_PCT   = 0.15
_POST_MAX_GAP  = 0.15  # skip if price already gapped >15% since report
_POST_HOLD     = 15

_RATIONALE_PRE  = "EARNINGS_PRE:"   # prefix to identify pre-earnings positions
_RATIONALE_POST = "EARNINGS_POST:"  # prefix to identify post-earnings positions
_MIN_INVEST     = 50.0


class EarningsStrategy:
    """
    Active earnings trading strategy.  Designed to run alongside SwingStrategy
    in the same analysis cycle — it never duplicates positions SwingStrategy
    already holds.

    Usage in runner.py::
        result = earnings_strategy.evaluate(ticker, current_price, portfolio_value)
        exits  = earnings_strategy.check_pre_earnings_exits()
    """

    def __init__(self, portfolio: Portfolio, broker) -> None:
        self.portfolio  = portfolio
        self.broker     = broker
        self._ef        = EarningsFilter(block_days=4, warn_days=_PRE_WIN_MAX)
        self._predictor = EarningsPredictor()
        self._surprise  = EarningsSurprise()

    # ── Public API ──────────────────────────────────────────────────────────

    def evaluate(self, ticker: str, current_price: float, portfolio_value: float) -> Optional[str]:
        """
        Check both pre- and post-earnings opportunities for *ticker*.
        Returns an action string (like SwingStrategy.evaluate) or None.
        """
        if self.portfolio.get_position(ticker):
            return None  # already holding this ticker

        if current_price <= 0 or portfolio_value <= 0:
            return None

        post = self._evaluate_post(ticker, current_price, portfolio_value)
        if post:
            return post

        pre = self._evaluate_pre(ticker, current_price, portfolio_value)
        return pre

    def check_pre_earnings_exits(self) -> List[str]:
        """
        Force-close all pre-earnings positions where earnings are ≤1 day away.
        Call this at the start of each cycle (before new evaluations).
        Returns list of action strings for logging.
        """
        actions: List[str] = []
        for ticker, pos in list(self.portfolio.all_positions().items()):
            if not pos.rationale.startswith(_RATIONALE_PRE):
                continue
            ed = self._ef.next_earnings(ticker)
            if ed is None:
                continue
            days_left = (ed.date() - datetime.now(timezone.utc).replace(tzinfo=None).date()).days
            if days_left <= 1:
                price = self.broker.get_price(ticker) or pos.entry_price
                pnl = self.portfolio.close_position(ticker, price, reason="EARNINGS_EXIT")
                pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                actions.append(
                    f"[EARNINGS_PRE][{ticker}] 🗓 Earnings morgen – Position geschlossen "
                    f"@ ${price:.2f} ({pnl_str})"
                )
                log.info("[%s] Pre-Earnings Exit: %d Tage bis Report", ticker, days_left)
        return actions

    # ── Pre-Earnings ────────────────────────────────────────────────────────

    def _evaluate_pre(
        self, ticker: str, current_price: float, portfolio_value: float
    ) -> Optional[str]:
        # 1. Days until earnings
        ec = self._ef.check(ticker)
        days = ec.get("days_until")
        if days is None or not (_PRE_WIN_MIN <= days <= _PRE_WIN_MAX):
            return None

        # 2. Earnings beat prediction
        pred = self._predictor.predict(ticker)
        if pred.direction != "BEAT" or pred.confidence == "LOW":
            return None

        # 3. Position sizing (conservative: 40% of normal)
        max_pct   = config.max_position_pct * _PRE_POS_PCT
        invest    = min(portfolio_value * max_pct, self.portfolio.cash * 0.90)
        if invest < _MIN_INVEST:
            return f"[EARNINGS_PRE][{ticker}] Nicht genug Kapital (${self.portfolio.cash:.0f})"

        shares = math.floor(invest / current_price * 100) / 100
        if shares <= 0:
            return None

        sl = round(current_price * (1 - _PRE_SL_PCT), 2)
        tp = round(current_price * (1 + _PRE_TP_PCT), 2)
        earnings_date = ec["date"] or "?"

        fill = self.broker.buy(ticker, shares, current_price)
        actual_price = fill.get("avg_price", current_price) if fill else current_price

        pos = Position(
            ticker=ticker,
            shares=shares,
            entry_price=actual_price,
            entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            stop_loss=sl,
            take_profit=tp,
            target_hold_days=_PRE_HOLD,
            rationale=f"{_RATIONALE_PRE} {days}d, Beat-Score {pred.score:.2f}, Exit vor {earnings_date}",
            entry_catalysts=[f"Earnings in {days}d ({earnings_date})", f"Predictor: BEAT ({pred.confidence})"],
        )
        self.portfolio.open_position(pos)

        return (
            f"[EARNINGS_PRE][{ticker}] 📈 GEKAUFT {shares:.2f} Stk @ ${actual_price:.2f} "
            f"| Earnings in {days}T ({earnings_date}) | Beat-Score {pred.score:.2f} ({pred.confidence}) "
            f"| SL ${sl:.2f} | TP ${tp:.2f} | Auto-Exit 1T vor Report"
        )

    # ── Post-Earnings ───────────────────────────────────────────────────────

    def _evaluate_post(
        self, ticker: str, current_price: float, portfolio_value: float
    ) -> Optional[str]:
        # 1. Earnings surprise check
        surprise = self._surprise.check(ticker)
        label = surprise.get("label", "UNKNOWN")
        if label not in ("BEAT", "STRONG_BEAT"):
            return None

        # 2. Timing: happened 0–2 days ago
        days_since = surprise.get("days_since_earnings")
        if days_since is None or days_since > _POST_WIN_MAX:
            return None

        # 3. Don't chase extreme gaps
        gap = surprise.get("price_change_since_report", 0.0) or 0.0
        if gap > _POST_MAX_GAP:
            log.debug("[%s] Post-Earnings: gap %.1f%% zu groß – kein Entry", ticker, gap * 100)
            return None

        # 4. Position sizing (80% of normal)
        max_pct = config.max_position_pct * _POST_POS_PCT
        invest  = min(portfolio_value * max_pct, self.portfolio.cash * 0.90)
        if invest < _MIN_INVEST:
            return None

        shares = math.floor(invest / current_price * 100) / 100
        if shares <= 0:
            return None

        sl  = round(current_price * (1 - _POST_SL_PCT), 2)
        tp  = round(current_price * (1 + _POST_TP_PCT), 2)
        surp_pct = surprise.get("surprise_pct", 0.0) or 0.0

        fill = self.broker.buy(ticker, shares, current_price)
        actual_price = fill.get("avg_price", current_price) if fill else current_price

        pos = Position(
            ticker=ticker,
            shares=shares,
            entry_price=actual_price,
            entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            stop_loss=sl,
            take_profit=tp,
            target_hold_days=_POST_HOLD,
            rationale=f"{_RATIONALE_POST} {label}, Surprise {surp_pct:+.1%}, vor {days_since}T",
            entry_catalysts=[f"Earnings {label}: {surp_pct:+.1%}", f"Post-Earnings-Drift ({days_since}T nach Report)"],
        )
        self.portfolio.open_position(pos)

        return (
            f"[EARNINGS_POST][{ticker}] 🚀 GEKAUFT {shares:.2f} Stk @ ${actual_price:.2f} "
            f"| {label}: {surp_pct:+.1%} Überraschung vor {days_since}T "
            f"| SL ${sl:.2f} | TP ${tp:.2f}"
        )
