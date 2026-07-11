"""
Tests für den Live-SL-Cooldown (analyzers/sl_cooldown.py, verdrahtet Juli 2026):
nach einem verlustigen Clean-Stop-Loss (kein Partial-TP vorher) wird der Ticker
für config.sl_cooldown_days gesperrt; der Kauf-Gate in swing_strategy prüft die
Sperre, der Executor setzt sie nach dem gebuchten Exit.
"""
import json
import threading
import types
from datetime import datetime, timedelta, timezone

import pytest

import analyzers.sl_cooldown as slc_mod
import portfolio.portfolio as port_mod
from analyzers.sl_cooldown import StopLossCooldown
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import SwingStrategy, StrategyResult
from strategy.executor import TradeExecutor, _NullNotifier


@pytest.fixture
def slc_file(tmp_path, monkeypatch):
    f = str(tmp_path / "sl_cooldown.json")
    monkeypatch.setattr(slc_mod, "_FILE", f)
    return f


class FakeBroker:
    def __init__(self):
        self.sells = []
        self.buys = []

    def sell(self, ticker, shares, price):
        self.sells.append((ticker, shares, price))
        return {"status": "filled", "ticker": ticker, "shares": shares,
                "fill_price": price, "market_price": price}

    def buy(self, ticker, shares, price):
        self.buys.append((ticker, shares, price))
        return {"status": "filled", "ticker": ticker, "shares": shares,
                "fill_price": price, "market_price": price}


def make_portfolio(tmp_path, monkeypatch, capital=100_000.0):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def open_pos(p, ticker, entry, sl, tp, shares=10):
    p.open_position(Position(
        ticker=ticker, shares=shares, entry_price=entry,
        entry_date="2026-07-01T00:00:00", stop_loss=sl, take_profit=tp,
        target_hold_days=10,
    ))


# ── Klasse ───────────────────────────────────────────────────────────────────

def test_record_blocks_and_reason(slc_file):
    c = StopLossCooldown(cooldown_days=2)
    c.record("AAPL", 150.0)
    blocked, why = c.is_blocked("AAPL")
    assert blocked
    assert "SL-Sperre aktiv" in why
    # Case-insensitiv über upper()
    blocked2, _ = c.is_blocked("aapl")
    assert blocked2


def test_unknown_ticker_not_blocked(slc_file):
    c = StopLossCooldown(cooldown_days=2)
    assert c.is_blocked("MSFT") == (False, "")


def test_expired_entry_unblocks_and_cleans(slc_file):
    c = StopLossCooldown(cooldown_days=2)
    old_ts = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)).isoformat()
    with open(slc_file, "w") as f:
        json.dump({"NVDA": {"price": 100.0, "timestamp": old_ts}}, f)
    blocked, _ = c.is_blocked("NVDA")
    assert not blocked
    with open(slc_file) as f:
        assert "NVDA" not in json.load(f)


def test_default_days_from_config(slc_file):
    from config import config
    c = StopLossCooldown()
    assert c.cooldown_days == config.sl_cooldown_days


# ── Executor-Verdrahtung ─────────────────────────────────────────────────────

def _sell(ex, ticker, reason, price):
    res = StrategyResult("SELL", ticker, reason, price=price, shares=10)
    return ex.execute(res)


def test_executor_records_clean_losing_sl(tmp_path, monkeypatch, slc_file):
    p = make_portfolio(tmp_path, monkeypatch)
    open_pos(p, "AAPL", entry=150.0, sl=139.5, tp=180.0)
    ex = TradeExecutor(p, FakeBroker(), journal=None, notifier=_NullNotifier())
    out = _sell(ex, "AAPL", "Stop-Loss ausgelöst @ $139.50", 139.5)
    assert "VERKAUFT" in out
    blocked, why = StopLossCooldown(cooldown_days=2).is_blocked("AAPL")
    assert blocked, why


