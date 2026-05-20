"""
bot/runner.py – Analysis cycle, news collection, and related helpers.
"""

import os
from collections import defaultdict
from datetime import datetime, date
from typing import List, Dict, Optional

from rich.console import Console

from config import config
from logger import get_logger
from collectors import (
    RedditCollector, YahooCollector, NewsAPICollector,
    InsiderCollector, USASpendingCollector,
    SECEdgarCollector, StockTwitsCollector, WireCollector,
    OptionsFlowCollector, EuropeanNewsCollector, TwitterCollector,
    SEC8KCollector, ShortInterestCollector, InstitutionalCollector,
    AnalystCollector, EarningsTranscriptCollector, PatentCollector,
    JobListingsCollector, CEOInterviewCollector, EURegulationCollector,
    ChineseMediaCollector, WebTrafficCollector,
)
from collectors.news_archive import NewsArchive
from collectors.crypto_news_collector import CryptoNewsCollector
from analyzers import ClaudeAnalyzer, AnalysisResult
from analyzers.chart_patterns import ChartPatternAnalyzer
from analyzers.reflection_engine import ReflectionEngine
from analyzers.dynamic_watchlist import DynamicWatchlist
from analyzers.signal_expander import SignalDrivenExpander
from analyzers.news_velocity import NewsVelocityAnalyzer
from analyzers.multi_timeframe_sentiment import MultiTimeframeSentiment
from analyzers.reentry_tracker import ReEntryTracker
from analyzers.analysis_cache import AnalysisCache
from analyzers.rl_agent import RLAgent
from analyzers.earnings_predictor import EarningsPredictor
from analyzers.cross_asset import CrossAssetSignals
from analyzers.multi_agent_analyzer import MultiAgentAnalyzer
from broker.paper_broker import PaperBroker
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from strategy import SwingStrategy
from notifier.telegram_notifier import TelegramNotifier
from collectors.tradingview_webhook import get_pending_sells, get_pending_macro_events

console = Console()
log = get_logger(__name__)

_dynamic_watchlist  = DynamicWatchlist(max_picks=config.scan_max_picks or 12) if config.auto_scan_watchlist else None
_rl_agent           = RLAgent()
_earnings_predictor = EarningsPredictor()
_cross_asset        = CrossAssetSignals()
_signal_expander    = SignalDrivenExpander()
_analysis_cache     = AnalysisCache()

_collect_log = get_logger("collectors")


def _is_crypto(ticker: str) -> bool:
    """True wenn der Ticker ein Krypto-Asset ist (z.B. 'BTC', 'ETH/USD')."""
    from analyzers.crypto_universe import CRYPTO_UNIVERSE
    base = ticker.split("/")[0].upper()
    return base in CRYPTO_UNIVERSE or ticker.endswith("/USD")


def _get_watchlist(portfolio: Portfolio) -> List[str]:
    """Returns watchlist: dynamic/static US + optional EU + optional Crypto."""
    if _dynamic_watchlist:
        active = list(portfolio.all_positions().keys())
        base = _dynamic_watchlist.get_watchlist(active_tickers=active)
    else:
        base = list(config.watchlist)

    # EU-Aktien anhängen wenn aktiviert (Duplikate vermeiden)
    if config.eu_stocks_enabled and config.eu_watchlist:
        for t in config.eu_watchlist:
            if t not in base:
                base.append(t)

    # Krypto anhängen wenn aktiviert
    if config.crypto_enabled and config.crypto_watchlist:
        for t in config.crypto_watchlist:
            if t not in base:
                base.append(t)

    return base


def _make_phase_ctrl() -> PhaseController:
    return PhaseController(
        initial_capital=config.initial_capital,
        growth_target_multiple=config.growth_target_multiple,
        monthly_target_eur=config.monthly_distribution_eur,
        buffer_months=config.distribution_buffer_months,
    )


