"""
dashboard/instruments.py — Leitstand-Instrumente (Design D7.1, H7.1).

Reine SVG-String-Funktionen (Muster: conveyor.py) — Zahlen, die als
nüchterne Metrics untergehen würden, werden zu Industrie-Instrumenten:

- Manometer  = Circuit-Breaker-„Kesseldruck" (Tagesverlust vs. Limit)
- Tank       = Claude-Tagesbudget (Füllstand = Rest-Treibstoff)
- 7-Segment  = Depotwert wie an einer alten Maschinensteuerung
- Gesicht    = Werksleiter-Stimmung nach echtem Bot-Score (Ausbau H7.1)

Hier ist NUR Darstellung — die echten Werte liefert der Aufrufer (app.py),
jedes Instrument hängt damit an einer echten Datenquelle. Alle dynamischen
Texte werden escaped.
"""
from __future__ import annotations

import html
import math

from dashboard.theme import PALETTE


def _clamp_pct(pct: float) -> float:
    try:
        return max(0.0, min(100.0, float(pct)))
    except (TypeError, ValueError):
        return 0.0


# ── Manometer ────────────────────────────────────────────────────────────────

def _polar(cx: float, cy: float, r: float, pct: float) -> tuple[float, float]:
    """Punkt auf dem Halbkreis: 0 % = links (180°), 100 % = rechts (0°)."""
    ang = math.radians(180.0 - pct * 1.8)
    return cx + r * math.cos(ang), cy - r * math.sin(ang)


def _arc(cx: float, cy: float, r: float, p1: float, p2: float,
         color: str, width: int = 10) -> str:
    x1, y1 = _polar(cx, cy, r, p1)
    x2, y2 = _polar(cx, cy, r, p2)
    return (
        f'<path d="M {x1:.1f} {y1:.1f} A {r} {r} 0 0 1 {x2:.1f} {y2:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="{width}" />'
    )


def gauge_svg(pct: float, label: str, sublabel: str = "") -> str:
    """Halbkreis-Manometer, `pct` = Druck 0–100 (100 = Limit erreicht).
    Zonen grün <60, amber 60–85, rot >85 — bei >85 blinkt die Nadel-Nabe
    (fx-blink, respektiert prefers-reduced-motion via theme.py)."""
    pct = _clamp_pct(pct)
    p = PALETTE
    cx, cy, r = 110, 108, 78
    nx, ny = _polar(cx, cy, r - 16, pct)
    hub_cls = ' class="fx-blink"' if pct > 85 else ""
    hub_color = p["red"] if pct > 85 else p["amber"] if pct > 60 else p["neon_green"]
    return (
        f'<svg class="px-instrument" viewBox="0 0 220 150" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;" '
        f'role="img" aria-label="{html.escape(label)}">'
        f'<rect x="2" y="2" width="216" height="146" rx="6" '
        f'fill="{p["bg_panel"]}" stroke="{p["border"]}" stroke-width="1.5" />'
        f'{_arc(cx, cy, r, 0, 60, p["neon_green"])}'
        f'{_arc(cx, cy, r, 60, 85, p["amber"])}'
        f'{_arc(cx, cy, r, 85, 100, p["red"])}'
        f'<line x1="{cx}" y1="{cy}" x2="{nx:.1f}" y2="{ny:.1f}" '
        f'stroke="{p["text"]}" stroke-width="3" />'
        f'<circle{hub_cls} cx="{cx}" cy="{cy}" r="7" fill="{hub_color}" />'
        f'<text x="{cx}" y="128" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="16" fill="{p["text"]}">'
        f'{html.escape(label)}</text>'
        f'<text x="{cx}" y="143" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="13" fill="{p["text_muted"]}">'
        f'{html.escape(sublabel)}</text>'
        f'</svg>'
    )


# ── Treibstofftank ───────────────────────────────────────────────────────────

