"""
dashboard/departures.py — Werksbahnhof-Abfahrtstafel (Design D8.1).

Kommende Termine als Bahnhofs-Anzeigetafel: Was kommt auf die Fabrik zu?
Vier echte Datenquellen, alle bisher in keinem Panel sichtbar:

- Makro-Termine (FOMC/CPI/NFP) aus `data/macro_calendar.json`
- Earnings-Termine der aktuellen Watchlist (yfinance über den bestehenden
  `EarningsFilter` — Netz! Der Aufrufer deckelt das per `st.cache_data`,
  dieses Modul selbst ruft NIE ungefragt ins Netz: `upcoming_events()`
  nimmt Earnings nur als fertige `extra_rows` entgegen)
- nächster Bot-Zyklus (`system.live_status.read_status().next_run`)
- nächstes Backup (`systemctl show aktien_backup.timer`, read-only mit
  2s-Timeout — Muster `controls.service_state()`; startet/stoppt nichts)

Fail-open in jeder Quelle: eine kaputte Datei/fehlendes systemd lässt
Zeilen weg statt die Tafel zu crashen. Alle dynamischen Texte escaped.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from dashboard.theme import PALETTE

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_MACRO_FILE = os.path.join(_DATA_DIR, "macro_calendar.json")
_WATCHLIST_FILE = os.path.join(_DATA_DIR, "dynamic_watchlist.json")
_BACKUP_TIMER = "aktien_backup.timer"
_SYSTEMCTL_TIMEOUT_S = 2


# ── Datenquellen (je Quelle fail-open) ───────────────────────────────────────

def _macro_rows(now: datetime) -> List[Dict]:
    try:
        with open(_MACRO_FILE, encoding="utf-8") as fh:
            events = json.load(fh).get("events") or []
    except Exception:
        return []
    rows = []
    for ev in events:
        try:
            d = datetime.fromisoformat(str(ev.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        if d.date() < now.date():
            continue
        rows.append({
            "date": d.date().isoformat(),
            "label": str(ev.get("name") or "?"),
            "impact": str(ev.get("impact") or "").upper(),
            "kind": "makro",
        })
    return rows


def _next_backup(now: datetime) -> Optional[str]:
    """Nächster Lauf des Backup-Timers, als ISO-Datum. Read-only über
    `systemctl show`; kein systemd / Timer aus / Timeout → None."""
    try:
        out = subprocess.run(
            ["systemctl", "show", _BACKUP_TIMER,
             "--property=NextElapseUSecRealtime", "--value"],
            capture_output=True, text=True, timeout=_SYSTEMCTL_TIMEOUT_S,
        )
        raw = (out.stdout or "").strip()
        if not raw or raw in ("0", "n/a", "infinity"):
            return None
        # Format: "Fri 2026-07-17 03:00:00 CEST" — das Datum steht in Feld 2
        for part in raw.split():
            try:
                return datetime.fromisoformat(part).date().isoformat()
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _system_rows(now: datetime) -> List[Dict]:
    rows = []
    try:
        from system.live_status import read_status
        nxt = (read_status() or {}).get("next_run")
        if nxt:
            d = datetime.fromisoformat(str(nxt)[:19])
            if d >= now - timedelta(hours=1):
                rows.append({
                    "date": d.date().isoformat(),
                    "label": f"Bot-Zyklus {d.strftime('%H:%M')}",
                    "impact": "",
                    "kind": "system",
                })
    except Exception:
        pass
    backup_date = _next_backup(now)
    if backup_date:
        rows.append({
            "date": backup_date,
            "label": "Backup (03:00, täglich)",
            "impact": "",
            "kind": "system",
        })
    return rows


def watchlist_tickers(limit: int = 12) -> List[str]:
    """Aktuelle dynamische Watchlist (read-only), für den Earnings-Abruf."""
    try:
        with open(_WATCHLIST_FILE, encoding="utf-8") as fh:
            tickers = json.load(fh).get("tickers") or []
        return [str(t).upper() for t in tickers][:limit]
    except Exception:
        return []


def held_tickers() -> List[str]:
    """Ticker der offenen Positionen (read-only, netzfrei). Für den
    Earnings-Abruf des Aufrufers: eine Position, die in ihre Earnings
    hineinläuft, ist ein echtes Risiko — anders als ein Watchlist-Titel."""
    try:
        from portfolio.portfolio import Portfolio
        return [str(t).upper() for t in Portfolio().all_positions()]
    except Exception:
        return []


def _position_rows(now: datetime) -> List[Dict]:
    """L3.1: je offene Position eine planmäßige Abfahrt — Zieltag ist
    `entry_date + target_hold_days` (dieselben echten Felder, die auch
    Lager/Regal nutzen; KEINE zweite Datenhaltung). Netzfrei.

    Überschrittene Ziele verschwinden NICHT stillschweigend, sondern
    bleiben als „überfällig" stehen (board_html macht daraus die
    Beschriftung) — eine Position, die über ihr Ziel hinausläuft, ist
    genau das, was man sehen will."""
    try:
        from portfolio.portfolio import Portfolio
        positions = Portfolio().all_positions()
    except Exception:
        return []
    rows = []
    for ticker, pos in positions.items():
        try:
            entry = datetime.fromisoformat(str(pos.entry_date)[:10])
            due = entry + timedelta(days=int(pos.target_hold_days or 0))
        except (TypeError, ValueError, AttributeError):
            continue
        overdue = due.date() < now.date()
        rows.append({
            "date": due.date().isoformat(),
            "label": f"{ticker} — planmäßige Abfahrt",
            "impact": "ÜBERFÄLLIG" if overdue else "ABFAHRT",
            "kind": "position",
        })
    return rows


def earnings_rows(tickers: List[str], now: Optional[datetime] = None,
                  filter_obj=None, held: Optional[List[str]] = None) -> List[Dict]:
    """Earnings-Termine je Ticker über den bestehenden `EarningsFilter`
    (yfinance → NETZ; der Aufrufer cached das). `filter_obj` injizierbar
    für netzfreie Tests. Fail-open je Ticker.

    `held` = Ticker mit offener Position: deren Earnings sind
    „Frachtrisiko" (L3.1) — wir halten das Papier, wenn die Zahlen
    kommen. Reine Watchlist-Earnings bleiben ein normaler Termin."""
    now = now or datetime.now()
    held_set = {str(t).upper() for t in (held or [])}
    if filter_obj is None:
        try:
            from analyzers.earnings_filter import EarningsFilter
            filter_obj = EarningsFilter()
        except Exception:
            return []
    rows = []
    for t in tickers:
        try:
            ed = filter_obj.next_earnings(t)
        except Exception:
            continue
        if ed is None or ed.date() < now.date():
            continue
        is_held = str(t).upper() in held_set
        rows.append({
            "date": ed.date().isoformat(),
            "label": f"{t} Earnings" + (" (im Depot)" if is_held else ""),
            "impact": "FRACHTRISIKO" if is_held else "EARNINGS",
            "kind": "earnings",
        })
    return rows


def upcoming_events(extra_rows: Optional[List[Dict]] = None,
                    limit: int = 14, days_ahead: int = 60,
                    now: Optional[datetime] = None) -> List[Dict]:
    """Alle netzfreien Quellen (Makro, System, Positions-Abfahrten) +
    optionale fertige Zusatz-Zeilen (Earnings — die brauchen Netz und
    kommen darum gecacht vom Aufrufer), gefiltert auf die nächsten
    `days_ahead` Tage, sortiert, gedeckelt.

    Der Horizont-Filter schneidet nur nach VORNE ab: überfällige
    Positions-Abfahrten (Datum in der Vergangenheit) bleiben bewusst
    drin und stehen durch die Datums-Sortierung ganz oben."""
    now = now or datetime.now()
    horizon = (now + timedelta(days=days_ahead)).date().isoformat()
    rows = (_macro_rows(now) + _system_rows(now) + _position_rows(now)
            + list(extra_rows or []))
    rows = [r for r in rows if r.get("date") and r["date"] <= horizon]
    rows.sort(key=lambda r: (r["date"], r.get("kind", ""), r.get("label", "")))
    return rows[:limit]


# ── Darstellung ──────────────────────────────────────────────────────────────

_IMPACT_COLOR_KEY = {
    "HIGH": "red", "MEDIUM": "amber", "EARNINGS": "amber",
    # L3.1: eigene Sprache für die Fracht — planmäßige Abfahrt ist ein
    # ruhiger Betriebszustand (cobalt = „läuft", wie in der Szenen-
    # Legende), überfällig und Earnings-im-Depot sind echte Warnungen.
    "ABFAHRT": "cobalt", "ÜBERFÄLLIG": "red", "FRACHTRISIKO": "red",
}


def board_html(rows: List[Dict], now: Optional[datetime] = None) -> str:
    """Abfahrtstafel als HTML (inline-styles, keine externen Ressourcen).
    Leere Tafel zeigt einen ehrlichen Hinweis statt einer leeren Box."""
    now = now or datetime.now()
    p = PALETTE
    head = (
        f'<div style="background:{p["bg_panel"]};border:1.5px solid {p["border"]};'
        f'border-radius:6px;padding:10px 14px;font-family:VT323,monospace;">'
        f'<div style="color:{p["amber"]};font-size:18px;letter-spacing:2px;'
        f'border-bottom:1px solid {p["border"]};padding-bottom:6px;'
        f'margin-bottom:6px;">🚉 WERKSBAHNHOF — ABFAHRT</div>'
    )
    if not rows:
        return (head +
                f'<div style="color:{p["text_muted"]};font-size:15px;">'
                f'Keine anstehenden Termine in Sicht.</div></div>')
    lines = []
    for r in rows:
        try:
            d = datetime.fromisoformat(r["date"])
            days = (d.date() - now.date()).days
            if days < 0:
                # L3.1: überfällige Abfahrt — „in -3 Tagen" wäre Unsinn.
                n = abs(days)
                when = f"überfällig ({n} Tag{'e' if n != 1 else ''})"
            elif days == 0:
                when = "heute"
            elif days == 1:
                when = "morgen"
            else:
                when = f"in {days} Tagen"
            datestr = d.strftime("%d.%m.")
        except (KeyError, ValueError, TypeError):
            continue
        color = p[_IMPACT_COLOR_KEY.get(r.get("impact", ""), "neon_green")]
        impact_txt = r.get("impact") or ("SYSTEM" if r.get("kind") == "system" else "")
        lines.append(
            f'<div style="display:flex;gap:10px;align-items:baseline;'
            f'font-size:16px;padding:2px 0;color:{p["text"]};">'
            f'<span style="color:{p["amber"]};min-width:52px;">{datestr}</span>'
            f'<span style="flex:1;">{html.escape(r.get("label") or "?")}</span>'
            f'<span style="color:{p["text_muted"]};font-size:13px;">{html.escape(when)}</span>'
            f'<span style="color:{color};font-size:13px;min-width:72px;'
            f'text-align:right;">{html.escape(impact_txt)}</span>'
            f'</div>'
        )
    return head + "".join(lines) + "</div>"
