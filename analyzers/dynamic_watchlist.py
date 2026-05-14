"""
DynamicWatchlist – scannt täglich ein Aktien-Universum und wählt automatisch
die vielversprechendsten Kandidaten als Watchlist aus.

Scoring (0–100 Punkte):
  30 % Volumen-Ratio     (ungewöhnlich hoher Umsatz = Interesse)
  25 % Preis-Momentum    (kurzfristige Kursstärke)
  25 % RSI-Score         (Zielbereich 40–60, Überkauft/verkauft = Abzug)
  20 % MACD-Signal       (Histogram positiv = Aufwärtsmomentum)

Ergebnis wird in data/dynamic_watchlist.json gespeichert und beim nächsten
Start sofort geladen (kein Netz-Scan nötig).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import yfinance as yf

# ─── Scan-Universum ──────────────────────────────────────────────────────────
SCAN_UNIVERSE = [
    # Mega-Cap Tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "ADBE",
    "CRM", "AMD", "INTC", "QCOM", "CSCO", "TXN", "INTU", "NOW", "PANW", "SNOW",
    # Finanzen
    "JPM", "BAC", "GS", "MS", "V", "MA", "AXP", "BLK",
    # Healthcare
    "LLY", "UNH", "JNJ", "MRK", "PFE", "ABBV", "TMO",
    # Energie / Industrie
    "XOM", "CVX", "CAT", "BA", "GE", "RTX",
    # Konsum
    "WMT", "COST", "HD", "MCD", "NKE", "DIS", "NFLX",
    # Wachstum / Momentum
    "PLTR", "COIN", "SHOP", "UBER", "ABNB", "SOFI",
    # Halbleiter
    "ASML", "TSM", "AMAT", "MU", "MRVL", "ARM",
]

_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "dynamic_watchlist.json")
_REFRESH_HOURS = 24  # Watchlist alle 24h neu berechnen


class DynamicWatchlist:
    """Automatisch verwaltete Watchlist basierend auf technischen Signalen."""

    def __init__(
        self,
        universe: List[str] = None,
        max_picks: int = 12,
        refresh_hours: int = _REFRESH_HOURS,
    ):
        self.universe = universe or SCAN_UNIVERSE
        self.max_picks = max_picks
        self.refresh_hours = refresh_hours
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)

    def get_watchlist(self, active_tickers: List[str] = None) -> List[str]:
        """
        Gibt die aktuelle Watchlist zurück.
        Lädt aus Cache wenn frisch genug, sonst neuer Scan.
        active_tickers: Positionen die immer enthalten bleiben.
        """
        active_tickers = list(active_tickers or [])
        cached = self._load_cache()

        needs_refresh = (
            cached is None
            or self._cache_age_hours(cached) >= self.refresh_hours
        )

        if needs_refresh:
            print("🔍 Watchlist-Scan läuft (aktualisiert alle 24h)…")
            tickers = self._scan()
            self._save_cache(tickers)
        else:
            tickers = cached["tickers"]
            age_h = self._cache_age_hours(cached)
            print(f"📋 Watchlist geladen (vor {age_h:.1f}h aktualisiert): {', '.join(tickers)}")

        # Immer aktive Positionen einfügen
        for t in active_tickers:
            if t not in tickers:
                tickers.insert(0, t)

        return tickers

    def force_refresh(self, active_tickers: List[str] = None) -> List[str]:
        """Erzwingt einen neuen Scan unabhängig vom Cache-Alter."""
        active_tickers = list(active_tickers or [])
        print("🔄 Watchlist wird neu gescannt…")
        tickers = self._scan()
        self._save_cache(tickers)
        for t in active_tickers:
            if t not in tickers:
                tickers.insert(0, t)
        return tickers

    def get_scored_candidates(self) -> List[Dict]:
        """Gibt alle bewerteten Kandidaten mit Scores zurück (für Dashboard)."""
        return self._score_universe()

    # ── Internes ─────────────────────────────────────────────────────────────

    def _scan(self) -> List[str]:
        scored = self._score_universe()
        return [s["ticker"] for s in scored[: self.max_picks]]

    def _score_universe(self) -> List[Dict]:
        results = []
        for ticker in self.universe:
            score_data = self._score_ticker(ticker)
            if score_data:
                results.append(score_data)
        results.sort(key=lambda x: x["total_score"], reverse=True)
        return results

    def _score_ticker(self, ticker: str) -> Optional[Dict]:
        try:
            hist = yf.Ticker(ticker).history(period="3mo")
            if hist.empty or len(hist) < 30:
                return None

            close = hist["Close"]
            volume = hist["Volume"]

            # ── Volumen-Score (30%) ──────────────────────────────────────────
            avg_vol = volume.iloc[:-5].mean()
            recent_vol = volume.iloc[-5:].mean()
            vol_ratio = float(recent_vol / avg_vol) if avg_vol > 0 else 1.0
            vol_score = min(vol_ratio / 3.0, 1.0) * 30  # max 30 Punkte

            # ── Momentum-Score (25%) ─────────────────────────────────────────
            price_now  = float(close.iloc[-1])
            price_20d  = float(close.iloc[-20])
            momentum   = (price_now - price_20d) / price_20d  # 20-Tage-Return
            mom_score  = max(min((momentum + 0.10) / 0.20, 1.0), 0.0) * 25

            # ── RSI-Score (25%) ──────────────────────────────────────────────
            rsi = self._rsi(close, 14)
            if rsi is None:
                rsi_score = 12.5  # neutral
            elif 40 <= rsi <= 60:
                rsi_score = 25.0  # Idealbereich
            elif 30 <= rsi < 40 or 60 < rsi <= 70:
                rsi_score = 15.0  # akzeptabel
            else:
                rsi_score = 5.0   # überkauft / überverkauft

            # ── MACD-Score (20%) ─────────────────────────────────────────────
            macd_hist = self._macd_hist(close)
            if macd_hist is None:
                macd_score = 10.0
            elif macd_hist > 0:
                macd_score = min(macd_hist / price_now * 5000, 1.0) * 20
            else:
                macd_score = 0.0

            total = vol_score + mom_score + rsi_score + macd_score

            return {
                "ticker":       ticker,
                "total_score":  round(total, 2),
                "price":        round(price_now, 2),
                "vol_ratio":    round(vol_ratio, 2),
                "momentum_20d": round(momentum * 100, 2),
                "rsi":          round(rsi, 1) if rsi else None,
                "macd_hist":    round(macd_hist, 4) if macd_hist else None,
                "vol_score":    round(vol_score, 1),
                "mom_score":    round(mom_score, 1),
                "rsi_score":    round(rsi_score, 1),
                "macd_score":   round(macd_score, 1),
            }
        except Exception:
            return None

    @staticmethod
    def _rsi(close, period: int = 14) -> Optional[float]:
        if len(close) < period + 1:
            return None
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        val = rsi.iloc[-1]
        return float(val) if not (val != val) else None  # NaN check

    @staticmethod
    def _macd_hist(close) -> Optional[float]:
        if len(close) < 35:
            return None
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        return float((macd - signal).iloc[-1])

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _load_cache(self) -> Optional[Dict]:
        try:
            with open(_DATA_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _save_cache(self, tickers: List[str]):
        data = {
            "tickers":    tickers,
            "updated_at": datetime.utcnow().isoformat(),
        }
        with open(_DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)

    @staticmethod
    def _cache_age_hours(cache: Dict) -> float:
        try:
            updated = datetime.fromisoformat(cache["updated_at"])
            return (datetime.utcnow() - updated).total_seconds() / 3600
        except Exception:
            return 999.0
