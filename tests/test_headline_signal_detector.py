"""
Tests für analyzers/headline_signal_detector.py — Regex-Basis + die neue
LLM-Zweitstufe (Roadmap 6.9f). Netzfrei (_fetch_articles nicht involviert,
_classify()/_classify_llm() werden direkt aufgerufen).
"""
from analyzers.headline_signal_detector import HeadlineSignalDetector
from analyzers.ollama_prescreener import PrescreenResult


class _StubPrescreener:
    def __init__(self, result: PrescreenResult):
        self._result = result
        self.calls = []

    def prescreen(self, ticker, news_items, has_open_position=False):
        self.calls.append((ticker, news_items))
        return self._result


def _bullish(score=0.8):
    return PrescreenResult(
        score=score, direction="BULLISH", confidence="HIGH", reason="x",
        send_to_claude=True, skip_reason="", ollama_used=True, latency_ms=10,
    )


def _bearish(score=0.2):
    return PrescreenResult(
        score=score, direction="BEARISH", confidence="HIGH", reason="x",
        send_to_claude=False, skip_reason="bearish", ollama_used=True, latency_ms=10,
    )


def _neutral(score=0.5):
    return PrescreenResult(
        score=score, direction="NEUTRAL", confidence="LOW", reason="x",
        send_to_claude=False, skip_reason="neutral", ollama_used=True, latency_ms=10,
    )


def _offline():
    return PrescreenResult(
        score=0.5, direction="NEUTRAL", confidence="LOW", reason="Ollama offline",
        send_to_claude=True, skip_reason="", ollama_used=False, latency_ms=0,
    )


# ── Bestehendes Regex-Verhalten bleibt unverändert ───────────────────────────

def test_regex_match_wins_without_llm_prescreener():
    det = HeadlineSignalDetector()
    sig = det._classify({"title": "$ACME to be acquired by BigCorp", "summary": ""})
    assert sig is not None
    assert sig.signal_type == "ACQUISITION"
    assert sig.ticker == "ACME"


def test_no_match_and_no_llm_prescreener_returns_none():
    det = HeadlineSignalDetector()
    sig = det._classify({"title": "$XYZ opens a new office", "summary": ""})
    assert sig is None


def test_regex_match_does_not_consult_llm_even_if_configured():
    stub = _StubPrescreener(_bullish())
    det = HeadlineSignalDetector(llm_prescreener=stub)
    sig = det._classify({"title": "$ACME to be acquired by BigCorp", "summary": ""})
    assert sig.signal_type == "ACQUISITION"
    assert stub.calls == []


# ── Neue LLM-Zweitstufe (6.9f) ────────────────────────────────────────────────

def test_llm_fallback_used_when_regex_finds_nothing():
    stub = _StubPrescreener(_bullish(0.82))
    det = HeadlineSignalDetector(llm_prescreener=stub)
    sig = det._classify({"title": "$XYZ opens a new office", "summary": "expansion news"})
    assert sig is not None
    assert sig.signal_type == "LLM_SCORED"
    assert sig.ticker == "XYZ"
    assert sig.score == 0.82
    assert len(stub.calls) == 1


def test_llm_bearish_yields_no_signal():
    stub = _StubPrescreener(_bearish())
    det = HeadlineSignalDetector(llm_prescreener=stub)
    sig = det._classify({"title": "$XYZ opens a new office", "summary": ""})
    assert sig is None


def test_llm_neutral_yields_no_signal():
    stub = _StubPrescreener(_neutral())
    det = HeadlineSignalDetector(llm_prescreener=stub)
    sig = det._classify({"title": "$XYZ opens a new office", "summary": ""})
    assert sig is None


def test_llm_offline_yields_no_signal_not_a_fake_one():
    stub = _StubPrescreener(_offline())
    det = HeadlineSignalDetector(llm_prescreener=stub)
    sig = det._classify({"title": "$XYZ opens a new office", "summary": ""})
    assert sig is None


def test_llm_not_consulted_when_ticker_extraction_fails():
    stub = _StubPrescreener(_bullish())
    det = HeadlineSignalDetector(llm_prescreener=stub)
    sig = det._classify({"title": "market update for the week ahead", "summary": ""})
    assert sig is None
    assert stub.calls == []
