"""
Tests für system/dead_man_switch.py — externer Heartbeat (Roadmap 1.7).
"""
import pytest

from config import config
import system.dead_man_switch as dms


@pytest.fixture(autouse=True)
def _reset_throttle():
    """Jeder Test startet mit einer frischen Drossel, unabhängig von der
    Ausführungsreihenfolge der anderen Tests."""
    dms._last_ping_ts = 0.0
    yield
    dms._last_ping_ts = 0.0


def test_noop_without_url(monkeypatch):
    """Ohne konfigurierte URL ist ping() ein No-Op — Feature ist per Default aus."""
    monkeypatch.setattr(config, "dead_man_switch_url", "")
    calls = []
    monkeypatch.setattr(dms, "http_get", lambda *a, **kw: calls.append((a, kw)))
    assert dms.ping() is False
    assert calls == []


def test_pings_configured_url(monkeypatch):
    monkeypatch.setattr(config, "dead_man_switch_url", "https://hc-ping.com/test-uuid")
    monkeypatch.setattr(config, "dead_man_switch_interval_min", 5)
    calls = []
    monkeypatch.setattr(dms, "http_get", lambda *a, **kw: calls.append((a, kw)))
    assert dms.ping() is True
    assert calls[0][0] == ("https://hc-ping.com/test-uuid",)


def test_throttled_within_interval(monkeypatch):
    """Zweiter Aufruf innerhalb des Intervalls pingt nicht erneut."""
    monkeypatch.setattr(config, "dead_man_switch_url", "https://hc-ping.com/test-uuid")
    monkeypatch.setattr(config, "dead_man_switch_interval_min", 5)
    calls = []
    monkeypatch.setattr(dms, "http_get", lambda *a, **kw: calls.append((a, kw)))
    assert dms.ping() is True
    assert dms.ping() is False
    assert len(calls) == 1


def test_fail_open_on_network_error(monkeypatch):
    """Ein fehlgeschlagener Ping darf niemals eine Exception nach außen werfen."""
    monkeypatch.setattr(config, "dead_man_switch_url", "https://hc-ping.com/test-uuid")

    def _boom(*a, **kw):
        raise ConnectionError("network down")

    monkeypatch.setattr(dms, "http_get", _boom)
    assert dms.ping() is False
