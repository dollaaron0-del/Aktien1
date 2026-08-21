"""
dashboard/conveyor.py — Förderband-Visualisierung des Entscheidungs-Funnels
(Design-Roadmap D4, das Vorzeige-Stück).

Reine Funktion, kein Streamlit-Import nötig — leicht isoliert testbar. Input
ist exakt das Dict von `analyzers.decision_log.DecisionLog.funnel(day)`:
`{"total": n, "actions": {...}, "skip_reasons": {...}}`. Das Visual zeigt die
ECHTEN Funnel-Zahlen (Datenwürfel = analysierte Titel, Sortier-Arme = die
tatsächlichen SKIP-Gründe, die in beschriftete Behälter fallen) — kein
Deko-Bild, sondern die Statistik selbst.
"""
from __future__ import annotations

import html
from typing import Dict, List, Tuple

from dashboard.theme import PALETTE

_TOP_N_REASONS = 5

_REASON_LABELS = {
    "kein_kaufsignal":   "Kein Kaufsignal",
    "unter_schwelle":    "Unter Schwelle",
    "zu_wenige_quellen": "Zu wenige Quellen",
    "max_positionen":    "Max Positionen",
    "earnings_sperre":   "Earnings-Sperre",
    "korrelation":       "Korrelation",
    "liquiditaet":       "Liquidität",
    "lernfilter_avoid":  "Lernfilter",
    "positionsgroesse":  "Positionsgröße",
    "tagesverlust":      "Tagesverlust",
    "kein_kurs":         "Kein Kurs",
    "daten_gate":        "Daten-Gate",
    "sonstiges":         "Sonstiges",
}

_HEIGHT = 260


def _reason_label(key: str) -> str:
    return _REASON_LABELS.get(key, key.replace("_", " ").title())


def _top_reasons(skip_reasons: Dict[str, int]) -> Tuple[List[Tuple[str, int]], int]:
    """Top-`_TOP_N_REASONS` (bereits absteigend sortiert von funnel()) +
    Summe des Rests. Keine erneute Sortierung nötig, aber robust auch wenn
    der Aufrufer unsortiert liefert (nie von einem Zufallswert abhängig)."""
    items = sorted(skip_reasons.items(), key=lambda kv: -kv[1])
    top = items[:_TOP_N_REASONS]
    rest = sum(n for _, n in items[_TOP_N_REASONS:])
    return top, rest


