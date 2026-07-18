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
from collections.abc import Mapping
from functools import lru_cache
from typing import Optional

import streamlit as st

_PALETTE_PIXEL: dict[str, str] = {
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
    # Vision W6 (17.7.): Ziegel/Cozy-Akzente NUR für die Fabrik-Szene
    # (dashboard/factory/) — der Rest des Dashboards bleibt beim
    # bestätigten Industrie-Neon-Look, diese zwei Keys werden sonst
    # nirgends referenziert.
    "brick":       "#B5563A",  # Ziegelrot (Baukörper der Fabrik von oben)
    "grass":       "#5B7A52",  # gedecktes Werksgelände-Grün
}

# H6.4: drittes Theme "Blaupause" — technische Zeichnung, weiß auf Blau.
# Gleiche Schlüssel wie _PALETTE_PIXEL (Pflicht, sonst brechen alle
# bestehenden PALETTE["..."]-Zugriffe im ganzen dashboard/-Baum).
PALETTE_BLUEPRINT: dict[str, str] = {
    "bg":          "#0B2A4A",
    "bg_panel":    "#123A61",
    "border":      "#3D6A96",
    "text":        "#E8F1FF",
    "text_muted":  "#9FC2E8",
    "cobalt":      "#66AEFF",
    "cobalt_hi":   "#9CCBFF",
    "copper":      "#E8C170",
    "copper_hi":   "#F4D999",
    "neon_green":  "#7CFFC4",
    "amber":       "#FFD98A",
    "red":         "#FF8A8A",
    "neon_cyan":   "#8EEBFF",
    "brick":       "#E8A87C",  # blaupausen-warmer Ziegelton
    "grass":       "#8FBFAE",  # blaupausen-warmes Werksgelände-Grün
}


def _current_palette_name() -> str:
    """Einzige Stelle, die DASHBOARD_THEME auf einen Palette-Namen
    abbildet — "blueprint" ist ein drittes, aktives (nicht-plain) Theme."""
    raw = os.getenv("DASHBOARD_THEME", "pixel").strip().lower()
    return "blueprint" if raw == "blueprint" else "pixel"


def _current_palette() -> dict[str, str]:
    return PALETTE_BLUEPRINT if _current_palette_name() == "blueprint" else _PALETTE_PIXEL


class _PaletteProxy(Mapping):
    """H6.4: PALETTE bleibt nach außen ein ganz normales Dict-artiges
    Objekt (`PALETTE["bg"]`, `p = PALETTE; p["bg"]`, `.items()`, …) —
    aber löst bei JEDEM Zugriff live gegen `_current_palette()` auf,
    statt einmalig beim Modul-Import eingefroren zu werden. Dadurch
    brauchte der Wechsel auf ein drittes Theme KEINE Änderung an den
    ~35 bestehenden `PALETTE[...]`-Zugriffsstellen im ganzen
    dashboard/-Baum (grep-Liste vorher erstellt, siehe Commit-Text) —
    einzige Ausnahme war `factory/machines.py`s `_STATUS_COLOR`, das
    PALETTE-Werte in einem MODUL-level-Dict eingefroren hatte (dort zu
    einer Funktion gemacht)."""

    def __getitem__(self, key):
        return _current_palette()[key]

    def __iter__(self):
        return iter(_current_palette())

    def __len__(self):
        return len(_current_palette())


PALETTE = _PaletteProxy()

