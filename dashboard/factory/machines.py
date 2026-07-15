"""
dashboard/factory/machines.py — SVG-Bausteine je Maschine (Vision W1.2).

Skelett-Formen (Rechteck + Status-LED + Label) — seit W5.1 rendert
`machine_box()` statt des Skelett-Rechtecks automatisch ein echtes
Pixel-Art-Asset (`dashboard/assets/img/factory_<id>.png` via
`theme.image_b64()`), sobald die Datei für eine Maschine existiert. Ohne
Datei bleibt das Rechteck der Fallback — am Aufrufer (scene.py) oder der
LED/Tooltip-Logik ändert sich dabei nichts, das Asset wird pro Maschine
unabhängig eingesetzt (W5.2: Bilder je Maschine liefern).

WACHSTUMS-REGEL (W4.5), zwei Fälle:

1. Neue MASCHINE (eigenes Subsystem, braucht einen eigenen Kasten in der
   Halle): `MachineState`-Leser in `state.py` (Muster: `_read_<id>()`,
   fail-open, in `_READERS` eintragen), Platz in `scene.LAYOUT`, Tooltip
   mit echten Zahlen, Test gegen eine echte (isolierte) Datenquelle wie die
   bestehenden. Die Box selbst braucht KEINEN neuen Code — `machine_box()`
   ist generisch, reicht automatisch über jede neue ID.
2. Neues EREIGNIS/Requisit (kein eigenes Subsystem, nur ein seltener
   Zustand): boolescher Flag in `state._read_events()` (fail-open,
   `dict`-Eintrag), neue Bedingung in `scene.scene_events()`, Test mit
   Flag an/aus. Kein Platz in LAYOUT nötig — Requisiten hängen frei über/
   neben einer bestehenden Maschine.

In beiden Fällen: nie Zufalls-Deko, jede Requisite/jeder Status hängt an
einer echten Datenquelle.
"""
from __future__ import annotations

import html

from dashboard.factory.state import MachineState
from dashboard.theme import PALETTE, image_b64

_STATUS_COLOR = {
    "ok":     PALETTE["neon_green"],
    "warn":   PALETTE["amber"],
    "err":    PALETTE["red"],
    "off":    PALETTE["border"],
    "active": PALETTE["cobalt"],
}

_MAX_DOCK_SLOTS = 10


