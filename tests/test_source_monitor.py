"""
Tests für analyzers/source_monitor.py – Live-Quellen-Health pro Zyklus.
"""
import analyzers.source_monitor as sm
from analyzers.source_monitor import SourceHealthMonitor


class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def _mon(tmp_path):
    sm._STATE_FILE = tmp_path / "sm.json"
    return SourceHealthMonitor()


def test_healthy_source_no_alert(tmp_path):
    m = _mon(tmp_path)
    n = _Notifier()
    for _ in range(3):                       # Baseline aufbauen
        m.start_cycle()
        m.note_result("yahoo", 5)
        assert m.finalize_cycle(n) == []
    assert n.sent == []


def test_sudden_zero_triggers_alert(tmp_path):
    m = _mon(tmp_path)
    n = _Notifier()
    for _ in range(3):                       # gesunde Baseline
        m.start_cycle(); m.note_result("yahoo", 5); m.finalize_cycle(n)
    m.start_cycle()
    m.note_result("yahoo", 0)               # plötzlich nichts
    deg = m.finalize_cycle(n)
    assert any(d["source"] == "yahoo" for d in deg)
    assert len(n.sent) == 1
    assert "yahoo" in n.sent[0]


def test_error_triggers_alert_even_without_baseline(tmp_path):
    m = _mon(tmp_path)
    n = _Notifier()
    m.start_cycle()
    m.note_error("sec_edgar")               # Collector-Fehler
    deg = m.finalize_cycle(n)
    assert any(d["source"] == "sec_edgar" and d["errors"] == 1 for d in deg)


def test_alert_is_throttled(tmp_path):
    m = _mon(tmp_path)
    n = _Notifier()
    for _ in range(3):
        m.start_cycle(); m.note_result("yahoo", 5); m.finalize_cycle(n)
    # zwei aufeinanderfolgende Ausfälle → nur 1 Alert (Throttle)
    m.start_cycle(); m.note_result("yahoo", 0); m.finalize_cycle(n)
    m.start_cycle(); m.note_result("yahoo", 0); m.finalize_cycle(n)
    assert len(n.sent) == 1


def test_never_seen_source_not_alerted(tmp_path):
    m = _mon(tmp_path)
    n = _Notifier()
    # Quelle ohne Baseline, die einfach 0 liefert (z.B. für diesen Ticker irrelevant)
    m.start_cycle()
    m.note_result("patents", 0)
    assert m.finalize_cycle(n) == []
    assert n.sent == []
