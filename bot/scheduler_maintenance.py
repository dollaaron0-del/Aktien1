"""
bot/scheduler_maintenance.py – tägliche Wartungs-/Digest-Jobs.

Ausgelagert aus bot/scheduler.py::run_bot_loop (Roadmap 4.4a, 500-Zeilen-
Regel, erste Naht): die drei vollständig eigenständigen Tages-Jobs ohne
geteilten Mutable-Closure-State und ohne gegenseitige Aufrufe — dadurch
risikoärmster erster Schnitt aus den 37 Job-Closures. scheduler.py behält
für jeden Job einen gleichnamigen dünnen Wrapper (`def _daily_..._job():
scheduler_maintenance.daily_..._job(...)`), weil `schedule`'s Job-Objekte
den Funktionsnamen über `functools.update_wrapper` übernehmen und
tests/test_scheduler_registration.py genau diesen Namen prüft
(`by_time.get("02:00") == "_daily_maintenance_job"`) — eine direkte
Registrierung der hiesigen Funktionen würde diesen Vertrag brechen.

Funktionskörper sind wortwörtlich aus scheduler.py übernommen (nur
TelegramNotifier als Parameter statt Modul-Import, damit bestehende
monkeypatch.setattr(sched_mod, "TelegramNotifier", Fake)-Tests weiterhin
greifen).
"""
from __future__ import annotations

from datetime import datetime

from config import config
from logger import get_logger

log = get_logger(__name__)


def daily_maintenance_job(archive, reflection, signal_queue, telegram_notifier_cls) -> None:
    """
    Läuft täglich um 02:00 UTC. Bereinigt alle Datenbanken und verhindert
    unkontrolliertes Wachstum über Monate und Jahre hinweg.
    """
    import sqlite3 as _sqlite3
    report_lines = []

    # 1. News-Archiv: älter als 32 Tage löschen
    try:
        archive.cleanup_old(keep_days=32)
        report_lines.append("✅ News-Archiv: alte Artikel bereinigt (>32 Tage)")
    except Exception as e:
        report_lines.append(f"⚠️ News-Archiv Cleanup: {e}")

    # 2. Regime-Snapshots: älter als 90 Tage löschen
    try:
        from analyzers.recession_detector import RecessionDetector
        n = RecessionDetector().cleanup_old_snapshots(keep_days=90)
        if n:
            report_lines.append(f"✅ Regime-Snapshots: {n} alte Einträge gelöscht")
    except Exception as e:
        report_lines.append(f"⚠️ Regime-Snapshot Cleanup: {e}")

    # 4. Reflection-Engine: älteste Memos/Reviews löschen
    try:
        n = reflection.cleanup_old(keep_memos=30, keep_monthly=24)
        if n:
            report_lines.append(f"✅ Reflections: {n} alte Einträge gelöscht")
    except Exception as e:
        report_lines.append(f"⚠️ Reflection Cleanup: {e}")

    # 5. Signal-Queue: abgelaufene Signale bereinigen (nutzt bestehende Logik)
    try:
        expired = signal_queue.cleanup_expired()
        if expired:
            report_lines.append(f"✅ Signal-Queue: {expired} abgelaufene Signale entfernt")
    except Exception as e:
        report_lines.append(f"⚠️ Signal-Queue Cleanup: {e}")

    # 6. VACUUM auf allen SQLite-Datenbanken (gibt gelöschte Seiten frei)
    db_paths = [
        "data/news_archive.db",
        "data/trade_journal.db",
        "data/performance.db",
        "data/reflections.db",
        "data/signal_queue.db",
        "data/portfolio.db",
    ]
    vacuumed = 0
    for db_path in db_paths:
        try:
            conn = _sqlite3.connect(db_path)
            conn.execute("VACUUM")
            conn.close()
            vacuumed += 1
        except Exception:
            pass
    if vacuumed:
        report_lines.append(f"✅ VACUUM: {vacuumed} Datenbanken komprimiert")

    summary = "\n".join(report_lines)
    log.info("Tägliche Wartung abgeschlossen:\n%s", summary)

    if any("⚠️" in l for l in report_lines):
        telegram_notifier_cls().send(
            f"🔧 <b>Tägliche DB-Wartung</b>\n\n{summary}"
        )


def daily_summary_job(broker, portfolio, phase_ctrl, telegram_notifier_cls) -> None:
    """Sendet einmal täglich (abends, Werktag) die Tages-Zusammenfassung:
    Portfolio-Stand + gebündelte Aktionen des Tages."""
    if datetime.now().date().weekday() >= 5:
        return
    try:
        from bot.runner import pop_daily_actions
        prices = broker.get_prices(list(portfolio.all_positions().keys()))
        total_value = portfolio.total_value(prices)
        phase = phase_ctrl.current_phase(total_value)
        telegram_notifier_cls().notify_daily_summary(
            total_value=total_value,
            cash=portfolio.cash,
            open_positions=len(portfolio.all_positions()),
            phase=phase,
            progress_pct=phase_ctrl.progress_pct(total_value),
            actions_today=pop_daily_actions(),
        )
    except Exception as _ds_err:
        log.warning("Tages-Zusammenfassung (Abend) fehlgeschlagen: %s", _ds_err)


def daily_dashboard_job(dashboard, portfolio, tracker, broker, bot_scorer_cls,
                        telegram_notifier_cls) -> None:
    if not dashboard.should_send():
        return
    if not dashboard.try_claim_send():
        log.debug("Daily Dashboard: anderer Prozess hat bereits gesendet – übersprungen.")
        return
    try:
        msg = dashboard.generate(
            portfolio=portfolio,
            tracker=tracker,
            scorer=bot_scorer_cls(),
            broker=broker,
            initial_capital=config.initial_capital,
        )
        telegram_notifier_cls().send(msg, level="digest")
        dashboard.mark_sent()
        log.info("Tägliches Dashboard gesendet.")
    except Exception as e:
        log.warning("Daily Dashboard fehlgeschlagen: %s", e)
