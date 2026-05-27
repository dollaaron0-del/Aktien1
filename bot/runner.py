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
from analyzers.onchain_signals import OnChainSignalAnalyzer
from analyzers.eu_market_context import EUMarketContext
from collectors.onchain_collector import OnChainCollector
from analyzers.reflection_engine import ReflectionEngine
from analyzers.dynamic_watchlist import DynamicWatchlist
from analyzers.signal_expander import SignalDrivenExpander
from analyzers.news_velocity import NewsVelocityAnalyzer
from analyzers.multi_timeframe_sentiment import MultiTimeframeSentiment
from analyzers.reentry_tracker import ReEntryTracker
from analyzers.analysis_cache import AnalysisCache
from analyzers.analysis_log import AnalysisLog
import analyzers.user_request_queue as _urq
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
_analysis_log       = AnalysisLog()

_collect_log = get_logger("collectors")


def _is_crypto(ticker: str) -> bool:
    """True wenn der Ticker ein Krypto-Asset ist (z.B. 'BTC', 'ETH/USD')."""
    from analyzers.crypto_universe import CRYPTO_UNIVERSE
    base = ticker.split("/")[0].upper()
    return base in CRYPTO_UNIVERSE or ticker.endswith("/USD")


_EU_SUFFIXES = {
    ".DE", ".F", ".MU", ".PA", ".AS", ".MI", ".MC",
    ".BR", ".BE", ".VI", ".L", ".SW", ".CO", ".ST", ".HE", ".OL",
}


# Bekannte falsche Ticker → korrekter Yahoo-Finance-Ticker
_TICKER_CORRECTIONS: Dict[str, str] = {
    "LVMH.PA":  "MC.PA",    # LVMH Moët Hennessy
    "LVMH":     "MC.PA",
    "VOLKSWAGEN.DE": "VOW3.DE",
    "VW.DE":    "VOW3.DE",
    "DAIMLER.DE": "MBG.DE",  # Mercedes-Benz
}


def _normalize_ticker(ticker: str) -> str:
    """Korrigiert bekannte falsche Ticker-Symbole."""
    return _TICKER_CORRECTIONS.get(ticker.upper(), ticker)


def _is_eu_stock(ticker: str) -> bool:
    upper = ticker.upper()
    return any(upper.endswith(s.upper()) for s in _EU_SUFFIXES)


def _base_symbol(ticker: str) -> str:
    """Strip exchange suffix so ASML and ASML.AS map to the same base."""
    upper = ticker.upper()
    for s in sorted(_EU_SUFFIXES, key=len, reverse=True):
        if upper.endswith(s.upper()):
            return upper[: -len(s)]
    return upper