def test_executor_skips_cooldown_on_take_profit(tmp_path, monkeypatch, slc_file):
    p = make_portfolio(tmp_path, monkeypatch)
    open_pos(p, "MSFT", entry=100.0, sl=93.0, tp=120.0)
    ex = TradeExecutor(p, FakeBroker(), journal=None, notifier=_NullNotifier())
    _sell(ex, "MSFT", "Take-Profit erreicht @ $120.00", 120.0)
    assert StopLossCooldown(cooldown_days=2).is_blocked("MSFT") == (False, "")


def test_executor_skips_cooldown_on_winning_trailing_stop(tmp_path, monkeypatch, slc_file):
    """Trailing-Stop im Gewinn (SL über Einstieg) darf NICHT sperren —
    das war kein fallendes Messer, sondern ein gesicherter Gewinn."""
    p = make_portfolio(tmp_path, monkeypatch)
    open_pos(p, "NVDA", entry=100.0, sl=102.0, tp=125.0)
    ex = TradeExecutor(p, FakeBroker(), journal=None, notifier=_NullNotifier())
    _sell(ex, "NVDA", "Stop-Loss ausgelöst @ $102.00", 102.0)
    assert StopLossCooldown(cooldown_days=2).is_blocked("NVDA") == (False, "")


def test_executor_skips_cooldown_after_partial_tp(tmp_path, monkeypatch, slc_file):
    """SL nach Partial-TP (Breakeven-Stop) = kein Clean-SL → keine Sperre
    (gleiche Semantik wie backtesting/engine.py)."""
    p = make_portfolio(tmp_path, monkeypatch)
    open_pos(p, "AMZN", entry=100.0, sl=93.0, tp=130.0)
    p.update_partial_tp(ticker="AMZN", new_shares=7.5, new_stop_loss=101.0,
                        sell_shares=2.5, sell_price=110.0, pnl=25.0, new_count=1)
    ex = TradeExecutor(p, FakeBroker(), journal=None, notifier=_NullNotifier())
    _sell(ex, "AMZN", "Stop-Loss ausgelöst @ $99.00", 99.0)
    assert StopLossCooldown(cooldown_days=2).is_blocked("AMZN") == (False, "")


# ── Kauf-Gate (SwingStrategy.evaluate) ───────────────────────────────────────

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


@pytest.fixture
def neutral_macro(monkeypatch):
    import analyzers.macro_context as mc
    fake = types.SimpleNamespace(size_modifier=lambda t: 1.0, bias_score=lambda: 0.0)
    monkeypatch.setattr(mc, "get_macro_context", lambda: fake)


def _bullish_analysis(ticker="TSLA"):
    return types.SimpleNamespace(
        ticker=ticker, sentiment_score=0.95, confidence="HIGH",
        direction="BULLISH", recommendation="BUY", suggested_hold_days=10,
        entry_rationale="Starkes Momentum", key_catalysts=["Earnings-Beat"],
        risk_factors=["Bewertung"], target_price=260.0, sources_used=5,
    )


def test_buy_gate_blocks_during_cooldown(tmp_path, monkeypatch, slc_file, neutral_macro):
    p = make_portfolio(tmp_path, monkeypatch)
    strat = make_strategy(p)
    StopLossCooldown(cooldown_days=2).record("TSLA", 200.0)

    result = strat.evaluate("TSLA", _bullish_analysis(), current_price=210.0, regime="NEUTRAL")
    assert result.action == "SKIP"
    assert "SL-Sperre" in result.reason


def test_buy_gate_open_after_cooldown_expires(tmp_path, monkeypatch, slc_file, neutral_macro):
    """Abgelaufene Sperre darf den Kauf nicht mehr verhindern (das Ergebnis
    hängt dann an den übrigen Gates, nicht mehr an der SL-Sperre)."""
    p = make_portfolio(tmp_path, monkeypatch)
    strat = make_strategy(p)
    old_ts = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)).isoformat()
    with open(slc_file, "w") as f:
        json.dump({"TSLA": {"price": 200.0, "timestamp": old_ts}}, f)

    result = strat.evaluate("TSLA", _bullish_analysis(), current_price=210.0, regime="NEUTRAL")
    assert "SL-Sperre" not in (result.reason or "")
