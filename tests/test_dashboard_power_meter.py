"""
Tests für dashboard/power_meter.py (Design D8.2 — E-Werk-Stromzähler).

Liest über `analyzers.api_cost_tracker._FILE` — hier immer auf eine
Temp-Datei gepatcht (read-only-Vertrag trotzdem testen: das Modul darf
die Datei nie verändern).
"""
import json
from datetime import date

import pytest

import analyzers.api_cost_tracker as tracker_mod
from dashboard import power_meter

_TODAY = date(2026, 7, 16)


@pytest.fixture()
def _savings_file(tmp_path, monkeypatch):
    f = tmp_path / "api_savings.json"
    f.write_text(json.dumps({
        "total_cost_eur": 123.45,
        "total_saved_eur": 67.89,
        "claude_calls": 813,
        "ollama_skips": 517,
        "daily": {
            "2026-07-16": {"cost": 2.5, "saved": 1.0, "cache_saved": 0.5,
                           "claude": 10, "ollama_skips": 4},
            "2026-07-14": {"cost": 1.0, "saved": 0.0, "cache_saved": 0.0,
                           "claude": 5, "ollama_skips": 0},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(tracker_mod, "_FILE", str(f))
    return f


def test_read_energy_today_and_totals(_savings_file):
    e = power_meter.read_energy(days=14, today=_TODAY)
    assert e["today_cost"] == 2.5
    assert e["today_saved"] == 1.5          # saved + cache_saved
    assert e["today_claude"] == 10
    assert e["today_ollama"] == 4
    assert e["total_cost"] == 123.45
    assert e["total_saved"] == 67.89


def test_read_energy_history_has_gaps_as_zero(_savings_file):
    e = power_meter.read_energy(days=3, today=_TODAY)
    assert [h["date"] for h in e["history"]] == \
        ["2026-07-14", "2026-07-15", "2026-07-16"]
    assert e["history"][1]["cost"] == 0.0   # 15.7. fehlt in der Datei
    assert e["history"][2]["cost"] == 2.5


def test_read_energy_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker_mod, "_FILE", str(tmp_path / "nix.json"))
    e = power_meter.read_energy(today=_TODAY)
    assert e["today_cost"] == 0.0
    assert len(e["history"]) == 14


def test_read_energy_never_writes(_savings_file):
    before = _savings_file.read_bytes()
    power_meter.read_energy(today=_TODAY)
    assert _savings_file.read_bytes() == before


def test_meter_svg_spins_only_with_todays_cost(_savings_file):
    e = power_meter.read_energy(today=_TODAY)
    assert "fx-spin" in power_meter.meter_svg(e)
    e["today_cost"] = 0.0
    assert "fx-spin" not in power_meter.meter_svg(e)


def test_meter_svg_contains_split_and_savings(_savings_file):
    svg = power_meter.meter_svg(power_meter.read_energy(today=_TODAY))
    assert "Claude 10" in svg
    assert "Ollama 4" in svg
    assert "67.89" in svg
    assert "123.45" in svg


def test_meter_svg_empty_state_renders():
    svg = power_meter.meter_svg({"history": []})
    assert "STROMZÄHLER" in svg
    assert "fx-spin" not in svg
