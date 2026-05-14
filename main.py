"""
Stock Sentiment Trading Bot
Sammelt Nachrichten & Reddit-Posts, analysiert per Claude API,
und handelt Aktien automatisch (Paper-Trading).

Starten:  python main.py
          python main.py --once          (einmalige Analyse, dann beenden)
          python main.py --status        (Portfolioübersicht)
          python main.py --report        (Lernbericht + Phaseninfo)
          python main.py --dashboard     (Startet Streamlit-Dashboard)
          python main.py --focus         (Aktiven Fokus-Modus anzeigen)
          python main.py --journal       (Trade-Tagebuch alle Trades)
          python main.py --journal AAPL  (Trade-Tagebuch für einen Ticker)
          python main.py --reflect       (Monats-Selbsteinschätzung)
          python main.py --reflect 2026-04 (Selbsteinschätzung für Monat YYYY-MM)
          python main.py --backtest      (Strategie auf historischen Daten testen)
          python main.py --scan          (Watchlist-Scanner)
          python main.py --export-csv    (Trades als CSV exportieren)
          python main.py --export-pdf    (PDF-Report)
          python main.py --kelly-info    (Kelly-Criterion-Statistik)
          python main.py --pulse         (Social-Marktpuls der letzten 6h anzeigen)
          python main.py --queue         (Warteschlange ausstehender Signale)
          python main.py --weekend       (Wochenvorbereitung jetzt ausführen)
          python main.py --briefing      (Letztes Wochenbriefing anzeigen)

Analyse-Zeitplan (.env):
  MARKET_EXCHANGES=XETRA,NYSE,TSE    # Vollanalyse 30 Min vor Börseneröffnung
                                     # Optionen: XETRA NYSE NASDAQ TSE HKEX SSE LSE ASX
  MARKET_LEAD_MINUTES=30             # Vorlauf in Minuten
  ENABLE_SOCIAL_SCAN=true            # Stündlicher Social-Scan (Reddit+StockTwits)
  SIGNAL_QUEUE_MAX_AGE_HOURS=48      # Signale verfallen nach 48h
"""

import argparse
import subprocess
import sys
import schedule
import time
from datetime import datetime
from typing import List, Dict, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import config
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
from analyzers import ClaudeAnalyzer, AnalysisResult
from analyzers.reflection_engine import ReflectionEngine
from analyzers.earnings_filter import EarningsFilter
from analyzers.correlation_check import CorrelationChecker
from analyzers.kelly_sizing import KellySizer
from analyzers.watchlist_scanner import WatchlistScanner
from analyzers.backtester import Backtester
from broker.paper_broker import PaperBroker
from broker.alpaca_broker import AlpacaBroker
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.phase_controller import PhaseController
from portfolio.focus_mode import FocusController, FocusMode
from portfolio.trade_journal import TradeJournal
from strategy import SwingStrategy
from notifier.telegram_notifier import TelegramNotifier
from reporting.exporter import Exporter
from portfolio.signal_queue import SignalQueue
from portfolio.goal_risk_assessor import GoalRiskAssessor
from collectors.social_scan import SocialPulseDB
from collectors.reddit_collector import RedditCollector as _RedditColl
from collectors.stocktwits_collector import StockTwitsCollector as _TwitsColl
from collectors.twitter_collector import TwitterCollector as _TwitterColl
from analyzers.market_schedule import MarketSchedule
from analyzers.weekend_prep import WeekendPrep
from analyzers.recession_detector import RecessionDetector, BULL, NEUTRAL, BEAR, CRISIS
from analyzers.dynamic_watchlist import DynamicWatchlist
from analyzers.signal_expander import SignalDrivenExpander
from strategy.hedge_strategy import HedgeStrategy

console = Console()

_dynamic_watchlist = DynamicWatchlist(max_picks=config.scan_max_picks or 12) if config.auto_scan_watchlist else None
_signal_expander   = SignalDrivenExpander()


def _get_watchlist(portfolio: Portfolio) -> List[str]:
    """Returns dynamic or static watchlist depending on config."""
    if _dynamic_watchlist:
        active = list(portfolio.all_positions().keys())
        return _dynamic_watchlist.get_watchlist(active_tickers=active)
    return config.watchlist


def _make_phase_ctrl() -> PhaseController:
    return PhaseController(
        initial_capital=config.initial_capital,
        growth_target_multiple=config.growth_target_multiple,
        monthly_target_eur=config.monthly_distribution_eur,
        buffer_months=config.distribution_buffer_months,
    )


def _make_focus_ctrl() -> FocusController:
    return FocusController(
        mode=config.focus_mode,
        target_amount=config.target_goal_amount or None,
        target_date=config.target_goal_date or None,
        initial_capital=config.initial_capital,
    )


