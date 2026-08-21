"""
collectors/usgs_collector.py – Erdbeben-Backdrop als Risiko-Kontext.

Schwester-Komponente zu eonet_collector: EONET kennt Stürme/Brände/Vulkane/Fluten,
aber KEINE Erdbeben. Diese Lücke schließt USGS. Idee identisch: starke Beben in
industriell relevanten Regionen (Japan/Taiwan → Halbleiter, US-Westküste → Tech/
Häfen, Chile/Peru → Kupfer) sind ein grober *Risiko-/Stimmungs-Hintergrund* für
betroffene Sektoren – Lieferketten, Versicherung/Rückversicherung, Rohstoffe.
NUR als Makro-Kontext-Notiz, NICHT als Trade-Trigger und NICHT als Bias/Sizing-
Faktor (vgl. EONET-/Wetter-/Small-Cap-Radar-Philosophie).

Quelle: USGS Earthquake Hazards Program – frei, OHNE API-Key.
  https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson
  (fertig aggregierter GeoJSON-Feed: alle Beben M≥4.5 der letzten 24 h)
  read() → Dict, 6h gepuffert in data/usgs_quakes.json.
Bewusst schlank & fail-safe: jeder Fehler → letztes/leeres Ergebnis, nie ein Crash.

Limitierung (ehrlich): Ein Beben ist beim Auftauchen im Feed längst passiert und
in den News – kein Vorlauf-Signal. Region→Sektor ist grobe Heuristik. Darum rein
als Hintergrund-Notiz, kein Handels-Trigger.

Standalone-Test:  ./venv/bin/python -m collectors.usgs_collector
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

# Fertiger 24h-Feed aller Beben M≥4.5 (kein Query-Bau nötig, keylos).
_API = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
_CACHE = Path("data/usgs_quakes.json")
_TTL_S = 6 * 3600

# Schwellen fürs Backdrop-Label (bewusst konservativ, dokumentiert heuristisch).
_MAG_GLOBAL_ELEVATED = 6.5      # jedes sehr starke Beben irgendwo
_MAG_KEY_REGION = 5.5           # etwas schwächer reicht in Industrieregionen

# Grobe Boxen industriell/marktrelevanter Regionen (lat_min, lat_max, lon_min, lon_max).
_KEY_REGIONS = {
    "Japan":      (30.0, 46.0, 129.0, 146.0),   # Halbleiter/Auto/Häfen
    "Taiwan":     (21.5, 25.5, 119.0, 122.5),   # TSMC/Chips
    "US-Westk.":  (32.0, 49.0, -125.0, -114.0), # Tech/Häfen LA/LB
    "Chile/Peru": (-40.0, -5.0, -80.0, -66.0),  # Kupfer/Bergbau
}


class USGSCollector:
    # ── öffentliche API (keylos, gepuffert) ───────────────────────────────────
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        try:
            data = self._fetch()
        except Exception as e:
            log.debug("USGS: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        if not data:
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    # ── Abruf + Aggregation ───────────────────────────────────────────────────
    def _fetch(self) -> Dict:
        resp = http_get(_API, timeout=20)
        if resp.status_code != 200:
            return {}
        feats = (resp.json() or {}).get("features") or []
        if not feats:
            # Leerer Feed ist ein gültiges Ergebnis (ruhige 24 h) → NORMAL merken.
            return {"count": 0, "max_mag": 0.0, "key_hits": [],
                    "hazard_label": "NORMAL", "strongest": ""}

        max_mag = 0.0
        strongest = ""
        key_hits: List[str] = []
        for f in feats:
            props = f.get("properties") or {}
            geom = f.get("geometry") or {}
            mag = props.get("mag")
            if not isinstance(mag, (int, float)):
                continue
            place = props.get("place") or "?"
            if mag > max_mag:
                max_mag = float(mag)
                strongest = f"M{mag:.1f} {place}"
            region = _region_of(geom)
            if region and mag >= _MAG_KEY_REGION:
                key_hits.append(f"M{mag:.1f} {region}")

        elevated = max_mag >= _MAG_GLOBAL_ELEVATED or bool(key_hits)
        return {
            "count": len(feats),                    # aktive Beben M≥4.5 (24 h)
            "max_mag": round(max_mag, 1),
            "strongest": strongest,
            "key_hits": key_hits,                   # starke Beben in Industrieregionen
            "hazard_label": "ELEVATED" if elevated else "NORMAL",
        }


# ── Helfer ────────────────────────────────────────────────────────────────────
def _region_of(geom: Dict) -> Optional[str]:
    """Liegt das Epizentrum in einer markt­relevanten Region? GeoJSON-Punkt:
    coordinates = [lon, lat, depth]."""
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    lon, lat = coords[0], coords[1]
    for name, (la0, la1, lo0, lo1) in _KEY_REGIONS.items():
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return name
    return None


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
        log.debug("USGS-Cache schreiben fehlgeschlagen: %s", e)


def quake_summary(data: Dict) -> str:
    """Kompakte Klartext-Zeile für den Prompt (bevorzugt Industrieregion-Treffer)."""
    hits = data.get("key_hits") or []
    if hits:
        return ", ".join(hits[:3])
    return data.get("strongest") or ""


if __name__ == "__main__":
    print("USGS: hole aktive Beben M≥4.5 der letzten 24 h (keylos)…")
    out = USGSCollector().read()
    print(json.dumps(out, indent=2) if out else "kein Ergebnis")
