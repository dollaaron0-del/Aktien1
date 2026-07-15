"""
dashboard/factory/scene.py — komplette SVG-Szene der Fabrikhalle
(Vision W1.2, docs/DESIGN_FABRIK.md).

`build_scene_svg(state)` ist eine reine Funktion (State → SVG-String), leicht
isoliert testbar. Layout-Koordinaten sind bewusst FEST (kein Autolayout) —
Positionen ändern sich nur, wenn eine neue Maschine dazukommt (W4.5).
"""
from __future__ import annotations

from dashboard.factory.machines import machine_box
from dashboard.factory.state import FactoryState
from dashboard.theme import PALETTE

_WIDTH, _HEIGHT = 1200, 675

# Maschine → (x, y, w, h). Fest laut Design-Dokument.
LAYOUT = {
    "clock":           (500,  20, 200,  70),
    "weather":         (950,  20, 220,  90),
    "docks":           (20, 130, 180, 420),
    "analyzer_claude": (280, 150, 200, 130),
    "analyzer_ollama": (520, 150, 160, 110),
    "conveyor":        (240, 340, 620, 120),
    "warehouse":       (900, 250, 270, 220),
    "gate":            (930, 500, 240, 120),
    "lab":             (620, 500, 260, 120),
    "breaker":         (380, 510, 180, 100),
    "backup_bot":      (60, 580, 200,  80),
}


def build_scene_svg(state: FactoryState) -> str:
    p = PALETTE
    parts = [
        f'<svg viewBox="0 0 {_WIDTH} {_HEIGHT}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:auto;">',
        f'<rect x="0" y="0" width="{_WIDTH}" height="{_HEIGHT}" fill="{p["bg"]}" '
        f'stroke="{p["border"]}" />',
        # Boden
        f'<rect x="0" y="{_HEIGHT - 24}" width="{_WIDTH}" height="24" fill="{p["bg_panel"]}" />',
    ]

    for machine_id, (x, y, w, h) in LAYOUT.items():
        m = state.machines.get(machine_id)
        if m is None:
            continue
        parts.append(machine_box(m, x, y, w, h))

    parts.append("</svg>")
    return "".join(parts)
