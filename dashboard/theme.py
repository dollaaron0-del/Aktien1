"""
dashboard/theme.py — zentrale Stil-Quelle fürs Dashboard (Design-Roadmap D0).

16-Bit-Industrieautomations-Theme (Kobaltblau/Kupfer/Stahlgrau/Neon). Details
und Arbeitsprotokoll: docs/DESIGN_ROADMAP.md. Neue Styles gehören AUSNAHMSLOS
hierher — kein Tab definiert eigene Farben/CSS-Klassen.

DASHBOARD_THEME=plain (ENV) schaltet alles ab (Default: pixel/an). Jeder
Helfer prüft selbst is_enabled() und liefert bei plain schlichtes,
ungestyltes HTML/no-op zurück — Aufrufer brauchen keine eigene
Fallunterscheidung.
"""
from __future__ import annotations

import base64
import html
import os
from functools import lru_cache
from typing import Optional

import streamlit as st

PALETTE: dict[str, str] = {
    "bg":          "#14171C",  # Seiten-Hintergrund (Stahl, fast schwarz)
    "bg_panel":    "#1E232B",  # Panel-/Karten-Hintergrund
    "border":      "#3A4250",  # Panel-Rahmen (Stahl)
    "text":        "#E8ECF2",  # Primärtext
    "text_muted":  "#9AA4B2",  # gedämpfter Text
    "cobalt":      "#2E6BE6",  # Primär-Akzent (aktive Tabs, Zyklus-Events)
    "cobalt_hi":   "#4D8DFF",  # Hover/Highlight
    "copper":      "#C87533",  # Kupfer (Gate-Blocks, Warn-Sekundär, Deltas)
    "copper_hi":   "#E09A5A",
    "neon_green":  "#39FF88",  # OK-LED, Gewinne, Trades
    "amber":       "#FFC857",  # Warn-LED
    "red":         "#FF4D4D",  # Fehler-LED, Verluste
    "neon_cyan":   "#33E0FF",  # sparsam: Glow/Scanline/Sonder-Highlights
}

_LED_COLOR = {
    "ok":   PALETTE["neon_green"],
    "warn": PALETTE["amber"],
    "err":  PALETTE["red"],
    "off":  PALETTE["text_muted"],
}

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
_FONTS_DIR = os.path.join(_ASSETS_DIR, "fonts")
_IMG_DIR = os.path.join(_ASSETS_DIR, "img")


def is_enabled() -> bool:
    """DASHBOARD_THEME=plain schaltet das Pixel-Theme komplett aus (Default
    an). Einzige Stelle, die die ENV-Variable liest."""
    return os.getenv("DASHBOARD_THEME", "pixel").strip().lower() != "plain"


@lru_cache(maxsize=None)
def _font_face_css() -> str:
    """Lädt die lokal gebundelten Fonts (D0.4) als Base64-data-URI —
    kein Laufzeit-Netzzugriff. Fehlt eine Datei (Download schlug fehl),
    wird ihr @font-face-Block übersprungen; die CSS-Fallback-Kette
    ("Press Start 2P", monospace bzw. "VT323", "Courier New", monospace)
    greift dann automatisch."""
    faces = []
    fonts = (
        ("Press Start 2P", "PressStart2P-Regular.woff2"),
        ("VT323", "VT323-Regular.woff2"),
    )
    for family, filename in fonts:
        path = os.path.join(_FONTS_DIR, filename)
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            continue
        faces.append(f"""
@font-face {{
    font-family: '{family}';
    src: url(data:font/woff2;base64,{b64}) format('woff2');
    font-weight: 400;
    font-style: normal;
    font-display: swap;
}}""")
    return "\n".join(faces)


