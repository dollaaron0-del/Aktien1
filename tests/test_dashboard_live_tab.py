"""
Tests für dashboard/tabs/live.py im Pixel-Theme (Design D3).

Headless via streamlit.testing.v1 AppTest gegen isolierte Temp-DBs (Muster:
Order-Log-Dashboard-Verifikation, Commit 8fb561b). Prüft: Terminal-Feed
rendert mit Escaping, Zeitleiste zeigt Stationen, Order-Historie nutzt LEDs
— jeweils im pixel- UND im plain-Modus (Notausstieg).
"""
import json
import os

import system.live_status as ls_mod
import broker.order_log as ol_mod
from streamlit.testing.v1 import AppTest


class _Ctx:
    def ticker_label(self, t):
        return t

    _hdr_paused = True
    _ls = None


def _make_app_test(tmp_path, monkeypatch, theme_env=None):
    if theme_env is not None:
        monkeypatch.setenv("DASHBOARD_THEME", theme_env)
    else:
        monkeypatch.delenv("DASHBOARD_THEME", raising=False)

    feed = ls_mod.ActivityFeed(db_path=str(tmp_path / "activity_feed.db"))
    monkeypatch.setattr(ls_mod, "_feed", feed)

    order_log = ol_mod.OrderLog(db_path=str(tmp_path / "order_log.db"))
    monkeypatch.setattr(ol_mod, "_instance", order_log)

    return feed, order_log


_SCRIPT = """
class _Ctx:
    def ticker_label(self, t):
        return t
    _hdr_paused = True
    _ls = None

from dashboard.tabs import live
live.render(_Ctx())
"""


def test_activity_feed_pixel_escapes_html_and_colors_by_event(tmp_path, monkeypatch):
    feed, _ = _make_app_test(tmp_path, monkeypatch)
    feed.emit("trade", ticker="<script>alert(1)</script>", detail="GEKAUFT 3 @ $100")
    feed.emit("gate_blocked", ticker="AAPL", detail="Korrelation zu hoch")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-terminal" in html_out
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "--px-neon-green" in html_out   # trade
    assert "--px-copper" in html_out       # gate_blocked


def test_activity_feed_plain_mode_unchanged(tmp_path, monkeypatch):
    feed, _ = _make_app_test(tmp_path, monkeypatch, theme_env="plain")
    feed.emit("trade", ticker="AAPL", detail="GEKAUFT 3 @ $100")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-terminal" not in html_out
    assert "💼" in html_out
    assert "AAPL" in html_out


def test_phase_timeline_pixel_shows_stations(tmp_path, monkeypatch):
    _make_app_test(tmp_path, monkeypatch)
    status = {
        "phase_history": [
            {"phase": "Start", "started_at": "2026-07-15T09:00:00",
             "ended_at": "2026-07-15T09:00:05"},
            {"phase": "Analyse", "started_at": "2026-07-15T09:00:05",
             "ended_at": None},
        ]
    }
    script = _SCRIPT.replace("_ls = None", f"_ls = {status!r}")
    at = AppTest.from_string(script)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Start" in html_out
    assert "Analyse" in html_out
    assert "px-blink" in html_out  # laufende Phase pulsiert


def test_order_history_pixel_uses_led(tmp_path, monkeypatch):
    from broker.order_result import OrderResult
    _, order_log = _make_app_test(tmp_path, monkeypatch)
    order_log.record(OrderResult.filled("AAPL", 3, 101.05, mode="paper"), "BUY")
    order_log.record(OrderResult.error(ticker="NVDA", reason="IBKR nicht verbunden",
                                        mode="ibkr"), "SELL")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-led--ok" in html_out
    assert "px-led--err" in html_out
    assert "IBKR nicht verbunden" in html_out


def test_order_history_plain_mode_unchanged(tmp_path, monkeypatch):
    from broker.order_result import OrderResult
    _, order_log = _make_app_test(tmp_path, monkeypatch, theme_env="plain")
    order_log.record(OrderResult.filled("AAPL", 3, 101.05, mode="paper"), "BUY")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-led" not in html_out
    assert "✅" in html_out
