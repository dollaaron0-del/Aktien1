"""
Tests für Roadmap 1.12 — Trailing-Stop-Ratchet zieht den broker-seitigen
GTC-Schutz-Stop (Roadmap 1.9) mit nach, statt ihn auf dem alten Niveau
stehen zu lassen.
"""
import threading
import types

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy


class FakeBrokerWithStops:
    """Simuliert einen IBKR-artigen Broker mit update_stop()-API."""
    def __init__(self):
        self.stop_calls = []

    def update_stop(self, ticker, shares, stop_price):
        self.stop_calls.append((ticker, shares, stop_price))
        return True


class FakeBrokerWithoutStops:
    """Simuliert PaperBroker: keine update_stop()-API."""
    pass


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def make_strategy(portfolio, broker):
    s = object.__new__(SwingStrategy)
    s.portfolio = portfolio
    s.broker = broker
    s.signal_queue = None
    s.earnings_filter = None
    s.correlation_checker = None
    s.kelly_sizer = None
    s.goal_risk_assessor = None
    s.focus_ctrl = types.SimpleNamespace(get_max_positions=lambda pv: 12)
    s._lock = threading.Lock()
    s._daily_loss_usd = 0.0
    s._daily_loss_date = ""
    return s


def test_trailing_ratchet_syncs_broker_stop(tmp_path, monkeypatch):
    """+13% Gewinn löst Trailing-Stufe (Schwelle +12% -> SL auf Einstieg+6%)
    aus → der neue SL muss auch an den Broker (GTC-Backstop) durchgereicht
    werden."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="NVDA", shares=10, entry_price=100.0,
        entry_date="2026-06-10T00:00:00", stop_loss=90.0, take_profit=150.0,
        target_hold_days=10,
    ))
    broker = FakeBrokerWithStops()
    strat = make_strategy(p, broker)

    # +9%: über der ersten Trailing-Schwelle (+8%), aber noch unter der
    # ersten Partial-TP-Schwelle (+10%) — reiner Trailing-Fall.
    results = strat.check_exits({"NVDA": 109.0}, regime="NEUTRAL")
    assert results == []  # kein Exit, nur Trailing-Update

    pos = p.get_position("NVDA")
    assert pos.stop_loss == 102.0  # entry * 1.02

    assert broker.stop_calls == [("NVDA", 10, 102.0)]


def test_trailing_ratchet_without_broker_stop_api_is_noop(tmp_path, monkeypatch):
    """PaperBroker (keine update_stop()-API) darf keinen AttributeError werfen."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="NVDA", shares=10, entry_price=100.0,
        entry_date="2026-06-10T00:00:00", stop_loss=90.0, take_profit=150.0,
        target_hold_days=10,
    ))
    strat = make_strategy(p, FakeBrokerWithoutStops())

    results = strat.check_exits({"NVDA": 109.0}, regime="NEUTRAL")
    assert results == []
    pos = p.get_position("NVDA")
    assert pos.stop_loss == 102.0


def test_no_ratchet_below_first_step_skips_broker_call(tmp_path, monkeypatch):
    """Unterhalb der ersten Trailing-Schwelle (+8%) bleibt der SL unverändert
    → keine Broker-Nachführung nötig."""
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="NVDA", shares=10, entry_price=100.0,
        entry_date="2026-06-10T00:00:00", stop_loss=90.0, take_profit=150.0,
        target_hold_days=10,
    ))
    broker = FakeBrokerWithStops()
    strat = make_strategy(p, broker)

    strat.check_exits({"NVDA": 103.0}, regime="NEUTRAL")  # +3%, unter Stufe 1

    assert broker.stop_calls == []
    assert p.get_position("NVDA").stop_loss == 90.0
