"""
cli/commands.py – CLI command implementations (backtest, scan, social scan, etc.)
"""

import os
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from config import config
from logger import get_logger
from portfolio import Portfolio
from portfolio.performance_tracker import PerformanceTracker
from analyzers.backtester import Backtester
from analyzers.watchlist_scanner import WatchlistScanner
from analyzers.weekend_prep import WeekendPrep
from analyzers.signal_expander import SignalDrivenExpander
from collectors.social_scan import SocialPulseDB
from collectors.reddit_collector import RedditCollector as _RedditColl
from collectors.stocktwits_collector import StockTwitsCollector as _TwitsColl
from collectors.twitter_collector import TwitterCollector as _TwitterColl
from notifier.telegram_notifier import TelegramNotifier

console = Console()
log = get_logger(__name__)

_signal_expander = SignalDrivenExpander()


def _get_watchlist_for_scan(portfolio: Optional[Portfolio] = None):
    """Returns watchlist for social scan; imports from runner to avoid duplication."""
    from bot.runner import _get_watchlist
    return _get_watchlist(portfolio or Portfolio(config.initial_capital))


def run_social_scan(pulse_db: SocialPulseDB, strategy=None):
    """Collects Reddit + StockTwits + Twitter for all watchlist tickers and stores pulse snapshot."""
    reddit = _RedditColl()
    twits = _TwitsColl(lookback_hours=2)
    twitter = _TwitterColl()
    _scan_portfolio = strategy.portfolio if strategy else None
    for ticker in _get_watchlist_for_scan(_scan_portfolio):
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


def run_weekend_prep(wp: WeekendPrep):
    console.rule("[bold blue]Wochenvorbereitung")
    console.print("[cyan]Sammle Earnings-Kalender, Marktdaten und Makro-News...[/cyan]")

    # Show what we find before generating briefing
    earnings = wp.collect_earnings_calendar()
    sentiment = wp.collect_market_sentiment()

    # Earnings table (US + EU)
    wl_earn  = earnings.get("watchlist_earnings", [])
    brd_earn = earnings.get("broader_earnings", [])
    eu_earn  = earnings.get("eu_earnings", [])
    if wl_earn or brd_earn or eu_earn:
        et = Table(title=f"Quartalszahlen nächste Woche ({earnings['week']})", box=box.ROUNDED)
        et.add_column("Ticker", style="cyan")
        et.add_column("Name")
        et.add_column("Wochentag")
        et.add_column("Datum")
        et.add_column("Typ")
        for e in wl_earn:
            et.add_row(e["ticker"], "", e["weekday"], e["date"], "[bold yellow]⚠ Watchlist[/bold yellow]")
        for e in brd_earn[:6]:
            et.add_row(e["ticker"], "", e["weekday"], e["date"], "[dim]US Mover[/dim]")
        for e in eu_earn[:6]:
            flag = "[bold yellow]⚠ EU Watch[/bold yellow]" if e.get("in_watchlist") else "[dim]EU[/dim]"
            et.add_row(e["ticker"], e.get("name", ""), e["weekday"], e["date"], flag)
        console.print(et)
    else:
        console.print("[dim]Keine bekannten Earnings für nächste Woche gefunden.[/dim]")

    # EZB / BoE Warnung
    if sentiment.get("ecb_next_week"):
        console.print(f"  [bold yellow]🏦 EZB Zinsentscheid nächste Woche: {sentiment['ecb_next_week']}[/bold yellow]")
    if sentiment.get("boe_next_week"):
        console.print(f"  [bold yellow]🏦 BoE Zinsentscheid nächste Woche: {sentiment['boe_next_week']}[/bold yellow]")

    # Sector & index table (US + EU)
    if sentiment.get("indices"):
        it = Table(title="Markt letzte Woche (US + EU + Asien)", box=box.SIMPLE)
        it.add_column("Index / Indikator")
        it.add_column("Wert", justify="right")
        it.add_column("Woche", justify="right")
        for name, data in sentiment["indices"].items():
            chg = data["week_change_pct"]
            chg_str = f"[green]+{chg:.2f}%[/green]" if chg >= 0 else f"[red]{chg:.2f}%[/red]"
            extra = f"  {sentiment.get('vix_signal', '')}" if name == "VIX (Angst-Index)" else ""
            it.add_row(name + extra, str(data["value"]), chg_str)
        console.print(it)

    # Crypto weekend
    if sentiment.get("crypto"):
        ct = Table(title="Krypto (7-Tage)", box=box.SIMPLE)
        ct.add_column("Coin")
        ct.add_column("Preis", justify="right")
        ct.add_column("7d", justify="right")
        for name, data in sentiment["crypto"].items():
            chg = data["week_change_pct"]
            chg_str = f"[green]+{chg:.1f}%[/green]" if chg >= 0 else f"[red]{chg:.1f}%[/red]"
            ct.add_row(name, f"${data['price']:,.0f}", chg_str)
        console.print(ct)

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


