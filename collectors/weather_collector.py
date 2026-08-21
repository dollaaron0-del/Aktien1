"""
collectors/weather_collector.py – Wetter-Vorlauf als Energie-Nachfrage-Proxy.

EXPERIMENT / NICHT im Live-Pfad eingehängt (wie tanker_flow_collector). Idee:
Temperatur-Extreme in den großen Energie-Nachfragezentren sind ein *vorlaufender*
Proxy für Strom-/Gas-Nachfrage. Kalte Forecast-Woche ⇒ mehr Heizen (HDD),
heiße ⇒ mehr Kühlen (CDD) – beides hebt die Energienachfrage und ist ein grober
Rücken-/Gegenwind-Hinweis für Versorger & Energiewerte. Als *Stimmungs-Faktor*,
NICHT als Trade-Trigger (vgl. Tanker- und Small-Cap-Radar-Philosophie).

Signal: Heiz-/Kühl-Gradtage (HDD/CDD, Basis 18 °C) je Region. Wir vergleichen die
kommenden 7 Forecast-Tage gegen die letzten 7 beobachteten Tage (gleiche
recent-vs-baseline-Logik wie ENTSO-E). Wind kommt als Zusatzinfo mit
(hoher Wind = mehr Windstrom = tendenziell tiefere Strompreise).

Quelle: Open-Meteo Forecast API – frei, OHNE API-Key, ohne Limit-Drama.
  https://api.open-meteo.com/v1/forecast (daily temp max/min + wind, past_days=7,
  forecast_days=7). read() → Dict, 6h gepuffert in data/weather_macro.json.
Bewusst schlank & fail-safe: jeder Fehler → letztes/leeres Ergebnis, nie ein Crash.

Limitierung (ehrlich): Forecast-Unsicherheit steigt mit dem Horizont; der
Absolutwert ist Stichprobe, erst der Trend/das Verhältnis ist belastbar. Ein
keyloser Read über read() – Muster wie ENTSO-E.

Standalone-Test:  ./venv/bin/python -m collectors.weather_collector
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_API = "https://api.open-meteo.com/v1/forecast"
_CACHE = Path("data/weather_macro.json")
_TTL_S = 6 * 3600
_HDD_CDD_BASE_C = 18.0      # Standard-Komfortbasis für Gradtage
_PAST_DAYS = 7
_FORECAST_DAYS = 7

# Große Energie-Nachfragezentren (Heizen/Kühlen treibt Strom-/Gasnachfrage).
# Bewusst klein gehalten – ein Request pro Region.
_REGIONS: Dict[str, Dict[str, float]] = {
    "us_northeast": {"lat": 40.71, "lon": -74.01},   # New York – Gas-Heizlast
    "us_texas":     {"lat": 29.76, "lon": -95.37},    # Houston – ERCOT-Kühllast
    "us_midwest":   {"lat": 41.88, "lon": -87.63},    # Chicago – Heizlast
    "nw_europe":    {"lat": 50.11, "lon": 8.68},      # Frankfurt – EU Strom/Gas
}


class WeatherCollector:
    def __init__(self, regions: Optional[Dict[str, Dict[str, float]]] = None):
        self.regions = regions or _REGIONS

    # ── öffentliche API (keylos, gepuffert) ───────────────────────────────────
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        try:
            data = self._fetch_all()
        except Exception as e:
            log.debug("Weather: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        if not data:
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    # ── Abruf + Aggregation ───────────────────────────────────────────────────
    def _fetch_all(self) -> Dict:
        per_region: Dict[str, Dict] = {}
        for name, loc in self.regions.items():
            region = self._fetch_region(loc["lat"], loc["lon"])
            if region:
                per_region[name] = region

        if not per_region:
            return {}

        # Marktweiter Demand-Proxy: mittlere Gradtage/Tag über alle Regionen,
        # Forecast-Fenster vs. beobachtetes Baseline-Fenster.
        cur = _avg([r["degdays_forecast_per_day"] for r in per_region.values()])
        base = _avg([r["degdays_recent_per_day"] for r in per_region.values()])
        ratio = (cur / base) if base else 1.0
        label = ("ELEVATED" if ratio >= 1.15 else
                 "SUBDUED" if ratio <= 0.85 else "NORMAL")

        return {
            "regions": per_region,
            "degdays_forecast_per_day": round(cur, 1),
            "degdays_recent_per_day": round(base, 1),
            "ratio": round(ratio, 3),
            "demand_label": label,   # erwartete Energienachfrage vs. zuletzt
        }

    def _fetch_region(self, lat: float, lon: float) -> Optional[Dict]:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,wind_speed_10m_max",
            "past_days": _PAST_DAYS,
            "forecast_days": _FORECAST_DAYS,
            "timezone": "UTC",
        }
        resp = http_get(_API, params=params, timeout=20)
        if resp.status_code != 200:
            return None
        daily = (resp.json() or {}).get("daily") or {}
        tmax = daily.get("temperature_2m_max") or []
        tmin = daily.get("temperature_2m_min") or []
        wind = daily.get("wind_speed_10m_max") or []
        n = min(len(tmax), len(tmin))
        if n < _PAST_DAYS + 1:
            return None

        # Tagesmittel → Gradtage (HDD+CDD = Abweichung vom Komfort, beide Richtungen)
        degdays = [_degree_days((tmax[i] + tmin[i]) / 2.0) for i in range(n)]
        recent = degdays[:_PAST_DAYS]                 # beobachtete Vorwoche
        forecast = degdays[_PAST_DAYS:]               # kommende Tage
        if not forecast:
            return None

        return {
            "degdays_recent_per_day": round(_avg(recent), 1),
            "degdays_forecast_per_day": round(_avg(forecast), 1),
            "wind_forecast_max_kmh": round(_avg(wind[_PAST_DAYS:]) if len(wind) > _PAST_DAYS else 0.0, 1),
        }


# ── Helfer ────────────────────────────────────────────────────────────────────
def _degree_days(mean_temp_c: float) -> float:
    """HDD + CDD zur Basis 18 °C: wie weit weicht der Tag vom Komfort ab
    (beide Richtungen treiben Energienachfrage)."""
    return abs(mean_temp_c - _HDD_CDD_BASE_C)


def _avg(vals: List[float]) -> float:
    return (sum(vals) / len(vals)) if vals else 0.0


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
        log.debug("Weather-Cache schreiben fehlgeschlagen: %s", e)


if __name__ == "__main__":
    print("Weather: hole Energie-Nachfrage-Wetterproxy (Open-Meteo, keylos)…")
    out = WeatherCollector().read()
    print(json.dumps(out, indent=2) if out else "kein Ergebnis")
