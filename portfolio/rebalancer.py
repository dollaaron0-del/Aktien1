"""
PortfolioRebalancer – Überwacht Sektor-Allokationen und generiert Rebalancing-Signale.

Prüft ob die aktuelle Portfoliogewichtung pro Sektor mehr als TARGET_DRIFT_PCT
vom Ziel abweicht, und generiert Kauf-/Verkaufssignale zum Rebalancieren.

Standard-Ziel-Allokationen (konfigurierbar):
  Technology:  30%
  Healthcare:  15%
  Finance:     15%
  Energy:      10%
  Consumer:    15%
  Other:       15%

Kein Rebalancing bei:
  - Portfolio-Wert < $10.000
  - Weniger als 3 offene Positionen
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, TYPE_CHECKING

from logger import get_logger

if TYPE_CHECKING:
    from portfolio.portfolio import Portfolio
    from broker.paper_broker import PaperBroker

log = get_logger(__name__)

# Mindest-Portfolio-Wert für Rebalancing
_MIN_PORTFOLIO_VALUE = float(os.getenv("REBALANCE_MIN_VALUE", "10000.0"))
# Mindest-Anzahl Positionen
_MIN_POSITIONS = 3
# Maximale Drift vom Ziel bevor Rebalancing ausgelöst wird (Standard 5%)
_TARGET_DRIFT_PCT = float(os.getenv("REBALANCE_DRIFT_PCT", "5.0"))

# Standard-Sektor-Zielallokationen (Summe = 1.0)
_DEFAULT_TARGET_ALLOCATIONS: Dict[str, float] = {
    "Technology": 0.30,
    "Healthcare": 0.15,
    "Finance":    0.15,
    "Energy":     0.10,
    "Consumer":   0.15,
    "Other":      0.15,
}

# Einfaches Ticker-zu-Sektor-Mapping für bekannte Tickers
# Wenn ein Ticker hier nicht steht, wird "Other" verwendet
_TICKER_SECTOR_MAP: Dict[str, str] = {
    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology",
    "GOOG": "Technology", "META": "Technology", "NVDA": "Technology",
    "AMD": "Technology", "INTC": "Technology", "AVGO": "Technology",
    "ORCL": "Technology", "CRM": "Technology", "ADBE": "Technology",
    "QCOM": "Technology", "TXN": "Technology", "IBM": "Technology",
    "CSCO": "Technology", "NOW": "Technology", "SNOW": "Technology",
    "PLTR": "Technology", "NET": "Technology", "DDOG": "Technology",
    "ZS": "Technology", "CRWD": "Technology", "PANW": "Technology",
    "AMZN": "Technology",  # primär als Tech/Cloud kategorisiert
    # Healthcare
    "JNJ": "Healthcare", "PFE": "Healthcare", "MRK": "Healthcare",
    "ABBV": "Healthcare", "LLY": "Healthcare", "BMY": "Healthcare",
    "AMGN": "Healthcare", "GILD": "Healthcare", "BIIB": "Healthcare",
    "REGN": "Healthcare", "VRTX": "Healthcare", "MRNA": "Healthcare",
    "ZBH": "Healthcare", "MDT": "Healthcare", "ABT": "Healthcare",
    "UNH": "Healthcare", "CVS": "Healthcare", "CI": "Healthcare",
    # Finance
    "JPM": "Finance", "BAC": "Finance", "WFC": "Finance",
    "GS": "Finance", "MS": "Finance", "C": "Finance",
    "BLK": "Finance", "AXP": "Finance", "V": "Finance",
    "MA": "Finance", "PYPL": "Finance", "SQ": "Finance",
    "USB": "Finance", "PNC": "Finance", "TFC": "Finance",
    "BRK.B": "Finance", "CB": "Finance", "MET": "Finance",
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "EOG": "Energy", "SLB": "Energy", "PXD": "Energy",
    "PSX": "Energy", "VLO": "Energy", "MPC": "Energy",
    "HAL": "Energy", "OXY": "Energy", "DVN": "Energy",
    # Consumer (Discretionary + Staples)
    "TSLA": "Consumer", "AMZN": "Consumer",  # Hinweis: AMZN doppelt, Technology gewinnt
    "HD": "Consumer", "LOW": "Consumer", "TGT": "Consumer",
    "WMT": "Consumer", "COST": "Consumer", "KO": "Consumer",
    "PEP": "Consumer", "MCD": "Consumer", "SBUX": "Consumer",
    "NKE": "Consumer", "DIS": "Consumer", "NFLX": "Consumer",
    "BKNG": "Consumer", "MAR": "Consumer",
}


def _get_sector(ticker: str) -> str:
    """Gibt den Sektor eines Tickers zurück, Standard 'Other'."""
    return _TICKER_SECTOR_MAP.get(ticker.upper(), "Other")


class PortfolioRebalancer:
    """
    Überwacht Sektor-Allokationen im Portfolio und generiert Rebalancing-Signale.
    """

    def __init__(
        self,
        target_allocations: Optional[Dict[str, float]] = None,
        drift_threshold_pct: float = _TARGET_DRIFT_PCT,
    ) -> None:
        """
        Args:
            target_allocations: Ziel-Allokationen pro Sektor (Summe sollte 1.0 sein).
                                 Standard: _DEFAULT_TARGET_ALLOCATIONS
            drift_threshold_pct: Maximale erlaubte Abweichung in % (Standard: 5.0)
        """
        self._targets = target_allocations or _DEFAULT_TARGET_ALLOCATIONS.copy()
        self._drift_threshold = drift_threshold_pct

    def check_rebalance_needed(
        self,
        portfolio: "Portfolio",
        current_prices: Dict[str, float],
    ) -> bool:
        """
        Prüft ob ein Rebalancing notwendig ist.

        Bedingungen für KEIN Rebalancing:
          - Portfolio-Wert < $10.000
          - Weniger als 3 offene Positionen
          - Keine Sektor-Drift > drift_threshold_pct

        Args:
            portfolio:      Portfolio-Instanz
            current_prices: Aktuelle Preise {ticker: price}

        Returns:
            True wenn Rebalancing empfohlen wird
        """
        positions = portfolio.all_positions()

        # Mindest-Bedingungen prüfen
        if len(positions) < _MIN_POSITIONS:
            log.debug(
                "Rebalancer: Kein Rebalancing – weniger als %d Positionen (%d vorhanden)",
                _MIN_POSITIONS, len(positions),
            )
            return False

        total_value = self._calc_portfolio_value(portfolio, current_prices)
        if total_value < _MIN_PORTFOLIO_VALUE:
            log.debug(
                "Rebalancer: Kein Rebalancing – Portfolio-Wert $%.0f < $%.0f",
                total_value, _MIN_PORTFOLIO_VALUE,
            )
            return False

        # Sektor-Allokationen berechnen
        current_alloc = self._calc_sector_allocations(positions, current_prices, total_value)

        # Drift prüfen
        for sektor, target_pct in self._targets.items():
            current_pct = current_alloc.get(sektor, 0.0) * 100.0
            target_display = target_pct * 100.0
            drift = abs(current_pct - target_display)
            if drift > self._drift_threshold:
                log.info(
                    "Rebalancer: Drift erkannt – Sektor %s: ist=%.1f%% ziel=%.1f%% abweichung=%.1f%%",
                    sektor, current_pct, target_display, drift,
                )
                return True

        return False

    def get_rebalance_plan(
        self,
        portfolio: "Portfolio",
        current_prices: Dict[str, float],
    ) -> List[Dict]:
        """
        Erstellt einen Rebalancing-Plan.

        Returns:
            Liste von Dicts: {
                ticker: str,
                action: "REDUCE" | "INCREASE",
                sektor: str,
                current_pct: float,
                target_pct: float,
                diff_pct: float,
            }
            Sortiert nach absolutem Diff (größte Abweichungen zuerst).
        """
        positions = portfolio.all_positions()
        total_value = self._calc_portfolio_value(portfolio, current_prices)

        if total_value <= 0 or len(positions) < _MIN_POSITIONS:
            return []

        current_alloc = self._calc_sector_allocations(positions, current_prices, total_value)

        plan: List[Dict] = []
        for sektor, target_ratio in self._targets.items():
            current_ratio = current_alloc.get(sektor, 0.0)
            diff_pct = (current_ratio - target_ratio) * 100.0  # positiv → zu viel, negativ → zu wenig

            if abs(diff_pct) <= self._drift_threshold:
                continue  # Innerhalb Toleranz

            action = "REDUCE" if diff_pct > 0 else "INCREASE"

            # Finde den repräsentativen Ticker für diesen Sektor (größte Position)
            sektor_positions = [
                (pos, current_prices.get(pos.ticker, pos.entry_price))
                for pos in positions.values()
                if _get_sector(pos.ticker) == sektor
            ]
            if not sektor_positions and action == "REDUCE":
                continue  # Keine Positionen im Sektor die reduziert werden könnten

            # Größte Position im Sektor
            if sektor_positions:
                sektor_positions.sort(
                    key=lambda x: x[0].shares * x[1], reverse=True
                )
                rep_ticker = sektor_positions[0][0].ticker
            else:
                rep_ticker = sektor  # Platzhalter wenn keine Position vorhanden

            plan.append({
                "ticker":      rep_ticker,
                "action":      action,
                "sektor":      sektor,
                "current_pct": round(current_ratio * 100.0, 2),
                "target_pct":  round(target_ratio * 100.0, 2),
                "diff_pct":    round(diff_pct, 2),
            })

        plan.sort(key=lambda x: abs(x["diff_pct"]), reverse=True)

        if plan:
            log.info(
                "Rebalancer: Plan erstellt – %d Sektoren außerhalb Toleranz (%.1f%%)",
                len(plan), self._drift_threshold,
            )
        return plan

    def apply_rebalance(
        self,
        portfolio: "Portfolio",
        broker: "PaperBroker",
        plan: List[Dict],
    ) -> List[str]:
        """
        Führt den Rebalancing-Plan aus.

        Aktuell werden nur REDUCE-Aktionen ausgeführt (Positionen verkleinern).
        INCREASE wird nur geloggt (kein automatischer Kauf ohne Signal).

        Args:
            portfolio: Portfolio-Instanz
            broker:    Broker für Orderausführung
            plan:      Rebalancing-Plan aus get_rebalance_plan()

        Returns:
            Log-Einträge der ausgeführten Aktionen
        """
        if not plan:
            return ["Kein Rebalancing-Plan vorhanden."]

        log_eintraege: List[str] = []

        for eintrag in plan:
            ticker = eintrag["ticker"]
            action = eintrag["action"]
            sektor = eintrag["sektor"]
            diff   = eintrag["diff_pct"]

            if action == "REDUCE":
                # Position um diff_pct / current_pct Anteil reduzieren
                positions = portfolio.all_positions()
                pos = positions.get(ticker)
                if not pos:
                    nachricht = f"SKIP {ticker}: Position nicht mehr vorhanden"
                    log_eintraege.append(nachricht)
                    log.info("Rebalancer: %s", nachricht)
                    continue

                # Anteil der Shares die verkauft werden sollen
                current_price = broker.get_price(ticker) or pos.entry_price
                reduce_ratio  = min(abs(diff) / max(eintrag["current_pct"], 1.0), 0.5)
                shares_to_sell = round(pos.shares * reduce_ratio, 4)

                if shares_to_sell <= 0:
                    continue

                result = broker.sell(ticker, shares_to_sell, current_price)
                # Nur bei bestätigtem Fill buchen – und mit den echten Fill-Werten.
                if not (isinstance(result, dict) and result.get("status") == "filled"):
                    warn = (result.get("reason") or result.get("error") or "unbekannt") \
                        if isinstance(result, dict) else str(result)
                    nachricht = (f"FEHLER REDUCE {ticker}: Order fehlgeschlagen ({warn}) "
                                 f"– Buch unverändert")
                    log_eintraege.append(nachricht)
                    log.error("Rebalancer: %s", nachricht)
                    continue
                fill_price = float(result.get("fill_price") or current_price)
                sold = float(result.get("shares") or shares_to_sell)
                new_shares = round(pos.shares - sold, 4)
                pnl = sold * (fill_price - pos.entry_price)
                # Buch nachziehen: Teilverkauf reduziert Position + bucht Erlös.
                if new_shares > 0:
                    portfolio.update_partial_tp(
                        ticker,
                        new_shares=new_shares,
                        new_stop_loss=pos.stop_loss,
                        sell_shares=sold,
                        sell_price=fill_price,
                        pnl=pnl,
                        new_count=pos.partial_tp_count,
                        currency=pos.currency,
                        fx_rate=pos.fx_rate_at_entry,
                    )
                else:
                    portfolio.close_position(ticker, fill_price, reason="Rebalancing")
                nachricht = (
                    f"REDUCE {ticker} ({sektor}): "
                    f"-{sold:.4f} Shares @ ${fill_price:.2f} "
                    f"(Drift: {diff:+.1f}%)"
                )
                log_eintraege.append(nachricht)
                log.info("Rebalancer: %s", nachricht)

            elif action == "INCREASE":
                # Kein automatischer Kauf – nur protokollieren
                nachricht = (
                    f"INFO INCREASE {ticker} ({sektor}): "
                    f"Sektor unter Ziel (Drift: {diff:+.1f}%) – "
                    "kein automatischer Kauf (Signal erforderlich)"
                )
                log_eintraege.append(nachricht)
                log.info("Rebalancer: %s", nachricht)

        return log_eintraege

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────

    def _calc_portfolio_value(
        self,
        portfolio: "Portfolio",
        current_prices: Dict[str, float],
    ) -> float:
        """Berechnet den Gesamtwert des Portfolios."""
        positions = portfolio.all_positions()
        positions_value = sum(
            pos.shares * current_prices.get(pos.ticker, pos.entry_price)
            for pos in positions.values()
        )
        cash = portfolio.cash
        return positions_value + cash

    def _calc_sector_allocations(
        self,
        positions: Dict,
        current_prices: Dict[str, float],
        total_value: float,
    ) -> Dict[str, float]:
        """
        Berechnet die aktuelle Sektor-Allokation als Anteil am Gesamt-Portfolio-Wert.

        Returns:
            Dict[sektor → ratio] (0.0–1.0, nur Positionen, ohne Cash)
        """
        sektor_werte: Dict[str, float] = {s: 0.0 for s in self._targets}

        for ticker, pos in positions.items():
            preis = current_prices.get(ticker, pos.entry_price)
            wert  = pos.shares * preis
            sektor = _get_sector(ticker)
            # Unbekannte Sektoren → "Other"
            bucket = sektor if sektor in sektor_werte else "Other"
            sektor_werte[bucket] = sektor_werte.get(bucket, 0.0) + wert

        if total_value <= 0:
            return {s: 0.0 for s in sektor_werte}

        return {sektor: wert / total_value for sektor, wert in sektor_werte.items()}
