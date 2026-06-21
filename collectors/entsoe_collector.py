"""
collectors/entsoe_collector.py – EU-Stromlast als Industrieaktivitäts-Proxy.

Markt-weiter Input für macro_context. Die realisierte Gesamt-Stromlast (Default
Deutschland) ist ein hochfrequenter Proxy für Industrie-/Wirtschaftsaktivität;
ein deutlicher Rückgang gegenüber dem jüngsten Schnitt kann auf nachlassende
Aktivität hindeuten (relevant für Versorger & zyklische EU-Werte).

Quelle: ENTSO-E Transparency Platform (freier Token, ENTSOE_API_TOKEN; XML-API).
documentType A65 (System total load), processType A16 (realised). read() → Dict,
6h gepuffert in data/entsoe_macro.json. Fail-safe → {}.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_API = "https://web-api.tp.entsoe.eu/api"
_DOMAIN_DE = "10Y1001A1001A83F"     # Deutschland (Bidding Zone)
_CACHE = Path("data/entsoe_macro.json")
_TTL_S = 6 * 3600


class ENTSOECollector:
    def __init__(self, domain: str = _DOMAIN_DE, zone_name: str = "DE"):
        self.domain = domain
        self.zone_name = zone_name

    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        token = os.getenv("ENTSOE_API_TOKEN", "").strip()
        if not token:
            log.debug("ENTSO-E: ENTSOE_API_TOKEN fehlt – übersprungen.")
            return cached.get("data", {}) if cached else {}
        try:
            data = self._fetch(token)
        except Exception as e:
            log.debug("ENTSO-E: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    def _fetch(self, token: str) -> Dict:
        now = datetime.utcnow()
        start = now - timedelta(days=8)
        params = {
            "securityToken": token,
            "documentType": "A65",
            "processType": "A16",
            "outBiddingZone_Domain": self.domain,
            "periodStart": start.strftime("%Y%m%d0000"),
            "periodEnd": now.strftime("%Y%m%d0000"),
        }
        resp = http_get(_API, params=params, timeout=20)
        if resp.status_code != 200:
            return {}
        loads = self._parse_loads(resp.content)
        if len(loads) < 48:
            return {}
        # Letzter Tag (grob letzte 96 Viertelstunden) vs. Wochen-Schnitt
        recent = loads[-96:] if len(loads) >= 96 else loads
        cur_avg = sum(recent) / len(recent)
        base_avg = sum(loads) / len(loads)
        ratio = cur_avg / base_avg if base_avg else 1.0
        label = ("ELEVATED" if ratio >= 1.05 else
                 "SUBDUED" if ratio <= 0.95 else "NORMAL")
        return {
            "zone": self.zone_name,
            "load_recent_mw": round(cur_avg),
            "load_week_avg_mw": round(base_avg),
            "ratio": round(ratio, 3),
            "activity_label": label,
        }

    @staticmethod
    def _parse_loads(xml_bytes: bytes) -> List[float]:
        from xml.etree import ElementTree as ET
        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            return []
        ns = ""
        if root.tag.startswith("{"):
            ns = "{" + root.tag.split("}")[0].strip("{") + "}"
        vals: List[float] = []
        for pt in root.iter(f"{ns}Point"):
            q = pt.find(f"{ns}quantity")
            if q is not None and q.text:
                try:
                    vals.append(float(q.text))
                except ValueError:
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
        log.debug("ENTSO-E-Cache schreiben fehlgeschlagen: %s", e)
