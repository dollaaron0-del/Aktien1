"""
Insider Trading Collector
Sammelt drei Arten von Insider-Signalen:

1. Congressional Trades (STOCK Act Meldungen):
   - Abgeordnete des US-Repräsentantenhauses → housestockwatcher.com (S3 JSON)
   - US-Senatoren → senatestockwatcher.com (S3 JSON)
   Hintergrund: Kongressmitglieder müssen Trades innerhalb von 45 Tagen melden.
   Käufe/Verkäufe von Politikern mit Zugang zu nicht-öffentlichen Infos gelten als
   starkes Signal.

2. Corporate Insider Trades (SEC Form 4):
   - openinsider.com bietet einen frei zugänglichen Screener
   - Trades von CEOs, CFOs, Directors und >10%-Aktionären
"""

import requests
import json
from datetime import datetime, timedelta
from typing import List, Dict


_HEADERS = {"User-Agent": "StockSentimentBot/1.0 (educational use)"}
_TIMEOUT = 15

# Public S3 JSON feeds – no API key required
_HOUSE_URL = (
    "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com"
    "/data/all_transactions.json"
)
_SENATE_URL = (
    "https://senate-stock-watcher-data.s3-us-east-2.amazonaws.com"
    "/aggregate/all_transactions.json"
)


