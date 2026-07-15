"""
Tests für dashboard/instruments.py (Design D7.1 — Leitstand-Instrumente).

Reine SVG-String-Funktionen, daher direkt und schnell testbar (Muster:
test_dashboard_conveyor.py). Geprüft: Clamping, Zonen-/Schwellenfarben,
Escaping, 7-Segment-Korrektheit.
"""
import pytest

from dashboard.instruments import (
    _SEG_MAP,
    _clamp_pct,
    gauge_svg,
    seven_segment_svg,
    tank_svg,
)
from dashboard.theme import PALETTE


# ── Clamping ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    (-5, 0.0), (0, 0.0), (50, 50.0), (100, 100.0), (140, 100.0),
    (None, 0.0), ("kaputt", 0.0),
])
def test_clamp_pct(raw, expected):
    assert _clamp_pct(raw) == expected


# ── Manometer ────────────────────────────────────────────────────────────────

def test_gauge_contains_all_three_zones_and_needle():
    svg = gauge_svg(30, "KESSELDRUCK")
    assert svg.startswith('<svg class="px-instrument"')
    for color_key in ("neon_green", "amber", "red"):
        assert f'stroke="{PALETTE[color_key]}"' in svg
    assert "<line" in svg  # Nadel
    assert "KESSELDRUCK" in svg


def test_gauge_hub_blinks_only_above_85():
    assert "fx-blink" in gauge_svg(90, "X")
    assert "fx-blink" not in gauge_svg(85, "X")
    assert "fx-blink" not in gauge_svg(10, "X")


def test_gauge_hub_color_matches_zone():
    assert f'fill="{PALETTE["neon_green"]}" />' in gauge_svg(10, "X")
    assert f'fill="{PALETTE["amber"]}" />' in gauge_svg(70, "X")
    assert f'fill="{PALETTE["red"]}" />' in gauge_svg(95, "X")


def test_gauge_escapes_labels():
    svg = gauge_svg(50, "<script>x</script>", "<b>sub</b>")
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "&lt;b&gt;sub&lt;/b&gt;" in svg


def test_gauge_needle_moves_with_pct():
    assert gauge_svg(0, "X") != gauge_svg(100, "X")


# ── Tank ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fill,color_key", [
    (80, "neon_green"), (41, "neon_green"),
    (40, "amber"), (15, "amber"),
    (14, "red"), (0, "red"),
])
def test_tank_color_thresholds(fill, color_key):
    assert f'fill="{PALETTE[color_key]}" opacity="0.85"' in tank_svg(fill, "T")


def test_tank_fill_height_scales():
    full = tank_svg(100, "T")
    empty = tank_svg(0, "T")
    assert 'height="88.0"' in full   # ganze Tankhöhe
    assert 'height="0.0"' in empty


def test_tank_escapes_labels():
    svg = tank_svg(50, "<x>", "<y>")
    assert "<x>" not in svg and "&lt;x&gt;" in svg


# ── 7-Segment ────────────────────────────────────────────────────────────────

def test_seven_segment_lit_counts():
    """Jede Ziffer hat die klassische Segment-Anzahl (8=7, 1=2, 0=6)."""
    for ch, n_lit in (("8", 7), ("1", 2), ("0", 6), ("-", 1)):
        svg = seven_segment_svg(ch)
        assert svg.count('opacity="1.0"') == n_lit, ch
        assert svg.count('opacity="0.08"') == 7 - n_lit, ch


def test_seven_segment_dot_and_unknown_chars():
    # Punkt/Komma werden als kleiner Punkt gerendert, Unbekanntes übersprungen
    svg = seven_segment_svg("1.5")
    assert svg.count('opacity="1.0"') == 2 + 5  # "1" + "5"
    assert 'width="2.5"' in svg                 # der Punkt
    svg2 = seven_segment_svg("€$X42")
    assert svg2.count('opacity="1.0"') == (
        len(_SEG_MAP["4"]) + len(_SEG_MAP["2"])
    )


def test_seven_segment_escapes_label():
    svg = seven_segment_svg("42", "<script>")
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_seven_segment_width_grows_with_digits():
    short = seven_segment_svg("1")
    long = seven_segment_svg("123456")
    def _w(s):
        return float(s.split('viewBox="0 0 ')[1].split(" ")[0])
    assert _w(long) > _w(short)