def _make_focus_ctrl():
    from portfolio.focus_mode import FocusController
    return FocusController(
        mode=config.focus_mode,
        target_amount=config.target_goal_amount or None,
        target_date=config.target_goal_date or None,
        initial_capital=config.initial_capital,
    )


def _safe_collect(collector_name: str, fn, *args, **kwargs) -> List[Dict]:
    """Ruft einen Collector auf und loggt Fehler – gibt bei Ausnahme [] zurück."""
    try:
        return fn(*args, **kwargs) or []
    except Exception as e:
        _collect_log.warning("Collector %s fehlgeschlagen: %s", collector_name, e)
        return []


def _make_collectors() -> Dict:
    """Build all collector instances once per analysis cycle."""
    _twitter = TwitterCollector()
    return {
        "yahoo":             YahooCollector(),
        "reddit":            RedditCollector(),
        "newsapi":           NewsAPICollector(),
        "insider":           InsiderCollector(lookback_days=90),
        "usaspending":       USASpendingCollector(lookback_days=180, min_award_usd=1_000_000),
        "sec_edgar":         SECEdgarCollector(lookback_days=30),
        "stocktwits":        StockTwitsCollector(lookback_hours=48),
        "wire":              WireCollector(lookback_days=7),
        "options_flow":      OptionsFlowCollector(),
        "european_news":     EuropeanNewsCollector(lookback_hours=72),
        "twitter":           _twitter if _twitter.available else None,
        "sec_8k":            SEC8KCollector(),
        "short_interest":    ShortInterestCollector(),
        "institutional_13f": InstitutionalCollector(),
        "analyst_ratings":   AnalystCollector(),
        "earn_transcripts":  EarningsTranscriptCollector(),
        "patents":           PatentCollector(),
        "job_listings":      JobListingsCollector(),
        "ceo_interviews":    CEOInterviewCollector(),
        "eu_regulation":     EURegulationCollector(),
        "chinese_media":     ChineseMediaCollector(),
        "web_traffic":       WebTrafficCollector(),
        "crypto_news":       CryptoNewsCollector(),
    }


def collect_news(ticker: str, archive: NewsArchive, collectors: Dict) -> tuple:
    """
    Collects fresh news from all sources, stores in archive,
    and returns (deduplicated_items, sources_breakdown).
    collectors: pre-built dict from _make_collectors() – reused across tickers.
    """
    all_items: List[Dict] = []
    sources_breakdown: Dict[str, int] = {}

    is_crypto = _is_crypto(ticker)
    # Collectors that make sense for crypto assets; stock-specific ones are skipped.
    _CRYPTO_ALLOWED = {
        "yahoo", "reddit", "newsapi", "wire", "stocktwits",
        "twitter", "crypto_news",
    }

    for name, collector in collectors.items():
        if is_crypto and name not in _CRYPTO_ALLOWED:
            sources_breakdown[name] = 0
            continue
        items = _safe_collect(name, collector.collect, ticker) if collector is not None else []
        sources_breakdown[name] = len(items)
        all_items.extend(items)

    archive.store(ticker, all_items)

    try:
        NewsVelocityAnalyzer().record_articles(ticker, all_items)
    except Exception:
        pass

    seen: set = set()
    unique: List[Dict] = []
    for item in all_items:
        key = (item.get("title") or "").lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    return unique, sources_breakdown


