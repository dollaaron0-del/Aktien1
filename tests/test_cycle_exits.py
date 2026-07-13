"""
Tests für bot/cycle_exits.py (Roadmap 4.4a) – SL/TP-Check + TradingView-SELL-
Signale. Eigenständiges Modul mit klaren injizierten Abhängigkeiten (kein
run_analysis_cycle-Fixture-Aufwand nötig), bisher aber ungetestet (die
run_analysis_cycle-Charakterisierungstests schalten tradingview_webhook_enabled
auf False und haben ein leeres Portfolio) – genau die Lücke aus
[[monolith-split-4-4a-status]] ("TradingView-Sell-Signale noch nicht gepinnt").
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bot.cycle_exits as cycle_exits_mod
from bot.cycle_exits import run_exit_checks
from strategy.swing_strategy import StrategyResult


class _FakeLive:
    def set_phase(self, *a, **k): pass


class _FakePosition:
    def __init__(self, ticker="AAPL", shares=5, entry_price=100.0, days_ago=3):
        self.ticker = ticker
        self.shares = shares
        self.entry_price = entry_price
        self.entry_date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago)).isoformat()


class _FakePortfolio:
    def __init__(self, positions=None):
        self._positions = positions or {}
    def all_positions(self):        return dict(self._positions)
    def get_position(self, ticker): return self._positions.get(ticker)


class _FakeBroker:
    def __init__(self, prices=None, price=None):
        self._prices = prices or {}
        self._price = price
    def get_prices(self, tickers): return dict(self._prices)
    def get_price(self, ticker):   return self._price


class _FakeStrategy:
    def __init__(self, exits=None): self._exits = exits or []
    def check_exits(self, prices, regime): return list(self._exits)


class _FakeExecutor:
    def __init__(self, action="VERKAUFT 5 AAPL @ $95"):
        self._action = action
        self.calls = []
    def execute(self, res, *, days_held=0):
        self.calls.append((res, days_held))
        return self._action


# ── SL/TP-Check ───────────────────────────────────────────────────────────────

def test_sl_tp_exit_executes_and_records_action():
    portfolio = _FakePortfolio({"AAPL": _FakePosition()})
    broker = _FakeBroker(prices={"AAPL": 95.0})
    strategy = _FakeStrategy(exits=[SimpleNamespace(ticker="AAPL")])
    executor = _FakeExecutor(action="VERKAUFT 5 AAPL @ $95")
    cycle_actions = []

    run_exit_checks(portfolio, broker, strategy, executor, "NEUTRAL", cycle_actions, _FakeLive())

    assert executor.calls and executor.calls[0][0].ticker == "AAPL"
    assert cycle_actions == ["VERKAUFT 5 AAPL @ $95"]


def test_sl_tp_exit_days_held_computed_from_entry_date():
    portfolio = _FakePortfolio({"AAPL": _FakePosition(days_ago=7)})
    broker = _FakeBroker(prices={"AAPL": 95.0})
    strategy = _FakeStrategy(exits=[SimpleNamespace(ticker="AAPL")])
    executor = _FakeExecutor()

    run_exit_checks(portfolio, broker, strategy, executor, "NEUTRAL", [], _FakeLive())

    assert executor.calls[0][1] == 7   # days_held


def test_no_open_positions_no_action_no_crash():
    portfolio = _FakePortfolio({})
    broker = _FakeBroker()
    strategy = _FakeStrategy(exits=[])
    executor = _FakeExecutor()
    cycle_actions = []

    run_exit_checks(portfolio, broker, strategy, executor, "NEUTRAL", cycle_actions, _FakeLive())

    assert cycle_actions == []
    assert executor.calls == []


def test_check_exits_exception_is_caught_and_does_not_propagate(monkeypatch):
    class _BrokenStrategy:
        def check_exits(self, prices, regime):
            raise RuntimeError("boom")

    portfolio = _FakePortfolio({"AAPL": _FakePosition()})
    broker = _FakeBroker(prices={"AAPL": 95.0})
    executor = _FakeExecutor()
    monkeypatch.setattr(cycle_exits_mod.config, "tradingview_webhook_enabled", False)

    # Wirft NICHT, obwohl check_exits() crasht – Fehler bei einem Ticker darf
    # den restlichen Zyklus nicht abreißen.
    run_exit_checks(portfolio, broker, _BrokenStrategy(), executor, "NEUTRAL", [], _FakeLive())
    assert executor.calls == []


# ── TradingView-SELL-Signale ──────────────────────────────────────────────────

def test_tradingview_sell_executes_when_position_open(monkeypatch):
    monkeypatch.setattr(cycle_exits_mod.config, "tradingview_webhook_enabled", True)
    monkeypatch.setattr(cycle_exits_mod, "get_pending_sells",
                        lambda: [{"ticker": "AAPL", "strategy": "Bearish Engulfing"}])
    portfolio = _FakePortfolio({"AAPL": _FakePosition(shares=5, entry_price=100.0)})
    broker = _FakeBroker(price=95.0)
    strategy = _FakeStrategy(exits=[])
    executor = _FakeExecutor(action="VERKAUFT 5 AAPL @ $95 (TradingView)")
    cycle_actions = []

    run_exit_checks(portfolio, broker, strategy, executor, "NEUTRAL", cycle_actions, _FakeLive())

    assert len(executor.calls) == 1
    res, days_held = executor.calls[0]
    assert isinstance(res, StrategyResult)
    assert res.action == "SELL" and res.ticker == "AAPL" and res.shares == 5 and res.price == 95.0
    assert "Bearish Engulfing" in res.reason
    assert cycle_actions == ["VERKAUFT 5 AAPL @ $95 (TradingView)"]


def test_tradingview_sell_falls_back_to_entry_price_when_broker_has_no_quote(monkeypatch):
    monkeypatch.setattr(cycle_exits_mod.config, "tradingview_webhook_enabled", True)
    monkeypatch.setattr(cycle_exits_mod, "get_pending_sells",
                        lambda: [{"ticker": "AAPL", "strategy": "Short"}])
    portfolio = _FakePortfolio({"AAPL": _FakePosition(shares=5, entry_price=100.0)})
    broker = _FakeBroker(price=None)   # kein Kurs verfügbar
    executor = _FakeExecutor()

    run_exit_checks(portfolio, broker, _FakeStrategy(), executor, "NEUTRAL", [], _FakeLive())

    assert executor.calls[0][0].price == 100.0   # Fallback: Einstandspreis


def test_tradingview_sell_skipped_when_no_open_position(monkeypatch):
    monkeypatch.setattr(cycle_exits_mod.config, "tradingview_webhook_enabled", True)
    monkeypatch.setattr(cycle_exits_mod, "get_pending_sells",
                        lambda: [{"ticker": "MSFT", "strategy": "Short"}])
    portfolio = _FakePortfolio({})   # keine offene Position in MSFT
    broker = _FakeBroker(price=300.0)
    executor = _FakeExecutor()
    cycle_actions = []

    run_exit_checks(portfolio, broker, _FakeStrategy(), executor, "NEUTRAL", cycle_actions, _FakeLive())

    assert executor.calls == []
    assert cycle_actions == []


def test_tradingview_disabled_by_default_skips_pending_sells_lookup(monkeypatch):
    monkeypatch.setattr(cycle_exits_mod.config, "tradingview_webhook_enabled", False)
    called = []
    monkeypatch.setattr(cycle_exits_mod, "get_pending_sells", lambda: called.append(1) or [])

    run_exit_checks(_FakePortfolio({}), _FakeBroker(), _FakeStrategy(), _FakeExecutor(),
                    "NEUTRAL", [], _FakeLive())

    assert called == []   # get_pending_sells() wurde gar nicht erst aufgerufen
