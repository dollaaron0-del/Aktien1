"""
bot/scheduler_macro.py – Makro-/Kontext-Jobs: Geopolitik-Radar, Markt-Breadth,
Markt-Overview-Refresh, Morgen-Lagebericht, Nutzeranfragen, Wochenvorbereitung,
IPO-Check.

Ausgelagert aus bot/scheduler.py::run_bot_loop (Roadmap 4.4a, dritte Naht
nach scheduler_maintenance.py/scheduler_risk.py) — Jobs ohne Cross-Calls
zueinander (die sich gegenseitig aufrufende Analyse-Registrierungs-Gruppe
[_register_analysis_jobs/_pre_market_job/_reschedule_analysis/
_catchup_missed_window] und der State-schwere _run_regime_check bleiben
bewusst in scheduler.py, eigene Folge-Naht).

scheduler.py behält für jeden Job einen gleichnamigen dünnen Wrapper (wie
bei den ersten beiden Nähten): `schedule` übernimmt den Funktionsnamen per
functools.update_wrapper, test_scheduler_registration.py prüft ihn.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, List

from analyzers.recession_detector import SECTOR_ETFS as _SECTOR_ETFS
from analyzers.regime_adaptive import (
    set_market_caution, clear_market_caution, get_market_caution,
)
from logger import get_logger
from rich.console import Console

log = get_logger(__name__)
console = Console()

_BREADTH_CRASH_PCT  = 0.60   # ≥60% der Sektoren rot = ≥7 von 11 → Crash-Verdacht
_BREADTH_MIN_SAMPLE = 6      # mind. so viele ETFs müssen Daten liefern
_SPY_CONFIRM_PCT    = -1.0   # SPY muss ≥1% fallen für „echten" Breitmarkt-Einbruch


def geopolitical_radar_job(config, telegram_notifier_cls, scanner_notify_fn: Callable) -> None:
    """
    Scannt Weltpolitik-Feeds auf geopolitische Frühsignale und
    leitet Marktauswirkungen ab (Rüstung, Öl, Safe-Haven, etc.).
    Severity 2+ → sofortiger Telegram-Alert.
    Severity 3  → kritischer Alert + Sofort-Analyse für Watchlist-Ticker.
    """
    try:
        from analyzers.geopolitical_radar import GeopoliticalRadar
        radar  = GeopoliticalRadar()
        events = radar.scan()
        if events:
            notifier = telegram_notifier_cls()
            added = radar.process_events(
                events,
                notify_fn=lambda _m: scanner_notify_fn(notifier, _m),
            )
            if added:
                console.print(
                    f"  [bold red]🌍 Geo-Radar: {len(events)} Event(s) – "
                    f"Ticker → BenchList: {', '.join(added[:8])}[/bold red]"
                )
            # Severity-3 Ereignisse: Watchlist-Ticker sofort analysieren
            watchlist_set = {t.upper() for t in config.watchlist}
            geo_urgent: list = []
            for ev in events:
                if ev.severity == 3:
                    for impact in ev.impacts:
                        for t in impact.tickers:
                            if t.upper() in watchlist_set and t not in geo_urgent:
                                geo_urgent.append(t)
            if geo_urgent:
                from analyzers.user_request_queue import add_ticker as _req_ticker_inline
                for t in geo_urgent:
                    _req_ticker_inline(t)
                console.print(
                    f"  [bold red]🌍 Geo-Severity-3: Sofort-Analyse: "
                    f"{', '.join(geo_urgent)}[/bold red]"
                )
    except Exception as e:
        log.warning("Geopolitical-Radar-Job fehlgeschlagen: %s", e)


def market_breadth_job(telegram_notifier_cls) -> None:
    """Aktiviert Vorsichts-Modus bei breitem Sektor-Einbruch. Fallen ≥60% der
    Sektoren UND bestätigt SPY (≤ -1%) → 24h-Vorsicht (echter Einbruch).
    Sektoren breit rot, aber SPY flach → 12h-Beobachtung, kein harter Stopp."""
    try:
        if datetime.now(timezone.utc).replace(tzinfo=None).weekday() >= 5:
            return
        sectors = list(_SECTOR_ETFS)
        import yfinance as _yf
        dl_tickers = sectors + ["SPY"]
        hist = _yf.download(dl_tickers, period="2d", auto_adjust=True, progress=False, threads=False)
        closes = hist.get("Close") if hasattr(hist, "get") else hist["Close"]
        if closes is None or closes.shape[0] < 2:
            return
        prev_row  = closes.iloc[-2]
        curr_row  = closes.iloc[-1]

        def _chg(t):
            try:
                prev = float(prev_row[t]) if t in prev_row else None
                curr = float(curr_row[t]) if t in curr_row else None
                if prev and curr:
                    return (curr - prev) / prev * 100.0
            except Exception:
                pass
            return None

        spy_change = _chg("SPY")
        checked, down_sectors = 0, []
        for t in sectors:
            c = _chg(t)
            if c is None:
                continue
            checked += 1
            if c < 0:
                down_sectors.append(t)
        if checked < _BREADTH_MIN_SAMPLE:
            return

        pct_down = len(down_sectors) / checked

        if pct_down < _BREADTH_CRASH_PCT:
            # Markt hat sich erholt → aktiven Vorsichts-Modus aufheben
            if get_market_caution() and clear_market_caution():
                log.info("Marktbreite erholt (%d%% Sektoren rot) – Vorsichts-Modus aufgehoben.", int(pct_down*100))
            return

        spy_str = f"SPY {spy_change:+.1f}%" if spy_change is not None else "SPY n/v"
        spy_confirms = (spy_change is None) or (spy_change <= _SPY_CONFIRM_PCT)

        if spy_confirms:
            reason = (
                f"Breiter Markteinbruch: {int(pct_down*100)}% der Sektoren gefallen "
                f"({len(down_sectors)}/{checked}, {spy_str})"
            )
            newly = set_market_caution(reason, pct_down, hours=24)
            log.warning("Marktbreite-Vorsicht (Crash) aktiviert: %s", reason)
            if newly:
                telegram_notifier_cls().send(
                    f"⚠️ <b>Vorsichts-Modus aktiviert (24h)</b>\n\n"
                    f"{reason}\n\n"
                    f"Positionsgröße reduziert, Kaufhürde angehoben – "
                    f"nur noch hohe Konviktion. Kein harter Stopp."
                )
        else:
            # Sektoren breit rot, aber SPY flach → kein echter Einbruch, beobachten
            reason = (
                f"{int(pct_down*100)}% der Sektoren gefallen ({len(down_sectors)}/{checked}), "
                f"aber {spy_str} – kein Breitmarkt-Einbruch, nur Beobachtung."
            )
            newly = set_market_caution(reason, pct_down, hours=12)
            log.info("Marktbreite: Sektoren rot, SPY flach – beobachten statt Stopp: %s", reason)
            if newly:
                telegram_notifier_cls().send(
                    f"👀 <b>Beobachtungs-Modus (12h)</b>\n\n"
                    f"{reason}\n\n"
                    f"Leicht vorsichtiger (kleinere Positionen), aber kein Handelsstopp."
                )
    except Exception as _e:
        log.warning("Marktbreite-Job fehlgeschlagen: %s", _e)


def market_overview_refresh_job() -> None:
    """Aktualisiert den Market-Overview-Cache (SPY-Trend, Put/Call, VIX, etc.)."""
    try:
        from analyzers.market_overview import MarketOverview
        MarketOverview().full_assessment()
        log.debug("Market-Overview-Cache aktualisiert.")
    except Exception as _e:
        log.warning("Market-Overview-Refresh fehlgeschlagen: %s", _e)


def morning_lagebericht_job(lagebericht_sent_date: List[str], telegram_notifier_cls) -> None:
    """Sendet täglich um 08:30 UTC einen strukturierten Markt-Lagebericht via
    Telegram. lagebericht_sent_date ist ein 1-elementiger Mutable-Container
    ([0] = ISO-Datum des letzten Sendens) — vom Aufrufer gehalten, damit der
    Zustand über Aufrufe hinweg erhalten bleibt (kein Modul-Singleton nötig)."""
    _today = datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()
    if lagebericht_sent_date[0] == _today:
        log.debug("Morgen-Lagebericht heute bereits gesendet – übersprungen.")
        return
    try:
        from analyzers.market_overview import MarketOverview
        overview = MarketOverview()
        overview.full_assessment()  # Cache auffrischen
        msg = overview.format_telegram()
        telegram_notifier_cls().send(msg)
        lagebericht_sent_date[0] = _today
        log.info("Morgen-Lagebericht gesendet.")
    except Exception as _e:
        log.warning("Morgen-Lagebericht fehlgeschlagen: %s", _e)


def user_request_job(portfolio, broker, strategy, tracker, phase_ctrl, archive,
                     reflection, weekend_prep_inst, hedge_strategy_inst,
                     earnings_strategy, safe_run_analysis_cycle_fn: Callable) -> None:
    """Sofort-Analyse wenn Nutzer Ticker über das Dashboard angefordert hat.
    Außerhalb der Handelszeiten (06–23 Uhr, Wochentags) verbleiben Ticker in
    der Queue und werden bei der nächsten vorbörslichen Analyse verarbeitet.
    """
    try:
        _now = datetime.now()
        if _now.weekday() >= 5 or not (6 <= _now.hour < 23):
            return  # Queue wartet – nächste Vorbörsliche Analyse nimmt sie mit
        from analyzers import user_request_queue as _urq
        pending = _urq.peek()
        if not pending:
            return
        log.info(
            "Nutzeranfrage-Job: %d Ticker sofort analysieren: %s",
            len(pending), pending,
        )
        console.print(
            f"\n[bold cyan]📬 Nutzeranfrage – sofortige Analyse: {', '.join(pending)}[/bold cyan]"
        )
        safe_run_analysis_cycle_fn(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
    except Exception as _urq_err:
        log.error("Nutzeranfrage-Job fehlgeschlagen: %s", _urq_err)


def weekend_prep_job(weekend_prep_inst, telegram_notifier_cls,
                     run_weekend_prep_fn: Callable) -> None:
    """Runs weekend preparation. Called Saturday 09:00 and Sunday 14:00."""
    console.print(f"\n[bold cyan]📅 Wochenvorbereitung startet...[/bold cyan]")
    try:
        run_weekend_prep_fn(weekend_prep_inst)
    except Exception as _wp_err:
        log.warning("Wochenvorbereitung fehlgeschlagen: %s", _wp_err)
        try:
            telegram_notifier_cls().send(
                f"⚠️ <b>Wochenvorbereitung fehlgeschlagen</b>\n\n"
                f"Fehler: {str(_wp_err)[:200]}\n\n"
                f"Bot läuft weiter – Briefing wird beim nächsten Versuch (So 14:00) nachgeholt."
            )
        except Exception:
            pass

    # Weekly buy-blocked diagnostics report
    try:
        import json as _j
        import os as _os
        _bb_path = _os.path.join(_os.path.dirname(__file__), "..", "data", "buy_blocked.json")
        with open(_bb_path, encoding="utf-8") as _fh:
            _bb = _j.load(_fh)
        # Get last 2 weeks of data
        _weeks = sorted(_bb.keys())[-2:]
        if _weeks:
            _lines = []
            for _wk in _weeks:
                _counts = _bb[_wk]
                _sorted = sorted(_counts.items(), key=lambda x: x[1], reverse=True)
                _wk_lines = "\n".join(
                    f"  • {k}: {v}×" for k, v in _sorted[:8]
                )
                _lines.append(f"<b>{_wk}</b>\n{_wk_lines}")
            telegram_notifier_cls().send(
                f"🔒 <b>Wöchentlicher Kauf-Blockier-Bericht</b>\n\n"
                + "\n\n".join(_lines)
                + "\n\n<i>Tipps: 'sentiment_schwelle' → Schwelle senken; "
                f"'positionslimit' → mehr Slots; 'sektor_schwach' → normal.</i>"
            )
    except FileNotFoundError:
        pass  # No data yet – skip silently
    except Exception as _bb_err:
        log.debug("Buy-Blocked-Report fehlgeschlagen: %s", _bb_err)


def ipo_check_job(telegram_notifier_cls) -> None:
    try:
        from analyzers.ipo_tracker import IPOTracker
        tracker_ipo = IPOTracker()
        new_ipos = tracker_ipo.run_daily_check()
        for event in new_ipos:
            cand = event["candidate"]
            ticker = event["live_ticker"]
            notifier_ipo = telegram_notifier_cls()
            eligible_txt = (
                f"✅ Ticker <b>{ticker}</b> wurde zur Analyse-Queue hinzugefügt."
                if cand.auto_watchlist_eligible
                else f"⚠️ Bewertung unter $25 Mrd. → Ticker NICHT automatisch aufgenommen."
            )
            notifier_ipo.send(
                f"🚀 <b>IPO ERKANNT: {cand.name}</b>\n\n"
                f"Ticker: <b>{ticker}</b>\n"
                f"Sektor: {cand.sector}\n"
                f"Bewertung: ~${cand.expected_valuation_b:.0f} Mrd.\n"
                f"{cand.notes}\n\n"
                f"{eligible_txt}\n\n"
                f"<i>Erster Handelstag – noch wenig Daten vorhanden.</i>"
            )
            tracker_ipo.mark_notified(event["slug"])
            console.print(
                f"  [bold magenta]🚀 IPO erkannt: {cand.name} ({ticker})[/bold magenta]"
            )
    except Exception as e:
        log.warning("IPO-Check fehlgeschlagen: %s", e)
