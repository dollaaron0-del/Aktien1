from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from logger import get_logger
from portfolio.portfolio import Portfolio, Position
from portfolio.performance_tracker import PerformanceTracker
from portfolio.trade_journal import TradeJournal
from portfolio.signal_queue import SignalQueue
from analyzers.conditional_entry import ConditionalEntry, ConditionalEntryWatcher

log = get_logger(__name__)

# ── Selbstlern-Filter (lazy Singleton, fail-open) ─────────────────────
_entry_filter = None
_entry_filter_tried = False


def _get_entry_filter():
    """Lädt den EntryFilter einmalig. Gibt None zurück, wenn er nicht verfügbar
    ist (z.B. kein Kalibrierungsmodell) – der Aufrufer behandelt das fail-open."""
    global _entry_filter, _entry_filter_tried
    if _entry_filter_tried:
        return _entry_filter
    _entry_filter_tried = True
    try:
        from analyzers.entry_filter import EntryFilter
        _entry_filter = EntryFilter()
    except Exception as _e:  # pragma: no cover - defensiv
        log.debug("EntryFilter nicht verfügbar: %s", _e)
        _entry_filter = None
    return _entry_filter

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


def _is_fractional_asset(ticker: str) -> bool:
    """Assets, die in Bruchteilen handelbar sind (Krypto). Aktien nicht: die
    IBKR-API lehnt Teilaktien ab (Error 10243)."""
    upper = (ticker or "").upper()
    return "/" in upper or upper.endswith("-USD")


def _capital_scarcity_adjustment(cash_pct: float, max_adj: float, pivot_pct: float) -> float:
    """Aufschlag auf buy_threshold als durchgehende Exponentialkurve über die
    GESAMTE Spanne 0-100% Cash (kein Plateau an keinem Ende).

    Formel: max_adj * 2^(-cash_pct / pivot_pct)
    - cash_pct=0 (kein Cash mehr) → exakt max_adj (asymptotisches Maximum)
    - cash_pct=pivot_pct ("Kipppunkt") → exakt max_adj/2
    - cash_pct→∞ → geht glatt gegen 0, nie hart geklemmt

    Reine Funktion (kein self/Portfolio-Zugriff) - macht die Kurvenform
    unabhängig vom restlichen Strategie-Gerüst testbar."""
    if pivot_pct <= 0:
        return 0.0
    cash_pct = max(0.0, cash_pct)
    return max_adj * (2.0 ** (-cash_pct / pivot_pct))

# Congress×CEO-Confluence-Overlay: kaufen Exec UND Politiker unabhängig denselben
# Ticker (analyzers/insider_signal.InsiderScore.confluence), gilt das als seltene,
# wirklich unabhängige Bestätigung → Kaufschwelle leicht runter + Sizing leicht hoch.
# Bewusst klein gehalten; per ENV abschaltbar/justierbar. Fail-open (kein Confluence).
_CONFLUENCE_OVERLAY     = os.getenv("INSIDER_CONFLUENCE_OVERLAY", "1") not in ("0", "false", "False")
_CONFLUENCE_THR_RELIEF  = float(os.getenv("CONFLUENCE_THR_RELIEF", "0.03"))   # Schwelle −0.03
_CONFLUENCE_SIZE_MULT   = float(os.getenv("CONFLUENCE_SIZE_MULT", "1.15"))    # Größe ×1.15
_CONFLUENCE_THR_FLOOR   = float(os.getenv("CONFLUENCE_THR_FLOOR", "0.50"))    # nie unter 0.50