def _get_watchlist(portfolio: Portfolio) -> List[str]:
    """Returns watchlist: dynamic/static US + optional EU + optional Crypto."""
    if _dynamic_watchlist:
        active = list(portfolio.all_positions().keys())
        base = _dynamic_watchlist.get_watchlist(active_tickers=active)
    else:
        base = list(config.watchlist)

    # EU-Aktien anhängen wenn aktiviert
    if config.eu_stocks_enabled:
        eu_tickers = list(config.eu_watchlist)
        if not eu_tickers:
            # Keine manuelle Watchlist → EU-Scanner automatisch ausführen (Top 6)
            try:
                from analyzers.eu_stock_scanner import EUStockScanner
                scan = EUStockScanner(max_results=6).scan()
                eu_tickers = [c.ticker for c in scan.candidates]
                if eu_tickers:
                    log.info(
                        "EU-Auto-Scan: %d Kandidaten gefunden: %s",
                        len(eu_tickers), ", ".join(eu_tickers),
                    )
            except Exception as e:
                log.warning("EU-Auto-Scan fehlgeschlagen: %s", e)
        _base_symbols_in_list = {_base_symbol(x) for x in base}
        for t in eu_tickers:
            if t not in base and _base_symbol(t) not in _base_symbols_in_list:
                base.append(t)
                _base_symbols_in_list.add(_base_symbol(t))
            elif _base_symbol(t) in _base_symbols_in_list and t not in base:
                log.debug("EU-Ticker %s übersprungen (Basisymbol bereits als US-Ticker vorhanden)", t)

    # Krypto anhängen wenn aktiviert
    if config.crypto_enabled and config.crypto_watchlist:
        for t in config.crypto_watchlist:
            if t not in base:
                base.append(t)

    # Vom Dashboard manuell angeforderte Ticker einmalig analysieren
    requested = _urq.consume_all()
    for t in requested:
        if t not in base:
            log.info("Nutzeranfrage: %s wird in diesem Zyklus analysiert", t)
            base.append(t)

    # Opportunity-Scan: bei ≥50% freien Slots nach weiteren Kandidaten suchen
    _opportunity_scan(portfolio, base)

    # Verwandte Aktien: für jeden BUY/HOLD-Ticker die bekannten Verbindungen einbeziehen
    _bench_picks = int(os.getenv("ANALYSIS_BENCH_PICKS", "3"))
    try:
        from analyzers.stock_relations import StockRelations
        from analyzers.analysis_cache import AnalysisCache
        _relations = StockRelations()
        _cache = AnalysisCache()
        _active_buys = [t for t in base if _cache.get(t) and _cache.get(t).get("recommendation") in ("BUY", "HOLD")]
        _related_added = []
        for _bt in _active_buys:
            for _rt in _relations.get_related(_bt):
                if _rt not in base and len(_related_added) < 4:
                    base.append(_rt)
                    _related_added.append(_rt)
        if _related_added:
            console.print(
                f"  [cyan]🔗 Verwandte Ticker aus BUY/HOLD-Netz: "
                f"{', '.join(_related_added)}[/cyan]"
            )
    except Exception as e:
        log.debug("Verwandte-Ticker-Lookup fehlgeschlagen: %s", e)

    # BenchList: Top-Kandidaten immer für Analyse einbeziehen (unabhängig von Slots)
    # _bench_geo_contexts: ticker → geo_context Dict (für Claude-Analyse)
    _bench_geo_contexts: dict = {}
    try:
        from analyzers.bench_list import BenchList
        _bench = BenchList()
        _bench.cleanup()
        _bench_candidates = _bench.pop_candidates(_bench_picks, exclude=base)
        for _bt in _bench_candidates:
            base.append(_bt)
            ctx = _bench.get_context(_bt)
            if ctx and ctx.get("geo_context"):
                _bench_geo_contexts[_bt] = ctx["geo_context"]
        if _bench_candidates:
            geo_flagged = [t for t in _bench_candidates if t in _bench_geo_contexts]
            flag_str = f" (🌍 Geo: {', '.join(geo_flagged)})" if geo_flagged else ""
            console.print(
                f"  [magenta]📋 BenchList → Analyse: {', '.join(_bench_candidates)}{flag_str}[/magenta]"
            )
    except Exception as e:
        log.debug("BenchList-Analyse-Pull fehlgeschlagen: %s", e)

    # Sektor-Stichprobe: rotierende Sektor-Aktien für Netz-Aufbau
    try:
        from analyzers.sector_sampler import SectorSampler
        sector_name, sample = SectorSampler().get_sample(exclude=base)
        added_sample = [t for t in sample if t not in base]
        for t in added_sample:
            base.append(t)
        if added_sample:
            console.print(
                f"  [blue]🔬 Sektor-Stichprobe ({sector_name}): "
                f"{', '.join(added_sample)}[/blue]"
            )
    except Exception as e:
        log.debug("Sektor-Sampler fehlgeschlagen: %s", e)

    log.info("Analyse-Watchlist: %d Aktien → %s", len(base), ", ".join(base[:15]))
    return base


