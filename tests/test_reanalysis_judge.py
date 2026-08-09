"""
Tests für analyzers/reanalysis_judge.py (Roadmap 6.9c: Re-Analyse-Studie).
Netzfrei — prescreener.generate() wird gestubbt.
"""
from analyzers.reanalysis_judge import (
    TAXONOMY, build_judge_prompt, judge_decision, _parse_judge_response,
)


class _StubPrescreener:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def generate(self, prompt, max_tokens=300):
        self.calls.append((prompt, max_tokens))
        return self._response


def test_build_prompt_includes_key_fields():
    p = build_judge_prompt(
        "AAPL", "BUY", "BULLISH", "HIGH",
        ["Product launch"], ["Supply chain risk"], "LOSS", -3.5,
    )
    assert "AAPL" in p
    assert "BUY" in p
    assert "Product launch" in p
    assert "Supply chain risk" in p
    assert "LOSS" in p
    assert "-3.50%" in p


def test_build_prompt_handles_empty_lists():
    p = build_judge_prompt("AAPL", "BUY", "BULLISH", "HIGH", [], [], "WIN", 2.0)
    assert "(keine genannt)" in p


def test_parse_direct_json():
    result = _parse_judge_response('{"category": "korrekt", "reason": "matched"}')
    assert result == {"category": "KORREKT", "reason": "matched"}


def test_parse_json_embedded_in_text():
    raw = 'Sure, here is my judgement:\n{"category": "PECH", "reason": "bad luck"}\nDone.'
    result = _parse_judge_response(raw)
    assert result["category"] == "PECH"


def test_parse_invalid_category_returns_none():
    result = _parse_judge_response('{"category": "MADE_UP", "reason": "x"}')
    assert result is None


def test_parse_garbage_returns_none():
    assert _parse_judge_response("not json at all") is None


def test_judge_decision_returns_parsed_result():
    stub = _StubPrescreener('{"category": "RISIKO_UEBERSEHEN", "reason": "missed recall risk"}')
    result = judge_decision(
        stub, "AAPL", "BUY", "BULLISH", "HIGH",
        ["Earnings beat"], [], "LOSS", -4.2,
    )
    assert result["category"] == "RISIKO_UEBERSEHEN"
    assert len(stub.calls) == 1


def test_judge_decision_none_on_ollama_failure():
    stub = _StubPrescreener(None)
    result = judge_decision(
        stub, "AAPL", "BUY", "BULLISH", "HIGH", [], [], "LOSS", -1.0,
    )
    assert result is None


def test_judge_decision_none_on_unparsable_response():
    stub = _StubPrescreener("I cannot decide.")
    result = judge_decision(
        stub, "AAPL", "BUY", "BULLISH", "HIGH", [], [], "LOSS", -1.0,
    )
    assert result is None


def test_taxonomy_has_five_categories():
    assert len(TAXONOMY) == 5
    assert "UNKLAR" in TAXONOMY
