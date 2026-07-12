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

    # ── Exit-Lab (Roadmap 2.1): parametrisierbare Exits, alle Defaults
    # reproduzieren exakt das bisherige feste %-Verhalten ──────────────────────
    sl_mode: str = "fixed"           # "fixed" | "atr_trail" | "regime"
    atr_period: int = 14             # Wilder-ATR-Lookback (wie technical_indicators.py)
    atr_mult: float = 2.5            # Trailing-Abstand = atr_mult × ATR

    regime_lookback: int = 60        # kausal rückblickende Bars für die Entry-Regime-Klassifikation
    regime_vol_threshold: float = 0.30
    regime_sl_mult_up_low: float = 1.0
    regime_sl_mult_up_high: float = 1.0
    regime_sl_mult_down_low: float = 1.0
    regime_sl_mult_down_high: float = 1.0
    regime_tp_mult_up_low: float = 1.0
    regime_tp_mult_up_high: float = 1.0
    regime_tp_mult_down_low: float = 1.0
    regime_tp_mult_down_high: float = 1.0

    time_stop_mode: str = "hard"     # "hard" | "soft"
    soft_time_stop_min_gain: float = 0.05      # wie swing_strategy.py's Live-Schwelle
    soft_time_stop_extension_days: int = 20    # wird nur EINMAL gewährt


# ── Indicator helpers ──────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Wilder-ATR (spiegelt analyzers/technical_indicators.py::_atr)."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def _entry_regime(df: pd.DataFrame, signal_idx: int, cfg: BacktestConfig) -> str:
    """Kausale, günstige Ein-Ticker-Regime-Klassifikation NUR aus Bars bis
    einschließlich signal_idx (kein Look-Ahead) – bewusst einfacher als
    strategy_lab/regime.py::classify_window (fenster-/universumsweit, für
    Pro-Trade-Aufrufe zu teuer). Wird einmal je Trade beim Entry aufgerufen,
    nicht pro Bar."""
    start = max(0, signal_idx - cfg.regime_lookback + 1)
    window = df["Close"].iloc[start:signal_idx + 1]
    if len(window) < 20:
        return "UP_LOW"  # neutraler Fallback; Multiplikatoren sind ohnehin 1.0
    rets = window.pct_change().dropna()
    trend = "UP" if window.iloc[-1] >= window.mean() else "DOWN"
    ann_vol = float(rets.std(ddof=0) * (252 ** 0.5)) if len(rets) > 1 else 0.0
    vol = "HIGH" if ann_vol > cfg.regime_vol_threshold else "LOW"
    return f"{trend}_{vol}"


_REGIME_SL_MULT_FIELD = {
    "UP_HIGH": "regime_sl_mult_up_high", "UP_LOW": "regime_sl_mult_up_low",
    "DOWN_HIGH": "regime_sl_mult_down_high", "DOWN_LOW": "regime_sl_mult_down_low",
}
_REGIME_TP_MULT_FIELD = {
    "UP_HIGH": "regime_tp_mult_up_high", "UP_LOW": "regime_tp_mult_up_low",
    "DOWN_HIGH": "regime_tp_mult_down_high", "DOWN_LOW": "regime_tp_mult_down_low",
}


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


