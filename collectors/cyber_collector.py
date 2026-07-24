"""
collectors/cyber_collector.py – Cyber-Bedrohungs-Tempo als Risiko-Kontext.

Schlanke Komponente im EONET-Stil. Idee: ein plötzlicher Anstieg aktiv
ausgenutzter Schwachstellen ist ein grober Risiko-/Stimmungs-Hintergrund –
relevant für Cybersecurity-Titel (Rückenwind) und potenziell betroffene Branchen
(Gegenwind bei Großvorfällen). NUR als Makro-Kontext-Notiz, NICHT als Trade-
Trigger und NICHT als Bias/Sizing-Faktor.

Quelle: CISA KEV – Katalog bekannter, aktiv ausgenutzter Schwachstellen der US-
Cyber-Behörde. Frei, OHNE API-Key, sehr stabil.
  https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
  read() → Dict, 6h gepuffert in data/cyber_threat.json.
Bewusst schlank & fail-safe: jeder Fehler → letztes/leeres Ergebnis, nie ein Crash.

(Hinweis: ransomware.live wurde bewusst NICHT eingebunden – deren API verlangt
inzwischen einen API-Key und fällt damit aus dem keylosen Prinzip. CISA KEV allein
ist ausreichend und dauerhaft keylos.)

Limitierung (ehrlich): kein Ticker-Mapping, verrauscht, Meldelatenz. Rein als
Hintergrund-Notiz, kein Vorlauf-Signal. Handelsnutzen bewusst als gering
eingestuft.

Standalone-Test:  ./venv/bin/python -m collectors.cyber_collector
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

from logger import get_logger
from system.http import http_get

log = get_logger(__name__)

_KEV_API = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_CACHE = Path("data/cyber_threat.json")
_TTL_S = 6 * 3600
_WINDOW_DAYS = 7

# Schwelle fürs Backdrop-Label (bewusst konservativ, dokumentiert heuristisch).
_KEV_ELEVATED = 5           # ≥5 neue aktiv-ausgenutzte CVEs in 7 Tagen


class CyberCollector:
    # ── öffentliche API (keylos, gepuffert) ───────────────────────────────────
    def read(self) -> Dict:
        cached = _load_cache()
        if cached and (time.time() - cached.get("_ts", 0)) < _TTL_S:
            return cached.get("data", {})
        try:
            data = self._fetch()
        except Exception as e:
            log.debug("Cyber: Abruf fehlgeschlagen: %s", e)
            return cached.get("data", {}) if cached else {}
        if not data:
            return cached.get("data", {}) if cached else {}
        _save_cache(data)
        return data

    # ── Abruf + Aggregation ───────────────────────────────────────────────────
    def _fetch(self) -> Dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_WINDOW_DAYS)
        kev = _recent_kev(cutoff)
        if kev is None:
            return {}                                # Quelle tot → nichts merken
        return {
            "kev_7d": kev,                           # neue aktiv-ausgenutzte CVEs (7 T.)
            "threat_label": "ELEVATED" if kev >= _KEV_ELEVATED else "NORMAL",
        }


# ── Quellen-Parser ──────────────────────────────────────────────────────────
def _recent_kev(cutoff: datetime) -> Optional[int]:
    """Anzahl neu in den KEV-Katalog aufgenommener CVEs seit `cutoff`."""
    try:
        resp = http_get(_KEV_API, timeout=20)
        if resp.status_code != 200:
            return None
        vulns = (resp.json() or {}).get("vulnerabilities") or []
    except Exception:
        return None
    n = 0
    for v in vulns:
        d = _parse_date(v.get("dateAdded"))
        if d and d >= cutoff:
            n += 1
    return n


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ── Helfer ────────────────────────────────────────────────────────────────────
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
        log.debug("Cyber-Cache schreiben fehlgeschlagen: %s", e)


def cyber_summary(data: Dict) -> str:
    """Kompakte Klartext-Zeile für den Prompt."""
    n = data.get("kev_7d")
    return f"{n} neue aktiv-ausgenutzte CVEs (7 T.)" if n else ""


if __name__ == "__main__":
    print("Cyber: hole CISA KEV (keylos)…")
    out = CyberCollector().read()
    print(json.dumps(out, indent=2) if out else "kein Ergebnis")
