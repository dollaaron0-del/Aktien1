"""
MacroCalendar – überwacht wichtige US-Wirtschaftstermine via FRED API (kostenlos).
Vor kritischen Terminen pausiert der Bot neue Käufe oder reduziert Positionsgrößen.

Wichtige Events:
  FOMC-Zinsentscheid, CPI, PPI, NFP (Jobs), GDP, PCE, Retail Sales
"""
from __future__ import annotations

import os
import json
import requests
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional

_FRED_BASE = "https://api.stlouisfed.org/fred"
_CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "macro_calendar.json")
_CACHE_TTL_HOURS = 12

# FRED Series IDs für wichtige Indikatoren
_FRED_SERIES = {
    "CPI":          "CPIAUCSL",
    "PPI":          "PPIACO",
    "PCE":          "PCE",
    "GDP":          "GDP",
    "RETAIL_SALES": "RSAFS",
    "UNEMPLOYMENT": "UNRATE",
    "NFP":          "PAYEMS",
}

# Kritische Events (Markt reagiert stark)
_HIGH_IMPACT = {"CPI", "NFP", "FOMC", "GDP", "PCE"}
# Tage vor dem Event: neue Käufe blockieren
_BLOCK_DAYS_BEFORE = 1
# Tage nach dem Event: erhöhte Vorsicht
_CAUTION_DAYS_AFTER = 1


class MacroEvent:
    def __init__(self, name: str, release_date: date, impact: str = "MEDIUM"):
        self.name = name
        self.release_date = release_date
        self.impact = impact  # HIGH | MEDIUM | LOW

    @property
    def days_until(self) -> int:
        return (self.release_date - date.today()).days

    @property
    def days_since(self) -> int:
        return (date.today() - self.release_date).days


