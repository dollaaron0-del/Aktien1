"""
Charakterisierungs-Test für bot.scheduler.run_bot_loop — Roadmap 4.4a
Vorbereitung (Sicherheitsnetz VOR dem Monolith-Split).

run_bot_loop() ist eine ~2160-Zeilen-Funktion, die beim Aufruf sofort alle
~35 Jobs beim schedule-Modul registriert (Zeitpunkt/Intervall) und danach in
eine Endlosschleife läuft. Ein Kommentar im Code macht eine echte
Reihenfolge-Abhängigkeit explizit: das Pre-Market-Briefing MUSS vor der
vollen Analyse registriert werden, weil schedule gleichzeitig fällige Jobs
in Registrierungsreihenfolge abarbeitet — genau die Art Detail, die ein
mechanisches Verschieben beim Split kaputtmachen könnte, ohne dass es
auffällt.

Dieser Test ruft run_bot_loop() mit durchweg gemockten Abhängigkeiten auf,
bricht die Endlosschleife nach der Registrierungsphase ab (time.sleep wird
zum Abbruch-Signal) und prüft NUR die Schedule-STRUKTUR: welche Jobs zu
welcher Zeit/welchem Intervall registriert wurden, in welcher Reihenfolge.
Keine Aussage über die Job-KÖRPER selbst (die laufen hier nie) — das ist
bewusst eine Charakterisierung des Registrierungs-Verhaltens, kein
Vollständigkeits-Test der Analyse-Logik.
"""
import types
from unittest.mock import MagicMock

import pytest
import schedule

import bot.scheduler as sched_mod


class _StopLoop(Exception):
    """Sentinel, um run_bot_loop()'s while-True-Schleife nach der
    Registrierungsphase kontrolliert zu verlassen."""


@pytest.fixture(autouse=True)
def _clear_schedule():
    schedule.clear()
    yield
    schedule.clear()


def _make_args(portfolio, monkeypatch, tmp_path, *, ibkr=False, use_margin=False,
               goal_active=False, hedge=False, intraday=False, weekend_slot=None):
    """Baut ein durchweg gemocktes Parameter-Set für run_bot_loop()."""
    broker = MagicMock()
    broker.get_prices.return_value = {}
    broker.get_filled_limit_orders.return_value = []

    strategy = MagicMock()
    strategy.journal = None

    tracker = MagicMock()

    phase_ctrl = MagicMock()
    phase_ctrl.get_info.return_value = {
        "phase": "GROWTH", "progress_pct": 0.0,
        "growth_target": 30_000.0, "remaining_to_goal": 20_000.0,
    }

    focus_ctrl = MagicMock()
    focus_ctrl.profile.label = "Test-Profil"

    archive = MagicMock()

    reflection = MagicMock()

    signal_queue = MagicMock()
    signal_queue.count_pending.return_value = 0

    weekend_prep_inst = MagicMock()
    weekend_prep_inst.get_current_briefing.return_value = "existing"  # Startup-Thread nicht nötig

    goal_risk = MagicMock()
    goal_risk.active = goal_active

    hedge_strategy_inst = MagicMock() if hedge else None

    mkt_schedule = MagicMock()
    mkt_schedule.get_schedule_strings.return_value = (
        [{"hhmm": "09:00", "exchange": "NASDAQ"}] if weekend_slot is None else weekend_slot
    )
    mkt_schedule.next_window.return_value = {"analysis_local": "09:00"}
    mkt_schedule.describe.return_value = "Testplan"

    # config ist ein Modul-weites Singleton (from config import config) —
    # gezielt einzelne Attribute für den Testfall überschreiben.
    monkeypatch.setattr(sched_mod.config, "broker_mode", "ibkr" if ibkr else "paper")
    monkeypatch.setattr(sched_mod.config, "use_margin", use_margin)
    monkeypatch.setattr(sched_mod.config, "regime_check_interval_hours", 4)
    monkeypatch.setattr(sched_mod.config, "intraday_scan_enabled", intraday)
    monkeypatch.setattr(sched_mod.config, "intraday_scan_time", "17:30")
    monkeypatch.setattr(sched_mod.config, "watchlist", ["AAPL"])
    monkeypatch.setattr(sched_mod.config, "market_lead_minutes", 30)

    # AnalysisLog/analysis_log.db etc. real, aber in ein Temp-Verzeichnis
    # umgelenkt — verhindert Seiteneffekte auf echte data/-Dateien.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)

    return dict(
        args=types.SimpleNamespace(), portfolio=portfolio, broker=broker,
        strategy=strategy, tracker=tracker, phase_ctrl=phase_ctrl,
        focus_ctrl=focus_ctrl, archive=archive, reflection=reflection,
        signal_queue=signal_queue, weekend_prep_inst=weekend_prep_inst,
        goal_risk=goal_risk, hedge_strategy_inst=hedge_strategy_inst,
        mkt_schedule=mkt_schedule, earnings_strategy=None,
    )


