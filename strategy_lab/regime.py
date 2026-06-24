"""
Regime-Tagging (Roadmap Phase 4-Rest) – "in welchem Markt funktioniert es?".

Jedes Walk-Forward-Testfenster bekommt ein Markt-Regime-Label aus dem
aggregierten Universums-Verhalten *in genau diesem Fenster* (kein externer
Index nötig, netzfrei): Trend (BULL/BEAR/SIDE) × Volatilität (CALM/VOLATILE).

Damit lässt sich die OOS-Robustheit nach Regime aufschlüsseln: Eine Strategie,
die nur in BULL_CALM trägt, ist etwas anderes als eine, die über mehrere Regime
positiv bleibt. Der spätere (regime-bedingte) Meta-Allokator kann das nutzen.

Rein deterministisch, numpy/pandas, kein Look-Ahead (klassifiziert nur das
bereits abgeschlossene Fenster zu Auswertungszwecken).
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

# Schwellen auf annualisierter Basis (fenstergrößen-unabhängig). Bewusst
# konservativ; per Aufruf justierbar.
_BULL_ANN_RET = 0.10     # > +10 %/Jahr → Aufwärtsregime (nur wenn auch Trend sauber)
_BEAR_ANN_RET = -0.05    # < −5 %/Jahr → Abwärtsregime
_VOL_ANN_THR = 0.25      # > 25 % annualisierte Vola → "volatil"
_TRADING_DAYS = 252.0
# Intra-Fenster-Schärfung: ein Fenster ist nur dann ein sauberer Bulle, wenn es
# auch die meiste ZEIT über seiner Trendlinie lag (sonst frisst ein Endpunkt-Hoch
# einen zwischenzeitlichen Crash auf — genau die 2020–22-Falle).
_DOWNTREND_BULL_MAX = 0.45   # >45% der Tage unter Trend → kein sauberer BULL mehr
_DOWNTREND_BEAR_MIN = 0.65   # >65% der Tage unter Trend → BEAR (auch bei netto ~0)
_DRAWDOWN_VOL_THR = -0.20    # Intra-Fenster-MaxDD < −20% → "volatil" (Stress sichtbar)
_TREND_SPAN = 50             # EWM-Spanne der Trendlinie (≈ 50 Handelstage)


def _aggregate_returns(dfs: Dict[str, pd.DataFrame]) -> pd.Series:
    """Gleichgewichteter Tages-Return über alle Ticker im Fenster (Proxy für das
    Universums-Regime). Leere/zu kurze Reihen werden ignoriert."""
    series: List[pd.Series] = []
    for df in dfs.values():
        if df is None or len(df) < 5 or "Close" not in df:
            continue
        series.append(df["Close"].pct_change())
    if not series:
        return pd.Series(dtype=float)
    mat = pd.concat(series, axis=1)
    return mat.mean(axis=1).dropna()


def _intra_window_shape(rets: pd.Series) -> tuple:
    """Form des Fensters JENSEITS der Netto-Rendite: (downtrend_frac, max_drawdown).

    downtrend_frac = Anteil der Tage, an denen der gleichgewichtete Index unter
    seiner eigenen EWM-Trendlinie lag (Maß für sustained Schwäche; ein Endpunkt-Hoch
    kann das nicht kaschieren). max_drawdown = tiefster Peak-to-Trough-Einbruch im
    Fenster (≤0). Beide machen einen zwischenzeitlichen Crash sichtbar, den die
    Start-zu-Ende-Rendite verschluckt."""
    equity = (1.0 + rets).cumprod()
    trend = equity.ewm(span=_TREND_SPAN, min_periods=1).mean()
    downtrend_frac = float((equity < trend).mean())
    max_dd = float((equity / equity.cummax() - 1.0).min())
    return downtrend_frac, max_dd


def classify_window(
    dfs: Dict[str, pd.DataFrame],
    bull_ann_ret: float = _BULL_ANN_RET,
    bear_ann_ret: float = _BEAR_ANN_RET,
    vol_ann_thr: float = _VOL_ANN_THR,
) -> str:
    """Regime-Label des Fensters: '<TREND>_<VOL>' (z.B. 'BULL_CALM').
    'UNKNOWN' bei zu wenig Daten – fail-open, blockiert nie.

    Geschärft: der Trend zählt nur dann als BULL, wenn das Fenster auch die meiste
    ZEIT über seiner Trendlinie lag; ein crashdurchzogenes Fenster, das per Endpunkt
    netto positiv ist, kippt auf SIDE/BEAR. Volatilität greift zusätzlich bei tiefem
    Intra-Fenster-Drawdown (Stress), nicht nur bei hoher Tages-Vola."""
    rets = _aggregate_returns(dfs)
    if len(rets) < 20:
        return "UNKNOWN"
    n = len(rets)
    ann_ret = float((1.0 + rets).prod() ** (_TRADING_DAYS / n) - 1.0)
    ann_vol = float(rets.std(ddof=0) * np.sqrt(_TRADING_DAYS))
    downtrend_frac, max_dd = _intra_window_shape(rets)

    if ann_ret < bear_ann_ret or downtrend_frac > _DOWNTREND_BEAR_MIN:
        trend = "BEAR"
    elif ann_ret > bull_ann_ret and downtrend_frac < _DOWNTREND_BULL_MAX:
        trend = "BULL"
    else:
        trend = "SIDE"   # netto-positiv-aber-zerklüftet ODER flach
    vol = "VOLATILE" if (ann_vol > vol_ann_thr or max_dd < _DRAWDOWN_VOL_THR) else "CALM"
    return f"{trend}_{vol}"


def regime_breakdown(windows) -> Dict[str, Dict]:
    """Schlüsselt OOS-Ergebnisse nach Regime auf: je Label n, Median-Test-Return
    und %-positive Fenster. `windows` = Liste mit .regime und .test_return."""
    from collections import defaultdict
    import statistics as st

    by: Dict[str, List[float]] = defaultdict(list)
    for w in windows:
        label = getattr(w, "regime", "") or "UNKNOWN"
        by[label].append(w.test_return)
    out: Dict[str, Dict] = {}
    for label, rets in by.items():
        out[label] = {
            "n": len(rets),
            "median_test_return": round(st.median(rets), 4),
            "pct_positive": round(sum(1 for r in rets if r > 0) / len(rets), 3),
        }
    return out


def robust_regimes(breakdown: Dict[str, Dict], min_n: int = 2) -> List[str]:
    """Regime, in denen die Strategie OOS positiv UND mehrfach getestet ist
    (Median-Test-Return > 0 bei mind. min_n Fenstern)."""
    return sorted(
        label for label, s in breakdown.items()
        if s["n"] >= min_n and s["median_test_return"] > 0
    )
