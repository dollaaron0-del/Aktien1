"""
PortfolioRiskMonitor — forward-looking risk metrics for open positions.

Computes four risk dimensions from recent daily returns (60 days):
  1. Value at Risk (1-day, 95%, parametric)  — expected max 1-day loss
  2. Portfolio Beta                           — market sensitivity vs SPY
  3. Herfindahl concentration index          — position diversification
  4. Average pairwise price correlation      — how correlated the holdings are

These combine into a composite Risk Score (0–100):
  0–30   → LOW      (well-diversified, low volatility)
  30–60  → MEDIUM   (normal operating range)
  60–80  → HIGH     (consider trimming / blocking new positions)
  80–100 → CRITICAL (max concentration, correlated, volatile)

No API key required; uses yfinance with a 60-day sliding window (cached 1h).
Gracefully returns None for any missing ticker so the bot never crashes.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from logger import get_logger

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
_CACHE: Dict[str, Tuple[float, pd.Series]] = {}   # ticker → (timestamp, returns)
_CACHE_TTL = 3_600                                  # 1 hour
_LOOKBACK = "60d"
_CONFIDENCE = 0.95                                  # VaR confidence level
_TRADING_DAYS = 252

# Risk-score thresholds
_HIGH_RISK = 60
_CRITICAL_RISK = 80


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #
@dataclass
class PositionRisk:
    ticker: str
    weight: float        # fraction of portfolio value
    vol_daily: float     # daily volatility (std of returns)
    beta: float          # beta vs SPY
    var_1d: float        # 1-day VaR (95%), positive number → expected max loss $


@dataclass
class PortfolioRisk:
    risk_score: float                         # 0–100 composite
    risk_label: str                           # LOW / MEDIUM / HIGH / CRITICAL
    var_1d_pct: float                         # portfolio 1-day VaR as % of value
    var_1d_dollar: float                      # portfolio 1-day VaR in $
    portfolio_beta: float                     # weighted beta vs SPY
    concentration: float                      # Herfindahl index 0–1 (1 = one position)
    avg_correlation: float                    # mean pairwise return correlation
    positions: List[PositionRisk] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _fetch_returns(ticker: str) -> Optional[pd.Series]:
    """Return 60-day daily return series for ticker, cached for 1h."""
    now = time.time()
    if ticker in _CACHE:
        ts, series = _CACHE[ticker]
        if now - ts < _CACHE_TTL:
            return series

    try:
        import yfinance as yf
        raw = yf.download(ticker, period=_LOOKBACK, progress=False, auto_adjust=True)
        if raw.empty or len(raw) < 10:
            return None
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        rets = raw["Close"].pct_change().dropna()
        _CACHE[ticker] = (now, rets)
        return rets
    except Exception as e:
        log.debug("RiskMonitor: fetch error %s – %s", ticker, e)
        return None


def _beta(stock_rets: pd.Series, market_rets: pd.Series) -> float:
    """OLS beta of stock vs market on aligned series."""
    common = stock_rets.index.intersection(market_rets.index)
    if len(common) < 10:
        return 1.0
    s = stock_rets.loc[common].values
    m = market_rets.loc[common].values
    cov = float(np.cov(s, m)[0, 1])
    var = float(np.var(m, ddof=1))
    return cov / var if var > 1e-12 else 1.0


def _herfindahl(weights: List[float]) -> float:
    """Herfindahl-Hirschman concentration index (0 = perfect spread, 1 = one stock)."""
    if not weights:
        return 0.0
    return float(sum(w ** 2 for w in weights))


def _avg_pairwise_corr(return_matrix: pd.DataFrame) -> float:
    """Mean of upper-triangle pairwise correlations."""
    if return_matrix.shape[1] < 2:
        return 0.0
    corr = return_matrix.corr()
    n = corr.shape[0]
    total, count = 0.0, 0
    for i in range(n):
        for j in range(i + 1, n):
            v = corr.iloc[i, j]
            if not math.isnan(v):
                total += v
                count += 1
    return total / count if count else 0.0


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
class PortfolioRiskMonitor:
    """
    Compute forward-looking risk metrics for the current open positions.

    Usage:
        monitor = PortfolioRiskMonitor()
        risk = monitor.compute(positions_value, portfolio_total)
        if risk and risk.risk_score >= 70:
            # block new positions …
    """

    def compute(
        self,
        positions_value: Dict[str, float],   # ticker → current market value
        portfolio_total: float,
    ) -> Optional[PortfolioRisk]:
        """
        Return PortfolioRisk or None if fewer than 1 open positions.
        Never raises; all errors produce debug log entries.
        """
        if not positions_value or portfolio_total <= 0:
            return None

        spy_rets = _fetch_returns("SPY")

        weights: Dict[str, float] = {
            t: v / portfolio_total for t, v in positions_value.items() if v > 0
        }

        pos_risks: List[PositionRisk] = []
        return_series: Dict[str, pd.Series] = {}

        for ticker, w in weights.items():
            rets = _fetch_returns(ticker)
            if rets is None:
                log.debug("RiskMonitor: no data for %s – skipping", ticker)
                continue

            vol = float(rets.std())
            b = _beta(rets, spy_rets) if spy_rets is not None else 1.0
            # Parametric VaR: z_{95%} = 1.645
            var_1d_pct = 1.645 * vol
            var_1d_dollar = var_1d_pct * w * portfolio_total

            pos_risks.append(PositionRisk(
                ticker=ticker,
                weight=w,
                vol_daily=vol,
                beta=b,
                var_1d=var_1d_dollar,
            ))
            return_series[ticker] = rets

        if not pos_risks:
            return None

        # Portfolio-level aggregation
        weight_list = [p.weight for p in pos_risks]
        hhi = _herfindahl(weight_list)

        # Weighted beta
        port_beta = sum(p.weight * p.beta for p in pos_risks)

        # Portfolio volatility (using individual vols + weights; simplified)
        port_vol = sum(p.weight * p.vol_daily for p in pos_risks)
        port_var_pct = 1.645 * port_vol
        port_var_dollar = port_var_pct * portfolio_total

        # Average pairwise correlation
        if len(return_series) >= 2:
            ret_df = pd.DataFrame(return_series).dropna()
            avg_corr = _avg_pairwise_corr(ret_df)
        else:
            avg_corr = 0.0

        # ── Risk Score (0–100) ─────────────────────────────────────────────
        # Four sub-scores (0–25 each):
        #   VaR sub-score:        <1% daily = 0 pts, >4% = 25 pts
        #   Beta sub-score:       <0.8 = 0, >2.0 = 25
        #   Concentration score:  <0.1 HHI = 0, >0.8 HHI = 25
        #   Correlation score:    <0.2 = 0, >0.9 = 25
        var_score   = min(25, max(0, (port_var_pct - 0.01) / 0.03 * 25))
        beta_score  = min(25, max(0, (port_beta - 0.8) / 1.2 * 25))
        hhi_score   = min(25, max(0, (hhi - 0.10) / 0.70 * 25))
        corr_score  = min(25, max(0, (avg_corr - 0.20) / 0.70 * 25))
        total_score = var_score + beta_score + hhi_score + corr_score

        if total_score >= _CRITICAL_RISK:
            label = "CRITICAL"
        elif total_score >= _HIGH_RISK:
            label = "HIGH"
        elif total_score >= 30:
            label = "MEDIUM"
        else:
            label = "LOW"

        warnings: List[str] = []
        if hhi > 0.50:
            biggest = max(pos_risks, key=lambda p: p.weight)
            warnings.append(
                f"Hohe Konzentration: {biggest.ticker} = {biggest.weight*100:.0f}% des Portfolios"
            )
        if avg_corr > 0.70 and len(pos_risks) > 1:
            warnings.append(
                f"Positionen stark korreliert (Ø {avg_corr:.2f}) – Diversifikationsvorteil begrenzt"
            )
        if port_beta > 1.5:
            warnings.append(
                f"Hohes Markt-Exposure: Portfolio-Beta {port_beta:.2f} (>1.5 = aggressiv)"
            )
        if port_var_pct > 0.03:
            warnings.append(
                f"Hohes tägliches Verlustrisiko: VaR(95%) = {port_var_pct*100:.1f}% "
                f"(${port_var_dollar:,.0f})"
            )

        return PortfolioRisk(
            risk_score=round(total_score, 1),
            risk_label=label,
            var_1d_pct=port_var_pct,
            var_1d_dollar=port_var_dollar,
            portfolio_beta=round(port_beta, 2),
            concentration=round(hhi, 3),
            avg_correlation=round(avg_corr, 3),
            positions=pos_risks,
            warnings=warnings,
        )

    def should_block_new_position(self, risk: Optional["PortfolioRisk"]) -> Tuple[bool, str]:
        """
        Returns (block, reason).  Blocks new entries only at CRITICAL risk.
        HIGH risk returns False — still allowed but logged as warning.
        """
        if risk is None:
            return False, ""
        if risk.risk_label == "CRITICAL":
            return True, (
                f"Portfolio-Risiko CRITICAL (Score {risk.risk_score:.0f}/100) – "
                "keine neuen Positionen bis Risiko reduziert."
            )
        return False, ""
