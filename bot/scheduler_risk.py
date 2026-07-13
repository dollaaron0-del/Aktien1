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
    Liquidität, Sizing) — es kauft nur, was aktuell noch gültig ist."""
    try:
        if signal_queue.count_pending() == 0:
            return
        from strategy.executor import process_signal_queue
        for _msg in process_signal_queue(strategy, executor, broker, regime="NEUTRAL"):
            console.print(f"  [bold green]{_msg}[/bold green]")
    except Exception as _e:
        log.warning("Signal-Queue-Drain fehlgeschlagen: %s", _e)


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
