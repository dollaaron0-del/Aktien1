"""
Tests für broker-seitige GTC-Schutz-Stops (Roadmap 1.9, 11.7.2026).

Semantik: Der Broker-Stop ist ein NOTFALLNETZ für Bot-Ausfälle — die
Exit-Logik des Bots bleibt führend. Lebenszyklus, der hier festgenagelt wird:
- buy(stop_loss=…) platziert nach dem Fill einen GTC-Stop (SELL, ganze Stück).
- sell() räumt ruhende Stops VOR dem Verkauf weg (sonst würde der Stop nach
  dem Exit auf leerer Position auslösen → Short).
- update_stop() ersetzt den Stop (Partial-TP: Restmenge + neuer SL).
- sync_protective_stops() heilt fehlende Stops beim Start, fasst vorhandene
  nicht an.
- Der TradeExecutor reicht stop_loss nur an Broker durch, deren buy() den
  Parameter kennt, und alarmiert bei stop_placed=False.
"""
import types

import pytest

import broker.ibkr_broker as ibm
from broker.ibkr_broker import IBKRBroker


class _OrderStatus:
    def __init__(self, status="Submitted", filled=0.0, avg=0.0):
        self.status = status
        self.filled = filled
        self.avgFillPrice = avg


class _FakeTrade:
    _next_id = 100

    def __init__(self, contract, order, status="Submitted"):
        self.contract = contract
        self.order = order
        if getattr(order, "orderId", 0) in (0, None):
            order.orderId = _FakeTrade._next_id
            _FakeTrade._next_id += 1
        self.orderStatus = _OrderStatus(status=status)


class _FakeIB:
    """ib_insync-Ersatz: Market-Orders füllen sofort, Stops ruhen; führt eine
    Liste offener Orders wie der echte Gateway."""

    def __init__(self, market_fill_price=100.0):
        self.market_fill_price = market_fill_price
        self.placed = []          # alle placeOrder-Aufrufe (trade)
        self.cancelled = []       # gecancelte Orders
        self.open = []            # ruhende (nicht gefüllte/gecancelte) Trades

    def isConnected(self):
        return True

    def sleep(self, _s):
        pass

    def qualifyContracts(self, contract):
        return [contract]

    def placeOrder(self, contract, order):
        tr = _FakeTrade(contract, order)
        self.placed.append(tr)
        if getattr(order, "orderType", "MKT") == "MKT":
            tr.orderStatus.status = "Filled"
            tr.orderStatus.filled = order.totalQuantity
            tr.orderStatus.avgFillPrice = self.market_fill_price
        else:
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


def _broker(monkeypatch, fill_price=100.0):
    monkeypatch.setattr(IBKRBroker, "_connect", lambda self: False)
    monkeypatch.setattr(ibm, "_ORDER_TIMEOUT", 0.05)
    monkeypatch.setattr(ibm, "_SERVER_STOPS", True)
    b = IBKRBroker()
    b._ib = _FakeIB(market_fill_price=fill_price)
    b._connected = True
    b._active_account = "DU123"
    return b


def _stops(ib):
    return [t for t in ib.open if t.order.orderType == "STP"]


def test_buy_places_gtc_stop_after_fill(monkeypatch):
    b = _broker(monkeypatch)
    res = b.buy("AAPL", 10, 100.0, stop_loss=92.5)
    assert res["status"] == "filled"
    assert res["stop_placed"] is True
    stops = _stops(b._ib)
    assert len(stops) == 1
    o = stops[0].order
    assert o.action == "SELL" and o.totalQuantity == 10
    assert o.auxPrice == 92.5 and o.tif == "GTC"


def test_buy_without_stop_places_no_stop(monkeypatch):
    b = _broker(monkeypatch)
    res = b.buy("AAPL", 10, 100.0)
    assert res["status"] == "filled"
    assert "stop_placed" not in res
    assert _stops(b._ib) == []


def test_sell_cancels_resting_stop_first(monkeypatch):
    """Regression-Schutz: liegengebliebener Stop würde nach dem Exit shorten."""
    b = _broker(monkeypatch)
    b.buy("AAPL", 10, 100.0, stop_loss=92.5)
    assert len(_stops(b._ib)) == 1
    res = b.sell("AAPL", 10, 105.0)
    assert res["status"] == "filled"
    assert _stops(b._ib) == []          # Stop wurde geräumt
    assert len(b._ib.cancelled) == 1


def test_update_stop_replaces_qty_and_price(monkeypatch):
    b = _broker(monkeypatch)
    b.buy("AAPL", 10, 100.0, stop_loss=92.5)
    ok = b.update_stop("AAPL", 6, 97.0)   # Partial-TP: Restmenge 6, SL hoch
    assert ok is True
    stops = _stops(b._ib)
    assert len(stops) == 1
    assert stops[0].order.totalQuantity == 6
    assert stops[0].order.auxPrice == 97.0


def test_update_stop_with_zero_shares_only_cleans_up(monkeypatch):
    b = _broker(monkeypatch)
    b.buy("AAPL", 10, 100.0, stop_loss=92.5)
    assert b.update_stop("AAPL", 0, 0.0) is True
    assert _stops(b._ib) == []


