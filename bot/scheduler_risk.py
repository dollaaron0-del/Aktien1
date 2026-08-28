"""
bot/scheduler_risk.py – Positions-/Risiko-Jobs: Conditional-Entry-Ausführung,
IBKR-Limit-Fill-Buchung, Signal-Queue-Drain, SL/TP-Check, Position-Aging.

Ausgelagert aus bot/scheduler.py::run_bot_loop (Roadmap 4.4a, zweite Naht
nach scheduler_maintenance.py). signal_queue_job und sl_tp_check_job sind
gekoppelt (ein SL/TP-Exit gibt Kapital/Slots frei → sl_tp_check_job stößt
danach sofort einen Signal-Queue-Drain an) — sl_tp_check_job bekommt die
Signal-Queue-Job-Funktion daher als Parameter statt sie fest zu verdrahten,
das hält beide unabhängig testbar.

scheduler.py behält für jeden Job einen gleichnamigen dünnen Wrapper (wie
bei scheduler_maintenance.py): `schedule` übernimmt den Funktionsnamen per
functools.update_wrapper, und test_scheduler_registration.py prüft genau
diesen Namen.
"""
from __future__ import annotations

from datetime import datetime, timezone

from logger import get_logger
from rich.console import Console

log = get_logger(__name__)
console = Console()


def conditional_entry_job(broker, strategy, executor, telegram_notifier_cls) -> None:
    """Führt ausstehende bedingte Einstiege aus sobald der Trigger-Kurs erreicht ist.
    Entries mit IBKR-Limit-Order (ibkr_order_id gesetzt) werden übersprungen –
    der ibkr_fill_check_job übernimmt deren Abwicklung."""
    try:
        from analyzers.conditional_entry import ConditionalEntryWatcher
        from analyzers import AnalysisResult
        watcher = ConditionalEntryWatcher()
        active = watcher.get_active()
        if not active:
            return
        # Nur Entries ohne IBKR-Limit-Order per Polling prüfen
        manual_entries = [e for e in active if not e.ibkr_order_id]
        if not manual_entries:
            return
        prices = broker.get_prices([e.ticker for e in manual_entries])
        triggered = watcher.check_triggered(prices)
        triggered = [e for e in triggered if not e.ibkr_order_id]
        if not triggered:
            return
        notifier = telegram_notifier_cls()
        for entry in triggered:
            price = prices.get(entry.ticker, entry.trigger_price)
            console.print(
                f"\n[bold green]📌 Conditional Entry ausgelöst: "
                f"{entry.ticker} @ ${price:.2f} "
                f"(Trigger war: ${entry.trigger_price:.2f})[/bold green]"
            )
            analysis = AnalysisResult(
                ticker=entry.ticker,
                sentiment_score=entry.sentiment_score,
                direction="BULLISH",
                confidence=entry.confidence,
                recommendation="BUY",
                entry_rationale=(
                    f"[Conditional Entry @ ${price:.2f}] {entry.entry_rationale}"
                ),
                risk_factors=entry.risk_factors,
                key_catalysts=entry.key_catalysts,
                suggested_hold_days=entry.suggested_hold_days,
                target_price=entry.target_price,
                target_price_rationale=entry.target_price_rationale,
                thesis_valid=None,
                thesis_break_reason="",
                # Quellenzahl der Ursprungs-Analyse replayen (Floor 1), sonst
                # blockt die min_sources-Schranke den bereits vollständig
                # analysierten Entry fälschlich mit 0 Quellen.
                sources_used={"conditional_entry": max(1, entry.sources_count)},
                bull_case=entry.bull_case,
                bear_case=entry.bear_case,
                debate_winner="BULL",
            )
            result = strategy.evaluate(entry.ticker, analysis, price, "NEUTRAL")
            action = executor.execute(result, analysis=analysis)
            if action and "GEKAUFT" in action:
                watcher.remove(entry.ticker)
                # Executor hat bereits die Standard-Kauf-Benachrichtigung
                # gesendet (inkl. "[Conditional Entry @ …]"-Kontext).
                log.info("Conditional Entry ausgeführt: %s @ $%.2f", entry.ticker, price)
            else:
                log.info(
                    "Conditional Entry ausgelöst aber kein Trade: %s (Strategy-Filter: %s)",
                    entry.ticker, action,
                )
    except Exception as _e:
        log.warning("Conditional-Entry-Job fehlgeschlagen: %s", _e)


