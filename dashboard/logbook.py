"""
dashboard/logbook.py — Schichtbuch (Ausbau-Roadmap H7.3).

`write_entry(day)` fasst die echten Feed-Ereignisse eines Tages zu einem
kurzen Werkstagebuch-Eintrag zusammen. Ehrlicher Regel-Text zuerst (ohne
LLM, feste Sätze aus echten Zählern) — optional dahinter durch schönere
3-Satz-Prosa vom LOKALEN Ollama ersetzt. NIEMALS Claude (Kostenregel:
das Schichtbuch ist reine Atmosphäre, kein Katalysator-Ereignis, das
echtes Claude-Budget rechtfertigt — siehe Frugal-Routing-Prinzip). Ist
Ollama nicht erreichbar/langsam/kaputt, bleibt der Regel-Text einfach
stehen (fail-open, kurzer 5s-Timeout).

Ablage: data/logbook.jsonl, EIN Eintrag je Tag (ein erneuter Aufruf für
denselben Tag überschreibt statt zu duplizieren).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LOGBOOK_FILE = os.path.join(_DATA_DIR, "logbook.jsonl")

_OLLAMA_TIMEOUT_S = 5
_OLLAMA_URL = "http://localhost:11434/api/generate"


def _read_events_of_day(day: str, feed_db_path: Optional[str] = None) -> List[Dict]:
    """Read-only — nutzt dieselbe Activity-Feed-Lesefunktion wie das
    Tages-Replay (H2.3), nur für den ganzen Tag statt bis zu einem
    Zeitpunkt."""
    from dashboard.factory.state import read_feed_events_until
    return read_feed_events_until(day, f"{day}T23:59:59", db_path=feed_db_path)


def _rule_based_text(day: str, events: List[Dict]) -> str:
    """Ehrlicher Fallback ohne LLM: feste Sätze aus echten Ereignis-
    Zählern, kein Zufalls-Text."""
    if not events:
        return f"{day}: Keine Aktivität aufgezeichnet."

    n_analyses = sum(1 for e in events if e.get("event") == "analysis_done")
    n_trades_events = [e for e in events if e.get("event") == "trade"]
    n_blocked = sum(1 for e in events if e.get("event") == "gate_blocked")

    parts = [f"{n_analyses} Analysen, {len(n_trades_events)} Trade(s)."]
    if n_trades_events:
        tickers = sorted({e.get("ticker") for e in n_trades_events if e.get("ticker")})
        if tickers:
            parts.append(f"Bewegt: {', '.join(tickers)}.")
    else:
        parts.append("Ruhiger Tag ohne Trades.")
    if n_blocked:
        parts.append(f"{n_blocked}× durch ein Gate blockiert.")
    return " ".join(parts)


def _ollama_prose(rule_text: str, model: Optional[str] = None) -> Optional[str]:
    """Optional: lokaler Ollama formuliert den Regel-Text als kurze,
    sachliche Prosa um — KEINE neuen Fakten, nur schönere Sprache.
    NIEMALS Claude. Fail-open: jeder Fehler/Timeout → None, Aufrufer
    behält dann den Regel-Text."""
    try:
        import requests
        if model is None:
            from config import config
            model = config.ollama_model
        resp = requests.post(
            _OLLAMA_URL,
            json={
                "model": model,
                "prompt": (
                    "Formuliere folgende Stichpunkte eines Trading-Bot-"
                    "Schichtbuchs in maximal 3 kurzen, sachlichen Sätzen "
                    "auf Deutsch um, ohne neue Fakten zu erfinden:\n" + rule_text
                ),
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 120},
            },
            timeout=_OLLAMA_TIMEOUT_S,
        )
        if resp.status_code != 200:
            return None
        text = (resp.json().get("response") or "").strip()
        return text or None
    except Exception:
        return None


def _load_all(path: Optional[str] = None) -> Dict[str, Dict]:
    target = path or LOGBOOK_FILE
    entries: Dict[str, Dict] = {}
    if not os.path.exists(target):
        return entries
    try:
        with open(target, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("day"):
                    entries[row["day"]] = row
    except Exception:
        pass
    return entries


def _save_all(entries: Dict[str, Dict], path: Optional[str] = None) -> None:
    target = path or LOGBOOK_FILE
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for day in sorted(entries):
            fh.write(json.dumps(entries[day], ensure_ascii=False) + "\n")
    os.replace(tmp, target)


def write_entry(
    day: str, path: Optional[str] = None, feed_db_path: Optional[str] = None,
    use_ollama: bool = True,
) -> str:
    """Baut/aktualisiert den Schichtbuch-Eintrag für `day` (YYYY-MM-DD)
    und persistiert ihn — ein zweiter Aufruf für denselben Tag
    überschreibt statt zu duplizieren. Gibt den finalen Text zurück.
    Fail-open in jeder Teilstufe: ein kaputtes Feed-Log liefert den
    Leer-Tag-Text, ein kaputter Ollama den Regel-Text."""
    try:
        events = _read_events_of_day(day, feed_db_path)
    except Exception:
        events = []
    rule_text = _rule_based_text(day, events)

    final_text = rule_text
    if use_ollama:
        prose = _ollama_prose(rule_text)
        if prose:
            final_text = prose

    entries = _load_all(path)
    entries[day] = {"day": day, "text": final_text, "rule_text": rule_text}
    try:
        _save_all(entries, path)
    except Exception:
        pass
    return final_text


def read_entry(day: str, path: Optional[str] = None) -> Optional[Dict]:
    """Liefert den gespeicherten Eintrag eines Tages, oder None."""
    return _load_all(path).get(day)


def all_entries(path: Optional[str] = None) -> List[Dict]:
    """Alle gespeicherten Einträge, neuestes Datum zuerst."""
    entries = _load_all(path)
    return [entries[d] for d in sorted(entries, reverse=True)]
