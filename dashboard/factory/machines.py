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

_STATUS_COLOR_KEY = {
    "ok":     "neon_green",
    "warn":   "amber",
    "err":    "red",
    "off":    "border",
    "active": "cobalt",
}


def _status_color(status: str) -> str:
    """H6.4: bewusst eine Funktion statt eines Modul-level-Dicts —
    `PALETTE[...]` live bei jedem Aufruf ausgewertet (die einzige Stelle
    im dashboard/-Baum, die PALETTE-Werte sonst beim Import eingefroren
    hätte; siehe theme.py._PaletteProxy)."""
    return PALETTE[_STATUS_COLOR_KEY.get(status, "border")]


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
        # D7.2(c): Rauch-Intensität = echter Routing-Anteil der letzten
        # Analysen (state.py liefert n/total im payload) — 1 Wolke bei
        # <25 %, bis 4 Wolken ab 75 %. Frugal-Routing wird so in der
        # Szene ablesbar: die Ollama-Werkbank soll stärker rauchen als
        # der teure Claude-Analysator.
        pl = m.payload or {}
        total = pl.get("total") or 0
        share = (pl.get("n") or 0) / total if total else 0.0
        n_puffs = 1 + min(3, int(share * 4))
        cx = x + w / 2
        for i in range(n_puffs):
            parts.append(
                f'<circle class="fx-smoke" style="animation-delay:{i * 0.4}s" '
                f'cx="{cx + i * 8 - 8}" cy="{y - 6}" r="5" fill="{PALETTE["text_muted"]}" '
                f'opacity="0.5" />'
            )

    return "".join(parts)


_MAX_CRATES = 12


