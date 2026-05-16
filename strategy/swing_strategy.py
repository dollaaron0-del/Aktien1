import math
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from analyzers.claude_analyzer import AnalysisResult
from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from portfolio.focus_mode import FocusController
from portfolio.trade_journal import TradeJournal
from portfolio.signal_queue import SignalQueue
from portfolio.goal_risk_assessor import GoalRiskAssessor, CAUTION, DANGER, UNREACHABLE
from portfolio.circuit_breaker import CircuitBreaker
from broker.paper_broker import PaperBroker
from notifier.telegram_notifier import TelegramNotifier
from analyzers.earnings_filter import EarningsFilter
from analyzers.correlation_check import CorrelationChecker
from analyzers.kelly_sizing import KellySizer
from analyzers.macro_calendar import MacroCalendar
from analyzers.sector_rotation import SectorRotation
from analyzers.earnings_surprise import EarningsSurprise
from config import config
from logger import get_logger

log = get_logger(__name__)

# Confidence → Positionsgröße-Multiplikator
_CONFIDENCE_SIZING = {"HIGH": 1.0, "MEDIUM": 0.70, "LOW": 0.45}

# Trailing-Stop: Gewinnsicherungs-Stufen
# (min_gain_pct, new_stop_below_peak_pct)
_TRAILING_STEPS = [
    (0.20, 0.10),   # +20% Gewinn → SL auf peak - 10%
    (0.12, 0.06),   # +12% Gewinn → SL auf peak - 6%
    (0.06, 0.03),   # +6%  Gewinn → SL auf breakeven + 3%
]


