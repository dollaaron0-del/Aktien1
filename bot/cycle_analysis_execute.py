"""
bot/cycle_analysis_execute.py – Handelsentscheidung, Ausführung und
Nachverfolgung je Ticker, ausgelagert aus bot/cycle_analysis.py (Roadmap
4.4a-Folge, 500-Zeilen-Regel: zweiter der beiden Schnitte, die
cycle_analysis.py von 709 wieder unter die Grenze bringen).

strategy.evaluate() + executor.execute() (RL-Veto-gesteuerter Kauf/Verkauf),
Decision-Log, Prediction-Tracker, Experience-Store, Earnings-Strategy,
Korrektur-Follow-Up. Funktionskörper wortwörtlich aus cycle_analysis.py
übernommen – kein Verhalten geändert, nur die Kapselung. Ist der LETZTE
Block je Ticker-Iteration in der Schleife, daher self-contained bis auf
lessons_memo (wird über Iterationen hinweg neu gebunden, s. Rückgabewert –
exakt dieselbe Neu-Bindung wie im ungeschnittenen Original).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional

from logger import get_logger

log = get_logger(__name__)


def execute_and_track(
    ticker: str,
    price_data: Optional[dict],
    analysis,
    sources_breakdown: dict,
    regime,
    *,
    config, valid_price: Callable,
    strategy, executor, rl_agent,
    console, cycle_actions: List[str], live,
    tracker, get_experience_store: Callable,
    reflection, lessons_memo,
    earnings_strategy, portfolio, broker,
    headline_meta: dict, telegram_notifier_cls,
    analysis_row_id,
):
    """Siehe Modul-Docstring. Gibt das (ggf. aktualisierte) lessons_memo
    zurück – einziger Zustand, der über die Ticker-Iterationen hinweg lebt."""
    _raw_px = (price_data or {}).get("current_price")
    _cur_px = float(_raw_px) if valid_price(_raw_px) else 0.0
    _result = None
    if _cur_px > 0:
        # Fehler bei EINEM Ticker darf den restlichen Zyklus nicht abreißen
        # und muss sichtbar sein (nicht still verschluckt – vgl. Execution-Bug).
        try:
            _rl = rl_agent if getattr(config, "rl_veto_enabled", False) else None
            _result = strategy.evaluate(ticker, analysis, _cur_px, regime, rl_agent=_rl)
            action = executor.execute(_result, analysis=analysis, sources_breakdown=sources_breakdown)
        except Exception as _exec_err:
            log.error("Evaluate/Execute [%s] fehlgeschlagen: %s", ticker, _exec_err, exc_info=True)
            console.print(f"  [bold red]⚠ {ticker}: Entscheidung/Ausführung fehlgeschlagen – {_exec_err}[/bold red]")
            action = None
    else:
        action = f"[{ticker}] Kein Kurs verfügbar – übersprungen"
    # Entscheidungs-Transparenz: jede Strategie-Entscheidung samt Grund und
    # Kontext persistieren — das Dashboard (Tab "Entscheidungen") zeigt
    # daraus, WARUM gekauft/übersprungen wurde. Fail-open.
    try:
        from analyzers.decision_log import get_decision_log
        _dlog = get_decision_log()
        if _dlog is not None:
            _dl_mb = None
            try:
                from analyzers.macro_context import get_macro_context
                _dl_mb = round(float(get_macro_context().bias_score()), 4)
            except Exception:
                pass
            _dl_su = getattr(analysis, "sources_used", 0)
            _dl_nsrc = (sum(int(v or 0) for v in _dl_su.values())
                        if isinstance(_dl_su, dict) else int(_dl_su or 0))
            _dlog.log({
                "ticker": ticker,
                "action": _result.action if _result is not None else "SKIP",
                "reason": (_result.reason if _result is not None
                           else "Kein Kurs verfügbar"),
                "executed": action,
                "source": "cycle",
                "recommendation": getattr(analysis, "recommendation", None),
                "direction": getattr(analysis, "direction", None),
                "sentiment_score": getattr(analysis, "sentiment_score", None),
                "confidence": getattr(analysis, "confidence", None),
                "sources_used": _dl_nsrc,
                "regime": str(regime) if regime else None,
                "macro_bias": _dl_mb,
                "analysis_id": analysis_row_id,
            })
    except Exception as _dl_err:
        log.debug("Decision-Log Fehler [%s]: %s", ticker, _dl_err)
    if action:
        if "GEKAUFT" in action:
            color = "bold green"
        elif "VERKAUFT" in action:
            color = "bold red"
        elif "übersprungen" in action or "Limit" in action or "Schwelle" in action or "übersprungen" in action:
            color = "yellow"
        else:
            color = "dim"
        console.print(f"  [{color}]{action}[/{color}]")
        # Nur echte Käufe/Verkäufe in die Tages-Zusammenfassung
        if "GEKAUFT" in action or "VERKAUFT" in action:
            cycle_actions.append(action)
            live.feed_emit("trade", ticker=ticker, detail=action)
            # Prediction-Tracking: Kauf = neue Vorhersage, Verkauf = Outcome.
            # Speist Genauigkeits-Reports, Reflektion, Kalibrierung, adaptive Schwellen.
            _px = float((price_data or {}).get("current_price") or 0)
            if _px > 0:
                try:
                    if "GEKAUFT" in action:
                        # Idempotent: pro Ticker nur eine offene Vorhersage
                        if tracker.open_prediction_id(ticker) is None:
                            tracker.record_prediction(
                                ticker, analysis.direction, analysis.confidence,
                                analysis.sentiment_score, _px, analysis.debate_winner,
                            )
                    else:  # VERKAUFT
                        _pid = tracker.open_prediction_id(ticker)
                        if _pid is not None:
                            tracker.record_outcome(_pid, _px, exit_reason=action)
                except Exception as _pt_err:
                    log.debug("Prediction-Tracking Fehler [%s]: %s", ticker, _pt_err)
                # Experience-Store (Selbstlern-Datensatz): spiegelt Entry/Exit
                # als gelabelte Erfahrung mit label_source='live'. Fail-open.
                try:
                    _es = get_experience_store()
                    if _es is not None:
                        if "GEKAUFT" in action:
                            if _es.open_decision_id(ticker) is None:
                                # Kontext-Features zum Entscheidungszeitpunkt:
                                # Marktregime (Scope-Var) + Makro-Bias. Beide fail-open
                                # (None), damit ein fehlender Makro-Snapshot nie den
                                # Entry-Log reißt.
                                _macro_bias = None
                                try:
                                    from analyzers.macro_context import get_macro_context
                                    _macro_bias = round(float(get_macro_context().bias_score()), 4)
                                except Exception:
                                    _macro_bias = None
                                _es.record_live_entry({
                                    "decided_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                                    "ticker": ticker,
                                    "recommendation": analysis.recommendation,
                                    "direction": analysis.direction,
                                    "sentiment_score": analysis.sentiment_score,
                                    "confidence": analysis.confidence,
                                    "debate_winner": analysis.debate_winner,
                                    "target_price": getattr(analysis, "target_price", None),
                                    "suggested_hold": getattr(analysis, "suggested_hold_days", None),
                                    "sources_used": int(getattr(analysis, "sources_used", 0) or 0)
                                    if not isinstance(getattr(analysis, "sources_used", 0), dict)
                                    else sum((analysis.sources_used or {}).values()),
                                    "key_catalysts": list(getattr(analysis, "key_catalysts", []) or []),
                                    "risk_factors": list(getattr(analysis, "risk_factors", []) or []),
                                    "regime": str(regime) if regime else None,
                                    "macro_bias": _macro_bias,
                                }, _px)
                        else:  # VERKAUFT
                            _es.record_live_exit(ticker, _px, exit_reason=action)
                except Exception as _es_err:
                    log.debug("Experience-Store Fehler [%s]: %s", ticker, _es_err)
            # Lessons-Memo nach jedem Verkauf aktualisieren
            if reflection and "VERKAUFT" in action:
                new_memo = reflection.generate_memo()
                if new_memo:
                    console.print("  [dim]📚 Lessons-Memo aktualisiert[/dim]")
                    lessons_memo = new_memo
    elif analysis.recommendation == "BUY":
        console.print(
            f"  [dim][{ticker}] BUY-Signal vorhanden, aber kein Trade "
            f"(Konfidenz/Schwelle/Filter – Logs prüfen)[/dim]"
        )

    # Earnings Strategy: pre/post-earnings plays (independent of swing signal)
    if earnings_strategy and price_data:
        _ep = price_data.get("current_price") or 0
        if _ep > 0:
            try:
                _pv = portfolio.total_value(broker.get_prices(list(portfolio.all_positions().keys()) + [ticker]))
                earn_action = earnings_strategy.evaluate(ticker, _ep, _pv)
                if earn_action:
                    _ec = "bold green" if "GEKAUFT" in earn_action else "dim"
                    console.print(f"  [{_ec}]{earn_action}[/{_ec}]")
                    if "GEKAUFT" in earn_action:
                        cycle_actions.append(earn_action)
            except Exception as _eae:
                log.debug("EarningsStrategy.evaluate error [%s]: %s", ticker, _eae)

    # Korrektur-Follow-Up: Headline-BUY-Signal durch Strategy geblockt → User informieren
    if (
        ticker in headline_meta
        and analysis.recommendation == "BUY"
        and (action is None or "GEKAUFT" not in action)
    ):
        try:
            _block_reason = action or f"[{ticker}] Kein Kauf (Filter-Details in Logs)"
            telegram_notifier_cls().send(
                f"⚠️ <b>{ticker} – Kauf nicht ausgeführt</b>\n\n"
                f"Claude: BUY (Score {analysis.sentiment_score:.2f}) – aber Trade blockiert:\n"
                f"{_block_reason.replace(f'[{ticker}] ', '')}"
            )
        except Exception as _corr_err:
            log.debug("Korrektur-Follow-Up fehlgeschlagen: %s", _corr_err)

    return lessons_memo
