"""
Tests für dashboard/factory/ (Vision W1, docs/DESIGN_FABRIK.md).
"""
import os

import pytest

from dashboard.factory import state as st_mod
from dashboard.factory.machines import machine_box
from dashboard.factory.scene import LAYOUT, build_scene_svg
from dashboard.factory.state import MACHINE_IDS, FactoryState, MachineState, read_state
from dashboard.theme import PALETTE


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

# ── W3.1: Tooltip-Join ────────────────────────────────────────────────────────

def test_tooltip_lines_joined_with_literal_entity():
    m = MachineState(id="lab", label="Labor", status="ok",
                     tooltip=["gelabelt: 42", "Gewinne: 20"])
    box = machine_box(m, 0, 0, 100, 100)
    assert "gelabelt: 42&#10;Gewinne: 20" in box


def test_tooltip_lines_escaped_individually_not_the_join_entity():
    m = MachineState(id="lab", label="Labor", status="ok",
                     tooltip=["<b>42</b>", "normal"])
    box = machine_box(m, 0, 0, 100, 100)
    assert "&lt;b&gt;42&lt;/b&gt;" in box
    assert "&amp;#10;" not in box  # die Trenn-Entity selbst bleibt unversehrt


# ── W3.2: Klick-Fokus per Query-Param ─────────────────────────────────────────

def test_machine_box_wrapped_in_self_link():
    m = MachineState(id="gate", label="Tor", status="ok")
    box = machine_box(m, 0, 0, 100, 100)
    assert '<a href="?factory=gate" target="_self">' in box


# ── W4.1: Ereignis-Framework ──────────────────────────────────────────────────

from datetime import datetime as _dt

from dashboard.factory.scene import scene_events


def _state_with_events(**event_flags):
    state = _empty_state()
    state.events = event_flags
    return state


def test_breaker_err_shows_notaus():
    state = _empty_state()
    state.machines["breaker"] = MachineState(id="breaker", label="Not-Aus", status="err")
    events = scene_events(state)
    assert any("NOT-AUS" in e for e in events)


def test_breaker_ok_shows_no_notaus():
    state = _empty_state()
    state.machines["breaker"] = MachineState(id="breaker", label="Not-Aus", status="ok")
    events = scene_events(state)
    assert not any("NOT-AUS" in e for e in events)


def test_hazard_active_shows_cloud():
    state = _state_with_events(hazard_active=True)
    events = scene_events(state)
    assert any("ellipse" in e for e in events)


def test_hazard_inactive_shows_no_cloud():
    state = _state_with_events(hazard_active=False)
    events = scene_events(state)
    assert not any("ellipse" in e for e in events)


def test_sl_cooldown_active_shows_sperrzone():
    state = _state_with_events(sl_cooldown_active=True)
    events = scene_events(state)
    assert any("SPERRZONE" in e for e in events)


def test_sl_cooldown_inactive_shows_nothing():
    state = _state_with_events(sl_cooldown_active=False)
    assert not any("SPERRZONE" in e for e in scene_events(state))


def test_read_events_all_flags_present_and_fail_open():
    from dashboard.factory.state import _read_events
    flags = _read_events()
    assert set(flags.keys()) == {
        "hazard_active", "sl_cooldown_active", "thesis_proven",
        "first_live_trade", "backup_ran_recently",
    }
    assert all(isinstance(v, bool) for v in flags.values())


def test_hazard_active_reflects_real_eonet_file(tmp_path, monkeypatch):
    import json
    f = tmp_path / "eonet_hazards.json"
    f.write_text(json.dumps({"data": {"hazard_label": "ELEVATED"}}))
    monkeypatch.setattr(st_mod, "_EONET_FILE", str(f))
    assert st_mod._hazard_active() is True

    f.write_text(json.dumps({"data": {"hazard_label": "NORMAL"}}))
    assert st_mod._hazard_active() is False


def test_sl_cooldown_active_reflects_real_unexpired_lock():
    """Nutzt die echte StopLossCooldown-Klasse (isoliert per
    conftest._isolate_sl_cooldown-Autouse-Fixture) statt der Rohdatei — so
    testen wir denselben Ablaufmechanismus, den auch all_blocked() nutzt."""
    from analyzers.sl_cooldown import StopLossCooldown
    StopLossCooldown(cooldown_days=2).record("GILD", 100.0)
    assert st_mod._sl_cooldown_active() is True


def test_sl_cooldown_inactive_when_no_locks():
    assert st_mod._sl_cooldown_active() is False


