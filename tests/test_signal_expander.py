"""
Tests für analyzers/signal_expander.py – passive Sammlung + Eskalation.

Konzept: Small-Cap-Signale werden passiv gesammelt (Gewicht akkumuliert) und
erst eskaliert (→ Analyse), wenn GENUG Gewicht (_READY_WEIGHT) UND Bestätigung
aus mehreren Quellen (_MIN_SOURCES) vorliegt. Zusätzlich begrenzt ein Gesamt-
Deckel (_MAX_READY_TOTAL), wie viele Small-Caps INSGESAMT gleichzeitig in der
Analyse stehen. So flutet kein Social-Rauschen die teure Claude-Analyse.
"""
import json
from datetime import datetime, timedelta

import analyzers.signal_expander as se
from analyzers.signal_expander import SignalDrivenExpander


def _expander(tmp_path):
    se._DATA_FILE = str(tmp_path / "sig.json")
    return SignalDrivenExpander()


def _insider(ticker):
    return {"source": "SEC Form 4", "ticker": ticker, "action": "buy", "value": 500_000}


def _contract(ticker):
    return {"source": "usaspending", "ticker": ticker, "amount": 10_000_000}


def test_single_social_mention_collects_but_not_ready(tmp_path):
    e = _expander(tmp_path)
    e.process_news_items([{"source": "stocktwits", "title": "$ABCD to the moon"}])
    assert e.get_ready_tickers() == []                       # noch nicht eskaliert
    entry = e.get_all_entries()[0]
    assert entry["ticker"] == "ABCD"
    assert entry["status"] == "📥 sammelt"
    assert entry["weight"] < se._READY_WEIGHT


def test_multi_source_escalates(tmp_path):
    """Gewicht-Schwelle UND zwei verschiedene Quellen → eskaliert."""
    e = _expander(tmp_path)
    e.process_news_items([_insider("ABCD")])                 # 1 Quelle
    assert e.get_ready_tickers() == []                       # reicht allein nicht
    promoted = e.process_news_items([_contract("ABCD")])     # 2. Quelle → Gewicht 2.0
    assert "ABCD" in e.get_ready_tickers()
    assert promoted == ["ABCD"]


def test_single_source_never_escalates(tmp_path):
    """Selbst ein starkes Einzelsignal (Insider) eskaliert ohne 2. Quelle nicht –
    auch wiederholt aus derselben Quelle nicht."""
    e = _expander(tmp_path)
    assert e.process_news_items([_insider("XYZ")]) == []
    assert e.process_news_items([_insider("XYZ")]) == []     # Gewicht hoch, aber 1 Quelle
    assert e.get_ready_tickers() == []


def test_small_insider_buy_below_threshold_ignored(tmp_path):
    e = _expander(tmp_path)
    e.process_news_items([{
        "source": "SEC Form 4", "ticker": "XYZ", "action": "buy", "value": 50_000,
    }])
    assert e.get_ready_tickers() == []
    assert e.get_all_entries() == []                         # unter Mindest-Kaufvolumen


def test_ready_total_is_capped(tmp_path, monkeypatch):
    """Gesamt-Deckel: nie mehr als _MAX_READY_TOTAL Small-Caps gleichzeitig in
    Analyse, egal wie viele eskalieren."""
    monkeypatch.setattr(se, "_MAX_READY_TOTAL", 3)
    monkeypatch.setattr(se, "_MAX_PROMOTE_PER_CYCLE", 10)
    e = _expander(tmp_path)
    for t in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        e.process_news_items([_insider(t)])
        e.process_news_items([_contract(t)])                 # macht t eligible
    assert len(e.get_ready_tickers()) == 3                   # 5 eligible, Deckel 3
    e.process_news_items([])                                 # Folgezyklus ändert nichts
    assert len(e.get_ready_tickers()) == 3


def test_index_and_universe_tickers_blocked(tmp_path):
    """Indizes/ETFs und Haupt-Universum (Large-Caps) gehören nicht in den Radar."""
    e = _expander(tmp_path)
    e.process_news_items([_insider("SPY"), _insider("AAPL"), _insider("QQQ")])
    assert e.get_all_entries() == []


def test_legacy_ready_flag_honored_and_capped(tmp_path, monkeypatch):
    """Alt-Eintrag mit ready=True (Live-Daten) wird respektiert, aber gedeckelt."""
    monkeypatch.setattr(se, "_MAX_READY_TOTAL", 3)
    e = _expander(tmp_path)
    exp = (datetime.utcnow() + timedelta(days=3)).isoformat()
    data = {
        t: {"reason": "x", "added_at": exp, "expires_at": exp, "signals": 5,
            "weight": 3.0, "ready": True}
        for t in ("AAA", "BBB", "CCC", "DDD", "EEE")
    }
    json.dump(data, open(se._DATA_FILE, "w"))
    assert len(e.get_ready_tickers()) == 3                   # hart auf Deckel begrenzt


def test_prune_removes_junk_and_recaps(tmp_path, monkeypatch):
    """prune() wirft Müll/Indizes raus und normalisiert ready-Flags neu."""
    monkeypatch.setattr(se, "_MAX_READY_TOTAL", 3)
    e = _expander(tmp_path)
    exp = (datetime.utcnow() + timedelta(days=3)).isoformat()
    data = {}
    for junk in ("BUY", "SPY", "HIV"):                       # Blacklist + Index
        data[junk] = {"reason": "x", "added_at": exp, "expires_at": exp,
                      "signals": 1, "weight": 3.0,
                      "sources": ["insider", "contract"], "ready": True}
    for t in ("AAA", "BBB", "CCC", "DDD", "EEE"):            # echte, eligible
        data[t] = {"reason": "x", "added_at": exp, "expires_at": exp,
                   "signals": 2, "weight": 2.5,
                   "sources": ["insider", "contract"], "ready": True}
    json.dump(data, open(se._DATA_FILE, "w"))
    stats = e.prune()
    assert stats["removed"] == 3
    assert len(e.get_ready_tickers()) == 3


def test_phantom_ticker_blocked(tmp_path):
    """SPCX (Yahoo-Phantom für die private SpaceX) darf nicht in den Radar."""
    e = _expander(tmp_path)
    e.process_news_items([{"source": "stocktwits", "title": "$SPCX SpaceX to the moon"}])
    assert e.get_all_entries() == []                         # geblockt, nicht gesammelt


def test_promotion_is_idempotent(tmp_path):
    """Einmal promotet → weitere Signale promoten nicht erneut (kein Spam)."""
    e = _expander(tmp_path)
    e.process_news_items([_insider("WXYZ")])
    e.process_news_items([_contract("WXYZ")])                # eskaliert hier
    again = e.process_news_items([_contract("WXYZ")])
    assert again == []                                       # nicht nochmal promotet
    assert "WXYZ" in e.get_ready_tickers()
