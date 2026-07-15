"""dashboard/factory — Fabrik-Wimmelbild-Tab (Vision W, docs/DESIGN_FABRIK.md).

Jede Maschine ist ein echtes Subsystem, ihr Zustand kommt aus echten Daten
(state.py) und wird als SVG dargestellt (scene.py/machines.py) — die Szene
ist eine dritte Darstellungsform neben Tabellen und Charts, kein Deko-Bild.
"""
from __future__ import annotations

from dashboard.factory.scene import build_scene_svg  # noqa: F401
from dashboard.factory.state import read_state  # noqa: F401


def render_scene() -> str:
    """Bequemlichkeits-Helfer: aktueller Zustand → fertiges SVG."""
    return build_scene_svg(read_state())