def run_small_cap_scan(min_sector_gain: float = 1.0, max_results: int = 10) -> None:
    """Small-Cap Sektor-Follower Scanner – nur manuell aufrufbar."""
    from analyzers.small_cap_scanner import SmallCapScanner

    console.rule("[bold magenta]Small-Cap Sektor-Follower Scanner")
    console.print(
        "[dim]Sucht Small-Cap Aktien (200 Mio – 2 Mrd USD) in aktuell trending Sektoren.\n"
        f"Sektor-ETF muss mind. +{min_sector_gain:.1f}% in 5 Tagen gestiegen sein.[/dim]\n"
    )
    console.print("[dim]Lade Marktdaten … (kann 30–60 Sekunden dauern)[/dim]")

    scanner = SmallCapScanner(max_results=max_results, min_sector_etf_gain=min_sector_gain)
    result = scanner.scan()

    if not result.trending_sectors:
        console.print(
            Panel(
                f"[yellow]Kein Sektor zeigt derzeit ausreichend Momentum "
                f"(Schwelle: +{min_sector_gain:.1f}% in 5 Tagen).[/yellow]\n"
                "[dim]Tipp: Schwelle senken mit --sc-min-gain 0.5[/dim]",
                border_style="yellow",
            )
        )
        return

    console.print(
        Panel(
            "Trending Sektoren: " + ", ".join(f"[cyan]{s}[/cyan]" for s in result.trending_sectors) + "\n"
            f"Scan-Dauer: {result.scan_duration_s}s  |  "
            f"Kandidaten geprüft: {len(result.candidates) + result.skipped_count}  |  "
            f"Kriterien nicht erfüllt: {result.skipped_count}",
            border_style="magenta",
        )
    )

    if not result.candidates:
        console.print("[dim]Keine Kandidaten gefunden die alle Kriterien erfüllen.[/dim]")
        console.print("[dim]Tipp: --sc-min-gain 0.5 für niedrigere Schwelle[/dim]")
        return

    table = Table(title=f"Top Small-Cap Kandidaten ({len(result.candidates)})", box=box.ROUNDED)
    table.add_column("Ticker",    style="cyan bold", no_wrap=True)
    table.add_column("Name",      style="white",     max_width=22)
    table.add_column("Sektor",    style="magenta")
    table.add_column("Kurs",      justify="right")
    table.add_column("MktCap",    justify="right")
    table.add_column("1d",        justify="right")
    table.add_column("5d",        justify="right")
    table.add_column("MA20",      justify="center")
    table.add_column("Vol/Tag",   justify="right")
    table.add_column("SL",        justify="right", style="red")
    table.add_column("TP",        justify="right", style="green")
    table.add_column("Score",     justify="right", style="bold")

    for c in result.candidates:
        d1 = f"[green]+{c.change_1d_pct}%[/green]" if c.change_1d_pct >= 0 else f"[red]{c.change_1d_pct}%[/red]"
        d5 = f"[green]+{c.change_5d_pct}%[/green]" if c.change_5d_pct >= 0 else f"[red]{c.change_5d_pct}%[/red]"
        ma = "[green]✓[/green]" if c.above_ma20 else "[red]✗[/red]"
        vol_k = f"{c.volume // 1000}K"
        mktcap = f"{c.market_cap_m:.0f}M"
        score_color = "green" if c.score >= 60 else "yellow" if c.score >= 40 else "red"
        table.add_row(
            c.ticker, c.name[:22], c.sector_name,
            f"${c.price:.2f}", mktcap,
            d1, d5, ma, vol_k,
            f"${c.stop_loss:.2f}", f"${c.take_profit:.2f}",
            f"[{score_color}]{c.score:.0f}[/{score_color}]",
        )

    console.print(table)
    console.print()
    console.print(
        "[dim]Stop-Loss:[/dim] [red]−7%[/red]  "
        "[dim]Take-Profit:[/dim] [green]+20%[/green]  "
        "[dim](strenger als normale Strategie)[/dim]"
    )
    console.print(
        "[dim]Ticker in Watchlist aufnehmen:[/dim] "
        "[cyan]WATCHLIST=AAPL,MSFT,... (in .env)[/cyan]"
    )
    console.print()


