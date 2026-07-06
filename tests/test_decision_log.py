"""Tests für analyzers/decision_log.py — Persistenz der Strategie-Entscheidungen
inkl. Funnel-Aggregation für den Dashboard-Tab "Entscheidungen"."""
import pytest

from analyzers.decision_log import DecisionLog, bucket_reason


@pytest.fixture
def dlog(tmp_path):
    d = DecisionLog(db_path=str(tmp_path / "decision_log.db"))
    yield d
    d.close()


def test_log_roundtrip_and_days(dlog):
    dlog.log({
        "decided_at": "2026-07-06T14:30:00", "ticker": "NVDA", "action": "BUY",
        "reason": "Alle Kriterien erfüllt", "executed": "GEKAUFT 3 NVDA @ $120",
        "source": "cycle", "recommendation": "BUY", "direction": "BULLISH",
        "sentiment_score": 0.91, "confidence": "HIGH", "sources_used": 7,
        "regime": "BULL", "macro_bias": 0.4,
    })
    dlog.log({
        "decided_at": "2026-07-06T14:31:00", "ticker": "SAP.DE", "action": "SKIP",
        "reason": "Sentiment 0.55 < Schwelle 0.70", "source": "cycle",
    })
    assert dlog.days() == ["2026-07-06"]
    day = dlog.get_day("2026-07-06")
    assert len(day) == 2
    assert day[0]["ticker"] == "SAP.DE"          # neueste zuerst
    assert day[1]["executed"].startswith("GEKAUFT")
    assert day[1]["macro_bias"] == pytest.approx(0.4)


def test_decided_at_default(dlog):
    dlog.log({"ticker": "X", "action": "SKIP", "reason": "test"})
    assert len(dlog.get_recent(limit=5)) == 1
    assert dlog.get_recent(limit=5)[0]["decided_at"]  # gesetzt


def test_funnel_buckets_skip_reasons(dlog):
    for reason in (
        "Kein Kaufsignal: HOLD/NEUTRAL",
        "Sentiment 0.60 < Schwelle 0.70",
        "Sentiment 0.65 < Schwelle 0.70",
        "Zu wenige Quellen (0 < 1) – übersprungen",
        "Max Positionen (12) erreicht – Signal in Queue",
        "Zu hohe Sektor-Korrelation",
    ):
        dlog.log({"decided_at": "2026-07-06T10:00:00", "ticker": "T",
                  "action": "SKIP", "reason": reason})
    dlog.log({"decided_at": "2026-07-06T10:05:00", "ticker": "B",
              "action": "BUY", "reason": "ok"})
    fn = dlog.funnel("2026-07-06")
    assert fn["total"] == 7
    assert fn["actions"] == {"SKIP": 6, "BUY": 1}
    assert fn["skip_reasons"]["unter_schwelle"] == 2
    assert fn["skip_reasons"]["kein_kaufsignal"] == 1
    assert fn["skip_reasons"]["zu_wenige_quellen"] == 1
    assert fn["skip_reasons"]["max_positionen"] == 1
    assert fn["skip_reasons"]["korrelation"] == 1


def test_bucket_reason_fallback():
    assert bucket_reason("Etwas völlig Anderes") == "sonstiges"
    assert bucket_reason("") == "sonstiges"
    assert bucket_reason("Earnings-Sperre aktiv") == "earnings_sperre"
    assert bucket_reason("Liquiditäts-Gate: Ø-Dollar-Volumen zu klein") == "liquiditaet"


def test_get_recent_ticker_filter(dlog):
    dlog.log({"ticker": "AAA", "action": "SKIP", "reason": "r"})
    dlog.log({"ticker": "BBB", "action": "BUY", "reason": "r"})
    assert len(dlog.get_recent(ticker="AAA")) == 1
    assert dlog.get_recent(ticker="AAA")[0]["ticker"] == "AAA"
