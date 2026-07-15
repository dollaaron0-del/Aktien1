"""
Tests für dashboard/why_not.py (Ausbau-Roadmap H3.1 — "Warum nicht?"-Explorer).

gate_trail() gegen echte DecisionLog-Einträge (bare DecisionLog() —
conftest.py bindet DECISION_LOG_PATH auf eine Test-DB, geteilt über die
GANZE Testsession). Bewusst eigene, sonst nirgends verwendete Test-Ticker
(ZWN*) statt AAPL/MSFT/NVDA/TSLA — gate_trail() nimmt den NEUESTEN
Eintrag eines Tickers+Tages, und andere Testdateien loggen diese
gängigen Ticker ohne explizites decided_at (= "jetzt") auf denselben
Tag; ein "passender" Ticker aus einer anderen Datei würde sonst
den eigens gesetzten Test-Zeitstempel hier überschatten.
"""
from dashboard.why_not import gate_trail, gate_trail_svg
from dashboard.theme import PALETTE


def _log(ticker, action, reason, decided_at):
    from analyzers.decision_log import DecisionLog
    DecisionLog().log({
        "ticker": ticker, "action": action, "reason": reason,
        "recommendation": action, "sentiment_score": 0.5,
        "decided_at": decided_at,
    })


def test_gate_trail_empty_without_any_entry():
    assert gate_trail("NOPE", "2026-07-15") == []


def test_gate_trail_all_passed_plus_result_for_buy():
    _log("ZWN1", "BUY", "", "2026-07-15T09:00:00")
    trail = gate_trail("ZWN1", "2026-07-15")
    assert all(s["status"] == "passed" for s in trail[:-1])
    assert trail[-1] == {"key": "ergebnis", "label": "Ergebnis: BUY", "status": "result"}


def test_gate_trail_marks_matched_bucket_blocked_and_rest_unreached():
    _log("ZWN2", "SKIP", "Sektor-Korrelation zu hoch", "2026-07-15T09:05:00")
    trail = gate_trail("ZWN2", "2026-07-15")

    statuses = {s["key"]: s["status"] for s in trail}
    assert statuses["korrelation"] == "blocked"
    # alles VOR "korrelation" in der echten Bucket-Reihenfolge ist "passed":
    order = [s["key"] for s in trail]
    idx = order.index("korrelation")
    assert all(s["status"] == "passed" for s in trail[:idx])
    assert all(s["status"] == "unreached" for s in trail[idx + 1:])

    blocked = next(s for s in trail if s["status"] == "blocked")
    assert blocked["reason"] == "Sektor-Korrelation zu hoch"


def test_gate_trail_uses_newest_entry_of_the_day():
    _log("ZWN3", "SKIP", "< Schwelle", "2026-07-15T08:00:00")
    _log("ZWN3", "SKIP", "Max Positionen", "2026-07-15T10:00:00")
    trail = gate_trail("ZWN3", "2026-07-15")
    blocked = next(s for s in trail if s["status"] == "blocked")
    assert blocked["key"] == "max_positionen"


def test_gate_trail_unknown_reason_falls_back_to_sonstiges():
    _log("ZWN4", "SKIP", "Irgendein neuer, unbekannter Grund", "2026-07-15T09:00:00")
    trail = gate_trail("ZWN4", "2026-07-15")
    assert trail[-1]["key"] == "sonstiges"
    assert trail[-1]["status"] == "blocked"
    assert all(s["status"] == "passed" for s in trail[:-1])


def test_gate_trail_fail_open_on_broken_decision_log(monkeypatch):
    import analyzers.decision_log as dlog_mod

    class _Boom:
        def get_day(self, day):
            raise RuntimeError("kaputt")

    monkeypatch.setattr(dlog_mod, "DecisionLog", _Boom)
    assert gate_trail("ZWN5", "2026-07-15") == []


# ── gate_trail_svg() ──────────────────────────────────────────────────────────

def test_gate_trail_svg_empty_for_empty_trail():
    assert gate_trail_svg([]) == ""


def test_gate_trail_svg_colors_and_escapes_reason():
    trail = [
        {"key": "a", "label": "A", "status": "passed"},
        {"key": "b", "label": "<b>B</b>", "status": "blocked", "reason": "<script>x</script>"},
        {"key": "c", "label": "C", "status": "unreached"},
    ]
    svg = gate_trail_svg(trail)
    assert svg.startswith("<svg")
    assert f'stroke="{PALETTE["neon_green"]}"' in svg
    assert f'stroke="{PALETTE["red"]}"' in svg
    assert f'stroke="{PALETTE["border"]}"' in svg
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
    assert "&lt;b&gt;B&lt;/b&gt;" in svg
    assert 'opacity="0.4"' in svg  # unreached gedimmt
