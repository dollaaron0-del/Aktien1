"""
Tests für Roadmap 2.1 (Exit-Lab) — parametrisierbare Exits in backtesting.engine
(ATR-Trailing-SL, regime-abhängige SL/TP-Skalierung, Soft-Time-Stop-Variante),
zusätzlich zum bestehenden festen %-SL/TP/Time-Stop.

test_default_config_golden_* pinnt die exakten Trade-Felder für
BacktestConfig()-Defaults auf deterministischer Synthetik — VOR dem Umbau
aufgenommen (git log 2026-07-12). Der Refactor (_simulate → dünner Wrapper
um _run_exit_loop) darf am 'fixed'-Pfad NICHTS verändern; diese Werte sind
das Regressionsnetz dafür.
"""
import numpy as np
import pandas as pd
import pytest

from backtesting.engine import BacktestConfig, _simulate, _entry_regime


def _synth_df(n=200, seed=42, trend=0.0006, noise=0.015):
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, noise, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range("2020-01-02", periods=n, freq="B")
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    openp = close * (1 + rng.normal(0, 0.003, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def _ramp_df(n=60, daily=0.015, base=100.0, start="2022-01-03"):
    """Monotoner Pfad; Open=High=Low=Close für präzise TP/SL-Kontrolle
    (gleiches Muster wie tests/test_paper_forward.py::_ramp_df)."""
    price = base * (1 + daily) ** np.arange(n)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame({"Open": price, "High": price, "Low": price,
                         "Close": price, "Volume": np.full(n, 1e6)}, index=idx)


# ── Golden-Pins: 'fixed'-Pfad (Default) unverändert seit vor dem Umbau ──────────

def test_default_config_golden_sl():
    df = _synth_df()
    trade = _simulate(df, 50, BacktestConfig(), "TEST")
    assert trade.entry_price == pytest.approx(111.5705771654219)
    assert trade.exit_price == pytest.approx(103.76063676384236)
    assert trade.return_pct == pytest.approx(-0.07)
    assert trade.exit_reason == "SL"
    assert (trade.tp1_hit, trade.tp2_hit) == (False, False)
    assert str(trade.exit_date.date()) == "2020-05-14"


def test_default_config_golden_time_stop_no_tp():
    df = _synth_df()
    trade = _simulate(df, 150, BacktestConfig(), "TEST")
    assert trade.entry_price == pytest.approx(94.76044047412691)
    assert trade.exit_price == pytest.approx(101.80984592140408)
    assert trade.return_pct == pytest.approx(0.07439186027424505)
    assert trade.exit_reason == "time_stop"
    assert (trade.tp1_hit, trade.tp2_hit) == (False, False)


def test_default_config_golden_time_stop_tp1_only():
    df = _synth_df(trend=0.004, seed=7)
    trade = _simulate(df, 40, BacktestConfig(), "TEST")
    assert trade.return_pct == pytest.approx(0.17673585600724814)
    assert trade.exit_reason == "time_stop"
    assert (trade.tp1_hit, trade.tp2_hit) == (True, False)


def test_default_config_golden_time_stop_tp1_and_tp2():
    df = _synth_df(trend=0.006, seed=3, noise=0.01)
    trade = _simulate(df, 5, BacktestConfig(), "TEST")
    assert trade.return_pct == pytest.approx(0.283687, abs=1e-5)
    assert trade.exit_reason == "time_stop"
    assert (trade.tp1_hit, trade.tp2_hit) == (True, True)


def test_default_sl_return_is_exactly_minus_sl_pct():
    """sl_mode='fixed' (Default): SL-Return ist exakt -sl_pct, keine
    Gleitkomma-Drift durch eine abgeleitete Formel (sl_price/entry-1)."""
    df = _ramp_df(n=30, daily=-0.02)
    cfg = BacktestConfig()
    trade = _simulate(df, 0, cfg, "TEST")
    assert trade.exit_reason == "SL"
    assert trade.return_pct == -cfg.sl_pct


# ── ATR-Trailing-Stop ────────────────────────────────────────────────────────

def _up_then_crash_df(n_up=30, n_down=30, up_daily=0.02, down_daily=0.10):
    up = 100.0 * (1 + up_daily) ** np.arange(n_up)
    down = up[-1] * (1 - down_daily) ** np.arange(1, n_down + 1)
    price = np.concatenate([up, down])
    idx = pd.date_range("2022-01-03", periods=len(price), freq="B")
    return pd.DataFrame({"Open": price, "High": price * 1.001, "Low": price * 0.999,
                         "Close": price, "Volume": np.full(len(price), 1e6)}, index=idx)


def test_atr_trailing_locks_in_more_profit_than_fixed_on_reversal():
    """Der diagnostizierte Fehlerfall (Roadmap 2.1): Rallye, dann scharfer
    Rückschlag. Fixer %-SL gibt fast alles wieder her; der ATR-Trail zieht
    früher raus und behält deutlich mehr Gewinn."""
    df = _up_then_crash_df()
    t_fixed = _simulate(df, 0, BacktestConfig(sl_mode="fixed", max_hold_days=55), "X")
    t_atr = _simulate(df, 0, BacktestConfig(sl_mode="atr_trail", atr_mult=2.5, max_hold_days=55), "X")
    assert t_atr.exit_reason == "SL_trail"
    assert t_atr.return_pct > 0
    assert t_atr.return_pct > t_fixed.return_pct


def test_atr_trailing_stop_never_moves_down():
    """Up / flacher Dip / neues Hoch / Crash: der SL darf beim (innerhalb der
    ATR-Trail-Distanz liegenden) Dip NICHT auslösen und muss danach vom
    HÖHEREN zweiten Hoch aus getriggert werden, nicht vom ersten."""
    seg1 = 100.0 * 1.02 ** np.arange(25)
    seg2 = seg1[-1] * 0.995 ** np.arange(1, 3)     # flacher, kurzer Dip (~1%, < ATR-Trail-Abstand)
    seg3 = seg2[-1] * 1.02 ** np.arange(1, 21)     # neues, deutlich höheres Hoch
    seg4 = seg3[-1] * 0.90 ** np.arange(1, 20)     # Crash
    price = np.concatenate([seg1, seg2, seg3, seg4])
    idx = pd.date_range("2022-01-03", periods=len(price), freq="B")
    df = pd.DataFrame({"Open": price, "High": price * 1.001, "Low": price * 0.999,
                       "Close": price, "Volume": np.full(len(price), 1e6)}, index=idx)
    cfg = BacktestConfig(sl_mode="atr_trail", atr_mult=2.5, max_hold_days=70)
    trade = _simulate(df, 0, cfg, "X")
    assert trade.exit_reason == "SL_trail"
    # Exit muss NACH dem zweiten (höheren) Hoch liegen, nicht beim ersten Dip.
    assert trade.exit_date > idx[len(seg1) + len(seg2)]
    assert trade.return_pct > 0.50   # profitiert vom höheren zweiten Peak, nicht nur vom ersten


# ── Regime-abhängige Stops ───────────────────────────────────────────────────

def test_entry_regime_classifies_up_low_and_down_high():
    up_flat = _ramp_df(n=60, daily=0.001)
    assert _entry_regime(up_flat, 59, BacktestConfig()) == "UP_LOW"

    rng = np.random.default_rng(1)
    n = 60
    rets = rng.normal(-0.01, 0.03, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    down_vol = pd.DataFrame({"Open": close, "High": close, "Low": close,
                             "Close": close, "Volume": np.full(n, 1e6)}, index=idx)
    assert _entry_regime(down_vol, 59, BacktestConfig()) == "DOWN_HIGH"


def test_entry_regime_is_causal_no_lookahead():
    """Bars NACH signal_idx dürfen die Klassifikation nicht beeinflussen."""
    df = _ramp_df(n=60, daily=0.001)
    df2 = df.copy()
    df2.iloc[40:, df2.columns.get_loc("Close")] = 99_999.0
    assert _entry_regime(df, 30, BacktestConfig()) == _entry_regime(df2, 30, BacktestConfig())


def test_regime_sl_mult_widens_stop_and_survives_where_default_stops_out():
    """UP_LOW-Entry, dann ein Rückschlag von genau -sl_pct direkt nach Entry:
    mult=1.0 (Default) stoppt aus, mult=2.0 übersteht denselben Rückschlag."""
    up = 100.0 * 1.001 ** np.arange(60)
    drop = up[-1] * (1 - 0.07)
    price = np.concatenate([up, [drop], [up[-1]] * 5])
    idx = pd.date_range("2022-01-03", periods=len(price), freq="B")
    df = pd.DataFrame({"Open": price, "High": price, "Low": price,
                       "Close": price, "Volume": np.full(len(price), 1e6)}, index=idx)

    t_default = _simulate(df, 58, BacktestConfig(sl_mode="regime", max_hold_days=10), "X")
    t_wide = _simulate(df, 58, BacktestConfig(sl_mode="regime", regime_sl_mult_up_low=2.0,
                                              max_hold_days=10), "X")
    assert t_default.exit_reason == "SL"
    assert t_wide.exit_reason != "SL"


# ── Soft-Time-Stop-Variante ──────────────────────────────────────────────────

def test_soft_time_stop_extends_when_profitable_at_boundary():
    df = _ramp_df(n=80, daily=0.004)
    cfg_hard = BacktestConfig(sl_mode="fixed", time_stop_mode="hard", max_hold_days=20,
                              tp1_pct=100.0, tp2_pct=200.0)   # TP effektiv deaktiviert
    cfg_soft = BacktestConfig(sl_mode="fixed", time_stop_mode="soft", max_hold_days=20,
                              soft_time_stop_min_gain=0.01, soft_time_stop_extension_days=10,
                              tp1_pct=100.0, tp2_pct=200.0)
    t_hard = _simulate(df, 0, cfg_hard, "X")
    t_soft = _simulate(df, 0, cfg_soft, "X")
    assert t_soft.exit_date > t_hard.exit_date
    assert t_soft.return_pct > t_hard.return_pct


def test_soft_time_stop_does_not_extend_when_flat():
    flat = np.concatenate([100.0 * 1.001 ** np.arange(20), [100.0 * 1.001 ** 19] * 10])
    idx = pd.date_range("2022-01-03", periods=len(flat), freq="B")
    df = pd.DataFrame({"Open": flat, "High": flat, "Low": flat, "Close": flat,
                       "Volume": np.full(len(flat), 1e6)}, index=idx)
    cfg_hard = BacktestConfig(sl_mode="fixed", time_stop_mode="hard", max_hold_days=15,
                              tp1_pct=100.0, tp2_pct=200.0)
    cfg_soft = BacktestConfig(sl_mode="fixed", time_stop_mode="soft", max_hold_days=15,
                              soft_time_stop_min_gain=0.05, tp1_pct=100.0, tp2_pct=200.0)
    t_hard = _simulate(df, 0, cfg_hard, "X")
    t_soft = _simulate(df, 0, cfg_soft, "X")
    assert t_soft.exit_date == t_hard.exit_date   # kein Gewinn genug -> keine Verlängerung


def test_soft_time_stop_extends_only_once():
    df = _ramp_df(n=200, daily=0.004, base=50.0)
    cfg = BacktestConfig(sl_mode="fixed", time_stop_mode="soft", max_hold_days=20,
                         soft_time_stop_min_gain=0.01, soft_time_stop_extension_days=10,
                         tp1_pct=100.0, tp2_pct=200.0)
    trade = _simulate(df, 0, cfg, "X")
    # Entry ist Bar 1; genau EINE Verlängerung -> Exit bei Bar 1+20+10=31, nicht später.
    assert trade.exit_date == df.index[31]
