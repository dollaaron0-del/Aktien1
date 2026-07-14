"""
Tests für analyzers/decision_replay.py (Roadmap 4.5: Entscheidungs-Replay).
"""
import json

from analyzers.decision_replay import (
    diff_fields,
    is_thesis_check_prompt,
    replay_analysis,
    replay_recent,
    replay_response,
)


class _StubArchive:
    def __init__(self, entries):
        self._entries = entries  # analysis_id -> entry dict

    def get_by_analysis_id(self, analysis_id):
        return self._entries.get(analysis_id)

    def recent(self, limit=200):
        return list(self._entries.values())[:limit]


class _StubLog:
    def __init__(self, rows):
        self._rows = rows  # analysis_id -> dict

    def get_by_id(self, analysis_id):
        return self._rows.get(analysis_id)


def _analysis_entry(analysis_id, ticker, response, user_prompt="Analysiere folgende News für X"):
    return {
        "analysis_id": analysis_id,
        "ticker": ticker,
        "created_at": "2026-07-01T10:00:00",
        "model": "claude-sonnet-4-5",
        "user_prompt": user_prompt,
        "response_text": json.dumps(response),
    }


# ── is_thesis_check_prompt ────────────────────────────────────────────────────

def test_is_thesis_check_prompt_detects_marker():
    assert is_thesis_check_prompt("Ist die Kaufthese noch intakt? Antworte...")


def test_is_thesis_check_prompt_false_for_standard_analysis():
    assert not is_thesis_check_prompt("Analysiere folgende News für AAPL...")


def test_is_thesis_check_prompt_handles_none():
    assert not is_thesis_check_prompt(None)


# ── replay_response ───────────────────────────────────────────────────────────

def test_replay_response_parses_standard_analysis():
    response = json.dumps({
        "sentiment_score": 0.8, "direction": "BULLISH", "confidence": "HIGH",
        "recommendation": "BUY", "entry_rationale": "stark",
    })
    r = replay_response("AAPL", "Analysiere folgende News für AAPL", response)
    assert r["kind"] == "analysis"
    assert r["recommendation"] == "BUY"
    assert r["direction"] == "BULLISH"
    assert r["confidence"] == "HIGH"
    assert r["sentiment_score"] == 0.8


def test_replay_response_applies_current_buy_floor_logic():
    """Ein bullishes HIGH-Signal ueber der Kaufschwelle, das als HOLD
    zurueckkam, wird von _enforce_buy_floor auch beim Replay auf BUY
    angehoben – identisch zum Live-Pfad (Roadmap-Kommentar in
    claude_analyzer._enforce_buy_floor)."""
    response = json.dumps({
        "sentiment_score": 0.9, "direction": "BULLISH", "confidence": "HIGH",
        "recommendation": "HOLD",
    })
    r = replay_response("AAPL", "Analysiere folgende News für AAPL", response)
    assert r["recommendation"] == "BUY"


def test_replay_response_parses_thesis_check():
    response = json.dumps({
        "thesis_valid": False, "thesis_break_reason": "Guidance gesenkt",
        "sentiment_score": 0.3, "recommendation": "SELL",
    })
    r = replay_response("AAPL", "Ist die Kaufthese noch intakt? ...", response)
    assert r["kind"] == "thesis_check"
    assert r["recommendation"] == "SELL"
    assert r["thesis_valid"] is False
    assert r["thesis_break_reason"] == "Guidance gesenkt"


def test_replay_response_malformed_json_falls_back_to_empty_result():
    r = replay_response("AAPL", "Analysiere folgende News für AAPL", "kein json hier")
    assert r["kind"] == "analysis"
    assert r["recommendation"] == "SKIP"


# ── diff_fields ────────────────────────────────────────────────────────────────

def test_diff_fields_analysis_kind_detects_recommendation_change():
    original = {"recommendation": "HOLD", "direction": "BULLISH",
                "confidence": "HIGH", "sentiment_score": 0.9}
    replayed = {"kind": "analysis", "recommendation": "BUY", "direction": "BULLISH",
                "confidence": "HIGH", "sentiment_score": 0.9}
    assert diff_fields(original, replayed) == ["recommendation"]


def test_diff_fields_identical_returns_empty():
    original = {"recommendation": "BUY", "direction": "BULLISH",
                "confidence": "HIGH", "sentiment_score": 0.8}
    replayed = {"kind": "analysis", **original}
    assert diff_fields(original, replayed) == []


