"""
Tests für bot/scheduler_macro.py (Roadmap 4.4a) — dritte Naht aus dem
scheduler.py-Split: Geopolitik-Radar, Markt-Breadth, Markt-Overview-Refresh,
Morgen-Lagebericht, Nutzeranfragen, Wochenvorbereitung, IPO-Check. Direkt
gegen die ausgelagerten Funktionen, kein run_bot_loop-Registrierungs-Umweg.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import bot.scheduler_macro as sm


class _FakeNotifier:
    sent = []
    def send(self, msg, level=None):
        _FakeNotifier.sent.append(msg)


def _scanner_notify_passthrough(notifier, msg):
    notifier.send(msg)


# ── geopolitical_radar_job ───────────────────────────────────────────────────

class _FakeRadar:
    def __init__(self, events=None, added=None):
        self._events = events or []
        self._added = added or []
    def scan(self): return list(self._events)
    def process_events(self, events, notify_fn=None):
        return list(self._added)


def test_geopolitical_radar_job_no_events_is_noop(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("analyzers.geopolitical_radar.GeopoliticalRadar", lambda: _FakeRadar(events=[]))
    config = SimpleNamespace(watchlist=["AAPL"])
    sm.geopolitical_radar_job(config, _FakeNotifier, _scanner_notify_passthrough)
    assert _FakeNotifier.sent == []


def test_geopolitical_radar_job_severity_3_escalates_watchlist_ticker(monkeypatch):
    escalated = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker", lambda t: escalated.append(t))
    ev = SimpleNamespace(severity=3, impacts=[SimpleNamespace(tickers=["AAPL", "XYZ"])])
    monkeypatch.setattr("analyzers.geopolitical_radar.GeopoliticalRadar",
                        lambda: _FakeRadar(events=[ev], added=[]))
    config = SimpleNamespace(watchlist=["AAPL"])
    sm.geopolitical_radar_job(config, _FakeNotifier, _scanner_notify_passthrough)
    assert escalated == ["AAPL"]   # XYZ nicht in watchlist -> nicht eskaliert


# ── market_breadth_job ────────────────────────────────────────────────────────

class _FakeDatetimeWeekend:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 7, 11, 12, 0)   # Samstag


def test_market_breadth_job_skips_on_weekend(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr(sm, "datetime", _FakeDatetimeWeekend)
    called = []
    monkeypatch.setattr(sm, "get_market_caution", lambda: called.append(1) or False)
    sm.market_breadth_job(_FakeNotifier)
    assert called == []   # Job kehrt vor jedem Caution-Check zurück
    assert _FakeNotifier.sent == []


# ── market_overview_refresh_job ──────────────────────────────────────────────

class _FakeMarketOverview:
    calls = []
    def full_assessment(self):
        _FakeMarketOverview.calls.append(1)
    def format_telegram(self):
        return "Lagebericht-Text"


def test_market_overview_refresh_job_calls_full_assessment(monkeypatch):
    _FakeMarketOverview.calls = []
    monkeypatch.setattr("analyzers.market_overview.MarketOverview", _FakeMarketOverview)
    sm.market_overview_refresh_job()
    assert _FakeMarketOverview.calls == [1]


# ── morning_lagebericht_job ───────────────────────────────────────────────────

def test_morning_lagebericht_job_skips_when_already_sent_today(monkeypatch):
    _FakeNotifier.sent = []
    today = datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()
    monkeypatch.setattr("analyzers.market_overview.MarketOverview", _FakeMarketOverview)
    sm.morning_lagebericht_job([today], _FakeNotifier)
    assert _FakeNotifier.sent == []


def test_morning_lagebericht_job_sends_and_marks_date(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("analyzers.market_overview.MarketOverview", _FakeMarketOverview)
    sent_date = [""]
    sm.morning_lagebericht_job(sent_date, _FakeNotifier)
    assert _FakeNotifier.sent == ["Lagebericht-Text"]
    assert sent_date[0] == datetime.now(timezone.utc).replace(tzinfo=None).date().isoformat()


# ── user_request_job ──────────────────────────────────────────────────────────

def test_user_request_job_skips_outside_trading_hours(monkeypatch):
    called = []
    monkeypatch.setattr(sm, "datetime", SimpleNamespace(now=lambda: datetime(2026, 7, 13, 2, 0)))  # 02:00
    sm.user_request_job(None, None, None, None, None, None, None, None, None, None,
                        lambda *a, **k: called.append(1))
    assert called == []


def test_user_request_job_runs_focus_cycle_when_pending(monkeypatch):
    called = []
    monkeypatch.setattr(sm, "datetime", SimpleNamespace(now=lambda: datetime(2026, 7, 13, 10, 0)))  # Montag 10:00
    monkeypatch.setattr("analyzers.user_request_queue.peek", lambda: ["AAPL"])
    sm.user_request_job("p", "b", "s", "t", "pc", "a", "r", "w", "h", "e",
                        lambda *args: called.append(args))
    assert len(called) == 1
    assert called[0][0] == "p"   # portfolio durchgereicht


def test_user_request_job_noop_when_queue_empty(monkeypatch):
    called = []
    monkeypatch.setattr(sm, "datetime", SimpleNamespace(now=lambda: datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("analyzers.user_request_queue.peek", lambda: [])
    sm.user_request_job(None, None, None, None, None, None, None, None, None, None,
                        lambda *a, **k: called.append(1))
    assert called == []


# ── weekend_prep_job ──────────────────────────────────────────────────────────

def test_weekend_prep_job_runs_prep_callback(monkeypatch):
    # buy_blocked.json wird über __file__-relativen Pfad gelesen (nicht cwd-
    # relativ) -> Test läuft bewusst gegen die reale Projekt-Datei, prüft nur
    # dass der Prep-Callback lief (Kernverhalten dieses Jobs).
    _FakeNotifier.sent = []
    called = []
    sm.weekend_prep_job("inst", _FakeNotifier, lambda inst: called.append(inst))
    assert called == ["inst"]


def test_weekend_prep_job_notifies_on_failure():
    _FakeNotifier.sent = []
    def _raise(inst):
        raise RuntimeError("boom")
    sm.weekend_prep_job("inst", _FakeNotifier, _raise)
    assert any("Wochenvorbereitung fehlgeschlagen" in m for m in _FakeNotifier.sent)


# ── ipo_check_job ─────────────────────────────────────────────────────────────

class _FakeIPOTracker:
    def __init__(self, events=None):
        self._events = events or []
        self.notified = []
    def run_daily_check(self): return list(self._events)
    def mark_notified(self, slug): self.notified.append(slug)


def test_ipo_check_job_no_new_ipos_is_noop(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("analyzers.ipo_tracker.IPOTracker", lambda: _FakeIPOTracker(events=[]))
    sm.ipo_check_job(_FakeNotifier)
    assert _FakeNotifier.sent == []


def test_ipo_check_job_notifies_and_marks_new_ipo(monkeypatch):
    _FakeNotifier.sent = []
    cand = SimpleNamespace(name="NewCo", sector="Tech", expected_valuation_b=30.0,
                           notes="", auto_watchlist_eligible=True)
    event = {"candidate": cand, "live_ticker": "NEWCO", "slug": "newco-ipo"}
    tracker = _FakeIPOTracker(events=[event])
    monkeypatch.setattr("analyzers.ipo_tracker.IPOTracker", lambda: tracker)
    sm.ipo_check_job(_FakeNotifier)
    assert len(_FakeNotifier.sent) == 1
    assert "NewCo" in _FakeNotifier.sent[0]
    assert tracker.notified == ["newco-ipo"]
