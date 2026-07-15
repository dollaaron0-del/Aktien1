"""
Tests für dashboard/factory/ (Vision W1, docs/DESIGN_FABRIK.md).
"""
import pytest

from dashboard.factory import state as st_mod
from dashboard.factory.machines import machine_box
from dashboard.factory.scene import LAYOUT, build_scene_svg
from dashboard.factory.state import MACHINE_IDS, FactoryState, MachineState, read_state


def _empty_state(status="off") -> FactoryState:
    return FactoryState(
        machines={mid: MachineState(id=mid, label=mid.replace("_", " ").title(), status=status)
                  for mid in MACHINE_IDS},
        paused=False, generated_at="2026-07-15T10:00:00",
    )


def test_read_state_returns_all_eleven_machine_ids():
    state = read_state()
    assert set(state.machines.keys()) == set(MACHINE_IDS)
    assert len(MACHINE_IDS) == 11


def test_read_state_has_paused_bool_and_timestamp():
    state = read_state()
    assert isinstance(state.paused, bool)
    assert state.generated_at


def test_read_state_is_fail_open_when_every_reader_raises(monkeypatch):
    def _boom():
        raise RuntimeError("kaputt")

    for machine_id in MACHINE_IDS:
        monkeypatch.setitem(st_mod._READERS, machine_id, _boom)

    state = read_state()
    assert set(state.machines.keys()) == set(MACHINE_IDS)
    assert all(m.status == "off" for m in state.machines.values())


def test_read_state_fail_open_when_bot_control_raises(monkeypatch):
    monkeypatch.setattr("system.bot_control.is_paused", lambda: (_ for _ in ()).throw(RuntimeError()))
    state = read_state()
    assert state.paused is False  # Fail-open-Default


# ── Detail-Leser gegen echte (isolierte) Datenquellen ────────────────────────

def test_read_conveyor_reflects_real_decision_log_funnel():
    from analyzers.decision_log import DecisionLog
    from datetime import datetime, timezone
    dlog = DecisionLog()
    dlog.log({"ticker": "AAPL", "action": "BUY", "reason": "Test",
              "recommendation": "BUY", "sentiment_score": 0.8})

    m = st_mod._read_conveyor()
    assert m.id == "conveyor"
    assert m.status == "active"
    assert m.payload.get("total", 0) >= 1


def test_read_warehouse_reflects_real_portfolio_positions(fresh_portfolio):
    from portfolio.portfolio import Position

    fresh_portfolio._conn.execute(
        "INSERT INTO positions (ticker, shares, entry_price, entry_date, "
        "stop_loss, take_profit, target_hold_days) VALUES (?,?,?,?,?,?,?)",
        ("NVDA", 5.0, 100.0, "2026-07-01", 90.0, 130.0, 14),
    )
    fresh_portfolio._conn.commit()

    m = st_mod._read_warehouse()
    assert m.id == "warehouse"
    assert m.status == "ok"
    assert m.payload.get("NVDA") == 5.0


def test_read_warehouse_off_when_no_positions(fresh_portfolio):
    m = st_mod._read_warehouse()
    assert m.status == "off"


@pytest.mark.parametrize("regime,expected_status", [
    ("BULL", "ok"), ("NEUTRAL", "warn"), ("BEAR", "err"), ("CRISIS", "err"),
])
def test_read_weather_reflects_real_regime_file(tmp_path, monkeypatch, regime, expected_status):
    import json
    regime_file = tmp_path / "current_regime.json"
    regime_file.write_text(json.dumps({"regime": regime}))
    monkeypatch.setattr(st_mod, "_REGIME_FILE", str(regime_file))

    m = st_mod._read_weather()
    assert m.status == expected_status
    assert m.payload["regime"] == regime


def test_read_weather_off_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(st_mod, "_REGIME_FILE", str(tmp_path / "does-not-exist.json"))
    m = st_mod._read_weather()
    assert m.status == "off"


def test_read_gate_err_when_ibkr_unreachable(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "ibkr_host", "127.0.0.1")
    monkeypatch.setattr(config, "ibkr_port", 1)  # kein Listener dort
    m = st_mod._read_gate()
    assert m.status == "err"


