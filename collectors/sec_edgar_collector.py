"""
SEC EDGAR Collector – 8-K Current Reports (Material Events)

Warum SEC EDGAR eine Frühquelle ist:
  Unternehmen müssen wesentliche Ereignisse innerhalb von 4 Geschäftstagen
  als 8-K bei der SEC einreichen – BEVOR sie in Nachrichtenportalen erscheinen.
  Ein 8-K über ein neues Übernahme-Agreement oder einen CEO-Wechsel ist oft
  2–24 Stunden früher als Yahoo Finance, Reuters oder Bloomberg.

8-K Item-Codes (materiell für Swing-Trading):
  1.01 – Material Definitive Agreement (Deals, Kooperationen)
  1.02 – Termination of Material Agreement (Vertrag beendet)
  2.01 – Completion of Acquisition or Disposition (Übernahme abgeschlossen)
  2.02 – Results of Operations (Earnings-Vorab)
  2.05 – Costs / Exit Activities (Restrukturierung)
  3.01 – Notice of Delisting
  5.02 – Departure/Election of Directors or Officers (CEO/CFO-Wechsel)
  5.03 – Amendments to Articles (Satzungsänderung)
  7.01 – Regulation FD Disclosure (freiwillige Investor-Mitteilung)
  8.01 – Other Events (sonstige Wesentlichkeit)
  9.01 – Financial Statements (bei Übernahmen)

API: https://data.sec.gov – keine Registrierung, kein API-Key.
"""

import requests
from system.http import http_get, sec_user_agent
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

_HEADERS = {
    "User-Agent": sec_user_agent(),
    "Accept": "application/json",
}
_TIMEOUT = 15

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Hardcoded CIKs for default watchlist + most traded US stocks
# Used as instant fallback when sec.gov is unreachable
_BUILTIN_CIKS: Dict[str, int] = {
    "AAPL": 320193,   "MSFT": 789019,   "NVDA": 1045810,  "TSLA": 1318605,
    "AMZN": 1018724,  "GOOGL": 1652044, "GOOG": 1652044,  "META": 1326801,
    "NFLX": 1065280,  "AMD":   2488,    "INTC": 50863,    "QCOM": 804328,
    "CRM":  1108524,  "ADBE":  796343,  "ORCL": 1341439,  "IBM":  51143,
    "CSCO": 858877,   "TXN":   97476,   "AVGO": 1730168,  "ASML": 937966,
    "JPM":  19617,    "BAC":   70858,   "GS":   886982,   "MS":   895421,
    "WMT":  104169,   "COST":  909832,  "TGT":  27419,    "AMGN": 318154,
    "LLY":  59478,    "JNJ":   200406,  "PFE":  78003,    "MRK":  310158,
    "BA":   12927,    "CAT":   18230,   "GE":   40987,    "F":    37996,
    "GM":   1467858,  "RIVN":  1874178, "NIO":  1736541,  "UBER": 1543151,
    "LYFT": 1759509,  "ABNB":  1559720, "SNOW": 1640147,  "PLTR": 1321655,
    "COIN": 1679788,  "HOOD":  1783398, "SOFI": 1818874,
}

# 8-K item codes with human-readable labels and signal direction
_ITEM_LABELS = {
    "1.01": ("Material Definitive Agreement",     "BULLISH"),
    "1.02": ("Termination of Material Agreement", "BEARISH"),
    "1.03": ("Bankruptcy or Receivership",        "BEARISH"),
    "2.01": ("Acquisition/Disposition Complete",  "BULLISH"),
    "2.02": ("Results of Operations",             "NEUTRAL"),
    "2.03": ("Direct Financial Obligation",       "BEARISH"),
    "2.04": ("Triggering Events / Defaults",      "BEARISH"),
    "2.05": ("Costs / Exit Activities",           "BEARISH"),
    "2.06": ("Material Impairment",               "BEARISH"),
    "3.01": ("Notice of Delisting",               "BEARISH"),
    "3.02": ("Unregistered Sales of Equity",      "BEARISH"),
    "4.01": ("Changes in Registrant's Certifier", "NEUTRAL"),
    "5.01": ("Changes in Control",                "BULLISH"),
    "5.02": ("Officers / Directors Change",       "NEUTRAL"),
    "5.03": ("Amendments to Articles",            "NEUTRAL"),
    "5.04": ("Temporary Suspension of Trading",   "BEARISH"),
    "7.01": ("Regulation FD Disclosure",          "NEUTRAL"),
    "8.01": ("Other Events",                      "NEUTRAL"),
    "9.01": ("Financial Statements (Acquisition)","BULLISH"),
}

# Only alert on these high-signal items
_HIGH_SIGNAL_ITEMS = {"1.01", "1.02", "1.03", "2.01", "2.02", "2.05", "3.01", "5.01", "5.02"}