def run_crypto_scan(max_results: int = 8) -> None:
    """Krypto-Scanner – nur manuell aufrufbar."""
    from analyzers.crypto_universe import CryptoScanner, CRYPTO_UNIVERSE
    from config import config as _cfg

    watchlist = _cfg.crypto_watchlist or list(CRYPTO_UNIVERSE.keys())
    console.rule("[bold yellow]Krypto-Scanner")
    console.print(
        f"[dim]Analysiert {len(watchlist)} Coins auf Momentum, Volatilität und BTC-Korrelation.\n"
        "Datenquelle: yfinance (30 Tage) | Mindest-Volumen: 100 Mio USD/Tag[/dim]\n"
    )
    console.print("[dim]Lade Marktdaten … (kann 20–40 Sekunden dauern)[/dim]")

    scanner = CryptoScanner(watchlist=watchlist, max_results=max_results)
    result = scanner.scan()

    btc_color = "green" if result.btc_trend == "BULL" else "red" if result.btc_trend == "BEAR" else "yellow"
    console.print(Panel(
        f"BTC-Markttrend: [{btc_color}]{result.btc_trend}[/{btc_color}]  |  "
        f"Coins analysiert: {len(result.candidates)}  |  "
        f"Scan-Dauer: {result.scan_duration_s}s",
        border_style="yellow",
    ))

    if not result.candidates:
        console.print("[dim]Keine Kandidaten gefunden (Volumen zu gering oder Daten nicht verfügbar).[/dim]")
        return

    table = Table(title=f"Krypto-Kandidaten (Top {len(result.candidates)})", box=box.ROUNDED)
    table.add_column("Coin",         style="yellow bold", no_wrap=True)
    table.add_column("Name",         max_width=18)
    table.add_column("Kurs USD",     justify="right")
    table.add_column("24h",          justify="right")
    table.add_column("7d",           justify="right")
    table.add_column("Vola/Jahr",    justify="right")
    table.add_column("MA7",          justify="center")
    table.add_column("BTC-Korr.",    justify="right")
    table.add_column("Vol/Tag",      justify="right")
    table.add_column("Empfehlung",   justify="center")
    table.add_column("Score",        justify="right", style="bold")

    rec_color = {"STRONG_BUY": "green", "BUY": "cyan", "HOLD": "yellow", "AVOID": "red"}
    for c in result.candidates:
        d24 = f"[green]+{c.change_24h_pct:.1f}%[/green]" if c.change_24h_pct >= 0 else f"[red]{c.change_24h_pct:.1f}%[/red]"
        d7  = f"[green]+{c.change_7d_pct:.1f}%[/green]"  if c.change_7d_pct  >= 0 else f"[red]{c.change_7d_pct:.1f}%[/red]"
        ma  = "[green]✓[/green]" if c.above_ma7 else "[red]✗[/red]"
        vol = f"{c.volume_24h_usd_m:.0f}M"
        rc  = rec_color.get(c.recommendation, "white")
        sc  = "green" if c.score >= 60 else "yellow" if c.score >= 40 else "red"
        table.add_row(
            c.symbol, c.name[:18],
            f"${c.price_usd:,.2f}", d24, d7,
            f"{c.volatility_ann_pct:.0f}%", ma,
            f"{c.btc_correlation:.2f}", vol,
            f"[{rc}]{c.recommendation}[/{rc}]",
            f"[{sc}]{c.score:.0f}[/{sc}]",
        )

    console.print(table)
    console.print()
    console.print(f"[dim]Stop-Loss: [red]−{int(_cfg.crypto_stop_loss_pct*100)}%[/red]  "
                  f"Take-Profit: [green]+{int(_cfg.crypto_take_profit_pct*100)}%[/green]  "
                  f"Max. Portfolio-Anteil Krypto: [yellow]{int(_cfg.crypto_max_portfolio_pct*100)}%[/yellow][/dim]")
    console.print("[dim]Krypto-Handel aktivieren: [cyan]CRYPTO_ENABLED=true[/cyan] in .env[/dim]\n")