# ── scene.py / machines.py (W1.2) ────────────────────────────────────────────

def test_scene_contains_all_machine_labels():
    svg = build_scene_svg(_empty_state())
    for mid in MACHINE_IDS:
        assert mid.replace("_", " ").title() in svg


def test_scene_layout_has_entry_for_every_machine():
    assert set(LAYOUT.keys()) == set(MACHINE_IDS)


def test_scene_renders_without_error_when_all_off():
    svg = build_scene_svg(_empty_state(status="off"))
    assert "<svg" in svg and "</svg>" in svg


def test_scene_skips_unknown_machine_gracefully():
    """Ein State ohne alle Maschinen (z.B. teilweiser Mock) darf die Szene
    nicht crashen lassen."""
    state = FactoryState(machines={}, paused=False, generated_at="x")
    svg = build_scene_svg(state)
    assert "<svg" in svg


def test_machine_box_escapes_label_and_tooltip():
    m = MachineState(id="docks", label="<script>alert(1)</script>", status="ok",
                     tooltip=["<img onerror=alert(1)>"])
    box = machine_box(m, 0, 0, 100, 100)
    assert "<script>alert(1)</script>" not in box
    assert "<img onerror=alert(1)>" not in box
    assert "&lt;script&gt;" in box


def test_machine_box_led_color_matches_status():
    m_ok = MachineState(id="gate", label="Tor", status="ok")
    m_err = MachineState(id="gate", label="Tor", status="err")
    from dashboard.theme import PALETTE
    assert PALETTE["neon_green"] in machine_box(m_ok, 0, 0, 50, 50)
    assert PALETTE["red"] in machine_box(m_err, 0, 0, 50, 50)


def test_render_scene_convenience_wrapper_runs_end_to_end():
    from dashboard.factory import render_scene
    svg = render_scene()
    assert "<svg" in svg


def test_analyzer_share_counts_model_route_prefix(monkeypatch):
    class _FakeLog:
        def get_recent(self, limit=50):
            return [
                {"provenance": {"model_route": "claude"}},
                {"provenance": {"model_route": "ollama_frugal_full"}},
                {"provenance": {}},
            ]

    monkeypatch.setattr("analyzers.analysis_log.AnalysisLog", _FakeLog)
    m_claude = st_mod._read_analyzer_claude()
    m_ollama = st_mod._read_analyzer_ollama()
    assert m_claude.status == "active"
    assert m_ollama.status == "active"
    assert "1/3" in m_claude.tooltip[0]


# ── W2.1: Aktivitäts-Animationen ─────────────────────────────────────────────

def test_belt_runs_only_when_conveyor_active():
    active = MachineState(id="conveyor", label="Förderband", status="active")
    idle = MachineState(id="conveyor", label="Förderband", status="off")
    assert "fx-belt-run" in machine_box(active, 0, 0, 100, 100)
    assert "fx-belt-run" not in machine_box(idle, 0, 0, 100, 100)


def test_smoke_only_for_active_analyzer():
    active = MachineState(id="analyzer_claude", label="Claude", status="active")
    idle = MachineState(id="analyzer_claude", label="Claude", status="off")
    assert "fx-smoke" in machine_box(active, 0, 0, 100, 100)
    assert "fx-smoke" not in machine_box(idle, 0, 0, 100, 100)


def test_no_activity_overlay_for_unrelated_machine_types():
    """Nur conveyor/analyzer_* bekommen die Overlays — z.B. warehouse
    nicht, auch wenn sein Status zufaellig 'active' waere."""
    m = MachineState(id="warehouse", label="Lager", status="active")
    box = machine_box(m, 0, 0, 100, 100)
    assert "fx-belt-run" not in box
    assert "fx-smoke" not in box


@pytest.mark.parametrize("status", ["warn", "err"])
def test_led_blinks_on_warn_and_err(status):
    m = MachineState(id="gate", label="Tor", status=status)
    assert "fx-blink" in machine_box(m, 0, 0, 100, 100)


@pytest.mark.parametrize("status", ["ok", "off", "active"])
def test_led_does_not_blink_otherwise(status):
    m = MachineState(id="gate", label="Tor", status=status)
    assert "fx-blink" not in machine_box(m, 0, 0, 100, 100)


