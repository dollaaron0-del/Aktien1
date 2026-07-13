"""
bot/scheduler_analysis.py – Analyse-Registrierungs-Gruppe: Pre-Market-Job,
Analyse-Job-Registrierung (+ täglicher Reschedule für DST), Monats-Review,
Catch-up bei verpasstem Fenster, Tages-Watchdog.

Ausgelagert aus bot/scheduler.py::run_bot_loop (Roadmap 4.4a, fünfte und
letzte Naht). Diese Gruppe ist anders als alle vorherigen: die Funktionen
rufen sich GEGENSEITIG auf (reschedule_analysis → register_analysis_jobs →
registriert pre_market_job_fn/monthly_review_check_fn per schedule.every()
...do(...); catchup_missed_window → ruft pre_market_job_fn direkt). Die
Job-Funktionen werden daher als PARAMETER hereingereicht (nicht hier
importiert) — scheduler.py übergibt seine eigenen, gleichnamigen Wrapper-
Closures, wodurch `schedule`'s Namens-Introspektion (functools.update_
wrapper) weiterhin "_pre_market_job"/"_monthly_review_check" liefert und
der in test_scheduler_registration.py verankerte Reihenfolge-Vertrag
(Pre-Market MUSS vor der vollen Analyse registriert sein) automatisch
erhalten bleibt, weil der Funktionskörper wortwörtlich übernommen ist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import schedule
from rich.console import Console
from rich.panel import Panel

from logger import get_logger

log = get_logger(__name__)
console = Console()

_CATCHUP_MAX_MINUTES = 180


def monthly_review_check(reflection) -> None:
    if datetime.now(timezone.utc).replace(tzinfo=None).day == 1:
        console.print("[bold magenta]📋 Erstelle monatliche Selbsteinschätzung...[/bold magenta]")
        content = reflection.generate_monthly_review()
        if content:
            console.print(Panel(content[:800] + "...", title="Monatsreview erstellt", border_style="magenta"))


def pre_market_job(exchange: str, portfolio, telegram_notifier_cls, pre_market_scanner_cls) -> None:
    """Pre-Market Briefing: schneller Daten-Scan ohne Claude."""
    console.rule(f"[bold yellow]Pre-Market Briefing – {exchange}[/bold yellow]")
    try:
        from bot.runner import _get_watchlist
        watchlist, _ = _get_watchlist(portfolio)
        scanner = pre_market_scanner_cls()
        briefing = scanner.run(exchange=exchange, watchlist=watchlist)
        if briefing:
            for line in briefing.to_console_lines():
                console.print(line)
            telegram_notifier_cls().send(briefing.to_telegram())
    except Exception as e:
        log.warning("Pre-Market-Job %s fehlgeschlagen: %s", exchange, e)


def register_analysis_jobs(mkt_schedule, portfolio, broker, strategy, tracker, phase_ctrl,
                           archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                           earnings_strategy, safe_run_analysis_cycle_fn: Callable,
                           pre_market_job_fn: Callable, monthly_review_check_fn: Callable) -> None:
    # Use LOCAL date (not UTC) so midnight reschedule doesn't get "yesterday"
    local_date = datetime.now().date()
    slots = mkt_schedule.get_schedule_strings(date=local_date)
    is_weekend = local_date.weekday() >= 5
    if not slots or is_weekend:
        if is_weekend:
            console.print("[dim]Wochenende – keine Vollanalysen geplant (nur Wochenvorbereitung).[/dim]")
        else:
            console.print("[dim]Heute kein Handelstag.[/dim]")
        return
    for slot in slots:
        # Pre-Market Briefing 30 Min vor Open (gleiche Zeit wie Vollanalyse).
        # MUSS ZUERST registriert werden: schedule arbeitet gleichzeitig fällige
        # Jobs in Registrierungsreihenfolge sequenziell im selben Thread ab.
        # Wird die Vollanalyse vorher registriert, blockiert sie (Claude, ~15-40 min)
        # das schnelle Briefing-Telegram bis nach Analyse-Ende.
        pre_hhmm = slot["hhmm"]
        exch = slot["exchange"]
        pre_job = schedule.every().day.at(pre_hhmm).do(pre_market_job_fn, exch)
        pre_job._is_analysis_job = True

        # Volle Analyse 30 Min vor Open – läuft direkt nach dem Briefing
        job = schedule.every().day.at(slot["hhmm"]).do(
            safe_run_analysis_cycle_fn,
            portfolio, broker, strategy, tracker, phase_ctrl, archive, reflection,
            weekend_prep_inst, hedge_strategy_inst, earnings_strategy,
            announce_start=True,
        )
        job._is_analysis_job = True
        review_job = schedule.every().day.at(slot["hhmm"]).do(monthly_review_check_fn)
        review_job._is_analysis_job = True

    times_str = ", ".join(f"{s['hhmm']} ({s['exchange']})" for s in slots)
    console.print(f"[dim]Analyse-Jobs registriert: {times_str}[/dim]")
    pre_times = ", ".join(
        f"{s['hhmm']} pre-market ({s['exchange']})" for s in slots
    )
    console.print(f"[dim]Pre-Market-Jobs: {pre_times}[/dim]")


def reschedule_analysis(register_analysis_jobs_fn: Callable) -> None:
    """Rebuilds analysis schedule for the new day (handles DST changes)."""
    # Snapshot existing jobs BEFORE cancelling — restore on error
    cancelled = [
        job for job in list(schedule.jobs)
        if getattr(job, "_is_analysis_job", False)
    ]
    for job in cancelled:
        schedule.cancel_job(job)
    try:
        register_analysis_jobs_fn()
    except Exception as _rsa_err:
        log.error("_register_analysis_jobs fehlgeschlagen – stelle Jobs wieder her: %s", _rsa_err)
        # Entferne halb-erstellte Jobs bevor alte wiederhergestellt werden
        for _partial in [j for j in list(schedule.jobs) if getattr(j, "_is_analysis_job", False)]:
            schedule.cancel_job(_partial)
        for job in cancelled:
            schedule.jobs.append(job)


def catchup_missed_window(mkt_schedule, portfolio, broker, strategy, tracker, phase_ctrl,
                          archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                          earnings_strategy, telegram_notifier_cls,
                          pre_market_job_fn: Callable,
                          safe_run_analysis_cycle_fn: Callable) -> None:
    now_local = datetime.now()
    local_date = now_local.date()
    if local_date.weekday() >= 5:
        return
    slots = mkt_schedule.get_schedule_strings(date=local_date)

    # Analyse-Log einmal laden um doppelte Nachholungen zu verhindern
    _today_str = local_date.isoformat()
    _already_ran = False
    try:
        from analyzers.analysis_log import AnalysisLog as _AL
        _recent = _AL().get_recent(limit=1)
        if _recent and (_recent[0].get("analyzed_at") or "").startswith(_today_str):
            _already_ran = True
    except Exception:
        pass

    if _already_ran:
        return  # Analyse hat heute schon stattgefunden – kein Nachholen nötig

    for slot in slots:
        try:
            h, m = map(int, slot["hhmm"].split(":"))
            slot_dt = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
            diff = (now_local - slot_dt).total_seconds() / 60
            if 0 < diff <= _CATCHUP_MAX_MINUTES:
                console.print(
                    f"\n[bold yellow]⏰ Analyse-Fenster {slot['hhmm']} ({slot['exchange']}) "
                    f"um {diff:.0f} Min verpasst – hole jetzt nach...[/bold yellow]"
                )
                telegram_notifier_cls().send(
                    f"⏰ <b>Nachhol-Analyse</b>\n\n"
                    f"Bot wurde nach dem geplanten Fenster gestartet "
                    f"({slot['hhmm']} {slot['exchange']} vor {diff:.0f} Min).\n"
                    f"Starte Pre-Market Briefing und Analyse jetzt..."
                )
                pre_market_job_fn(slot["exchange"])
                safe_run_analysis_cycle_fn(
                    portfolio, broker, strategy, tracker, phase_ctrl,
                    archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                    earnings_strategy,
                )
                break
        except Exception as e:
            log.warning("Catch-up-Check fehlgeschlagen: %s", e)


def daily_analysis_watchdog(mkt_schedule, portfolio, broker, strategy, tracker, phase_ctrl,
                            archive, reflection, weekend_prep_inst, hedge_strategy_inst,
                            earnings_strategy, watchdog_last_triggered: dict,
                            telegram_notifier_cls, safe_run_analysis_cycle_fn: Callable) -> None:
    """
    Prüft stündlich ob heute bereits eine Analyse gelaufen ist.
    Fehlt sie (nach dem geplanten Zeitfenster), wird eine Nachhol-Analyse gestartet.
    Verhindert stille Ausfälle durch Reschedule-Fehler oder Bot-Neustart.
    Skippt nur wenn die letzte Watchdog-Analyse weniger als 3 Stunden her ist,
    damit nach einem Neustart (z.B. nach Deploy) eine Folgeanalyse möglich ist.
    """
    now = datetime.now()
    today = now.date()
    if today.weekday() >= 5:
        return
    today_str = today.isoformat()

    # Nur skippen wenn dieser Watchdog heute schon eine Analyse ausgelöst hat
    # UND das weniger als 3 Stunden her ist
    _last = watchdog_last_triggered.get(today_str)
    if _last is not None:
        _age_h = (now - _last).total_seconds() / 3600
        if _age_h < 3.0:
            return

    slots = mkt_schedule.get_schedule_strings(date=today)
    if not slots:
        return

    # Prüfe ob wir den ersten Slot um mehr als 30 Min überschritten haben
    h, m = map(int, slots[0]["hhmm"].split(":"))
    slot_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < slot_dt + __import__("datetime").timedelta(minutes=30):
        return  # noch zu früh

    # Obergrenze: nach dem letzten Slot + 90 Min ist der Handelstag vorbei.
    # Keine Nachhol-Analyse am Abend/in der Nacht (verhindert Telegram-Spam).
    _last_h, _last_m = 0, 0
    for _s in slots:
        _hh, _mm = map(int, _s["hhmm"].split(":"))
        if (_hh, _mm) > (_last_h, _last_m):
            _last_h, _last_m = _hh, _mm
    last_slot_dt = now.replace(hour=_last_h, minute=_last_m, second=0, microsecond=0)
    if now > last_slot_dt + __import__("datetime").timedelta(minutes=90):
        return  # Handelstag vorbei – nachts nicht nachholen

    # Prüfe ob IRGENDEIN heutiger Slot in den letzten 45 Min war → Analyse läuft noch
    _td = __import__("datetime").timedelta
    for _slot in slots:
        _sh, _sm = map(int, _slot["hhmm"].split(":"))
        _slot_dt = now.replace(hour=_sh, minute=_sm, second=0, microsecond=0)
        if _td(0) <= now - _slot_dt <= _td(minutes=45):
            return  # Analyse läuft wahrscheinlich noch

    # Jüngsten "fälligen" Slot bestimmen: dessen Zeit liegt >45 Min zurück.
    # Eine feste 3h-Freshness war falsch – zwischen zwei Börsen-Slots (z.B.
    # XETRA 07:30 → NYSE 15:00) liegen legitime ~7,5h, in denen der Watchdog
    # sonst grundlos feuert (+ überflüssige Nachhol-Analyse + Tages-Summary).
    _due_slot_dt = None
    for _slot in slots:
        _sh, _sm = map(int, _slot["hhmm"].split(":"))
        _sd = now.replace(hour=_sh, minute=_sm, second=0, microsecond=0)
        if now - _sd > _td(minutes=45) and (_due_slot_dt is None or _sd > _due_slot_dt):
            _due_slot_dt = _sd
    if _due_slot_dt is None:
        return  # heute noch kein Slot fällig

    # Lief seit (fälligem Slot − 30 Min) eine Analyse? Dann wurde der Slot
    # bedient → kein Ausfall, nicht feuern. Nur ein echter Slot-Miss alarmiert.
    try:
        from analyzers.analysis_log import AnalysisLog as _AL
        recent = _AL().get_recent(limit=1)
        if recent:
            _last_ts = recent[0].get("analyzed_at") or ""
            try:
                from datetime import datetime as _dt
                # analysis_log speichert analyzed_at als naive UTC (utcnow()).
                # Der Watchdog rechnet in lokaler Zeit (now/_due_slot_dt). Ohne
                # Umrechnung erscheint eine 07:30-Analyse als 05:30 (UTC+2) und
                # der Watchdog hält den planmäßig bedienten Slot fälschlich für
                # verpasst → unnötige Nachhol-Analyse + Telegram-Spam.
                _utc_offset = datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)
                _last_dt = _dt.fromisoformat(_last_ts) + _utc_offset
                if _last_dt >= _due_slot_dt - _td(minutes=30):
                    watchdog_last_triggered[today_str] = _last_dt
                    return
            except Exception:
                pass
    except Exception:
        pass

    log.warning(
        "Tages-Watchdog: Geplanter Slot %s ohne Analyse – starte Nachhol-Analyse.",
        _due_slot_dt.strftime("%H:%M"),
    )
    console.print(
        f"\n[bold yellow]🔔 Tages-Watchdog: Slot {_due_slot_dt.strftime('%H:%M')} verpasst – hole nach...[/bold yellow]"
    )
    telegram_notifier_cls().send(
        "⏰ <b>Tages-Watchdog</b>\n\n"
        f"Geplante Analyse um {_due_slot_dt.strftime('%H:%M')} fehlt – starte Nachhol-Analyse jetzt."
    )
    safe_run_analysis_cycle_fn(
        portfolio, broker, strategy, tracker, phase_ctrl,
        archive, reflection, weekend_prep_inst, hedge_strategy_inst,
        earnings_strategy,
    )
    # Zeitstempel merken – nächster Watchdog-Lauf in 1h prüft ob Analyse frisch genug
    watchdog_last_triggered[today_str] = datetime.now()
    try:
        from analyzers.analysis_log import AnalysisLog as _AL
        after = _AL().get_recent(limit=1)
        if not (after and (after[0].get("analyzed_at") or "").startswith(today_str)):
            log.warning("Tages-Watchdog: Analyse lief, aber kein Log-Eintrag – erneuter Versuch in 1h")
            del watchdog_last_triggered[today_str]  # Retry erlauben
    except Exception:
        pass  # Im Zweifel nächste Stunde wieder prüfen
