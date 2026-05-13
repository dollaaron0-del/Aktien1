import math
from datetime import datetime
from typing import Optional, List, Dict
from analyzers.claude_analyzer import AnalysisResult
from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from broker.paper_broker import PaperBroker
from notifier.telegram_notifier import TelegramNotifier
from config import config


class SwingStrategy:
    def __init__(
        self,
        portfolio: Portfolio,
        broker: PaperBroker,
        tracker: PerformanceTracker,
        phase_ctrl: PhaseController,
    ):
        self.portfolio = portfolio
        self.broker = broker
        self.tracker = tracker
        self.phase_ctrl = phase_ctrl
        self._notifier = TelegramNotifier()

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
        adaptive_threshold = self.tracker.get_adaptive_threshold(config.buy_threshold)
        phase_modifier = self.phase_ctrl.get_entry_threshold_modifier(portfolio_value)
        effective_threshold = adaptive_threshold + phase_modifier

        if analysis.sentiment_score < effective_threshold:
            return (
                f"[{ticker}] Sentiment {analysis.sentiment_score:.2f} unter Schwelle "
                f"{effective_threshold:.2f} – übersprungen."
            )

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
                self._do_close(ticker, pos, price, reason)
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
            pnl = self._do_close(ticker, pos, price, reason)
            sign = "+" if pnl >= 0 else ""
            thesis_tag = " ⚠️ THESE GEBROCHEN" if thesis_broken else ""
            self._notifier.notify_sell(
                ticker=ticker, shares=pos.shares, price=price,
                entry_price=pos.entry_price, pnl=pnl,
                reason=reason, thesis_broken=thesis_broken,
            )
            return f"[{ticker}] VERKAUFT{thesis_tag} – {reason} | P&L: {sign}{pnl:.2f} USD"

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
        max_invest = portfolio_value * config.max_position_pct
        invest = min(max_invest, self.portfolio.cash * 0.95)

        if invest < 50:
            return f"[{ticker}] Nicht genug freies Kapital für neuen Trade."

        shares = math.floor(invest / price * 100) / 100

        stop_loss = round(price * (1 - config.stop_loss_pct), 2)
        fixed_tp = round(price * (1 + config.take_profit_pct), 2)
        if analysis.target_price and analysis.target_price > price:
            take_profit = min(analysis.target_price, fixed_tp)
            tp_source = f"Claude ${analysis.target_price:.2f} → TP ${take_profit:.2f}"
        else:
            take_profit = fixed_tp
            tp_source = f"Fix {config.take_profit_pct*100:.0f}% → TP ${take_profit:.2f}"

        position = Position(
            ticker=ticker,
            shares=shares,
            entry_price=price,
            entry_date=datetime.utcnow().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=analysis.suggested_hold_days,
            rationale=analysis.entry_rationale,
            entry_catalysts=analysis.key_catalysts[:5],
        )

        self.broker.buy(ticker, shares, price)
        self.portfolio.open_position(position)

        self._notifier.notify_buy(
            ticker=ticker, shares=shares, price=price,
            stop_loss=stop_loss, take_profit=take_profit,
            hold_days=analysis.suggested_hold_days,
            rationale=analysis.entry_rationale,
            sentiment_score=analysis.sentiment_score,
        )

        self.tracker.record_prediction(
            ticker=ticker,
            entry_price=price,
            predicted_target_price=analysis.target_price,
            predicted_hold_days=analysis.suggested_hold_days,
            predicted_direction=analysis.direction,
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            sources_used=analysis.sources_used,
            sources_breakdown=sources_breakdown,
        )

        return (
            f"[{ticker}] GEKAUFT – {shares} Stück @ ${price:.2f} "
            f"| SL: ${stop_loss} | {tp_source} "
            f"| Haltedauer: {analysis.suggested_hold_days}d "
            f"| Investiert: ${shares * price:.2f}"
        )

    def _do_close(self, ticker: str, pos: Position, price: float, reason: str) -> float:
        self.tracker.record_outcome(
            ticker=ticker,
            entry_price=pos.entry_price,
            entry_date=pos.entry_date,
            sell_price=price,
            sell_reason=reason,
        )
        return self.portfolio.close_position(ticker, price, reason)

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
