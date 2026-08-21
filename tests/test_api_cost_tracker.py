"""
Tests für analyzers/api_cost_tracker.py – EUR-Umstellung + Tageslimit.
"""
import json

import analyzers.api_cost_tracker as act
from analyzers.api_cost_tracker import APICostTracker


def _tracker(tmp_path):
    act._FILE = str(tmp_path / "api_savings.json")
    return APICostTracker()


def test_records_cost_in_eur(tmp_path):
    t = _tracker(tmp_path)
    t.record(claude_called=True, ollama_used=False)
    s = t.summary()
    assert s["currency"] == "EUR"
    assert s["today_cost_eur"] == round(act._COST_PER_CLAUDE_CALL, 2)
    assert s["claude_calls"] == 1


def test_daily_limit_enforced_in_eur(tmp_path, monkeypatch):
    monkeypatch.setattr(act, "_MAX_DAILY_COST_EUR", 0.05)
    t = _tracker(tmp_path)
    assert t.check_daily_limit()[0] is True
    # genug Aufrufe bis Limit überschritten
    for _ in range(10):
        t.record(claude_called=True, ollama_used=False)
    ok, reason = t.check_daily_limit()
    assert ok is False
    assert "€" in reason


def test_ollama_skip_counts_savings_not_cost(tmp_path):
    t = _tracker(tmp_path)
    t.record(claude_called=False, ollama_used=True)
    s = t.summary()
    assert s["today_cost_eur"] == 0.0
    assert s["today_saved_eur"] > 0.0
    assert s["ollama_skips"] == 1


def test_migrates_old_usd_keys(tmp_path):
    act._FILE = str(tmp_path / "api_savings.json")
    old = {
        "total_analyses": 5, "claude_calls": 3, "ollama_skips": 2,
        "total_cost_usd": 0.42, "total_saved_usd": 0.10,
        "daily": {"2026-06-15": {"analyses": 5, "claude": 3, "ollama_skips": 2,
                                  "cost_usd": 0.42, "saved_usd": 0.10, "cache_saved_usd": 0.0}},
    }
    open(act._FILE, "w").write(json.dumps(old))
    t = APICostTracker()
    s = t.summary()
    assert s["total_cost_eur"] == 0.42        # Wert übernommen, Key migriert
    assert "total_cost_usd" not in t._data
