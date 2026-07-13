"""
Tests für bot/scheduler_scanners2.py (Roadmap 4.4a) — vierte Naht, Teil 2
aus dem scheduler.py-Split: Reddit-Hype, Kursbewegungs-Alarm, Options-Flow,
PEAD, Short-Squeeze, Insider-Proaktiv, Sektor-Kaskade, Intraday-Scan.
"""
from datetime import datetime
from types import SimpleNamespace

import bot.scheduler_scanners2 as ss2


class _FakeNotifier:
    sent = []
    def send(self, msg, level=None):
        _FakeNotifier.sent.append(msg)


def _scanner_notify_passthrough(notifier, msg):
    notifier.send(msg)


class _FakeDT:
    def __init__(self, fixed):
        self._fixed = fixed
    def now(self):
        return self._fixed


class _FakePortfolio:
    def all_positions(self): return {}


_CYCLE_ARGS = (_FakePortfolio(), "b", "s", "t", "pc", "a", "r", "w", "h", "e")
_CONFIG = SimpleNamespace(watchlist=["AAPL", "MSFT"])


# ── reddit_hype_job ───────────────────────────────────────────────────────────

def _reddit_hit(ticker="GME", velocity=2.0, mentions=10):
    return SimpleNamespace(ticker=ticker, velocity=velocity, mentions=mentions,
                           subreddits=["wallstreetbets"], sample_titles=["To the moon"])


def test_reddit_hype_job_no_hits_is_noop(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("collectors.reddit_hype_scanner.RedditHypeScanner",
                        lambda: SimpleNamespace(scan=lambda **k: []))
    ss2.reddit_hype_job(*_CYCLE_ARGS, _CONFIG, _FakeNotifier, _scanner_notify_passthrough,
                        lambda *a, **k: None)
    assert _FakeNotifier.sent == []


def test_reddit_hype_job_queues_and_runs_cycle(monkeypatch):
    hit = _reddit_hit()
    monkeypatch.setattr("collectors.reddit_hype_scanner.RedditHypeScanner",
                        lambda: SimpleNamespace(scan=lambda **k: [hit]))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    called = []
    ss2.reddit_hype_job(*_CYCLE_ARGS, _CONFIG, _FakeNotifier, _scanner_notify_passthrough,
                        lambda *a, **k: called.append(1))
    assert queued == ["GME"]
    assert called == [1]


def test_reddit_hype_job_skips_low_velocity_low_mentions(monkeypatch):
    hit = _reddit_hit(velocity=0.5, mentions=2)
    monkeypatch.setattr("collectors.reddit_hype_scanner.RedditHypeScanner",
                        lambda: SimpleNamespace(scan=lambda **k: [hit]))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    ss2.reddit_hype_job(*_CYCLE_ARGS, _CONFIG, _FakeNotifier, _scanner_notify_passthrough,
                        lambda *a, **k: None)
    assert queued == []


# ── price_move_job ────────────────────────────────────────────────────────────

class _FakeBroker:
    def __init__(self, prices=None):
        self._prices = prices or {}
    def get_prices(self, tickers): return dict(self._prices)


def test_price_move_job_skips_outside_hours(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 23, 0)))
    _FakeNotifier.sent = []
    ss2.price_move_job(_FakeBroker(), _CONFIG, {}, _FakeNotifier, _scanner_notify_passthrough)
    assert _FakeNotifier.sent == []


def test_price_move_job_triggers_on_threshold_breach(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    last = {"AAPL": 100.0}
    ss2.price_move_job(_FakeBroker(prices={"AAPL": 105.0}), _CONFIG, last,
                       _FakeNotifier, _scanner_notify_passthrough)
    assert queued == ["AAPL"]
    assert last["AAPL"] == 105.0   # aktualisiert für die nächste Runde


def test_price_move_job_no_trigger_below_threshold(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    last = {"AAPL": 100.0}
    ss2.price_move_job(_FakeBroker(prices={"AAPL": 100.5}), _CONFIG, last,
                       _FakeNotifier, _scanner_notify_passthrough)
    assert queued == []


# ── options_flow_job ──────────────────────────────────────────────────────────

def test_options_flow_job_skips_outside_hours(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))  # vor 14 Uhr
    _FakeNotifier.sent = []
    ss2.options_flow_job(_CONFIG, _FakeNotifier, _scanner_notify_passthrough)
    assert _FakeNotifier.sent == []


def test_options_flow_job_queues_bullish_signal(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 15, 0)))
    monkeypatch.setattr("collectors.options_flow_collector.OptionsFlowCollector",
                        lambda **k: SimpleNamespace(
                            collect=lambda t: [{"signal": "BULLISCH", "title": "Big call buy"}]))
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    ss2.options_flow_job(_CONFIG, _FakeNotifier, _scanner_notify_passthrough)
    assert set(queued) == {"AAPL", "MSFT"}


# ── pead_scan_job ─────────────────────────────────────────────────────────────

def test_pead_scan_job_skips_on_weekend(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 11, 10, 0)))
    called = []
    ss2.pead_scan_job(*_CYCLE_ARGS, _FakeNotifier, _scanner_notify_passthrough,
                      lambda *a, **k: called.append(1))
    assert called == []


