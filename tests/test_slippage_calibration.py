"""
Tests für scripts/slippage_calibration.py (Roadmap 5.3).

load_slippage_rows() liest data/order_log.db direkt (netzfrei, isolierte
Temp-DB über tmp_path) und berechnet die vorzeichenrichtige Slippage:
BUY teurer als market_price → positiv (schlechter), SELL billiger als
market_price → positiv (schlechter). Nur mode='ibkr' + status='filled' +
gesetztes market_price zählt.
"""
import sqlite3

import pytest

from scripts.slippage_calibration import load_slippage_rows


def _make_db(tmp_path, rows):
    db_path = tmp_path / "order_log.db"
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            ticker TEXT, action TEXT, mode TEXT, status TEXT,
            shares REAL, fill_price REAL, order_id INTEGER,
            partial INTEGER, reason TEXT, market_price REAL
        );
    """)
    for r in rows:
        con.execute(
            "INSERT INTO orders (ts, ticker, action, mode, status, shares, "
            "fill_price, partial, market_price) VALUES (?,?,?,?,?,?,?,?,?)",
            (r.get("ts", "2026-08-09T10:00:00"), r["ticker"], r["action"],
             r.get("mode", "ibkr"), r.get("status", "filled"),
             r.get("shares", 1.0), r.get("fill_price"),
             1 if r.get("partial") else 0, r.get("market_price")),
        )
    con.commit()
    con.close()
    return db_path


def test_missing_db_returns_empty_list(tmp_path):
    assert load_slippage_rows(tmp_path / "nope.db") == []


def test_buy_filled_above_reference_is_positive_slippage(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "AAPL", "action": "BUY", "fill_price": 101.0, "market_price": 100.0},
    ])
    rows = load_slippage_rows(db)
    assert len(rows) == 1
    assert rows[0]["slippage_pct"] == pytest.approx(1.0)


def test_sell_filled_below_reference_is_positive_slippage(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "AAPL", "action": "SELL", "fill_price": 99.0, "market_price": 100.0},
    ])
    rows = load_slippage_rows(db)
    assert rows[0]["slippage_pct"] == pytest.approx(1.0)


def test_sell_filled_above_reference_is_negative_slippage_ie_better(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "AAPL", "action": "SELL", "fill_price": 101.0, "market_price": 100.0},
    ])
    rows = load_slippage_rows(db)
    assert rows[0]["slippage_pct"] == pytest.approx(-1.0)


def test_excludes_paper_mode(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "AAPL", "action": "BUY", "fill_price": 101.0,
         "market_price": 100.0, "mode": "paper"},
    ])
    assert load_slippage_rows(db) == []


def test_excludes_rows_without_market_price(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "AAPL", "action": "BUY", "fill_price": 101.0, "market_price": None},
    ])
    assert load_slippage_rows(db) == []


def test_excludes_non_filled_status(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "AAPL", "action": "BUY", "fill_price": 0.0,
         "market_price": 100.0, "status": "cancelled"},
    ])
    assert load_slippage_rows(db) == []


def test_partial_fill_included_with_flag(tmp_path):
    db = _make_db(tmp_path, [
        {"ticker": "AAPL", "action": "BUY", "fill_price": 101.0,
         "market_price": 100.0, "partial": True},
    ])
    rows = load_slippage_rows(db)
    assert len(rows) == 1
    assert rows[0]["partial"] is True


