import math
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional, List, Dict, Tuple
from analyzers.claude_analyzer import AnalysisResult

if TYPE_CHECKING:
    from strategy.short_strategy import ShortStrategy
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
from analyzers.bot_scorer import BotScorer, get_modifiers as _get_score_mod
from analyzers.sentiment_memory import SentimentMemory
from analyzers.margin_readiness import MarginTierTracker
from analyzers.reentry_tracker import ReEntryTracker
from analyzers.regime_adaptive import RegimeAdaptiveConfig, get_adaptive_params
from analyzers.sharpe_sizer import SharpeSizer
from analyzers.cross_asset import CrossAssetSignals
from analyzers.options_intelligence import OptionsIntelligence
from analyzers.rl_agent import RLAgent, RLState
from config import config
from logger import get_logger

log = get_logger(__name__)


def _is_crypto(ticker: str) -> bool:
    """True wenn der Ticker ein Krypto-Asset ist (z.B. 'BTC', 'ETH/USD')."""
    try:
        from analyzers.crypto_universe import CRYPTO_UNIVERSE
        return ticker.split("/")[0].upper() in CRYPTO_UNIVERSE or ticker.endswith("/USD")
    except Exception:
        return False


_FAILED_STATUSES = {"error", "canceled", "cancelled", "expired", "rejected"}


