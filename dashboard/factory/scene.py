"""
dashboard/factory/scene.py — komplette SVG-Szene der Ziegel-Fabrik von oben
(Vision W1.2, erweitert Vision W6, docs/DESIGN_FABRIK.md).

`build_scene_svg(state)` ist eine reine Funktion (State → SVG-String), leicht
isoliert testbar. Layout-Koordinaten sind bewusst FEST (kein Autolayout) —
Positionen ändern sich nur, wenn eine neue Maschine dazukommt. `scene_events()`
(Vision W4.1) ist der zweite Erweiterungspunkt für seltene Requisiten ohne
eigenen Kasten (Not-Aus, Wetter-Overlay, Easter Eggs) — Wachstums-Regel für
beide Fälle steht in machines.py (W4.5).

Vision W6 (17.7.2026, User-Vorgabe "Fabrik aus Ziegelstein, Top-Down,
Maschinen sinnvoll verbunden, cozy wie Stardew Valley"): Kamera-Wechsel von
der ursprünglichen Seitenansicht-Halle auf einen Top-Down-Grundriss. Die
Reihenfolge oben→unten spiegelt den echten Datenfluss des Bots (Zulauf →
Analyse → Entscheidung → Lager/Sicherheit → Backoffice) — wie in
Factorio/Mindustry Rohstoffe oben rein-, Ergebnis unten rausfließt.
`_CONNECTIONS`/`_connection_paths()` zeichnen die Leitungen zwischen den
Maschinen; eine Leitung "fließt" nur, wenn beide Enden echt aktiv sind
(dieselbe Nur-echte-Daten-Regel wie `_activity_overlay`).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from dashboard.factory.machines import machine_box
from dashboard.factory.state import FactoryState
from dashboard.theme import PALETTE

_WIDTH, _HEIGHT = 1200, 820

# Maschine → (x, y, w, h). Fest laut Design-Dokument — Top-Down-Grundriss
# (Vision W6), Reihenfolge oben→unten = echter Datenfluss. Alle Boxen von
# Hand auf Nicht-Überlappung geprüft (test_layout_boxes_do_not_overlap).
LAYOUT = {
    "clock":           (500,  20, 200,  70),
    "weather":         (980,  20, 200,  90),
    "docks":           (20, 110, 200, 420),
    "analyzer_claude": (260, 150, 220, 140),
    "analyzer_ollama": (520, 150, 190, 140),
    "conveyor":        (260, 330, 560, 110),
    "gate":            (980, 320, 200, 110),
    "warehouse":       (260, 460, 280, 200),
    "breaker":         (580, 480, 170, 110),
    "lab":             (790, 460, 220, 140),
    "backup_bot":      (260, 690, 220,  90),
    # Karten-Umbau 18.7.2026: Kontrollraum als "Backoffice"-Pendant zum
    # Nachtschicht-Roboter — administrativ, kein Platz im Datenfluss,
    # daher symmetrisch am Fuß der Halle statt in der Pipeline-Reihenfolge.
    "control_room":    (960, 690, 220,  90),
    # Stufe 3 (24.7.2026): granulare Entscheidungs-Ketten-Maschinen zwischen
    # Förderband und Verladetor (risk_check → position_limit → gate) plus die
    # zwei Sammelstellen unten (Ausschuss/Warteschlange). Von Hand auf
    # Nicht-Überlappung geprüft (test_layout_boxes_do_not_overlap).
    "risk_check":      (835, 320, 135, 105),
    "position_limit":  (1030, 450, 150, 140),
    "ausschuss":       (730, 690, 200,  90),
    "queue":           (520, 690, 190,  90),
    # Stufe 4 (24.7.2026): restliche Ketten-Stationen (SVG-Fallback-Positionen;
    # die animierte Canvas-Szene hat ihr eigenes Hallen-Layout).
    "data_gate":       (240,  20, 180,  80),
    "catalyst_check":  (740,  20, 170,  80),
    "position_check":  (790, 130, 180,  80),
    "signal_check":    (790, 230, 180,  80),
}

# Vision W6: Leitungen zwischen Maschinen (Factorio/Mindustry-Optik) — jede
# spiegelt eine ECHTE Abhängigkeit im Bot-Code, keine erfundene Deko.
# "main" = Daten-/Entscheidungsfluss (fließt animiert, wenn beide Enden
# aktiv sind), "feedback" = Lern-Rückkopplung, "utility" = Wartung —
# beide immer gestrichelt/statisch (zeigen Beziehung, nicht Durchsatz).
_CONNECTIONS: List[Tuple[str, str, str]] = [
    ("docks", "analyzer_claude", "main"),
    ("docks", "analyzer_ollama", "main"),
    ("weather", "analyzer_claude", "main"),
    ("weather", "analyzer_ollama", "main"),
    ("clock", "conveyor", "main"),
    ("analyzer_claude", "conveyor", "main"),
    ("analyzer_ollama", "conveyor", "main"),
    ("conveyor", "warehouse", "main"),
    # Stufe 3: echte Entscheidungs-Kette Förderband → Risiko → Positions-Limit
    # → Verladetor (ersetzt die frühere Direkt-Leitung conveyor→gate).
    ("conveyor", "risk_check", "main"),
    ("risk_check", "position_limit", "main"),
    ("position_limit", "gate", "main"),
    ("conveyor", "ausschuss", "utility"),
    ("queue", "conveyor", "utility"),
    ("warehouse", "breaker", "main"),
    ("warehouse", "lab", "main"),
    ("lab", "analyzer_claude", "feedback"),
    ("lab", "analyzer_ollama", "feedback"),
    ("warehouse", "backup_bot", "utility"),
    # W7.9 (18.7.2026, User-Vorgabe zu den Referenzbildern: "jedes Tool
    # bekommt seine eigene Maschine, alles verbunden" — dichter vernetzt
    # per ECHTER Code-Abhängigkeit, keine erfundene Deko, Prinzip bleibt):
    # Circuit-Breaker blockiert Kaufentscheidungen wirklich
    # (strategy/swing_strategy.py:224 _circuit_breaker_active()).
    ("breaker", "conveyor", "feedback"),
    # Lern-Daten UND Entscheidungs-Log werden mitgesichert
    # (scripts/backup.sh: experience.db / decision_log.db).
    ("lab", "backup_bot", "utility"),
    ("conveyor", "backup_bot", "utility"),
    # Konfiguration steuert die Kaufschwelle direkt
    # (strategy/swing_strategy.py:280 config.buy_threshold).
    ("control_room", "conveyor", "utility"),
    # Konfiguration bestimmt, welcher Broker/welches Gateway läuft
    # (main.py:223 config.broker_mode == "ibkr").
    ("control_room", "gate", "utility"),
]


def _box_center(rect: Tuple[float, float, float, float]) -> Tuple[float, float]:
    x, y, w, h = rect
    return x + w / 2, y + h / 2


def _connector_endpoints(
    r1: Tuple[float, float, float, float], r2: Tuple[float, float, float, float]
) -> Tuple[float, float, float, float]:
    """Vision W6: Anker-Punkt an der jeweils näherliegenden Kante — bei
    überwiegend vertikalem Fluss Boden-Mitte→Kopf-Mitte, bei überwiegend
    horizontalem Fluss Seiten-Mitte→Seiten-Mitte. Reine Geometrie, kein
    Pfadfinding nötig (feste Positionen, keine Hindernisse dazwischen)."""
    x1, y1, w1, h1 = r1
    x2, y2, w2, h2 = r2
    c1x, c1y = _box_center(r1)
    c2x, c2y = _box_center(r2)
    dx, dy = c2x - c1x, c2y - c1y
    if abs(dy) >= abs(dx):
        if dy >= 0:
            return c1x, y1 + h1, c2x, y2
        return c1x, y1, c2x, y2 + h2
    if dx >= 0:
        return x1 + w1, c1y, x2, c2y
    return x1, c1y, x2 + w2, c2y


def _pipe_joints(x1: float, y1: float, x2: float, y2: float, src_id: str, dst_id: str) -> str:
    """W7.8 (18.7.2026, User-Referenzbilder Factorio-Cluster): kurze
    Muffen-Striche quer zur Leitung in fester Schrittweite — mehr
    visuelles Gewicht/Rohr-Optik wie in den Factorio-Screenshots, aber
    bewusst in der bestehenden Ziegel-Palette (Kupfer) statt eines
    Stil-/Farbwechsels. Nur auf geraden (horizontalen/vertikalen)
    Segmenten sinnvoll — genau das, was `_connector_endpoints` liefert."""
    length = math.hypot(x2 - x1, y2 - y1)
    step = 22
    n = int(length // step)
    if n < 2:
        return ""
    horizontal = abs(x2 - x1) >= abs(y2 - y1)
    parts: List[str] = []
    for i in range(1, n):
        t = i * step / length
        jx, jy = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        if horizontal:
            jx1, jy1, jx2, jy2 = jx, jy - 5, jx, jy + 5
        else:
            jx1, jy1, jx2, jy2 = jx - 5, jy, jx + 5, jy
        parts.append(
            f'<line data-connection-joint="{src_id}-{dst_id}" '
            f'x1="{jx1:.0f}" y1="{jy1:.0f}" x2="{jx2:.0f}" y2="{jy2:.0f}" '
            f'stroke="{PALETTE["copper"]}" stroke-width="2" opacity="0.6" />'
        )
    return "".join(parts)


def _belt_treads(x1: float, y1: float, x2: float, y2: float, src_id: str, dst_id: str) -> str:
    """W8.6 (18.7.2026, User-Vorgabe zu den neuen Referenzbildern:
    "orientiere dich sehr stark daran und hol dir Inspiration wie
    Maschinen und Förderbänder aussehen könnten") — NUR für kind="main"
    (echter Waren-/Datenfluss): Rollen-Chevrons statt der Kupfer-Muffen
    aus `_pipe_joints`, näher am Förderband-Look der neuen Referenzbilder
    (Rollen-Segmente quer zum Band, Pfeilspitzen in Flussrichtung). Die
    Unterscheidung main=Band/feedback+utility=Rohr bleibt (Vision W6) —
    nur main-Leitungen sind ein echter Warenfluss, nur die sollen wie ein
    Förderband aussehen; `_pipe_joints` bleibt für die anderen unverändert."""
    length = math.hypot(x2 - x1, y2 - y1)
    step = 16
    n = int(length // step)
    if n < 2:
        return ""
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    nx, ny = -uy, ux
    parts: List[str] = []
    for i in range(1, n):
        t = i * step / length
        cx, cy = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        tip_x, tip_y = cx + ux * 4, cy + uy * 4
        base1_x, base1_y = cx - ux * 3 + nx * 5, cy - uy * 3 + ny * 5
        base2_x, base2_y = cx - ux * 3 - nx * 5, cy - uy * 3 - ny * 5
        parts.append(
            f'<polyline data-connection-joint="{src_id}-{dst_id}" '
            f'points="{base1_x:.0f},{base1_y:.0f} {tip_x:.0f},{tip_y:.0f} '
            f'{base2_x:.0f},{base2_y:.0f}" fill="none" '
            f'stroke="{PALETTE["copper_hi"]}" stroke-width="2" '
            f'stroke-linecap="round" opacity="0.7" />'
        )
    return "".join(parts)


def _connection_paths(state: FactoryState) -> str:
    """Vision W6: Leitungen zwischen Maschinen — "fließt" (fx-pipe-flow)
    NUR, wenn es eine echte Daten-Verbindung ("main") ist UND beide
    Enden gerade aktiv sind (dieselbe Nur-echte-Daten-Regel wie
    `_activity_overlay`); "feedback"/"utility" bleiben immer gestrichelt/
    statisch — sie zeigen eine Beziehung, keinen Durchsatz.

    W7.8: dickere Gehäuse-Linie + Kupfer-Muffen darunter (Factorio-
    Anleihe aus den User-Referenzbildern vom 18.7., nur mehr Gewicht,
    keine Farb-/Stiländerung) — die eigentliche (ggf. fließende)
    Leitung kommt zuletzt oben drauf, exakt wie vorher.

    W8.6: main-Leitungen (echter Waren-/Datenfluss) bekommen Rollen-
    Chevrons (`_belt_treads`) statt der Kupfer-Muffen — sehen jetzt aus
    wie ein echtes Förderband. feedback/utility bleiben bei den
    ursprünglichen Muffen (`_pipe_joints`), die zeigen weiter nur eine
    Beziehung, keinen Warenfluss."""
    p = PALETTE
    parts: List[str] = []
    for src_id, dst_id, kind in _CONNECTIONS:
        if src_id not in LAYOUT or dst_id not in LAYOUT:
            continue
        x1, y1, x2, y2 = _connector_endpoints(LAYOUT[src_id], LAYOUT[dst_id])
        src = state.machines.get(src_id)
        dst = state.machines.get(dst_id)
        flowing = (
            kind == "main" and src is not None and dst is not None
            and src.status in ("ok", "active") and dst.status in ("ok", "active")
        )
        cls = "fx-pipe-flow" if flowing else ""
        stroke = p["cobalt"] if flowing else p["border"]
        dash_attr = "" if kind == "main" else ' stroke-dasharray="6 5"'
        parts.append(
            f'<line data-connection-casing="{src_id}-{dst_id}" '
            f'x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{p["border"]}" stroke-width="7" stroke-linecap="round" opacity="0.5" />'
        )
        if kind == "main":
            parts.append(_belt_treads(x1, y1, x2, y2, src_id, dst_id))
        else:
            parts.append(_pipe_joints(x1, y1, x2, y2, src_id, dst_id))
        parts.append(
            f'<line class="{cls}" data-connection="{src_id}-{dst_id}" '
            f'x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" '
            f'stroke="{stroke}" stroke-width="3"{dash_attr} opacity="0.85" />'
        )
    return "".join(parts)


def _crate_travel_marker(state: FactoryState) -> str:
    """W7.10 (18.7.2026, User-Vorgabe wörtlich: "wenn eine Aktie gekauft
    wird, wandert die Kiste über das Förderband ins Lager"): eine
    sichtbare Kiste, die entlang der conveyor→warehouse-Leitung wandert
    — macht die bisher abstrakte fx-pipe-flow-Farb-Animation wörtlich.
    Nur gerendert, wenn diese Leitung wirklich fließt (dieselbe Nur-
    echte-Daten-Regel wie `_connection_paths`) UND der Bot nicht
    pausiert ist (W2.3-Nachtmodus-Konvention). Reine CSS-Animation
    (offset-path), kein Rerun-Kostenaufwand; `fx-crate-travel` ist in
    theme.py in derselben prefers-reduced-motion-Abschaltliste wie die
    übrigen fx-*-Animationen."""
    if state.paused or "conveyor" not in LAYOUT or "warehouse" not in LAYOUT:
        return ""
    src = state.machines.get("conveyor")
    dst = state.machines.get("warehouse")
    flowing = (
        src is not None and dst is not None
        and src.status in ("ok", "active") and dst.status in ("ok", "active")
    )
    if not flowing:
        return ""
    x1, y1, x2, y2 = _connector_endpoints(LAYOUT["conveyor"], LAYOUT["warehouse"])
    p = PALETTE
    return (
        f'<g class="fx-crate-travel" data-crate="conveyor-warehouse" '
        f"style=\"offset-path: path('M {x1:.0f} {y1:.0f} L {x2:.0f} {y2:.0f}');\">"
        f'<rect x="-7" y="-6" width="14" height="12" '
        f'fill="{p["copper_hi"]}" stroke="{p["border"]}" stroke-width="1.5" />'
        f'<line x1="-7" y1="0" x2="7" y2="0" stroke="{p["border"]}" stroke-width="1.5" />'
        f"</g>"
    )


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
        f'preserveAspectRatio="xMidYMid meet" '
        f'style="width:100%; height:auto; display:block;">',
        '<defs>'
        # W2.1: geteiltes Streifenmuster fürs laufende Förderband (nur
        # sichtbar genutzt, wenn conveyor.status=="active" — machines.py
        # hängt die fx-belt-run-Klasse dann an ein Rect mit dieser Fill an).
        '<pattern id="fx-belt-pattern" width="16" height="16" '
        'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="16" height="16" fill="{p["bg_panel"]}" />'
        f'<rect width="8" height="16" fill="{p["cobalt"]}" /></pattern>'
        # Vision W6: Ziegel-Musterung (Running-Bond) für den Baukörper —
        # rein prozedural, kein Bild-Asset nötig (echte Sprites bleiben
        # W5.2, User-Task).
        '<pattern id="fx-brick-pattern" width="40" height="20" '
        'patternUnits="userSpaceOnUse">'
        f'<rect width="40" height="20" fill="{p["brick"]}" />'
        f'<rect x="0" y="0" width="40" height="20" fill="none" '
        f'stroke="{p["border"]}" stroke-width="1.5" />'
        f'<line x1="20" y1="0" x2="20" y2="10" stroke="{p["border"]}" stroke-width="1.5" />'
        f'<line x1="0" y1="10" x2="40" y2="10" stroke="{p["border"]}" stroke-width="1.5" />'
        f'<line x1="20" y1="10" x2="20" y2="20" stroke="{p["border"]}" stroke-width="1.5" />'
        '</pattern>'
        '</defs>',
        # Vision W6: Werksgelände (Rasen) als äußerer Canvas, der Ziegel-
        # Baukörper sitzt darauf eingerückt — top-down "Grundriss von oben"
        # statt der alten Seitenansicht-Halle.
        f'<rect x="0" y="0" width="{_WIDTH}" height="{_HEIGHT}" fill="{p["grass"]}" />',
        f'<rect x="10" y="10" width="{_WIDTH - 20}" height="{_HEIGHT - 20}" '
        f'fill="url(#fx-brick-pattern)" stroke="{p["border"]}" stroke-width="2" />',
        # Vision W4.2: Himmelsstreifen nach echter Server-Uhrzeit (kein
        # Übergang, nur ein ehrliches Tag/Nacht-Signal statt Dauer-Tag).
        f'<rect x="0" y="0" width="{_WIDTH}" height="14" '
        f'fill="{_sky_color(now.hour)}" opacity="0.35" />',
    ]

    # Vision W6: Leitungen zwischen Maschinen VOR den Maschinen-Boxen, damit
    # sie optisch "unter" den Gebäuden verlaufen (wie Förderbänder/Rohre in
    # Factorio/Mindustry, die zwischen den Gebäuden hindurchlaufen).
    parts.append(_connection_paths(state))
    parts.append(_crate_travel_marker(state))

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
