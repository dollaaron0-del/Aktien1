"""
Tests für bot/scheduler_scanners.py (Roadmap 4.4a) — vierte Naht (Teil 1)
aus dem scheduler.py-Split: escalate_ticker, Headline-Scanner, Momentum-
Scanner, Breakout-Watch-Scanner. Direkt gegen die ausgelagerten Funktionen,
kein run_bot_loop-Registrierungs-Umweg.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import bot.scheduler_scanners as ss


class _FakeNotifier:
    sent = []
    def send(self, msg, level=None):
        _FakeNotifier.sent.append(msg)


def _scanner_notify_passthrough(notifier, msg):
    notifier.send(msg)


class _FakeDT:
    """Ersatz für das datetime-Modul-Objekt in scheduler_scanners (nur .now())."""
    def __init__(self, fixed):
        self._fixed = fixed
    def now(self):
        return self._fixed


_CYCLE_ARGS = ("p", "b", "s", "t", "pc", "a", "r", "w", "h", "e")


# ── escalate_ticker ───────────────────────────────────────────────────────────

def test_escalate_ticker_empty_list_is_noop():
    calls = []
    ss.escalate_ticker([], *_CYCLE_ARGS, lambda *a, **k: calls.append((a, k)))
    assert calls == []


def test_escalate_ticker_calls_cycle_with_only_tickers():
    calls = []
    ss.escalate_ticker(["AAPL", "AAPL", "MSFT"], *_CYCLE_ARGS,
                       lambda *a, **k: calls.append((a, k)), reason="Test")
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == _CYCLE_ARGS
    assert kwargs == {"only_tickers": ["AAPL", "MSFT"]}   # dedupliziert, Reihenfolge erhalten


# ── headline_scan_job ─────────────────────────────────────────────────────────

class _FakeDetector:
    def __init__(self, signals=None, added=None):
        self._signals = signals or []
        self._added = added or []
    def scan(self): return list(self._signals)
    def process_signals(self, signals, notify_fn=None, exclude_tickers=None):
        return list(self._added)


def _signal(ticker="AAPL", score=0.95, signal_type="MA"):
    return SimpleNamespace(ticker=ticker, score=score, signal_type=signal_type, headline="News")


def test_headline_scan_job_no_signals_is_noop(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("analyzers.headline_signal_detector.HeadlineSignalDetector",
                        lambda: _FakeDetector(signals=[]))
    ss.headline_scan_job({}, _FakeNotifier, _scanner_notify_passthrough, lambda *a, **k: None)
    assert _FakeNotifier.sent == []


def test_headline_scan_job_urgent_signal_in_trading_hours_escalates(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))   # Montag 10:00
    sig = _signal(score=0.95)
    monkeypatch.setattr("analyzers.headline_signal_detector.HeadlineSignalDetector",
                        lambda: _FakeDetector(signals=[sig]))
    escalated = []
    ss.headline_scan_job({}, _FakeNotifier, _scanner_notify_passthrough,
                         lambda tickers, reason=None: escalated.append((tickers, reason)))
    assert escalated == [(["AAPL"], "Headline-Trigger")]


def test_headline_scan_job_urgent_signal_outside_trading_hours_queues(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 13, 2, 0)))   # Montag 02:00
    sig = _signal(score=0.95)
    monkeypatch.setattr("analyzers.headline_signal_detector.HeadlineSignalDetector",
                        lambda: _FakeDetector(signals=[sig]))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    escalated = []
    ss.headline_scan_job({}, _FakeNotifier, _scanner_notify_passthrough,
                         lambda tickers, reason=None: escalated.append(tickers))
    assert queued == ["AAPL"]
    assert escalated == []


def test_headline_scan_job_respects_cooldown(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    sig = _signal(score=0.95, ticker="AAPL")
    monkeypatch.setattr("analyzers.headline_signal_detector.HeadlineSignalDetector",
                        lambda: _FakeDetector(signals=[sig]))
    recent = {"AAPL": datetime(2026, 7, 13, 9, 0)}   # 1h alt, Cooldown 4h -> noch aktiv
    escalated = []
    ss.headline_scan_job(recent, _FakeNotifier, _scanner_notify_passthrough,
                         lambda tickers, reason=None: escalated.append(tickers))
    assert escalated == []


# ── momentum_scan_job ─────────────────────────────────────────────────────────

class _FakePortfolio:
    def all_positions(self): return {}


class _FakeScanner:
    def __init__(self, hits=None):
        self._hits = hits or []
    def scan(self, exclude=None): return list(self._hits)


def _hit(ticker="MSFT", volume_ratio=3.0, change_pct=4.0, streak_days=2):
    return {"ticker": ticker, "volume_ratio": volume_ratio, "change_pct": change_pct,
            "streak_days": streak_days}


def test_momentum_scan_job_skips_on_weekend(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 11, 10, 0)))   # Samstag
    called = []
    ss.momentum_scan_job(_FakePortfolio(), "b", "s", "t", "pc", "a", "r", "w", "h", "e",
                         {}, _FakeNotifier, _scanner_notify_passthrough,
                         lambda *a, **k: called.append(1))
    assert called == []


def test_momentum_scan_job_queues_and_runs_full_cycle(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("analyzers.watchlist_scanner.WatchlistScanner",
                        lambda **k: _FakeScanner(hits=[_hit()]))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    # Analysis-Cache-Datei existiert hier nicht -> FileNotFoundError, still geschluckt.
    monkeypatch.setattr(ss.os.path, "dirname", lambda f: str(tmp_path))
    called = []
    portfolio = _FakePortfolio()
    ss.momentum_scan_job(portfolio, "b", "s", "t", "pc", "a", "r", "w", "h", "e",
                         {}, _FakeNotifier, _scanner_notify_passthrough,
                         lambda *a, **k: called.append(a))
    assert queued == ["MSFT"]
    assert called == [(portfolio, "b", "s", "t", "pc", "a", "r", "w", "h", "e")]


# ── breakout_watch_job ────────────────────────────────────────────────────────

def _breakout_hit(ticker="NVDA", setup_score=2.0, signals=None):
    return {"ticker": ticker, "setup_score": setup_score,
            "signals": signals or ["bb_squeeze"], "price": 900.0, "dist_52w_pct": None}


def test_breakout_watch_job_skips_outside_hours(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 13, 23, 0)))   # 23:00, außerhalb 07:30-21:00
    called = []
    ss.breakout_watch_job(_FakePortfolio(), "b", "s", "t", "pc", "a", "r", "w", "h", "e",
                          {}, _FakeNotifier, _scanner_notify_passthrough,
                          lambda *a, **k: called.append(1))
    assert called == []


def test_breakout_watch_job_queues_new_hit_and_runs_cycle(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("analyzers.watchlist_scanner.WatchlistScanner",
                        lambda **k: _FakeScanner(hits=[_breakout_hit()]))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    called = []
    ss.breakout_watch_job(_FakePortfolio(), "b", "s", "t", "pc", "a", "r", "w", "h", "e",
                          {}, _FakeNotifier, _scanner_notify_passthrough,
                          lambda *a, **k: called.append(1))
    assert queued == ["NVDA"]
    assert called == [1]


def test_breakout_watch_job_respects_cooldown(monkeypatch):
    monkeypatch.setattr(ss, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("analyzers.watchlist_scanner.WatchlistScanner",
                        lambda **k: _FakeScanner(hits=[_breakout_hit()]))
    recent = {"NVDA": datetime(2026, 7, 13, 9, 0)}   # 1h alt, Cooldown 12h -> noch aktiv
    called = []
    ss.breakout_watch_job(_FakePortfolio(), "b", "s", "t", "pc", "a", "r", "w", "h", "e",
                          recent, _FakeNotifier, _scanner_notify_passthrough,
                          lambda *a, **k: called.append(1))
    assert called == []
