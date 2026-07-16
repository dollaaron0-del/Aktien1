"""
dashboard/power_meter.py — E-Werk-Stromzähler (Design D8.2).

Die echten KI-Kosten aus `data/api_savings.json` als Drehstromzähler.
Abgrenzung zum D7.1-Treibstofftank (heute vs. Tageslimit): der Zähler
zeigt SPLIT (Claude vs. Ollama), ERSPARNIS (lokale Vorprüfung + Cache)
und den 14-Tage-TREND — der Frugal-Mode wird damit erstmals im
Dashboard sichtbar.

Read-only: der Datei-Pfad kommt aus `analyzers.api_cost_tracker._FILE`
(Single Source, kein zweiter hartkodierter Pfad); geschrieben wird hier
nie. Fail-open: fehlende/kaputte Datei → leerer Zustand, kein Crash.
"""
from __future__ import annotations

import html
import json
from datetime import date, timedelta
from typing import Dict, List, Optional

from dashboard.theme import PALETTE


def read_energy(days: int = 14, today: Optional[date] = None) -> Dict:
    """Kosten-Zusammenfassung: heute, Gesamt, Verlaufsliste der letzten
    `days` Kalendertage (fehlende Tage = 0, damit die Balkenreihe ehrlich
    Lücken zeigt statt sie zusammenzuschieben)."""
    today = today or date.today()
    out: Dict = {
        "today_cost": 0.0, "today_saved": 0.0,
        "today_claude": 0, "today_ollama": 0,
        "total_cost": 0.0, "total_saved": 0.0,
        "claude_calls": 0, "ollama_skips": 0,
        "history": [],
    }
    try:
        from analyzers.api_cost_tracker import _FILE
        with open(_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    daily = data.get("daily") or {}
    t = daily.get(today.isoformat()) or {}
    out["today_cost"] = float(t.get("cost") or 0.0)
    out["today_saved"] = float(t.get("saved") or 0.0) + float(t.get("cache_saved") or 0.0)
    out["today_claude"] = int(t.get("claude") or 0)
    out["today_ollama"] = int(t.get("ollama_skips") or 0)
    out["total_cost"] = float(data.get("total_cost_eur") or 0.0)
    out["total_saved"] = float(data.get("total_saved_eur") or 0.0)
    out["claude_calls"] = int(data.get("claude_calls") or 0)
    out["ollama_skips"] = int(data.get("ollama_skips") or 0)
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        row = daily.get(d) or {}
        out["history"].append({
            "date": d,
            "cost": max(0.0, float(row.get("cost") or 0.0)),
            "saved": max(0.0, float(row.get("saved") or 0.0)
                         + float(row.get("cache_saved") or 0.0)),
        })
    return out


def meter_svg(energy: Dict) -> str:
    """Drehstromzähler-Optik: Zählerscheibe (dreht via fx-spin nur, wenn
    heute wirklich Verbrauch anfiel — eine stehende Scheibe ist die
    ehrliche Anzeige eines pausierten Werks), Zählerstand, Split-Zeile,
    14-Tage-Balken (Kosten rot-orange, Ersparnis grün darunter)."""
    p = PALETTE
    hist: List[Dict] = energy.get("history") or []
    max_v = max([max(h["cost"], h["saved"]) for h in hist], default=0.0) or 1.0
    bars = []
    bw, gap, x0, base = 14, 4, 190, 92
    for i, h in enumerate(hist):
        x = x0 + i * (bw + gap)
        ch = round(h["cost"] / max_v * 40)
        sh = round(h["saved"] / max_v * 40)
        title = (f"{h['date']}: Kosten {h['cost']:.2f}€ · "
                 f"gespart {h['saved']:.2f}€")
        bars.append(
            f'<g><title>{html.escape(title)}</title>'
            f'<rect x="{x}" y="{base - ch}" width="{bw}" height="{max(ch, 1)}" '
            f'fill="{p["red"] if ch else p["border"]}" opacity="0.9" />'
            f'<rect x="{x}" y="{base + 4}" width="{bw}" height="{max(sh, 1)}" '
            f'fill="{p["neon_green"] if sh else p["border"]}" opacity="0.9" />'
            f'</g>'
        )
    spinning = ' class="fx-spin"' if energy.get("today_cost", 0) > 0 else ""
    counter = f'{energy.get("total_cost", 0.0):9.2f}'.replace(" ", " ")
    split = (f'Claude {energy.get("today_claude", 0)} · '
             f'Ollama {energy.get("today_ollama", 0)} (heute)')
    saved_line = (f'gespart gesamt {energy.get("total_saved", 0.0):.2f}€ '
                  f'(lokale Vorprüfung + Cache)')
    return (
        f'<svg class="px-instrument" viewBox="0 0 460 150" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;" '
        f'role="img" aria-label="E-Werk Stromzähler">'
        f'<rect x="2" y="2" width="456" height="146" rx="6" '
        f'fill="{p["bg_panel"]}" stroke="{p["border"]}" stroke-width="1.5" />'
        f'<text x="16" y="26" font-family="VT323, monospace" font-size="17" '
        f'fill="{p["amber"]}">⚡ E-WERK — KI-STROMZÄHLER (EUR)</text>'
        # Zählerscheibe
        f'<g{spinning} style="transform-origin:60px 78px;">'
        f'<circle cx="60" cy="78" r="26" fill="{p["bg"]}" '
        f'stroke="{p["border"]}" stroke-width="2" />'
        f'<rect x="58" y="54" width="4" height="10" fill="{p["red"]}" /></g>'
        # Zählerstand (Lebenszeit-Kosten)
        f'<text x="100" y="72" font-family="VT323, monospace" font-size="26" '
        f'fill="{p["neon_green"]}">{counter}€</text>'
        f'<text x="100" y="90" font-family="VT323, monospace" font-size="13" '
        f'fill="{p["text_muted"]}">Zählerstand gesamt · heute '
        f'{energy.get("today_cost", 0.0):.2f}€</text>'
        f'<text x="100" y="107" font-family="VT323, monospace" font-size="13" '
        f'fill="{p["text"]}">{html.escape(split)}</text>'
        f'<text x="100" y="124" font-family="VT323, monospace" font-size="13" '
        f'fill="{p["neon_green"]}">{html.escape(saved_line)}</text>'
        # 14-Tage-Balken
        f'{"".join(bars)}'
        f'<text x="{x0}" y="118" font-family="VT323, monospace" font-size="11" '
        f'fill="{p["text_muted"]}">14 Tage · oben Kosten / unten gespart</text>'
        f'</svg>'
    )