def tank_svg(fill_pct: float, label: str, sublabel: str = "") -> str:
    """Vertikaler Tank, `fill_pct` = Füllstand 0–100 (100 = voll = nichts
    verbraucht). Farbe grün >40, amber 15–40, rot <15."""
    fill_pct = _clamp_pct(fill_pct)
    p = PALETTE
    color = (p["neon_green"] if fill_pct > 40
             else p["amber"] if fill_pct >= 15 else p["red"])
    tank_x, tank_y, tank_w, tank_h = 40, 24, 60, 88
    fh = tank_h * fill_pct / 100.0
    marks = "".join(
        f'<line x1="{tank_x}" y1="{tank_y + tank_h * i / 4:.1f}" '
        f'x2="{tank_x + 8}" y2="{tank_y + tank_h * i / 4:.1f}" '
        f'stroke="{p["border"]}" stroke-width="1" />'
        for i in range(1, 4)
    )
    return (
        f'<svg class="px-instrument" viewBox="0 0 140 150" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;" '
        f'role="img" aria-label="{html.escape(label)}">'
        f'<rect x="2" y="2" width="136" height="146" rx="6" '
        f'fill="{p["bg_panel"]}" stroke="{p["border"]}" stroke-width="1.5" />'
        # Einfüllstutzen
        f'<rect x="{tank_x + tank_w / 2 - 8:.1f}" y="{tank_y - 10}" width="16" height="10" '
        f'fill="{p["border"]}" />'
        # Füllung (von unten)
        f'<rect x="{tank_x}" y="{tank_y + tank_h - fh:.1f}" width="{tank_w}" '
        f'height="{fh:.1f}" fill="{color}" opacity="0.85" />'
        # Tank-Umriss über der Füllung
        f'<rect x="{tank_x}" y="{tank_y}" width="{tank_w}" height="{tank_h}" rx="4" '
        f'fill="none" stroke="{p["text_muted"]}" stroke-width="2" />'
        f'{marks}'
        f'<text x="70" y="128" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="16" fill="{p["text"]}">'
        f'{html.escape(label)}</text>'
        f'<text x="70" y="143" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="13" fill="{p["text_muted"]}">'
        f'{html.escape(sublabel)}</text>'
        f'</svg>'
    )


# ── 7-Segment-Anzeige ────────────────────────────────────────────────────────

# Segmente je Ziffernzelle (Ursprung 0,0; Breite 10, Höhe 18):
#   a oben, b rechts-oben, c rechts-unten, d unten, e links-unten,
#   f links-oben, g Mitte — als (x, y, w, h)-Rechtecke.
_SEGS = {
    "a": (1, 0, 8, 2), "b": (8, 1, 2, 8), "c": (8, 9, 2, 8),
    "d": (1, 16, 8, 2), "e": (0, 9, 2, 8), "f": (0, 1, 2, 8),
    "g": (1, 8, 8, 2),
}
_SEG_MAP = {
    "0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc",
    "5": "afgcd", "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abfgcd",
    "-": "g", " ": "",
}
_CELL_W, _CELL_H, _GAP = 10, 18, 4
_DOT_W = 4  # schmale Zelle für "." / ","


def seven_segment_svg(text: str, label: str = "") -> str:
    """Ziffernanzeige im 7-Segment-Stil. Unterstützt 0-9, '-', ' ', '.',
    ','. Unbekannte Zeichen werden übersprungen (kein Crash bei z.B.
    Währungssymbolen). Ungenutzte Segmente als „Geister" schwach sichtbar —
    klassischer LED-Anzeigen-Look."""
    p = PALETTE
    parts: list[str] = []
    x = 6.0
    for ch in str(text):
        if ch in (".", ","):
            parts.append(
                f'<rect x="{x:.1f}" y="{6 + _CELL_H - 3:.1f}" width="2.5" height="2.5" '
                f'fill="{p["neon_green"]}" />'
            )
            x += _DOT_W
            continue
        segs = _SEG_MAP.get(ch)
        if segs is None:
            continue
        for name, (sx, sy, sw, sh) in _SEGS.items():
            lit = name in segs
            parts.append(
                f'<rect x="{x + sx:.1f}" y="{6 + sy}" width="{sw}" height="{sh}" '
                f'fill="{p["neon_green"] if lit else p["text_muted"]}" '
                f'opacity="{1.0 if lit else 0.08}" />'
            )
        x += _CELL_W + _GAP
    width = max(60.0, x + 6)
    return (
        f'<svg class="px-instrument" viewBox="0 0 {width:.0f} 46" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'style="width:auto;max-width:100%;height:46px;" '
        f'role="img" aria-label="{html.escape(label or str(text))}">'
        f'<rect x="0" y="0" width="{width:.0f}" height="46" rx="4" '
        f'fill="#0A0C0F" stroke="{PALETTE["border"]}" stroke-width="1.5" />'
        f'{"".join(parts)}'
        f'<text x="6" y="42" font-family="VT323, monospace" font-size="11" '
        f'fill="{p["text_muted"]}">{html.escape(label)}</text>'
        f'</svg>'
    )


# ── Werksleiter-Gesicht (Ausbau-Roadmap H7.1) ────────────────────────────────