def _run_registration_only(monkeypatch, kwargs):
    """Führt run_bot_loop() bis zum Ende der Registrierungsphase aus und
    bricht die anschließende Endlosschleife kontrolliert ab.

    Neutralisiert außerdem die echten, teuren Arbeits-Funktionen
    (safe_run_analysis_cycle & co.), die run_bot_loop bei bestimmten
    Uhrzeiten SOFORT beim Setup auslösen kann (Catch-up/Watchdog-Checks
    laufen synchron VOR der Schleife) — ohne das würde der Test echte
    Analyse-/Netzwerk-Arbeit anstoßen und je nach Tageszeit hängen bleiben."""
    monkeypatch.setattr(sched_mod, "TelegramNotifier",
                        lambda *a, **kw: MagicMock())
    # __name__ explizit gesetzt: schedule.every(...).do(safe_run_analysis_cycle)
    # übernimmt sonst KEINEN Namen von einer nackten MagicMock, und Tests
    # könnten den registrierten Job nicht mehr über job.job_func.__name__
    # identifizieren.
    monkeypatch.setattr(sched_mod, "safe_run_analysis_cycle",
                        MagicMock(__name__="safe_run_analysis_cycle"))
    monkeypatch.setattr(sched_mod, "run_analysis_cycle",
                        MagicMock(__name__="run_analysis_cycle"))
    monkeypatch.setattr(sched_mod, "_print_portfolio_summary", MagicMock())
    monkeypatch.setattr(sched_mod, "run_weekend_prep", MagicMock())
    # _catchup_missed_window() kann _pre_market_job() SYNCHRON auslösen (wenn
    # das gemockte Zeitfenster laut aktueller Uhrzeit "verpasst" wirkt) — das
    # ruft echten bot.runner._get_watchlist() -> _opportunity_scan() ->
    # echte yfinance-Netzwerk-Scans auf (EU-Universum!). Das ist KEIN
    # run_bot_loop-eigener Code, sondern Fremdaufruf — an der Quelle kappen.
    import bot.runner as runner_mod
    monkeypatch.setattr(runner_mod, "_get_watchlist",
                        lambda portfolio: (["AAPL"], "static"))
    monkeypatch.setattr(sched_mod, "PreMarketScanner", MagicMock())
    # Der reale Pause-Zustand dieses Servers (Bot ist bewusst pausiert,
    # CLAUDE.md) darf das Testverhalten nicht beeinflussen — sonst hängt
    # das Ergebnis vom Tag ab, an dem der Test läuft.
    import system.bot_control as bot_control_mod
    monkeypatch.setattr(bot_control_mod, "is_paused", lambda: False)
    monkeypatch.setattr(sched_mod.time, "sleep",
                        MagicMock(side_effect=_StopLoop))
    # NUR .start() stumm schalten (kein echter OS-Thread/Timer läuft an) —
    # die Klassen selbst NICHT ersetzen: run_bot_loop nutzt sowohl
    # threading.Thread (Weekend-Prep/IPO-Startup) als auch threading.Timer
    # (gestaffelter Scanner-Start, Timer erbt von Thread). Ein MagicMock
    # anstelle der Klasse bricht Timer.__init__'s eigene Thread.__init__-
    # Kette (führt zu "Thread.__init__() not called").
    import threading as _threading_mod
    monkeypatch.setattr(_threading_mod.Thread, "start", lambda self: None)
    with pytest.raises(_StopLoop):
        sched_mod.run_bot_loop(**kwargs)


