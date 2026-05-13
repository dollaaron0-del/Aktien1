"""
Hedge Strategy – öffnet und schließt Inverse-ETF-Positionen basierend
auf dem aktuellen Marktregime vom RecessionDetector.

Grundprinzip:
  - Primär Long-Strategie bleibt unverändert
  - Bei BEAR/CRISIS-Regime: inverse ETFs kaufen (SH, PSQ, SDS etc.)
  - Bei Rückkehr zu BULL/NEUTRAL: Hedges schließen
  - Maximale Hedge-Allokation: konfigurierbar (Standard 20% des Portfolios)
  - Inverse ETFs sind normale Long-Käufe – kein Margin, kein Short-Squeeze
"""
import math
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.trade_journal import TradeJournal
from broker.paper_broker import PaperBroker
from notifier.telegram_notifier import TelegramNotifier
from analyzers.recession_detector import RecessionDetector, BULL, NEUTRAL, BEAR, CRISIS


class HedgeStrategy:
    def __init__(
        self,
        portfolio: Portfolio,
        broker: PaperBroker,
        tracker: PerformanceTracker,
        journal: TradeJournal,
        detector: RecessionDetector,
        max_hedge_pct: float = 0.20,   # Max 20% of portfolio in hedges
        min_regime_for_hedge: str = BEAR,  # Only hedge from BEAR upward
    ):
        self.portfolio = portfolio
        self.broker = broker
        self.tracker = tracker
        self.journal = journal
        self.detector = detector
        self.max_hedge_pct = max_hedge_pct
        self._notifier = TelegramNotifier()
        self._min_regime_values = {BULL: 0, NEUTRAL: 1, BEAR: 2, CRISIS: 3}
        self._min_level = self._min_regime_values.get(min_regime_for_hedge, 2)

        # Tickers we currently hold as hedges
        self._hedge_tickers: set = set()

    # ── Main entry points ────────────────────────────────────────────────────

    def evaluate_regime(self, macro_news: Optional[List[Dict]] = None) -> Tuple[str, List[str]]:
        """
        Runs regime detection and adjusts hedge positions accordingly.
        Returns (regime, list_of_actions).
        Called during each analysis cycle.
        """
        result = self.detector.analyze(macro_news)
        regime   = result["regime"]
        hedges   = result["recommended_hedges"]
        intensity = result["hedge_intensity"]
        actions:  List[str] = []

        regime_level = self._min_regime_values.get(regime, 0)

        if regime_level < self._min_level:
            # Regime improved → close all hedges
            close_actions = self._close_all_hedges(regime)
            actions.extend(close_actions)
        else:
            # Regime warrants hedging → open/maintain positions
            open_actions = self._open_hedges(hedges, result)
            actions.extend(open_actions)
            # Also close any hedges whose theme is no longer relevant
            relevant_tickers = {h["ticker"] for h in hedges}
            stale_actions = self._close_stale_hedges(relevant_tickers, regime)
            actions.extend(stale_actions)

        return regime, actions

    def check_hedge_exits(self) -> List[str]:
        """
        Hourly: checks take-profit and stop-loss for open hedge positions.
        Hedge TP: +20% (regime collapse is usually fast)
        Hedge SL: -8% (if our recession call was wrong, cut quickly)
        """
        actions = []
        hedge_tickers = self._get_open_hedge_tickers()
        if not hedge_tickers:
            return actions

        prices = self.broker.get_prices(list(hedge_tickers))
        for ticker in hedge_tickers:
            pos = self.portfolio.get_position(ticker)
            if not pos:
                self._hedge_tickers.discard(ticker)
                continue
            price = prices.get(ticker)
            if not price:
                continue

            days_held = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
            reason = None

            if price >= pos.take_profit:
                reason = f"Hedge Take-Profit bei ${price:.2f}"
            elif price <= pos.stop_loss:
                reason = f"Hedge Stop-Loss bei ${price:.2f} (Markt erholt sich)"
            elif days_held >= pos.target_hold_days:
                reason = f"Hedge max. Haltedauer ({days_held}d)"

            if reason:
                pnl = self._close_hedge(ticker, pos, price, reason, days_held)
                sign = "+" if pnl >= 0 else ""
                actions.append(
                    f"[{ticker}] 🛡 HEDGE GESCHLOSSEN – {reason} | P&L: {sign}{pnl:.2f} USD"
                )
        return actions

    # ── Opening hedges ───────────────────────────────────────────────────────

    def _open_hedges(self, hedges: List[Dict], regime_result: Dict) -> List[str]:
        actions = []
        portfolio_value = self.portfolio.total_value(
            self.broker.get_prices(
                list(self.portfolio.all_positions().keys())
            )
        )
        current_hedge_value = self._current_hedge_value(portfolio_value)
        max_hedge_total = portfolio_value * self.max_hedge_pct

        for hedge in hedges:
            ticker = hedge["ticker"]
            if self.portfolio.get_position(ticker):
                continue  # Already hedged with this instrument

            # Cap: don't exceed max_hedge_pct total
            remaining_hedge_capacity = max_hedge_total - current_hedge_value
            if remaining_hedge_capacity < 50:
                break

            target_invest = portfolio_value * hedge["allocation_pct"]
            invest = min(target_invest, remaining_hedge_capacity, self.portfolio.cash * 0.95)
            if invest < 50:
                continue

            price = self.broker.get_price(ticker)
            if not price:
                continue

            shares = math.floor(invest / price * 100) / 100
            # Hedge SL: -8% (wrong regime call), TP: +25% (crash plays out)
            stop_loss  = round(price * 0.92, 2)
            take_profit = round(price * 1.25, 2)
            # Hedges hold max 30 days; if regime doesn't worsen in 30d, re-evaluate
            hold_days  = 30

            position = Position(
                ticker=ticker,
                shares=shares,
                entry_price=price,
                entry_date=datetime.utcnow().isoformat(),
                stop_loss=stop_loss,
                take_profit=take_profit,
                target_hold_days=hold_days,
                rationale=f"[HEDGE] {hedge['reason']}",
                entry_catalysts=[hedge["description"], regime_result["regime"]],
            )

            self.broker.buy(ticker, shares, price)
            self.portfolio.open_position(position)
            self._hedge_tickers.add(ticker)
            current_hedge_value += shares * price

            self.journal.log_entry(
                ticker=ticker, price=price,
                sentiment=regime_result["recession_score"],
                confidence="HIGH",
                direction="BEARISH",
                rationale=hedge["reason"],
                catalysts=[regime_result["regime"], hedge["description"]],
                risks=["Markt erholt sich schneller als erwartet"],
                sources={"recession_detector": 1},
                target_price=take_profit,
                hold_days=hold_days,
            )

            regime = regime_result["regime"]
            self._notifier.send(
                f"🛡 <b>HEDGE ERÖFFNET: {ticker}</b>\n"
                f"Regime: <b>{regime}</b> | Score: {regime_result['recession_score']:.2f}\n"
                f"Thema: {hedge['description']}\n"
                f"Grund: {hedge['reason']}\n"
                f"Investiert: ${shares * price:.0f} | SL: ${stop_loss} | TP: ${take_profit}"
            )

            actions.append(
                f"[{ticker}] 🛡 HEDGE ERÖFFNET – {hedge['description']} "
                f"| {shares} Stück @ ${price:.2f} "
                f"| Regime: {regime} (Score: {regime_result['recession_score']:.2f}) "
                f"| ${shares * price:.0f} investiert"
            )

        return actions

    # ── Closing hedges ───────────────────────────────────────────────────────

    def _close_all_hedges(self, reason_regime: str) -> List[str]:
        actions = []
        for ticker in list(self._get_open_hedge_tickers()):
            pos = self.portfolio.get_position(ticker)
            if not pos:
                self._hedge_tickers.discard(ticker)
                continue
            price = self.broker.get_price(ticker)
            if not price:
                continue
            reason = f"Regime verbessert zu {reason_regime} – Hedge nicht mehr nötig"
            pnl = self._close_hedge(ticker, pos, price, reason, 0)
            sign = "+" if pnl >= 0 else ""
            actions.append(
                f"[{ticker}] 🛡 HEDGE GESCHLOSSEN – {reason} | P&L: {sign}{pnl:.2f} USD"
            )
        return actions

    def _close_stale_hedges(self, relevant_tickers: set, regime: str) -> List[str]:
        """Closes hedge positions that are no longer in the recommended list."""
        actions = []
        for ticker in list(self._get_open_hedge_tickers()):
            if ticker not in relevant_tickers:
                pos = self.portfolio.get_position(ticker)
                if not pos:
                    continue
                price = self.broker.get_price(ticker)
                if not price:
                    continue
                reason = f"Hedge-Thema nicht mehr relevant (Regime: {regime})"
                pnl = self._close_hedge(ticker, pos, price, reason, 0)
                sign = "+" if pnl >= 0 else ""
                actions.append(
                    f"[{ticker}] 🛡 HEDGE ROTIERT – {reason} | P&L: {sign}{pnl:.2f} USD"
                )
        return actions

    def _close_hedge(
        self, ticker: str, pos: Position, price: float, reason: str, days_held: int
    ) -> float:
        self.tracker.record_outcome(
            ticker=ticker, entry_price=pos.entry_price,
            entry_date=pos.entry_date, sell_price=price, sell_reason=reason,
        )
        pnl = self.portfolio.close_position(ticker, price, reason)
        self.journal.log_exit(
            ticker=ticker, price=price, entry_price=pos.entry_price,
            pnl=pnl, reason=reason, days_held=days_held,
        )
        self._hedge_tickers.discard(ticker)
        self._notifier.send(
            f"🛡 <b>HEDGE GESCHLOSSEN: {ticker}</b>\n"
            f"Grund: {reason}\n"
            f"P&L: {'+'if pnl>=0 else ''}{pnl:.2f} USD"
        )
        return pnl

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_open_hedge_tickers(self) -> set:
        """Returns hedge tickers that still have open positions."""
        open_positions = set(self.portfolio.all_positions().keys())
        # Detect hedges by rationale prefix OR tracked set
        hedge_tickers = set()
        for ticker, pos in self.portfolio.all_positions().items():
            if (ticker in self._hedge_tickers or
                    (pos.rationale and pos.rationale.startswith("[HEDGE]"))):
                hedge_tickers.add(ticker)
        self._hedge_tickers = hedge_tickers  # sync
        return hedge_tickers

    def _current_hedge_value(self, portfolio_value: float) -> float:
        hedge_tickers = self._get_open_hedge_tickers()
        if not hedge_tickers:
            return 0.0
        prices = self.broker.get_prices(list(hedge_tickers))
        total = 0.0
        for ticker in hedge_tickers:
            pos = self.portfolio.get_position(ticker)
            if pos:
                total += pos.shares * prices.get(ticker, pos.entry_price)
        return total

    def regime_summary(self) -> Optional[Dict]:
        """Returns latest stored regime for display."""
        return self.detector.get_latest()