def collect_news(ticker: str, archive: NewsArchive) -> tuple[List[Dict], Dict[str, int]]:
    """
    Collects fresh news from all sources (incl. Congressional + SEC insider trades),
    stores them in the 30-day archive, and returns (deduplicated_items, sources_breakdown).
    """
    yahoo        = YahooCollector()
    reddit       = RedditCollector()
    newsapi      = NewsAPICollector()
    insider      = InsiderCollector(lookback_days=90)
    usaspending  = USASpendingCollector(lookback_days=180, min_award_usd=1_000_000)
    sec_edgar    = SECEdgarCollector(lookback_days=30)
    stocktwits   = StockTwitsCollector(lookback_hours=48)
    wire         = WireCollector(lookback_days=7)
    options_flow = OptionsFlowCollector()
    euro_news    = EuropeanNewsCollector(lookback_hours=72)
    twitter      = TwitterCollector()
    sec_8k          = SEC8KCollector()
    short_interest  = ShortInterestCollector()
    institutional   = InstitutionalCollector()
    analyst         = AnalystCollector()
    earn_transcript = EarningsTranscriptCollector()
    patent          = PatentCollector()
    job_listings    = JobListingsCollector()
    ceo_interview   = CEOInterviewCollector()
    eu_regulation   = EURegulationCollector()
    chinese_media   = ChineseMediaCollector()
    web_traffic     = WebTrafficCollector()

    yahoo_items          = yahoo.collect(ticker)
    reddit_items         = reddit.collect(ticker)
    newsapi_items        = newsapi.collect(ticker)
    insider_items        = insider.collect(ticker)
    contract_items       = usaspending.collect(ticker)
    edgar_items          = sec_edgar.collect(ticker)
    twits_items          = stocktwits.collect(ticker)
    wire_items           = wire.collect(ticker)
    options_items        = options_flow.collect(ticker)
    euro_items           = euro_news.collect(ticker)
    twitter_items        = twitter.collect(ticker) if twitter.available else []
    sec_8k_items         = sec_8k.collect(ticker)
    short_interest_items = short_interest.collect(ticker)
    institutional_items  = institutional.collect(ticker)
    analyst_items        = analyst.collect(ticker)
    transcript_items     = earn_transcript.collect(ticker)
    patent_items         = patent.collect(ticker)
    job_items            = job_listings.collect(ticker)
    ceo_items            = ceo_interview.collect(ticker)
    eu_items             = eu_regulation.collect(ticker)
    chinese_items        = chinese_media.collect(ticker)
    traffic_items        = web_traffic.collect(ticker)

    sources_breakdown = {
        "yahoo":               len(yahoo_items),
        "reddit":              len(reddit_items),
        "newsapi":             len(newsapi_items),
        "insider":             len(insider_items),
        "usaspending":         len(contract_items),
        "sec_edgar":           len(edgar_items),
        "stocktwits":          len(twits_items),
        "wire":                len(wire_items),
        "options_flow":        len(options_items),
        "european_news":       len(euro_items),
        "twitter":             len(twitter_items),
        "sec_8k":              len(sec_8k_items),
        "short_interest":      len(short_interest_items),
        "institutional_13f":   len(institutional_items),
        "analyst_ratings":     len(analyst_items),
        "earn_transcripts":    len(transcript_items),
        "patents":             len(patent_items),
        "job_listings":        len(job_items),
        "ceo_interviews":      len(ceo_items),
        "eu_regulation":       len(eu_items),
        "chinese_media":       len(chinese_items),
        "web_traffic":         len(traffic_items),
    }

    all_items = (
        yahoo_items + reddit_items + newsapi_items + insider_items
        + contract_items + edgar_items + twits_items + wire_items
        + options_items + euro_items + twitter_items
        + sec_8k_items + short_interest_items + institutional_items
        + analyst_items + transcript_items + patent_items + job_items
        + ceo_items + eu_items + chinese_items + traffic_items
    )

    # Archive everything before deduplication (archive handles its own dedup)
    archive.store(ticker, all_items)

    # Deduplicate for the current analysis batch
    seen: set = set()
    unique: List[Dict] = []
    for item in all_items:
        key = (item.get("title") or "").lower()[:80]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    return unique, sources_breakdown


