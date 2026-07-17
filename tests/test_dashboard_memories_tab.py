"""
Tests für die Erinnerungs-Plakette im Fabrik-Tab (Roadmap L2.2).

Isoliertes Mini-Skript (Muster test_dashboard_dossier_tab.py).
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.factory import _render_memories
_render_memories()
"""


def test_shows_memories_when_present(monkeypatch):
    monkeypatch.setattr(
        "dashboard.memories.memories_for",
        lambda: [{"when": "vor 2 Wochen", "text": "der allererste Trade des Werks (ZX)",
                  "date": "2026-07-02"}],
    )
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("markdown"))
    assert "HEUTE VOR" in md
    assert "vor 2 Wochen" in md
    assert "allererste Trade" in md


def test_renders_nothing_without_memories(monkeypatch):
    """L2.2-Kernregel: kein 'noch nichts passiert'-Gefüll."""
    monkeypatch.setattr("dashboard.memories.memories_for", lambda: [])
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("markdown"))
    assert "HEUTE VOR" not in md
    assert not at.get("caption")


def test_escapes_memory_text(monkeypatch):
    monkeypatch.setattr(
        "dashboard.memories.memories_for",
        lambda: [{"when": "vor 1 Woche", "text": "<script>alert(1)</script>",
                  "date": "2026-07-09"}],
    )
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    md = "".join(str(m.value) for m in at.get("markdown"))
    assert "<script>" not in md
    assert "&lt;script&gt;" in md


def test_fail_open_on_broken_source(monkeypatch):
    def _boom():
        raise RuntimeError("kaputt")

    monkeypatch.setattr("dashboard.memories.memories_for", _boom)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