_LED_COLOR = {
    "ok":   "neon_green",
    "warn": "amber",
    "err":  "red",
    "off":  "text_muted",
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


def _legacy_css() -> str:
    """Exakt der alte Inline-CSS-Block aus app.py (vor D1.1) — der
    plain-Zustand ist wortwörtlich das heutige Aussehen, kein Redesign."""
    return """
<style>
/* Tighter metric cards */
[data-testid="metric-container"] {
    background: #1e2130;
    border: 1px solid #2d3250;
    border-radius: 10px;
    padding: 12px 16px;
}
/* Tab font */
button[data-baseweb="tab"] { font-size: 0.9rem; font-weight: 600; }
/* Regime badge helpers */
.regime-bull    { color: #00e676; font-weight: 700; font-size: 1.1rem; }
.regime-neutral { color: #ffd740; font-weight: 700; font-size: 1.1rem; }
.regime-bear    { color: #ff7043; font-weight: 700; font-size: 1.1rem; }
.regime-crisis  { color: #f44336; font-weight: 700; font-size: 1.1rem; }
.badge-pending  { color: #ffd740; }
.badge-ok       { color: #00e676; }
.badge-red      { color: #f44336; }
</style>
"""


def _crt_enabled() -> bool:
    """D7.4: CRT-Atmosphäre (Scanlines/Vignette) — die EINZIGE reine Optik
    im Design (alles andere hängt an echten Daten). Default an, per
    DASHBOARD_CRT=0 abschaltbar, falls sie auf einem Beamer/Monitor stört."""
    return os.getenv("DASHBOARD_CRT", "1").strip().lower() not in ("0", "off", "false")


def _crt_css() -> str:
    if not _crt_enabled():
        return ""
    # Sehr dezent gehalten (Gesamt-Abdunklung ≤ ~6 %): feine statische
    # Scanlines + leichte Rand-Vignette. Statisch = kein reduced-motion-
    # Problem; pointer-events:none = keine Interaktions-Störung.
    return """
/* D7.4 CRT-Atmosphäre (rein optisch, DASHBOARD_CRT=0 schaltet ab) */
body::after {
    content: "";
    position: fixed;
    inset: 0;
    z-index: 9999;
    pointer-events: none;
    background:
        repeating-linear-gradient(0deg,
            rgba(0, 0, 0, 0.05) 0px, rgba(0, 0, 0, 0.05) 1px,
            transparent 1px, transparent 3px),
        radial-gradient(ellipse at center,
            transparent 60%, rgba(0, 0, 0, 0.14) 100%);
}
"""


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

/* W8.1 (18.7.2026, User-Vorgabe: "die Fabrik soll im Prinzip das
   Einzigste sein [...] Zusatzinformationen werden am Rand eingeblendet
   ähnlich wie bei einem Base-Bau-Spiel auf dem Handy") — Streamlit-
   eigene Kopfzeile/Toolbar/Menü/Footer raus, Block-Container-Ränder auf
   ein Minimum: die Szene bekommt fast die volle Bildschirmbreite/-höhe
   statt in einem eingerahmten "Fenster" zu stehen. NUR bei aktivem
   Pixel-Theme (hier in _base_css, nicht in _legacy_css) — der Plain-
   Notausstieg bleibt unangetastet bei den originalen Streamlit-Rändern.
   W8.3 (18.7.2026, Fix): stHeader NICHT per display:none aus dem Render-
   Baum nehmen — der Ausklapp-Pfeil für eine eingeklappte Sidebar
   (initial_sidebar_state="auto" kollabiert je nach Fensterbreite) ist ein
   Kind-Element von stHeader; display:none auf dem Elternelement hätte ihn
   mitversteckt und die Sidebar dauerhaft unerreichbar gemacht (genau das
   vom User gemeldete "die Sidebar ist nicht mehr da"). Höhe/Hintergrund
   auf 0 statt komplett entfernen — der Pfeil bleibt sichtbar+klickbar. */
[data-testid="stHeader"] {{
    background: transparent;
    height: 0;
    min-height: 0;
}}
[data-testid="stToolbar"], #MainMenu, footer {{
    display: none;
}}
[data-testid="stAppViewContainer"] .block-container {{
    max-width: 100%;
    padding-top: 0.6rem;
    padding-bottom: 1rem;
    padding-left: 1rem;
    padding-right: 1rem;
}}

/* Schwebende HUD-Leiste (Kopf/Status/Ampel) — bleibt beim Scrollen oben
   sitzen, halbtransparent+geweicht statt als eigener Block, der die
   Szene nach unten drückt (app.py öffnet/schließt den Div drumherum). */
.px-hud-bar {{
    position: sticky;
    top: 0;
    z-index: 999;
    background: rgba(20, 23, 28, 0.92);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    padding: 4px 8px 8px;
    margin-bottom: 6px;
    border-bottom: 1px solid var(--px-border);
    border-radius: 0 0 6px 6px;
}}

/* Szenen-Rahmen: großzügige Mindesthöhe, damit die Fabrik den Bildschirm
   dominiert statt ein kleines Fenster zu sein. Die SVG selbst bleibt
   seitenverhältnistreu (eigenes width:100%/height:auto, scene.py) —
   der Rahmen zentriert sie per Flexbox in der Mindesthöhe (Letterbox
   statt Verzerrung/Kappung, falls die Breite die Höhe zuerst begrenzt).
   NUR die Live-Hauptszene bekommt diese Klasse (tabs/factory.py); die
   Zeitreise-Archiv-/Mobile-Zweitverwendungen bleiben bewusst kompakt. */
.px-scene-wrap {{
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 76vh;
}}

