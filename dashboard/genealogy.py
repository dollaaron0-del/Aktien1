"""
dashboard/genealogy.py — Entscheidungs-Genealogie (Ausbau-Roadmap H3.2).

Verkettet eine Order rückwärts: Order → Analyse → Quellen. Read-only,
eigene sqlite3-Connections (kein Eingriff in broker/order_log.py oder
analyzers/analysis_log.py — außerhalb des für diese Ausbau-Session
erlaubten Pfads).

WICHTIGE EINSCHRÄNKUNG (Heuristik, kein Bug): es gibt KEINE direkte
Fremdschlüssel-Verkettung zwischen `orders` und `analyses` in den
Datenbanken. Die Zuordnung Order→Analyse läuft hier über
Ticker + den zeitlich NÄCHSTLIEGENDEN analysis_log-Eintrag VOR dem
Order-Zeitstempel — in der ganz überwiegenden Mehrheit der Fälle korrekt
(der Bot analysiert unmittelbar vor einer Order), aber theoretisch könnte
eine dazwischenliegende, ungenutzte Analyse fälschlich zugeordnet werden.
Für v1 ausreichend; bei Bedarf ließe sich das über eine echte
analysis_id-Spalte im order_log lösen (das wäre ein Change in
broker/order_log.py, außerhalb dieses Scopes).
"""
from __future__ import annotations

import html
import json
import os
import sqlite3
from typing import Dict, Optional

from dashboard.theme import PALETTE


def _read_order(order_id: int, db_path: Optional[str] = None) -> Optional[Dict]:
    from broker.order_log import DB_PATH as _ORDER_DB_PATH
    path = db_path or _ORDER_DB_PATH
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _read_nearest_analysis_before(
    ticker: str, before_ts: str, db_path: Optional[str] = None,
) -> Optional[Dict]:
    from analyzers.analysis_log import DB_PATH as _ANALYSIS_DB_PATH
    path = db_path or _ANALYSIS_DB_PATH
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM analyses WHERE ticker=? AND analyzed_at<=? "
                "ORDER BY analyzed_at DESC LIMIT 1",
                (ticker, before_ts),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def order_lineage(
    order_id: int, order_db_path: Optional[str] = None,
    analysis_db_path: Optional[str] = None,
) -> Dict:
    """Baut die Kette Order → Analyse → Quellen für eine Order-ID.
    Fail-open: fehlende Stufen sind None statt einer Exception."""
    order = _read_order(order_id, order_db_path)
    if order is None:
        return {"order": None, "analysis": None, "sources": None}

    analysis = _read_nearest_analysis_before(
        order.get("ticker") or "", order.get("ts") or "", analysis_db_path,
    )
    sources: Optional[Dict] = None
    if analysis is not None:
        try:
            sources = json.loads(analysis.get("sources_breakdown") or "{}")
        except Exception:
            sources = {}
    return {"order": order, "analysis": analysis, "sources": sources}


_BOX_W, _BOX_H, _GAP_Y = 200, 50, 40


def lineage_svg(lineage: Dict) -> str:
    """Dreistufiger Stammbaum als SVG (Order → Analyse → Quellen).
    Fehlende Stufen zeigen einen gedimmten „(keine Analyse gefunden)"-
    Kasten statt einfach zu verschwinden. Tooltips (`<title>`) statt
    Klick — reicht für v1 (Muster machines.py)."""
    p = PALETTE
    order = lineage.get("order")
    analysis = lineage.get("analysis")
    sources = lineage.get("sources") or {}

    width = _BOX_W + 40
    n_source_boxes = max(1, len(sources))
    height = _BOX_H * 3 + _GAP_Y * 2 + 20 + (n_source_boxes - 1) * 22

    def _box(y, title, lines, color, tooltip):
        esc_title = html.escape(str(title))
        esc_tooltip = "&#10;".join(html.escape(str(t)) for t in tooltip)
        text_lines = "".join(
            f'<text x="{width / 2}" y="{y + 22 + i * 14}" text-anchor="middle" '
            f'font-family="VT323, monospace" font-size="12" fill="{p["text_muted"]}">'
            f'{html.escape(str(line))}</text>'
            for i, line in enumerate(lines)
        )
        return (
            f'<g><title>{esc_tooltip or esc_title}</title>'
            f'<rect x="20" y="{y}" width="{_BOX_W}" height="{_BOX_H}" rx="4" '
            f'fill="{p["bg_panel"]}" stroke="{color}" stroke-width="2.5" />'
            f'<text x="{width / 2}" y="{y + 16}" text-anchor="middle" '
            f'font-family="VT323, monospace" font-size="13" fill="{p["text"]}">'
            f'{esc_title}</text>'
            f'{text_lines}'
            f'</g>'
        )

    def _connector(y):
        x = 20 + _BOX_W / 2
        return (
            f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y + _GAP_Y}" '
            f'stroke="{p["text_muted"]}" stroke-width="2" />'
        )

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;height:auto;">'
    ]

    y = 0
    if order:
        parts.append(_box(
            y, f"Order · {order.get('action', '?')} {order.get('ticker', '?')}",
            [f"{order.get('shares', '?')} Stk. @ {order.get('fill_price', '?')}",
             str(order.get("ts", ""))[:16]],
            p["neon_green"], [f"Status: {order.get('status', '?')}"],
        ))
    else:
        parts.append(_box(y, "Order", ["(nicht gefunden)"], p["border"], []))
    y += _BOX_H
    parts.append(_connector(y))
    y += _GAP_Y

    if analysis:
        parts.append(_box(
            y, f"Analyse · {analysis.get('recommendation', '?')}",
            [f"Score {analysis.get('sentiment_score', '?')}",
             str(analysis.get("analyzed_at", ""))[:16]],
            p["cobalt"], [f"Konfidenz: {analysis.get('confidence', '?')}"],
        ))
    else:
        parts.append(_box(y, "Analyse", ["(keine Analyse gefunden)"], p["border"], []))
    y += _BOX_H
    parts.append(_connector(y))
    y += _GAP_Y

    if sources:
        lines = [f"{src}: {cnt}×" for src, cnt in sorted(sources.items(), key=lambda kv: -kv[1])]
        parts.append(_box(y, "Quellen", lines, p["amber"], lines))
    else:
        parts.append(_box(y, "Quellen", ["(kein Breakdown gespeichert)"], p["border"], []))

    parts.append("</svg>")
    return "".join(parts)