def ibkr_fill_check_job(broker, portfolio, telegram_notifier_cls) -> None:
    """Prüft ob hinterlegte IBKR Limit-Orders ausgeführt wurden und trägt Positionen ein."""
    try:
        from analyzers.conditional_entry import ConditionalEntryWatcher
        from portfolio.portfolio import Position
        from datetime import datetime as _dt
        watcher = ConditionalEntryWatcher()
        active = watcher.get_active()
        ibkr_entries = [e for e in active if e.ibkr_order_id]
        if not ibkr_entries:
            return
        order_ids = [e.ibkr_order_id for e in ibkr_entries]
        fills = broker.get_filled_limit_orders(order_ids)
        if not fills:
            return
        notifier = telegram_notifier_cls()
        filled_ids = {f["order_id"] for f in fills}
        fill_map   = {f["order_id"]: f for f in fills}
        for entry in ibkr_entries:
            if entry.ibkr_order_id not in filled_ids:
                continue
            fill = fill_map[entry.ibkr_order_id]
            fill_price = fill["fill_price"]
            shares     = fill["shares"]
            sl = fill_price * (1 - 0.07)
            tp = entry.target_price or fill_price * 1.20
            pos = Position(
                ticker=entry.ticker,
                shares=shares,
                entry_price=fill_price,
                entry_date=_dt.utcnow().isoformat()[:10],
                stop_loss=round(sl, 4),
                take_profit=round(tp, 4),
                target_hold_days=entry.suggested_hold_days,
                rationale=f"[IBKR Limit-Fill @ ${fill_price:.2f}] {entry.entry_rationale}",
                entry_catalysts=entry.key_catalysts,
            )
            try:
                # force=True: der Fill ist beim Broker bereits Tatsache
                # (Geld ausgegeben) – ein Buch-Cash-Mangel darf die
                # Buchung nicht auf ewig verhindern (sonst hält IBKR die
                # Position, das Buch weiß nie davon, Job spammt alle 5min).
                portfolio.open_position(pos, force=True)
                watcher.remove(entry.ticker)
                # Broker-seitigen GTC-Schutz-Stop hinterlegen (greift
                # auch bei Bot-Ausfall); update_stop platziert neu.
                _upd = getattr(broker, "update_stop", None)
                if callable(_upd):
                    try:
                        _upd(entry.ticker, shares, round(sl, 4))
                    except Exception as _ste:
                        log.warning("Schutz-Stop für Limit-Fill %s nicht platziert: %s",
                                    entry.ticker, _ste)
                log.info(
                    "IBKR Limit-Order gefüllt: %s %.4f @ $%.4f (Order #%d)",
                    entry.ticker, shares, fill_price, entry.ibkr_order_id,
                )
                notifier.send(
                    f"📌 <b>IBKR Limit-Order ausgeführt: {entry.ticker}</b>\n\n"
                    f"Fill: {shares} Stück @ <b>${fill_price:.2f}</b>\n"
                    f"SL: ${sl:.2f} | TP: ${tp:.2f}\n"
                    f"<b>Bull-Case:</b> {entry.bull_case}\n"
                    f"<b>Halteziel:</b> {entry.suggested_hold_days}d"
                    + (f"\n<b>Kursziel:</b> ${entry.target_price:.2f}" if entry.target_price else "")
                )
            except ValueError as _ve:
                log.warning("IBKR Fill – Portfolio-Eintrag fehlgeschlagen (%s): %s", entry.ticker, _ve)
                notifier.send(
                    f"⚠️ IBKR Limit-Fill {entry.ticker} konnte nicht ins Portfolio eingetragen werden: {_ve}"
                )
    except Exception as _e:
        log.warning("IBKR-Fill-Check fehlgeschlagen: %s", _e)


def signal_queue_job(signal_queue, strategy, executor, broker) -> None:
    """N3-Befund: process_signal_queue() war nie verdrahtet — vorgemerkte
    Signale ("Max Positionen erreicht") liefen deshalb immer nur ab
    (historisch 0 von 144 ausgeführt). Jeder Eintrag wird beim Drain über
    strategy.evaluate() komplett neu geprüft (Slots, Schwelle, Korrelation,
    Liquidität, Sizing) — es kauft nur, was aktuell noch gültig ist.

    'market_closed'-Einträge laufen NICHT hier durch (process_signal_queue
    überspringt sie bewusst) — die drained market_closed_signal_job unten."""
    try:
        if signal_queue.count_pending() == 0:
            return
        from strategy.executor import process_signal_queue
        for _msg in process_signal_queue(strategy, executor, broker, regime="NEUTRAL"):
            console.print(f"  [bold green]{_msg}[/bold green]")
    except Exception as _e:
        log.warning("Signal-Queue-Drain fehlgeschlagen: %s", _e)


