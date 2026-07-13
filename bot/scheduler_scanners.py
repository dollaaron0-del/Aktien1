"""
bot/scheduler_scanners.py – Signal-Scanner-Jobs, Teil 1: Eskalations-Helfer,
Headline-Scanner, Momentum-Scanner, Breakout-Watch-Scanner (je mit Cooldown-
Zustand).

Ausgelagert aus bot/scheduler.py::run_bot_loop (Roadmap 4.4a, vierte Naht).
Die Scanner-Gruppe ist die größte verbleibende (~11 Jobs) und wird in
mehreren Nähten aufgeteilt; dies ist die erste (escalate_ticker + die drei
Scanner mit eigenem Cooldown-Zustand). Cooldown-Dicts bleiben Objekte, die
run_bot_loop hält und bei jedem Aufruf durchreicht (kein Modul-Singleton) —
gleiches Muster wie sl_tp_check_job/morning_lagebericht_job in den
vorherigen Nähten.

scheduler.py behält für jeden Job einen gleichnamigen dünnen Wrapper (wie
bei den ersten drei Nähten): `schedule` übernimmt den Funktionsnamen per
functools.update_wrapper, test_scheduler_registration.py prüft ihn.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Callable, Dict

from logger import get_logger
from rich.console import Console

log = get_logger(__name__)
console = Console()

_SIGNAL_TRIGGER_SCORE = 0.90   # Ab hier sofortige Analyse auslösen
_HEADLINE_COOLDOWN_HOURS = int(os.getenv("HEADLINE_COOLDOWN_HOURS", "4"))
_HEADLINE_COOLDOWN_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "headline_cooldown.json"
)
_MOMENTUM_COOLDOWN_HOURS = int(os.getenv("MOMENTUM_COOLDOWN_HOURS", "8"))
_MOMENTUM_COOLDOWN_FILE  = os.path.join(
    os.path.dirname(__file__), "..", "data", "momentum_cooldown.json"
)
_BREAKOUT_COOLDOWN_HOURS = int(os.getenv("BREAKOUT_COOLDOWN_HOURS", "12"))


def escalate_ticker(tickers, portfolio, broker, strategy, tracker, phase_ctrl, archive,
                    reflection, weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                    safe_run_analysis_cycle_fn: Callable, reason: str = "Signal") -> None:
    _tk = list(dict.fromkeys(t for t in (tickers or []) if t))
    if not _tk:
        return
    log.info("Eskalation (%s): Fokus-Analyse %s", reason, ", ".join(_tk))
    console.print(
        f"  [bold yellow]⚡ {reason}: Fokus-Analyse {', '.join(_tk)}[/bold yellow]"
    )
    safe_run_analysis_cycle_fn(
        portfolio, broker, strategy, tracker, phase_ctrl,
        archive, reflection, weekend_prep_inst, hedge_strategy_inst,
        earnings_strategy, only_tickers=_tk,
    )


def load_headline_cooldown() -> dict:
    import json as _j
    try:
        with open(_HEADLINE_COOLDOWN_FILE) as _f:
            raw = _j.load(_f)
        return {k: datetime.fromisoformat(v) for k, v in raw.items()}
    except Exception:
        return {}


def save_headline_cooldown(cd: dict) -> None:
    import json as _j
    try:
        with open(_HEADLINE_COOLDOWN_FILE, "w") as _f:
            _j.dump({k: v.isoformat() for k, v in cd.items()}, _f)
    except Exception:
        pass


def headline_scan_job(headline_last_queued: Dict[str, datetime], telegram_notifier_cls,
                      scanner_notify_fn: Callable, escalate_ticker_fn: Callable) -> None:
    """
    Scannt allgemeine Börsennachrichten auf starke Signale (M&A, FDA,
    Earnings-Beats, etc.) und speist entdeckte Ticker in die BenchList.
    Sehr starke Signale (Score ≥ 0.85) → Telegram + sofortige Analyse (alle Ticker).
    Follow-Up nach der Analyse: Telegram mit Kauf-/Skip-Ergebnis.
    """
    try:
        from analyzers.headline_signal_detector import HeadlineSignalDetector
        detector = HeadlineSignalDetector()
        signals  = detector.scan()
        if signals:
            notifier = telegram_notifier_cls()
            # Urgent-Signale vorab bestimmen damit ihre Meldung
            # in einer einzigen kombinierten Nachricht landet
            import datetime as _dt_hl
            _hl_cutoff = datetime.now() - _dt_hl.timedelta(hours=_HEADLINE_COOLDOWN_HOURS)
            urgent = [
                sig for sig in signals
                if sig.score >= _SIGNAL_TRIGGER_SCORE
                and (
                    headline_last_queued.get(sig.ticker) is None
                    or headline_last_queued[sig.ticker] < _hl_cutoff
                )
            ]
            _urgent_tickers = {sig.ticker for sig in urgent}
            # Headline-Scanner-Meldung: urgent-Ticker ausschließen
            added = detector.process_signals(
                signals,
                notify_fn=lambda _m: scanner_notify_fn(notifier, _m),
                exclude_tickers=_urgent_tickers,
            )
            if added:
                console.print(
                    f"  [magenta]📰 Headline-Scanner: "
                    f"{len(added)} neue Kandidaten → BenchList: "
                    f"{', '.join(added[:6])}[/magenta]"
                )
            if urgent:
                for sig in urgent:
                    headline_last_queued[sig.ticker] = datetime.now()
                save_headline_cooldown(headline_last_queued)
                tickers_str = ", ".join(sig.ticker for sig in urgent)
                console.print(
                    f"  [bold yellow]⚡ Signal-Trigger ({_SIGNAL_TRIGGER_SCORE:.0%}): "
                    f"Fokus-Analyse: {tickers_str}[/bold yellow]"
                )
                _in_trading_hours = (
                    datetime.now().weekday() < 5
                    and 6 <= datetime.now().hour < 23
                )
                if _in_trading_hours:
                    # Nur die getriggerten Aktien analysieren (Fokus-Lauf),
                    # NICHT mehr den ganzen Watchlist-Zyklus. Frugal-Routing
                    # entscheidet Ollama-vs-Claude; nur handelbare Ergebnisse
                    # melden sich über den Trade-/Digest-Pfad.
                    escalate_ticker_fn(
                        [sig.ticker for sig in urgent], reason="Headline-Trigger"
                    )
                else:
                    # Außerhalb der Handelszeiten: für das nächste geplante
                    # Fenster vormerken statt nachts zu analysieren.
                    from analyzers.user_request_queue import add_ticker as _req_ticker_inline
                    for sig in urgent:
                        _req_ticker_inline(sig.ticker, meta={
                            "signal_type":  sig.signal_type,
                            "score":        sig.score,
                            "headline":     getattr(sig, "headline", ""),
                            "from_headline": True,
                        })
                    scanner_notify_fn(
                        notifier,
                        f"⚡ <b>Signal-Trigger</b> (außerhalb Handelszeiten)\n\n"
                        + "\n".join(
                            f"  • <b>{sig.ticker}</b> – {sig.signal_type} "
                            f"(Score {sig.score:.2f})"
                            for sig in urgent
                        )
                        + "\n\n📋 In Queue gespeichert – Analyse startet mit dem nächsten Vorbörslichen Fenster.",
                    )
                    log.info(
                        "Signal-Trigger außerhalb Handelszeiten – %d Ticker in Queue für Vorbörsliche Analyse.",
                        len(urgent),
                    )
    except Exception as e:
        log.warning("Headline-Scan-Job fehlgeschlagen: %s", e)


def load_momentum_cooldown() -> dict:
    import json as _j
    try:
        with open(_MOMENTUM_COOLDOWN_FILE) as _f:
            raw = _j.load(_f)
        return {k: datetime.fromisoformat(v) for k, v in raw.items()}
    except Exception:
        return {}


def save_momentum_cooldown(cd: dict) -> None:
    import json as _j
    try:
        with open(_MOMENTUM_COOLDOWN_FILE, "w") as _f:
            _j.dump({k: v.isoformat() for k, v in cd.items()}, _f)
    except Exception:
        pass


def momentum_scan_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                      weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                      momentum_last_queued: Dict[str, datetime], telegram_notifier_cls,
                      scanner_notify_fn: Callable, safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Scannt das Universum auf Aktien mit ungewöhnlichem Kaufdruck
    (Volumen ≥ 2× Schnitt UND Kurs ≥ +2%). Wer gerade gehyped wird,
    kommt sofort in die Analyse-Queue und löst eine Sofort-Analyse aus.
    Läuft nur an Handelstagen 08:00–22:00 Lokalzeit.
    Cooldown: Dieselbe Aktie wird frühestens nach MOMENTUM_COOLDOWN_HOURS (8h)
    erneut analysiert — effektiv einmal pro Handelstag.
    """
    import datetime as _dt
    try:
        local_now = datetime.now()
        if local_now.weekday() >= 5 or not (8 <= local_now.hour < 22):
            return
        from analyzers.watchlist_scanner import WatchlistScanner
        from analyzers.user_request_queue import add_ticker as _req_ticker
        scanner = WatchlistScanner(
            min_volume_ratio=2.0,
            min_price_change_pct=2.0,
            max_picks=5,
        )
        exclude = list(portfolio.all_positions().keys())
        hits = scanner.scan(exclude=exclude)
        if not hits:
            return

        # Cooldown-Filter: Ticker die in den letzten N Stunden bereits analysiert wurden
        cutoff = local_now - _dt.timedelta(hours=_MOMENTUM_COOLDOWN_HOURS)
        today_str = local_now.date().isoformat()

        # Zusätzlich: Ticker aus dem Analysis-Cache prüfen (heute bereits analysiert?)
        _analyzed_today: set = set()
        try:
            import json as _json
            _cache_path = os.path.join(os.path.dirname(__file__), "..", "data", "analysis_cache.json")
            with open(_cache_path) as _cf:
                _cache_data = _json.load(_cf)
            for _t, _d in _cache_data.items():
                if isinstance(_d, dict) and (_d.get("updated_at") or "").startswith(today_str):
                    _analyzed_today.add(_t.upper())
        except Exception:
            pass

        new_hits = [
            h for h in hits
            if h["ticker"].upper() not in _analyzed_today
            and (
                momentum_last_queued.get(h["ticker"]) is None
                or momentum_last_queued[h["ticker"]] < cutoff
            )
        ]
        if not new_hits:
            skipped = [h["ticker"] for h in hits]
            log.debug(
                "Momentum-Scanner: alle Kandidaten im Cooldown oder heute analysiert: %s",
                skipped,
            )
            return

        notifier = telegram_notifier_cls()
        for h in new_hits:
            _req_ticker(h["ticker"], meta={
                "signal_type":   "MOMENTUM",
                "score":         min(0.95, 0.70 + h["volume_ratio"] * 0.05),
                "headline":      f"Vol ×{h['volume_ratio']:.1f}, +{h['change_pct']:.1f}%",
                "from_headline": False,
                "momentum":      True,
            })
            momentum_last_queued[h["ticker"]] = local_now
        save_momentum_cooldown(momentum_last_queued)

        msg = "\n".join(
            f"  • <b>{h['ticker']}</b> +{h['change_pct']:.1f}% | "
            f"Vol ×{h['volume_ratio']:.1f} | {h['streak_days']}d↑"
            for h in new_hits
        )
        scanner_notify_fn(
            notifier,
            f"📈 <b>Momentum-Scanner</b>\n\n{msg}\n\n"
            f"🔍 Sofort-Analyse gestartet."
        )
        console.print(
            f"  [bold green]📈 Momentum-Scanner: "
            f"{len(new_hits)} Hype-Kandidaten → "
            f"{', '.join(h['ticker'] for h in new_hits)}[/bold green]"
        )
        safe_run_analysis_cycle_fn(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
    except Exception as e:
        log.warning("Momentum-Scan-Job fehlgeschlagen: %s", e)


def breakout_watch_job(portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
                       weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
                       breakout_last_queued: Dict[str, datetime], telegram_notifier_cls,
                       scanner_notify_fn: Callable, safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Prädiktiver Scanner: erkennt Breakout-Setups BEVOR der Kurs steigt.
    Signale: volume_buildup, bb_squeeze, resistance_obv.
    Läuft an Handelstagen 07:30–21:00 (breiter als Momentum, erfasst Pre-Market).
    Cooldown: 12h pro Ticker (einmal pro Tag reicht).
    """
    import datetime as _dt
    try:
        local_now = datetime.now()
        if local_now.weekday() >= 5 or not (7 <= local_now.hour < 21):
            return
        from analyzers.watchlist_scanner import WatchlistScanner
        from analyzers.user_request_queue import add_ticker as _req_ticker

        scanner = WatchlistScanner(max_picks=6)
        exclude = list(portfolio.all_positions().keys())
        hits = scanner.scan(exclude=exclude)
        if not hits:
            return

        cutoff = local_now - _dt.timedelta(hours=_BREAKOUT_COOLDOWN_HOURS)
        new_hits = [
            h for h in hits
            if (
                breakout_last_queued.get(h["ticker"]) is None
                or breakout_last_queued[h["ticker"]] < cutoff
            )
        ]
        if not new_hits:
            return

        _SIGNAL_LABELS = {
            "volume_buildup": "Vol↑ Akkumulation",
            "bb_squeeze":     "BB-Squeeze",
            "resistance_obv": "Widerstand+OBV",
        }

        notifier = telegram_notifier_cls()
        for h in new_hits:
            sig_label = " + ".join(_SIGNAL_LABELS.get(s, s) for s in h["signals"])
            _req_ticker(h["ticker"], meta={
                "signal_type":   "BREAKOUT_WATCH",
                "score":         0.60 + h["setup_score"] * 0.08,
                "headline":      sig_label,
                "from_headline": False,
                "momentum":      False,
                "breakout_watch": True,
            })
            breakout_last_queued[h["ticker"]] = local_now

        msg_lines = []
        for h in new_hits:
            sig_label = " + ".join(_SIGNAL_LABELS.get(s, s) for s in h["signals"])
            dist = f" | {h['dist_52w_pct']:.1f}% u. 52W-Hoch" if h.get("dist_52w_pct") is not None else ""
            msg_lines.append(f"  • <b>{h['ticker']}</b> ${h['price']:.2f} | {sig_label}{dist}")

        scanner_notify_fn(
            notifier,
            f"🎯 <b>Breakout-Watch</b> – Setup erkannt (kein Kursanstieg nötig)\n\n"
            + "\n".join(msg_lines)
            + "\n\n🔍 Analyse vorgemerkt."
        )
        log.info(
            "Breakout-Watch: %d Kandidaten → %s",
            len(new_hits), [h["ticker"] for h in new_hits],
        )
        safe_run_analysis_cycle_fn(
            portfolio, broker, strategy, tracker, phase_ctrl,
            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
            earnings_strategy,
        )
    except Exception as e:
        log.warning("Breakout-Watch-Job fehlgeschlagen: %s", e)
