"""
Tests für den IBKR-Order-Fill-Pfad – speziell die Timeout-/Cancel-Härtung.

Ziel: eine getimeoutete Market-Order darf NICHT als "pending" offen bleiben
(sonst füllt der Broker später und das Buch weiß nichts → umgekehrtes Phantom).
Sie wird gecancelt; ein Teilfill wird mit der tatsächlich gefüllten Menge gebucht.
"""
import broker.ibkr_broker as ibm
from broker.ibkr_broker import IBKRBroker


class _OrderStatus:
    def __init__(self, status="Submitted", filled=0.0, avg=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avg


class _Order:
    orderId = 42
    totalQuantity = 10


class _Trade:
    def __init__(self, status):
        self.order = _Order()
        self.orderStatus = status


class _FakeIB:
    """Minimaler ib_insync-Ersatz. on_cancel(status) wird beim Cancel ausgeführt,
    um den finalen Order-Stand zu simulieren."""
    def __init__(self, trade, on_cancel=None):
        self._trade = trade
        self._on_cancel = on_cancel
        self.cancelled = False

    def isConnected(self):
        return True

    def sleep(self, _s):
        pass

    def placeOrder(self, contract, order):
        return self._trade

    def cancelOrder(self, order):
        self.cancelled = True
        if self._on_cancel:
            self._on_cancel(self._trade.orderStatus)


class _Contract:
    symbol = "TEST"


def _broker(monkeypatch, trade, on_cancel=None):
    monkeypatch.setattr(IBKRBroker, "_connect", lambda self: False)
    monkeypatch.setattr(ibm, "_ORDER_TIMEOUT", 0.05)
    b = IBKRBroker()
    b._ib = _FakeIB(trade, on_cancel)
    b._connected = True
    b._active_account = "DU123"
    return b


def test_timeout_unfilled_is_cancelled_not_pending(monkeypatch):
    trade = _Trade(_OrderStatus(status="Submitted", filled=0.0))

    def _cancel(st):
        st.status = "Cancelled"
    b = _broker(monkeypatch, trade, on_cancel=_cancel)
    res = b._place_order(_Contract(), "BUY", 10)
    assert res["status"] == "cancelled"          # NICHT "pending"
    assert b._ib.cancelled is True


def test_timeout_partial_fill_books_filled_qty(monkeypatch):
    trade = _Trade(_OrderStatus(status="Submitted", filled=0.0))

    def _cancel(st):
        st.status = "Cancelled"
        st.filled = 6.0                          # 6 von 10 wurden gefüllt
        st.avgFillPrice = 20.5
    b = _broker(monkeypatch, trade, on_cancel=_cancel)
    res = b._place_order(_Contract(), "BUY", 10)
    assert res["status"] == "filled"
    assert res["shares"] == 6.0
    assert res["fill_price"] == 20.5
    assert res.get("partial") is True


def test_fill_within_timeout(monkeypatch):
    # Order füllt sofort → normaler Filled-Pfad (kein Cancel)
    trade = _Trade(_OrderStatus(status="Filled", filled=10.0, avg=21.0))
    b = _broker(monkeypatch, trade)
    res = b._place_order(_Contract(), "BUY", 10)
    assert res["status"] == "filled"
    assert res["shares"] == 10
    assert res["fill_price"] == 21.0
    assert b._ib.cancelled is False


# ── reference_price → market_price (Roadmap 5.3: Slippage-Kalibrierung) ──────

def test_reference_price_flows_into_market_price_on_immediate_fill(monkeypatch):
    trade = _Trade(_OrderStatus(status="Filled", filled=10.0, avg=21.0))
    b = _broker(monkeypatch, trade)
    res = b._place_order(_Contract(), "BUY", 10, reference_price=20.80)
    assert res["market_price"] == 20.80


def test_reference_price_flows_into_market_price_on_race_fill(monkeypatch):
    trade = _Trade(_OrderStatus(status="Submitted", filled=0.0))

    def _cancel(st):
        st.status = "Filled"
        st.filled = 10.0
        st.avgFillPrice = 20.5
    b = _broker(monkeypatch, trade, on_cancel=_cancel)
    res = b._place_order(_Contract(), "BUY", 10, reference_price=20.0)
    assert res["market_price"] == 20.0


def test_reference_price_flows_into_market_price_on_partial_fill(monkeypatch):
    trade = _Trade(_OrderStatus(status="Submitted", filled=0.0))

    def _cancel(st):
        st.status = "Cancelled"
        st.filled = 6.0
        st.avgFillPrice = 20.5
    b = _broker(monkeypatch, trade, on_cancel=_cancel)
    res = b._place_order(_Contract(), "BUY", 10, reference_price=20.0)
    assert res["market_price"] == 20.0


def test_no_reference_price_means_no_market_price_key(monkeypatch):
    """Rückwärtskompatibel: Aufrufer ohne reference_price (z.B. bisheriger
    buy_crypto/sell_crypto-Pfad) bekommen kein market_price=None-Rauschen."""
    trade = _Trade(_OrderStatus(status="Filled", filled=10.0, avg=21.0))
    b = _broker(monkeypatch, trade)
    res = b._place_order(_Contract(), "BUY", 10)
    assert "market_price" not in res


def test_get_filled_limit_orders_returns_filled(monkeypatch):
    filled = _Trade(_OrderStatus(status="Filled", filled=5.0, avg=12.0))
    filled.order.orderId = 100
    pending = _Trade(_OrderStatus(status="Submitted", filled=0.0))
    pending.order.orderId = 101

    class _IB(_FakeIB):
        def trades(self):
            return [filled, pending]
        def reqCompletedOrders(self, apiOnly=True):
            pass

    monkeypatch.setattr(IBKRBroker, "_connect", lambda self: False)
    b = IBKRBroker()
    b._ib = _IB(filled)
    b._connected = True
    out = b.get_filled_limit_orders([100, 101])
    assert len(out) == 1
    assert out[0]["order_id"] == 100
    assert out[0]["shares"] == 5.0
    assert out[0]["fill_price"] == 12.0