def _opportunity_scan(portfolio: "Portfolio", base: List[str]) -> None:
    """
    Erweitert die Watchlist wenn mehr als die Hälfte der Positions-Slots frei ist.
    Verhindert verschwendetes Kapital wenn alle Watchlist-Aktien kein Signal liefern.
    """
    try:
        from portfolio.focus_mode import get_scaling
        prices = {t: pos.entry_price for t, pos in portfolio.all_positions().items()}
        portfolio_value = portfolio.cash + sum(
            pos.shares * pos.entry_price for pos in portfolio.all_positions().values()
        )
        max_pos, _ = get_scaling(portfolio_value)
        open_count = len(portfolio.all_positions())
        free_slots = max_pos - open_count

        if free_slots < max(2, max_pos // 2):
            return  # Portfolio ist gut gefüllt – kein Scan nötig

        extra_needed = min(free_slots, 8)
        active = list(portfolio.all_positions().keys())

        log.info(
            "Opportunity-Scan: %d/%d Slots frei – suche bis zu %d neue Kandidaten",
            free_slots, max_pos, extra_needed,
        )

        added = []

        # 1. Zuerst aus der BenchList schöpfen (vom Bot bereits entdeckte Kandidaten)
        try:
            from analyzers.bench_list import BenchList
            bench_picks = BenchList().pop_candidates(extra_needed, exclude=base)
            for t in bench_picks:
                base.append(t)
                added.append(t)
            if bench_picks:
                log.info("BenchList: %d Kandidaten geholt: %s", len(bench_picks), ", ".join(bench_picks))
        except Exception as e:
            log.debug("BenchList-Abruf fehlgeschlagen: %s", e)

        # 2. US-Momentum Scan wenn noch Slots offen
        remaining = extra_needed - len(added)
        if remaining > 0:
            try:
                from analyzers.dynamic_watchlist import DynamicWatchlist as _DWL
                scan_tickers = _DWL(max_picks=remaining).get_watchlist(active_tickers=active)
                for t in scan_tickers:
                    if t not in base:
                        base.append(t)
                        added.append(t)
            except Exception as e:
                log.debug("Opportunity US-Scan fehlgeschlagen: %s", e)

        # 3. EU-Scan wenn noch Slots frei
        remaining = extra_needed - len(added)
        if remaining > 0:
            try:
                from analyzers.eu_stock_scanner import EUStockScanner
                eu_scan = EUStockScanner(max_results=min(remaining, 4)).scan()
                for c in eu_scan.candidates:
                    if c.ticker not in base:
                        base.append(c.ticker)
                        added.append(c.ticker)
            except Exception as e:
                log.debug("Opportunity EU-Scan fehlgeschlagen: %s", e)

        if added:
            console.print(
                f"  [magenta]🔍 Opportunity-Scan: {len(added)} neue Kandidaten → "
                f"{', '.join(added[:6])}{'...' if len(added) > 6 else ''}[/magenta]"
            )
    except Exception as e:
        log.debug("Opportunity-Scan Fehler: %s", e)


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

    # EU Marktbarometer einmal pro Zyklus laden (cached 2h)
    _eu_market_ctx = None
    active_watchlist = _get_watchlist(portfolio)
    if any(_is_eu_stock(t) for t in active_watchlist):
        try:
            _eu_market_ctx = EUMarketContext().get_snapshot()
            if _eu_market_ctx:
                sig_color = {"RISK_ON": "green", "RISK_OFF": "red"}.get(
                    _eu_market_ctx.signal, "yellow"
                )
                dax = _eu_market_ctx.indices.get("DAX")
                stoxx = _eu_market_ctx.indices.get("STOXX50")
                dax_str  = f"DAX {dax.change_5d_pct:+.1f}%"  if dax   else ""
                stx_str  = f"STOXX50 {stoxx.change_5d_pct:+.1f}%" if stoxx else ""
                console.print(
                    f"  [{sig_color}]🇪🇺 EU-Markt: {_eu_market_ctx.signal} "
                    f"| {dax_str} | {stx_str}[/{sig_color}]"
                )
                if _eu_market_ctx.ecb_note:
                    console.print(f"  [yellow]{_eu_market_ctx.ecb_note}[/yellow]")
        except Exception as e:
            log.debug("EU Marktkontext fehlgeschlagen: %s", e)

    for ticker in active_watchlist:
        ticker = _normalize_ticker(ticker)
        console.print(f"\n[cyan]Sammle Daten für {ticker}...[/cyan]")

        news, sources_breakdown = collect_news(ticker, archive, collectors)
        if _is_crypto(ticker):
            crypto_price = broker.get_crypto_price(ticker)
            price_data = {"current_price": crypto_price, "volume": 0}
        else:
            price_data = collectors["yahoo"].get_price_data(ticker)

        # Kurs-Check vor Claude: kein Kurs → Claude-Aufruf sparen
        if not _is_crypto(ticker) and not price_data.get("current_price"):
            console.print(f"  [dim]Kein Kurs für {ticker} verfügbar – übersprungen[/dim]")
            continue

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

        # On-Chain-Analyse (nur für Krypto-Assets)
        onchain_snapshot = None
        if _is_crypto(ticker):
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

        console.print(f"  [cyan]Analysiere mit Claude ({config.claude_model})...[/cyan]")
        _geo_ctx = _bench_geo_contexts.get(ticker) if "_bench_geo_contexts" in dir() else None
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
        )

        _print_analysis(analysis)
        _analysis_cache.store(
            ticker, analysis.direction, analysis.sentiment_score,
            analysis.confidence, analysis.recommendation,
        )
        _analysis_log.store(analysis)

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

        action = strategy.evaluate(analysis, sources_breakdown)
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