def test_pead_scan_job_queues_ready_and_runs_cycle(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("bot.runner._get_watchlist", lambda p: (["AAPL"], {}))
    marked = []
    tracker_pead = SimpleNamespace(
        scan_watchlist=lambda wl: None,
        get_ready_for_analysis=lambda: [{"ticker": "AAPL", "surprise_pct": 0.08, "label": "BEAT"}],
        mark_queued=lambda t: marked.append(t),
        cleanup_expired=lambda: None,
    )
    monkeypatch.setattr("analyzers.pead_tracker.PEADTracker", lambda: tracker_pead)
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    called = []
    ss2.pead_scan_job(*_CYCLE_ARGS, _FakeNotifier, _scanner_notify_passthrough,
                      lambda *a, **k: called.append(1))
    assert queued == ["AAPL"] and marked == ["AAPL"] and called == [1]


# ── short_squeeze_scan_job ────────────────────────────────────────────────────

def test_short_squeeze_scan_job_skips_outside_hours(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 23, 0)))
    called = []
    ss2.short_squeeze_scan_job(*_CYCLE_ARGS, _CONFIG, _FakeNotifier,
                               _scanner_notify_passthrough, lambda *a, **k: called.append(1))
    assert called == []


def test_short_squeeze_scan_job_queues_hits_and_runs_cycle(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    hit = {"ticker": "GME", "squeeze_score": 0.3, "si_pct": 20.0, "days_to_cover": 5.0}
    detector = SimpleNamespace(
        scan_watchlist=lambda wl: [hit],
        build_signal_item=lambda t, h: {"title": "Squeeze setup"},
    )
    monkeypatch.setattr("analyzers.short_squeeze_detector.ShortSqueezeDetector", lambda: detector)
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    called = []
    ss2.short_squeeze_scan_job(*_CYCLE_ARGS, _CONFIG, _FakeNotifier,
                               _scanner_notify_passthrough, lambda *a, **k: called.append(1))
    assert queued == ["GME"] and called == [1]


# ── insider_proactive_job ─────────────────────────────────────────────────────

def test_insider_proactive_job_skips_on_weekend(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 11, 10, 0)))
    called = []
    ss2.insider_proactive_job(*_CYCLE_ARGS, {}, _FakeNotifier, _scanner_notify_passthrough,
                              lambda *a, **k: called.append(1))
    assert called == []


def test_insider_proactive_job_queues_strong_buy_and_runs_cycle(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("bot.runner._get_watchlist", lambda p: (["AAPL"], {}))
    score = SimpleNamespace(signal="STRONG_BUY", bullish_count=3, score=0.9, message="Insider buying")
    monkeypatch.setattr("analyzers.insider_signal.get_insider_score", lambda t, lookback_days=3: score)
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    called = []
    ss2.insider_proactive_job(*_CYCLE_ARGS, {}, _FakeNotifier, _scanner_notify_passthrough,
                              lambda *a, **k: called.append(1))
    assert queued == ["AAPL"] and called == [1]


def test_insider_proactive_job_respects_cooldown(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("bot.runner._get_watchlist", lambda p: (["AAPL"], {}))
    recent = {"AAPL": datetime(2026, 7, 13, 9, 0)}   # 1h alt, Cooldown 24h -> aktiv
    called = []
    ss2.insider_proactive_job(*_CYCLE_ARGS, recent, _FakeNotifier, _scanner_notify_passthrough,
                              lambda *a, **k: called.append(1))
    assert called == []


# ── sector_cascade_job ────────────────────────────────────────────────────────

def test_sector_cascade_job_skips_outside_hours(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 22, 0)))
    called = []
    ss2.sector_cascade_job(*_CYCLE_ARGS, {}, _FakeNotifier, _scanner_notify_passthrough,
                           lambda *a, **k: called.append(1))
    assert called == []


def test_sector_cascade_job_queues_siblings_on_sector_move(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    monkeypatch.setattr("bot.runner._get_watchlist", lambda p: (["AAPL", "MSFT"], {}))

    class _FakeHist:
        def __init__(self, closes):
            import pandas as pd
            self._df = pd.DataFrame({"Close": closes})
        def __len__(self): return len(self._df)
        def __getitem__(self, k): return self._df[k]

    def _fake_ticker(etf):
        # XLK bewegt sich stark, alle anderen ETFs bleiben flach.
        closes = [100.0, 103.0] if etf == "XLK" else [100.0, 100.1]
        return SimpleNamespace(history=lambda period=None: _FakeHist(closes))

    monkeypatch.setattr("yfinance.Ticker", _fake_ticker)
    queued = []
    monkeypatch.setattr("analyzers.user_request_queue.add_ticker",
                        lambda t, meta=None: queued.append(t))
    called = []
    ss2.sector_cascade_job(*_CYCLE_ARGS, {}, _FakeNotifier, _scanner_notify_passthrough,
                           lambda *a, **k: called.append(1))
    assert set(queued) == {"AAPL", "MSFT"}   # beide sind XLK-Titel
    assert called == [1]


# ── intraday_scan_job ─────────────────────────────────────────────────────────

def test_intraday_scan_job_skips_on_weekend(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 11, 10, 0)))
    called = []
    ss2.intraday_scan_job(*_CYCLE_ARGS, lambda *a, **k: called.append(1))
    assert called == []


def test_intraday_scan_job_runs_cycle_on_weekday(monkeypatch):
    monkeypatch.setattr(ss2, "datetime", _FakeDT(datetime(2026, 7, 13, 10, 0)))
    called = []
    ss2.intraday_scan_job(*_CYCLE_ARGS, lambda *a, **k: called.append(1))
    assert called == [1]
