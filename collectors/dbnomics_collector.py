"""
collectors/dbnomics_collector.py – Globales BIP-Wachstums-Momentum (keylos).

Markt-weiter Input für macro_context (analog ENTSO-E/FRED). FRED ist US-zentriert;
hier kommt der globale Kontext: reales BIP-Quartalswachstum der großen Blöcke
(US, Euro-Raum, China, Deutschland, Japan, UK) aus EINEM konsistenten OECD-Datensatz.
Verdichtet zu einem einfachen Momentum-Signal: Wie viele Blöcke beschleunigen vs.
verlangsamen sich (aktuelles Quartal vs. Vorquartal) → GLOBAL_ACCELERATING /
NEUTRAL / GLOBAL_DECELERATING. Zusätzlich: wie viele Blöcke schrumpfen (Wachstum<0)
und das G20-Aggregat als Schlagzahl. Gedacht als *breiter Risk-Kontext*, NICHT als
Trade-Trigger.

Quelle: DBnomics (https://db.nomics.world) – frei, OHNE API-Key, ohne Limit-Drama.
Aggregiert offizielle Statistik (hier: OECD Quarterly National Accounts, G20).
  /v22/series/{provider}/{dataset}/{series_code}?observations=true → JSON mit
  parallelen `period`/`value`-Arrays. read() → Dict, 24h gepuffert
  (data/dbnomics_macro.json). Fail-safe → {}.

Hinweis (ehrlich): BIP ist niederfrequent (1×/Quartal) und wird revidiert – das
Signal bewegt sich langsam und ist Kontext, kein Timing. Werte je Block sind nicht
quer-vergleichbar (US annualisiert vs. EU QoQ), daher zählt nur die Richtung je
Block (eigener Vorwert), nie der Quervergleich der Absolutwerte.

Standalone-Test:  ./venv/bin/python -m collectors.dbnomics_collector
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_API = "https://api.db.nomics.world/v22/series/{sid}"
_CACHE = Path("data/dbnomics_macro.json")
_TTL_S = 24 * 3600

# OECD "Quarterly real GDP growth - G20", QoQ-Wachstum (G1), BIP (B1GQ).
_DS = "OECD/DSD_NAMAIN1@DF_QNA_EXPENDITURE_GROWTH_G20"
_SUFFIX = "S1.S1.B1GQ._Z._Z._Z.PC.L.G1.T0102"

# label → DBnomics-Serien-ID (alle live verifiziert, aktuell bis 2026-Q1).
_BLOCKS: Dict[str, str] = {
    "us": f"{_DS}/Q.Y.USA.{_SUFFIX}",
    "ea": f"{_DS}/Q.Y.EA.{_SUFFIX}",    # Euro-Raum
    "cn": f"{_DS}/Q.Y.CHN.{_SUFFIX}",
    "de": f"{_DS}/Q.Y.DEU.{_SUFFIX}",
    "jp": f"{_DS}/Q.Y.JPN.{_SUFFIX}",
    "uk": f"{_DS}/Q.Y.GBR.{_SUFFIX}",
}
_G20_AGG = f"{_DS}/Q.Y.G20.{_SUFFIX}"   # Schlagzahl-Aggregat


class DBnomicsCollector:
    def __init__(self, blocks: Optional[Dict[str, str]] = None):
        self.blocks = blocks or _BLOCKS

    # ── öffentliche API (keylos, gepuffert) ───────────────────────────────────
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        try:
            data = self._fetch_all()
        except Exception as e:
            log.debug("DBnomics: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        if not data:
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    # ── Abruf + Aggregation ───────────────────────────────────────────────────
    def _fetch_all(self) -> Dict:
        per_block: Dict[str, Dict] = {}
        for label, sid in self.blocks.items():
            point = self._fetch_series(sid)
            if point:
                per_block[label] = point
            else:
                log.debug("DBnomics: Serie %s lieferte keine Daten.", sid)

        if not per_block:
            return {}

        accel = sum(1 for p in per_block.values() if p["trend"] == "UP")
        decel = sum(1 for p in per_block.values() if p["trend"] == "DOWN")
        contracting = sum(1 for p in per_block.values() if p["latest"] < 0)
        n = len(per_block)
        diffusion = (accel - decel) / n   # -1.0 … +1.0
        label = ("GLOBAL_ACCELERATING" if diffusion >= 0.34 else
                 "GLOBAL_DECELERATING" if diffusion <= -0.34 else "GLOBAL_NEUTRAL")

        g20 = self._fetch_series(_G20_AGG)

        return {
            "blocks": per_block,
            "accelerating": accel,
            "decelerating": decel,
            "contracting": contracting,
            "n": n,
            "diffusion": round(diffusion, 2),
            "momentum_label": label,
            "g20_latest": g20["latest"] if g20 else None,
            "period": next(iter(per_block.values()))["period"],
        }

    def _fetch_series(self, sid: str) -> Optional[Dict]:
        params = {"observations": "true", "limit": "1"}
        resp = http_get(_API.format(sid=sid), params=params, timeout=20)
        if resp.status_code != 200:
            return None
        try:
            obj = resp.json()
        except (ValueError, TypeError):
            return None

        periods, values = _extract_observations(obj)
        # NaN/None-Guard (vgl. yfinance-NaN-Falle): nur endliche Werte zählen.
        clean: List[Tuple[str, float]] = [
            (p, float(v)) for p, v in zip(periods, values)
            if v is not None and _is_finite(v)
        ]
        if len(clean) < 2:
            return None

        latest_period, latest = clean[-1]
        _, prev = clean[-2]
        trend = "UP" if latest > prev else "DOWN" if latest < prev else "FLAT"
        return {
            "latest": round(latest, 2),
            "prev": round(prev, 2),
            "trend": trend,
            "period": latest_period,
        }


# ── Helfer ────────────────────────────────────────────────────────────────────
def _extract_observations(obj: Dict) -> Tuple[List, List]:
    """Holt (period, value) aus der DBnomics-Serien-Antwort:
    {"series": {"docs": [{"period": [...], "value": [...]}]}}"""
    try:
        docs = ((obj or {}).get("series") or {}).get("docs") or []
        if not docs:
            return [], []
        doc = docs[0] or {}
        return list(doc.get("period") or []), list(doc.get("value") or [])
    except (AttributeError, TypeError):
        return [], []


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
        log.debug("DBnomics-Cache schreiben fehlgeschlagen: %s", e)


if __name__ == "__main__":
    print("DBnomics: hole globales BIP-Wachstums-Momentum (keylos)…")
    out = DBnomicsCollector().read()
    print(json.dumps(out, indent=2) if out else "kein Ergebnis")
