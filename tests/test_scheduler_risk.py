"""
Tests für bot/scheduler_risk.py (Roadmap 4.4a) — zweite Naht aus dem
scheduler.py-Split: Conditional-Entry-Ausführung, IBKR-Fill-Buchung,
Signal-Queue-Drain, SL/TP-Check, Position-Aging. Direkt gegen die
ausgelagerten Funktionen, kein run_bot_loop-Registrierungs-Umweg.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bot.scheduler_risk as sr
from strategy.swing_strategy import StrategyResult


class _FakeNotifier:
    sent = []
    sent_with_level = []
    def send(self, msg, level=None, link_target=None):
        _FakeNotifier.sent.append(msg)
        _FakeNotifier.sent_with_level.append((msg, level))


class _FakeBroker:
    def __init__(self, prices=None, fills=None):
        self._prices = prices or {}
        self._fills = fills or []
    def get_prices(self, tickers): return dict(self._prices)
    def get_filled_limit_orders(self, order_ids): return list(self._fills)


class _FakeExecutor:
    def __init__(self, action="GEKAUFT 1 X @ $10"):
        self._action = action
        self.calls = []
    def execute(self, res, *, analysis=None, days_held=0):
        self.calls.append((res, analysis, days_held))
        return self._action


class _FakeStrategy:
    def __init__(self, evaluate_result=None, exits=None):
        self._evaluate_result = evaluate_result
        self._exits = exits or []
    def evaluate(self, ticker, analysis, price, regime):
        return self._evaluate_result
    def check_exits(self, prices):
        return list(self._exits)


def _entry(ticker="AAPL", ibkr_order_id=None, trigger_price=90.0, sources_count=2):
    return SimpleNamespace(
        ticker=ticker, trigger_price=trigger_price, sentiment_score=0.7,
        ibkr_order_id=ibkr_order_id, confidence="HIGH", entry_rationale="These",
        bull_case="Bull", bear_case="Bear", risk_factors=[], key_catalysts=[],
        suggested_hold_days=14, target_price=120.0, target_price_rationale="",
        sources_count=sources_count,
    )


class _FakeWatcher:
    def __init__(self, active=None, triggered=None):
        self._active = active or []
        self._triggered = triggered if triggered is not None else list(self._active)
        self.removed = []
    def get_active(self): return list(self._active)
    def check_triggered(self, prices): return list(self._triggered)
    def remove(self, ticker): self.removed.append(ticker)


# ── conditional_entry_job ────────────────────────────────────────────────────

def test_conditional_entry_job_no_active_entries_is_noop(monkeypatch):
    _FakeNotifier.sent = []
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher",
                        lambda: _FakeWatcher(active=[]))
    sr.conditional_entry_job(_FakeBroker(), _FakeStrategy(), _FakeExecutor(), _FakeNotifier)
    assert _FakeNotifier.sent == []


def test_conditional_entry_job_skips_ibkr_managed_entries(monkeypatch):
    watcher = _FakeWatcher(active=[_entry(ibkr_order_id="123")])
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher", lambda: watcher)
    executor = _FakeExecutor()
    sr.conditional_entry_job(_FakeBroker(), _FakeStrategy(), executor, _FakeNotifier)
    assert executor.calls == []
    assert watcher.removed == []


def test_conditional_entry_job_executes_and_removes_on_buy(monkeypatch):
    entry = _entry(ticker="AAPL", trigger_price=90.0)
    watcher = _FakeWatcher(active=[entry], triggered=[entry])
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher", lambda: watcher)
    result = StrategyResult(action="BUY", ticker="AAPL", reason="ok", shares=1, price=95.0)
    executor = _FakeExecutor(action="GEKAUFT 1 AAPL @ $95")
    broker = _FakeBroker(prices={"AAPL": 95.0})
    sr.conditional_entry_job(broker, _FakeStrategy(evaluate_result=result), executor, _FakeNotifier)
    assert len(executor.calls) == 1
    assert executor.calls[0][0] is result
    assert watcher.removed == ["AAPL"]


def test_conditional_entry_job_keeps_entry_when_not_executed(monkeypatch):
    entry = _entry(ticker="AAPL")
    watcher = _FakeWatcher(active=[entry], triggered=[entry])
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher", lambda: watcher)
    executor = _FakeExecutor(action=None)   # Strategy-Filter blockt
    sr.conditional_entry_job(_FakeBroker(prices={"AAPL": 95.0}), _FakeStrategy(), executor, _FakeNotifier)
    assert watcher.removed == []


# ── ibkr_fill_check_job ──────────────────────────────────────────────────────

class _FakePortfolio:
    def __init__(self, raises=None):
        self.opened = []
        self.raises = raises
    def open_position(self, pos, force=False):
        if self.raises:
            raise self.raises
        self.opened.append((pos, force))


def test_ibkr_fill_check_job_no_ibkr_entries_is_noop(monkeypatch):
    _FakeNotifier.sent = []
    watcher = _FakeWatcher(active=[])
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher", lambda: watcher)
    sr.ibkr_fill_check_job(_FakeBroker(), _FakePortfolio(), _FakeNotifier)
    assert _FakeNotifier.sent == []


def test_ibkr_fill_check_job_books_fill_and_notifies(monkeypatch):
    _FakeNotifier.sent = []
    entry = _entry(ticker="AAPL", ibkr_order_id=42)
    watcher = _FakeWatcher(active=[entry])
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher", lambda: watcher)
    broker = _FakeBroker(fills=[{"order_id": 42, "fill_price": 100.0, "shares": 3}])
    portfolio = _FakePortfolio()
    sr.ibkr_fill_check_job(broker, portfolio, _FakeNotifier)
    assert len(portfolio.opened) == 1
    pos, force = portfolio.opened[0]
    assert pos.ticker == "AAPL" and pos.shares == 3 and pos.entry_price == 100.0
    assert force is True
    assert watcher.removed == ["AAPL"]
    assert len(_FakeNotifier.sent) == 1
    assert "IBKR Limit-Order ausgeführt" in _FakeNotifier.sent[0]


def test_ibkr_fill_check_job_notifies_on_booking_failure(monkeypatch):
    _FakeNotifier.sent = []
    entry = _entry(ticker="AAPL", ibkr_order_id=42)
    watcher = _FakeWatcher(active=[entry])
    monkeypatch.setattr("analyzers.conditional_entry.ConditionalEntryWatcher", lambda: watcher)
    broker = _FakeBroker(fills=[{"order_id": 42, "fill_price": 100.0, "shares": 3}])
    portfolio = _FakePortfolio(raises=ValueError("kein Cash"))
    sr.ibkr_fill_check_job(broker, portfolio, _FakeNotifier)
    assert watcher.removed == []   # Buchung fehlgeschlagen -> Entry bleibt
    assert len(_FakeNotifier.sent) == 1
    assert "konnte nicht ins Portfolio eingetragen werden" in _FakeNotifier.sent[0]


# ── signal_queue_job ──────────────────────────────────────────────────────────

class _FakeSignalQueue:
    def __init__(self, pending=0):
        self._pending = pending
    def count_pending(self): return self._pending


def test_signal_queue_job_skips_when_empty(monkeypatch):
    called = []
    monkeypatch.setattr("strategy.executor.process_signal_queue",
                        lambda *a, **k: called.append(1) or [])
    sr.signal_queue_job(_FakeSignalQueue(pending=0), _FakeStrategy(), _FakeExecutor(), _FakeBroker())
    assert called == []


def test_signal_queue_job_drains_when_pending(monkeypatch, capsys):
    monkeypatch.setattr("strategy.executor.process_signal_queue",
                        lambda *a, **k: ["GEKAUFT 1 MSFT"])
    sr.signal_queue_job(_FakeSignalQueue(pending=1), _FakeStrategy(), _FakeExecutor(), _FakeBroker())
    out = capsys.readouterr().out
    assert "GEKAUFT" in out and "MSFT" in out


# ── sl_tp_check_job ───────────────────────────────────────────────────────────

class _FakePosition:
    def __init__(self, entry_date=None):
        self.entry_date = entry_date or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class _FakePortfolioWithPositions:
    def __init__(self, positions=None):
        self._positions = positions or {}
    def all_positions(self): return dict(self._positions)
    def get_position(self, ticker): return self._positions.get(ticker)


def test_sl_tp_check_job_no_positions_is_noop():
    calls = []
    sr.sl_tp_check_job(_FakePortfolioWithPositions({}), _FakeBroker(), _FakeStrategy(),
                       _FakeExecutor(), lambda: calls.append(1))
    assert calls == []


def test_sl_tp_check_job_triggers_signal_queue_on_sell():
    calls = []
    portfolio = _FakePortfolioWithPositions({"AAPL": _FakePosition()})
    exit_res = StrategyResult(action="SELL", ticker="AAPL", reason="SL", shares=1, price=90.0)
    strategy = _FakeStrategy(exits=[exit_res])
    executor = _FakeExecutor(action="VERKAUFT 1 AAPL @ $90")
    sr.sl_tp_check_job(portfolio, _FakeBroker(prices={"AAPL": 90.0}), strategy, executor,
                       lambda: calls.append(1))
    assert calls == [1]


def test_sl_tp_check_job_no_signal_queue_trigger_without_sell():
    calls = []
    portfolio = _FakePortfolioWithPositions({"AAPL": _FakePosition()})
    exit_res = StrategyResult(action="HOLD", ticker="AAPL", reason="ok", shares=0, price=90.0)
    strategy = _FakeStrategy(exits=[exit_res])
    executor = _FakeExecutor(action=None)
    sr.sl_tp_check_job(portfolio, _FakeBroker(prices={"AAPL": 90.0}), strategy, executor,
                       lambda: calls.append(1))
    assert calls == []


# ── position_aging_job ────────────────────────────────────────────────────────

class _FakeAgingPosition:
    def __init__(self, entry_price, target_hold_days, days_ago):
        self.entry_price = entry_price
        self.target_hold_days = target_hold_days
        self.entry_date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)).isoformat()


def test_position_aging_job_no_positions_is_noop():
    _FakeNotifier.sent = []
    sr.position_aging_job(_FakePortfolioWithPositions({}), _FakeBroker(), _FakeNotifier)
    assert _FakeNotifier.sent == []


def test_position_aging_job_warns_on_stale_loser():
    _FakeNotifier.sent = []
    # 9 von 10 Ziel-Tagen (Ratio 0.9 >= 0.8), im Minus.
    pos = _FakeAgingPosition(entry_price=100.0, target_hold_days=10, days_ago=9)
    portfolio = _FakePortfolioWithPositions({"AAPL": pos})
    sr.position_aging_job(portfolio, _FakeBroker(prices={"AAPL": 95.0}), _FakeNotifier)
    assert len(_FakeNotifier.sent) == 1
    assert "Aging-Warnung" in _FakeNotifier.sent[0]


def test_position_aging_job_notes_runner():
    _FakeNotifier.sent = []
    # Haltedauer überschritten (Ratio > 1), im Plus.
    pos = _FakeAgingPosition(entry_price=100.0, target_hold_days=10, days_ago=12)
    portfolio = _FakePortfolioWithPositions({"AAPL": pos})
    sr.position_aging_job(portfolio, _FakeBroker(prices={"AAPL": 110.0}), _FakeNotifier)
    assert len(_FakeNotifier.sent) == 1
    assert "Läufer" in _FakeNotifier.sent[0]


# ── broker_healing_pass ──────────────────────────────────────────────────────
# Bis 27.7.2026 lief Broker-Abgleich + Schutz-Stop-Sync nur beim Bot-Start
# (main.py) – ein broker-seitig gefeuerter GTC-Stop blieb sonst bis zum
# nächsten Neustart unbemerkt (META-Doppelkauf-Vorfall). Diese Tests laufen
# gegen ein echtes Portfolio (wie tests/test_integrity.py), weil
# reconcile_against_broker() direkt auf der sqlite-Verbindung arbeitet.

class _FakeBrokerRecon:
    def __init__(self, positions=None, sync_result=None):
        self._positions = positions
        self._sync_result = {} if sync_result is None else sync_result
        self.sync_calls = []

    def positions(self):
        return self._positions

    def sync_protective_stops(self, book):
        self.sync_calls.append(book)
        return self._sync_result


def _make_real_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    import portfolio.portfolio as port_mod
    from portfolio.portfolio import Portfolio
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def _make_real_position(ticker="AAPL", shares=10, entry_price=150.0):
    from portfolio.portfolio import Position
    return Position(
        ticker=ticker, shares=shares, entry_price=entry_price,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=entry_price * 0.9, take_profit=entry_price * 1.2,
        target_hold_days=14,
    )


def test_broker_healing_pass_books_real_sl_loss_for_phantom(tmp_path, monkeypatch):
    """Kern des Fixes: ein von IBKR verschwundener Titel wird zum vermuteten
    SL-Preis mit echtem Verlust gebucht, nicht mehr zum Einstiegspreis mit
    pnl=0 (das hätte den realen Verlust verschleiert)."""
    p = _make_real_portfolio(tmp_path, monkeypatch)
    p.open_position(_make_real_position("MSFT", 10, 400.0))  # SL=360.0
    broker = _FakeBrokerRecon(positions={})  # IBKR hält nichts mehr
    sr.broker_healing_pass(p, broker, _FakeNotifier, context="Test")

    assert p.get_position("MSFT") is None
    row = p._conn.execute(
        "SELECT price, pnl FROM trades WHERE ticker='MSFT' AND action='SELL'"
    ).fetchone()
    assert row[0] == pytest.approx(360.0)
    assert row[1] == pytest.approx(-400.0)


def test_broker_healing_pass_syncs_missing_stops_and_notifies(tmp_path, monkeypatch):
    p = _make_real_portfolio(tmp_path, monkeypatch)
    p.open_position(_make_real_position("AAPL", 10, 150.0))
    broker = _FakeBrokerRecon(positions={"AAPL": 10.0}, sync_result={"AAPL": False})
    _FakeNotifier.sent = []
    sr.broker_healing_pass(p, broker, _FakeNotifier, context="Test")

    assert broker.sync_calls, "Schutz-Stop-Sync sollte für die gedeckte Position versucht werden"
    assert any("Schutz-Stops" in m for m in _FakeNotifier.sent)


def test_broker_healing_pass_offline_broker_is_noop(tmp_path, monkeypatch):
    """Broker offline (positions() liefert None) → NICHT als 'flach' werten,
    sonst würde jede Buch-Position fälschlich als Phantom ausgebucht."""
    p = _make_real_portfolio(tmp_path, monkeypatch)
    p.open_position(_make_real_position("AAPL", 10, 150.0))
    broker = _FakeBrokerRecon(positions=None)
    sr.broker_healing_pass(p, broker, _FakeNotifier, context="Test")
    assert p.get_position("AAPL") is not None


# ── Alert-Eskalation (Regression 28.08.2026, RHM.DE-Fall) ─────────────────────
# Eine Teil-Abweichung (Buch-Stückzahl nachweislich falsch) lief bisher mit
# level="info" durch send() — unter dem Default TELEGRAM_MODE=important wird
# "info" NIE zugestellt, nur geloggt. Genau das ließ RHM.DE (Buch 14,7875 vs.
# real 2 Stück) wochenlang unbemerkt bei jedem 30-Min-Zyklus verpuffen. Jetzt:
# alles, was die Buch-Stückzahl selbst betrifft (reconciled/partial_mismatch/
# snapshot_rejected), geht auf level="critical" raus und kommt durch.

def _reset_reconcile_throttle(tmp_path, monkeypatch):
    import strategy.executor as ex_mod
    monkeypatch.setattr(ex_mod, "_THROTTLE_FILE", tmp_path / "throttle.json")


def test_partial_mismatch_escalates_to_critical(tmp_path, monkeypatch):
    _reset_reconcile_throttle(tmp_path, monkeypatch)
    p = _make_real_portfolio(tmp_path, monkeypatch)
    p.open_position(_make_real_position("RHM.DE", 14.7875, 1017.0))
    broker = _FakeBrokerRecon(positions={"RHM": 2.0})  # Teil-Deckung
    _FakeNotifier.sent, _FakeNotifier.sent_with_level = [], []

    sr.broker_healing_pass(p, broker, _FakeNotifier, context="Test")

    assert any(lvl == "critical" for _, lvl in _FakeNotifier.sent_with_level), (
        "Teil-Abweichung (falsche Buch-Stückzahl) muss level='critical' senden, "
        "sonst wird sie unter TELEGRAM_MODE=important nie zugestellt"
    )


def test_untracked_only_stays_info(tmp_path, monkeypatch):
    """Gegenprobe: eine bei IBKR gehaltene, aber im Buch unbekannte Position
    verfälscht das Buch selbst nicht → bleibt auf info (kein Alert-Fatigue für
    seit langem bekannte Fremd-Positionen)."""
    _reset_reconcile_throttle(tmp_path, monkeypatch)
    p = _make_real_portfolio(tmp_path, monkeypatch)
    p.open_position(_make_real_position("AAPL", 10, 150.0))
    broker = _FakeBrokerRecon(positions={"AAPL": 10.0, "MSFT": 5.0})
    _FakeNotifier.sent, _FakeNotifier.sent_with_level = [], []

    sr.broker_healing_pass(p, broker, _FakeNotifier, context="Test")

    assert _FakeNotifier.sent_with_level, "untracked-Fall sollte trotzdem melden (nur eben nicht kritisch)"
    assert all(lvl == "info" for _, lvl in _FakeNotifier.sent_with_level)


def test_critical_alert_throttled_when_unchanged(tmp_path, monkeypatch):
    """Dieselbe Teil-Abweichung darf nicht bei jedem 30-Min-Lauf erneut als
    critical raus (Dauerspam) — Throttle wie bei anderen Fail-Alerts, 12h."""
    _reset_reconcile_throttle(tmp_path, monkeypatch)
    p = _make_real_portfolio(tmp_path, monkeypatch)
    p.open_position(_make_real_position("RHM.DE", 14.7875, 1017.0))
    broker = _FakeBrokerRecon(positions={"RHM": 2.0})
    _FakeNotifier.sent, _FakeNotifier.sent_with_level = [], []

    sr.broker_healing_pass(p, broker, _FakeNotifier, context="Test")
    n_first = len(_FakeNotifier.sent_with_level)
    assert n_first >= 1

    sr.broker_healing_pass(p, broker, _FakeNotifier, context="Test")
    assert len(_FakeNotifier.sent_with_level) == n_first, (
        "unveränderte Teil-Abweichung darf im Cooldown-Fenster nicht erneut senden"
    )