def _print_analysis(a: AnalysisResult):
    color = {"BULLISH": "green", "BEARISH": "red", "NEUTRAL": "yellow"}.get(a.direction, "white")
    conf_color = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(a.confidence, "white")
    rec_color = {"BUY": "bold green", "SELL": "bold red", "HOLD": "yellow", "SKIP": "dim"}.get(
        a.recommendation, "white"
    )

    console.print(
        f"  Sentiment: [{color}]{a.direction}[/{color}] "
        f"(Score: {a.sentiment_score:.2f}) | "
        f"Konfidenz: [{conf_color}]{a.confidence}[/{conf_color}] | "
        f"Empfehlung: [{rec_color}]{a.recommendation}[/{rec_color}]"
    )
    if a.bull_case or a.bear_case:
        winner_color = {"BULL": "green", "BEAR": "red", "DRAW": "yellow"}.get(a.debate_winner, "white")
        winner_icon  = {"BULL": "🟢", "BEAR": "🔴", "DRAW": "⚖️"}.get(a.debate_winner, "")
        console.print(f"  {winner_icon} Debatte: [{winner_color}]{a.debate_winner}[/{winner_color}]")
        if a.bull_case:
            console.print(f"  [green]▲ Bull:[/green] [italic]{a.bull_case}[/italic]")
        if a.bear_case:
            console.print(f"  [red]▼ Bear:[/red] [italic]{a.bear_case}[/italic]")
    if a.entry_rationale:
        console.print(f"  Begründung: [italic]{a.entry_rationale}[/italic]")
    if a.target_price:
        console.print(f"  [bold]Zielkurs: ${a.target_price:.2f}[/bold] – {a.target_price_rationale}")
    if a.thesis_valid is False:
        console.print(f"  [bold red]⚠ THESE GEBROCHEN: {a.thesis_break_reason}[/bold red]")
    elif a.thesis_valid is True:
        console.print(f"  [green]✓ Kaufthese weiterhin gültig[/green]")
    if a.key_catalysts:
        console.print(f"  Katalysatoren: {', '.join(a.key_catalysts[:3])}")
    if a.risk_factors:
        console.print(f"  Risiken: {', '.join(a.risk_factors[:3])}")


def _print_portfolio_summary(portfolio: Portfolio, broker, phase_ctrl: PhaseController):
    from rich.table import Table
    from rich import box
    from rich.panel import Panel

    positions = portfolio.all_positions()
    prices = broker.get_prices(list(positions.keys())) if positions else {}
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)

    table = Table(title="Portfolio-Übersicht", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Stück", justify="right")
    table.add_column("Einstieg", justify="right")
    table.add_column("Aktuell", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("SL", justify="right")
    table.add_column("TP", justify="right")
    table.add_column("Tage", justify="right")

    for ticker, pos in positions.items():
        price = prices.get(ticker, pos.entry_price)
        pnl = (price - pos.entry_price) * pos.shares
        days = (datetime.utcnow() - datetime.fromisoformat(pos.entry_date)).days
        pnl_str = f"[green]+${pnl:.2f}[/green]" if pnl >= 0 else f"[red]-${abs(pnl):.2f}[/red]"
        table.add_row(
            ticker, f"{pos.shares:.2f}", f"${pos.entry_price:.2f}",
            f"${price:.2f}", pnl_str, f"${pos.stop_loss:.2f}", f"${pos.take_profit:.2f}", str(days),
        )

    console.print()
    console.print(table)

    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"
    progress_bar = _progress_bar(phase_info["progress_pct"])
    summary_lines = [
        f"Cash: [bold]${portfolio.cash:,.2f}[/bold]  |  Gesamtwert: [bold]${total:,.2f}[/bold]",
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}]  |  "
        f"Ziel: ${phase_info['growth_target']:,.0f}  |  "
        f"Fortschritt: {progress_bar} {phase_info['progress_pct']:.1f}%",
    ]
    if phase_info["phase"] == "DISTRIBUTION":
        summary_lines.append(
            f"[bold magenta]Monatliche Ausschüttung: ${phase_info.get('monthly_distribution', 0):,.2f} "
            f"(Ziel: ${phase_info['monthly_target']:,.2f})[/bold magenta]"
        )
    else:
        summary_lines.append(f"Noch ${phase_info['remaining_to_goal']:,.2f} bis zur Ausschüttungsphase")

    console.print(Panel("\n".join(summary_lines), title="Kapital & Phase", border_style=phase_color))


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return f"[{'█' * filled}{'░' * (width - filled)}]"


