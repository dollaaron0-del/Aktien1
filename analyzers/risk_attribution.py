"""
RiskAttribution – Dekomponiert Portfolio-Returns in Beta, Alpha und Timing.

Berechnet für eine Liste abgeschlossener Trades:
  - Beta:   Systematisches Marktrisiko (Kovarianz mit SPY / Varianz SPY)
  - Alpha:  Stock-Picking-Beitrag (Return über Beta-bereinigtes Marktrisiko)
  - Timing: Beitrag durch Entry-/Exit-Timing

Benutzt yfinance für SPY-Benchmark-Daten.
Keine externen Bibliotheken außer yfinance – Beta wird manuell via
Kovarianz/Varianz berechnet (kein scipy/sklearn).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, TYPE_CHECKING

from logger import get_logger

if TYPE_CHECKING:
    from portfolio.performance_tracker import PerformanceTracker

log = get_logger(__name__)

# SPY als Markt-Benchmark
_BENCHMARK_TICKER = "SPY"

# Mindest-Trades für aussagekräftige Attribution
_MIN_TRADES = 5


def _mean(values: List[float]) -> float:
    """Mittelwert einer Liste."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _variance(values: List[float]) -> float:
    """Biaskorrigierte Varianz (n-1)."""
    n = len(values)
    if n < 2:
        return 0.0
    mu = _mean(values)
    return sum((x - mu) ** 2 for x in values) / (n - 1)


def _covariance(xs: List[float], ys: List[float]) -> float:
    """Biaskorrigierte Kovarianz zweier Listen (n-1)."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mu_x = _mean(xs[:n])
    mu_y = _mean(ys[:n])
    return sum((xs[i] - mu_x) * (ys[i] - mu_y) for i in range(n)) / (n - 1)


def _r_squared(xs: List[float], ys: List[float]) -> float:
    """Bestimmtheitsmaß R² zwischen zwei Rendite-Serien."""
    cov = _covariance(xs, ys)
    var_x = _variance(xs)
    var_y = _variance(ys)
    if var_x <= 0 or var_y <= 0:
        return 0.0
    corr = cov / math.sqrt(var_x * var_y)
    return round(corr ** 2, 4)


def _fetch_spy_returns(start_date: str, end_date: str) -> Dict[str, float]:
    """
    Lädt SPY-Tagesrenditen für den angegebenen Zeitraum.

    Returns:
        Dict[date_str → daily_return_pct]  (z.B. {"2026-01-15": 1.23})
    """
    try:
        import yfinance as yf
        spy = yf.download(
            _BENCHMARK_TICKER,
            start=start_date,
            end=end_date,
            progress=False,
            auto_adjust=True,
        )
        if spy.empty:
            log.warning("RiskAttribution: Keine SPY-Daten für %s–%s", start_date, end_date)
            return {}

        closes = spy["Close"]
        result: Dict[str, float] = {}
        dates = list(closes.index)
        for i in range(1, len(dates)):
            prev = float(closes.iloc[i - 1])
            curr = float(closes.iloc[i])
            if prev > 0:
                date_str = dates[i].strftime("%Y-%m-%d")
                result[date_str] = (curr / prev - 1) * 100
        return result
    except Exception as exc:
        log.warning("RiskAttribution: SPY-Abruf fehlgeschlagen – %s", exc)
        return {}


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parst ein ISO-Datumsformat robust."""
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.fromisoformat(date_str[:10])
        except Exception:
            return None


