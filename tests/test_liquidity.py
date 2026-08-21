"""
Tests für analyzers/liquidity.py – Markt-Liquiditäts-Gate.
"""
import analyzers.liquidity as liq
from analyzers.liquidity import check_liquidity, LiquidityResult


def setup_function(_):
    liq._cache.clear()
    liq._ENABLED = True


def test_crypto_exempt():
    r = check_liquidity("BTC/USD", 60000)
    assert r.ok is True
    assert "Krypto" in r.reason


def test_disabled_gate_passes(monkeypatch):
    monkeypatch.setattr(liq, "_ENABLED", False)
    r = check_liquidity("ILLIQ", 5.0)
    assert r.ok is True


def test_illiquid_blocked(monkeypatch):
    monkeypatch.setattr(liq, "_MIN_ADV_USD", 1_000_000)
    monkeypatch.setattr(liq, "_measure",
                        lambda t, p: LiquidityResult(False, 50_000, 10_000, "Zu illiquide"))
    r = check_liquidity("ILLIQ", 5.0)
    assert r.ok is False


def test_liquid_passes(monkeypatch):
    monkeypatch.setattr(liq, "_measure",
                        lambda t, p: LiquidityResult(True, 5_000_000, 100_000, "Liquide"))
    r = check_liquidity("AAPL", 150.0)
    assert r.ok is True


def test_result_cached(monkeypatch):
    calls = {"n": 0}

    def _m(t, p):
        calls["n"] += 1
        return LiquidityResult(True, 9_000_000, 1, "ok")
    monkeypatch.setattr(liq, "_measure", _m)
    check_liquidity("AAPL", 150.0)
    check_liquidity("AAPL", 150.0)
    assert calls["n"] == 1                       # zweiter Aufruf aus Cache


def test_measure_failopen(monkeypatch):
    # _measure fängt yfinance-Fehler ab und gibt ok=True (fail-open)
    import builtins
    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "yfinance":
            raise RuntimeError("kein Netz")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _boom)
    r = liq._measure("AAPL", None)
    assert r.ok is True
    assert "unbekannt" in r.reason.lower()
