"""Tests für die Telegram-Wichtigkeits-Stufen (TELEGRAM_MODE=important):
nur trade/critical/digest erreichen den Chat, info landet im Log."""
import types

import pytest

import notifier.telegram_notifier as tn
from config import config


@pytest.fixture
def sent(monkeypatch):
    """Aktiviert den Notifier (Fake-Token) und fängt alle HTTP-Posts ab."""
    calls = []
    monkeypatch.setattr(config, "telegram_bot_token", "FAKE")
    monkeypatch.setattr(config, "telegram_chat_id", "42")
    monkeypatch.setattr(
        tn.requests, "post",
        lambda url, json=None, timeout=None: calls.append(json) or types.SimpleNamespace(ok=True),
    )
    return calls


def test_important_mode_suppresses_info(sent, monkeypatch):
    monkeypatch.setattr(config, "telegram_mode", "important")
    n = tn.TelegramNotifier()
    n.send("Routine-Info")                      # default level="info"
    n.send("Scanner-Meldung", level="info")
    assert sent == []


def test_important_mode_passes_trade_critical_digest(sent, monkeypatch):
    monkeypatch.setattr(config, "telegram_mode", "important")
    n = tn.TelegramNotifier()
    n.send("Kauf!", level="trade")
    n.send("Fehler!", level="critical")
    n.send("Tagesbericht", level="digest")
    assert [c["text"] for c in sent] == ["Kauf!", "Fehler!", "Tagesbericht"]


def test_all_mode_sends_everything(sent, monkeypatch):
    monkeypatch.setattr(config, "telegram_mode", "all")
    n = tn.TelegramNotifier()
    n.send("Routine-Info")
    n.send("Kauf!", level="trade")
    assert len(sent) == 2


def test_typed_helpers_use_trade_and_digest_levels(sent, monkeypatch):
    monkeypatch.setattr(config, "telegram_mode", "important")
    n = tn.TelegramNotifier()
    n.notify_buy(
        ticker="NVDA", shares=3, price=120.0, stop_loss=111.6, take_profit=144.0,
        hold_days=10, rationale="Test", sentiment_score=0.9,
    )
    n.notify_sell(
        ticker="NVDA", shares=3, price=130.0, entry_price=120.0,
        pnl=30.0, reason="TP",
    )
    n.notify_daily_summary(
        total_value=10_500.0, cash=5_000.0, open_positions=2,
        phase="GROWTH", progress_pct=35.0, actions_today=[],
    )
    # Insider-/Contract-Signale sind level=info → im important-Modus unterdrückt
    n.notify_insider_signal("NVDA", "Person", "BUY", "$1M", "Congressional")
    assert len(sent) == 3
    assert "KAUF" in sent[0]["text"]
    assert "VERKAUF" in sent[1]["text"]
    assert "Tages-Zusammenfassung" in sent[2]["text"]


def test_disabled_without_token(monkeypatch):
    monkeypatch.setattr(config, "telegram_bot_token", "")
    monkeypatch.setattr(config, "telegram_chat_id", "")
    called = []
    monkeypatch.setattr(tn.requests, "post", lambda *a, **k: called.append(1))
    tn.TelegramNotifier().send("x", level="critical")
    assert called == []
