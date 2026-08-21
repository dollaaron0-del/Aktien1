"""
collectors/tanker_flow_collector.py – Tanker-Verkehr an Öl-Chokepoints (AIS).

EXPERIMENT / NICHT im Live-Pfad eingehängt. Idee: Tanker-Dichte an den großen
Öl-Nadelöhren (Hormuz, Singapur/Malakka) ist ein *langsames* Angebots-/Nachfrage-
Proxy für den Ölmarkt. Steigt die Tanker-Zahl deutlich über den eigenen Schnitt,
ist das ein grober Rückenwind/Gegenwind-Hinweis für Energiewerte – als Stimmungs-
Faktor, NICHT als Trade-Trigger.

Freie, offene Quelle: AISStream.io (kostenloser API-Key, globaler AIS-WebSocket).
  - ShipStaticData (AIS-Typ 5) liefert den ShipType je MMSI → Tanker = 80–89.
  - PositionReport (Typ 1–3) liefert Live-Positionen.
Wir sammeln in einem kurzen, begrenzten Fenster (Default 90s) pro Box die
*distinct* MMSIs und – soweit ein Static-Frame kam – die distinct Tanker.

Bewusst schlank & fail-safe (vgl. Small-Cap-Radar-Philosophie):
  - kein Dauer-Socket, nur ein kurzes Sample pro Aufruf
  - jeder Fehler → leeres/letztes Ergebnis, nie ein Crash
  - Ergebnis wird mit rollender Historie nach data/tanker_flow.json geschrieben;
    get_latest() ist ein schneller, keyloser Read (Muster wie RecessionDetector).

Limitierung (ehrlich): In 90s ist die Static-Abdeckung (alle ~6min gesendet)
unvollständig → die Tanker-Zahl ist ein Stichproben-Index, kein Zensus. Erst der
Trend über viele Samples ist aussagekräftig, nicht der Absolutwert.

Voraussetzung: AISSTREAM_API_KEY in .env (kostenlos: aisstream.io).
Standalone-Test:  ./venv/bin/python -m collectors.tanker_flow_collector
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional

from logger import get_logger

log = get_logger(__name__)

_STREAM_URL = "wss://stream.aisstream.io/v0/stream"
_STATE_FILE = Path("data/tanker_flow.json")
_HISTORY_MAX = 60          # rollende Samples, die wir behalten
_DEFAULT_WINDOW_S = 90     # Sammeldauer pro Aufruf

# Öl-Chokepoints als BoundingBox [[lat_min, lon_min], [lat_max, lon_max]].
# Bewusst klein gehalten – zwei der wichtigsten Tanker-Nadelöhre.
_BOXES: Dict[str, list] = {
    "hormuz":    [[24.0, 54.0], [27.5, 58.5]],   # Straße von Hormuz
    "singapore": [[0.5, 102.0], [2.5, 105.5]],   # Singapur / Malakka-Ausgang
}


class TankerFlowCollector:
    def __init__(self, window_s: int = _DEFAULT_WINDOW_S, boxes: Optional[Dict[str, list]] = None):
        self.window_s = window_s
        self.boxes = boxes or _BOXES

    # ── öffentliche API ───────────────────────────────────────────────────────
    def sample(self) -> Dict:
        """
        Sammelt EIN AIS-Fenster, aktualisiert die Historie und gibt das
        aggregierte Ergebnis zurück. Fail-safe: liefert notfalls {}.
        """
        api_key = os.getenv("AISSTREAM_API_KEY", "").strip()
        if not api_key:
            log.warning("TankerFlow: AISSTREAM_API_KEY fehlt – übersprungen.")
            return {}

        try:
            counts = self._collect_window(api_key)
        except Exception as e:
            log.warning("TankerFlow: Sammeln fehlgeschlagen: %s", e)
            return {}

        if not counts:
            return {}

        sample = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "window_s": self.window_s,
            "boxes": counts,  # {box: {"vessels": int, "tankers": int}}
            "tankers_total": sum(c["tankers"] for c in counts.values()),
            "vessels_total": sum(c["vessels"] for c in counts.values()),
        }
        self._persist(sample)
        return sample

    # ── AIS-WebSocket: ein begrenztes Sammelfenster ───────────────────────────
    def _collect_window(self, api_key: str) -> Dict[str, Dict[str, int]]:
        import websocket  # websocket-client (synchron, blockierend)

        bbox = [box for box in self.boxes.values()]
        sub = {
            "APIKey": api_key,
            "BoundingBoxes": bbox,
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }

        # MMSI → ShipType (aus ShipStaticData); MMSIs je Box gesehen.
        ship_types: Dict[int, int] = {}
        seen_per_box: Dict[str, set] = {name: set() for name in self.boxes}

        ws = websocket.create_connection(_STREAM_URL, timeout=15)
        try:
            ws.send(json.dumps(sub))
            deadline = time.time() + self.window_s
            ws.settimeout(5)
            while time.time() < deadline:
                try:
                    raw = ws.recv()
                except Exception:
                    continue
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                self._ingest(msg, ship_types, seen_per_box)
        finally:
            try:
                ws.close()
            except Exception:
                pass

        # Auswertung je Box: distinct Schiffe + distinct Tanker (Typ 80–89)
        result: Dict[str, Dict[str, int]] = {}
        for name, mmsis in seen_per_box.items():
            tankers = sum(1 for m in mmsis if 80 <= ship_types.get(m, -1) <= 89)
            result[name] = {"vessels": len(mmsis), "tankers": tankers}
        return result

    def _ingest(self, msg: Dict, ship_types: Dict[int, int], seen_per_box: Dict[str, set]) -> None:
        mtype = msg.get("MessageType")
        meta = msg.get("MetaData") or {}
        mmsi = meta.get("MMSI")
        if not isinstance(mmsi, int):
            return

        if mtype == "ShipStaticData":
            st = (((msg.get("Message") or {}).get("ShipStaticData")) or {}).get("Type")
            if isinstance(st, int):
                ship_types[mmsi] = st
            return

        if mtype == "PositionReport":
            lat = meta.get("latitude")
            lon = meta.get("longitude")
            if lat is None or lon is None:
                return
            for name, ((la_min, lo_min), (la_max, lo_max)) in self.boxes.items():
                if la_min <= lat <= la_max and lo_min <= lon <= lo_max:
                    seen_per_box[name].add(mmsi)

    # ── Persistenz: rollende Historie ─────────────────────────────────────────
    def _persist(self, sample: Dict) -> None:
        state = _load_state()
        history: List[Dict] = state.get("history", [])
        history.append(sample)
        state["history"] = history[-_HISTORY_MAX:]
        state["latest"] = sample
        state["signal"] = _compute_signal(state["history"])
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            log.debug("TankerFlow: Schreiben fehlgeschlagen: %s", e)


# ── Read-Schnittstelle (schnell, keylos – für spätere macro_context-Anbindung) ──
def get_latest() -> Optional[Dict]:
    """Letztes Sample + abgeleitetes Signal aus dem Cache. None, wenn leer."""
    state = _load_state()
    if not state.get("latest"):
        return None
    return {"latest": state["latest"], "signal": state.get("signal")}


def _load_state() -> Dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _compute_signal(history: List[Dict]) -> Dict:
    """
    Sehr einfaches Anomalie-Signal: aktueller Tanker-Wert vs. Median der
    Historie. label ∈ {ELEVATED, NORMAL, LOW, INSUFFICIENT_DATA}.
    Erst ab ~10 Samples belastbar – vorher bewusst 'INSUFFICIENT_DATA'.
    """
    vals = [s.get("tankers_total", 0) for s in history]
    if len(vals) < 10:
        return {"label": "INSUFFICIENT_DATA", "samples": len(vals)}
    base = median(vals[:-1]) or 1
    cur = vals[-1]
    ratio = cur / base
    if ratio >= 1.25:
        label = "ELEVATED"
    elif ratio <= 0.75:
        label = "LOW"
    else:
        label = "NORMAL"
    return {"label": label, "ratio": round(ratio, 2), "current": cur,
            "baseline_median": round(base, 1), "samples": len(vals)}


if __name__ == "__main__":
    print("TankerFlow: sammle ein AIS-Fenster (Default 90s)…")
    out = TankerFlowCollector().sample()
    print(json.dumps(out, indent=2) if out else "kein Ergebnis (API-Key gesetzt?)")
    sig = get_latest()
    if sig:
        print("Signal:", json.dumps(sig.get("signal"), indent=2))
