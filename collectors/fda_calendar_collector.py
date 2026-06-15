"""
collectors/fda_calendar_collector.py – Biotech-Katalysator-Kalender.

Binäre Katalysatoren (Studien-Readouts, FDA-Entscheidungen) bewegen Small/Mid-
Biotech massiv – und die Termine sind Wochen vorher bekannt = echter Vorlauf.

Freie, strukturierte Quelle: clinicaltrials.gov API v2. Wir fragen die laufenden
Phase-2/3-Studien des jeweiligen Unternehmens ab und melden bevorstehende
(oder gerade fällige) primäre Abschluss-Termine (primaryCompletionDate) als
Readout-Katalysatoren. PDUFA-Daten gibt es in keiner sauberen Gratis-API –
diese werden ohnehin via SEC-8-K/PR erfasst.

Effizienz: nur Healthcare-Ticker werden überhaupt abgefragt (Sektor via yfinance,
30-Tage-Cache), Studien-Ergebnisse 1-Tag-Cache. Fail-safe: [] bei jedem Fehler.
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

_CT_API        = "https://clinicaltrials.gov/api/v2/studies"
_PROFILE_CACHE = Path("data/ticker_profiles.json")
_PROFILE_TTL_S = 30 * 24 * 3600
_TRIAL_TTL_S   = 24 * 3600
_HEALTHCARE_HINTS = ("healthcare", "biotech", "drug", "pharmaceutical", "life sciences", "health")


class FDACalendarCollector:
    def __init__(self, lookahead_days: int = 120, lookback_days: int = 21):
        self.lookahead_days = lookahead_days
        self.lookback_days  = lookback_days
        self._trial_cache: Dict[str, tuple] = {}   # ticker → (items, ts)

    # ── öffentliche API ───────────────────────────────────────────────────────
    def collect(self, ticker: str) -> List[Dict]:
        try:
            profile = self._profile(ticker)
            if not profile or not self._is_healthcare(profile):
                return []
            company = profile.get("company")
            if not company:
                return []
            cached = self._trial_cache.get(ticker)
            if cached and (time.time() - cached[1]) < _TRIAL_TTL_S:
                return cached[0]
            studies = self._query_trials(company)
            items = self._to_items(ticker, studies)
            self._trial_cache[ticker] = (items, time.time())
            return items
        except Exception as e:
            log.debug("[%s] FDA-Kalender: %s", ticker, e)
            return []

    # ── Sektor/Company-Profil (yfinance, 30-Tage-Cache) ───────────────────────
    def _profile(self, ticker: str) -> Optional[Dict]:
        store = self._load_profiles()
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
        self._save_profiles(store)
        return rec

    @staticmethod
    def _is_healthcare(profile: Dict) -> bool:
        blob = f"{profile.get('sector','')} {profile.get('industry','')}".lower()
        return any(h in blob for h in _HEALTHCARE_HINTS)

    # ── clinicaltrials.gov v2 ─────────────────────────────────────────────────
    def _query_trials(self, company: str) -> List[Dict]:
        params = {
            "query.spons": company,
            "filter.overallStatus": "RECRUITING|ACTIVE_NOT_RECRUITING|ENROLLING_BY_INVITATION",
            "pageSize": 50,
        }
        resp = http_get(_CT_API, params=params, timeout=15)
        if resp.status_code != 200:
            log.debug("clinicaltrials HTTP %d für %s", resp.status_code, company)
            return []
        return (resp.json() or {}).get("studies", []) or []

    # ── Studien → News-Items ──────────────────────────────────────────────────
    def _to_items(self, ticker: str, studies: List[Dict]) -> List[Dict]:
        now = datetime.utcnow()
        lo  = now - timedelta(days=self.lookback_days)
        hi  = now + timedelta(days=self.lookahead_days)
        items: List[Dict] = []
        for st in studies:
            try:
                ps     = st.get("protocolSection", {}) or {}
                ident  = ps.get("identificationModule", {}) or {}
                design = ps.get("designModule", {}) or {}
                status = ps.get("statusModule", {}) or {}
                phases = [p.upper() for p in (design.get("phases") or [])]
                if not any(p in ("PHASE2", "PHASE3") for p in phases):
                    continue
                pcd = (status.get("primaryCompletionDateStruct", {}) or {}).get("date", "")
                dt  = self._parse_date(pcd)
                if dt is None or not (lo <= dt <= hi):
                    continue
                title = ident.get("briefTitle") or "Studie"
                nct   = ident.get("nctId") or ""
                phase = "/".join(p.replace("PHASE", "Phase ") for p in phases if p.startswith("PHASE"))
                when  = "fällig" if dt < now else f"~{dt.date().isoformat()}"
                items.append({
                    "source":       "FDA/ClinicalTrials",
                    "ticker":       ticker,
                    "title":        f"{phase} Studien-Readout {when}: {title}",
                    "text":         f"NCT {nct}, primärer Abschluss {pcd}. Binärer Katalysator – Readout kann den Kurs stark bewegen.",
                    "url":          f"https://clinicaltrials.gov/study/{nct}" if nct else "",
                    "published_at": now.isoformat(),
                    "priority":     "HIGH",
                })
            except Exception:
                continue
        return items

    @staticmethod
    def _parse_date(s: str) -> Optional[datetime]:
        for fmt in ("%Y-%m-%d", "%Y-%m"):
            try:
                return datetime.strptime(s, fmt)
            except (ValueError, TypeError):
                continue
        return None

    # ── Profil-Cache (Persistenz) ─────────────────────────────────────────────
    @staticmethod
    def _load_profiles() -> Dict:
        try:
            return json.loads(_PROFILE_CACHE.read_text())
        except Exception:
            return {}

    @staticmethod
    def _save_profiles(store: Dict) -> None:
        try:
            _PROFILE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _PROFILE_CACHE.write_text(json.dumps(store))
        except Exception as e:
            log.debug("Profil-Cache schreiben fehlgeschlagen: %s", e)
