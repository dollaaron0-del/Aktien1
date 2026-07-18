"""
Tests für dashboard/position_notes.py (Ausbau-Roadmap H1.4).
"""
from dashboard.position_notes import PositionNotes


def test_get_returns_empty_string_when_no_note_exists(tmp_path):
    notes = PositionNotes(db_path=str(tmp_path / "notes.db"))
    assert notes.get("AAPL") == ""


def test_set_and_get_roundtrip(tmp_path):
    notes = PositionNotes(db_path=str(tmp_path / "notes.db"))
    notes.set("AAPL", "Warte auf Earnings am 24.7.")
    assert notes.get("AAPL") == "Warte auf Earnings am 24.7."


def test_set_overwrites_existing_note(tmp_path):
    notes = PositionNotes(db_path=str(tmp_path / "notes.db"))
    notes.set("AAPL", "Erste Notiz")
    notes.set("AAPL", "Zweite Notiz")
    assert notes.get("AAPL") == "Zweite Notiz"


def test_notes_are_isolated_per_ticker(tmp_path):
    notes = PositionNotes(db_path=str(tmp_path / "notes.db"))
    notes.set("AAPL", "Apple-Notiz")
    notes.set("NVDA", "Nvidia-Notiz")
    assert notes.get("AAPL") == "Apple-Notiz"
    assert notes.get("NVDA") == "Nvidia-Notiz"


def test_set_empty_string_clears_note(tmp_path):
    notes = PositionNotes(db_path=str(tmp_path / "notes.db"))
    notes.set("AAPL", "Notiz")
    notes.set("AAPL", "")
    assert notes.get("AAPL") == ""


def test_notes_persist_across_instances(tmp_path):
    db_path = str(tmp_path / "notes.db")
    PositionNotes(db_path=db_path).set("AAPL", "Persistente Notiz")
    assert PositionNotes(db_path=db_path).get("AAPL") == "Persistente Notiz"


# ── Tab-Einbau (isoliert, ohne die schweren render(ctx)-Abhängigkeiten) ──────

from streamlit.testing.v1 import AppTest

_NOTES_SCRIPT = """
from dashboard.portfolio_panel import _render_position_notes
_render_position_notes(lambda t: t, ["AAPL", "NVDA"])
"""


def test_notes_expander_renders_per_ticker(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.position_notes._DB_PATH", str(tmp_path / "notes.db"))
    at = AppTest.from_string(_NOTES_SCRIPT)
    at.run()
    assert not at.exception
    labels = [e.label for e in at.get("expander")]
    assert "📝 Notiz — AAPL" in labels
    assert "📝 Notiz — NVDA" in labels


def test_notes_expander_shows_saved_note(tmp_path, monkeypatch):
    monkeypatch.setattr("dashboard.position_notes._DB_PATH", str(tmp_path / "notes.db"))
    from dashboard.position_notes import PositionNotes
    PositionNotes(db_path=str(tmp_path / "notes.db")).set("AAPL", "Warte auf Earnings")

    at = AppTest.from_string(_NOTES_SCRIPT)
    at.run()
    assert not at.exception
    text_areas = at.get("text_area")
    aapl_area = next(t for t in text_areas if "AAPL" in t.key)
    assert aapl_area.value == "Warte auf Earnings"


def test_notes_html_in_text_is_not_rendered_as_markup(tmp_path):
    """st.text_area/st.caption escapen automatisch — kein
    unsafe_allow_html verwendet, HTML in einer Notiz darf also nie als
    Markup interpretiert werden."""
    db_path = str(tmp_path / "notes.db")
    from dashboard.position_notes import PositionNotes
    PositionNotes(db_path=db_path).set("AAPL", "<script>alert(1)</script>")

    script = f"""
from dashboard.portfolio_panel import _render_position_notes
import dashboard.position_notes as pn
pn._DB_PATH = {db_path!r}
_render_position_notes(lambda t: t, ["AAPL"])
"""
    at = AppTest.from_string(script)
    at.run()
    assert not at.exception
    text_areas = at.get("text_area")
    assert text_areas[0].value == "<script>alert(1)</script>"  # roher Text, kein HTML-Render