def test_sync_places_missing_and_keeps_existing(monkeypatch):
    b = _broker(monkeypatch)
    b.buy("AAPL", 10, 100.0, stop_loss=92.5)   # AAPL hat schon einen Stop
    res = b.sync_protective_stops({"AAPL": (10, 92.5), "NVDA": (5, 180.0)})
    assert res == {"AAPL": True, "NVDA": True}
    stops = _stops(b._ib)
    assert len(stops) == 2                      # AAPL-Stop NICHT dupliziert
    nvda = [t for t in stops if t.contract.symbol == "NVDA"][0]
    assert nvda.order.totalQuantity == 5 and nvda.order.auxPrice == 180.0


def test_server_stops_flag_disables_everything(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(ibm, "_SERVER_STOPS", False)
    res = b.buy("AAPL", 10, 100.0, stop_loss=92.5)
    assert res["status"] == "filled" and "stop_placed" not in res
    assert _stops(b._ib) == []
    assert b.sync_protective_stops({"AAPL": (10, 92.5)}) is None


# ── Executor-Verdrahtung ─────────────────────────────────────────────────────

import portfolio.portfolio as port_mod
from portfolio.portfolio import Portfolio, Position
from strategy.swing_strategy import StrategyResult
from strategy.executor import TradeExecutor, _NullNotifier


class _LegacyBroker:
    """buy() OHNE stop_loss-Parameter – Executor darf ihn nicht durchreichen."""
    def __init__(self):
        self.calls = []

    def buy(self, ticker, shares, price):
        self.calls.append((ticker, shares, price))
        return {"status": "filled", "shares": shares, "fill_price": price}


class _StopBroker(_LegacyBroker):
    """buy() MIT stop_loss + update_stop – wie IBKR."""
    def __init__(self, stop_ok=True):
        super().__init__()
        self.stop_ok = stop_ok
        self.stop_kwargs = []
        self.updates = []

    def buy(self, ticker, shares, price, stop_loss=None):
        self.calls.append((ticker, shares, price))
        self.stop_kwargs.append(stop_loss)
        return {"status": "filled", "shares": shares, "fill_price": price,
                "stop_placed": self.stop_ok if stop_loss else None}

    def sell(self, ticker, shares, price):
        return {"status": "filled", "shares": shares, "fill_price": price}

    def update_stop(self, ticker, shares, stop_price):
        self.updates.append((ticker, shares, stop_price))
        return True


def _pf(tmp_path, monkeypatch):
    db_file = str(tmp_path / "data" / "portfolio.db")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", db_file)
    return Portfolio(initial_capital=100_000.0)


def test_executor_passes_stop_loss_when_supported(tmp_path, monkeypatch):
    p = _pf(tmp_path, monkeypatch)
    broker = _StopBroker()
    ex = TradeExecutor(p, broker, notifier=_NullNotifier())
    out = ex.execute(StrategyResult("BUY", "AAPL", "Test", shares=10, price=100.0,
                                    stop_loss=92.5, take_profit=120.0, hold_days=10))
    assert "GEKAUFT" in out
    assert broker.stop_kwargs == [92.5]


def test_executor_skips_stop_loss_for_legacy_broker(tmp_path, monkeypatch):
    p = _pf(tmp_path, monkeypatch)
    broker = _LegacyBroker()
    ex = TradeExecutor(p, broker, notifier=_NullNotifier())
    out = ex.execute(StrategyResult("BUY", "AAPL", "Test", shares=10, price=100.0,
                                    stop_loss=92.5, take_profit=120.0, hold_days=10))
    assert "GEKAUFT" in out
    assert broker.calls == [("AAPL", 10, 100.0)]


def test_executor_alerts_when_stop_not_placed(tmp_path, monkeypatch):
    p = _pf(tmp_path, monkeypatch)
    broker = _StopBroker(stop_ok=False)
    sent = []
    notifier = types.SimpleNamespace(
        send=lambda msg, **k: sent.append(msg),
        notify_buy=lambda **k: None, notify_sell=lambda **k: None,
    )
    ex = TradeExecutor(p, broker, notifier=notifier)
    out = ex.execute(StrategyResult("BUY", "AAPL", "Test", shares=10, price=100.0,
                                    stop_loss=92.5, take_profit=120.0, hold_days=10))
    assert "GEKAUFT" in out                      # Kauf selbst gilt
    assert any("Schutz-Stop" in m for m in sent)  # … aber Alarm ging raus


def test_executor_resyncs_stop_after_partial_tp(tmp_path, monkeypatch):
    p = _pf(tmp_path, monkeypatch)
    # Buch nach Engine-Partial-TP: Restmenge 6, SL bereits angehoben
    p.open_position(Position(
        ticker="AAPL", shares=6.0, entry_price=100.0,
        entry_date="2026-07-01T00:00:00", stop_loss=97.0, take_profit=120.0,
        target_hold_days=10,
    ))
    broker = _StopBroker()
    ex = TradeExecutor(p, broker, notifier=_NullNotifier())
    out = ex.execute(StrategyResult("SELL", "AAPL", "Partial-TP Stufe 1",
                                    shares=4.0, price=110.0))
    assert "Partial-TP" in out
    assert broker.updates == [("AAPL", 6.0, 97.0)]
