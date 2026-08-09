"""
Tests für analyzers/observation_radar.py (Roadmap 6.11a: Beobachtungs-Radar).
Netzfrei — prescreener wird gestubbt, DB in tmp_path isoliert.
"""
from analyzers.observation_radar import ObservationRadar, observe_ticker
from analyzers.ollama_prescreener import PrescreenResult


def make_radar(tmp_path):
    return ObservationRadar(db_path=str(tmp_path / "radar.db"))


class _StubPrescreener:
    model = "qwen2.5:14b"

    def __init__(self, result):
        self._result = result

    def prescreen(self, ticker, news_items, has_open_position=False):
        return self._result


def _result(score=0.7, direction="BULLISH", confidence="HIGH", ollama_used=True):
    return PrescreenResult(
        score=score, direction=direction, confidence=confidence, reason="x",
        send_to_claude=True, skip_reason="", ollama_used=ollama_used, latency_ms=5,
    )


def test_init_creates_table(tmp_path):
    radar = make_radar(tmp_path)
    cols = {r[1] for r in radar._conn.execute("PRAGMA table_info(observations)")}
    assert cols == {"id", "observed_at", "ticker", "score", "direction",
                     "confidence", "model", "n_headlines"}


def test_record_and_history(tmp_path):
    radar = make_radar(tmp_path)
    radar.record("AAPL", 0.7, "BULLISH", "HIGH", "qwen2.5:14b", 3)
    hist = radar.history("AAPL")
    assert len(hist) == 1
    assert hist[0]["ticker"] == "AAPL"
    assert hist[0]["score"] == 0.7
    assert hist[0]["n_headlines"] == 3


def test_history_newest_first(tmp_path):
    radar = make_radar(tmp_path)
    radar.record("AAPL", 0.5, "NEUTRAL", "LOW", "m", 1)
    radar.record("AAPL", 0.8, "BULLISH", "HIGH", "m", 2)
    hist = radar.history("AAPL")
    assert [h["score"] for h in hist] == [0.8, 0.5]


def test_history_scoped_to_ticker(tmp_path):
    radar = make_radar(tmp_path)
    radar.record("AAPL", 0.7, "BULLISH", "HIGH", "m", 1)
    radar.record("MSFT", 0.3, "BEARISH", "HIGH", "m", 1)
    assert len(radar.history("AAPL")) == 1
    assert radar.history("AAPL")[0]["ticker"] == "AAPL"


def test_latest_per_ticker_returns_one_row_per_ticker(tmp_path):
    radar = make_radar(tmp_path)
    radar.record("AAPL", 0.5, "NEUTRAL", "LOW", "m", 1)
    radar.record("AAPL", 0.8, "BULLISH", "HIGH", "m", 2)   # jüngste AAPL-Zeile
    radar.record("MSFT", 0.3, "BEARISH", "HIGH", "m", 1)
    latest = radar.latest_per_ticker()
    by_ticker = {r["ticker"]: r["score"] for r in latest}
    assert by_ticker == {"AAPL": 0.8, "MSFT": 0.3}


def test_record_is_fail_open(tmp_path):
    radar = make_radar(tmp_path)
    radar._conn.close()
    assert radar.record("AAPL", 0.5, "NEUTRAL", "LOW", "m", 1) is None


# ── observe_ticker() ──────────────────────────────────────────────────────────

def test_observe_ticker_records_on_success(tmp_path):
    radar = make_radar(tmp_path)
    stub = _StubPrescreener(_result(score=0.75, direction="BULLISH"))
    out = observe_ticker(radar, stub, "AAPL", [{"title": "x"}])
    assert out["ticker"] == "AAPL"
    assert out["score"] == 0.75
    hist = radar.history("AAPL")
    assert len(hist) == 1
    assert hist[0]["model"] == "qwen2.5:14b"
    assert hist[0]["n_headlines"] == 1


def test_observe_ticker_no_record_when_ollama_offline(tmp_path):
    radar = make_radar(tmp_path)
    stub = _StubPrescreener(_result(ollama_used=False))
    out = observe_ticker(radar, stub, "AAPL", [{"title": "x"}])
    assert out is None
    assert radar.history("AAPL") == []