def face_svg(score, label: str = "") -> str:
    """Pixel-Gesicht als Stimmungs-Indikator — echter Bot-Score
    (`BotScorer().get().current`) statt drei separater Panels. Vier
    Zustände: >75 zufrieden (Lächeln + grüne LED-Augen), 40–75 neutral
    (gerader Mund), <40 besorgt (Sorgenfalte + amber, Mund nach oben
    gebogen), `score=None` (Score nicht verfügbar) → graues
    Schlaf-Gesicht mit "Zzz". Nicht-numerische Eingaben werden wie
    `None` behandelt (fail-open, kein Crash bei kaputten Upstream-Daten)."""
    p = PALETTE
    safe_label = html.escape(str(label))
    cx, cy = 60, 55

    if score is not None:
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = None

    if score is None:
        color = p["text_muted"]
        eyes = (
            f'<line x1="{cx - 20}" y1="{cy - 5}" x2="{cx - 10}" y2="{cy - 5}" '
            f'stroke="{color}" stroke-width="3" stroke-linecap="round" />'
            f'<line x1="{cx + 10}" y1="{cy - 5}" x2="{cx + 20}" y2="{cy - 5}" '
            f'stroke="{color}" stroke-width="3" stroke-linecap="round" />'
        )
        mouth = (
            f'<line x1="{cx - 12}" y1="{cy + 18}" x2="{cx + 12}" y2="{cy + 18}" '
            f'stroke="{color}" stroke-width="3" stroke-linecap="round" />'
        )
        extra = (
            f'<text x="{cx + 30}" y="{cy - 22}" font-family="VT323, monospace" '
            f'font-size="18" fill="{color}">Zzz</text>'
        )
    elif score >= 75:
        color = p["neon_green"]
        eyes = (
            f'<circle cx="{cx - 15}" cy="{cy - 5}" r="4" fill="{color}" />'
            f'<circle cx="{cx + 15}" cy="{cy - 5}" r="4" fill="{color}" />'
        )
        # Lächeln: Kontrollpunkt UNTER den Endpunkten (curve nach unten).
        mouth = (
            f'<path d="M {cx - 15} {cy + 15} Q {cx} {cy + 28} {cx + 15} {cy + 15}" '
            f'fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" />'
        )
        extra = ""
    elif score >= 40:
        color = p["text"]
        eyes = (
            f'<circle cx="{cx - 15}" cy="{cy - 5}" r="4" fill="{color}" />'
            f'<circle cx="{cx + 15}" cy="{cy - 5}" r="4" fill="{color}" />'
        )
        mouth = (
            f'<line x1="{cx - 14}" y1="{cy + 18}" x2="{cx + 14}" y2="{cy + 18}" '
            f'stroke="{color}" stroke-width="3" stroke-linecap="round" />'
        )
        extra = ""
    else:
        color = p["amber"]
        eyes = (
            f'<circle cx="{cx - 15}" cy="{cy - 5}" r="4" fill="{color}" />'
            f'<circle cx="{cx + 15}" cy="{cy - 5}" r="4" fill="{color}" />'
        )
        # Sorgenfalte über den Augen:
        extra = (
            f'<line x1="{cx - 22}" y1="{cy - 16}" x2="{cx - 8}" y2="{cy - 11}" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" />'
            f'<line x1="{cx + 8}" y1="{cy - 11}" x2="{cx + 22}" y2="{cy - 16}" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" />'
        )
        # Sorge: Kontrollpunkt ÜBER den Endpunkten (curve nach oben).
        mouth = (
            f'<path d="M {cx - 15} {cy + 22} Q {cx} {cy + 10} {cx + 15} {cy + 22}" '
            f'fill="none" stroke="{color}" stroke-width="3" stroke-linecap="round" />'
        )

    return (
        f'<svg class="px-instrument" viewBox="0 0 160 110" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;" '
        f'role="img" aria-label="{safe_label or "Werksleiter-Stimmung"}">'
        f'<rect x="2" y="2" width="156" height="106" rx="6" '
        f'fill="{p["bg_panel"]}" stroke="{p["border"]}" stroke-width="1.5" />'
        f'<circle cx="{cx}" cy="{cy}" r="40" fill="none" stroke="{color}" '
        f'stroke-width="2.5" />'
        f'{eyes}{extra}{mouth}'
        f'<text x="{cx}" y="102" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="13" fill="{p["text_muted"]}">'
        f'{safe_label}</text>'
        f'</svg>'
    )