/* W8.2 (18.7.2026, User-Vorgabe: Maschinen-Detailpanels als echtes
   Overlay statt eines Blocks unter der Szene — "wie bei einem Base-
   Bau-Spiel auf dem Handy"). Backdrop ist ein <a href="?">, das hinter
   dem Panel liegt (kleinerer z-index) — Klick irgendwo AUSSERHALB des
   Panels schließt, Klicks INNERHALB des Panels treffen das Panel/seine
   Kind-Elemente zuerst (normale DOM-Stapelreihenfolge, kein JS nötig).
   scene.py::_scene() öffnet Backdrop+Panel-Div vor `_render_detail_panel`
   und schließt den Panel-Div danach wieder. */
.px-detail-backdrop {{
    position: fixed;
    inset: 0;
    background: rgba(10, 12, 15, 0.72);
    z-index: 1000;
    display: block;
}}
.px-detail-panel {{
    position: fixed;
    top: 5vh;
    left: 50%;
    transform: translateX(-50%);
    width: min(920px, 94vw);
    max-height: 88vh;
    overflow-y: auto;
    background: var(--px-bg-panel);
    border: 1px solid var(--px-border);
    border-radius: 8px;
    padding: 8px 22px 26px;
    z-index: 1001;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.6);
}}
.px-detail-close {{
    position: sticky;
    top: 0;
    float: right;
    margin-top: 6px;
    background: var(--px-bg-panel);
    color: var(--px-text-muted);
    font-family: "VT323", "Courier New", monospace;
    font-size: 1.1rem;
    text-decoration: none;
    border: 1px solid var(--px-border);
    border-radius: 4px;
    padding: 2px 10px;
    z-index: 5;
}}
.px-detail-close:hover {{
    color: var(--px-text);
    border-color: var(--px-cobalt-hi);
}}
@media (max-width: 640px) {{
    .px-detail-panel {{ top: 2vh; max-height: 94vh; padding: 8px 14px 20px; }}
}}

.px-head {{
    font-family: "Press Start 2P", monospace;
    color: var(--px-cobalt);
    line-height: 1.6;
}}

/* st.title() wird im ganzen Dashboard NUR auf der Login-Seite verwendet
   (dashboard/auth.py) — diese Regel ist also faktisch auf D1.6 begrenzt. */