def run_analysis_cycle(
    portfolio: Portfolio,
    broker,
    strategy,
    tracker: PerformanceTracker,
    phase_ctrl: PhaseController,
    archive: NewsArchive,
    reflection: Optional[ReflectionEngine] = None,
    weekend_prep=None,
    hedge_strategy=None,
):
    rule_suffix = "  [bold yellow][EXPLORATION][/bold yellow]" if config.exploration_mode else ""
    console.rule(f"[bold blue]Analyse-Zyklus – {datetime.now().strftime('%Y-%m-%d %H:%M')}{rule_suffix}")

    # Multi-Agent Konsens wenn aktiviert, sonst Standard-Analyzer
    _multi_agent_enabled = os.getenv("MULTI_AGENT_ENABLED", "false").lower() in ("1", "true", "yes")
    analyzer = MultiAgentAnalyzer() if _multi_agent_enabled else ClaudeAnalyzer()
    if _multi_agent_enabled:
        console.print("  [bold magenta]🤝 Multi-Agent Konsens aktiv[/bold magenta] (3 Claude-Analysten)")
    collectors = _make_collectors()

    # Inject continuous learning memo into Claude's system prompt
    lessons_memo = reflection.get_active_memo() if reflection else None
    if lessons_memo:
        console.print(f"  [dim]📚 Lessons-Memo aktiv ({len(lessons_memo)} Zeichen)[/dim]")

    # Inject weekly briefing as additional context
    weekly_briefing = weekend_prep.get_current_briefing() if weekend_prep else None
    if weekly_briefing:
        console.print(f"  [dim]📋 Wochenbriefing aktiv ({len(weekly_briefing)} Zeichen)[/dim]")

    # Track all material trade actions for the daily summary
    cycle_actions: List[str] = []

    cycle_log = get_logger(__name__)

    # ── Regime check + hedge evaluation ──────────────────────────────────────
    if hedge_strategy:
        macro_news_for_regime = []
        try:
            macro_news_for_regime = NewsAPICollector().collect_general("market recession economy", max_results=10)
        except Exception as e:
            cycle_log.warning("Hedge-Regime: Macro-News konnten nicht geladen werden – %s", e)
        regime, hedge_actions = hedge_strategy.evaluate_regime(macro_news_for_regime or None)
        regime_color = {"BULL": "green", "NEUTRAL": "yellow", "BEAR": "red", "CRISIS": "bold red"}.get(regime, "white")
        latest = hedge_strategy.regime_summary()
        score_str = f" (Score: {latest['recession_score']:.2f})" if latest else ""
        console.print(f"  Marktregime: [{regime_color}]{regime}[/{regime_color}]{score_str}")
        for action in hedge_actions:
            console.print(f"  [magenta]{action}[/magenta]")
            cycle_actions.append(action)

    # ── Makro-Events aus Webhook anzeigen ────────────────────────────────────
    if config.tradingview_webhook_enabled:
        try:
            macro_events = get_pending_macro_events(since_hours=24)
            for me in macro_events:
                surprise_color = "red" if me.get("surprise") == "ABOVE" and me.get("event") in ("CPI", "PPI") \
                                 else "green" if me.get("surprise") == "BELOW" else "yellow"
                console.print(
                    f"  [{surprise_color}]📣 Makro: {me['event']} "
                    f"(Surprise={me.get('surprise','?')}, Impact={me.get('impact','?')})[/{surprise_color}]"
                )
        except Exception:
            pass

    # Check stop-loss / take-profit first (no Claude needed)
    exit_actions = strategy.check_open_positions()
    for action in exit_actions:
        console.print(f"  [yellow]{action}[/yellow]")
    cycle_actions.extend(exit_actions)

    # TradingView SELL-Signale verarbeiten (Short/Bearish Engulfing)
    if config.tradingview_webhook_enabled:
        tv_sells = get_pending_sells()
        for sig in tv_sells:
            tv_ticker = sig["ticker"]
            pos = portfolio.get_position(tv_ticker)
            if pos:
                price = broker.get_price(tv_ticker) or pos.entry_price
                action = strategy._do_close(
                    tv_ticker, pos, price,
                    f"TradingView SELL-Signal ({sig.get('strategy', 'TV')})"
                )
                msg = (
                    f"[{tv_ticker}] 📉 TradingView SELL ({sig.get('strategy', 'TV')}) "
                    f"@ ${price:.2f}"
                )
                console.print(f"  [bold red]{msg}[/bold red]")
                cycle_actions.append(msg)
            else:
                console.print(
                    f"  [dim]TradingView SELL [{tv_ticker}]: keine offene Position[/dim]"
                )

    active_watchlist = _get_watchlist(portfolio)
    for ticker in active_watchlist:
        console.print(f"\n[cyan]Sammle Daten für {ticker}...[/cyan]")

        news, sources_breakdown = collect_news(ticker, archive, collectors)
        if _is_crypto(ticker):
            crypto_price = broker.get_crypto_price(ticker)
            price_data = {"current_price": crypto_price, "volume": 0}
        else:
            price_data = collectors["yahoo"].get_price_data(ticker)

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
            f"Yahoo:{src['yahoo']} Reddit:{src['reddit']} NewsAPI:{src['newsapi']} "
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
            vel = NewsVelocityAnalyzer().analyze(ticker)
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
                mtf_result = MultiTimeframeSentiment().analyze(ticker, dict(by_date))
                mtf_line = MultiTimeframeSentiment().to_text(mtf_result)
                if mtf_result.trend in ("UPTREND", "DOWNTREND"):
                    t_color = "green" if mtf_result.trend == "UPTREND" else "red"
                    console.print(f"  [{t_color}]📈 {mtf_line}[/{t_color}]")
        except Exception:
            pass

        # Re-Entry-Tracker: Preise aktualisieren (einmal instanziieren)
        try:
            rt = ReEntryTracker()
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
            pattern_result = ChartPatternAnalyzer().analyze(ticker)
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

        console.print(f"  [cyan]Analysiere mit Claude ({config.claude_model})...[/cyan]")
        analysis = analyzer.analyze(
            ticker=ticker,
            news_items=news,
            price_data=price_data,
            historical_news=historical if historical else None,
            open_position=open_position_ctx,
            lessons_memo=lessons_memo,
            weekly_briefing=weekly_briefing,
            pattern_result=pattern_result,
        )

        _print_analysis(analysis)
        _analysis_cache.store(
            ticker, analysis.direction, analysis.sentiment_score,
            analysis.confidence, analysis.recommendation,
        )

        action = strategy.evaluate(analysis, sources_breakdown)
        if action:
            color = "bold red" if "VERKAUFT" in action else "bold green"
            console.print(f"  [{color}]{action}[/{color}]")
            # Only material trades go into the Telegram summary, not skip/hold notices
            if "GEKAUFT" in action or "VERKAUFT" in action:
                cycle_actions.append(action)
                # Refresh continuous learning memo after each closed trade
                if reflection and "VERKAUFT" in action:
                    new_memo = reflection.generate_memo()
                    if new_memo:
                        console.print("  [dim]📚 Lessons-Memo aktualisiert[/dim]")
                        lessons_memo = new_memo

    # Record portfolio snapshot
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total_value = portfolio.total_value(prices)
    positions_value = total_value - portfolio.cash
    phase = phase_ctrl.current_phase(total_value)
    tracker.record_snapshot(total_value, portfolio.cash, positions_value, phase)

    # Clean up news older than 32 days
    archive.cleanup_old(keep_days=32)

    _print_portfolio_summary(portfolio, broker, phase_ctrl)

    # Send Telegram daily summary
    notifier = TelegramNotifier()
    notifier.notify_daily_summary(
        total_value=total_value,
        cash=portfolio.cash,
        open_positions=len(portfolio.all_positions()),
        phase=phase,
        progress_pct=phase_ctrl.progress_pct(total_value),
        actions_today=cycle_actions,
    )