# ATR-Volatilitäts-Sizing: Positionsgröße auf gleiches Tages-Risiko normieren.
# Ruhige Aktie (kleines ATR%) → größere Position, wilde Aktie → kleinere, sodass
# der Dollar-Einsatz pro Trade ähnlich riskant ist. Multiplikator = Ziel-ATR% /
# aktuelles ATR%, hart gedeckelt. Per ENV abschaltbar; fail-open (Faktor 1.0).
_ATR_VOL_SIZING  = os.getenv("ATR_VOL_SIZING", "1") not in ("0", "false", "False")
_ATR_TARGET_PCT  = float(os.getenv("ATR_TARGET_PCT", "0.025"))   # "normale" Tages-ATR = 2.5%
_ATR_SIZE_FLOOR  = float(os.getenv("ATR_SIZE_FLOOR", "0.50"))    # wilde Aktie: nie unter ×0.5
_ATR_SIZE_CAP    = float(os.getenv("ATR_SIZE_CAP",  "1.50"))     # ruhige Aktie: nie über ×1.5

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
    # Lern-Filter-Verdikt zum Einstiegszeitpunkt, für den Hinterher-Vergleich
    # "hat der Filter recht behalten" beim Exit (analyzers/entry_filter.py).
    # Leer, wenn der Filter deaktiviert war oder kein Verdikt lieferte.
    entry_filter_verdict: str = ""
    entry_filter_p_win: float = 0.0
    entry_filter_edge: float = 0.0
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

        # 2b. Cross-Listing: dieselbe Firma steht schon unter einem anderen
        # Börsenkürzel im Buch (SAP vs. SAP.DE, ASML vs. ASML.AS …). Ohne
        # diesen Check bewertete der Bot das US-ADR als brandneue Firma und
        # kaufte dieselbe Firma ein zweites Mal – Sizing, Einzelpositions-
        # Deckel und Korrelations-Check sahen zwei getrennte Werte (real
        # passiert am 24.7.2026 mit SAP). Aufstocken bleibt möglich, aber
        # ausschließlich über den Ticker, unter dem die Position geführt wird:
        # der läuft im selben Zyklus durch _evaluate_existing (Scale-in).
        alias = self._held_under_other_listing(ticker)
        if alias:
            return StrategyResult(
                "SKIP", ticker,
                f"Firma bereits im Depot als {alias} (Cross-Listing) – "
                f"Aufstocken läuft über {alias}")

        # 3. New position evaluation
        return self._evaluate_new(ticker, analysis, current_price, params, regime, market_is_ranging, rl_agent, config)

    def _held_under_other_listing(self, ticker: str) -> Optional[str]:
        """Ticker einer offenen Position derselben Firma an einem anderen
        Handelsplatz – oder None. Fail-open: ohne Mapping kein Block."""
        try:
            from analyzers.stock_relations import canonical
            canon = canonical(ticker)
            for held in self.portfolio.all_positions():
                if held.upper() != ticker.upper() and canonical(held) == canon:
                    return held
        except Exception as e:
            log.debug("Cross-Listing-Prüfung übersprungen [%s]: %s", ticker, e)
        return None

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

    def _enqueue_signal(self, ticker, analysis, sentiment, confidence, direction, n_src) -> None:
        """Merkt ein an sich gültiges BUY-Signal vor, das nur an einer
        aktuellen Kapazitätsgrenze scheitert (Max-Positionen ODER Cash-Reserve-
        Boden erreicht) - nicht an der Sentiment-Schwelle selbst. Gemeinsamer
        Helper für beide Fälle, damit ein starkes Signal nicht ersatzlos
        verpufft: die Queue drained automatisch stündlich und sofort nach
        jedem SL/TP-Exit (bot/scheduler_risk.py: sl_tp_check_job →
        signal_queue_job), also genau dann, wenn wieder Slots/Kapital frei
        werden. enqueue() dedupliziert selbst (überschreibt einen älteren
        pending-Eintrag desselben Tickers)."""
        if not self.signal_queue:
            return
        self.signal_queue.enqueue(
            ticker=ticker,
            sentiment_score=sentiment,
            confidence=confidence,
            target_price=getattr(analysis, "target_price", None),
            direction=direction,
            entry_rationale=getattr(analysis, "entry_rationale", "") or "",
            key_catalysts=list(getattr(analysis, "key_catalysts", []) or []),
            risk_factors=list(getattr(analysis, "risk_factors", []) or []),
            sources_used=n_src,
            sources_breakdown=getattr(analysis, "sources_breakdown", {}) or {},
            suggested_hold_days=int(getattr(analysis, "suggested_hold_days", 0) or 0),
        )

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
        except Exception as _e:
            log.debug("Makro-Bias-Threshold übersprungen [%s]: %s", ticker, _e)

        # Kapitalknappheits-Aufschlag: sinkt das frei verfügbare Cash (% der
        # Equity), steigt die Kaufschwelle - asymmetrisch wie der Makro-
        # Aufschlag (nur strenger, nie lockerer). Verhindert, dass eine
        # Kaufwelle das Kapital für Tage komplett bindet, weil auch
        # mittelmäßige Signale noch durchgehen, bis das Cash-Polster abrupt am
        # Reserve-Boden aufschlägt (Auslöser 25.7.2026: 6+ Tage
        # handlungsunfähig nach einer Kaufwoche).
        #
        # Durchgehende Exponentialkurve ohne harte Stufen (User-Präzisierung
        # 25.7.2026: "schon ab 100% fängt das an, nur wenig am Anfang, ab
        # einem Kipppunkt steiler - keine harten Grenzen"). Eine erste Version
        # hatte noch feste Plateaus (0 oberhalb 20% Cash, Maximum unterhalb 5%)
        # mit einer Kurve nur dazwischen - genau die harte Grenze, die hier
        # vermieden werden soll. Jetzt gilt für JEDEN Cash-Stand von 0-100%:
        #
        #   Aufschlag = max_adj * 2^(-cash_pct / pivot_pct)
        #
        # Exponentieller Zerfall über die GESAMTE Spanne: bei cash_pct=0 exakt
        # max_adj (asymptotisches Maximum, kein Clamp), bei cash_pct=pivot_pct
        # ("Kipppunkt") genau die Hälfte von max_adj, bei viel Cash strebt der
        # Aufschlag glatt gegen 0 (bei 100% Cash + Default-Pivot 10%: ~0,0001 -
        # praktisch nicht spürbar, aber nie hart auf 0 geklemmt).
        if getattr(config, "capital_scarcity_threshold_enabled", True):
            try:
                equity = self.portfolio.total_value({})
                cash_pct = (self.portfolio.cash / equity) if equity > 0 else 0.0
                pivot   = float(getattr(config, "capital_scarcity_pivot_pct", 0.10))
                max_adj = float(getattr(config, "capital_scarcity_max_adj", 0.15))
                adj = _capital_scarcity_adjustment(cash_pct, max_adj, pivot)
                threshold += adj
                if adj >= 0.005:  # Log-Rauschen vermeiden bei kaum spürbaren Beträgen
                    log.info(
                        "[%s] Kapitalknappheit (Cash %.1f%% der Equity) → "
                        "Schwelle +%.3f auf %.3f",
                        ticker, cash_pct * 100, adj, threshold,
                    )
            except Exception as _e:
                log.debug("Kapitalknappheits-Schwelle übersprungen [%s]: %s", ticker, _e)

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

        # Congress×CEO-Confluence: einmal ziehen (erst hier, nachdem Bullish-Signal
        # feststeht – kein Netzaufruf für SKIP-Kandidaten). Senkt die Kaufschwelle
        # leicht ab (asymmetrisch wie der Makro-Aufschlag, nur in Gegenrichtung) und
        # wird unten ans Sizing weitergereicht. Floor verhindert Über-Absenkung.
        confluence = self._has_insider_confluence(ticker)
        if confluence and _CONFLUENCE_THR_RELIEF > 0:
            threshold = max(_CONFLUENCE_THR_FLOOR, threshold - _CONFLUENCE_THR_RELIEF)
            log.info("[%s] Insider-Confluence (Exec×Congress) → Schwelle %.2f", ticker, threshold)

        if sentiment < threshold:
            return StrategyResult("SKIP", ticker, f"Sentiment {sentiment:.2f} < Schwelle {threshold:.2f}")

        # Informationsdichte-Boden: nicht auf zu dünner Quellenlage handeln.
        # (Beim 7.6.-Umbau verloren gegangen – hier wiederhergestellt.)
        min_src = config.min_sources
        _su = getattr(analysis, "sources_used", 0)
        # sources_used ist je nach Analyzer-Pfad int ODER Dict[str,int] (Quelle→Anzahl).
        if isinstance(_su, dict):
            n_src = sum(int(v or 0) for v in _su.values())
        else:
            try:
                n_src = int(_su or 0)
            except (TypeError, ValueError):
                n_src = 0
        if n_src < min_src:
            return StrategyResult("SKIP", ticker, f"Zu wenige Quellen ({n_src} < {min_src}) – übersprungen")

        # Max positions (dynamisch nach Portfolio-Größe via FocusController –
        # config.max_positions existiert nicht; früher self.focus.get_max_positions).
        n_pos = len(self.portfolio.all_positions())
        try:
            max_pos = self.focus_ctrl.get_max_positions(self.portfolio.total_value({}))
        except Exception:
            max_pos = 12
        if n_pos >= max_pos:
            self._enqueue_signal(ticker, analysis, sentiment, confidence, direction, n_src)
            return StrategyResult("SKIP", ticker, f"Max Positionen ({max_pos}) erreicht – Signal in Queue")

        # Earnings filter
        if self.earnings_filter and self.earnings_filter.check(ticker).get("block"):
            return StrategyResult("SKIP", ticker, "Earnings-Sperre aktiv")

        # SL-Cooldown: nach verlustigem Stop-Loss N Tage keinen Re-Entry
        # (config.sl_cooldown_days; Pendant zur Backtest-Regel in
        # backtesting/engine.py). Fail-open bei Lese-/Importfehlern.
        try:
            from analyzers.sl_cooldown import StopLossCooldown
            _sl_blocked, _sl_why = StopLossCooldown().is_blocked(ticker)
            if _sl_blocked:
                return StrategyResult("SKIP", ticker, _sl_why)
        except Exception as _e:
            log.debug("SL-Cooldown-Check übersprungen [%s]: %s", ticker, _e)

        # Liquiditäts-Gate: nicht in untradeable Small-Caps kaufen (Ø-Dollar-
        # Volumen). Fail-open bei Datenausfall (siehe analyzers/liquidity).
        try:
            from analyzers.liquidity import check_liquidity
            liq = check_liquidity(ticker, current_price)
            if not liq.ok:
                return StrategyResult("SKIP", ticker, liq.reason)
        except Exception as _e:
            log.debug("Liquiditäts-Gate übersprungen [%s]: %s", ticker, _e)

        # Position sizing (VOR dem Korrelations-Check nötig: der braucht die
        # geplante Investitionssumme, die es vorher noch nicht gibt).
        position_value = self._calc_position_size(
            analysis, current_price, params, config, confluence, sentiment, threshold)
        if position_value <= 0:
            # Kapitalmangel (Cash-Reserve-Boden erreicht), nicht ein schwaches
            # Signal – Grund, warum Position sizing = 0 werden kann, obwohl das
            # Signal die Schwelle bereits übersprungen hat. Ohne Queue verpufft
            # ein starkes Signal (z.B. Score 0.9) hier ersatzlos: anders als der
            # Max-Positionen-Fall wurde dieser Skip bislang NICHT vorgemerkt.
            # In die Queue statt zu verlieren – dieselbe Infrastruktur wie beim
            # Max-Positionen-Fall drained automatisch stündlich UND sofort nach
            # jedem SL/TP-Exit (also genau dann, wenn Kapital frei wird).
            self._enqueue_signal(ticker, analysis, sentiment, confidence, direction, n_src)
            return StrategyResult("SKIP", ticker, "Positionsgröße = 0 (Kapital knapp) – Signal in Queue")

        # Correlation check
        if self.correlation_checker:
            positions = self.portfolio.all_positions()
            existing_value = {t: p.shares * p.entry_price for t, p in positions.items()}
            corr = self.correlation_checker.can_open(
                ticker, position_value, self.portfolio.total_value({}), existing_value,
            )
            if not corr.get("allowed"):
                return StrategyResult("SKIP", ticker, corr.get("reason") or "Zu hohe Sektor-Korrelation")

        # Selbstlern-Filter: konsultiert das gelernte Kalibrierungsmodell.
        # AVOID → SKIP (wenn block aktiv), CAUTION → kleinere Position. Fail-open.
        # `_ef_verdict`/`_ef_pwin`/`_ef_edge` wandern unten in den StrategyResult,
        # damit der Executor sie an die Position hängen kann — Grundlage für den
        # Hinterher-Vergleich "hat der Filter recht behalten" beim Exit.
        _ef_verdict, _ef_pwin, _ef_edge = "", 0.0, 0.0
        if getattr(config, "learning_filter_enabled", False):
            ef = _get_entry_filter()
            if ef is not None:
                try:
                    verdict = ef.evaluate({
                        "ticker": ticker,
                        "sentiment_score": sentiment,
                        "confidence": confidence,
                        "debate_winner": getattr(analysis, "debate_winner", "") or "",
                        "regime": str(regime) if regime else "",
                    })
                    _ef_verdict, _ef_pwin, _ef_edge = (
                        verdict.verdict, verdict.p_win, verdict.expected_edge)
                    if verdict.verdict == "AVOID":
                        if getattr(config, "learning_filter_block", True):
                            return StrategyResult(
                                "SKIP", ticker,
                                f"Lern-Filter AVOID (Edge {verdict.expected_edge:+.2f}%, "
                                f"P(Win) {verdict.p_win:.0%})")
                        # Block deaktiviert (User-Entscheidung 22.7.2026: mehr Trades
                        # für echte Lern-Daten statt Datenmangel) — AVOID kauft dann
                        # trotzdem, aber mit derselben verkleinerten Größe wie CAUTION
                        # (bisher lief AVOID hier ungebremst in VOLLER Größe durch —
                        # Lücke zwischen Doku-Kommentar oben und Code).
                        mult = float(getattr(config, "learning_filter_caution_size_mult", 0.5))
                        position_value *= max(0.0, min(1.0, mult))
                        log.info("[%s] Lern-Filter AVOID (Block aus) → Position ×%.2f", ticker, mult)
                    elif verdict.verdict == "CAUTION":
                        mult = float(getattr(config, "learning_filter_caution_size_mult", 0.5))
                        position_value *= max(0.0, min(1.0, mult))
                        log.info("[%s] Lern-Filter CAUTION → Position ×%.2f", ticker, mult)
                    elif verdict.verdict == "PROCEED":
                        # Zweiseitig: klar positive gelernte Kante darf die Position auch
                        # vergrößern (Default 1.0 = aus). Gedeckelt, damit der Filter nie
                        # exzessiv hebelt.
                        pmult = float(getattr(config, "learning_filter_proceed_size_mult", 1.0))
                        if pmult != 1.0:
                            position_value *= max(1.0, min(2.0, pmult))
                            log.info("[%s] Lern-Filter PROCEED → Position ×%.2f", ticker, pmult)
                except Exception as _ef_err:
                    log.debug("Lern-Filter übersprungen [%s]: %s", ticker, _ef_err)

        # RLAgent-Veto: separat trainierter linearer Policy-Agent (eigenes Paradigma,
        # parallel zum EntryFilter). Nur aktiv, wenn der Runner einen rl_agent durchreicht
        # (config.rl_veto_enabled). should_buy → (kaufen?, modifier 0.5–1.25): negativer
        # Policy-Score ⇒ SKIP, sonst zweiseitige Size-Anpassung. Fail-open.
        if rl_agent is not None:
            try:
                from analyzers.rl_agent import RLState
                state = RLState(
                    sentiment_score=max(0.0, min(1.0, float(sentiment or 0.0))),
                    vix_level=0.3,        # neutraler Default (konsistent mit Trainings-State)
                    momentum_5d=0.0,
                    news_velocity=0.5,
                    confidence_encoded=RLState.encode_confidence(str(confidence or "MEDIUM")),
                    regime_encoded=RLState.encode_regime(str(regime or "NEUTRAL")),
                )
                rl_buy, rl_mod = rl_agent.should_buy(state)
                if not rl_buy:
                    return StrategyResult(
                        "SKIP", ticker, "RL-Agent-Veto (negativer Policy-Score)")
                position_value *= max(0.25, min(1.5, float(rl_mod)))
                log.info("[%s] RL-Agent OK → Position ×%.2f", ticker, rl_mod)
            except Exception as _rl_err:
                log.debug("RL-Veto übersprungen [%s]: %s", ticker, _rl_err)

        shares = position_value / current_price

        # Aktien-Orders unter 1 Stück kann die IBKR-API nicht ausführen. Ohne
        # diesen Guard ging die Order trotzdem raus, scheiterte beim Broker und
        # löste einen lauten "BUY-Order fehlgeschlagen"-Alarm aus, obwohl nur
        # das Budget zu klein war (24.7.2026: SAP mit 0,86 Stück, weil der
        # Cash-Reserve-Boden fast erreicht war). Krypto handelt Bruchteile und
        # bleibt ausgenommen.
        if shares < 1 and not _is_fractional_asset(ticker):
            return StrategyResult(
                "SKIP", ticker,
                f"Positionsgröße {shares:.2f} Stück < 1 bei Kurs {current_price:.2f} "
                f"(einsetzbares Budget {position_value:.0f}) – keine Teilaktien handelbar")

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
            except Exception as _e:
                log.debug("RL-Agent-Veto übersprungen [%s]: %s", ticker, _e)

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
            entry_filter_verdict=_ef_verdict,
            entry_filter_p_win=_ef_pwin,
            entry_filter_edge=_ef_edge,
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
        days_held = (datetime.now(timezone.utc).replace(tzinfo=None) - entry_dt).days
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
                    new_sl = round(new_sl, 4)
                    self.portfolio.update_position_state(ticker, stop_loss=new_sl)
                    self._sync_broker_stop(ticker, pos.shares, new_sl)
                break

    def _sync_broker_stop(self, ticker: str, shares: float, stop_price: float) -> None:
        """Broker-seitigen GTC-Schutz-Stop (Roadmap 1.9) auf den frisch
        angehobenen Trailing-SL nachziehen (Roadmap 1.12) – sonst bliebe der
        Backstop bei Bot-Ausfall auf dem alten, niedrigeren Niveau stehen statt
        mit der Bot-eigenen Stufen-Logik mitzuwandern. Nur bei einem Broker mit
        update_stop()-API (IBKR); PaperBroker hat keine offene Order zu pflegen."""
        upd = getattr(self.broker, "update_stop", None)
        if not callable(upd):
            return
        try:
            upd(ticker, shares, stop_price)
        except Exception as e:
            log.warning("[%s] Broker-Stop-Nachführung (Trailing) fehlgeschlagen: %s", ticker, e)

    # ── Insider-Confluence ────────────────────────────────────────────────────

    @staticmethod
    def _has_insider_confluence(ticker: str) -> bool:
        """True, wenn Exec UND Congress denselben Ticker gekauft haben.

        Ein einziger Netzaufruf pro BUY-Kandidat (nicht pro Zyklus-Ticker), da
        nur für bereits bullishe Kaufkandidaten aufgerufen. Fail-open: jeder
        Fehler/Datenausfall ⇒ False (kein Bonus, nie ein Block).
        """
        if not _CONFLUENCE_OVERLAY:
            return False
        try:
            from analyzers.insider_signal import get_insider_score
            return bool(get_insider_score(ticker, lookback_days=30).confluence)
        except Exception as _e:
            log.debug("Insider-Confluence übersprungen [%s]: %s", ticker, _e)
            return False

    @staticmethod
    def _atr_vol_multiplier(ticker: str, current_price: float, broker=None) -> float:
        """Volatilitäts-Normierung der Positionsgröße über ATR(14).

        Gleicher Dollar-Einsatz pro Trade unabhängig von der Tagesvolatilität:
        Multiplikator = Ziel-ATR% / aktuelles ATR%, hart gedeckelt auf
        [_ATR_SIZE_FLOOR, _ATR_SIZE_CAP]. Ein einziger yfinance-Aufruf pro
        BUY-Kandidat. Fail-open: fehlendes/ungültiges ATR ⇒ 1.0 (kein Eingriff).
        """
        if not _ATR_VOL_SIZING:
            return 1.0
        try:
            from analyzers.technical_indicators import TechnicalIndicators
            kwargs = {"broker": broker} if broker is not None else {}
            snap = TechnicalIndicators().calculate(ticker, **kwargs)
            atr   = getattr(snap, "atr_14", None) if snap else None
            price = (getattr(snap, "price", None) if snap else None) or current_price
            if not atr or not price or price <= 0:
                return 1.0
            atr_pct = atr / price
            if atr_pct <= 0:
                return 1.0
            mult = _ATR_TARGET_PCT / atr_pct
            return max(_ATR_SIZE_FLOOR, min(_ATR_SIZE_CAP, mult))
        except Exception as _e:
            log.debug("ATR-Vol-Sizing übersprungen [%s]: %s", ticker, _e)
            return 1.0

    # ── Position sizing ─────────────────────────────────────────────────────

    def _calc_position_size(
        self, analysis, current_price: float, params, config, confluence: bool = False,
        sentiment: float = 0.0, threshold: float = 0.0,
    ) -> float:
        """Calculate dollar position size."""
        # Basis ist ein Anteil der GESAMT-EQUITY (Cash + Positionswert), nicht
        # nur des Rest-Cash. Sonst schrumpfen spätere Käufe im selben Zyklus,
        # weil das Cash mit jedem Buy sinkt. total_value({}) bewertet bestehende
        # Positionen mangels Live-Preisen mit dem Einstandspreis – stabile,
        # ausreichende Schätzung für das Sizing. Der Cash-Reserve-Boden weiter
        # unten (_deployable_cash) verhindert weiterhin ein Überziehen.
        equity = self.portfolio.total_value({})
        base = equity * config.max_position_pct

        # Regime multiplier
        base *= params.position_size_mult

        # Konviction-Multiplier: Confidence-Basis (dämpft) PLUS Sentiment-
        # Überschuss über der Kaufschwelle (hebt an). Anders als früher kann das
        # Ergebnis >1.0 werden → starke Signale bekommen mehr Kapital. Die harte
        # Einzelpositions-Grenze unten deckelt die Freiheit.
        confidence = getattr(analysis, "confidence", "MEDIUM")
        conf_base = _CONFIDENCE_SIZING.get(confidence, 0.70)
        margin = max(0.0, float(sentiment) - float(threshold))
        # 0.15 Sentiment-Überschuss ≈ voller Bonus (linear gedeckelt).
        sent_bonus = min(margin / 0.15, 1.0) * float(getattr(config, "conviction_max_bonus", 0.6))
        base *= conf_base * (1.0 + sent_bonus)

        # Kelly sizing (optional)
        if self.kelly_sizer:
            try:
                kelly_mult = self.kelly_sizer.fraction
                base = min(base, self.portfolio.cash * kelly_mult)
            except Exception as _e:
                log.debug("Kelly-Sizing übersprungen: %s", _e)

        # Goal risk
        if self.goal_risk_assessor:
            try:
                risk_mult = self.goal_risk_assessor.position_size_multiplier()
                base *= risk_mult
            except Exception as _e:
                log.debug("Goal-Risk-Sizing übersprungen: %s", _e)

        # Makro-Modifier (VIX × Makro-Kalender × Sektor-Rotation).
        # Regime ist bereits über params.position_size_mult abgedeckt – hier
        # kommen die übrigen, bislang ungenutzten Makro-Faktoren hinzu.
        try:
            from analyzers.macro_context import get_macro_context
            ticker = getattr(analysis, "ticker", "") or ""
            base *= get_macro_context().size_modifier(ticker)
        except Exception as _e:
            log.debug("Makro-Size-Modifier übersprungen: %s", _e)

        # ATR-Volatilitäts-Normierung: gleiches Tages-Risiko pro Trade, egal ob
        # ruhige oder wilde Aktie. Helper ist fail-open (Faktor 1.0 bei fehlendem
        # ATR), daher ohne zusätzlichen Guard direkt multipliziert.
        ticker = getattr(analysis, "ticker", "") or ""
        base *= self._atr_vol_multiplier(ticker, current_price, broker=getattr(self, "broker", None))

        # Congress×CEO-Confluence-Bonus (Flag aus _evaluate_new durchgereicht –
        # kein zweiter Netzaufruf). Leichter Größen-Aufschlag; durch den Einzel-
        # Deckel und den Cash-Reserve-Boden unten weiterhin gedeckelt.
        if confluence and _CONFLUENCE_SIZE_MULT != 1.0:
            base *= _CONFLUENCE_SIZE_MULT

        # Min/max guardrails
        base = max(base, 10.0)
        # Harte Einzelpositions-Obergrenze (% der Gesamt-Equity): begrenzt die
        # Konviction-Freiheit nach oben, egal wie stark das Signal ist.
        base = min(base, equity * float(getattr(config, "max_single_position_pct", 0.25)))
        # Reserve-bewusste Kaufkraft: nie so viel kaufen, dass der Cash-Boden
        # unterschritten wird (hält den Bot handlungsfähig). Ersetzt die alte
        # 40%-pro-Trade-Regel, die Konviction-Käufe unnötig gedeckelt hätte.
        base = min(base, self._deployable_cash(equity, config))

        return round(max(base, 0.0), 2)

    # ── Liquiditäts-Steuerung ────────────────────────────────────────────────

    def _deployable_cash(self, equity: float, config) -> float:
        """
        Einsetzbares Cash unter Beachtung des Reserve-Bodens.

        Normal wird der Soft-Boden (cash_reserve_pct) frei gehalten. Ist
        Rückfluss-Timing aktiv und wird bald Kapital frei, darf sich der Bot
        Richtung hartem Boden (cash_reserve_hard_pct) lehnen – nie darunter.
        """
        cash = self.portfolio.cash
        soft = equity * float(getattr(config, "cash_reserve_pct", 0.10))
        hard = equity * min(
            float(getattr(config, "cash_reserve_hard_pct", 0.05)),
            float(getattr(config, "cash_reserve_pct", 0.10)),
        )
        floor = soft
        if getattr(config, "reflow_sizing_enabled", True) and soft > hard:
            reflow = self._capital_freeing_soon(int(getattr(config, "reflow_lookahead_days", 5)))
            # Anteil der Reserve-Lücke, den der nahe Rückfluss "abdeckt".
            lean = max(0.0, min(reflow / soft, 1.0)) if soft > 0 else 0.0
            floor = soft - lean * (soft - hard)
        return max(0.0, cash - floor)

    def _capital_freeing_soon(self, days: int) -> float:
        """Kostenbasis der Positionen, deren erwarteter Exit binnen `days` liegt."""
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cutoff = now + timedelta(days=max(0, days))
            total = 0.0
            for pos in self.portfolio.all_positions().values():
                exit_dt = self._expected_exit(pos)
                if exit_dt is not None and exit_dt <= cutoff:
                    total += float(pos.shares) * float(pos.entry_price)
            return total
        except Exception as _e:  # fail-safe: kein Rückfluss angenommen → Soft-Boden bleibt
            log.debug("Rückfluss-Schätzung übersprungen: %s", _e)
            return 0.0

    @staticmethod
    def _expected_exit(pos):
        """Erwartetes Freigabe-Datum = Einstieg + target_hold_days (fail-open)."""
        raw = str(getattr(pos, "entry_date", "") or "")
        if not raw:
            return None
        dt = None
        try:
            dt = datetime.fromisoformat(raw[:19])
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(raw[:len("2026-01-01 00:00:00") if " " in raw else 10], fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
        return dt + timedelta(days=max(0, int(getattr(pos, "target_hold_days", 0) or 0)))

    # ── Circuit breaker ─────────────────────────────────────────────────────

    def _circuit_breaker_active(self, config) -> bool:
        """Returns True if daily loss limit exceeded."""
        today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
        if self._daily_loss_date != today:
            self._daily_loss_usd = 0.0
            self._daily_loss_date = today
        limit = getattr(config, "circuit_breaker_loss_pct", 0.05) * self.portfolio.cash
        return self._daily_loss_usd >= limit

    def record_loss(self, pnl: float):
        """Record a realized loss for circuit-breaker tracking."""
        if pnl < 0:
            today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
            if self._daily_loss_date != today:
                self._daily_loss_usd = 0.0
                self._daily_loss_date = today
            self._daily_loss_usd += abs(pnl)
