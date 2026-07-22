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

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from logger import get_logger
from portfolio.portfolio import Position
from strategy.swing_strategy import StrategyResult

log = get_logger(__name__)

# Persistenter Throttle gegen wiederholte, wortgleiche Fehlschlag-Alerts.
# Ohne ihn feuert z.B. eine dauerhaft nicht ausführbare SELL-Order jede ~30 Min
# dieselbe Telegram-Nachricht (Spam über Nacht). Der TradeExecutor wird pro
# Zyklus neu erzeugt → State muss auf Platte liegen, nicht in-memory.
_THROTTLE_FILE = Path("data/notify_throttle.json")
_THROTTLE_COOLDOWN_H = 12  # gleiche Meldung höchstens alle 12h erneut senden


def _throttle_load() -> dict:
    try:
        return json.loads(_THROTTLE_FILE.read_text())
    except Exception:
        return {}


def _throttle_should_send(key: str, signature: str) -> bool:
    """True, wenn diese Meldung gesendet werden soll: noch nie gesehen, Inhalt
    geändert, oder Cooldown abgelaufen. Aktualisiert bei True den Eintrag."""
    data = _throttle_load()
    rec = data.get(key)
    now = datetime.now(timezone.utc)
    if rec and rec.get("sig") == signature:
        try:
            last = datetime.fromisoformat(rec["ts"])
            if (now - last).total_seconds() < _THROTTLE_COOLDOWN_H * 3600:
                return False  # identische Meldung innerhalb Cooldown → unterdrücken
        except Exception:
            pass
    data[key] = {"sig": signature, "ts": now.isoformat()}
    try:
        _THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _THROTTLE_FILE.write_text(json.dumps(data))
    except Exception as e:
        log.debug("Throttle-State schreiben fehlgeschlagen: %s", e)
    return True


def _throttle_clear(key: str) -> None:
    """Eintrag löschen (z.B. nach erfolgreichem Exit), damit ein künftiger
    neuer Fehlschlag sofort wieder alarmiert statt erst nach Cooldown."""
    data = _throttle_load()
    if data.pop(key, None) is not None:
        try:
            _THROTTLE_FILE.write_text(json.dumps(data))
        except Exception as e:
            log.debug("Throttle-State schreiben fehlgeschlagen: %s", e)


def _is_filled(fill) -> bool:
    return isinstance(fill, dict) and fill.get("status") == "filled"


def _fill_price(fill, fallback: float) -> float:
    if isinstance(fill, dict):
        fp = fill.get("fill_price") or fill.get("market_price")
        if fp:
            return float(fp)
    return float(fallback)


def _fill_shares(fill, fallback: float) -> float:
    """Tatsächlich ausgeführte Stückzahl aus dem Fill. IBKR rundet Aktien auf
    ganze Stück (Teilaktien nicht API-fähig, Error 10243) und liefert die
    georderte Menge im Fill zurück. Das Buch MUSS diese echte Menge führen –
    sonst entstehen Bruchteils-Phantome (z.B. 57.91 angefragt, 57 gefüllt →
    0.91 Dust, das nie verkäuflich ist)."""
    if isinstance(fill, dict):
        fs = fill.get("shares")
        if fs is not None:
            try:
                return float(fs)
            except (TypeError, ValueError):
                pass
    return float(fallback)


def _fail_reason(fill) -> str:
    """Fehlergrund aus einem nicht-gefüllten Fill (IBKR nutzt 'reason',
    Paper ggf. 'error'; sonst Roh-Status)."""
    if isinstance(fill, dict):
        return str(fill.get("reason") or fill.get("error") or fill.get("status") or "unbekannt")
    return str(fill)


