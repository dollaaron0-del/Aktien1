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
from typing import Callable, Dict, List

import pandas as pd

from backtesting.engine import BacktestConfig, Trade, run as _engine_run


@dataclass
class Strategy:
    name: str
    description: str
    runner: Callable[[pd.DataFrame, str, dict], List[Trade]]
    default_params: Dict = field(default_factory=dict)
    # Parameter-Raum für Phase 2/3 (Name → Liste von Kandidatenwerten).
    param_space: Dict[str, List] = field(default_factory=dict)


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


# ── Baseline: die bestehende Swing-Mechanik (EMA21-Pullback + RSI + Volumen) ────
def _baseline_swing_runner(df: pd.DataFrame, ticker: str, params: dict) -> List[Trade]:
    # Nur bekannte BacktestConfig-Felder durchreichen (robust gegen Extra-Keys).
    valid = {f for f in BacktestConfig.__dataclass_fields__}
    cfg = BacktestConfig(**{k: v for k, v in (params or {}).items() if k in valid})
    return _engine_run(df, ticker, cfg)


register(Strategy(
    name="baseline_swing",
    description="EMA21-Pullback/Crossover + RSI-Fenster + Volumen (backtesting.engine)",
    runner=_baseline_swing_runner,
    default_params={},
    # Sinnvoll begrenzter Raum – wird erst in Phase 3 (Walk-Forward) durchsucht.
    param_space={
        "sl_pct":   [0.05, 0.07, 0.10],
        "tp1_pct":  [0.10, 0.15, 0.20],
        "tp2_pct":  [0.25, 0.30, 0.40],
        "rsi_max":  [60.0, 65.0, 70.0],
        "max_hold_days": [30, 45, 60],
    },
))
