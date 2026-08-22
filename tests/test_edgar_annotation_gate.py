"""
Tests für scripts/edgar_annotation_gate.py (Roadmap 6.8a) — JSON-Label-Parsing,
Stichproben-Ziehung, Forward-Return-Berechnung, Bootstrap-Gate-Verdikt und
Übereinstimmungs-Quote. Netzfrei (keine echten Ollama-/Claude-/yfinance-Aufrufe).
"""
import numpy as np
import pandas as pd
import pytest

from scripts.edgar_annotation_gate import (
    _parse_json_label,
    _sample_filings,
    _forward_return,
    _group_by_direction,
    _evaluate_gate,
    _agreement_rate,
    label_with_ollama,
    label_with_claude,
)


# ── _parse_json_label ───────────────────────────────────────────────────────

def test_parse_json_label_direct():
    raw = '{"direction": "bullish", "confidence": "high", "reason": "guidance raised"}'
    d = _parse_json_label(raw)
    assert d == {"direction": "BULLISH", "confidence": "HIGH", "reason": "guidance raised"}


def test_parse_json_label_with_prefix_suffix_text():
    raw = 'Hier ist meine Einschätzung:\n{"direction": "BEARISH", "confidence": "LOW"}\nDanke.'
    d = _parse_json_label(raw)
    assert d["direction"] == "BEARISH"
    assert d["confidence"] == "LOW"
    assert d["reason"] == ""


def test_parse_json_label_invalid_direction_rejected():
    raw = '{"direction": "MAYBE", "confidence": "HIGH"}'
    assert _parse_json_label(raw) is None


def test_parse_json_label_malformed_json_returns_none():
    assert _parse_json_label("not json at all") is None
    assert _parse_json_label("") is None
    assert _parse_json_label(None) is None


def test_parse_json_label_unknown_confidence_falls_back_to_medium():
    raw = '{"direction": "NEUTRAL", "confidence": "SUPER_HIGH"}'
    d = _parse_json_label(raw)
    assert d["confidence"] == "MEDIUM"


# ── _sample_filings ──────────────────────────────────────────────────────────

def test_sample_filings_only_existing_files(tmp_path):
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "a.txt").write_text("x")
    manifest = pd.DataFrame([
        {"ticker": "AAA", "path": "1/a.txt", "accession": "a"},
        {"ticker": "AAA", "path": "1/missing.txt", "accession": "b"},
    ])
    sample = _sample_filings(manifest, n=5, seed=1, repo_root=tmp_path)
    assert list(sample["accession"]) == ["a"]


def test_sample_filings_deterministic_with_same_seed(tmp_path):
    for i in range(20):
        (tmp_path / str(i)).mkdir()
        (tmp_path / str(i) / "f.txt").write_text("x")
    manifest = pd.DataFrame([
        {"ticker": "T1" if i % 2 == 0 else "T2", "path": f"{i}/f.txt", "accession": str(i)}
        for i in range(20)
    ])
    s1 = _sample_filings(manifest, n=8, seed=42, repo_root=tmp_path)
    s2 = _sample_filings(manifest, n=8, seed=42, repo_root=tmp_path)
    assert list(s1["accession"]) == list(s2["accession"])
    assert len(s1) <= 8


def test_sample_filings_spreads_across_tickers(tmp_path):
    for i in range(20):
        (tmp_path / str(i)).mkdir()
        (tmp_path / str(i) / "f.txt").write_text("x")
    manifest = pd.DataFrame([
        {"ticker": "DOMINANT" if i < 18 else "RARE", "path": f"{i}/f.txt", "accession": str(i)}
        for i in range(20)
    ])
    sample = _sample_filings(manifest, n=4, seed=1, repo_root=tmp_path)
    assert set(sample["ticker"]) == {"DOMINANT", "RARE"}, "beide Ticker sollten vertreten sein"


def test_sample_filings_empty_pool_returns_empty(tmp_path):
    manifest = pd.DataFrame([{"ticker": "AAA", "path": "nope.txt", "accession": "a"}])
    sample = _sample_filings(manifest, n=5, seed=1, repo_root=tmp_path)
    assert sample.empty


# ── _forward_return ──────────────────────────────────────────────────────────

def _bars(dates_closes):
    return [{"date": d, "close": c} for d, c in dates_closes]


def test_forward_return_computes_pct_change():
    bars = _bars([("2024-01-02", 100.0), ("2024-01-03", 102.0), ("2024-01-04", 105.0),
                  ("2024-01-05", 108.0), ("2024-01-08", 110.0), ("2024-01-09", 112.0)])
    ret = _forward_return(bars, "2024-01-02", hold_days=5)
    assert ret == pytest.approx((112.0 - 100.0) / 100.0 * 100.0)