def _dock_slots(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """Vision W2.2: die Laderampen als einzelne Slots je Collector-Quelle
    (healthy/weak/dead aus AnalysisLog.source_health(), state.py) statt
    einer Sammel-LED — jede Quelle bekommt ihre eigene, echte Farbe. Auf
    `_MAX_DOCK_SLOTS` gekappt, der Rest als "+n weitere"-Zeile."""
    healthy = list((m.payload or {}).get("healthy") or [])
    weak = list((m.payload or {}).get("weak") or [])
    dead = list((m.payload or {}).get("dead") or [])
    slots = (
        [(name, PALETTE["neon_green"]) for name in healthy]
        + [(name, PALETTE["amber"]) for name in weak]
        + [(name, PALETTE["border"]) for name in dead]
    )
    if not slots:
        return ""

    shown, rest_n = slots[:_MAX_DOCK_SLOTS], max(0, len(slots) - _MAX_DOCK_SLOTS)
    slot_h = 22
    parts: list[str] = []
    for i, (name, color) in enumerate(shown):
        sy = y + 24 + i * slot_h
        parts.append(
            f'<rect x="{x + 8}" y="{sy}" width="10" height="10" rx="2" fill="{color}" />'
            f'<text x="{x + 24}" y="{sy + 9}" font-family="VT323, monospace" '
            f'font-size="12" fill="{PALETTE["text_muted"]}">'
            f'{html.escape(str(name))[:18]}</text>'
        )
    if rest_n:
        sy = y + 24 + len(shown) * slot_h
        parts.append(
            f'<text x="{x + 8}" y="{sy + 9}" font-family="VT323, monospace" '
            f'font-size="12" fill="{PALETTE["text_muted"]}">+{rest_n} weitere</text>'
        )
    return "".join(parts)


def _activity_overlay(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """Zusätzliche, rein dekorative SVG-Elemente je Maschinen-Typ + Status
    (Vision W2.1) — laufendes Band überm Förderband, Rauch über den
    Analysatoren. Bewusst getrennt von machine_box(), damit die Basis-Box
    für ALLE Maschinen generisch bleibt. Alles CSS-animiert (keine
    Streamlit-Rerun-Kosten), abschaltbar über prefers-reduced-motion
    (siehe theme.py)."""
    parts: list[str] = []

    if m.id == "conveyor" and m.status == "active":
        parts.append(
            f'<rect class="fx-belt-run" x="{x + 10}" y="{y + h / 2 - 6}" '
            f'width="{w - 20}" height="12" '
            f'fill="url(#fx-belt-pattern)" opacity="0.6" />'
        )

    if m.id.startswith("analyzer_") and m.status == "active":
        cx = x + w / 2
        for i, dy in enumerate((0, 8, 16)):
            parts.append(
                f'<circle class="fx-smoke" style="animation-delay:{i * 0.4}s" '
                f'cx="{cx + dy - 8}" cy="{y - 6}" r="5" fill="{PALETTE["text_muted"]}" '
                f'opacity="0.5" />'
            )

    return "".join(parts)


def machine_box(m: MachineState, x: float, y: float, w: float, h: float,
                animate: bool = True) -> str:
    """Eine Maschine als Skelett-Box: Panel-Rechteck, Label (VT323), Status-LED
    oben rechts. `m.label`/Tooltip-Zeilen werden escaped. LED pulsiert
    (`fx-blink`, W2.1) bei warn/err — CSS-Keyframe in theme.py, dort auch die
    prefers-reduced-motion-Abschaltung. `animate=False` (Vision W2.3,
    Nachtmodus bei Pause) unterdrückt Blink/Band/Rauch komplett, unabhängig
    vom Status — die Halle steht dann wirklich still statt nur optisch."""
    color = _STATUS_COLOR.get(m.status, PALETTE["border"])
    label = html.escape(m.label)
    # W3.1: jede Tooltip-Zeile einzeln escaped, dann mit dem literalen
    # &#10;-Entity gejoint (NICHT durch html.escape() laufen lassen — das
    # würde das "&" zu "&amp;" verstümmeln und die Entity zerstören).
    tooltip_lines = [html.escape(str(line)) for line in m.tooltip] if m.tooltip else [label]
    tooltip_text = "&#10;".join(tooltip_lines)
    led_cx, led_cy, led_r = x + w - 14, y + 14, 6
    blinking = animate and m.status in ("warn", "err")
    led_cls = "fx-led fx-blink" if blinking else "fx-led"
    machine_id = html.escape(m.id)

    # W5.1: liegt ein echtes Pixel-Art-Asset vor (dashboard/assets/img/
    # factory_<id>.png), wird es statt der Skelett-Form gerendert — LED/
    # Tooltip/Klick-Link drumherum bleiben identisch. Fehlt die Datei
    # (image_b64 liefert dann ""), bleibt das Skelett-Rechteck der Fallback.
    asset_uri = image_b64(f"factory_{m.id}.png")
    if asset_uri:
        base_shape = (
            f'<image href="{asset_uri}" x="{x}" y="{y}" width="{w}" height="{h}" '
            f'preserveAspectRatio="xMidYMid slice" />'
        )
    else:
        base_shape = (
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" '
            f'fill="{PALETTE["bg_panel"]}" stroke="{PALETTE["border"]}" stroke-width="1.5" />'
        )

    inner = (
        f'<title>{label}&#10;{tooltip_text}</title>'
        f'{base_shape}'
        f'{_activity_overlay(m, x, y, w, h) if animate else ""}'
        f'{_dock_slots(m, x, y, w, h) if m.id == "docks" else ""}'
        f'<circle class="{led_cls}" cx="{led_cx}" cy="{led_cy}" r="{led_r}" fill="{color}" '
        f'data-status="{m.status}" />'
        f'<text class="fx-label" x="{x + w / 2}" y="{y + h - 10}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="15" fill="{PALETTE["text_muted"]}">'
        f'{label}</text>'
    )
    # W3.2: Klick-Fokus per Query-Param — die ganze Box ist ein Link auf
    # sich selbst mit ?factory=<id>, target="_self" verhindert einen neuen
    # Tab/Popup (Streamlit läuft im selben Frame). tabs/factory.py liest den
    # Parameter und rendert darunter ein Detail-Panel.
    return (
        f'<g class="fx-machine" data-machine-id="{machine_id}">'
        f'<a href="?factory={machine_id}" target="_self">{inner}</a>'
        f'</g>'
    )
