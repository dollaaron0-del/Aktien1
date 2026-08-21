"""
Tests für dashboard/controls.py (Ausbau-Roadmap H1.1/H1.3 — Bedien-Logik).

Alle Tests laufen gegen die per conftest-autouse isolierten Temp-Dateien
(_isolate_bot_pause / _isolate_circuit_breaker_state) — der ECHTE
Pause-Flag und der echte Breaker-State werden hier nie berührt.
"""
import json

from dashboard.controls import (
    pause_status,
    reset_circuit_breaker,
    service_state,
    set_bot_paused,
)


# ── Sicherheits-Netz: Isolation muss greifen ─────────────────────────────────

def test_pause_file_is_isolated_from_real_state():
    """Wichtigster Test der Datei: die autouse-Fixture muss den Pfad
    wirklich umbiegen. Ohne sie würde set_bot_paused(False) den echten,
    bewusst gesetzten Pause-Flag löschen."""
    import system.bot_control as bc_mod
    assert "/opt/Aktien/data/bot_paused.json" not in bc_mod._PAUSE_FILE
    assert "tmp" in bc_mod._PAUSE_FILE or "pytest" in bc_mod._PAUSE_FILE


def test_breaker_file_is_isolated_from_real_state():
    import portfolio.circuit_breaker as cb_mod
    assert "/opt/Aktien/data/circuit_breaker.json" not in cb_mod._CACHE_FILE


# ── H1.1: Pause-Schalter ─────────────────────────────────────────────────────

def test_set_bot_paused_toggles_state():
    set_bot_paused(True, reason="Testlauf")
    assert pause_status()["paused"] is True
    set_bot_paused(False)
    assert pause_status()["paused"] is False


def test_set_bot_paused_records_reason_and_actor():
    set_bot_paused(True, reason="Wartung", by="dashboard")
    status = pause_status()
    assert status["reason"] == "Wartung"
    assert status["by"] == "dashboard"
    assert status["since"]


def test_set_bot_paused_writes_manual_marker_to_feed(monkeypatch):
    """H1-Kopfregel: jede Aktion muss im Feed als MANUELL erkennbar sein."""
    calls = []
    monkeypatch.setattr(
        "system.live_status.feed_emit",
        lambda event, ticker=None, detail=None: calls.append((event, detail)),
    )
    set_bot_paused(True, reason="Wartung")
    assert len(calls) == 1
    event, detail = calls[0]
    assert event == "bot_paused"
    assert detail.startswith("[manuell]")
    assert "Wartung" in detail

    calls.clear()
    set_bot_paused(False)
    assert calls[0][0] == "bot_resumed"
    assert calls[0][1].startswith("[manuell]")


def test_set_bot_paused_survives_broken_feed(monkeypatch):
    """Ein kaputtes Protokoll darf die Aktion selbst nie verhindern."""
    def _boom(*a, **k):
        raise RuntimeError("feed kaputt")

    monkeypatch.setattr("system.live_status.feed_emit", _boom)
    set_bot_paused(True)
    assert pause_status()["paused"] is True


def test_pause_status_fail_open_when_module_broken(monkeypatch):
    import system.bot_control as bc_mod
    monkeypatch.setattr(bc_mod, "get_status", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert pause_status()["paused"] is False


# ── service_state(): read-only, fail-open ────────────────────────────────────

def test_service_state_returns_known_value():
    """Gegen das echte systemd — der Dienst ist bewusst gestoppt, also
    "inactive" (oder "unknown", falls systemctl nicht verfügbar ist)."""
    assert service_state() in ("active", "inactive", "failed", "activating", "unknown")


def test_service_state_unknown_on_timeout(monkeypatch):
    import subprocess as sp

    def _boom(*a, **k):
        raise sp.TimeoutExpired(cmd="systemctl", timeout=2)

    monkeypatch.setattr("dashboard.controls.subprocess.run", _boom)
    assert service_state() == "unknown"


def test_service_state_unknown_on_garbage_output(monkeypatch):
    class _Out:
        stdout = "voellig unerwartete ausgabe"

    monkeypatch.setattr("dashboard.controls.subprocess.run", lambda *a, **k: _Out())
    assert service_state() == "unknown"


def test_service_state_never_starts_or_stops_the_service(monkeypatch):
    """Sicherheits-Vertrag: dieses Modul darf systemd NUR abfragen."""
    seen = []

    class _Out:
        stdout = "inactive"

    monkeypatch.setattr(
        "dashboard.controls.subprocess.run",
        lambda cmd, **k: (seen.append(cmd), _Out())[1],
    )
    service_state()
    assert seen == [["systemctl", "is-active", "aktien_bot.service"]]
    for cmd in seen:
        assert not any(verb in cmd for verb in ("start", "stop", "enable", "disable", "restart"))


# ── H1.3: Not-Aus-Reset ──────────────────────────────────────────────────────

def test_reset_circuit_breaker_clears_trigger():
    from portfolio.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker()
    cb.register_day_open(100_000.0)
    # 10 % Tagesverlust -> ueber dem 5 %-Limit -> ausgeloest
    assert cb.status(90_000.0)["triggered"] is True

    reset_circuit_breaker(90_000.0)
    assert CircuitBreaker().status(90_000.0)["triggered"] is False


def test_reset_circuit_breaker_preserves_previous_values_as_history():
    """Die Vorzustände dürfen nicht still verschwinden — sonst ist die
    Übersteuerung hinterher nicht mehr nachvollziehbar."""
    from portfolio.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker()
    cb.register_day_open(100_000.0)
    result = reset_circuit_breaker(80_000.0)

    assert result["prev_open_value"] == 100_000.0
    assert result["prev_peak_value"] == 100_000.0
    assert result["reset_to"] == 80_000.0
    assert result["by"] == "dashboard"
    assert result["at"]

    stored = CircuitBreaker().status(80_000.0)["last_reset"]
    assert stored["prev_open_value"] == 100_000.0


def test_reset_circuit_breaker_writes_manual_marker_to_feed(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "system.live_status.feed_emit",
        lambda event, ticker=None, detail=None: calls.append((event, detail)),
    )
    reset_circuit_breaker(50_000.0)
    assert calls[0][0] == "breaker_reset"
    assert calls[0][1].startswith("[manuell]")


def test_reset_circuit_breaker_fail_open_returns_none(monkeypatch):
    import portfolio.circuit_breaker as cb_mod

    class _Boom:
        def reset(self, *a, **k):
            raise RuntimeError("kaputt")

    monkeypatch.setattr(cb_mod, "CircuitBreaker", _Boom)
    assert reset_circuit_breaker(1000.0) is None
