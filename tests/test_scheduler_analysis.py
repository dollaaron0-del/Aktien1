"""
Tests für bot/scheduler_analysis.py (Roadmap 4.4a) — fünfte und letzte Naht
aus dem scheduler.py-Split: Pre-Market-Job, Analyse-Job-Registrierung
(+ täglicher Reschedule), Monats-Review, Catch-up, Tages-Watchdog. Direkt
gegen die ausgelagerten Funktionen, kein run_bot_loop-Umweg.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import schedule

import bot.scheduler_analysis as sa


@pytest.fixture(autouse=True)
def _clear_schedule():
    schedule.clear()
    yield
    schedule.clear()


class _FakeNotifier:
    sent = []
    def send(self, msg, level=None):
        _FakeNotifier.sent.append(msg)


class _FakeDT:
    def __init__(self, fixed):
        self._fixed = fixed
    def now(self, tz=None):
        return self._fixed


_CYCLE_ARGS = ("p", "b", "s", "t", "pc", "a", "r", "w", "h", "e")


# ── monthly_review_check ──────────────────────────────────────────────────────

def test_monthly_review_check_noop_when_not_first_of_month(monkeypatch):
    monkeypatch.setattr(sa, "datetime", _FakeDT(datetime(2026, 7, 13, tzinfo=timezone.utc)))
    calls = []
    reflection = SimpleNamespace(generate_monthly_review=lambda: calls.append(1) or "text")
    sa.monthly_review_check(reflection)
    assert calls == []


def test_monthly_review_check_generates_on_first_of_month(monkeypatch):
    monkeypatch.setattr(sa, "datetime", _FakeDT(datetime(2026, 7, 1, tzinfo=timezone.utc)))
    reflection = SimpleNamespace(generate_monthly_review=lambda: "Monatsbericht-Inhalt")
    sa.monthly_review_check(reflection)   # darf nicht werfen


# ── pre_market_job ────────────────────────────────────────────────────────────

class _FakeBriefing:
    def to_console_lines(self): return ["Zeile 1"]
    def to_telegram(self): return "Telegram-Text"


def test_pre_market_job_sends_when_briefing_available(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("bot.runner._get_watchlist", lambda p: (["AAPL"], {}))
    scanner_cls = lambda: SimpleNamespace(run=lambda exchange, watchlist: _FakeBriefing())
    sa.pre_market_job("NYSE", "portfolio", _FakeNotifier, scanner_cls)
    assert _FakeNotifier.sent == ["Telegram-Text"]


def test_pre_market_job_no_briefing_is_noop(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("bot.runner._get_watchlist", lambda p: (["AAPL"], {}))
    scanner_cls = lambda: SimpleNamespace(run=lambda exchange, watchlist: None)
    sa.pre_market_job("NYSE", "portfolio", _FakeNotifier, scanner_cls)
    assert _FakeNotifier.sent == []


def test_pre_market_job_survives_scanner_exception(monkeypatch):
    monkeypatch.setattr("bot.runner._get_watchlist", lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    sa.pre_market_job("NYSE", "portfolio", _FakeNotifier, lambda: None)   # darf nicht werfen


# ── register_analysis_jobs ────────────────────────────────────────────────────

class _FakeMktSchedule:
    def __init__(self, slots):
        self._slots = slots
    def get_schedule_strings(self, date=None):
        return list(self._slots)


def test_register_analysis_jobs_weekend_registers_nothing(monkeypatch):
    monkeypatch.setattr(sa, "datetime", _FakeDT(datetime(2026, 7, 11)))   # Samstag
    mkt = _FakeMktSchedule([{"hhmm": "07:30", "exchange": "XETRA"}])
    sa.register_analysis_jobs(mkt, *_CYCLE_ARGS, lambda *a, **k: None,
                              lambda ex: None, lambda: None)
    assert schedule.jobs == []


def test_register_analysis_jobs_registers_pre_market_before_full_analysis(monkeypatch):
    monkeypatch.setattr(sa, "datetime", _FakeDT(datetime(2026, 7, 13)))   # Montag
    mkt = _FakeMktSchedule([{"hhmm": "07:30", "exchange": "XETRA"}])

    def _pre(exchange): pass
    def _cycle(*a, **k): pass
    def _review(): pass

    sa.register_analysis_jobs(mkt, *_CYCLE_ARGS, _cycle, _pre, _review)

    funcs = [j.job_func.func if hasattr(j.job_func, "func") else j.job_func for j in schedule.jobs]
    names = [getattr(f, "__name__", str(f)) for f in funcs]
    assert names.index("_pre") < names.index("_cycle")
    assert all(getattr(j, "_is_analysis_job", False) for j in schedule.jobs)


# ── reschedule_analysis ───────────────────────────────────────────────────────

def test_reschedule_analysis_calls_register_fn():
    calls = []
    sa.reschedule_analysis(lambda: calls.append(1))
    assert calls == [1]


def test_reschedule_analysis_restores_jobs_on_failure():
    job = schedule.every().day.at("07:30").do(lambda: None)
    job._is_analysis_job = True

    def _failing_register():
        raise RuntimeError("boom")

    sa.reschedule_analysis(_failing_register)
    # Der alte Job muss wiederhergestellt sein (nicht verloren gegangen).
    assert any(getattr(j, "_is_analysis_job", False) for j in schedule.jobs)


# ── catchup_missed_window ─────────────────────────────────────────────────────

def test_catchup_missed_window_weekend_is_noop(monkeypatch):
    monkeypatch.setattr(sa, "datetime", _FakeDT(datetime(2026, 7, 11, 10, 0)))
    called = []
    sa.catchup_missed_window(_FakeMktSchedule([]), *_CYCLE_ARGS, _FakeNotifier,
                             lambda ex: called.append(("pre", ex)),
                             lambda *a, **k: called.append(("cycle",)))
    assert called == []


def test_catchup_missed_window_triggers_within_max_minutes(monkeypatch):
    now = datetime(2026, 7, 13, 8, 0)   # Montag 08:00
    monkeypatch.setattr(sa, "datetime", _FakeDT(now))
    monkeypatch.setattr("analyzers.analysis_log.AnalysisLog",
                        lambda: SimpleNamespace(get_recent=lambda limit=1: []))
    mkt = _FakeMktSchedule([{"hhmm": "07:30", "exchange": "XETRA"}])   # 30 Min her
    called = []
    _FakeNotifier.sent = []
    sa.catchup_missed_window(mkt, *_CYCLE_ARGS, _FakeNotifier,
                             lambda ex: called.append(("pre", ex)),
                             lambda *a, **k: called.append(("cycle",)))
    assert called == [("pre", "XETRA"), ("cycle",)]
    assert len(_FakeNotifier.sent) == 1


def test_catchup_missed_window_skips_when_already_analyzed_today(monkeypatch):
    now = datetime(2026, 7, 13, 8, 0)
    monkeypatch.setattr(sa, "datetime", _FakeDT(now))
    monkeypatch.setattr("analyzers.analysis_log.AnalysisLog",
                        lambda: SimpleNamespace(
                            get_recent=lambda limit=1: [{"analyzed_at": "2026-07-13T07:35:00"}]))
    mkt = _FakeMktSchedule([{"hhmm": "07:30", "exchange": "XETRA"}])
    called = []
    sa.catchup_missed_window(mkt, *_CYCLE_ARGS, _FakeNotifier,
                             lambda ex: called.append(("pre", ex)),
                             lambda *a, **k: called.append(("cycle",)))
    assert called == []


# ── daily_analysis_watchdog ───────────────────────────────────────────────────

def test_daily_analysis_watchdog_weekend_is_noop(monkeypatch):
    monkeypatch.setattr(sa, "datetime", _FakeDT(datetime(2026, 7, 11, 10, 0)))
    called = []
    sa.daily_analysis_watchdog(_FakeMktSchedule([]), *_CYCLE_ARGS, {}, _FakeNotifier,
                               lambda *a, **k: called.append(1))
    assert called == []


def test_daily_analysis_watchdog_skips_within_recent_trigger_cooldown(monkeypatch):
    now = datetime(2026, 7, 13, 10, 0)
    monkeypatch.setattr(sa, "datetime", _FakeDT(now))
    triggered = {"2026-07-13": now - timedelta(hours=1)}   # 1h alt, Cooldown 3h
    called = []
    sa.daily_analysis_watchdog(_FakeMktSchedule([]), *_CYCLE_ARGS, triggered, _FakeNotifier,
                               lambda *a, **k: called.append(1))
    assert called == []


def test_daily_analysis_watchdog_too_early_is_noop(monkeypatch):
    now = datetime(2026, 7, 13, 7, 40)   # nur 10 Min nach 07:30-Slot (< 30 Min Puffer)
    monkeypatch.setattr(sa, "datetime", _FakeDT(now))
    mkt = _FakeMktSchedule([{"hhmm": "07:30", "exchange": "XETRA"}])
    called = []
    sa.daily_analysis_watchdog(mkt, *_CYCLE_ARGS, {}, _FakeNotifier,
                               lambda *a, **k: called.append(1))
    assert called == []


def test_daily_analysis_watchdog_triggers_catchup_when_slot_missed(monkeypatch):
    now = datetime(2026, 7, 13, 9, 0)   # 1,5h nach 07:30-Slot, kein Log-Eintrag
    monkeypatch.setattr(sa, "datetime", _FakeDT(now))
    # Kein frischer Log-Eintrag auch NACH dem Trigger -> Code erkennt das als
    # "Analyse lief, aber kein Beweis" und erlaubt einen Retry (Marker wieder
    # entfernt) statt fälschlich als erledigt zu markieren.
    monkeypatch.setattr("analyzers.analysis_log.AnalysisLog",
                        lambda: SimpleNamespace(get_recent=lambda limit=1: []))
    mkt = _FakeMktSchedule([{"hhmm": "07:30", "exchange": "XETRA"}])
    called = []
    triggered = {}
    sa.daily_analysis_watchdog(mkt, *_CYCLE_ARGS, triggered, _FakeNotifier,
                               lambda *a, **k: called.append(1))
    assert called == [1]
    assert triggered == {}   # Retry erlaubt, kein falsches "erledigt"-Signal


def test_daily_analysis_watchdog_skips_when_log_shows_slot_already_served(monkeypatch):
    now = datetime(2026, 7, 13, 9, 0)
    monkeypatch.setattr(sa, "datetime", _FakeDT(now))
    # Log zeigt eine Analyse, die den fälligen Slot bereits bedient hat (Zeit
    # >= due_slot - 30 Min) -> kein echter Ausfall, kein Trigger, nur Marker.
    monkeypatch.setattr("analyzers.analysis_log.AnalysisLog",
                        lambda: SimpleNamespace(
                            get_recent=lambda limit=1: [{"analyzed_at": "2026-07-13T08:59:00"}]))
    mkt = _FakeMktSchedule([{"hhmm": "07:30", "exchange": "XETRA"}])
    called = []
    triggered = {}
    sa.daily_analysis_watchdog(mkt, *_CYCLE_ARGS, triggered, _FakeNotifier,
                               lambda *a, **k: called.append(1))
    assert called == []
    assert "2026-07-13" in triggered
