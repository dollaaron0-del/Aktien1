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
    """Reine String-Arbeit — Vision W2.4 Performance-Check (15.7.2026, drei
    Läufe hintereinander mit echtem read_state()): 0.18ms / 0.07ms / 0.06ms
    pro Aufruf (SVG-Länge ~5,4KB), weit unter dem 50ms-Ziel. Der
    Datenzugriff in read_state() (Datei-/DB-Lesen) dominiert die
    Gesamtkosten, nicht der SVG-Bau selbst — kein Optimierungsbedarf."""
    p = PALETTE
    parts = [
        f'<svg viewBox="0 0 {_WIDTH} {_HEIGHT}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:auto;">',
        # W2.1: geteiltes Streifenmuster fürs laufende Förderband (nur
        # sichtbar genutzt, wenn conveyor.status=="active" — machines.py
        # hängt die fx-belt-run-Klasse dann an ein Rect mit dieser Fill an).
        '<defs><pattern id="fx-belt-pattern" width="16" height="16" '
        'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="16" height="16" fill="{p["bg_panel"]}" />'
        f'<rect width="8" height="16" fill="{p["cobalt"]}" /></pattern></defs>',
        f'<rect x="0" y="0" width="{_WIDTH}" height="{_HEIGHT}" fill="{p["bg"]}" '
        f'stroke="{p["border"]}" />',
        # Boden
        f'<rect x="0" y="{_HEIGHT - 24}" width="{_WIDTH}" height="24" fill="{p["bg_panel"]}" />',
    ]

    # Vision W2.3: Nachtmodus bei Pause — die Halle steht wirklich still
    # (keine Animationen, unabhängig vom Einzelstatus), nicht nur optisch
    # gedimmt. Die Werksuhr bleibt normal (sie zeigt ja gerade den
    # Pause-/Wartezustand an) und wird deshalb NACH dem Dimm-Overlay
    # gezeichnet, damit sie nicht mit abgedunkelt wird.
    for machine_id, (x, y, w, h) in LAYOUT.items():
        if machine_id == "clock":
            continue
        m = state.machines.get(machine_id)
        if m is None:
            continue
        parts.append(machine_box(m, x, y, w, h, animate=not state.paused))

    if state.paused:
        parts.append(
            f'<rect class="fx-night-overlay" x="0" y="0" width="{_WIDTH}" height="{_HEIGHT}" '
            f'fill="{p["bg"]}" opacity="0.55" />'
        )

    clock = state.machines.get("clock")
    if clock is not None:
        x, y, w, h = LAYOUT["clock"]
        parts.append(machine_box(clock, x, y, w, h, animate=True))

    parts.append("</svg>")
    return "".join(parts)
