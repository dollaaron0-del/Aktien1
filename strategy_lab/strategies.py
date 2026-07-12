"""
Strategie-Registry – die Erweiterungs-Naht für die historische Suche.

Eine Strategy bündelt: Name, Beschreibung, einen Runner (df, ticker, params)
→ Liste von Trades, Default-Parameter und einen Parameter-Raum (für die spätere
Grid-/Walk-Forward-Suche in Phase 2/3). Phase 1 registriert genau eine: die
bestehende Swing-Mechanik als Wrapper um backtesting.engine.

Bewusst begrenzt: neue Familien kommen als weitere register()-Aufrufe dazu –
KEIN unbegrenztes Regel-Generieren (das wäre Data-Dredging).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import pandas as pd

from backtesting.engine import (
    BacktestConfig, Trade, run as _engine_run,
    _prepare as _engine_prepare, _is_signal as _engine_is_signal,
)


@dataclass
class Strategy:
    name: str
    description: str
    runner: Callable[[pd.DataFrame, str, dict], List[Trade]]
    default_params: Dict = field(default_factory=dict)
    # Parameter-Raum für Phase 2/3 (Name → Liste von Kandidatenwerten).
    param_space: Dict[str, List] = field(default_factory=dict)
    # Phase 5: feuert das Entry-Signal HEUTE (letzter Balken)? Naht für den
    # Meta-Allokator. None = unterstützt keine Heute-Abfrage (z.B. baseline_swing).
    signal: Optional[Callable[[pd.DataFrame, dict], bool]] = None


REGISTRY: Dict[str, Strategy] = {}


def register(strategy: Strategy) -> Strategy:
    REGISTRY[strategy.name] = strategy
    return strategy


def get(name: str) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unbekannte Strategie '{name}'. Bekannt: {all_names()}")
    return REGISTRY[name]


def all_names() -> List[str]:
    return sorted(REGISTRY)


# Exit-Achse (Roadmap 2.1, + 2.5 tp_mode) – wird in jede Familie gemischt,
# damit der bestehende Walk-Forward-Grid-Search automatisch auch Exit-Stile
# testet. Bewusst klein gehalten (kein Data-Dredging, s. Modul-Docstring): nur
# die Stil-Wahl + der eine ATR-Hyperparameter werden gesucht, alle übrigen
# neuen BacktestConfig-Felder (regime_*, soft_time_stop_*, atr_period,
# tp_reanchor_*) bleiben auf sicheren Fix-Defaults.
EXIT_PARAM_SPACE: Dict[str, List] = {
    "sl_mode":        ["fixed", "atr_trail", "regime"],
    "atr_mult":       [2.0, 2.5, 3.0],
    "time_stop_mode": ["hard", "soft"],
    "tp_mode":        ["fixed", "reanchor"],
}


# ── Baseline: die bestehende Swing-Mechanik (EMA21-Pullback + RSI + Volumen) ────
def _baseline_cfg(params: dict) -> BacktestConfig:
    # Nur bekannte BacktestConfig-Felder durchreichen (robust gegen Extra-Keys).
    valid = {f for f in BacktestConfig.__dataclass_fields__}
    return BacktestConfig(**{k: v for k, v in (params or {}).items() if k in valid})


def _baseline_swing_runner(df: pd.DataFrame, ticker: str, params: dict) -> List[Trade]:
    return _engine_run(df, ticker, _baseline_cfg(params))


def _baseline_fires_today(df: pd.DataFrame, params: dict) -> bool:
    """Feuert die Engine-Entry-Flanke HEUTE (letzter Balken)? Gleiche prepare/_is_signal
    wie der Backtest (kausale Indikatoren), Entry erfolgt sowieso erst am nächsten Open –
    kein Look-Ahead. Macht den robusten Baseline-Swing für den Meta-Allokator handelbar."""
    cfg = _baseline_cfg(params)
    d = _engine_prepare(df.copy(), cfg)
    if len(d) < 2:
        return False
    return bool(_engine_is_signal(d, len(d) - 1, cfg))


register(Strategy(
    name="baseline_swing",
    description="EMA21-Pullback/Crossover + RSI-Fenster + Volumen (backtesting.engine)",
    runner=_baseline_swing_runner,
    signal=_baseline_fires_today,
    default_params={},
    # Sinnvoll begrenzter Raum – wird erst in Phase 3 (Walk-Forward) durchsucht.
    param_space={
        "sl_pct":   [0.05, 0.07, 0.10],
        "tp1_pct":  [0.10, 0.15, 0.20],
        "tp2_pct":  [0.25, 0.30, 0.40],
        "rsi_max":  [60.0, 65.0, 70.0],
        "max_hold_days": [30, 45, 60],
        **EXIT_PARAM_SPACE,
    },
))