def test_diff_fields_thesis_check_only_compares_recommendation():
    original = {"recommendation": "HOLD", "direction": "NEUTRAL",
                "confidence": "LOW", "sentiment_score": 0.5}
    replayed = {"kind": "thesis_check", "recommendation": "SELL"}
    assert diff_fields(original, replayed) == ["recommendation"]


# ── replay_analysis (mit injizierten Stubs) ──────────────────────────────────

def test_replay_analysis_no_archived_prompt_returns_none():
    archive = _StubArchive({})
    log = _StubLog({1: {"recommendation": "BUY"}})
    assert replay_analysis(1, analysis_log=log, prompt_archive=archive) is None


def test_replay_analysis_missing_analysis_log_row_returns_none():
    archive = _StubArchive({1: _analysis_entry(1, "AAPL", {
        "sentiment_score": 0.8, "direction": "BULLISH", "confidence": "HIGH",
        "recommendation": "BUY",
    })})
    log = _StubLog({})
    assert replay_analysis(1, analysis_log=log, prompt_archive=archive) is None


def test_replay_analysis_flags_drift():
    archive = _StubArchive({1: _analysis_entry(1, "AAPL", {
        "sentiment_score": 0.9, "direction": "BULLISH", "confidence": "HIGH",
        "recommendation": "HOLD",
    })})
    # Damals wurde HOLD tatsaechlich geloggt (vor Einbau des Buy-Bodens).
    log = _StubLog({1: {"recommendation": "HOLD", "direction": "BULLISH",
                         "confidence": "HIGH", "sentiment_score": 0.9}})
    r = replay_analysis(1, analysis_log=log, prompt_archive=archive)
    assert r["changed"] is True
    assert r["changed_fields"] == ["recommendation"]
    assert r["replayed"]["recommendation"] == "BUY"
    assert r["ticker"] == "AAPL"


def test_replay_analysis_no_drift_when_identical():
    archive = _StubArchive({1: _analysis_entry(1, "AAPL", {
        "sentiment_score": 0.8, "direction": "BULLISH", "confidence": "HIGH",
        "recommendation": "BUY",
    })})
    log = _StubLog({1: {"recommendation": "BUY", "direction": "BULLISH",
                         "confidence": "HIGH", "sentiment_score": 0.8}})
    r = replay_analysis(1, analysis_log=log, prompt_archive=archive)
    assert r["changed"] is False
    assert r["changed_fields"] == []


# ── replay_recent (Batch) ────────────────────────────────────────────────────

def test_replay_recent_skips_entries_without_analysis_id():
    entries = {
        1: _analysis_entry(1, "AAPL", {"sentiment_score": 0.8, "direction": "BULLISH",
                                        "confidence": "HIGH", "recommendation": "BUY"}),
    }
    entries[2] = dict(entries[1])
    entries[2]["analysis_id"] = None
    archive = _StubArchive(entries)
    log = _StubLog({1: {"recommendation": "BUY", "direction": "BULLISH",
                         "confidence": "HIGH", "sentiment_score": 0.8}})
    out = replay_recent(limit=10, analysis_log=log, prompt_archive=archive)
    assert len(out) == 1
    assert out[0]["analysis_id"] == 1


def test_replay_recent_reports_only_what_is_replayable():
    archive = _StubArchive({
        1: _analysis_entry(1, "AAPL", {"sentiment_score": 0.9, "direction": "BULLISH",
                                        "confidence": "HIGH", "recommendation": "HOLD"}),
        2: _analysis_entry(2, "MSFT", {"sentiment_score": 0.4, "direction": "NEUTRAL",
                                        "confidence": "LOW", "recommendation": "SKIP"}),
    })
    log = _StubLog({
        1: {"recommendation": "HOLD", "direction": "BULLISH", "confidence": "HIGH",
            "sentiment_score": 0.9},
        2: {"recommendation": "SKIP", "direction": "NEUTRAL", "confidence": "LOW",
            "sentiment_score": 0.4},
    })
    out = replay_recent(limit=10, analysis_log=log, prompt_archive=archive)
    changed = [r for r in out if r["changed"]]
    assert len(out) == 2
    assert len(changed) == 1
    assert changed[0]["ticker"] == "AAPL"
