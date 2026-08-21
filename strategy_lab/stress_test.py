"""
Stress-Test-Harness (Roadmap 2.3, Teil A) – 2008/2020/2022 durch die
bestehende Portfolio-Backtest-Maschinerie (Roadmap 2.2) jagen.

Bewusst KEIN neuer Simulationscode: `run_portfolio_backtest()` bleibt
unverändert, dieses Modul liefert nur einen Loader-Wrapper, der auf ein
festes historisches Krisenfenster zuschneidet, plus benannte Fenster.
Zielt auf die mechanische strategy_lab-Pipeline (deterministisch,
reproduzierbar), NICHT auf den echten Live-Pfad mit Claude-Aufrufen – der
braucht Echtzeit-News/Sentiment, die für 2008 schlicht nicht existieren.

EHRLICHER HINWEIS (s. strategy_lab/universe.py-Kopf): yfinance purgt fast
alle 2008-Pleiten (Lehman, WaMu, Bear Stearns, …) – ein GFC_2008-Lauf über
das heutige Universum läuft praktisch nur auf Überlebenden und unterschätzt
damit den echten Drawdown. Ticker, die im Fenster noch nicht existierten
(z.B. TSLA/META vor ihrem IPO), fallen über die Datumszuschneidung von
selbst raus (leere/zu kurze Slice → None).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import pandas as pd

from backtesting import data_loader
from strategy_lab.portfolio_backtest import PortfolioBacktestResult, run_portfolio_backtest

CRISIS_WINDOWS: Dict[str, Tuple[str, str]] = {
    "GFC_2008":   ("2007-10-01", "2009-06-01"),
    "COVID_2020": ("2020-02-01", "2020-06-01"),
    "BEAR_2022":  ("2022-01-01", "2022-12-01"),
}


def _crisis_loader(base_loader: Callable[[str, int], object], start: str, end: str):
    """Schneidet einen (ticker, years)->df-Loader auf [start, end] zu. Ticker
    ohne/zu wenig Daten im Fenster (existierten noch nicht, delisted, von
    yfinance gepurgt) liefern None – derselbe 'ok zu überspringen'-Vertrag,
    den run_portfolio_backtest._collect_trades bereits kennt."""
    def _loader(ticker: str, years: int):
        df = base_loader(ticker, years)
        if df is None:
            return None
        sub = df.loc[start:end]
        return sub if len(sub) >= 60 else None
    return _loader


def run_stress_test(
    window_name: str,
    plan: List[Dict],
    universe: List[str],
    loader: Callable[[str, int], object] = data_loader.load,
    **portfolio_kwargs,
) -> PortfolioBacktestResult:
    """Simuliert den `plan` (weight_plan()-förmig) über ein benanntes
    historisches Krisenfenster statt der üblichen Trailing-Jahre. Unbekannter
    Fenstername -> KeyError (kein stilles No-Op)."""
    start, end = CRISIS_WINDOWS[window_name]
    # Genug Trailing-Historie, dass der Basis-Loader (trailing-N-Jahre-ab-heute)
    # das Krisenfenster überhaupt einschließt, plus Puffer.
    years_needed = (pd.Timestamp.now() - pd.Timestamp(start)).days // 365 + 2
    return run_portfolio_backtest(
        plan, universe, years=years_needed,
        loader=_crisis_loader(loader, start, end),
        **portfolio_kwargs,
    )
