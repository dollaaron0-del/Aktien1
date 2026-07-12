"""
Tests für Roadmap 1.13 — historische Kursreihen via IBKR reqHistoricalData
statt/neben yfinance (broker/ibkr_broker.py::get_history) und deren
Verdrahtung in TechnicalIndicators (Live-Pfad: swing_strategy._atr_vol_multiplier).
"""
import types
from datetime import date, timedelta

import pandas as pd
import pytest

import broker.ibkr_broker as ibm
from broker.ibkr_broker import IBKRBroker


def _make_bars(n=30, start_price=100.0):
    from ib_insync.objects import BarData
    bars = []
    d = date.today() - timedelta(days=n)
    for i in range(n):
        px = start_price + i * 0.5
        bars.append(BarData(
            date=d + timedelta(days=i),
            open=px, high=px + 1, low=px - 1, close=px,
            volume=1000 + i, average=px, barCount=10,
        ))
    return bars


class _FakeIBHist:
    def __init__(self, bars):
        self._bars = bars
        self.requested = []

    def isConnected(self):
        return True

    def qualifyContracts(self, contract):
        return [contract]

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting,
                           whatToShow, useRTH, formatDate):
        self.requested.append((contract.symbol, durationStr, barSizeSetting))
        return self._bars


def _broker(monkeypatch, bars):
    monkeypatch.setattr(IBKRBroker, "_connect", lambda self: False)
    monkeypatch.setattr(ibm, "_HISTORICAL_DATA", True)
    b = IBKRBroker()
    b._ib = _FakeIBHist(bars)
    b._connected = True
    b._active_account = "DU123"
    return b


def test_get_history_returns_yfinance_shaped_df(monkeypatch):
    bars = _make_bars(30)
    b = _broker(monkeypatch, bars)
    df = b.get_history("AAPL")
    assert list(df.columns[:5]) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 30
    assert b._ib.requested == [("AAPL", "3 M", "1 day")]


def test_get_history_flag_off_skips_ibkr(monkeypatch):
    bars = _make_bars(30)
    b = _broker(monkeypatch, bars)
    monkeypatch.setattr(ibm, "_HISTORICAL_DATA", False)
    called = {"yf": False}

    def _fake_yf(ticker, yf_period):
        called["yf"] = True
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr(IBKRBroker, "_yf_history", staticmethod(_fake_yf))
    b.get_history("AAPL")
    assert called["yf"] is True
    assert b._ib.requested == []


def test_get_history_empty_bars_falls_back_to_yfinance(monkeypatch):
    b = _broker(monkeypatch, [])
    called = {"yf": False}

    def _fake_yf(ticker, yf_period):
        called["yf"] = True
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr(IBKRBroker, "_yf_history", staticmethod(_fake_yf))
    b.get_history("AAPL")
    assert called["yf"] is True


def test_get_history_ibkr_exception_falls_back_to_yfinance(monkeypatch):
    b = _broker(monkeypatch, _make_bars(5))

    def _raise(*a, **kw):
        raise RuntimeError("Gateway weg")
    b._ib.reqHistoricalData = _raise

    called = {"yf": False}

    def _fake_yf(ticker, yf_period):
        called["yf"] = True
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr(IBKRBroker, "_yf_history", staticmethod(_fake_yf))
    b.get_history("AAPL")
    assert called["yf"] is True


def test_get_history_disconnected_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setattr(IBKRBroker, "_connect", lambda self: False)
    monkeypatch.setattr(ibm, "_HISTORICAL_DATA", True)
    b = IBKRBroker()
    b._ib = None
    b._connected = False

    called = {"yf": False}

    def _fake_yf(ticker, yf_period):
        called["yf"] = True
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr(IBKRBroker, "_yf_history", staticmethod(_fake_yf))
    b.get_history("AAPL")
    assert called["yf"] is True


# ── TechnicalIndicators: bevorzugt Broker, fällt bei Bedarf zurück ──────────

def test_technical_indicators_prefers_broker_history():
    from analyzers.technical_indicators import TechnicalIndicators

    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    df = pd.DataFrame({
        "Open": range(60), "High": range(60), "Low": range(60),
        "Close": [100.0 + i * 0.3 for i in range(60)],
        "Volume": [1000] * 60,
    }, index=idx)

    calls = []

    class _Broker:
        def get_history(self, ticker, yf_period="3mo"):
            calls.append((ticker, yf_period))
            return df

    snap = TechnicalIndicators().calculate("AAPL", broker=_Broker())
    assert snap is not None
    assert calls == [("AAPL", "3mo")]


def test_technical_indicators_falls_back_without_get_history():
    """Broker ohne get_history()-API (z.B. PaperBroker) darf nicht crashen."""
    from analyzers.technical_indicators import TechnicalIndicators

    snap = TechnicalIndicators().calculate("__NONEXISTENT_TICKER_XYZ__", broker=types.SimpleNamespace())
    assert snap is None  # yfinance liefert nichts Verwertbares für einen Fantasie-Ticker


def test_technical_indicators_broker_short_history_falls_back_to_yfinance(monkeypatch):
    """Liefert der Broker zu wenige Bars, greift der yfinance-Fallback in _history()."""
    import analyzers.technical_indicators as ti_mod
    from analyzers.technical_indicators import TechnicalIndicators

    short_df = pd.DataFrame({
        "Open": [1, 2], "High": [1, 2], "Low": [1, 2],
        "Close": [1.0, 2.0], "Volume": [10, 20],
    })

    class _Broker:
        def get_history(self, ticker, yf_period="3mo"):
            return short_df

    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    long_df = pd.DataFrame({
        "Open": range(60), "High": range(60), "Low": range(60),
        "Close": [50.0 + i * 0.2 for i in range(60)],
        "Volume": [500] * 60,
    }, index=idx)

    class _FakeYfTicker:
        def __init__(self, ticker):
            pass

        def history(self, period):
            return long_df

    monkeypatch.setattr(ti_mod.yf, "Ticker", _FakeYfTicker)
    snap = TechnicalIndicators().calculate("AAPL", broker=_Broker())
    assert snap is not None
    assert snap.price == pytest.approx(long_df["Close"].iloc[-1], abs=0.01)
