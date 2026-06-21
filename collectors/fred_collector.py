"""
collectors/fred_collector.py – Makro-Risikoreihen von FRED (St. Louis Fed).

Markt-weiter Input für macro_context (kein Pro-Ticker-Collector). Zwei robuste
Risiko-Barometer:
  - BAMLH0A0HYM2 : High-Yield Credit Spread (OAS) – steigt früh bei Stress.
  - ICSA         : Initial Jobless Claims – Arbeitsmarkt-Frühindikator.

Quelle: FRED API (freier Key, FRED_API_KEY). read() liefert ein kompaktes Dict,
gepuffert (6h) in data/fred_macro.json. Fail-safe → {}.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_API = "https://api.stlouisfed.org/fred/series/observations"
_CACHE = Path("data/fred_macro.json")
_TTL_S = 6 * 3600


class FREDCollector:
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        key = os.getenv("FRED_API_KEY", "").strip()
        if not key:
            log.debug("FRED: FRED_API_KEY fehlt – übersprungen.")
            return cached.get("data", {}) if cached else {}
        try:
            data = self._fetch(key)
        except Exception as e:
            log.debug("FRED: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    def _fetch(self, key: str) -> Dict:
        hy = self._series(key, "BAMLH0A0HYM2")        # täglich
        claims = self._series(key, "ICSA")            # wöchentlich
        out: Dict = {}

        if len(hy) >= 22:
            cur = hy[-1]
            avg = sum(hy[-22:]) / 22
            out["hy_oas"] = round(cur, 2)
            out["hy_oas_trend"] = "WIDENING" if cur > avg * 1.05 else (
                "TIGHTENING" if cur < avg * 0.95 else "FLAT")

        if len(claims) >= 5:
            cur = claims[-1]
            avg = sum(claims[-5:]) / 5
            out["claims"] = int(cur)
            out["claims_trend"] = "RISING" if cur > avg * 1.05 else (
                "FALLING" if cur < avg * 0.95 else "FLAT")

        # Grobes Stress-Label aus beiden Reihen
        stress = 0
        if out.get("hy_oas_trend") == "WIDENING":
            stress += 1
        if out.get("claims_trend") == "RISING":
            stress += 1
        if out.get("hy_oas", 0) >= 5.0:
            stress += 1
        out["stress_label"] = ("ELEVATED" if stress >= 2 else
                               "WATCH" if stress == 1 else "CALM")
        return out

    def _series(self, key: str, series_id: str) -> List[float]:
        params = {
            "series_id": series_id, "api_key": key, "file_type": "json",
            "sort_order": "asc", "limit": 60,
        }
        resp = http_get(_API, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        vals: List[float] = []
        for o in (resp.json() or {}).get("observations", []) or []:
            try:
                vals.append(float(o["value"]))
            except (ValueError, KeyError, TypeError):
                continue
        return vals


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
        log.debug("FRED-Cache schreiben fehlgeschlagen: %s", e)
