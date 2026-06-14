"""
Integrationstest: SwingStrategy.evaluate() → TradeExecutor.execute().

Beweist, dass die seit 7.6.2026 gebrochene Kette (reine Entscheidungs-Engine
+ fehlende Ausführungs-Schicht) wieder durchläuft: aus einem bullischen
Analyse-Ergebnis wird real eine Position eröffnet bzw. ein Exit ausgeführt.
"""
import threading
import types

import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy
from strategy.executor import TradeExecutor, _NullNotifier


class FakeBroker:
    def __init__(self, price=120.0):
        self.buys = []
        self.sells = []
        self._price = price

    def buy(self, ticker, shares, price):
        self.buys.append((ticker, shares, price))
        return {"status": "filled", "fill_price": price, "shares": shares}

    def sell(self, ticker, shares, price):
        self.sells.append((ticker, shares, price))
        return {"status": "filled", "fill_price": price, "shares": shares}

    def get_price(self, ticker):
        return self._price


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def make_strategy(portfolio):
    s = object.__new__(SwingStrategy)
    s.portfolio = portfolio
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


@pytest.fixture(autouse=True)
def neutral_macro(monkeypatch):
    import analyzers.macro_context as mc
    fake = types.SimpleNamespace(size_modifier=lambda t: 1.0, bias_score=lambda: 0.0)
    monkeypatch.setattr(mc, "get_macro_context", lambda: fake)


def _bullish_analysis():
    return types.SimpleNamespace(
        ticker="NVDA", sentiment_score=0.95, confidence="HIGH",
        direction="BULLISH", recommendation="BUY", suggested_hold_days=10,
        entry_rationale="Starkes Momentum", key_catalysts=["Earnings-Beat"],
        risk_factors=["Bewertung"], target_price=160.0, sources_used=5,
    )


def test_too_few_sources_skips(tmp_path, monkeypatch):
    """Informationsdichte-Boden: 0 Quellen → kein Kauf (SKIP)."""
    p = make_portfolio(tmp_path, monkeypatch)
    strat = make_strategy(p)
    a = _bullish_analysis()
    a.sources_used = 0
    result = strat.evaluate("NVDA", a, current_price=120.0, regime="NEUTRAL")
    assert result.action == "SKIP"
    assert "Quellen" in result.reason


def test_bullish_signal_results_in_buy(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    strat = make_strategy(p)
    broker = FakeBroker()
    ex = TradeExecutor(p, broker, journal=None, notifier=_NullNotifier())

    analysis = _bullish_analysis()
    result = strat.evaluate("NVDA", analysis, current_price=120.0, regime="NEUTRAL")
    assert result.action == "BUY", result.reason

    out = ex.execute(result, analysis=analysis)
    assert "GEKAUFT" in out
    assert broker.buys and broker.buys[0][0] == "NVDA"
    pos = p.get_position("NVDA")
    assert pos is not None and pos.shares > 0
    assert pos.target_hold_days >= 3       # 0-Tage-Floor


def test_stop_loss_exit_chain(tmp_path, monkeypatch):
    p = make_portfolio(tmp_path, monkeypatch)
    p.open_position(Position(
        ticker="NVDA", shares=10, entry_price=120.0,
        entry_date="2026-06-10T00:00:00", stop_loss=110.0, take_profit=150.0,
        target_hold_days=10,
    ))
    strat = make_strategy(p)
    broker = FakeBroker()
    ex = TradeExecutor(p, broker, journal=None, notifier=_NullNotifier())

    # Kurs unter Stop-Loss → check_exits liefert SELL
    results = strat.check_exits({"NVDA": 105.0}, regime="NEUTRAL")
    assert any(r.action == "SELL" for r in results)

    for r in results:
        ex.execute(r, days_held=4)
    assert broker.sells and broker.sells[0][0] == "NVDA"
    assert p.get_position("NVDA") is None


def test_signal_queue_drains_into_buy(tmp_path, monkeypatch):
    """Vorgemerktes Queue-Signal wird ausgeführt, sobald ein Slot frei ist."""
    import portfolio.signal_queue as sq_mod
    monkeypatch.setattr(sq_mod, "DB_PATH", str(tmp_path / "data" / "sq.db"))
    from portfolio.signal_queue import SignalQueue
    from strategy.executor import process_signal_queue

    p = make_portfolio(tmp_path, monkeypatch)
    sq = SignalQueue()
    sq.enqueue(
        ticker="NVDA", sentiment_score=0.95, confidence="HIGH", target_price=160.0,
        direction="BULLISH", entry_rationale="vorgemerkt", key_catalysts=["k"],
        risk_factors=["r"], sources_used=5, sources_breakdown={"news": 5},
        suggested_hold_days=10,
    )
    assert sq.count_pending() == 1

    strat = make_strategy(p)
    strat.signal_queue = sq
    broker = FakeBroker(price=120.0)
    ex = TradeExecutor(p, broker, journal=None, notifier=_NullNotifier())

    msgs = process_signal_queue(strat, ex, broker, regime="NEUTRAL")
    assert msgs and "GEKAUFT" in msgs[0]
    assert p.get_position("NVDA") is not None
    assert sq.count_pending() == 0          # als ausgeführt markiert
