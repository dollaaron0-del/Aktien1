"""
Tests für die Schichtbuch-Einbindung im Fabrik-Tab (H7.3). Isoliertes
Mini-Skript (Muster: test_dashboard_thesis_board_tab.py) statt des
vollen factory.render(ctx) — _render_logbook() hängt an keiner der
Fabrik-Szene-Abhängigkeiten.
"""
from datetime import date

from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.factory import _render_logbook
_render_logbook()
"""


def test_logbook_shows_empty_state_and_generate_button():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Noch kein Schichtbuch-Eintrag" in captions
    buttons = [b.label for b in at.get("button")]
    assert "Eintrag erzeugen" in buttons


def test_logbook_shows_existing_entry_text(monkeypatch):
    from dashboard.logbook import write_entry
    monkeypatch.setattr("system.live_status.FEED_PATH", "")  # keine echte Feed-DB nötig
    today = date.today().isoformat()
    write_entry(today, use_ollama=False)  # nutzt die autouse-isolierte LOGBOOK_FILE

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Keine Aktivität aufgezeichnet" in html_out


def test_logbook_generate_button_creates_entry(monkeypatch):
    monkeypatch.setattr("dashboard.logbook._ollama_prose", lambda rule_text, model=None: None)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    at.button(key="logbook_generate").click().run()
    assert not at.exception
    html_out = "".join(str(m.value) for m in at.get("markdown"))
    assert "Keine Aktivität aufgezeichnet" in html_out or "Trade(s)" in html_out
