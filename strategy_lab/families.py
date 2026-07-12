"""
Strategie-Familien (Roadmap Phase 2 + 5.1) – klassische, klar abgegrenzte Archetypen.

Bewusst begrenzter Hypothesenraum (kein Regel-Generator → kein Data-Dredging):
  * donchian_breakout – Trendfolge: Ausbruch über das N-Tage-Hoch
  * rsi_meanrev       – Mean-Reversion: RSI-Oversold IM Aufwärtstrend (>SMA200)
  * ts_momentum       – Zeitreihen-Momentum: positives 6-Monats-Momentum >SMA200
  * w52_high          – Momentum: Nähe zum 52-Wochen-Hoch (George/Hwang-Effekt)
  * gap_meanrev       – Mean-Reversion: starker Overnight-Gap-down im Aufwärtstrend

Roadmap 5.1 nannte zusätzlich PEAD (Post-Earnings-Drift) und Saisonalität —
bewusst NICHT gebaut: PEAD (analyzers/pead_tracker.py) ist ein LIVE-Event-
Tracker gegen die aktuelle Erde-News-Lage, keine punkt-in-Zeit-Historie von
EPS-Überraschungen über 20 Jahre vorhanden → nicht ehrlich backtestbar, ohne
die Daten zu erfinden. Saisonalität (Kalendereffekte) bewusst zurückgestellt:
der Parameterraum (Monat/Wochentag/Handelstag-im-Monat …) lädt zum Data-
Dredging geradezu ein — genau das, was dieses Modul vermeiden will.

Wiederverwendung: die getestete Trade-Simulation der Engine (`_simulate`: TP1/TP2/
SL/Time/Kosten) bleibt identisch – nur die Entry-Signale unterscheiden sich. Das
hält die Familien vergleichbar (gleiche Exit-Mechanik) und den Code klein.
"""
from __future__ import annotations

from typing import Callable, Dict, List

import pandas as pd

from backtesting.engine import BacktestConfig, Trade, _simulate, _rsi
from strategy_lab.strategies import Strategy, register, EXIT_PARAM_SPACE

_CFG_FIELDS = set(BacktestConfig.__dataclass_fields__)


def _cfg_from(params: dict) -> BacktestConfig:
    """Baut die Exit-/Risiko-Config; Entry-spezifische Keys werden ignoriert."""
    return BacktestConfig(**{k: v for k, v in (params or {}).items() if k in _CFG_FIELDS})


def _generic_run(
    df: pd.DataFrame,
    ticker: str,
    params: dict,
    prepare: Callable[[pd.DataFrame, dict], pd.DataFrame],
    signal: Callable[[pd.DataFrame, int, dict], bool],
    min_bars: int,
) -> List[Trade]:
    """Generischer Backtest-Loop: identische Struktur wie engine.run, aber mit
    austauschbarem prepare/signal. Eine offene Position je Ticker, SL-Cooldown."""
    cfg = _cfg_from(params)
    df = prepare(df.copy(), params).dropna()
    if len(df) < min_bars:
        return []

    trades: List[Trade] = []
    last_exit_idx = -1
    last_sl_idx = -999
    i = 1
    while i < len(df) - 1:
        if (i - last_sl_idx) < cfg.cooldown_days:
            i += 1
            continue
        if i <= last_exit_idx:
            i += 1
            continue
        if signal(df, i, params):
            trade = _simulate(df, i, cfg, ticker)
            trades.append(trade)
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