def _base_css() -> str:
    p = PALETTE
    return f"""
<style>
:root {{
    --px-bg: {p['bg']};
    --px-bg-panel: {p['bg_panel']};
    --px-border: {p['border']};
    --px-text: {p['text']};
    --px-text-muted: {p['text_muted']};
    --px-cobalt: {p['cobalt']};
    --px-cobalt-hi: {p['cobalt_hi']};
    --px-copper: {p['copper']};
    --px-copper-hi: {p['copper_hi']};
    --px-neon-green: {p['neon_green']};
    --px-amber: {p['amber']};
    --px-red: {p['red']};
    --px-neon-cyan: {p['neon_cyan']};
}}

{_font_face_css()}

.px-head {{
    font-family: "Press Start 2P", monospace;
    color: var(--px-cobalt);
    line-height: 1.6;
}}

.px-panel {{
    background: var(--px-bg-panel);
    border: 1px solid var(--px-border);
    border-radius: 4px;
    padding: 10px 14px;
    margin-bottom: 8px;
}}

.px-terminal {{
    font-family: "VT323", "Courier New", monospace;
    font-size: 1.15rem;
    background: #0E1116;
    color: var(--px-neon-green);
    border: 1px solid var(--px-border);
    border-radius: 4px;
    padding: 10px 14px;
    box-shadow: inset 0 0 12px rgba(57, 255, 136, 0.08);
    max-height: 420px;
    overflow-y: auto;
}}

.px-led {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: "VT323", "Courier New", monospace;
    font-size: 1.05rem;
    color: var(--px-text);
}}

.px-led::before {{
    content: "";
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
}}

.px-led--ok::before   {{ background: var(--px-neon-green); }}
.px-led--warn::before {{ background: var(--px-amber); animation: px-blink 1.2s infinite; }}
.px-led--err::before  {{ background: var(--px-red);   animation: px-blink 1.2s infinite; }}
.px-led--off::before  {{ background: var(--px-text-muted); }}

@keyframes px-blink {{
    0%, 100% {{ opacity: 1; }}
    50%      {{ opacity: 0.25; }}
}}
</style>
"""


def inject() -> None:
    """Rendert die Basis-CSS-Klassen + Fonts einmalig. No-Op bei
    DASHBOARD_THEME=plain (heutiges Aussehen bleibt exakt erhalten)."""
    if not is_enabled():
        return
    st.markdown(_base_css(), unsafe_allow_html=True)


def led(status: str, label: str) -> str:
    """Baut eine Status-LED als HTML-Span (`.px-led--ok/--warn/--err/--off`).
    Bei plain: einfacher Emoji-Text (altes Verhalten), damit Aufrufer nicht
    selbst unterscheiden müssen."""
    safe_label = html.escape(str(label))
    if not is_enabled():
        emoji = {"ok": "🟢", "warn": "🟡", "err": "🔴", "off": "⚪"}.get(status, "⚪")
        return f"{emoji} {safe_label}"
    cls = status if status in _LED_COLOR else "off"
    return f'<span class="px-led px-led--{cls}">{safe_label}</span>'


def panel(html_body: str) -> str:
    """Umschließt beliebiges HTML mit einem `.px-panel`-Div. Bei plain wird
    der Body unverändert durchgereicht (kein zusätzliches Markup)."""
    if not is_enabled():
        return html_body
    return f'<div class="px-panel">{html_body}</div>'


def image_b64(name: str) -> str:
    """Liest ein PNG aus dashboard/assets/img/ und gibt es als data-URI
    zurück. Fehlt die Datei, liefert es einen leeren String — Aufrufer
    lassen dann ihren Platzhalter stehen (Roadmap D5)."""
    path = os.path.join(_IMG_DIR, name)
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return ""
    return f"data:image/png;base64,{b64}"


_charts_registered = False


def register_chart_themes() -> None:
    """Registriert das Altair-Theme + Plotly-Template aus PALETTE (D2).
    No-Op bei plain (Bibliotheks-Defaults bleiben aktiv). Idempotent —
    Streamlit reruns dürfen den zweiten Aufruf nicht crashen lassen."""
    global _charts_registered
    if not is_enabled() or _charts_registered:
        return
    _charts_registered = True
