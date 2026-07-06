"""
OptionsHedgeStrategy – Portfolioabsicherung über SPY-Put-Optionen.

Kauft schützende Puts auf SPY wenn alle Bedingungen erfüllt sind:
  1. Portfolio-Wert > $50.000
  2. VIX < 20 (günstige Put-Prämien)
  3. Markt-Regime: BEAR oder CRISIS

Im Paper-Mode wird der Kauf simuliert und als "Versicherungsprämie" erfasst.
Im Live-Mode: Alpaca Options API (erfordert Options-Berechtigung).

Absicherungsgröße:
  - Jeder SPY-Put-Kontrakt deckt 100 Aktien ab (Standard-Optionskontrakt)
  - Delta-Ziel: -0.5 (At-the-money Put)
  - Anzahl Kontrakte = Portfolio-Wert / (SPY-Kurs × 100)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, TYPE_CHECKING

from logger import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# ── Bedingungen für Absicherung ────────────────────────────────────────────────
_MIN_PORTFOLIO_VALUE     = float(os.getenv("HEDGE_MIN_PORTFOLIO",  "50000.0"))
_MAX_VIX_FOR_HEDGE       = float(os.getenv("HEDGE_MAX_VIX",        "20.0"))
_HEDGE_REGIMES           = {"BEAR", "CRISIS"}

# ── Options-Parameter ─────────────────────────────────────────────────────────
_SHARES_PER_CONTRACT     = 100       # Standard US-Optionskontrakt
_PUT_PREMIUM_ESTIMATE    = 0.02      # ~2% vom Strikepreis (Schätzung ohne Marktdaten)
_MAX_HEDGE_COST_PCT      = 0.02      # Max. 2% des Portfolio-Wertes für Prämien

# ── Paper-Mode ────────────────────────────────────────────────────────────────
_PAPER_MODE = os.getenv("TRADING_MODE", "paper").lower() == "paper"


class OptionsHedgeStrategy:
    """
    Absicherungsstrategie über SPY-Put-Optionen.

    Im Paper-Mode: Simuliert Kauf und erfasst Prämienkosten im Trade-Journal.
    Im Live-Mode:  Nutzt Alpaca Options API (benötigt Options-Genehmigung).
    """

    def __init__(self) -> None:
        self._active_hedges: Dict[str, Dict] = {}   # Aktive Absicherungs-Positionen
        self._total_premium_paid: float = 0.0        # Kumulierte Prämien (Versicherungskosten)
        self._hedge_count: int = 0

    # ── Hauptmethoden ─────────────────────────────────────────────────────────

    def should_hedge(self, portfolio_value: float, vix: float, regime: str) -> bool:
        """
        Prüft ob eine Absicherung aktuell sinnvoll ist.

        Bedingungen:
          1. Portfolio-Wert > MIN_PORTFOLIO_VALUE ($50k)
          2. VIX < MAX_VIX_FOR_HEDGE (20) – günstige Prämien
          3. Regime ist BEAR oder CRISIS

        Args:
            portfolio_value: Aktueller Portfoliowert in USD
            vix:             Aktueller VIX-Stand
            regime:          Markt-Regime: "BULL" | "NEUTRAL" | "BEAR" | "CRISIS"

        Returns:
            True wenn Absicherung empfohlen wird
        """
        if portfolio_value < _MIN_PORTFOLIO_VALUE:
            log.debug(
                "Hedge: Kein Hedging – Portfolio $%.0f < $%.0f Minimum",
                portfolio_value, _MIN_PORTFOLIO_VALUE,
            )
            return False

        if vix >= _MAX_VIX_FOR_HEDGE:
            log.debug(
                "Hedge: Kein Hedging – VIX %.1f >= %.1f (Prämien zu teuer)",
                vix, _MAX_VIX_FOR_HEDGE,
            )
            return False

        regime_upper = regime.upper()
        if regime_upper not in _HEDGE_REGIMES:
            log.debug(
                "Hedge: Kein Hedging – Regime %s nicht in %s",
                regime_upper, _HEDGE_REGIMES,
            )
            return False

        log.info(
            "Hedge: Absicherung empfohlen – Portfolio=$%.0f VIX=%.1f Regime=%s",
            portfolio_value, vix, regime_upper,
        )
        return True

    def calculate_hedge_size(
        self,
        portfolio_value: float,
        delta: float = -0.5,
        spy_price: Optional[float] = None,
    ) -> Dict:
        """
        Berechnet die optimale Anzahl SPY-Put-Kontrakte zur Portfolioabsicherung.

        Formel:
          Kontrakte = Portfolio-Wert / (SPY-Kurs × Shares/Kontrakt) × |Delta|
          Max Prämie = Portfolio-Wert × MAX_HEDGE_COST_PCT

        Args:
            portfolio_value: Portfoliowert in USD
            delta:           Ziel-Delta des Puts (Standard: -0.5 = ATM)
            spy_price:       Aktueller SPY-Kurs (wenn None: wird via yfinance geladen)

        Returns:
            Dict mit: contracts, spy_price, strike_price, estimated_premium_usd,
                      notional_coverage, delta_used
        """
        # SPY-Kurs laden wenn nicht übergeben
        if spy_price is None:
            spy_price = self._get_spy_price()

        if spy_price is None or spy_price <= 0:
            log.warning("Hedge: SPY-Kurs nicht verfügbar – Berechnung nicht möglich")
            return {"contracts": 0, "message": "SPY-Kurs nicht verfügbar"}

        abs_delta = abs(delta)

        # Anzahl Kontrakte: vollständige Absicherung × |Delta|
        full_contracts = portfolio_value / (spy_price * _SHARES_PER_CONTRACT)
        contracts = max(1, round(full_contracts * abs_delta))

        # Geschätzte Prämie (vereinfacht ohne echtes Options-Pricing)
        strike_price = round(spy_price * (1.0 - 0.03), 2)   # 3% OTM Put
        estimated_premium_per_share = spy_price * _PUT_PREMIUM_ESTIMATE
        estimated_premium_usd = estimated_premium_per_share * _SHARES_PER_CONTRACT * contracts

        # Kosten-Cap: max MAX_HEDGE_COST_PCT des Portfolio-Wertes
        max_premium = portfolio_value * _MAX_HEDGE_COST_PCT
        if estimated_premium_usd > max_premium:
            contracts = max(1, int(max_premium / (estimated_premium_per_share * _SHARES_PER_CONTRACT)))
            estimated_premium_usd = estimated_premium_per_share * _SHARES_PER_CONTRACT * contracts
            log.info(
                "Hedge: Kontrakte auf %d begrenzt (Max-Prämie $%.0f)",
                contracts, max_premium,
            )

        notional_coverage = round(contracts * spy_price * _SHARES_PER_CONTRACT / portfolio_value * 100, 1)

        return {
            "contracts":              contracts,
            "spy_price":              round(spy_price, 2),
            "strike_price":           strike_price,
            "estimated_premium_usd":  round(estimated_premium_usd, 2),
            "notional_coverage_pct":  notional_coverage,
            "delta_used":             delta,
            "max_hedge_cost_pct":     _MAX_HEDGE_COST_PCT * 100,
        }

    def execute_hedge(
        self,
        broker,
        portfolio_value: float,
        expiry_days: int = 30,
    ) -> Optional[Dict]:
        """
        Führt den Absicherungskauf aus.

        Im Paper-Mode: Simuliert den Kauf und loggt als Versicherungsprämie.
        Im Live-Mode:  Alpaca Options API (Stub – erfordert Implementierung).

        Args:
            broker:          Broker-Instanz (PaperBroker oder Live-Broker)
            portfolio_value: Aktueller Portfoliowert
            expiry_days:     Laufzeit der Puts in Tagen (Standard: 30)

        Returns:
            Dict mit Hedge-Details oder None bei Fehler
        """
        spy_price = self._get_spy_price()
        if spy_price is None:
            log.warning("Hedge: Ausführung abgebrochen – SPY-Kurs nicht verfügbar")
            return None

        hedge_size = self.calculate_hedge_size(portfolio_value, spy_price=spy_price)
        if hedge_size.get("contracts", 0) == 0:
            log.warning("Hedge: Keine Kontrakte berechnet")
            return None

        contracts = hedge_size["contracts"]
        premium   = hedge_size["estimated_premium_usd"]
        expiry_dt = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expiry_days)).strftime("%Y-%m-%d")

        if _PAPER_MODE or getattr(broker, "_mode", "paper") == "paper":
            # Paper-Mode: Simulierung
            hedge_record = {
                "mode":              "paper",
                "type":              "PUT",
                "underlying":        "SPY",
                "contracts":         contracts,
                "strike":            hedge_size["strike_price"],
                "expiry":            expiry_dt,
                "spy_price_at_buy":  spy_price,
                "estimated_premium": premium,
                "notional_coverage_pct": hedge_size["notional_coverage_pct"],
                "timestamp":         datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "status":            "simulated",
            }
            hedge_id = f"HEDGE_{datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y%m%d_%H%M%S')}"
            self._active_hedges[hedge_id] = hedge_record
            self._total_premium_paid += premium
            self._hedge_count += 1

            log.info(
                "Hedge [Paper]: %d SPY Put-Kontrakte (Strike $%.2f, Expiry %s) "
                "| Prämie: $%.2f | Absicherung: %.1f%% des Portfolios",
                contracts, hedge_size["strike_price"], expiry_dt,
                premium, hedge_size["notional_coverage_pct"],
            )
            return hedge_record

        else:
            # Live-Mode: Alpaca Options API (Stub)
            log.warning(
                "Hedge [Live]: Alpaca Options API nicht implementiert. "
                "Bitte Alpaca Options-Berechtigung aktivieren und API integrieren."
            )
            return None

    def get_hedge_stats(self) -> Dict:
        """
        Gibt Statistiken über aktive und vergangene Absicherungen zurück.

        Returns:
            Dict mit: active_hedges, hedge_count, total_premium_paid_usd,
                      active_positions (Liste aktiver Hedge-Details)
        """
        return {
            "active_hedges":           len(self._active_hedges),
            "hedge_count":             self._hedge_count,
            "total_premium_paid_usd":  round(self._total_premium_paid, 2),
            "active_positions":        list(self._active_hedges.values()),
        }

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _get_spy_price() -> Optional[float]:
        """Lädt den aktuellen SPY-Kurs via yfinance."""
        try:
            import yfinance as yf
            ticker = yf.Ticker("SPY")
            info = ticker.fast_info
            price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            if price and price > 0:
                return float(price)
            # Fallback: History
            hist = ticker.history(period="1d")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as exc:
            log.warning("Hedge: SPY-Preis-Abruf fehlgeschlagen – %s", exc)
        return None
