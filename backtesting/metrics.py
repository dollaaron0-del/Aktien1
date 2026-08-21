"""Performance metrics derived from a list of completed trades."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List

import numpy as np

from backtesting.engine import Trade


@dataclass
class TickerMetrics:
    ticker: str
    n_trades: int
    win_rate: float        # fraction of trades with positive return
    avg_win: float         # mean return of winning trades
    avg_loss: float        # mean return of losing trades (negative value)
    profit_factor: float   # gross profit / gross loss
    total_return: float    # compounded equity curve return
    max_drawdown: float    # max peak-to-trough decline on equity curve
    sharpe: float          # trade-level Sharpe (mean/std × √n)
    cagr: float            # compound annual growth rate
    tp1_rate: float        # fraction of trades that reached TP1
    tp2_rate: float        # fraction of trades that reached TP2


def compute(trades: List[Trade], ticker: str, years: float = 3.0) -> TickerMetrics:
    """Derive all metrics from a list of completed trades for one ticker."""
    if not trades:
        return TickerMetrics(ticker=ticker, n_trades=0, win_rate=0, avg_win=0,
                             avg_loss=0, profit_factor=0, total_return=0,
                             max_drawdown=0, sharpe=0, cagr=0, tp1_rate=0, tp2_rate=0)

    returns = np.array([t.return_pct for t in trades])
    wins    = returns[returns > 0]
    losses  = returns[returns <= 0]

    gross_profit = float(wins.sum())   if len(wins)   else 0.0
    gross_loss   = float(-losses.sum()) if len(losses) else 1e-9
    profit_factor = gross_profit / gross_loss

    # Equity curve: $1 per trade (equal-weighted, sequential)
    equity = np.cumprod(1 + returns)
    peak   = np.maximum.accumulate(equity)
    dd     = (equity - peak) / peak
    max_dd = float(dd.min())

    # Trade-level Sharpe (not annualised by time — by trade count)
    std = returns.std(ddof=1) if len(returns) > 1 else 1e-9
    sharpe = float(returns.mean() / std * math.sqrt(len(returns))) if std > 0 else 0.0

    final_equity = float(equity[-1])
    total_return = final_equity - 1
    cagr = (final_equity ** (1 / max(years, 0.1))) - 1

    return TickerMetrics(
        ticker        = ticker,
        n_trades      = len(trades),
        win_rate      = len(wins) / len(trades),
        avg_win       = float(wins.mean())   if len(wins)   else 0.0,
        avg_loss      = float(losses.mean()) if len(losses) else 0.0,
        profit_factor = profit_factor,
        total_return  = total_return,
        max_drawdown  = max_dd,
        sharpe        = sharpe,
        cagr          = cagr,
        tp1_rate      = sum(1 for t in trades if t.tp1_hit) / len(trades),
        tp2_rate      = sum(1 for t in trades if t.tp2_hit) / len(trades),
    )


def aggregate(results: List[TickerMetrics]) -> TickerMetrics:
    """Combine per-ticker metrics into a single portfolio-level summary."""
    valid = [m for m in results if m.n_trades > 0]
    if not valid:
        return TickerMetrics("PORTFOLIO", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    total_trades = sum(m.n_trades for m in valid)
    weighted = lambda attr: sum(getattr(m, attr) * m.n_trades for m in valid) / total_trades

    return TickerMetrics(
        ticker        = "PORTFOLIO",
        n_trades      = total_trades,
        win_rate      = weighted("win_rate"),
        avg_win       = weighted("avg_win"),
        avg_loss      = weighted("avg_loss"),
        profit_factor = weighted("profit_factor"),
        total_return  = float(np.mean([m.total_return  for m in valid])),
        max_drawdown  = float(np.mean([m.max_drawdown  for m in valid])),
        sharpe        = float(np.mean([m.sharpe        for m in valid])),
        cagr          = float(np.mean([m.cagr          for m in valid])),
        tp1_rate      = weighted("tp1_rate"),
        tp2_rate      = weighted("tp2_rate"),
    )
