"""
dashboard/warehouse_shelf.py — Lager-Detailregal (Design D8.3).

Positionen als Kisten im Hochregal, gruppiert nach Sektor
(`data/ticker_profiles.json`, read-only). Je Kiste: Füllstand =
Positionsgröße relativ zur größten Position, Aufkleber = P&L, Etikett =
Haltedauer + Ziel-Countdown. P&L kommt aus den bereits geladenen
`ctx.prices` — dieses Modul macht KEINEN eigenen Netz-Abruf.

Abgrenzung zur Fabrik-Szene (D7.2-Kisten im Wimmelbild): dort ist das
Lager EIN Element unter vielen, Farbe nach Haltedauer; hier ist es die
Detail-Ansicht im Portfolio-Tab mit P&L, Sektor-Gruppierung und Zahlen
zum Ablesen. Ehrlicher Leer-Zustand bei 0 Positionen.
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime
from typing import Dict, List, Optional

from dashboard.theme import PALETTE

_PROFILES_FILE = os.path.join(os.path.dirname(__file__), "..", "data",
                              "ticker_profiles.json")


def _sector_of(ticker: str, profiles: Dict) -> str:
    prof = profiles.get(ticker) or {}
    return str(prof.get("sector") or "Sonstige")


def _load_profiles() -> Dict:
    try:
        with open(_PROFILES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def shelf_data(positions: Dict, prices: Dict,
               now: Optional[datetime] = None) -> List[Dict]:
    """Aus Portfolio-Positionen + geladenen Kursen die Regal-Gruppen bauen:
    `[{"sector": str, "crates": [{ticker, value, fill_pct, pnl_pct,
    age_days, hold_days}]}]`, Sektoren alphabetisch, Kisten je Sektor nach
    Wert absteigend. Fail-open je Position (kein Kurs → P&L None)."""
    now = now or datetime.now()
    profiles = _load_profiles()
    crates = []
    for ticker, pos in (positions or {}).items():
        try:
            shares = float(pos.shares)
            entry = float(pos.entry_price)
            price = prices.get(ticker)
            value = shares * (float(price) if price else entry)
            pnl_pct = ((float(price) - entry) / entry * 100) if price and entry else None
            try:
                age_days = (now.date()
                            - datetime.fromisoformat(str(pos.entry_date)[:10]).date()).days
            except (TypeError, ValueError):
                age_days = None
            crates.append({
                "ticker": str(ticker),
                "sector": _sector_of(str(ticker), profiles),
                "value": value,
                "pnl_pct": pnl_pct,
                "age_days": age_days,
                "hold_days": int(getattr(pos, "target_hold_days", 0) or 0),
            })
        except Exception:
            continue
    if not crates:
        return []
    max_value = max(c["value"] for c in crates) or 1.0
    for c in crates:
        c["fill_pct"] = max(8.0, c["value"] / max_value * 100)
    groups: Dict[str, List[Dict]] = {}
    for c in crates:
        groups.setdefault(c["sector"], []).append(c)
    return [
        {"sector": s, "crates": sorted(groups[s], key=lambda c: -c["value"])}
        for s in sorted(groups)
    ]


def shelf_svg(groups: List[Dict]) -> str:
    """Hochregal als SVG. Leeres Regal = ehrliche Anzeige (Lager leer),
    kein Platzhalter-Inventar."""
    p = PALETTE
    crate_w, crate_h, gap = 74, 84, 10
    x, cols = 14, []
    for g in groups:
        sector_x0 = x
        for c in g["crates"]:
            pnl = c.get("pnl_pct")
            pnl_txt = f"{pnl:+.1f}%" if pnl is not None else "–"
            pnl_color = (p["neon_green"] if pnl is not None and pnl >= 0
                         else p["red"] if pnl is not None else p["text_muted"])
            fill_h = round(c.get("fill_pct", 8) / 100 * (crate_h - 26))
            age = c.get("age_days")
            hold = c.get("hold_days") or 0
            label = (f"{age}/{hold}d" if age is not None and hold
                     else f"{age}d" if age is not None else "")
            # L1.4: jede Kiste führt in die Personalakte des Titels —
            # gleiches Link-Muster wie die Maschinen der Fabrik-Szene
            # (machines.py: <a href="?…" target="_self">).
            cols.append(
                f'<a href="?dossier={html.escape(c["ticker"])}" target="_self">'
                f'<g><title>{html.escape(c["ticker"])}: '
                f'${c["value"]:,.0f} · P&amp;L {html.escape(pnl_txt)}'
                f'{" · " + html.escape(label) if label else ""}'
                f' — klicken für die Akte</title>'
                f'<rect x="{x}" y="20" width="{crate_w}" height="{crate_h}" '
                f'fill="{p["bg"]}" stroke="{p["copper"]}" stroke-width="2" rx="3" />'
                f'<rect x="{x + 4}" y="{20 + crate_h - 4 - fill_h}" '
                f'width="{crate_w - 8}" height="{fill_h}" '
                f'fill="{p["copper"]}" opacity="0.45" />'
                f'<text x="{x + crate_w / 2}" y="46" text-anchor="middle" '
                f'font-family="VT323, monospace" font-size="17" '
                f'fill="{p["text"]}">{html.escape(c["ticker"][:6])}</text>'
                f'<text x="{x + crate_w / 2}" y="64" text-anchor="middle" '
                f'font-family="VT323, monospace" font-size="14" '
                f'fill="{pnl_color}">{html.escape(pnl_txt)}</text>'
                f'<text x="{x + crate_w / 2}" y="80" text-anchor="middle" '
                f'font-family="VT323, monospace" font-size="12" '
                f'fill="{p["text_muted"]}">{html.escape(label)}</text>'
                f'</g></a>'
            )
            x += crate_w + gap
        cols.append(
            f'<text x="{sector_x0}" y="{20 + crate_h + 18}" '
            f'font-family="VT323, monospace" font-size="13" '
            f'fill="{p["amber"]}">{html.escape(g["sector"].upper())}</text>'
        )
        x += 14  # Sektor-Abstand
    width = max(x, 320)
    if not groups:
        cols.append(
            f'<text x="24" y="70" font-family="VT323, monospace" '
            f'font-size="17" fill="{p["text_muted"]}">Lager leer — keine '
            f'offenen Positionen eingelagert.</text>'
        )
    return (
        f'<div style="overflow-x:auto;">'
        f'<svg viewBox="0 0 {width} 140" xmlns="http://www.w3.org/2000/svg" '
        f'style="min-width:{min(width, 900)}px;width:100%;height:auto;" '
        f'role="img" aria-label="Hochregallager">'
        f'<rect x="2" y="2" width="{width - 4}" height="136" rx="6" '
        f'fill="{p["bg_panel"]}" stroke="{p["border"]}" stroke-width="1.5" />'
        f'<text x="14" y="16" font-family="VT323, monospace" font-size="14" '
        f'fill="{p["amber"]}">🏗 HOCHREGALLAGER — BESTAND NACH SEKTOR</text>'
        f'{"".join(cols)}'
        f'</svg></div>'
    )