class InsiderCollector:
    def __init__(self, lookback_days: int = 90):
        self.lookback_days = lookback_days
        self._cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).date()

    def collect(self, ticker: str) -> List[Dict]:
        """Returns a combined list of insider news items for the given ticker."""
        items: List[Dict] = []
        items += self._house_trades(ticker)
        items += self._senate_trades(ticker)
        items += self._openinsider_trades(ticker)
        return items

    # ── Congressional Trades: House ───────────────────────────────────────────

    def _house_trades(self, ticker: str) -> List[Dict]:
        try:
            resp = requests.get(_HOUSE_URL, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results = []
        for tx in data:
            if not self._ticker_matches(tx.get("ticker", ""), ticker):
                continue
            trade_date = self._parse_date(tx.get("transaction_date", ""))
            if trade_date and trade_date < self._cutoff:
                continue
            tx_type = tx.get("type", "").lower()
            amount = tx.get("amount", "")
            rep = tx.get("representative", "Unknown")
            party = tx.get("party", "")
            district = tx.get("district", "")
            action = "gekauft" if "purchase" in tx_type else ("verkauft" if "sale" in tx_type else tx_type)
            results.append({
                "source": "Congressional-House",
                "ticker": ticker,
                "title": f"Abgeordnete/r {rep} ({party}) hat {ticker} {action} – Betrag: {amount}",
                "text": (
                    f"US-Kongressmitglied {rep} ({party}, {district}) hat "
                    f"{ticker} am {trade_date} {action}. Betrag: {amount}. "
                    f"Meldung nach STOCK Act. "
                    f"Congressional Insider-Trade ist ein starkes Marktsignal da "
                    f"Abgeordnete oft Zugang zu nicht-öffentlichen Informationen haben."
                ),
                "published_at": str(trade_date) if trade_date else "",
                "url": "",
                "insider_type": "congressional_house",
                "action": action,
                "amount": amount,
                "person": rep,
                "party": party,
            })
        return results

    # ── Congressional Trades: Senate ──────────────────────────────────────────

    def _senate_trades(self, ticker: str) -> List[Dict]:
        try:
            resp = requests.get(_SENATE_URL, headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        results = []
        for senator in data:
            txs = senator.get("transactions", [])
            name = senator.get("senator", "Unknown Senator")
            party = senator.get("party", "")
            state = senator.get("state", "")
            for tx in txs:
                if not self._ticker_matches(tx.get("ticker", ""), ticker):
                    continue
                trade_date = self._parse_date(tx.get("transaction_date", ""))
                if trade_date and trade_date < self._cutoff:
                    continue
                tx_type = tx.get("type", "").lower()
                amount = tx.get("amount", "")
                action = "gekauft" if "purchase" in tx_type else ("verkauft" if "sale" in tx_type else tx_type)
                results.append({
                    "source": "Congressional-Senate",
                    "ticker": ticker,
                    "title": f"Senator {name} ({party}-{state}) hat {ticker} {action} – {amount}",
                    "text": (
                        f"US-Senator {name} ({party}, {state}) hat {ticker} am {trade_date} {action}. "
                        f"Betrag: {amount}. Meldung nach STOCK Act erforderlich. "
                        f"Senats-Insider-Trades sind besonders relevant bei Branchen-Ausschüssen."
                    ),
                    "published_at": str(trade_date) if trade_date else "",
                    "url": "",
                    "insider_type": "congressional_senate",
                    "action": action,
                    "amount": amount,
                    "person": name,
                    "party": party,
                })
        return results

    # ── Corporate Insider Trades: OpenInsider (SEC Form 4) ───────────────────

    def _openinsider_trades(self, ticker: str) -> List[Dict]:
        """
        Fetches recent Form 4 filings from openinsider.com (CSV export).
        Only returns purchases (P) and sales (S) by insiders with significant roles.
        """
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

        # OpenInsider returns HTML – parse the table rows
        results = []
        try:
            from html.parser import HTMLParser

            class _TableParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.rows: List[List[str]] = []
                    self._in_td = False
                    self._current_row: List[str] = []
                    self._in_tr = False
                    self._cell_text = ""

                def handle_starttag(self, tag, attrs):
                    if tag == "tr":
                        self._in_tr = True
                        self._current_row = []
                    elif tag == "td":
                        self._in_td = True
                        self._cell_text = ""

                def handle_endtag(self, tag):
                    if tag == "td" and self._in_td:
                        self._current_row.append(self._cell_text.strip())
                        self._in_td = False
                    elif tag == "tr" and self._current_row:
                        self.rows.append(self._current_row)
                        self._current_row = []

                def handle_data(self, data):
                    if self._in_td:
                        self._cell_text += data

            parser = _TableParser()
            parser.feed(resp.text)

            # OpenInsider columns (approx): Filing Date, Trade Date, Ticker, Company,
            # Insider Name, Title, Trade Type, Price, Qty, Owned, ΔOwn, Value
            for row in parser.rows:
                if len(row) < 12:
                    continue
                trade_date_raw = row[1] if len(row) > 1 else ""
                insider_name = row[4] if len(row) > 4 else ""
                title = row[5] if len(row) > 5 else ""
                trade_type = row[6] if len(row) > 6 else ""
                price = row[7] if len(row) > 7 else ""
                qty = row[8] if len(row) > 8 else ""
                value = row[11] if len(row) > 11 else ""

                if not insider_name or not trade_type:
                    continue
                trade_type_clean = trade_type.strip().upper()
                if trade_type_clean not in ("P", "S"):
                    continue

                action = "gekauft (P)" if trade_type_clean == "P" else "verkauft (S)"
                signal = "BULLISH" if trade_type_clean == "P" else "BEARISH"
                trade_date = self._parse_date(trade_date_raw)
                if trade_date and trade_date < self._cutoff:
                    continue

                results.append({
                    "source": "SEC-Form4-Insider",
                    "ticker": ticker,
                    "title": f"Insider {insider_name} ({title}) hat {ticker} {action} – Wert: {value}",
                    "text": (
                        f"Unternehmensinsider {insider_name} (Rolle: {title}) hat {ticker} am "
                        f"{trade_date} {action}. Preis: {price}, Menge: {qty}, Gesamtwert: {value}. "
                        f"SEC Form-4-Meldung. Signal: {signal}. "
                        f"Insider-Käufe durch Führungskräfte gelten als starkes bullisches Signal, "
                        f"da sie eigenes Kapital einsetzen."
                    ),
                    "published_at": str(trade_date) if trade_date else "",
                    "url": f"https://openinsider.com/search?q={ticker}",
                    "insider_type": "corporate_form4",
                    "action": action,
                    "person": insider_name,
                    "role": title,
                    "value": value,
                })
        except Exception:
            pass

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
