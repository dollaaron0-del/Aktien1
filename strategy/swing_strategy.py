import math
from datetime import datetime
from typing import Optional, List
from analyzers.claude_analyzer import AnalysisResult
from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from broker.paper_broker import PaperBroker
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

    def evaluate(self, analysis: AnalysisResult) -> Optional[str]:
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

        # Adaptive threshold + phase modifier (Distribution phase = more conservative)
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

        return self._open_position(ticker, current_price, analysis, portfolio_value)

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
                self.tracker.record_outcome(
                    ticker=ticker,
                    entry_price=pos.entry_price,
                    entry_date=pos.entry_date,
                    sell_price=price,
                    sell_reason=reason,
                )
                pnl = self.portfolio.close_position(ticker, price, reason)
                actions.append(
                    f"[{ticker}] VERKAUFT – {reason} | P&L: {'+' if pnl >= 0 else ''}{pnl:.2f} USD"
                )

        return actions

    def _check_exit(self, pos: Position, price: float, analysis: AnalysisResult) -> Optional[str]:
        ticker = pos.ticker
        days_held = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
        reason = None

        if price <= pos.stop_loss:
            reason = f"Stop-Loss bei ${price:.2f}"
        elif price >= pos.take_profit:
            reason = f"Take-Profit bei ${price:.2f}"
        elif days_held >= pos.target_hold_days:
            reason = f"Haltedauer abgelaufen ({days_held}d)"
        elif analysis.recommendation == "SELL" and analysis.confidence != "LOW":
            reason = f"Sentiment-Signal SELL (Score: {analysis.sentiment_score:.2f})"

        if reason:
            self.tracker.record_outcome(
                ticker=ticker,
                entry_price=pos.entry_price,
                entry_date=pos.entry_date,
                sell_price=price,
                sell_reason=reason,
            )
            pnl = self.portfolio.close_position(ticker, price, reason)
            sign = "+" if pnl >= 0 else ""
            return f"[{ticker}] VERKAUFT – {reason} | P&L: {sign}{pnl:.2f} USD"

        return f"[{ticker}] Position gehalten ({days_held}d) | Kurs: ${price:.2f}"

    def _open_position(
        self, ticker: str, price: float, analysis: AnalysisResult, portfolio_value: float
    ) -> str:
        max_invest = portfolio_value * config.max_position_pct
        invest = min(max_invest, self.portfolio.cash * 0.95)

        if invest < 50:
            return f"[{ticker}] Nicht genug freies Kapital für neuen Trade."

        shares = math.floor(invest / price * 100) / 100

        stop_loss = round(price * (1 - config.stop_loss_pct), 2)
        # Take-profit: use whichever is lower – Claude's target price or fixed %
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
        )

        self.broker.buy(ticker, shares, price)
        self.portfolio.open_position(position)

        self.tracker.record_prediction(
            ticker=ticker,
            entry_price=price,
            predicted_target_price=analysis.target_price,
            predicted_hold_days=analysis.suggested_hold_days,
            predicted_direction=analysis.direction,
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            sources_used=analysis.sources_used,
        )

        return (
            f"[{ticker}] GEKAUFT – {shares} Stück @ ${price:.2f} "
            f"| SL: ${stop_loss} | {tp_source} "
            f"| Haltedauer: {analysis.suggested_hold_days}d "
            f"| Investiert: ${shares * price:.2f}"
        )
