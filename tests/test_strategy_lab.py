"""
Tests für strategy_lab (Roadmap Phase 1) – Registry + Lab-Evaluierung.

Netzfrei: der Daten-Loader wird durch synthetische OHLCV-Reihen injiziert.
"""
import numpy as np
import pandas as pd
import pytest

from strategy_lab import get, all_names, register, Strategy
from strategy_lab import lab


def _synth_df(n=400, trend=0.0008, seed=0):
    """Synthetische OHLCV-Reihe mit leichtem Aufwärtstrend + Rauschen."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.015, n)
    close = 100 * np.cumprod(1 + rets)
    idx = pd.date_range("2010-01-01", periods=n, freq="B")
    high = close * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, n)))
    openp = close * (1 + rng.normal(0, 0.004, n))
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def test_registry_has_baseline():
    assert "baseline_swing" in all_names()
    s = get("baseline_swing")
    assert callable(s.runner)
    assert "sl_pct" in s.param_space


def test_families_registered():
    for fam in ("donchian_breakout", "rsi_meanrev", "ts_momentum"):
        assert fam in all_names()
        assert callable(get(fam).runner)
        assert get(fam).param_space  # nicht leer (für Phase 3)


def test_all_families_expose_exit_param_space():
    """Roadmap 2.1 (Exit-Lab): EXIT_PARAM_SPACE muss in JEDER Familie stecken,
    sonst durchsucht der Walk-Forward die neuen Exit-Stile nur teilweise."""
    for fam in ("baseline_swing", "donchian_breakout", "rsi_meanrev", "ts_momentum"):
        space = get(fam).param_space
        assert "sl_mode" in space, fam
        assert "atr_mult" in space, fam
        assert "time_stop_mode" in space, fam
        assert "tp_mode" in space, fam          # Roadmap 2.5 (TP-Nachführung)


@pytest.mark.parametrize("fam", ["baseline_swing", "donchian_breakout",
                                 "rsi_meanrev", "ts_momentum"])
def test_each_family_runs_without_error(fam):
    # Auf trendenden Daten laufen alle Familien crashfrei durch.
    loader = lambda ticker, years: _synth_df(n=600, seed=hash(ticker) % 1000)
    portfolio, per_ticker = lab.evaluate(fam, ["AAA", "BBB"], years=5, loader=loader)
    assert portfolio.ticker == "PORTFOLIO"
    # n_trades >= 0 und kein Crash reicht; manche Familien handeln auf Synthetik selten.
    assert portfolio.n_trades >= 0


def test_evaluate_with_injected_loader():
    loader = lambda ticker, years: _synth_df(seed=hash(ticker) % 1000)
    portfolio, per_ticker = lab.evaluate(
        "baseline_swing", ["AAA", "BBB", "CCC"], years=5, loader=loader)
    assert len(per_ticker) == 3
    assert portfolio.ticker == "PORTFOLIO"
    # Auf trendenden Daten sollte es überhaupt Trades geben.
    assert portfolio.n_trades > 0


def test_evaluate_skips_missing_data():
    # Loader gibt None / zu kurze Reihen -> Ticker wird übersprungen, kein Crash.
    def loader(ticker, years):
        if ticker == "GOOD":
            return _synth_df()
        return None
    portfolio, per_ticker = lab.evaluate(
        "baseline_swing", ["GOOD", "MISSING1", "MISSING2"], years=5, loader=loader)
    assert len(per_ticker) == 1  # nur GOOD


def test_save_and_load_card(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "_CARD_DIR", str(tmp_path))
    loader = lambda ticker, years: _synth_df()
    portfolio, _ = lab.evaluate("baseline_swing", ["AAA"], years=5, loader=loader)
    path = lab.save_card("baseline_swing", portfolio, {"universe_size": 1})
    assert path
    card = lab.load_card("baseline_swing")
    assert card["strategy"] == "baseline_swing"
    assert "portfolio" in card and "meta" in card


def test_custom_strategy_registration():
    register(Strategy(name="_noop_test", description="liefert keine Trades",
                      runner=lambda df, t, p: []))
    portfolio, per_ticker = lab.evaluate(
        "_noop_test", ["AAA"], years=5, loader=lambda t, y: _synth_df())
    assert portfolio.n_trades == 0
