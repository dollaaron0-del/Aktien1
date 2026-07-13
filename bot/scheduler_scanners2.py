"""
bot/scheduler_scanners2.py – Signal-Scanner-Jobs, Teil 2: Reddit-Hype,
Kursbewegungs-Alarm, Options-Flow, PEAD, Short-Squeeze, Insider-Proaktiv,
Sektor-Kaskade, Intraday-Scan.

Ausgelagert aus bot/scheduler.py::run_bot_loop (Roadmap 4.4a, vierte Naht,
Teil 2 — Rest der Scanner-Gruppe, keine Cross-Calls zueinander im
Unterschied zu Teil 1's escalate_ticker-Abhängigkeit). Cooldown-Dicts
bleiben Objekte, die run_bot_loop hält und durchreicht (kein Modul-
Singleton) — gleiches Muster wie in allen vorherigen Nähten.

scheduler.py behält für jeden Job einen gleichnamigen dünnen Wrapper:
`schedule` übernimmt den Funktionsnamen per functools.update_wrapper,
test_scheduler_registration.py prüft ihn.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Dict

from logger import get_logger
from rich.console import Console

log = get_logger(__name__)
console = Console()

_INSIDER_COOLDOWN_HOURS = int(os.getenv("INSIDER_COOLDOWN_HOURS", "24"))
_CASCADE_COOLDOWN_HOURS = int(os.getenv("CASCADE_COOLDOWN_HOURS", "6"))


def reddit_hype_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                    weekend_prep_inst, hedge_strategy_inst, earnings_strategy, config,
                    telegram_notifier_cls, scanner_notify_fn: Callable,
                    safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Scannt Reddit (wallstreetbets, stocks, investing …) auf organisch
    trending Ticker — unabhängig von der Watchlist.
    Tickers mit steigender Erwähnungsfrequenz (velocity > 1.5) kommen
    direkt in die Analyse-Queue.
    """
    try:
        from collectors.reddit_hype_scanner import RedditHypeScanner
        from analyzers.user_request_queue import add_ticker as _req_ticker

        log.info("Reddit-Hype-Scanner gestartet …")
        hits = RedditHypeScanner().scan(top_n=10, min_mentions=3)
        if not hits:
            return

        exclude   = set(portfolio.all_positions().keys())
        watchlist = set(getattr(config, "watchlist", []))
        notifier  = telegram_notifier_cls()

        queued = []
        for h in hits:
            # Skip existing positions and tickers already on watchlist
            # (watchlist is handled by existing scanners)
            if h.ticker in exclude:
                continue
            if h.velocity < 1.5 and h.mentions < 5:
                continue
            _req_ticker(h.ticker, meta={
                "signal_type":   "REDDIT_HYPE",
                "score":         min(0.90, 0.55 + min(h.velocity, 5.0) * 0.07),
                "headline":      (
                    f"Reddit: {h.mentions}× erwähnt | "
                    f"Velocity ×{h.velocity:.1f} | "
                    f"r/{', r/'.join(h.subreddits[:2])}"
                ),
                "from_headline": False,
                "reddit_hype":   True,
            })
            queued.append(h)

        if not queued:
            return

        lines = "\n".join(
            f"  • <b>{h.ticker}</b> – {h.mentions}× | "
            f"Velocity ×{h.velocity:.1f} | "
            f"{h.sample_titles[0][:60] if h.sample_titles else ''}"
            for h in queued
        )
        scanner_notify_fn(
            notifier,
            f"🔥 <b>Reddit-Hype-Scanner</b>\n\n{lines}\n\n"
            f"🔍 Analyse wird gestartet …"
        )
        console.print(
            f"  [bold magenta]🔥 Reddit-Hype: "
            f"{', '.join(h.ticker for h in queued)} "
            f"→ Analyse-Queue[/bold magenta]"
        )
        safe_run_analysis_cycle_fn(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
    except Exception as e:
        log.warning("Reddit-Hype-Job fehlgeschlagen: %s", e)


def price_move_job(broker, config, price_move_last: Dict[str, float],
                   telegram_notifier_cls, scanner_notify_fn: Callable) -> None:
    """
    Prüft alle 5 Min ob eine Watchlist-Aktie um ≥ PRICE_MOVE_THRESHOLD
    gestiegen/gefallen ist. Bei Ausschlag: sofortige Claude-Analyse +
    Telegram-Alert. Läuft nur an Handelstagen während der Kernzeiten.
    """
    _MOVE_THRESHOLD = float(os.getenv("PRICE_MOVE_THRESHOLD", "0.02"))  # 2%
    try:
        local_now = datetime.now()
        # Nur wochentags zwischen 08:00 und 22:00 Lokalzeit prüfen
        if local_now.weekday() >= 5 or not (8 <= local_now.hour < 22):
            return
        watchlist = list(config.watchlist)
        if not watchlist:
            return
        prices = broker.get_prices(watchlist)
        triggered = []
        for ticker, price in prices.items():
            if not price or price <= 0:
                continue
            last = price_move_last.get(ticker)
            if last and last > 0:
                move = (price - last) / last
                if abs(move) >= _MOVE_THRESHOLD:
                    direction = "📈" if move > 0 else "📉"
                    triggered.append((ticker, price, last, move, direction))
            price_move_last[ticker] = price
        if not triggered:
            return
        notifier = telegram_notifier_cls()
        from analyzers.user_request_queue import add_ticker as _add_req
        # Alle ausgelösten Ticker in EINER Sammelnachricht bündeln (statt einer
        # Einzelnachricht pro Aktie – das war die Telegram-Flut). Queuing bleibt pro Ticker.
        _alert_lines = []
        for ticker, price, last_p, move, icon in triggered:
            console.print(
                f"  [bold {'green' if move > 0 else 'red'}]"
                f"{icon} Kursalarm {ticker}: {move:+.1%} "
                f"(${last_p:.2f} → ${price:.2f})[/bold {'green' if move > 0 else 'red'}]"
            )
            # Options-Flow als Bestätigung prüfen
            options_note = ""
            try:
                from collectors.options_flow_collector import OptionsFlowCollector
                flow = OptionsFlowCollector().collect(ticker)
                bullish = [f for f in flow if f.get("signal") == "BULLISCH"]
                bearish = [f for f in flow if f.get("signal") == "BÄRISCH"]
                if bullish:
                    options_note = "  📊 Flow bestätigt"
                elif bearish:
                    options_note = "  📊 Flow warnt"
            except Exception:
                pass
            _alert_lines.append(
                f"{icon} <b>{ticker}</b>  <b>{move:+.1%}</b>  "
                f"${last_p:.2f} → ${price:.2f}{options_note}"
            )
            _add_req(ticker, meta={
                "signal_type":   "PRICE_MOVE",
                "score":         min(0.95, 0.70 + abs(move) * 5),
                "headline":      f"{icon} {ticker} {move:+.1%} in 5 Min",
                "from_headline": True,
                "move_pct":      round(move * 100, 2),
            })
            log.info("Kursalarm %s: %+.1f%% → Sofort-Analyse ausgelöst", ticker, move * 100)
        scanner_notify_fn(
            notifier,
            f"⚡ <b>Kursalarm</b> ({len(triggered)} Titel · 5 Min)\n"
            f"━━━━━━━━━━━━━━\n"
            + "\n".join(_alert_lines)
            + "\n\n🔍 Vollanalyse läuft – Ergebnis folgt in wenigen Minuten."
        )
    except Exception as e:
        log.debug("Kursbewegungs-Job fehlgeschlagen: %s", e)


def options_flow_job(config, telegram_notifier_cls, scanner_notify_fn: Callable) -> None:
    """
    Scannt Options-Flow der gesamten Watchlist auf ungewöhnliche
    Call/Put-Aktivität. C/P-Ratio ≥ 3 oder P/C-Ratio ≥ 3 → Sofort-Analyse.
    """
    _OPT_RATIO = float(os.getenv("OPTIONS_FLOW_RATIO", "3.0"))
    try:
        local_now = datetime.now()
        if local_now.weekday() >= 5 or not (14 <= local_now.hour < 21):
            return   # Nur während NYSE-Handelszeiten sinnvoll
        from collectors.options_flow_collector import OptionsFlowCollector
        from analyzers.user_request_queue import add_ticker as _add_req
        collector = OptionsFlowCollector(min_volume_ratio=_OPT_RATIO)
        notifier = telegram_notifier_cls()
        # Treffer sammeln und in EINER Nachricht bündeln (kein Spam pro Ticker).
        _flow_hits = []
        for ticker in config.watchlist:
            try:
                signals = collector.collect(ticker)
                bullish = [s for s in signals if s.get("signal") == "BULLISCH"]
                if not bullish:
                    continue
                headline = bullish[0]["title"]
                console.print(f"  [cyan]📊 Options-Flow: {headline}[/cyan]")
                _add_req(ticker, meta={
                    "signal_type":   "OPTIONS_FLOW",
                    "score":         0.80,
                    "headline":      headline,
                    "from_headline": True,
                })
                _flow_hits.append((ticker, headline))
            except Exception:
                continue
        if _flow_hits:
            scanner_notify_fn(
                notifier,
                f"📊 <b>Options-Flow Signale</b> ({len(_flow_hits)} Titel)\n"
                f"━━━━━━━━━━━━━━\n"
                + "\n".join(f"• <b>{t}</b> – {h}" for t, h in _flow_hits)
                + "\n\n🔍 Analyse läuft – Ergebnis folgt in wenigen Minuten."
            )
    except Exception as e:
        log.debug("Options-Flow-Job fehlgeschlagen: %s", e)


def pead_scan_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                  weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                  telegram_notifier_cls, scanner_notify_fn: Callable,
                  safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Post-Earnings Drift: Aktien mit starkem Earnings-Beat (≥5%) werden
    24-48h nach dem Report analysiert — statistischer Drift-Vorteil.
    Läuft nur an Wochentagen.
    """
    try:
        local_now = datetime.now()
        if local_now.weekday() >= 5:
            return
        from analyzers.pead_tracker import PEADTracker
        from analyzers.user_request_queue import add_ticker as _req_ticker
        from bot.runner import _get_watchlist
        watchlist, _ = _get_watchlist(portfolio)
        tracker_pead = PEADTracker()
        tracker_pead.scan_watchlist(watchlist)  # Neue Beats registrieren
        ready = tracker_pead.get_ready_for_analysis()
        if not ready:
            return
        notifier = telegram_notifier_cls()
        queued_tickers = []
        for entry in ready:
            t = entry["ticker"]
            _req_ticker(t, meta={
                "signal_type":   "PEAD",
                "score":         min(0.90, 0.70 + entry.get("surprise_pct", 0.05) * 2),
                "headline":      f"PEAD: {entry.get('label','BEAT')} {entry.get('surprise_pct',0)*100:+.1f}% EPS-Surprise",
                "from_headline": False,
                "pead":          True,
            })
            tracker_pead.mark_queued(t)
            queued_tickers.append(t)
        tracker_pead.cleanup_expired()
        if queued_tickers:
            msg = "\n".join(f"  • <b>{t}</b>" for t in queued_tickers)
            scanner_notify_fn(
                notifier,
                f"📈 <b>PEAD-Scanner</b>\n\n{msg}\n\n"
                f"Earnings-Beat erkannt → Post-Drift-Analyse gestartet."
            )
            console.print(
                f"  [bold green]📈 PEAD: {', '.join(queued_tickers)} → Queue[/bold green]"
            )
            safe_run_analysis_cycle_fn(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
    except Exception as e:
        log.warning("PEAD-Scan-Job fehlgeschlagen: %s", e)


def short_squeeze_scan_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                           weekend_prep_inst, hedge_strategy_inst, earnings_strategy, config,
                           telegram_notifier_cls, scanner_notify_fn: Callable,
                           safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Scannt Watchlist auf Short-Squeeze-Setups: Short-Interest >15% + positiver Trend.
    Ticker mit Squeeze-Potenzial kommen in die Analyse-Queue.
    Läuft nur an Handelstagen.
    """
    try:
        local_now = datetime.now()
        if local_now.weekday() >= 5 or not (8 <= local_now.hour < 22):
            return
        from analyzers.short_squeeze_detector import ShortSqueezeDetector
        from analyzers.user_request_queue import add_ticker as _req_ticker
        detector = ShortSqueezeDetector()
        hits = detector.scan_watchlist(list(config.watchlist))
        if not hits:
            return
        notifier = telegram_notifier_cls()
        for h in hits:
            t = h["ticker"]
            signal_item = detector.build_signal_item(t, h)
            _req_ticker(t, meta={
                "signal_type":   "SHORT_SQUEEZE",
                "score":         min(0.88, 0.65 + h.get("squeeze_score", 0.2)),
                "headline":      signal_item["title"],
                "from_headline": False,
                "squeeze_setup": True,
            })
        tickers_str = ", ".join(h["ticker"] for h in hits)
        scanner_notify_fn(
            notifier,
            f"🎯 <b>Short-Squeeze-Scanner</b>\n\n"
            + "\n".join(
                f"  • <b>{h['ticker']}</b> – SI {h.get('si_pct',0):.1f}% | "
                f"DtC {h.get('days_to_cover',0):.1f}"
                for h in hits
            )
            + f"\n\n🔍 Analyse gestartet."
        )
        console.print(
            f"  [bold yellow]🎯 Short-Squeeze: {tickers_str} → Queue[/bold yellow]"
        )
        safe_run_analysis_cycle_fn(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
    except Exception as e:
        log.warning("Short-Squeeze-Scan-Job fehlgeschlagen: %s", e)


def insider_proactive_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                          weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                          insider_last_queued: Dict[str, datetime], telegram_notifier_cls,
                          scanner_notify_fn: Callable, safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Scannt alle Watchlist-Ticker auf frische Form-4-Insider-Käufe
    (letzte 3 Tage). STRONG_BUY → sofortige Analyse.
    Läuft einmal täglich morgens + einmal mittags.
    """
    import datetime as _dt
    try:
        local_now = datetime.now()
        if local_now.weekday() >= 5:
            return
        from analyzers.insider_signal import get_insider_score
        from analyzers.user_request_queue import add_ticker as _req_ticker
        from bot.runner import _get_watchlist

        watchlist, _ = _get_watchlist(portfolio)
        cutoff = local_now - _dt.timedelta(hours=_INSIDER_COOLDOWN_HOURS)
        hits = []
        for ticker in watchlist:
            if (
                insider_last_queued.get(ticker) is not None
                and insider_last_queued[ticker] >= cutoff
            ):
                continue
            try:
                score = get_insider_score(ticker, lookback_days=3)
                if score.signal == "STRONG_BUY":
                    hits.append((ticker, score))
                    insider_last_queued[ticker] = local_now
            except Exception:
                continue

        if not hits:
            return

        notifier = telegram_notifier_cls()
        for ticker, score in hits:
            _req_ticker(ticker, meta={
                "signal_type":   "INSIDER_BUY",
                "score":         0.82,
                "headline":      score.message if hasattr(score, "message") else f"Insider STRONG_BUY ({score.bullish_count} Käufe)",
                "from_headline": False,
                "insider_buy":   True,
            })

        msg = "\n".join(
            f"  • <b>{t}</b> – {s.bullish_count} Insider-Käufe (Score {s.score:.1f})"
            for t, s in hits
        )
        scanner_notify_fn(
            notifier,
            f"🏦 <b>Insider-Scanner</b> – Frische Form-4-Käufe\n\n{msg}\n\n"
            f"🔍 Sofort-Analyse gestartet."
        )
        log.info("Insider-Proaktiv: %d Kandidaten → %s", len(hits), [t for t, _ in hits])
        safe_run_analysis_cycle_fn(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
    except Exception as e:
        log.warning("Insider-Proaktiv-Job fehlgeschlagen: %s", e)


def sector_cascade_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                       weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                       cascade_last_queued: Dict[str, datetime], telegram_notifier_cls,
                       scanner_notify_fn: Callable, safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Wenn ein Sektor-ETF (XLK, XLF, etc.) stark bewegt (+/- ≥ 1.5%),
    werden die Top-Aktien desselben Sektors aus der Watchlist
    sofort zur Analyse vorgemerkt (Kaskaden-Effekt).
    Läuft nur an Handelstagen 09:00–20:00.
    """
    import datetime as _dt
    try:
        local_now = datetime.now()
        if local_now.weekday() >= 5 or not (9 <= local_now.hour < 20):
            return

        import yfinance as yf
        from analyzers.user_request_queue import add_ticker as _req_ticker
        from bot.runner import _get_watchlist

        _SECTOR_ETFS = {
            "XLK": "Technologie", "XLF": "Finanzen", "XLE": "Energie",
            "XLV": "Gesundheit",  "XLI": "Industrie", "XLY": "Konsum",
            "XLC": "Kommunikation",
        }
        _TICKER_SECTOR = {
            "AAPL":"XLK","MSFT":"XLK","NVDA":"XLK","AMD":"XLK","INTC":"XLK","QCOM":"XLK",
            "GOOGL":"XLC","META":"XLC","NFLX":"XLC","DIS":"XLC",
            "AMZN":"XLY","TSLA":"XLY","NKE":"XLY","MCD":"XLY","SBUX":"XLY",
            "JPM":"XLF","BAC":"XLF","GS":"XLF","MS":"XLF","V":"XLF","MA":"XLF","AXP":"XLF",
            "XOM":"XLE","CVX":"XLE","COP":"XLE",
            "JNJ":"XLV","PFE":"XLV","MRK":"XLV","ABBV":"XLV","LLY":"XLV","UNH":"XLV",
            "CAT":"XLI","BA":"XLI","GE":"XLI","RTX":"XLI","HON":"XLI",
        }

        _MIN_SECTOR_MOVE = float(os.getenv("CASCADE_MIN_SECTOR_MOVE", "1.5"))

        # Sektor-ETFs auf Tagesbewegung prüfen
        moving_sectors: list = []
        for etf, name in _SECTOR_ETFS.items():
            try:
                hist = yf.Ticker(etf).history(period="2d")
                if len(hist) < 2:
                    continue
                move = (float(hist["Close"].iloc[-1]) / float(hist["Close"].iloc[-2]) - 1) * 100
                if abs(move) >= _MIN_SECTOR_MOVE:
                    moving_sectors.append((etf, name, move))
            except Exception:
                continue

        if not moving_sectors:
            return

        watchlist = set(_get_watchlist(portfolio)[0])
        cutoff = local_now - _dt.timedelta(hours=_CASCADE_COOLDOWN_HOURS)
        notifier = telegram_notifier_cls()
        all_queued = []

        for etf, sector_name, move in moving_sectors:
            # Finde Watchlist-Ticker die zu diesem Sektor gehören
            siblings = [
                t for t, s in _TICKER_SECTOR.items()
                if s == etf and t in watchlist
                and (
                    cascade_last_queued.get(t) is None
                    or cascade_last_queued[t] < cutoff
                )
            ][:5]  # max 5 pro Sektor

            if not siblings:
                continue

            direction = "steigt" if move > 0 else "fällt"
            for t in siblings:
                _req_ticker(t, meta={
                    "signal_type":    "SECTOR_CASCADE",
                    "score":          0.65,
                    "headline":       f"{sector_name} {direction} {move:+.1f}% → Sektor-Kaskade",
                    "from_headline":  False,
                    "cascade_sector": etf,
                })
                cascade_last_queued[t] = local_now
                all_queued.append(t)

            icon = "📈" if move > 0 else "📉"
            scanner_notify_fn(
                notifier,
                f"{icon} <b>Sektor-Kaskade: {sector_name} {move:+.1f}%</b>\n\n"
                f"Verwandte Aktien analysiert: {', '.join(siblings)}"
            )

        if all_queued:
            log.info("Sektor-Kaskade: %d Ticker → Queue: %s", len(all_queued), all_queued)
            safe_run_analysis_cycle_fn(
                portfolio, broker, strategy, tracker, phase_ctrl,
                archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                earnings_strategy,
            )
    except Exception as e:
        log.warning("Sektor-Kaskaden-Job fehlgeschlagen: %s", e)


def intraday_scan_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                      weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                      safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Drittes Analysefenster (US-Session) – scannt Watchlist + BenchList
    auf Intraday-Setups. Läuft nur an Handelstagen.
    """
    local_date = datetime.now().date()
    if local_date.weekday() >= 5:
        return
    console.rule(
        f"[bold cyan]Intraday-Scan – {datetime.now().strftime('%H:%M')}[/bold cyan]"
    )
    try:
        safe_run_analysis_cycle_fn(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
    except Exception as e:
        log.warning("Intraday-Scan-Job fehlgeschlagen: %s", e)