def _check_fill(fill: dict, ticker: str, side: str) -> Tuple[bool, float, str]:
    """
    Returns (ok, fill_price, warning_msg).
    ok=False means the order definitively failed and the position must NOT be recorded.
    ok=True with warning_msg != '' means 'pending' — position recorded but operator must verify.
    """
    status = fill.get("status", "")
    fill_price = fill.get("fill_price")
    order_id = fill.get("order_id", "?")

    if status in _FAILED_STATUSES:
        reason = fill.get("reason", status)
        log.error("[%s] %s order FAILED: %s (order_id=%s)", ticker, side.upper(), reason, order_id)
        return False, 0.0, reason

    if status == "pending":
        msg = (
            f"⚠️ <b>{ticker} {side.upper()}-Order UNBESTÄTIGT</b>\n"
            f"Order-ID: <code>{order_id}</code>\n"
            f"Position wird vorläufig eingebucht — bitte manuell im Broker prüfen!"
        )
        log.warning("[%s] %s order pending after timeout (order_id=%s)", ticker, side.upper(), order_id)
        return True, fill_price or 0.0, msg

    return True, fill_price or 0.0, ""


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
        self.macro_cal       = MacroCalendar()
        self.sector_rot      = SectorRotation()
        self.earn_surp       = EarningsSurprise()
        self.circuit_breaker = CircuitBreaker()
        self.regime_cfg      = RegimeAdaptiveConfig()
        self.sharpe_sizer    = SharpeSizer()
        self.cross_asset     = CrossAssetSignals()
        self.options_intel   = OptionsIntelligence()
        self.rl_agent        = RLAgent()
        # Short-Strategy (optional, wird von main.py gesetzt)
        self._short_strategy: Optional["ShortStrategy"] = None

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

        # ── Turbo-Modus: Maximale Aggressivität (nur Paper-Trading) ──────────
        if config.turbo_mode:
            if config.broker_mode != "paper":
                log.error("TURBO_MODE ist nur mit BROKER_MODE=paper erlaubt – ignoriert.")
            else:
                return self._evaluate_turbo(ticker, current_price, analysis, sources_breakdown)

        if analysis.recommendation != "BUY":
            # Short-Modus: SELL-Signal mit hoher Konfidenz → Short-Position prüfen
            if analysis.recommendation == "SELL" and analysis.confidence in ("HIGH", "MEDIUM"):
                if hasattr(self, '_short_strategy') and self._short_strategy:
                    short_result = self._short_strategy.evaluate_short(analysis)
                    if short_result:
                        return short_result
            return None
        if analysis.confidence == "LOW":
            return f"[{ticker}] BUY-Signal, aber Konfidenz zu niedrig – übersprungen."
        if analysis.sources_used < config.min_sources:
            return f"[{ticker}] Zu wenige Quellen ({analysis.sources_used}) – übersprungen."

        portfolio_value = self.portfolio.total_value(
            self.broker.get_prices(list(self.portfolio.all_positions().keys()) + [ticker])
        )

        # Score-Modifier für Positions-Limit und Kaufschwelle (einmal laden, mehrfach nutzen)
        _bot_scorer = BotScorer()
        _bot_score_state = _bot_scorer.get()
        _current_score = _bot_score_state.current
        _smod = _get_score_mod(_current_score)

        # Score-Malus erst nach 10 bewerteten Trades anwenden (frischer Bot soll starten können)
        _trades_scored = _bot_score_state.trades_scored
        if _trades_scored < 10 and _smod.threshold_adj > 0:
            log.debug(
                "Score-Malus ausgesetzt (erst %d/10 Trades bewertet) – Threshold unverändert",
                _trades_scored,
            )
            from dataclasses import replace as _dc_replace
            _smod = _dc_replace(_smod, threshold_adj=0.0, position_count_adj=max(0, _smod.position_count_adj))

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

        # Sentiment-Verlässlichkeit pro Ticker (aus historischen Trades gelernt)
        sentiment_memory_adj = 0.0
        try:
            sentiment_memory_adj = SentimentMemory().get_threshold_adjustment(ticker)
        except Exception:
            pass

        # Score-Modifier auf Kaufschwelle anwenden
        effective_threshold = self.focus.get_effective_threshold(
            adaptive_threshold + phase_modifier + _smod.threshold_adj + sentiment_memory_adj,
            portfolio_value
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

        # ── Cross-Asset Risk-Appetite ────────────────────────────────────────
        try:
            ca = self.cross_asset.fetch()
            if ca.recommendation == "RISK_OFF":
                log.info("[%s] Cross-Asset RISK_OFF (score=%.2f) – Kaufschwelle erhöht", ticker, ca.risk_appetite_score)
                if analysis.sentiment_score < effective_threshold + 0.08:
                    return f"[{ticker}] ⚡ Cross-Asset RISK_OFF (Score={ca.risk_appetite_score:.2f}) – Signal zu schwach."
        except Exception as e:
            log.debug("Cross-Asset-Check fehlgeschlagen: %s", e)

        # ── Options Intelligence ─────────────────────────────────────────────
        try:
            opt = self.options_intel.analyze(ticker)
            if opt.smart_money_signal == "BEARISH" and opt.signal_strength == "HIGH":
                return f"[{ticker}] 📊 Options: Smart-Money BEARISH (P/C={opt.put_call_ratio:.2f}) – übersprungen."
        except Exception as e:
            log.debug("Options-Intelligence fehlgeschlagen: %s", e)

        # ── RL Agent Entscheidung ────────────────────────────────────────────
        try:
            from collectors.vix_monitor import get_vix
            rl_state = RLState(
                sentiment_score=analysis.sentiment_score,
                vix_level=min(1.0, (get_vix() or 20.0) / 50.0),
                momentum_5d=0.0,   # Wird vom RL aus vergangenen Trades gelernt
                news_velocity=min(1.0, analysis.sources_used / 20.0),
                confidence_encoded=RLState.encode_confidence(analysis.confidence),
                regime_encoded=RLState.encode_regime(
                    "BULL" if analysis.direction == "BULLISH" else
                    "BEAR" if analysis.direction == "BEARISH" else "NEUTRAL"
                ),
            )
            rl_buy, rl_modifier = self.rl_agent.should_buy(rl_state)
            if not rl_buy:
                log.info("[%s] RL-Agent rät ab (explain: %s)", ticker, self.rl_agent.explain(rl_state))
                # RL blockiert nur wenn es genug Erfahrung hat (> 10 Trades)
                stats = self.rl_agent.get_stats()
                if stats.get("total_trades", 0) >= 10:
                    return f"[{ticker}] 🤖 RL-Agent: Trade nicht empfohlen (Lernbasis: {stats['total_trades']} Trades)"
        except Exception as e:
            log.debug("RL-Agent fehlgeschlagen: %s", e)
            rl_modifier = 1.0

        result = self._open_position(ticker, current_price, analysis, portfolio_value, sources_breakdown or {},
                                     score_mod=_smod, current_score=_current_score, rl_modifier=rl_modifier)

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

    def _evaluate_turbo(
        self,
        ticker: str,
        current_price: float,
        analysis: AnalysisResult,
        sources_breakdown: Optional[Dict[str, int]],
    ) -> Optional[str]:
        """
        Turbo-Modus: Maximale Aggressivität für Paper-Trading-Experimente.
        Alle normalen Filter (Konfidenz, Schwelle, CircuitBreaker, Makro, Sektor)
        werden umgangen. Ziel: herausfinden ob rücksichtslose Gewinnoptimierung
        tatsächlich mehr Rendite bringt oder das Kapital vernichtet.
        """
        # Kein BUY-Signal → trotzdem prüfen ob SHORT sinnvoll wäre
        if analysis.recommendation != "BUY":
            if analysis.recommendation == "SELL" and hasattr(self, '_short_strategy') and self._short_strategy:
                short_result = self._short_strategy.evaluate_short(analysis)
                if short_result:
                    return short_result
            return None

        portfolio_value = self.portfolio.total_value(
            self.broker.get_prices(list(self.portfolio.all_positions().keys()) + [ticker])
        )

        # Positionslimit (Turbo hat eigenes höheres Limit)
        open_count = len(self.portfolio.all_positions())
        if open_count >= config.turbo_max_positions:
            return (
                f"[TURBO][{ticker}] Positionslimit {config.turbo_max_positions} erreicht – übersprungen."
            )

        # Kapitalcheck: mind. $50 Cash verfügbar
        invest = portfolio_value * config.turbo_max_position_pct
        invest = min(invest, self.portfolio.cash * 0.95)
        if invest < 50:
            return f"[TURBO][{ticker}] Nicht genug Kapital (${self.portfolio.cash:.0f}) – übersprungen."

        shares = math.floor(invest / current_price * 100) / 100
        if shares <= 0:
            return f"[TURBO][{ticker}] Zu teuer für verfügbares Kapital – übersprungen."

        sl_pct = config.turbo_stop_loss_pct
        tp_pct = config.turbo_take_profit_pct
        stop_loss  = round(current_price * (1 - sl_pct), 2)
        take_profit = round(current_price * (1 + tp_pct), 2)
        if analysis.target_price and analysis.target_price > current_price:
            take_profit = max(take_profit, analysis.target_price)

        fill = self.broker.buy(ticker, shares, current_price)
        ok, actual_price, warn = _check_fill(fill, ticker, "buy")
        if not ok:
            return f"[TURBO][{ticker}] ⛔ BUY-Order fehlgeschlagen: {warn}"
        if not actual_price:
            actual_price = current_price
        if warn:
            self._notifier.send(warn)

        # SL/TP auf tatsächlichen Fill-Preis anpassen
        stop_loss   = round(actual_price * (1 - sl_pct), 2)
        take_profit = round(actual_price * (1 + tp_pct), 2)
        if analysis.target_price and analysis.target_price > actual_price:
            take_profit = max(take_profit, analysis.target_price)

        position = Position(
            ticker=ticker,
            shares=shares,
            entry_price=actual_price,
            entry_date=datetime.utcnow().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=analysis.suggested_hold_days or 14,
            rationale=f"[TURBO] {analysis.entry_rationale or ''}",
            entry_catalysts=analysis.key_catalysts[:5],
        )
        self.portfolio.open_position(position)

        self._notifier.notify_buy(
            ticker=ticker, shares=shares, price=actual_price,
            stop_loss=stop_loss, take_profit=take_profit,
            hold_days=analysis.suggested_hold_days or 14,
            rationale=f"🚀 TURBO-MODUS | {analysis.entry_rationale or ''}",
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            direction=analysis.direction,
            target_price=analysis.target_price,
            key_catalysts=analysis.key_catalysts[:4],
            risk_factors=analysis.risk_factors[:3],
            sources_breakdown=sources_breakdown or {},
        )
        self.tracker.record_prediction(
            ticker=ticker,
            entry_price=actual_price,
            predicted_target_price=analysis.target_price,
            predicted_hold_days=analysis.suggested_hold_days or 14,
            predicted_direction=analysis.direction,
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            sources_used=analysis.sources_used,
            sources_breakdown=sources_breakdown or {},
            mode="turbo",
        )
        self.journal.log_entry(
            ticker=ticker,
            price=actual_price,
            sentiment=analysis.sentiment_score,
            confidence=analysis.confidence,
            direction=analysis.direction,
            rationale=f"[TURBO] {analysis.entry_rationale or ''}",
            catalysts=analysis.key_catalysts[:5],
            risks=analysis.risk_factors[:5],
            sources=sources_breakdown or {},
            target_price=analysis.target_price,
            hold_days=analysis.suggested_hold_days or 14,
        )
        log.info(
            "[TURBO][%s] GEKAUFT %.2f Stück @ $%.2f | SL $%.2f | TP $%.2f | Conf: %s | Score: %.2f",
            ticker, shares, actual_price, stop_loss, take_profit,
            analysis.confidence, analysis.sentiment_score,
        )
        return (
            f"[TURBO][{ticker}] 🚀 GEKAUFT – {shares} Stück @ ${actual_price:.2f} "
            f"| SL: ${stop_loss} | TP: ${take_profit} "
            f"| Investiert: ${shares * actual_price:.2f} "
            f"| Konfidenz: {analysis.confidence} | Score: {analysis.sentiment_score:.2f}"
        )

    def check_open_positions(self) -> List[str]:
        actions = []
        positions = self.portfolio.all_positions()
        if not positions:
            # No open positions → try queued signals directly
            queued = self.process_signal_queue()
            return queued

        prices = self.broker.get_prices(list(positions.keys()))
        for ticker, pos in positions.items():
            if _is_crypto(ticker):
                price = self.broker.get_crypto_price(ticker)
            else:
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
                rationale=f"[Signal vom {created_at}] {signal.get('entry_rationale', '')}",
                sentiment_score=signal["sentiment_score"],
                confidence=signal.get("confidence", "MEDIUM"),
                direction=signal.get("direction", "BULLISH"),
                target_price=signal.get("target_price"),
                key_catalysts=signal.get("key_catalysts", [])[:4],
                risk_factors=signal.get("risk_factors", [])[:3],
                sources_breakdown=signal.get("sources_breakdown", {}),
            )
        return results

    def _open_position_from_signal(
        self, ticker: str, price: float, signal: Dict, portfolio_value: float, invest: float
    ) -> str:
        """Opens a position from a queued signal (no AnalysisResult needed)."""
        # Liquidität + Gap-Schutz (auch für TV/Queue-Signale)
        liq_ok, liq_reason = self._check_liquidity_and_gap(ticker, price)
        if not liq_ok:
            log.warning("[%s] Queue-Signal abgebrochen: %s", ticker, liq_reason)
            return f"[{ticker}] ⛔ {liq_reason} – Queue-Signal abgebrochen."

        shares = math.floor(invest / price * 100) / 100
        sl_pct = self.focus.get_stop_loss_pct()
        tp_pct = self.focus.get_take_profit_pct()
        stop_loss = round(price * (1 - sl_pct), 2)
        fixed_tp = round(price * (1 + tp_pct), 2)
        tp = signal.get("target_price")
        take_profit = min(tp, fixed_tp) if (tp and tp > price) else fixed_tp
        capped_hold = self.focus.cap_hold_days(signal.get("suggested_hold_days") or 14)

        fill = self.broker.buy(ticker, shares, price)
        ok, actual_price, warn = _check_fill(fill, ticker, "buy")
        if not ok:
            return f"[{ticker}] ⛔ BUY-Order fehlgeschlagen: {warn}"
        if not actual_price:
            actual_price = price
        if warn:
            self._notifier.send(warn)
        stop_loss = round(actual_price * (1 - sl_pct), 2)
        take_profit = min(tp, round(actual_price * (1 + tp_pct), 2)) if (tp and tp > actual_price) else round(actual_price * (1 + tp_pct), 2)
        position = Position(
            ticker=ticker,
            shares=shares,
            entry_price=actual_price,
            entry_date=datetime.utcnow().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=capped_hold,
            rationale=signal.get("entry_rationale"),
            entry_catalysts=signal.get("key_catalysts", []),
        )
        self.portfolio.open_position(position)
        self.tracker.record_prediction(
            ticker=ticker,
            entry_price=actual_price,
            predicted_target_price=signal.get("target_price"),
            predicted_hold_days=capped_hold,
            predicted_direction=signal.get("direction", "BULLISH"),
            sentiment_score=signal["sentiment_score"],
            confidence=signal["confidence"],
            sources_used=signal.get("sources_used", 0),
            sources_breakdown=signal.get("sources_breakdown", {}),
            mode="exploration" if config.exploration_mode else "normal",
        )
        self.journal.log_entry(
            ticker=ticker,
            price=actual_price,
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
            f"{shares} Stück @ ${actual_price:.2f} "
            f"| SL: ${stop_loss} | TP: ${take_profit} "
            f"| Investiert: ${shares * actual_price:.2f}"
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
                days_held=days_held,
                target_hold_days=pos.target_hold_days,
                entry_catalysts=pos.entry_catalysts,
                entry_rationale=pos.rationale or "",
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

    # ── Liquiditäts- und Gap-Prüfung ─────────────────────────────────────────

    def _check_liquidity_and_gap(self, ticker: str, current_price: float) -> Tuple[bool, str]:
        """
        Prüft zwei Bedingungen vor jedem Kauf:
        1. Mindest-Tagesvolumen (Standard: 500k Aktien) – illiquide Werte meiden
        2. Market-Open-Gap (Standard: max. 4 %) – nicht in Gaps hinein kaufen

        Gibt (True, "") zurück wenn alles in Ordnung ist.
        Gibt (False, Grund) zurück wenn der Kauf übersprungen werden soll.
        """
        min_volume = int(os.getenv("MIN_DAILY_VOLUME", "500000"))
        max_gap_pct = float(os.getenv("MAX_GAP_PCT", "0.04"))

        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period="3d", interval="1d")
            if hist.empty or len(hist) < 2:
                return True, ""  # Kein Datenpunkt → nicht blockieren

            avg_vol = int(hist["Volume"].mean())
            if avg_vol < min_volume:
                return False, (
                    f"Zu geringes Volumen ({avg_vol:,} Ø vs. Minimum {min_volume:,}) – "
                    f"illiquid, Slippage-Risiko"
                )

            prev_close = float(hist["Close"].iloc[-2])
            if prev_close > 0:
                gap = abs(current_price - prev_close) / prev_close
                if gap > max_gap_pct:
                    direction = "hoch" if current_price > prev_close else "runter"
                    return False, (
                        f"Gap {gap*100:.1f}% {direction} vs. Vortag ${prev_close:.2f} – "
                        f"nicht in Gap hinein kaufen"
                    )
        except Exception as e:
            log.debug("Liquiditäts-Check für %s fehlgeschlagen (ignoriert): %s", ticker, e)

        return True, ""

    def _open_position(
        self,
        ticker: str,
        price: float,
        analysis: AnalysisResult,
        portfolio_value: float,
        sources_breakdown: Dict[str, int],
        score_mod=None,
        current_score: float = 50.0,
        rl_modifier: float = 1.0,
    ) -> str:
        # Score-Modifier ggf. neu laden (z.B. bei direktem Aufruf ohne evaluate())
        _score_mod = score_mod if score_mod is not None else _get_score_mod(current_score)

        # Regime-adaptive Parameter (BULL/NEUTRAL/BEAR/CRISIS)
        try:
            # Seitwärts-Flag aus letztem Detector-Ergebnis holen (kein extra API-Call)
            _is_ranging = False
            try:
                from analyzers.recession_detector import RecessionDetector as _RD
                _latest = _RD().get_latest()
                _is_ranging = bool(_latest and _latest.get("market_is_ranging"))
            except Exception:
                pass
            regime_params = get_adaptive_params(market_is_ranging=_is_ranging)
            regime_sl_pct = regime_params.sl_pct
            regime_tp_pct = regime_params.tp_pct
            regime_pos_mult = regime_params.position_size_mult
        except Exception:
            regime_params = None
            regime_sl_pct = config.stop_loss_pct
            regime_tp_pct = config.take_profit_pct
            regime_pos_mult = 1.0

        # Sharpe-basierter Größen-Multiplikator
        try:
            sharpe_mult = self.sharpe_sizer.get_size_modifier(ticker)
        except Exception:
            sharpe_mult = 1.0

        max_pos_pct = self.focus.get_max_position_pct(portfolio_value)
        if self.kelly:
            kelly_pct = self.kelly.compute(fallback_pct=max_pos_pct)
            max_pos_pct = min(max_pos_pct, kelly_pct)

        # Konfidenz-basiertes Positions-Sizing
        conf_mult    = _CONFIDENCE_SIZING.get(analysis.confidence, 0.70)
        sector_mult  = self.sector_rot.get_position_size_modifier(ticker)
        macro_mult   = self.macro_cal.get_position_size_modifier()
        earn_adj     = self.earn_surp.get_sentiment_adjustment(ticker)

        # Währungsrisiko für EU-Aktien (GBP/CHF/SEK Gegenwind → kleinere Position)
        fx_mult = 1.0
        try:
            from analyzers.currency_risk import get_currency_modifier
            fx_mult, fx_note = get_currency_modifier(ticker)
            if fx_note:
                log.info("[%s] FX: %s (×%.2f)", ticker, fx_note, fx_mult)
        except Exception:
            pass

        total_mult   = conf_mult * sector_mult * macro_mult * _score_mod.position_size_mult * regime_pos_mult * sharpe_mult * rl_modifier * fx_mult
        max_invest   = portfolio_value * max_pos_pct * total_mult

        # Margin: nur bei HIGH Confidence und wenn aktiviert
        margin_factor = 1.0
        using_margin = False
        if (config.use_margin
                and analysis.confidence == config.margin_min_confidence):
            try:
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

        # Liquidität + Gap-Schutz
        liq_ok, liq_reason = self._check_liquidity_and_gap(ticker, price)
        if not liq_ok:
            return f"[{ticker}] ⛔ {liq_reason} – Kauf abgebrochen."

        # Krypto: erweiterte SL/TP-Prozentsätze aus Config
        if _is_crypto(ticker):
            sl_pct = config.crypto_stop_loss_pct
            tp_pct = config.crypto_take_profit_pct
        else:
            sl_pct = regime_sl_pct
            tp_pct = regime_tp_pct

        shares = math.floor(invest / price * 100) / 100
        # Regime-adaptive SL/TP (überschreibt fixe Config-Werte)
        stop_loss = round(price * (1 - sl_pct), 2)
        fixed_tp  = round(price * (1 + tp_pct), 2)
        if analysis.target_price and analysis.target_price > price:
            take_profit = min(analysis.target_price, fixed_tp)
            tp_source = f"Claude ${analysis.target_price:.2f} → TP ${take_profit:.2f}"
        else:
            take_profit = fixed_tp
            tp_source = f"Regime {tp_pct*100:.0f}% → TP ${take_profit:.2f}"

        hold_regime_mult = regime_params.hold_days_mult if regime_params else 1.0
        raw_hold    = round(analysis.suggested_hold_days * _score_mod.hold_days_mult * hold_regime_mult)
        capped_hold = self.focus.cap_hold_days(raw_hold)

        if _is_crypto(ticker):
            fill = self.broker.buy_crypto(ticker, invest)
        else:
            fill = self.broker.buy(ticker, shares, price)
        ok, actual_price, warn = _check_fill(fill, ticker, "buy")
        if not ok:
            return f"[{ticker}] ⛔ BUY-Order fehlgeschlagen: {warn}"
        if not actual_price:
            actual_price = price
        if warn:
            self._notifier.send(warn)
        # Recalculate SL/TP from actual fill price to keep levels accurate
        if actual_price != price:
            stop_loss = round(actual_price * (1 - sl_pct), 2)
            fixed_tp2 = round(actual_price * (1 + tp_pct), 2)
            if analysis.target_price and analysis.target_price > actual_price:
                take_profit = min(analysis.target_price, fixed_tp2)
                tp_source = f"Claude ${analysis.target_price:.2f} → TP ${take_profit:.2f}"
            else:
                take_profit = fixed_tp2
                tp_source = f"Regime {tp_pct*100:.0f}% → TP ${take_profit:.2f}"

        position = Position(
            ticker=ticker,
            shares=shares,
            entry_price=actual_price,
            entry_date=datetime.utcnow().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=capped_hold,
            rationale=analysis.entry_rationale,
            entry_catalysts=analysis.key_catalysts[:5],
        )
        self.portfolio.open_position(position)

        self._notifier.notify_buy(
            ticker=ticker, shares=shares, price=actual_price,
            stop_loss=stop_loss, take_profit=take_profit,
            hold_days=capped_hold,
            rationale=analysis.entry_rationale or "",
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            direction=analysis.direction,
            target_price=analysis.target_price,
            key_catalysts=analysis.key_catalysts[:4],
            risk_factors=analysis.risk_factors[:3],
            sources_breakdown=sources_breakdown,
        )
        self.tracker.record_prediction(
            ticker=ticker,
            entry_price=actual_price,
            predicted_target_price=analysis.target_price,
            predicted_hold_days=capped_hold,
            predicted_direction=analysis.direction,
            sentiment_score=analysis.sentiment_score,
            confidence=analysis.confidence,
            sources_used=analysis.sources_used,
            sources_breakdown=sources_breakdown,
            mode="exploration" if config.exploration_mode else "normal",
        )
        self.journal.log_entry(
            ticker=ticker,
            price=actual_price,
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
        score_tag  = f" | Score {current_score:.0f} ({_score_mod.score_range})"
        return (
            f"[{ticker}] GEKAUFT – {shares} Stück @ ${actual_price:.2f} "
            f"| SL: ${stop_loss} | {tp_source} "
            f"| Haltedauer: {capped_hold}d "
            f"| Investiert: ${shares * actual_price:.2f}"
            f"{earn_tag}{margin_tag}{score_tag}"
        )

    def _do_close(self, ticker: str, pos: Position, price: float, reason: str, days_held: int = 0) -> float:
        if _is_crypto(ticker):
            fill = self.broker.sell_crypto(ticker, pos.shares)
        else:
            fill = self.broker.sell(ticker, pos.shares, price)
        ok, actual_price, warn = _check_fill(fill, ticker, "sell")
        if not ok:
            # SELL failed — log and keep position open; notifier alert so operator can intervene
            self._notifier.send(
                f"🚨 <b>{ticker} SELL-Order FEHLGESCHLAGEN</b> ({reason})\n"
                f"Position bleibt offen — bitte manuell im Broker prüfen!\n"
                f"Fehler: {warn}"
            )
            log.error("[%s] SELL failed (%s) – position kept open", ticker, warn)
            return 0.0
        if not actual_price:
            actual_price = price
        if warn:
            self._notifier.send(warn)
        self.tracker.record_outcome(
            ticker=ticker,
            entry_price=pos.entry_price,
            entry_date=pos.entry_date,
            sell_price=actual_price,
            sell_reason=reason,
        )
        pnl = self.portfolio.close_position(ticker, actual_price, reason)
        self.journal.log_exit(
            ticker=ticker, price=actual_price,
            entry_price=pos.entry_price,
            pnl=pnl, reason=reason,
            days_held=days_held,
        )
        # Einmalig letzten Trade lesen – wird an beide Methoden weitergegeben
        last_trade: Optional[dict] = None
        try:
            recent = self.tracker.get_recent_trades(n=1)
            last_trade = recent[0] if recent else {}
        except Exception:
            pass

        self._run_goal_risk_check()
        self._run_score_update(ticker, pos.entry_price, price, reason, last_trade)
        self._run_post_close_learning(ticker, pos.entry_price, price, reason, last_trade)

        # ── RL Agent: aus Trade-Ergebnis lernen ──────────────────────────────
        try:
            rl_state = self.rl_agent._state_from_entry({
                "sentiment":  last_trade.get("sentiment_score"),
                "confidence": last_trade.get("confidence"),
                "direction":  last_trade.get("direction"),
            })
            return_pct = (price - pos.entry_price) / pos.entry_price * 100
            planned_hold = int(last_trade.get("hold_days") or pos.target_hold_days or 14)
            reward = self.rl_agent.compute_reward(
                return_pct=return_pct,
                hold_days=days_held,
                planned_hold_days=planned_hold,
                stop_loss_triggered="stop" in reason.lower(),
            )
            self.rl_agent.record_outcome(rl_state, reward)
            log.info("[%s] RL-Update: return=%.1f%% reward=%+.4f", ticker, return_pct, reward)
        except Exception as _rl_exc:
            log.debug("RL record_outcome fehlgeschlagen: %s", _rl_exc)

        return pnl

    def _run_score_update(self, ticker: str, entry_price: float, exit_price: float, reason: str,
                          last_trade: Optional[dict] = None) -> None:
        """Bot-Score nach Trade aktualisieren und bei Meilensteinen benachrichtigen."""
        last_trade = last_trade or {}
        try:
            return_pct = (exit_price - entry_price) / entry_price * 100
            tier = 0
            try:
                tier = MarginTierTracker(self.tracker).get_active_tier().active_tier.level
            except Exception:
                pass

            conf_raw = last_trade.get("confidence") or "MEDIUM"
            confidence = conf_raw.upper()

            # Normalise reason string to match bot_scorer exit_reason keys
            exit_reason_key = reason.lower()
            if "stop" in exit_reason_key:
                exit_reason_key = "stop_loss"
            elif "take" in exit_reason_key or "profit" in exit_reason_key:
                exit_reason_key = "take_profit"
            elif "these" in exit_reason_key or "thesis" in exit_reason_key:
                exit_reason_key = "thesis_broken"
            elif "haltedauer" in exit_reason_key or "hold" in exit_reason_key:
                exit_reason_key = "hold_expired"
            elif "sentiment" in exit_reason_key:
                exit_reason_key = "sentiment_sell"

            scorer = BotScorer()
            delta, new_milestones, record_msgs = scorer.record_trade(
                ticker=ticker,
                return_pct=return_pct,
                confidence=confidence,
                exit_reason=exit_reason_key,
                current_tier=tier,
                tracker=self.tracker,
            )

            score = scorer.get()
            sign  = "+" if delta >= 0 else ""
            log.info("Score: %s%+.1f → %.1f/100 (%s)", sign, delta, score.current, score.label)

            if record_msgs:
                self._notifier.send(scorer.to_telegram_record(record_msgs))

            for milestone in new_milestones:
                self._notifier.send(scorer.to_telegram_milestone(milestone))

        except Exception as e:
            log.warning("Score-Update fehlgeschlagen: %s", e)

    def _run_post_close_learning(
        self, ticker: str, entry_price: float, exit_price: float, reason: str, last_trade: Optional[dict] = None
    ) -> None:
        """SentimentMemory + ReEntryTracker nach Trade-Abschluss aktualisieren."""
        last_trade = last_trade or {}
        return_pct = (exit_price - entry_price) / entry_price * 100
        confidence = (last_trade.get("confidence") or "MEDIUM").upper()

        # Sentiment-Verlässlichkeit pro Ticker lernen
        try:
            sentiment_at_entry = last_trade.get("sentiment_score")
            if sentiment_at_entry is not None:
                SentimentMemory().record(ticker, sentiment_at_entry, return_pct)
        except Exception as e:
            log.debug("SentimentMemory.record fehlgeschlagen: %s", e)

        # Verkaufte Position zur Re-Entry-Beobachtung hinzufügen
        try:
            ReEntryTracker().add_sold(
                ticker=ticker,
                sell_price=exit_price,
                sell_reason=reason,
                confidence=confidence,
            )
        except Exception as e:
            log.debug("ReEntryTracker.add_sold fehlgeschlagen: %s", e)

    def _run_goal_risk_check(self) -> None:
        """Nach jedem Trade: prüfe ob das Portfolio-Ziel noch erreichbar ist."""
        if not self.goal_risk or not self.goal_risk.active:
            return
        try:
            prices = self.broker.get_prices(list(self.portfolio.all_positions().keys()))
            portfolio_value = self.portfolio.total_value(prices)
            stats = self.tracker.get_accuracy_report()
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
        if pos.entry_price <= 0:
            return False
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

        fill = self.broker.buy(ticker, add_shares, current_price)
        ok, actual_add_price, warn = _check_fill(fill, ticker, "buy")
        if not ok:
            log.warning("[%s] Scale-In BUY fehlgeschlagen: %s", ticker, warn)
            return None
        if not actual_add_price:
            actual_add_price = current_price
        if warn:
            self._notifier.send(warn)
        # Durchschnittskurs berechnen
        new_total_shares = pos.shares + add_shares
        avg_price = (pos.shares * pos.entry_price + add_shares * actual_add_price) / new_total_shares
        pos.shares = new_total_shares
        pos.entry_price = round(avg_price, 4)

        self._notifier.send(
            f"📈 <b>Scale-In {ticker}</b>\n"
            f"+{add_shares} Stück @ ${actual_add_price:.2f}\n"
            f"Ø Einstieg jetzt: ${avg_price:.2f} | Gesamt: {new_total_shares:.2f} Stück"
        )
        return (
            f"[{ticker}] 📈 SCALE-IN +{add_shares} Stück @ ${actual_add_price:.2f} "
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