class RiskAttribution:
    """
    Dekomponiert Portfolio-Returns in:
      - Beta (Marktexposure über SPY)
      - Alpha (Stock-Picking-Beitrag)
      - Timing (Entry/Exit-Timing-Beitrag)

    Berechnung ohne scipy/sklearn – nur stdlib + yfinance.
    """

    def calculate(self, trades: List[Dict]) -> Dict:
        """
        Berechnet die Risiko-Attribution für eine Liste abgeschlossener Trades.

        Args:
            trades: Liste von Trade-Dicts mit Feldern:
                      - ticker, entry_date, sell_date, actual_return_pct,
                        entry_price (optional), sell_price (optional)

        Returns:
            Dict mit: beta, alpha_pct, timing_contribution_pct,
                      r_squared, market_correlation,
                      portfolio_return_pct, market_return_pct,
                      trades_analyzed, message
        """
        # Nur abgeschlossene Trades mit vollständigen Daten
        closed = [
            t for t in trades
            if t.get("sell_date") and t.get("entry_date") and t.get("actual_return_pct") is not None
        ]

        if len(closed) < _MIN_TRADES:
            return {
                "beta": None,
                "alpha_pct": None,
                "timing_contribution_pct": None,
                "r_squared": None,
                "market_correlation": None,
                "portfolio_return_pct": None,
                "market_return_pct": None,
                "trades_analyzed": len(closed),
                "message": (
                    f"Zu wenig Trades für Attribution (vorhanden: {len(closed)}, "
                    f"mind. {_MIN_TRADES} nötig)."
                ),
            }

        # Datumsbereich bestimmen
        entry_dates = [_parse_date(t["entry_date"]) for t in closed if _parse_date(t["entry_date"])]
        sell_dates  = [_parse_date(t["sell_date"])  for t in closed if _parse_date(t["sell_date"])]
        if not entry_dates or not sell_dates:
            return self._empty_result(len(closed), "Ungültige Datumswerte in Trades.")

        earliest = min(entry_dates) - timedelta(days=2)
        latest   = max(sell_dates)  + timedelta(days=2)
        start_str = earliest.strftime("%Y-%m-%d")
        end_str   = latest.strftime("%Y-%m-%d")

        spy_returns = _fetch_spy_returns(start_str, end_str)
        if not spy_returns:
            return self._empty_result(len(closed), "SPY-Daten nicht verfügbar.")

        # Portfolio-Returns und korrespondierende SPY-Returns sammeln
        portfolio_returns: List[float] = []
        market_returns: List[float]    = []

        for trade in closed:
            entry_dt = _parse_date(trade["entry_date"])
            sell_dt  = _parse_date(trade["sell_date"])
            if not entry_dt or not sell_dt:
                continue

            # SPY-Return über die Haltedauer des Trades
            hold_days = max(1, (sell_dt - entry_dt).days)
            spy_cumulative = self._spy_cumulative_return(spy_returns, entry_dt, sell_dt)

            portfolio_returns.append(float(trade["actual_return_pct"]))
            market_returns.append(spy_cumulative)

        if len(portfolio_returns) < _MIN_TRADES:
            return self._empty_result(len(closed), "Nicht genug übereinstimmende SPY-Daten.")

        # Beta: Kovarianz(Portfolio, SPY) / Varianz(SPY)
        cov    = _covariance(portfolio_returns, market_returns)
        var_m  = _variance(market_returns)
        beta   = round(cov / var_m, 4) if var_m > 0 else 0.0

        # Durchschnittliche Returns
        avg_portfolio = _mean(portfolio_returns)
        avg_market    = _mean(market_returns)

        # Alpha: Portfolio-Return - Beta * Markt-Return (Jensen's Alpha vereinfacht)
        alpha_pct = round(avg_portfolio - beta * avg_market, 4)

        # Timing-Beitrag: Differenz zwischen tatsächlichem Return und Beta-erklärtem Return
        beta_explained = beta * avg_market
        timing_contribution_pct = round(avg_portfolio - beta_explained - alpha_pct, 4)

        # R² und Korrelation
        r2   = _r_squared(portfolio_returns, market_returns)
        corr_cov = _covariance(portfolio_returns, market_returns)
        corr_std = math.sqrt(_variance(portfolio_returns) * _variance(market_returns))
        market_corr = round(corr_cov / corr_std, 4) if corr_std > 0 else 0.0

        log.info(
            "RiskAttribution: %d Trades | Beta=%.4f Alpha=%.2f%% Timing=%.2f%% R²=%.3f",
            len(portfolio_returns), beta, alpha_pct, timing_contribution_pct, r2,
        )

        return {
            "beta":                     beta,
            "alpha_pct":                alpha_pct,
            "timing_contribution_pct":  timing_contribution_pct,
            "r_squared":                r2,
            "market_correlation":       market_corr,
            "portfolio_return_pct":     round(avg_portfolio, 4),
            "market_return_pct":        round(avg_market, 4),
            "trades_analyzed":          len(portfolio_returns),
            "message":                  "OK",
        }

    def _spy_cumulative_return(
        self,
        spy_returns: Dict[str, float],
        entry_dt: datetime,
        sell_dt: datetime,
    ) -> float:
        """
        Berechnet den kumulativen SPY-Return zwischen Entry und Exit.
        Verwendet tägliche Returns und multipliziert sie zusammen.
        """
        cumulative = 1.0
        current = entry_dt
        while current <= sell_dt:
            date_str = current.strftime("%Y-%m-%d")
            if date_str in spy_returns:
                daily_pct = spy_returns[date_str]
                cumulative *= (1.0 + daily_pct / 100.0)
            current += timedelta(days=1)
        return round((cumulative - 1.0) * 100.0, 4)

    def get_report(self, performance_tracker: "PerformanceTracker") -> str:
        """
        Generiert einen formatierten Bericht der Risiko-Attribution.

        Args:
            performance_tracker: PerformanceTracker-Instanz mit Trade-History

        Returns:
            Formatierter Text-Report als String
        """
        trades = performance_tracker.get_recent_trades(n=100)
        result = self.calculate(trades)

        if result.get("message") != "OK":
            return f"[RiskAttribution] {result['message']}"

        lines = [
            "=" * 55,
            "  RISIKO-ATTRIBUTION (letzte 100 Trades)",
            "=" * 55,
            f"  Analysierte Trades:      {result['trades_analyzed']}",
            f"  Portfolio-Return (Ø):    {result['portfolio_return_pct']:+.2f}%",
            f"  Markt-Return SPY (Ø):    {result['market_return_pct']:+.2f}%",
            "-" * 55,
            f"  Beta (Marktexposure):    {result['beta']:.4f}",
            f"    → {'Überdurchschnittlich sensitiv auf Marktbewegungen' if result['beta'] > 1.1 else 'Marktähnliches Risiko' if result['beta'] > 0.9 else 'Geringer als Markt'}",
            f"  Alpha (Stock-Picking):   {result['alpha_pct']:+.4f}%",
            f"    → {'Positiver Alpha: Stock-Picking generiert Mehrwert' if result['alpha_pct'] > 0 else 'Negativer Alpha: Markt wird nicht geschlagen'}",
            f"  Timing-Beitrag:          {result['timing_contribution_pct']:+.4f}%",
            f"    → {'Entry/Exit-Timing trägt positiv bei' if result['timing_contribution_pct'] > 0 else 'Entry/Exit-Timing kostet Performance'}",
            "-" * 55,
            f"  R² (Marktkorrelation):   {result['r_squared']:.4f}",
            f"  Korrelation mit SPY:     {result['market_correlation']:.4f}",
            f"    → {result['r_squared']*100:.1f}% der Rendite durch Marktbewegung erklärt",
            "=" * 55,
        ]

        return "\n".join(lines)

    @staticmethod
    def _empty_result(trades_analyzed: int, message: str) -> Dict:
        return {
            "beta": None,
            "alpha_pct": None,
            "timing_contribution_pct": None,
            "r_squared": None,
            "market_correlation": None,
            "portfolio_return_pct": None,
            "market_return_pct": None,
            "trades_analyzed": trades_analyzed,
            "message": message,
        }
