"""
Tests für OllamaPrescreener.sample_consistency() (Roadmap 6.9e:
Selbst-Konsistenz-Ensemble). Netzfrei, requests.post gemockt.
"""
import json

import requests

from analyzers.ollama_prescreener import OllamaPrescreener

_NEWS = [{"title": "Foo beats estimates", "source": "wire", "published_at": "2026-08-09"}]


def _resp(payload: dict, status: int = 200):
    class _R:
        status_code = status

        def json(self):
            return payload
    return _R()


def _available(monkeypatch, prescreener):
    monkeypatch.setattr(prescreener, "is_available", lambda: True)


def test_samples_are_aggregated_with_majority_direction(monkeypatch):
    p = OllamaPrescreener(model="llama3.1:8b")
    _available(monkeypatch, p)
    outputs = [
        {"score": 0.6, "direction": "BULLISH", "confidence": "HIGH", "reason": "a"},
        {"score": 0.62, "direction": "BULLISH", "confidence": "MEDIUM", "reason": "b"},
        {"score": 0.4, "direction": "NEUTRAL", "confidence": "LOW", "reason": "c"},
    ]
    encoded = [json.dumps(o) for o in outputs]
    calls = iter(encoded)

    def _post(url, json=None, timeout=None):
        return _resp({"response": next(calls)})
    monkeypatch.setattr(requests, "post", _post)

    result = p.sample_consistency("FOO", _NEWS, n=3, temperature=0.7)
    assert result.n_requested == 3
    assert result.n_valid == 3
    assert result.majority_direction == "BULLISH"
    assert result.direction_agreement == 2 / 3
    assert result.score_mean == (0.6 + 0.62 + 0.4) / 3
    assert result.confidence_mix == {"HIGH": 1, "MEDIUM": 1, "LOW": 1}


def test_temperature_is_passed_through_to_ollama_options(monkeypatch):
    p = OllamaPrescreener(model="llama3.1:8b")
    _available(monkeypatch, p)
    seen_temps = []

    def _post(url, json=None, timeout=None):
        seen_temps.append(json["options"]["temperature"])
        return _resp({"response": '{"score":0.5,"direction":"NEUTRAL","confidence":"LOW","reason":"x"}'})
    monkeypatch.setattr(requests, "post", _post)

    p.sample_consistency("FOO", _NEWS, n=4, temperature=0.9)
    assert seen_temps == [0.9, 0.9, 0.9, 0.9]


def test_default_call_ollama_temperature_unchanged_for_existing_callers(monkeypatch):
    """Rückwärtskompatibilität: prescreen()/generate() ohne explizite
    temperature müssen weiterhin 0.1 senden (bisheriges Verhalten)."""
    p = OllamaPrescreener(model="llama3.1:8b")
    seen = {}

    def _post(url, json=None, timeout=None):
        seen["temperature"] = json["options"]["temperature"]
        return _resp({"response": "ok"})
    monkeypatch.setattr(requests, "post", _post)

    p.generate("irgendein Prompt")
    assert seen["temperature"] == 0.1


def test_all_samples_fail_returns_nan_result(monkeypatch):
    p = OllamaPrescreener(model="llama3.1:8b")
    _available(monkeypatch, p)

    def _post(url, json=None, timeout=None):
        return _resp({}, status=500)
    monkeypatch.setattr(requests, "post", _post)

    result = p.sample_consistency("FOO", _NEWS, n=3)
    assert result.n_valid == 0
    assert result.samples == []
    import math
    assert math.isnan(result.score_mean)


def test_ollama_unavailable_short_circuits_without_calls(monkeypatch):
    p = OllamaPrescreener(model="llama3.1:8b")
    monkeypatch.setattr(p, "is_available", lambda: False)
    calls = []

    def _post(url, json=None, timeout=None):
        calls.append(1)
        return _resp({"response": "{}"})
    monkeypatch.setattr(requests, "post", _post)

    result = p.sample_consistency("FOO", _NEWS, n=5)
    assert result.n_valid == 0
    assert calls == []


def test_partial_parse_failures_are_excluded_not_counted_as_zero(monkeypatch):
    p = OllamaPrescreener(model="llama3.1:8b")
    _available(monkeypatch, p)
    responses = [
        '{"score":0.8,"direction":"BULLISH","confidence":"HIGH","reason":"x"}',
        "not valid json at all",
        '{"score":0.7,"direction":"BULLISH","confidence":"HIGH","reason":"y"}',
    ]
    calls = iter(responses)

    def _post(url, json=None, timeout=None):
        return _resp({"response": next(calls)})
    monkeypatch.setattr(requests, "post", _post)

    result = p.sample_consistency("FOO", _NEWS, n=3)
    assert result.n_requested == 3
    assert result.n_valid == 2
    assert result.score_mean == (0.8 + 0.7) / 2


def test_single_valid_sample_has_zero_std_not_nan(monkeypatch):
    p = OllamaPrescreener(model="llama3.1:8b")
    _available(monkeypatch, p)

    def _post(url, json=None, timeout=None):
        return _resp({"response": '{"score":0.6,"direction":"BULLISH","confidence":"HIGH","reason":"x"}'})
    monkeypatch.setattr(requests, "post", _post)

    result = p.sample_consistency("FOO", _NEWS, n=1)
    assert result.n_valid == 1
    assert result.score_std == 0.0
    assert result.direction_agreement == 1.0