def test_scene_defines_belt_pattern_once():
    svg = build_scene_svg(_empty_state())
    assert svg.count('id="fx-belt-pattern"') == 1


# ── W2.2: Rampen-Slots ────────────────────────────────────────────────────────

def test_dock_slots_show_all_sources_when_under_cap():
    m = MachineState(id="docks", label="Laderampen", status="ok",
                     payload={"healthy": ["yahoo", "sec_8k"], "weak": ["reddit"], "dead": ["twitter"]})
    box = machine_box(m, 0, 0, 180, 420)
    assert "yahoo" in box and "sec_8k" in box and "reddit" in box and "twitter" in box
    assert "weitere" not in box


def test_dock_slots_capped_with_rest_count():
    sources = [f"quelle_{i}" for i in range(14)]
    m = MachineState(id="docks", label="Laderampen", status="ok",
                     payload={"healthy": sources, "weak": [], "dead": []})
    box = machine_box(m, 0, 0, 180, 420)
    assert "quelle_0" in box
    assert "quelle_9" in box
    assert "quelle_10" not in box
    assert "+4 weitere" in box


def test_dock_slots_escape_source_names():
    m = MachineState(id="docks", label="Laderampen", status="ok",
                     payload={"healthy": ["<script>alert(1)</script>"], "weak": [], "dead": []})
    box = machine_box(m, 0, 0, 180, 420)
    assert "<script>alert(1)</script>" not in box
    assert "&lt;script&gt;" in box


def test_dock_slots_empty_payload_renders_nothing_extra():
    m = MachineState(id="docks", label="Laderampen", status="off", payload={})
    box = machine_box(m, 0, 0, 180, 420)
    assert "<svg" not in box  # sanity: ist nur ein <g>-Fragment
    assert box.count("<rect") == 1  # nur die Basis-Box, keine Slot-Rechtecke


def test_non_dock_machine_ignores_payload_source_lists():
    """Nur docks bekommt Slots — andere Maschinen mit zufaellig aehnlichem
    Payload duerfen keine Slot-Rechtecke zeigen."""
    m = MachineState(id="warehouse", label="Lager", status="ok",
                     payload={"healthy": ["yahoo"]})
    box = machine_box(m, 0, 0, 180, 420)
    assert "yahoo" not in box


# ── W2.3: Nachtmodus bei Pause ────────────────────────────────────────────────

def test_scene_shows_night_overlay_when_paused():
    state = _empty_state()
    state.paused = True
    svg = build_scene_svg(state)
    assert "fx-night-overlay" in svg


def test_scene_no_night_overlay_when_active():
    state = _empty_state()
    state.paused = False
    svg = build_scene_svg(state)
    assert "fx-night-overlay" not in svg


def test_paused_scene_suppresses_animation_even_for_active_machines():
    state = _empty_state(status="active")
    state.paused = True
    svg = build_scene_svg(state)
    assert "fx-belt-run" not in svg
    assert "fx-smoke" not in svg


def test_paused_scene_still_renders_clock():
    state = _empty_state()
    state.paused = True
    svg = build_scene_svg(state)
    assert 'data-machine-id="clock"' in svg


def test_machine_box_animate_false_suppresses_blink():
    m = MachineState(id="gate", label="Tor", status="err")
    assert "fx-blink" not in machine_box(m, 0, 0, 100, 100, animate=False)
    assert "fx-blink" in machine_box(m, 0, 0, 100, 100, animate=True)


# ── W2.4: Performance-Regressionsschutz ──────────────────────────────────────

def test_build_scene_svg_stays_well_under_50ms_budget():
    """Reine String-Arbeit, keine I/O in build_scene_svg() selbst — großzügige
    Schwelle (10x das gemessene Ist), damit der Test nicht auf einer
    langsamen CI-Maschine flackert, aber eine echte Regression trotzdem
    auffällt."""
    import time
    state = _empty_state()
    t0 = time.perf_counter()
    for _ in range(10):
        build_scene_svg(state)
    avg_ms = (time.perf_counter() - t0) / 10 * 1000
    assert avg_ms < 50
