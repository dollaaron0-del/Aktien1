"""
ShortInterestCollector – lädt Short-Interest-Daten von FINRA und Yahoo Finance.
Hohes Short-Interest + positive News = Short-Squeeze möglich (→ BULLISH-Signal).
Steigendes Short-Interest = institutionelle Skepsis (→ BEARISH-Signal).

Short Interest = % der Aktien die leer verkauft sind.
  <5%  = normal
  5-15% = erhöht
  >15%  = sehr hoch (Short-Squeeze-Potenzial)
  >30%  = extrem (GameStop-Niveau)
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
import yfinance as yf

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "short_interest_cache")


class ShortInterestCollector:

    def __init__(self):
        os.makedirs(_CACHE_DIR, exist_ok=True)

    def collect(self, ticker: str, lookback_days: int = 30) -> List[Dict]:
        data = self._get_short_interest(ticker)
        if not data:
            return []

        si_pct      = data.get("short_interest_pct", 0)
        shares_short = data.get("shares_short", 0)
        days_to_cover = data.get("days_to_cover", 0)
        change_pct  = data.get("change_pct", 0)

        # Klassifizierung
        if si_pct >= 30:
            level = "EXTREME"
            sentiment = "Short-Squeeze-Risiko extrem hoch"
        elif si_pct >= 15:
            level = "VERY_HIGH"
            sentiment = "Sehr hohes Short-Interest – Short-Squeeze möglich"
        elif si_pct >= 5:
            level = "ELEVATED"
            sentiment = "Erhöhtes Short-Interest – institutionelle Skepsis"
        else:
            level = "NORMAL"
            sentiment = "Normales Short-Interest"

        change_note = ""
        if change_pct > 20:
            change_note = f" (↑{change_pct:.0f}% vs. Vormonat – Short-Positionen wachsen)"
        elif change_pct < -20:
            change_note = f" (↓{abs(change_pct):.0f}% vs. Vormonat – Short-Deckung)"

        text = (
            f"Short Interest {ticker}: {si_pct:.1f}% der Aktien leer verkauft "
            f"({shares_short:,} Stück, {days_to_cover:.1f} Tage zum Decken). "
            f"{sentiment}.{change_note}"
        )

        return [{
            "source":            "Short Interest (FINRA/Yahoo)",
            "ticker":            ticker,
            "title":             f"Short Interest {ticker}: {si_pct:.1f}% ({level})",
            "text":              text,
            "url":               f"https://finance.yahoo.com/quote/{ticker}/",
            "published_at":      datetime.utcnow().isoformat(),
            "short_interest_pct": si_pct,
            "days_to_cover":     days_to_cover,
            "level":             level,
        }]

    def _get_short_interest(self, ticker: str) -> Optional[Dict]:
        cached = self._load_cache(ticker)
        if cached:
            return cached

        try:
            stock = yf.Ticker(ticker)
            info  = stock.info or {}

            shares_short      = info.get("sharesShort", 0) or 0
            shares_outstanding = info.get("sharesOutstanding", 0) or 0
            shares_short_prior = info.get("sharesShortPriorMonth", 0) or 0
            days_to_cover     = info.get("shortRatio", 0) or 0
            short_pct_float   = info.get("shortPercentOfFloat", 0) or 0

            si_pct = short_pct_float * 100 if short_pct_float < 1 else short_pct_float
            if si_pct == 0 and shares_outstanding > 0:
                si_pct = shares_short / shares_outstanding * 100

            change_pct = 0.0
            if shares_short_prior > 0:
                change_pct = (shares_short - shares_short_prior) / shares_short_prior * 100

            result = {
                "short_interest_pct": round(si_pct, 2),
                "shares_short":       int(shares_short),
                "days_to_cover":      round(float(days_to_cover), 1),
                "change_pct":         round(change_pct, 1),
                "fetched_at":         datetime.utcnow().isoformat(),
            }
            self._save_cache(ticker, result)
            return result
        except Exception:
            return None

    def _cache_path(self, ticker: str) -> str:
        return os.path.join(_CACHE_DIR, f"{ticker}.json")

    def _load_cache(self, ticker: str) -> Optional[Dict]:
        try:
            with open(self._cache_path(ticker)) as f:
                data = json.load(f)
            fetched = datetime.fromisoformat(data["fetched_at"])
            if (datetime.utcnow() - fetched).total_seconds() < 12 * 3600:
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