def test_sl_cooldown_inactive_when_lock_expired():
    """Der reale Bug, den der End-to-End-Check gegen echte Daten aufgedeckt
    hat: ein Cooldown-Eintrag von vor über einem Monat darf NICHT mehr als
    aktiv gelten, obwohl er noch in der Datei steht."""
    from datetime import datetime, timedelta, timezone
    from analyzers.sl_cooldown import StopLossCooldown, _FILE
    import json

    expired_ts = (datetime.now(timezone.utc).replace(tzinfo=None)
                 - timedelta(days=40)).isoformat()
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    with open(_FILE, "w") as f:
        json.dump({"GILD": {"price": 100.0, "timestamp": expired_ts}}, f)

    assert st_mod._sl_cooldown_active() is False


def test_thesis_proven_reflects_real_registry(tmp_path, monkeypatch):
    import json
    f = tmp_path / "thesis_registry.json"
    f.write_text(json.dumps({"a": {"status": "PENDING"}, "b": {"status": "PROVEN"}}))
    monkeypatch.setattr(st_mod, "_THESIS_REGISTRY_FILE", str(f))
    assert st_mod._thesis_proven() is True

    f.write_text(json.dumps({"a": {"status": "PENDING"}}))
    assert st_mod._thesis_proven() is False


# ── W4.2: Tag/Nacht ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("hour,expect_day", [(10, True), (2, False)])
def test_scene_sky_color_reflects_hour(hour, expect_day):
    from dashboard.theme import PALETTE
    state = _empty_state()
    svg_day = build_scene_svg(state, now=_dt(2026, 7, 15, hour))
    day_color = PALETTE["cobalt_hi"]
    night_color = PALETTE["bg"]
    if expect_day:
        assert f'fill="{day_color}" opacity="0.35"' in svg_day
    else:
        assert f'fill="{night_color}" opacity="0.35"' in svg_day


# ── W4.3: Echtes Wetter ────────────────────────────────────────────────────────

def test_weather_elevated_shows_rain():
    state = _empty_state()
    state.weather_demand_label = "ELEVATED"
    events = scene_events(state)
    assert any("fx-rain" in e for e in events)


def test_weather_subdued_shows_sun():
    state = _empty_state()
    state.weather_demand_label = "SUBDUED"
    events = scene_events(state)
    assert any(f'fill="{PALETTE["amber"]}"' in e for e in events)


def test_weather_normal_shows_no_overlay():
    state = _empty_state()
    state.weather_demand_label = "NORMAL"
    events = scene_events(state)
    assert not any("fx-rain" in e for e in events)
    assert not any(f'fill="{PALETTE["amber"]}"' in e for e in events)


def test_read_weather_demand_label_reflects_real_file(tmp_path, monkeypatch):
    import json
    f = tmp_path / "weather_macro.json"
    f.write_text(json.dumps({"data": {"demand_label": "ELEVATED"}}))
    monkeypatch.setattr(st_mod, "_WEATHER_MACRO_FILE", str(f))
    assert st_mod._read_weather_demand_label() == "ELEVATED"


def test_read_weather_demand_label_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(st_mod, "_WEATHER_MACRO_FILE", str(tmp_path / "nope.json"))
    assert st_mod._read_weather_demand_label() == ""


# ── W4.4: Easter Eggs ──────────────────────────────────────────────────────────

def test_first_live_trade_shows_golden_pennant():
    state = _state_with_events(first_live_trade=True)
    assert any("gold" in e for e in scene_events(state))


def test_no_live_trade_shows_no_pennant():
    state = _state_with_events(first_live_trade=False)
    assert not any("gold" in e for e in scene_events(state))


def test_thesis_proven_shows_statue():
    state = _state_with_events(thesis_proven=True)
    assert any("gold" in e for e in scene_events(state))


def test_backup_ran_recently_shows_robot_detail():
    state = _state_with_events(backup_ran_recently=True)
    events = scene_events(state)
    assert any("neon_green" not in e and PALETTE["neon_green"] in e for e in events)


def test_first_live_trade_exists_reflects_real_experience_store():
    from analyzers.experience_store import ExperienceStore
    store = ExperienceStore()
    did = store.upsert_decision({
        "ticker": "AAPL", "decided_at": "2026-01-01T10:00:00",
        "recommendation": "BUY", "direction": "LONG", "sentiment_score": 0.8,
        "confidence": "HIGH",
    })
    store.attach_outcome(did, {"outcome": "WIN", "pnl_pct": 5.0, "label_source": "live"})
    assert st_mod._first_live_trade_exists() is True


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
