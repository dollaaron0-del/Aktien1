"""
Tests für die Plaketten-Wand-Einbindung im Fabrik-Tab (H7.2). Isoliertes
Mini-Skript (Muster: test_dashboard_thesis_board_tab.py) statt des
vollen factory.render(ctx).
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.factory import _render_achievements
_render_achievements()
"""


def _isolate_all_checks_to_locked(monkeypatch):
    from dashboard.achievements import CATALOG
    for item in CATALOG:
        monkeypatch.setitem(item, "check", lambda: False)


def test_achievements_shows_locked_entries_with_condition_text(monkeypatch):
    _isolate_all_checks_to_locked(monkeypatch)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "🔒" in captions
    assert "Der erste echte (nicht Backfill-)Trade wurde ausgeführt." in captions


def test_achievements_shows_unlocked_entry_pixel_mode(monkeypatch):
    monkeypatch.delenv("DASHBOARD_THEME", raising=False)
    from dashboard.achievements import CATALOG
    for item in CATALOG:
        monkeypatch.setitem(item, "check", lambda item=item: item["id"] == "first_live_trade")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Erster Live-Trade" in html_out
    assert "erreicht am" in html_out
    assert "px-panel" in html_out


def test_achievements_plain_mode_shows_success_box(monkeypatch):
    monkeypatch.setenv("DASHBOARD_THEME", "plain")
    from dashboard.achievements import CATALOG
    for item in CATALOG:
        monkeypatch.setitem(item, "check", lambda item=item: item["id"] == "first_live_trade")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    success_out = "".join(str(s.value) for s in at.get("success"))
    assert "Erster Live-Trade" in success_out
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "px-panel" not in html_out
