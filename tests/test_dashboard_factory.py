"""
Tests für dashboard/factory/ (Vision W1, docs/DESIGN_FABRIK.md).
"""
import os
from datetime import datetime

import pytest

from dashboard.factory import state as st_mod
from dashboard.factory.machines import machine_box
from dashboard.factory.scene import _CONNECTIONS, LAYOUT, _connection_paths, build_scene_svg
from dashboard.factory.state import MACHINE_IDS, FactoryState, MachineState, read_state
from dashboard.theme import PALETTE


def _empty_state(status="off") -> FactoryState:
    return FactoryState(
        machines={mid: MachineState(id=mid, label=mid.replace("_", " ").title(), status=status)
                  for mid in MACHINE_IDS},
        paused=False, generated_at="2026-07-15T10:00:00",
    )


def test_read_state_returns_all_twelve_machine_ids():
    # Karten-Umbau 18.7.2026: Kontrollraum (Einstellungen-Detailpanel) als
    # zwölfte Maschine dazugekommen.
    state = read_state()
    assert set(state.machines.keys()) == set(MACHINE_IDS)
    assert len(MACHINE_IDS) == 12


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
    nvda = m.payload["positions"]["NVDA"]
    assert nvda["shares"] == 5.0
    # Haltedauer-Ratio (D7.2): entry 1.7., Ziel 14 Tage — heute (15.7.)
    # exakt an der Zielgrenze; ratio muss berechnet und plausibel sein.
    assert nvda["age_ratio"] is not None and nvda["age_ratio"] >= 0.9


def test_read_warehouse_off_when_no_positions(fresh_portfolio):
    m = st_mod._read_warehouse()
    assert m.status == "off"


# ── L3.6: Lager-Zählwerk (Zu-/Abgänge) ───────────────────────────────────────

def _add_trade(pf, ticker, action, shares=1.0, ts=None):
    from datetime import datetime
    ts = ts or datetime.now().isoformat()
    pf._conn.execute(
        "INSERT INTO trades (ticker, action, shares, price, timestamp, pnl) "
        "VALUES (?,?,?,?,?,?)", (ticker, action, shares, 100.0, ts, 0.0),
    )
    pf._conn.commit()


def test_warehouse_movements_counts_todays_trades(fresh_portfolio):
    _add_trade(fresh_portfolio, "ZWH1", "BUY")
    _add_trade(fresh_portfolio, "ZWH2", "BUY")
    _add_trade(fresh_portfolio, "ZWH3", "SELL")
    moves = st_mod._warehouse_movements()
    assert moves["in_today"] == 2
    assert moves["out_today"] == 1


def test_warehouse_movements_ignores_other_days(fresh_portfolio):
    _add_trade(fresh_portfolio, "ZOLD", "BUY", ts="2020-01-01T10:00:00")
    moves = st_mod._warehouse_movements()
    assert moves["in_today"] == 0
    assert moves["recent"][0]["ticker"] == "ZOLD"  # in der Historie bleibt er


def test_warehouse_movements_recent_newest_first_and_limited(fresh_portfolio):
    for i in range(7):
        _add_trade(fresh_portfolio, f"ZR{i}", "BUY", ts=f"2026-07-1{i%10}T10:00:00")
    moves = st_mod._warehouse_movements(limit=5)
    assert len(moves["recent"]) == 5
    tss = [r["ts"] for r in moves["recent"]]
    assert tss == sorted(tss, reverse=True)


def test_warehouse_movements_zero_without_trades(fresh_portfolio):
    moves = st_mod._warehouse_movements()
    assert moves == {"in_today": 0, "out_today": 0, "recent": []}


def test_warehouse_movements_fail_open(monkeypatch):
    import portfolio.portfolio as port_mod
    monkeypatch.setattr(port_mod, "PORTFOLIO_DB", "/nicht/vorhanden/x.db")
    assert st_mod._warehouse_movements() == {"in_today": 0, "out_today": 0,
                                             "recent": []}


def test_read_warehouse_exposes_movements_and_tooltip(fresh_portfolio):
    _add_trade(fresh_portfolio, "ZWH1", "BUY")
    m = st_mod._read_warehouse()
    assert m.payload["movements"]["in_today"] == 1
    assert any("heute: +1 rein" in t for t in m.tooltip)


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
    assert m.payload == {"host": "127.0.0.1", "port": 1}


def test_read_weather_payload_includes_demand_label_and_timestamp(tmp_path, monkeypatch):
    import json
    regime_file = tmp_path / "current_regime.json"
    regime_file.write_text(json.dumps({"regime": "BULL", "timestamp": "2026-07-17T12:00:00"}))
    monkeypatch.setattr(st_mod, "_REGIME_FILE", str(regime_file))
    monkeypatch.setattr(st_mod, "_read_weather_demand_label", lambda: "ELEVATED")

    m = st_mod._read_weather()
    assert m.payload["demand_label"] == "ELEVATED"
    assert m.payload["timestamp"] == "2026-07-17T12:00:00"


