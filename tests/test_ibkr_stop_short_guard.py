"""
Tests für den Anti-Short-Schutz der broker-seitigen GTC-Stops (22.8.2026).

Befund im Live-Paper-Konto: Ein Schutz-Stop wurde blind über die BUCH-Menge
gelegt. Lief das Buch gegenüber IBKR auseinander, war die ruhende Order größer
als der reale Bestand — beim Auslösen drehte sie das Konto short, exakt um die
Differenz. Nachweisbar an acht Positionen, u.a.:

    S    : IBKR   1 Stk, Buch 695    →  -694
    LRCX : IBKR   1 Stk, Buch  55.25 →   -54
    SSNC : IBKR  13 Stk, Buch  23.4  →   -10
    CLSK : IBKR   0 Stk, Buch 284    →  -284

Der Anti-Short-Schutz im Executor griff nur für Markt-Verkäufe; die ruhende
Stop-Order umging ihn und richtete den Schaden zeitversetzt an.

Netzfrei: ib_insync durch einen Fake ersetzt.
"""
import types

import pytest

import broker.ibkr_broker as ibm
from broker.ibkr_broker import IBKRBroker


class _OrderStatus:
    def __init__(self, status="Submitted"):
        self.status = status
        self.filled = 0.0
        self.avgFillPrice = 0.0


class _FakeTrade:
    _next_id = 500

    def __init__(self, contract, order, status="Submitted"):
        self.contract = contract
        self.order = order
        if getattr(order, "orderId", 0) in (0, None):
            order.orderId = _FakeTrade._next_id
            _FakeTrade._next_id += 1
        self.orderStatus = _OrderStatus(status)


class _FakePos:
    def __init__(self, symbol, shares):
        self.contract = types.SimpleNamespace(symbol=symbol)
        self.position = shares


class _FakeIB:
    """Wie der Fake in test_ibkr_stops.py, aber MIT positions()-Unterstützung —
    genau die Information, die der Stop-Pfad bisher nicht konsultiert hat."""

    def __init__(self, held=None):
        self._held = dict(held or {})
        self.placed = []
        self.cancelled = []
        self.open = []
        self.positions_raise = False

    def isConnected(self):
        return True

    def sleep(self, _s):
        pass

    def qualifyContracts(self, contract):
        return [contract]

    def managedAccounts(self):
        return ["DU123"]

    def positions(self, _account=None):
        if self.positions_raise:
            raise RuntimeError("Verbindung weg")
        return [_FakePos(s, q) for s, q in self._held.items()]

    def placeOrder(self, contract, order):
        tr = _FakeTrade(contract, order)
        self.placed.append(tr)
        self.open.append(tr)
        return tr

    def cancelOrder(self, order):
        self.cancelled.append(order)
        for tr in self.open:
            if tr.order is order:
                tr.orderStatus.status = "Cancelled"
        self.open = [t for t in self.open if t.orderStatus.status != "Cancelled"]

    def reqAllOpenOrders(self):
        return list(self.open)

    def openTrades(self):
        return list(self.open)

    def reqContractDetails(self, contract):
        return [types.SimpleNamespace(minTick=0.01, marketRuleIds="")]

    def reqMarketRule(self, _rid):
        return [types.SimpleNamespace(lowEdge=0.0, increment=0.01)]


def _broker(monkeypatch, held=None):
    monkeypatch.setattr(IBKRBroker, "_connect", lambda self: False)
    monkeypatch.setattr(ibm, "_ORDER_TIMEOUT", 0.05)
    monkeypatch.setattr(ibm, "_SERVER_STOPS", True)
    monkeypatch.setattr(ibm, "_MARKET_HOURS_GATE", False)
    b = IBKRBroker()
    b._ib = _FakeIB(held=held)
    b._connected = True
    b._active_account = "DU123"
    return b


def _stops(ib):
    return [t for t in ib.open if t.order.orderType == "STP"]


# ── _stop_qty_cap: die reine Entscheidung ──────────────────────────────────

def test_cap_leaves_covered_quantity_untouched(monkeypatch):
    b = _broker(monkeypatch, held={"AAPL": 100})
    assert b._stop_qty_cap("AAPL", 100) == 100


def test_cap_limits_to_actually_held(monkeypatch):
    """SSNC-Fall: 13 real, 23 im Buch → Stop darf nur 13 groß sein."""
    b = _broker(monkeypatch, held={"SSNC": 13})
    assert b._stop_qty_cap("SSNC", 23) == 13


def test_cap_refuses_stop_when_flat(monkeypatch):
    """CLSK-Fall: gar keine Position → None = überhaupt keinen Stop legen."""
    b = _broker(monkeypatch, held={})
    assert b._stop_qty_cap("CLSK", 284) is None


def test_cap_refuses_stop_when_already_short(monkeypatch):
    """Ein bereits negativer Bestand darf nie noch einen SELL-Stop bekommen."""
    b = _broker(monkeypatch, held={"S": -694})
    assert b._stop_qty_cap("S", 695) is None