def run_analysis_cycle(
    portfolio: Portfolio,
    broker: PaperBroker,
    strategy: SwingStrategy,
    tracker: PerformanceTracker,
    phase_ctrl: PhaseController,
    archive: NewsArchive,
    reflection: Optional[ReflectionEngine] = None,
    weekend_prep: Optional["WeekendPrep"] = None,
    hedge_strategy: Optional["HedgeStrategy"] = None,
):
    console.rule(f"[bold blue]Analyse-Zyklus – {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    analyzer = ClaudeAnalyzer()
    yahoo = YahooCollector()

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

    # ── Regime check + hedge evaluation ──────────────────────────────────────
    if hedge_strategy:
        macro_news_for_regime = []
        try:
            from collectors.news_api_collector import NewsAPICollector as _NAPI
            macro_news_for_regime = _NAPI().collect_general("market recession economy", max_results=10)
        except Exception:
            pass
        regime, hedge_actions = hedge_strategy.evaluate_regime(macro_news_for_regime or None)
        regime_color = {"BULL": "green", "NEUTRAL": "yellow", "BEAR": "red", "CRISIS": "bold red"}.get(regime, "white")
        latest = hedge_strategy.regime_summary()
        score_str = f" (Score: {latest['recession_score']:.2f})" if latest else ""
        console.print(f"  Marktregime: [{regime_color}]{regime}[/{regime_color}]{score_str}")
        for action in hedge_actions:
            console.print(f"  [magenta]{action}[/magenta]")
            cycle_actions.append(action)

    # Check stop-loss / take-profit first (no Claude needed)
    exit_actions = strategy.check_open_positions()
    for action in exit_actions:
        console.print(f"  [yellow]{action}[/yellow]")
    cycle_actions.extend(exit_actions)

    active_watchlist = _get_watchlist(portfolio)
    for ticker in active_watchlist:
        console.print(f"\n[cyan]Sammle Daten für {ticker}...[/cyan]")

        news, sources_breakdown = collect_news(ticker, archive)
        price_data = yahoo.get_price_data(ticker)

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

        # Build open-position context for thesis check
        open_position_ctx = strategy.build_open_position_context(ticker)
        if open_position_ctx:
            console.print(f"  [yellow]Offene Position – prüfe Kaufthese...[/yellow]")

        console.print(f"  [cyan]Analysiere mit Claude ({config.claude_model})...[/cyan]")
        analysis = analyzer.analyze(
            ticker=ticker,
            news_items=news,
            price_data=price_data,
            historical_news=historical if historical else None,
            open_position=open_position_ctx,
            lessons_memo=lessons_memo,
            weekly_briefing=weekly_briefing,
        )

        _print_analysis(analysis)

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


def _print_portfolio_summary(portfolio: Portfolio, broker: PaperBroker, phase_ctrl: PhaseController):
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


def show_status(portfolio: Portfolio, broker: PaperBroker, phase_ctrl: PhaseController):
    _print_portfolio_summary(portfolio, broker, phase_ctrl)

    trades = portfolio.trade_history()
    if trades:
        trade_table = Table(title="Trade-History (letzte 20)", box=box.ROUNDED)
        trade_table.add_column("Datum", style="dim")
        trade_table.add_column("Ticker", style="cyan")
        trade_table.add_column("Aktion")
        trade_table.add_column("Stück", justify="right")
        trade_table.add_column("Kurs", justify="right")
        trade_table.add_column("P&L", justify="right")
        trade_table.add_column("Grund")

        for t in trades[-20:]:
            pnl = t.pnl
            pnl_str = (
                f"[green]+${pnl:.2f}[/green]" if pnl > 0
                else (f"[red]-${abs(pnl):.2f}[/red]" if pnl < 0 else "")
            )
            action_color = "bold green" if t.action == "BUY" else "bold red"
            trade_table.add_row(
                t.timestamp[:10], t.ticker,
                f"[{action_color}]{t.action}[/{action_color}]",
                f"{t.shares:.2f}", f"${t.price:.2f}", pnl_str,
                (t.reason or "")[:45],
            )
        console.print(trade_table)


def show_report(
    tracker: PerformanceTracker,
    phase_ctrl: PhaseController,
    portfolio: Portfolio,
    broker: PaperBroker,
):
    console.rule("[bold blue]Lern- und Phasenbericht")

    # ── 1. Accuracy overview ───────────────────────────────────────────────
    acc = tracker.get_accuracy_report()
    if acc.get("total_closed", 0) == 0:
        console.print(Panel(
            "[dim]Noch keine abgeschlossenen Trades.[/dim]",
            title="Vorhersage-Genauigkeit",
        ))
    else:
        adaptive_threshold = tracker.get_adaptive_threshold(config.buy_threshold)
        acc_lines = [
            f"Abgeschlossene Trades:     [bold]{acc['total_closed']}[/bold]",
            f"Win-Rate:                  [bold]{acc['win_rate_pct']}%[/bold]",
            f"Richtungs-Genauigkeit:     [bold]{acc['direction_accuracy_pct']}%[/bold]",
            f"Zielkurs-Trefferquote:     [bold]{acc['target_hit_pct']}%[/bold]",
            f"Ø Rendite pro Trade:       [bold]{acc['avg_return_pct']:+.2f}%[/bold]",
            f"Ø Haltedauer (Ist/Plan):   [bold]{acc['avg_hold_days_actual']}d[/bold] / {acc['avg_hold_days_predicted']}d",
            f"Adaptiver Kauf-Threshold:  [bold]{adaptive_threshold:.2f}[/bold] (Basis: {config.buy_threshold:.2f})",
        ]
        console.print(Panel("\n".join(acc_lines), title="Vorhersage-Genauigkeit", border_style="cyan"))

    # ── 2. Exit-Grund-Statistik ────────────────────────────────────────────
    exit_stats = tracker.get_exit_reason_stats()
    if exit_stats:
        et = Table(title="Exit-Grund vs. P&L", box=box.SIMPLE)
        et.add_column("Ausstiegsgrund")
        et.add_column("Trades", justify="right")
        et.add_column("Ø Rendite", justify="right")
        et.add_column("Win-Rate", justify="right")
        labels = {
            "stop_loss": "Stop-Loss",
            "take_profit": "Take-Profit",
            "thesis_broken": "These gebrochen ⚠",
            "hold_expired": "Haltedauer abgelaufen",
            "sentiment_sell": "Sentiment-SELL",
            "other": "Sonstiges",
        }
        for row in exit_stats:
            ret = row["avg_return_pct"]
            ret_str = f"[green]{ret:+.2f}%[/green]" if ret >= 0 else f"[red]{ret:+.2f}%[/red]"
            et.add_row(
                labels.get(row["category"], row["category"]),
                str(row["trades"]),
                ret_str,
                f"{row['win_rate_pct']}%",
            )
        console.print(et)

    # ── 3. Sentiment-Score-Buckets ─────────────────────────────────────────
    buckets = tracker.get_sentiment_score_buckets()
    if buckets:
        bt = Table(title="Sentiment-Score-Bereich vs. Performance", box=box.SIMPLE)
        bt.add_column("Score-Bereich")
        bt.add_column("Trades", justify="right")
        bt.add_column("Win-Rate", justify="right")
        bt.add_column("Ø Rendite", justify="right")
        for b in buckets:
            ret = b["avg_return_pct"]
            ret_str = f"[green]{ret:+.2f}%[/green]" if ret >= 0 else f"[red]{ret:+.2f}%[/red]"
            bt.add_row(b["score_range"], str(b["trades"]), f"{b['win_rate_pct']}%", ret_str)
        console.print(bt)

    # ── 4. Quellen-Trefferquote ────────────────────────────────────────────
    source_acc = tracker.get_source_accuracy()
    if source_acc:
        st = Table(title="Quellen-Trefferquote (top 10)", box=box.SIMPLE)
        st.add_column("Quelle")
        st.add_column("Ticker")
        st.add_column("Trades", justify="right")
        st.add_column("Win-Rate", justify="right")
        st.add_column("Ø Rendite", justify="right")
        for row in source_acc[:10]:
            ret = row["avg_return_pct"]
            ret_str = f"[green]{ret:+.2f}%[/green]" if ret >= 0 else f"[red]{ret:+.2f}%[/red]"
            st.add_row(row["source"], row["ticker"], str(row["trades"]), f"{row['win_rate_pct']}%", ret_str)
        console.print(st)

    # ── 5. Letzte abgeschlossene Trades ───────────────────────────────────
    recent = tracker.get_recent_trades(5)
    if recent:
        rt = Table(title="Letzte 5 geschlossene Trades", box=box.SIMPLE)
        rt.add_column("Ticker")
        rt.add_column("Rendite", justify="right")
        rt.add_column("Haltedauer", justify="right")
        rt.add_column("These ✓")
        rt.add_column("Zielkurs ✓")
        rt.add_column("Ausstiegsgrund")
        for t in recent:
            ret = t.get("actual_return_pct") or 0
            ret_str = f"[green]{ret:+.1f}%[/green]" if ret >= 0 else f"[red]{ret:+.1f}%[/red]"
            rt.add_row(
                t["ticker"], ret_str,
                f"{t.get('actual_hold_days', '?')}d",
                "✓" if t.get("direction_correct") else "✗",
                "✓" if t.get("target_hit") else "✗",
                (t.get("sell_reason") or "")[:40],
            )
        console.print(rt)

    # ── 6. Phase-Info ──────────────────────────────────────────────────────
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)
    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"
    phase_lines = [
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}]",
        f"Startkapital:  ${phase_info['initial_capital']:,.2f}",
        f"Aktuell:       ${phase_info['portfolio_value']:,.2f}",
        f"Wachstumsziel: ${phase_info['growth_target']:,.2f}  ({config.growth_target_multiple:.1f}×)",
        f"Fortschritt:   {_progress_bar(phase_info['progress_pct'])} {phase_info['progress_pct']:.1f}%",
    ]
    if phase_info["phase"] == "GROWTH":
        phase_lines.append(f"Noch ${phase_info['remaining_to_goal']:,.2f} bis zur Ausschüttungsphase")
    else:
        phase_lines += [
            "",
            f"[bold magenta]Monatliche Ausschüttung: ${phase_info['monthly_distribution']:,.2f}[/bold magenta]",
            f"Ziel-Ausschüttung:       ${phase_info['monthly_target']:,.2f}",
            f"Sicherheitspuffer:       ${phase_info['buffer_reserve']:,.2f} ({config.distribution_buffer_months} Monate)",
        ]
    console.print(Panel("\n".join(phase_lines), title="Portfolio-Phase", border_style=phase_color))


