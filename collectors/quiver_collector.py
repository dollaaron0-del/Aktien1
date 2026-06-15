"""
Quiver Quantitative Collector – Congressional Trades mit Ausschuss-Kontext
und Regierungsaufträge pro Ticker.

Warum Quiver besser als rohe STOCK-Act-Daten ist:
  Die freien S3-Feeds von housestockwatcher.com enthalten KEINE Ausschuss-Info.
  Quiver liefert z.B. "Committees: ['Armed Services', 'Intelligence']" →
  Ein Mitglied des Verteidigungsausschusses kauft Rheinmetall = extrem starkes Signal.
  Ein zufälliges Mitglied kauft das gleiche = mittleres Signal.

Außerdem:
  • govcontract: Welche Unternehmen bekommen gerade Bundesaufträge?
    Großer Regierungsauftrag ist direkter Umsatztreiber.
  • lobbying: Wer lobbyiert für ein Unternehmen, und wie viel?

API: https://api.quiverquant.com  – kostenloser Tier (500 Req/Tag)
Key:  QUIVER_API_KEY in .env  (kostenlos auf quiverquant.com registrieren)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Dict, Optional

import requests
from system.http import http_get

from config import config

_TIMEOUT = 12
_BASE    = "https://api.quiverquant.com/beta"
_HEADERS_BASE = {"User-Agent": "StockSentimentBot/1.0 (educational use)"}

# Ausschüsse die besonders relevante Signale liefern
_HIGH_SIGNAL_COMMITTEES = {
    "armed services":       "Verteidigung",
    "intelligence":         "Geheimdienste",
    "financial services":   "Finanzen",
    "banking":              "Banken",
    "energy":               "Energie",
    "commerce":             "Handel/Tech",
    "health":               "Gesundheit",
    "judiciary":            "Justiz/Big Tech",
    "foreign affairs":      "Außenpolitik",
    "appropriations":       "Haushalt",
}


class QuiverCollector:
    """
    Congressional Trades (mit Ausschuss-Kontext) + Regierungsaufträge via
    Quiver Quantitative API. Erfordert kostenlosen API-Key (QUIVER_API_KEY).
    """

    def __init__(self, lookback_days: int = 90):
        self.lookback_days = lookback_days
        self._cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).date()

    @property
    def available(self) -> bool:
        return bool(config.quiver_api_key)

    def collect(self, ticker: str) -> List[Dict]:
        if not self.available:
            return []
        items: List[Dict] = []
        items += self._congress_trades(ticker)
        items += self._gov_contracts(ticker)
        return items

    # ── Congressional Trades mit Ausschuss-Info ───────────────────────────────

    def _congress_trades(self, ticker: str) -> List[Dict]:
        url  = f"{_BASE}/historical/congresstrading/{ticker}"
        data = self._get(url)
        if not data:
            return []

        results = []
        for tx in data:
            trade_date = self._parse_date(tx.get("Date", ""))
            if trade_date and trade_date < self._cutoff:
                continue

            rep        = tx.get("Representative", "Unbekannt")
            party      = tx.get("Party", "")
            state      = tx.get("State", "")
            house      = tx.get("House", "")     # "House" | "Senate"
            tx_type    = tx.get("Transaction", "").lower()
            amount     = tx.get("Amount", "")
            committees = tx.get("Committees", []) or []

            action = "gekauft" if "purchase" in tx_type else ("verkauft" if "sale" in tx_type else tx_type)

            # Ausschuss-Kontext berechnen
            committee_str = ", ".join(committees) if committees else "—"
            signal_boost  = self._committee_signal(committees, ticker)

            title = (
                f"{'🏛️' if signal_boost else '📋'} Quiver: {rep} ({party}-{state}, {house}) "
                f"hat {ticker} {action} – {amount}"
            )
            text = (
                f"Congressional Trade (Quiver): {rep} ({party}, {state}, {house}) "
                f"hat {ticker} am {trade_date} {action}. Betrag: {amount}. "
                f"Ausschüsse: {committee_str}. "
            )
            if signal_boost:
                text += (
                    f"⚡ HOHES SIGNAL: Mitglied im Ausschuss {signal_boost} – "
                    f"direkter Zugang zu Branchen-Informationen vor Öffentlichkeit. "
                )
            text += (
                f"Quelle: Quiver Quantitative (STOCK Act). "
                f"Congressional Insider-Trades mit Ausschuss-Bezug gelten als "
                f"stärkstes legales Marktsignal."
            )

            results.append({
                "source":       f"Quiver-Congress-{house}",
                "ticker":       ticker,
                "title":        title,
                "text":         text,
                "published_at": str(trade_date) if trade_date else "",
                "url":          f"https://www.quiverquant.com/congresstrading/",
                "insider_type": "quiver_congressional",
                "action":       action,
                "amount":       amount,
                "person":       rep,
                "party":        party,
                "committees":   committees,
                "signal_boost": bool(signal_boost),
            })
        return results

    # ── Regierungsaufträge (Gov Contracts) ────────────────────────────────────

    def _gov_contracts(self, ticker: str) -> List[Dict]:
        url  = f"{_BASE}/historical/govcontract/{ticker}"
        data = self._get(url)
        if not data:
            return []

        results = []
        for contract in data[:10]:
            date_raw   = contract.get("Date") or contract.get("ReportDate") or ""
            trade_date = self._parse_date(date_raw)
            if trade_date and trade_date < self._cutoff:
                continue

            amount     = contract.get("Amount", 0)
            agency     = contract.get("Agency", "Unbekannte Behörde")
            desc       = (contract.get("Description") or "")[:200]
            amount_fmt = f"${amount:,.0f}" if isinstance(amount, (int, float)) else str(amount)

            results.append({
                "source":       "Quiver-GovContract",
                "ticker":       ticker,
                "title":        f"🏛️ Regierungsauftrag {ticker}: {amount_fmt} von {agency}",
                "text": (
                    f"US-Regierungsauftrag für {ticker}: {amount_fmt} von {agency}. "
                    f"Beschreibung: {desc}. Datum: {date_raw}. "
                    f"Direkt umsatzwirksam – Regierungsaufträge sind gesicherte Einnahmen "
                    f"und oft frühzeitiger Indikator für Unternehmenswachstum. "
                    f"Quelle: Quiver Quantitative (USASpending.gov)."
                ),
                "published_at": date_raw,
                "url":          f"https://www.quiverquant.com/govcontract/{ticker}",
                "insider_type": "gov_contract",
                "amount":       amount,
                "agency":       agency,
            })
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get(self, url: str) -> Optional[List[Dict]]:
        headers = {
            **_HEADERS_BASE,
            "Authorization": f"Token {config.quiver_api_key}",
        }
        try:
            resp = http_get(url, headers=headers, timeout=_TIMEOUT)
            if resp.status_code == 401:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    @staticmethod
    def _committee_signal(committees: List[str], ticker: str) -> str:
        """Gibt den Ausschuss-Namen zurück wenn er für den Ticker besonders relevant ist."""
        for c in committees:
            c_lower = c.lower()
            for keyword, label in _HIGH_SIGNAL_COMMITTEES.items():
                if keyword in c_lower:
                    return f"{c} ({label})"
        return ""

    @staticmethod
    def _parse_date(raw: str):
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw.strip()[:10], fmt).date()
            except ValueError:
                continue
        return None