def market_closed_signal_job(signal_queue, escalate_fn) -> None:
    """Drained BUY-Signale, die NUR an geschlossener Börse gescheitert sind
    (z.B. Order während des vorbörslichen Analyse-Zyklus, NYSE noch zu –
    27.7.2026-Befund: so ein Signal verpuffte bislang ersatzlos, anders als
    der Kapitalmangel-/Max-Positionen-Fall).

    Bewusst KEINE Wiederverwendung des alten, ggf. stunden-/tagealten
    Sentiments: sobald die zuständige Börse wieder offen ist, wird der
    Ticker über escalate_fn() komplett NEU analysiert (frische News/Sentiment/
    Kaufthese, gleicher Pfad wie Headline-/Momentum-Eskalation). Nur was JETZT
    noch gilt, wird gekauft — der Queue-Eintrag gilt danach als 'rechecked',
    nicht als 'executed' (das würde einen Kauf suggerieren, den es evtl.
    gar nicht gab)."""
    try:
        pending = [s for s in signal_queue.get_pending() if s.get("reason") == "market_closed"]
        if not pending:
            return
        from analyzers.market_schedule import market_closed_reason
        for sig in pending:
            ticker = sig["ticker"]
            if market_closed_reason(ticker) is not None:
                continue  # Börse noch zu – weiter warten
            console.print(f"  [bold yellow]⏳→🔍 {ticker}: Marktöffnung – frische Neu-Analyse[/bold yellow]")
            escalate_fn([ticker], reason="Marktöffnung (zurückgestelltes BUY-Signal)")
            signal_queue.mark_rechecked(sig["id"])
    except Exception as _e:
        log.warning("Markt-geschlossen-Queue-Drain fehlgeschlagen: %s", _e)


def sl_tp_check_job(portfolio, broker, strategy, executor, signal_queue_job_fn) -> None:
    try:
        positions = portfolio.all_positions()
        if not positions:
            return
        prices = broker.get_prices(list(positions.keys()))
        _closed = False
        for _res in strategy.check_exits(prices):
            _p = portfolio.get_position(_res.ticker)
            _dh = 0
            if _p:
                try:
                    _dh = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(_p.entry_date)).days
                except Exception:
                    _dh = 0
            _act = executor.execute(_res, days_held=_dh)
            if _act:
                console.print(f"  [yellow]{_act}[/yellow]")
                if "VERKAUFT" in _act:
                    _closed = True
        if _closed:
            # Slot/Kapital frei geworden → vorgemerkte Signale sofort prüfen
            signal_queue_job_fn()
    except Exception as _e:
        log.warning("SL/TP-Check fehlgeschlagen: %s", _e)


