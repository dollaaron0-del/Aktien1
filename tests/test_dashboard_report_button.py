"""
Tests für den Wochen-Report-Download-Knopf im Portfolio-Tab (H5.1).
Isoliertes Mini-Skript (Muster: test_dashboard_thesis_board_tab.py) statt
des vollen render(ctx) — _render_weekly_report_button() hängt an keiner
der schweren ctx-Abhängigkeiten.
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.portfolio import _render_weekly_report_button
_render_weekly_report_button()
"""


def test_download_button_renders_with_html_report():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    buttons = at.get("download_button")
    assert len(buttons) == 1
    assert buttons[0].label == "📄 Wochen-Report (HTML)"


def test_download_button_fails_open_on_broken_report(monkeypatch):
    def _boom(end_day=None):
        raise RuntimeError("kaputt")

    monkeypatch.setattr("dashboard.report.build_weekly_html", _boom)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    assert len(at.get("download_button")) == 0
