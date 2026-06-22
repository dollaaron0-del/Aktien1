"""
collectors/econdb_collector.py – Globale Makro-Momentum als Ergänzung zum US-FRED.

Markt-weiter Input für macro_context (analog ENTSO-E/FRED). FRED ist US-zentriert;
Econdb liefert dieselbe Art Reihen für die großen Volkswirtschaften (US, Euro-Raum,
China, Deutschland). Wir lesen Wachstums-Momentum-Reihen (Default: reales BIP je
Land) und verdichten sie zu einem einfachen Diffusions-Signal: Wie viele der großen
Blöcke beschleunigen vs. verlangsamen sich → GLOBAL_EXPANSION / NEUTRAL / SLOWDOWN.
Gedacht als *breiter Risk-Kontext*, NICHT als Trade-Trigger.

Quelle: Econdb (https://www.econdb.com/api/series/{TICKER}/?token=...&format=json).
WICHTIG – ENTGEGEN der public-apis-Liste ist Econdb NICHT keylos: jeder Aufruf
verlangt einen (kostenlosen) Token. DRF-TokenAuth via Query-Param `?token=`.
  → ECONDB_API_TOKEN in .env setzen (kostenlos: econdb.com registrieren).
Ohne Token wird sauber übersprungen (fail-safe → {}), wie bei ENTSO-E.

Hinweis: Die Default-Ticker (_SERIES) und das exakte JSON-Schema sind nach
bestem Wissen gesetzt, aber ohne gültigen Token nicht live verifizierbar. Der
Standalone-Lauf druckt pro Ticker, ob er auflöst – danach Ticker bei Bedarf
in _SERIES anpassen. Keylose Alternative für dasselbe Ziel: DBnomics.

read() → Dict, 12h gepuffert in data/econdb_macro.json (Makro = niederfrequent).
Standalone-Test:  ECONDB_API_TOKEN=... ./venv/bin/python -m collectors.econdb_collector
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_API = "https://www.econdb.com/api/series/{ticker}/"
_CACHE = Path("data/econdb_macro.json")
_TTL_S = 12 * 3600

# label → Econdb-Ticker. Reales BIP der großen Blöcke (Wachstums-Momentum).
# Ggf. nach Token-Check anpassen (Standalone-Lauf zeigt, welche auflösen).
_SERIES: Dict[str, str] = {
    "gdp_us":   "RGDPUS",   # USA
    "gdp_ea":   "RGDPEA",   # Euro-Raum
    "gdp_cn":   "RGDPCN",   # China
    "gdp_de":   "RGDPDE",   # Deutschland
}


class EcondbCollector:
    def __init__(self, series: Optional[Dict[str, str]] = None):
        self.series = series or _SERIES

    # ── öffentliche API (token-gated, gepuffert) ──────────────────────────────
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        token = os.getenv("ECONDB_API_TOKEN", "").strip()
        if not token:
            log.debug("Econdb: ECONDB_API_TOKEN fehlt – übersprungen.")
            return cached.get("data", {}) if cached else {}
        try:
            data = self._fetch_all(token)
        except Exception as e:
            log.debug("Econdb: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        if not data:
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    # ── Abruf + Aggregation ───────────────────────────────────────────────────
    def _fetch_all(self, token: str) -> Dict:
        per_series: Dict[str, Dict] = {}
        for label, ticker in self.series.items():
            point = self._fetch_series(ticker, token)
            if point:
                per_series[label] = point
            else:
                log.debug("Econdb: Ticker %s lieferte keine Daten.", ticker)

        if not per_series:
            return {}

        rising = sum(1 for p in per_series.values() if p["trend"] == "UP")
        falling = sum(1 for p in per_series.values() if p["trend"] == "DOWN")
        n = len(per_series)
        diffusion = (rising - falling) / n   # -1.0 … +1.0
        label = ("GLOBAL_EXPANSION" if diffusion >= 0.34 else
                 "GLOBAL_SLOWDOWN" if diffusion <= -0.34 else "GLOBAL_NEUTRAL")

        return {
            "series": per_series,
            "diffusion": round(diffusion, 2),
            "n": n,
            "global_label": label,
        }

    def _fetch_series(self, ticker: str, token: str) -> Optional[Dict]:
        params = {"token": token, "format": "json"}
        resp = http_get(_API.format(ticker=ticker), params=params, timeout=20)
        if resp.status_code != 200:
            return None
        try:
            obj = resp.json()
        except (ValueError, TypeError):
            return None

        dates, values = _extract_series(obj)
        # NaN/Inf-Guard (vgl. yfinance-NaN-Falle): nur endliche Werte zählen.
        clean: List[Tuple[str, float]] = [
            (d, float(v)) for d, v in zip(dates, values)
            if v is not None and _is_finite(v)
        ]
        if len(clean) < 2:
            return None

        latest_date, latest = clean[-1]
        _, prev = clean[-2]
        trend = "UP" if latest > prev else "DOWN" if latest < prev else "FLAT"
        return {
            "ticker": ticker,
            "latest": round(latest, 3),
            "prev": round(prev, 3),
            "trend": trend,
            "date": latest_date,
        }


# ── Helfer ────────────────────────────────────────────────────────────────────
def _extract_series(obj: Dict) -> Tuple[List, List]:
    """Holt (dates, values) aus dem Econdb-JSON. Robust gegenüber zwei Formen:
    Einzelobjekt mit `data:{dates,values}` oder `results:[{data:...}]`."""
    if not isinstance(obj, dict):
        return [], []
    data = obj.get("data")
    if data is None:
        results = obj.get("results")
        if isinstance(results, list) and results:
            data = (results[0] or {}).get("data")
    if not isinstance(data, dict):
        return [], []
    dates = data.get("dates") or []
    values = data.get("values") or []
    return list(dates), list(values)


def _is_finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


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
        log.debug("Econdb-Cache schreiben fehlgeschlagen: %s", e)


if __name__ == "__main__":
    tok = os.getenv("ECONDB_API_TOKEN", "").strip()
    if not tok:
        print("Econdb: ECONDB_API_TOKEN fehlt – setze ihn (kostenlos: econdb.com) "
              "und starte erneut. Ohne Token wird im Live-Pfad sauber übersprungen.")
    else:
        print("Econdb: hole globale BIP-Momentum-Reihen…")
        c = EcondbCollector()
        # Pro-Ticker-Diagnose: zeigt sofort, welche Ticker auflösen.
        for label, ticker in c.series.items():
            p = c._fetch_series(ticker, tok)
            print(f"  {label:8} {ticker:8} -> {p['trend'] if p else 'KEINE DATEN (Ticker prüfen)'}")
        out = c.read()
        print(json.dumps(out, indent=2) if out else "kein aggregiertes Ergebnis")
