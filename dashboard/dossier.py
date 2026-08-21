"""
dashboard/dossier.py — Personalakten-Kartei (Design-Roadmap L1.1,
docs/FABRIK_LEBENDIG.md).

Bündelt read-only ALLES, was das Programm je über einen Ticker gesammelt
hat: Profil, Analyse-Historie (Score-EKG-Rohdaten), Trade-Bilanz aus
gelabelten Ausgängen, Themen-Verwandte, News-Puls, eigene Notiz.

Kern-Ehrlichkeitsregel (Roadmap-Vorgabe): jede Quelle ist einzeln
fail-open — eine dünne/fehlende Quelle ergibt ein leeres Feld, nie einen
Crash und nie eine erfundene Zahl. Schreibt NIRGENDS — reiner Leser über
bestehende, bereits sanktionierte Schnittstellen (AnalysisLog,
ExperienceStore, StockRelations, PositionNotes); keine neue
Datenhaltung.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
_PROFILES_FILE = os.path.join(_DATA_DIR, "ticker_profiles.json")
_NEWS_VELOCITY_FILE = os.path.join(_DATA_DIR, "news_velocity.json")


# ── Einzel-Quellen (jede für sich fail-open) ─────────────────────────────────

def _profile(ticker: str) -> Dict:
    try:
        with open(_PROFILES_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return dict(data.get(ticker) or {})
    except Exception:
        return {}


def analysis_history(ticker: str, limit: int = 100) -> List[Dict]:
    """Chronologisch AUFSTEIGEND (für das Score-EKG) — AnalysisLog liefert
    absteigend, hier gedreht."""
    try:
        from analyzers.analysis_log import AnalysisLog
        rows = AnalysisLog().get_recent(limit=limit, ticker=ticker)
        return list(reversed(rows))
    except Exception:
        return []


def all_known_tickers(limit: int = 300) -> List[Dict]:
    """Alle je analysierten Ticker, absteigend nach Analysen-Anzahl —
    für die Auswahlliste der Kartei. Eigene Aggregat-Query (die
    öffentliche AnalysisLog-Schnittstelle hat keine Zähl-Methode je
    Ticker), read-only über dieselbe, bereits offene Verbindung."""
    try:
        from analyzers.analysis_log import AnalysisLog
        conn = AnalysisLog()._conn
        rows = conn.execute(
            "SELECT ticker, COUNT(*) n FROM analyses "
            "GROUP BY ticker ORDER BY n DESC, ticker LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"ticker": r["ticker"], "n_analyses": r["n"]} for r in rows]
    except Exception:
        return []


def trade_bilanz(ticker: str, store=None) -> Dict:
    """Bilanz aus GELABELTEN Ausgängen (experience.db) — nur echte
    Ausgänge zählen, keine offenen/unbekannten. `rows` chronologisch
    absteigend für die „letzte Entscheidungen"-Liste. `store`
    injizierbar (Muster `calibration_curve.confidence_win_rates`):
    `ExperienceStore.__init__` bindet `db_path` als Default-Parameter
    zur Modul-Ladezeit, ein reines DB_PATH-Monkeypatch griffe darum in
    Tests nicht."""
    out = {"n_trades": 0, "wins": 0, "losses": 0, "avg_pnl_pct": None, "rows": []}
    try:
        from analyzers.experience_store import ExperienceStore
        owns_store = store is None
        s = store or ExperienceStore()
        rows = []
        try:
            for features, outcome in s.iter_labeled():
                if (features.get("ticker") or "").upper() != ticker:
                    continue
                rows.append({
                    "decided_at": features.get("decided_at"),
                    "recommendation": features.get("recommendation"),
                    "pnl_pct": outcome.get("pnl_pct"),
                    "outcome": outcome.get("outcome"),
                    "exit_reason": outcome.get("exit_reason"),
                    "hold_days": outcome.get("hold_days"),
                })
        finally:
            if owns_store:
                s.close()
    except Exception:
        return out
    rows.sort(key=lambda r: r.get("decided_at") or "", reverse=True)
    pnls = [r["pnl_pct"] for r in rows if isinstance(r.get("pnl_pct"), (int, float))]
    out["n_trades"] = len(rows)
    out["wins"] = sum(1 for p in pnls if p > 0)
    out["losses"] = sum(1 for p in pnls if p <= 0)
    out["avg_pnl_pct"] = (sum(pnls) / len(pnls)) if pnls else None
    out["rows"] = rows
    return out


def themes_and_related(ticker: str) -> Dict:
    try:
        from analyzers.stock_relations import StockRelations
        rel = StockRelations()
        return {
            "themes": rel.get_themes(ticker),
            "related": rel.get_related(ticker, limit=6),
        }
    except Exception:
        return {"themes": [], "related": []}


def news_pulse(ticker: str, days: int = 14) -> List[Dict]:
    """Tages-Summen der News-Zählung der letzten `days` Tage — Rohdaten
    sind stündlich, hier zu Tageswerten aggregiert. Ehrlich: die Quelle
    aktualisiert nur bei laufendem Bot, kann also veraltet/leer sein."""
    try:
        with open(_NEWS_VELOCITY_FILE, encoding="utf-8") as fh:
            entries = json.load(fh).get(ticker) or []
    except Exception:
        return []
    by_day: Dict[str, int] = defaultdict(int)
    for e in entries:
        try:
            day = str(e["ts"])[:10]
            by_day[day] += int(e.get("count") or 0)
        except (KeyError, TypeError, ValueError):
            continue
    if not by_day:
        return []
    last_day = max(by_day)
    try:
        end = datetime.fromisoformat(last_day)
    except ValueError:
        return []
    out = []
    for i in range(days - 1, -1, -1):
        d = (end - timedelta(days=i)).date().isoformat()
        out.append({"date": d, "count": by_day.get(d, 0)})
    return out


def note(ticker: str) -> str:
    try:
        from dashboard.position_notes import PositionNotes
        return PositionNotes().get(ticker)
    except Exception:
        return ""


# ── Zusammenbau ──────────────────────────────────────────────────────────────

def akte_links_md(tickers, limit: int = 20) -> str:
    """L1.4: kompakte Markdown-Zeile mit „→ Akte"-Links je Ticker.

    Bewusst eine LINK-ZEILE statt Links in der Tabelle: `st.dataframe`
    rendert kein HTML/Markdown (bekannte Falle, siehe LED-Migration) —
    ein Link in einer Zelle bliebe roher Text. Leere Eingabe → leerer
    String (der Aufrufer rendert dann nichts)."""
    seen, out = set(), []
    for raw in tickers or []:
        if not raw:              # None/"" vor der str()-Wandlung abfangen —
            continue             # str(None) wäre sonst der Ticker "NONE"
        t = str(raw).strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        # Karten-Umbau 18.7.2026: Kartei lebt im Lager-Detailpanel — der
        # Link muss die Fabrik-Szene mit fokussiertem Lager öffnen, nicht
        # nur den (nicht mehr existierenden) Kartei-Tab ansteuern.
        out.append(f"[{t}](?factory=warehouse&dossier={t})")
        if len(out) >= limit:
            break
    return " · ".join(out)


def dossier(ticker: str) -> Dict:
    """Die vollständige Akte. Jede Quelle unabhängig fail-open — eine
    kaputte Quelle darf die anderen nie mitreißen."""
    ticker = (ticker or "").strip().upper()
    rel = themes_and_related(ticker)
    return {
        "ticker": ticker,
        "profile": _profile(ticker),
        "history": analysis_history(ticker),
        "trades": trade_bilanz(ticker),
        "themes": rel["themes"],
        "related": rel["related"],
        "news_pulse": news_pulse(ticker),
        "note": note(ticker),
    }
