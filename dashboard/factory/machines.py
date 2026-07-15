"""
dashboard/factory/machines.py — SVG-Bausteine je Maschine (Vision W1.2).

Skelett-Formen (Rechteck + Status-LED + Label) — W5 ersetzt diese später
Stück für Stück durch echte Pixel-Art-Assets (`theme.image_b64`), ohne dass
sich am Aufrufer (scene.py) oder der LED/Tooltip-Logik etwas ändert.

WACHSTUMS-REGEL (W4.5): eine neue Bot-Funktion bekommt künftig "ihre
Maschine" — MachineState-Leser in state.py, Platz in scene.LAYOUT, Box hier
(reicht automatisch über machine_box(), keine neue Funktion nötig), Tooltip,
Test.
"""
from __future__ import annotations

import html

from dashboard.factory.state import MachineState
from dashboard.theme import PALETTE

_STATUS_COLOR = {
    "ok":     PALETTE["neon_green"],
    "warn":   PALETTE["amber"],
    "err":    PALETTE["red"],
    "off":    PALETTE["border"],
    "active": PALETTE["cobalt"],
}


def machine_box(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """Eine Maschine als Skelett-Box: Panel-Rechteck, Label (VT323), Status-LED
    oben rechts. `m.label`/Tooltip-Zeilen werden escaped."""
    color = _STATUS_COLOR.get(m.status, PALETTE["border"])
    label = html.escape(m.label)
    tooltip_text = html.escape("\n".join(m.tooltip)) if m.tooltip else label
    led_cx, led_cy, led_r = x + w - 14, y + 14, 6

    return (
        f'<g class="fx-machine" data-machine-id="{html.escape(m.id)}">'
        f'<title>{label}&#10;{tooltip_text}</title>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" '
        f'fill="{PALETTE["bg_panel"]}" stroke="{PALETTE["border"]}" stroke-width="1.5" />'
        f'<circle class="fx-led" cx="{led_cx}" cy="{led_cy}" r="{led_r}" fill="{color}" '
        f'data-status="{m.status}" />'
        f'<text class="fx-label" x="{x + w / 2}" y="{y + h - 10}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="15" fill="{PALETTE["text_muted"]}">'
        f'{label}</text>'
        f'</g>'
    )
