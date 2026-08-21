"""Historical OHLCV loader with local parquet cache (refreshes daily)."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

from logger import get_logger

log = get_logger(__name__)

_CACHE_DIR = Path(__file__).parent / ".cache"
_CACHE_TTL_SECONDS = 86_400  # 24 h


def load(ticker: str, years: int = 3) -> Optional[pd.DataFrame]:
    """Return daily OHLCV DataFrame for *ticker*, covering the last *years* years.

    Data is cached as parquet next to this module and refreshed once per day.
    Returns None if the ticker is unavailable or has insufficient history.
    """
    _CACHE_DIR.mkdir(exist_ok=True)
    cache_file = _CACHE_DIR / f"{ticker}_{years}y.parquet"

    if cache_file.exists():
        age = datetime.now() - datetime.fromtimestamp(cache_file.stat().st_mtime)
        if age.total_seconds() < _CACHE_TTL_SECONDS:
            try:
                return pd.read_parquet(cache_file)
            except Exception:
                pass  # corrupt cache → re-download

    end = datetime.now()
    start = end - timedelta(days=years * 365 + 30)  # small buffer for indicators
    try:
        df = yf.download(
            ticker,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            log.debug("Backtest loader: no data for %s", ticker)
            return None

        # yfinance sometimes returns MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) < 60:
            log.debug("Backtest loader: too few rows for %s (%d)", ticker, len(df))
            return None

        df.to_parquet(cache_file)
        log.debug("Backtest loader: cached %s (%d rows)", ticker, len(df))
        return df
    except Exception as e:
        log.debug("Backtest loader error for %s: %s", ticker, e)
        return None
