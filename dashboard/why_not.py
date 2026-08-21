"""
dashboard/why_not.py — „Warum nicht?"-Explorer (Ausbau-Roadmap H3.1).

Baut die Gate-Weichenstrecke für einen Ticker aus dem ECHTEN
DecisionLog-Eintrag — keine eigene Gate-Logik erfunden:
`analyzers.decision_log._REASON_BUCKETS` ist die reale, bestehende
Kategorisierung (dieselbe, die auch der Förderband-Funnel im
Entscheidungen-Tab nutzt, `dashboard/conveyor.py`). Die Bucket-Labels
werden von dort wiederverwendet (`_reason_label`), keine zweite Liste.

Wichtige Einschränkung, dokumentiert statt verschwiegen: die
Bucket-REIHENFOLGE ist die Reihenfolge, in der die Regex geprüft werden
(erster Treffer gewinnt) — das ist eine sinnvolle Näherung, aber NICHT
zwingend die exakte Ausführungs-Reihenfolge der echten Strategie-Gates
in `bot/`/`strategy_lab/` (außerhalb dieses Dashboard-Scopes, hier nur
read-only gegen das Decision-Log). Für BUY/HOLD/SELL-Einträge gilt: kein
Gate hat blockiert, die komplette Strecke gilt als "passiert".
"""
from __future__ import annotations

import html
from typing import Dict, List

from dashboard.conveyor import _reason_label
from dashboard.theme import PALETTE


def gate_trail(ticker: str, day: str) -> List[Dict]:
    """Gate-Weichenstrecke für den neuesten Entscheidungs-Eintrag eines
    Tickers an einem Tag. Fail-open: fehlende/kaputte Daten oder kein
    Eintrag liefern eine leere Liste statt zu crashen."""
    try:
        from analyzers.decision_log import DecisionLog, _REASON_BUCKETS, bucket_reason
        dlog = DecisionLog()
        entries = [e for e in dlog.get_day(day) if e.get("ticker") == ticker]
    except Exception:
        return []
    if not entries:
        return []
    entry = entries[0]  # get_day() liefert neueste zuerst

    order = [name for name, _rx in _REASON_BUCKETS]
    action = str(entry.get("action") or "?").upper()
    reason = str(entry.get("reason") or "")

    if action != "SKIP":
        trail = [{"key": k, "label": _reason_label(k), "status": "passed"} for k in order]
        trail.append({"key": "ergebnis", "label": f"Ergebnis: {action}", "status": "result"})
        return trail

    matched = bucket_reason(reason)
    trail = []
    blocked_seen = False
    for k in order:
        if k == matched:
            trail.append({
                "key": k, "label": _reason_label(k), "status": "blocked", "reason": reason,
            })
            blocked_seen = True
        elif blocked_seen:
            trail.append({"key": k, "label": _reason_label(k), "status": "unreached"})
        else:
            trail.append({"key": k, "label": _reason_label(k), "status": "passed"})

    if not blocked_seen:
        # bucket_reason() fiel auf "sonstiges" zurück (kein Regex-Treffer)
        # — der SKIP ist trotzdem real, nur der Grund nicht kategorisierbar.
        trail.append({
            "key": "sonstiges", "label": _reason_label("sonstiges"),
            "status": "blocked", "reason": reason,
        })
    return trail


_BOX_W, _BOX_H, _GAP = 96, 46, 14


def gate_trail_svg(trail: List[Dict]) -> str:
    """Weichenstrecke als SVG: grün = passiert, rot = hier geblockt
    (mit echtem Grund darunter), grau = nicht erreicht. Reine
    String-Funktion (Muster conveyor.py), alle Texte escaped."""
    if not trail:
        return ""
    p = PALETTE
    _COLOR = {
        "passed": p["neon_green"], "blocked": p["red"],
        "unreached": p["border"], "result": p["cobalt"],
    }
    n = len(trail)
    width = n * _BOX_W + (n - 1) * _GAP + 20
    reason_h = 24 if any(t.get("status") == "blocked" for t in trail) else 0
    height = _BOX_H + 20 + reason_h

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;">'
    ]
    for i, step in enumerate(trail):
        x = 10 + i * (_BOX_W + _GAP)
        y = 10
        color = _COLOR.get(step["status"], p["border"])
        opacity = "1" if step["status"] != "unreached" else "0.4"
        label = html.escape(str(step["label"]))
        parts.append(
            f'<g opacity="{opacity}">'
            f'<rect x="{x}" y="{y}" width="{_BOX_W}" height="{_BOX_H}" rx="4" '
            f'fill="{PALETTE["bg_panel"]}" stroke="{color}" stroke-width="2.5" />'
            f'<text x="{x + _BOX_W / 2}" y="{y + _BOX_H / 2 + 4}" text-anchor="middle" '
            f'font-family="VT323, monospace" font-size="12" fill="{PALETTE["text"]}">'
            f'{label}</text>'
            f'</g>'
        )
        if step.get("reason"):
            reason_text = html.escape(str(step["reason"]))[:60]
            parts.append(
                f'<text x="{x + _BOX_W / 2}" y="{y + _BOX_H + 18}" text-anchor="middle" '
                f'font-family="VT323, monospace" font-size="11" fill="{p["red"]}">'
                f'{reason_text}</text>'
            )
        if i < n - 1:
            ax = x + _BOX_W + 2
            ay = y + _BOX_H / 2
            parts.append(
                f'<line x1="{ax}" y1="{ay}" x2="{ax + _GAP - 4}" y2="{ay}" '
                f'stroke="{PALETTE["text_muted"]}" stroke-width="2" />'
            )
    parts.append("</svg>")
    return "".join(parts)
