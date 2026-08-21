"""
collectors/aviation_collector.py – Luftverkehrs-Aktivität als grober Kontext.

Schlanke Komponente im EONET-Stil. Idee: die Zahl aktiver Flugzeuge über einer
Region ist ein sehr grober Aktivitäts-/Nachfrage-Puls (Reise, Fracht, Wirtschaft).
Ein deutlicher Einbruch gegenüber dem gleitenden Schnitt kann auf Störungen
(Wetter, Streik, Luftraum-Sperre, Krise) hindeuten – relevant u. a. für Airlines,
Tourismus, Logistik. NUR als Makro-Kontext-Notiz, NICHT als Trade-Trigger und
NICHT als Bias/Sizing-Faktor.

Quelle: OpenSky Network – frei, OHNE API-Key (anonym, aber ratenlimitiert; das
6h-TTL hält uns bei ~4 Abrufen/Tag weit im Rahmen).
  https://opensky-network.org/api/states/all?lamin=..&lamax=..&lomin=..&lomax=..
  read() → Dict, 6h gepuffert in data/aviation_activity.json.
Bewusst schlank & fail-safe: jeder Fehler → letztes/leeres Ergebnis, nie ein Crash.

Limitierung (ehrlich): grobstes aller Signale – kein Ticker-Mapping, hohe Tages-/
Wochensaisonalität, ADS-B-Abdeckung schwankt. Der Vergleich läuft nur gegen einen
selbst geführten gleitenden Schnitt (data/aviation_activity.json). Reiner
Hintergrund, kein Vorlauf-Signal. Handelsnutzen bewusst als marginal eingestuft.

Standalone-Test:  ./venv/bin/python -m collectors.aviation_collector
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_API = "https://opensky-network.org/api/states/all"
_CACHE = Path("data/aviation_activity.json")
_TTL_S = 6 * 3600
_HIST_MAX = 28              # gleitender Schnitt über die letzten ~28 Messungen
_DROP_ELEVATED = 0.70      # aktueller Wert < 70 % des Schnitts → auffälliger Einbruch

# Europa-Box (dichte, verlässliche ADS-B-Abdeckung → stabile Referenz).
_BBOX = {"lamin": 35.0, "lamax": 60.0, "lomin": -10.0, "lomax": 30.0}


class AviationCollector:
    # ── öffentliche API (keylos, gepuffert) ───────────────────────────────────
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        try:
            count = self._fetch_count()
        except Exception as e:
            log.debug("Aviation: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        if count is None:
            return cached.get("data", {}) if cached else {}

        hist: List[int] = (cached or {}).get("hist", []) if cached else []
        avg = sum(hist) / len(hist) if hist else count
        elevated = bool(hist) and count < avg * _DROP_ELEVATED
        data = {
            "count": count,                         # aktive Flugzeuge in der Box
            "avg": round(avg, 1),
            "ratio": round(count / avg, 2) if avg else 1.0,
            "activity_label": "LOW" if elevated else "NORMAL",
        }
        # Historie fortschreiben (Ringpuffer) und mitspeichern.
        hist = (hist + [count])[-_HIST_MAX:]
        _save_cache(data, hist)
        return data

    # ── Abruf ─────────────────────────────────────────────────────────────────
    def _fetch_count(self) -> Optional[int]:
        resp = http_get(_API, params=_BBOX, timeout=20)
        if resp.status_code != 200:
            return None
        states = (resp.json() or {}).get("states")
        if states is None:
            return None
        return len(states)


# ── Helfer ────────────────────────────────────────────────────────────────────
def _load_cache() -> Optional[Dict]:
    try:
        return json.loads(_CACHE.read_text())
    except Exception:
        return None


def _save_cache(data: Dict, hist: List[int]) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps({"_ts": time.time(), "data": data, "hist": hist}))
    except Exception as e:
        log.debug("Aviation-Cache schreiben fehlgeschlagen: %s", e)


def aviation_summary(data: Dict) -> str:
    """Kompakte Klartext-Zeile für den Prompt."""
    c, avg = data.get("count"), data.get("avg")
    if not isinstance(c, int) or not avg:
        return ""
    return f"{c} Flüge (Europa) vs. Ø {avg} → {int(data.get('ratio', 1) * 100)}%"


if __name__ == "__main__":
    print("Aviation: hole aktive Flüge (OpenSky, Europa-Box, keylos)…")
    out = AviationCollector().read()
    print(json.dumps(out, indent=2) if out else "kein Ergebnis")
