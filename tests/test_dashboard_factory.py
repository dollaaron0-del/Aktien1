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
