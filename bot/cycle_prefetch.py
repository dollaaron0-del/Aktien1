"""
bot/cycle_prefetch.py – paralleles Vorladen von News/Preis + optionale
Analyse-Vorberechnung für die komplette Watchlist eines Zyklus.

Ausgelagert aus bot/runner.py (Roadmap 4.4a, 500-Zeilen-Regel): der Zyklus-
Schritt direkt vor der eigentlichen seriellen Analyse-Schleife. Zwei Stufen:
  1. News+Preis für ALLE Ticker parallel laden (ThreadPoolExecutor) statt
     seriell pro Ticker zu warten.
  2. Optional (PARALLEL_ANALYSIS, mehrere Ticker) den teuersten Teil – der
     eigentliche Claude/Ollama-Aufruf inkl. Chart/History/FinBERT – ebenfalls
     vorab parallel berechnen. Beides ist read-only/thread-safe; alle JSON-
     Schreiber (velocity, earnings, signal_expander, cache, log) UND Trades
     bleiben bewusst in der seriellen Schleife in runner.py.

Analyzer-Klassen (NewsVelocityAnalyzer/MultiTimeframeSentiment/ReEntryTracker/
ChartPatternAnalyzer) und `collect_news`/die Ticker-Helfer werden bewusst als
Parameter hereingereicht statt hier frisch importiert: runner.py löst sie bei
JEDEM Aufruf aus dem eigenen Modul-Namensraum auf, wodurch Tests, die dort
`monkeypatch.setattr(runner_mod, "NewsVelocityAnalyzer", Fake)` setzen,
weiterhin greifen (ein eigener Import hier würde die gepatchte Fake-Klasse
nicht sehen).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)


@dataclass
class PrefetchResult:
    vel_analyzer: object
    mtf_sentiment: object
    reentry_tracker: object
    chart_analyzer: object
    mech_conv: Dict[str, dict]
    mech_brief_fn: Callable
    news: Dict[str, tuple] = field(default_factory=dict)
    price: Dict[str, dict] = field(default_factory=dict)
    analysis: Dict[str, dict] = field(default_factory=dict)


def run_prefetch(
    active_watchlist: List[str],
    *,
    broker,
    strategy,
    analyzer,
    archive,
    collectors: Dict,
    lessons_memo,
    weekly_briefing,
    macro_brief: str,
    eu_market_ctx,
    bench_geo_contexts: dict,
    regime: str,
    force_claude_tickers: set,
    multi_agent_enabled: bool,
    live,
    normalize_ticker: Callable[[str], str],
    is_crypto: Callable[[str], bool],
    is_eu_stock: Callable[[str], bool],
    collect_news: Callable,
    news_velocity_cls,
    multi_timeframe_sentiment_cls,
    reentry_tracker_cls,
    chart_pattern_analyzer_cls,
) -> PrefetchResult:
    """News+Preis vorladen, danach optional die Analyse vorberechnen. Gibt
    die Analyzer-Singletons + alle Prefetch-Dicts als PrefetchResult zurück –
    runner.py's serielle Schleife greift für jeden Ticker zuerst hierauf
    zurück (Cache-Hit) und fällt sonst auf ihren eigenen seriellen Pfad
    zurück (z.B. wenn PARALLEL_ANALYSIS aus ist oder ein Ticker fehlschlug)."""
    # Analyzer singletons — created once, reused across all tickers in this cycle
    try:
        vel_analyzer    = news_velocity_cls()
        mtf_sentiment   = multi_timeframe_sentiment_cls()
        reentry_tracker = reentry_tracker_cls()
        chart_analyzer  = chart_pattern_analyzer_cls()
    except Exception:
        vel_analyzer = mtf_sentiment = reentry_tracker = chart_analyzer = None

    # Pre-fetch news + price data for ALL tickers in parallel before the analysis loop.
    # This eliminates sequential waiting: all 12 tickers fetch their 30 collectors simultaneously.
    normalized_watchlist = [normalize_ticker(t) for t in active_watchlist]
    prefetch_news:  Dict[str, tuple] = {}
    prefetch_price: Dict[str, dict]  = {}

    # ── strategy_lab Live-Bridge (Roadmap d) – STANDARD AUS, flaggengeschützt ──
    # Liefert nur bei gesetztem STRATEGY_LAB_LIVE eine mechanische Konviktion je
    # Ticker (additiver Analyse-Kontext, kein Auto-Trade). Komplett defensiv:
    # ein Fehler hier darf den Zyklus nie reißen.
    mech_conv: Dict[str, dict] = {}
    mech_brief_fn = lambda _t, _m, _r=None: ""   # immer definiert; "" = kein Zusatzkontext
    try:
        from strategy_lab import live_bridge as _live_bridge
        if _live_bridge.is_enabled():
            mech_conv = _live_bridge.conviction_map(normalized_watchlist)
            mech_brief_fn = _live_bridge.brief_for
            if mech_conv:
                log.info("strategy_lab Live-Bridge aktiv: mechanische Konviktion für %d Ticker",
                         len(mech_conv))
    except Exception as _lbe:
        log.debug("Live-Bridge übersprungen: %s", _lbe)
        mech_conv = {}

    def _prefetch_ticker(t: str):
        news_result = collect_news(t, archive, collectors)
        if is_crypto(t):
            price = {"current_price": broker.get_crypto_price(t), "volume": 0}
        else:
            price = collectors["yahoo"].get_price_data(t)
        return t, news_result, price

    live.set_phase("Vorladen", total=len(normalized_watchlist))
    pf_workers = min(len(normalized_watchlist), int(os.getenv("PREFETCH_WORKERS", "8")))  # cap: avoid overwhelming APIs
    if pf_workers > 1:
        with ThreadPoolExecutor(max_workers=pf_workers) as pf_pool:
            pf_futures = {pf_pool.submit(_prefetch_ticker, t): t for t in normalized_watchlist}
            for pf_fut in as_completed(pf_futures):
                try:
                    t, nr, pr = pf_fut.result()
                    prefetch_news[t]  = nr
                    prefetch_price[t] = pr
                except Exception as pfe:
                    t = pf_futures[pf_fut]
                    log.debug("Prefetch fehlgeschlagen für %s: %s", t, pfe)
    else:
        for t in normalized_watchlist:
            try:
                _, nr, pr = _prefetch_ticker(t)
                prefetch_news[t]  = nr
                prefetch_price[t] = pr
            except Exception:
                pass

    # ── Parallele Analyse-Vorberechnung ──────────────────────────────────────
    # Der teuerste Teil (Claude-Call + Chart + History + FinBERT) ist read-only
    # und thread-safe → vorab im Pool berechnen. Alle JSON-Schreiber (velocity,
    # earnings, signal_expander, cache, log) UND Trades bleiben seriell in der
    # Schleife. Per ENV abschaltbar (PARALLEL_ANALYSIS=false) als Kill-Switch.
    prefetch_analysis: Dict[str, dict] = {}

    def _precompute_analysis(t: str) -> Optional[dict]:
        pf = prefetch_news.get(t)
        if pf:
            news, sb = pf
        else:
            news, sb = collect_news(t, archive, collectors)
        price = prefetch_price.get(t) or (
            {"current_price": broker.get_crypto_price(t), "volume": 0}
            if is_crypto(t) else collectors["yahoo"].get_price_data(t)
        )
        if not is_crypto(t):
            # Daten-Qualitäts-Gate (Roadmap 1.8): ungültiger/veralteter/
            # unplausibler Kurs → Claude-Call sparen. Die serielle Schleife
            # prüft erneut und übernimmt das Logging (SKIP + Event).
            from analyzers.data_quality import check_price_data
            if not check_price_data(t, price).ok:
                return None
        # FinBERT-Signal voranstellen (read-only)
        try:
            from analyzers.finbert_analyzer import FinBERTAnalyzer
            fb_an = FinBERTAnalyzer()
            if fb_an.is_available() and news:
                hl = [(it.get("title") or it.get("text") or "")[:120]
                      for it in news if it.get("title") or it.get("text")]
                if hl:
                    fb_item = fb_an.build_signal_item(t, fb_an.analyze_headlines(hl))
                    if fb_item:
                        news = [fb_item] + news
        except Exception:
            pass
        if not news:
            return {"news": news, "sources_breakdown": sb, "price_data": price,
                    "analysis": None, "pattern_result": None, "onchain": None}
        cur_titles = {it.get("title") or "" for it in news}
        hist = archive.get_history(t, days=30, exclude_titles=cur_titles)
        opc = strategy.build_open_position_context(t)
        pat = None
        try:
            pat = (chart_analyzer or chart_pattern_analyzer_cls()).analyze(t)
        except Exception:
            pass
        oc = None
        if is_crypto(t):
            try:
                from analyzers.onchain_signals import OnChainSignalAnalyzer
                from collectors.onchain_collector import OnChainCollector
                base = t.split("/")[0].upper().removesuffix("-USD")
                oc = OnChainSignalAnalyzer().analyze(OnChainCollector().collect(base))
            except Exception:
                pass
        try:
            an = analyzer.analyze(
                ticker=t, news_items=news, price_data=price,
                historical_news=hist if hist else None, open_position=opc,
                lessons_memo=lessons_memo, weekly_briefing=weekly_briefing,
                pattern_result=pat, onchain_snapshot=oc,
                eu_market_snapshot=eu_market_ctx if is_eu_stock(t) else None,
                geo_context=bench_geo_contexts.get(t), macro_brief=macro_brief,
                mechanical_brief=mech_brief_fn(t, mech_conv, regime),
                force_claude=t in force_claude_tickers,
            )
        except Exception as ae:
            log.debug("Analyse-Prefetch analyze(%s) fehlgeschlagen: %s", t, ae)
            an = None
        return {"news": news, "sources_breakdown": sb, "price_data": price,
                "analysis": an, "pattern_result": pat, "onchain": oc}

    parallel_analysis = os.getenv("PARALLEL_ANALYSIS", "true").lower() in ("1", "true", "yes")
    an_workers = min(len(normalized_watchlist), int(os.getenv("ANALYSIS_WORKERS", "4")))
    # Auf reiner CPU zieht EINE lokale Ollama-Analyse bereits ~4–5 Kerne. Mehrere
    # parallele Analysen übersubskribieren die Kerne → jede Generierung kriecht in
    # ihr Timeout → Circuit Breaker schaltet Ollama ab → alles fällt auf das
    # budgetgedeckelte Claude → leere SKIP-Analysen ohne Kaufsignale (Befund 18.6.,
    # 0 Trades seit 15.6.). Daher die Analyse-Worker an den Ressourcen-Tier koppeln
    # (MINIMAL/CPU = 1 Worker), sofern ANALYSIS_WORKERS nicht explizit gesetzt ist.
    if "ANALYSIS_WORKERS" not in os.environ:
        try:
            from system.resource_manager import get_resource_manager
            rm_for_workers = get_resource_manager()
            rm_for_workers.update()
            an_workers = max(1, min(an_workers, rm_for_workers.max_workers()))
        except Exception as rmw_err:
            log.debug("Worker-Cap via Resource-Manager übersprungen: %s", rmw_err)
    if parallel_analysis and an_workers > 1 and not multi_agent_enabled:
        log.info("Analyse-Prefetch: %d Titel mit %d Workern", len(normalized_watchlist), an_workers)
        with ThreadPoolExecutor(max_workers=an_workers) as an_pool:
            an_futs = {an_pool.submit(_precompute_analysis, t): t for t in normalized_watchlist}
            for an_fut in as_completed(an_futs):
                t = an_futs[an_fut]
                try:
                    ctx_r = an_fut.result()
                    if ctx_r:
                        prefetch_analysis[t] = ctx_r
                except Exception as ace:
                    log.debug("Analyse-Prefetch fehlgeschlagen für %s: %s", t, ace)

    return PrefetchResult(
        vel_analyzer=vel_analyzer, mtf_sentiment=mtf_sentiment,
        reentry_tracker=reentry_tracker, chart_analyzer=chart_analyzer,
        mech_conv=mech_conv, mech_brief_fn=mech_brief_fn,
        news=prefetch_news, price=prefetch_price, analysis=prefetch_analysis,
    )
