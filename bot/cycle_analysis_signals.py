"""
bot/cycle_analysis_signals.py – lokale Signal-Anreicherung vor der Claude-
Analyse, ausgelagert aus bot/cycle_analysis.py (Roadmap 4.4a-Folge,
500-Zeilen-Regel: cycle_analysis.py lag mit 709 Zeilen weiter über der
Grenze, dies ist der erste von zwei Schnitten).

FinBERT/Insider-Cluster/Signal-Expander/News-Geschwindigkeit/Multi-
Zeitrahmen-Sentiment/Re-Entry-Preis-Update/Earnings-Prognose/Chart-Muster/
On-Chain – alles rein lesend bzw. lokal (kein Claude-Aufruf, kein Trade).
Funktionskörper wortwörtlich aus cycle_analysis.py übernommen (dasselbe
Verschiebungs-Prinzip wie bei den bisherigen 4.4a-Schnitten): kein Verhalten
geändert, nur die Kapselung. Rückgabe bündelt alles, was die anschließende
Claude-Analyse in cycle_analysis.py noch braucht (news kann sich durch den
FinBERT-Prepend geändert haben, daher als Rückgabewert statt In-Place-
Mutation – dieselbe Neu-Bindung wie im Original).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, List, Optional

from analyzers.onchain_signals import OnChainSignalAnalyzer
from collectors.onchain_collector import OnChainCollector
from logger import get_logger

log = get_logger(__name__)


@dataclass
class LocalSignalResult:
    news: List[dict]
    skip: bool
    historical: List[dict] = field(default_factory=list)
    open_position_ctx: Optional[dict] = None
    pattern_result: object = None
    onchain_snapshot: object = None


def resolve_local_signals(
    ticker: str,
    news: List[dict],
    sources_breakdown: dict,
    price_data: dict,
    ctx: Optional[dict],
    *,
    archive, signal_expander, strategy,
    is_crypto: bool,
    console,
    vel_analyzer, news_velocity_cls,
    mtf_sentiment, multi_timeframe_sentiment_cls,
    reentry_tracker, reentry_tracker_cls, broker,
    earnings_predictor,
    chart_analyzer, chart_pattern_analyzer_cls,
) -> LocalSignalResult:
    """Siehe Modul-Docstring."""
    # ── FinBERT lokales Sentiment ─────────────────────────────────────────
    try:
        from analyzers.finbert_analyzer import FinBERTAnalyzer
        _finbert = FinBERTAnalyzer()
        if ctx is None and _finbert.is_available() and news:
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
    new_signal_tickers = signal_expander.process_news_items(news)
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
        return LocalSignalResult(news=news, skip=True)

    # News-Geschwindigkeit anzeigen
    try:
        vel = (vel_analyzer or news_velocity_cls()).analyze(ticker)
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
            _mtf = mtf_sentiment or multi_timeframe_sentiment_cls()
            mtf_result = _mtf.analyze(ticker, dict(by_date))
            mtf_line = _mtf.to_text(mtf_result)
            if mtf_result.trend in ("UPTREND", "DOWNTREND"):
                t_color = "green" if mtf_result.trend == "UPTREND" else "red"
                console.print(f"  [{t_color}]📈 {mtf_line}[/{t_color}]")
    except Exception:
        pass

    # Re-Entry-Tracker: Preise aktualisieren
    try:
        rt = reentry_tracker or reentry_tracker_cls()
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
        ep = earnings_predictor.predict(ticker)
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
            ctx["pattern_result"] if ctx is not None
            else (chart_analyzer or chart_pattern_analyzer_cls()).analyze(ticker)
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
    if ctx is not None:
        onchain_snapshot = ctx.get("onchain")
    elif is_crypto:
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

    return LocalSignalResult(
        news=news, skip=False, historical=historical,
        open_position_ctx=open_position_ctx,
        pattern_result=pattern_result, onchain_snapshot=onchain_snapshot,
    )
