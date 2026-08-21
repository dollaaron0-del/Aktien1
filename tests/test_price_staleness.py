"""
Tests für den Stale-Close-Fallback in _ticker_price/get_price/get_prices
(25.7.2026).

Auslöser: SAP.DE hatte diese Session keinen einzigen Live-Tick (marketPrice
und last beide -1/ungültig). Der Bot fiel auf td.close zurück – ein Feld, das
im Streaming-Snapshot-Cache tagealt hängen bleiben kann, ohne dass sich das an
irgendeinem Zeitstempel erkennen lässt (td.time zeigt nur den Empfang des
Snapshots, nicht das Alter des Preises). Real lieferte close 128,32 (Schluss
von vor zwei Handelstagen) statt des echten Freitagsschlusses 141,16 – der Bot
hielt das für einen Stop-Loss-Bruch und versuchte wiederholt zu verkaufen,
obwohl die Position komfortabel im Plus lag.

Semantik: marketPrice()/last sind echte Live-Ticks dieser Session und bleiben
die erste Wahl. Fehlen beide, fragt _historical_close() aktiv per
reqHistoricalData nach (bewiesen korrekt) statt dem Snapshot-Cache zu
vertrauen. Erst wenn auch das fehlschlägt, ist td.close der letzte Ausweg
(besser ein möglicherweise alter Preis als gar keiner).
"""
import types

import pytest

import broker.ibkr_broker as ibm
from broker.ibkr_broker import IBKRBroker, _ticker_price


class _Ticker:
    def __init__(self, market_price=-1.0, last=-1.0, close=-1.0):
        self._market_price = market_price
        self.last = last
        self.close = close

    def marketPrice(self):
        return self._market_price


class _Bar:
    def __init__(self, close):
        self.close = close


class _FakeIB:
    """Nur die Methoden, die der Preis-Pfad braucht."""

    def __init__(self):
        self.connected = True
        self.hist_bars = {}          # symbol -> [_Bar, …]
        self.hist_calls = []         # Aufruf-Protokoll
        self.mkt_data = {}           # symbol -> _Ticker

    def isConnected(self):
        return self.connected

    def sleep(self, _s):
        pass

    def qualifyContracts(self, *contracts):
        return list(contracts)

    def reqMktData(self, contract, *_a, **_kw):
        return self.mkt_data.get(contract.symbol, _Ticker())

    def cancelMktData(self, _contract):
        pass

    def reqHistoricalData(self, contract, **_kw):
        self.hist_calls.append(contract.symbol)
        return self.hist_bars.get(contract.symbol, [])


def _broker(monkeypatch):
    monkeypatch.setattr(IBKRBroker, "_connect", lambda self: False)
    b = IBKRBroker()
    b._ib = _FakeIB()
    b._connected = True
    return b


# ── _ticker_price: reine Funktions-Semantik ─────────────────────────────────

def test_live_tick_wins_over_fallback():
    td = _Ticker(market_price=141.16, close=128.32)
    assert _ticker_price(td, stale_close_fallback=lambda: pytest.fail("nicht nötig")) == 141.16


def test_last_wins_when_market_price_invalid():
    td = _Ticker(market_price=-1.0, last=141.16, close=128.32)
    assert _ticker_price(td, stale_close_fallback=lambda: pytest.fail("nicht nötig")) == 141.16


def test_falls_back_to_historical_close_when_no_live_tick():
    """Der Kernfall: kein marketPrice, kein last → td.close wird NICHT
    blind vertraut, stattdessen der Fallback befragt."""
    td = _Ticker(market_price=-1.0, last=-1.0, close=128.32)
    assert _ticker_price(td, stale_close_fallback=lambda: 141.16) == 141.16


def test_stale_close_only_used_if_fallback_also_fails():
    td = _Ticker(market_price=-1.0, last=-1.0, close=128.32)
    assert _ticker_price(td, stale_close_fallback=lambda: None) == 128.32


def test_fallback_exception_does_not_crash():
    td = _Ticker(market_price=-1.0, last=-1.0, close=128.32)

    def _boom():
        raise RuntimeError("reqHistoricalData kaputt")

    assert _ticker_price(td, stale_close_fallback=_boom) == 128.32


def test_no_price_available_anywhere():
    td = _Ticker(market_price=-1.0, last=-1.0, close=-1.0)
    assert _ticker_price(td, stale_close_fallback=lambda: None) is None


# ── get_price/get_prices: End-to-End über den Broker ────────────────────────

def test_get_price_uses_historical_close_on_no_live_tick(monkeypatch):
    """Regression: get_price('SAP.DE') lieferte real 128,32 statt 141,16."""
    b = _broker(monkeypatch)
    b._ib.mkt_data["SAP"] = _Ticker(market_price=-1.0, last=-1.0, close=128.32)
    b._ib.hist_bars["SAP"] = [_Bar(close=141.16)]
    assert b.get_price("SAP.DE") == 141.16


def test_get_price_prefers_live_tick_over_historical(monkeypatch):
    b = _broker(monkeypatch)
    b._ib.mkt_data["DWS"] = _Ticker(market_price=70.6, last=70.6, close=70.6)
    b._ib.hist_bars["DWS"] = [_Bar(close=999.0)]   # dürfte nie abgefragt werden
    assert b.get_price("DWS.DE") == 70.6
    assert "DWS" not in b._ib.hist_calls


def test_get_prices_batch_applies_fallback_per_ticker(monkeypatch):
    """Regression aus dem realen Vorfall: SAP fehlt der Live-Tick, DWS und
    RHM haben ihn – jeder Ticker bekommt den für ihn richtigen Preis."""
    b = _broker(monkeypatch)
    b._ib.mkt_data["SAP"] = _Ticker(market_price=-1.0, last=-1.0, close=128.32)
    b._ib.hist_bars["SAP"] = [_Bar(close=141.16)]
    b._ib.mkt_data["DWS"] = _Ticker(market_price=70.6, last=70.6, close=70.6)
    b._ib.mkt_data["RHM"] = _Ticker(market_price=1035.8, last=1035.8, close=1035.8)

    prices = b.get_prices(["SAP.DE", "DWS.DE", "RHM.DE"])

    assert prices == {"SAP.DE": 141.16, "DWS.DE": 70.6, "RHM.DE": 1035.8}


def test_historical_close_cached_within_ttl(monkeypatch):
    """Mehrere Preisabfragen kurz hintereinander (typisch: Portfolio-weiter
    get_prices-Aufruf) lösen nicht pro Aufruf eine neue reqHistoricalData-
    Runde aus."""
    b = _broker(monkeypatch)
    b._ib.mkt_data["SAP"] = _Ticker(market_price=-1.0, last=-1.0, close=128.32)
    b._ib.hist_bars["SAP"] = [_Bar(close=141.16)]

    b.get_price("SAP.DE")
    b.get_price("SAP.DE")

    assert b._ib.hist_calls.count("SAP") == 1


def test_historical_close_empty_bars_is_none(monkeypatch):
    b = _broker(monkeypatch)
    b._ib.mkt_data["SAP"] = _Ticker(market_price=-1.0, last=-1.0, close=128.32)
    b._ib.hist_bars["SAP"] = []   # IBKR liefert nichts zurück
    assert b.get_price("SAP.DE") == 128.32   # letzter Ausweg: der alte Wert
