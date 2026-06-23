"""
Tests für analyzers/experience_store.py und die Backfill-Outcome-Mathematik
in scripts/backfill_outcomes.py.

Netzfrei: Kursreihen werden synthetisch konstruiert und direkt an die reine
Funktion simulate_outcome() gegeben.
"""
import pytest

from analyzers.experience_store import ExperienceStore
from scripts.backfill_outcomes import simulate_outcome, normalize_direction


# ── ExperienceStore ────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = ExperienceStore(db_path=str(tmp_path / "exp.db"))
    yield s
    s.close()


def _feat(ticker="AAPL", decided_at="2026-01-01T10:00:00", **kw):
    base = {
        "decided_at": decided_at, "ticker": ticker, "recommendation": "BUY",
        "direction": "LONG", "sentiment_score": 0.7, "confidence": "HIGH",
        "debate_winner": "BULL", "key_catalysts": ["earnings"], "risk_factors": [],
    }
    base.update(kw)
    return base


def test_roundtrip_and_outcome(store):
    did = store.upsert_decision(_feat())
    store.attach_outcome(did, {
        "entry_price": 100.0, "exit_price": 120.0, "exit_reason": "TP",
        "pnl_pct": 20.0, "hold_days": 5, "outcome": "WIN", "label_source": "backfill",
    })
    labeled = list(store.iter_labeled())
    assert len(labeled) == 1
    feat, out = labeled[0]
    assert feat["ticker"] == "AAPL"
    assert feat["key_catalysts"] == ["earnings"]   # JSON round-trip
    assert out["outcome"] == "WIN"
    assert out["pnl_pct"] == 20.0


def test_upsert_idempotent(store):
    id1 = store.upsert_decision(_feat(sentiment_score=0.7))
    id2 = store.upsert_decision(_feat(sentiment_score=0.9))  # selber (ticker, decided_at)
    assert id1 == id2                       # kein Duplikat
    assert store.stats()["total"] == 1
    # Features wurden aktualisiert (UPSERT, nicht ignoriert)
    rows = store._conn.execute("SELECT sentiment_score FROM decisions").fetchall()
    assert rows[0]["sentiment_score"] == 0.9


def test_iter_labeled_filters(store):
    a = store.upsert_decision(_feat(ticker="AAA", recommendation="BUY"))
    b = store.upsert_decision(_feat(ticker="BBB", recommendation="SELL"))
    store.attach_outcome(a, {"outcome": "WIN", "pnl_pct": 1.0, "label_source": "backfill"})
    store.attach_outcome(b, {"outcome": "LOSS", "pnl_pct": -1.0, "label_source": "live"})
    assert len(list(store.iter_labeled(label_source="backfill"))) == 1
    assert len(list(store.iter_labeled(label_source="live"))) == 1
    assert len(list(store.iter_labeled(recommendation="SELL"))) == 1


def test_stats_winrate(store):
    for i, oc in enumerate(["WIN", "WIN", "LOSS"]):
        did = store.upsert_decision(_feat(ticker=f"T{i}"))
        store.attach_outcome(did, {"outcome": oc, "pnl_pct": 1.0 if oc == "WIN" else -1.0,
                                   "label_source": "backfill"})
    s = store.stats()
    assert s["labeled"] == 3 and s["wins"] == 2
    assert s["win_rate"] == pytest.approx(2 / 3, abs=1e-3)


# ── Outcome-Simulation ─────────────────────────────────────────────────────────
def _bars(closes, highs=None, lows=None):
    out = []
    for i, c in enumerate(closes):
        h = highs[i] if highs else c
        lo = lows[i] if lows else c
        out.append({"open": c, "high": h, "low": lo, "close": c})
    return out


def test_long_take_profit():
    # Entry 100, TP 20% -> 120. Bar 2 erreicht High 125.
    bars = _bars([100, 110, 125], highs=[100, 112, 125], lows=[100, 108, 118])
    out = simulate_outcome(bars, "LONG", sl_pct=0.07, tp_pct=0.20, max_hold=10)
    assert out["exit_reason"] == "TP"
    assert out["exit_price"] == pytest.approx(120.0)
    assert out["outcome"] == "WIN"
    assert out["hold_days"] == 2


