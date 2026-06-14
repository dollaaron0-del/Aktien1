from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from logger import get_logger
from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.trade_journal import TradeJournal
from portfolio.signal_queue import SignalQueue
from analyzers.conditional_entry import ConditionalEntry, ConditionalEntryWatcher

log = get_logger(__name__)

# ── Trailing-Stop-Stufen ──────────────────────────────────────────────
# Jede Stufe: (Gewinn-Schwelle %, neuer SL als % vom Einstieg)
_TRAILING_STEPS = [
    (0.08, 0.02),   # +8%  → SL auf +2%
    (0.12, 0.06),   # +12% → SL auf +6%
    (0.18, 0.10),   # +18% → SL auf +10%
    (0.25, 0.15),   # +25% → SL auf +15%
]
_TRAILING_STEPS_BEAR = [
    (0.05, 0.01),
    (0.08, 0.04),
    (0.12, 0.07),
]
_TRAILING_STEPS_CRISIS = [
    (0.04, 0.00),
    (0.06, 0.02),
]

# Konfidenz-basierte Positionsgrößen
_CONFIDENCE_SIZING = {"HIGH": 1.0, "MEDIUM": 0.70, "LOW": 0.45}

# Mindest-Haltedauer in Tagen – verhindert Sofort-Exit von Positionen, deren
# target_hold_days fälschlich 0 ist (gleicher Floor wie der Entry-Pfad, max(3,…)).
_MIN_HOLD_DAYS = 3

# Partial-TP Stufen: (Gewinn%, Anteil Verkauf)
_PARTIAL_TP_STAGES = [
    (0.10, 0.35),   # bei +10%: 35% der Position verkaufen
    (0.18, 0.35),   # bei +18%: weitere 35% verkaufen
]


@dataclass
class StrategyResult:
    action: str            # BUY | SELL | HOLD | SKIP
    ticker: str
    reason: str
    shares: float = 0.0
    price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    hold_days: int = 0