class MacroCalendar:
    """Lädt Makro-Termine und gibt Warn-Signale für den Bot."""

    def __init__(self, fred_api_key: str = ""):
        self.fred_api_key = fred_api_key or os.getenv("FRED_API_KEY", "")
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)

    def get_upcoming_events(self, days_ahead: int = 7) -> List[MacroEvent]:
        """Gibt alle Makro-Events der nächsten N Tage zurück."""
        events = self._load_or_fetch()
        today = date.today()
        cutoff = today + timedelta(days=days_ahead)
        result = []
        for e in events:
            if today - timedelta(days=_CAUTION_DAYS_AFTER) <= e.release_date <= cutoff:
                result.append(e)
        result.sort(key=lambda x: x.release_date)
        return result

    def should_block_buy(self) -> tuple[bool, str]:
        """
        Gibt (True, Grund) zurück wenn ein hochrelevantes Event bevorsteht
        oder gerade stattgefunden hat.
        """
        events = self._load_or_fetch()
        for e in events:
            if e.impact != "HIGH":
                continue
            if 0 <= e.days_until <= _BLOCK_DAYS_BEFORE:
                return True, f"{e.name} in {e.days_until}d – neue Käufe pausiert"
            if 0 <= e.days_since <= _CAUTION_DAYS_AFTER:
                return True, f"{e.name} war gestern – Marktvolatilität erhöht"
        return False, ""

    def get_position_size_modifier(self) -> float:
        """
        Gibt einen Multiplikator für Positionsgrößen zurück.
        1.0 = normal, 0.5 = halbe Größe bei erhöhter Unsicherheit.
        """
        events = self._load_or_fetch()
        for e in events:
            if e.impact == "HIGH" and 0 <= e.days_until <= 3:
                return 0.5
            if e.impact == "MEDIUM" and e.days_until == 0:
                return 0.75
        return 1.0

    def summary(self) -> str:
        """Kurze Textzusammenfassung für Logs/Dashboard."""
        events = self.get_upcoming_events(7)
        if not events:
            return "Keine kritischen Makro-Termine in den nächsten 7 Tagen."
        lines = ["Kommende Makro-Termine:"]
        for e in events:
            tag = "🔴" if e.impact == "HIGH" else "🟡"
            when = f"in {e.days_until}d" if e.days_until >= 0 else f"vor {e.days_since}d"
            lines.append(f"  {tag} {e.name} ({when}, {e.release_date})")
        return "\n".join(lines)

    # ── Intern ───────────────────────────────────────────────────────────────

    def _load_or_fetch(self) -> List[MacroEvent]:
        cached = self._load_cache()
        if cached is not None:
            return cached
        events = self._fetch_events()
        self._save_cache(events)
        return events

    def _fetch_events(self) -> List[MacroEvent]:
        events: List[MacroEvent] = []

        # FOMC-Termine (hardcoded, Fed veröffentlicht Jahresplan)
        fomc_dates = self._get_fomc_dates()
        for d in fomc_dates:
            events.append(MacroEvent("FOMC-Zinsentscheid", d, "HIGH"))

        # FRED-Serien: letztes Release-Datum als Proxy für nächstes Datum
        if self.fred_api_key:
            for name, series_id in _FRED_SERIES.items():
                release_date = self._fetch_next_release(series_id)
                if release_date:
                    impact = "HIGH" if name in _HIGH_IMPACT else "MEDIUM"
                    events.append(MacroEvent(name, release_date, impact))
        else:
            # Ohne API-Key: Standard-Daten schätzen
            events += self._estimate_monthly_events()

        return events

    @staticmethod
    def _get_fomc_dates() -> List[date]:
        """FOMC tagt ca. alle 6 Wochen. Schätze nächste 3 Termine."""
        today = date.today()
        # Bekannte 2025/2026 FOMC-Termine (vereinfacht)
        known = [
            date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
            date(2025, 10, 29), date(2025, 12, 10),
            date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
            date(2026, 6, 17), date(2026, 7, 29),
        ]
        return [d for d in known if d >= today - timedelta(days=2)][:3]

    def _fetch_next_release(self, series_id: str) -> Optional[date]:
        """Holt das letzte bekannte Release-Datum von FRED."""
        try:
            url = f"{_FRED_BASE}/series/observations"
            params = {
                "series_id": series_id,
                "api_key": self.fred_api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            }
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code != 200:
                return None
            obs = resp.json().get("observations", [])
            if not obs:
                return None
            last_date = datetime.strptime(obs[0]["date"], "%Y-%m-%d").date()
            # Nächste monatliche Veröffentlichung schätzen (+30 Tage)
            next_date = last_date + timedelta(days=32)
            next_date = next_date.replace(day=min(last_date.day, 28))
            return next_date if next_date >= date.today() else None
        except Exception:
            return None

    @staticmethod
    def _estimate_monthly_events() -> List[MacroEvent]:
        """Schätzt monatliche Termine ohne API-Key."""
        today = date.today()
        events = []
        for month_offset in range(3):
            ref = today + timedelta(days=30 * month_offset)
            # CPI typisch 2. Woche des Monats
            events.append(MacroEvent("CPI (geschätzt)", ref.replace(day=12), "HIGH"))
            # NFP typisch 1. Freitag
            events.append(MacroEvent("NFP Jobs (geschätzt)", ref.replace(day=5), "HIGH"))
        return [e for e in events if e.release_date >= today]

    def _load_cache(self) -> Optional[List[MacroEvent]]:
        try:
            with open(_CACHE_FILE) as f:
                data = json.load(f)
            updated = datetime.fromisoformat(data["updated_at"])
            if (datetime.utcnow() - updated).total_seconds() > _CACHE_TTL_HOURS * 3600:
                return None
            return [
                MacroEvent(e["name"], date.fromisoformat(e["date"]), e["impact"])
                for e in data["events"]
            ]
        except Exception:
            return None

    def _save_cache(self, events: List[MacroEvent]):
        data = {
            "updated_at": datetime.utcnow().isoformat(),
            "events": [
                {"name": e.name, "date": e.release_date.isoformat(), "impact": e.impact}
                for e in events
            ],
        }
        with open(_CACHE_FILE, "w") as f:
            json.dump(data, f, indent=2)