def broker_healing_pass(portfolio, broker, telegram_notifier_cls, context: str = "periodisch") -> None:
    """Broker-Abgleich (Phantome ausbuchen) + Schutz-Stop-Sync in einem Rutsch.

    Bis 27.7.2026 lief diese Heilung NUR einmalig beim Bot-Start (main.py) –
    schließt ein broker-seitig gefeuerter GTC-Stop während laufendem Betrieb
    eine Position, bemerkt der Bot das erst beim nächsten Neustart (Stunden
    bis Tage später, META-Vorfall). Single-Source-of-Truth-Funktion, von
    main.py (context='Start') UND periodisch aus bot/scheduler.py
    (context='periodisch') aufgerufen, damit Start- und Laufzeit-Heilung
    nie auseinanderlaufen. reconcile_against_broker() selbst bucht den
    Gegen-SELL inzwischen zum vermuteten SL-/TP-Preis mit echtem PnL statt
    pauschal 0 (portfolio/integrity.py) – dieser Job sorgt nur noch dafür,
    dass diese Korrektur zeitnah statt erst beim Neustart greift."""
    try:
        _pos_fn = getattr(broker, "positions", None)
        if not callable(_pos_fn):
            return
        _bpos = _pos_fn()
        if _bpos is None:
            log.info("Broker-Abgleich (%s) übersprungen – IBKR-Positionen nicht ermittelbar (offline?).", context)
        else:
            from portfolio.integrity import reconcile_against_broker
            _br = reconcile_against_broker(portfolio._conn, _bpos)
            if _br.ok:
                log.info("Broker-Abgleich (%s): %s", context, _br.summary())
            else:
                log.warning("Broker-Abgleich (%s): %s", context, _br.summary())
                # reconciled/partial_mismatch/snapshot_rejected heißen: das BUCH
                # selbst führt eine nachweislich falsche Stückzahl (RHM.DE-Fall
                # 27.8.2026: Buch 14,7875 vs. real 2 Stück, WOCHENLANG unbemerkt,
                # weil dieser Alert bisher mit level="info" lief und vom Default
                # TELEGRAM_MODE=important stillschweigend verschluckt wurde – nur
                # untracked (bei IBKR, nicht im Buch) verfälscht das Buch selbst
                # NICHT, bleibt darum auf info). Jetzt: level="critical" kommt
                # durch. 12h-Throttle (derselbe Mechanismus wie
                # strategy.executor._throttle_should_send) gegen Dauerspam, falls
                # dieselbe Abweichung unverändert bestehen bleibt.
                try:
                    from strategy.executor import _stable_signature, _throttle_should_send
                    _summary = _br.summary()
                    _book_wrong = bool(_br.reconciled or _br.partial_mismatch or _br.snapshot_rejected)
                    _level = "critical" if _book_wrong else "info"
                    if _level != "critical" or _throttle_should_send(
                        "BROKER_RECONCILE_CRITICAL", _stable_signature(_summary)
                    ):
                        telegram_notifier_cls().send(
                            f"🔄 <b>Broker-Abgleich ({context})</b>\n" + _summary, level=_level,
                        )
                except Exception:
                    pass
    except Exception as _be:
        log.debug("Broker-Abgleich (%s) übersprungen: %s", context, _be)

    try:
        _sync_stops = getattr(broker, "sync_protective_stops", None)
        if not callable(_sync_stops):
            return
        _stop_book = {t: (p.shares, p.stop_loss)
                      for t, p in portfolio.all_positions().items()
                      if p.stop_loss and p.shares > 0}
        if not _stop_book:
            return
        _sres = _sync_stops(_stop_book)
        if _sres is None:
            log.info("Schutz-Stop-Sync (%s) übersprungen (offline/deaktiviert).", context)
            return
        _missing = [t for t, ok in _sres.items() if not ok]
        if _missing:
            log.warning("Schutz-Stop-Sync (%s): NICHT platziert für %s", context, ", ".join(_missing))
            try:
                telegram_notifier_cls().send(
                    f"⚠️ <b>Schutz-Stops ({context}) unvollständig</b>\n"
                    "Kein Broker-Stop platzierbar für: " + ", ".join(_missing),
                    level="critical",
                )
            except Exception:
                pass
        else:
            log.info("Schutz-Stop-Sync (%s): %d Position(en) broker-seitig abgesichert.", context, len(_sres))
    except Exception as _se:
        log.debug("Schutz-Stop-Sync (%s) übersprungen: %s", context, _se)


def position_aging_job(portfolio, broker, telegram_notifier_cls) -> None:
    """Warnt per Telegram wenn eine Position zu lange ohne Gewinn gehalten wird."""
    try:
        positions = portfolio.all_positions()
        if not positions:
            return
        prices = broker.get_prices(list(positions.keys()))
        warnings = []
        runners = []
        for ticker, pos in positions.items():
            price = prices.get(ticker, pos.entry_price)
            days_held = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(pos.entry_date)).days
            pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
            ratio = days_held / max(pos.target_hold_days, 1)
            if ratio >= 0.8 and pnl_pct < 0:
                warnings.append(
                    f"  ⚠️ <b>{ticker}</b>: {days_held}/{pos.target_hold_days}d · "
                    f"P&L {pnl_pct:+.1f}% · Kurs ${price:.2f}"
                )
            elif ratio >= 1.0 and pnl_pct > 0:
                runners.append(
                    f"  🏃 <b>{ticker}</b>: {days_held}d (Ziel {pos.target_hold_days}d überschritten) · "
                    f"+{pnl_pct:.1f}% · Kurs ${price:.2f}"
                )
        if warnings or runners:
            parts = []
            if warnings:
                parts.append("⚠️ <b>Aging-Warnung – Positionen ohne Gewinn nahe Halteziel:</b>\n" + "\n".join(warnings))
            if runners:
                parts.append("🏃 <b>Läufer – Haltedauer überschritten, noch im Gewinn:</b>\n" + "\n".join(runners))
            telegram_notifier_cls().send("\n\n".join(parts))
            for line in warnings + runners:
                console.print(f"  {line}")
    except Exception as e:
        log.warning("Position-Aging-Job fehlgeschlagen: %s", e)
