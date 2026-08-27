"""
Google-Trends-Signal im earnings_predictor: der (inoffizielle) pytrends-
Endpoint wird von Google praktisch dauerhaft mit HTTP 429 gedrosselt. Ohne
Bremse gab das pro analysiertem Ticker eine sinnlose Anfrage + WARNING-Log,
obwohl das Signal ohnehin immer auf neutral 0.5 zurückfällt (27.8.2026).

Nach einem 429 setzt der Fetch prozessweit für _TRENDS_COOLDOWN_S aus.
"""
import time

import pytest

import analyzers.earnings_predictor as ep_mod
from analyzers.earnings_predictor import EarningsPredictor


@pytest.fixture(autouse=True)
def _reset_cooldown():
    EarningsPredictor._trends_blocked_until = 0.0
    yield
    EarningsPredictor._trends_blocked_until = 0.0


def _predictor(monkeypatch):
    monkeypatch.setattr(EarningsPredictor, "__init__", lambda self: None)
    return EarningsPredictor()


def test_429_sets_process_wide_cooldown(monkeypatch):
    calls = []

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def build_payload(self, *a, **k):
            calls.append(1)
            raise RuntimeError("The request failed: Google returned a response with code 429")

    import pytrends.request as ptr
    monkeypatch.setattr(ptr, "TrendReq", _Boom)

    p = _predictor(monkeypatch)
    score, info = p._signal_google_trends("AAPL")
    assert score == 0.5
    assert "rate-limited" in info["note"]
    assert EarningsPredictor._trends_blocked_until > time.monotonic()

    # Zweiter Ticker im selben Zyklus: gar keine Anfrage mehr
    score2, info2 = p._signal_google_trends("MSFT")
    assert score2 == 0.5
    assert len(calls) == 1, "nach 429 darf kein weiterer pytrends-Call rausgehen"


def test_non_429_error_still_warns_and_does_not_block(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def build_payload(self, *a, **k):
            raise RuntimeError("connection reset")

    import pytrends.request as ptr
    monkeypatch.setattr(ptr, "TrendReq", _Boom)

    p = _predictor(monkeypatch)
    score, info = p._signal_google_trends("AAPL")
    assert score == 0.5
    assert "error" in info
    assert EarningsPredictor._trends_blocked_until == 0.0
