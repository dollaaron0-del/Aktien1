"""
Tests für das Handelszeiten-Gate vor Market-Orders (25.7.2026).

Auslöser: An einem Samstag reichte der Bot dreimal eine SELL-Market-Order für
SAP.DE ein. IBKR cancelte sie sofort (Error 10349), der Bot deutete das als
Fill-Timeout und meldete "SELL fehlgeschlagen" — dabei war schlicht die Börse
zu. Weil sell() den GTC-Schutz-Stop VOR dem Verkauf wegräumt, stand die
Position danach ungeschützt da.

Semantik, die hier festgenagelt wird:
- Ist die zuständige Börse geschlossen, geht gar keine Order raus (typisierter
  OrderResult.error, placeOrder wird NIE gerufen).
- Bei SELL wird das VOR dem Stop-Cancel geprüft — der Schutz-Stop bleibt liegen.
- Der Handelsplatz kommt aus dem Ticker-Suffix (SAP.DE → XETRA, AAPL → NYSE).
- Fail-open: ist der Kalender nicht befragbar, blockiert das Gate nichts.
"""
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

import pytest

import broker.ibkr_broker as ibm
from analyzers.market_schedule import (
    EXCHANGE_DEFS,
    exchange_for_ticker,
    is_exchange_open,
    market_closed_reason,
)

from tests.test_ibkr_stops import _broker, _stops


# ── Ticker → Börsenplatz ─────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker,expected", [
    ("SAP.DE",  "XETRA"),
    ("RHM.DE",  "XETRA"),
    ("AIR.PA",  "XETRA"),   # Euronext läuft über den XETRA-Kalender
    ("ASML.AS", "XETRA"),
    ("SHEL.L",  "LSE"),
    ("AAPL",    "NYSE"),
    ("SAP",     "NYSE"),    # US-ADR – anderer Kalender als SAP.DE
    ("",        "NYSE"),
])
def test_exchange_for_ticker(ticker, expected):
    assert exchange_for_ticker(ticker) == expected


# ── Kalender ─────────────────────────────────────────────────────────────────

def _freeze(monkeypatch, iso_local: str, tz: str):
    """Friert datetime.now(tz) im market_schedule-Modul auf einen Zeitpunkt ein."""
    import analyzers.market_schedule as ms
    target = datetime.fromisoformat(iso_local).replace(tzinfo=ZoneInfo(tz))

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return target.astimezone(tz) if tz else target.replace(tzinfo=None)

    monkeypatch.setattr(ms, "datetime", _FrozenDT)


def test_saturday_closes_every_exchange(monkeypatch):
    # 25.7.2026 war ein Samstag – der Tag des Vorfalls.
    assert date(2026, 7, 25).weekday() == 5
    _freeze(monkeypatch, "2026-07-25T11:00:00", "Europe/Berlin")
    assert is_exchange_open("XETRA") is False
    reason = market_closed_reason("SAP.DE")
    assert reason and "Wochenende" in reason


def test_weekday_within_session_is_open(monkeypatch):
    _freeze(monkeypatch, "2026-07-24T11:00:00", "Europe/Berlin")   # Freitag
    assert is_exchange_open("XETRA") is True
    assert market_closed_reason("SAP.DE") is None


def test_xetra_afternoon_is_still_open(monkeypatch):
    """XETRA schließt 17:30, nicht 16:00 — die alte Pauschale hätte legitime
    Nachmittags-Orders auf deutschen Titeln blockiert."""
    _freeze(monkeypatch, "2026-07-24T17:00:00", "Europe/Berlin")
    assert is_exchange_open("XETRA") is True
    assert EXCHANGE_DEFS["XETRA"]["close"] == dtime(17, 30)


def test_before_open_is_closed(monkeypatch):
    _freeze(monkeypatch, "2026-07-24T07:30:00", "Europe/Berlin")
    reason = market_closed_reason("SAP.DE")
    assert reason and "Handelszeit" in reason


def test_holiday_is_closed(monkeypatch):
    _freeze(monkeypatch, "2026-05-01T11:00:00", "Europe/Berlin")   # Tag der Arbeit
    reason = market_closed_reason("SAP.DE")
    assert reason and "Feiertag" in reason


def test_us_ticker_uses_us_calendar(monkeypatch):
    """Freitag 17:00 Berlin = 11:00 New York → US offen, XETRA ebenfalls."""
    _freeze(monkeypatch, "2026-07-24T17:00:00", "Europe/Berlin")
    assert market_closed_reason("AAPL") is None


# ── Broker-Gate ──────────────────────────────────────────────────────────────

def _gated_broker(monkeypatch, reason="Frankfurt / Tradegate geschlossen (Wochenende)"):
    b = _broker(monkeypatch)
    monkeypatch.setattr(ibm, "_MARKET_HOURS_GATE", True)
    monkeypatch.setattr(ibm, "_closed_market_reason", lambda ticker: reason)
    return b


def test_buy_blocked_when_market_closed(monkeypatch):
    b = _gated_broker(monkeypatch)
    res = b.buy("SAP.DE", 10, 100.0)
    assert res["status"] == "error"
    assert "Wochenende" in res["reason"]
    assert b._ib.placed == [], "keine Order darf eingereicht werden"


def test_sell_blocked_when_market_closed_and_stop_survives(monkeypatch):
    """Kern der Regression: das Gate greift VOR dem Stop-Cancel."""
    b = _broker(monkeypatch)
    b.buy("SAP.DE", 406, 138.64, stop_loss=130.32)     # Börse noch "offen"
    assert len(_stops(b._ib)) == 1

    monkeypatch.setattr(ibm, "_MARKET_HOURS_GATE", True)
    monkeypatch.setattr(ibm, "_closed_market_reason",
                        lambda ticker: "Frankfurt / Tradegate geschlossen (Wochenende)")
    res = b.sell("SAP.DE", 406, 140.46)

    assert res["status"] == "error"
    assert "Wochenende" in res["reason"]
    assert len(_stops(b._ib)) == 1, "Schutz-Stop darf nicht angefasst werden"
    assert b._ib.cancelled == []


def test_gate_disabled_lets_order_through(monkeypatch):
    b = _broker(monkeypatch)
    monkeypatch.setattr(ibm, "_MARKET_HOURS_GATE", False)
    res = b.buy("SAP.DE", 10, 100.0)
    assert res["status"] == "filled"


def test_gate_fails_open_on_calendar_error(monkeypatch):
    """Kaputter Kalender darf den Handel nicht lahmlegen."""
    monkeypatch.setattr(ibm, "_MARKET_HOURS_GATE", True)
    import analyzers.market_schedule as ms

    def _boom(_ticker):
        raise RuntimeError("Kalender kaputt")

    monkeypatch.setattr(ms, "market_closed_reason", _boom)
    assert ibm._closed_market_reason("SAP.DE") is None


def test_crypto_path_is_not_gated(monkeypatch):
    """Krypto handelt 24/7 – buy_crypto/sell_crypto laufen bewusst am Gate
    vorbei (nur buy()/sell() für Aktien sind gegated)."""
    import inspect
    src = inspect.getsource(ibm.IBKRBroker.buy_crypto)
    assert "_closed_market_reason" not in src
    src = inspect.getsource(ibm.IBKRBroker.sell_crypto)
    assert "_closed_market_reason" not in src