# ── Familie 1: Donchian-Breakout (Trendfolge) ──────────────────────────────────
def _donchian_prepare(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    win = int(params.get("breakout_window", 20))
    # Höchstes Hoch der letzten `win` Tage, OHNE den heutigen Tag (shift 1).
    df["roll_high"] = df["High"].rolling(win).max().shift(1)
    return df


def _donchian_signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    row, prev = df.iloc[i], df.iloc[i - 1]
    # Steigende Flanke: heute Ausbruch, gestern noch nicht.
    return bool(row["Close"] > row["roll_high"]) and not bool(prev["Close"] > prev["roll_high"])


def _donchian_run(df, ticker, params):
    win = int((params or {}).get("breakout_window", 20))
    return _generic_run(df, ticker, params, _donchian_prepare, _donchian_signal, min_bars=win + 5)


# ── Familie 2: RSI-Mean-Reversion im Aufwärtstrend ─────────────────────────────
def _rsimr_prepare(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    ma = int(params.get("trend_ma", 200))
    df["rsi"] = _rsi(df["Close"], int(params.get("rsi_period", 14)))
    df["trend_ma"] = df["Close"].rolling(ma).mean()
    return df


def _rsimr_signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    row, prev = df.iloc[i], df.iloc[i - 1]
    buy = float(params.get("rsi_buy", 30.0))
    uptrend = bool(row["Close"] > row["trend_ma"])
    # Steigende Flanke: RSI taucht unter die Schwelle (Dip), Trend bleibt aufwärts.
    return uptrend and bool(row["rsi"] < buy) and not bool(prev["rsi"] < buy)


def _rsimr_run(df, ticker, params):
    ma = int((params or {}).get("trend_ma", 200))
    return _generic_run(df, ticker, params, _rsimr_prepare, _rsimr_signal, min_bars=ma + 5)


# ── Familie 3: Zeitreihen-Momentum ─────────────────────────────────────────────
def _tsmom_prepare(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    lb = int(params.get("lookback", 126))
    ma = int(params.get("trend_ma", 200))
    df["mom"] = df["Close"].pct_change(lb)
    df["trend_ma"] = df["Close"].rolling(ma).mean()
    return df


def _tsmom_signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    row, prev = df.iloc[i], df.iloc[i - 1]
    cond = bool(row["mom"] > 0) and bool(row["Close"] > row["trend_ma"])
    prev_cond = bool(prev["mom"] > 0) and bool(prev["Close"] > prev["trend_ma"])
    return cond and not prev_cond   # steigende Flanke


def _tsmom_run(df, ticker, params):
    p = params or {}
    need = max(int(p.get("lookback", 126)), int(p.get("trend_ma", 200))) + 5
    return _generic_run(df, ticker, params, _tsmom_prepare, _tsmom_signal, min_bars=need)


# ── Familie 4: 52-Wochen-Hoch-Nähe (Momentum, George/Hwang 2004) ──────────────
# Bewusst NICHT parametrisierbares Fenster (fest 252 Handelstage = "52 Wochen" –
# der Suchparameter WÄRE hier schon Data-Dredging, das ist die Definition des
# Effekts). Anders als donchian_breakout: kein reiner Ausbruchstag, sondern
# "nah am Hoch" (near_pct) – der akademische Befund ist Nähe, nicht Neuheit.
def _w52_prepare(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df["hi_252"] = df["High"].rolling(252).max().shift(1)
    df["pct_of_high"] = df["Close"] / df["hi_252"]
    return df


def _w52_signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    row, prev = df.iloc[i], df.iloc[i - 1]
    near = float(params.get("near_pct", 0.95))
    # Steigende Flanke: heute erstmals nah am 52W-Hoch, gestern noch nicht
    # (verhindert Dauerfeuer, solange der Kurs mehrere Tage dort verharrt).
    return bool(row["pct_of_high"] >= near) and not bool(prev["pct_of_high"] >= near)


def _w52_run(df, ticker, params):
    return _generic_run(df, ticker, params, _w52_prepare, _w52_signal, min_bars=257)


# ── Familie 5: Gap-Mean-Reversion (Overnight-Gap-down im Aufwärtstrend) ───────
# Anders als rsi_meanrev (Dip IM laufenden Trend über mehrere Tage): hier ein
# einzelner, plötzlicher Kurssprung zwischen Schlusskurs und Eröffnung. Kein
# Flanken-Trigger nötig – jeder Gap-Tag ist ein eigenständiges Ereignis.
def _gap_prepare(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    df["gap_pct"] = df["Open"] / df["Close"].shift(1) - 1
    ma = int(params.get("trend_ma", 200))
    df["trend_ma"] = df["Close"].rolling(ma).mean()
    return df


def _gap_signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    row = df.iloc[i]
    thresh = float(params.get("gap_down_pct", -0.04))
    uptrend = bool(row["Close"] > row["trend_ma"])  # nur gegen den Gap kaufen, wenn der Trend trägt
    return bool(row["gap_pct"] <= thresh) and uptrend


def _gap_run(df, ticker, params):
    ma = int((params or {}).get("trend_ma", 200))
    return _generic_run(df, ticker, params, _gap_prepare, _gap_signal, min_bars=ma + 5)


# ── Heute-Signal (Phase-5-Naht) ────────────────────────────────────────────────
class _FiresToday:
    """Heute-Abfrage: feuert die Entry-Flanke auf dem LETZTEN Balken?
    Gleiche prepare/signal wie der Backtest – kein Look-Ahead (roll_high etc.
    sind bereits geshiftet). Ignoriert Position/Cooldown (reine Signal-Sicht).
    Klasse statt Closure, damit Strategy-Objekte picklebar sind
    (multiprocessing im Parallel-Walk-Forward, Roadmap 6.3)."""

    def __init__(self, prepare, signal, min_bars: int):
        self.prepare, self.signal, self.min_bars = prepare, signal, min_bars

    def __call__(self, df: pd.DataFrame, params: dict) -> bool:
        d = self.prepare(df.copy(), params or {}).dropna()
        if len(d) < self.min_bars:
            return False
        return bool(self.signal(d, len(d) - 1, params or {}))


def _fires_today(prepare, signal, min_bars):
    return _FiresToday(prepare, signal, min_bars)


# ── Registrierung ──────────────────────────────────────────────────────────────
register(Strategy(
    name="donchian_breakout",
    description="Trendfolge: Ausbruch über das N-Tage-Hoch",
    runner=_donchian_run,
    default_params={"breakout_window": 20},
    param_space={"breakout_window": [20, 40, 55], "sl_pct": [0.07, 0.10],
                 "tp1_pct": [0.15, 0.20], "max_hold_days": [45, 60, 90],
                 **EXIT_PARAM_SPACE},
    signal=_fires_today(_donchian_prepare, _donchian_signal, min_bars=25),
))

register(Strategy(
    name="rsi_meanrev",
    description="Mean-Reversion: RSI-Oversold im Aufwärtstrend (>SMA200)",
    runner=_rsimr_run,
    default_params={"rsi_buy": 30.0, "trend_ma": 200},
    param_space={"rsi_buy": [25.0, 30.0, 35.0], "trend_ma": [100, 200],
                 "sl_pct": [0.05, 0.07], "tp1_pct": [0.08, 0.12], "max_hold_days": [15, 30],
                 **EXIT_PARAM_SPACE},
    signal=_fires_today(_rsimr_prepare, _rsimr_signal, min_bars=205),
))

register(Strategy(
    name="ts_momentum",
    description="Zeitreihen-Momentum: 6-Mon.-Momentum >0 und >SMA200",
    runner=_tsmom_run,
    default_params={"lookback": 126, "trend_ma": 200},
    param_space={"lookback": [63, 126, 252], "trend_ma": [100, 200],
                 "sl_pct": [0.07, 0.10], "tp1_pct": [0.15, 0.20], "max_hold_days": [60, 90],
                 **EXIT_PARAM_SPACE},
    signal=_fires_today(_tsmom_prepare, _tsmom_signal, min_bars=205),
))

register(Strategy(
    name="w52_high",
    description="Momentum: Nähe zum 52-Wochen-Hoch (George/Hwang-Effekt)",
    runner=_w52_run,
    default_params={"near_pct": 0.95},
    param_space={"near_pct": [0.92, 0.95, 0.98], "sl_pct": [0.07, 0.10],
                 "tp1_pct": [0.15, 0.20], "max_hold_days": [45, 60, 90],
                 **EXIT_PARAM_SPACE},
    signal=_fires_today(_w52_prepare, _w52_signal, min_bars=257),
))

register(Strategy(
    name="gap_meanrev",
    description="Mean-Reversion: starker Overnight-Gap-down im Aufwärtstrend",
    runner=_gap_run,
    default_params={"gap_down_pct": -0.04, "trend_ma": 200},
    param_space={"gap_down_pct": [-0.03, -0.04, -0.06], "trend_ma": [100, 200],
                 "sl_pct": [0.05, 0.07], "tp1_pct": [0.08, 0.12], "max_hold_days": [10, 20],
                 **EXIT_PARAM_SPACE},
    signal=_fires_today(_gap_prepare, _gap_signal, min_bars=205),
))
