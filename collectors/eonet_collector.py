"""
collectors/eonet_collector.py – Naturgefahren-Backdrop als Risiko-Kontext.

Schlanke Komponente im Stil von weather_/tanker_flow_collector. Idee: aktive
Naturkatastrophen (Tropenstürme, Großbrände, Vulkane, Fluten) sind ein grober
*Risiko-/Stimmungs-Hintergrund* für betroffene Sektoren – Insurance/Rück-
versicherung, Energie/Raffinerie (Golf-Stürme), Lumber/Versorger (Brände),
Aviation (Vulkanasche). NUR als Makro-Kontext-Notiz, NICHT als Trade-Trigger
und NICHT als Bias/Sizing-Faktor (vgl. Small-Cap-Radar- & Wetter-Philosophie).

Quelle: NASA EONET v3 – frei, OHNE API-Key, ohne Limit-Drama.
  https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=30
  read() → Dict, 6h gepuffert in data/eonet_hazards.json.
Bewusst schlank & fail-safe: jeder Fehler → letztes/leeres Ergebnis, nie ein Crash.

Limitierung (ehrlich): EONET ist ein *kuratierter Aggregator*, kein Echtzeit-
Sensor – Events erscheinen mit Verzug und sind beim Auftauchen oft längst in den
News (und im Kurs). Kein sauberes Ticker-Mapping, nur grobes Region→Sektor.
Darum bewusst rein als Hintergrund-Notiz, kein Vorlauf-Signal.

Standalone-Test:  ./venv/bin/python -m collectors.eonet_collector
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_API = "https://eonet.gsfc.nasa.gov/api/v3/events"
_CACHE = Path("data/eonet_hazards.json")
_TTL_S = 6 * 3600
_DAYS = 30          # nur zuletzt ~30 Tage aktualisierte offene Events

# EONET-Kategorien mit Marktrelevanz → menschliches Label.
_RELEVANT = {
    "severeStorms": "Stürme",
    "wildfires":    "Großbrände",
    "volcanoes":    "Vulkane",
    "floods":       "Fluten",
}

# Grobe Box für US/Atlantik/Golf – dort treffen Stürme Energie & Insurance am
# direktesten (lat 10–50 N, lon -100…-30). Reiner Heuristik-Filter.
_US_ATLANTIC = {"lat": (10.0, 50.0), "lon": (-100.0, -30.0)}

# Schwellen für das Backdrop-Label (bewusst konservativ, dokumentiert heuristisch).
_STORM_ELEVATED = 1     # ≥1 aktiver US/Atlantik-Sturm
_FIRE_ELEVATED = 12     # auffällig viele aktive Großbrände


class EONETCollector:
    def __init__(self, days: int = _DAYS):
        self.days = days

    # ── öffentliche API (keylos, gepuffert) ───────────────────────────────────
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        try:
            data = self._fetch()
        except Exception as e:
            log.debug("EONET: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        if not data:
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    # ── Abruf + Aggregation ───────────────────────────────────────────────────
    def _fetch(self) -> Dict:
        params = {"status": "open", "days": self.days}
        resp = http_get(_API, params=params, timeout=20)
        if resp.status_code != 200:
            return {}
        events = (resp.json() or {}).get("events") or []
        if not events:
            return {}

        counts: Dict[str, int] = {k: 0 for k in _RELEVANT}
        us_atlantic_storms = 0
        for ev in events:
            cats = {c.get("id") for c in (ev.get("categories") or [])}
            for cat in cats:
                if cat in counts:
                    counts[cat] += 1
            if "severeStorms" in cats and _in_us_atlantic(ev):
                us_atlantic_storms += 1

        storms = counts["severeStorms"]
        fires = counts["wildfires"]
        elevated = us_atlantic_storms >= _STORM_ELEVATED or fires >= _FIRE_ELEVATED
        label = "ELEVATED" if elevated else "NORMAL"

        return {
            "counts": counts,                       # aktive Events je Kategorie
            "us_atlantic_storms": us_atlantic_storms,
            "hazard_label": label,                  # Naturgefahren-Backdrop
            "total_relevant": sum(counts.values()),
        }


# ── Helfer ────────────────────────────────────────────────────────────────────
def _in_us_atlantic(ev: Dict) -> bool:
    """Letzter bekannter Track-Punkt des Events in der US/Atlantik-Box?"""
    geom = ev.get("geometry") or []
    if not geom:
        return False
    coords = geom[-1].get("coordinates") or []
    if len(coords) < 2:
        return False
    lon, lat = coords[0], coords[1]
    return (_US_ATLANTIC["lat"][0] <= lat <= _US_ATLANTIC["lat"][1]
            and _US_ATLANTIC["lon"][0] <= lon <= _US_ATLANTIC["lon"][1])


def _load_cache() -> Optional[Dict]:
    try:
        return json.loads(_CACHE.read_text())
    except Exception:
        return None


def _save_cache(data: Dict) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps({"_ts": time.time(), "data": data}))
    except Exception as e:
        log.debug("EONET-Cache schreiben fehlgeschlagen: %s", e)


def hazard_summary(data: Dict) -> str:
    """Kompakte Klartext-Zeile der relevanten aktiven Gefahren (für Prompt)."""
    counts = data.get("counts") or {}
    parts = [f"{n} {_RELEVANT[k]}" for k, n in counts.items() if n]
    return ", ".join(parts)


if __name__ == "__main__":
    print("EONET: hole aktive Naturgefahren (NASA EONET v3, keylos)…")
    out = EONETCollector().read()
    print(json.dumps(out, indent=2) if out else "kein Ergebnis")