def _make_backup(tmp_path, name, age_hours, size_bytes=1024):
    p = tmp_path / name
    p.write_bytes(b"0" * size_bytes)
    ts = datetime.now().timestamp() - age_hours * 3600
    os.utime(p, (ts, ts))
    return p


def test_read_backup_bot_lists_recent_backups_newest_first(tmp_path, monkeypatch):
    _make_backup(tmp_path, "aktien_backup_old.tar.gz", age_hours=48)
    _make_backup(tmp_path, "aktien_backup_new.tar.gz", age_hours=1)
    monkeypatch.setattr(st_mod, "_BACKUPS_DIR", str(tmp_path))

    m = st_mod._read_backup_bot()
    names = [r["name"] for r in m.payload["recent"]]
    assert names == ["aktien_backup_new.tar.gz", "aktien_backup_old.tar.gz"]
    assert m.payload["recent"][0]["age_hours"] == pytest.approx(1.0, abs=0.1)


def test_read_backup_bot_respects_recent_limit(tmp_path, monkeypatch):
    for i in range(7):
        _make_backup(tmp_path, f"aktien_backup_{i}.tar.gz", age_hours=i)
    monkeypatch.setattr(st_mod, "_BACKUPS_DIR", str(tmp_path))

    m = st_mod._read_backup_bot(recent_limit=3)
    assert len(m.payload["recent"]) == 3


def test_read_backup_bot_off_when_no_backups(tmp_path, monkeypatch):
    monkeypatch.setattr(st_mod, "_BACKUPS_DIR", str(tmp_path))
    m = st_mod._read_backup_bot()
    assert m.status == "off"