class SwingStrategy:
    def __init__(
        self,
        portfolio: Portfolio,
        broker: PaperBroker,
        tracker: PerformanceTracker,
        phase_ctrl: PhaseController,
        focus_ctrl: FocusController,
        journal: TradeJournal,
        signal_queue: Optional[SignalQueue] = None,
        earnings_filter: Optional[EarningsFilter] = None,
        correlation_checker: Optional[CorrelationChecker] = None,
        kelly_sizer: Optional[KellySizer] = None,
        goal_risk_assessor: Optional[GoalRiskAssessor] = None,
    ):
        self.portfolio = portfolio
        self.broker = broker
        self.tracker = tracker
        self.phase_ctrl = phase_ctrl
        self.focus = focus_ctrl
        self.journal = journal
        self.signal_queue = signal_queue
        self._notifier = TelegramNotifier()
        self.earnings_filter = earnings_filter
        self.correlation = correlation_checker
        self.kelly = kelly_sizer
        self.goal_risk = goal_risk_assessor
        self.macro_cal      = MacroCalendar()
        self.sector_rot     = SectorRotation()
        self.earn_surp      = EarningsSurprise()
        self.circuit_breaker = CircuitBreaker()

    def evaluate(self, analysis: AnalysisResult, sources_breakdown: Optional[Dict[str, int]] = None) -> Optional[str]:
        ticker = analysis.ticker
        existing_position = self.portfolio.get_position(ticker)
        current_price = self.broker.get_price(ticker)

        if current_price is None:
            return f"[{ticker}] Kein Kurs verfügbar – übersprungen."

        if existing_position:
            # Scale-In: bei starkem Signal bestehende Position aufstocken
            scale_result = self._try_scale_in(existing_position, current_price, analysis)
            if scale_result:
                return scale_result
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

        # Score-Modifier für Positions-Limit und Kaufschwelle
        from analyzers.bot_scorer import BotScorer, get_modifiers as _get_score_mod
        _smod = _get_score_mod(BotScorer().get().current)

        # Skalierungs-Check: Positionslimit (Score-adjustiert)
        open_count  = len(self.portfolio.all_positions())
        max_allowed = max(1, self.focus.get_max_positions(portfolio_value) + _smod.position_count_adj)
        if open_count >= max_allowed:
            return (
                f"[{ticker}] Positionslimit erreicht ({open_count}/{max_allowed} bei "
                f"${portfolio_value:,.0f} Portfolio, Score-Mod: {_smod.position_count_adj:+d}) – kein neuer Kauf."
            )
        adaptive_threshold = self.tracker.get_adaptive_threshold(config.buy_threshold)
        phase_modifier = self.phase_ctrl.get_entry_threshold_modifier(portfolio_value)
        # Score-Modifier auf Kaufschwelle anwenden
        effective_threshold = self.focus.get_effective_threshold(
            adaptive_threshold + phase_modifier + _smod.threshold_adj, portfolio_value
        )

        if analysis.sentiment_score < effective_threshold:
            return (
                f"[{ticker}] Sentiment {analysis.sentiment_score:.2f} unter Schwelle "
                f"{effective_threshold:.2f} ({self.focus.profile.label}) – übersprungen."
            )

        # Circuit Breaker: Tagesverlust / Drawdown prüfen
        self.circuit_breaker.register_day_open(portfolio_value)
        cb_allowed, cb_reason = self.circuit_breaker.check_buy_allowed(portfolio_value)
        if not cb_allowed:
            self._notifier.send(cb_reason)
            log.warning("CircuitBreaker ausgelöst für %s: %s", ticker, cb_reason)
            return f"[{ticker}] {cb_reason}"

        # Makro-Kalender: Kauf pausieren vor kritischen Terminen
        macro_block, macro_reason = self.macro_cal.should_block_buy()
        if macro_block:
            return f"[{ticker}] ⏸ Makro-Pause: {macro_reason}"

        # Sektor-Rotation: schwache Sektoren meiden
        in_strong, sector_reason = self.sector_rot.is_ticker_in_strong_sector(ticker)
        if not in_strong:
            return f"[{ticker}] 📉 Sektor schwach: {sector_reason} – übersprungen."

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

        result = self._open_position(ticker, current_price, analysis, portfolio_value, sources_breakdown or {})

        # If capital was insufficient, save signal for later execution
        if result and "Nicht genug freies Kapital" in result and self.signal_queue:
            sig_id = self.signal_queue.enqueue(
                ticker=ticker,
                sentiment_score=analysis.sentiment_score,
                confidence=analysis.confidence,
                target_price=analysis.target_price,
                direction=analysis.direction,
                entry_rationale=analysis.entry_rationale,
                key_catalysts=analysis.key_catalysts,
                risk_factors=analysis.risk_factors,
                sources_used=analysis.sources_used,
                sources_breakdown=sources_breakdown or {},
                suggested_hold_days=analysis.suggested_hold_days,
            )
            return (
                f"[{ticker}] 📋 Signal in Warteschlange gespeichert "
                f"(Score: {analysis.sentiment_score:.2f}, ID #{sig_id}) – "
                f"wird ausgeführt sobald Kapital frei ist."
            )
        return result

    def check_open_positions(self) -> List[str]:
        actions = []
        positions = self.portfolio.all_positions()
        if not positions:
            # No open positions → try queued signals directly
            queued = self.process_signal_queue()
            return queued

        prices = self.broker.get_prices(list(positions.keys()))
        for ticker, pos in positions.items():
            price = prices.get(ticker)
            if price is None:
                continue

            days_held = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days

            # ── Trailing Stop-Loss aktualisieren ─────────────────────────────
            trailing_updated = self._update_trailing_stop(pos, price)
            if trailing_updated:
                actions.append(
                    f"[{ticker}] 📈 Trailing-Stop angepasst → ${pos.stop_loss:.2f}"
                )

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

        # After closes, try to execute queued signals with freed capital
        if actions:
            queued = self.process_signal_queue()
            actions.extend(queued)

        return actions

    def process_signal_queue(self) -> List[str]:
        """Tries to execute pending BUY signals with currently available capital."""
        if not self.signal_queue:
            return []

        results = []
        for signal in self.signal_queue.get_pending():
            ticker = signal["ticker"]

            # Skip if we now have a position in this ticker
            if self.portfolio.get_position(ticker):
                self.signal_queue.mark_expired(signal["id"])
                continue

            current_price = self.broker.get_price(ticker)
            if not current_price:
                continue

            portfolio_value = self.portfolio.total_value(
                self.broker.get_prices(list(self.portfolio.all_positions().keys()) + [ticker])
            )
            max_pos_pct = self.focus.get_max_position_pct(portfolio_value)
            if self.kelly:
                kelly_pct = self.kelly.compute(fallback_pct=max_pos_pct)
                max_pos_pct = min(max_pos_pct, kelly_pct)

            invest = min(portfolio_value * max_pos_pct, self.portfolio.cash * 0.95)
            if invest < 50:
                continue  # Still not enough capital, keep in queue

            # Execute the queued signal
            created_at = signal["created_at"][:10]
            result = self._open_position_from_signal(ticker, current_price, signal, portfolio_value, invest)
            self.signal_queue.mark_executed(signal["id"])
            results.append(
                f"[{ticker}] 📋 Warteschlangen-Signal ausgeführt (Signal vom {created_at}) "
                f"| {result}"
            )
            self._notifier.notify_buy(
                ticker=ticker,
                shares=invest / current_price,
                price=current_price,
                stop_loss=round(current_price * (1 - self.focus.get_stop_loss_pct()), 2),
                take_profit=round(current_price * (1 + self.focus.get_take_profit_pct()), 2),
                hold_days=self.focus.cap_hold_days(signal["suggested_hold_days"]),
                rationale=f"[Warteschlange vom {created_at}] {signal.get('entry_rationale', '')}",
                sentiment_score=signal["sentiment_score"],
            )
        return results

    def _open_position_from_signal(
        self, ticker: str, price: float, signal: Dict, portfolio_value: float, invest: float
    ) -> str:
        """Opens a position from a queued signal (no AnalysisResult needed)."""
        shares = math.floor(invest / price * 100) / 100
        sl_pct = self.focus.get_stop_loss_pct()
        tp_pct = self.focus.get_take_profit_pct()
        stop_loss = round(price * (1 - sl_pct), 2)
        fixed_tp = round(price * (1 + tp_pct), 2)
        tp = signal.get("target_price")
        take_profit = min(tp, fixed_tp) if (tp and tp > price) else fixed_tp
        capped_hold = self.focus.cap_hold_days(signal.get("suggested_hold_days") or 14)

        position = Position(
            ticker=ticker,
            shares=shares,
            entry_price=price,
            entry_date=datetime.utcnow().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=capped_hold,
            rationale=signal.get("entry_rationale"),
            entry_catalysts=signal.get("key_catalysts", []),
        )
        self.broker.buy(ticker, shares, price)
        self.portfolio.open_position(position)
        self.tracker.record_prediction(
            ticker=ticker,
            entry_price=price,
            predicted_target_price=signal.get("target_price"),
            predicted_hold_days=capped_hold,
            predicted_direction=signal.get("direction", "BULLISH"),
            sentiment_score=signal["sentiment_score"],
            confidence=signal["confidence"],
            sources_used=signal.get("sources_used", 0),
            sources_breakdown=signal.get("sources_breakdown", {}),
        )
        self.journal.log_entry(
            ticker=ticker,
            price=price,
            sentiment=signal["sentiment_score"],
            confidence=signal["confidence"],
            direction=signal.get("direction", "BULLISH"),
            rationale=signal.get("entry_rationale"),
            catalysts=signal.get("key_catalysts", []),
            risks=signal.get("risk_factors", []),
            sources=signal.get("sources_breakdown", {}),
            target_price=signal.get("target_price"),
            hold_days=capped_hold,
        )
        return (
            f"{shares} Stück @ ${price:.2f} "
            f"| SL: ${stop_loss} | TP: ${take_profit} "
            f"| Investiert: ${shares * price:.2f}"
        )

    def _check_exit(self, pos: Position, price: float, analysis: AnalysisResult) -> Optional[str]:
        ticker = pos.ticker
        days_held = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
        reason = None

        if analysis.thesis_valid is False and analysis.confidence != "LOW":
            reason = f"These gebrochen: {analysis.thesis_break_reason or 'Ursprüngliche Kaufkatalysatoren nicht mehr gültig'}"
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

        self.journal.log_daily_check(
            ticker=ticker, price=price,
            sentiment=analysis.sentiment_score,
            confidence=analysis.confidence,
            thesis_valid=analysis.thesis_valid,
            rationale=analysis.entry_rationale or analysis.raw_summary,
            recommendation=analysis.recommendation,
        )
        if analysis.thesis_valid is False:
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
        # Score-basierte Verhaltens-Modifier laden
        from analyzers.bot_scorer import BotScorer, get_modifiers
        _score_mod = get_modifiers(BotScorer().get().current)

        max_pos_pct = self.focus.get_max_position_pct(portfolio_value)
        if self.kelly:
            kelly_pct = self.kelly.compute(fallback_pct=max_pos_pct)
            max_pos_pct = min(max_pos_pct, kelly_pct)

        # Konfidenz-basiertes Positions-Sizing
        conf_mult    = _CONFIDENCE_SIZING.get(analysis.confidence, 0.70)
        sector_mult  = self.sector_rot.get_position_size_modifier(ticker)
        macro_mult   = self.macro_cal.get_position_size_modifier()
        earn_adj     = self.earn_surp.get_sentiment_adjustment(ticker)
        total_mult   = conf_mult * sector_mult * macro_mult * _score_mod.position_size_mult
        max_invest   = portfolio_value * max_pos_pct * total_mult

        # Margin: nur bei HIGH Confidence und wenn aktiviert
        margin_factor = 1.0
        using_margin = False
        if (config.use_margin
                and analysis.confidence == config.margin_min_confidence):
            try:
                from analyzers.margin_readiness import MarginTierTracker
                tier_result  = MarginTierTracker(self.tracker).get_active_tier()
                margin_factor = tier_result.factor
                using_margin  = margin_factor > 1.0
                if using_margin:
                    log.info(
                        "[%s] Margin Tier %d aktiv: %.2f× (%s)",
                        ticker, tier_result.active_tier.level,
                        margin_factor, tier_result.active_tier.label,
                    )
            except Exception as e:
                log.warning("[%s] Margin-Tier-Check fehlgeschlagen: %s", ticker, e)

        cash_limit = self.portfolio.cash * 0.95 * margin_factor
        invest = min(max_invest * margin_factor, cash_limit)

        if invest < 50:
            return f"[{ticker}] Nicht genug freies Kapital für neuen Trade."

        shares = math.floor(invest / price * 100) / 100
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

        raw_hold    = round(analysis.suggested_hold_days * _score_mod.hold_days_mult)
        capped_hold = self.focus.cap_hold_days(raw_hold)

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

        earn_info = self.earn_surp.check(ticker)
        earn_tag  = f" | {EarningsSurprise.format_surprise(earn_info)}" if earn_info.get("label") not in ("UNKNOWN", "IN_LINE", None) else ""

        margin_tag = f" | ⚡ MARGIN {margin_factor:.1f}×" if using_margin else ""
        score_tag  = f" | Score {BotScorer().get().current:.0f} ({_score_mod.score_range})"
        return (
            f"[{ticker}] GEKAUFT – {shares} Stück @ ${price:.2f} "
            f"| SL: ${stop_loss} | {tp_source} "
            f"| Haltedauer: {capped_hold}d "
            f"| Investiert: ${shares * price:.2f}"
            f"{earn_tag}{margin_tag}{score_tag}"
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
        self.journal.log_exit(
            ticker=ticker, price=price,
            entry_price=pos.entry_price,
            pnl=pnl, reason=reason,
            days_held=days_held,
        )
        self._run_goal_risk_check()
        self._run_score_update(ticker, pos.entry_price, price, reason)
        return pnl

    def _run_score_update(self, ticker: str, entry_price: float, exit_price: float, reason: str) -> None:
        """Bot-Score nach Trade aktualisieren und bei Meilensteinen benachrichtigen."""
        try:
            from analyzers.bot_scorer import BotScorer
            from analyzers.margin_readiness import MarginTierTracker

            return_pct = (exit_price - entry_price) / entry_price * 100
            tier = 0
            try:
                tier = MarginTierTracker(self.tracker).get_active_tier().active_tier.level
            except Exception:
                pass

            # Konfidenz aus letztem Journal-Eintrag
            confidence = "MEDIUM"
            try:
                recent = self.tracker.get_recent_trades(n=1)
                if recent:
                    conf_raw = recent[0].get("confidence") or "MEDIUM"
                    confidence = conf_raw.upper()
            except Exception:
                pass

            scorer = BotScorer()
            delta, new_milestones = scorer.record_trade(
                ticker=ticker,
                return_pct=return_pct,
                confidence=confidence,
                exit_reason=reason,
                current_tier=tier,
            )

            score = scorer.get()
            sign  = "+" if delta >= 0 else ""
            log.info("Score: %s%+.1f → %.1f/100 (%s)", sign, delta, score.current, score.label)

            for milestone in new_milestones:
                self._notifier.send(scorer.to_telegram_milestone(milestone))

        except Exception as e:
            log.warning("Score-Update fehlgeschlagen: %s", e)

    def _run_goal_risk_check(self) -> None:
        """Nach jedem Trade: prüfe ob das Portfolio-Ziel noch erreichbar ist."""
        if not self.goal_risk or not self.goal_risk.active:
            return
        try:
            prices = self.broker.get_prices(list(self.portfolio.all_positions().keys()))
            portfolio_value = self.portfolio.total_value(prices)
            stats = self.tracker.get_stats()
            assessment = self.goal_risk.assess(portfolio_value, stats)
            if assessment is None:
                return

            risk = assessment.risk_level
            if risk == UNREACHABLE:
                icon = "🚨"
            elif risk == DANGER:
                icon = "⚠️"
            elif risk == CAUTION:
                icon = "🟡"
            else:
                return  # OK – kein Alarm nötig

            # Telegram-Benachrichtigung
            msg = (
                f"{icon} *ZIEL-RISIKOANALYSE* nach Trade-Abschluss\n\n"
                f"{assessment.to_text()}"
            )
            self._notifier.send(msg)
        except Exception:
            pass

    @staticmethod
    def _update_trailing_stop(pos: Position, current_price: float) -> bool:
        """Zieht den Stop-Loss nach oben wenn der Kurs steigt. Gibt True zurück wenn aktualisiert."""
        gain = (current_price - pos.entry_price) / pos.entry_price
        best_stop = pos.stop_loss
        for min_gain, trail_pct in _TRAILING_STEPS:
            if gain >= min_gain:
                candidate = round(current_price * (1 - trail_pct), 2)
                best_stop = max(best_stop, candidate)
                break
        if best_stop > pos.stop_loss:
            pos.stop_loss = best_stop
            return True
        return False

    def _try_scale_in(
        self, pos: Position, current_price: float, analysis: AnalysisResult
    ) -> Optional[str]:
        """
        Stockt eine bestehende Position auf wenn:
        - Signal ist BUY mit HIGH-Konfidenz
        - Kurs ist nicht mehr als 8% über Einstiegskurs (kein Nachjagen)
        - Bestehende Position ist kleiner als 80% des erlaubten Maximums
        - Genug freies Kapital
        """
        if analysis.recommendation != "BUY" or analysis.confidence != "HIGH":
            return None
        if analysis.sentiment_score < config.buy_threshold + 0.15:
            return None

        ticker = pos.ticker
        price_drift = (current_price - pos.entry_price) / pos.entry_price
        if abs(price_drift) > 0.08:
            return None  # Kurs zu weit vom Einstieg (oben oder unten)

        portfolio_value = self.portfolio.total_value(
            self.broker.get_prices(list(self.portfolio.all_positions().keys()))
        )
        max_pos_value = portfolio_value * self.focus.get_max_position_pct(portfolio_value)
        current_pos_value = pos.shares * current_price

        # Nur aufstocken wenn aktuelle Position < 80% des Maximums
        if current_pos_value >= max_pos_value * 0.80:
            return None

        add_invest = min(
            (max_pos_value - current_pos_value) * 0.50,  # max. 50% der Lücke
            self.portfolio.cash * 0.30,
        )
        if add_invest < 50:
            return None

        add_shares = math.floor(add_invest / current_price * 100) / 100
        if add_shares <= 0:
            return None

        self.broker.buy(ticker, add_shares, current_price)
        # Durchschnittskurs berechnen
        new_total_shares = pos.shares + add_shares
        avg_price = (pos.shares * pos.entry_price + add_shares * current_price) / new_total_shares
        pos.shares = new_total_shares
        pos.entry_price = round(avg_price, 4)

        self._notifier.send(
            f"📈 <b>Scale-In {ticker}</b>\n"
            f"+{add_shares} Stück @ ${current_price:.2f}\n"
            f"Ø Einstieg jetzt: ${avg_price:.2f} | Gesamt: {new_total_shares:.2f} Stück"
        )
        return (
            f"[{ticker}] 📈 SCALE-IN +{add_shares} Stück @ ${current_price:.2f} "
            f"| Ø ${avg_price:.2f} | Gesamt: {new_total_shares:.2f} Stück"
        )

    def build_open_position_context(self, ticker: str) -> Optional[Dict]:
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
