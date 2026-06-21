"""
collectors/eia_collector.py – US-Rohöl-Lagerbestände (EIA).

Markt-weiter Energie-Fundamentaldaten-Input für macro_context. Ergänzt das
Tanker-Experiment: Schiffsbewegung (Fluss) vs. Lagerbestand (Bestand) sind zwei
unabhängige Signale derselben Angebots-/Nachfrage-Story.

Reihe WCESTUS1 = Weekly U.S. Ending Stocks of Crude Oil. Wochenveränderung
(Build/Draw) ist der marktrelevante Impuls für Öl & Energiewerte.

Quelle: EIA API v2 (freier Key, EIA_API_KEY). read() → Dict, 6h gepuffert in
data/eia_macro.json. Fail-safe → {}.
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

_API = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
_CACHE = Path("data/eia_macro.json")
_TTL_S = 6 * 3600


class EIACollector:
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        key = os.getenv("EIA_API_KEY", "").strip()
        if not key:
            log.debug("EIA: EIA_API_KEY fehlt – übersprungen.")
            return cached.get("data", {}) if cached else {}
        try:
            data = self._fetch(key)
        except Exception as e:
            log.debug("EIA: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    def _fetch(self, key: str) -> Dict:
        params = {
            "api_key": key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": "WCESTUS1",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 8,
        }
        resp = http_get(_API, params=params, timeout=15)
        if resp.status_code != 200:
            return {}
        rows = (((resp.json() or {}).get("response", {})) or {}).get("data", []) or []
        series: List[float] = []
        for r in rows:                       # rows sind desc → umdrehen
            try:
                series.append(float(r["value"]))
            except (ValueError, KeyError, TypeError):
                continue
        series.reverse()
        if len(series) < 2:
            return {}
        cur, prev = series[-1], series[-2]
        wow = cur - prev
        # Lageraufbau = bärisch für Öl(preis), Lagerabbau = bullisch
        label = "BUILD" if wow > 0 else ("DRAW" if wow < 0 else "FLAT")
        return {
            "crude_stocks_mbbl": round(cur, 1),
            "wow_change_mbbl": round(wow, 1),
            "label": label,
            "oil_bias": "BEARISH" if label == "BUILD" else ("BULLISH" if label == "DRAW" else "NEUTRAL"),
        }


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
        log.debug("EIA-Cache schreiben fehlgeschlagen: %s", e)
