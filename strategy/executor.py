"""
TradeExecutor – führt die Entscheidungen der SwingStrategy aus.

Die SwingStrategy ist seit dem Umbau vom 7.6.2026 eine reine Entscheidungs-
Engine: `evaluate()`/`check_exits()` liefern `StrategyResult`-Objekte
(BUY/SELL/HOLD/SKIP), platzieren aber selbst keine Orders. Diese Klasse
schließt die Lücke: sie nimmt ein StrategyResult, platziert die Broker-Order,
bucht die Position, benachrichtigt und journalisiert.

Sie gibt einen menschenlesbaren String zurück ("[TICKER] GEKAUFT …" /
"… VERKAUFT …" / "… übersprungen"), damit die bestehende runner-Logik
(Farben, Tages-Zusammenfassung, Prediction-Tracking) unverändert weiterläuft.

Wichtige Ausführungs-Feinheiten der Engine-Ergebnisse:
- BUY bei bereits offener Position = Scale-in. Es gibt (noch) keine
  Portfolio-Add-API, und open_position() würde die bestehende Position via
  INSERT OR REPLACE überschreiben → Scale-in wird sicher übersprungen (HOLD).
- SELL mit "Partial-TP" im Grund: die Engine hat das Portfolio-Buch in
  _check_partial_tp bereits aktualisiert. Hier nur die Broker-Order auslösen,
  NICHT erneut close_position(). Rückgabe enthält bewusst NICHT "VERKAUFT",
  damit runner die offene Vorhersage nicht fälschlich als Outcome schließt.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from logger import get_logger
from portfolio.portfolio import Position
from strategy.swing_strategy import StrategyResult

log = get_logger(__name__)


def _is_filled(fill) -> bool:
    return isinstance(fill, dict) and fill.get("status") == "filled"


def _fill_price(fill, fallback: float) -> float:
    if isinstance(fill, dict):
        fp = fill.get("fill_price") or fill.get("market_price")
        if fp:
            return float(fp)
    return float(fallback)


def _fail_reason(fill) -> str:
    """Fehlergrund aus einem nicht-gefüllten Fill (IBKR nutzt 'reason',
    Paper/Alpaca ggf. 'error'; sonst Roh-Status)."""
    if isinstance(fill, dict):
        return str(fill.get("reason") or fill.get("error") or fill.get("status") or "unbekannt")
    return str(fill)


class TradeExecutor:
    def __init__(self, portfolio, broker, journal=None, notifier=None):
        self.portfolio = portfolio
        self.broker = broker
        self.journal = journal
        self._notifier = notifier  # lazy: erst bei Bedarf erstellen

    @property
    def notifier(self):
        if self._notifier is None:
            try:
                from notifier.telegram_notifier import TelegramNotifier
                self._notifier = TelegramNotifier()
            except Exception:
                self._notifier = _NullNotifier()
        return self._notifier

    # ── Public ──────────────────────────────────────────────────────────────
    def execute(
        self,
        result: Optional[StrategyResult],
        analysis=None,
        sources_breakdown: Optional[dict] = None,
        days_held: int = 0,
    ) -> Optional[str]:
        if result is None:
            return None
        action = (result.action or "").upper()
        if action == "BUY":
            return self._execute_buy(result, analysis, sources_breakdown)
        if action == "SELL":
            return self._execute_sell(result, days_held)
        if action == "HOLD":
            return f"[{result.ticker}] {result.reason}"
        # SKIP (oder unbekannt)
        return f"[{result.ticker}] {result.reason} – übersprungen" if result.reason else None

    # ── BUY ─────────────────────────────────────────────────────────────────
    def _execute_buy(self, result, analysis, sources_breakdown) -> str:
        ticker = result.ticker

        # Scale-in (Position existiert bereits) wird sicher übersprungen –
        # open_position() würde die bestehende Position überschreiben.
        if self.portfolio.get_position(ticker) is not None:
            log.info("[%s] BUY-Signal bei offener Position (Scale-in) – nicht ausgeführt", ticker)
            return f"[{ticker}] Scale-in erkannt – kein Add-Mechanismus, gehalten"

        shares = result.shares
        if shares <= 0:
            return f"[{ticker}] Positionsgröße = 0 – übersprungen"

        fill = self.broker.buy(ticker, shares, result.price)
        if not _is_filled(fill):
            warn = _fail_reason(fill)
            return f"[{ticker}] ⛔ BUY-Order fehlgeschlagen: {warn}"
        actual_price = _fill_price(fill, result.price)

        # SL/TP auf den tatsächlichen Fill-Preis nachziehen, falls abgewichen.
        stop_loss = result.stop_loss
        take_profit = result.take_profit
        if actual_price and result.price and actual_price != result.price and result.price > 0:
            ratio = actual_price / result.price
            stop_loss = round(result.stop_loss * ratio, 4) if result.stop_loss else 0.0
            take_profit = round(result.take_profit * ratio, 4) if result.take_profit else 0.0

        catalysts = list(getattr(analysis, "key_catalysts", []) or [])[:5]
        position = Position(
            ticker=ticker,
            shares=shares,
            entry_price=actual_price,
            entry_date=datetime.utcnow().isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=result.hold_days,
            rationale=result.reason or getattr(analysis, "entry_rationale", "") or "",
            entry_catalysts=catalysts,
        )
        try:
            self.portfolio.open_position(position)
        except ValueError as ve:
            # z.B. nicht genug Cash – Broker-Order müsste eigentlich auch scheitern,
            # aber im Paper-Modus füllt sie immer → defensiv abfangen.
            log.warning("[%s] open_position fehlgeschlagen nach Fill: %s", ticker, ve)
            return f"[{ticker}] ⛔ Buchung fehlgeschlagen: {ve}"

        self._notify_buy(ticker, shares, actual_price, stop_loss, take_profit, result.hold_days,
                         analysis, sources_breakdown)
        self._journal_entry(ticker, actual_price, result.hold_days, take_profit, analysis, sources_breakdown)

        return (
            f"[{ticker}] GEKAUFT – {shares:.4f} Stück @ ${actual_price:.2f} "
            f"| SL: ${stop_loss:.2f} | TP: ${take_profit:.2f} "
            f"| Haltedauer: {result.hold_days}d "
            f"| Investiert: ${shares * actual_price:.2f}"
        )

    # ── SELL ────────────────────────────────────────────────────────────────
    def _execute_sell(self, result, days_held) -> str:
        ticker = result.ticker
        is_partial = "Partial-TP" in (result.reason or "")
        pos = self.portfolio.get_position(ticker)

        if pos is None and not is_partial:
            return f"[{ticker}] SELL-Signal, aber keine offene Position"

        # Partial-TP: Buch wurde von der Engine bereits aktualisiert – nur die
        # Broker-Order auslösen, NICHT close_position().
        if is_partial:
            sell_shares = result.shares
            fill = self.broker.sell(ticker, sell_shares, result.price)
            if not _is_filled(fill):
                warn = _fail_reason(fill)
                self.notifier.send(
                    f"🚨 <b>{ticker} Partial-TP Broker-Order FEHLGESCHLAGEN</b>\n"
                    f"Buch wurde bereits reduziert – bitte Broker manuell prüfen! ({warn})"
                )
                log.error("[%s] Partial-TP Broker-Sell fehlgeschlagen: %s", ticker, warn)
            actual_price = _fill_price(fill, result.price)
            self.notifier.send(
                f"📊 <b>{ticker} Partial-TP</b>: {sell_shares:.4f} Stück @ ${actual_price:.2f}\n{result.reason}"
            )
            # Bewusst KEIN "VERKAUFT" → runner schließt die Vorhersage nicht.
            return f"[{ticker}] 📊 Partial-TP: {sell_shares:.4f} @ ${actual_price:.2f} | {result.reason}"

        # Voll-Exit
        sell_shares = pos.shares
        fill = self.broker.sell(ticker, sell_shares, result.price)
        if not _is_filled(fill):
            warn = _fail_reason(fill)
            self.notifier.send(
                f"🚨 <b>{ticker} SELL-Order FEHLGESCHLAGEN</b> ({result.reason})\n"
                f"Position bleibt offen – bitte manuell im Broker prüfen!\nFehler: {warn}"
            )
            log.error("[%s] SELL fehlgeschlagen (%s) – Position bleibt offen", ticker, warn)
            return f"[{ticker}] ⛔ SELL-Order fehlgeschlagen: {warn}"
        actual_price = _fill_price(fill, result.price)

        pnl = self.portfolio.close_position(ticker, actual_price, result.reason)
        self._notify_sell(ticker, sell_shares, actual_price, pos, pnl, result.reason, days_held)
        self._journal_exit(ticker, actual_price, pos.entry_price, pnl, result.reason, days_held)

        sign = "+" if pnl >= 0 else ""
        return f"[{ticker}] VERKAUFT – {result.reason} | P&L: {sign}{pnl:.2f} USD"

    # ── Notify / Journal (best effort) ────────────────────────────────────────
    def _notify_buy(self, ticker, shares, price, sl, tp, hold_days, analysis, sources_breakdown):
        try:
            self.notifier.notify_buy(
                ticker=ticker, shares=shares, price=price, stop_loss=sl, take_profit=tp,
                hold_days=hold_days,
                rationale=getattr(analysis, "entry_rationale", "") or "",
                sentiment_score=float(getattr(analysis, "sentiment_score", 0.0) or 0.0),
                confidence=getattr(analysis, "confidence", "MEDIUM") or "MEDIUM",
                direction=getattr(analysis, "direction", "BULLISH") or "BULLISH",
                target_price=getattr(analysis, "target_price", None),
                key_catalysts=list(getattr(analysis, "key_catalysts", []) or [])[:4],
                risk_factors=list(getattr(analysis, "risk_factors", []) or [])[:3],
                sources_breakdown=sources_breakdown,
            )
        except Exception as e:
            log.debug("notify_buy fehlgeschlagen [%s]: %s", ticker, e)

    def _notify_sell(self, ticker, shares, price, pos, pnl, reason, days_held):
        try:
            self.notifier.notify_sell(
                ticker=ticker, shares=shares, price=price, entry_price=pos.entry_price,
                pnl=pnl, reason=reason, days_held=days_held,
                target_hold_days=pos.target_hold_days,
                entry_catalysts=list(getattr(pos, "entry_catalysts", []) or []),
                entry_rationale=getattr(pos, "rationale", "") or "",
            )
        except Exception as e:
            log.debug("notify_sell fehlgeschlagen [%s]: %s", ticker, e)

    def _journal_entry(self, ticker, price, hold_days, target_price, analysis, sources_breakdown):
        if not self.journal:
            return
        try:
            self.journal.log_entry(
                ticker=ticker, price=price,
                sentiment=float(getattr(analysis, "sentiment_score", 0.0) or 0.0),
                confidence=getattr(analysis, "confidence", "MEDIUM") or "MEDIUM",
                direction=getattr(analysis, "direction", "BULLISH") or "BULLISH",
                rationale=getattr(analysis, "entry_rationale", "") or "",
                catalysts=list(getattr(analysis, "key_catalysts", []) or [])[:5],
                risks=list(getattr(analysis, "risk_factors", []) or [])[:5],
                sources=sources_breakdown or {},
                target_price=getattr(analysis, "target_price", None),
                hold_days=hold_days,
            )
        except Exception as e:
            log.debug("journal.log_entry fehlgeschlagen [%s]: %s", ticker, e)

    def _journal_exit(self, ticker, price, entry_price, pnl, reason, days_held):
        if not self.journal:
            return
        try:
            self.journal.log_exit(
                ticker=ticker, price=price, entry_price=entry_price,
                pnl=pnl, reason=reason, days_held=days_held,
            )
        except Exception as e:
            log.debug("journal.log_exit fehlgeschlagen [%s]: %s", ticker, e)


def _analysis_from_signal(sig: dict):
    """Rekonstruiert ein analysis-ähnliches Objekt aus einem Queue-Eintrag,
    damit strategy.evaluate() es erneut bewerten kann."""
    import types
    return types.SimpleNamespace(
        ticker=sig.get("ticker"),
        sentiment_score=float(sig.get("sentiment_score", 0.0) or 0.0),
        confidence=sig.get("confidence", "MEDIUM") or "MEDIUM",
        direction=sig.get("direction", "BULLISH") or "BULLISH",
        recommendation="BUY",
        target_price=sig.get("target_price"),
        entry_rationale=sig.get("entry_rationale", "") or "",
        key_catalysts=list(sig.get("key_catalysts", []) or []),
        risk_factors=list(sig.get("risk_factors", []) or []),
        sources_used=int(sig.get("sources_used", 0) or 0),
        suggested_hold_days=int(sig.get("suggested_hold_days", 0) or 0),
    )


def process_signal_queue(strategy, executor: "TradeExecutor", broker, regime: str = "NEUTRAL") -> list:
    """Arbeitet vorgemerkte BUY-Signale ab, sobald wieder ein Slot frei ist.

    Bewertet jeden Eintrag erneut über strategy.evaluate() (prüft also Slots,
    Korrelation, Sentiment-Schwelle erneut) und führt nur echte Käufe aus.
    Limit-/Pullback-Einträge werden erst bei erreichtem Limitpreis ausgeführt.
    """
    sq = getattr(strategy, "signal_queue", None)
    if not sq:
        return []
    msgs = []
    for sig in list(sq.get_pending()):
        ticker = sig["ticker"]
        try:
            price = broker.get_price(ticker)
            if not price:
                continue
            limit_price = sig.get("limit_price")
            if limit_price and price > limit_price:
                continue  # Pullback noch nicht erreicht – weiter warten
            analysis = _analysis_from_signal(sig)
            result = strategy.evaluate(ticker, analysis, float(price), regime)
            out = executor.execute(result, analysis=analysis,
                                   sources_breakdown=sig.get("sources_breakdown"))
            if out and "GEKAUFT" in out:
                sq.mark_executed(sig["id"])
                msgs.append(out)
        except Exception as e:
            log.warning("Signal-Queue-Abarbeitung [%s] fehlgeschlagen: %s", ticker, e)
    return msgs


class _NullNotifier:
    def send(self, *a, **k):
        pass

    def notify_buy(self, *a, **k):
        pass

    def notify_sell(self, *a, **k):
        pass