def show_monthly_review(reflection: ReflectionEngine, year_month: Optional[str] = None):
    console.rule("[bold blue]Monatliche Selbsteinschätzung")
    content = reflection.generate_monthly_review(year_month)
    if not content:
        console.print("[dim]Nicht genug abgeschlossene Trades im Zeitraum oder API-Fehler.[/dim]")
        return
    console.print(Panel(content, title=f"Self-Assessment {year_month or 'letzter Monat'}", border_style="magenta"))


def show_trade_journal(journal: TradeJournal, ticker: Optional[str] = None):
    console.rule(f"[bold blue]Trade-Tagebuch{' – ' + ticker if ticker else ''}")
    if ticker:
        stories = journal.get_trade_story(ticker, limit_trades=5)
    else:
        stories = journal.get_all_trade_summaries(limit=20)
    if not stories:
        console.print("[dim]Noch keine Trade-Events gespeichert.[/dim]")
        return
    for s in stories[:10]:
        status = "🟢 OFFEN" if s.get("is_open") else (
            "🟩 GEWINN" if (s.get("pnl") or 0) >= 0 else "🔴 VERLUST"
        )
        lines = [
            f"[bold]{s['ticker']}[/bold]  {status}",
            f"  Eingestiegen: {s.get('entry_date', '?')[:10]} @ ${s.get('entry_price', 0):.2f}",
            f"  Sentiment: {s.get('entry_sentiment', 0):.2f}  |  These: [italic]{(s.get('entry_rationale') or '')[:120]}[/italic]",
        ]
        if s.get("catalysts"):
            lines.append(f"  Katalysatoren: {', '.join(s['catalysts'][:3])}")
        if s.get("risks"):
            lines.append(f"  Risiken: {', '.join(s['risks'][:3])}")
        lines.append(f"  Tagesprüfungen: {s.get('n_daily_checks', 0)}  |  Warnungen: {s.get('n_warnings', 0)}")
        if not s.get("is_open"):
            pnl = s.get("pnl", 0)
            color = "green" if pnl >= 0 else "red"
            lines += [
                f"  Verkauft: {s.get('exit_date', '?')[:10]} @ ${s.get('exit_price', 0):.2f}",
                f"  P&L: [{color}]{pnl:+.2f} USD ({s.get('pnl_pct', 0):+.1f}%)[/{color}]  |  "
                f"Haltedauer: {s.get('actual_hold_days', '?')}d",
                f"  Grund: {(s.get('exit_reason') or '')[:120]}",
            ]
        console.print(Panel("\n".join(lines), border_style="cyan" if s.get("is_open") else "dim"))


def show_focus_info(focus_ctrl: FocusController, portfolio: Portfolio, broker: PaperBroker):
    console.rule("[bold blue]Fokus-Modus")
    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    info = focus_ctrl.get_info(total)
    lines = [
        f"Modus:               [bold]{info['label']}[/bold]",
        f"Beschreibung:        [italic]{info['description']}[/italic]",
        "",
        f"Stop-Loss:           {info['stop_loss_pct']*100:.0f}%",
        f"Take-Profit:         {info['take_profit_pct']*100:.0f}%",
        f"Max. Positionsgröße: {info['max_position_pct']*100:.0f}%",
        f"Min. Sentiment:      {info['min_sentiment']:.2f}",
        f"Bevorzugte Haltedauer: {info['preferred_hold_days']}d",
    ]
    if info["mode"] == FocusMode.TARGET_GOAL and info.get("target_amount"):
        status = "✓ Im Plan" if info["on_track"] else ("⚠ Hinter Plan" if info["behind_plan"] else "✓ Voraus")
        lines += [
            "",
            f"Zielbetrag:    ${info['target_amount']:,.2f}",
            f"Zieldatum:     {info['target_date']}",
            f"Tage übrig:    {info['days_remaining']}",
            f"Fortschritt:   {info['progress_pct']:.1f}%",
            f"Status:        [bold]{status}[/bold]  (Urgency {info['urgency']:.2f})",
        ]
    console.print(Panel("\n".join(lines), title="Aktiver Fokus", border_style="cyan"))


def run_social_scan(pulse_db: SocialPulseDB, strategy: Optional[SwingStrategy] = None):
    """Collects Reddit + StockTwits + Twitter for all watchlist tickers and stores pulse snapshot."""
    reddit = _RedditColl()
    twits = _TwitsColl(lookback_hours=2)
    twitter = _TwitterColl()
    spikes = []
    _scan_portfolio = strategy.portfolio if strategy else None
    for ticker in _get_watchlist(_scan_portfolio or Portfolio(config.initial_capital)):
        try:
            r_items = reddit.collect(ticker)
            t_items = twits.collect(ticker)
            tw_items = twitter.collect(ticker, max_results=20, hours_back=2) if twitter.available else []
            if r_items:
                pulse_db.record(ticker, "reddit", r_items)
            if t_items:
                pulse_db.record(ticker, "stocktwits", t_items)
            if tw_items:
                pulse_db.record(ticker, "twitter", tw_items)
        except Exception:
            continue
    pulse_db.cleanup(keep_days=7)
    _signal_expander.cleanup_expired()
    spikes = pulse_db.get_spikes(hours=2, min_mentions=3)
    if spikes:
        # Feed spikes to signal expander for small-cap discovery
        new_spike_tickers = _signal_expander.process_social_spikes(spikes)
        if new_spike_tickers:
            console.print(f"  [magenta]📡 Social-Spike-Ticker hinzugefügt: {', '.join(new_spike_tickers)}[/magenta]")
        console.print(f"\n[bold yellow]📡 Social-Spike erkannt:[/bold yellow]")
        for s in spikes:
            score_label = "🟢 Bullisch" if s["avg_score"] > 0.1 else ("🔴 Bärisch" if s["avg_score"] < -0.1 else "⚪ Neutral")
            console.print(
                f"  [cyan]{s['ticker']}[/cyan]: {s['total_mentions']} Erwähnungen "
                f"(+{s['spike_ratio']}× normal) | {score_label} ({s['avg_score']:+.2f})"
            )
        # If strategy is provided, try to process signal queue after a spike
        if strategy:
            queued = strategy.process_signal_queue()
            for q in queued:
                console.print(f"  [bold green]{q}[/bold green]")
    return spikes


