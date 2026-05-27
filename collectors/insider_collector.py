"""
Insider Trading Collector
Sammelt vier Arten von Insider-Signalen:

1. Congressional Trades (STOCK Act): Repräsentantenhaus + Senat
   (housestockwatcher.com / senatestockwatcher.com S3 JSON)
   → Große JSON-Dateien werden 4h lokal gecacht.

2. Corporate Insider Trades (SEC Form 4):
   openinsider.com – CEOs, CFOs, Directors, >10%-Aktionäre

3. SEC Form 144 – Geplante Insider-Verkäufe:
   Ankündigung bis zu 90 Tage vor dem eigentlichen Verkauf.
   Frühwarnsignal: Insider plant Ausstieg.
"""
from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests

_HEADERS  = {"User-Agent": "StockSentimentBot/1.0 (educational use)"}
_TIMEOUT  = 15
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
_CACHE_TTL = timedelta(hours=4)

_HOUSE_URL  = (
    "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com"
    "/data/all_transactions.json"
)
_SENATE_URL = (
    "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com"
    "/aggregate/all_transactions.json"
)


def _cached_get(cache_key: str, url: str) -> Optional[object]:
    """Fetch JSON from URL with 4h disk cache. Returns None on failure."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = os.path.join(_CACHE_DIR, f"{cache_key}.json")
    try:
        with open(path) as f:
            obj = json.load(f)
        age = datetime.utcnow() - datetime.fromisoformat(obj["fetched_at"])
        if age < _CACHE_TTL:
            return obj["data"]
    except Exception:
        pass
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    obj = {"fetched_at": datetime.utcnow().isoformat(), "data": data}
    with tempfile.NamedTemporaryFile(
        mode="w", dir=_CACHE_DIR, suffix=".tmp", delete=False
    ) as tmp:
        json.dump(obj, tmp)
        tmp_path = tmp.name
    os.replace(tmp_path, path)
    return data


class InsiderCollector:
    def __init__(self, lookback_days: int = 90):
        self.lookback_days = lookback_days
        self._cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).date()

    def collect(self, ticker: str) -> List[Dict]:
        items: List[Dict] = []
        items += self._house_trades(ticker)
        items += self._senate_trades(ticker)
        items += self._openinsider_trades(ticker)
        items += self._form144_trades(ticker)
        return items

    # ── Congressional Trades: House ───────────────────────────────────────────

    def _house_trades(self, ticker: str) -> List[Dict]:
        data = _cached_get("congress_house", _HOUSE_URL)
        if not data:
            return []
        results = []
        for tx in data:
            if not self._ticker_matches(tx.get("ticker", ""), ticker):
                continue
            trade_date = self._parse_date(tx.get("transaction_date", ""))
            if trade_date and trade_date < self._cutoff:
                continue
            tx_type = tx.get("type", "").lower()
            amount  = tx.get("amount", "")
            rep     = tx.get("representative", "Unknown")
            party   = tx.get("party", "")
            district = tx.get("district", "")
            action  = "gekauft" if "purchase" in tx_type else ("verkauft" if "sale" in tx_type else tx_type)
            results.append({
                "source":       "Congressional-House",
                "ticker":       ticker,
                "title":        f"Abgeordnete/r {rep} ({party}) hat {ticker} {action} – {amount}",
                "text": (
                    f"US-Kongressmitglied {rep} ({party}, {district}) hat {ticker} "
                    f"am {trade_date} {action}. Betrag: {amount}. "
                    f"STOCK-Act-Meldung. Congressional-Insider-Trade ist starkes Signal "
                    f"da Abgeordnete oft Zugang zu nicht-öffentlichen Infos haben."
                ),
                "published_at": str(trade_date) if trade_date else "",
                "url":          "",
                "insider_type": "congressional_house",
                "action":       action,
                "amount":       amount,
                "person":       rep,
                "party":        party,
            })
        return results

    # ── Congressional Trades: Senate ──────────────────────────────────────────

    def _senate_trades(self, ticker: str) -> List[Dict]:
        data = _cached_get("congress_senate", _SENATE_URL)
        if not data:
            return []
        results = []
        for senator in data:
            txs   = senator.get("transactions", [])
            name  = senator.get("senator", "Unknown Senator")
            party = senator.get("party", "")
            state = senator.get("state", "")
            for tx in txs:
                if not self._ticker_matches(tx.get("ticker", ""), ticker):
                    continue
                trade_date = self._parse_date(tx.get("transaction_date", ""))
                if trade_date and trade_date < self._cutoff:
                    continue
                tx_type = tx.get("type", "").lower()
                amount  = tx.get("amount", "")
                action  = "gekauft" if "purchase" in tx_type else ("verkauft" if "sale" in tx_type else tx_type)
                results.append({
                    "source":       "Congressional-Senate",
                    "ticker":       ticker,
                    "title":        f"Senator {name} ({party}-{state}) hat {ticker} {action} – {amount}",
                    "text": (
                        f"US-Senator {name} ({party}, {state}) hat {ticker} am {trade_date} {action}. "
                        f"Betrag: {amount}. STOCK-Act-Meldung. "
                        f"Senats-Trades sind besonders relevant bei Branchen-Ausschüssen."
                    ),
                    "published_at": str(trade_date) if trade_date else "",
                    "url":          "",
                    "insider_type": "congressional_senate",
                    "action":       action,
                    "amount":       amount,
                    "person":       name,
                    "party":        party,
                })
        return results

    # ── Corporate Insider Trades: OpenInsider (SEC Form 4) ───────────────────

    def _openinsider_trades(self, ticker: str) -> List[Dict]:
        url = (
            f"http://openinsider.com/screener?s={ticker}&fd=-1&fdr=&td=0&tdr=&"
            f"fdlyl=&fdlyh=&daysago={self.lookback_days}&xp=1&xs=1&"
            f"vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&"
            f"grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&oc2l=&oc2h=&"
            f"sortcol=0&cnt=20&page=1&action=1"
        )
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
        except Exception:
            return []

        results = []
        try:
            from html.parser import HTMLParser

            class _TableParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.rows: List[List[str]] = []
                    self._in_td   = False
                    self._cur_row: List[str] = []
                    self._cell    = ""

                def handle_starttag(self, tag, attrs):
                    if tag == "tr":
                        self._cur_row = []
                    elif tag == "td":
                        self._in_td = True
                        self._cell  = ""

                def handle_endtag(self, tag):
                    if tag == "td" and self._in_td:
                        self._cur_row.append(self._cell.strip())
                        self._in_td = False
                    elif tag == "tr" and self._cur_row:
                        self.rows.append(self._cur_row)
                        self._cur_row = []

                def handle_data(self, data):
                    if self._in_td:
                        self._cell += data

            parser = _TableParser()
            parser.feed(resp.text)

            for row in parser.rows:
                if len(row) < 12:
                    continue
                insider_name   = row[4] if len(row) > 4 else ""
                title_role     = row[5] if len(row) > 5 else ""
                trade_type     = row[6] if len(row) > 6 else ""
                price          = row[7] if len(row) > 7 else ""
                qty            = row[8] if len(row) > 8 else ""
                value          = row[11] if len(row) > 11 else ""
                trade_date_raw = row[1] if len(row) > 1 else ""

                if not insider_name or not trade_type:
                    continue
                tt = trade_type.strip().upper()
                if tt not in ("P", "S"):
                    continue

                action = "gekauft (P)" if tt == "P" else "verkauft (S)"
                signal = "BULLISH" if tt == "P" else "BEARISH"
                trade_date = self._parse_date(trade_date_raw)
                if trade_date and trade_date < self._cutoff:
                    continue

                results.append({
                    "source":       "SEC-Form4-Insider",
                    "ticker":       ticker,
                    "title":        f"Insider {insider_name} ({title_role}) hat {ticker} {action} – {value}",
                    "text": (
                        f"Unternehmensinsider {insider_name} (Rolle: {title_role}) hat {ticker} "
                        f"am {trade_date} {action}. Preis: {price}, Menge: {qty}, Wert: {value}. "
                        f"SEC Form-4-Meldung. Signal: {signal}. "
                        f"Insider-Käufe durch Führungskräfte sind starkes bullisches Signal."
                    ),
                    "published_at": str(trade_date) if trade_date else "",
                    "url":          f"https://openinsider.com/search?q={ticker}",
                    "insider_type": "corporate_form4",
                    "action":       action,
                    "person":       insider_name,
                    "role":         title_role,
                    "value":        value,
                })
        except Exception:
            pass
        return results

    # ── SEC Form 144 – Geplante Insider-Verkäufe ─────────────────────────────

    def _form144_trades(self, ticker: str) -> List[Dict]:
        """
        Form 144 = Vorankündigung eines Insider-Verkaufs (bis zu 90 Tage vorher).
        Frühwarnsignal: Insider plant Ausstieg, bevor es Form 4 sichtbar wird.
        """
        start = (datetime.utcnow() - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d")
        query = urllib.parse.quote(f'"{ticker}"')
        url   = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?q={query}&forms=144&dateRange=custom&startdt={start}"
        )
        try:
            resp = requests.get(
                url, headers={**_HEADERS, "Accept": "application/json"}, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])
        except Exception:
            return []

        results = []
        for hit in hits[:8]:
            src       = hit.get("_source", {})
            person    = src.get("entity_name", "Unbekannt")
            file_date = src.get("file_date", "")
            period    = src.get("period_of_report", "")
            trade_date = self._parse_date(period or file_date)
            if trade_date and trade_date < self._cutoff:
                continue
            results.append({
                "source":       "SEC-Form144",
                "ticker":       ticker,
                "title":        f"⚠️ Form 144: {person} plant {ticker}-Verkauf (Ankündigung {file_date})",
                "text": (
                    f"SEC Form 144 – Geplanter Insider-Verkauf: {person} hat eine "
                    f"Verkaufsabsicht für {ticker} am {file_date} angekündigt. "
                    f"Form 144 ist eine Vorankündigung – Verkauf innerhalb von 90 Tagen möglich. "
                    f"BEARISCHES Frühwarnsignal: Insider erwartet fallenden Kurs oder will diversifizieren."
                ),
                "published_at": file_date,
                "url":          f"https://efts.sec.gov/LATEST/search-index?q={query}&forms=144",
                "insider_type": "form_144_planned_sale",
                "person":       person,
            })
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _ticker_matches(raw: str, ticker: str) -> bool:
        return raw.strip().upper() == ticker.strip().upper()

    @staticmethod
    def _parse_date(raw: str):
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(raw.strip()[:10], fmt).date()
            except ValueError:
                continue
        return None
