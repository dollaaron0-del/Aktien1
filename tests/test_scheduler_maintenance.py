"""
Tests für bot/scheduler_maintenance.py (Roadmap 4.4a) — erste Naht aus dem
scheduler.py-Split (37 Job-Closures). Direkt gegen die drei ausgelagerten
Funktionen, ohne den run_bot_loop-Registrierungs-Umweg.
"""
from datetime import datetime

import bot.scheduler_maintenance as sm


class _FakeArchive:
    def __init__(self, raises=False):
        self.raises = raises
        self.cleaned_days = None
    def cleanup_old(self, keep_days=32):
        if self.raises:
            raise RuntimeError("archive boom")
        self.cleaned_days = keep_days


class _FakeReflection:
    def __init__(self, n=0):
        self.n = n
    def cleanup_old(self, keep_memos=30, keep_monthly=24):
        return self.n


class _FakeSignalQueue:
    def __init__(self, n=0):
        self.n = n
    def cleanup_expired(self):
        return self.n


class _FakeNotifier:
    sent = []
    def send(self, msg, level=None):
        _FakeNotifier.sent.append(msg)
    def notify_daily_summary(self, **kwargs):
        _FakeNotifier.sent.append(("summary", kwargs))


# ── daily_maintenance_job ────────────────────────────────────────────────────

def test_daily_maintenance_job_no_notification_on_full_success(monkeypatch, tmp_path):
    _FakeNotifier.sent = []
    monkeypatch.chdir(tmp_path)   # VACUUM-Pfade existieren hier nicht -> stiller Skip, kein Fehler
    sm.daily_maintenance_job(_FakeArchive(), _FakeReflection(), _FakeSignalQueue(), _FakeNotifier)
    assert _FakeNotifier.sent == []


def test_daily_maintenance_job_notifies_on_failure(monkeypatch, tmp_path):
    _FakeNotifier.sent = []
    monkeypatch.chdir(tmp_path)
    sm.daily_maintenance_job(_FakeArchive(raises=True), _FakeReflection(), _FakeSignalQueue(), _FakeNotifier)
    assert len(_FakeNotifier.sent) == 1
    assert "News-Archiv Cleanup" in _FakeNotifier.sent[0]


# ── daily_summary_job ────────────────────────────────────────────────────────

class _FakePortfolio:
    cash = 5_000.0
    def all_positions(self): return {}
    def total_value(self, prices): return 12_345.0


class _FakeBroker:
    def get_prices(self, tickers): return {}


class _FakePhaseCtrl:
    def current_phase(self, total): return "GROWTH"
    def progress_pct(self, total): return 42.0


class _FakeDatetime:
    def __init__(self, dt):
        self._dt = dt
    def now(self):
        return self._dt


def test_daily_summary_job_skips_on_weekend(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr(sm, "datetime", _FakeDatetime(datetime(2026, 7, 11)))  # Samstag
    sm.daily_summary_job(_FakeBroker(), _FakePortfolio(), _FakePhaseCtrl(), _FakeNotifier)
    assert _FakeNotifier.sent == []


def test_daily_summary_job_sends_on_weekday(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr(sm, "datetime", _FakeDatetime(datetime(2026, 7, 13)))  # Montag
    monkeypatch.setattr("bot.runner.pop_daily_actions", lambda: ["GEKAUFT 1 AAPL"])
    sm.daily_summary_job(_FakeBroker(), _FakePortfolio(), _FakePhaseCtrl(), _FakeNotifier)
    assert len(_FakeNotifier.sent) == 1
    kind, kwargs = _FakeNotifier.sent[0]
    assert kind == "summary"
    assert kwargs["total_value"] == 12_345.0
    assert kwargs["cash"] == 5_000.0
    assert kwargs["actions_today"] == ["GEKAUFT 1 AAPL"]


# ── daily_dashboard_job ───────────────────────────────────────────────────────

class _FakeDashboard:
    def __init__(self, should_send=True, claim_ok=True):
        self._should_send = should_send
        self._claim_ok = claim_ok
        self.marked_sent = False
        self.generate_calls = []
    def should_send(self): return self._should_send
    def try_claim_send(self): return self._claim_ok
    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return "dashboard message"
    def mark_sent(self): self.marked_sent = True


class _FakeBotScorer:
    pass


def test_daily_dashboard_job_skips_when_should_not_send():
    _FakeNotifier.sent = []
    dashboard = _FakeDashboard(should_send=False)
    sm.daily_dashboard_job(dashboard, _FakePortfolio(), None, _FakeBroker(), _FakeBotScorer, _FakeNotifier)
    assert _FakeNotifier.sent == []
    assert dashboard.marked_sent is False


def test_daily_dashboard_job_skips_when_claim_fails():
    _FakeNotifier.sent = []
    dashboard = _FakeDashboard(should_send=True, claim_ok=False)
    sm.daily_dashboard_job(dashboard, _FakePortfolio(), None, _FakeBroker(), _FakeBotScorer, _FakeNotifier)
    assert _FakeNotifier.sent == []
    assert dashboard.marked_sent is False


def test_daily_dashboard_job_sends_and_marks_sent():
    _FakeNotifier.sent = []
    dashboard = _FakeDashboard(should_send=True, claim_ok=True)
    sm.daily_dashboard_job(dashboard, _FakePortfolio(), "tracker", _FakeBroker(), _FakeBotScorer, _FakeNotifier)
    assert _FakeNotifier.sent == ["dashboard message"]
    assert dashboard.marked_sent is True
    assert dashboard.generate_calls[0]["tracker"] == "tracker"
