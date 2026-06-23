"""
Tests für strategy_lab.universe (Survivorship-Bias mildern, Roadmap b).

Netzfrei: der Loader wird injiziert; ein Fake liefert nur für bestimmte Ticker
„Daten“ (DataFrame mit genug Zeilen), für den Rest None (= delistet ohne Historie).
"""
import pandas as pd

from strategy_lab.universe import (
    GRAVEYARD, graveyard_tickers, extended_universe, coverage, build,
    UniverseInfo,
)


def _fake_loader(have_data):
    """Loader, der für Ticker in `have_data` ein DataFrame liefert, sonst None."""
    have = {t.upper() for t in have_data}

    def _load(ticker, years):
        if ticker.upper() in have:
            return pd.DataFrame({"Close": list(range(100))})  # 100 Zeilen ≥ 60
        return None
    return _load


def test_graveyard_nonempty_and_unique():
    tickers = graveyard_tickers()
    assert len(tickers) >= 20                       # breite Stichprobe
    assert len(tickers) == len(set(tickers))        # keine Dubletten
    assert all(isinstance(d.year, int) for d in GRAVEYARD)


def test_extended_universe_appends_and_dedups():
    base = ["AAPL", "MSFT"]
    ext = extended_universe(base)
    assert ext[:2] == ["AAPL", "MSFT"]              # Survivors zuerst, Reihenfolge stabil
    assert "LEHMQ" in ext                            # Graveyard angehängt
    assert len(ext) == len(set(ext))                 # dedupliziert


def test_extended_universe_dedups_overlap():
    # Falls ein Survivor zufällig auch im Graveyard stünde → nicht doppelt.
    base = ["AAPL", graveyard_tickers()[0]]
    ext = extended_universe(base)
    assert ext.count(graveyard_tickers()[0]) == 1


def test_coverage_flags_data_availability():
    gy = graveyard_tickers()
    alive = gy[:3]
    cov = coverage(gy, _fake_loader(alive), years=20)
    assert all(cov[t] for t in alive)
    assert not cov[gy[3]]                             # kein Fake-Datensatz → False


def test_build_without_delisted_is_survivors_only():
    base = ["AAPL", "MSFT"]
    info = build(base, include_delisted=False)
    assert info.tickers == ["AAPL", "MSFT"]
    assert info.graveyard_requested == 0
    assert "NICHT gemildert" in info.survivorship_note


def test_build_with_delisted_drops_dataless_graveyard():
    base = ["AAPL"]
    gy = graveyard_tickers()
    alive = gy[:2]
    info = build(base, include_delisted=True,
                 loader=_fake_loader(["AAPL"] + alive), years=20)
    # Survivor bleibt, nur die zwei Graveyard-Titel MIT Daten kommen rein.
    assert "AAPL" in info.tickers
    assert set(alive).issubset(set(info.tickers))
    assert gy[5] not in info.tickers                 # ohne Daten → rausgeworfen
    assert info.graveyard_with_data == 2
    assert info.graveyard_requested == len(gy)
    assert "gemildert" in info.survivorship_note


def test_build_with_delisted_no_loader_keeps_all():
    # Ohne Loader wird nicht geladen → volle Liste, Abdeckung unbekannt (0).
    base = ["AAPL"]
    info = build(base, include_delisted=True, loader=None)
    assert "LEHMQ" in info.tickers
    assert info.graveyard_with_data == 0
    assert info.graveyard_requested == len(graveyard_tickers())
