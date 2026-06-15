"""
EconomicCalendarCollector – upcoming macro events as trading context.

Sources (all keyless):
  - FOMC meeting dates (hardcoded from Fed's published annual calendar)
  - BLS CPI/PPI/NFP release dates (scraped from bls.gov schedule page)

Returns events within the next 14 days as news items relevant to all tickers.
Results are cached for 24h to avoid hammering public endpoints.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
from system.http import http_get

from logger import get_logger

log = get_logger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockBot/1.0; educational)"}
_CACHE: Optional[Dict] = None
_CACHE_TS: float = 0.0
_CACHE_TTL = 86_400  # 24h

# ── FOMC meeting dates (decision day = second day of two-day meeting) ─────────
_FOMC_DATES = [
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-10",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]


def _fetch_bls_events(lookahead_days: int = 14) -> List[Dict]:
    """Scrape upcoming CPI/PPI/NFP release dates from bls.gov."""
    events = []
    today = datetime.utcnow().date()
    cutoff = today + timedelta(days=lookahead_days)
    year = today.year

    # BLS publishes a simple text schedule per release
    release_urls = {
        "CPI":  f"https://www.bls.gov/schedule/news_release/cpi.htm",
        "PPI":  f"https://www.bls.gov/schedule/news_release/ppi.htm",
        "NFP":  f"https://www.bls.gov/schedule/news_release/empsit.htm",
    }
    # Regex: find dates like "Wednesday, June 11, 2025"
    _date_re = re.compile(
        r"\b(?:Monday|Tuesday|Wednesday|Thursday|Friday),\s+"
        r"(\w+ \d{1,2},\s+\d{4})"
    )
    for label, url in release_urls.items():
        try:
            resp = http_get(url, headers=_HEADERS, timeout=10)
            if resp.status_code != 200:
                continue
            for m in _date_re.finditer(resp.text):
                try:
                    dt = datetime.strptime(m.group(1).strip(), "%B %d, %Y").date()
                except ValueError:
                    continue
                if today <= dt <= cutoff:
                    days_left = (dt - today).days
                    events.append({
                        "source":       "Economic Calendar (BLS)",
                        "ticker":       "__MACRO__",
                        "title":        f"⚠️ {label} Release in {days_left} Tag(e) ({dt.isoformat()})",
                        "text":         (
                            f"Makro-Ereignis: US {label}-Veröffentlichung am {dt.isoformat()}. "
                            f"{'CPI = Inflationsdaten – wichtig für Fed-Zinsentscheid.' if label == 'CPI' else ''}"
                            f"{'PPI = Erzeugerpreisindex – Vorbote für CPI.' if label == 'PPI' else ''}"
                            f"{'NFP = Non-Farm Payrolls – wichtigster US-Arbeitsmarktbericht.' if label == 'NFP' else ''}"
                        ),
                        "score":        90,
                        "url":          url,
                        "published_at": datetime.utcnow().isoformat(),
                        "event_date":   dt.isoformat(),
                        "event_type":   label,
                        "days_until":   days_left,
                    })
        except Exception as e:
            log.debug("BLS fetch %s: %s", label, e)
    return events


def _fomc_events(lookahead_days: int = 14) -> List[Dict]:
    today = datetime.utcnow().date()
    cutoff = today + timedelta(days=lookahead_days)
    events = []
    for ds in _FOMC_DATES:
        try:
            dt = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        if today <= dt <= cutoff:
            days_left = (dt - today).days
            events.append({
                "source":       "Economic Calendar (Fed)",
                "ticker":       "__MACRO__",
                "title":        f"🏦 FOMC-Zinsentscheid in {days_left} Tag(e) ({dt.isoformat()})",
                "text":         (
                    f"FOMC-Meeting: Fed-Zinsentscheid am {dt.isoformat()}. "
                    "Märkte reagieren stark auf überraschende Zinsschritte oder Guidance-Änderungen. "
                    "Erhöhte Volatilität in den Tagen davor und danach erwartet."
                ),
                "score":        95,
                "url":          "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                "published_at": datetime.utcnow().isoformat(),
                "event_date":   dt.isoformat(),
                "event_type":   "FOMC",
                "days_until":   days_left,
            })
    return events


class EconomicCalendarCollector:
    """Returns upcoming macro events (FOMC, CPI, PPI, NFP) as news items."""

    def __init__(self, lookahead_days: int = 14):
        self.lookahead = lookahead_days

    def _get_events(self) -> List[Dict]:
        global _CACHE, _CACHE_TS
        if _CACHE is not None and (time.time() - _CACHE_TS) < _CACHE_TTL:
            return _CACHE
        events = _fomc_events(self.lookahead) + _fetch_bls_events(self.lookahead)
        events.sort(key=lambda x: x["days_until"])
        _CACHE = events
        _CACHE_TS = time.time()
        return events

    def collect(self, ticker: str) -> List[Dict]:
        """Returns macro events relevant to all tickers."""
        events = self._get_events()
        # Tag each event with the current ticker for downstream processing
        return [{**e, "ticker": ticker} for e in events]