def run_eu_scan(
    max_results: int = 12,
    country_filter: list = None,
    sector_filter: list = None,
) -> None:
    """EU-Aktien-Scanner – nur manuell aufrufbar."""
    from analyzers.eu_stock_scanner import EUStockScanner, EU_UNIVERSE

    console.rule("[bold blue]Europäischer Aktien-Scanner")
    label_parts = []
    if country_filter:
        label_parts.append(f"Länder: {', '.join(country_filter)}")
    if sector_filter:
        label_parts.append(f"Sektoren: {', '.join(sector_filter)}")
    console.print(
        f"[dim]Scannt {len(EU_UNIVERSE)} EU-Aktien (XETRA/AEX/CAC40/SMI/FTSE)."
        + (f"\nFilter: {' | '.join(label_parts)}" if label_parts else "")
        + "\nDatenquelle: yfinance (30 Tage)[/dim]\n"
    )
    console.print("[dim]Lade Marktdaten … (kann 30–60 Sekunden dauern)[/dim]")

    scanner = EUStockScanner(
        max_results=max_results,
        country_filter=country_filter,
        sector_filter=sector_filter,
    )
    result = scanner.scan()

    if not result.candidates:
        console.print("[dim]Keine Kandidaten gefunden die alle Kriterien erfüllen.[/dim]")
        return

    by_c = "  ".join(f"[cyan]{k}[/cyan]:{v}" for k, v in sorted(result.by_country.items(), key=lambda x: -x[1]))
    by_s = "  ".join(f"[magenta]{k}[/magenta]:{v}" for k, v in sorted(result.by_sector.items(), key=lambda x: -x[1]))
    console.print(Panel(
        f"Kandidaten: {len(result.candidates)} / {len(EU_UNIVERSE)}  |  "
        f"Scan-Dauer: {result.scan_duration_s}s\n"
        f"Länder:  {by_c}\n"
        f"Sektoren: {by_s}",
        border_style="blue",
    ))

    table = Table(title=f"Top EU-Aktien ({len(result.candidates)})", box=box.ROUNDED)
    table.add_column("Ticker",   style="cyan bold", no_wrap=True)
    table.add_column("Name",     max_width=20)
    table.add_column("Land",     justify="center")
    table.add_column("Sektor",   max_width=16)
    table.add_column("Kurs",     justify="right")
    table.add_column("Whg.",     justify="center")
    table.add_column("1d",       justify="right")
    table.add_column("5d",       justify="right")
    table.add_column("1M",       justify="right")
    table.add_column("MA20",     justify="center")
    table.add_column("Vol-Ratio",justify="right")
    table.add_column("SL",       justify="right", style="red")
    table.add_column("TP",       justify="right", style="green")
    table.add_column("Score",    justify="right", style="bold")

    for c in result.candidates:
        d1 = f"[green]+{c.change_1d_pct:.1f}%[/green]" if c.change_1d_pct >= 0 else f"[red]{c.change_1d_pct:.1f}%[/red]"
        d5 = f"[green]+{c.change_5d_pct:.1f}%[/green]" if c.change_5d_pct >= 0 else f"[red]{c.change_5d_pct:.1f}%[/red]"
        dm = f"[green]+{c.change_1m_pct:.1f}%[/green]" if c.change_1m_pct >= 0 else f"[red]{c.change_1m_pct:.1f}%[/red]"
        ma = "[green]✓[/green]" if c.above_ma20 else "[red]✗[/red]"
        sc = "green" if c.score >= 60 else "yellow" if c.score >= 40 else "red"
        table.add_row(
            c.ticker, c.name[:20], c.country, c.sector[:16],
            f"{c.price:.2f}", c.currency,
            d1, d5, dm, ma, f"{c.volume_ratio:.2f}×",
            f"{c.stop_loss:.2f}", f"{c.take_profit:.2f}",
            f"[{sc}]{c.score:.0f}[/{sc}]",
        )

    console.print(table)
    console.print()
    console.print("[dim]Stop-Loss: [red]−6%[/red]  Take-Profit: [green]+15%[/green][/dim]")
    console.print("[dim]EU-Handel aktivieren: [cyan]EU_STOCKS_ENABLED=true[/cyan] in .env\n"
                  "Eigene Watchlist: [cyan]EU_WATCHLIST=SAP.DE,ASML.AS,...[/cyan] in .env[/dim]\n")


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


