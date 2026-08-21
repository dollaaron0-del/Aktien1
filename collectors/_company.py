"""
collectors/_company.py – Geteilter Ticker→Firma/Sektor-Resolver.

Mehrere Collectors brauchen aus einem Ticker den Firmennamen bzw. den Sektor.
Statt das (wie historisch gewachsen) je Collector zu duplizieren, kapselt dieses
Modul den Lookup einmal: yfinance-Abruf, gepuffert in data/ticker_profiles.json
(gleiches Format, das der FDA-Collector nutzt → ein gemeinsamer Cache).

Fail-safe: bei jedem Fehler leeres Profil bzw. None, nie ein Crash.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional

from logger import get_logger

log = get_logger(__name__)

_PROFILE_CACHE = Path("data/ticker_profiles.json")
_PROFILE_TTL_S = 30 * 24 * 3600

_LEGAL_SUFFIXES = (
    ", Inc.", " Inc.", ", LLC", " LLC", " Corp.", ", Corp.", " Corporation",
    " Limited", " Ltd.", " Holdings", " Co.", " plc", " PLC", " S.A.", " AG", " N.V.",
)


def profile(ticker: str) -> Dict:
    """{'sector','industry','company','ts'} – gepuffert 30 Tage. Nie None."""
    store = _load()
    rec = store.get(ticker.upper())
    if rec and (time.time() - rec.get("ts", 0)) < _PROFILE_TTL_S:
        return rec
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        rec = {
            "sector":   (info.get("sector") or "").strip(),
            "industry": (info.get("industry") or "").strip(),
            "company":  (info.get("longName") or info.get("shortName") or "").strip(),
            "ts":       time.time(),
        }
    except Exception as e:
        log.debug("[%s] Profil-Abruf fehlgeschlagen: %s", ticker, e)
        rec = {"sector": "", "industry": "", "company": "", "ts": time.time()}
    store[ticker.upper()] = rec
    _save(store)
    return rec


def company_name(ticker: str, strip_legal: bool = True) -> Optional[str]:
    """Firmenname zum Ticker; optional ohne Rechtsform-Suffixe. None, wenn unbekannt."""
    name = (profile(ticker).get("company") or "").strip()
    if not name:
        return None
    if strip_legal:
        for suffix in _LEGAL_SUFFIXES:
            name = name.replace(suffix, "")
    return name.strip() or None


def sector(ticker: str) -> str:
    return (profile(ticker).get("sector") or "").strip()


def matches_sector(ticker: str, hints: tuple) -> bool:
    """True, wenn Sektor/Industrie einen der Hinweise (lowercase) enthält."""
    p = profile(ticker)
    blob = f"{p.get('sector','')} {p.get('industry','')}".lower()
    return any(h in blob for h in hints)


def _load() -> Dict:
    try:
        return json.loads(_PROFILE_CACHE.read_text())
    except Exception:
        return {}


def _save(store: Dict) -> None:
    try:
        _PROFILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _PROFILE_CACHE.write_text(json.dumps(store))
    except Exception as e:
        log.debug("Profil-Cache schreiben fehlgeschlagen: %s", e)
