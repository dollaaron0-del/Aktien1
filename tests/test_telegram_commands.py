"""
Tests für system/telegram_commands.py (Roadmap 1.5g: Telegram /status-Befehl).
"""
import pytest

import system.telegram_commands as tgc
from config import config


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _updates(*messages, ok=True):
    return _FakeResponse({"ok": ok, "result": list(messages)})


def _msg(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


@pytest.fixture
def offset_file(tmp_path):
    return str(tmp_path / "telegram_offset.json")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "telegram_bot_token", "FAKE-TOKEN")
    monkeypatch.setattr(config, "telegram_chat_id", "42")


@pytest.fixture
def fake_notifier(monkeypatch):
    """Fängt TelegramNotifier().send()-Aufrufe ab, ohne echte HTTP-Posts."""
    sent = []

    class _Fake:
        def send(self, text, level="info"):
            sent.append((text, level))

    monkeypatch.setattr("notifier.telegram_notifier.TelegramNotifier", _Fake)
    return sent


@pytest.fixture
def stub_status_text(monkeypatch):
    monkeypatch.setattr(tgc, "build_status_text", lambda: "STATUS_TEXT")


# ── Offset-Datei ──────────────────────────────────────────────────────────────

def test_load_offset_missing_file_returns_zero(tmp_path):
    assert tgc._load_offset(str(tmp_path / "missing.json")) == 0


def test_save_and_load_offset_roundtrip(tmp_path):
    f = str(tmp_path / "offset.json")
    tgc._save_offset(42, f)
    assert tgc._load_offset(f) == 42


def test_load_offset_corrupted_file_returns_zero(tmp_path):
    f = tmp_path / "offset.json"
    f.write_text("not json")
    assert tgc._load_offset(str(f)) == 0


# ── poll(): Grundverhalten ────────────────────────────────────────────────────

def test_poll_noop_without_token(monkeypatch, offset_file):
    monkeypatch.setattr(config, "telegram_bot_token", "")
    monkeypatch.setattr(config, "telegram_chat_id", "")
    calls = []
    monkeypatch.setattr(tgc, "http_get", lambda *a, **k: calls.append(1))
    tgc.poll(offset_file)
    assert calls == []