def show_pulse(pulse_db: SocialPulseDB):
    """Displays aggregated social pulse for the last 6 hours."""
    console.rule("[bold blue]Social-Marktpuls (letzte 6h)")
    summary = pulse_db.get_pulse_summary(hours=6)
    if not summary:
        console.print("[dim]Noch keine Social-Scan-Daten.[/dim]")
        return
    table = Table(title="Marktpuls per Ticker", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Erwähnungen", justify="right")
    table.add_column("Bullisch", justify="right")
    table.add_column("Bärisch", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Trend")
    for row in summary:
        score = row["avg_score"]
        score_str = (
            f"[green]{score:+.2f}[/green]" if score > 0.1
            else (f"[red]{score:+.2f}[/red]" if score < -0.1 else f"{score:+.2f}")
        )
        trend = "🟢 Bullisch" if score > 0.1 else ("🔴 Bärisch" if score < -0.1 else "⚪ Neutral")
        table.add_row(
            row["ticker"],
            str(row["total_mentions"]),
            str(row["bull"]),
            str(row["bear"]),
            score_str,
            trend,
        )
    console.print(table)
    spikes = pulse_db.get_spikes(hours=2)
    if spikes:
        console.print("\n[bold yellow]Aktuelle Spikes (2h-Fenster):[/bold yellow]")
        for s in spikes:
            console.print(f"  {s['ticker']}: {s['spike_ratio']}× normales Volumen")


def show_signal_queue(signal_queue: SignalQueue):
    """Displays all pending signals in the queue."""
    console.rule("[bold blue]Signal-Warteschlange")
    pending = signal_queue.get_pending()
    history = signal_queue.get_history(limit=10)
    if not pending:
        console.print("[dim]Keine ausstehenden Signale.[/dim]")
    else:
        table = Table(title=f"{len(pending)} ausstehende Signale", box=box.ROUNDED)
        table.add_column("ID", justify="right")
        table.add_column("Ticker", style="cyan")
        table.add_column("Score", justify="right")
        table.add_column("Konfidenz")
        table.add_column("Erstellt")
        table.add_column("Läuft ab")
        table.add_column("Begründung")
        for s in pending:
            table.add_row(
                str(s["id"]), s["ticker"],
                f"{s['sentiment_score']:.2f}", s["confidence"],
                s["created_at"][:16], s["expires_at"][:16],
                (s.get("entry_rationale") or "")[:60],
            )
        console.print(table)
    if history:
        ht = Table(title="Historie (letzte 10)", box=box.SIMPLE)
        ht.add_column("Ticker", style="cyan")
        ht.add_column("Score", justify="right")
        ht.add_column("Status")
        ht.add_column("Erstellt")
        status_color = {"pending": "yellow", "executed": "green", "expired": "dim", "superseded": "dim"}
        for s in history:
            color = status_color.get(s["status"], "white")
            ht.add_row(
                s["ticker"], f"{s['sentiment_score']:.2f}",
                f"[{color}]{s['status']}[/{color}]",
                s["created_at"][:16],
            )
        console.print(ht)


def run_weekend_prep(wp: WeekendPrep):
    console.rule("[bold blue]Wochenvorbereitung")
    console.print("[cyan]Sammle Earnings-Kalender, Marktdaten und Makro-News...[/cyan]")

    # Show what we find before generating briefing
    earnings = wp.collect_earnings_calendar()
    sentiment = wp.collect_market_sentiment()

    # Earnings table
    wl_earn = earnings.get("watchlist_earnings", [])
    brd_earn = earnings.get("broader_earnings", [])
    if wl_earn or brd_earn:
        et = Table(title=f"Quartalszahlen nächste Woche ({earnings['week']})", box=box.ROUNDED)
        et.add_column("Ticker", style="cyan")
        et.add_column("Wochentag")
        et.add_column("Datum")
        et.add_column("Watchlist?")
        for e in wl_earn:
            et.add_row(e["ticker"], e["weekday"], e["date"], "[bold yellow]⚠ JA[/bold yellow]")
        for e in brd_earn[:8]:
            et.add_row(e["ticker"], e["weekday"], e["date"], "[dim]nein[/dim]")
        console.print(et)
    else:
        console.print("[dim]Keine bekannten Earnings für nächste Woche gefunden.[/dim]")

    # Sector & index table
    if sentiment.get("indices"):
        it = Table(title="Markt letzte Woche", box=box.SIMPLE)
        it.add_column("Index / Indikator")
        it.add_column("Wert", justify="right")
        it.add_column("Woche", justify="right")
        for name, data in sentiment["indices"].items():
            chg = data["week_change_pct"]
            chg_str = f"[green]+{chg:.2f}%[/green]" if chg >= 0 else f"[red]{chg:.2f}%[/red]"
            extra = ""
            if name == "VIX (Angst-Index)":
                extra = f"  {sentiment.get('vix_signal', '')}"
            it.add_row(name + extra, str(data["value"]), chg_str)
        console.print(it)

    vix = sentiment.get("vix")
    if vix:
        vix_color = "green" if vix < 20 else ("yellow" if vix < 28 else "red")
        console.print(f"\n  VIX: [{vix_color}]{vix}[/{vix_color}] – {sentiment.get('vix_signal', '')}")

    # Generate Claude briefing
    console.print("\n[cyan]Generiere Wochenbriefing mit Claude...[/cyan]")
    briefing = wp.generate_briefing(newsapi_key=config.newsapi_key)
    if briefing:
        console.print(Panel(briefing, title="Wochenbriefing für die nächste Handelswoche", border_style="cyan"))
        # Send via Telegram
        notifier = TelegramNotifier()
        notifier.send(f"📋 <b>Wochenbriefing</b>\n\n{briefing[:3000]}")
    else:
        console.print("[red]Briefing konnte nicht generiert werden (API-Fehler oder kein Key).[/red]")


def show_briefing(wp: WeekendPrep):
    console.rule("[bold blue]Wochenbriefings")
    entries = wp.get_latest_briefing(limit=3)
    if not entries:
        console.print("[dim]Noch keine Briefings gespeichert. Starte mit: python main.py --weekend[/dim]")
        return
    for entry in entries:
        console.print(Panel(
            entry["briefing"],
            title=f"Woche ab {entry['week_start']} (generiert {entry['generated_at'][:16]})",
            border_style="cyan",
        ))


def show_regime(detector: RecessionDetector):
    console.rule("[bold blue]Marktregime-Analyse")
    console.print("[cyan]Analysiere VIX, Zinskurve, Sektorbreite, Credit-Spreads...[/cyan]")
    result = detector.analyze()

    regime = result["regime"]
    score  = result["recession_score"]
    regime_color = {"BULL": "green", "NEUTRAL": "yellow", "BEAR": "red", "CRISIS": "bold red"}.get(regime, "white")
    score_bar = _progress_bar(score * 100)

    lines = [
        f"Regime: [{regime_color}]{regime}[/{regime_color}]  |  Score: {score:.3f}",
        f"Rezessions-Risiko: {score_bar} {score*100:.1f}%",
        "",
    ]
    comp = result.get("components", {})
    if "vix" in comp:
        v = comp["vix"]
        lines.append(f"VIX:            {v['value']}  →  {v['label']}")
    if "yield_curve" in comp:
        yc = comp["yield_curve"]
        spread = f"{yc['spread_pct']:+.2f}%" if yc['spread_pct'] is not None else "N/A"
        lines.append(f"Zinskurve:      Spread {spread}  →  {yc['label']}")
    if "sp500_ma200" in comp:
        sp = comp["sp500_ma200"]
        gap = f"{sp['gap_pct']:+.1f}%" if sp['gap_pct'] is not None else "N/A"
        lines.append(f"S&P vs 200-MA:  {gap}  →  {sp['label']}")
    if "sector_breadth" in comp:
        sb = comp["sector_breadth"]
        lines.append(f"Sektoren bear:  {sb['bear_sectors_pct']}%  →  {sb['label']}")
    if result.get("macro_summary"):
        lines += ["", f"Makro-Signal: [italic]{result['macro_summary'][:200]}[/italic]"]

    console.print(Panel("\n".join(lines), title="Aktuelles Marktregime", border_style=regime_color))

    hedges = result.get("recommended_hedges", [])
    if hedges:
        ht = Table(title=f"Empfohlene Hedges ({result['hedge_intensity']})", box=box.ROUNDED)
        ht.add_column("ETF", style="cyan")
        ht.add_column("Thema")
        ht.add_column("Allokation", justify="right")
        ht.add_column("Grund")
        for h in hedges:
            ht.add_row(
                h["ticker"], h["description"],
                f"{h['allocation_pct']*100:.0f}%", h["reason"][:60],
            )
        console.print(ht)
    else:
        console.print("[dim]Keine Hedges empfohlen – Regime zu gut.[/dim]")


def run_backtest(period: str = "2y"):
    console.rule(f"[bold blue]Backtest – {period}")
    bt = Backtester(
        tickers=config.watchlist,
        initial_capital=config.initial_capital,
        stop_loss_pct=config.stop_loss_pct,
        take_profit_pct=config.take_profit_pct,
        max_position_pct=config.max_position_pct,
        sentiment_threshold=config.buy_threshold,
    )
    result = bt.run(period=period)
    if "error" in result:
        console.print(f"[red]{result['error']}[/red]")
        return
    summary_lines = [
        f"Startkapital:       ${result['initial_capital']:,.2f}",
        f"Endkapital:         ${result['final_capital']:,.2f}",
        f"Gesamtrendite:      [bold]{result['total_return_pct']:+.2f}%[/bold]",
        f"Anzahl Trades:      {result['num_trades']}",
        f"Win-Rate:           {result['win_rate_pct']}%",
        f"Ø Rendite/Trade:    {result['avg_return_pct']:+.2f}%",
        f"Max. Drawdown:      {result['max_drawdown_pct']}%",
    ]
    if result.get("best_trade"):
        bt_b = result["best_trade"]
        summary_lines.append(f"Bester Trade:       {bt_b['ticker']} {bt_b['return_pct']:+.2f}% ({bt_b['entry_date']}→{bt_b['exit_date']})")
    if result.get("worst_trade"):
        wt = result["worst_trade"]
        summary_lines.append(f"Schlechtester:      {wt['ticker']} {wt['return_pct']:+.2f}% ({wt['entry_date']}→{wt['exit_date']})")
    console.print(Panel("\n".join(summary_lines), title="Backtest-Ergebnis", border_style="cyan"))
    console.print(
        "[dim]Hinweis: Sentiment-Proxy basiert auf Momentum+Volumen "
        "(echte Claude-News-Signale historisch nicht verfügbar).[/dim]"
    )


def run_scan(portfolio: Portfolio):
    console.rule("[bold blue]Watchlist-Scanner")
    existing = list(portfolio.all_positions().keys())
    scanner = WatchlistScanner(max_picks=config.scan_max_picks)
    picks = scanner.scan(exclude=existing + list(config.watchlist))
    if not picks:
        console.print("[dim]Keine auffälligen Kandidaten gefunden.[/dim]")
        return
    table = Table(title="Auffällige Kandidaten", box=box.ROUNDED)
    table.add_column("Ticker", style="cyan")
    table.add_column("Kurs", justify="right")
    table.add_column("Tageskursänderung", justify="right")
    table.add_column("Volumen-Ratio", justify="right")
    for p in picks:
        change_str = (
            f"[green]+{p['change_pct']}%[/green]"
            if p['change_pct'] >= 0 else f"[red]{p['change_pct']}%[/red]"
        )
        table.add_row(p["ticker"], f"${p['price']}", change_str, f"{p['volume_ratio']}×")
    console.print(table)
    console.print("[dim]Tipp: Ticker in config.watchlist aufnehmen für tägliche Analyse.[/dim]")


def main():
    parser = argparse.ArgumentParser(description="Stock Sentiment Trading Bot")
    parser.add_argument("--once", action="store_true", help="Einmaliger Analysezyklus")
    parser.add_argument("--status", action="store_true", help="Portfolio-Übersicht")
    parser.add_argument("--report", action="store_true", help="Lernbericht")
    parser.add_argument("--dashboard", action="store_true", help="Streamlit-Dashboard")
    parser.add_argument("--reflect", nargs="?", const="latest",
                        help="Monatliche Selbsteinschätzung (optional: YYYY-MM)")
    parser.add_argument("--journal", nargs="?", const="all",
                        help="Trade-Tagebuch anzeigen (optional: Ticker)")
    parser.add_argument("--focus", action="store_true", help="Aktuellen Fokus-Modus anzeigen")
    parser.add_argument("--backtest", action="store_true", help="Strategie auf historischen Daten testen")
    parser.add_argument("--backtest-period", default="2y", help="Backtest-Zeitraum (z.B. 1y, 2y, 5y)")
    parser.add_argument("--scan", action="store_true", help="Watchlist-Scanner ausführen")
    parser.add_argument("--export-csv", action="store_true", help="Trades als CSV exportieren")
    parser.add_argument("--export-pdf", action="store_true", help="Monatsbericht als PDF exportieren")
    parser.add_argument("--kelly-info", action="store_true", help="Kelly-Criterion-Statistik anzeigen")
    parser.add_argument("--pulse", action="store_true", help="Social-Marktpuls anzeigen")
    parser.add_argument("--queue", action="store_true", help="Signal-Warteschlange anzeigen")
    parser.add_argument("--weekend", action="store_true", help="Wochenvorbereitung jetzt ausführen")
    parser.add_argument("--briefing", action="store_true", help="Letztes Wochenbriefing anzeigen")
    parser.add_argument("--regime", action="store_true", help="Aktuelles Marktregime analysieren")
    args = parser.parse_args()

    if args.dashboard:
        dashboard_path = __file__.replace("main.py", "dashboard/app.py")
        console.print("[cyan]Starte Streamlit-Dashboard...[/cyan]")
        subprocess.run(["streamlit", "run", dashboard_path])
        return

    # Select broker based on config
    if config.broker_mode == "alpaca":
        broker = AlpacaBroker()
        if not broker._check_creds():
            console.print("[yellow]⚠ Alpaca-Credentials fehlen – Fallback auf Paper-Broker.[/yellow]")
            broker = PaperBroker()
        else:
            console.print("[green]✓ Alpaca-Broker aktiv[/green]")
    else:
        broker = PaperBroker()

    portfolio = Portfolio(config.initial_capital)
    tracker = PerformanceTracker()
    phase_ctrl = _make_phase_ctrl()
    focus_ctrl = _make_focus_ctrl()
    journal = TradeJournal()
    archive = NewsArchive()
    reflection = ReflectionEngine(tracker, journal)
    signal_queue = SignalQueue(max_age_hours=config.signal_queue_max_age_hours)
    pulse_db = SocialPulseDB()
    weekend_prep_inst = WeekendPrep(
        anthropic_api_key=config.anthropic_api_key,
        watchlist=config.watchlist,
    )

    if args.status:
        show_status(portfolio, broker, phase_ctrl)
        return

    if args.report:
        show_report(tracker, phase_ctrl, portfolio, broker)
        return

    if args.focus:
        show_focus_info(focus_ctrl, portfolio, broker)
        return

    if args.reflect is not None:
        year_month = None if args.reflect == "latest" else args.reflect
        show_monthly_review(reflection, year_month)
        return

    if args.journal is not None:
        ticker = None if args.journal == "all" else args.journal.upper()
        show_trade_journal(journal, ticker)
        return

    if args.backtest:
        run_backtest(args.backtest_period)
        return

    if args.scan:
        run_scan(portfolio)
        return

    if args.export_csv:
        path = Exporter(tracker, journal).export_trades_csv()
        console.print(f"[green]CSV exportiert:[/green] {path or '(keine geschlossenen Trades)'}")
        return

    if args.export_pdf:
        path = Exporter(tracker, journal).export_pdf_report()
        console.print(f"[green]Report exportiert:[/green] {path}")
        return

    if args.kelly_info:
        kelly = KellySizer(tracker, fraction=config.kelly_fraction)
        info = kelly.info()
        console.print(Panel(
            "\n".join(f"{k}: {v}" for k, v in info.items()),
            title="Kelly-Criterion-Statistik", border_style="cyan",
        ))
        return

    if args.pulse:
        show_pulse(pulse_db)
        return

    if args.queue:
        show_signal_queue(signal_queue)
        return

    if args.weekend:
        run_weekend_prep(weekend_prep_inst)
        return

    if args.briefing:
        show_briefing(weekend_prep_inst)
        return

    if args.regime:
        recession_detector = RecessionDetector(anthropic_api_key=config.anthropic_api_key)
        show_regime(recession_detector)
        return

    if not config.anthropic_api_key:
        console.print("[bold red]Fehler: ANTHROPIC_API_KEY nicht gesetzt.[/bold red]")
        sys.exit(1)

    # Initialize risk filters
    earnings_filter = EarningsFilter(block_days=config.block_earnings_days)
    correlation_checker = CorrelationChecker(max_sector_pct=config.max_sector_pct)
    kelly_sizer = KellySizer(tracker, fraction=config.kelly_fraction) if config.use_kelly_sizing else None

    goal_risk = GoalRiskAssessor(
        target_value=config.target_goal_amount,
        target_date_str=config.target_goal_date,
        initial_capital=config.initial_capital,
    )

    strategy = SwingStrategy(
        portfolio, broker, tracker, phase_ctrl, focus_ctrl, journal,
        signal_queue=signal_queue,
        earnings_filter=earnings_filter,
        correlation_checker=correlation_checker,
        kelly_sizer=kelly_sizer,
        goal_risk_assessor=goal_risk,
    )

    # Recession detector + hedge strategy
    recession_detector = RecessionDetector(anthropic_api_key=config.anthropic_api_key)
    hedge_strategy_inst = HedgeStrategy(
        portfolio=portfolio,
        broker=broker,
        tracker=tracker,
        journal=journal,
        detector=recession_detector,
        max_hedge_pct=config.max_hedge_pct,
        min_regime_for_hedge=config.hedge_from_regime,
    ) if config.enable_hedging else None

    prices = broker.get_prices(list(portfolio.all_positions().keys()))
    total = portfolio.total_value(prices)
    phase_info = phase_ctrl.get_info(total)
    phase_color = "green" if phase_info["phase"] == "GROWTH" else "magenta"

    # Build market schedule
    mkt_schedule = MarketSchedule(
        exchanges=config.market_exchanges,
        lead_minutes=config.market_lead_minutes,
    )
    today_slots = mkt_schedule.get_schedule_strings()
    social_label = "[green]aktiv[/green]" if config.enable_social_scan else "[dim]deaktiviert[/dim]"
    queue_label = f"{signal_queue.count_pending()} Signal(e) ausstehend"

    # Build display string: "08:30 (XETRA), 13:00 (NYSE), 23:30 (TSE)"
    if today_slots:
        schedule_display = ", ".join(
            f"[cyan]{s['hhmm']}[/cyan] ({s['exchange']})" for s in today_slots
        )
    else:
        schedule_display = "[dim]Heute kein Handelstag[/dim]"

    nxt = mkt_schedule.next_window()
    next_str = nxt["analysis_local"] if nxt else "–"

    console.print(Panel(
        f"[bold]Stock Sentiment Bot gestartet[/bold]\n"
        f"Broker: [cyan]{config.broker_mode.upper()}[/cyan] | "
        f"Watchlist: [cyan]{', '.join(config.watchlist)}[/cyan]\n"
        f"Fokus: [magenta]{focus_ctrl.profile.label}[/magenta] | "
        f"Nächste Analyse: [bold]{next_str}[/bold]\n"
        f"Heute: {schedule_display} ({config.market_lead_minutes} Min vor Börseneröffnung)\n"
        f"Social-Scan: {social_label} (stündlich) | "
        f"Signal-Queue: [yellow]{queue_label}[/yellow]\n"
        f"Phase: [{phase_color}]{phase_info['phase']}[/{phase_color}] | "
        f"Kapital: ${total:,.2f} | Ziel: ${phase_info['growth_target']:,.0f}",
        border_style="green",
    ))

    # Print full market schedule
    console.print(f"\n[dim]Markt-Zeitplan für heute:[/dim]")
    console.print(f"[dim]{mkt_schedule.describe()}[/dim]\n")

    def _monthly_review_check():
        if datetime.utcnow().day == 1:
            console.print("[bold magenta]📋 Erstelle monatliche Selbsteinschätzung...[/bold magenta]")
            content = reflection.generate_monthly_review()
            if content:
                console.print(Panel(content[:800] + "...", title="Monatsreview erstellt", border_style="magenta"))

    def _social_scan_job():
        spikes = run_social_scan(pulse_db, strategy)
        if spikes:
            notifier = TelegramNotifier()
            spike_lines = [
                f"{s['ticker']}: {s['spike_ratio']}× Volumen, Score {s['avg_score']:+.2f}"
                for s in spikes[:5]
            ]
            notifier.notify_daily_summary(
                total_value=portfolio.total_value(broker.get_prices(list(portfolio.all_positions().keys()))),
                cash=portfolio.cash,
                open_positions=len(portfolio.all_positions()),
                phase=phase_ctrl.current_phase(portfolio.total_value({})),
                progress_pct=phase_ctrl.progress_pct(portfolio.total_value({})),
                actions_today=[f"📡 Social-Spike: {l}" for l in spike_lines],
            )

    def _reschedule_analysis():
        """Rebuilds analysis schedule for the new day (handles DST changes)."""
        for job in list(schedule.jobs):
            if getattr(job, "_is_analysis_job", False):
                schedule.cancel_job(job)
        _register_analysis_jobs()

    def _register_analysis_jobs():
        slots = mkt_schedule.get_schedule_strings()
        is_weekend = datetime.utcnow().weekday() >= 5
        if not slots or is_weekend:
            if is_weekend:
                console.print("[dim]Wochenende – keine Vollanalysen geplant (nur Wochenvorbereitung).[/dim]")
            else:
                console.print("[dim]Heute kein Handelstag.[/dim]")
            return
        for slot in slots:
            job = schedule.every().day.at(slot["hhmm"]).do(
                run_analysis_cycle,
                portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                weekend_prep_inst, hedge_strategy_inst,
            )
            job._is_analysis_job = True
            review_job = schedule.every().day.at(slot["hhmm"]).do(_monthly_review_check)
            review_job._is_analysis_job = True
        times_str = ", ".join(f"{s['hhmm']} ({s['exchange']})" for s in slots)
        console.print(f"[dim]Analyse-Jobs registriert: {times_str}[/dim]")

    def _weekend_prep_job():
        """Runs weekend preparation. Called Saturday 09:00 and Sunday 14:00."""
        console.print(f"\n[bold cyan]📅 Wochenvorbereitung startet...[/bold cyan]")
        run_weekend_prep(weekend_prep_inst)

    if args.once:
        run_analysis_cycle(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst,
        )
        return

    # Register today's analysis jobs (weekdays only)
    _register_analysis_jobs()

    # Reschedule every day at 00:01 (picks up DST changes and weekday/weekend transitions)
    schedule.every().day.at("00:01").do(_reschedule_analysis)

    # Weekend preparation: Saturday 09:00 and Sunday 14:00 (updated briefing after Sunday news)
    schedule.every().saturday.at("09:00").do(_weekend_prep_job)
    schedule.every().sunday.at("14:00").do(_weekend_prep_job)

    # If today is already weekend, run prep now if no briefing exists for next week
    if datetime.utcnow().weekday() >= 5 and not weekend_prep_inst.get_current_briefing():
        console.print("[bold cyan]📅 Wochenende erkannt – starte Wochenvorbereitung...[/bold cyan]")
        _weekend_prep_job()

    # Hourly tasks (7 days a week)
    schedule.every().hour.do(strategy.check_open_positions)
    if config.enable_social_scan:
        schedule.every().hour.do(_social_scan_job)

    # Periodic regime check + hedge exit monitoring
    if hedge_strategy_inst:
        schedule.every(config.regime_check_interval_hours).hours.do(
            lambda: _run_regime_check()
        )
        schedule.every().hour.do(
            lambda: [
                console.print(f"  [magenta]{a}[/magenta]")
                for a in hedge_strategy_inst.check_hedge_exits()
            ]
        )

    def _run_regime_check():
        regime, actions = hedge_strategy_inst.evaluate_regime()
        if actions:
            for a in actions:
                console.print(f"\n  [magenta]{a}[/magenta]")
            TelegramNotifier().notify_daily_summary(
                total_value=portfolio.total_value(broker.get_prices(list(portfolio.all_positions().keys()))),
                cash=portfolio.cash,
                open_positions=len(portfolio.all_positions()),
                phase=phase_ctrl.current_phase(portfolio.total_value({})),
                progress_pct=phase_ctrl.progress_pct(portfolio.total_value({})),
                actions_today=actions,
            )

    console.print(f"[dim]Stop-Loss-Check stündlich. Wochenvorbereitung Sa 09:00 + So 14:00. Ctrl+C zum Beenden.[/dim]")

    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot gestoppt.[/yellow]")


if __name__ == "__main__":
    main()
