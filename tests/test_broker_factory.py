"""
Tests für broker.factory.get_readonly_broker() (25.7.2026).

Auslöser: dashboard/app.py, system/telegram_commands.py und
dashboard/dry_run.py hingen hart an PaperBroker(), unabhängig von
config.broker_mode. Lief der Bot live über IBKR, zeigten Dashboard und
Telegram dadurch eine andere Preisquelle als die, auf der der Bot
tatsächlich handelte – sichtbar geworden am SAP-Vorfall, wo eine stale
IBKR-Preisquelle einen falschen Stop-Loss-Bruch auslöste, den ein
PaperBroker-gespeistes Dashboard nie gezeigt hätte.

Semantik: get_readonly_broker() liefert denselben Broker-TYP wie main.py
(config.broker_mode), aber bei IBKR über eine EIGENE Client-ID und
readonly=True – darf die Live-Handels-Session des Bots (eigene Client-ID)
nicht stören und kann selbst bei einem Programmierfehler nie eine Order
platzieren.
"""
import types

import pytest


@pytest.fixture(autouse=True)
def _reset_factory_singleton():
    """factory._instance ist ein Prozess-Singleton – zwischen Tests zurücksetzen."""
    import broker.factory as factory
    factory._instance = None
    yield
    factory._instance = None


def test_paper_mode_returns_paper_broker(monkeypatch):
    import broker.factory as factory
    from broker.paper_broker import PaperBroker
    monkeypatch.setattr(factory.config, "broker_mode", "paper")
    b = factory.get_readonly_broker()
    assert isinstance(b, PaperBroker)


def test_ibkr_mode_uses_distinct_readonly_client_id(monkeypatch):
    """Kernregel: eigene Client-ID und readonly=True – nie die des Live-Bots."""
    import broker.factory as factory
    import broker.ibkr_broker as ibm

    captured = {}

    class _FakeIBKRBroker:
        def __init__(self, client_id=None, readonly=False):
            captured["client_id"] = client_id
            captured["readonly"] = readonly

    monkeypatch.setattr(factory.config, "broker_mode", "ibkr")
    monkeypatch.setattr(ibm, "IBKRBroker", _FakeIBKRBroker)
    monkeypatch.setattr("broker.ibkr_broker.IBKRBroker", _FakeIBKRBroker)

    factory.get_readonly_broker()

    assert captured["readonly"] is True
    assert captured["client_id"] == factory._READONLY_CLIENT_ID
    assert captured["client_id"] != ibm._CLIENT_ID, (
        "readonly-Client-ID darf niemals mit der Handels-Client-ID kollidieren"
    )


def test_singleton_across_calls(monkeypatch):
    import broker.factory as factory
    monkeypatch.setattr(factory.config, "broker_mode", "paper")
    a = factory.get_readonly_broker()
    b = factory.get_readonly_broker()
    assert a is b


def test_thread_safe_singleton_init(monkeypatch):
    """Zwei gleichzeitige erste Aufrufe (z.B. Streamlit + Telegram-Handler im
    selben Prozess) dürfen nicht zwei Instanzen erzeugen."""
    import threading
    import broker.factory as factory

    monkeypatch.setattr(factory.config, "broker_mode", "paper")
    results = []

    def _call():
        results.append(factory.get_readonly_broker())

    threads = [threading.Thread(target=_call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len({id(r) for r in results}) == 1


# ── IBKRBroker: client_id/readonly-Konstruktor-Parameter ───────────────────

def test_ibkr_broker_defaults_preserve_main_py_behaviour(monkeypatch):
    """Ohne Angabe (main.py-Aufruf: IBKRBroker()) bleibt alles wie zuvor –
    Handels-Client-ID, volle Rechte."""
    import broker.ibkr_broker as ibm

    monkeypatch.setattr(ibm.IBKRBroker, "_connect", lambda self: False)
    b = ibm.IBKRBroker()
    assert b._client_id == ibm._CLIENT_ID
    assert b._readonly is False


def test_ibkr_broker_accepts_distinct_readonly_client(monkeypatch):
    import broker.ibkr_broker as ibm

    monkeypatch.setattr(ibm.IBKRBroker, "_connect", lambda self: False)
    b = ibm.IBKRBroker(client_id=9, readonly=True)
    assert b._client_id == 9
    assert b._readonly is True


def test_connect_passes_client_id_and_readonly_to_ib(monkeypatch):
    import broker.ibkr_broker as ibm

    captured = {}

    class _FakeIB:
        RequestTimeout = 0

        def connect(self, host, port, clientId, readonly, timeout):
            captured.update(clientId=clientId, readonly=readonly)

        def managedAccounts(self):
            return ["DU123"]

        def reqMarketDataType(self, _t):
            pass

    monkeypatch.setattr(ibm, "IB", _FakeIB, raising=False)
    import ib_insync
    monkeypatch.setattr(ib_insync, "IB", _FakeIB)

    b = object.__new__(ibm.IBKRBroker)
    b._ib = None
    b._connected = False
    b._active_account = ""
    b._lock = __import__("threading").RLock()
    b._client_id = 9
    b._readonly = True
    b._market_rule_cache = {}
    b._hist_close_cache = {}

    b._connect()

    assert captured == {"clientId": 9, "readonly": True}
