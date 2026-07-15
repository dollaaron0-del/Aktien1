"""
Tests für dashboard/tabs/factory.py (Vision W1.3/W1.4).

Headless via streamlit.testing.v1 AppTest auf einem isolierten Mini-Skript
(Muster: tests/test_dashboard_auth.py) statt des vollen app.py.
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
class _Ctx:
    pass

from dashboard.tabs import factory
factory.render(_Ctx())
"""


def test_factory_tab_renders_svg_scene():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "<svg" in html_out


def test_factory_tab_shows_legend():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    caption_out = "".join(str(c.value) for c in at.get("caption"))
    assert "aktiv/gesund" in caption_out


def test_factory_tab_shows_paused_banner_when_bot_paused(monkeypatch):
    monkeypatch.setattr("system.bot_control.is_paused", lambda: True)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Werk pausiert" in html_out


def test_factory_tab_no_paused_banner_when_bot_active(monkeypatch):
    monkeypatch.setattr("system.bot_control.is_paused", lambda: False)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Werk pausiert" not in html_out


# ── W3.2/W3.3: Klick-Fokus + Detail-Panels ───────────────────────────────────

def test_no_detail_panel_without_query_param():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    assert len(at.get("markdown")) > 0
    assert not any("Status:" in str(c.value) for c in at.get("caption"))


def test_unknown_factory_id_is_ignored():
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "does-not-exist"
    at.run()
    assert not at.exception
    assert not any("Status:" in str(c.value) for c in at.get("caption"))


def test_known_machine_id_shows_generic_detail_panel():
    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "gate"
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Status:" in captions


def test_conveyor_detail_panel_shows_funnel_metrics():
    from analyzers.decision_log import DecisionLog
    dlog = DecisionLog()
    dlog.log({"ticker": "AAPL", "action": "BUY", "reason": "Test",
              "recommendation": "BUY", "sentiment_score": 0.8})

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "conveyor"
    at.run()
    assert not at.exception
    metric_labels = [m.label for m in at.get("metric")]
    assert "Analysiert heute" in metric_labels


def test_warehouse_detail_panel_shows_positions_table(fresh_portfolio):
    fresh_portfolio._conn.execute(
        "INSERT INTO positions (ticker, shares, entry_price, entry_date, "
        "stop_loss, take_profit, target_hold_days) VALUES (?,?,?,?,?,?,?)",
        ("NVDA", 5.0, 100.0, "2026-07-01", 90.0, 130.0, 14),
    )
    fresh_portfolio._conn.commit()

    at = AppTest.from_string(_SCRIPT)
    at.query_params["factory"] = "warehouse"
    at.run()
    assert not at.exception
    assert len(at.get("table")) == 1
