"""
Tests für collectors/fda_calendar_collector.py – Biotech-Katalysator-Kalender.
"""
from datetime import datetime, timedelta, timezone

from collectors.fda_calendar_collector import FDACalendarCollector


def _study(phase, days_from_now, title="Pivotal Trial", nct="NCT0001"):
    d = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days_from_now)).strftime("%Y-%m-%d")
    return {
        "protocolSection": {
            "identificationModule": {"briefTitle": title, "nctId": nct},
            "designModule": {"phases": [phase]},
            "statusModule": {"primaryCompletionDateStruct": {"date": d}},
        }
    }


def test_is_healthcare():
    c = FDACalendarCollector()
    assert c._is_healthcare({"sector": "Healthcare", "industry": "Biotechnology"})
    assert c._is_healthcare({"sector": "", "industry": "Drug Manufacturers"})
    assert not c._is_healthcare({"sector": "Technology", "industry": "Software"})


def test_upcoming_phase3_readout_emitted():
    c = FDACalendarCollector(lookahead_days=120, lookback_days=21)
    items = c._to_items("XYZ", [_study("PHASE3", 60)])
    assert len(items) == 1
    assert items[0]["ticker"] == "XYZ"
    assert items[0]["priority"] == "HIGH"
    assert "Phase 3" in items[0]["title"]
    assert items[0]["source"] == "FDA/ClinicalTrials"


def test_phase1_ignored():
    c = FDACalendarCollector()
    assert c._to_items("XYZ", [_study("PHASE1", 30)]) == []


def test_far_future_ignored():
    c = FDACalendarCollector(lookahead_days=120)
    assert c._to_items("XYZ", [_study("PHASE3", 400)]) == []


def test_recently_due_included():
    c = FDACalendarCollector(lookback_days=21)
    items = c._to_items("XYZ", [_study("PHASE2", -10)])
    assert len(items) == 1
    assert "fällig" in items[0]["title"]


def test_non_healthcare_ticker_skips_query(monkeypatch):
    c = FDACalendarCollector()
    monkeypatch.setattr(c, "_profile", lambda t: {"sector": "Technology", "industry": "Software", "company": "Tech Inc"})
    called = {"q": False}
    monkeypatch.setattr(c, "_query_trials", lambda comp: called.__setitem__("q", True) or [])
    assert c.collect("AAPL") == []
    assert called["q"] is False                  # gar nicht erst abgefragt


def test_collect_healthcare_happy_path(monkeypatch):
    c = FDACalendarCollector()
    monkeypatch.setattr(c, "_profile", lambda t: {"sector": "Healthcare", "industry": "Biotech", "company": "Bio Inc"})
    monkeypatch.setattr(c, "_query_trials", lambda comp: [_study("PHASE3", 45)])
    items = c.collect("BIO")
    assert len(items) == 1 and items[0]["ticker"] == "BIO"