def _run_optimizer(tracker, apply: bool = False) -> None:
    """Zeigt Parameter-Optimierungsvorschläge und schreibt sie optional in .env."""
    from analyzers.parameter_optimizer import ParameterOptimizer
    optimizer = ParameterOptimizer(tracker)
    report = optimizer.analyze()
    console.rule("[bold blue]Parameter-Optimierung")
    console.print(report.to_text())

    if not report.has_suggestions:
        return

    if apply:
        console.print("\n[cyan]Schreibe Änderungen in .env...[/cyan]")
        lines = optimizer.apply(report)
        for l in lines:
            console.print(f"  [green]{l}[/green]")
        console.print("\n[bold green]Fertig![/bold green] Bot neu starten damit Änderungen wirksam werden.")
        console.print("[dim]Backup der alten .env wurde automatisch erstellt.[/dim]")
    else:
        console.print(
            "\n[dim]Zum Anwenden:[/dim] [cyan]python main.py --optimize --apply[/cyan]\n"
            "[dim]Zum Ablehnen:[/dim] Nichts tun – .env bleibt unverändert."
        )


def _set_env_var(key: str, value: str) -> None:
    """Schreibt einen Key=Value Eintrag in .env (oder legt ihn an)."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    lines: list = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                lines[i] = f"{key}={value}\n"
                found = True
                break
    if not found:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _handle_exploration_command(cmd: str) -> None:
    """Verarbeitet --exploration on|off|status."""
    if cmd == "on":
        _set_env_var("EXPLORATION_MODE", "true")
        console.print(Panel(
            "[bold yellow]EXPLORATION MODE AKTIVIERT[/bold yellow]\n\n"
            "Der Bot handelt jetzt mit lockeren Parametern:\n"
            f"  • Kaufschwelle:    [cyan]{config.expl_buy_threshold}[/cyan] (normal: {config.buy_threshold})\n"
            f"  • Min. Quellen:    [cyan]{config.expl_min_sources}[/cyan] (normal: {config.min_sources})\n"
            f"  • Max. Position:   [cyan]{config.expl_max_position_pct*100:.0f}%[/cyan] (normal: {config.max_position_pct*100:.0f}%)\n"
            f"  • Tagesverlust-CB: [cyan]{config.expl_max_daily_loss*100:.0f}%[/cyan] (normal: 5%)\n\n"
            "Ziel: Daten sammeln, Fehler machen, RL-Agent trainieren.\n"
            "Deaktivieren: [dim]python main.py --exploration off[/dim]",
            title="Exploration Mode", border_style="yellow",
        ))
    elif cmd == "off":
        _set_env_var("EXPLORATION_MODE", "false")
        console.print(Panel(
            "[bold green]EXPLORATION MODE DEAKTIVIERT[/bold green]\n\n"
            "Der Bot kehrt zu normalen (strengeren) Parametern zurück.\n"
            "Tipp: [dim]python main.py --optimize[/dim] um aus den gesammelten\n"
            "Daten optimierte Parameter abzuleiten.",
            title="Exploration Mode", border_style="green",
        ))
    elif cmd == "status":
        active = config.exploration_mode
        color  = "yellow" if active else "green"
        status = "AKTIV" if active else "INAKTIV"
        lines  = [f"Exploration Mode: [{color}]{status}[/{color}]\n"]
        if active:
            lines += [
                f"  Kaufschwelle:    {config.expl_buy_threshold} (normal: {config.buy_threshold})",
                f"  Min. Quellen:    {config.expl_min_sources} (normal: {config.min_sources})",
                f"  Max. Position:   {config.expl_max_position_pct*100:.0f}% (normal: {config.max_position_pct*100:.0f}%)",
                f"  Tagesverlust-CB: {config.expl_max_daily_loss*100:.0f}% (normal: 5%)",
            ]
        console.print(Panel("\n".join(lines), title="Exploration Status", border_style=color))
    else:
        console.print(f"[red]Unbekannte Option '{cmd}'. Nutze: on | off | status[/red]")


def _apply_exploration_overrides() -> None:
    """
    Überschreibt Config-Werte mit Exploration-Parametern wenn EXPLORATION_MODE aktiv.
    Wird einmal beim Bot-Start aufgerufen – alle Downstream-Komponenten erhalten
    automatisch die gelockerten Werte.
    """
    if not config.exploration_mode:
        return
    config.buy_threshold    = config.expl_buy_threshold
    config.min_sources      = config.expl_min_sources
    config.max_position_pct = config.expl_max_position_pct
    # Circuit Breaker liest MAX_DAILY_LOSS_PCT direkt aus env → env var setzen
    os.environ["MAX_DAILY_LOSS_PCT"] = str(config.expl_max_daily_loss)
