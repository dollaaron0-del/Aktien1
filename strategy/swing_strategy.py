import math
from datetime import datetime
from typing import Optional, List, Dict
from analyzers.claude_analyzer import AnalysisResult
from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from portfolio.focus_mode import FocusController
from portfolio.trade_journal import TradeJournal
from broker.paper_broker import PaperBroker
from notifier.telegram_notifier import TelegramNotifier
from analyzers.earnings_filter import EarningsFilter
from analyzers.correlation_check import CorrelationChecker
from analyzers.kelly_sizing import KellySizer
from config import config


class SwingStrategy:
    def __init__(
        self,
        portfolio: Portfolio,
        broker: PaperBroker,
        tracker: PerformanceTracker,
        phase_ctrl: PhaseController,
        focus_ctrl: FocusController,
        journal: TradeJournal,
        earnings_filter: Optional[EarningsFilter] = None,
        correlation_checker: Optional[CorrelationChecker] = None,
        kelly_sizer: Optional[KellySizer] = None,
    ):
        self.portfolio = portfolio
        self.broker = broker
        self.tracker = tracker
        self.phase_ctrl = phase_ctrl
        self.focus = focus_ctrl
        self.journal = journal
        self._notifier = TelegramNotifier()
        self.earnings_filter = earnings_filter
        self.correlation = correlation_checker
        self.kelly = kelly_sizer

    def evaluate(self, analysis: AnalysisResult, sources_breakdown: Optional[Dict[str, int]] = None) -> Optional[str]:
        ticker = analysis.ticker
        existing_position = self.portfolio.get_position(ticker)
        current_price = self.broker.get_price(ticker)

        if current_price is None:
            return f"[{ticker}] Kein Kurs verfügbar – übersprungen."

        if existing_position:
            return self._check_exit(existing_position, current_price, analysis)

        if analysis.recommendation != "BUY":
            return None
        if analysis.confidence == "LOW":
            return f"[{ticker}] BUY-Signal, aber Konfidenz zu niedrig – übersprungen."
        if analysis.sources_used < config.min_sources:
            return f"[{ticker}] Zu wenige Quellen ({analysis.sources_used}) – übersprungen."

        portfolio_value = self.portfolio.total_value(
            self.broker.get_prices(list(self.portfolio.all_positions().keys()) + [ticker])
        )
        # Effective buy threshold: combines adaptive learning + phase + focus mode
        adaptive_threshold = self.tracker.get_adaptive_threshold(config.buy_threshold)
        phase_modifier = self.phase_ctrl.get_entry_threshold_modifier(portfolio_value)
        effective_threshold = self.focus.get_effective_threshold(
            adaptive_threshold + phase_modifier, portfolio_value
        )

        if analysis.sentiment_score < effective_threshold:
            return (
                f"[{ticker}] Sentiment {analysis.sentiment_score:.2f} unter Schwelle "
                f"{effective_threshold:.2f} ({self.focus.profile.label}) – übersprungen."
            )

        # Earnings filter: skip buy if earnings imminent
        if self.earnings_filter:
            ec = self.earnings_filter.check(ticker)
            if ec["block"]:
                return (
                    f"[{ticker}] Earnings in {ec['days_until']}d ({ec['date']}) – "
                    f"Kauf übersprungen (Volatilitäts-Risiko)."
                )

        # Sector correlation: prevent over-concentration
        if self.correlation:
            existing_values = {
                t: p.shares * (self.broker.get_price(t) or p.entry_price)
                for t, p in self.portfolio.all_positions().items()
            }
            tentative_invest = portfolio_value * self.focus.get_max_position_pct(portfolio_value)
            sec_check = self.correlation.can_open(
                ticker, tentative_invest, portfolio_value, existing_values
            )
            if not sec_check["allowed"]:
                return f"[{ticker}] {sec_check['reason']} – übersprungen."

        return self._open_position(ticker, current_price, analysis, portfolio_value, sources_breakdown or {})

    def check_open_positions(self) -> List[str]:
        actions = []
        positions = self.portfolio.all_positions()
        if not positions:
            return actions

        prices = self.broker.get_prices(list(positions.keys()))
        for ticker, pos in positions.items():
            price = prices.get(ticker)
            if price is None:
                continue

            days_held = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
            reason = None

            if price <= pos.stop_loss:
                reason = f"Stop-Loss ausgelöst bei ${price:.2f}"
            elif price >= pos.take_profit:
                reason = f"Take-Profit erreicht bei ${price:.2f}"
            elif days_held >= pos.target_hold_days:
                reason = f"Max. Haltedauer ({pos.target_hold_days}d) erreicht"

            if reason:
                pnl = self._do_close(ticker, pos, price, reason, days_held)
                pnl_approx = (price - pos.entry_price) * pos.shares
                actions.append(
                    f"[{ticker}] VERKAUFT – {reason} | P&L: {'+' if pnl_approx >= 0 else ''}{pnl_approx:.2f} USD"
                )

        return actions

    def _check_exit(self, pos: Position, price: float, analysis: AnalysisResult) -> Optional[str]:
        ticker = pos.ticker
        days_held = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
        reason = None

        # 1. These gebrochen – höchste Priorität
        if analysis.thesis_valid is False and analysis.confidence != "LOW":
            reason = f"These gebrochen: {analysis.thesis_break_reason or 'Ursprüngliche Kaufkatalysatoren nicht mehr gültig'}"

        # 2. Technische Exit-Bedingungen
        elif price <= pos.stop_loss:
            reason = f"Stop-Loss bei ${price:.2f}"
        elif price >= pos.take_profit:
            reason = f"Take-Profit bei ${price:.2f}"
        elif days_held >= pos.target_hold_days:
            reason = f"Haltedauer abgelaufen ({days_held}d)"
        elif analysis.recommendation == "SELL" and analysis.confidence != "LOW":
            reason = f"Sentiment-Signal SELL (Score: {analysis.sentiment_score:.2f})"

        if reason:
            thesis_broken = "gebrochen" in reason.lower()
            pnl = self._do_close(ticker, pos, price, reason, days_held)
            sign = "+" if pnl >= 0 else ""
            thesis_tag = " ⚠️ THESE GEBROCHEN" if thesis_broken else ""
            self._notifier.notify_sell(
                ticker=ticker, shares=pos.shares, price=price,
                entry_price=pos.entry_price, pnl=pnl,
                reason=reason, thesis_broken=thesis_broken,
            )
            return f"[{ticker}] VERKAUFT{thesis_tag} – {reason} | P&L: {sign}{pnl:.2f} USD"

        # Log daily check (no exit triggered)
        self.journal.log_daily_check(
            ticker=ticker, price=price,
            sentiment=analysis.sentiment_score,
            confidence=analysis.confidence,
            thesis_valid=analysis.thesis_valid,
            rationale=analysis.entry_rationale or analysis.raw_summary,
            recommendation=analysis.recommendation,
        )
        if analysis.thesis_valid is False:
            # Thesis weakened but confidence low → warning only
            self.journal.log_warning(
                ticker=ticker, price=price,
                reason=analysis.thesis_break_reason or "These angeschlagen",
                confidence=analysis.confidence,
            )

        thesis_note = ""
        if analysis.thesis_valid is True:
            thesis_note = " | These: ✓ gültig"
        elif analysis.thesis_valid is False:
            thesis_note = " | These: ✗ gebrochen (Konfidenz zu niedrig für Sofortausstieg)"

        return f"[{ticker}] Position gehalten ({days_held}d) | Kurs: ${price:.2f}{thesis_note}"

    def _open_position(
        self,
        ticker: str,
        price: float,
        analysis: AnalysisResult,
        portfolio_value: float,
        sources_breakdown: Dict[str, int],
    ) -> str:
        # Apply focus-mode position sizing, optionally overridden by Kelly criterion
        max_pos_pct = self.focus.get_max_position_pct(portfolio_value)
        if self.kelly:
            kelly_pct = self.kelly.compute(fallback_pct=max_pos_pct)
            max_pos_pct = min(max_pos_pct, kelly_pct)
        max_invest = portfolio_value * max_pos_pct
        invest = min(max_invest, self.portfolio.cash * 0.95)

        if invest < 50:
            return f"[{ticker}] Nicht genug freies Kapital für neuen Trade."

        shares = math.floor(invest / price * 100) / 100

        # Apply focus-mode SL/TP percentages
        sl_pct = self.focus.get_stop_loss_pct()
        tp_pct = self.focus.get_take_profit_pct()
        stop_loss = round(price * (1 - sl_pct), 2)
        fixed_tp = round(price * (1 + tp_pct), 2)
        if analysis.target_price and analysis.target_price > price:
            take_profit = min(analysis.target_price, fixed_tp)
            tp_source = f"Claude ${analysis.target_price:.2f} → TP ${take_profit:.2f}"
        else:
            take_profit = fixed_tp
            tp_source = f"Fix {tp_pct*100:.0f}% → TP ${take_profit:.2f}"

        # Cap hold days by focus mode preference
        capped_hold = self.focus.cap_hold_days(analysis.suggested_hold_days)

        position = Position(
            ticker=ticker,
            shares=shares,
            entry_price=price,
            entry_date=datetime.utcnow().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=capped_hold,
            rationale=analysis.entry_rationale,
            entry_catalysts=analysis.key_catalysts[:5],
        )

        self.broker.buy(ticker, shares, price)
        self.portfolio.open_position(position)

        self._notifier.notify_buy(
            ticker=ticker, shares=shares, price=price,
            stop_loss=stop_loss, take_profit=take_profit,
            hold_days=capped_hold,
            rationale=analysis.entry_rationale,
            sentiment_score=analysis.sentiment_score,
        )

        self.tracker.record_prediction(
            ticker=ticker,
            entry_price=price,
            predicted_target_price=analysis.target_price,
            predicted_hold_days=capped_hold,
            predicted_direction=analysis.direction,
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            sources_used=analysis.sources_used,
            sources_breakdown=sources_breakdown,
        )

        # Log full entry context to trade journal
        self.journal.log_entry(
            ticker=ticker,
            price=price,
            sentiment=analysis.sentiment_score,
            confidence=analysis.confidence,
            direction=analysis.direction,
            rationale=analysis.entry_rationale,
            catalysts=analysis.key_catalysts[:5],
            risks=analysis.risk_factors[:5],
            sources=sources_breakdown,
            target_price=analysis.target_price,
            hold_days=capped_hold,
        )

        return (
            f"[{ticker}] GEKAUFT – {shares} Stück @ ${price:.2f} "
            f"| SL: ${stop_loss} | {tp_source} "
            f"| Haltedauer: {capped_hold}d "
            f"| Investiert: ${shares * price:.2f}"
        )

    def _do_close(self, ticker: str, pos: Position, price: float, reason: str, days_held: int = 0) -> float:
        self.tracker.record_outcome(
            ticker=ticker,
            entry_price=pos.entry_price,
            entry_date=pos.entry_date,
            sell_price=price,
            sell_reason=reason,
        )
        pnl = self.portfolio.close_position(ticker, price, reason)
        # Log exit to journal
        self.journal.log_exit(
            ticker=ticker, price=price,
            entry_price=pos.entry_price,
            pnl=pnl, reason=reason,
            days_held=days_held,
        )
        return pnl

    def build_open_position_context(self, ticker: str) -> Optional[Dict]:
        """Returns position context dict for Claude's thesis-check prompt."""
        pos = self.portfolio.get_position(ticker)
        if not pos:
            return None
        return {
            "entry_price": pos.entry_price,
            "entry_date": pos.entry_date,
            "hold_days": pos.target_hold_days,
            "thesis": pos.rationale,
            "catalysts": pos.entry_catalysts,
        }
