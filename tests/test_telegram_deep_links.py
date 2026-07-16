"""
Tests für die Telegram-Rückverweise (Ausbau-Roadmap H5.3).

Deep-Link ins Dashboard an wichtigen Nachrichten. Der Versand selbst wird
gemockt — kein Test schickt je eine echte Telegram-Nachricht.
"""
import pytest

from notifier.telegram_notifier import TelegramNotifier, dashboard_link


@pytest.fixture()
def _sent(monkeypatch):
    """Fängt den HTTP-Versand ab und liefert die gesendeten Payloads."""
    posts = []

    def _fake_post(url, json=None, timeout=None):
        posts.append(json)

    monkeypatch.setattr("notifier.telegram_notifier.requests.post", _fake_post)
    return posts


def _notifier(monkeypatch, url=""):
    from config import config
    monkeypatch.setattr(config, "telegram_bot_token", "test-token", raising=False)
    monkeypatch.setattr(config, "telegram_chat_id", "test-chat", raising=False)
    monkeypatch.setattr(config, "dashboard_url", url, raising=False)
    return TelegramNotifier()


# ── dashboard_link() ─────────────────────────────────────────────────────────

def test_dashboard_link_empty_without_url(monkeypatch):
    """Bewusster Default: ohne DASHBOARD_URL gar kein Link — das
    Dashboard ist nur über den SSH-Tunnel erreichbar, ein fester
    Default-Link wäre meistens tot."""
    from config import config
    monkeypatch.setattr(config, "dashboard_url", "", raising=False)
    assert dashboard_link("warehouse") == ""


def test_dashboard_link_builds_factory_deep_link(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "dashboard_url", "http://localhost:8503", raising=False)
    link = dashboard_link("warehouse")
    assert 'href="http://localhost:8503/?factory=warehouse"' in link
    assert "Im Leitstand ansehen" in link


def test_dashboard_link_without_target_points_to_start_page(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "dashboard_url", "http://localhost:8503", raising=False)
    link = dashboard_link("")
    assert 'href="http://localhost:8503"' in link
    assert "factory=" not in link


def test_dashboard_link_strips_trailing_slash(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "dashboard_url", "http://localhost:8503/", raising=False)
    assert 'href="http://localhost:8503/?factory=gate"' in dashboard_link("gate")


def test_dashboard_link_custom_label(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "dashboard_url", "http://x", raising=False)
    assert "Positionen öffnen" in dashboard_link("warehouse", label="Positionen öffnen")


# ── send(link_target=...) ────────────────────────────────────────────────────

def test_send_appends_link_when_url_configured(monkeypatch, _sent):
    n = _notifier(monkeypatch, url="http://localhost:8503")
    n.send("Testnachricht", level="trade", link_target="warehouse")
    assert len(_sent) == 1
    text = _sent[0]["text"]
    assert text.startswith("Testnachricht")
    assert "?factory=warehouse" in text


def test_send_without_link_target_has_no_link(monkeypatch, _sent):
    n = _notifier(monkeypatch, url="http://localhost:8503")
    n.send("Testnachricht", level="trade")
    assert "factory=" not in _sent[0]["text"]


def test_send_unchanged_when_url_not_configured(monkeypatch, _sent):
    """Ohne DASHBOARD_URL bleibt die Nachricht exakt wie vorher —
    keine Leerzeile, kein Rest."""
    n = _notifier(monkeypatch, url="")
    n.send("Testnachricht", level="trade", link_target="warehouse")
    assert _sent[0]["text"] == "Testnachricht"


def test_send_still_respects_important_mode_filter(monkeypatch, _sent):
    """Der Link darf den TELEGRAM_MODE-Filter nicht aushebeln."""
    from config import config
    monkeypatch.setattr(config, "telegram_mode", "important", raising=False)
    n = _notifier(monkeypatch, url="http://localhost:8503")
    n._mode = "important"
    n.send("Unwichtig", level="info", link_target="warehouse")
    assert _sent == []  # info wird weiterhin unterdrückt


# ── Echte Ereignis-Nachrichten ───────────────────────────────────────────────

def test_notify_buy_links_to_warehouse(monkeypatch, _sent):
    n = _notifier(monkeypatch, url="http://localhost:8503")
    n.notify_buy(
        ticker="NVDA", shares=3, price=100.0, stop_loss=90.0, take_profit=120.0,
        hold_days=14, rationale="Test", sentiment_score=0.8,
    )
    assert "?factory=warehouse" in _sent[0]["text"]
    assert "KAUF: NVDA" in _sent[0]["text"]


def test_notify_sell_links_to_warehouse(monkeypatch, _sent):
    n = _notifier(monkeypatch, url="http://localhost:8503")
    n.notify_sell(
        ticker="NVDA", shares=3, price=110.0, entry_price=100.0, pnl=30.0,
        reason="Take-Profit",
    )
    assert "?factory=warehouse" in _sent[0]["text"]


def test_notify_thesis_warning_links_to_warehouse(monkeypatch, _sent):
    n = _notifier(monkeypatch, url="http://localhost:8503")
    n.notify_thesis_warning("NVDA", "Grund", "HIGH")
    assert "?factory=warehouse" in _sent[0]["text"]


def test_notify_daily_summary_links_to_start_page(monkeypatch, _sent):
    n = _notifier(monkeypatch, url="http://localhost:8503")
    n.notify_daily_summary(
        total_value=100000.0, cash=5000.0, open_positions=3,
        phase="GROWTH", progress_pct=12.5, actions_today=[],
    )
    text = _sent[0]["text"]
    assert 'href="http://localhost:8503"' in text
    assert "factory=" not in text  # Digest zeigt aufs Ganze, nicht auf eine Maschine


def test_link_target_matches_a_real_factory_machine():
    """Der Deep-Link muss auf eine ECHTE Maschinen-ID zeigen — sonst
    ignoriert das Dashboard den Parameter stillschweigend (W3.2) und der
    Link führt ins Leere."""
    from dashboard.factory.state import MACHINE_IDS
    assert "warehouse" in MACHINE_IDS
