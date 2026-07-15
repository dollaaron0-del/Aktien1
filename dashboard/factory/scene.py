"""
dashboard/factory/scene.py — komplette SVG-Szene der Fabrikhalle
(Vision W1.2, docs/DESIGN_FABRIK.md).

`build_scene_svg(state)` ist eine reine Funktion (State → SVG-String), leicht
isoliert testbar. Layout-Koordinaten sind bewusst FEST (kein Autolayout) —
Positionen ändern sich nur, wenn eine neue Maschine dazukommt. `scene_events()`
(Vision W4.1) ist der zweite Erweiterungspunkt für seltene Requisiten ohne
eigenen Kasten (Not-Aus, Wetter-Overlay, Easter Eggs) — Wachstums-Regel für
beide Fälle steht in machines.py (W4.5).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

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


def _sky_color(hour: int) -> str:
    """Vision W4.2: Tag/Nacht nach echter Server-Uhrzeit — kein
    Realismus-Anspruch (kein Übergang), nur ein ehrliches Signal statt
    Dauer-Tag."""
    return PALETTE["cobalt_hi"] if 6 <= hour < 20 else PALETTE["bg"]


def scene_events(state: FactoryState, *, now: Optional[datetime] = None) -> List[str]:
    """Requisiten für die Entdeckungs-Ebene (Vision W4.1/W4.3/W4.4) — jede
    einzelne an einen ECHTEN Zustand aus `state` gebunden, kein
    Zufalls-Deko-Generator. Reine Funktion (State → SVG-Snippets), `now`
    injizierbar für Tests."""
    p = PALETTE
    events = state.events or {}
    parts: List[str] = []

    # W4.1(a): Not-Aus-Rundumleuchte + Schild, wenn der Circuit-Breaker
    # ausgelöst hat (direkt aus dem bestehenden Maschinen-Status, keine
    # eigene Requisiten-Datenquelle nötig).
    breaker = state.machines.get("breaker")
    if breaker is not None and breaker.status == "err":
        bx, by, bw, _bh = LAYOUT["breaker"]
        cx, cy = bx + bw / 2, by - 16
        parts.append(
            f'<circle class="fx-blink" cx="{cx}" cy="{cy}" r="9" fill="{p["red"]}" />'
            f'<text x="{cx}" y="{cy + 24}" text-anchor="middle" '
            f'font-family="VT323, monospace" font-size="14" fill="{p["red"]}">NOT-AUS</text>'
        )

    # W4.1(b): dunkle Wolke überm Dach bei aktiven EONET-Naturgefahren.
    if events.get("hazard_active"):
        parts.append(
            f'<ellipse cx="{_WIDTH / 2}" cy="10" rx="140" ry="14" '
            f'fill="{p["text_muted"]}" opacity="0.5" />'
        )

    # W4.1(c): Absperrband/"Sperrzone" am Förderband bei aktivem SL-Cooldown.
    if events.get("sl_cooldown_active"):
        cx, cy, cw, _ch = LAYOUT["conveyor"]
        parts.append(
            f'<rect x="{cx}" y="{cy - 14}" width="{cw}" height="7" fill="{p["copper"]}" '
            f'opacity="0.85" />'
            f'<text x="{cx + cw / 2}" y="{cy - 18}" text-anchor="middle" '
            f'font-family="VT323, monospace" font-size="13" fill="{p["copper_hi"]}">'
            f'SPERRZONE</text>'
        )

    # W4.3: Regen/Sonne über der Wetterstation, passend zum echten
    # Wetter-Collector-Inhalt (ELEVATED=Extremwetter treibt Energienachfrage
    # → Regen/Sturm, SUBDUED=mildes Wetter → Sonne, NORMAL=kein Overlay).
    wx, wy, ww, wh = LAYOUT["weather"]
    if state.weather_demand_label == "ELEVATED":
        for i in range(5):
            dx = wx + 20 + i * (ww - 40) / 4
            parts.append(
                f'<line class="fx-rain" x1="{dx}" y1="{wy + wh + 4}" '
                f'x2="{dx - 6}" y2="{wy + wh + 18}" stroke="{p["cobalt_hi"]}" '
                f'stroke-width="2" />'
            )
    elif state.weather_demand_label == "SUBDUED":
        parts.append(
            f'<circle cx="{wx + ww - 20}" cy="{wy - 14}" r="12" fill="{p["amber"]}" />'
        )

    # W4.4(a): goldener Wimpel überm Verladetor beim ersten echten Live-Trade.
    if events.get("first_live_trade"):
        gx, gy, gw, _gh = LAYOUT["gate"]
        parts.append(
            f'<polygon points="{gx + gw / 2},{gy - 24} {gx + gw / 2 - 14},{gy - 8} '
            f'{gx + gw / 2 + 14},{gy - 8}" fill="gold" />'
        )

    # W4.4(b): goldene Statue vor der Halle, sobald eine These PROVEN ist.
    if events.get("thesis_proven"):
        parts.append(
            f'<rect x="{_WIDTH / 2 - 10}" y="{_HEIGHT - 60}" width="20" height="36" '
            f'fill="gold" /><circle cx="{_WIDTH / 2}" cy="{_HEIGHT - 66}" r="10" fill="gold" />'
        )

    # W4.4(c): zufriedener Nachtschicht-Roboter (Kaffeetassen-Andeutung),
    # wenn das Backup in der letzten Nacht durchgelaufen ist.
    if events.get("backup_ran_recently"):
        bx, by, _bw, bh = LAYOUT["backup_bot"]
        parts.append(
            f'<circle cx="{bx + 20}" cy="{by + bh - 10}" r="6" fill="none" '
            f'stroke="{p["neon_green"]}" stroke-width="2" />'
        )

    return parts


def build_scene_svg(state: FactoryState, *, now: Optional[datetime] = None) -> str:
    """Reine String-Arbeit — Vision W2.4 Performance-Check (15.7.2026, drei
    Läufe hintereinander mit echtem read_state()): 0.18ms / 0.07ms / 0.06ms
    pro Aufruf (SVG-Länge ~5,4KB), weit unter dem 50ms-Ziel. Der
    Datenzugriff in read_state() (Datei-/DB-Lesen) dominiert die
    Gesamtkosten, nicht der SVG-Bau selbst — kein Optimierungsbedarf.
    `now` (Vision W4.2, Tag/Nacht) ist injizierbar für Tests, Default =
    echte Serverzeit."""
    now = now or datetime.now()
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
        # Vision W4.2: Himmelsstreifen nach echter Server-Uhrzeit (kein
        # Übergang, nur ein ehrliches Tag/Nacht-Signal statt Dauer-Tag).
        f'<rect x="0" y="0" width="{_WIDTH}" height="14" '
        f'fill="{_sky_color(now.hour)}" opacity="0.35" />',
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

    parts.extend(scene_events(state))

    parts.append("</svg>")
    return "".join(parts)
