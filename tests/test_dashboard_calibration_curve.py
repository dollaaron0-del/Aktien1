"""
Tests für dashboard/calibration_curve.py (Ausbau-Roadmap H3.3 —
Kalibrier-Kurve live).
"""
from dashboard.calibration_curve import confidence_win_rates


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def iter_labeled(self):
        for feat, out in self._rows:
            yield feat, out

    def close(self):
        pass


def test_confidence_win_rates_computes_per_bucket():
    rows = [
        ({"confidence": "HIGH"}, {"outcome": "WIN"}),
        ({"confidence": "HIGH"}, {"outcome": "LOSS"}),
        ({"confidence": "MEDIUM"}, {"outcome": "WIN"}),
        ({"confidence": "MEDIUM"}, {"outcome": "WIN"}),
        ({"confidence": "MEDIUM"}, {"outcome": "LOSS"}),
    ]
    result = confidence_win_rates(store=_FakeStore(rows))
    by_conf = {r["confidence"]: r for r in result}
    assert by_conf["HIGH"]["n"] == 2
    assert by_conf["HIGH"]["wins"] == 1
    assert by_conf["HIGH"]["win_rate"] == 0.5
    assert by_conf["MEDIUM"]["n"] == 3
    assert by_conf["MEDIUM"]["win_rate"] == round(2 / 3, 4)
    assert by_conf["LOW"]["n"] == 0
    assert by_conf["LOW"]["win_rate"] is None


def test_confidence_win_rates_always_returns_all_three_levels():
    result = confidence_win_rates(store=_FakeStore([]))
    assert [r["confidence"] for r in result] == ["HIGH", "MEDIUM", "LOW"]
    assert all(r["n"] == 0 and r["win_rate"] is None for r in result)


def test_confidence_win_rates_ignores_unknown_confidence_and_outcome():
    rows = [
        ({"confidence": "MAYBE"}, {"outcome": "WIN"}),
        ({"confidence": "HIGH"}, {"outcome": "PENDING"}),
        ({"confidence": "HIGH"}, {"outcome": None}),
        ({"confidence": "HIGH"}, {"outcome": "WIN"}),
    ]
    result = confidence_win_rates(store=_FakeStore(rows))
    high = next(r for r in result if r["confidence"] == "HIGH")
    assert high["n"] == 1
    assert high["wins"] == 1


def test_confidence_win_rates_confidence_lowercased_in_source():
    rows = [({"confidence": "high"}, {"outcome": "WIN"})]
    result = confidence_win_rates(store=_FakeStore(rows))
    high = next(r for r in result if r["confidence"] == "HIGH")
    assert high["n"] == 1


def test_confidence_win_rates_fail_open_on_broken_source(monkeypatch):
    import analyzers.experience_store as es_mod

    class _Boom:
        def iter_labeled(self):
            raise RuntimeError("kaputt")

        def close(self):
            pass

    monkeypatch.setattr(es_mod, "ExperienceStore", _Boom)
    assert confidence_win_rates() == []
