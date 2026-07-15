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
