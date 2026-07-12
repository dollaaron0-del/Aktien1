"""
Tests für das Dashboard-Passwort-Gate (Roadmap 0.4-Rest).

Kern-Zusagen: (1) Ohne DASHBOARD_PASSWORD kein Gate — exakt altes Verhalten
(Default AUS, kein Breaking Change). (2) Mit gesetztem Passwort blockiert
require_login() das Rendering (st.stop()), bis das richtige Passwort
eingegeben wurde. (3) Falsches Passwort zeigt einen Fehler UND bleibt
gesperrt. (4) Erfolgreicher Login merkt sich den Zustand in session_state
(kein erneutes Passwort bei jedem Rerun). Headless via streamlit.testing.v1
AppTest, netzfrei — kein echtes app.py-Rendering (das lädt Broker/DBs),
sondern ein isoliertes Mini-Skript, das nur auth.require_login() aufruft.
"""
import os

from streamlit.testing.v1 import AppTest

_SCRIPT = """
import dashboard.auth as auth
auth.require_login()
import streamlit as st
st.write("GESCHUETZTER_INHALT")
"""


def _run(monkeypatch, password=None):
    if password is None:
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("DASHBOARD_PASSWORD", password)
    at = AppTest.from_string(_SCRIPT)
    at.run()
    return at


def test_no_password_set_renders_content_directly(monkeypatch):
    at = _run(monkeypatch, password=None)
    assert not at.exception
    texts = [w.value for w in at.get("markdown")] + [w.value for w in at.get("text")]
    assert any("GESCHUETZTER_INHALT" in t for t in texts) or \
        any("GESCHUETZTER_INHALT" in str(w.value) for w in at.get("text"))


def test_password_set_shows_login_gate_not_content(monkeypatch):
    at = _run(monkeypatch, password="geheim123")
    assert not at.exception
    assert len(at.title) == 1 and "Login" in at.title[0].value
    assert len(at.text_input) == 1
    all_text = " ".join(str(w.value) for w in at.get("markdown") + at.get("text"))
    assert "GESCHUETZTER_INHALT" not in all_text


def test_wrong_password_shows_error_and_stays_locked(monkeypatch):
    at = _run(monkeypatch, password="geheim123")
    at.text_input[0].input("falsch").run()
    assert not at.exception
    assert len(at.error) == 1
    assert len(at.title) == 1                      # weiterhin gesperrt


def test_correct_password_unlocks_content(monkeypatch):
    at = _run(monkeypatch, password="geheim123")
    at.text_input[0].input("geheim123").run()
    assert not at.exception
    assert len(at.error) == 0
    all_text = " ".join(str(w.value) for w in at.get("markdown") + at.get("text"))
    assert "GESCHUETZTER_INHALT" in all_text


def test_password_with_surrounding_whitespace_in_env_is_stripped(monkeypatch):
    at = _run(monkeypatch, password="  geheim123  ")
    at.text_input[0].input("geheim123").run()
    assert not at.exception
    all_text = " ".join(str(w.value) for w in at.get("markdown") + at.get("text"))
    assert "GESCHUETZTER_INHALT" in all_text