class SECEdgarCollector:
    """
    Fetches recent 8-K filings for a ticker directly from SEC EDGAR.
    Returns filings as news items, typically hours before media coverage.
    """

    def __init__(self, lookback_days: int = 30, all_items: bool = False):
        self.lookback_days = lookback_days
        self.all_items = all_items  # if False, only high-signal items
        self._cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).date()
        self._cik_cache: Optional[Dict[str, int]] = None

    def collect(self, ticker: str) -> List[Dict]:
        cik = self._get_cik(ticker)
        if not cik:
            return []
        return self._fetch_8k_filings(ticker, cik)

    # ── CIK lookup ────────────────────────────────────────────────────────────

    def _get_cik(self, ticker: str) -> Optional[int]:
        t = ticker.upper()
        # 1. Immediate return from builtin map (no network needed)
        if t in _BUILTIN_CIKS:
            return _BUILTIN_CIKS[t]
        # 2. Try live SEC tickers file (full ~10k ticker list)
        if self._cik_cache is None:
            self._cik_cache = self._load_cik_map()
        return self._cik_cache.get(t)

    def _load_cik_map(self) -> Dict[str, int]:
        try:
            resp = http_get(_TICKERS_URL, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            # Format: {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, ...}
            return {
                entry["ticker"].upper(): int(entry["cik_str"])
                for entry in data.values()
                if "ticker" in entry and "cik_str" in entry
            }
        except Exception:
            return {}

    # ── Submissions API ───────────────────────────────────────────────────────

    def _fetch_8k_filings(self, ticker: str, cik: int) -> List[Dict]:
        try:
            resp = http_get(
                _SUBMISSIONS_URL.format(cik=cik),
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        company_name = data.get("name", ticker)
        recent = data.get("filings", {}).get("recent", {})

        forms         = recent.get("form", [])
        dates         = recent.get("filingDate", [])
        items_list    = recent.get("items", [])
        accessions    = recent.get("accessionNumber", [])
        descriptions  = recent.get("primaryDocDescription", [])

        results = []
        for i, form in enumerate(forms):
            if form != "8-K":
                continue

            filing_date = self._parse_date(dates[i] if i < len(dates) else "")
            if not filing_date or filing_date < self._cutoff:
                # Filings are newest-first; once we're past the cutoff we can stop
                if filing_date and filing_date < self._cutoff:
                    break
                continue

            items_raw   = items_list[i] if i < len(items_list) else ""
            accession   = accessions[i] if i < len(accessions) else ""
            description = descriptions[i] if i < len(descriptions) else ""

            parsed_items = self._parse_items(items_raw)
            if not parsed_items:
                continue

            # Filter to high-signal items unless all_items=True
            relevant = (
                parsed_items if self.all_items
                else [it for it in parsed_items if it in _HIGH_SIGNAL_ITEMS]
            )
            if not relevant:
                continue

            item_str = ", ".join(relevant)
            item_labels = [_ITEM_LABELS.get(it, (it, "NEUTRAL")) for it in relevant]
            item_desc = " | ".join(f"Item {it}: {label}" for it, (label, _) in zip(relevant, item_labels))

            # Overall signal: BEARISH beats BULLISH (conservative)
            signals = [sig for _, sig in item_labels]
            if "BEARISH" in signals:
                overall_signal = "BEARISH"
            elif "BULLISH" in signals:
                overall_signal = "BULLISH"
            else:
                overall_signal = "NEUTRAL"

            filing_url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                f"&CIK={cik}&type=8-K&dateb=&owner=include&count=10"
            )
            if accession:
                acc_clean = accession.replace("-", "")
                filing_url = (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}"
                    f"/{acc_clean}/{accession}-index.htm"
                )

            results.append({
                "source": "SEC-EDGAR-8K",
                "ticker": ticker,
                "title": (
                    f"SEC 8-K: {company_name} meldet {item_desc[:80]} "
                    f"– {filing_date}"
                ),
                "text": (
                    f"SEC-Pflichtmeldung (8-K) von {company_name}. "
                    f"Gemeldet am: {filing_date}. "
                    f"Items: {item_desc}. "
                    f"Beschreibung: {description or 'Keine Kurzbeschreibung verfügbar'}. "
                    f"Signal: {overall_signal}. "
                    f"8-K-Filings erscheinen direkt bei der SEC – oft Stunden vor "
                    f"Nachrichtenportalen. Item 1.01 (Deals) und 2.01 (Übernahme) "
                    f"sind besonders starke Kursimpulse."
                ),
                "published_at": str(filing_date),
                "url": filing_url,
                "sec_items": item_str,
                "overall_signal": overall_signal,
                "company_name": company_name,
            })

        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_items(raw: str) -> List[str]:
        """Parses '1.01,5.02' or '1.01 5.02' into a list of item codes."""
        if not raw:
            return []
        import re
        return re.findall(r"\d+\.\d+", raw)

    @staticmethod
    def _parse_date(raw: str):
        if not raw:
            return None
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