def test_read_control_room_warns_without_dashboard_password(monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    m = st_mod._read_control_room()
    assert m.status == "warn"
    assert m.payload["password_set"] is False


def test_read_control_room_ok_with_dashboard_password(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "s3cret")
    m = st_mod._read_control_room()
    assert m.status == "ok"
    assert m.payload["password_set"] is True


def test_read_control_room_fail_open_on_config_error(monkeypatch):
    import config as _config_mod
    monkeypatch.setattr(_config_mod, "config", None)
    m = st_mod._read_control_room()
    assert m.id == "control_room"
    assert m.status in ("off", "warn", "ok")


# ── scene.py / machines.py (W1.2) ────────────────────────────────────────────

def test_scene_contains_all_machine_labels():
    svg = build_scene_svg(_empty_state())
    for mid in MACHINE_IDS:
        assert mid.replace("_", " ").title() in svg


def test_scene_layout_has_entry_for_every_machine():
    assert set(LAYOUT.keys()) == set(MACHINE_IDS)


def _rects_overlap(r1, r2) -> bool:
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    return x1 < x2 + w2 and x2 < x1 + w1 and y1 < y2 + h2 and y2 < y1 + h1


def test_layout_boxes_do_not_overlap():
    """Vision W6 (Top-Down-Grundriss): mit elf Maschinen in einem Grid
    statt einer Reihe ist Überlappungsfreiheit eine echte, bisher nie
    geprüfte Invariante."""
    ids = list(LAYOUT.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            assert not _rects_overlap(LAYOUT[ids[i]], LAYOUT[ids[j]]), (
                f"{ids[i]} und {ids[j]} überlappen"
            )


def _state_with_statuses(statuses: dict) -> FactoryState:
    return FactoryState(
        machines={mid: MachineState(id=mid, label=mid, status=statuses.get(mid, "off"))
                  for mid in MACHINE_IDS},
        paused=False, generated_at="2026-07-17T10:00:00",
    )


def test_all_connections_reference_known_machine_ids_and_kinds():
    for src, dst, kind in _CONNECTIONS:
        assert src in MACHINE_IDS
        assert dst in MACHINE_IDS
        assert kind in ("main", "feedback", "utility")


def test_connection_paths_include_known_main_connection():
    svg = _connection_paths(_state_with_statuses({}))
    assert 'data-connection="docks-analyzer_claude"' in svg


def test_connection_only_animates_when_both_endpoints_active():
    active = _connection_paths(_state_with_statuses(
        {"docks": "ok", "analyzer_claude": "active"}))
    idle = _connection_paths(_state_with_statuses(
        {"docks": "ok", "analyzer_claude": "off"}))
    assert 'class="fx-pipe-flow" data-connection="docks-analyzer_claude"' in active
    assert 'class="fx-pipe-flow" data-connection="docks-analyzer_claude"' not in idle


def test_feedback_connection_always_dashed_regardless_of_status():
    all_active = _state_with_statuses({mid: "ok" for mid in MACHINE_IDS})
    svg = _connection_paths(all_active)
    assert 'data-connection="lab-analyzer_claude"' in svg
    assert 'class="fx-pipe-flow" data-connection="lab-analyzer_claude"' not in svg
    assert 'stroke-dasharray="6 5"' in svg


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
    assert m_claude.payload["route_breakdown"] == {"claude": 1}
    assert m_ollama.payload["route_breakdown"] == {"ollama_frugal_full": 1}


def test_analyzer_share_route_breakdown_separates_distinct_routes(monkeypatch):
    class _FakeLog:
        def get_recent(self, limit=50):
            return [
                {"provenance": {"model_route": "ollama_frugal_full"}},
                {"provenance": {"model_route": "ollama_frugal_full"}},
                {"provenance": {"model_route": "ollama_legacy"}},
            ]

    monkeypatch.setattr("analyzers.analysis_log.AnalysisLog", _FakeLog)
    m_ollama = st_mod._read_analyzer_ollama()
    assert m_ollama.payload["route_breakdown"] == {"ollama_frugal_full": 2, "ollama_legacy": 1}


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


def test_dock_slots_empty_payload_renders_nothing_extra(tmp_path, monkeypatch):
    # "docks" hat inzwischen ein echtes W5.2-Asset — isoliertes leeres
    # Verzeichnis erzwingt hier gezielt den Skelett-Pfad (siehe
    # test_machine_box_uses_skeleton_rect_without_asset_file).
    import dashboard.theme as theme_mod
    monkeypatch.setattr(theme_mod, "_IMG_DIR", str(tmp_path))

    m = MachineState(id="docks", label="Laderampen", status="off", payload={})
    box = machine_box(m, 0, 0, 180, 420)
    assert "<svg" not in box  # sanity: ist nur ein <g>-Fragment
    # Vision W6: Basis-Skelett hat jetzt 2 Rechtecke (Panel + Ziegel-
    # Dachkante, siehe machine_box()) statt vorher 1 — keine Slot-
    # Rechtecke bei leerem Payload kommen NICHT hinzu.
    assert box.count("<rect") == 2


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


# ── W5.1: Asset-Slots (echtes PNG statt Skelett-Form) ────────────────────────

def test_machine_box_uses_skeleton_rect_without_asset_file(tmp_path, monkeypatch):
    # "gate" hat inzwischen ein echtes W5.2-Asset — isoliertes leeres
    # Verzeichnis erzwingt hier gezielt den Skelett-Pfad.
    import dashboard.theme as theme_mod
    monkeypatch.setattr(theme_mod, "_IMG_DIR", str(tmp_path))

    m = MachineState(id="gate", label="Tor", status="ok")
    box = machine_box(m, 0, 0, 100, 100)
    assert "<rect" in box
    assert "<image" not in box


def test_machine_box_uses_image_when_asset_file_present(tmp_path, monkeypatch):
    import dashboard.theme as theme_mod
    img_dir = tmp_path / "img"
    img_dir.mkdir()
    (img_dir / "factory_gate.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(theme_mod, "_IMG_DIR", str(img_dir))

    m = MachineState(id="gate", label="Tor", status="ok")
    box = machine_box(m, 0, 0, 100, 100)
    assert '<image href="data:image/png;base64,' in box
    # Basis-Skelett-Rechteck weicht dem Bild, LED/Label/Link bleiben:
    assert "fx-led" in box
    assert "fx-label" in box
    assert '<a href="?factory=gate"' in box


def test_machine_box_only_uses_asset_for_matching_machine_id(tmp_path, monkeypatch):
    """Ein Asset für 'gate' darf nicht versehentlich bei einer anderen
    Maschine einspringen."""
    import dashboard.theme as theme_mod
    img_dir = tmp_path / "img"
    img_dir.mkdir()
    (img_dir / "factory_gate.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(theme_mod, "_IMG_DIR", str(img_dir))

    m = MachineState(id="warehouse", label="Lager", status="ok")
    box = machine_box(m, 0, 0, 100, 100)
    assert "<rect" in box
    assert "<image" not in box


# ── D7.2: Fabrik-Detailtiefe ─────────────────────────────────────────────────

def test_warehouse_crates_one_per_position_with_age_colors():
    m = MachineState(id="warehouse", label="Lager", status="ok", payload={
        "positions": {
            "JUNG": {"shares": 3, "age_ratio": 0.2},   # grün
            "REIF": {"shares": 2, "age_ratio": 0.9},   # amber
            "ALT":  {"shares": 1, "age_ratio": 1.3},   # rot
            "UNBEK": {"shares": 1, "age_ratio": None}, # kobalt
        },
    })
    box = machine_box(m, 0, 0, 270, 220)
    assert f'fill="{PALETTE["neon_green"]}" opacity="0.85"' in box
    assert f'fill="{PALETTE["amber"]}" opacity="0.85"' in box
    assert f'fill="{PALETTE["red"]}" opacity="0.85"' in box
    assert f'fill="{PALETTE["cobalt"]}" opacity="0.85"' in box
    assert box.count('opacity="0.85"') == 4  # genau eine Kiste je Position


def test_warehouse_counter_shows_both_directions():
    """L3.6: das Zählwerk zeigt Zu- UND Abgänge des Tages."""
    m = MachineState(id="warehouse", label="Lager", status="ok", payload={
        "positions": {}, "movements": {"in_today": 2, "out_today": 1,
                                       "recent": []},
    })
    box = machine_box(m, 0, 0, 270, 220)
    assert ">+02<" in box
    assert ">-01<" in box


def test_warehouse_counter_shows_zero_not_hidden():
    """0 Bewegungen ist eine Information ('heute nichts bewegt'), kein
    Grund das Zählwerk auszublenden."""
    m = MachineState(id="warehouse", label="Lager", status="off", payload={
        "positions": {}, "movements": {"in_today": 0, "out_today": 0,
                                       "recent": []},
    })
    box = machine_box(m, 0, 0, 270, 220)
    assert ">+00<" in box
    assert ">-00<" in box


def test_warehouse_counter_caps_at_99():
    m = MachineState(id="warehouse", label="Lager", status="ok", payload={
        "positions": {}, "movements": {"in_today": 250, "out_today": 0,
                                       "recent": []},
    })
    assert ">+99<" in machine_box(m, 0, 0, 270, 220)


def test_warehouse_counter_absent_without_movement_payload():
    m = MachineState(id="warehouse", label="Lager", status="off",
                     payload={"positions": {}})
    box = machine_box(m, 0, 0, 270, 220)
    assert ">+00<" not in box  # fail-open: kein payload -> kein Zählwerk


def test_warehouse_crates_capped_with_rest_hint():
    positions = {f"T{i}": {"shares": 1, "age_ratio": 0.1} for i in range(15)}
    m = MachineState(id="warehouse", label="Lager", status="ok",
                     payload={"positions": positions})
    box = machine_box(m, 0, 0, 270, 220)
    assert box.count('opacity="0.85"') == 12  # _MAX_CRATES
    assert "+3 weitere" in box


def test_warehouse_no_crates_without_positions():
    m = MachineState(id="warehouse", label="Lager", status="off", payload={})
    box = machine_box(m, 0, 0, 270, 220)
    assert 'opacity="0.85"' not in box


def test_conveyor_counter_shows_padded_total():
    m = MachineState(id="conveyor", label="Band", status="active",
                     payload={"total": 14})
    box = machine_box(m, 0, 0, 620, 120)
    assert ">014</text>" in box


def test_conveyor_counter_caps_at_999_and_hides_without_payload():
    m = MachineState(id="conveyor", label="Band", status="active",
                     payload={"total": 5000})
    assert ">999</text>" in machine_box(m, 0, 0, 620, 120)
    m2 = MachineState(id="conveyor", label="Band", status="off")
    assert "</text>" not in machine_box(m2, 0, 0, 620, 120).replace(
        "Band</text>", "")  # nur das Label, kein Zählwerk


def test_smoke_intensity_scales_with_routing_share():
    low = MachineState(id="analyzer_claude", label="C", status="active",
                       payload={"n": 5, "total": 50})    # 10 % → 1 Wolke
    high = MachineState(id="analyzer_ollama", label="O", status="active",
                        payload={"n": 45, "total": 50})  # 90 % → 4 Wolken
    assert machine_box(low, 0, 0, 200, 130).count("fx-smoke") == 1
    assert machine_box(high, 0, 0, 200, 130).count("fx-smoke") == 4


def test_backup_battery_charge_reflects_age():
    fresh = MachineState(id="backup_bot", label="R", status="ok",
                         payload={"age_hours": 0.0})
    stale = MachineState(id="backup_bot", label="R", status="err",
                         payload={"age_hours": 200.0})
    fresh_box = machine_box(fresh, 0, 0, 200, 80)
    stale_box = machine_box(stale, 0, 0, 200, 80)
    assert f'width="30.0"' in fresh_box   # (34-4) * 1.0 — voll
    assert f'width="0.0"' in stale_box    # leer (geclampt)
    assert f'fill="{PALETTE["neon_green"]}" opacity="0.9"' in fresh_box
    assert f'fill="{PALETTE["red"]}" opacity="0.9"' in stale_box


def test_backup_battery_hidden_without_age():
    m = MachineState(id="backup_bot", label="R", status="off")
    assert 'opacity="0.9"' not in machine_box(m, 0, 0, 200, 80)
