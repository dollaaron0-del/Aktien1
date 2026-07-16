"""
Tests für das Steuerpult im Fabrik-Tab (Ausbau-Roadmap H1.1/H1.3).

Isoliertes Mini-Skript (Muster: test_dashboard_thesis_board_tab.py).
Pause-Flag und Breaker-State sind über die conftest-autouse-Fixtures
isoliert — kein Test berührt den echten, bewusst gesetzten Pause-Zustand.
"""
from streamlit.testing.v1 import AppTest

_SCRIPT = """
from dashboard.tabs.factory import _render_control_panel
_render_control_panel(100000.0)
"""


def _btn(at, label):
    return next(b for b in at.get("button") if b.label == label)


# ── Ehrlichkeit: Flag vs. Dienst ─────────────────────────────────────────────

def test_panel_warns_when_service_not_running(monkeypatch):
    """Kernpunkt H1.1: läuft der Dienst nicht, darf der Hebel keine
    Wirkung vortäuschen — die UI muss das unmissverständlich sagen."""
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "inactive")
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    warnings = "".join(str(w.value) for w in at.get("warning"))
    assert "läuft **nicht**" in warnings
    assert "systemctl start" in warnings


def test_panel_no_warning_when_service_active(monkeypatch):
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "active")
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    warnings = "".join(str(w.value) for w in at.get("warning"))
    assert "läuft **nicht**" not in warnings
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "läuft" in captions


def test_panel_makes_no_claim_when_service_state_unknown(monkeypatch):
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "unknown")
    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "nicht ermittelbar" in captions


# ── H1.1: Pause-Hebel ────────────────────────────────────────────────────────

def test_pause_requires_confirmation(monkeypatch):
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "active")
    from dashboard.controls import set_bot_paused
    set_bot_paused(False)  # Ausgangslage: nicht pausiert (isolierte Datei)

    at = AppTest.from_string(_SCRIPT)
    at.run()
    _btn(at, "⏸ Pausieren").click().run()  # ohne Haken
    assert not at.exception
    assert "Bitte zuerst bestätigen" in "".join(str(w.value) for w in at.get("warning"))

    from dashboard.controls import pause_status
    assert pause_status()["paused"] is False  # nichts passiert


def test_pause_toggles_after_confirmation(monkeypatch):
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "active")
    from dashboard.controls import pause_status, set_bot_paused
    set_bot_paused(False)

    at = AppTest.from_string(_SCRIPT)
    at.run()
    at.checkbox(key="pause_confirm").check()
    at.text_input(key="pause_reason").set_value("Testgrund")
    _btn(at, "⏸ Pausieren").click().run()
    assert not at.exception

    status = pause_status()
    assert status["paused"] is True
    assert status["reason"] == "Testgrund"


def test_resume_button_shown_when_paused(monkeypatch):
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "active")
    from dashboard.controls import set_bot_paused
    set_bot_paused(True, reason="Testlauf")

    at = AppTest.from_string(_SCRIPT)
    at.run()
    assert not at.exception
    labels = [b.label for b in at.get("button")]
    assert "▶ Weiter" in labels
    assert "⏸ Pausieren" not in labels
    assert "pausiert seit" in "".join(str(m.value) for m in at.get("markdown"))


# ── H1.3: Not-Aus-Reset (zwei Schritte) ──────────────────────────────────────

def test_reset_needs_both_checkbox_and_typed_word(monkeypatch):
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "active")
    calls = []
    monkeypatch.setattr(
        "dashboard.controls.reset_circuit_breaker",
        lambda v, by="dashboard": calls.append(v),
    )

    # (a) gar nichts
    at = AppTest.from_string(_SCRIPT)
    at.run()
    _btn(at, "🔴 Not-Aus zurücksetzen").click().run()
    assert "Beide Schritte nötig" in "".join(str(w.value) for w in at.get("warning"))
    assert calls == []

    # (b) nur Haken, kein Wort
    at = AppTest.from_string(_SCRIPT)
    at.run()
    at.checkbox(key="breaker_ack").check()
    _btn(at, "🔴 Not-Aus zurücksetzen").click().run()
    assert calls == []

    # (c) nur Wort, kein Haken
    at = AppTest.from_string(_SCRIPT)
    at.run()
    at.text_input(key="breaker_typed").set_value("RESET")
    _btn(at, "🔴 Not-Aus zurücksetzen").click().run()
    assert calls == []


def test_reset_fires_only_with_both_steps(monkeypatch):
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "active")
    calls = []
    monkeypatch.setattr(
        "dashboard.controls.reset_circuit_breaker",
        lambda v, by="dashboard": (calls.append(v), {"prev_open_value": 1})[1],
    )
    at = AppTest.from_string(_SCRIPT)
    at.run()
    at.checkbox(key="breaker_ack").check()
    at.text_input(key="breaker_typed").set_value("reset")  # Kleinschreibung erlaubt
    _btn(at, "🔴 Not-Aus zurücksetzen").click().run()
    assert not at.exception
    assert calls == [100000.0]  # der uebergebene Depotwert


def test_reset_warns_about_risk_override(monkeypatch):
    """Der Knopf muss sagen, was er wirklich tut — nicht nur 'Reset'."""
    monkeypatch.setattr("dashboard.controls.service_state", lambda: "active")
    at = AppTest.from_string(_SCRIPT)
    at.run()
    captions = "".join(str(c.value) for c in at.get("caption"))
    assert "Risiko-Übersteuerung" in captions
    assert "Allzeithoch" in captions
