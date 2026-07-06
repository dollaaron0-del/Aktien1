"""
ShortSqueezeDetector – erkennt Short-Squeeze-Setups.

Short-Squeeze = Hoher Short-Interest (>15%) + positiver Katalysator → explosive Kursbewegung.
Klassische Beispiele: GameStop, AMC, HOOD.

Gibt Tickers mit Squeeze-Potenzial zurück für priorisierte Analyse.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional


class ShortSqueezeDetector:
    """
    Erkennt Short-Squeeze-Setups anhand von Short-Interest-Daten.

    Ein Setup liegt vor wenn:
      - Short Interest >= min_short_interest_pct (default 15%)
      - Days-to-Cover  >= min_days_to_cover      (default 2.0)

    Beide Bedingungen zusammen erhöhen das Risiko eines explosiven Anstiegs
    sobald positive Nachrichten / Katalysatoren hinzukommen.
    """

    def __init__(
        self,
        min_short_interest_pct: float = 15.0,
        min_days_to_cover: float = 2.0,
    ) -> None:
        self.min_short_interest_pct = min_short_interest_pct
        self.min_days_to_cover = min_days_to_cover

    # ── Öffentliche API ───────────────────────────────────────────────────────

    def check_ticker(self, ticker: str) -> Dict:
        """
        Prüft einen einzelnen Ticker auf Short-Squeeze-Potenzial.

        Returns:
            Dict mit ticker, si_pct, days_to_cover, squeeze_setup,
            squeeze_score (0-1) und label.
        """
        data = self._fetch_short_data(ticker)
        si_pct = data.get("short_interest_pct", 0.0)
        days_to_cover = data.get("days_to_cover", 0.0)

        squeeze_score = min(1.0, si_pct / 30.0)
        squeeze_setup = (
            si_pct >= self.min_short_interest_pct
            and days_to_cover >= self.min_days_to_cover
        )

        if si_pct >= 30:
            label = "EXTREME"
        elif si_pct >= 20:
            label = "VERY_HIGH"
        elif si_pct >= self.min_short_interest_pct:
            label = "HIGH"
        else:
            label = "NORMAL"

        return {
            "ticker": ticker,
            "si_pct": si_pct,
            "days_to_cover": days_to_cover,
            "squeeze_setup": squeeze_setup,
            "squeeze_score": round(squeeze_score, 3),
            "label": label,
        }

    def scan_watchlist(self, watchlist: List[str]) -> List[Dict]:
        """
        Scannt eine Liste von Tickern und gibt nur jene mit
        squeeze_setup == True zurück, absteigend nach squeeze_score sortiert.
        """
        results = []
        for ticker in watchlist:
            try:
                entry = self.check_ticker(ticker)
                if entry["squeeze_setup"]:
                    results.append(entry)
            except Exception:
                continue
        results.sort(key=lambda x: x["squeeze_score"], reverse=True)
        return results

    def build_signal_item(self, ticker: str, data: Dict) -> Dict:
        """
        Erzeugt ein News-Item-Dict im einheitlichen Collector-Format.

        Args:
            ticker: Ticker-Symbol
            data:   Rückgabe von check_ticker()
        """
        si_pct = data["si_pct"]
        dtc = data["days_to_cover"]
        score = data["squeeze_score"]

        summary = (
            f"{ticker} hat {si_pct:.1f}% Short-Float bei {dtc:.1f} Days-to-Cover "
            f"(Squeeze-Score: {score:.2f}). Hoher Short-Interest erhöht das Risiko "
            f"eines explosiven Kursanstiegs sobald ein positiver Katalysator "
            f"(Earnings-Beat, FDA-Zulassung, Upgrade) auftritt."
        )

        return {
            "title": (
                f"Short-Squeeze-Setup {ticker}: "
                f"{si_pct:.1f}% Short-Float, {dtc:.1f} Days-to-Cover"
            ),
            "summary": summary,
            "source": "short_squeeze",
            "signal": "BULLISCH",
            "url": f"https://finance.yahoo.com/quote/{ticker}",
            "published": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }

    # ── Interna ───────────────────────────────────────────────────────────────

    def _fetch_short_data(self, ticker: str) -> Dict:
        """
        Ruft Roh-Short-Interest-Daten vom ShortInterestCollector ab.
        Gibt leeres Dict bei Fehler zurück.
        """
        try:
            from collectors.short_interest_collector import ShortInterestCollector
            return ShortInterestCollector()._get_short_interest(ticker) or {}
        except Exception:
            return {}
