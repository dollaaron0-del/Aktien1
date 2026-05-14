"""
EarningsSurprise – reagiert auf Quartalszahlen-Überraschungen.

Logik:
  1. Prüft nach jeder Analyse ob Earnings gerade stattgefunden haben (<3 Tage)
  2. Berechnet ob tatsächliches EPS über/unter Konsens-Erwartung lag
  3. Bei positiver Überraschung >5%: BULLISH-Bonus auf Sentiment-Score
  4. Bei negativer Überraschung >5%: BEARISH-Warnung, erhöht Exit-Bereitschaft
  5. Starke Überraschung (>15%) → sofortige Telegram-Benachrichtigung
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Tuple

import yfinance as yf

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "earnings_cache")

# Schwellwerte für Überraschungs-Klassifizierung
_STRONG_BEAT  =  0.15   # +15% über Konsens
_BEAT         =  0.05   # +5%  über Konsens
_MISS         = -0.05   # -5%  unter Konsens
_STRONG_MISS  = -0.15   # -15% unter Konsens

# Bonus/Malus auf Sentiment-Score
_BEAT_BONUS        =  0.12
_STRONG_BEAT_BONUS =  0.20
_MISS_MALUS        = -0.12
_STRONG_MISS_MALUS = -0.22

# Wie viele Tage nach Earnings ist der Effekt noch aktiv?
_EFFECT_DAYS = 3


class EarningsSurprise:
    """Analysiert Earnings-Überraschungen und gibt Sentiment-Anpassungen zurück."""

    def __init__(self):
        os.makedirs(_CACHE_DIR, exist_ok=True)

    def check(self, ticker: str) -> Dict:
        """
        Prüft ob kürzliche Earnings-Überraschung vorliegt.
        Gibt Dict zurück:
          surprise_pct: float (z.B. 0.12 = +12%)
          label: "STRONG_BEAT" | "BEAT" | "MISS" | "STRONG_MISS" | "IN_LINE" | "UNKNOWN"
          sentiment_adjustment: float (addieren auf sentiment_score)
          days_ago: int
          eps_actual: float | None
          eps_estimate: float | None
        """
        cached = self._load_cache(ticker)
        if cached is not None:
            return cached

        result = self._fetch_earnings(ticker)
        self._save_cache(ticker, result)
        return result

    def get_sentiment_adjustment(self, ticker: str) -> float:
        """Schnelle Abfrage: nur den Sentiment-Anpassungswert."""
        return self.check(ticker).get("sentiment_adjustment", 0.0)

    def _fetch_earnings(self, ticker: str) -> Dict:
        base = {
            "ticker": ticker,
            "surprise_pct": 0.0,
            "label": "UNKNOWN",
            "sentiment_adjustment": 0.0,
            "days_ago": None,
            "eps_actual": None,
            "eps_estimate": None,
            "checked_at": datetime.utcnow().isoformat(),
        }
        try:
            stock = yf.Ticker(ticker)
            earnings = stock.earnings_dates
            if earnings is None or earnings.empty:
                return base

            # Letzter bekannter Earnings-Termin
            past = earnings[earnings.index <= datetime.now()]
            if past.empty:
                return base

            latest = past.iloc[0]
            earnings_date = past.index[0].date()
            days_ago = (date.today() - earnings_date).days

            if days_ago > _EFFECT_DAYS or days_ago < 0:
                base["label"] = "IN_LINE"
                base["days_ago"] = days_ago
                return base

            eps_actual   = latest.get("Reported EPS")
            eps_estimate = latest.get("EPS Estimate")

            if eps_actual is None or eps_estimate is None or eps_estimate == 0:
                base["days_ago"] = days_ago
                return base

            surprise_pct = float((eps_actual - eps_estimate) / abs(eps_estimate))

            if surprise_pct >= _STRONG_BEAT:
                label = "STRONG_BEAT"
                adj   = _STRONG_BEAT_BONUS
            elif surprise_pct >= _BEAT:
                label = "BEAT"
                adj   = _BEAT_BONUS
            elif surprise_pct <= _STRONG_MISS:
                label = "STRONG_MISS"
                adj   = _STRONG_MISS_MALUS
            elif surprise_pct <= _MISS:
                label = "MISS"
                adj   = _MISS_MALUS
            else:
                label = "IN_LINE"
                adj   = 0.0

            return {
                "ticker":               ticker,
                "surprise_pct":         round(surprise_pct, 4),
                "label":                label,
                "sentiment_adjustment": round(adj, 3),
                "days_ago":             days_ago,
                "eps_actual":           float(eps_actual),
                "eps_estimate":         float(eps_estimate),
                "earnings_date":        earnings_date.isoformat(),
                "checked_at":           datetime.utcnow().isoformat(),
            }
        except Exception:
            return base

    def _cache_path(self, ticker: str) -> str:
        return os.path.join(_CACHE_DIR, f"{ticker}.json")

    def _load_cache(self, ticker: str) -> Optional[Dict]:
        try:
            with open(self._cache_path(ticker)) as f:
                data = json.load(f)
            checked = datetime.fromisoformat(data["checked_at"])
            # Cache 6 Stunden gültig
            if (datetime.utcnow() - checked).total_seconds() < 6 * 3600:
                return data
        except Exception:
            pass
        return None

    def _save_cache(self, ticker: str, data: Dict):
        try:
            with open(self._cache_path(ticker), "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def format_surprise(data: Dict) -> str:
        label = data.get("label", "UNKNOWN")
        pct   = data.get("surprise_pct", 0) * 100
        eps_a = data.get("eps_actual")
        eps_e = data.get("eps_estimate")
        icon  = {"STRONG_BEAT": "🚀", "BEAT": "✅", "MISS": "⚠️",
                 "STRONG_MISS": "🔴", "IN_LINE": "➡️"}.get(label, "❓")
        parts = [f"{icon} Earnings {label} ({pct:+.1f}%)"]
        if eps_a is not None and eps_e is not None:
            parts.append(f"EPS: Ist ${eps_a:.2f} vs. Erwartet ${eps_e:.2f}")
        return " | ".join(parts)
