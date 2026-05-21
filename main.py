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
          python main.py --goal          (Ziel-Analyse: Wahrscheinlichkeit und Status)
          python main.py --optimize      (Parameter-Optimierung basierend auf Trade-History)
          python main.py --optimize --apply  (Vorschläge direkt in .env schreiben)
          python main.py --margin        (Margin-Bereitschaft prüfen und Empfehlung anzeigen)
          python main.py --score         (Bot-Score anzeigen: Punkte, Meilensteine, History)
          python main.py --reentry       (Re-Entry-Kandidaten: verkaufte Positionen die sich erholen)
          python main.py --velocity AAPL (News-Geschwindigkeit für einen Ticker anzeigen)
          python main.py --sentiment-memory  (Sentiment-Verlässlichkeit pro Ticker anzeigen)
          python main.py --small-cap-scan   (Small-Cap Sektor-Follower: 200 Mio – 2 Mrd USD)
          python main.py --small-cap-scan --sc-min-gain 0.5  (niedrigere Schwelle)
          python main.py --small-cap-scan --sc-results 15    (mehr Ergebnisse)
          python main.py --crypto-scan                       (Krypto: BTC/ETH/SOL/... Momentum & Volatilität)
          python main.py --crypto-scan --crypto-results 12  (mehr Coins anzeigen)
          python main.py --eu-scan                          (EU-Aktien: XETRA/CAC40/SMI/FTSE/AEX)
          python main.py --eu-scan --eu-country DE FR       (nur Deutschland + Frankreich)
          python main.py --eu-scan --eu-sector Technologie  (nur Technologie-Sektor)
          python main.py --risk-metrics                     (Sharpe, Sortino, Calmar, Max Drawdown)
          python main.py --tax [--tax-year 2025] [--tax-csv] (Abgeltungssteuer-Report)
          python main.py --dividends [--dividend-ticker AAPL] (Dividenden-Übersicht + Ex-Div-Termine)
          python main.py --fx-pnl                          (P&L aufgeschlüsselt nach Währung)

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

from rich.console import Console
from rich.panel import Panel

from config import config, validate_config
from logger import get_logger
from broker.paper_broker import PaperBroker
from broker.alpaca_broker import AlpacaBroker
from broker.ibkr_broker import IBKRBroker
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from portfolio.trade_journal import TradeJournal
from portfolio.signal_queue import SignalQueue
from portfolio.goal_risk_assessor import GoalRiskAssessor
from analyzers.reflection_engine import ReflectionEngine
from analyzers.earnings_filter import EarningsFilter
from analyzers.correlation_check import CorrelationChecker
from analyzers.kelly_sizing import KellySizer
from analyzers.market_schedule import MarketSchedule
from analyzers.weekend_prep import WeekendPrep
from analyzers.recession_detector import RecessionDetector
from collectors.news_archive import NewsArchive
from collectors.social_scan import SocialPulseDB
from collectors.tradingview_webhook import start_webhook_server
from collectors.tv_executor import start_tv_executor
from collectors.earnings_protector import start_earnings_protector
from collectors.vix_monitor import vix_summary
from collectors.rl_trainer import start_rl_trainer
from strategy import SwingStrategy
from strategy.hedge_strategy import HedgeStrategy
from reporting.exporter import Exporter

from bot.runner import (
    _make_phase_ctrl, _make_focus_ctrl, _rl_agent,
    run_analysis_cycle,
)
from bot.scheduler import run_bot_loop
from cli.display import (
    show_status, show_report, show_monthly_review, show_trade_journal,
    show_focus_info, show_pulse, show_signal_queue, show_briefing,
    show_regime, show_goal, show_crash_radar, show_fx_status,
    _run_score_display, _run_margin_check, _run_velocity_display,
    _run_sentiment_memory_display, _run_reentry_display,
)
from cli.commands import (
    run_social_scan, run_weekend_prep, run_backtest, run_scan,
    run_small_cap_scan, run_crypto_scan, run_eu_scan,
    _run_optimizer, _handle_exploration_command, _apply_exploration_overrides,
)
from cli.tax_commands import (
    run_risk_metrics, run_tax_report, run_dividend_overview, run_fx_pnl,
)