def build_conveyor_svg(funnel: Dict, width: int = 900) -> str:
    p = PALETTE
    total = int((funnel or {}).get("total") or 0)
    actions = (funnel or {}).get("actions") or {}
    skip_reasons = (funnel or {}).get("skip_reasons") or {}

    buy_n = int(actions.get("BUY") or 0)
    hold_n = int(actions.get("HOLD") or 0)
    sell_n = int(actions.get("SELL") or 0)

    top, rest = _top_reasons(skip_reasons)
    bins = [(_reason_label(k), n) for k, n in top]
    if rest:
        bins.append(("…", rest))

    margin = 20
    belt_y, belt_h = 120, 36
    in_w, in_h = 90, 100
    out_w = 160
    belt_x0 = margin + in_w + 16
    belt_x1 = width - margin - out_w - 16
    belt_w = max(belt_x1 - belt_x0, 40)

    parts: List[str] = [
        f'<svg viewBox="0 0 {width} {_HEIGHT}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%; height:auto;">',
        # D4.3: laufendes Band als CSS-animiertes Streifenmuster (Keyframe
        # + prefers-reduced-motion-Aus in theme.py). Rein dekorativ, keine
        # Streamlit-Rerun-Kosten (läuft im Browser).
        '<defs><pattern id="px-belt-pattern" width="24" height="24" '
        'patternUnits="userSpaceOnUse" patternTransform="rotate(45)" '
        f'class="px-belt-anim"><rect width="24" height="24" fill="{p["bg_panel"]}" />'
        f'<rect width="12" height="24" fill="{p["border"]}" /></pattern></defs>',
        f'<rect x="0" y="0" width="{width}" height="{_HEIGHT}" fill="{p["bg"]}" />',
    ]

    # Einlauf (Datenwürfel = analysierte Titel)
    parts.append(
        f'<rect x="{margin}" y="{belt_y - (in_h - belt_h) / 2:.0f}" width="{in_w}" '
        f'height="{in_h}" rx="4" fill="{p["bg_panel"]}" stroke="{p["border"]}" />'
    )
    parts.append(
        f'<text x="{margin + in_w / 2:.0f}" y="{belt_y - 6}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="14" fill="{p["text_muted"]}">Analysiert</text>'
    )
    parts.append(
        f'<text x="{margin + in_w / 2:.0f}" y="{belt_y + belt_h / 2 + 8:.0f}" '
        f'text-anchor="middle" font-family="VT323, monospace" font-size="26" '
        f'fill="{p["text"]}">{total}</text>'
    )

    # Band (Basisfarbe fürs Fallback + gemusterte Fläche darüber für die
    # laufende Optik, D4.3)
    parts.append(
        f'<rect x="{belt_x0}" y="{belt_y}" width="{belt_w}" height="{belt_h}" '
        f'fill="{p["bg_panel"]}" stroke="{p["border"]}" />'
    )
    parts.append(
        f'<rect x="{belt_x0}" y="{belt_y}" width="{belt_w}" height="{belt_h}" '
        f'fill="url(#px-belt-pattern)" opacity="0.5" />'
    )
    n_segments = max(int(belt_w // 30), 1)
    seg_w = belt_w / n_segments
    for i in range(n_segments):
        parts.append(
            f'<line x1="{belt_x0 + i * seg_w:.0f}" y1="{belt_y}" '
            f'x2="{belt_x0 + i * seg_w:.0f}" y2="{belt_y + belt_h}" '
            f'stroke="{p["border"]}" stroke-width="1" />'
        )

    # Sortier-Arme + Behälter (nur wenn es überhaupt SKIP-Gründe gibt)
    if bins:
        step = belt_w / len(bins)
        for i, (label, count) in enumerate(bins):
            cx = belt_x0 + step * (i + 0.5)
            bin_w = min(step - 10, 110)
            bin_x = cx - bin_w / 2
            bin_y = belt_y + belt_h + 30
            # Sortier-Arm: diagonale Linie vom Band zum Behälter
            parts.append(
                f'<line x1="{cx:.0f}" y1="{belt_y + belt_h}" x2="{cx:.0f}" y2="{bin_y}" '
                f'stroke="{p["copper"]}" stroke-width="3" />'
            )
            parts.append(
                f'<rect x="{bin_x:.0f}" y="{bin_y}" width="{bin_w:.0f}" height="46" rx="3" '
                f'fill="{p["bg_panel"]}" stroke="{p["copper"]}" stroke-width="1.5" />'
            )
            parts.append(
                f'<text x="{cx:.0f}" y="{bin_y + 18}" text-anchor="middle" '
                f'font-family="VT323, monospace" font-size="12" fill="{p["copper_hi"]}">'
                f'{html.escape(label)[:16]}</text>'
            )
            parts.append(
                f'<text x="{cx:.0f}" y="{bin_y + 38}" text-anchor="middle" '
                f'font-family="VT323, monospace" font-size="18" fill="{p["text"]}">'
                f'{count}</text>'
            )

    # Auslauf: BUY (neon_green) oben, HOLD/SELL darunter
    out_x = belt_x1 + 16
    parts.append(
        f'<rect x="{out_x}" y="{belt_y - 10}" width="{out_w}" height="46" rx="4" '
        f'fill="{p["bg_panel"]}" stroke="{p["neon_green"]}" stroke-width="2" />'
    )
    parts.append(
        f'<text x="{out_x + out_w / 2:.0f}" y="{belt_y + 4}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="16" fill="{p["neon_green"]}">BUY</text>'
    )
    parts.append(
        f'<text x="{out_x + out_w / 2:.0f}" y="{belt_y + 26}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="20" fill="{p["text"]}">{buy_n}</text>'
    )
    parts.append(
        f'<rect x="{out_x}" y="{belt_y + 46}" width="{out_w}" height="40" rx="4" '
        f'fill="{p["bg_panel"]}" stroke="{p["border"]}" />'
    )
    parts.append(
        f'<text x="{out_x + out_w / 2:.0f}" y="{belt_y + 63}" text-anchor="middle" '
        f'font-family="VT323, monospace" font-size="13" fill="{p["text_muted"]}">'
        f'HOLD {hold_n} · SELL {sell_n}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)