def _run_exit_loop(
    df: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    cfg: BacktestConfig,
    *,
    force_close_at_boundary: bool,
) -> dict:
    """Gemeinsamer Exit-Resolver für _simulate (schließt am Fenster-Rand immer
    zwangsweise, force_close_at_boundary=True) und
    strategy_lab.paper_forward._resolve (bleibt OFFEN, wenn die Zukunft
    schlicht noch nicht existiert, force_close_at_boundary=False) – Roadmap 2.1.
    Vorher waren beide Implementierungen unabhängig kopiert; ein Auseinander-
    laufen hätte neue Exit-Logik nur in einem der beiden Pfade wirksam werden
    lassen. Returns dict: status ('CLOSED'|'OPEN'), exit_idx, exit_price,
    return_pct, exit_reason, tp1_hit, tp2_hit."""
    sl_mult = tp_mult = 1.0
    if cfg.sl_mode == "regime":
        regime  = _entry_regime(df, entry_idx - 1, cfg)  # Signal-Bar, ein Tag vor Entry
        sl_mult = getattr(cfg, _REGIME_SL_MULT_FIELD[regime])
        tp_mult = getattr(cfg, _REGIME_TP_MULT_FIELD[regime])

    tp1_price = entry_price * (1 + cfg.tp1_pct * tp_mult)
    tp2_price = entry_price * (1 + cfg.tp2_pct * tp_mult)

    atr_series   = _atr(df, cfg.atr_period) if cfg.sl_mode == "atr_trail" else None
    running_high = entry_price

    if cfg.sl_mode == "atr_trail":
        atr0 = atr_series.iloc[entry_idx] if entry_idx < len(atr_series) else float("nan")
        sl_price = (entry_price - cfg.atr_mult * float(atr0)) if pd.notna(atr0) \
            else entry_price * (1 - cfg.sl_pct)   # ATR noch nicht warmgelaufen → fester %-Fallback
    elif cfg.sl_mode == "regime":
        sl_price = entry_price * (1 - cfg.sl_pct * sl_mult)
    else:
        sl_price = entry_price * (1 - cfg.sl_pct)   # unverändert wie vor Roadmap 2.1

    realized       = 0.0
    remaining_frac = 1.0
    breakeven      = False
    tp1_hit        = False
    tp2_hit        = False
    extended       = False

    last_idx    = len(df) - 1
    horizon_idx = entry_idx + cfg.max_hold_days
    end_idx     = min(horizon_idx, last_idx)

    j = entry_idx
    while j <= end_idx:
        row   = df.iloc[j]
        high  = float(row["High"])
        low   = float(row["Low"])
        close = float(row["Close"])

        if cfg.sl_mode == "atr_trail":
            running_high = max(running_high, high)
            atr_j = atr_series.iloc[j]
            if pd.notna(atr_j):
                sl_price = max(sl_price, running_high - cfg.atr_mult * float(atr_j))  # nur nach oben

        # TP1 check (only if not already hit)
        if not tp1_hit and high >= tp1_price:
            realized       += cfg.tp1_frac * cfg.tp1_pct * tp_mult
            remaining_frac -= cfg.tp1_frac
            breakeven       = True
            sl_price        = max(sl_price, entry_price) if cfg.sl_mode == "atr_trail" else entry_price
            tp1_hit         = True

        # TP2 check
        if tp1_hit and not tp2_hit and high >= tp2_price:
            sell_frac       = cfg.tp2_frac * remaining_frac
            realized       += sell_frac * cfg.tp2_pct * tp_mult
            remaining_frac -= sell_frac
            tp2_hit         = True

        # SL check (conservative: if same candle hits TP1 then SL, TP1 wins because
        # price had to travel up first; breakeven SL can still close remaining)
        if low <= sl_price:
            if cfg.sl_mode == "fixed":
                sl_ret = 0.0 if breakeven else -cfg.sl_pct              # unverändert
            elif cfg.sl_mode == "regime":
                sl_ret = 0.0 if breakeven else -(cfg.sl_pct * sl_mult)
            else:  # atr_trail
                sl_ret = 0.0 if (breakeven and sl_price <= entry_price) else (sl_price / entry_price - 1)
            realized += remaining_frac * sl_ret
            reason = ("SL_trail" if cfg.sl_mode == "atr_trail" and sl_price > entry_price
                      else "SL_breakeven" if breakeven else "SL")
            return {"status": "CLOSED", "exit_idx": j, "exit_price": sl_price,
                    "return_pct": realized, "exit_reason": reason,
                    "tp1_hit": tp1_hit, "tp2_hit": tp2_hit}

        # Time stop (last bar of window)
        if j == end_idx:
            full_horizon = horizon_idx <= last_idx
            gain_now = realized + remaining_frac * (close / entry_price - 1)

            if not full_horizon and not force_close_at_boundary:
                # Balken sind ausgegangen (Zukunft fehlt noch) → OFFEN statt Zwangs-Schließung.
                return {"status": "OPEN", "exit_idx": j, "exit_price": close,
                        "return_pct": gain_now, "exit_reason": "open",
                        "tp1_hit": tp1_hit, "tp2_hit": tp2_hit}

            if (cfg.time_stop_mode == "soft" and full_horizon and not extended
                    and gain_now >= cfg.soft_time_stop_min_gain):
                end_idx  = min(end_idx + cfg.soft_time_stop_extension_days, last_idx)
                extended = True
                j += 1
                continue

            # force_close_at_boundary=True schließt hier IMMER mit "time_stop" –
            # unabhängig von full_horizon (identisch zum Verhalten vor Roadmap 2.1).
            return {"status": "CLOSED", "exit_idx": j, "exit_price": close,
                    "return_pct": gain_now, "exit_reason": "time_stop",
                    "tp1_hit": tp1_hit, "tp2_hit": tp2_hit}
        j += 1

    # Fallback (should not normally be reached)
    close = float(df.iloc[end_idx]["Close"])
    return {"status": "CLOSED", "exit_idx": end_idx, "exit_price": close,
            "return_pct": realized + remaining_frac * (close / entry_price - 1),
            "exit_reason": "end_of_data", "tp1_hit": tp1_hit, "tp2_hit": tp2_hit}


def _simulate(df: pd.DataFrame, signal_idx: int, cfg: BacktestConfig, ticker: str) -> Trade:
    """Simulate one trade; entry is at Open of bar signal_idx+1."""
    entry_row   = df.iloc[signal_idx + 1]
    entry_price = float(entry_row["Open"])
    entry_date  = entry_row.name

    trade = Trade(ticker=ticker, entry_date=entry_date, entry_price=entry_price)

    result = _run_exit_loop(df, signal_idx + 1, entry_price, cfg, force_close_at_boundary=True)

    trade.exit_date   = df.index[result["exit_idx"]]
    trade.exit_price  = result["exit_price"]
    trade.return_pct  = result["return_pct"]
    trade.exit_reason = result["exit_reason"]
    trade.tp1_hit     = result["tp1_hit"]
    trade.tp2_hit     = result["tp2_hit"]
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