def test_cap_is_fail_open_when_positions_unknown(monkeypatch):
    """Bestand nicht ermittelbar → altes Verhalten (lieber Stop mit Buchmenge
    als eine ungeschützte Position)."""
    b = _broker(monkeypatch, held={"AAPL": 100})
    b._ib.positions_raise = True
    assert b._stop_qty_cap("AAPL", 999) == 999


def test_cap_floors_fractional_holdings(monkeypatch):
    """LRCX-Fall: Buch 55.25, real 1 → auf ganze Stück abgerundet."""
    b = _broker(monkeypatch, held={"LRCX": 1.9})
    assert b._stop_qty_cap("LRCX", 55) == 1


# ── _place_stop: Wirkung auf die tatsächliche Order ────────────────────────

def test_place_stop_caps_order_quantity(monkeypatch):
    b = _broker(monkeypatch, held={"SSNC": 13})
    contract = b._stock_contract("SSNC")
    assert b._place_stop(contract, 23.4, 80.0) is True
    stops = _stops(b._ib)
    assert len(stops) == 1
    assert stops[0].order.totalQuantity == 13


def test_place_stop_refused_without_position(monkeypatch):
    """Kern-Regression: kein Bestand → gar keine Order, statt einer, die
    beim Auslösen shortet."""
    b = _broker(monkeypatch, held={})
    contract = b._stock_contract("CLSK")
    assert b._place_stop(contract, 284, 12.0) is False
    assert _stops(b._ib) == []


def test_buy_then_stop_is_not_oversized(monkeypatch):
    """Der reguläre Weg bleibt unverändert, solange Buch und Broker übereinstimmen."""
    b = _broker(monkeypatch, held={"AAPL": 10})
    contract = b._stock_contract("AAPL")
    assert b._place_stop(contract, 10, 92.5) is True
    assert _stops(b._ib)[0].order.totalQuantity == 10


# ── _cancel_oversized_stops: Altlasten abräumen ────────────────────────────

def _add_stop(b, symbol, qty, price=100.0):
    from ib_insync import StopOrder
    o = StopOrder("SELL", qty, price, tif="GTC")
    return b._ib.placeOrder(b._stock_contract(symbol), o)


def test_oversized_orphan_stop_is_cancelled(monkeypatch):
    """SAP-Fall: drei Alt-Stops über 105/276/406 bei 1 real gehaltenen Stück."""
    b = _broker(monkeypatch, held={"SAP": 1})
    for q in (105, 276, 406):
        _add_stop(b, "SAP", q)
    assert b._cancel_oversized_stops() == 3
    assert _stops(b._ib) == []


def test_covered_stop_survives_the_sweep(monkeypatch):
    """Gegenprobe: gedeckte Stops dürfen NICHT abgeräumt werden — sonst nimmt
    die Reparatur echten Positionen den Schutz weg."""
    b = _broker(monkeypatch, held={"RHM": 16})
    _add_stop(b, "RHM", 14)
    assert b._cancel_oversized_stops() == 0
    assert len(_stops(b._ib)) == 1


def test_sweep_cancels_only_the_oversized_one(monkeypatch):
    """RHM-Realfall: 14er-Stop bleibt, 35er-Stop fliegt."""
    b = _broker(monkeypatch, held={"RHM": 16})
    _add_stop(b, "RHM", 14)
    _add_stop(b, "RHM", 35)
    assert b._cancel_oversized_stops() == 1
    verbleibend = [t.order.totalQuantity for t in _stops(b._ib)]
    assert verbleibend == [14]


def test_sweep_does_nothing_when_positions_unknown(monkeypatch):
    """Ohne belastbaren Bestand darf nichts storniert werden – ein
    Verbindungsabriss dürfte sonst alle Schutz-Stops abräumen."""
    b = _broker(monkeypatch, held={"SAP": 1})
    _add_stop(b, "SAP", 406)
    b._ib.positions_raise = True
    assert b._cancel_oversized_stops() == 0
    assert len(_stops(b._ib)) == 1


def test_sweep_ignores_non_stop_orders(monkeypatch):
    from ib_insync import LimitOrder
    b = _broker(monkeypatch, held={"AAPL": 0})
    b._ib.placeOrder(b._stock_contract("AAPL"), LimitOrder("SELL", 50, 100.0))
    assert b._cancel_oversized_stops() == 0


def test_sync_protective_stops_sweeps_first(monkeypatch):
    """Die periodische Heilung räumt Altlasten mit ab — dort kommt der Sweep
    im Live-Betrieb überhaupt zum Tragen."""
    b = _broker(monkeypatch, held={"SAP": 1, "AAPL": 10})
    _add_stop(b, "SAP", 406)
    res = b.sync_protective_stops({"AAPL": (10, 92.5)})
    assert res["AAPL"] is True
    qtys = sorted(t.order.totalQuantity for t in _stops(b._ib))
    assert qtys == [10]          # SAP-Altlast weg, AAPL frisch geschützt