console = Console()
log = get_logger(__name__)


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
    parser.add_argument("--goal", action="store_true", help="Ziel-Analyse: Wahrscheinlichkeit und Status")
    parser.add_argument("--optimize", action="store_true", help="Parameter-Optimierung basierend auf Trade-History")
    parser.add_argument("--apply", action="store_true", help="Optimierungs-Vorschläge in .env schreiben")
    parser.add_argument("--margin", action="store_true", help="Margin-Bereitschaft prüfen und Empfehlung anzeigen")
    parser.add_argument("--score", action="store_true", help="Bot-Score anzeigen (Punkte, Meilensteine, History)")
    parser.add_argument("--reentry", action="store_true", help="Re-Entry-Kandidaten anzeigen (verkaufte Positionen die sich erholen)")
    parser.add_argument("--velocity", metavar="TICKER", help="News-Geschwindigkeit für einen Ticker anzeigen")
    parser.add_argument("--sentiment-memory", action="store_true", help="Sentiment-Verlässlichkeit pro Ticker anzeigen")
    parser.add_argument("--exploration", metavar="{on|off|status}", help="Exploration-Mode ein-/ausschalten oder Status anzeigen")
    parser.add_argument("--crash-radar", action="store_true", help="Crash-Wahrscheinlichkeit, Blasen-Detektor und historischer Vergleich")
    parser.add_argument("--refresh", action="store_true", help="Cache ignorieren (zusammen mit --crash-radar)")
    parser.add_argument("--walk-forward", action="store_true", help="Walk-Forward Backtesting: Parameter-Stabilität über Zeitfenster validieren")
    parser.add_argument("--wf-tickers", nargs="+", metavar="TICKER", help="Ticker für Walk-Forward (Standard: Watchlist aus .env)")
    parser.add_argument("--short-status", action="store_true", help="Aktive Short/Inverse-ETF Positionen und unrealisierten P&L anzeigen")
    parser.add_argument("--small-cap-scan", action="store_true", help="Small-Cap Sektor-Follower Scanner (manuell, 200 Mio – 2 Mrd USD)")
    parser.add_argument("--sc-min-gain", type=float, default=1.0, metavar="PCT", help="Mindest-Sektor-Gain in %% für Small-Cap Scan (Standard: 1.0)")
    parser.add_argument("--sc-results", type=int, default=10, metavar="N", help="Maximale Anzahl Ergebnisse im Small-Cap Scan (Standard: 10)")
    parser.add_argument("--crypto-scan", action="store_true", help="Krypto-Scanner: Momentum, Volatilität und BTC-Korrelation anzeigen")
    parser.add_argument("--crypto-results", type=int, default=8, metavar="N", help="Maximale Anzahl Ergebnisse im Krypto-Scan (Standard: 8)")
    parser.add_argument("--eu-scan", action="store_true", help="EU-Aktien Scanner: XETRA/AEX/CAC40/SMI/FTSE Kandidaten")
    parser.add_argument("--eu-country", nargs="+", metavar="DE|FR|NL|CH|GB|DK|ES", help="Länderfilter für EU-Scan (z.B. --eu-country DE FR)")
    parser.add_argument("--eu-sector", nargs="+", metavar="SEKTOR", help="Sektorfilter für EU-Scan (z.B. --eu-sector Technologie Halbleiter)")
    parser.add_argument("--fx-status", action="store_true", help="Wechselkurs-Signale für EU-Aktien anzeigen (GBP/CHF/SEK Gegenwind)")
    parser.add_argument("--risk-metrics", action="store_true", help="Sharpe, Sortino, Calmar Ratio + Max Drawdown anzeigen")
    parser.add_argument("--tax", action="store_true", help="Abgeltungssteuer-Report anzeigen")
    parser.add_argument("--tax-year", type=int, default=None, metavar="YYYY", help="Jahr für Steuer-Report (Standard: aktuelles Jahr)")
    parser.add_argument("--tax-csv", action="store_true", help="Steuer-Report als CSV exportieren (data/steuerbericht_YYYY.csv)")
    parser.add_argument("--dividends", action="store_true", help="Dividenden-Übersicht und bevorstehende Ex-Div-Termine")
    parser.add_argument("--dividend-ticker", metavar="TICKER", help="Dividenden nur für einen bestimmten Ticker anzeigen")
    parser.add_argument("--fx-pnl", action="store_true", help="Unrealisierten P&L nach Handelswährung aufschlüsseln")
    args = parser.parse_args()

    if args.exploration is not None:
        _handle_exploration_command(args.exploration.lower())
        return

    if args.fx_status:
        show_fx_status()
        return

    if args.risk_metrics:
        run_risk_metrics()
        return

    if args.tax:
        run_tax_report(year=args.tax_year, export_csv=args.tax_csv)
        return

    if args.dividends:
        run_dividend_overview(ticker=args.dividend_ticker)
        return

    if args.fx_pnl:
        run_fx_pnl()
        return

    if args.small_cap_scan:
        run_small_cap_scan(min_sector_gain=args.sc_min_gain, max_results=args.sc_results)
        return

    if args.crypto_scan:
        run_crypto_scan(max_results=args.crypto_results)
        return

    if args.eu_scan:
        run_eu_scan(
            max_results=15,
            country_filter=[c.upper() for c in args.eu_country] if args.eu_country else None,
            sector_filter=args.eu_sector,
        )
        return

    if args.dashboard:
        dashboard_path = __file__.replace("main.py", "dashboard/app.py")
        console.print("[cyan]Starte Streamlit-Dashboard...[/cyan]")
        subprocess.run(["streamlit", "run", dashboard_path])
        return

    # Konfiguration validieren – bricht bei fatalen Fehlern ab
    validate_config()

    # Exploration-Mode: lockere Parameter für Datensammlung (vor allem anderen!)
    _apply_exploration_overrides()
    if config.exploration_mode:
        console.print(Panel(
            "[bold yellow]EXPLORATION MODE AKTIV[/bold yellow]  –  "
            f"Kaufschwelle={config.buy_threshold}  |  "
            f"MinQuellen={config.min_sources}  |  "
            f"MaxPos={config.max_position_pct*100:.0f}%  |  "
            f"CB-Verlust={config.expl_max_daily_loss*100:.0f}%\n"
            "[dim]Deaktivieren: python main.py --exploration off[/dim]",
            title="⚠  Exploration Mode", border_style="yellow",
        ))

    # Select broker based on config
    if config.broker_mode == "alpaca":
        broker = AlpacaBroker()
        if not broker._check_creds():
            console.print("[yellow]⚠ Alpaca-Credentials fehlen – Fallback auf Paper-Broker.[/yellow]")
            broker = PaperBroker()
        else:
            console.print("[green]✓ Alpaca-Broker aktiv[/green]")
    elif config.broker_mode == "ibkr":
        broker = IBKRBroker()
        if broker.is_connected():
            console.print(
                f"[green]✓ Interactive Brokers aktiv "
                f"({config.ibkr_host}:{config.ibkr_port})[/green]"
            )
        else:
            console.print(
                "[yellow]⚠ IBKR-Verbindung fehlgeschlagen "
                f"({config.ibkr_host}:{config.ibkr_port}) – Fallback auf Paper-Broker.[/yellow]"
            )
            broker = PaperBroker()
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

    # TradingView Webhook-Server (optional, läuft als Background-Thread)
    if config.tradingview_webhook_enabled:
        start_webhook_server(
            signal_queue=signal_queue,
            port=config.tradingview_webhook_port,
            secret=config.tradingview_webhook_secret,
        )
        console.print(
            f"  [bold green]📡 TradingView Webhook aktiv[/bold green] "
            f"(Port {config.tradingview_webhook_port})"
        )

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

    if args.goal:
        goal_risk_tmp = GoalRiskAssessor(
            target_value=config.target_goal_amount,
            target_date_str=config.target_goal_date,
            initial_capital=config.initial_capital,
        )
        show_goal(goal_risk_tmp, portfolio, broker, tracker)
        return

    if args.optimize:
        _run_optimizer(tracker, apply=args.apply)
        return

    if args.margin:
        _run_margin_check(tracker)
        return

    if args.score:
        _run_score_display()
        return

    if args.reentry:
        _run_reentry_display(broker)
        return

    if args.velocity:
        _run_velocity_display(args.velocity.upper())
        return

    if args.sentiment_memory:
        _run_sentiment_memory_display()
        return

    if args.crash_radar:
        show_crash_radar(force=getattr(args, "refresh", False))
        return

    if args.walk_forward:
        from analyzers.walk_forward_backtester import WalkForwardBacktester
        tickers = args.wf_tickers or config.watchlist
        if not tickers:
            console.print("[red]Keine Ticker angegeben. Watchlist in .env setzen oder --wf-tickers AAPL MSFT ... nutzen.[/red]")
            return
        console.print(Panel(
            f"[bold cyan]Walk-Forward Backtesting[/bold cyan]\n"
            f"Ticker: {', '.join(tickers[:10])}{'...' if len(tickers) > 10 else ''}\n"
            f"Zeitraum: 3 Jahre | Trainings-Fenster: 6M | Test-Fenster: 3M",
            border_style="cyan",
        ))
        wf = WalkForwardBacktester(tickers=tickers, initial_capital=config.initial_capital)
        result = wf.run()
        console.print(Panel(
            f"[bold]Ergebnisse[/bold]\n"
            f"Fenster analysiert:         {result.total_windows}\n"
            f"Ø Test-Return:              {result.avg_test_return:+.1f}%\n"
            f"Ø Train-Return:             {result.avg_train_return:+.1f}%\n"
            f"Walk-Forward-Effizienz:     {result.walk_forward_efficiency:.2f} "
            f"({'[green]gut[/green]' if result.walk_forward_efficiency >= 0.5 else '[yellow]mittel[/yellow]' if result.walk_forward_efficiency >= 0.2 else '[red]schwach[/red]'})\n"
            f"Stabilitäts-Score:          {result.stability_score:.0f}%\n"
            f"Beste Parameter:            SL={result.best_params.get('stop_loss_pct', 0)*100:.0f}%  "
            f"TP={result.best_params.get('take_profit_pct', 0)*100:.0f}%  "
            f"Hold={result.best_params.get('hold_days', 0)}d\n"
            f"Regime-Breakdown:           "
            + "  ".join(f"{r}={v:+.1f}%" for r, v in result.regime_breakdown.items()) + "\n"
            f"\n[bold]{result.recommendation}[/bold]",
            title="Walk-Forward Backtest", border_style="cyan",
        ))
        return

    if args.short_status:
        from strategy.short_strategy import ShortStrategy
        short = ShortStrategy(portfolio, broker, tracker, journal, None)
        s = short.summary()
        if not s["enabled"]:
            console.print("[yellow]Short-Selling ist deaktiviert. SHORT_ENABLED=true in .env setzen.[/yellow]")
            return
        if s["active_count"] == 0:
            console.print("[dim]Keine aktiven Short-Positionen.[/dim]")
            return
        rows = []
        for p in s["positions"]:
            pnl_color = "green" if p["pnl_unrealized"] >= 0 else "red"
            rows.append(
                f"  {p['ticker']:6s} → {p['inverse_ticker']:6s}  "
                f"{p['shares']:.2f} Stk  "
                f"@ ${p['entry_price']:.2f} → ${p['current_price'] or '?':.2f}  "
                f"[{pnl_color}]P&L ${p['pnl_unrealized']:+.2f}[/{pnl_color}]  "
                f"SL ${p['stop_loss']:.2f}  TP ${p['take_profit']:.2f}  "
                f"{p['days_held']}d"
            )
        console.print(Panel(
            f"Aktive Shorts: {s['active_count']}/{s['max_shorts']}\n\n" + "\n".join(rows),
            title="Short-Positionen (Inverse ETFs)", border_style="yellow",
        ))
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

    # TradingView Sofortausführungs-Thread
    if config.tradingview_webhook_enabled:
        start_tv_executor(strategy, interval_seconds=60)
        console.print("  [bold green]⚡ TradingView Sofortausführung aktiv[/bold green] (alle 60s)")

    # Earnings-Schutz: schließt Positionen 2 Tage vor Quartalsergebnissen
    if config.earnings_protection_enabled:
        start_earnings_protector(strategy, check_interval_hours=12)
        console.print("  [bold yellow]🛡 Earnings-Protector aktiv[/bold yellow] (prüft alle 12h)")

    # VIX-Status beim Start anzeigen
    if config.vix_risk_enabled:
        try:
            console.print(f"  [dim]📊 {vix_summary()}[/dim]")
        except Exception:
            pass

    # RL-Trainer: trainiert alle 24h auf Trade-History
    try:
        import os as _os
        journal_db = _os.path.join("data", "trade_journal.db")
        start_rl_trainer(_rl_agent, journal_db, interval_hours=24)
        console.print("  [bold cyan]🤖 RL-Agent aktiv[/bold cyan] (trainiert alle 24h auf Trade-History)")
        rl_stats = _rl_agent.get_stats()
        if rl_stats.get("total_trades", 0) > 0:
            console.print(f"  [dim]   RL: {rl_stats['total_trades']} Trades gelernt, avg_reward={rl_stats.get('avg_reward', 0):.3f}[/dim]")
    except Exception as e:
        log.warning("RL-Trainer Start fehlgeschlagen: %s", e)

    # Regime + Cross-Asset Status
    try:
        from analyzers.regime_adaptive import get_adaptive_params, RegimeAdaptiveConfig as _RAC
        regime_params = get_adaptive_params()
        console.print(f"  [dim]📈 {_RAC().summary(regime_params.label.split()[0] if hasattr(regime_params,'label') else 'NEUTRAL')}[/dim]")
    except Exception:
        pass
    try:
        from bot.runner import _cross_asset
        ca = _cross_asset.fetch()
        ca_color = "green" if ca.recommendation == "RISK_ON" else "red" if ca.recommendation == "RISK_OFF" else "yellow"
        console.print(f"  [dim]🌐 Cross-Asset: [{ca_color}]{ca.recommendation}[/{ca_color}] (Score={ca.risk_appetite_score:.2f})[/dim]")
    except Exception:
        pass

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

    # Build market schedule
    mkt_schedule = MarketSchedule(
        exchanges=config.market_exchanges,
        lead_minutes=config.market_lead_minutes,
    )

    if args.once:
        run_analysis_cycle(
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst,
        )
        return

    run_bot_loop(
        args=args,
        portfolio=portfolio,
        broker=broker,
        strategy=strategy,
        tracker=tracker,
        phase_ctrl=phase_ctrl,
        focus_ctrl=focus_ctrl,
        archive=archive,
        reflection=reflection,
        signal_queue=signal_queue,
        pulse_db=pulse_db,
        weekend_prep_inst=weekend_prep_inst,
        goal_risk=goal_risk,
        hedge_strategy_inst=hedge_strategy_inst,
        mkt_schedule=mkt_schedule,
    )


if __name__ == "__main__":
    main()
