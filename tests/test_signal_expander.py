"""
Tests für analyzers/signal_expander.py – passive Sammlung + Eskalation.

Konzept: Small-Cap-Signale werden passiv gesammelt (Gewicht akkumuliert) und
erst ab Schwelle (_READY_WEIGHT) zur Analyse promoted. Verhindert, dass jede
einzelne Social-Erwähnung sofort teuer (Claude) analysiert wird.
"""
import os

import analyzers.signal_expander as se
from analyzers.signal_expander import SignalDrivenExpander


def _expander(tmp_path):
    se._DATA_FILE = str(tmp_path / "sig.json")
    return SignalDrivenExpander()


def test_single_social_mention_collects_but_not_ready(tmp_path):
    e = _expander(tmp_path)
    e.process_news_items([{"source": "stocktwits", "title": "$ABCD to the moon"}])
    assert e.get_ready_tickers() == []                       # noch nicht eskaliert
    entry = e.get_all_entries()[0]
    assert entry["ticker"] == "ABCD"
    assert entry["status"] == "📥 sammelt"
    assert entry["weight"] < 1.0


def test_repeated_mentions_escalate(tmp_path):
    e = _expander(tmp_path)
    for txt in ("to the moon", "breakout", "squeeze"):
        promoted = e.process_news_items([{"source": "stocktwits", "title": f"$ABCD {txt}"}])
    assert "ABCD" in e.get_ready_tickers()                   # 3×0.34 > 1.0 → bereit
    assert promoted == ["ABCD"]                              # letzter Call promotet


def test_insider_buy_escalates_immediately(tmp_path):
    e = _expander(tmp_path)
    promoted = e.process_news_items([{
        "source": "SEC Form 4", "ticker": "XYZ", "action": "buy", "value": 500_000,
    }])
    assert promoted == ["XYZ"]
    assert "XYZ" in e.get_ready_tickers()


def test_small_insider_buy_below_threshold_ignored(tmp_path):
    e = _expander(tmp_path)
    e.process_news_items([{
        "source": "SEC Form 4", "ticker": "XYZ", "action": "buy", "value": 50_000,
    }])
    assert e.get_ready_tickers() == []
    assert e.get_all_entries() == []                         # unter Mindest-Kaufvolumen


def test_legacy_entry_without_weight_is_compatible(tmp_path):
    """Alt-Einträge ohne 'weight' (nur signals) müssen weiter funktionieren."""
    import json
    e = _expander(tmp_path)
    from datetime import datetime, timedelta
    exp = (datetime.utcnow() + timedelta(days=3)).isoformat()
    json.dump(
        {"OLD": {"reason": "x", "added_at": exp, "expires_at": exp, "signals": 5}},
        open(se._DATA_FILE, "w"),
    )
    assert "OLD" in e.get_ready_tickers()                    # 5×0.34 ≥ 1.0


def test_promotion_capped_per_cycle(tmp_path, monkeypatch):
    """Schwung von Eskalationen wird pro Zyklus gedeckelt; Rest rückt nach."""
    monkeypatch.setattr(se, "_MAX_PROMOTE_PER_CYCLE", 3)
    e = _expander(tmp_path)
    items = [
        {"source": "SEC Form 4", "ticker": t, "action": "buy", "value": 500_000}
        for t in ("AAA", "BBB", "CCC", "DDD", "EEE")
    ]
    promoted = e.process_news_items(items)
    assert len(promoted) == 3                                 # Deckel greift
    # Folgezyklus (ohne neue Signale): übrige reife Ticker rücken nach
    rest = e.process_news_items([])
    assert sorted(promoted + rest) == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert len(e.get_ready_tickers()) == 5


def test_phantom_ticker_blocked(tmp_path):
    """SPCX (Yahoo-Phantom für die private SpaceX) darf nicht in den Radar."""
    e = _expander(tmp_path)
    e.process_news_items([{"source": "stocktwits", "title": "$SPCX SpaceX to the moon"}])
    assert e.get_all_entries() == []                         # geblockt, nicht gesammelt


def test_promotion_is_idempotent(tmp_path):
    """Einmal promotet → weitere Signale promoten nicht erneut (kein Spam)."""
    e = _expander(tmp_path)
    e.process_news_items([{"source": "SEC Form 4", "ticker": "XYZ", "action": "buy", "value": 500_000}])
    again = e.process_news_items([{"source": "SEC Form 4", "ticker": "XYZ", "action": "buy", "value": 500_000}])
    assert again == []                                       # nicht nochmal promotet
    assert "XYZ" in e.get_ready_tickers()
