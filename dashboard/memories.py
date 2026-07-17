"""
dashboard/memories.py — „Heute vor …"-Erinnerungen (Roadmap L2.1,
docs/FABRIK_LEBENDIG.md).

Das Werk erinnert sich an die EIGENE Geschichte. Jede Erinnerung ist ein
echtes Ereignis mit exaktem Datum aus den Bot-Daten — es wird nichts
erfunden, nichts geschätzt.

TREFFER-REGEL (bewusst eng): eine Erinnerung erscheint nur, wenn das
Ereignis heute exakt N ganze Wochen her ist (N ≥ 1). Damit taucht jedes
Ereignis nur an einem Wochentag auf, und es gibt keinen Zwang, täglich
irgendetwas zu zeigen — „keine Erinnerung" ist ein völlig normaler Tag.

QUELLEN (16.7.2026 alle real geprüft):
- `portfolio.db` (trades) — erster Trade überhaupt
- `experience.db` (decisions) — größter Gewinn / größter Verlust, jeweils
  nur aus GELABELTEN Zeilen (pnl_pct gesetzt)
- `data/achievements.json` — freigeschaltete Plaketten (H7.2)
- `data/thesis_registry.json` — registrierte Thesen (Roadmap 6.10)

NICHT gebaut — ehrlicher Befund statt Erfindung: „Regime kippte auf BEAR"
war in der Roadmap-Skizze vorgesehen, ist aber NICHT baubar:
`data/current_regime.json` speichert ausschließlich den AKTUELLEN Stand
(`{"regime": …, "timestamp": …}`), es existiert keinerlei Regime-Historie.
Eine Erinnerung daraus wäre geraten.

Read-only, netzfrei, fail-open je Quelle.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_ACHIEVEMENTS_FILE = os.path.join(_DATA_DIR, "achievements.json")
_THESIS_FILE = os.path.join(_DATA_DIR, "thesis_registry.json")

_MAX_MEMORIES = 3


def _as_date(value) -> Optional[date]:
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except (TypeError, ValueError):
        return None


# ── Kandidaten je Quelle (jede fail-open) ────────────────────────────────────

def _first_trade() -> List[Dict]:
    try:
        import sqlite3

        from portfolio.portfolio import PORTFOLIO_DB
        conn = sqlite3.connect(PORTFOLIO_DB)
        try:
            row = conn.execute(
                "SELECT ticker, timestamp FROM trades ORDER BY timestamp LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return []
    if not row:
        return []
    d = _as_date(row[1])
    return [{"date": d, "text": f"der allererste Trade des Werks ({row[0]})"}] if d else []


def _pnl_extremes(store=None) -> List[Dict]:
    """Größter Gewinn/Verlust aus gelabelten Ausgängen. `store`
    injizierbar (Muster calibration_curve.confidence_win_rates)."""
    try:
        from analyzers.experience_store import ExperienceStore
        owns = store is None
        s = store or ExperienceStore()
        rows = []
        try:
            for feat, out in s.iter_labeled():
                pnl = out.get("pnl_pct")
                d = _as_date(feat.get("decided_at"))
                if isinstance(pnl, (int, float)) and d is not None:
                    rows.append((pnl, d, str(feat.get("ticker") or "?")))
        finally:
            if owns:
                s.close()
    except Exception:
        return []
    if not rows:
        return []
    best = max(rows, key=lambda r: r[0])
    worst = min(rows, key=lambda r: r[0])
    out_rows = [{"date": best[1],
                 "text": f"der bis heute größte Gewinn ({best[2]} {best[0]:+.1f} %)"}]
    if worst[1] != best[1] or worst[2] != best[2]:
        out_rows.append({"date": worst[1],
                         "text": f"der bis heute größte Verlust ({worst[2]} {worst[0]:+.1f} %)"})
    return out_rows


def _achievements() -> List[Dict]:
    try:
        with open(_ACHIEVEMENTS_FILE, encoding="utf-8") as fh:
            data = json.load(fh) or {}
        from dashboard.achievements import CATALOG
        titles = {item["id"]: item.get("title", item["id"]) for item in CATALOG}
    except Exception:
        return []
    rows = []
    for key, entry in data.items():
        d = _as_date((entry or {}).get("unlocked_at"))
        if d is not None:
            rows.append({"date": d,
                         "text": f"Plakette „{titles.get(key, key)}" + "“ erreicht"})
    return rows


def _theses() -> List[Dict]:
    try:
        with open(_THESIS_FILE, encoding="utf-8") as fh:
            data = json.load(fh) or {}
    except Exception:
        return []
    rows = []
    for key, entry in data.items():
        d = _as_date((entry or {}).get("started_at"))
        if d is not None:
            rows.append({"date": d, "text": f"These „{key}" + "“ registriert"})
    return rows


# ── Zusammenbau ──────────────────────────────────────────────────────────────

def _phrase(weeks: int) -> str:
    if weeks % 52 == 0:
        years = weeks // 52
        return "vor einem Jahr" if years == 1 else f"vor {years} Jahren"
    return "vor einer Woche" if weeks == 1 else f"vor {weeks} Wochen"


def memories_for(day: Optional[date] = None, store=None) -> List[Dict]:
    """Erinnerungen für `day`: `[{"when", "text", "date"}]`, höchstens
    `_MAX_MEMORIES`, älteste zuerst (die entferntesten sind die
    eindrucksvollsten). Leere Liste ist der Normalfall, kein Fehler."""
    day = day or date.today()
    candidates = _first_trade() + _pnl_extremes(store) + _achievements() + _theses()
    hits = []
    for c in candidates:
        d = c.get("date")
        if not isinstance(d, date):
            continue
        days = (day - d).days
        if days <= 0 or days % 7 != 0:
            continue
        hits.append({"when": _phrase(days // 7), "text": c["text"],
                     "date": d.isoformat()})
    hits.sort(key=lambda h: h["date"])
    return hits[:_MAX_MEMORIES]
