"""
dashboard/factory/canvas.py — animierte Canvas-Fabrik (Vision-Transfer 24.7.2026).

Liefert eine in sich geschlossene HTML-Seite (Canvas + Engine
`scene_canvas.js`), die per `st.components.v1.html` in einem iframe läuft —
`st.markdown` würde die Skripte strippen, Animation braucht also den iframe.

`build_scene_html(state)` ist eine reine Funktion (FactoryState → HTML-String),
genau wie `scene.build_scene_svg`: die echten Daten (Status/Payload je Maschine,
Pause, Ereignisse) kommen aus `state.read_state()` und werden als JSON in die
Engine injiziert; die Farben aus `theme.PALETTE` (beide Themes). Der SVG-Pfad
(scene.py) bleibt unangetastet — er trägt weiterhin Plain-Theme, Archiv-Replay,
Report-Schnappschuss und die Mobile-Ansicht.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass

from dashboard.theme import PALETTE

_JS_PATH = os.path.join(os.path.dirname(__file__), "scene_canvas.js")

# Logische Canvas-Größe (muss zu scene_canvas.js WIDTH/HEIGHT passen).
SCENE_W, SCENE_H = 1200, 820
# iframe-Höhe für components.html — Seitenverhältnis bei ~1140px Containerbreite.
SCENE_EMBED_HEIGHT = 800

# Palette-Schlüssel, die die Engine nutzt (theme.PALETTE ist ein Proxy —
# hier bewusst eine feste Liste, damit ein fehlender Key nie die Szene bricht).
_PALETTE_KEYS = (
    "bg", "bg_panel", "border", "text", "text_muted", "cobalt", "cobalt_hi",
    "copper", "copper_hi", "neon_green", "amber", "red", "brick", "grass",
)


def _palette_dict() -> dict:
    out = {}
    for k in _PALETTE_KEYS:
        try:
            out[k] = PALETTE[k]
        except Exception:
            out[k] = "#888888"
    return out


def _machine_dict(m) -> dict:
    if is_dataclass(m):
        return {"id": m.id, "label": m.label, "status": m.status,
                "tooltip": list(m.tooltip or []), "payload": m.payload or {}}
    # Fallback: schon ein dict
    return {"id": m.get("id"), "label": m.get("label"), "status": m.get("status", "off"),
            "tooltip": list(m.get("tooltip") or []), "payload": m.get("payload") or {}}


def state_to_dict(state) -> dict:
    """FactoryState → JSON-fähiges dict (nur was die Engine braucht)."""
    machines = {}
    try:
        for mid, m in (state.machines or {}).items():
            machines[mid] = _machine_dict(m)
    except Exception:
        machines = {}
    return {
        "machines": machines,
        "paused": bool(getattr(state, "paused", False)),
        "events": dict(getattr(state, "events", {}) or {}),
        "weather_demand_label": getattr(state, "weather_demand_label", "") or "",
        "generated_at": getattr(state, "generated_at", ""),
    }


def _read_js() -> str:
    with open(_JS_PATH, encoding="utf-8") as fh:
        return fh.read()


def build_scene_html(state) -> str:
    """Fertige HTML-Seite mit animiertem Canvas, an den echten State gebunden.
    Fail-open: kann die Engine/JSON nicht gebaut werden, wirft der Aufrufer
    (tabs/factory.py) auf den SVG-Pfad zurück."""
    js = _read_js()
    palette_json = json.dumps(_palette_dict())
    state_json = json.dumps(state_to_dict(state), default=str)
    js = js.replace("__PALETTE__", palette_json).replace("__STATE__", state_json)
    bg = _palette_dict().get("bg", "#14171C")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>"
        "html,body{margin:0;padding:0;background:" + bg + ";overflow:hidden;}"
        "#factory{display:block;width:100%;height:auto;max-width:" + str(SCENE_W) + "px;"
        "margin:0 auto;image-rendering:auto;}"
        "</style></head><body>"
        "<canvas id='factory'></canvas>"
        "<script>" + js + "</script>"
        "</body></html>"
    )


def render_scene_html() -> str:
    """Bequemlichkeits-Helfer: aktueller Zustand → fertiges Canvas-HTML."""
    from dashboard.factory.state import read_state
    return build_scene_html(read_state())
