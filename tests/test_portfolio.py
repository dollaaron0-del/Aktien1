"""
Tests for portfolio/portfolio.py  (SQLite-backed implementation)
"""
import os
import pytest

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_portfolio(tmp_path, monkeypatch, capital=10_000.0):
    """Return a fresh Portfolio pointing at a temp SQLite DB."""
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=capital)


def make_position(ticker="AAPL", shares=10, entry_price=150.0,
                  stop_loss=138.0, take_profit=180.0, target_hold_days=14):
    from datetime import datetime, timezone
    return Position(
        ticker=ticker,
        shares=shares,
        entry_price=entry_price,
        entry_date=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        stop_loss=stop_loss,
        take_profit=take_profit,
        target_hold_days=target_hold_days,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_initial_cash(tmp_path, monkeypatch):
    """Cash equals initial_capital right after construction."""
    p = make_portfolio(tmp_path, monkeypatch, capital=10_000.0)
    assert p.cash == pytest.approx(10_000.0)


def test_open_position_reduces_cash(tmp_path, monkeypatch):
    """Cash decreases by entry_value when a position is opened."""
    p = make_portfolio(tmp_path, monkeypatch)
    pos = make_position(shares=10, entry_price=150.0)  # cost = 1500
    p.open_position(pos)
    assert p.cash == pytest.approx(10_000.0 - 1_500.0)


def test_open_position_insufficient_funds(tmp_path, monkeypatch):
    """ValueError is raised when the position cost exceeds available cash."""
    p = make_portfolio(tmp_path, monkeypatch, capital=100.0)
    pos = make_position(shares=10, entry_price=150.0)  # cost = 1500 > 100
    with pytest.raises(ValueError, match="Nicht genug Cash"):
        p.open_position(pos)


def test_close_position_returns_cash(tmp_path, monkeypatch):
    """Cash increases after closing a position."""
    p = make_portfolio(tmp_path, monkeypatch)
    pos = make_position(shares=10, entry_price=150.0)
    p.open_position(pos)
    cash_after_buy = p.cash

    p.close_position("AAPL", current_price=160.0)
    assert p.cash > cash_after_buy


def test_close_position_pnl_positive(tmp_path, monkeypatch):
    """pnl > 0 when exit price is above entry price."""
    p = make_portfolio(tmp_path, monkeypatch)
    pos = make_position(shares=10, entry_price=150.0)
    p.open_position(pos)
    pnl = p.close_position("AAPL", current_price=160.0)
    assert pnl > 0
    assert pnl == pytest.approx(10 * (160.0 - 150.0))


def test_close_position_pnl_negative(tmp_path, monkeypatch):
    """pnl < 0 when exit price is below entry price."""
    p = make_portfolio(tmp_path, monkeypatch)
    pos = make_position(shares=10, entry_price=150.0)
    p.open_position(pos)
    pnl = p.close_position("AAPL", current_price=140.0)
    assert pnl < 0
    assert pnl == pytest.approx(10 * (140.0 - 150.0))


def test_all_positions_returns_dict(tmp_path, monkeypatch):
    """all_positions() returns a dict, empty initially, populated after open."""
    p = make_portfolio(tmp_path, monkeypatch)
    assert isinstance(p.all_positions(), dict)
    assert len(p.all_positions()) == 0

    pos = make_position()
    p.open_position(pos)
    positions = p.all_positions()
    assert isinstance(positions, dict)
    assert "AAPL" in positions


def test_total_value_with_prices(tmp_path, monkeypatch):
    """total_value correctly sums cash + current market value of positions."""
    p = make_portfolio(tmp_path, monkeypatch)
    pos = make_position(shares=10, entry_price=150.0)  # cost = 1500
    p.open_position(pos)
    # cash = 10000 - 1500 = 8500; position value at 160 = 1600
    total = p.total_value({"AAPL": 160.0})
    assert total == pytest.approx(8_500.0 + 10 * 160.0)


def test_position_not_found_returns_none(tmp_path, monkeypatch):
    """get_position returns None for a ticker that was never opened."""
    p = make_portfolio(tmp_path, monkeypatch)
    result = p.get_position("TSLA")
    assert result is None