def test_forward_return_entry_is_first_bar_on_or_after_filing_date():
    bars = _bars([("2024-01-02", 100.0), ("2024-01-03", 50.0), ("2024-01-04", 60.0)])
    # Filing kam nach dem 01-02-Close rein -> Entry ist 01-03, nicht 01-02
    ret = _forward_return(bars, "2024-01-03", hold_days=1)
    assert ret == pytest.approx((60.0 - 50.0) / 50.0 * 100.0)


def test_forward_return_none_when_not_enough_future_bars():
    bars = _bars([("2024-01-02", 100.0), ("2024-01-03", 102.0)])
    assert _forward_return(bars, "2024-01-02", hold_days=5) is None


def test_forward_return_none_when_filing_date_after_all_bars():
    bars = _bars([("2024-01-02", 100.0)])
    assert _forward_return(bars, "2024-06-01", hold_days=1) is None


# ── _group_by_direction / _evaluate_gate ────────────────────────────────────

def _row(direction, ret, label_key="ollama"):
    return {label_key: {"direction": direction, "confidence": "HIGH", "reason": ""},
            "forward_return_pct": ret}


def test_group_by_direction_splits_bull_bear_and_skips_missing():
    rows = [
        _row("BULLISH", 3.0), _row("BEARISH", -2.0), _row("NEUTRAL", 0.1),
        {"ollama": {"direction": "BULLISH"}, "forward_return_pct": None},  # kein Kurs -> raus
        {"ollama": None, "forward_return_pct": 1.0},                      # kein Label -> raus
    ]
    bull, bear = _group_by_direction(rows, "ollama")
    assert bull == [3.0]
    assert bear == [-2.0]


def test_evaluate_gate_insufficient_below_min_n():
    rng = np.random.default_rng(1)
    verdict = _evaluate_gate([1.0, 2.0], [-1.0, -2.0], rng, min_n=10)
    assert verdict["status"] == "unzureichend"
    assert verdict["n_bullish"] == 2 and verdict["n_bearish"] == 2


def test_evaluate_gate_signal_when_clearly_separated():
    rng = np.random.default_rng(2)
    bull = [5.0] * 15
    bear = [-5.0] * 15
    verdict = _evaluate_gate(bull, bear, rng, min_n=10)
    assert verdict["status"] == "signal"
    assert verdict["lo"] > 0


def test_evaluate_gate_kein_signal_when_no_real_separation():
    rng = np.random.default_rng(3)
    bull = [1.0, -1.0, 2.0, -2.0, 0.5, -0.5, 1.5, -1.5, 0.0, 0.2] * 2
    bear = list(bull)
    verdict = _evaluate_gate(bull, bear, rng, min_n=10)
    assert verdict["status"] == "kein_signal"
    assert verdict["lo"] < 0 < verdict["hi"]


# ── _agreement_rate ──────────────────────────────────────────────────────────

def test_agreement_rate_counts_only_rows_with_both_labels():
    rows = [
        {"ollama": {"direction": "BULLISH"}, "claude": {"direction": "BULLISH"}},
        {"ollama": {"direction": "BEARISH"}, "claude": {"direction": "NEUTRAL"}},
        {"ollama": {"direction": "BULLISH"}, "claude": None},  # zählt nicht mit
    ]
    agree = _agreement_rate(rows)
    assert agree["n"] == 2
    assert agree["rate"] == pytest.approx(0.5)


def test_agreement_rate_empty_when_no_overlap():
    rows = [{"ollama": {"direction": "BULLISH"}, "claude": None}]
    agree = _agreement_rate(rows)
    assert agree["n"] == 0 and agree["rate"] is None


# ── label_with_ollama / label_with_claude (injizierte Fakes) ───────────────

class _FakePrescreener:
    def __init__(self, response):
        self._response = response
        self.reset_calls = 0

    def _call_ollama(self, prompt, max_tokens=150, temperature=0.1):
        return self._response

    def reset_availability_cache(self):
        self.reset_calls += 1


def test_label_with_ollama_parses_fake_response():
    fake = _FakePrescreener('{"direction": "BULLISH", "confidence": "MEDIUM"}')
    label = label_with_ollama(fake, "irrelevanter Prompt")
    assert label["direction"] == "BULLISH"


def test_label_with_ollama_none_on_offline():
    fake = _FakePrescreener(None)
    assert label_with_ollama(fake, "prompt") is None


def test_label_with_ollama_resets_availability_cache_on_failure():
    fake = _FakePrescreener(None)
    label_with_ollama(fake, "prompt")
    assert fake.reset_calls == 1


def test_label_with_ollama_does_not_reset_cache_on_success():
    fake = _FakePrescreener('{"direction": "NEUTRAL"}')
    label_with_ollama(fake, "prompt")
    assert fake.reset_calls == 0


def test_label_with_claude_uses_injected_call_fn():
    label = label_with_claude(lambda p: '{"direction": "NEUTRAL"}', "prompt")
    assert label["direction"] == "NEUTRAL"