def _names(jobs):
    return [getattr(j.job_func, "__name__", str(j.job_func)) for j in jobs]


# ── Grundstruktur ─────────────────────────────────────────────────────────────

def test_run_bot_loop_registers_expected_fixed_time_jobs(monkeypatch, fresh_portfolio, tmp_path):
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path)
    _run_registration_only(monkeypatch, kwargs)

    by_time = {j.at_time.strftime("%H:%M"): getattr(j.job_func, "__name__", "")
              for j in schedule.jobs if j.at_time is not None}
    # Kern-Fixpunkte, die es laut Code an genau dieser Uhrzeit geben MUSS.
    assert by_time.get("00:01") == "_reschedule_analysis"
    assert by_time.get("02:00") == "_daily_maintenance_job"
    assert by_time.get("06:00") == "_ipo_check_job"
    assert by_time.get("20:30") == "_daily_dashboard_job"
    assert by_time.get("22:15") == "_daily_summary_job"       # Default DAILY_SUMMARY_AT


def test_run_bot_loop_registers_expected_interval_jobs(monkeypatch, fresh_portfolio, tmp_path):
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path)
    _run_registration_only(monkeypatch, kwargs)

    by_name = {getattr(j.job_func, "__name__", ""): j for j in schedule.jobs}
    assert by_name["_sl_tp_check_job"].interval == 30 and by_name["_sl_tp_check_job"].unit == "minutes"
    assert by_name["_position_aging_job"].interval == 4 and by_name["_position_aging_job"].unit == "hours"
    assert by_name["_headline_scan_job"].interval == 20 and by_name["_headline_scan_job"].unit == "minutes"
    assert by_name["_cb_monitor_job"].interval == 15 and by_name["_cb_monitor_job"].unit == "minutes"
    assert by_name["_conditional_entry_job"].interval == 15 and by_name["_conditional_entry_job"].unit == "minutes"
    assert by_name["_user_request_job"].interval == 15 and by_name["_user_request_job"].unit == "minutes"


def test_run_bot_loop_pre_market_registered_before_full_analysis(monkeypatch, fresh_portfolio, tmp_path):
    """Der explizit im Code kommentierte Reihenfolge-Vertrag: das Pre-Market-
    Briefing MUSS vor der vollen Analyse registriert werden, weil schedule
    gleichzeitig fällige Jobs in Registrierungsreihenfolge abarbeitet."""
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path)
    _run_registration_only(monkeypatch, kwargs)

    names = _names(schedule.jobs)
    pre_idx = names.index("_pre_market_job")
    analysis_idx = next(i for i, n in enumerate(names) if n == "safe_run_analysis_cycle")
    assert pre_idx < analysis_idx, (
        "Pre-Market-Job muss VOR der vollen Analyse registriert sein "
        "(schedule führt gleichzeitig fällige Jobs in Registrierungs-"
        "reihenfolge aus)."
    )


def test_run_bot_loop_no_trading_day_skips_per_slot_jobs(monkeypatch, fresh_portfolio, tmp_path):
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path, weekend_slot=[])
    _run_registration_only(monkeypatch, kwargs)

    names = _names(schedule.jobs)
    assert "_pre_market_job" not in names
    assert "safe_run_analysis_cycle" not in names
    # Die tagesfixen Jobs (Wartung, IPO, …) müssen trotzdem registriert sein.
    assert "_daily_maintenance_job" in names


# ── Bedingte Registrierung (Feature-Flags) ───────────────────────────────────

def test_ibkr_fill_check_only_registered_for_ibkr_broker(monkeypatch, fresh_portfolio, tmp_path):
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path, ibkr=True)
    _run_registration_only(monkeypatch, kwargs)
    assert "_ibkr_fill_check_job" in _names(schedule.jobs)