def test_long_stop_loss():
    # Entry 100, SL 7% -> 93. Bar 1 Low 90 triggert SL.
    bars = _bars([100, 95], highs=[100, 98], lows=[100, 90])
    out = simulate_outcome(bars, "LONG", sl_pct=0.07, tp_pct=0.20, max_hold=10)
    assert out["exit_reason"] == "SL"
    assert out["exit_price"] == pytest.approx(93.0)
    assert out["outcome"] == "LOSS"


def test_time_exit():
    # Weder SL noch TP -> Zeit-Exit am letzten Bar-Close.
    bars = _bars([100, 102, 101, 103], highs=[100, 103, 102, 104], lows=[100, 101, 100, 102])
    out = simulate_outcome(bars, "LONG", sl_pct=0.07, tp_pct=0.20, max_hold=3)
    assert out["exit_reason"] == "TIME"
    assert out["exit_price"] == pytest.approx(103.0)
    assert out["hold_days"] == 3


def test_short_take_profit():
    # SHORT Entry 100, TP 20% -> 80 (Kurs fällt). Bar 1 Low 78.
    bars = _bars([100, 82], highs=[100, 85], lows=[100, 78])
    out = simulate_outcome(bars, "SHORT", sl_pct=0.07, tp_pct=0.20, max_hold=10)
    assert out["exit_reason"] == "TP"
    assert out["exit_price"] == pytest.approx(80.0)
    assert out["pnl_pct"] == pytest.approx(20.0)   # Short-Gewinn bei fallendem Kurs
    assert out["outcome"] == "WIN"


def test_double_trigger_is_conservative():
    # Eine Bar trifft SOWOHL SL als auch TP -> konservativ SL gewinnt.
    bars = _bars([100, 100], highs=[100, 130], lows=[100, 90])
    out = simulate_outcome(bars, "LONG", sl_pct=0.07, tp_pct=0.20, max_hold=10)
    assert out["exit_reason"] == "SL"


def test_nan_guard_and_empty():
    assert simulate_outcome([], "LONG") is None
    assert simulate_outcome([{"close": float("nan")}, {"close": 100}], "LONG") is None
    # nur Entry-Bar, keine Zukunft
    assert simulate_outcome(_bars([100]), "LONG") is None


def test_live_entry_exit_roundtrip(store):
    feat = _feat(ticker="NVDA", direction="LONG")
    did = store.record_live_entry(feat, entry_price=100.0)
    # offen: open_decision_id findet sie, iter_labeled nicht
    assert store.open_decision_id("NVDA") == did
    assert list(store.iter_labeled()) == []
    # Exit labelt das Ergebnis
    out_id = store.record_live_exit("NVDA", exit_price=110.0, exit_reason="Take-Profit")
    assert out_id == did
    assert store.open_decision_id("NVDA") is None    # nicht mehr offen
    labeled = list(store.iter_labeled(label_source="live"))
    assert len(labeled) == 1
    _, out = labeled[0]
    assert out["outcome"] == "WIN"
    assert out["pnl_pct"] == pytest.approx(10.0)


def test_live_exit_short_and_no_open(store):
    # SHORT: Kurs fällt -> Gewinn
    store.record_live_entry(_feat(ticker="X", direction="SHORT"), entry_price=100.0)
    store.record_live_exit("X", exit_price=90.0)
    _, out = list(store.iter_labeled())[0]
    assert out["outcome"] == "WIN" and out["pnl_pct"] == pytest.approx(10.0)
    # kein offener Trade -> None, kein Crash
    assert store.record_live_exit("NONEXISTENT", 50.0) is None


def test_exit_date_reported():
    # Bars mit Datum -> Outcome enthält das Exit-Datum (für Dedup-Fenster).
    bars = [
        {"date": "2026-01-01", "open": 100, "high": 100, "low": 100, "close": 100},
        {"date": "2026-01-02", "open": 110, "high": 112, "low": 108, "close": 110},
        {"date": "2026-01-03", "open": 125, "high": 125, "low": 118, "close": 125},
    ]
    out = simulate_outcome(bars, "LONG", sl_pct=0.07, tp_pct=0.20, max_hold=10)
    assert out["exit_reason"] == "TP"
    assert out["hold_days"] == 2
    assert out["exit_date"] == "2026-01-03"   # future[hold_days-1] = future[1]


def test_normalize_direction():
    assert normalize_direction("LONG") == "LONG"
    assert normalize_direction("bearish") == "SHORT"
    assert normalize_direction("", "SELL") == "SHORT"
    assert normalize_direction("", "BUY") == "LONG"
