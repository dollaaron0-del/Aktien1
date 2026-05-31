"""
Backtest simulation engine for the Ruflo swing strategy.

Entry signal (technical proxy for bot buy signal):
  - Price crosses above EMA21 (yesterday below → today above)
    OR price is within [0 %, +ema_tolerance] above EMA21 (pullback re-entry)
  - RSI in [rsi_min, rsi_max]  →  not in freefall, not overbought
  - Volume >= volume_ratio × 20-day average  →  confirms momentum

Trade lifecycle:
  1. Enter at next bar's Open after signal day
  2. TP1 at +tp1_pct  →  sell tp1_frac of shares; SL moves to breakeven
  3. TP2 at +tp2_pct  →  sell tp2_frac of remaining shares
  4. SL at -sl_pct (or 0 % after TP1 breakeven)  →  full exit
  5. Time stop at max_hold_days  →  exit at Close

One open position per ticker at a time; SL cooldown respected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from logger import get_logger

log = get_logger(__name__)


@dataclass
class Trade:
    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    return_pct: float = 0.0
    exit_reason: str = ""
    tp1_hit: bool = False
    tp2_hit: bool = False


@dataclass
class BacktestConfig:
    sl_pct: float = 0.07
    tp1_pct: float = 0.15
    tp1_frac: float = 0.25       # fraction of position sold at TP1
    tp2_pct: float = 0.30
    tp2_frac: float = 0.40       # fraction of remaining sold at TP2
    ema_period: int = 21
    ema_tolerance: float = 0.06  # max % above EMA21 for pullback entry
    rsi_period: int = 14
    rsi_min: float = 30.0
    rsi_max: float = 65.0
    volume_ratio: float = 0.80   # min volume vs 20-day avg
    max_hold_days: int = 45
    cooldown_days: int = 2


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _prepare(df: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    df = df.copy()
    df["ema21"]     = _ema(df["Close"], cfg.ema_period)
    df["rsi"]       = _rsi(df["Close"], cfg.rsi_period)
    df["vol_avg20"] = df["Volume"].rolling(20).mean()
    df["above_ema"] = df["Close"] >= df["ema21"]
    df["ema_dev"]   = (df["Close"] - df["ema21"]) / df["ema21"]
    return df.dropna()


def _is_signal(df: pd.DataFrame, i: int, cfg: BacktestConfig) -> bool:
    row  = df.iloc[i]
    prev = df.iloc[i - 1]

    crossover = (not prev["above_ema"]) and bool(row["above_ema"])
    pullback  = bool(row["above_ema"]) and (row["ema_dev"] <= cfg.ema_tolerance)

    if not (crossover or pullback):
        return False
    if not (cfg.rsi_min <= row["rsi"] <= cfg.rsi_max):
        return False
    if row["Volume"] < cfg.volume_ratio * row["vol_avg20"]:
        return False
    return True


def _simulate(df: pd.DataFrame, signal_idx: int, cfg: BacktestConfig, ticker: str) -> Trade:
    """Simulate one trade; entry is at Open of bar signal_idx+1."""
    entry_row   = df.iloc[signal_idx + 1]
    entry_price = float(entry_row["Open"])
    entry_date  = entry_row.name

    trade = Trade(ticker=ticker, entry_date=entry_date, entry_price=entry_price)

    sl_price  = entry_price * (1 - cfg.sl_pct)
    tp1_price = entry_price * (1 + cfg.tp1_pct)
    tp2_price = entry_price * (1 + cfg.tp2_pct)

    realized       = 0.0     # weighted P&L realised so far
    remaining_frac = 1.0
    breakeven      = False

    end_idx = min(signal_idx + 1 + cfg.max_hold_days, len(df) - 1)

    for j in range(signal_idx + 1, end_idx + 1):
        row   = df.iloc[j]
        high  = float(row["High"])
        low   = float(row["Low"])
        close = float(row["Close"])

        # TP1 check (only if not already hit)
        if not trade.tp1_hit and high >= tp1_price:
            realized       += cfg.tp1_frac * cfg.tp1_pct
            remaining_frac -= cfg.tp1_frac
            breakeven       = True
            sl_price        = entry_price  # move stop to breakeven
            trade.tp1_hit   = True

        # TP2 check
        if trade.tp1_hit and not trade.tp2_hit and high >= tp2_price:
            sell_frac       = cfg.tp2_frac * remaining_frac
            realized       += sell_frac * cfg.tp2_pct
            remaining_frac -= sell_frac
            trade.tp2_hit   = True

        # SL check  (conservative: if same candle hits TP1 then SL, TP1 wins because
        # price had to travel up first; breakeven SL can still close remaining)
        effective_sl = sl_price  # entry_price after breakeven is set
        if low <= effective_sl:
            sl_ret   = 0.0 if breakeven else -cfg.sl_pct
            realized += remaining_frac * sl_ret
            trade.exit_date   = row.name
            trade.exit_price  = effective_sl
            trade.return_pct  = realized
            trade.exit_reason = "SL_breakeven" if breakeven else "SL"
            return trade

        # Time stop (last bar of window)
        if j == end_idx:
            final = close / entry_price - 1
            realized += remaining_frac * final
            trade.exit_date   = row.name
            trade.exit_price  = close
            trade.return_pct  = realized
            trade.exit_reason = "time_stop"
            return trade

    # Fallback (should not normally be reached)
    last = df.iloc[end_idx]
    trade.exit_date   = last.name
    trade.exit_price  = float(last["Close"])
    trade.return_pct  = realized + remaining_frac * (trade.exit_price / entry_price - 1)
    trade.exit_reason = "end_of_data"
    return trade


# ── Public API ─────────────────────────────────────────────────────────────────

def run(df: pd.DataFrame, ticker: str, cfg: Optional[BacktestConfig] = None) -> List[Trade]:
    """Run the full backtest for one ticker.  Returns list of completed trades."""
    if cfg is None:
        cfg = BacktestConfig()

    df = _prepare(df, cfg)
    if len(df) < cfg.ema_period + 20:
        log.debug("Backtest: not enough data for %s (%d rows)", ticker, len(df))
        return []

    trades: List[Trade] = []
    last_exit_idx = -1
    last_sl_idx   = -999
    i = 1  # need i-1 for crossover check

    while i < len(df) - 1:
        # Respect cooldown after clean SL (no TP1 hit)
        if (i - last_sl_idx) < cfg.cooldown_days:
            i += 1
            continue

        # Don't enter while a trade is open
        if i <= last_exit_idx:
            i += 1
            continue

        if _is_signal(df, i, cfg):
            trade = _simulate(df, i, cfg, ticker)
            trades.append(trade)

            # Advance past the closed trade
            try:
                exit_idx = df.index.get_loc(trade.exit_date)
            except Exception:
                exit_idx = i + cfg.max_hold_days

            last_exit_idx = exit_idx

            if trade.exit_reason == "SL" and not trade.tp1_hit:
                last_sl_idx = exit_idx

            i = exit_idx + 1
        else:
            i += 1

    return trades