def test_ibkr_fill_check_not_registered_for_paper_broker(monkeypatch, fresh_portfolio, tmp_path):
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path, ibkr=False)
    _run_registration_only(monkeypatch, kwargs)
    assert "_ibkr_fill_check_job" not in _names(schedule.jobs)


def test_margin_tier_watch_registered_only_when_margin_enabled(monkeypatch, fresh_portfolio, tmp_path):
    # _margin_tier_watch wird als lambda registriert — schedule zeigt bei
    # Lambdas keinen aussagekräftigen Funktionsnamen/String an, deshalb hier
    # bewusst ein Jobzahl-Vergleich statt Namenssuche (robuster als String-
    # Matching auf einer Lambda-Repräsentation).
    kwargs_on = _make_args(fresh_portfolio, monkeypatch, tmp_path, use_margin=True)
    _run_registration_only(monkeypatch, kwargs_on)
    n_on = len(schedule.jobs)
    schedule.clear()

    kwargs_off = _make_args(fresh_portfolio, monkeypatch, tmp_path, use_margin=False)
    _run_registration_only(monkeypatch, kwargs_off)
    n_off = len(schedule.jobs)

    assert n_on == n_off + 1, (
        "MARGIN aktiviert muss genau 1 zusätzlichen wiederkehrenden Job "
        "registrieren (_margin_tier_watch, alle 2h)."
    )


def test_hedge_jobs_registered_only_with_hedge_strategy(monkeypatch, fresh_portfolio, tmp_path):
    # _run_regime_check + Hedge-Exit-Check sind beide Lambdas — gleiches
    # Argument wie beim Margin-Test: Jobzahl-Vergleich statt Namenssuche.
    kwargs_on = _make_args(fresh_portfolio, monkeypatch, tmp_path, hedge=True)
    _run_registration_only(monkeypatch, kwargs_on)
    n_on = len(schedule.jobs)
    schedule.clear()

    kwargs_off = _make_args(fresh_portfolio, monkeypatch, tmp_path, hedge=False)
    _run_registration_only(monkeypatch, kwargs_off)
    n_off = len(schedule.jobs)

    assert n_on == n_off + 2, (
        "Hedge-Strategie aktiviert muss genau 2 zusätzliche wiederkehrende "
        "Jobs registrieren (_run_regime_check + Hedge-Exit-Check)."
    )


def test_intraday_scan_registered_at_configured_time_when_enabled(monkeypatch, fresh_portfolio, tmp_path):
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path, intraday=True)
    _run_registration_only(monkeypatch, kwargs)
    by_time = {j.at_time.strftime("%H:%M"): getattr(j.job_func, "__name__", "")
              for j in schedule.jobs if j.at_time is not None}
    assert by_time.get("17:30") == "_intraday_scan_job"


def test_intraday_scan_not_registered_when_disabled(monkeypatch, fresh_portfolio, tmp_path):
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path, intraday=False)
    _run_registration_only(monkeypatch, kwargs)
    assert "_intraday_scan_job" not in _names(schedule.jobs)


def test_insider_proactive_job_registered_twice_daily(monkeypatch, fresh_portfolio, tmp_path):
    """Bewusst zwei Aufrufe (08:30 + 13:00) — Regression falls beim Split
    einer der beiden Registrierungs-Aufrufe verloren geht."""
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path)
    _run_registration_only(monkeypatch, kwargs)
    times = sorted(
        j.at_time.strftime("%H:%M") for j in schedule.jobs
        if getattr(j.job_func, "__name__", "") == "_insider_proactive_job"
    )
    assert times == ["08:30", "13:00"]


def test_total_job_count_is_stable(monkeypatch, fresh_portfolio, tmp_path):
    """Grobe Stückzahl-Regression: ein beim Split versehentlich verlorener
    oder doppelt registrierter Job soll auffallen, ohne bei jeder kleinen
    Änderung händisch nachgezählt werden zu müssen."""
    kwargs = _make_args(fresh_portfolio, monkeypatch, tmp_path)
    _run_registration_only(monkeypatch, kwargs)
    n = len(schedule.jobs)
    assert 25 <= n <= 40, f"Unerwartete Job-Anzahl nach Registrierung: {n}"
