"""
SignalDrivenExpander – erkennt unbekannte Ticker aus bestehenden Datenquellen
und fügt sie temporär zur Watchlist hinzu.

Quellen die Ticker liefern:
  • SEC Form 4 (Insider-Trades)      → große Insider-Käufe
  • USASpending (Regierungsaufträge) → große neue Aufträge
  • OptionsFlow (Options-Daten)      → ungewöhnlicher C/P-Flow
  • Reddit / StockTwits              → Social-Media-Spikes
  • Reddit-Titel / Texte             → Ticker-Erwähnungen

Jeder erkannte Ticker bekommt einen Ablauf-Timestamp (Standard: 7 Tage).
Wenn kein neues Signal innerhalb der Frist eintrifft → automatisch entfernt.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# ─── Persistenz ──────────────────────────────────────────────────────────────
_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "signal_tickers.json")

# Bekannte Nicht-Ticker die in Texten auftauchen (False-Positive-Filter)
_BLACKLIST = {
    "CEO", "CFO", "CTO", "IPO", "FDA", "SEC", "ETF", "GDP", "CPI", "FED",
    "USA", "USD", "EUR", "GBP", "JPY", "API", "AI", "ML", "EPS", "PE",
    "ALL", "ARE", "FOR", "NEW", "NOW", "BIG", "TOP", "MAX", "NET", "PAY",
    "IT", "US", "UK", "EU", "AM", "PM", "DD", "RE", "DM", "PR", "IR",
    "YOLO", "FOMO", "IMO", "ATH", "ATL", "OTC", "RH", "WSB",
}

# Mindest-Kaufvolumen für Insider-Signal (USD)
_MIN_INSIDER_BUY_USD = 200_000

# Mindest-Auftragswert für USASpending-Signal (USD)
_MIN_CONTRACT_USD = 5_000_000

# Mindest-Score für Social-Signal (Reddit/StockTwits pulse)
_MIN_SOCIAL_SCORE = 0.25

# Ablaufzeit in Tagen
_DEFAULT_TTL_DAYS = 7


class SignalDrivenExpander:
    """
    Sammelt Signal-Ticker aus allen vorhandenen Collector-Ergebnissen
    und pflegt eine persistente temporäre Watchlist-Erweiterung.
    """

    def __init__(self, ttl_days: int = _DEFAULT_TTL_DAYS):
        self.ttl_days = ttl_days
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def process_news_items(self, items: List[Dict]) -> List[str]:
        """
        Analysiert News-Items (aus beliebigen Collectors) auf Signal-Ticker.
        Gibt neu hinzugefügte Ticker zurück.
        """
        found: Dict[str, str] = {}  # ticker → grund

        for item in items:
            source = item.get("source", "").lower()
            ticker = (item.get("ticker") or "").upper().strip()

            # ── Insider-Trades (SEC Form 4) ───────────────────────────────
            if "insider" in source or "form 4" in source or "sec" in source:
                action = (item.get("action") or item.get("text") or "").lower()
                value  = item.get("value") or item.get("amount") or 0
                try:
                    value = float(str(value).replace(",", "").replace("$", ""))
                except Exception:
                    value = 0
                if "buy" in action or "purchase" in action or "acqui" in action:
                    if value >= _MIN_INSIDER_BUY_USD and ticker and ticker not in _BLACKLIST:
                        found[ticker] = f"Insider-Kauf ${value:,.0f}"

            # ── US-Regierungsaufträge ─────────────────────────────────────
            elif "usaspending" in source or "contract" in source or "government" in source:
                value = item.get("amount") or item.get("value") or 0
                try:
                    value = float(str(value).replace(",", "").replace("$", ""))
                except Exception:
                    value = 0
                if value >= _MIN_CONTRACT_USD and ticker and ticker not in _BLACKLIST:
                    found[ticker] = f"Regierungsauftrag ${value:,.0f}"

            # ── Options-Flow ──────────────────────────────────────────────
            elif "option" in source or "flow" in source:
                cp_ratio = item.get("call_put_ratio") or item.get("cp_ratio") or 0
                try:
                    cp_ratio = float(cp_ratio)
                except Exception:
                    cp_ratio = 0
                if cp_ratio >= 3.0 and ticker and ticker not in _BLACKLIST:
                    found[ticker] = f"Options-Flow C/P={cp_ratio:.1f}"

            # ── Social-Erwähnungen (Reddit / StockTwits) ──────────────────
            elif any(s in source for s in ("reddit", "stocktwits", "twitter")):
                score = abs(item.get("sentiment_score") or item.get("score") or 0)
                try:
                    score = float(score)
                except Exception:
                    score = 0
                if score >= _MIN_SOCIAL_SCORE and ticker and ticker not in _BLACKLIST:
                    found[ticker] = f"Social-Signal score={score:.2f}"
                # Auch Ticker aus Texten extrahieren
                text = item.get("title", "") + " " + item.get("text", "")
                for t in self._extract_tickers_from_text(text):
                    if t not in found:
                        found[t] = f"Social-Erwähnung ({source.split('/')[0]})"

        return self._add_tickers(found)

    def process_social_spikes(self, spikes: List[Dict]) -> List[str]:
        """
        Verarbeitet Social-Pulse-Spikes direkt (aus run_social_scan).
        spikes: Liste mit {ticker, spike_ratio, avg_score}
        """
        found: Dict[str, str] = {}
        for spike in spikes:
            ticker = (spike.get("ticker") or "").upper().strip()
            ratio  = spike.get("spike_ratio", 0)
            score  = spike.get("avg_score", 0)
            if ticker and ticker not in _BLACKLIST and ratio >= 3.0:
                found[ticker] = f"Social-Spike {ratio:.1f}× (score {score:+.2f})"
        return self._add_tickers(found)

    def get_active_tickers(self) -> List[str]:
        """Gibt alle noch gültigen Signal-Ticker zurück (nicht abgelaufen)."""
        data = self._load()
        now = datetime.utcnow()
        active = []
        for ticker, entry in data.items():
            try:
                expires = datetime.fromisoformat(entry["expires_at"])
                if expires > now:
                    active.append(ticker)
            except Exception:
                pass
        return active

    def get_all_entries(self) -> List[Dict]:
        """Alle Einträge mit Metadaten (für Dashboard)."""
        data = self._load()
        now = datetime.utcnow()
        result = []
        for ticker, entry in data.items():
            try:
                expires = datetime.fromisoformat(entry["expires_at"])
                active  = expires > now
            except Exception:
                active = False
            result.append({
                "ticker":     ticker,
                "reason":     entry.get("reason", "–"),
                "added_at":   entry.get("added_at", "–")[:16],
                "expires_at": entry.get("expires_at", "–")[:16],
                "active":     active,
                "signals":    entry.get("signals", 1),
            })
        result.sort(key=lambda x: (not x["active"], x["expires_at"]), reverse=False)
        return result

    def cleanup_expired(self) -> int:
        """Entfernt abgelaufene Einträge. Gibt Anzahl entfernter zurück."""
        data = self._load()
        now = datetime.utcnow()
        before = len(data)
        data = {
            t: e for t, e in data.items()
            if datetime.fromisoformat(e["expires_at"]) > now
        }
        self._save(data)
        return before - len(data)

    def renew(self, ticker: str, reason: str = ""):
        """Verlängert einen bestehenden Ticker um ttl_days."""
        data = self._load()
        if ticker in data:
            data[ticker]["expires_at"] = (
                datetime.utcnow() + timedelta(days=self.ttl_days)
            ).isoformat()
            data[ticker]["signals"] = data[ticker].get("signals", 1) + 1
            if reason:
                data[ticker]["reason"] = reason
            self._save(data)

    # ── Interne Helfer ────────────────────────────────────────────────────────

    def _add_tickers(self, found: Dict[str, str]) -> List[str]:
        """Fügt neue Ticker hinzu, erneuert bestehende. Gibt neue zurück."""
        data = self._load()
        newly_added = []
        now = datetime.utcnow()
        expires = (now + timedelta(days=self.ttl_days)).isoformat()

        for ticker, reason in found.items():
            if not self._is_valid_ticker(ticker):
                continue
            if ticker in data:
                # Bestehendem Eintrag das Ablaufdatum verlängern
                data[ticker]["expires_at"] = expires
                data[ticker]["signals"]    = data[ticker].get("signals", 1) + 1
                data[ticker]["reason"]     = reason
            else:
                data[ticker] = {
                    "reason":     reason,
                    "added_at":   now.isoformat(),
                    "expires_at": expires,
                    "signals":    1,
                }
                newly_added.append(ticker)

        self._save(data)
        return newly_added

    @staticmethod
    def _extract_tickers_from_text(text: str) -> List[str]:
        """Extrahiert $TICKER und ##TICKER Muster aus Text."""
        # $AAPL oder $aapl
        cashtags = re.findall(r'\$([A-Z]{1,5})\b', text.upper())
        # Hashtags wie #AAPL
        hashtags = re.findall(r'#([A-Z]{1,5})\b', text.upper())
        result = []
        for t in cashtags + hashtags:
            if t not in _BLACKLIST and 1 <= len(t) <= 5:
                result.append(t)
        return list(set(result))

    @staticmethod
    def _is_valid_ticker(ticker: str) -> bool:
        """Grobe Validierung: 1–5 Großbuchstaben, nicht in Blacklist."""
        if not ticker:
            return False
        if ticker in _BLACKLIST:
            return False
        if not re.match(r'^[A-Z]{1,5}$', ticker):
            return False
        return True

    def _load(self) -> Dict:
        try:
            with open(_DATA_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self, data: Dict):
        with open(_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
