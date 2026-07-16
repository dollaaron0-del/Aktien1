"""
Tests für dashboard/warehouse_shelf.py (Design D8.3 — Lager-Detailregal).

Netzfrei: Kurse kommen als fertiges dict (ctx.prices-Vertrag),
Sektor-Profile aus einer Temp-Datei.
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from dashboard import warehouse_shelf

_NOW = datetime(2026, 7, 16)


def _pos(shares=10, entry=100.0, entry_date="2026-07-01", hold=15):
    return SimpleNamespace(shares=shares, entry_price=entry,
                           entry_date=entry_date, target_hold_days=hold)


@pytest.fixture()
def _profiles(tmp_path, monkeypatch):
    f = tmp_path / "profiles.json"
    f.write_text(json.dumps({
        "TSM": {"sector": "Technology"},
        "LLY": {"sector": "Healthcare"},
    }))
    monkeypatch.setattr(warehouse_shelf, "_PROFILES_FILE", str(f))
    return f


def test_shelf_data_groups_by_sector(_profiles):
    groups = warehouse_shelf.shelf_data(
        {"TSM": _pos(), "LLY": _pos(), "XXX": _pos()},
        {"TSM": 110.0, "LLY": 90.0, "XXX": 100.0},
        now=_NOW,
    )
    sectors = [g["sector"] for g in groups]
    assert sectors == ["Healthcare", "Sonstige", "Technology"]  # alphabetisch


def test_shelf_data_pnl_and_age(_profiles):
    groups = warehouse_shelf.shelf_data(
        {"TSM": _pos(shares=10, entry=100.0, entry_date="2026-07-01", hold=15)},
        {"TSM": 110.0},
        now=_NOW,
    )
    crate = groups[0]["crates"][0]
    assert crate["pnl_pct"] == pytest.approx(10.0)
    assert crate["age_days"] == 15
    assert crate["hold_days"] == 15


def test_shelf_data_missing_price_gives_none_pnl(_profiles):
    groups = warehouse_shelf.shelf_data({"TSM": _pos()}, {}, now=_NOW)
    crate = groups[0]["crates"][0]
    assert crate["pnl_pct"] is None
    assert crate["value"] == 1000.0  # Fallback: Einstandswert


def test_shelf_data_fill_relative_to_biggest(_profiles):
    groups = warehouse_shelf.shelf_data(
        {"TSM": _pos(shares=10), "LLY": _pos(shares=5)},
        {"TSM": 100.0, "LLY": 100.0},
        now=_NOW,
    )
    crates = {c["ticker"]: c for g in groups for c in g["crates"]}
    assert crates["TSM"]["fill_pct"] == 100.0
    assert crates["LLY"]["fill_pct"] == 50.0


def test_shelf_data_empty():
    assert warehouse_shelf.shelf_data({}, {}) == []


def test_shelf_data_broken_position_skipped(_profiles):
    broken = SimpleNamespace()  # ohne shares/entry_price
    groups = warehouse_shelf.shelf_data(
        {"TSM": _pos(), "KAPUTT": broken}, {"TSM": 100.0}, now=_NOW)
    tickers = [c["ticker"] for g in groups for c in g["crates"]]
    assert tickers == ["TSM"]


def test_shelf_svg_empty_state():
    svg = warehouse_shelf.shelf_svg([])
    assert "Lager leer" in svg
    assert "HOCHREGALLAGER" in svg


def test_shelf_svg_escapes_ticker(_profiles):
    groups = [{"sector": "X", "crates": [{
        "ticker": "<b>&", "value": 100.0, "pnl_pct": 1.0,
        "age_days": 1, "hold_days": 5, "fill_pct": 50.0,
    }]}]
    svg = warehouse_shelf.shelf_svg(groups)
    assert "<b>&" not in svg.replace("&lt;b&gt;&amp;", "")
    assert "&lt;b&gt;" in svg


def test_shelf_svg_shows_pnl_colors(_profiles):
    from dashboard.theme import PALETTE
    groups = warehouse_shelf.shelf_data(
        {"TSM": _pos(entry=100.0), "LLY": _pos(entry=100.0)},
        {"TSM": 110.0, "LLY": 90.0}, now=_NOW)
    svg = warehouse_shelf.shelf_svg(groups)
    assert "+10.0%" in svg
    assert "-10.0%" in svg
    assert PALETTE["neon_green"] in svg
    assert PALETTE["red"] in svg
