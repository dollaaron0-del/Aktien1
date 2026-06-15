"""
Tests für broker/order_result.py – typisiertes, dict-kompatibles Order-Ergebnis.
"""
import pytest

from broker.order_result import OrderResult


def test_is_a_dict_and_isinstance():
    r = OrderResult.filled("AAPL", 10, 150.0, mode="paper")
    assert isinstance(r, dict)                 # Konsumenten mit isinstance(dict) ok
    assert r["status"] == "filled"
    assert r["shares"] == 10
    assert r["fill_price"] == 150.0
    assert r.get("mode") == "paper"


def test_is_filled_property():
    assert OrderResult.filled("X", 1, 1.0).is_filled is True
    assert OrderResult.error(reason="x").is_filled is False
    assert OrderResult.cancelled().is_filled is False


def test_typo_in_core_field_raises():
    with pytest.raises(TypeError):
        OrderResult("filled", fill_pirce=10.0)   # Tippfehler → sofort TypeError


def test_extra_fields_preserved():
    r = OrderResult.filled(
        "AAPL", 10, 150.0, mode="paper",
        extra={"slippage_usd": 0.5, "commission": 1.0, "market_price": 149.0},
    )
    assert r["slippage_usd"] == 0.5
    assert r["commission"] == 1.0
    assert r["market_price"] == 149.0


def test_error_and_cancelled_factories():
    e = OrderResult.error(ticker="T", reason="kaputt", mode="ibkr")
    assert e["status"] == "error"
    assert e["reason"] == "kaputt"
    c = OrderResult.cancelled(ticker="T", reason="timeout", mode="ibkr")
    assert c["status"] == "cancelled"
    assert c["reason"] == "timeout"


def test_optional_fields_omitted_when_default():
    r = OrderResult("filled", ticker="T", shares=1, fill_price=1.0)
    assert "reason" not in r          # leerer Grund nicht gespeichert
    assert "partial" not in r
    assert "usd_amount" not in r
