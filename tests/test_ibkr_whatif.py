"""Tests für den whatIf-Margin-Check vor Orders (Roadmap 1.14).

Semantik: Nur ein KLARES Nein von IBKR (DBL_MAX-Sentinel in den
Margin-Feldern oder Init-Margin > Eigenkapital nach der Order) blockt die
Einreichung — dann kommt ein typisierter OrderResult.error zurück und
placeOrder wird NIE gerufen. Alles andere (leere Antwort, Exception,
Flag aus) ist fail-open: die Order geht normal raus, der echte Gateway
lehnt zur Not selbst ab.
"""
import pytest

import broker.ibkr_broker as ibm
from broker.ibkr_broker import IBKRBroker

from tests.test_ibkr_stops import _FakeIB, _broker


class _WhatIfState:
    def __init__(self, init_change="100.0", init_after="500.0",
                 equity_after="10000.0"):
        self.initMarginChange = init_change
        self.initMarginAfter = init_after
        self.equityWithLoanAfter = equity_after


def _with_whatif(monkeypatch, state, record=None):
    b = _broker(monkeypatch)
    monkeypatch.setattr(ibm, "_WHATIF_CHECK", True)

    def _whatif(contract, order):
        if record is not None:
            record.append(order)
        if isinstance(state, Exception):
            raise state
        return state

    b._ib.whatIfOrder = _whatif
    return b


def test_ok_whatif_lets_order_through(monkeypatch):
    b = _with_whatif(monkeypatch, _WhatIfState())
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "filled"
    assert len(b._ib.placed) == 1


def test_sentinel_blocks_order(monkeypatch):
    st = _WhatIfState(init_change="1.7976931348623157E308")
    b = _with_whatif(monkeypatch, st)
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "error"
    assert "Margin-Sentinel" in res["reason"]
    assert b._ib.placed == []           # nie eingereicht


def test_margin_exceeds_equity_blocks_order(monkeypatch):
    st = _WhatIfState(init_after="15000.0", equity_after="10000.0")
    b = _with_whatif(monkeypatch, st)
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "error"
    assert "Eigenkapital" in res["reason"]
    assert b._ib.placed == []


def test_sell_also_checked(monkeypatch):
    st = _WhatIfState(init_change="1.7976931348623157E308")
    b = _with_whatif(monkeypatch, st)
    res = b.sell("AAPL", 10, 100.0)
    assert res["status"] == "error"
    assert b._ib.placed == []


def test_empty_state_fails_open(monkeypatch):
    b = _with_whatif(monkeypatch, None)
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "filled"


def test_unparseable_fields_fail_open(monkeypatch):
    st = _WhatIfState(init_change="", init_after="n/a", equity_after=None)
    b = _with_whatif(monkeypatch, st)
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "filled"


def test_whatif_exception_fails_open(monkeypatch):
    b = _with_whatif(monkeypatch, RuntimeError("Gateway weg"))
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "filled"


def test_missing_whatif_method_fails_open(monkeypatch):
    # _FakeIB ohne whatIfOrder-Attribut → AttributeError → fail-open
    b = _broker(monkeypatch)
    monkeypatch.setattr(ibm, "_WHATIF_CHECK", True)
    assert not hasattr(b._ib, "whatIfOrder")
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "filled"


def test_flag_off_skips_whatif_entirely(monkeypatch):
    calls = []
    st = _WhatIfState(init_change="1.7976931348623157E308")  # würde blocken
    b = _with_whatif(monkeypatch, st, record=calls)
    monkeypatch.setattr(ibm, "_WHATIF_CHECK", False)
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "filled"
    assert calls == []                  # whatIf nie gerufen


def test_thousands_separator_parsed(monkeypatch):
    st = _WhatIfState(init_after="15,000.0", equity_after="10,000.0")
    b = _with_whatif(monkeypatch, st)
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "error"
