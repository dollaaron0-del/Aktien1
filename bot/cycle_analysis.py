"""
bot/cycle_analysis.py – serielle Analyse-Schleife über die Watchlist.

Ausgelagert aus bot/runner.py (Roadmap 4.4a, 500-Zeilen-Regel): der Kern von
run_analysis_cycle – für jeden Ticker News/Preis auflösen (Prefetch-Cache-Hit
oder eigener serieller Pfad), Daten-Gate, FinBERT/Insider/Signal-Expander,
News-Geschwindigkeit/Multi-Zeitrahmen-Sentiment/Re-Entry/Earnings-Prognose/
Chart-/On-Chain-Signale, Claude-Analyse (Cache-Hit oder frischer Aufruf),
Cache/Log-Persistenz, Conditional-Entry, Stock-Relations, RL-Veto-gesteuertes
strategy.evaluate()+Executor, Decision-Log, Prediction-Tracker, Experience-
Store, Earnings-Strategy, Korrektur-Follow-Up. Der größte/riskanteste Einzel-
schnitt der 4.4a-Aufräumung – hier liegt die eigentliche Handelsentscheidung,
nicht nur Registrierungs-/Vorbereitungsstruktur.

Der Funktionskörper ist bewusst WORTWÖRTLICH aus runner.py übernommen (nur
über den Alias-Block am Funktionsanfang an die Parameter gebunden) – das
Risiko bei einer so großen Verschiebung ist eine subtile Verhaltensänderung,
kein Transkriptionsfehler soll das Verhalten anders machen. Analyzer-Klassen/
Singletons/Modul-Funktionen (TelegramNotifier, _analysis_cache, _rl_agent, …)
werden als Parameter hereingereicht statt hier frisch importiert – runner.py
löst sie bei jedem Aufruf aus dem eigenen Modul-Namensraum auf, wodurch
bestehende monkeypatch.setattr(runner_mod, "X", Fake)-Tests weiterhin greifen
(s. bot/cycle_prefetch.py für dasselbe, dort schon etablierte Muster).

cycle_actions und headline_results werden IN PLACE mutiert (dieselben Listen
wie die übrigen Zyklus-Schritte); lessons_memo wird nur INNERHALB der
Schleife über Iterationen hinweg neu gebunden (nach jedem Verkauf, für die
nächste Analyse in DERSELBEN Schleife) und danach nirgends mehr gebraucht –
kein Rückgabewert nötig.

NACHTRAG (Roadmap 4.4a-Folge, 10.8.2026): war mit der Zeit auf 709 Zeilen
gewachsen, wieder über der 500-Zeilen-Regel. Zwei klar abgrenzbare Blöcke je
Ticker-Iteration nach demselben Prinzip (Körper wortwörtlich verschoben,
diff-verifiziert) weiter ausgelagert: die lokale Signal-Anreicherung VOR der
Claude-Analyse (bot/cycle_analysis_signals.py::resolve_local_signals) und die
Handelsentscheidung+Ausführung+Tracking NACH der Claude-Analyse
(bot/cycle_analysis_execute.py::execute_and_track). Der eigentliche
Claude-Aufruf samt Cache/Log/Prompt-Archiv-Persistenz bleibt bewusst HIER –
das ist der Kern der Handelsentscheidung, der riskanteste Teil, nicht nur
Vor-/Nachbereitung.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List, Optional

from config import config
from logger import get_logger
from rich.console import Console

from bot.cycle_analysis_execute import execute_and_track
from bot.cycle_analysis_signals import resolve_local_signals

log = get_logger(__name__)
console = Console()


def run_ticker_loop(
    active_watchlist: List[str],
    *,
    portfolio, broker, strategy, executor, tracker, reflection, earnings_strategy,
    archive, collectors, analyzer,
    cycle_actions: List[str],
    regime: str,
    lessons_memo,
    weekly_briefing,
    macro_brief: str,
    eu_market_ctx,
    bench_geo_contexts: dict,
    force_claude_tickers: set,
    headline_meta: dict,
    headline_results: List[str],
    mech_conv: dict,
    mech_brief_fn: Callable,
    prefetch_analysis: dict,
    prefetch_news: dict,
    prefetch_price: dict,
    frugal_cache_hours: int,
    announce_start: bool,
    wl_total: int,
    hb_every: int,
    live,
    vel_analyzer, mtf_sentiment, reentry_tracker, chart_analyzer,
    normalize_ticker: Callable[[str], str],
    is_crypto: Callable[[str], bool],
    is_eu_stock: Callable[[str], bool],
    collect_news: Callable,
    ensure_current_price: Callable,
    valid_price: Callable,
    print_analysis: Callable,
    news_velocity_cls, multi_timeframe_sentiment_cls,
    reentry_tracker_cls, chart_pattern_analyzer_cls,
    telegram_notifier_cls,
    analysis_cache, analysis_log, earnings_predictor, signal_expander,
    rl_agent, get_experience_store: Callable,
    prompt_archive=None,
) -> None:
    """Serielle Analyse-Schleife über `active_watchlist`. Mutiert cycle_actions
    und headline_results in place, gibt sonst nichts zurück (lessons_memo wird
    nur schleifenintern über Iterationen hinweg weitergereicht)."""
    # ── Alias-Block: bindet die Parameter an die Original-Namen aus runner.py,
    # damit der Schleifenkörper unten wortwörtlich übernommen werden kann. ──
    _live = live
    _macro_brief = macro_brief
    # Verarbeitungs-Trace (Roadmap 1.4c): welche Makro-Bausteine tatsächlich in
    # _macro_brief eingeflossen sind. Einmal pro Zyklus (nicht pro Ticker) –
    # snapshot() ist ohnehin 30min gecacht, macro_brief ist zyklusweit gleich.
    try:
        from analyzers.macro_context import get_macro_context, summarize_sources
        _macro_sources = summarize_sources(get_macro_context().snapshot())
    except Exception:
        _macro_sources = {}
    _eu_market_ctx = eu_market_ctx
    _bench_geo_contexts = bench_geo_contexts
    _force_claude_tickers = force_claude_tickers
    _headline_meta = headline_meta
    _headline_results = headline_results
    _mech_conv = mech_conv
    _mech_brief_fn = mech_brief_fn
    _prefetch_analysis = prefetch_analysis
    _prefetch_news = prefetch_news
    _prefetch_price = prefetch_price
    _frugal_cache_hours = frugal_cache_hours
    _wl_total = wl_total
    _hb_every = hb_every
    _vel_analyzer = vel_analyzer
    _mtf_sentiment = mtf_sentiment
    _reentry_tracker = reentry_tracker
    _chart_analyzer = chart_analyzer
    _normalize_ticker = normalize_ticker
    _is_crypto = is_crypto
    _is_eu_stock = is_eu_stock
    _ensure_current_price = ensure_current_price
    _valid_price = valid_price
    _print_analysis = print_analysis
    _analysis_cache = analysis_cache
    _analysis_log = analysis_log
    _prompt_archive = prompt_archive
    _earnings_predictor = earnings_predictor
    _signal_expander = signal_expander
    _rl_agent = rl_agent
    _get_experience_store = get_experience_store
    NewsVelocityAnalyzer = news_velocity_cls
    MultiTimeframeSentiment = multi_timeframe_sentiment_cls
    ReEntryTracker = reentry_tracker_cls
    ChartPatternAnalyzer = chart_pattern_analyzer_cls
    TelegramNotifier = telegram_notifier_cls

    for _wl_idx, ticker in enumerate(active_watchlist, start=1):
        ticker = _normalize_ticker(ticker)
        _live.set_phase("Analyse", ticker=ticker, idx=_wl_idx, total=_wl_total)
        _gate = None   # Daten-Qualitäts-Gate: bleibt None für Krypto (kein Gate-Lauf)

        # Heartbeat: periodisches Lebenszeichen während des langen Zyklus. Nur bei
        # der angekündigten Hauptanalyse (announce_start) – intraday/getriggerte
        # Zyklen laufen still, dort ist "Analyse läuft"-Spam unerwünscht.
        if announce_start and _hb_every and _wl_idx > 1 and (_wl_idx - 1) % _hb_every == 0:
            try:
                _done = _wl_idx - 1
                _pct = int(_done / _wl_total * 100) if _wl_total else 0
                _trades = sum(1 for a in cycle_actions if "GEKAUFT" in a or "VERKAUFT" in a)
                TelegramNotifier().send(
                    f"⏳ <b>Analyse läuft</b> – {_done}/{_wl_total} Titel ({_pct}%)"
                    + (f"\n💼 {_trades} Trade(s) bisher" if _trades else "")
                )
            except Exception:
                pass

        # Frugal-Modus: frisch gecachte Ticker überspringen (spart ~50% Ollama-Calls)
        if config.frugal_mode and not portfolio.get_position(ticker):
            cached = _analysis_cache.get(ticker)
            if cached:
                _cached_at = cached.get("updated_at", "")
                if _cached_at:
                    try:
                        _age_h = (datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(_cached_at.replace("Z", ""))).total_seconds() / 3600
                        if _age_h < _frugal_cache_hours:
                            log.debug("[%s] Frugal: Cache %dh alt – übersprungen", ticker, int(_age_h))
                            continue
                    except Exception:
                        pass

        console.print(f"\n[cyan]Analysiere {ticker}...[/cyan]")

        _ctx = _prefetch_analysis.get(ticker)
        if _ctx is not None:
            news = _ctx["news"]
            sources_breakdown = _ctx["sources_breakdown"]
            price_data = _ctx["price_data"]
        else:
            _pf = _prefetch_news.get(ticker)
            if _pf:
                news, sources_breakdown = _pf
            else:
                news, sources_breakdown = collect_news(ticker, archive, collectors)
            price_data = _prefetch_price.get(ticker) or (
                {"current_price": broker.get_crypto_price(ticker), "volume": 0}
                if _is_crypto(ticker) else collectors["yahoo"].get_price_data(ticker)
            )

        # Yahoo leer? → IBKR-Delayed (+yfinance) als Fallback, BEVOR die Schranke
        # greift. Macht Titel handelbar, die die Primärquelle nicht quotet.
        # Serieller Pfad → broker-Aufruf ist hier thread-safe (s. _ensure_current_price).
        if not _is_crypto(ticker):
            price_data = _ensure_current_price(ticker, price_data, broker)

        # Daten-Qualitäts-Gate vor Claude (Roadmap 1.8): ungültiger Kurs
        # (None/NaN/inf/≤0 — die alte _valid_price-Schranke), veralteter Kurs
        # (stale) oder Skalenfehler (Kurs weit außerhalb der 52W-Spanne) →
        # Claude-Aufruf sparen, SKIP loggen statt mit Müll zu rechnen.
        # Bereinigt zudem nicht-finite Begleitfelder (NaN-Falle) in place.
        if not _is_crypto(ticker):
            from analyzers.data_quality import check_price_data
            _gate = check_price_data(ticker, price_data)
            if _gate.sanitized_fields:
                log.debug("[%s] Daten-Gate: nicht-finite Felder bereinigt: %s",
                          ticker, ", ".join(_gate.sanitized_fields))
            if not _gate.ok:
                console.print(f"  [dim]⛔ Daten-Gate {ticker}: {_gate.reason} – übersprungen[/dim]")
                _live.feed_emit("gate_blocked", ticker=ticker, detail=_gate.reason)
                try:
                    from analyzers.decision_log import get_decision_log
                    _dg = get_decision_log()
                    if _dg is not None:
                        _dg.log({"ticker": ticker, "action": "SKIP",
                                 "reason": f"Daten-Gate: {_gate.reason}",
                                 "source": "cycle",
                                 "regime": str(regime) if regime else None})
                except Exception:
                    pass
                continue

        # Lokale Signal-Anreicherung vor Claude (Roadmap 4.4a-Folge, ausgelagert
        # nach bot/cycle_analysis_signals.py): FinBERT/Insider-Cluster/Signal-
        # Expander/News-Geschwindigkeit/Multi-Zeitrahmen-Sentiment/Re-Entry-
        # Preis-Update/Earnings-Prognose/Chart-Muster/On-Chain.
        _sig = resolve_local_signals(
            ticker, news, sources_breakdown, price_data, _ctx,
            archive=archive, signal_expander=_signal_expander, strategy=strategy,
            is_crypto=_is_crypto(ticker), console=console,
            vel_analyzer=_vel_analyzer, news_velocity_cls=NewsVelocityAnalyzer,
            mtf_sentiment=_mtf_sentiment, multi_timeframe_sentiment_cls=MultiTimeframeSentiment,
            reentry_tracker=_reentry_tracker, reentry_tracker_cls=ReEntryTracker, broker=broker,
            earnings_predictor=_earnings_predictor,
            chart_analyzer=_chart_analyzer, chart_pattern_analyzer_cls=ChartPatternAnalyzer,
        )
        if _sig.skip:
            continue
        news = _sig.news
        historical = _sig.historical
        open_position_ctx = _sig.open_position_ctx
        pattern_result = _sig.pattern_result
        onchain_snapshot = _sig.onchain_snapshot

        _geo_ctx = _bench_geo_contexts.get(ticker)
        if _ctx is not None and _ctx.get("analysis") is not None:
            analysis = _ctx["analysis"]  # parallel vorberechnet
        else:
            console.print(f"  [cyan]Analysiere mit Claude ({config.claude_model})...[/cyan]")
            if _geo_ctx:
                log.info("[%s] Geopolitischer Kontext wird an Claude übergeben: %s",
                         ticker, _geo_ctx.get("kategorie", ""))
            analysis = analyzer.analyze(
                ticker=ticker,
                news_items=news,
                price_data=price_data,
                historical_news=historical if historical else None,
                open_position=open_position_ctx,
                lessons_memo=lessons_memo,
                weekly_briefing=weekly_briefing,
                pattern_result=pattern_result,
                onchain_snapshot=onchain_snapshot,
                eu_market_snapshot=_eu_market_ctx if _is_eu_stock(ticker) else None,
                geo_context=_geo_ctx,
                macro_brief=_macro_brief,
                mechanical_brief=_mech_brief_fn(ticker, _mech_conv, regime),
                force_claude=ticker in _force_claude_tickers,
            )

        # Bugfix 20.7.2026: ClaudeAnalyzer setzt analysis.sources_used nie
        # (Dataclass-Default bleibt {}), aber swing_strategy.evaluate() prüft
        # genau dieses Feld gegen config.min_sources – jeder Claude-Kauf wurde
        # dadurch fälschlich mit "Zu wenige Quellen (0 < 1)" blockiert, obwohl
        # sources_breakdown (dieselbe Zahl, die ins Analysis-Log geht) echte
        # Treffer zeigt. Nur auffüllen, wenn leer – Pfade, die sources_used
        # bereits selbst setzen (z.B. multi_agent_analyzer), bleiben unberührt.
        if not getattr(analysis, "sources_used", None):
            analysis.sources_used = sources_breakdown

        _print_analysis(analysis)
        # store(ticker, direction, sentiment_score, confidence, recommendation)
        # confidence = Konviktion (HIGH/MEDIUM/LOW), recommendation = Aktion (BUY/HOLD/SKIP).
        # Reihenfolge muss zur Signatur passen, sonst landen Aktion und Konviktion
        # vertauscht im Cache (Dashboard zeigt dann ~0 BUY, TV-Filter falsch).
        _analysis_cache.store(
            ticker, analysis.direction, analysis.sentiment_score,
            analysis.confidence, analysis.recommendation,
        )
        # Echte Nicht-Signale (Score ~0.5, HOLD/SKIP, keine Position) nicht ins Log –
        # verhindert Flut von sinnlosen 0.5-Einträgen.
        _is_noise = (
            0.43 <= analysis.sentiment_score <= 0.57
            and analysis.recommendation in ("HOLD", "SKIP")
            and not portfolio.get_position(ticker)
            and ticker not in _force_claude_tickers
        )
        _analysis_row_id = None
        if not _is_noise:
            # Persistenz-Fehler eines einzelnen Tickers darf den gesamten
            # Analyse-Zyklus nicht abreißen (vgl. sources_used-dict-Crash).
            try:
                # Verarbeitungs-Trace (Roadmap 1.4c): Modell-Route/Grund kommen
                # direkt vom AnalysisResult (dort zentral gestempelt), Makro-
                # Bausteine/Daten-Gate werden hier je Ticker zusammengeführt.
                _provenance = {
                    "model_route": analysis.model_route,
                    "frugal_reason": analysis.frugal_reason,
                    "macro_sources": _macro_sources,
                    "gate_ok": _gate.ok if _gate is not None else None,
                    "gate_reason": _gate.reason if _gate is not None else None,
                    "gate_sanitized_fields": _gate.sanitized_fields if _gate is not None else [],
                }
                # Zeilen-ID einfangen: verkettet die Entscheidung unten mit
                # ihrer Analyse samt Quellen-Breakdown (Roadmap 1.4b).
                _analysis_row_id = _analysis_log.store(
                    analysis, sources_breakdown=sources_breakdown, provenance=_provenance)
            except Exception as _store_err:
                log.warning("Analysis-Log store(%s) fehlgeschlagen: %s", ticker, _store_err)
            # KI-Prompt-Archiv (Roadmap 1.4d): nur bei einem ECHTEN Claude-Aufruf
            # gesetzt (raw_response leer bei Ollama/Frugal/Cache-Hit-Routen,
            # siehe claude_analyzer._result_cache_store) und nur wenn die
            # Analyse überhaupt eine Zeilen-ID bekommen hat (sonst nichts zum
            # Verketten). Basis für Entscheidungs-Replay (Roadmap 4.5).
            if _prompt_archive is not None and _analysis_row_id is not None and analysis.raw_response:
                try:
                    _prompt_archive.store(
                        analysis_id=_analysis_row_id, ticker=ticker,
                        model=analysis.raw_model,
                        system_prompt=analysis.raw_system_prompt,
                        user_prompt=analysis.raw_user_prompt,
                        response_text=analysis.raw_response,
                    )
                except Exception as _archive_err:
                    log.warning("Prompt-Archiv store(%s) fehlgeschlagen: %s", ticker, _archive_err)
            _live.feed_emit(
                "analysis_done", ticker=ticker,
                detail=f"{analysis.recommendation} · Score "
                       f"{analysis.sentiment_score:.2f} · {analysis.confidence}",
            )

        # Headline-Signal-Ticker: Ergebnis als kompakte Zeile sammeln. Versand
        # erfolgt gebündelt am Zyklus-Ende (eine Digest-Nachricht), nicht pro Aktie.
        if ticker in _headline_meta:
            try:
                _cur = price_data.get("current_price") if price_data else None
                _price_str = f" @ ${_cur:.2f}" if _cur else ""
                _rec = analysis.recommendation
                _rec_icon = {"BUY": "🟢", "SKIP": "⏭️"}.get(_rec, "ℹ️")
                _headline_results.append(
                    f"{_rec_icon} <b>{ticker}</b> {_rec} "
                    f"({analysis.sentiment_score:.2f}){_price_str}"
                )
            except Exception as _fu_err:
                log.debug("Headline-Ergebnis sammeln fehlgeschlagen: %s", _fu_err)

        # Bei SKIP mit bullischem Potential: Conditional Entry speichern
        if (
            analysis.recommendation == "SKIP"
            and analysis.entry_trigger_price
            and analysis.sentiment_score >= 0.50
            and analysis.direction in ("BULLISH", "NEUTRAL")
        ):
            try:
                from analyzers.conditional_entry import ConditionalEntryWatcher
                _cur_price = price_data.get("current_price", 0) if price_data else 0
                if _cur_price and analysis.entry_trigger_price < _cur_price:
                    _ce = ConditionalEntryWatcher.build(
                        ticker, analysis.entry_trigger_price, _cur_price, analysis
                    )
                    ConditionalEntryWatcher().add(_ce)
                    console.print(
                        f"  [yellow]📌 Conditional Entry gesetzt: {ticker} – "
                        f"Kauf bei ${analysis.entry_trigger_price:.2f} "
                        f"({_ce.pct_to_trigger:.1f}% unter aktuellem Kurs, "
                        f"Ablauf {_ce.expires_at[:10]})[/yellow]"
                    )
            except Exception as _ce_err:
                log.debug("Conditional Entry konnte nicht gespeichert werden: %s", _ce_err)

        # Bei BUY: verwandte Aktien ins Netz aufnehmen
        if analysis.recommendation == "BUY" and analysis.related_tickers:
            try:
                from analyzers.stock_relations import StockRelations
                from analyzers.bench_list import BenchList
                StockRelations().add_relation(
                    ticker, analysis.related_tickers, analysis.entry_rationale
                )
                bench = BenchList()
                for rt in analysis.related_tickers:
                    bench.add(
                        rt,
                        score=round(analysis.sentiment_score * 0.85, 3),
                        reason=f"Verwandt mit {ticker}: {analysis.entry_rationale[:80]}",
                    )
                console.print(
                    f"  [cyan]🔗 Verwandte Aktien → BenchList: "
                    f"{', '.join(analysis.related_tickers)}[/cyan]"
                )
            except Exception as e:
                log.debug("Stock-Relations Fehler: %s", e)

        # Handelsentscheidung, Ausführung, Tracking (Roadmap 4.4a-Folge,
        # ausgelagert nach bot/cycle_analysis_execute.py): RL-Veto-gesteuertes
        # evaluate()+Executor, Decision-Log, Prediction-Tracker, Experience-
        # Store, Earnings-Strategy, Korrektur-Follow-Up.
        lessons_memo = execute_and_track(
            ticker, price_data, analysis, sources_breakdown, regime,
            config=config, valid_price=_valid_price,
            strategy=strategy, executor=executor, rl_agent=_rl_agent,
            console=console, cycle_actions=cycle_actions, live=_live,
            tracker=tracker, get_experience_store=_get_experience_store,
            reflection=reflection, lessons_memo=lessons_memo,
            earnings_strategy=earnings_strategy, portfolio=portfolio, broker=broker,
            headline_meta=_headline_meta, telegram_notifier_cls=TelegramNotifier,
            analysis_row_id=_analysis_row_id,
        )
