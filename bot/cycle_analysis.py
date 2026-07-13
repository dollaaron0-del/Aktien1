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
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional

from analyzers.onchain_signals import OnChainSignalAnalyzer
from collectors.onchain_collector import OnChainCollector
from config import config
from logger import get_logger
from rich.console import Console

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
) -> None:
    """Serielle Analyse-Schleife über `active_watchlist`. Mutiert cycle_actions
    und headline_results in place, gibt sonst nichts zurück (lessons_memo wird
    nur schleifenintern über Iterationen hinweg weitergereicht)."""
    # ── Alias-Block: bindet die Parameter an die Original-Namen aus runner.py,
    # damit der Schleifenkörper unten wortwörtlich übernommen werden kann. ──
    _live = live
    _macro_brief = macro_brief
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

        # ── FinBERT lokales Sentiment ─────────────────────────────────────────
        try:
            from analyzers.finbert_analyzer import FinBERTAnalyzer
            _finbert = FinBERTAnalyzer()
            if _ctx is None and _finbert.is_available() and news:
                _headlines = [
                    (item.get("title") or item.get("text") or "")[:120]
                    for item in news if item.get("title") or item.get("text")
                ]
                if _headlines:
                    _fb = _finbert.analyze_headlines(_headlines)
                    _fb_color = {"POSITIVE": "green", "NEGATIVE": "red", "NEUTRAL": "yellow"}[_fb["label"]]
                    console.print(
                        f"  [{_fb_color}]🤖 FinBERT: {_fb['label']} "
                        f"(Score {_fb['score']:.2f}, {_fb['confidence']}) "
                        f"| +{_fb['pos_pct']}% / -{_fb['neg_pct']}%[/{_fb_color}]"
                    )
                    _fb_item = _finbert.build_signal_item(ticker, _fb)
                    if _fb_item:
                        news = [_fb_item] + news  # prepend so Claude sees it first
        except Exception as _fbe:
            log.debug("FinBERT fehlgeschlagen: %s", _fbe)

        # ── Insider-Cluster-Detection ─────────────────────────────────────────
        try:
            _insider_items = [
                i for i in news
                if (i.get("source") or "").startswith("SEC-Form4")
                and "gekauft" in (i.get("title") or "").lower()
            ]
            if len(_insider_items) >= 2:
                _names = [i.get("person") or "Insider" for i in _insider_items[:4]]
                console.print(
                    f"  [bold green]👥 Insider-Cluster: {len(_insider_items)} Käufe "
                    f"({', '.join(_names[:3])}{'...' if len(_names) > 3 else ''})[/bold green]"
                )
        except Exception:
            pass

        # Feed news items to signal expander – detects unknown small-cap tickers
        new_signal_tickers = _signal_expander.process_news_items(news)
        if new_signal_tickers:
            console.print(f"  [magenta]📡 Neue Signal-Ticker entdeckt: {', '.join(new_signal_tickers)}[/magenta]")

        # Load 30-day history, excluding articles already in current batch
        current_titles = {item.get("title") or "" for item in news}
        historical = archive.get_history(ticker, days=30, exclude_titles=current_titles)

        src = sources_breakdown
        console.print(
            f"  [bold]{len(news)}[/bold] Artikel total | {len(historical)} historisch | "
            f"Yahoo:{src['yahoo']} NewsAPI:{src['newsapi']} "
            f"SEC:{src['sec_edgar']} Wire:{src['wire']} "
            f"Twits:{src['stocktwits']} Twitter:{src.get('twitter', 0)} Insider:{src['insider']} "
            f"Contracts:{src['usaspending']} OptFlow:{src['options_flow']} "
            f"EU:{src['european_news']} | "
            f"Kurs: ${price_data.get('current_price', 'N/A')}"
        )

        if not news:
            console.print("  [dim]Keine Nachrichten – übersprungen[/dim]")
            continue

        # News-Geschwindigkeit anzeigen
        try:
            vel = (_vel_analyzer or NewsVelocityAnalyzer()).analyze(ticker)
            if vel.acceleration in ("SPIKE", "HIGH"):
                boost_str = f" (Signal-Boost ×{vel.signal_boost:.2f})" if vel.signal_boost > 1.0 else ""
                color = "bold yellow" if vel.acceleration == "SPIKE" else "yellow"
                console.print(
                    f"  [{color}]📡 Nachrichten-{vel.acceleration}: "
                    f"{vel.articles_24h} Artikel/24h (Basis: {vel.baseline_per_day:.0f}/Tag){boost_str}[/{color}]"
                )
        except Exception:
            pass

        # Multi-Zeitrahmen-Sentiment (1d/7d/30d)
        try:
            hist_30d = archive.get_history(ticker, days=30)
            by_date: dict = defaultdict(list)
            for item in hist_30d:
                pub = (item.get("published") or item.get("timestamp") or "")[:10]
                if pub and item.get("sentiment_score") is not None:
                    by_date[pub].append(item)
            for item in news:
                today_key = date.today().isoformat()
                if item.get("sentiment_score") is not None:
                    by_date[today_key].append(item)
            if len(by_date) >= 2:
                _mtf = _mtf_sentiment or MultiTimeframeSentiment()
                mtf_result = _mtf.analyze(ticker, dict(by_date))
                mtf_line = _mtf.to_text(mtf_result)
                if mtf_result.trend in ("UPTREND", "DOWNTREND"):
                    t_color = "green" if mtf_result.trend == "UPTREND" else "red"
                    console.print(f"  [{t_color}]📈 {mtf_line}[/{t_color}]")
        except Exception:
            pass

        # Re-Entry-Tracker: Preise aktualisieren
        try:
            rt = _reentry_tracker or ReEntryTracker()
            tickers_watched = [c.ticker for c in rt.get_all_watched()]
            if tickers_watched:
                watch_prices = broker.get_prices(tickers_watched)
                rt.update_prices(watch_prices)
        except Exception:
            pass

        # Build open-position context for thesis check
        open_position_ctx = strategy.build_open_position_context(ticker)
        if open_position_ctx:
            console.print(f"  [yellow]Offene Position – prüfe Kaufthese...[/yellow]")

        # Earnings Surprise Predictor (Stufe 2) – vor Claude-Analyse
        try:
            ep = _earnings_predictor.predict(ticker)
            if ep.get("prediction") in ("BEAT", "MISS"):
                ep_color = "green" if ep["prediction"] == "BEAT" else "red"
                console.print(
                    f"  [{ep_color}]🔮 Earnings-Prognose: {ep['prediction']} "
                    f"(Konfidenz: {ep.get('confidence','LOW')}, "
                    f"Score: {ep.get('score', 0):.2f})[/{ep_color}]"
                )
        except Exception:
            pass

        # Chart-Muster-Analyse (für Krypto primär, für Aktien als Bestätigung)
        pattern_result = None
        try:
            pattern_result = (
                _ctx["pattern_result"] if _ctx is not None
                else (_chart_analyzer or ChartPatternAnalyzer()).analyze(ticker)
            )
            if pattern_result:
                sig_color = {"STRONG_BUY": "bold green", "BUY": "green",
                             "STRONG_SELL": "bold red", "SELL": "red"}.get(
                    pattern_result.primary_signal, "yellow"
                )
                patterns_found = ", ".join(p.name for p in pattern_result.patterns) if pattern_result.patterns else "–"
                console.print(
                    f"  [{sig_color}]📊 Chart-Signal: {pattern_result.primary_signal} "
                    f"(Score: {pattern_result.score:.0f}) | Muster: {patterns_found}[/{sig_color}]"
                )
        except Exception as e:
            log.debug("[%s] Chart-Muster-Analyse fehlgeschlagen: %s", ticker, e)

        # On-Chain-Analyse (nur für Krypto-Assets)
        onchain_snapshot = None
        if _ctx is not None:
            onchain_snapshot = _ctx.get("onchain")
        elif _is_crypto(ticker):
            try:
                base = ticker.split("/")[0].upper().removesuffix("-USD")
                metrics = OnChainCollector().collect(base)
                onchain_snapshot = OnChainSignalAnalyzer().analyze(metrics)
                if onchain_snapshot:
                    oc_color = {
                        "STRONG_BULLISH": "bold green", "BULLISH": "green",
                        "STRONG_BEARISH": "bold red",   "BEARISH": "red",
                    }.get(onchain_snapshot.signal, "yellow")
                    console.print(
                        f"  [{oc_color}]⛓ On-Chain: {onchain_snapshot.signal} "
                        f"(Score: {onchain_snapshot.score:.0f}) "
                        f"[{onchain_snapshot.source}][/{oc_color}]"
                    )
            except Exception as e:
                log.debug("[%s] On-Chain-Analyse fehlgeschlagen: %s", ticker, e)

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
                # Zeilen-ID einfangen: verkettet die Entscheidung unten mit
                # ihrer Analyse samt Quellen-Breakdown (Roadmap 1.4b).
                _analysis_row_id = _analysis_log.store(
                    analysis, sources_breakdown=sources_breakdown)
            except Exception as _store_err:
                log.warning("Analysis-Log store(%s) fehlgeschlagen: %s", ticker, _store_err)
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

        _raw_px = (price_data or {}).get("current_price")
        _cur_px = float(_raw_px) if _valid_price(_raw_px) else 0.0
        _result = None
        if _cur_px > 0:
            # Fehler bei EINEM Ticker darf den restlichen Zyklus nicht abreißen
            # und muss sichtbar sein (nicht still verschluckt – vgl. Execution-Bug).
            try:
                _rl = _rl_agent if getattr(config, "rl_veto_enabled", False) else None
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
                    "analysis_id": _analysis_row_id,
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
                _live.feed_emit("trade", ticker=ticker, detail=action)
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
                        _es = _get_experience_store()
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
            ticker in _headline_meta
            and analysis.recommendation == "BUY"
            and (action is None or "GEKAUFT" not in action)
        ):
            try:
                _block_reason = action or f"[{ticker}] Kein Kauf (Filter-Details in Logs)"
                TelegramNotifier().send(
                    f"⚠️ <b>{ticker} – Kauf nicht ausgeführt</b>\n\n"
                    f"Claude: BUY (Score {analysis.sentiment_score:.2f}) – aber Trade blockiert:\n"
                    f"{_block_reason.replace(f'[{ticker}] ', '')}"
                )
            except Exception as _corr_err:
                log.debug("Korrektur-Follow-Up fehlgeschlagen: %s", _corr_err)
