"""
Tests für die neuen Strategie-Familien (Roadmap 5.1): w52_high + gap_meanrev.

Kern-Zusagen: (1) w52_high feuert genau an der steigenden Flanke, wenn der
Kurs erstmals nah ans 52-Wochen-Hoch kommt, nicht an jedem Tag, an dem er
dort verharrt (verhindert Dauerfeuer). (2) gap_meanrev feuert bei einem
ausreichend großen Overnight-Gap-down NUR im Aufwärtstrend, ist aber (anders
als w52_high) KEIN Flanken-Trigger — jeder Gap-Tag ist ein eigenständiges
Signal. (3) _fires_today() (Heute-Abfrage für die Live-Naht) stimmt mit der
Backtest-Signallogik überein. Netzfrei, konstruierte Preisreihen.
"""
import numpy as np
import pandas as pd

from strategy_lab import get
from strategy_lab.families import (_gap_prepare, _gap_signal, _w52_prepare,
                                   _w52_signal)


def _flat_ohlcv(closes, start="2010-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="B")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "Open": closes, "High": closes * 1.001, "Low": closes * 0.999,
        "Close": closes, "Volume": np.full(len(closes), 2_000_000.0),
    }, index=idx)


# ── w52_high ──────────────────────────────────────────────────────────────────

def test_w52_signal_fires_only_on_rising_edge_near_high():
    # 260 Handelstage: erst flach bei 100, dann Rampe bis knapp unters Hoch,
    # dann mehrere Tage konstant nah am Hoch — Flanke darf nur EINMAL feuern.
    flat = [100.0] * 5
    ramp = list(np.linspace(100, 118, 250))     # Hoch = 118 (rolling max, geshiftet)
    plateau = [117.5, 117.6, 117.7]             # >=95% von 118 ≈ 112.1 -> alle "near"
    closes = flat + ramp + plateau
    df = _w52_prepare(_flat_ohlcv(closes), {"near_pct": 0.95})

    fires = [i for i in range(1, len(df)) if _w52_signal(df, i, {"near_pct": 0.95})]
    assert len(fires) >= 1
    # Nach dem ersten Feuern darf die Flanke nicht am unmittelbar folgenden
    # Tag erneut feuern, solange der Kurs weiter "near" bleibt.
    for a, b in zip(fires, fires[1:]):
        assert b > a + 1 or df["pct_of_high"].iloc[a] < 0.95 <= df["pct_of_high"].iloc[a + 1]


def test_w52_signal_false_far_from_high():
    closes = [100.0] * 5 + list(np.linspace(100, 150, 250)) + [80.0] * 5
    df = _w52_prepare(_flat_ohlcv(closes), {"near_pct": 0.95})
    assert _w52_signal(df, len(df) - 1, {"near_pct": 0.95}) is False


def test_w52_fires_today_matches_signal_on_last_bar():
    closes = [100.0] * 5 + list(np.linspace(100, 130, 255))
    df = _flat_ohlcv(closes)
    strat = get("w52_high")
    result = strat.signal(df, {"near_pct": 0.95})
    prepared = _w52_prepare(df.copy(), {"near_pct": 0.95}).dropna()
    expected = _w52_signal(prepared, len(prepared) - 1, {"near_pct": 0.95})
    assert result == expected


# ── gap_meanrev ───────────────────────────────────────────────────────────────

def test_gap_signal_fires_on_large_downward_gap_in_uptrend():
    n = 220
    closes = list(np.linspace(100, 140, n))       # klarer Aufwärtstrend -> über SMA200 möglich
    df = _flat_ohlcv(closes)
    df.loc[df.index[-1], "Open"] = df["Close"].iloc[-2] * 0.90   # -10% Gap
    df = _gap_prepare(df, {"trend_ma": 200, "gap_down_pct": -0.04})
    i = len(df) - 1
    assert df["gap_pct"].iloc[i] <= -0.04
    assert _gap_signal(df, i, {"trend_ma": 200, "gap_down_pct": -0.04}) is True


def test_gap_signal_false_when_gap_too_small():
    n = 220
    closes = list(np.linspace(100, 140, n))
    df = _flat_ohlcv(closes)
    df.loc[df.index[-1], "Open"] = df["Close"].iloc[-2] * 0.99    # nur -1% Gap
    df = _gap_prepare(df, {"trend_ma": 200, "gap_down_pct": -0.04})
    i = len(df) - 1
    assert _gap_signal(df, i, {"trend_ma": 200, "gap_down_pct": -0.04}) is False


def test_gap_signal_false_when_not_in_uptrend():
    n = 220
    closes = list(np.linspace(140, 100, n))        # Abwärtstrend -> unter SMA200
    df = _flat_ohlcv(closes)
    df.loc[df.index[-1], "Open"] = df["Close"].iloc[-2] * 0.90    # großer Gap, aber kein Uptrend
    df = _gap_prepare(df, {"trend_ma": 200, "gap_down_pct": -0.04})
    i = len(df) - 1
    assert _gap_signal(df, i, {"trend_ma": 200, "gap_down_pct": -0.04}) is False


def test_gap_signal_is_not_edge_triggered_fires_every_qualifying_day():
    n = 220
    closes = list(np.linspace(100, 140, n))
    df = _flat_ohlcv(closes)
    # zwei aufeinanderfolgende große Gap-Tage -> BEIDE müssen feuern (kein Flanken-Trigger)
    df.loc[df.index[-2], "Open"] = df["Close"].iloc[-3] * 0.90
    df.loc[df.index[-1], "Open"] = df["Close"].iloc[-2] * 0.90
    df = _gap_prepare(df, {"trend_ma": 200, "gap_down_pct": -0.04})
    params = {"trend_ma": 200, "gap_down_pct": -0.04}
    assert _gap_signal(df, len(df) - 2, params) is True
    assert _gap_signal(df, len(df) - 1, params) is True
