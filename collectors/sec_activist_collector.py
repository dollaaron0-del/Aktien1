"""
collectors/sec_activist_collector.py – Aktivisten-/Großbeteiligungen (SC 13D/G).

SC 13D/13G werden eingereicht, wenn ein Investor >5% an einer Firma hält. 13D
(aktive Absicht) ist ein klassischer Aktivisten-/M&A-Katalysator; 13G (passiv)
zeigt große neue Anker-Investoren. Beides erscheint direkt bei der SEC.

Quelle: SEC EDGAR Submissions-API (frei, kein Key, aber Pflicht-User-Agent via
SEC_CONTACT_EMAIL). Wir lösen Ticker→CIK über company_tickers.json und filtern
die jüngsten Filings auf 13D/13G(/A). Fail-safe → [].
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from logger import get_logger
from system.http import http_get, sec_user_agent

log = get_logger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_FORMS = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
_CACHE_TTL_S = 12 * 3600


class SECActivistCollector:
    def __init__(self, lookback_days: int = 21):
        self.lookback_days = lookback_days
        self._cik_cache: Optional[Dict[str, int]] = None
        self._cache: Dict[str, tuple] = {}

    @property
    def _headers(self) -> Dict[str, str]:
        return {"User-Agent": sec_user_agent()}

    def collect(self, ticker: str) -> List[Dict]:
        cached = self._cache.get(ticker)
        if cached and (time.time() - cached[1]) < _CACHE_TTL_S:
            return cached[0]
        try:
            cik = self._get_cik(ticker)
            items = self._fetch(ticker, cik) if cik else []
        except Exception as e:
            log.debug("[%s] SEC-13D/G: %s", ticker, e)
            items = []
        self._cache[ticker] = (items, time.time())
        return items

    def _get_cik(self, ticker: str) -> Optional[int]:
        if self._cik_cache is None:
            try:
                resp = http_get(_TICKERS_URL, headers=self._headers, timeout=15)
                resp.raise_for_status()
                data = resp.json()
                self._cik_cache = {
                    e["ticker"].upper(): int(e["cik_str"])
                    for e in data.values() if "ticker" in e and "cik_str" in e
                }
            except Exception:
                self._cik_cache = {}
        return self._cik_cache.get(ticker.upper())

    def _fetch(self, ticker: str, cik: int) -> List[Dict]:
        resp = http_get(_SUBMISSIONS_URL.format(cik=cik), headers=self._headers, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json() or {}
        name = data.get("name", ticker)
        recent = (data.get("filings", {}) or {}).get("recent", {}) or {}
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accs = recent.get("accessionNumber", [])
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=self.lookback_days)).date()

        out: List[Dict] = []
        for i, form in enumerate(forms):
            if form not in _FORMS:
                continue
            fd = self._parse_date(dates[i] if i < len(dates) else "")
            if fd is None or fd < cutoff:
                continue
            acc = accs[i] if i < len(accs) else ""
            is_active = form.startswith("SC 13D")
            kind = "Aktivisten-/aktive Beteiligung (13D)" if is_active else "Großbeteiligung (13G)"
            url = (f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                   f"&CIK={cik}&type=SC+13&dateb=&owner=include&count=20")
            if acc:
                a = acc.replace("-", "")
                url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{a}/{acc}-index.htm"
            out.append({
                "source": "SEC-13D/G",
                "ticker": ticker,
                "title": f"SEC {form}: {kind} an {name} – {fd}",
                "text": (f"{form}-Einreichung bei der SEC (Stichtag {fd}). {kind}. "
                         f"13D signalisiert oft Aktivismus oder M&A-Absicht und kann "
                         f"den Kurs deutlich bewegen; 13G zeigt einen großen passiven "
                         f"Anker-Investor."),
                "url": url,
                "published_at": str(fd),
                "priority": "HIGH" if is_active else "NORMAL",
            })
        return out

    @staticmethod
    def _parse_date(s: str):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