class TradeExecutor:
    def __init__(self, portfolio, broker, journal=None, notifier=None, strategy=None):
        self.portfolio = portfolio
        self.broker = broker
        self.journal = journal
        self._notifier = notifier  # lazy: erst bei Bedarf erstellen
        self._accepts_stop = None  # lazy: kennt broker.buy stop_loss?
        # Für den Circuit-Breaker: SwingStrategy.record_loss() muss den
        # REALISIERTEN Verlust (echter Fill-Preis) erfahren, sonst bleibt
        # SwingStrategy._daily_loss_usd für immer 0 und der Tagesverlust-
        # Circuit-Breaker (_circuit_breaker_active) greift nie.
        self._strategy = strategy

    @property
    def notifier(self):
        if self._notifier is None:
            try:
                from notifier.telegram_notifier import TelegramNotifier
                self._notifier = TelegramNotifier()
            except Exception:
                self._notifier = _NullNotifier()
        return self._notifier

    def _broker_accepts_stop(self) -> bool:
        """True, wenn broker.buy einen stop_loss-Parameter annimmt."""
        if self._accepts_stop is None:
            try:
                import inspect
                self._accepts_stop = "stop_loss" in inspect.signature(self.broker.buy).parameters
            except (TypeError, ValueError):
                self._accepts_stop = False
        return self._accepts_stop

    def _sync_stop_after_partial(self, ticker: str) -> None:
        """Nach Partial-TP den Broker-Schutz-Stop auf Restmenge + neuen SL
        nachziehen (die Engine hat das Buch bereits aktualisiert)."""
        upd = getattr(self.broker, "update_stop", None)
        if not callable(upd):
            return
        try:
            pos = self.portfolio.get_position(ticker)
            if pos and pos.shares > 0:
                upd(ticker, pos.shares, pos.stop_loss)
        except Exception as e:
            log.warning("[%s] Schutz-Stop-Nachführung nach Partial-TP fehlgeschlagen: %s",
                        ticker, e)

    # ── Broker-Deckungs-Check (Anti-Short bei Buch/IBKR-Desync) ──────────────
    def _broker_held(self, ticker: str) -> Optional[float]:
        """Tatsächlich beim Broker gehaltene Stückzahl für `ticker`.

        None = nicht prüfbar (Broker ohne positions()-API wie Paper,
        oder Stand nicht ermittelbar) → Guard greift bewusst NICHT, weil ein
        nicht-deckbarer Sell im No-Fallback-Modus ohnehin sicher errort statt
        zu shorten. Ein leeres {} heißt sicher 'flach' → 0.0."""
        pos_fn = getattr(self.broker, "positions", None)
        if not callable(pos_fn):
            return None
        try:
            held = pos_fn()
        except Exception:
            return None
        if held is None:
            return None
        base = ticker.split(".")[0].upper()
        return float(held.get(ticker) or held.get(base) or 0.0)

    def _short_guard_blocks(self, ticker: str, sell_shares: float) -> Optional[str]:
        """Gibt eine Fehlermeldung zurück, wenn der Broker WENIGER hält als
        verkauft werden soll (→ Order würde einen Short eröffnen). Sonst None."""
        held = self._broker_held(ticker)
        if held is None or held + 1e-4 >= sell_shares:
            return None
        warn = f"IBKR hält {held:g}, Buch will {sell_shares:g} verkaufen"
        if _throttle_should_send(f"DESYNC:{ticker}", f"{held:g}/{sell_shares:g}"):
            self.notifier.send(
                f"⚠️ <b>{ticker} SELL übersprungen – Buch/IBKR-Desync</b>\n"
                f"{warn} → Order würde shorten, daher blockiert.\n"
                f"Buch gegen IBKR abgleichen (Startup-Reconcile prüft das beim Neustart).",
                level="critical",
            )
        log.error("[%s] SELL übersprungen (Anti-Short): %s", ticker, warn)
        return warn

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

        # stop_loss nur durchreichen, wenn der Broker den Parameter kennt
        # (IBKR platziert daraus einen GTC-Schutz-Stop; Paper ignoriert ihn).
        # Signatur-Check statt try/TypeError: ein TypeError aus dem Broker-
        # Inneren darf NIE eine zweite Order auslösen.
        if self._broker_accepts_stop():
            fill = self.broker.buy(ticker, shares, result.price,
                                   stop_loss=result.stop_loss or None)
        else:
            fill = self.broker.buy(ticker, shares, result.price)
        if not _is_filled(fill):
            warn = _fail_reason(fill)
            return f"[{ticker}] ⛔ BUY-Order fehlgeschlagen: {warn}"
        actual_price = _fill_price(fill, result.price)

        # Buch auf die TATSÄCHLICH gefüllte Menge setzen (IBKR rundet auf ganze
        # Stück). Sonst führt das Buch z.B. 57.91, der Broker hält 57 → Dust,
        # das beim Voll-Exit nie verkauft werden kann (Phantom-Position).
        filled_shares = _fill_shares(fill, shares)
        if filled_shares <= 0:
            return f"[{ticker}] ⛔ BUY 0 Stück gefüllt (Teilaktie gerundet) – nicht gebucht"
        if filled_shares != shares:
            log.info("[%s] Buchung auf Fill-Menge korrigiert: %.4f → %.4f Stück",
                     ticker, shares, filled_shares)
        shares = filled_shares

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
            entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            target_hold_days=result.hold_days,
            rationale=result.reason or getattr(analysis, "entry_rationale", "") or "",
            entry_catalysts=catalysts,
            entry_filter_verdict=getattr(result, "entry_filter_verdict", "") or "",
            entry_filter_p_win=float(getattr(result, "entry_filter_p_win", 0.0) or 0.0),
            entry_filter_edge=float(getattr(result, "entry_filter_edge", 0.0) or 0.0),
        )
        # IBKR meldet stop_placed=False, wenn der GTC-Schutz-Stop nicht
        # hinterlegt werden konnte → Position ist NUR bot-seitig überwacht.
        if fill.get("stop_placed") is False:
            log.error("[%s] Schutz-Stop beim Broker NICHT platziert – nur Bot-Überwachung aktiv", ticker)
            self.notifier.send(
                f"⚠️ <b>{ticker}: Broker-Schutz-Stop NICHT platziert</b>\n"
                f"Position ist nur bot-seitig abgesichert – bitte IBKR prüfen.",
                level="critical",
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
            blocked = self._short_guard_blocks(ticker, sell_shares)
            if blocked:
                return f"[{ticker}] ⛔ Partial-TP übersprungen (Desync): {blocked}"
            fill = self.broker.sell(ticker, sell_shares, result.price)
            if not _is_filled(fill):
                warn = _fail_reason(fill)
                self.notifier.send(
                    f"🚨 <b>{ticker} Partial-TP Broker-Order FEHLGESCHLAGEN</b>\n"
                    f"Buch wurde bereits reduziert – bitte Broker manuell prüfen! ({warn})",
                    level="critical",
                )
                log.error("[%s] Partial-TP Broker-Sell fehlgeschlagen: %s", ticker, warn)
                return f"[{ticker}] ⛔ Partial-TP Broker-Order fehlgeschlagen: {warn}"
            actual_price = _fill_price(fill, result.price)
            # Broker-Schutz-Stop auf Restmenge/neuen SL nachziehen – der alte
            # Stop deckte die volle Menge und würde sonst überverkaufen.
            self._sync_stop_after_partial(ticker)
            self.notifier.send(
                f"📊 <b>{ticker} Partial-TP</b>: {sell_shares:.4f} Stück @ ${actual_price:.2f}\n{result.reason}",
                level="trade",
            )
            # Bewusst KEIN "VERKAUFT" → runner schließt die Vorhersage nicht.
            return f"[{ticker}] 📊 Partial-TP: {sell_shares:.4f} @ ${actual_price:.2f} | {result.reason}"

        # Voll-Exit
        sell_shares = pos.shares
        blocked = self._short_guard_blocks(ticker, sell_shares)
        if blocked:
            return f"[{ticker}] ⛔ SELL übersprungen (Desync): {blocked}"
        fill = self.broker.sell(ticker, sell_shares, result.price)
        if not _is_filled(fill):
            warn = _fail_reason(fill)
            # Throttle: dieselbe Fehlschlag-Meldung höchstens 1×/Cooldown senden,
            # sonst Spam jede ~30 Min bei dauerhaft nicht ausführbarer Order.
            if _throttle_should_send(f"SELL_FAIL:{ticker}", warn):
                self.notifier.send(
                    f"🚨 <b>{ticker} SELL-Order FEHLGESCHLAGEN</b> ({result.reason})\n"
                    f"Position bleibt offen – bitte manuell im Broker prüfen!\nFehler: {warn}",
                    level="critical",
                )
            log.error("[%s] SELL fehlgeschlagen (%s) – Position bleibt offen", ticker, warn)
            return f"[{ticker}] ⛔ SELL-Order fehlgeschlagen: {warn}"
        actual_price = _fill_price(fill, result.price)

        _throttle_clear(f"SELL_FAIL:{ticker}")  # Exit geglückt → Alarm zurücksetzen
        pnl = self.portfolio.close_position(ticker, actual_price, result.reason)
        if self._strategy is not None:
            try:
                self._strategy.record_loss(pnl)
            except Exception as e:
                log.debug("[%s] record_loss (Circuit-Breaker) fehlgeschlagen: %s", ticker, e)
        # SL-Cooldown: nur nach einem "sauberen", verlustigen Stop-Loss (kein
        # vorheriger Partial-TP, kein Trailing-Stop im Gewinn) den Ticker für
        # config.sl_cooldown_days sperren — Pendant zur Cooldown-Regel im
        # Backtest (backtesting/engine.py). Fail-open.
        if (
            (result.reason or "").startswith("Stop-Loss")
            and pnl < 0
            and not getattr(pos, "partial_tp_count", 0)
        ):
            try:
                from analyzers.sl_cooldown import StopLossCooldown
                StopLossCooldown().record(ticker, actual_price)
            except Exception as e:
                log.debug("[%s] SL-Cooldown record fehlgeschlagen: %s", ticker, e)
        # Fußnote (User-Vorgabe 22.7.2026): "hat der Lern-Filter recht behalten?" —
        # dieselbe Logik wie in Portfolio.close_position() (dort für die trades-
        # Tabelle), hier zusätzlich für Telegram/Journal, weil beide VOR dem
        # DELETE der Positionszeile bereits mit dem unangereicherten result.reason
        # aufgerufen würden, wenn man es nicht separat berechnet.
        try:
            from analyzers.entry_filter import hindsight_footnote
            _footnote = hindsight_footnote(
                getattr(pos, "entry_filter_verdict", ""), getattr(pos, "entry_filter_p_win", 0.0),
                getattr(pos, "entry_filter_edge", 0.0), pnl,
            )
        except Exception:
            _footnote = ""
        exit_reason = f"{result.reason} | {_footnote}" if _footnote else result.reason

        self._notify_sell(ticker, sell_shares, actual_price, pos, pnl, exit_reason, days_held)
        self._journal_exit(ticker, actual_price, pos.entry_price, pnl, exit_reason, days_held)

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
            # Entscheidungs-Transparenz (Dashboard): auch Queue-Drains loggen.
            try:
                from analyzers.decision_log import get_decision_log
                _dl = get_decision_log()
                if _dl is not None:
                    _dl.log({
                        "ticker": ticker,
                        "action": result.action,
                        "reason": result.reason,
                        "executed": out,
                        "source": "queue",
                        "recommendation": "BUY",
                        "direction": sig.get("direction"),
                        "sentiment_score": float(sig.get("sentiment_score") or 0),
                        "confidence": sig.get("confidence"),
                        "regime": regime,
                    })
            except Exception:
                pass
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