class SwingStrategy:
    """
    Swing-Trading-Strategie mit:
    - Regime-adaptiven SL/TP/Hold-Parametern
    - Trailing-Stop-Loss (3 Stufen)
    - Konfidenz-basierter Positionsgröße
    - Partial Take-Profit (2 Stufen)
    - Dip-Kauf-Logik
    - Soft-Stop-Extension bei hoher Konfidenz
    - EMA21 Conditional-Entry
    - Circuit-Breaker (tägliches Verlust-Limit)
    - Makro-Kalender + Sektor-Rotations-Filter
    - RL-Agent Integration
    """

    def __init__(
        self,
        portfolio: Portfolio,
        broker,
        tracker: PerformanceTracker,
        phase_ctrl,
        focus_ctrl,
        journal: TradeJournal,
        signal_queue: Optional[SignalQueue] = None,
        earnings_filter=None,
        correlation_checker=None,
        kelly_sizer=None,
        goal_risk_assessor=None,
    ):
        self.portfolio = portfolio
        self.broker = broker
        self.tracker = tracker
        self.phase_ctrl = phase_ctrl
        self.focus_ctrl = focus_ctrl
        self.journal = journal
        self.signal_queue = signal_queue
        self.earnings_filter = earnings_filter
        self.correlation_checker = correlation_checker
        self.kelly_sizer = kelly_sizer
        self.goal_risk_assessor = goal_risk_assessor
        self._lock = threading.Lock()
        self._conditional_watcher = ConditionalEntryWatcher()
        self._daily_loss_usd: float = 0.0
        self._daily_loss_date: str = ""

    # ── Public entry points ───────────────────────────────────────────────

    def evaluate(
        self,
        ticker: str,
        analysis,  # AnalysisResult
        current_price: float,
        regime: str = "NEUTRAL",
        market_is_ranging: bool = False,
        rl_agent=None,
    ) -> StrategyResult:
        """Main evaluation: returns BUY/SELL/HOLD/SKIP decision."""
        with self._lock:
            return self._evaluate_inner(ticker, analysis, current_price, regime, market_is_ranging, rl_agent)

    def check_exits(
        self,
        prices: Dict[str, float],
        regime: str = "NEUTRAL",
    ) -> List[StrategyResult]:
        """Check all open positions for exit conditions."""
        results = []
        with self._lock:
            for ticker, pos in list(self.portfolio.all_positions().items()):
                price = prices.get(ticker)
                if price is None:
                    continue
                result = self._check_exit(ticker, pos, price, regime)
                if result:
                    results.append(result)
        return results

    def check_conditional_entries(
        self,
        prices: Dict[str, float],
    ) -> List[ConditionalEntry]:
        """Returns triggered conditional entries."""
        return self._conditional_watcher.check_triggered(prices)

    def build_open_position_context(self, ticker: str) -> Optional[dict]:
        """Returns a context dict for an open position (for Claude thesis-check), or None."""
        pos = self.portfolio.all_positions().get(ticker)
        if pos is None:
            return None
        try:
            prices = self.broker.get_prices([ticker])
            current_price = prices.get(ticker) or pos.entry_price
            gain_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        except Exception:
            current_price = pos.entry_price
            gain_pct = 0.0
        return {
            "ticker":        ticker,
            "entry_price":   pos.entry_price,
            "current_price": round(current_price, 4),
            "gain_pct":      round(gain_pct, 2),
            "entry_date":    pos.entry_date,
            "stop_loss":     pos.stop_loss,
            "take_profit":   pos.take_profit,
            "rationale":     getattr(pos, "rationale", ""),
        }

    # ── Core evaluation logic ───────────────────────────────────────────────

    def _evaluate_inner(
        self,
        ticker: str,
        analysis,
        current_price: float,
        regime: str,
        market_is_ranging: bool,
        rl_agent,
    ) -> StrategyResult:
        from config import config
        from analyzers.regime_adaptive import get_adaptive_params

        params = get_adaptive_params(regime, market_is_ranging)

        # 1. Circuit-breaker
        if self._circuit_breaker_active(config):
            return StrategyResult("SKIP", ticker, "Circuit-Breaker aktiv (Tagesverlust-Limit)")

        # 2. Existing position → check for add/scale-in or thesis recheck
        pos = self.portfolio.get_position(ticker)
        if pos is not None:
            return self._evaluate_existing(ticker, pos, analysis, current_price, params, regime)

        # 3. New position evaluation
        return self._evaluate_new(ticker, analysis, current_price, params, regime, market_is_ranging, rl_agent, config)

    def _evaluate_existing(
        self, ticker, pos, analysis, current_price, params, regime
    ) -> StrategyResult:
        """Handle existing position: thesis recheck, scale-in, or hold."""
        from config import config

        # Thesis broken?
        if hasattr(analysis, "thesis_valid") and analysis.thesis_valid is False:
            reason = getattr(analysis, "thesis_break_reason", "These gebrochen")
            return StrategyResult("SELL", ticker, f"These gebrochen: {reason}", price=current_price)

        # Scale-in: sentiment sehr hoch + position profitable
        gain_pct = (current_price - pos.entry_price) / pos.entry_price
        sentiment = getattr(analysis, "sentiment_score", 0.0)
        confidence = getattr(analysis, "confidence", "MEDIUM")

        if (
            sentiment >= 0.85
            and confidence == "HIGH"
            and gain_pct >= 0.05
            and regime not in ("BEAR", "CRISIS")
        ):
            # Scale in: add up to 50% of original position size
            scale_shares = round(pos.shares * 0.50, 4)
            cost = scale_shares * current_price
            if cost <= self.portfolio.cash * 0.15:  # max 15% cash for scale-in
                return StrategyResult(
                    "BUY", ticker,
                    f"Scale-in: Sentiment={sentiment:.2f}, Gewinn={gain_pct*100:.1f}%",
                    shares=scale_shares, price=current_price,
                )

        return StrategyResult("HOLD", ticker, "Position läuft, keine Änderung")

    def _evaluate_new(
        self, ticker, analysis, current_price, params, regime,
        market_is_ranging, rl_agent, config
    ) -> StrategyResult:
        """Evaluate potential new position."""
        sentiment = getattr(analysis, "sentiment_score", 0.0)
        confidence = getattr(analysis, "confidence", "MEDIUM")
        direction  = getattr(analysis, "direction", "NEUTRAL")
        recommendation = getattr(analysis, "recommendation", "SKIP")

        # Buy threshold (adjusted by regime)
        threshold = config.buy_threshold + params.buy_threshold_adj

        # Makro-Gegenwind erhöht die Kaufschwelle (asymmetrisch: nur strenger,
        # nie lockerer – Rückenwind soll keine schwachen Signale durchwinken).
        try:
            from analyzers.macro_context import get_macro_context
            macro_bias = get_macro_context().bias_score()
            if macro_bias <= -0.6:
                threshold += 0.08
            elif macro_bias <= -0.3:
                threshold += 0.05
        except Exception:
            pass

        # Direction must be bullish
        if direction not in ("BULLISH",) or recommendation not in ("BUY",):
            # Check conditional entry
            trigger = getattr(analysis, "entry_trigger_price", None)
            if trigger and trigger > current_price * 0.98:
                # build() erfasst Analyse-Kontext (Bull/Bear, Kursziel) und
                # erzwingt den 3-Tage-Halte-Floor (sonst 0-Tage-Exit beim Trigger).
                self._conditional_watcher.add(
                    ConditionalEntryWatcher.build(ticker, trigger, current_price, analysis)
                )
            return StrategyResult("SKIP", ticker, f"Kein Kaufsignal: {recommendation}/{direction}")

        if sentiment < threshold:
            return StrategyResult("SKIP", ticker, f"Sentiment {sentiment:.2f} < Schwelle {threshold:.2f}")

        # Max positions (dynamisch nach Portfolio-Größe via FocusController –
        # config.max_positions existiert nicht; früher self.focus.get_max_positions).
        n_pos = len(self.portfolio.all_positions())
        try:
            max_pos = self.focus_ctrl.get_max_positions(self.portfolio.total_value({}))
        except Exception:
            max_pos = 12
        if n_pos >= max_pos:
            # Queue the signal (vormerken bis ein Slot frei wird)
            if self.signal_queue:
                self.signal_queue.enqueue(
                    ticker=ticker,
                    sentiment_score=sentiment,
                    confidence=confidence,
                    target_price=getattr(analysis, "target_price", None),
                    direction=direction,
                    entry_rationale=getattr(analysis, "entry_rationale", "") or "",
                    key_catalysts=list(getattr(analysis, "key_catalysts", []) or []),
                    risk_factors=list(getattr(analysis, "risk_factors", []) or []),
                    sources_used=int(getattr(analysis, "sources_used", 0) or 0),
                    sources_breakdown=getattr(analysis, "sources_breakdown", {}) or {},
                    suggested_hold_days=int(getattr(analysis, "suggested_hold_days", 0) or 0),
                )
            return StrategyResult("SKIP", ticker, f"Max Positionen ({max_pos}) erreicht – Signal in Queue")

        # Earnings filter
        if self.earnings_filter and self.earnings_filter.is_blocked(ticker):
            return StrategyResult("SKIP", ticker, "Earnings-Sperre aktiv")

        # Correlation check
        if self.correlation_checker:
            positions = self.portfolio.all_positions()
            if self.correlation_checker.is_correlated(ticker, list(positions.keys())):
                return StrategyResult("SKIP", ticker, "Zu hohe Sektor-Korrelation")

        # Position sizing
        position_value = self._calc_position_size(analysis, current_price, params, config)
        if position_value <= 0:
            return StrategyResult("SKIP", ticker, "Positionsgröße = 0")

        shares = position_value / current_price

        # SL/TP
        sl_pct = params.sl_pct
        tp_pct = params.tp_pct

        # Hold days
        # config.hold_days existiert nicht – Literal-Default (Analyse liefert i.d.R.
        # suggested_hold_days; greift nur als Fallback). Floor unten via max(3,…).
        suggested = getattr(analysis, "suggested_hold_days", 14) or 14
        hold_days = max(3, int(suggested * params.hold_days_mult))

        stop_loss   = round(current_price * (1 - sl_pct), 4)
        take_profit = round(current_price * (1 + tp_pct), 4)

        # RL agent veto
        if rl_agent is not None:
            try:
                rl_decision = rl_agent.decide(ticker, sentiment, current_price)
                if rl_decision == "SKIP":
                    return StrategyResult("SKIP", ticker, "RL-Agent: SKIP")
            except Exception:
                pass

        rationale = getattr(analysis, "entry_rationale", "") or getattr(analysis, "recommendation", "")
        catalysts  = getattr(analysis, "key_catalysts", []) or []

        return StrategyResult(
            action="BUY",
            ticker=ticker,
            reason=rationale,
            shares=round(shares, 4),
            price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            hold_days=hold_days,
        )

    # ── Exit logic ─────────────────────────────────────────────────────────────

    def _check_exit(self, ticker: str, pos: Position, price: float, regime: str) -> Optional[StrategyResult]:
        """Check one position for exit conditions."""
        from analyzers.regime_adaptive import get_adaptive_params
        params = get_adaptive_params(regime)

        gain_pct = (price - pos.entry_price) / pos.entry_price

        # 1. Hard stop-loss
        if price <= pos.stop_loss:
            return StrategyResult("SELL", ticker, f"Stop-Loss ausgelöst @ ${price:.2f}",
                                  price=price, shares=pos.shares)

        # 2. Take-profit
        if price >= pos.take_profit:
            return StrategyResult("SELL", ticker, f"Take-Profit erreicht @ ${price:.2f}",
                                  price=price, shares=pos.shares)

        # 3. Partial take-profit
        partial_result = self._check_partial_tp(ticker, pos, price, gain_pct, regime)
        if partial_result:
            return partial_result

        # 4. Trailing stop update
        self._update_trailing_stop(ticker, pos, price, gain_pct, regime)

        # 5. Hold-time expiry
        entry_dt = datetime.fromisoformat(pos.entry_date) if isinstance(pos.entry_date, str) else pos.entry_date
        days_held = (datetime.utcnow() - entry_dt).days
        # Mindest-Haltedauer erzwingen: Positionen mit target_hold_days=0 (z.B. aus
        # Conditional Entries mit ungesetztem suggested_hold_days) würden sonst beim
        # nächsten Check sofort wieder verkauft – purer Slippage-Verlust (vgl. CRM/CAT).
        effective_hold = max(int(pos.target_hold_days or 0), _MIN_HOLD_DAYS)
        if days_held >= effective_hold:
            # Only sell if not profitable enough to extend
            if gain_pct < 0.05:
                return StrategyResult("SELL", ticker,
                                      f"Haltedauer abgelaufen ({days_held}d)",
                                      price=price, shares=pos.shares)

        return None

    def _check_partial_tp(self, ticker, pos, price, gain_pct, regime) -> Optional[StrategyResult]:
        """2-stage partial take-profit."""
        if regime in ("BEAR", "CRISIS"):
            return None  # Kein Partial-TP in Bären/Krisen (zu volatil)

        stage = pos.partial_tp_count
        if stage >= len(_PARTIAL_TP_STAGES):
            return None

        threshold, sell_fraction = _PARTIAL_TP_STAGES[stage]
        if gain_pct >= threshold:
            sell_shares = round(pos.shares * sell_fraction, 4)
            pnl = sell_shares * (price - pos.entry_price)
            new_shares = round(pos.shares - sell_shares, 4)
            # Raise stop-loss to break-even after first partial TP
            new_sl = max(pos.stop_loss, pos.entry_price * 1.01) if stage == 0 else pos.stop_loss

            self.portfolio.update_partial_tp(
                ticker=ticker,
                new_shares=new_shares,
                new_stop_loss=new_sl,
                sell_shares=sell_shares,
                sell_price=price,
                pnl=pnl,
                new_count=stage + 1,
            )
            return StrategyResult(
                "SELL", ticker,
                f"Partial-TP Stufe {stage+1}: {sell_fraction*100:.0f}% @ +{gain_pct*100:.1f}%",
                price=price, shares=sell_shares,
            )
        return None

    def _update_trailing_stop(self, ticker, pos, price, gain_pct, regime):
        """Ratchet trailing stop-loss upward."""
        steps = (
            _TRAILING_STEPS_CRISIS if regime == "CRISIS" else
            _TRAILING_STEPS_BEAR   if regime == "BEAR"   else
            _TRAILING_STEPS
        )
        for gain_threshold, new_sl_pct in reversed(steps):
            if gain_pct >= gain_threshold:
                new_sl = pos.entry_price * (1 + new_sl_pct)
                if new_sl > pos.stop_loss:
                    self.portfolio.update_position_state(ticker, stop_loss=round(new_sl, 4))
                break

    # ── Position sizing ─────────────────────────────────────────────────────

    def _calc_position_size(
        self, analysis, current_price: float, params, config
    ) -> float:
        """Calculate dollar position size."""
        # Basis ist ein Anteil der GESAMT-EQUITY (Cash + Positionswert), nicht
        # nur des Rest-Cash. Sonst schrumpfen spätere Käufe im selben Zyklus,
        # weil das Cash mit jedem Buy sinkt. total_value({}) bewertet bestehende
        # Positionen mangels Live-Preisen mit dem Einstandspreis – stabile,
        # ausreichende Schätzung für das Sizing. Die finale Liquiditätsgrenze
        # (cash * 0.40 weiter unten) verhindert weiterhin ein Überziehen.
        equity = self.portfolio.total_value({})
        base = equity * config.max_position_pct

        # Regime multiplier
        base *= params.position_size_mult

        # Confidence multiplier
        confidence = getattr(analysis, "confidence", "MEDIUM")
        base *= _CONFIDENCE_SIZING.get(confidence, 0.70)

        # Kelly sizing (optional)
        if self.kelly_sizer:
            try:
                kelly_mult = self.kelly_sizer.fraction
                base = min(base, self.portfolio.cash * kelly_mult)
            except Exception:
                pass

        # Goal risk
        if self.goal_risk_assessor:
            try:
                risk_mult = self.goal_risk_assessor.position_size_multiplier()
                base *= risk_mult
            except Exception:
                pass

        # Makro-Modifier (VIX × Makro-Kalender × Sektor-Rotation).
        # Regime ist bereits über params.position_size_mult abgedeckt – hier
        # kommen die übrigen, bislang ungenutzten Makro-Faktoren hinzu.
        try:
            from analyzers.macro_context import get_macro_context
            ticker = getattr(analysis, "ticker", "") or ""
            base *= get_macro_context().size_modifier(ticker)
        except Exception:
            pass

        # Min/max guardrails
        base = max(base, 10.0)
        base = min(base, self.portfolio.cash * 0.40)  # never more than 40% in one trade

        return round(base, 2)

    # ── Circuit breaker ─────────────────────────────────────────────────────

    def _circuit_breaker_active(self, config) -> bool:
        """Returns True if daily loss limit exceeded."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._daily_loss_date != today:
            self._daily_loss_usd = 0.0
            self._daily_loss_date = today
        limit = getattr(config, "circuit_breaker_loss_pct", 0.05) * self.portfolio.cash
        return self._daily_loss_usd >= limit

    def record_loss(self, pnl: float):
        """Record a realized loss for circuit-breaker tracking."""
        if pnl < 0:
            today = datetime.utcnow().strftime("%Y-%m-%d")
            if self._daily_loss_date != today:
                self._daily_loss_usd = 0.0
                self._daily_loss_date = today
            self._daily_loss_usd += abs(pnl)