def _warehouse_crates(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """D7.2(a): eine Kiste je offener Position, gestapelt am Boden des
    Lagers. Farbe = Haltedauer-Ratio (grün <0.8, amber <1.0, rot ab
    Zielüberschreitung — gleiche Schwellen wie der Portfolio-Tab);
    unbekanntes Alter = kobalt. Auf `_MAX_CRATES` gekappt."""
    positions = (m.payload or {}).get("positions") or {}
    if not positions:
        return ""
    parts: list[str] = []
    crate, gap, per_row = 18, 6, max(1, int((w - 24) // (18 + 6)))
    for i, (_t, info) in enumerate(list(positions.items())[:_MAX_CRATES]):
        ratio = (info or {}).get("age_ratio")
        if ratio is None:
            color = PALETTE["cobalt"]
        elif ratio >= 1.0:
            color = PALETTE["red"]
        elif ratio >= 0.8:
            color = PALETTE["amber"]
        else:
            color = PALETTE["neon_green"]
        row, col = divmod(i, per_row)
        cx0 = x + 12 + col * (crate + gap)
        cy0 = y + h - 34 - row * (crate + gap)
        parts.append(
            f'<rect x="{cx0:.0f}" y="{cy0:.0f}" width="{crate}" height="{crate}" '
            f'rx="2" fill="{color}" opacity="0.85" stroke="{PALETTE["bg"]}" '
            f'stroke-width="1.5" />'
        )
    rest = len(positions) - _MAX_CRATES
    if rest > 0:
        parts.append(
            f'<text x="{x + 12}" y="{y + 20}" font-family="VT323, monospace" '
            f'font-size="12" fill="{PALETTE["text_muted"]}">+{rest} weitere</text>'
        )
    return "".join(parts)


def _conveyor_counter(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """D7.2(b): mechanischer Durchsatz-Zähler am Förderband — heutige
    Entscheidungen aus dem echten Funnel (DecisionLog), dreistellig wie
    ein altes Zählwerk."""
    total = (m.payload or {}).get("total")
    if total is None:
        return ""
    shown = min(int(total), 999)
    return (
        f'<rect x="{x + w - 62}" y="{y + 8}" width="54" height="22" rx="3" '
        f'fill="#0A0C0F" stroke="{PALETTE["border"]}" stroke-width="1.5" />'
        f'<text x="{x + w - 35}" y="{y + 25}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="17" '
        f'fill="{PALETTE["neon_green"]}">{shown:03d}</text>'
    )


def _warehouse_counter(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """L3.6: Wareneingangs-/Warenausgangs-Zählwerk am Lager — heutige
    Zu-/Abgänge aus der echten trades-Tabelle. Zwei kleine Zählwerke
    (Muster _conveyor_counter, zweistellig: mehr als 99 Bewegungen an
    einem Tag gibt der Funnel nicht her). 0 wird ANGEZEIGT, nicht
    versteckt — „heute nichts bewegt" ist eine Information."""
    moves = (m.payload or {}).get("movements")
    if not moves:
        return ""
    cx, cy = x + w - 58, y + h - 30
    return (
        f'<rect x="{cx}" y="{cy}" width="50" height="20" rx="3" '
        f'fill="#0A0C0F" stroke="{PALETTE["border"]}" stroke-width="1.5" />'
        f'<text x="{cx + 13}" y="{cy + 15}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="14" '
        f'fill="{PALETTE["neon_green"]}">+{min(int(moves.get("in_today", 0)), 99):02d}</text>'
        f'<text x="{cx + 25}" y="{cy + 15}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="13" '
        f'fill="{PALETTE["text_muted"]}">/</text>'
        f'<text x="{cx + 38}" y="{cy + 15}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="14" '
        f'fill="{PALETTE["copper"]}">-{min(int(moves.get("out_today", 0)), 99):02d}</text>'
    )


def _backup_battery(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """D7.2(d): Batterie-Balken am Nachtschicht-Roboter — Ladestand =
    Frische des letzten Backups (voll direkt danach, leer nach 48h).
    Farbe folgt dem Maschinen-Status (ok/warn/err)."""
    age_h = (m.payload or {}).get("age_hours")
    if age_h is None:
        return ""
    charge = max(0.0, min(1.0, 1.0 - float(age_h) / 48.0))
    color = _status_color(m.status)
    bx, by, bw, bh = x + 10, y + 8, 34, 14
    return (
        f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="2" '
        f'fill="none" stroke="{PALETTE["text_muted"]}" stroke-width="1.5" />'
        f'<rect x="{bx + bw}" y="{by + 4}" width="3" height="{bh - 8}" '
        f'fill="{PALETTE["text_muted"]}" />'
        f'<rect x="{bx + 2}" y="{by + 2}" width="{(bw - 4) * charge:.1f}" '
        f'height="{bh - 4}" fill="{color}" opacity="0.9" />'
    )


def _machine_extras(m: MachineState, x: float, y: float, w: float, h: float) -> str:
    """D7.2: maschinen-spezifische Detail-Elemente, jedes an eine echte
    Datenquelle gebunden (Wachstums-Regel W4.5 gilt weiter). Fail-open —
    fehlt der payload, rendert das Extra einfach nichts."""
    if m.id == "warehouse":
        return _warehouse_crates(m, x, y, w, h) + _warehouse_counter(m, x, y, w, h)
    if m.id == "conveyor":
        return _conveyor_counter(m, x, y, w, h)
    if m.id == "backup_bot":
        return _backup_battery(m, x, y, w, h)
    return ""


def machine_box(m: MachineState, x: float, y: float, w: float, h: float,
                animate: bool = True) -> str:
    """Eine Maschine als Skelett-Box: Panel-Rechteck, Label (VT323), Status-LED
    oben rechts. `m.label`/Tooltip-Zeilen werden escaped. LED pulsiert
    (`fx-blink`, W2.1) bei warn/err — CSS-Keyframe in theme.py, dort auch die
    prefers-reduced-motion-Abschaltung. `animate=False` (Vision W2.3,
    Nachtmodus bei Pause) unterdrückt Blink/Band/Rauch komplett, unabhängig
    vom Status — die Halle steht dann wirklich still statt nur optisch."""
    color = _status_color(m.status)
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
        f'{_machine_extras(m, x, y, w, h)}'
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