h1 {{
    font-family: "Press Start 2P", monospace;
    font-size: 1.3rem !important;
    color: var(--px-cobalt);
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

/* D4.3: laufendes Förderband (dashboard/conveyor.py) — reine CSS-Animation
   im Browser, keine Streamlit-Rerun-Kosten. */
@keyframes px-belt-scroll {{
    from {{ transform: translateX(0); }}
    to   {{ transform: translateX(-24px); }}
}}
.px-belt-anim {{
    animation: px-belt-scroll 0.8s linear infinite;
}}

/* Vision W2.1: Fabrik-Szene (dashboard/factory/) — zustandsgetriebene
   Aktivitäts-Animationen. fx-belt-run teilt sich das Keyframe mit
   .px-belt-anim (dieselbe Optik, andere Stelle); fx-blink teilt sich
   px-blink mit den bestehenden LED-Punkten. */
.fx-belt-run {{
    animation: px-belt-scroll 0.8s linear infinite;
}}
.fx-blink {{
    animation: px-blink 1.2s infinite;
}}
@keyframes fx-smoke-rise {{
    0%   {{ transform: translateY(0);    opacity: 0.5; }}
    100% {{ transform: translateY(-20px); opacity: 0; }}
}}
.fx-smoke {{
    animation: fx-smoke-rise 2s ease-out infinite;
}}
/* Vision W6: Verbindungs-Leitungen zwischen Maschinen (Factorio/Mindustry-
   Optik) — "fließt" nur, wenn beide Enden gerade echt aktiv sind
   (scene.py::_connection_paths). */
@keyframes fx-pipe-flow {{
    to {{ stroke-dashoffset: -28px; }}
}}
.fx-pipe-flow {{
    stroke-dasharray: 10 8;
    animation: fx-pipe-flow 1s linear infinite;
}}
/* W7.10: sichtbare Kiste (scene.py::_crate_travel_marker) wandert entlang
   der conveyor→warehouse-Leitung — macht fx-pipe-flow wörtlich statt nur
   abstrakter Farb-Fluss. offset-distance ist eine reine CSS-Eigenschaft
   (kein SMIL), fügt sich in dieselbe reduced-motion-Abschaltung wie die
   übrigen fx-*-Animationen unten ein. */
@keyframes fx-crate-travel {{
    0%   {{ offset-distance: 0%;   opacity: 0; }}
    8%   {{ opacity: 1; }}
    92%  {{ opacity: 1; }}
    100% {{ offset-distance: 100%; opacity: 0; }}
}}
.fx-crate-travel {{
    offset-rotate: 0deg;
    animation: fx-crate-travel 2.5s linear infinite;
}}
/* D8.2: Zählerscheibe des E-Werk-Stromzählers — dreht nur, wenn heute
   wirklich Verbrauch anfiel (power_meter.py setzt die Klasse bedingt). */
@keyframes fx-spin-turn {{
    from {{ transform: rotate(0deg); }}
    to   {{ transform: rotate(360deg); }}
}}
.fx-spin {{
    animation: fx-spin-turn 4s linear infinite;
}}

/* D7.3: Laufband-Anzeigetafel — LED-Ticker wie in einer Werkshalle.
   Der Track enthält den Inhalt ZWEIMAL (app.py dupliziert ihn); die
   Animation schiebt um genau -50 %, dadurch nahtlose Schleife. */
.px-ticker {{
    overflow: hidden;
    white-space: nowrap;
    background: #0A0C0F;
    border: 1px solid var(--px-border);
    border-radius: 4px;
    padding: 4px 0;
}}
.px-ticker-track {{
    display: inline-block;
    animation: px-ticker-scroll 30s linear infinite;
    font-family: 'VT323', monospace;
    font-size: 1.05rem;
    color: var(--px-neon-green);
}}
.px-ticker-track .px-ticker-sep {{ color: var(--px-copper); margin: 0 14px; }}
@keyframes px-ticker-scroll {{
    from {{ transform: translateX(0); }}
    to   {{ transform: translateX(-50%); }}
}}

@media (prefers-reduced-motion: reduce) {{
    .px-belt-anim, .fx-belt-run, .fx-blink, .fx-smoke, .fx-spin, .fx-pipe-flow, .fx-crate-travel {{ animation: none; }}
    .px-led--warn::before, .px-led--err::before {{ animation: none; }}
    .px-ticker-track {{ animation: none; }}
}}

/* ── D1.4 KPI-Leiste als Industriepanel ─────────────────────────────── */
[data-testid="stMetric"], [data-testid="metric-container"] {{
    background: var(--px-bg-panel);
    border: 1px solid var(--px-border);
    border-radius: 4px;
    padding: 12px 16px;
}}
[data-testid="stMetricLabel"] {{
    font-family: "VT323", "Courier New", monospace;
    color: var(--px-text-muted);
    font-size: 1.05rem;
}}
[data-testid="stMetricDelta"] svg[fill="rgb(9, 171, 59)"],
[data-testid="stMetricDelta"] div:has(svg[fill="rgb(9, 171, 59)"]) {{
    color: var(--px-neon-green) !important;
}}
[data-testid="stMetricDelta"] svg[fill="rgb(255, 43, 43)"],
[data-testid="stMetricDelta"] div:has(svg[fill="rgb(255, 43, 43)"]) {{
    color: var(--px-red) !important;
}}

/* ── D1.5 Tab-Leiste ─────────────────────────────────────────────────── */
button[data-baseweb="tab"] {{
    font-family: "VT323", "Courier New", monospace;
    font-size: 1.05rem;
    color: var(--px-text-muted);
}}
button[data-baseweb="tab"]:hover {{
    color: var(--px-cobalt-hi);
}}
button[aria-selected="true"][data-baseweb="tab"] {{
    color: var(--px-cobalt);
}}
[data-baseweb="tab-highlight"] {{
    background-color: var(--px-cobalt) !important;
}}

/* ── Regime-/Status-Badges (aus dem alten Inline-Block übernommen) ──── */
.regime-bull    {{ color: var(--px-neon-green); font-weight: 700; font-size: 1.1rem; }}
.regime-neutral {{ color: var(--px-amber);      font-weight: 700; font-size: 1.1rem; }}
.regime-bear    {{ color: var(--px-copper);     font-weight: 700; font-size: 1.1rem; }}
.regime-crisis  {{ color: var(--px-red);        font-weight: 700; font-size: 1.1rem; }}
.badge-pending  {{ color: var(--px-amber); }}
.badge-ok       {{ color: var(--px-neon-green); }}
.badge-red      {{ color: var(--px-red); }}

/* ── D7.4 Boot-Sequenz (Login-Seite) ─────────────────────────────────── */
/* Zeilen erscheinen gestaffelt; `both` hält vor dem Delay den Aus-Zustand.
   Bei prefers-reduced-motion fällt die Animation weg und die Basis-
   Deckkraft 1 greift sofort — alle Zeilen einfach sichtbar. */
.px-boot-line {{
    opacity: 1;
    animation: px-boot-in 0.01s both;
    font-family: "VT323", "Courier New", monospace;
    color: var(--px-neon-green);
}}
@keyframes px-boot-in {{
    from {{ opacity: 0; }}
    to   {{ opacity: 1; }}
}}
@media (prefers-reduced-motion: reduce) {{
    .px-boot-line {{ animation: none; }}
}}
{_crt_css()}</style>
"""


def inject() -> None:
    """Rendert das Theme-CSS einmalig. Bei DASHBOARD_THEME=plain exakt der
    alte Inline-Block (heutiges Aussehen bleibt wortwörtlich erhalten,
    Notausstieg D6.2); sonst das komplette Pixel-Theme (Basisklassen +
    pixel-ifizierte KPI-/Tab-/Regime-Styles, D1.1/D1.4/D1.5)."""
    st.markdown(_base_css() if is_enabled() else _legacy_css(), unsafe_allow_html=True)


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


def ticker(items: list) -> str:
    """D7.3: Laufband-Anzeigetafel. Baut aus echten Ereignis-Texten einen
    LED-Ticker (`.px-ticker`); jeder Eintrag wird hier escaped. Der Inhalt
    wird verdoppelt, damit die -50%-CSS-Schleife nahtlos läuft. Bei plain
    oder leerer Liste: leerer String (Aufrufer rendert dann nichts)."""
    if not is_enabled() or not items:
        return ""
    sep = '<span class="px-ticker-sep">◆</span>'
    body = sep.join(html.escape(str(i)) for i in items) + sep
    return (
        '<div class="px-ticker"><span class="px-ticker-track">'
        f'{body}{body}</span></div>'
    )


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

# D2.3 Drift-Schutz: neue Charts bekommen ihre Farben AUSSCHLIESSLICH über
# dieses zentrale Theme (Altair-Theme "pixel" + Plotly-Template "pixel").
# Kein eigenes Farb-Hardcoding in einem Tab-Modul — sonst driftet die
# Optik bei der nächsten Palette-Änderung auseinander.


def _altair_theme() -> dict:
    p = PALETTE
    return {
        "config": {
            "background": "transparent",
            "view": {"stroke": "transparent"},
            "axis": {
                "domainColor": p["border"],
                "gridColor": p["border"],
                "tickColor": p["border"],
                "labelColor": p["text_muted"],
                "titleColor": p["text_muted"],
            },
            "legend": {"labelColor": p["text_muted"], "titleColor": p["text_muted"]},
            "range": {
                "category": [p["cobalt"], p["copper"], p["neon_green"],
                             p["amber"], p["red"], p["neon_cyan"]],
            },
        }
    }


def register_chart_themes() -> None:
    """Registriert das Altair-Theme + Plotly-Template aus PALETTE (D2).
    No-Op bei plain (Bibliotheks-Defaults bleiben aktiv). Idempotent —
    Streamlit reruns dürfen den zweiten Aufruf nicht crashen lassen."""
    global _charts_registered
    if not is_enabled() or _charts_registered:
        return
    _charts_registered = True

    try:
        import altair as alt
        alt.themes.register("pixel", _altair_theme)
        alt.themes.enable("pixel")
    except Exception:
        pass

    try:
        import plotly.io as pio
        import plotly.graph_objects as go
        # H6.4-Einschränkung (bewusst nicht behoben): anders als das
        # Altair-Theme oben (registriert eine FUNKTION, die Plotly bei
        # jedem Chart-Aufbau neu aufruft) baut Plotly hier ein STATISCHES
        # Template-Objekt — die PALETTE-Werte werden einmalig zum
        # Registrierungszeitpunkt eingefroren (`_charts_registered`-Guard
        # verhindert Re-Registrierung pro Rerun). In der Praxis
        # unkritisch: DASHBOARD_THEME ist eine Server-ENV-Variable ohne
        # Laufzeit-Umschalter im UI, ändert sich also nie innerhalb eines
        # laufenden Prozesses. Für Tests, die pixel/blueprint im selben
        # Prozess vergleichen wollen, müsste `_charts_registered` vorher
        # zurückgesetzt werden.
        p = PALETTE
        pio.templates["pixel"] = go.layout.Template(
            layout=go.Layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color=p["text_muted"]),
                colorway=[p["cobalt"], p["copper"], p["neon_green"],
                         p["amber"], p["red"], p["neon_cyan"]],
            )
        )
        pio.templates.default = "pixel"
    except Exception:
        pass