def test_poll_fail_open_on_network_error(configured, offset_file, monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(tgc, "http_get", _boom)
    tgc.poll(offset_file)  # darf nicht werfen


def test_poll_noop_when_response_not_ok(configured, offset_file, monkeypatch, fake_notifier):
    monkeypatch.setattr(tgc, "http_get", lambda *a, **k: _updates(_msg(1, 42, "/status"), ok=False))
    tgc.poll(offset_file)
    assert fake_notifier == []


def test_poll_handles_missing_message_field(configured, offset_file, monkeypatch, fake_notifier):
    """Ein Update ohne 'message' (z.B. edited_message/channel_post) darf nicht crashen."""
    monkeypatch.setattr(tgc, "http_get", lambda *a, **k: _updates({"update_id": 1}))
    tgc.poll(offset_file)
    assert fake_notifier == []


# ── poll(): /status-Dispatch ─────────────────────────────────────────────────

def test_poll_responds_to_status_from_authorized_chat(
    configured, offset_file, monkeypatch, fake_notifier, stub_status_text
):
    monkeypatch.setattr(tgc, "http_get", lambda *a, **k: _updates(_msg(1, 42, "/status")))
    tgc.poll(offset_file)
    assert fake_notifier == [("STATUS_TEXT", "command")]


def test_poll_ignores_status_from_other_chat(
    configured, offset_file, monkeypatch, fake_notifier, stub_status_text
):
    monkeypatch.setattr(tgc, "http_get", lambda *a, **k: _updates(_msg(1, 999, "/status")))
    tgc.poll(offset_file)
    assert fake_notifier == []


def test_poll_ignores_other_text(
    configured, offset_file, monkeypatch, fake_notifier, stub_status_text
):
    monkeypatch.setattr(tgc, "http_get", lambda *a, **k: _updates(_msg(1, 42, "hallo bot")))
    tgc.poll(offset_file)
    assert fake_notifier == []


def test_poll_status_command_is_case_insensitive_and_strips_bot_mention(
    configured, offset_file, monkeypatch, fake_notifier, stub_status_text
):
    monkeypatch.setattr(tgc, "http_get", lambda *a, **k: _updates(_msg(1, 42, "/STATUS@MeinBot")))
    tgc.poll(offset_file)
    assert fake_notifier == [("STATUS_TEXT", "command")]


def test_poll_advances_offset_across_multiple_updates(
    configured, offset_file, monkeypatch, fake_notifier, stub_status_text
):
    monkeypatch.setattr(
        tgc, "http_get",
        lambda *a, **k: _updates(_msg(5, 42, "hallo"), _msg(6, 42, "/status")),
    )
    tgc.poll(offset_file)
    assert tgc._load_offset(offset_file) == 6
    assert len(fake_notifier) == 1


def test_poll_uses_persisted_offset_as_next_request_param(
    configured, offset_file, monkeypatch, fake_notifier, stub_status_text
):
    tgc._save_offset(10, offset_file)
    seen_params = {}

    def _capture(*a, **k):
        seen_params.update(k.get("params", {}))
        return _updates()
    monkeypatch.setattr(tgc, "http_get", _capture)
    tgc.poll(offset_file)
    assert seen_params.get("offset") == 11


# ── build_status_text(): einzelne Bausteine + Fail-Open ─────────────────────

def test_build_status_text_is_fail_open_when_everything_broken(monkeypatch):
    """Selbst wenn JEDE Unterkomponente wirft, liefert die Funktion trotzdem
    einen Text zurück (nur die Kopfzeile) statt einer Exception."""
    monkeypatch.setattr("system.bot_control.is_paused", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr("system.live_status.read_status", lambda: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError()))

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("kaputt")

    monkeypatch.setattr("analyzers.api_cost_tracker.APICostTracker", _Boom)
    monkeypatch.setattr("portfolio.portfolio.Portfolio", _Boom)

    text = tgc.build_status_text()
    assert "Bot-Status" in text
    assert "🔴 IB-Gateway nicht erreichbar" in text


def test_build_status_text_shows_paused_state(monkeypatch):
    monkeypatch.setattr("system.bot_control.is_paused", lambda: True)
    text = tgc.build_status_text()
    assert "Pausiert" in text


def test_build_status_text_shows_active_state(monkeypatch):
    monkeypatch.setattr("system.bot_control.is_paused", lambda: False)
    text = tgc.build_status_text()
    assert "Aktiv" in text


def test_build_status_text_shows_idle_next_run(monkeypatch):
    monkeypatch.setattr(
        "system.live_status.read_status",
        lambda: {"state": "idle", "next_run": "2026-07-15T06:00:00"},
    )
    text = tgc.build_status_text()
    assert "nächster Lauf" in text
    assert "2026-07-15 06:00" in text


class _FakePosition:
    def __init__(self, shares, entry_price):
        self.shares = shares
        self.entry_price = entry_price


class _FakePortfolio:
    def __init__(self, *a, **k):
        self.cash = 5000.0

    def all_positions(self):
        return {"AAPL": _FakePosition(shares=2, entry_price=100.0)}

    def total_value(self, prices):
        return self.cash + sum(
            p.shares * prices.get(t, p.entry_price) for t, p in self.all_positions().items()
        )


class _FakeCircuitBreaker:
    def __init__(self, *a, **k):
        pass

    def status(self, current_value):
        return {"triggered": False}


class _FakeCircuitBreakerTriggered(_FakeCircuitBreaker):
    def status(self, current_value):
        return {"triggered": True}


class _FakePaperBroker:
    def __init__(self, *a, **k):
        pass

    def get_prices(self, tickers):
        return {t: 110.0 for t in tickers}


def test_build_status_text_includes_portfolio_summary(monkeypatch):
    monkeypatch.setattr("portfolio.portfolio.Portfolio", _FakePortfolio)
    monkeypatch.setattr("portfolio.circuit_breaker.CircuitBreaker", _FakeCircuitBreaker)
    monkeypatch.setattr("broker.paper_broker.PaperBroker", _FakePaperBroker)
    text = tgc.build_status_text()
    assert "🟢 Circuit-Breaker" in text
    assert "Positionen: 1" in text
    assert "5,220.00" in text  # 5000 cash + 2*110 Kurswert


def test_build_status_text_flags_triggered_circuit_breaker(monkeypatch):
    monkeypatch.setattr("portfolio.portfolio.Portfolio", _FakePortfolio)
    monkeypatch.setattr("portfolio.circuit_breaker.CircuitBreaker", _FakeCircuitBreakerTriggered)
    monkeypatch.setattr("broker.paper_broker.PaperBroker", _FakePaperBroker)
    text = tgc.build_status_text()
    assert "AUSGELÖST" in text
